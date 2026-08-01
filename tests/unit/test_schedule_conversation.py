"""
Booking a search to start later, and asking to be let in.

Two ends of the same conversation, both reached from the summary screen and
neither on the path a test of the ordinary flow walks.

The scheduling one has a property worth stating: a search booked for 6 a.m.
tomorrow has to log in at 6 a.m. tomorrow, and by then the process that would
have been handed the password no longer exists. So the credentials go into
Redis with the schedule, and a session that has none cannot be scheduled at
all - which is a refusal that has to happen here, at the point of asking,
rather than silently at six in the morning.

The access one is about not leaving a dead end. Someone who used up their
trial and liked the bot should be one button away from asking for more, not
reading an instruction to contact a stranger somehow.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from korail_bot.handlers.conversation_handler import ConversationHandler
from korail_bot.models import (
    AccessRequest,
    OnboardedAccount,
    UserCredentials,
    UserProgress,
    UserSession,
)
from korail_bot.services import ReservationService, TelegramService
from korail_bot.services.access_service import AccessDecision, AccessLevel, AccessService
from korail_bot.services.scheduled_search_service import ScheduleError
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot.messages import Messages

MODULE = "korail_bot.services.scheduled_search_service"
CHAT_ID = 12345
USERNAME = "010-1234-5678"
PASSWORD = "korail-password"
OPERATOR = 6824596577


def answered_session(with_credentials=True):
    """A session that has answered every question the summary is built from."""
    session = UserSession(chat_id=CHAT_ID, in_progress=True)
    session.last_action = UserProgress.SCHEDULE_INPUT_PENDING
    session.train_info = {
        "depDate": "20260815",
        "srcLocate": "서울",
        "dstLocate": "부산",
        "depTime": "090000",
        "maxDepTime": "1800",
        "trainType": "TrainType.KTX",
        "trainTypeShow": "KTX 계열만",
        "specialInfo": "ReserveOption.GENERAL_FIRST",
        "specialInfoShow": "일반실 우선",
        "passengerCount": 1,
        "seatStrategy": "consecutive",
    }
    if with_credentials:
        session.credentials = UserCredentials(korail_id=USERNAME, korail_pw=PASSWORD)
    return session


class ScheduleFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.reservation = Mock(spec=ReservationService)
        self.access = Mock(spec=AccessService)
        self.handler = ConversationHandler(
            self.storage, self.telegram, self.reservation, self.access
        )
        self.scheduler = Mock()
        self.session = answered_session()

    def answer(self, text):
        with patch.object(self.handler, "_scheduler", return_value=self.scheduler):
            self.handler._handle_schedule_input(CHAT_ID, text, self.session)

    def replied(self):
        return self.telegram.send_message.call_args.args[1]

    def keyboard(self):
        return self.telegram.send_message.call_args.kwargs.get("reply_markup")


class TestAskingWhenToStart(ScheduleFixture):
    """The prompt, which is where the conversation waits."""

    def test_the_question_carries_the_search_it_is_about(self):
        self.handler._show_schedule_prompt(CHAT_ID, self.session)

        assert "서울" in self.replied() and "부산" in self.replied()
        assert "20260815" in self.replied()

    def test_the_session_is_left_waiting_on_a_time(self):
        """
        Otherwise whatever is typed next falls through to whichever step the
        session last claimed to be on.
        """
        self.session.last_action = UserProgress.STARTED

        self.handler._show_schedule_prompt(CHAT_ID, self.session)

        assert self.session.last_action == UserProgress.SCHEDULE_INPUT_PENDING
        self.storage.save_user_session.assert_called_once_with(self.session)

    def test_some_times_are_offered_rather_than_only_typed(self):
        self.handler._show_schedule_prompt(CHAT_ID, self.session)

        assert self.keyboard()


class TestReadingTheAnswer(ScheduleFixture):
    """What comes back, whether pressed or typed."""

    def test_a_time_it_cannot_read_is_said_back_rather_than_guessed_at(self):
        self.answer("아무때나")

        assert "아무때나" in self.replied()
        self.scheduler.schedule.assert_not_called()

    def test_the_question_stays_open_after_an_unreadable_answer(self):
        """The user is still on this step and still has to answer it."""
        self.answer("아무때나")

        assert self.keyboard()

    def test_a_time_it_can_read_is_booked(self):
        self.answer("202608150600")

        self.scheduler.schedule.assert_called_once()
        assert self.scheduler.schedule.call_args.kwargs["start_at"] == datetime(2026, 8, 15, 6, 0)


class TestBookingIt(ScheduleFixture):
    """Storing the search against its start time."""

    def schedule(self, start_at=None):
        with patch.object(self.handler, "_scheduler", return_value=self.scheduler):
            self.handler._schedule_reservation(
                CHAT_ID, self.session, start_at or (datetime.now() + timedelta(hours=2))
            )

    def test_the_search_the_user_described_is_what_gets_booked(self):
        self.schedule()

        params = self.scheduler.schedule.call_args.kwargs["search_params"]
        assert (params.src_locate, params.dst_locate) == ("서울", "부산")
        assert params.dep_date == "20260815"

    def test_the_credentials_go_with_it(self):
        """
        The search starts at six in the morning, when the process that would
        have been handed the password does not exist yet.
        """
        self.schedule()

        assert self.scheduler.schedule.call_args.kwargs["username"] == USERNAME
        assert self.scheduler.schedule.call_args.kwargs["password"] == PASSWORD

    def test_a_session_with_no_credentials_is_refused_at_the_point_of_asking(self):
        """
        Rather than at six in the morning, silently. Starting now would still
        work - that path hands the password straight to the process - which
        is why this is refused here and not at the summary.
        """
        self.session.credentials = None

        self.schedule()

        assert self.replied() == Messages.SCHEDULE_NO_CREDENTIALS
        self.scheduler.schedule.assert_not_called()

    def test_a_time_the_scheduler_will_not_take_says_why(self):
        """
        Each of these has something specific to say - too soon, after the
        train has left - and the user is still on the step.
        """
        self.scheduler.validate_start_time.side_effect = ScheduleError("이미 지난 시각입니다")

        self.schedule()

        assert self.replied() == "이미 지난 시각입니다"
        assert self.keyboard()
        self.scheduler.schedule.assert_not_called()

    def test_a_booking_that_could_not_be_stored_is_reported_as_failed(self):
        """
        Silence here would leave the user believing a search will start
        tomorrow morning that nothing is going to start.
        """
        self.scheduler.schedule.side_effect = Exception("redis is down")

        self.schedule()

        assert self.replied() == Messages.ERROR_RESERVATION_START_FAILED

    def test_a_booked_search_is_confirmed_with_when_and_what(self):
        self.schedule(datetime(2026, 8, 15, 6, 0))

        assert "08월 15일 06:00" in self.replied()
        assert "서울" in self.replied()

    def test_the_conversation_ends_and_takes_the_password_with_it(self):
        """
        The schedule now holds its own copy in Redis. Leaving one on the
        session keeps a second at rest for however long the wait is.
        """
        self.schedule()

        assert self.session.in_progress is False
        assert self.storage.save_user_session.call_args.args[0] is self.session


class AccessFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_all_developers.return_value = [OPERATOR]
        self.storage.get_access_request.return_value = None
        self.telegram = Mock(spec=TelegramService)
        self.access = Mock(spec=AccessService)
        self.handler = ConversationHandler(
            self.storage, self.telegram, Mock(spec=ReservationService), self.access
        )
        self.session = answered_session()

    def replied(self):
        return self.telegram.send_message.call_args.args[1]


class TestRunningOutOfTrials(AccessFixture):
    """
    The wall, and the door beside it.

    A search is a process asking Korail for seats every few seconds for
    hours, so there is a limit. Hitting it must not be a dead end.
    """

    def exhausted(self):
        self.handler._offer_access_request(
            CHAT_ID,
            self.session,
            USERNAME,
            AccessDecision(level=AccessLevel.EXHAUSTED, used=3, limit=3),
        )

    def test_the_user_is_told_how_many_they_used(self):
        self.exhausted()

        assert "3/3" in self.replied()

    def test_asking_the_operator_is_one_button_away(self):
        self.exhausted()

        assert self.telegram.send_message.call_args.kwargs["reply_markup"]

    def test_the_conversation_is_put_down_rather_than_left_half_open(self):
        """
        There is nothing left to answer, and a session still claiming to be
        mid-booking would take the next message as an answer.
        """
        self.exhausted()

        assert self.session.in_progress is False


class TestAskingToBeLetIn(AccessFixture):
    """The button, and what it sets in motion."""

    def account(self):
        self.storage.get_onboarded_account.return_value = OnboardedAccount(
            chat_id=CHAT_ID, korail_id=USERNAME, korail_pw="stored"
        )

    def test_the_request_is_filed_against_the_registered_account(self):
        """
        Not the session, which has been reset by the time this is pressed -
        and the account is the right source anyway: it is the Korail account
        being asked about.
        """
        self.account()
        self.access.request_access.return_value = AccessRequest(
            phone_hash="abc", chat_id=CHAT_ID, masked_phone="010-****-5678"
        )

        self.handler.request_access(CHAT_ID)

        self.access.request_access.assert_called_once_with(CHAT_ID, USERNAME)

    def test_the_operators_are_told_somebody_is_waiting(self):
        """
        All of them: an operator who is asleep should not be the reason a
        request sits unanswered.
        """
        self.account()
        self.access.request_access.return_value = AccessRequest(
            phone_hash="abc", chat_id=CHAT_ID, masked_phone="010-****-5678"
        )

        self.handler.request_access(CHAT_ID)

        assert self.telegram.send_to_multiple.call_args.args[0] == [OPERATOR]
        assert "010-****-5678" in self.telegram.send_to_multiple.call_args.args[1]

    def test_asking_twice_says_the_first_one_is_still_waiting(self):
        self.account()
        self.access.request_access.return_value = None

        self.handler.request_access(CHAT_ID)

        assert self.replied() == Messages.ACCESS_REQUEST_ALREADY
        self.telegram.send_to_multiple.assert_not_called()

    def test_asking_without_a_registered_account_says_so(self):
        self.storage.get_onboarded_account.return_value = None

        self.handler.request_access(CHAT_ID)

        assert self.replied() == Messages.ACCESS_REQUEST_NO_ACCOUNT
        self.access.request_access.assert_not_called()

    def test_a_request_with_nobody_to_notify_still_reaches_the_user(self):
        """
        No chat is in developer mode. The request is filed and will be seen
        whenever somebody claims the mode; telling the user it failed would
        be wrong.
        """
        self.account()
        self.storage.get_all_developers.return_value = []
        self.access.request_access.return_value = AccessRequest(
            phone_hash="abc", chat_id=CHAT_ID, masked_phone="010-****-5678"
        )

        self.handler.request_access(CHAT_ID)

        assert self.replied() == Messages.ACCESS_REQUEST_SENT
        self.telegram.send_to_multiple.assert_not_called()


@pytest.mark.parametrize(
    ("used", "limit"),
    [(0, 3), (2, 3), (3, 3)],
)
def test_the_remaining_count_never_goes_negative(used, limit):
    """It is shown to the user, and "-1회 남음" is not a thing to show."""
    assert AccessDecision(level=AccessLevel.TRIAL, used=used, limit=limit).remaining >= 0
