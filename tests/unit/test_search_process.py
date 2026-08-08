"""
The process that actually books the ticket.

Everything else in this bot is a conversation about a search; this is the
search. It runs as a child process with no terminal and no user watching it,
and when it gets something wrong the user does not find out from an error
message - they find out by not having a seat.

Which made it, by some distance, the least covered thing here. These tests
build one without ever spawning it: the real process reads its parameters off
argv and its Korail password off stdin, so both are handed to it directly, and
Korail, Telegram and Redis are all stand-ins. Nothing here reaches a network.
"""

import io
import json
import signal
import sys
from unittest.mock import Mock, patch

import pytest
import requests
from korail2 import ReserveOption, TrainType

from korail_bot.config.settings import settings
from korail_bot.models import Operator, ReservationPaymentStatus, SeatPreference
from korail_bot.services.korail_service import (
    DuplicateReservationError,
    KorailService,
    SearchUnavailableError,
)
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot.telebotBackProcess import (
    BackgroundReservationProcess,
    SearchStopped,
)

MODULE = "korail_bot.telegramBot.telebotBackProcess"
CHAT_ID = 12345
USERNAME = "010-1234-5678"
PASSWORD = "korail-password"

#: The nine arguments every search is started with, in order.
ARGV = [
    "telebotBackProcess.py",
    "20260815",  # dep_date
    "서울",  # src_locate
    "부산",  # dst_locate
    "090000",  # dep_time
    "TrainType.KTX",
    "ReserveOption.GENERAL_FIRST",
    str(CHAT_ID),
    "1800",  # max_dep_time
]

CREDENTIALS = json.dumps({"username": USERNAME, "password": PASSWORD}) + "\n"


class FakeReservation:
    """
    A korail2 reservation, as far as this process reads one.

    Not a Mock: the success path asks whether the reservation carries
    `_is_random_allocation`, and a Mock answers yes to every attribute there
    is, so every single-seat booking would be mistaken for a random one.
    """

    def __init__(self, rsv_id="320260815120000", **extra):
        self.rsv_id = rsv_id
        self.__dict__.update(extra)

    def __str__(self):
        return f"[KTX] 8월 15일, 서울~부산(09:00~11:40) {self.__dict__.get('rsv_id', '')}"


def build(argv=None, credentials=CREDENTIALS):
    """
    Construct a search process the way the bot starts one.

    Every collaborator is replaced: the real __init__ opens Redis, builds a
    Telegram client and a Korail client, none of which a test wants.
    """
    with (
        patch.object(sys, "argv", argv if argv is not None else ARGV),
        patch.object(sys, "stdin", io.StringIO(credentials)),
        patch(f"{MODULE}.RedisStorage") as storage,
        patch(f"{MODULE}.TelegramService"),
        patch(f"{MODULE}.PaymentReminderService"),
        patch(f"{MODULE}.MultiReservationReminderService"),
        patch(f"{MODULE}.KorailService"),
    ):
        storage.return_value.is_debug_mode.return_value = False
        return BackgroundReservationProcess()


class TestCredentialsOnStdin:
    """
    How the Korail password reaches the process.

    On stdin rather than on argv, because argv is readable by every process
    on the host - `ps` would print a user's Korail password to anyone logged
    into the same machine.
    """

    def test_the_credentials_are_read_from_the_first_line(self):
        process = build()

        assert (process.username, process.password) == (USERNAME, PASSWORD)

    def test_nothing_on_stdin_stops_the_process(self):
        """
        How this looks in practice: somebody ran the module by hand. There is
        no prompt to fall back to and no way to search without logging in.
        """
        with pytest.raises(SystemExit) as exited:
            build(credentials="")

        assert exited.value.code == 1

    @pytest.mark.parametrize(
        "line",
        [
            "not json at all\n",
            '{"username": "010-1234-5678"}\n',
            "[]\n",
            '{"username": "", "password": "pw"}\n',
            '{"username": "010-1234-5678", "password": ""}\n',
        ],
        ids=["garbage", "no-password", "not-an-object", "blank-user", "blank-password"],
    )
    def test_a_payload_that_cannot_be_used_stops_the_process(self, line):
        with pytest.raises(SystemExit):
            build(credentials=line)

    def test_the_password_is_never_logged(self, caplog):
        """
        The startup banner prints every parameter it was given. The username
        goes through mask_phone; the password is not in it at all.
        """
        with caplog.at_level("DEBUG"):
            build()

        assert PASSWORD not in caplog.text
        assert USERNAME not in caplog.text


