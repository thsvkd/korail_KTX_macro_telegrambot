"""
The boundary the Mini App's requests cross.

The chat flow never had to ask who was talking: an update arrives from
Telegram's own servers and the chat ID on it is a fact. The Mini App inverts
that - the page runs on a stranger's phone and calls this app directly - so
everything here is about one question. Can a request name somebody else?

The answer has to be no even when the caller is inventive: no signature, a
signature over different data, a signature made with a different bot's token,
a real signature from last month, a real signature with the user swapped out
afterwards. Each of those is a test below, because each of them is what an
attacker would actually try, and any one of them getting through would hand
over a registered railway account.
"""

import hashlib
import hmac
import json
import time
from unittest.mock import Mock

import pytest
from flask import Flask

from korail_bot.api.mini_app import INIT_DATA_HEADER, build_blueprint
from korail_bot.api.mini_app_auth import MiniAppAuthError, RateLimiter, verify_init_data
from korail_bot.services.mini_app_gateway import MiniAppError, MiniAppGateway

TOKEN = "123456:test-bot-token"
OTHER_TOKEN = "999999:someone-elses-bot"
CHAT_ID = 12345


def sign(fields: dict, token: str = TOKEN) -> str:
    """Build an initData string the way Telegram builds one."""
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()

    from urllib.parse import urlencode

    return urlencode({**fields, "hash": digest})


def init_data(chat_id: int = CHAT_ID, auth_date: int | None = None, token: str = TOKEN) -> str:
    """A launch payload for one user, signed now unless told otherwise."""
    return sign(
        {
            "auth_date": str(auth_date if auth_date is not None else int(time.time())),
            "query_id": "AAF",
            "user": json.dumps({"id": chat_id, "first_name": "테스트"}, ensure_ascii=False),
        },
        token=token,
    )


class TestEstablishingWhoIsAsking:
    """Only Telegram's signature decides, and only a fresh one."""

    def test_a_genuine_payload_names_its_user(self):
        identity = verify_init_data(init_data(), bot_token=TOKEN)

        assert identity.chat_id == CHAT_ID

    def test_nothing_at_all_is_refused(self):
        with pytest.raises(MiniAppAuthError):
            verify_init_data("", bot_token=TOKEN)

    def test_an_unsigned_payload_is_refused(self):
        """The shape is right and the signature is simply absent."""
        raw = f"auth_date={int(time.time())}&user=%7B%22id%22%3A1%7D"

        with pytest.raises(MiniAppAuthError):
            verify_init_data(raw, bot_token=TOKEN)

    def test_a_signature_from_another_bot_is_refused(self):
        """
        The key is derived from the token, so a payload signed by a bot the
        attacker does control must not open an account on this one.
        """
        with pytest.raises(MiniAppAuthError):
            verify_init_data(init_data(token=OTHER_TOKEN), bot_token=TOKEN)

    def test_a_refusal_says_which_kind_of_wrong_it_was(self):
        """
        Two unrelated faults produce this same refusal - a bot pointed at
        another bot's deployment, and this module rebuilding the signed
        string differently than Telegram did - and they need opposite fixes.
        The message has to separate them or the log is a coin toss.
        """
        with pytest.raises(MiniAppAuthError) as refusal:
            verify_init_data(init_data(token=OTHER_TOKEN), bot_token=TOKEN)

        assert "auth_date,hash,query_id,user" in str(refusal.value)
        assert "another bot's token" in str(refusal.value)
        assert "BotFather" in str(refusal.value)

    def test_a_refusal_says_so_when_the_ed25519_field_is_the_reason(self):
        """
        Telegram's docs are read both ways on whether `signature` belongs in
        the HMAC's input, so a deployment meeting it for the first time
        should not need a code change to find out.
        """
        fields = {
            "auth_date": str(int(time.time())),
            "user": json.dumps({"id": CHAT_ID}),
        }
        # Signed without it, then sent with it - which is the disputed shape.
        raw = f"{sign(fields)}&signature=an-ed25519-signature"

        with pytest.raises(MiniAppAuthError) as refusal:
            verify_init_data(raw, bot_token=TOKEN)

        assert "WOULD MATCH if `signature` were excluded" in str(refusal.value)

    def test_a_refusal_never_puts_the_payload_in_the_log(self):
        """
        These messages are read out of a public repository's issues, and the
        values are the user's id and name.
        """
        with pytest.raises(MiniAppAuthError) as refusal:
            verify_init_data(init_data(chat_id=778899, token=OTHER_TOKEN), bot_token=TOKEN)

        assert "778899" not in str(refusal.value)
        assert "테스트" not in str(refusal.value)

    def test_swapping_the_user_after_signing_is_refused(self):
        """
        The whole attack in one line: take your own valid payload, edit the
        id, book with somebody else's registered account.
        """
        raw = init_data(chat_id=CHAT_ID)
        tampered = raw.replace(str(CHAT_ID), "99999")

        with pytest.raises(MiniAppAuthError):
            verify_init_data(tampered, bot_token=TOKEN)

    def test_an_old_payload_is_refused(self):
        """
        A signature never stops being valid on its own, so one captured off a
        device would be a permanent key without this.
        """
        stale = init_data(auth_date=int(time.time()) - 90_000)

        with pytest.raises(MiniAppAuthError):
            verify_init_data(stale, bot_token=TOKEN)

    def test_a_payload_from_the_future_is_refused(self):
        with pytest.raises(MiniAppAuthError):
            verify_init_data(init_data(auth_date=int(time.time()) + 9_000), bot_token=TOKEN)

    def test_a_payload_naming_nobody_is_refused(self):
        """Telegram omits `user` where it cannot attribute the launch."""
        raw = sign({"auth_date": str(int(time.time())), "query_id": "AAF"})

        with pytest.raises(MiniAppAuthError):
            verify_init_data(raw, bot_token=TOKEN)

    def test_with_no_token_configured_everything_is_refused(self):
        """
        Not "let it through until a token is set". Without a key there is
        nothing to check against, and accepting unverified payloads would
        hand every account to whoever asks first.
        """
        with pytest.raises(MiniAppAuthError):
            verify_init_data(init_data(), bot_token="")


