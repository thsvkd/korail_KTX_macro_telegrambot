"""
The service that asks Korail for trains and takes one.

The pacing, the failure tracking and the payment check each have a file of
their own. This covers what is left, which is most of the actual work: logging
in and staying logged in, narrowing a search result down to what the user
asked for, reading Korail's answers to a reservation attempt, and the two
loops that run for hours doing it.

None of it touches a network - korail2's client is a stand-in throughout, and
the waits between requests are stubbed out. What is being pinned down is how
this service reads the answers it gets, because every one of them arrives as
either an exception or a string.
"""

from unittest.mock import Mock, patch

import pytest
from korail2 import NoResultsError, ReserveOption, SoldOutError

from korail_bot.services.korail_service import (
    DuplicateReservationError,
    KorailService,
    SearchUnavailableError,
)

USERNAME = "010-1234-5678"
PASSWORD = "korail-password"


class NeedToLoginError(Exception):
    """
    korail2's session-expiry error, by the only property this code reads.

    Matched on the class name rather than the type: korail2 has moved it
    between modules, and a search that stops re-authenticating stops finding
    seats an hour in without saying why.
    """


class FakeTrain:
    """A korail2 train, as far as this service reads one."""

    def __init__(self, train_no="101", departs="0944"):
        self.train_no = train_no
        self.departs = departs

    def __str__(self):
        return f"[KTX] 8월 15일, 서울~부산({self.departs[:2]}:{self.departs[2:]}~12:50), 특실"


def logged_in_service(**kwargs):
    """A service that believes it is logged in, with a stubbed-out client."""
    service = KorailService(**kwargs)
    service._logged_in = True
    service._korail_instance = Mock()
    service._username = USERNAME
    service._password = PASSWORD
    return service


class TestLoggingIn:
    """The one thing every search has to do before it can do anything."""

    def test_a_successful_login_is_remembered(self):
        service = KorailService()
        client = Mock()
        client.login.return_value = True

        with patch.object(service, "_build_client", return_value=client):
            assert service.login(USERNAME, PASSWORD) is True

        assert service.is_logged_in is True

    def test_the_credentials_are_kept_so_the_session_can_be_renewed(self):
        """
        A search runs for hours and Korail expires a session long before it
        finishes. Without these it would have to end where the session did.
        """
        service = KorailService()
        client = Mock()
        client.login.return_value = True

        with patch.object(service, "_build_client", return_value=client):
            service.login(USERNAME, PASSWORD)

        assert (service._username, service._password) == (USERNAME, PASSWORD)

    def test_a_refused_login_is_not_remembered(self):
        """
        Storing the credentials anyway would have the refresh retrying a
        password Korail has already said no to, every half hour, for hours.
        """
        service = KorailService()
        client = Mock()
        client.login.return_value = False

        with patch.object(service, "_build_client", return_value=client):
            assert service.login(USERNAME, PASSWORD) is False

        assert service._username is None

    def test_korail_being_unreachable_reads_as_a_failed_login(self):
        service = KorailService()

        with patch.object(service, "_build_client", side_effect=ConnectionError("no route")):
            assert service.login(USERNAME, PASSWORD) is False

    def test_the_password_is_never_logged(self, caplog):
        service = KorailService()

        with (
            caplog.at_level("DEBUG"),
            patch.object(service, "_build_client", side_effect=Exception),
        ):
            service.login(USERNAME, PASSWORD)

        assert PASSWORD not in caplog.text
        assert USERNAME not in caplog.text


class TestRenewingTheSession:
    """Staying logged in for the hours a search takes."""

    def make(self, login_result=True, error=None):
        service = logged_in_service()
        client = Mock()
        if error is not None:
            client.login.side_effect = error
        else:
            client.login.return_value = login_result
        self.build = patch.object(service, "_build_client", return_value=client)
        return service

    def test_a_renewed_session_is_counted(self):
        service = self.make()

        with self.build:
            assert service._relogin() is True

        assert service._relogin_count == 1

    def test_it_cannot_renew_what_it_never_stored(self):
        service = KorailService()

        assert service._relogin() is False

    def test_a_refused_renewal_leaves_the_service_logged_out(self):
        service = self.make(login_result=False)

        with self.build:
            assert service._relogin() is False

        assert service.is_logged_in is False

    def test_korail_being_unreachable_leaves_it_logged_out_too(self):
        service = self.make(error=ConnectionError("no route"))

        with self.build:
            assert service._relogin() is False

        assert service.is_logged_in is False

    def test_a_failed_renewal_still_moves_the_deadline(self):
        """
        Otherwise the deadline stays in the past and the next pass of the
        search tries again - a login attempt every second or so, for as long
        as Korail is unreachable. The session is renewed on demand anyway
        when Korail actually rejects it.
        """
        service = self.make(login_result=False)
        service._relogin_interval = 1800

        with self.build, patch("korail_bot.services.korail_service.time.time", return_value=1000.0):
            service._relogin()

        assert service._relogin_due_at > 1000.0

    def test_a_refresh_interval_of_zero_turns_the_refresh_off(self):
        """Leaving the session to be renewed when Korail rejects it."""
        service = self.make()
        service._relogin_interval = 0

        service._schedule_next_relogin()

        assert service._relogin_due_at == 0.0

    def test_the_refresh_happens_once_it_is_due(self):
        service = self.make()
        service._relogin_due_at = 1.0

        with patch.object(service, "_relogin") as relogin:
            service._check_session_refresh()

        relogin.assert_called_once()

    def test_nothing_happens_before_it_is_due(self):
        service = self.make()
        service._relogin_due_at = 0.0

        with patch.object(service, "_relogin") as relogin:
            service._check_session_refresh()

        relogin.assert_not_called()


