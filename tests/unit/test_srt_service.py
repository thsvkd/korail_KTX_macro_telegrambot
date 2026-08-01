"""
Unit tests for the SRT half of the bot.

SR's API differs from Korail's in ways the search loop must not have to know
about, and the whole job of SrtService is to absorb those differences. These
cover the places where absorbing them is not obvious: the departure cutoff
means one thing to the bot and another to SR, a train "available" to SR may
have nothing the user asked for, and every refusal - duplicate, sold out,
expired session, blocked address - arrives as the same exception type with a
different sentence inside it.

They never touch the network: a stand-in client is put on the service and the
service is told it is logged in.
"""

from unittest.mock import Mock

import pytest
from SRT import SeatType
from SRT.errors import SRTLoginError, SRTNotLoggedInError, SRTResponseError

from korail_bot.services.rail_service import SearchUnavailableError
from korail_bot.services.srt_service import IP_BLOCKED_MARKER, SrtBlockedError, SrtService

USERNAME = "010-1234-5678"
PASSWORD = "srt-password"


class FakeTrain:
    """A train as SR describes one, down to the two seat classes."""

    def __init__(self, number, dep_time="080000", general=True, special=True):
        self.train_number = number
        self.dep_time = dep_time
        self.general_seat_state = "예약가능" if general else "매진"
        self.special_seat_state = "예약가능" if special else "매진"

    def general_seat_available(self):
        return "예약가능" in self.general_seat_state

    def special_seat_available(self):
        return "예약가능" in self.special_seat_state

    def seat_available(self):
        return self.general_seat_available() or self.special_seat_available()

    def __str__(self):
        return f"[SRT {self.train_number}]"


class FakeReservation:
    def __init__(self, number, paid=False):
        self.reservation_number = number
        self.paid = paid

    def __str__(self):
        return f"[예약 {self.reservation_number}]"


@pytest.fixture
def service():
    return SrtService()


@pytest.fixture
def logged_in():
    """A service with a stand-in client, already past the login."""
    service = SrtService()
    service._srt_instance = Mock()
    service._logged_in = True
    service._username = USERNAME
    service._password = PASSWORD
    return service


class TestTheDepartureCutoff:
    """
    The bot says HHMM and means "leaving before this". SR takes HHMMSS and
    means "leaving at or before this". Handing SR the bot's number unchanged
    admits a train the user excluded.
    """

    def test_a_cutoff_excludes_the_train_leaving_on_it(self, service):
        assert service._time_limit("1200") == "115959"

    def test_2400_means_there_is_no_cutoff(self, service):
        assert service._time_limit("2400") is None

    def test_an_empty_cutoff_means_there_is_no_cutoff(self, service):
        assert service._time_limit("") is None

    def test_midnight_leaves_nothing_to_catch(self, service):
        assert service._time_limit("0000") == "000000"

    def test_the_first_minute_of_the_day_survives_the_subtraction(self, service):
        assert service._time_limit("0001") == "000059"

    def test_an_hour_boundary_borrows_correctly(self, service):
        assert service._time_limit("0600") == "055959"