class TestTheParametersItIsGiven:
    """What arrives on argv, and what it becomes."""

    def test_too_few_arguments_stops_the_process(self):
        with pytest.raises(SystemExit) as exited:
            build(argv=ARGV[:5])

        assert exited.value.code == 1

    def test_the_search_reads_back_what_it_was_given(self):
        process = build()

        assert process.dep_date == "20260815"
        assert process.src_locate == "서울"
        assert process.dst_locate == "부산"
        assert process.dep_time == "090000"
        assert process.max_dep_time == "1800"

    def test_the_chat_id_becomes_a_number(self):
        """
        Every storage call and the Telegram client want an int. It arrives as
        text because argv is text, and the places that got away with the
        string only did so by interpolating it into a key or a URL.
        """
        process = build()

        assert process.chat_id == CHAT_ID
        assert isinstance(process.chat_id, int)

    def test_a_search_without_the_optional_arguments_books_one_seat_together(self):
        """
        This is what a search started by an older build looks like, and it has
        to keep meaning what it meant then.
        """
        process = build()

        assert process.passenger_count == 1
        assert process.seat_strategy == "consecutive"
        assert process.train_numbers == []

    def test_the_chosen_trains_are_read_off_the_end(self):
        process = build(argv=[*ARGV, "2", "random", "101,105,417"])

        assert process.passenger_count == 2
        assert process.seat_strategy == "random"
        assert process.train_numbers == ["101", "105", "417"]

    def test_an_empty_train_list_means_the_whole_window(self):
        """
        The behaviour that predates picking trains at all, and the one a
        trailing comma must not turn into a watch on a train called "".
        """
        process = build(argv=[*ARGV, "1", "consecutive", ""])

        assert process.train_numbers == []


class TestReadingTheTrainType:
    """
    argv carries whatever str() made of the enum, and has for several builds.

    Getting this wrong is quiet and expensive: a search meant for KTX that
    reads as ALL will happily book a 무궁화호 and tell the user it succeeded.
    """

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("TrainType.KTX", TrainType.KTX),
            ("TrainType.ALL", TrainType.ALL),
            ("100", TrainType.KTX),
            ("0", TrainType.ALL),
            ("KTX", TrainType.KTX),
            ("ktx 계열만", TrainType.KTX),
            ("모든 열차", TrainType.ALL),
            ("", TrainType.ALL),
        ],
    )
    def test_it_reads_every_spelling_that_has_shipped(self, given, expected):
        process = object.__new__(BackgroundReservationProcess)
        process.operator = Operator.KORAIL

        assert process._parse_train_type(given) == expected


class TestReadingTheSeatOption:
    """The same problem for the seat class."""

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("ReserveOption.GENERAL_FIRST", ReserveOption.GENERAL_FIRST),
            ("ReserveOption.GENERAL_ONLY", ReserveOption.GENERAL_ONLY),
            ("ReserveOption.SPECIAL_FIRST", ReserveOption.SPECIAL_FIRST),
            ("ReserveOption.SPECIAL_ONLY", ReserveOption.SPECIAL_ONLY),
            ("general_only", ReserveOption.GENERAL_ONLY),
            ("special_first", ReserveOption.SPECIAL_FIRST),
            ("special_only", ReserveOption.SPECIAL_ONLY),
            ("general_first", ReserveOption.GENERAL_FIRST),
            ("무엇이든", ReserveOption.GENERAL_FIRST),
        ],
    )
    def test_it_reads_every_spelling_that_has_shipped(self, given, expected):
        process = object.__new__(BackgroundReservationProcess)
        process.operator = Operator.KORAIL

        assert process._parse_reserve_option(given) == expected

    def test_the_fallback_is_the_least_surprising_one(self):
        """
        An unreadable option must not silently upgrade someone to 특실, which
        they would find out about by being charged for it.
        """
        process = object.__new__(BackgroundReservationProcess)
        process.operator = Operator.KORAIL

        assert process._parse_reserve_option("???") == ReserveOption.GENERAL_FIRST


