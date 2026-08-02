"""
What a payment record has to survive to be worth reporting.

The record used to say only that a payment window was open, which was all the
reminder loop ever read. /status names the booking and the cancel button needs
its number, so the reservation is written down beside the flags - and it comes
back out of Redis, which is where it lives between the search process that
writes it and the app that reads it.

The expiry matters as much as the fields. The configured window is only this
bot's idea of how long a railway holds a seat; the deadline on the reservation
is the railway's, and a record that expired first would have /status say there
was nothing to pay for while the seat was still being held.
"""

from datetime import datetime, timedelta

from korail_bot.config.settings import settings
from korail_bot.models import (
    MultiReservationStatus,
    Operator,
    PaymentStatus,
    ReservationPaymentStatus,
    SingleReservationInfo,
)

CHAT_ID = 553311


def test_the_reservation_details_survive_the_round_trip(storage):
    deadline = datetime.now().replace(microsecond=0) + timedelta(minutes=9)
    storage.save_payment_status(
        PaymentStatus(
            chat_id=CHAT_ID,
            reminder_active=True,
            reservation_id="320260731221946",
            train_info="[KTX 101] 서울(09:00)->부산(11:40)",
            operator=Operator.SRT,
            expires_at=deadline,
        )
    )

    stored = storage.get_payment_status(CHAT_ID)

    assert stored.reservation_id == "320260731221946"
    assert stored.train_info == "[KTX 101] 서울(09:00)->부산(11:40)"
    assert stored.rail_operator is Operator.SRT
    assert stored.expires_at == deadline
    assert stored.cancelled is False

    storage.delete_payment_status(CHAT_ID)


def test_a_record_from_before_the_details_existed_still_reads(storage):
    """Written by an older build, or by the callback before the seat details land."""
    storage.redis.set(
        f"payment_status:{CHAT_ID}",
        f'{{"chat_id": {CHAT_ID}, "reminder_active": true, "completed": false}}',
    )

    stored = storage.get_payment_status(CHAT_ID)

    assert stored.reservation_id is None
    assert stored.rail_operator is Operator.KORAIL
    assert stored.is_awaiting_payment() is True

    storage.delete_payment_status(CHAT_ID)


def test_a_cancelled_record_is_not_awaiting_anything(storage):
    storage.save_payment_status(
        PaymentStatus(chat_id=CHAT_ID, completed=True, cancelled=True, reservation_id="222")
    )

    assert storage.get_payment_status(CHAT_ID).is_awaiting_payment() is False

    storage.delete_payment_status(CHAT_ID)


def test_the_record_outlives_the_deadline_it_carries(storage):
    """A deadline further out than the configured window pushes the expiry back."""
    far = settings.PAYMENT_TIMEOUT_MINUTES + 30
    storage.save_payment_status(
        PaymentStatus(chat_id=CHAT_ID, expires_at=datetime.now() + timedelta(minutes=far))
    )

    ttl = storage.redis.ttl(f"payment_status:{CHAT_ID}")

    assert ttl > (settings.PAYMENT_TIMEOUT_MINUTES + 5) * 60

    storage.delete_payment_status(CHAT_ID)


def test_the_railway_holding_a_random_run_survives_the_round_trip(storage):
    """Cancelling those seats needs to know whose they are."""
    storage.save_multi_reservation_status(
        MultiReservationStatus(
            chat_id=CHAT_ID,
            reservations=[
                SingleReservationInfo(
                    reservation_id="111",
                    reservation_obj=None,
                    reserved_at=datetime.now(),
                    expires_at=datetime.now() + timedelta(minutes=9),
                    status=ReservationPaymentStatus.PENDING,
                    seat_number=1,
                    train_info="[SRT 301] 수서->부산",
                )
            ],
            total_seats=1,
            seat_strategy="random",
            created_at=datetime.now(),
            operator=Operator.SRT,
        )
    )

    stored = storage.get_multi_reservation_status(CHAT_ID)

    assert stored.rail_operator is Operator.SRT

    storage.delete_multi_reservation_status(CHAT_ID)
