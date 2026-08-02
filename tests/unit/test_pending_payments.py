"""
What /status says about a seat that is already booked, and how to let it go.

The bot's job ends with a seat held and the user told to pay for it, and until
now that was where the bot stopped being able to say anything: /status talked
about searches only, so someone who had just caught a seat was told there was
nothing going on.

Two records hold the answer, in two shapes. A single booking is a
PaymentStatus; a random-seating run is a MultiReservationStatus with a row per
seat. Both are read here, and both are written back when a seat goes back -
including the flag that keeps the search process from announcing the
cancellation as a payment.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from korail_bot.models import (
    MultiReservationStatus,
    OnboardedAccount,
    Operator,
    PaymentStatus,
    ReservationPaymentStatus,
    SingleReservationInfo,
)
from korail_bot.services import TelegramService, pending_payment_service
from korail_bot.services.pending_payment_service import PendingPaymentService
from korail_bot.storage.base import StorageInterface

CHAT_ID = 5150
PHONE = "010-1234-5678"
PASSWORD = "korail-password"
MODULE = "korail_bot.services.pending_payment_service"


def single(reservation_id="320260731221946", **kwargs):
    """A booking of one seat, waiting to be paid for."""
    return PaymentStatus(
        chat_id=CHAT_ID,
        completed=kwargs.pop("completed", False),
        reminder_active=True,
        reservation_id=reservation_id,
        train_info="[KTX 101] 서울(09:00)->부산(11:40)",
        operator=kwargs.pop("operator", Operator.KORAIL),
        expires_at=kwargs.pop("expires_at", datetime.now() + timedelta(minutes=9)),
        **kwargs,
    )


def seat(number, reservation_id, status=ReservationPaymentStatus.PENDING, minutes=9):
    return SingleReservationInfo(
        reservation_id=reservation_id,
        reservation_obj=None,
        reserved_at=datetime.now(),
        expires_at=datetime.now() + timedelta(minutes=minutes),
        status=status,
        seat_number=number,
        train_info=f"[KTX 101] 좌석 {number}",
    )


def multi(seats, operator=Operator.KORAIL):
    return MultiReservationStatus(
        chat_id=CHAT_ID,
        reservations=seats,
        total_seats=len(seats),
        seat_strategy="random",
        created_at=datetime.now(),
        operator=operator,
    )


class PendingFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_payment_status.return_value = None
        self.storage.get_multi_reservation_status.return_value = None
        self.storage.is_developer.return_value = False
        self.storage.get_onboarded_account.return_value = OnboardedAccount(
            chat_id=CHAT_ID, korail_id=PHONE, korail_pw=PASSWORD
        )
        # No random-seating run part way through taking seats.
        self.storage.get_current_seat_index.return_value = None
        self.telegram = Mock(spec=TelegramService)
        self.service = PendingPaymentService(self.storage, self.telegram)

    def texts(self):
        return [call.args[1] for call in self.telegram.send_message.call_args_list]

    def last_text(self):
        return self.telegram.send_message.call_args.args[1]


def rail_client(cancels=True, logs_in=True):
    """A logged-in railway client that cancels, or refuses to."""
    client = Mock()
    client.login.return_value = logs_in
    client.cancel_reservation.return_value = cancels
    return client


def with_rail(client):
    """Both operators' services replaced by one stand-in."""
    return patch(f"{MODULE}.PendingPaymentService._rail_service", return_value=client)


