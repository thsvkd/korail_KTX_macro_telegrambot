"""
The thread that notices a search has died.

Every way a search is meant to end goes through a callback that clears its
record. Death skips that: a process killed for memory, or crashing somewhere
the error path does not reach, leaves the record behind and tells nobody. From
the user's side that is indistinguishable from a search that has not found
anything yet, and it can go on for the whole afternoon they were counting on
the bot to be watching.

What the finding and the reporting look like is covered against a real Redis
in tests/integration/test_dead_search.py. This is about the loop around it -
the part that has to keep running, including on the pass where something
throws, because a watchdog that dies quietly is worse than none at all: it is
trusted.
"""

from unittest.mock import Mock, patch

from korail_bot.services.reservation_service import ReservationService
from korail_bot.services.search_watchdog_service import SearchWatchdogService


class WatchdogFixture:
    def setup_method(self):
        self.reservation = Mock(spec=ReservationService)
        self.reservation.detect_dead_searches.return_value = 0
        self.watchdog = SearchWatchdogService(self.reservation)

    def teardown_method(self):
        self.watchdog.stop()
        if self.watchdog._thread:
            self.watchdog._thread.join(timeout=5)


class TestOnePass(WatchdogFixture):
    """What a single check does."""

    def test_a_quiet_pass_finds_nothing(self):
        assert self.watchdog.tick() == 0

    def test_the_searches_that_died_are_counted(self):
        self.reservation.detect_dead_searches.return_value = 2

        assert self.watchdog.tick() == 2


class TestTheLoop(WatchdogFixture):
    """The part that has to survive its own failures."""

    def test_it_keeps_going_after_a_pass_that_throws(self):
        """
        Redis hiccuping on one pass must not end the watching. The failure
        this thread exists to catch is precisely the one nobody else notices.
        """
        passes = []

        def sometimes_fails():
            passes.append(len(passes))
            if len(passes) == 1:
                raise Exception("redis is down")
            if len(passes) >= 3:
                self.watchdog.stop()
            return 0

        self.reservation.detect_dead_searches.side_effect = sometimes_fails

        # The real gap between passes is half a minute; the test is about the
        # pass after the failure, not about how long it waits for it.
        with patch.object(self.watchdog._stop_event, "wait"):
            self.watchdog.run()

        assert len(passes) == 3

    def test_being_asked_to_stop_ends_the_loop(self):
        self.watchdog.stop()

        self.watchdog.run()

        self.reservation.detect_dead_searches.assert_not_called()

    def test_it_waits_between_passes_rather_than_spinning(self):
        with patch.object(self.watchdog._stop_event, "wait") as wait:
            wait.side_effect = lambda _seconds: self.watchdog.stop()

            self.watchdog.run()

        assert wait.call_args.args[0] > 0

    def test_the_wait_is_interruptible(self):
        """
        Waiting on the event rather than sleeping is what lets a shutdown
        take the thread with it instead of waiting out a poll interval.
        """
        self.watchdog.start()

        self.watchdog.stop()
        self.watchdog._thread.join(timeout=5)

        assert not self.watchdog._thread.is_alive()


class TestTheThread(WatchdogFixture):
    """Starting it, and not starting it twice."""

    def test_starting_it_puts_it_on_a_thread_of_its_own(self):
        self.watchdog.start()

        assert self.watchdog._thread.is_alive()
        assert self.watchdog._thread.daemon

    def test_starting_it_twice_does_not_get_two_watchdogs(self):
        """
        Every dead search would be reported to the user twice, from two
        threads racing to be the one that clears the record.
        """
        self.watchdog.start()
        first = self.watchdog._thread

        self.watchdog.start()

        assert self.watchdog._thread is first

    def test_it_can_be_started_again_after_being_stopped(self):
        self.watchdog.start()
        self.watchdog.stop()
        self.watchdog._thread.join(timeout=5)

        self.watchdog.start()

        assert self.watchdog._thread.is_alive()
