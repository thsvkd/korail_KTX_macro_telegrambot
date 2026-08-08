"""
Giving back a seat that is not the one the user asked for.

Neither railway lets a booking request a particular seat, so a seat condition
is honoured after the fact: take whatever comes, look at it, hand it back if
it is wrong. That makes the decision destructive in a way most of this bot is
not - a wrong answer here throws away a seat somebody was waiting hours for -
so the cases that matter are the ones where the search should stop being
fussy: an unreadable label, a railway that does not report seats, a
cancellation the railway refuses, and the point where holding out has cost
more than it is worth.
"""

from unittest.mock import Mock

import pytest

from korail_bot.models import Operator, SeatPreference
from korail_bot.services.rail_service import RailService
from korail_bot.services.srt_service import SrtService


class FakeTicket:
    """An SRT ticket, as far as the seat check reads one."""

    def __init__(self, seat, car="5"):
        self.seat = seat
        self.car = car


class FakeReservation:
    """
    An SR booking, with the tickets SR fills in before payment.

    Not a Mock: the check asks whether the booking has tickets at all, and a
    Mock answers yes to every attribute, so a booking that reports no seats
    would be indistinguishable from one that reports several.
    """

    def __init__(self, seats=(), rsv_id="1234567890"):
        self.tickets = [FakeTicket(seat) for seat in seats]
        self.reservation_number = rsv_id


class SeatCheckingService(RailService):
    """
    A rail service that reports seats and can give them back.

    Built on the real base class rather than a Mock, because what is being
    tested is the base class's decision - which seats to keep - and the two
    operator hooks it leans on are exactly what a Mock would paper over.
    """

    operator_name = "테스트철도"

    def __init__(self, seats=(), cancel_succeeds=True):
        super().__init__()
        self._seats = list(seats)
        self.cancel_succeeds = cancel_succeeds
        self.cancelled: list[str] = []
        self.announced: list[str] = []
        self._on_status = self.announced.append

    # The two hooks the decision reads.
    def assigned_seats(self, reservation):
        return list(getattr(reservation, "seats_override", self._seats))

    def cancel_reservation(self, rsv_id: str) -> bool:
        self.cancelled.append(rsv_id)
        return self.cancel_succeeds

    @staticmethod
    def reservation_id(reservation):
        return getattr(reservation, "reservation_number", None)

    # Abstract members this test never exercises.
    @property
    def default_train_type(self):
        return None

    @property
    def default_reserve_option(self):
        return None

    def login(self, username, password):
        return True

    def _relogin(self):
        return True

    def search_trains(self, *args, **kwargs):
        return []

    def reserve_train(self, train, option=None, passenger_count=1):
        return None

    def is_reservation_outstanding(self, rsv_id):
        return None

    @staticmethod
    def describe_train(train):
        return {"no": "", "label": "", "soldout": True}

    @staticmethod
    def payment_due(reservation):
        return None, None


WINDOW_SEATS = SeatPreference(columns=("A", "D"))


class TestKeepingAMatchingSeat:
    """The cases where the booking stands."""

    def test_no_preference_keeps_anything(self):
        service = SeatCheckingService(seats=["7B"])

        assert service.keeps_seat(FakeReservation(), SeatPreference())
        assert service.keeps_seat(FakeReservation(), None)
        assert service.cancelled == []

    def test_a_seat_in_the_chosen_column_is_kept(self):
        service = SeatCheckingService(seats=["3A"])

        assert service.keeps_seat(FakeReservation(), WINDOW_SEATS)
        assert service.cancelled == []

    def test_every_seat_of_a_group_booking_has_to_match(self):
        """
        There is no way to give back only the wrong half of a booking, and a
        party split across the condition is not what was asked for.
        """
        service = SeatCheckingService(seats=["3A", "3B"])

        assert not service.keeps_seat(FakeReservation(), WINDOW_SEATS)
        assert service.cancelled == ["1234567890"]

    def test_a_group_booking_entirely_inside_the_condition_is_kept(self):
        service = SeatCheckingService(seats=["3A", "4D"])

        assert service.keeps_seat(FakeReservation(), WINDOW_SEATS)
        assert service.cancelled == []

    def test_the_row_range_is_checked_alongside_the_column(self):
        near_the_front = SeatPreference(columns=("A",), row_min=1, row_max=5)

        assert SeatCheckingService(seats=["3A"]).keeps_seat(FakeReservation(), near_the_front)
        assert not SeatCheckingService(seats=["9A"]).keeps_seat(FakeReservation(), near_the_front)


