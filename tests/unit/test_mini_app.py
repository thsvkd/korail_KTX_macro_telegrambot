"""The static Telegram Mini App and its untrusted-data boundary."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from korail_bot.config.settings import Settings, settings
from korail_bot.handlers import CommandHandler, TelegramUpdateProcessor
from korail_bot.handlers.conversation_handler import ConversationHandler
from korail_bot.models import OnboardedAccount, Operator, UserProgress, UserSession
from korail_bot.services import (
    MiniAppDataError,
    MiniAppSubmission,
    PaymentReminderService,
    ReservationService,
    TelegramService,
)
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot import keyboards

CHAT_ID = 180018
PHONE = "010-1234-5678"
PASSWORD = "railway-password"
WEBAPP = Path(__file__).parents[2] / "webapp"


def future_date(days: int = 1) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y%m%d")


def payload(**changes) -> str:
    values = {
        "v": 1,
        "action": "prepare_search",
        "operator": "korail",
        "dep_date": future_date(),
        "src_station": "서울",
        "dst_station": "부산",
        "dep_time": "0700",
        "max_dep_time": "1200",
        "train_type": "1",
        "seat_option": "1",
        "passenger_count": 2,
        "seat_strategy": "1",
    }
    values.update(changes)
    return json.dumps(values, ensure_ascii=False)


def start_parameter() -> str:
    """A profile/menu launch payload for 수서→부산 on SRT."""
    return "".join(
        (
            "ma1_",
            "s",
            future_date(),
            "0",  # 수서
            "7",  # 부산
            "0700",
            "1200",
            "1",  # SRT has one train type
            "1",  # general first
            "2",  # passengers
            "1",  # consecutive
        )
    )


class TestMiniAppSubmission:
    def test_valid_data_becomes_the_conversation_shape(self):
        submission = MiniAppSubmission.parse(payload())

        assert submission.operator is Operator.KORAIL
        assert submission.passenger_count == 2
        assert submission.as_train_info() == {
            "operator": "korail",
            "depDate": future_date(),
            "srcLocate": "서울",
            "dstLocate": "부산",
            "depTime": "070000",
            "maxDepTime": "1200",
            "trainType": "TrainType.KTX",
            "trainTypeShow": "KTX 계열만",
            "specialInfo": "ReserveOption.GENERAL_FIRST",
            "specialInfoShow": "GENERAL_FIRST",
            "passengerCount": 2,
            "seatStrategy": "consecutive",
            "seatStrategyShow": "연속 좌석",
            "selectedTrains": [],
        }

    def test_srt_ignores_the_korail_only_train_type(self):
        submission = MiniAppSubmission.parse(
            payload(operator="srt", src_station="수서", train_type="not-a-korail-choice")
        )

        info = submission.as_train_info()
        assert info["operator"] == "srt"
        assert info["trainType"] == "SRT"

    @pytest.mark.parametrize(
        ("changes", "message"),
        [
            ({"operator": "other"}, "철도"),
            ({"src_station": "서울", "dst_station": "서울"}, "달라야"),
            ({"dep_time": "1300", "max_dep_time": "1200"}, "늦어야"),
            ({"passenger_count": 10}, "최대 9명"),
            ({"seat_option": "5"}, "1, 2, 3, 4"),
            ({"operator": "srt", "src_station": "서울"}, "서지 않는 역"),
        ],
    )
    def test_invalid_fields_are_refused(self, changes, message):
        with pytest.raises(MiniAppDataError, match=message):
            MiniAppSubmission.parse(payload(**changes))

    @pytest.mark.parametrize("raw", [None, "", "not json", "[]", '{"v": 99}'])
    def test_malformed_or_unknown_payloads_are_refused(self, raw):
        with pytest.raises(MiniAppDataError):
            MiniAppSubmission.parse(raw)

    def test_telegram_data_limit_is_enforced_by_bytes(self):
        with pytest.raises(MiniAppDataError, match="너무 큽니다"):
            MiniAppSubmission.parse("가" * 1400)

    def test_profile_start_parameter_becomes_the_same_validated_submission(self):
        submission = MiniAppSubmission.parse_start_parameter(start_parameter())

        assert submission.operator is Operator.SRT
        assert submission.src_station == "수서"
        assert submission.dst_station == "부산"
        assert submission.passenger_count == 2

    @pytest.mark.parametrize("token", ["", "ma1_", "ma1_x2026080407070012001121"])
    def test_invalid_profile_start_parameters_are_refused(self, token):
        with pytest.raises(MiniAppDataError):
            MiniAppSubmission.parse_start_parameter(token)


class TestMiniAppKeyboard:
    def test_launch_uses_a_reply_keyboard_web_app_button(self):
        keyboard = keyboards.mini_app_keyboard("https://example.test/app")

        assert keyboard["keyboard"][0][0] == {
            "text": keyboards.MINI_APP_OPEN,
            "web_app": {"url": "https://example.test/app"},
        }
        assert keyboard["keyboard"][1][0]["text"] == keyboards.MINI_APP_CHAT_FALLBACK
        assert "inline_keyboard" not in keyboard


class TestMiniAppConfiguration:
    @pytest.mark.parametrize(
        ("url", "enabled"),
        [
            ("https://example.test/app", True),
            ("http://example.test/app", False),
            ("/relative", False),
            (None, False),
        ],
    )
    def test_only_absolute_https_urls_enable_the_app(self, url, enabled):
        with patch.object(Settings, "MINI_APP_URL", url):
            assert settings.mini_app_enabled() is enabled

    def test_the_page_takes_no_payment_details(self):
        """
        A railway login is collected here now - that is the point of the app
        having a registration screen - but a card number never is. The bot
        does not pay for anything and must not look like it might.
        """
        lower = (WEBAPP / "index.html").read_text().lower()
        # Only the <input> tags, not the whole page: 'card' is also the name
        # of the layout class every section here uses.
        inputs = " ".join(re.findall(r"<input\b[^>]*>", lower))

        assert "card" not in inputs
        assert "cvc" not in inputs
        assert "카드" not in lower

    def test_the_page_talks_only_to_the_bot_that_served_it(self):
        """
        The one host in the page is Telegram's own SDK. Everything else is
        same-origin, which is why the API needs no CORS and why a third party
        cannot be talked into answering for the bot.
        """
        page = (WEBAPP / "index.html").read_text()
        script = (WEBAPP / "app.js").read_text()

        assert "connect-src 'self'" in page
        assert re.findall(r"https?://[^\s\"')]+", script) == []
        for source in re.findall(r'src="(https?://[^"]+)"', page):
            assert source.startswith("https://telegram.org/")

    def test_the_page_offers_both_railways(self):
        page = (WEBAPP / "index.html").read_text()

        assert 'value="korail"' in page
        assert 'value="srt"' in page

    def test_no_station_list_is_hardcoded_in_the_page(self):
        """
        They used to be a constant here and another in Python, which meant
        Korail's several hundred stations were the seventeen someone had
        typed out, and the two copies could disagree. The page asks now.
        """
        script = (WEBAPP / "app.js").read_text()

        assert "수서" not in script
        assert "동대구" not in script
        assert "majorStations" in script

    def test_the_page_carries_no_secret(self):
        """
        It is served to every user, so anything in it is public. The bot token
        in particular would be the whole bot.
        """
        for name in ("index.html", "app.js", "app.css"):
            text = (WEBAPP / name).read_text().lower()

            assert "bottoken" not in text
            assert "session_secret" not in text
            assert not re.search(r"\d{8,10}:[a-z0-9_-]{35}", text, re.IGNORECASE)


class MiniAppConversationFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.is_developer.return_value = False
        self.storage.get_onboarded_operators.return_value = [Operator.KORAIL]
        self.session = UserSession(chat_id=CHAT_ID)
        self.storage.get_user_session.return_value = self.session
        self.telegram = Mock(spec=TelegramService)
        self.conversation = ConversationHandler(
            self.storage, self.telegram, Mock(spec=ReservationService)
        )

    def account(self, operator=Operator.KORAIL):
        self.storage.get_onboarded_account.return_value = OnboardedAccount(
            chat_id=CHAT_ID,
            korail_id=PHONE,
            korail_pw=PASSWORD,
            operator=operator,
        )


class TestMiniAppOffer(MiniAppConversationFixture):
    def test_start_offer_leaves_both_operators_available(self):
        with (
            patch.object(settings, "MINI_APP_URL", "https://example.test/app?source=bot"),
            patch.object(settings, "mini_app_enabled", return_value=True),
        ):
            offered = self.conversation.offer_mini_app(CHAT_ID, self.session)

        assert offered is True
        markup = self.telegram.send_message.call_args.kwargs["reply_markup"]
        url = markup["keyboard"][0][0]["web_app"]["url"]
        assert url == "https://example.test/app?source=bot"

    def test_no_registered_account_can_still_choose_an_operator_in_the_app(self):
        self.storage.get_onboarded_operators.return_value = []
        with (
            patch.object(settings, "MINI_APP_URL", "https://example.test/app"),
            patch.object(settings, "mini_app_enabled", return_value=True),
        ):
            assert self.conversation.offer_mini_app(CHAT_ID, self.session) is True


class TestApplyingMiniAppData(MiniAppConversationFixture):
    def test_valid_submission_logs_in_and_opens_train_selection(self):
        self.account()
        rail = Mock()
        rail.login.return_value = True
        self.conversation._show_train_selection = Mock()

        with patch("korail_bot.handlers.conversation_handler.KorailService", return_value=rail):
            self.conversation.handle_mini_app_data(CHAT_ID, payload())

        assert self.session.last_action == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        assert self.session.credentials.korail_pw == PASSWORD
        assert self.session.train_info["srcLocate"] == "서울"
        self.conversation._show_train_selection.assert_called_once_with(CHAT_ID, self.session)
        assert self.telegram.send_message.call_args.kwargs["reply_markup"] == {
            "remove_keyboard": True
        }

    def test_unregistered_srt_account_is_collected_without_losing_the_app_conditions(self):
        self.storage.get_onboarded_account.return_value = None
        self.conversation._show_train_selection = Mock()
        rail = Mock()
        rail.login.return_value = True

        self.conversation.handle_mini_app_data(CHAT_ID, payload(operator="srt", src_station="수서"))

        self.conversation._show_train_selection.assert_not_called()
        assert self.session.in_progress is True
        assert self.session.last_action == UserProgress.START_ACCEPTED
        assert self.session.train_info["operator"] == "srt"
        assert self.session.train_info["srcLocate"] == "수서"
        assert "등록되어 있지" in self.telegram.send_message.call_args.args[1]

        with patch("korail_bot.handlers.conversation_handler.SrtService", return_value=rail):
            self.conversation.handle_message(CHAT_ID, PHONE)
            self.conversation.handle_message(CHAT_ID, PASSWORD)

        assert self.session.last_action == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        assert self.session.train_info["srcLocate"] == "수서"
        self.conversation._show_train_selection.assert_called_once_with(CHAT_ID, self.session)

    def test_profile_start_parameter_uses_the_same_conversation_path(self):
        self.account(Operator.SRT)
        rail = Mock()
        rail.login.return_value = True
        self.conversation._show_train_selection = Mock()

        with patch("korail_bot.handlers.conversation_handler.SrtService", return_value=rail):
            assert (
                self.conversation.handle_mini_app_start_parameter(CHAT_ID, start_parameter())
                is True
            )

        self.conversation._show_train_selection.assert_called_once_with(CHAT_ID, self.session)

    def test_running_search_cannot_be_replaced(self):
        self.session.last_action = UserProgress.FINDING_TICKET
        before = dict(self.session.train_info)

        self.conversation.handle_mini_app_data(CHAT_ID, payload())

        assert self.session.last_action == UserProgress.FINDING_TICKET
        assert self.session.train_info == before
        assert "이미 취소표" in self.telegram.send_message.call_args.args[1]

    def test_invalid_submission_changes_no_session(self):
        self.session.last_action = UserProgress.DATE_INPUT_SUCCESS

        self.conversation.handle_mini_app_data(CHAT_ID, "bad json")

        assert self.session.last_action == UserProgress.DATE_INPUT_SUCCESS
        assert "사용할 수 없습니다" in self.telegram.send_message.call_args.args[1]


class TestMiniAppUpdateRouting:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.processor = TelegramUpdateProcessor(
            self.storage,
            self.telegram,
            Mock(spec=ReservationService),
            Mock(spec=PaymentReminderService),
        )
        self.processor.conversation_handler = Mock()
        self.processor.command_handler = Mock()
        self.processor.command_handler.claim_developer_mode.return_value = False

    def test_web_app_service_message_does_not_require_text(self):
        raw = payload()
        update = {
            "message": {
                "chat": {"id": CHAT_ID},
                "web_app_data": {"button_text": keyboards.MINI_APP_OPEN, "data": raw},
            }
        }

        self.processor.process(update)

        self.processor.conversation_handler.handle_mini_app_data.assert_called_once_with(
            CHAT_ID, raw
        )

    def test_chat_fallback_button_starts_the_original_flow(self):
        update = {
            "message": {
                "chat": {"id": CHAT_ID},
                "text": keyboards.MINI_APP_CHAT_FALLBACK,
            }
        }

        self.processor.process(update)

        self.processor.command_handler.handle_chat_start.assert_called_once_with(CHAT_ID)


class TestMiniAppStartCommandRouting:
    def test_start_parameter_is_given_to_the_mini_app_conversation(self):
        storage = Mock(spec=StorageInterface)
        telegram = Mock(spec=TelegramService)
        conversation = Mock()
        conversation.handle_mini_app_start_parameter.return_value = True
        handler = CommandHandler(
            storage,
            telegram,
            Mock(spec=ReservationService),
            Mock(spec=PaymentReminderService),
            conversation_handler=conversation,
        )

        assert handler.route_command(CHAT_ID, f"/start {start_parameter()}") is True

        conversation.handle_mini_app_start_parameter.assert_called_once_with(
            CHAT_ID, start_parameter()
        )
        storage.get_user_session.assert_not_called()
