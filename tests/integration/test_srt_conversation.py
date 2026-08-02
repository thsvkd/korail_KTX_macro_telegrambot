"""
Booking an SRT ticket through the conversation, end to end.

The flow is the same one Korail has always used, with three differences that
each have a way of going wrong quietly: the railway is asked first and decides
which account logs in, SR's stations are not Korail's, and the "which kind of
train" question has no answer worth asking for so it is skipped. A skipped
question is the kind of thing that breaks going backwards, so that is covered
here too.

Nothing logs in for real: the two services are patched at the point the
handler builds them.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from SRT import SeatType

from korail_bot.handlers import ConversationHandler
from korail_bot.models import OnboardedAccount, Operator, UserProgress, UserSession
from korail_bot.services import ReservationService, TelegramService

CHAT_ID = 12345
# Inside the window the date step will accept, rather than a far-future date
# that is refused for reasons that have nothing to do with these tests.
FUTURE_DATE = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")


@pytest.fixture
def telegram():
    return Mock(spec=TelegramService)


@pytest.fixture
def handler(storage, telegram):
    return ConversationHandler(storage, telegram, Mock(spec=ReservationService))


@pytest.fixture
def started(storage):
    """A chat that has just said yes to the welcome message."""
    session = UserSession(chat_id=CHAT_ID, in_progress=True, last_action=UserProgress.STARTED)
    storage.save_user_session(session)
    return session


def texts(telegram) -> list[str]:
    return [call[0][1] for call in telegram.send_message.call_args_list]


def logins_succeed():
    """Both clients answer yes, so no request leaves the test."""
    return patch.object(
        ConversationHandler,
        "_rail_service",
        side_effect=lambda chat_id, operator: Mock(
            login=Mock(return_value=True), operator_name=operator.display_name
        ),
    )


def walk_to_stations(handler, operator="srt"):
    """Answer everything up to and including the date."""
    with (
        logins_succeed(),
        patch("korail_bot.config.settings.settings.is_preapproved", return_value=True),
    ):
        handler.handle_message(CHAT_ID, "Y")
        handler.handle_message(CHAT_ID, operator)
        handler.handle_message(CHAT_ID, "010-1234-5678")
        handler.handle_message(CHAT_ID, "password123")
        handler.handle_message(CHAT_ID, FUTURE_DATE)


class TestChoosingTheRailway:
    def test_the_railway_is_asked_before_the_account(self, handler, telegram, started):
        handler.handle_message(CHAT_ID, "Y")

        assert "어느 철도" in texts(telegram)[0]

    def test_choosing_srt_is_remembered_on_the_session(self, handler, storage, started):
        handler.handle_message(CHAT_ID, "Y")
        handler.handle_message(CHAT_ID, "srt")

        session = storage.get_user_session(CHAT_ID)
        assert ConversationHandler.session_operator(session) is Operator.SRT

    def test_the_phone_number_is_asked_for_next(self, handler, telegram, storage, started):
        handler.handle_message(CHAT_ID, "Y")
        handler.handle_message(CHAT_ID, "srt")

        assert "휴대전화번호" in texts(telegram)[-1]
        assert storage.get_user_session(CHAT_ID).last_action == UserProgress.START_ACCEPTED

    def test_an_answer_nobody_recognises_asks_again(self, handler, telegram, storage, started):
        """
        Not defaulted to Korail: this is an answer to a question just asked,
        and booking the wrong railway without a word is worse than asking
        twice.
        """
        handler.handle_message(CHAT_ID, "Y")
        handler.handle_message(CHAT_ID, "신칸센")

        assert storage.get_user_session(CHAT_ID).last_action == UserProgress.OPERATOR_INPUT_PENDING
        assert "코레일 또는 SRT" in texts(telegram)[-1]

    def test_a_registered_srt_account_skips_the_typing(self, handler, storage, telegram, started):
        storage.save_onboarded_account(
            OnboardedAccount(
                chat_id=CHAT_ID,
                korail_id="010-1234-5678",
                korail_pw="pw",
                operator=Operator.SRT,
            )
        )

        with logins_succeed():
            handler.handle_message(CHAT_ID, "Y")
            handler.handle_message(CHAT_ID, "srt")

        assert storage.get_user_session(CHAT_ID).last_action == UserProgress.PW_INPUT_SUCCESS
        assert "휴대전화번호" not in " ".join(texts(telegram))

    def test_a_korail_registration_does_not_log_you_into_sr(
        self, handler, storage, telegram, started
    ):
        """
        The failure this guards against is silent: the bot would log in with
        the Korail account, fail, and blame the user's SR password.
        """
        storage.save_onboarded_account(
            OnboardedAccount(
                chat_id=CHAT_ID,
                korail_id="010-1234-5678",
                korail_pw="pw",
                operator=Operator.KORAIL,
            )
        )

        with logins_succeed():
            handler.handle_message(CHAT_ID, "Y")
            handler.handle_message(CHAT_ID, "srt")

        assert storage.get_user_session(CHAT_ID).last_action == UserProgress.START_ACCEPTED
        assert "휴대전화번호" in texts(telegram)[-1]


class TestSRsStations:
    def test_the_station_buttons_are_srs_own(self, handler, telegram, started):
        walk_to_stations(handler)

        markup = telegram.send_message.call_args.kwargs["reply_markup"]
        labels = [button["text"] for row in markup["inline_keyboard"] for button in row]
        assert "수서" in labels
        assert "서울" not in labels

    def test_a_station_sr_does_not_serve_is_refused(self, handler, telegram, storage, started):
        walk_to_stations(handler)

        handler.handle_message(CHAT_ID, "서울")

        assert storage.get_user_session(CHAT_ID).last_action == UserProgress.DATE_INPUT_SUCCESS
        assert "서지 않는 역" in texts(telegram)[-1]

    def test_an_srt_station_is_accepted(self, handler, storage, started):
        walk_to_stations(handler)

        handler.handle_message(CHAT_ID, "수서")

        session = storage.get_user_session(CHAT_ID)
        assert session.last_action == UserProgress.SRC_LOCATE_INPUT_SUCCESS
        assert session.train_info["srcLocate"] == "수서"

    def test_korail_still_takes_korail_stations(self, handler, storage, started):
        """The check must not have narrowed the railway that has always worked."""
        walk_to_stations(handler, operator="korail")

        handler.handle_message(CHAT_ID, "서울")

        assert (
            storage.get_user_session(CHAT_ID).last_action == UserProgress.SRC_LOCATE_INPUT_SUCCESS
        )


class TestTheTrainTypeQuestion:
    def walk_to_the_time_window(self, handler):
        walk_to_stations(handler)
        handler.handle_message(CHAT_ID, "수서")
        handler.handle_message(CHAT_ID, "부산")
        handler.handle_message(CHAT_ID, "0900")
        handler.handle_message(CHAT_ID, "1800")

    def test_sr_is_not_asked_which_kind_of_train(self, handler, telegram, storage, started):
        self.walk_to_the_time_window(handler)

        assert "열차 종류" not in texts(telegram)[-1]
        assert "좌석" in texts(telegram)[-1]
        assert (
            storage.get_user_session(CHAT_ID).last_action == UserProgress.TRAIN_TYPE_INPUT_SUCCESS
        )

    def test_the_skipped_answer_is_filled_in_rather_than_left_empty(
        self, handler, storage, started
    ):
        """The summary and /status read it back."""
        self.walk_to_the_time_window(handler)

        info = storage.get_user_session(CHAT_ID).train_info
        assert info["trainType"] == "SRT"
        assert info["trainTypeShow"] == "SRT"

    def test_going_back_from_the_seat_option_skips_it_too(
        self, handler, telegram, storage, started
    ):
        """
        Landing on a question the user has never seen would make "뒤로" look
        like it had gone somewhere at random. Back from the seat option lands
        on the departure cutoff, which is the last thing an SRT search was
        actually asked.
        """
        self.walk_to_the_time_window(handler)

        handler.handle_message(CHAT_ID, "*back")

        session = storage.get_user_session(CHAT_ID)
        assert session.last_action == UserProgress.DEP_TIME_INPUT_SUCCESS
        assert "열차 종류" not in texts(telegram)[-1]
        assert "종료" in texts(telegram)[-1]

    def test_korail_is_still_asked(self, handler, telegram, storage, started):
        walk_to_stations(handler, operator="korail")
        handler.handle_message(CHAT_ID, "서울")
        handler.handle_message(CHAT_ID, "부산")
        handler.handle_message(CHAT_ID, "0900")
        handler.handle_message(CHAT_ID, "1800")

        assert "열차 종류" in texts(telegram)[-1]
        assert (
            storage.get_user_session(CHAT_ID).last_action == UserProgress.MAX_DEP_TIME_INPUT_SUCCESS
        )


class TestWhatTheSearchIsGiven:
    def test_the_search_carries_the_railway(self, handler, storage, started):
        """
        The one thing the search process cannot work out for itself, and the
        difference between watching SRT 313 and a KTX of the same number.
        """
        walk_to_stations(handler)
        handler.handle_message(CHAT_ID, "수서")
        handler.handle_message(CHAT_ID, "부산")
        handler.handle_message(CHAT_ID, "0900")
        handler.handle_message(CHAT_ID, "1800")
        handler.handle_message(CHAT_ID, "1")  # 일반실 우선

        session = storage.get_user_session(CHAT_ID)
        params = handler._build_search_params(session)

        assert params.rail_operator is Operator.SRT
        assert params.validate() == (True, None)

    def test_the_summary_says_which_railway(self, handler, telegram, storage, started):
        walk_to_stations(handler)
        session = storage.get_user_session(CHAT_ID)
        session.train_info.update(
            {
                "srcLocate": "수서",
                "dstLocate": "부산",
                "depTime": "090000",
                "maxDepTime": "1800",
                "trainTypeShow": "SRT",
                "specialInfoShow": "일반실 우선",
                "passengerCount": 1,
                "seatStrategyShow": "1명",
            }
        )
        storage.save_user_session(session)

        handler._show_final_confirmation(CHAT_ID, session)

        assert "SRT" in texts(telegram)[-1]


class TestSeatOptionsCrossOver:
    """
    korail2 and SR spell the four seat preferences identically in two enums
    that know nothing of each other, so the stored answer means the same thing
    on either railway.
    """

    @pytest.mark.parametrize(
        ("answer", "expected"),
        [
            ("1", SeatType.GENERAL_FIRST),
            ("2", SeatType.GENERAL_ONLY),
            ("3", SeatType.SPECIAL_FIRST),
            ("4", SeatType.SPECIAL_ONLY),
        ],
    )
    def test_each_answer_survives_the_trip_to_sr(self, handler, storage, started, answer, expected):
        from korail_bot.telegramBot.telebotBackProcess import BackgroundReservationProcess

        walk_to_stations(handler)
        handler.handle_message(CHAT_ID, "수서")
        handler.handle_message(CHAT_ID, "부산")
        handler.handle_message(CHAT_ID, "0900")
        handler.handle_message(CHAT_ID, "1800")
        handler.handle_message(CHAT_ID, answer)

        stored = storage.get_user_session(CHAT_ID).train_info["specialInfo"]

        # What the search process does with it at the other end.
        process = object.__new__(BackgroundReservationProcess)
        process.operator = Operator.SRT
        assert process._parse_reserve_option(stored) is expected