class ProcessFixture:
    """
    A search process with its collaborators replaced.

    Built without __init__ on purpose: what is being tested is what the
    process does with a Korail answer, not how it came to be started.
    """

    def setup_method(self):
        self.process = object.__new__(BackgroundReservationProcess)
        self.process.operator = Operator.KORAIL
        self.process.username = USERNAME
        self.process.password = PASSWORD
        self.process.chat_id = CHAT_ID
        self.process.dep_date = "20260815"
        self.process.src_locate = "서울"
        self.process.dst_locate = "부산"
        self.process.dep_time = "090000"
        self.process.max_dep_time = "1800"
        self.process.train_type = TrainType.KTX
        self.process.reserve_option = ReserveOption.GENERAL_FIRST
        self.process.passenger_count = 1
        self.process.seat_strategy = "consecutive"
        self.process.train_numbers = []
        self.process.seat_preference = SeatPreference()
        self.process.rail = Mock()
        # Reading a reservation is not faked: which field holds the
        # reservation number, and which the payment deadline, is the
        # operator-specific translation this process depends on, and a Mock
        # would let a wrong reading through.
        self.process.rail.reservation_id = KorailService.reservation_id
        self.process.rail.payment_due = KorailService.payment_due
        self.process.telegram = Mock()
        self.process.storage = Mock(spec=StorageInterface)
        self.process.payment_reminder = Mock()
        self.process.multi_reminder = Mock()


class RunFixture(ProcessFixture):
    """The top-level run(), with the two things it hands off to stubbed."""

    def run_process(self):
        with (
            patch.object(BackgroundReservationProcess, "_send_callback") as callback,
            patch.object(BackgroundReservationProcess, "_watch_payment") as watch,
            patch.object(BackgroundReservationProcess, "_run_random_reservation") as random_run,
        ):
            self.process.run()

        self.callback = callback
        self.watch = watch
        self.random_run = random_run

    def statuses(self):
        return [call.kwargs["status"] for call in self.callback.call_args_list]

    def messages(self):
        return [call.args[0] for call in self.callback.call_args_list]


class TestRunEndsSomewhere(RunFixture):
    """
    Every way the search can finish, and the callback it finishes with.

    The callback is not decoration - it is what clears the search's record in
    Redis. A path that ends without one leaves the bot telling the user their
    search is still running when the process behind it is gone.
    """

    def test_a_failed_login_is_reported_and_the_search_stops(self):
        self.process.rail.login.return_value = False

        self.run_process()

        assert self.statuses() == [1]
        assert "로그인 실패" in self.messages()[0]
        self.process.rail.search_and_reserve_loop.assert_not_called()

    def test_a_booked_seat_is_reported_as_success(self):
        self.process.rail.login.return_value = True
        self.process.rail.search_and_reserve_loop.return_value = FakeReservation()

        self.run_process()

        assert self.statuses() == [0]
        assert "예약에 성공" in self.messages()[0]

    def test_a_booked_seat_is_then_watched_until_it_is_paid_for(self):
        """
        This process holds the only logged-in Korail session there is - the
        main app deletes the stored credentials the moment the callback lands
        - so it is the only thing that can find out whether the payment
        actually happened.
        """
        self.process.rail.login.return_value = True
        self.process.rail.search_and_reserve_loop.return_value = FakeReservation()

        self.run_process()

        self.watch.assert_called_once()

    def test_the_success_message_says_the_payment_window_is_short(self):
        self.process.rail.login.return_value = True
        self.process.rail.search_and_reserve_loop.return_value = FakeReservation()

        self.run_process()

        assert str(settings.PAYMENT_TIMEOUT_MINUTES) in self.messages()[0]
        assert settings.KORAIL_PAYMENT_URL in self.messages()[0]

    def test_a_loop_that_returns_nothing_still_reports_in(self):
        """
        Should not happen - the loop runs until it books something. If it
        ever does, silence would leave the user waiting on a dead process.
        """
        self.process.rail.login.return_value = True
        self.process.rail.search_and_reserve_loop.return_value = None

        self.run_process()

        assert len(self.callback.call_args_list) == 1
        self.watch.assert_not_called()

    def test_random_seating_goes_down_its_own_path(self):
        self.process.rail.login.return_value = True
        self.process.seat_strategy = "random"

        self.run_process()

        self.random_run.assert_called_once()
        self.process.rail.search_and_reserve_loop.assert_not_called()

    def test_being_told_to_stop_is_not_an_error_report(self):
        """
        run() turns everything it catches into an error message to the user.
        Being asked to stop is not one: whoever sent the signal already knows
        the search is over, and SearchStopped is a BaseException so that it
        travels through untouched.
        """
        self.process.rail.login.side_effect = SearchStopped(signal.SIGTERM)

        with pytest.raises(SearchStopped):
            self.run_process()


