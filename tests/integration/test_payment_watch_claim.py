"""
The claim that decides who watches a payment.

Two things can watch one: the search process that took the seat, which is
already logged in, and the app, which picks up whatever nobody else has. If
both watch the same reservation, both see it disappear and the user is told
twice that they paid.

So this is a real claim in Redis rather than a note, and it is exercised
against a real Redis: SET NX either takes it or does not, and the expiry that
hands it over when a watcher dies is the whole reason a restart does not leave
a payment unwatched.
"""

import time

CHAT_ID = 771122
SEARCH = "search:1234"
APP = "app:5678"


def test_the_first_asker_gets_it(storage):
    assert storage.claim_payment_watch(CHAT_ID, SEARCH, 30) is True

    storage.release_payment_watch(CHAT_ID, SEARCH)


def test_the_second_asker_is_turned_away(storage):
    storage.claim_payment_watch(CHAT_ID, SEARCH, 30)

    assert storage.claim_payment_watch(CHAT_ID, APP, 30) is False

    storage.release_payment_watch(CHAT_ID, SEARCH)


def test_the_holder_may_renew_it(storage):
    storage.claim_payment_watch(CHAT_ID, SEARCH, 30)

    assert storage.claim_payment_watch(CHAT_ID, SEARCH, 30) is True

    storage.release_payment_watch(CHAT_ID, SEARCH)


def test_renewing_pushes_the_expiry_back(storage):
    storage.claim_payment_watch(CHAT_ID, SEARCH, 5)

    storage.claim_payment_watch(CHAT_ID, SEARCH, 60)

    assert storage.redis.ttl(f"payment_watch:{CHAT_ID}") > 5

    storage.release_payment_watch(CHAT_ID, SEARCH)


def test_a_watcher_that_stopped_renewing_loses_it(storage):
    """A killed search process is exactly this, and the app has to take over."""
    storage.claim_payment_watch(CHAT_ID, SEARCH, 1)

    time.sleep(1.2)

    assert storage.claim_payment_watch(CHAT_ID, APP, 30) is True

    storage.release_payment_watch(CHAT_ID, APP)


def test_releasing_frees_it_for_the_next_watcher(storage):
    storage.claim_payment_watch(CHAT_ID, SEARCH, 30)

    storage.release_payment_watch(CHAT_ID, SEARCH)

    assert storage.claim_payment_watch(CHAT_ID, APP, 30) is True

    storage.release_payment_watch(CHAT_ID, APP)


def test_one_watcher_cannot_release_another_watcher_s_claim(storage):
    """Otherwise a late release would put two watchers back on one payment."""
    storage.claim_payment_watch(CHAT_ID, APP, 30)

    storage.release_payment_watch(CHAT_ID, SEARCH)

    assert storage.claim_payment_watch(CHAT_ID, SEARCH, 30) is False

    storage.release_payment_watch(CHAT_ID, APP)


def test_watches_are_per_chat(storage):
    storage.claim_payment_watch(CHAT_ID, SEARCH, 30)

    assert storage.claim_payment_watch(CHAT_ID + 1, APP, 30) is True

    storage.release_payment_watch(CHAT_ID, SEARCH)
    storage.release_payment_watch(CHAT_ID + 1, APP)