class TestRateLimiter:
    """A valid signature is not a licence to ask ten times a second."""

    def test_requests_within_the_cap_are_allowed(self):
        limiter = RateLimiter(limit=3, window_seconds=60)

        assert all(limiter.allow("a", now=100.0) for _ in range(3))

    def test_the_one_over_the_cap_is_refused(self):
        limiter = RateLimiter(limit=3, window_seconds=60)
        for _ in range(3):
            limiter.allow("a", now=100.0)

        assert limiter.allow("a", now=100.0) is False

    def test_the_window_slides_rather_than_resetting(self):
        limiter = RateLimiter(limit=2, window_seconds=10)
        limiter.allow("a", now=100.0)
        limiter.allow("a", now=105.0)

        # 106 is still inside the window that began at 100.
        assert limiter.allow("a", now=106.0) is False
        # By 111 the first has aged out, so there is room for one.
        assert limiter.allow("a", now=111.0) is True

    def test_callers_are_counted_separately(self):
        limiter = RateLimiter(limit=1, window_seconds=60)
        limiter.allow("a", now=100.0)

        assert limiter.allow("b", now=100.0) is True


@pytest.fixture
def gateway():
    return Mock(spec=MiniAppGateway)


@pytest.fixture
def bot_token(monkeypatch):
    """
    Make the verifier sign with this suite's token.

    Patched on the object ``mini_app_auth`` itself holds rather than on the
    one this module imported. They are not always the same: another test
    reloads korail_bot.config.settings, which rebinds `settings` to a fresh
    instance while every module that already imported it keeps the old one.
    Patching by name here is precise regardless of which instance won.
    """
    from korail_bot.api import mini_app_auth

    monkeypatch.setattr(mini_app_auth.settings, "TELEGRAM_BOT_TOKEN", TOKEN)


@pytest.fixture
def client(gateway, bot_token):
    """The API mounted on a throwaway app, with a stand-in for the work."""
    app = Flask(__name__)
    app.register_blueprint(build_blueprint(gateway), url_prefix="/api")
    return app.test_client()


#: Every route the API exposes. Listed here so that adding one without a
#: guard fails a test rather than going unnoticed.
ROUTES = [
    ("post", "/api/bootstrap"),
    ("post", "/api/register"),
    ("post", "/api/logout"),
    ("post", "/api/trains"),
    ("post", "/api/search"),
    ("post", "/api/schedule"),
    ("post", "/api/search/cancel"),
    ("get", "/api/status"),
    ("post", "/api/reservations/cancel"),
    ("post", "/api/access-request"),
    ("get", "/api/favourites"),
    ("post", "/api/favourites"),
    ("delete", "/api/favourites/abc"),
    ("post", "/api/notify"),
]