class TestRunSurvivesKorail(RunFixture):
    """What the user is told when the search cannot be carried out."""

    def setup_method(self):
        super().setup_method()
        self.process.rail.login.return_value = True

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (requests.exceptions.ConnectionError("no route"), "네트워크 오류"),
            (ValueError("서울역 같은 역은 없습니다"), "입력 데이터 오류"),
            (RuntimeError("무언가"), "예상치 못한 오류"),
        ],
        ids=["network", "bad-data", "unexpected"],
    )
    def test_a_failure_is_named_rather_than_reported_as_a_generic_error(self, error, expected):
        """
        The user's next move is different in each case - check the connection,
        fix the station name, or report it - and they can only pick one if
        they are told which happened.
        """
        self.process.rail.search_and_reserve_loop.side_effect = error

        self.run_process()

        assert self.statuses() == [1]
        assert expected in self.messages()[0]

    def test_every_failure_message_says_what_to_do_next(self):
        self.process.rail.search_and_reserve_loop.side_effect = RuntimeError("무언가")

        self.run_process()

        assert "/cancel" in self.messages()[0]


class TestAnExistingReservationIsNotTheEnd(RunFixture):
    """
    Korail refuses a second reservation on the same train.

    The user usually has one they forgot about, and cancelling it is a thing
    they can do from their phone in a few seconds. So this is told once and
    the search keeps going, rather than ending and making them start over.
    """

    def setup_method(self):
        super().setup_method()
        self.process.rail.login.return_value = True

    def test_the_user_is_told_and_the_search_carries_on(self):
        booked = FakeReservation()
        self.process.rail.search_and_reserve_loop.side_effect = [
            DuplicateReservationError("동일한 예약 내역이 존재합니다"),
            booked,
        ]

        self.run_process()

        assert self.statuses() == [2, 0]
        assert "기존 예약 감지" in self.messages()[0]
        assert self.process.rail.search_and_reserve_loop.call_count == 2

    def test_the_notice_says_the_search_has_not_stopped(self):
        """Otherwise it reads as a failure and the user starts over."""
        self.process.rail.search_and_reserve_loop.side_effect = [
            DuplicateReservationError("동일한 예약 내역이 존재합니다"),
            FakeReservation(),
        ]

        self.run_process()

        assert "계속 검색" in self.messages()[0]

    def test_a_second_duplicate_does_not_take_the_process_down(self):
        """Should not happen, having been reported once. It must not crash."""
        self.process.rail.search_and_reserve_loop.side_effect = [
            DuplicateReservationError("first"),
            DuplicateReservationError("again"),
        ]

        self.run_process()

        assert self.statuses() == [2, 0]