class TestSearchingForTrains:
    """Turning Korail's answer into a list the loop can act on."""

    def setup_method(self):
        self.service = logged_in_service()

    def found(self, trains):
        self.service._korail_instance.search_train.return_value = trains

    def search(self, **kwargs):
        return self.service.search_trains("20260815", "서울", "부산", **kwargs)

    def test_searching_without_logging_in_is_a_mistake_not_an_empty_result(self):
        """
        An empty list would send the loop round again forever on a service
        that can never answer.
        """
        with pytest.raises(ValueError):
            KorailService().search_trains("20260815", "서울", "부산")

    def test_the_trains_come_back(self):
        self.found([FakeTrain("101"), FakeTrain("105")])

        assert len(self.search()) == 2

    def test_no_trains_is_an_ordinary_answer(self):
        """NoResultsError is how korail2 says the window was empty."""
        self.service._korail_instance.search_train.side_effect = NoResultsError()

        assert self.search() == []

    def test_trains_after_the_users_latest_time_are_dropped(self):
        """
        Korail answers with everything from the start time onwards; the end
        of the window is this bot's, and is the answer the user actually gave.
        """
        self.found([FakeTrain("101", "0944"), FakeTrain("105", "1830")])

        kept = self.search(max_dep_time="1800")

        assert [t.train_no for t in kept] == ["101"]

    def test_the_whole_day_needs_no_filtering(self):
        self.found([FakeTrain("101", "0944"), FakeTrain("105", "2330")])

        assert len(self.search(max_dep_time="2400")) == 2

    def test_a_train_whose_time_cannot_be_read_is_left_out(self):
        """
        Rather than kept: the filter exists because the user said they cannot
        travel after a certain hour, and a train that might be after it is not
        one to book on their behalf.
        """
        unreadable = Mock()
        unreadable.__str__ = Mock(return_value="something else entirely")
        self.found([unreadable])

        assert self.search(max_dep_time="1800") == []

    def test_only_the_chosen_trains_come_back(self):
        self.found([FakeTrain("101"), FakeTrain("105"), FakeTrain("417")])

        kept = self.search(train_numbers=["105", "417"])

        assert [t.train_no for t in kept] == ["105", "417"]

    def test_choosing_no_trains_watches_them_all(self):
        """The behaviour that predates picking trains, and the better odds."""
        self.found([FakeTrain("101"), FakeTrain("105")])

        assert len(self.search(train_numbers=[])) == 2

    def test_a_chosen_train_that_stops_running_simply_stops_appearing(self):
        self.found([FakeTrain("101")])

        assert self.search(train_numbers=["999"]) == []

    def test_sold_out_trains_can_be_asked_for(self):
        """
        For showing the user what runs in the window, where the sold-out ones
        are the whole point. Off for the search loop, which only wants what
        it can reserve.
        """
        self.found([])

        self.search(include_no_seats=True)

        assert self.service._korail_instance.search_train.call_args.kwargs["include_no_seats"]

    def test_an_expired_session_is_renewed_and_the_loop_goes_round_again(self):
        self.service._korail_instance.search_train.side_effect = NeedToLoginError("session gone")

        with patch.object(self.service, "_relogin", return_value=True):
            assert self.search() == []

    def test_a_session_that_cannot_be_renewed_is_raised_rather_than_hidden(self):
        self.service._korail_instance.search_train.side_effect = NeedToLoginError("session gone")

        with (
            patch.object(self.service, "_relogin", return_value=False),
            pytest.raises(NeedToLoginError),
        ):
            self.search()

    @pytest.mark.parametrize(
        "error",
        [ConnectionError("no route"), TimeoutError(), ValueError("bad json")],
        ids=["refused", "timeout", "unreadable"],
    )
    def test_a_request_that_got_no_usable_answer_is_not_an_empty_window(self, error):
        """
        The distinction the whole failure-tracking feature rests on. Returned
        as an empty list, as it used to be, a search that had stopped working
        looked exactly like one where every train was sold out.
        """
        self.service._korail_instance.search_train.side_effect = error

        with pytest.raises(SearchUnavailableError):
            self.search()