class TestGivingASeatBack:
    """The rejection path, and that it leaves nothing held."""

    def test_a_wrong_seat_is_cancelled_and_refused(self):
        service = SeatCheckingService(seats=["7B"])

        assert not service.keeps_seat(FakeReservation(), WINDOW_SEATS)
        assert service.cancelled == ["1234567890"]

    def test_a_cancellation_the_railway_refuses_keeps_the_booking(self):
        """
        The seat could not be handed back, so it is still held. Walking away
        would leave the user owning a booking they were never told about.
        """
        service = SeatCheckingService(seats=["7B"], cancel_succeeds=False)

        assert service.keeps_seat(FakeReservation(), WINDOW_SEATS)
        assert service.cancelled == ["1234567890"]
        assert any("반납에 실패" in message for message in service.announced)

    def test_a_booking_with_no_number_cannot_be_given_back_and_is_kept(self):
        service = SeatCheckingService(seats=["7B"])
        booking = FakeReservation()
        booking.reservation_number = None

        assert service.keeps_seat(booking, WINDOW_SEATS)
        assert service.cancelled == []


class TestWhenTheSeatCannotBeChecked:
    """
    Cases where the answer is "cannot tell", which must not read as "wrong".

    A False here cancels, so anything unexpected has to fail towards keeping
    the seat - otherwise one surprise turns into a search that throws away
    every booking it ever wins.
    """

    def test_a_railway_that_reports_no_seats_keeps_the_booking(self):
        service = SeatCheckingService(seats=[])

        assert service.keeps_seat(FakeReservation(), WINDOW_SEATS)
        assert service.cancelled == []
        assert any("좌석 번호를 알려주지 않아" in m for m in service.announced)

    def test_an_unreadable_seat_label_keeps_the_booking(self):
        service = SeatCheckingService(seats=["입석"])

        assert service.keeps_seat(FakeReservation(), WINDOW_SEATS)
        assert service.cancelled == []
        assert any("읽지 못했습니다" in m for m in service.announced)

    def test_the_warning_is_said_once_however_long_the_search_runs(self):
        """A loop that runs for hours must not repeat this every pass."""
        service = SeatCheckingService(seats=[])

        for _ in range(5):
            service.keeps_seat(FakeReservation(), WINDOW_SEATS)

        assert len(service.announced) == 1

    def test_the_base_class_reports_no_seats(self):
        """
        Korail's case. Its unpaid bookings carry no seat number at all, so the
        default has to be empty rather than a guess.
        """
        assert RailService.assigned_seats(object()) == []


class TestGivingUpOnTheCondition:
    """Past some number of rejections, a seat beats no seat."""

    def test_the_search_stops_being_fussy_after_the_cap(self):
        service = SeatCheckingService(seats=["7B"])

        refused = sum(
            0 if service.keeps_seat(FakeReservation(), WINDOW_SEATS) else 1
            for _ in range(RailService.MAX_SEAT_REJECTIONS + 1)
        )

        assert refused == RailService.MAX_SEAT_REJECTIONS
        assert len(service.cancelled) == RailService.MAX_SEAT_REJECTIONS

    def test_the_user_is_told_which_seat_they_ended_up_with(self):
        service = SeatCheckingService(seats=["7B"])

        for _ in range(RailService.MAX_SEAT_REJECTIONS + 1):
            service.keeps_seat(FakeReservation(), WINDOW_SEATS)

        last = service.announced[-1]
        assert "7B" in last
        assert WINDOW_SEATS.describe() in last

    def test_rejections_are_counted_across_the_whole_search(self):
        """
        Not per pass. The cap is about how long the user has been held up
        altogether, and a per-pass counter would never reach it.
        """
        service = SeatCheckingService(seats=["7B"])

        service.keeps_seat(FakeReservation(), WINDOW_SEATS)
        service.keeps_seat(FakeReservation(), WINDOW_SEATS)

        assert service._seat_rejections == 2


class TestReadingSrSeats:
    """The SR-specific half: where the seat labels come from."""

    def test_seats_are_read_off_the_tickets_sr_fills_in(self):
        booking = FakeReservation(seats=["3A", "4D"])

        assert SrtService.assigned_seats(booking) == ["3A", "4D"]

    @pytest.mark.parametrize(
        "booking",
        [
            FakeReservation(seats=[]),
            # Shapes an unexpected SR response could produce. None of them
            # may be read as "this booking has no seats".
            Mock(tickets=None),
            object(),
        ],
    )
    def test_an_unexpected_shape_reports_nothing_rather_than_raising(self, booking):
        assert SrtService.assigned_seats(booking) == []

    def test_a_ticket_with_no_seat_is_left_out(self):
        booking = FakeReservation(seats=["3A"])
        booking.tickets.append(FakeTicket(seat=None))

        assert SrtService.assigned_seats(booking) == ["3A"]


class TestWhichRailwaysCanHonourTheCondition:
    """The capability the whole feature hangs off."""

    def test_only_sr_reports_seats_before_payment(self):
        assert Operator.SRT.reports_seats_before_payment
        assert not Operator.KORAIL.reports_seats_before_payment