class TestSeveralSeatsAtOnce(RunFixture):
    """
    Random allocation books one seat at a time and hands back the first one,
    with the rest hung off it as attributes.
    """

    def setup_method(self):
        super().setup_method()
        self.process.rail.login.return_value = True
        self.process.passenger_count = 3
        seats = [FakeReservation("1"), FakeReservation("2"), FakeReservation("3")]
        self.booked = FakeReservation(
            "1",
            _is_random_allocation=True,
            _total_seats=3,
            _all_reservations=seats,
        )
        self.process.rail.search_and_reserve_loop.return_value = self.booked

    def test_every_seat_is_named_in_the_message(self):
        with patch.object(BackgroundReservationProcess, "_create_multi_reservation_status"):
            self.run_process()

        message = self.messages()[0]
        assert "좌석 1" in message and "좌석 3" in message

    def test_the_callback_says_it_was_a_multi_booking(self):
        """
        The main app starts a different kind of reminder for these - one
        deadline per seat rather than one for the lot.
        """
        with patch.object(BackgroundReservationProcess, "_create_multi_reservation_status"):
            self.run_process()

        assert self.callback.call_args.kwargs["is_multi"] is True
        assert self.callback.call_args.kwargs["total_seats"] == 3

    def test_the_seats_are_recorded_for_the_reminder_service(self):
        with patch.object(BackgroundReservationProcess, "_create_multi_reservation_status") as made:
            self.run_process()

        made.assert_called_once()

    def test_a_reminder_that_could_not_be_set_up_does_not_lose_the_booking(self):
        """
        The seats are reserved. Failing here costs the user their reminders,
        which is worth a log line and nothing more - it is certainly not
        worth withholding the news that they got the tickets.
        """
        with patch.object(
            BackgroundReservationProcess,
            "_create_multi_reservation_status",
            side_effect=Exception("redis is down"),
        ):
            self.run_process()

        assert self.statuses() == [0]

    def test_the_payment_watch_stays_out_of_it(self):
        """
        The watch follows one reservation. Several deadlines at once are the
        multi-reminder service's job.
        """
        with patch.object(BackgroundReservationProcess, "_create_multi_reservation_status"):
            self.run_process()

        self.watch.assert_not_called()


class TestReportingBackToTheApp(ProcessFixture):
    """
    The callback, which is how a finished search stops being a running one.

    Over loopback HTTP because the search is a separate process: it shares no
    memory with the app, and the app is the only thing holding the Telegram
    conversation.
    """

    def send(self, *args, **kwargs):
        with patch(f"{MODULE}.requests.session") as session:
            session.return_value.get.return_value = Mock(status_code=200, text="ok")
            self.process._send_callback(*args, **kwargs)
            return session.return_value.get

    def test_the_result_reaches_the_app(self):
        get = self.send("예약 성공", status=0, is_multi=False, total_seats=1)

        params = get.call_args.kwargs["params"]
        assert params["chatId"] == CHAT_ID
        assert params["msg"] == "예약 성공"
        assert params["status"] == 0
        assert params["isMulti"] == "0"
        assert params["totalSeats"] == "1"

    def test_it_proves_it_came_from_inside(self):
        """
        /reservation-callback is reachable over loopback and takes a chat id and a message.
        Without the token any process on the host could send a user anything
        under the bot's name.
        """
        get = self.send("무언가", status=0)

        assert get.call_args.kwargs["params"]["token"] == settings.INTERNAL_CALLBACK_TOKEN

    def test_a_multi_booking_is_marked_as_one(self):
        get = self.send("무언가", status=0, is_multi=True, total_seats=3)

        params = get.call_args.kwargs["params"]
        assert params["isMulti"] == "1"
        assert params["totalSeats"] == "3"

    @pytest.mark.parametrize(
        "outcome",
        [
            Mock(status_code=500, text="server error"),
            requests.exceptions.Timeout(),
            requests.exceptions.ConnectionError("app is down"),
            RuntimeError("something else"),
        ],
        ids=["non-200", "timeout", "refused", "unexpected"],
    )
    def test_the_app_not_answering_does_not_crash_the_search(self, outcome):
        """
        This is often the last thing the process does, and by now the seat is
        already booked. A traceback here would replace an orderly exit with a
        crash and change nothing about the reservation.
        """
        with patch(f"{MODULE}.requests.session") as session:
            if isinstance(outcome, Exception):
                session.return_value.get.side_effect = outcome
            else:
                session.return_value.get.return_value = outcome

            self.process._send_callback("무언가", status=0)  # must not raise