class TestReadingWhatIsPending(PendingFixture):
    """One shape out of two records."""

    def test_a_single_booking_is_reported(self):
        self.storage.get_payment_status.return_value = single()

        pending = self.service.pending(CHAT_ID)

        assert [item.reservation_id for item in pending] == ["320260731221946"]

    def test_a_paid_booking_is_not_pending(self):
        self.storage.get_payment_status.return_value = single(completed=True)

        assert self.service.pending(CHAT_ID) == []

    def test_a_cancelled_booking_is_not_pending(self):
        self.storage.get_payment_status.return_value = single(cancelled=True)

        assert self.service.pending(CHAT_ID) == []

    def test_a_booking_past_its_deadline_is_not_pending(self):
        """The railway took the seat back; there is nothing left to pay for."""
        self.storage.get_payment_status.return_value = single(
            expires_at=datetime.now() - timedelta(minutes=1)
        )

        assert self.service.pending(CHAT_ID) == []

    def test_a_record_with_no_deadline_is_still_pending(self):
        """Written before the reservation details were - not a reason to hide it."""
        self.storage.get_payment_status.return_value = single(expires_at=None)

        assert len(self.service.pending(CHAT_ID)) == 1

    def test_every_unpaid_seat_of_a_random_run_is_reported(self):
        self.storage.get_multi_reservation_status.return_value = multi(
            [seat(1, "111"), seat(2, "222")]
        )

        pending = self.service.pending(CHAT_ID)

        assert [item.seat_number for item in pending] == [1, 2]

    def test_seats_already_paid_for_are_left_out(self):
        self.storage.get_multi_reservation_status.return_value = multi(
            [seat(1, "111", ReservationPaymentStatus.PAID), seat(2, "222")]
        )

        assert [item.reservation_id for item in self.service.pending(CHAT_ID)] == ["222"]

    def test_expired_seats_are_left_out(self):
        self.storage.get_multi_reservation_status.return_value = multi(
            [seat(1, "111", minutes=-1), seat(2, "222")]
        )

        assert [item.reservation_id for item in self.service.pending(CHAT_ID)] == ["222"]

    def test_nothing_booked_is_nothing_pending(self):
        assert self.service.pending(CHAT_ID) == []


class TestDescribingThem(PendingFixture):
    """The block /status grows when there is a seat to pay for."""

    def test_nothing_pending_adds_nothing(self):
        assert self.service.describe(CHAT_ID) is None

    def test_the_train_and_the_number_are_named(self):
        self.storage.get_payment_status.return_value = single()

        described = self.service.describe(CHAT_ID)

        assert "KTX 101" in described
        assert "320260731221946" in described

    def test_the_link_goes_to_the_railway_holding_the_seat(self):
        self.storage.get_payment_status.return_value = single(operator=Operator.SRT)

        assert "srail" in self.service.describe(CHAT_ID)


