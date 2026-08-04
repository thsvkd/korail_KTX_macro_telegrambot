"""
Unit tests for a session that reaches a login-dependent step without a login.

The session carries the Korail account from the login step through to the
search. It can arrive without one - a record written before a field existed,
a stored copy that could not be read back, a flow picked up from a draft -
and every one of those used to end the same way: an AttributeError inside the
handler, no reply, and a user left looking at a question they had answered.

What replaces it differs by step, and the difference is the point. At the
password step the number the password belongs to is what went missing, so it
is asked for again; filling it in from a registration would attach the typed
password to an id the user never gave here. At the booking step the account
is already known and verified, so the registration is picked up silently.
"""

from unittest.mock import Mock, patch

import pytest

from korail_bot.handlers.conversation_handler import ConversationHandler
from korail_bot.models import OnboardedAccount, Operator, UserProgress, UserSession
from korail_bot.services import ReservationService, TelegramService
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot.messages import Messages

CHAT_ID = 909090
REGISTERED_PHONE = "010-9876-5432"
REGISTERED_PASSWORD = "registered-pw"


@pytest.fixture
def storage():
    storage = Mock(spec=StorageInterface)
    storage.get_onboarded_account.return_value = None
    storage.get_or_create_app_session_start.return_value = 1700000000
    storage.get_running_reservation.return_value = None
    storage.get_dead_search.return_value = None
    return storage


@pytest.fixture
def handler(storage):
    return ConversationHandler(storage, Mock(spec=TelegramService), Mock(spec=ReservationService))


def _sent(handler):
    """Every message body the handler sent, in order."""
    return [call[0][1] for call in handler.telegram.send_message.call_args_list]


def _login(succeeds: bool):
    """Patch the Korail login so no request leaves the test."""
    korail = Mock()
    korail.login.return_value = succeeds
    return patch("korail_bot.handlers.conversation_handler.KorailService", return_value=korail)


def _confirmed_session():
    """A session at the last question, with its credentials gone."""
    session = UserSession(
        chat_id=CHAT_ID,
        in_progress=True,
        last_action=UserProgress.SEAT_STRATEGY_INPUT_SUCCESS,
    )
    session.credentials = None
    session.train_info = {
        "operator": str(Operator.KORAIL),
        "depDate": "20991231",
        "srcLocate": "서울",
        "dstLocate": "부산",
        "depTime": "090000",
        "maxDepTime": "1800",
        "trainType": "TrainType.KTX",
        "trainTypeShow": "KTX",
        "specialInfo": "ReserveOption.GENERAL_FIRST",
        "specialInfoShow": "GENERAL_FIRST",
        "passengerCount": 1,
        "seatStrategy": "consecutive",
        "seatStrategyShow": "연속 좌석",
    }
    return session


class TestThePasswordStep:
    """The number the password belongs to is what is missing."""

    def test_the_phone_number_is_asked_for_again(self, handler):
        session = UserSession(
            chat_id=CHAT_ID, in_progress=True, last_action=UserProgress.ID_INPUT_SUCCESS
        )
        session.credentials = None

        with _login(True) as korail:
            handler._handle_password_input(CHAT_ID, "some-password", session)

        assert Messages.ASK_AGAIN_PHONE in _sent(handler)
        # Nothing is logged in with a password whose owner is unknown.
        korail.return_value.login.assert_not_called()

    def test_the_session_goes_back_to_the_number_question(self, handler):
        session = UserSession(
            chat_id=CHAT_ID, in_progress=True, last_action=UserProgress.ID_INPUT_SUCCESS
        )
        session.credentials = None

        with _login(True):
            handler._handle_password_input(CHAT_ID, "some-password", session)

        assert session.last_action == UserProgress.START_ACCEPTED

    def test_a_registration_is_not_borrowed(self, handler, storage):
        """The registered account is a different answer to a different question."""
        storage.get_onboarded_account.return_value = OnboardedAccount(
            chat_id=CHAT_ID,
            korail_id=REGISTERED_PHONE,
            korail_pw=REGISTERED_PASSWORD,
        )
        session = UserSession(
            chat_id=CHAT_ID, in_progress=True, last_action=UserProgress.ID_INPUT_SUCCESS
        )
        session.credentials = None

        with _login(True):
            handler._handle_password_input(CHAT_ID, "some-password", session)

        assert Messages.ASK_AGAIN_PHONE in _sent(handler)
        assert session.credentials is None


class TestTheBookingStep:
    """The account is already known, so the booking continues."""

    def test_the_registration_is_picked_up(self, handler, storage):
        storage.get_onboarded_account.return_value = OnboardedAccount(
            chat_id=CHAT_ID,
            korail_id=REGISTERED_PHONE,
            korail_pw=REGISTERED_PASSWORD,
        )
        session = _confirmed_session()
        handler.reservation.start_reservation_process.return_value = True

        with _login(True):
            handler._start_reservation(CHAT_ID, session)

        handler.reservation.start_reservation_process.assert_called_once()
        started = handler.reservation.start_reservation_process.call_args.kwargs
        assert started["username"] == REGISTERED_PHONE
        assert started["password"] == REGISTERED_PASSWORD

    def test_no_registration_stops_the_booking_with_a_reply(self, handler):
        session = _confirmed_session()

        handler._start_reservation(CHAT_ID, session)

        handler.reservation.start_reservation_process.assert_not_called()
        assert Messages.NO_CREDENTIALS in _sent(handler)

    def test_a_stale_registration_is_not_reported_twice(self, handler, storage):
        """
        A registration that no longer logs in is announced by the code that
        tried it, and the booking then stops without a second explanation.
        """
        storage.get_onboarded_account.return_value = OnboardedAccount(
            chat_id=CHAT_ID,
            korail_id=REGISTERED_PHONE,
            korail_pw=REGISTERED_PASSWORD,
        )
        session = _confirmed_session()

        with _login(False):
            handler._start_reservation(CHAT_ID, session)

        handler.reservation.start_reservation_process.assert_not_called()
        assert Messages.NO_CREDENTIALS not in _sent(handler)