class TestBookingOneSeatAtATime(ProcessFixture):
    """
    Random allocation: search, book one, wait for it to be paid for, repeat.

    Sequential because Korail will not hold two unpaid reservations for the
    same person on the same train, so the second seat cannot even be asked
    for until the first is settled.
    """

    def setup_method(self):
        super().setup_method()
        self.process.seat_strategy = "random"
        self.process.passenger_count = 2
        self.process.storage.get_partial_reservations.return_value = [
            {"train_info": "[KTX] 좌석 1"},
            {"train_info": "[KTX] 좌석 2"},
        ]
        self.process.storage.wait_for_payment.return_value = True

    def run_random(self, seats=None):
        seats = seats if seats is not None else [FakeReservation("1"), FakeReservation("2")]
        with (
            patch.object(BackgroundReservationProcess, "_send_callback") as callback,
            patch.object(
                BackgroundReservationProcess, "_reserve_single_seat_random", side_effect=seats
            ) as reserve,
            patch.object(BackgroundReservationProcess, "_update_multi_reservation_status"),
            patch(f"{MODULE}.time.sleep"),
        ):
            self.process._run_random_reservation()

        self.callback = callback
        self.reserve = reserve

    def statuses(self):
        return [call.kwargs.get("status") for call in self.callback.call_args_list]

    def test_each_seat_is_booked_in_turn(self):
        self.run_random()

        assert self.reserve.call_count == 2

    def test_each_seat_is_written_down_as_it_is_booked(self):
        """
        The seats are booked minutes apart. Keeping them only in memory means
        a process that dies on the second one loses the first, which the user
        has already been asked to pay for.
        """
        self.run_random()

        assert self.process.storage.save_partial_reservation.call_count == 2

    def test_the_user_hears_about_each_seat_as_it_lands(self):
        self.run_random()

        assert self.statuses() == [2, 2, 2, 0]

    def test_the_last_seat_is_not_waited_on(self):
        """There is nothing after it to hold up."""
        self.run_random()

        self.process.storage.wait_for_payment.assert_called_once()

    def test_a_seat_left_unpaid_does_not_hold_up_the_rest(self):
        """
        Ten minutes is already a long time to make somebody wait, and the
        remaining seats are worth more than a tidy sequence.
        """
        self.process.storage.wait_for_payment.return_value = False

        self.run_random()

        assert self.statuses() == [2, 2, 2, 0]
        # Right after the first seat's own message, and before the second is
        # even looked for.
        assert "시간 초과" in self.callback.call_args_list[1].args[0]

    def test_the_seat_being_paid_for_is_only_named_once_it_exists(self):
        """
        Set after the booking, not before: the main app reads it to decide
        whether the user is mid-payment, and setting it early produced a
        "결제 대기중" for a seat that had not been reserved yet.
        """
        self.run_random()

        assert self.process.storage.set_current_seat_index.call_args_list[0].args[1] == 0
        assert self.process.storage.set_current_seat_index.call_args_list[-1].args[1] is None

    def test_a_seat_that_cannot_be_booked_ends_the_run(self):
        self.run_random(seats=[FakeReservation("1"), RuntimeError("korail said no")])

        assert self.statuses()[-1] == 1
        assert "실패" in self.callback.call_args.args[0]

    def test_a_search_that_gives_up_without_an_error_ends_the_run_too(self):
        self.run_random(seats=[None])

        assert self.statuses() == [1]

    def test_the_final_message_lists_every_seat(self):
        self.run_random()

        assert "좌석 1" in self.callback.call_args.args[0]
        assert "좌석 2" in self.callback.call_args.args[0]