class TestEveryRouteIsGuarded:
    """No route may be reachable without a signature."""

    @pytest.mark.parametrize("method,path", ROUTES)
    def test_without_init_data_the_answer_is_401(self, client, method, path):
        assert getattr(client, method)(path).status_code == 401

    @pytest.mark.parametrize("method,path", ROUTES)
    def test_a_forged_signature_is_401(self, client, method, path):
        headers = {INIT_DATA_HEADER: init_data(token=OTHER_TOKEN)}

        assert getattr(client, method)(path, headers=headers).status_code == 401

    def test_the_list_above_covers_every_route_the_api_registers(self, gateway):
        """
        The two tests above are only worth as much as this list. A route added
        without a line here would be tested by neither.
        """
        app = Flask(__name__)
        app.register_blueprint(build_blueprint(gateway), url_prefix="/api")

        registered = {
            rule.rule
            for rule in app.url_map.iter_rules()
            if rule.endpoint != "static" and rule.rule.startswith("/api")
        }
        covered = {path.replace("/abc", "/<fav_id>") for _, path in ROUTES}

        assert registered == covered


class TestTheChatIdComesFromTheSignature:
    """Never from the body, whatever the body says."""

    def test_the_signed_user_is_the_one_acted_for(self, client, gateway):
        gateway.bootstrap.return_value = {"ok": True}

        client.post(
            "/api/bootstrap",
            headers={INIT_DATA_HEADER: init_data(chat_id=CHAT_ID)},
            json={"chat_id": 99999},
        )

        gateway.bootstrap.assert_called_once_with(CHAT_ID)


class TestWhatIsNotOnThePublicListener:
    """
    The separation this whole arrangement exists for.

    /reservation-callback can send arbitrary text to an arbitrary chat. It
    guards itself by requiring a loopback source address - which every request
    has once a reverse proxy forwards it, including requests from the
    internet. So it must not be served on the socket that is published, and
    that has to stay true as routes get added.
    """

    @pytest.fixture
    def public(self, gateway, bot_token):
        from korail_bot.public_app import create_public_app

        return create_public_app(gateway).test_client()

    @pytest.mark.parametrize("path", ["/reservation-callback", "/check_payment"])
    def test_the_internal_callbacks_are_not_reachable(self, public, path):
        assert public.get(path).status_code == 404

    def test_the_mini_app_api_is(self, public, gateway):
        gateway.status.return_value = {}

        response = public.get("/api/status", headers={INIT_DATA_HEADER: init_data()})

        assert response.status_code == 200

    def test_the_health_check_needs_no_signature(self, public):
        """The container healthcheck has none, and names nothing if answered."""
        assert public.get("/health").status_code == 200

    def test_files_that_are_not_the_app_are_not_served(self, public):
        assert public.get("/../.env").status_code == 404
        assert public.get("/secrets.yaml").status_code == 404

    def test_json_answers_are_not_cacheable(self, public, gateway):
        """
        A signed request is a credential; a shared cache keyed on the URL
        alone is how one user's status reaches another user's screen.
        """
        gateway.status.return_value = {}

        response = public.get("/api/status", headers={INIT_DATA_HEADER: init_data()})

        assert response.headers["Cache-Control"] == "no-store"

    def test_the_page_may_only_be_framed_by_telegram(self, public):
        response = public.get("/health")

        assert "frame-ancestors" in response.headers["Content-Security-Policy"]


class TestHowRefusalsAreAnswered:
    """A refusal has to be renderable, and must not leak internals."""

    def headers(self):
        return {INIT_DATA_HEADER: init_data()}

    def test_a_stated_refusal_keeps_its_status_and_sentence(self, client, gateway):
        gateway.list_trains.side_effect = MiniAppError("계정이 없습니다.", status=428)

        response = client.post("/api/trains", headers=self.headers(), json={})

        assert response.status_code == 428
        assert response.get_json()["error"] == "계정이 없습니다."

    def test_an_unexpected_failure_says_nothing_about_the_internals(self, client, gateway):
        """
        This endpoint faces the internet. A traceback belongs in the log,
        never in the response.
        """
        gateway.bootstrap.side_effect = RuntimeError("redis://user:hunter2@10.0.0.5")

        response = client.post("/api/bootstrap", headers=self.headers())

        assert response.status_code == 500
        assert "hunter2" not in response.get_json()["error"]
        assert "redis" not in response.get_json()["error"].lower()