class TestWhichSeatsCount:
    """
    SR calls a train available when either class has a seat. A search for
    "일반실만" that stops on a train with only a special seat left would reserve
    something the user said no to - or, more often, fail to reserve at all and
    call it a lost race.
    """

    def test_general_only_ignores_a_train_with_only_special_seats(self, logged_in):
        logged_in._seat_type = SeatType.GENERAL_ONLY
        logged_in._srt_instance.search_train.return_value = [
            FakeTrain("300", general=False, special=True),
            FakeTrain("302", general=True, special=False),
        ]

        found = logged_in.search_trains("20260801", "수서", "부산", verbose=False)

        assert [t.train_number for t in found] == ["302"]

    def test_special_only_ignores_a_train_with_only_general_seats(self, logged_in):
        logged_in._seat_type = SeatType.SPECIAL_ONLY
        logged_in._srt_instance.search_train.return_value = [
            FakeTrain("300", general=False, special=True),
            FakeTrain("302", general=True, special=False),
        ]

        found = logged_in.search_trains("20260801", "수서", "부산", verbose=False)

        assert [t.train_number for t in found] == ["300"]

    def test_a_preference_takes_whichever_is_going(self, logged_in):
        logged_in._seat_type = SeatType.GENERAL_FIRST
        logged_in._srt_instance.search_train.return_value = [
            FakeTrain("300", general=False, special=True),
            FakeTrain("302", general=True, special=False),
            FakeTrain("304", general=False, special=False),
        ]

        found = logged_in.search_trains("20260801", "수서", "부산", verbose=False)

        assert [t.train_number for t in found] == ["300", "302"]

    def test_showing_the_user_the_window_keeps_the_sold_out_ones(self, logged_in):
        """The sold-out trains are the whole point of that list."""
        logged_in._srt_instance.search_train.return_value = [
            FakeTrain("300", general=False, special=False),
            FakeTrain("302", general=True, special=True),
        ]

        found = logged_in.search_trains(
            "20260801", "수서", "부산", verbose=False, include_no_seats=True
        )

        assert [t.train_number for t in found] == ["300", "302"]

    def test_sold_out_trains_are_dropped_for_the_search_loop(self, logged_in):
        logged_in._srt_instance.search_train.return_value = [
            FakeTrain("300", general=False, special=False),
            FakeTrain("302", general=True, special=True),
        ]

        found = logged_in.search_trains("20260801", "수서", "부산", verbose=False)

        assert [t.train_number for t in found] == ["302"]


class TestWatchingChosenTrains:
    def test_only_the_chosen_trains_come_back(self, logged_in):
        logged_in._srt_instance.search_train.return_value = [
            FakeTrain("300"),
            FakeTrain("302"),
            FakeTrain("304"),
        ]

        found = logged_in.search_trains(
            "20260801", "수서", "부산", verbose=False, train_numbers=["302", "304"]
        )

        assert [t.train_number for t in found] == ["302", "304"]

    def test_no_chosen_trains_means_the_whole_window(self, logged_in):
        logged_in._srt_instance.search_train.return_value = [FakeTrain("300"), FakeTrain("302")]

        found = logged_in.search_trains("20260801", "수서", "부산", verbose=False, train_numbers=[])

        assert len(found) == 2

    def test_a_chosen_train_that_stopped_running_simply_stops_appearing(self, logged_in):
        logged_in._srt_instance.search_train.return_value = [FakeTrain("300")]

        found = logged_in.search_trains(
            "20260801", "수서", "부산", verbose=False, train_numbers=["999"]
        )

        assert found == []


class TestSearchingBeforeLogin:
    def test_searching_without_a_session_is_a_programming_error(self, service):
        with pytest.raises(ValueError):
            service.search_trains("20260801", "수서", "부산")


class TestWhenSRWillNotAnswer:
    """
    "No trains" and "no answer" both leave the loop with nothing to reserve.
    Run together, a blocked search looks exactly like a sold-out one.
    """

    def test_a_refusal_is_not_an_empty_result(self, logged_in):
        logged_in._srt_instance.search_train.side_effect = SRTResponseError("서비스 점검 중")

        with pytest.raises(SearchUnavailableError):
            logged_in.search_trains("20260801", "수서", "부산", verbose=False)

    def test_an_unreadable_answer_is_not_an_empty_result(self, logged_in):
        logged_in._srt_instance.search_train.side_effect = ValueError("nonsense")

        with pytest.raises(SearchUnavailableError):
            logged_in.search_trains("20260801", "수서", "부산", verbose=False)

    def test_an_expired_session_is_renewed_and_the_pass_yields_nothing(self, logged_in):
        """Not a failure: the next pass of the loop asks again on a fresh session."""
        logged_in._srt_instance.search_train.side_effect = SRTNotLoggedInError()
        logged_in._relogin = Mock(return_value=True)

        assert logged_in.search_trains("20260801", "수서", "부산", verbose=False) == []
        logged_in._relogin.assert_called_once()

    def test_an_expired_session_that_cannot_be_renewed_is_reported(self, logged_in):
        logged_in._srt_instance.search_train.side_effect = SRTNotLoggedInError()
        logged_in._relogin = Mock(return_value=False)

        with pytest.raises(SearchUnavailableError):
            logged_in.search_trains("20260801", "수서", "부산", verbose=False)

    def test_a_refusal_that_reads_like_an_expired_session_renews_it(self, logged_in):
        """SR leaves is_login True when the cookie goes stale; only the text says so."""
        logged_in._srt_instance.search_train.side_effect = SRTResponseError("로그인 후 이용하세요")
        logged_in._relogin = Mock(return_value=True)

        assert logged_in.search_trains("20260801", "수서", "부산", verbose=False) == []
        logged_in._relogin.assert_called_once()