class TestSearchingForOneSeat(ProcessFixture):
    """
    The inner loop of random allocation.

    Its own loop rather than KorailService's, because it has to stop after
    exactly one seat and hand control back for the payment wait.
    """

    def setup_method(self):
        super().setup_method()
        self.train = Mock()
        self.process.rail.note_search_failure.return_value = 2.0

    def test_a_seat_that_is_there_is_taken(self):
        booked = FakeReservation()
        self.process.rail.search_trains.return_value = [self.train]
        self.process.rail.reserve_train.return_value = booked

        assert self.process._reserve_single_seat_random(0) is booked

    def test_it_asks_for_one_seat_however_many_the_user_wants(self):
        """The whole point of random allocation is that they are booked apart."""
        self.process.passenger_count = 4
        self.process.rail.search_trains.return_value = [self.train]
        self.process.rail.reserve_train.return_value = FakeReservation()

        self.process._reserve_single_seat_random(0)

        assert self.process.rail.search_trains.call_args.kwargs["passenger_count"] == 1
        assert self.process.rail.reserve_train.call_args.kwargs["passenger_count"] == 1

    def test_no_trains_means_look_again(self):
        booked = FakeReservation()
        self.process.rail.search_trains.side_effect = [[], [], [self.train]]
        self.process.rail.reserve_train.return_value = booked

        assert self.process._reserve_single_seat_random(0) is booked
        assert self.process.rail.wait_between_requests.call_count == 2

    def test_korail_not_answering_backs_the_search_off(self):
        """
        Rather than retrying at full rate against a door that is not opening,
        which is what made a blocked search look exactly like a busy one.
        """
        booked = FakeReservation()
        self.process.rail.search_trains.side_effect = [
            SearchUnavailableError("timeout"),
            [self.train],
        ]
        self.process.rail.reserve_train.return_value = booked

        self.process._reserve_single_seat_random(0)

        self.process.rail.note_search_failure.assert_called_once()
        self.process.rail.wait_between_requests.assert_called_once_with(2.0)

    def test_a_sold_out_train_is_no_reason_to_stop(self):
        booked = FakeReservation()
        self.process.rail.search_trains.return_value = [self.train, self.train]
        self.process.rail.reserve_train.side_effect = [None, booked]

        assert self.process._reserve_single_seat_random(0) is booked

    def test_an_existing_reservation_is_reported_once_and_only_once(self):
        """
        It is retried every ten seconds until the user cancels the old one,
        and a message on every pass would be a message every ten seconds.
        """
        booked = FakeReservation()
        self.process.rail.search_trains.return_value = [self.train]
        self.process.rail.reserve_train.side_effect = [
            "DUPLICATE",
            "DUPLICATE",
            "DUPLICATE",
            booked,
        ]

        self.process._reserve_single_seat_random(0)

        assert self.process.telegram.send_message.call_count == 1
        assert "기존 예약" in self.process.telegram.send_message.call_args.args[1]

    def test_an_existing_reservation_slows_the_retries_right_down(self):
        """
        Nothing will change until a person cancels something on their phone,
        so asking once a second is asking a few hundred times for nothing.
        """
        self.process.rail.search_trains.return_value = [self.train]
        self.process.rail.reserve_train.side_effect = ["DUPLICATE", FakeReservation()]

        self.process._reserve_single_seat_random(0)

        self.process.rail.wait_seconds.assert_called_once_with(10)

    def test_the_progress_report_is_offered_on_every_pass(self):
        """
        The throttle is the process's business, not the loop's - the loop
        just says how it is going.
        """
        self.process.rail.search_trains.side_effect = [[], [], [self.train]]
        self.process.rail.reserve_train.return_value = FakeReservation()

        self.process._reserve_single_seat_random(0)

        assert self.process.rail.report_progress.call_count == 3


