"""The get_all_* readers must return every record, exactly once.

They used to call KEYS, which walks the keyspace in a single blocking command;
they now call SCAN, which walks it in batches over several round trips. That is
a real behaviour change and not only a performance one: a cursor can hand the
same key back twice, and it takes more than one round trip once the keyspace is
larger than the batch size, so a reader that stopped at the first batch would
silently return a subset.

Every case here therefore writes more records than the batch size (100).
"""

from korail_bot.models import (
    PaymentStatus,
    RunningReservation,
    TrainSearchParams,
    UserProgress,
    UserSession,
)

# Larger than the SCAN batch size, so the cursor has to come back for more.
COUNT = 250


def _params():
    return TrainSearchParams(
        dep_date="20991231",
        src_locate="서울",
        dst_locate="부산",
        dep_time="080000",
    )


def test_scan_returns_every_user_session(storage):
    for chat_id in range(COUNT):
        storage.save_user_session(
            UserSession(chat_id=chat_id, in_progress=True, last_action=UserProgress.STARTED)
        )

    sessions = storage.get_all_user_sessions()

    assert len(sessions) == COUNT
    assert {s.chat_id for s in sessions} == set(range(COUNT))


def test_scan_returns_every_running_reservation(storage):
    for chat_id in range(COUNT):
        storage.save_running_reservation(
            RunningReservation(
                chat_id=chat_id,
                process_id=1000 + chat_id,
                korail_id="2071086655",
                search_params=_params(),
            )
        )

    reservations = storage.get_all_running_reservations()

    assert len(reservations) == COUNT
    assert {r.chat_id for r in reservations} == set(range(COUNT))


def test_scan_returns_every_payment_status(storage):
    for chat_id in range(COUNT):
        storage.save_payment_status(PaymentStatus(chat_id=chat_id))

    statuses = storage.get_all_payment_statuses()

    assert len(statuses) == COUNT
    assert {s.chat_id for s in statuses} == set(range(COUNT))


def test_scan_matches_only_the_requested_prefix(storage):
    """A pattern must not pick up neighbouring keyspaces."""
    storage.save_user_session(UserSession(chat_id=1, in_progress=False, last_action=0))
    storage.save_payment_status(PaymentStatus(chat_id=1))
    storage.save_running_reservation(
        RunningReservation(chat_id=1, process_id=1, korail_id="x", search_params=_params())
    )

    assert len(storage.get_all_user_sessions()) == 1
    assert len(storage.get_all_payment_statuses()) == 1
    assert len(storage.get_all_running_reservations()) == 1


def test_scan_does_not_repeat_keys(storage):
    """The cursor may report a key twice; the helper has to collapse that."""
    for chat_id in range(COUNT):
        storage.save_user_session(UserSession(chat_id=chat_id, in_progress=False, last_action=0))

    keys = storage._scan_keys("user_session:*")

    assert len(keys) == len(set(keys)) == COUNT


def test_clearing_multi_reservation_deletes_every_payment_ready_flag(storage):
    """The one SCAN whose result feeds a delete rather than a read."""
    chat_id = 555
    for seat_index in range(COUNT):
        storage.mark_payment_ready(chat_id, seat_index)
    # A flag belonging to somebody else must survive.
    storage.mark_payment_ready(999, 0)

    storage.delete_multi_reservation_status(chat_id)

    assert storage._scan_keys(f"payment_ready:{chat_id}:*") == []
    assert len(storage._scan_keys("payment_ready:999:*")) == 1