class TestReadingADepartureTime:
    """
    Pulled out of the printed form of the train, which is all korail2 offers.

    Station names contain brackets, which is what the last bracket rather
    than the first is for.
    """

    def setup_method(self):
        self.service = KorailService()

    @pytest.mark.parametrize(
        ("printed", "expected"),
        [
            ("[KTX] 4월 8일, 용산~광주송정(09:44~12:50), 특실", 944),
            ("[KTX] 4월 8일, 울산(통도사)~서울(09:44~12:50), 특실", 944),
            ("[KTX] 4월 8일, 서울~부산(23:30~02:10), 일반실", 2330),
        ],
    )
    def test_the_departure_time_is_read_out(self, printed, expected):
        train = Mock()
        train.__str__ = Mock(return_value=printed)

        assert self.service._extract_departure_time(train) == expected

    @pytest.mark.parametrize("printed", ["", "no brackets here", "[KTX] (xx:yy~12:50)"])
    def test_something_unreadable_is_zero_rather_than_a_crash(self, printed):
        """
        Zero, which the filter treats as "leave it out" - a train this bot
        cannot place in time is not one to book for somebody.
        """
        train = Mock()
        train.__str__ = Mock(return_value=printed)

        assert self.service._extract_departure_time(train) == 0


class TestTakingASeat:
    """Reading Korail's answer to a reservation attempt."""

    def setup_method(self):
        self.service = logged_in_service()
        self.train = FakeTrain()

    def reserve(self):
        return self.service.reserve_train(self.train, passenger_count=1)

    def answers(self, value=None, error=None):
        client = self.service._korail_instance
        if error is not None:
            client.reserve.side_effect = error
        else:
            client.reserve.return_value = value

    def test_reserving_without_logging_in_is_a_mistake(self):
        with pytest.raises(ValueError):
            KorailService().reserve_train(self.train)

    def test_a_seat_that_was_there_comes_back(self):
        booked = Mock()
        self.answers(booked)

        assert self.reserve() is booked

    def test_the_seat_class_the_user_asked_for_is_passed_on(self):
        self.answers(Mock())

        self.service.reserve_train(self.train, option=ReserveOption.GENERAL_ONLY)

        assert (
            self.service._korail_instance.reserve.call_args.kwargs["option"]
            == ReserveOption.GENERAL_ONLY
        )

    def test_a_seat_taken_in_the_meantime_is_not_a_failure(self):
        """
        The commonest outcome by far: the search found it a second ago and
        somebody else was quicker. The loop just goes round again.
        """
        self.answers(error=SoldOutError())

        assert self.reserve() is None

    def test_an_empty_answer_is_not_a_failure_either(self):
        self.answers(None)

        assert self.reserve() is None

    @pytest.mark.parametrize(
        "message",
        ["동일한 예약 내역이 존재합니다", "WRR800029: duplicate"],
        ids=["korean", "code"],
    )
    def test_an_existing_reservation_is_told_apart_from_a_failure(self, message):
        """
        Not an error to report and stop on: the user has a reservation they
        can cancel from their phone in a few seconds, and the search should
        still be running when they do.
        """
        self.answers(error=Exception(message))

        assert self.reserve() == "DUPLICATE"

    def test_an_expired_session_is_renewed_and_the_loop_goes_round_again(self):
        self.answers(error=NeedToLoginError("session gone"))

        with patch.object(self.service, "_relogin", return_value=True):
            assert self.reserve() is None

    def test_a_session_that_cannot_be_renewed_is_raised(self):
        self.answers(error=NeedToLoginError("session gone"))

        with (
            patch.object(self.service, "_relogin", return_value=False),
            pytest.raises(NeedToLoginError),
        ):
            self.reserve()

    def test_anything_else_costs_this_attempt_and_not_the_search(self):
        """
        There are hours of attempts left. Whatever this was, the next train
        in the list is worth trying.
        """
        self.answers(error=RuntimeError("something Korail said"))

        assert self.reserve() is None


class LoopFixture:
    """A search loop with the searching and reserving replaced."""

    def setup_method(self):
        self.service = logged_in_service()
        self.service.wait_between_requests = Mock(return_value=0.0)
        self.service.wait_seconds = Mock(return_value=0.0)
        self.trains = [FakeTrain("101")]

    def run_loop(self, results, reservations, **kwargs):
        with (
            patch.object(self.service, "search_trains", side_effect=results),
            patch.object(self.service, "reserve_train", side_effect=reservations),
        ):
            return self.service.search_and_reserve_loop(
                "20260815", "서울", "부산", passenger_count=kwargs.pop("passengers", 1), **kwargs
            )