class TestRecordingSeveralSeats(ProcessFixture):
    """
    What the multi-seat reminder service reads.

    Written by this process and read by the app, so it goes through Redis.
    """

    def setup_method(self):
        super().setup_method()
        self.process.seat_strategy = "random"
        self.process.storage.get_multi_reservation_status.return_value = None

    def saved(self):
        return self.process.storage.save_multi_reservation_status.call_args.args[0]

    def test_the_first_seat_starts_a_fresh_record(self):
        self.process._update_multi_reservation_status(0, FakeReservation("11"), total_seats=3)

        status = self.saved()
        assert status.chat_id == CHAT_ID
        assert status.total_seats == 3
        assert [r.seat_number for r in status.reservations] == [1]
        assert status.reservations[0].status == ReservationPaymentStatus.PENDING

    def test_a_stale_record_from_an_earlier_booking_is_cleared_first(self):
        """
        Otherwise the second booking of the day inherits the first one's
        seats and reminds the user about tickets they already used.
        """
        self.process.storage.get_multi_reservation_status.return_value = Mock()

        self.process._update_multi_reservation_status(0, FakeReservation("11"), total_seats=2)

        self.process.storage.delete_multi_reservation_status.assert_called_once_with(CHAT_ID)

    def test_later_seats_join_the_record_that_is_there(self):
        self.process._update_multi_reservation_status(0, FakeReservation("11"), total_seats=2)
        existing = self.saved()
        self.process.storage.get_multi_reservation_status.return_value = existing

        self.process._update_multi_reservation_status(1, FakeReservation("22"), total_seats=2)

        assert [r.seat_number for r in self.saved().reservations] == [1, 2]

    def test_a_seat_with_no_number_still_gets_recorded(self):
        """A reminder that cannot name the reservation is better than none."""
        seat = FakeReservation()
        del seat.rsv_id

        self.process._update_multi_reservation_status(0, seat, total_seats=1)

        assert self.saved().reservations[0].reservation_id == "seat_1"

    def test_redis_failing_does_not_lose_the_seat(self):
        """
        The booking already happened. Failing here costs the reminders and
        nothing else, and the user is about to be told they have the ticket.
        """
        self.process.storage.save_multi_reservation_status.side_effect = Exception("redis is down")

        self.process._update_multi_reservation_status(0, FakeReservation(), 1)  # must not raise

    def test_the_legacy_path_records_them_all_at_once(self):
        """
        Used when the whole set comes back from one loop rather than being
        booked one at a time.
        """
        seats = [FakeReservation("1"), FakeReservation("2")]

        self.process._create_multi_reservation_status(seats, total_seats=2)

        assert [r.seat_number for r in self.saved().reservations] == [1, 2]

    def test_the_legacy_path_survives_redis_too(self):
        self.process.storage.save_multi_reservation_status.side_effect = Exception("redis is down")

        self.process._create_multi_reservation_status([FakeReservation()], 1)  # must not raise


class TestTalkingToTheUserDirectly(ProcessFixture):
    """
    News about the search, as opposed to a result for it.

    Straight to Telegram rather than through the callback: every callback
    status means the search has ended one way or another, so routing a "still
    going" message through it would end the search to say it had not.
    """

    def test_a_status_message_goes_to_the_chat(self):
        self.process._announce_search_status("⚠️ 코레일 응답 없음")

        self.process.telegram.send_message.assert_called_once_with(CHAT_ID, "⚠️ 코레일 응답 없음")

    def test_a_partial_booking_names_the_seat_and_the_deadline(self):
        message = self.process._build_partial_reservation_message(1, 3, FakeReservation())

        assert "2/3" in message
        assert str(settings.PAYMENT_TIMEOUT_MINUTES) in message
        assert settings.KORAIL_PAYMENT_URL in message

    def test_the_final_message_survives_a_record_missing_its_train(self):
        """Redis is the only thing that has these, and it is not this process."""
        message = self.process._build_final_random_message([{}, {"train_info": "[KTX] 둘"}], 2)

        assert "N/A" in message
        assert "[KTX] 둘" in message