class TestReserving:
    def test_a_reservation_comes_back_as_it_is(self, logged_in):
        booked = FakeReservation("123456789")
        logged_in._srt_instance.reserve.return_value = booked

        assert logged_in.reserve_train(FakeTrain("300")) is booked

    def test_the_service_seat_type_is_used_when_none_is_given(self, logged_in):
        logged_in._seat_type = SeatType.SPECIAL_ONLY
        logged_in._srt_instance.reserve.return_value = FakeReservation("1")

        logged_in.reserve_train(FakeTrain("300"))

        assert logged_in._srt_instance.reserve.call_args.kwargs["special_seat"] == (
            SeatType.SPECIAL_ONLY
        )

    def test_the_passenger_count_reaches_sr(self, logged_in):
        logged_in._srt_instance.reserve.return_value = FakeReservation("1")

        logged_in.reserve_train(FakeTrain("300"), passenger_count=3)

        passengers = logged_in._srt_instance.reserve.call_args.kwargs["passengers"]
        assert [p.count for p in passengers] == [3]

    def test_reserving_without_a_session_is_a_programming_error(self, service):
        with pytest.raises(ValueError):
            service.reserve_train(FakeTrain("300"))


class TestWhatARefusalMeant:
    """
    SR declares SRTDuplicateError and never raises it. Every refusal is an
    SRTResponseError, and what it was is in the sentence.
    """

    def test_a_duplicate_is_told_apart_from_a_lost_race(self, logged_in):
        logged_in._srt_instance.reserve.side_effect = SRTResponseError(
            "이미 예약하신 열차가 있습니다"
        )

        assert logged_in.reserve_train(FakeTrain("300")) == "DUPLICATE"

    def test_a_sold_out_train_is_not_a_duplicate(self, logged_in):
        logged_in._srt_instance.reserve.side_effect = SRTResponseError("잔여석이 없습니다")

        assert logged_in.reserve_train(FakeTrain("300")) is None

    def test_an_expired_session_renews_and_lets_the_loop_retry(self, logged_in):
        logged_in._srt_instance.reserve.side_effect = SRTNotLoggedInError()
        logged_in._relogin = Mock(return_value=True)

        assert logged_in.reserve_train(FakeTrain("300")) is None
        logged_in._relogin.assert_called_once()

    def test_a_refusal_nobody_recognises_keeps_the_search_alive(self, logged_in):
        """
        The alternative is ending a search that may have been one attempt from
        a seat, on the strength of a sentence nobody has read yet.
        """
        logged_in._srt_instance.reserve.side_effect = SRTResponseError("알 수 없는 사유")

        assert logged_in.reserve_train(FakeTrain("300")) is None

    def test_an_error_that_is_not_even_an_SR_error_is_survived(self, logged_in):
        logged_in._srt_instance.reserve.side_effect = RuntimeError("boom")

        assert logged_in.reserve_train(FakeTrain("300")) is None


