"""
Unit tests for how a search process reacts to being told to stop.

A search is a child process that spends nearly all of its life asleep between
Korail requests, so a stop signal almost always lands in the middle of the
loop. These cover it leaving quietly instead of crashing, and instead of
reporting a failure the user never caused.

They run without Redis or a network.
"""
import signal

import pytest

from telegramBot.telebotBackProcess import SearchStopped, install_shutdown_handlers


@pytest.fixture
def restore_handlers():
    """Signal handlers are process-wide; put back whatever pytest had."""
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


class TestStopSignal:
    """Both ways of asking a search to stop end it the same way."""

    def test_sigterm_stops_the_search(self, restore_handlers):
        """How /cancel and the app shutting down end a search."""
        install_shutdown_handlers()

        with pytest.raises(SearchStopped) as stopped:
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

        assert stopped.value.signal_name == "SIGTERM"

    def test_sigint_stops_the_search(self, restore_handlers):
        """Without this a Ctrl-C prints a traceback that reads like a crash."""
        install_shutdown_handlers()

        with pytest.raises(SearchStopped) as stopped:
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)

        assert stopped.value.signal_name == "SIGINT"

    def test_being_stopped_is_not_a_failure(self):
        assert SearchStopped(signal.SIGTERM).code == 0

    def test_the_search_loop_cannot_swallow_it(self):
        """
        The loop wraps everything in `except Exception` and turns what it
        catches into an error message to the user. Being asked to stop is
        neither: whoever sent the signal already knows the search is over.
        """
        assert not issubclass(SearchStopped, Exception)
        assert issubclass(SearchStopped, SystemExit)
