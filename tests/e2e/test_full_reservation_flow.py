"""
End-to-end tests for complete reservation flows.

Tests the entire user journey from start to reservation completion.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from korail_bot.handlers import CommandHandler, ConversationHandler
from korail_bot.models import Operator, UserProgress
from korail_bot.services import PaymentReminderService, ReservationService, TelegramService
from korail_bot.storage import RedisStorage
from korail_bot.telegramBot import keyboards


class TestFullReservationFlow:
    """Test complete reservation flow end-to-end."""

    def setup_method(self):
        """Set up test fixtures."""
        self.storage = RedisStorage()
        self.telegram = Mock(spec=TelegramService)
        self.reservation = Mock(spec=ReservationService)
        self.payment_reminder = Mock(spec=PaymentReminderService)

        self.command_handler = CommandHandler(
            self.storage, self.telegram, self.reservation, self.payment_reminder
        )

        self.conversation_handler = ConversationHandler(
            self.storage, self.telegram, self.reservation
        )

    def teardown_method(self):
        """Clean up after each test."""
        self.storage.redis.flushdb()
        self.storage.close()

    @patch("korail_bot.services.korail_service.KorailService.login")
    def test_complete_single_reservation_happy_path(self, mock_login):
        """Test complete single passenger reservation flow."""
        mock_login.return_value = True
        self.reservation.start_reservation_process.return_value = True

        chat_id = 12345
        future_date = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")

        # Step 1: /start command
        self.command_handler.route_command(chat_id, "/start")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.STARTED

        # Step 2: Confirm start (Y)
        self.conversation_handler.handle_message(chat_id, "Y")
        # Which railway now comes between "yes" and the phone number.
        self.conversation_handler.handle_message(chat_id, "korail")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.START_ACCEPTED

        # Step 3: Enter phone number
        with patch("korail_bot.config.settings.settings.is_preapproved", return_value=True):
            self.conversation_handler.handle_message(chat_id, "010-1234-5678")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.ID_INPUT_SUCCESS
        assert session.credentials.korail_id == "010-1234-5678"

        # Step 4: Enter password
        self.conversation_handler.handle_message(chat_id, "password123")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.PW_INPUT_SUCCESS
        assert session.credentials.korail_pw == "password123"

        # Step 5: Enter date
        self.conversation_handler.handle_message(chat_id, future_date)
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.DATE_INPUT_SUCCESS
        assert session.train_info["depDate"] == future_date

        # Step 6: Enter source station
        self.conversation_handler.handle_message(chat_id, "서울")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.SRC_LOCATE_INPUT_SUCCESS
        assert session.train_info["srcLocate"] == "서울"

        # Step 7: Enter destination station
        self.conversation_handler.handle_message(chat_id, "부산")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.DST_LOCATE_INPUT_SUCCESS
        assert session.train_info["dstLocate"] == "부산"

        # Step 8: Enter departure time
        self.conversation_handler.handle_message(chat_id, "0900")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.DEP_TIME_INPUT_SUCCESS
        assert session.train_info["depTime"] == "090000"

        # Step 9: Enter max departure time
        self.conversation_handler.handle_message(chat_id, "1800")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.MAX_DEP_TIME_INPUT_SUCCESS
        assert session.train_info["maxDepTime"] == "1800"

        # Step 10: Select train type (KTX)
        self.conversation_handler.handle_message(chat_id, "1")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.TRAIN_TYPE_INPUT_SUCCESS
        assert session.train_info["trainType"] == "TrainType.KTX"

        # Step 11: Select seat option (GENERAL_FIRST)
        self.conversation_handler.handle_message(chat_id, "1")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.SPECIAL_INPUT_SUCCESS

        # Step 12: Enter passenger count (1)
        self.conversation_handler.handle_message(chat_id, "1")
        session = self.storage.get_user_session(chat_id)
        assert session.train_info["passengerCount"] == 1
        # Single passenger auto-sets consecutive, then the train list is
        # offered. Korail cannot be reached from a test, so the list falls
        # through to the summary with no train picked - which is the same
        # whole-window watch the flow had before trains could be picked.
        assert session.last_action == UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        assert session.train_info["seatStrategy"] == "consecutive"
        assert session.train_info["selectedTrains"] == []

        # Step 13: Final confirmation (Y)
        self.conversation_handler.handle_message(chat_id, "Y")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.FINDING_TICKET

        # Verify reservation started
        self.reservation.start_reservation_process.assert_called_once()
        call_args = self.reservation.start_reservation_process.call_args
        assert call_args[1]["chat_id"] == chat_id
        assert call_args[1]["username"] == "010-1234-5678"
        assert call_args[1]["password"] == "password123"
        assert call_args[1]["search_params"].src_locate == "서울"
        assert call_args[1]["search_params"].dst_locate == "부산"

    @patch("korail_bot.services.korail_service.KorailService.login")
    def test_complete_multi_passenger_consecutive_flow(self, mock_login):
        """Test complete multi-passenger consecutive seating flow."""
        mock_login.return_value = True
        self.reservation.start_reservation_process.return_value = True

        chat_id = 12345
        future_date = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")

        # Go through flow quickly
        self.command_handler.route_command(chat_id, "/start")
        self.conversation_handler.handle_message(chat_id, "Y")
        # Which railway now comes between "yes" and the phone number.
        self.conversation_handler.handle_message(chat_id, "korail")

        with patch("korail_bot.config.settings.settings.is_preapproved", return_value=True):
            self.conversation_handler.handle_message(chat_id, "010-1234-5678")

        self.conversation_handler.handle_message(chat_id, "password123")
        self.conversation_handler.handle_message(chat_id, future_date)
        self.conversation_handler.handle_message(chat_id, "서울")
        self.conversation_handler.handle_message(chat_id, "부산")
        self.conversation_handler.handle_message(chat_id, "0900")
        self.conversation_handler.handle_message(chat_id, "1800")
        self.conversation_handler.handle_message(chat_id, "1")  # KTX
        self.conversation_handler.handle_message(chat_id, "1")  # GENERAL_FIRST

        # Multiple passengers
        self.conversation_handler.handle_message(chat_id, "3")
        session = self.storage.get_user_session(chat_id)
        assert session.train_info["passengerCount"] == 3
        assert session.last_action == UserProgress.PASSENGER_COUNT_INPUT_SUCCESS

        # Select consecutive strategy
        self.conversation_handler.handle_message(chat_id, "1")
        session = self.storage.get_user_session(chat_id)
        assert session.train_info["seatStrategy"] == "consecutive"
        assert session.last_action == UserProgress.TRAIN_SELECT_INPUT_SUCCESS

        # Final confirmation
        self.conversation_handler.handle_message(chat_id, "Y")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.FINDING_TICKET

        # Verify reservation parameters
        call_args = self.reservation.start_reservation_process.call_args
        assert call_args[1]["search_params"].passenger_count == 3
        assert call_args[1]["search_params"].seat_strategy == "consecutive"

    @patch("korail_bot.services.korail_service.KorailService.login")
    def test_complete_multi_passenger_random_flow(self, mock_login):
        """Test complete multi-passenger random seating flow."""
        mock_login.return_value = True
        self.reservation.start_reservation_process.return_value = True

        chat_id = 12345
        future_date = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")

        # Quick flow setup
        self.command_handler.route_command(chat_id, "/start")
        self.conversation_handler.handle_message(chat_id, "Y")
        # Which railway now comes between "yes" and the phone number.
        self.conversation_handler.handle_message(chat_id, "korail")

        with patch("korail_bot.config.settings.settings.is_preapproved", return_value=True):
            self.conversation_handler.handle_message(chat_id, "010-1234-5678")

        self.conversation_handler.handle_message(chat_id, "password123")
        self.conversation_handler.handle_message(chat_id, future_date)
        self.conversation_handler.handle_message(chat_id, "서울")
        self.conversation_handler.handle_message(chat_id, "부산")
        self.conversation_handler.handle_message(chat_id, "0900")
        self.conversation_handler.handle_message(chat_id, "1800")
        self.conversation_handler.handle_message(chat_id, "1")
        self.conversation_handler.handle_message(chat_id, "1")

        # Multiple passengers
        self.conversation_handler.handle_message(chat_id, "5")

        # Select random strategy
        self.conversation_handler.handle_message(chat_id, "2")
        session = self.storage.get_user_session(chat_id)
        assert session.train_info["seatStrategy"] == "random"

        # Final confirmation
        self.conversation_handler.handle_message(chat_id, "Y")

        # Verify reservation parameters
        call_args = self.reservation.start_reservation_process.call_args
        assert call_args[1]["search_params"].passenger_count == 5
        assert call_args[1]["search_params"].seat_strategy == "random"

    @patch("korail_bot.services.korail_service.KorailService.login")
    def test_flow_with_cancellation_mid_way(self, mock_login):
        """Test user cancels in the middle of flow."""
        mock_login.return_value = True

        chat_id = 12345
        future_date = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")

        # Start flow
        self.command_handler.route_command(chat_id, "/start")
        self.conversation_handler.handle_message(chat_id, "Y")
        # Which railway now comes between "yes" and the phone number.
        self.conversation_handler.handle_message(chat_id, "korail")

        with patch("korail_bot.config.settings.settings.is_preapproved", return_value=True):
            self.conversation_handler.handle_message(chat_id, "010-1234-5678")

        self.conversation_handler.handle_message(chat_id, "password123")
        self.conversation_handler.handle_message(chat_id, future_date)

        # User decides to cancel
        self.command_handler.route_command(chat_id, "/cancel")

        # Session should be reset
        session = self.storage.get_user_session(chat_id)
        assert session.in_progress is False
        assert session.last_action == 0

    @patch("korail_bot.services.korail_service.KorailService.login")
    def test_flow_with_login_retry(self, mock_login):
        """Test flow with failed login and retry."""
        # First attempt fails, second succeeds
        mock_login.side_effect = [False, True]

        chat_id = 12345

        # Start flow
        self.command_handler.route_command(chat_id, "/start")
        self.conversation_handler.handle_message(chat_id, "Y")
        # Which railway now comes between "yes" and the phone number.
        self.conversation_handler.handle_message(chat_id, "korail")

        with patch("korail_bot.config.settings.settings.is_preapproved", return_value=True):
            self.conversation_handler.handle_message(chat_id, "010-1234-5678")

        # First password attempt - fails
        self.conversation_handler.handle_message(chat_id, "wrong_password")
        session = self.storage.get_user_session(chat_id)
        # Should stay in same state for retry
        assert session.last_action == UserProgress.ID_INPUT_SUCCESS

        # Retry with correct password
        self.conversation_handler.handle_message(chat_id, "correct_password")
        session = self.storage.get_user_session(chat_id)
        # Should progress
        assert session.last_action == UserProgress.PW_INPUT_SUCCESS

    def test_flow_rejection_at_start(self):
        """Test user rejects at start confirmation."""
        chat_id = 12345

        # Start
        self.command_handler.route_command(chat_id, "/start")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.STARTED

        # Reject
        self.conversation_handler.handle_message(chat_id, "N")
        session = self.storage.get_user_session(chat_id)
        # Should be reset
        assert session.in_progress is False
        assert session.last_action == 0

    def test_flow_rejection_at_final_confirmation(self):
        """Test user rejects at final confirmation."""
        chat_id = 12345

        # Create session at final confirmation stage
        from korail_bot.models import UserCredentials, UserSession

        session = UserSession(
            chat_id=chat_id, in_progress=True, last_action=UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        )
        session.credentials = UserCredentials(korail_id="010-1234-5678", korail_pw="password123")
        session.train_info = {
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
        }
        self.storage.save_user_session(session)

        # Reject
        self.conversation_handler.handle_message(chat_id, "N")
        session = self.storage.get_user_session(chat_id)
        # Should be reset
        assert session.in_progress is False


def fake_srt_train(number, dep="090000", arr="112000", name="SRT", seats=False):
    """A stand-in for an SR train, with only what SrtService.describe_train reads."""
    train = Mock()
    train.train_number = number
    train.dep_time = dep
    train.arr_time = arr
    train.train_name = name
    train.seat_available = Mock(return_value=seats)
    return train


class TestFullSrtReservationFlow:
    """
    The same journey as above, booked with SR instead of Korail.

    The class above walks Korail from /start to a started search. This one
    walks the other railway, because the two flows are not the same shape: SR
    is chosen at a question Korail users answer differently, its stations are
    its own, and the "which kind of train" step does not happen at all. A
    per-step test can say each of those works and still miss that the answers
    stop travelling together somewhere between /start and the search.

    Nothing reaches SR. Both the login and the timetable request are patched,
    the timetable one deliberately: an unpatched search here would put a real
    request to SR from every test run, which is the behaviour SR blocks
    addresses for.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.storage = RedisStorage()
        self.telegram = Mock(spec=TelegramService)
        self.reservation = Mock(spec=ReservationService)
        self.payment_reminder = Mock(spec=PaymentReminderService)

        # The id of the message the train list was sent as, which is kept on
        # the session so a tick can rewrite that message instead of sending
        # the list again. It goes to Redis as JSON, so the default Mock - which
        # does not serialise - would fail the train list rather than the test
        # that is actually about it.
        self.telegram.send_and_get_id.return_value = 4242

        self.command_handler = CommandHandler(
            self.storage, self.telegram, self.reservation, self.payment_reminder
        )

        self.conversation_handler = ConversationHandler(
            self.storage, self.telegram, self.reservation
        )

        self.chat_id = 12345
        self.future_date = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")

    def teardown_method(self):
        """Clean up after each test."""
        self.storage.redis.flushdb()
        self.storage.close()

    def say(self, *messages):
        """Answer the conversation, one message per argument."""
        for message in messages:
            self.conversation_handler.handle_message(self.chat_id, message)

    def session(self):
        return self.storage.get_user_session(self.chat_id)

    def walk_to_the_seat_option(self):
        """
        Everything from /start up to the question after the departure cutoff.

        Which question that is, is the point of
        :meth:`test_sr_is_never_asked_which_kind_of_train` - so this stops
        short of answering it.
        """
        self.command_handler.route_command(self.chat_id, "/start")
        self.say("Y", "srt")
        with patch("korail_bot.config.settings.settings.is_preapproved", return_value=True):
            self.say("010-1234-5678")
        self.say("password123", self.future_date, "수서", "부산", "0900", "1800")

    @patch("korail_bot.services.srt_service.SrtService.search_trains", return_value=[])
    @patch("korail_bot.services.srt_service.SrtService.login", return_value=True)
    def test_complete_srt_reservation_happy_path(self, mock_login, mock_search):
        """A single-passenger SRT booking, from /start to a started search."""
        self.reservation.start_reservation_process.return_value = True

        self.command_handler.route_command(self.chat_id, "/start")
        assert self.session().last_action == UserProgress.STARTED

        # Step 2: confirm, then answer the railway question with SR.
        self.say("Y", "srt")
        session = self.session()
        assert session.last_action == UserProgress.START_ACCEPTED
        assert ConversationHandler.session_operator(session) is Operator.SRT

        # Step 3-4: the SR account. Same two questions, a different login.
        with patch("korail_bot.config.settings.settings.is_preapproved", return_value=True):
            self.say("010-1234-5678")
        assert self.session().last_action == UserProgress.ID_INPUT_SUCCESS

        self.say("password123")
        assert self.session().last_action == UserProgress.PW_INPUT_SUCCESS

        # Step 5-7: date, then two stations SR actually stops at.
        self.say(self.future_date)
        assert self.session().last_action == UserProgress.DATE_INPUT_SUCCESS

        self.say("수서")
        session = self.session()
        assert session.last_action == UserProgress.SRC_LOCATE_INPUT_SUCCESS
        assert session.train_info["srcLocate"] == "수서"

        self.say("부산")
        assert self.session().train_info["dstLocate"] == "부산"

        # Step 8-9: the departure window.
        self.say("0900", "1800")
        session = self.session()
        assert session.train_info["depTime"] == "090000"
        assert session.train_info["maxDepTime"] == "1800"

        # The train type question is skipped, so the cutoff answer lands one
        # step further along than the same answer does on Korail.
        assert session.last_action == UserProgress.TRAIN_TYPE_INPUT_SUCCESS
        assert session.train_info["trainType"] == "SRT"

        # Step 10: the seat option, which is the next thing actually asked.
        self.say("1")
        assert self.session().last_action == UserProgress.SPECIAL_INPUT_SUCCESS

        # Step 11: one passenger, which settles the seating strategy on its
        # own and brings up the seat condition - the one question SR is asked
        # and Korail is not.
        self.say("1")
        session = self.session()
        assert session.train_info["passengerCount"] == 1
        assert session.train_info["seatStrategy"] == "consecutive"
        assert session.last_action == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS

        # Step 12: window seats near the front. Ticking leaves the screen up,
        # so the condition is built over several messages and then finished.
        self.say("A", "D", "1-15", keyboards.SEAT_PREFERENCE_DONE)
        session = self.session()
        assert session.train_info["seatPreference"] == "A,D:1-15"

        # Which offers the train list. SR answered with no trains, so the flow
        # carries on watching the whole window.
        assert session.last_action == UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        assert session.train_info["selectedTrains"] == []

        # Step 12: start the search. Unlike the railway question at the top,
        # nothing is asked between this and the search starting.
        self.say("Y")
        assert self.session().last_action == UserProgress.FINDING_TICKET

        # What the search was actually handed. The operator is the one thing
        # the search process cannot work out for itself.
        self.reservation.start_reservation_process.assert_called_once()
        call_args = self.reservation.start_reservation_process.call_args
        assert call_args[1]["chat_id"] == self.chat_id
        assert call_args[1]["username"] == "010-1234-5678"
        assert call_args[1]["password"] == "password123"

        params = call_args[1]["search_params"]
        assert params.rail_operator is Operator.SRT
        assert params.src_locate == "수서"
        assert params.dst_locate == "부산"
        assert params.seat_preference == "A,D:1-15"
        assert params.validate() == (True, None)

    @patch("korail_bot.services.srt_service.SrtService.search_trains", return_value=[])
    @patch("korail_bot.services.srt_service.SrtService.login", return_value=True)
    @patch("korail_bot.services.korail_service.KorailService.login", return_value=True)
    def test_an_srt_booking_never_logs_into_korail(self, mock_korail, mock_srt, mock_search):
        """
        The failure this guards against is silent and expensive: the bot logs
        in with the wrong company, and the user is told their password is
        wrong for an account that is fine. Both clients are made to succeed
        here precisely so that picking the wrong one would not show up as a
        failure - only as the wrong one having been asked.
        """
        self.walk_to_the_seat_option()
        # Seat option, one passenger, no seat condition, start.
        self.say("1", "1", keyboards.SEAT_PREFERENCE_ANY, "Y")

        assert self.session().last_action == UserProgress.FINDING_TICKET
        assert mock_srt.called
        assert not mock_korail.called

    @patch("korail_bot.services.srt_service.SrtService.search_trains", return_value=[])
    @patch("korail_bot.services.srt_service.SrtService.login", return_value=True)
    def test_sr_is_never_asked_which_kind_of_train(self, mock_login, mock_search):
        """
        SR runs SRT and nothing else, so the question has one answer. It is
        still filled in, because the summary and /status read it back and an
        empty train type there would read as a bug.
        """
        self.walk_to_the_seat_option()

        prompt = self.telegram.send_message.call_args[0][1]
        assert "열차 종류" not in prompt
        assert "좌석" in prompt

        info = self.session().train_info
        assert info["trainType"] == "SRT"
        assert info["trainTypeShow"] == "SRT"

    @patch("korail_bot.services.srt_service.SrtService.search_trains", return_value=[])
    @patch("korail_bot.services.srt_service.SrtService.login", return_value=True)
    def test_complete_srt_multi_passenger_random_flow(self, mock_login, mock_search):
        """Several passengers on SR, seated wherever they fit."""
        self.reservation.start_reservation_process.return_value = True

        self.walk_to_the_seat_option()
        self.say("1")

        # More than one passenger, so the seating strategy is asked rather
        # than settled.
        self.say("4")
        session = self.session()
        assert session.train_info["passengerCount"] == 4
        assert session.last_action == UserProgress.PASSENGER_COUNT_INPUT_SUCCESS

        self.say("2")
        assert self.session().train_info["seatStrategy"] == "random"

        self.say(keyboards.SEAT_PREFERENCE_ANY)
        assert self.session().train_info["seatPreference"] == ""

        self.say("Y")
        assert self.session().last_action == UserProgress.FINDING_TICKET

        params = self.reservation.start_reservation_process.call_args[1]["search_params"]
        assert params.rail_operator is Operator.SRT
        assert params.passenger_count == 4
        assert params.seat_strategy == "random"

    @patch("korail_bot.services.srt_service.SrtService.login", return_value=True)
    def test_srt_trains_can_be_picked_and_reach_the_search(self, mock_login):
        """
        Picking trains is the one step that needs SR to have answered, so it
        is the one place where a list that came back wrong would be noticed.
        The numbers have to survive as far as the search, or the search
        watches the whole window while telling the user it is watching three
        trains.
        """
        self.reservation.start_reservation_process.return_value = True

        offered = [fake_srt_train("301"), fake_srt_train("303"), fake_srt_train("305")]
        with patch(
            "korail_bot.services.srt_service.SrtService.search_trains", return_value=offered
        ):
            self.walk_to_the_seat_option()
            self.say("1", "1", keyboards.SEAT_PREFERENCE_ANY)

            # The list is up: the flow is waiting to be told which of them to
            # watch, rather than having moved past it.
            session = self.session()
            assert [option["no"] for option in session.train_info["trainOptions"]] == [
                "301",
                "303",
                "305",
            ]
            assert session.last_action == UserProgress.SEAT_PREFERENCE_INPUT_SUCCESS

            self.say("301", "305", keyboards.TRAIN_SELECT_DONE)

        session = self.session()
        assert session.last_action == UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        assert session.train_info["selectedTrains"] == ["301", "305"]

        self.say("Y")

        params = self.reservation.start_reservation_process.call_args[1]["search_params"]
        assert params.rail_operator is Operator.SRT
        assert params.train_numbers == ["301", "305"]

    @patch("korail_bot.services.srt_service.SrtService.search_trains", return_value=[])
    @patch("korail_bot.services.srt_service.SrtService.login", return_value=True)
    def test_srt_flow_with_cancellation_mid_way(self, mock_login, mock_search):
        """/cancel gets out of an SRT booking the same way it does a Korail one."""
        self.command_handler.route_command(self.chat_id, "/start")
        self.say("Y", "srt")
        with patch("korail_bot.config.settings.settings.is_preapproved", return_value=True):
            self.say("010-1234-5678")
        self.say("password123", self.future_date)

        self.command_handler.route_command(self.chat_id, "/cancel")

        session = self.session()
        assert session.in_progress is False
        assert session.last_action == 0
        self.reservation.start_reservation_process.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
