"""
Unit tests for the Korail client the bot builds.

korail2 ships a client that is shared and fixed in ways the bot cannot live
with: one HTTP session for every user in the process, an app build baked into
the library, and a launch timestamp minted whenever an object is constructed.
These cover what the service overrides on top of it.

They build clients but never log in, so they touch no network.
"""
import time
from unittest.mock import patch

import pytest

from config.settings import settings
from services.korail_service import KorailService


USERNAME = "010-1234-5678"
PASSWORD = "korail-password"


@pytest.fixture
def service():
    return KorailService()


class TestSessionIsolation:
    """
    korail2 keeps its requests.Session on the class.

    Left alone, two users answering the password prompt at the same time hand
    each other their Korail session.
    """

    def test_each_client_gets_its_own_session(self, service):
        one = service._build_client(USERNAME, PASSWORD)
        two = service._build_client("010-9999-8888", PASSWORD)

        assert one._session is not two._session

    def test_a_client_does_not_share_the_library_wide_session(self, service):
        from korail2.korail2 import Korail as LibraryKorail

        client = service._build_client(USERNAME, PASSWORD)

        assert client._session is not LibraryKorail._session

    def test_cookies_do_not_leak_between_clients(self, service):
        one = service._build_client(USERNAME, PASSWORD)
        two = service._build_client("010-9999-8888", PASSWORD)

        one._session.cookies.set("JSESSIONID", "first-users-session")

        assert two._session.cookies.get("JSESSIONID") is None

    def test_the_user_agent_survives_the_swap(self, service):
        """The library sets it on the session it hands out; ours needs it too."""
        client = service._build_client(USERNAME, PASSWORD)

        assert "Android" in client._session.headers["User-Agent"]


class TestAppVersion:
    """The reported app build has to be settable without patching korail2."""

    def test_the_library_build_is_used_when_nothing_is_configured(self, service):
        from korail2.korail2 import Korail as LibraryKorail

        with patch.object(settings, 'KORAIL_APP_VERSION', None):
            client = service._build_client(USERNAME, PASSWORD)

        assert client._version == LibraryKorail._version

    def test_a_configured_build_overrides_it(self, service):
        with patch.object(settings, 'KORAIL_APP_VERSION', '260101001'):
            client = service._build_client(USERNAME, PASSWORD)

        assert client._version == '260101001'


class TestAppSessionStart:
    """
    A search runs in a child process the bot restarts.

    The client stamps every request with when its app was started, so without
    carrying that value across the restart a search the user has had running
    since yesterday keeps announcing a freshly launched app.
    """

    def test_a_fresh_service_lets_the_client_stamp_itself(self, service):
        before = int(time.time() * 1000)

        client = service._build_client(USERNAME, PASSWORD)

        assert int(client._engine.app_start_ts) >= before

    def test_a_resumed_search_keeps_the_timestamp_it_began_with(self):
        service = KorailService(app_session_start="1750000000000")

        client = service._build_client(USERNAME, PASSWORD)

        assert client._engine.app_start_ts == "1750000000000"

    def test_re_logging_in_does_not_restart_the_app_session(self):
        """A refresh mid-search is the same session, not a relaunch."""
        service = KorailService(app_session_start="1750000000000")

        first = service._build_client(USERNAME, PASSWORD)
        second = service._build_client(USERNAME, PASSWORD)

        assert first._engine.app_start_ts == second._engine.app_start_ts


class TestSessionRefreshPacing:
    """
    The session is renewed before Korail closes it.

    On a fixed period a search running for half a day re-authenticates exactly
    on the half hour, every half hour.
    """

    def _deadlines(self, service, count=25):
        drawn = []
        for _ in range(count):
            service._schedule_next_relogin()
            drawn.append(service._relogin_due_at - time.time())
        return drawn

    def test_the_delay_is_drawn_again_after_every_login(self, service):
        service._relogin_interval = 1800
        service._relogin_jitter = 0.4

        drawn = self._deadlines(service)

        assert len(set(round(d, 3) for d in drawn)) > 1

    def test_the_delay_stays_within_the_configured_band(self, service):
        service._relogin_interval = 1800
        service._relogin_jitter = 0.4

        drawn = self._deadlines(service)

        assert all(1080 - 1 <= d <= 2520 + 1 for d in drawn), drawn

    def test_zero_jitter_keeps_the_interval_fixed(self, service):
        service._relogin_interval = 1800
        service._relogin_jitter = 0

        drawn = self._deadlines(service, count=5)

        assert all(abs(d - 1800) < 1 for d in drawn), drawn

    def test_a_zero_interval_turns_the_refresh_off(self, service):
        """Leaves the session to be renewed when Korail rejects it."""
        service._relogin_interval = 0

        service._schedule_next_relogin()

        assert service._relogin_due_at == 0.0
        with patch.object(service, '_relogin') as relogin:
            service._check_session_refresh()
        relogin.assert_not_called()

    def test_the_refresh_fires_once_the_deadline_passes(self, service):
        service._relogin_due_at = time.time() - 1

        with patch.object(service, '_relogin') as relogin:
            service._check_session_refresh()

        relogin.assert_called_once()

    def test_nothing_happens_before_the_deadline(self, service):
        service._relogin_due_at = time.time() + 600

        with patch.object(service, '_relogin') as relogin:
            service._check_session_refresh()

        relogin.assert_not_called()

    def test_a_failed_refresh_does_not_retry_on_every_search(self, service):
        """
        The deadline has to move even when the login failed, or the search
        loop would attempt one every couple of seconds.
        """
        service._relogin_interval = 1800
        service._relogin_jitter = 0.4
        service._username, service._password = USERNAME, PASSWORD
        service._relogin_due_at = time.time() - 1

        with patch.object(service, '_build_client', side_effect=RuntimeError("korail down")):
            assert service._relogin() is False

        assert service._relogin_due_at > time.time()