class TestTheConsecutiveLoop(LoopFixture):
    """Seats together, which is what most people want and Korail's default."""

    def test_searching_without_logging_in_is_a_mistake(self):
        with pytest.raises(ValueError):
            KorailService().search_and_reserve_loop("20260815", "서울", "부산")

    def test_it_stops_when_it_has_booked_something(self):
        booked = Mock()

        assert self.run_loop([self.trains], [booked]) is booked

    def test_an_empty_window_sends_it_round_again(self):
        booked = Mock()

        assert self.run_loop([[], [], self.trains], [booked]) is booked
        assert self.service.wait_between_requests.call_count == 2

    def test_korail_not_answering_backs_the_search_off(self):
        booked = Mock()

        with patch.object(self.service, "note_search_failure", return_value=4.0) as noted:
            result = self.run_loop([SearchUnavailableError("timeout"), self.trains], [booked])

        assert result is booked
        noted.assert_called_once()
        self.service.wait_between_requests.assert_called_once_with(4.0)

    def test_a_sold_out_train_does_not_stop_the_pass(self):
        booked = Mock()
        self.trains = [FakeTrain("101"), FakeTrain("105")]

        assert self.run_loop([self.trains], [None, booked]) is booked

    def test_an_existing_reservation_is_raised_so_the_user_hears_once(self):
        with pytest.raises(DuplicateReservationError):
            self.run_loop([self.trains], ["DUPLICATE"])

    def test_giving_up_is_possible_but_never_asked_for(self):
        """
        max_attempts exists for tests and for nothing else - the bot's
        searches run until they succeed or the user cancels.
        """
        assert self.run_loop([[], []], [], max_attempts=1) is None

    def test_the_clock_starts_when_the_loop_does(self):
        """
        Not when the service was built. A progress report saying "3시간째"
        is about the search, and the service is built during login.
        """
        with patch.object(self.service, "begin_search") as began:
            self.run_loop([self.trains], [Mock()])

        began.assert_called_once()


class TestTheRandomLoop(LoopFixture):
    """
    Seats apart, one at a time, for a group that would rather travel than sit
    together.
    """

    def run_random(self, results, reservations, **kwargs):
        return self.run_loop(results, reservations, seat_strategy="random", **kwargs)

    def test_it_keeps_going_until_it_has_them_all(self):
        first, second = Mock(), Mock()

        result = self.run_random([self.trains, self.trains], [first, second], passengers=2)

        assert result is first

    def test_the_whole_set_is_hung_off_the_one_it_returns(self):
        """
        The caller needs every reservation - one payment deadline each - and
        the return type predates there being more than one.
        """
        first, second = Mock(), Mock()

        result = self.run_random([self.trains, self.trains], [first, second], passengers=2)

        assert result._all_reservations == [first, second]
        assert result._is_random_allocation is True
        assert result._total_seats == 2

    def test_it_asks_for_one_seat_at_a_time(self):
        """Asking for two would be asking for them together."""
        with (
            patch.object(self.service, "search_trains", return_value=self.trains) as search,
            patch.object(self.service, "reserve_train", return_value=Mock()),
        ):
            self.service.search_and_reserve_loop(
                "20260815", "서울", "부산", passenger_count=3, seat_strategy="random"
            )

        assert search.call_args.kwargs["passenger_count"] == 1

    def test_an_existing_reservation_is_raised_here_too(self):
        with pytest.raises(DuplicateReservationError):
            self.run_random([self.trains], ["DUPLICATE"], passengers=2)

    def test_korail_not_answering_backs_this_loop_off_as_well(self):
        with patch.object(self.service, "note_search_failure", return_value=2.0):
            self.run_random([SearchUnavailableError("timeout"), self.trains], [Mock()])

        self.service.wait_between_requests.assert_any_call(2.0)

    def test_giving_up_part_way_lets_go_of_what_it_had(self):
        """
        Half a group's seats are worth less than nothing: they are money
        owed on a journey nobody can take together.
        """
        with patch.object(self.service, "_cancel_reservations") as cancelled:
            result = self.run_random([self.trains, []], [Mock()], passengers=2, max_attempts=2)

        assert result is None
        cancelled.assert_called_once()

    def test_letting_go_of_nothing_is_not_worth_a_call_to_korail(self):
        self.service._cancel_reservations([])  # must not raise

    def test_letting_go_survives_korail_refusing(self):
        """
        Reached while giving up on a search that has already gone wrong. It
        cannot be the thing that raises.
        """
        broken = Mock()
        broken.__str__ = Mock(side_effect=Exception("nope"))

        self.service._cancel_reservations([broken])  # must not raise