class TestLoggingIn:
    def test_a_good_login_is_reported_and_remembered(self, service):
        client = Mock()
        client.is_login = True
        service._build_client = Mock(return_value=client)

        assert service.login(USERNAME, PASSWORD) is True
        assert service.is_logged_in is True
        assert service._username == USERNAME

    def test_a_bad_password_is_a_failure_not_a_crash(self, service):
        client = Mock()
        client.login.side_effect = SRTLoginError("비밀번호 오류")
        service._build_client = Mock(return_value=client)

        assert service.login(USERNAME, PASSWORD) is False
        assert service.is_logged_in is False

    def test_credentials_are_not_kept_when_the_login_failed(self, service):
        client = Mock()
        client.login.side_effect = SRTLoginError("존재하지않는 회원입니다")
        service._build_client = Mock(return_value=client)

        service.login(USERNAME, PASSWORD)

        assert service._username is None
        assert service._password is None

    def test_a_blocked_address_is_raised_rather_than_reported(self, service):
        """
        Retrying is what produced the block. Reported as a plain failure it
        would be retried; raised, the caller has to decide what to do.
        """
        client = Mock()
        client.login.side_effect = SRTLoginError(IP_BLOCKED_MARKER)
        service._build_client = Mock(return_value=client)

        with pytest.raises(SrtBlockedError):
            service.login(USERNAME, PASSWORD)

    def test_a_blocked_address_during_a_refresh_is_raised_too(self, logged_in):
        client = Mock()
        client.login.side_effect = SRTLoginError(IP_BLOCKED_MARKER)
        logged_in._build_client = Mock(return_value=client)

        with pytest.raises(SrtBlockedError):
            logged_in._relogin()

    def test_a_refresh_without_credentials_gives_up_quietly(self, service):
        assert service._relogin() is False

    def test_a_failed_refresh_still_moves_the_deadline(self, logged_in):
        """Otherwise every pass of the search loop becomes a login attempt."""
        client = Mock()
        client.login.side_effect = SRTLoginError("비밀번호 오류")
        logged_in._build_client = Mock(return_value=client)
        logged_in._relogin_due_at = 0.0

        logged_in._relogin()

        assert logged_in._relogin_due_at > 0.0

    def test_one_netfunnel_helper_survives_a_re_login(self, logged_in):
        """A fresh helper would queue for a new key on every session refresh."""
        helper = logged_in._netfunnel
        client = Mock()
        client.is_login = True
        logged_in._build_client = Mock(return_value=client)

        logged_in._relogin()

        assert logged_in._netfunnel is helper


class TestWhetherItWasPaidFor:
    """
    Unlike Korail, SR keeps a reservation in the list after it is paid for and
    says so in a field. "Gone" and "paid" are different answers here.
    """

    def test_an_unpaid_reservation_is_still_outstanding(self, logged_in):
        logged_in._srt_instance.get_reservations.return_value = [FakeReservation("111", paid=False)]

        assert logged_in.is_reservation_outstanding("111") is True

    def test_a_paid_reservation_is_not(self, logged_in):
        logged_in._srt_instance.get_reservations.return_value = [FakeReservation("111", paid=True)]

        assert logged_in.is_reservation_outstanding("111") is False

    def test_a_reservation_that_is_gone_is_not(self, logged_in):
        logged_in._srt_instance.get_reservations.return_value = [FakeReservation("999")]

        assert logged_in.is_reservation_outstanding("111") is False

    def test_the_number_is_compared_as_text(self, logged_in):
        logged_in._srt_instance.get_reservations.return_value = [FakeReservation(111, paid=False)]

        assert logged_in.is_reservation_outstanding("111") is True

    def test_not_being_able_to_ask_is_not_an_answer(self, logged_in):
        """Read as "gone", it would revive the guess it was written to remove."""
        logged_in._srt_instance.get_reservations.side_effect = SRTResponseError("점검 중")

        assert logged_in.is_reservation_outstanding("111") is None

    def test_an_expired_session_is_not_an_answer_either(self, logged_in):
        logged_in._srt_instance.get_reservations.side_effect = SRTNotLoggedInError()

        assert logged_in.is_reservation_outstanding("111") is None

    def test_asking_before_logging_in_is_not_an_answer(self, service):
        assert service.is_reservation_outstanding("111") is None


class TestGivingSeatsBack:
    """
    SR's client can cancel outright, so a random-seating run that gives up
    does not have to leave its seats to expire.
    """

    def test_every_partial_reservation_is_cancelled(self, logged_in):
        one, two = FakeReservation("111"), FakeReservation("222")

        logged_in._cancel_reservations([one, two])

        cancelled = [call.args[0] for call in logged_in._srt_instance.cancel.call_args_list]
        assert cancelled == [one, two]

    def test_one_refusal_does_not_strand_the_rest(self, logged_in):
        one, two = FakeReservation("111"), FakeReservation("222")
        logged_in._srt_instance.cancel.side_effect = [SRTResponseError("안 됩니다"), None]

        logged_in._cancel_reservations([one, two])

        assert logged_in._srt_instance.cancel.call_count == 2

    def test_nothing_to_cancel_asks_nothing(self, logged_in):
        logged_in._cancel_reservations([])

        logged_in._srt_instance.cancel.assert_not_called()

    def test_without_a_session_they_are_left_to_expire(self, service):
        """Reached while giving up on a search that has already gone wrong."""
        service._cancel_reservations([FakeReservation("111")])  # must not raise
