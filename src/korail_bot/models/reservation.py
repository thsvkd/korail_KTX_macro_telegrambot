"""Reservation data models."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any

from korail_bot.models.operator import Operator


@dataclass
class TrainSearchParams:
    """Parameters for searching trains."""

    dep_date: str  # Format: YYYYMMDD
    src_locate: str  # Station name (without '역')
    dst_locate: str  # Station name (without '역')
    dep_time: str  # Format: HHMMSS
    max_dep_time: str = "2400"  # Format: HHMM
    # Which railway this search is against. Defaulted rather than required so
    # that every search stored before there were two still reads back as what
    # it was - see Operator.parse.
    operator: str = Operator.KORAIL
    train_type: str = "TrainType.KTX"  # korail2.TrainType enum as string
    train_type_display: str = "KTX"
    special_option: str = "ReserveOption.GENERAL_FIRST"  # korail2.ReserveOption enum as string
    special_option_display: str = "GENERAL_FIRST"
    passenger_count: int = 1  # Number of adult passengers (1-9)
    seat_strategy: str = "consecutive"  # "consecutive" or "random"
    # Korail train numbers to watch. Empty means every train in the time
    # window, which is what the bot did before trains could be picked and is
    # still the better odds - a narrower watch is a deliberate choice to wait
    # for one particular train rather than take the first seat going.
    train_numbers: list[str] = field(default_factory=list)

    def watches_specific_trains(self) -> bool:
        """Whether the search is narrowed to a chosen set of trains."""
        return bool(self.train_numbers)

    @property
    def rail_operator(self) -> Operator:
        """The operator this search is against, however it was stored."""
        return Operator.parse(self.operator)

    def validate(self) -> tuple[bool, str | None]:
        """
        Validate search parameters.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Validate date format
        if not self.dep_date.isdigit() or len(self.dep_date) != 8:
            return False, "날짜 형식이 올바르지 않습니다 (YYYYMMDD)"

        # Validate date is not in the past
        today = datetime.today().strftime("%Y%m%d")
        if self.dep_date < today:
            return False, "과거 날짜는 선택할 수 없습니다"

        # Validate time format
        if not self.dep_time[:4].isdigit() or len(self.dep_time) != 6:
            return False, "시간 형식이 올바르지 않습니다 (HHMMSS)"

        # Validate the stations are ones this operator stops at.
        #
        # Only SR can be checked here - its list is short, fixed and published
        # with the client. Korail's is fetched and cached, and lives behind
        # InputValidator, which is where a Korail search is checked instead.
        operator = self.rail_operator
        for station, label in ((self.src_locate, "출발역"), (self.dst_locate, "도착역")):
            if operator.serves(station) is False:
                return False, f"{operator.display_name}은(는) {station}에 서지 않습니다 ({label})"

        return True, None


@dataclass
class ScheduledSearch:
    """
    A search that has been set up but is not to begin yet.

    Everything a search needs, held until its start time comes round. The
    reason to want one is that tickets are not released evenly: holiday
    booking opens at an announced minute, and cancellations cluster at the
    hours around a departure. A search that starts at the right moment beats
    one that has been grinding away since yesterday.
    """

    chat_id: int
    korail_id: str
    search_params: "TrainSearchParams"
    start_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now())

    def is_due(self, now: datetime | None = None) -> bool:
        """Whether the moment has arrived."""
        return (now or datetime.now()) >= self.start_at

    def seconds_until_due(self, now: datetime | None = None) -> float:
        """How long until it starts; zero once it is due."""
        return max(0.0, (self.start_at - (now or datetime.now())).total_seconds())


@dataclass
class RunningReservation:
    """Information about a running reservation process."""

    chat_id: int
    process_id: int
    korail_id: str
    search_params: TrainSearchParams
    # Identifies the application run that spawned the search process. A record
    # carrying anything else was left behind by a run that is already gone,
    # which means nothing is searching for it any more.
    run_id: str = ""
    # default_factory, not datetime.now(): a plain default is evaluated once,
    # when the class is defined, so every record would carry the time the
    # process started rather than the time the search did.
    #
    # Wrapped in a lambda rather than passed as datetime.now, so that the name
    # is resolved when a record is built. Handing over the bound method here
    # would capture the real one at import, before anything that patches the
    # clock - freezegun in the tests - has replaced it.
    started_at: datetime = field(default_factory=lambda: datetime.now())

    def is_stale(self, current_run_id: str) -> bool:
        """Check whether this record outlived the process that owned it."""
        return self.run_id != current_run_id


class DeathCause(StrEnum):
    """Why a search stopped without saying so."""

    # Never got going: the process was spawned and was gone moments later.
    START_FAILED = "start_failed"
    # Ran for a while and then vanished, without the callback that a search
    # ending normally always sends.
    CRASHED = "crashed"


@dataclass
class DeadSearch:
    """
    A search that stopped without finishing, kept so it can be picked back up.

    A search ending normally - a seat booked, or the attempt given up on -
    calls back to the app, which is what clears its record away. Nothing calls
    back when the process simply dies, and the difference matters to the user:
    they are waiting on a search that no longer exists, and the tickets they
    were waiting for are still out there. So the details are moved here, where
    they are no longer mistaken for a running search but are still everything
    needed to start the same search again.
    """

    chat_id: int
    korail_id: str
    search_params: TrainSearchParams
    cause: DeathCause
    # Whether the login kept for restarts was still there when the death was
    # noticed. Without it a search cannot be resumed, and the user is better
    # told that up front than offered a button that fails.
    resumable: bool = True
    died_at: datetime = field(default_factory=lambda: datetime.now())