class TestGivingTheSeatBack(PendingFixture):
    """The half that touches the railway, and what is written down afterwards."""

    def test_the_reservation_is_cancelled_with_the_registered_account(self):
        self.storage.get_payment_status.return_value = single()
        client = rail_client()

        with with_rail(client):
            assert self.service.cancel(CHAT_ID) is True

        client.login.assert_called_once_with(PHONE, PASSWORD)
        client.cancel_reservation.assert_called_once_with("320260731221946")

    def test_the_record_says_cancelled_rather_than_paid(self):
        status = single()
        self.storage.get_payment_status.return_value = status

        with with_rail(rail_client()):
            self.service.cancel(CHAT_ID)

        assert status.cancelled is True
        self.storage.save_payment_status.assert_called_once_with(status)

    def test_the_watcher_is_kept_from_calling_it_a_payment(self):
        """
        The search process is still watching the reservation and will see it
        disappear. `completed` is what tells it the user already knows.
        """
        status = single()
        self.storage.get_payment_status.return_value = status

        with with_rail(rail_client()):
            self.service.cancel(CHAT_ID)

        assert status.completed is True
        assert status.reminder_active is False

    def test_every_seat_of_a_random_run_goes_back(self):
        booking = multi([seat(1, "111"), seat(2, "222")])
        self.storage.get_multi_reservation_status.return_value = booking
        client = rail_client()

        with with_rail(client):
            self.service.cancel(CHAT_ID)

        assert [call.args[0] for call in client.cancel_reservation.call_args_list] == ["111", "222"]
        assert all(r.status == ReservationPaymentStatus.CANCELLED for r in booking.reservations)

    def test_the_railway_that_holds_the_seat_is_the_one_asked(self):
        self.storage.get_payment_status.return_value = single(operator=Operator.SRT)

        with patch(f"{MODULE}.PendingPaymentService._rail_service") as build:
            build.return_value = rail_client()
            self.service.cancel(CHAT_ID)

        build.assert_called_once_with(Operator.SRT)

    def test_a_refusal_is_never_written_down_as_a_cancellation(self):
        status = single()
        self.storage.get_payment_status.return_value = status

        with with_rail(rail_client(cancels=False)):
            assert self.service.cancel(CHAT_ID) is False

        assert status.cancelled is False
        self.storage.save_payment_status.assert_not_called()

    def test_a_refusal_says_where_to_do_it_by_hand(self):
        self.storage.get_payment_status.return_value = single()

        with with_rail(rail_client(cancels=False)):
            self.service.cancel(CHAT_ID)

        assert "취소하지 못했습니다" in self.last_text()
        assert "korail.com" in self.last_text()

    def test_a_login_that_fails_cancels_nothing_and_says_so(self):
        status = single()
        self.storage.get_payment_status.return_value = status

        with with_rail(rail_client(logs_in=False)):
            assert self.service.cancel(CHAT_ID) is False

        assert status.cancelled is False
        assert "로그인하지 못했습니다" in self.last_text()

    def test_no_registered_account_means_no_cancelling(self):
        self.storage.get_payment_status.return_value = single()
        self.storage.get_onboarded_account.return_value = None

        with patch(f"{MODULE}.PendingPaymentService._rail_service") as build:
            assert self.service.cancel(CHAT_ID) is False

        build.assert_not_called()
        assert "로그인 정보가 없어" in self.last_text()

    def test_a_run_still_taking_seats_is_stopped_first(self):
        """
        Random seating books one seat at a time. Giving one back here would be
        followed by the search taking another, in the name of someone who was
        just told their booking was cancelled.
        """
        self.storage.get_current_seat_index.return_value = 1
        self.storage.get_multi_reservation_status.return_value = multi([seat(1, "111")])

        with patch(f"{MODULE}.PendingPaymentService._rail_service") as build:
            assert self.service.cancel(CHAT_ID) is False

        build.assert_not_called()
        assert "/cancel" in self.last_text()

    def test_nothing_pending_is_said_plainly(self):
        assert self.service.cancel(CHAT_ID) is False
        assert "결제를 기다리는 예약이 없습니다" in self.last_text()

    def test_a_partly_cancelled_run_does_not_read_as_done(self):
        """Some of those seats are still booked in the user's name."""
        booking = multi([seat(1, "111"), seat(2, "222")])
        self.storage.get_multi_reservation_status.return_value = booking
        client = rail_client()
        client.cancel_reservation.side_effect = [True, False]

        with with_rail(client):
            self.service.cancel(CHAT_ID)

        assert "취소하지 못했습니다" in " ".join(self.texts())
        assert booking.reservations[0].status == ReservationPaymentStatus.CANCELLED
        assert booking.reservations[1].status == ReservationPaymentStatus.PENDING


class TestAskingFirst(PendingFixture):
    """A seat waited hours for does not go back on one press."""

    def test_the_confirmation_names_what_would_go(self):
        self.storage.get_payment_status.return_value = single()

        self.service.confirm_cancellation(CHAT_ID)

        assert "정말" in self.last_text()
        assert "KTX 101" in self.last_text()

    def test_nothing_pending_is_said_instead_of_asking(self):
        self.service.confirm_cancellation(CHAT_ID)

        assert "결제를 기다리는 예약이 없습니다" in self.last_text()


@pytest.mark.parametrize(
    ("developer", "expected"),
    [(True, True), (False, False)],
)
def test_only_a_developer_chat_cancels_with_the_fixed_account(developer, expected):
    """
    The fixed login exists so the bot can be pointed at a test account. Only a
    developer chat books with it, so only one can have a seat to give back
    with it.
    """
    # The class behind the settings this module actually holds, not whatever
    # korail_bot.config.settings exports now: other tests reload that module,
    # which leaves a fresh Settings class in it while every module that
    # imported the singleton keeps the old one. Patching the wrong class of
    # the two is a no-op that only shows up when those tests run first.
    settings_class = type(pending_payment_service.settings)

    storage = Mock(spec=StorageInterface)
    storage.get_payment_status.return_value = single()
    storage.get_multi_reservation_status.return_value = None
    storage.get_onboarded_account.return_value = None
    storage.get_current_seat_index.return_value = None
    storage.is_developer.return_value = developer
    service = PendingPaymentService(storage, Mock(spec=TelegramService))

    with (
        patch.multiple(
            settings_class,
            KORAIL_ADMIN_USER_ID="010-0000-0000",
            KORAIL_ADMIN_PASSWORD="operator-pw",
        ),
        with_rail(rail_client()),
    ):
        assert service.cancel(CHAT_ID) is expected
