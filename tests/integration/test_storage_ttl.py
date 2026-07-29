"""Every key RedisStorage writes with an expiry must actually get one.

Written alongside the move off the deprecated `setex(key, ttl, value)` to
`set(key, value, ex=ttl)`. The two take their arguments in a different order,
and nothing in the suite looked at a TTL, so a swapped pair would have stored
the payload under a nonsense expiry - or the expiry as the payload - with every
existing test still green.

The values themselves matter too: a session that never expires keeps encrypted
credentials in Redis forever, and a payment-ready flag that outlives its 60
seconds re-opens a window that has closed.
"""

from datetime import datetime

import pytest

from korail_bot.config.settings import settings
from korail_bot.models import (
    MultiReservationStatus,
    PaymentStatus,
    UserProgress,
    UserSession,
)
from korail_bot.storage import RedisStorage

CHAT_ID = 987654

# Redis reports the payment TTL as the timeout plus a five-minute buffer.
PAYMENT_TTL = (settings.PAYMENT_TIMEOUT_MINUTES + 5) * 60


def _write_session(storage):
    storage.save_user_session(
        UserSession(chat_id=CHAT_ID, in_progress=True, last_action=UserProgress.STARTED)
    )


def _write_multi_status(storage):
    storage.save_multi_reservation_status(
        MultiReservationStatus(
            chat_id=CHAT_ID,
            reservations=[],
            total_seats=2,
            seat_strategy="random",
            created_at=datetime.now(),
        )
    )


# (label, write, key, expected ttl in seconds)
CASES = [
    ("user session", _write_session, f"user_session:{CHAT_ID}", settings.SESSION_TTL_SECONDS),
    (
        "resume credentials",
        lambda s: s.save_resume_credentials(CHAT_ID, "010-1234-5678", "pw"),
        f"resume_credentials:{CHAT_ID}",
        settings.RESUME_TTL_SECONDS,
    ),
    (
        "app session start",
        lambda s: s.get_or_create_app_session_start(CHAT_ID),
        f"app_session_start:{CHAT_ID}",
        settings.RESUME_TTL_SECONDS,
    ),
    (
        "payment status",
        lambda s: s.save_payment_status(PaymentStatus(chat_id=CHAT_ID)),
        f"payment_status:{CHAT_ID}",
        PAYMENT_TTL,
    ),
    (
        "admin authenticated",
        lambda s: s.set_admin_authenticated(CHAT_ID),
        f"admin_authenticated:{CHAT_ID}",
        settings.ADMIN_SESSION_TTL_SECONDS,
    ),
    (
        "waiting for admin password",
        lambda s: s.set_waiting_for_admin_password(CHAT_ID),
        f"admin_password_pending:{CHAT_ID}",
        300,
    ),
    (
        "pending admin command",
        lambda s: s.set_pending_admin_command(CHAT_ID, "/subscribe"),
        f"pending_admin_command:{CHAT_ID}",
        300,
    ),
    (
        "multi reservation status",
        _write_multi_status,
        f"multi_reservation_status:{CHAT_ID}",
        PAYMENT_TTL,
    ),
    (
        "partial reservation",
        lambda s: s.save_partial_reservation(CHAT_ID, 1, {"train": "KTX 101"}),
        f"partial_reservations:{CHAT_ID}",
        7200,
    ),
    (
        "current seat index",
        lambda s: s.set_current_seat_index(CHAT_ID, 1),
        f"current_seat_index:{CHAT_ID}",
        7200,
    ),
    (
        "payment ready",
        lambda s: s.mark_payment_ready(CHAT_ID, 1),
        f"payment_ready:{CHAT_ID}:1",
        60,
    ),
]


@pytest.fixture
def storage():
    s = RedisStorage()
    yield s
    s.redis.flushdb()


@pytest.mark.parametrize(
    ("write", "key", "expected"),
    [pytest.param(w, k, e, id=label) for label, w, k, e in CASES],
)
def test_key_expires(storage, write, key, expected):
    write(storage)

    ttl = storage.redis.ttl(key)

    # -1 is "key exists, no expiry"; -2 is "no such key".
    assert ttl != -2, f"{key} was not written"
    assert ttl != -1, f"{key} was written without an expiry"
    # Upper bound catches a TTL passed where the value belongs and vice versa;
    # the lower bound only allows for the seconds the write itself took.
    assert expected - 5 <= ttl <= expected
