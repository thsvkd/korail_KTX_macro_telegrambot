"""
A search that cannot reach Korail must not look like a search finding nothing.

search_trains used to answer every unexpected exception with an empty list,
which is also what it answers when every train is sold out. The loop could not
tell the two apart, so a search that had stopped working kept reporting itself
as still looking - at full request rate, with nothing said to the user - for as
long as the process lived.

These cover the three parts of telling them apart: the failure is raised rather
than flattened, a run of failures backs the search off instead of hammering a
door that is not opening, and the user is told once it has gone on long enough
to mean something.
"""

from unittest.mock import Mock, patch

import pytest
from korail2 import NoResultsError

from korail_bot.services.korail_service import KorailService, SearchUnavailableError

SEARCH = "korail_bot.services.korail_service.time.sleep"
CLOCK = "korail_bot.services.korail_service.time.time"


def make_service(on_status=None, threshold=3):
    """A logged-in service with a stub Korail client and no wait jitter."""
    service = KorailService(on_status=on_status)
    service._logged_in = True
    service._korail_instance = Mock()
    service._search_interval = 1.0
    service._search_jitter = 0.0
    service._failure_threshold = threshold
    return service


def search(service):
    return service.search_trains("20991231", "서울", "부산")


# ------------------------------------------------------- raising vs returning


def test_no_trains_is_an_ordinary_answer():
    """NoResultsError means Korail replied. That is not a failure."""
    service = make_service()
    service._korail_instance.search_train.side_effect = NoResultsError()

    assert search(service) == []


def test_an_unreachable_korail_is_raised_not_flattened():
    service = make_service()
    service._korail_instance.search_train.side_effect = ConnectionError("connection reset")

    with pytest.raises(SearchUnavailableError) as caught:
        search(service)

    # The original is kept as the cause, so the log still shows what broke.
    assert isinstance(caught.value.__cause__, ConnectionError)
    assert "connection reset" in str(caught.value)


def test_a_garbled_reply_is_a_failure_too():
    """Anything the client cannot read is unreachability, not an empty result."""
    service = make_service()
    service._korail_instance.search_train.side_effect = ValueError("no JSON object")

    with pytest.raises(SearchUnavailableError):
        search(service)


# ------------------------------------------------------------------- backoff


def test_backoff_doubles_with_each_consecutive_failure():
    service = make_service()

    waits = [service.note_search_failure(ConnectionError()) for _ in range(5)]

    assert waits == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_backoff_stops_at_the_cap():
    service = make_service()
    service._failure_backoff_cap = 10.0

    waits = [service.note_search_failure(ConnectionError()) for _ in range(12)]

    assert max(waits) == 10.0
    assert waits[-1] == 10.0


def test_a_long_outage_does_not_overflow_the_backoff():
    """Doubling for hours must stay a number the sleep call can take."""
    service = make_service()
    service._failure_backoff_cap = 60.0

    for _ in range(5000):
        wait = service.note_search_failure(ConnectionError())

    assert wait == 60.0


def test_success_resets_the_backoff():
    service = make_service()
    for _ in range(4):
        service.note_search_failure(ConnectionError())

    service.note_search_success()

    assert service.note_search_failure(ConnectionError()) == 1.0


# ------------------------------------------------------------ telling the user


def test_nothing_is_said_below_the_threshold():
    """One dropped request is noise; the user should not be woken for it."""
    told = Mock()
    service = make_service(on_status=told, threshold=3)

    service.note_search_failure(ConnectionError())
    service.note_search_failure(ConnectionError())

    told.assert_not_called()


def test_the_user_is_told_once_the_run_is_long_enough():
    told = Mock()
    service = make_service(on_status=told, threshold=3)

    for _ in range(3):
        service.note_search_failure(ConnectionError())

    told.assert_called_once()
    assert "코레일 응답" in told.call_args[0][0]


def test_the_user_is_not_told_again_on_every_further_failure():
    told = Mock()
    service = make_service(on_status=told, threshold=3)

    for _ in range(30):
        service.note_search_failure(ConnectionError())

    told.assert_called_once()


def test_the_user_is_reminded_once_the_outage_drags_on():
    told = Mock()
    service = make_service(on_status=told, threshold=3)
    service._failure_realert = 1800

    with patch(CLOCK) as clock:
        clock.return_value = 1_000_000.0
        for _ in range(3):
            service.note_search_failure(ConnectionError())
        assert told.call_count == 1

        # Still failing half an hour later.
        clock.return_value = 1_000_000.0 + 1800
        service.note_search_failure(ConnectionError())

    assert told.call_count == 2


def test_recovery_is_announced_when_the_outage_was():
    told = Mock()
    service = make_service(on_status=told, threshold=3)
    for _ in range(3):
        service.note_search_failure(ConnectionError())
    told.reset_mock()

    service.note_search_success()

    told.assert_called_once()
    assert "정상" in told.call_args[0][0]


def test_recovery_is_silent_when_nothing_was_reported():
    """A blip the user never heard about must not produce an all-clear."""
    told = Mock()
    service = make_service(on_status=told, threshold=3)
    service.note_search_failure(ConnectionError())

    service.note_search_success()

    told.assert_not_called()


def test_success_with_no_failures_says_nothing():
    told = Mock()
    service = make_service(on_status=told)

    service.note_search_success()

    told.assert_not_called()


def test_a_broken_notifier_does_not_stop_the_search():
    """Telegram being down must not take the search down with it."""
    told = Mock(side_effect=RuntimeError("telegram unreachable"))
    service = make_service(on_status=told, threshold=1)

    assert service.note_search_failure(ConnectionError()) == 1.0
    told.assert_called_once()


def test_no_notifier_is_allowed():
    service = make_service(on_status=None, threshold=1)

    assert service.note_search_failure(ConnectionError()) == 1.0


# ------------------------------------------------------------ the loop itself


def test_the_loop_keeps_searching_through_an_outage():
    """
    The whole point: failures must not end the search, and must not spin.

    Before this, an unreachable Korail produced an empty list, so the loop
    treated every attempt as 'sold out' and retried at the full search rate
    forever without telling anyone.
    """
    told = Mock()
    service = make_service(on_status=told, threshold=3)
    service._korail_instance.search_train.side_effect = ConnectionError("refused")

    with patch(SEARCH) as sleep:
        result = service.search_and_reserve_loop(
            dep_date="20991231",
            src_locate="서울",
            dst_locate="부산",
            max_attempts=6,
        )

    assert result is None  # gave up on max_attempts, did not crash
    assert service._korail_instance.search_train.call_count == 6
    told.assert_called_once()
    # Backed off rather than retrying at the configured 1s.
    assert [call.args[0] for call in sleep.call_args_list] == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0]


def test_the_loop_recovers_when_korail_comes_back():
    service = make_service(threshold=2)
    service._korail_instance.search_train.side_effect = [
        ConnectionError("refused"),
        ConnectionError("refused"),
        NoResultsError(),
        NoResultsError(),
    ]

    with patch(SEARCH):
        service.search_and_reserve_loop(
            dep_date="20991231",
            src_locate="서울",
            dst_locate="부산",
            max_attempts=4,
        )

    # The streak was cleared by the first answer, so the search is back at its
    # normal rate rather than still doubling.
    assert service._failure_streak == 0