@dataclass
class PaymentStatus:
    """Payment completion status for a reservation."""

    chat_id: int
    completed: bool = False
    # When the payment window opened. Named to match
    # MultiReservationStatus.created_at, which holds the same thing for the
    # multi-seat case; it was 'reservation_time' here alone.
    #
    # Stamped on creation so the field is never meaningless. Still optional,
    # because a record written before the field existed deserializes without
    # one and there is no way to invent the time it should have had.
    created_at: datetime | None = field(default_factory=lambda: datetime.now())
    reminder_active: bool = False

    # What is actually waiting to be paid for.
    #
    # The record used to say only that a window was open, which was all the
    # reminder loop needed. /status has to name the booking, and cancelling it
    # needs its number - so the search process, which is the only thing
    # holding the reservation, writes them here once the seat is secured.
    # Absent on a record written before this existed, and on one whose
    # reservation carried no number.
    reservation_id: str | None = None
    train_info: str = ""
    operator: str = Operator.KORAIL
    expires_at: datetime | None = None
    # Given back on purpose, as opposed to paid for or left to expire. Kept
    # apart from `completed`, which the reminder loop reads as "stop asking":
    # both are true here, and only one of them is what happened.
    cancelled: bool = False

    @property
    def rail_operator(self) -> Operator:
        """The railway holding this reservation, however it was stored."""
        return Operator.parse(self.operator)

    def is_awaiting_payment(self) -> bool:
        """Whether there is still a seat here for the user to pay for."""
        if self.completed or self.cancelled:
            return False
        return not (self.expires_at and datetime.now() >= self.expires_at)


class ReservationPaymentStatus(Enum):
    """Payment status for individual reservations."""

    PENDING = "pending"  # Reservation made, awaiting payment
    PAID = "paid"  # Payment completed by user
    EXPIRED = "expired"  # Reservation expired due to timeout
    CANCELLED = "cancelled"  # Manually cancelled by user


@dataclass
class SingleReservationInfo:
    """Information about a single train reservation."""

    reservation_id: str  # Unique ID from korail2
    reservation_obj: Any  # Original reservation object from korail2
    reserved_at: datetime  # When reservation was created
    expires_at: datetime  # When reservation will expire
    status: ReservationPaymentStatus  # Current payment status
    seat_number: int  # Seat number in the group (1, 2, 3...)
    train_info: str  # Human-readable train info for display

    def get_remaining_seconds(self) -> int:
        """Get remaining seconds until expiration."""
        if self.status != ReservationPaymentStatus.PENDING:
            return 0

        now = datetime.now()
        if now >= self.expires_at:
            return 0

        return int((self.expires_at - now).total_seconds())

    def get_remaining_minutes_display(self) -> str:
        """Get human-readable remaining time (e.g., '8분 30초')."""
        remaining = self.get_remaining_seconds()
        if remaining <= 0:
            return "만료됨"

        minutes = remaining // 60
        seconds = remaining % 60
        return f"{minutes}분 {seconds}초"

    def is_expired(self) -> bool:
        """Check if reservation has expired."""
        return datetime.now() >= self.expires_at


@dataclass
class MultiReservationStatus:
    """Status tracking for multiple reservations in random seat allocation."""

    chat_id: int
    reservations: list[SingleReservationInfo]
    total_seats: int
    seat_strategy: str  # "random" or "consecutive"
    created_at: datetime
    manually_stopped: bool = False  # True if user manually stopped reminders
    # Which railway is holding these seats. Defaulted rather than required so
    # that records written before there were two read back as what they were,
    # the same way TrainSearchParams.operator does.
    operator: str = Operator.KORAIL

    @property
    def rail_operator(self) -> Operator:
        """The railway holding these seats, however it was stored."""
        return Operator.parse(self.operator)

    def get_pending_count(self) -> int:
        """Count how many reservations are still pending payment."""
        return sum(
            1
            for r in self.reservations
            if r.status == ReservationPaymentStatus.PENDING and not r.is_expired()
        )

    def get_paid_count(self) -> int:
        """Count how many reservations have been paid."""
        return sum(1 for r in self.reservations if r.status == ReservationPaymentStatus.PAID)

    def get_expired_count(self) -> int:
        """Count how many reservations have expired."""
        return sum(
            1
            for r in self.reservations
            if r.status == ReservationPaymentStatus.EXPIRED or r.is_expired()
        )

    def should_show_reminder(self) -> bool:
        """Determine if reminder should be shown."""
        # Don't show if manually stopped
        if self.manually_stopped:
            return False

        # Show only if there are pending reservations that haven't expired
        return self.get_pending_count() > 0

    def get_most_urgent_reservation(self) -> SingleReservationInfo | None:
        """Get the reservation with least time remaining (most urgent)."""
        pending = [
            r
            for r in self.reservations
            if r.status == ReservationPaymentStatus.PENDING and not r.is_expired()
        ]

        if not pending:
            return None

        # Return the one with earliest expiration time
        return min(pending, key=lambda r: r.expires_at)

    def mark_all_expired(self) -> None:
        """Mark all pending reservations as expired."""
        for reservation in self.reservations:
            if reservation.status == ReservationPaymentStatus.PENDING:
                reservation.status = ReservationPaymentStatus.EXPIRED

    def mark_reservation_paid(self, seat_number: int) -> bool:
        """Mark a specific reservation as paid."""
        for reservation in self.reservations:
            if reservation.seat_number == seat_number:
                reservation.status = ReservationPaymentStatus.PAID
                return True
        return False
