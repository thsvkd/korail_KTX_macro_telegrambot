"""
Stopping a search process for real.

/cancel and shutting the app down both come down to signalling a PID that was
written to Redis by an earlier run of this program - possibly days ago, on a
box that has started and finished thousands of processes since.

Two ways that goes wrong, and both are worse than the thing they are meant to
prevent. Signalling a recycled PID kills whatever the kernel handed the number
to next, which on this server is as likely to be Redis as anything else.
Failing to stop the search leaves a process asking Korail for seats every few
seconds under a user who has been told it stopped, reporting to an HTTP
endpoint that may no longer exist - so a reservation it wins expires unpaid
without anyone hearing about it.

The whole ladder runs here without a real process anywhere: /proc is stubbed,
the child handles are stand-ins, and the clock the grace period is measured
against is driven by the test.
"""

import subprocess
from unittest.mock import Mock, mock_open, patch

import pytest

from korail_bot.services import ReservationService, TelegramService
from korail_bot.storage.base import StorageInterface

MODULE = "korail_bot.services.reservation_service"
PID = 31337


def child(running=True):
    """A handle on a process this run started."""
    handle = Mock(spec=subprocess.Popen)
    handle.pid = PID
    handle.poll.return_value = None if running else 0
    handle.returncode = None if running else 0
    return handle


class ProcessFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.service = ReservationService(self.storage, self.telegram)


class TestIsItStillOurs(ProcessFixture):
    """
    The guard in front of every signal.

    PIDs get recycled. A record left behind by an earlier run can point at a
    number the kernel has since handed to something else entirely.
    """

    def owns(self, cmdline=b"python\x00-m\x00korail_bot.telegramBot.telebotBackProcess\x00"):
        with (
            patch(f"{MODULE}.os.path.isdir", return_value=True),
            patch("builtins.open", mock_open(read_data=cmdline)),
        ):
            return self.service._owns_process(PID)

    def test_one_of_our_search_processes_may_be_signalled(self):
        assert self.owns() is True

    def test_something_else_wearing_our_old_pid_is_left_alone(self):
        """
        The case this exists for. The number is the same; the process is
        somebody's database.
        """
        assert self.owns(cmdline=b"/usr/bin/redis-server\x00*:6379\x00") is False

    def test_a_pid_that_no_longer_exists_is_not_signalled(self):
        with (
            patch(f"{MODULE}.os.path.isdir", return_value=True),
            patch("builtins.open", side_effect=FileNotFoundError),
        ):
            assert self.service._owns_process(PID) is False

    def test_a_pid_that_cannot_be_inspected_is_not_signalled(self):
        """Not knowing is not permission."""
        with (
            patch(f"{MODULE}.os.path.isdir", return_value=True),
            patch("builtins.open", side_effect=PermissionError("not yours")),
        ):
            assert self.service._owns_process(PID) is False

    def test_a_host_without_proc_keeps_the_old_behaviour(self):
        """
        macOS, and a container without /proc mounted. Refusing to cancel
        anything there would be a worse failure than the one being guarded
        against, which needs a recycled PID to happen at all.
        """
        with patch(f"{MODULE}.os.path.isdir", return_value=False):
            assert self.service._owns_process(PID) is True


class TestIsItStillRunning(ProcessFixture):
    """
    Told apart from "has exited but nobody has collected it yet".

    A zombie is still a process as far as kill() is concerned, so reading one
    as alive would earn a search that finished cleanly a pointless SIGKILL.
    """

    def test_a_child_of_ours_is_asked_through_its_handle(self):
        self.service._children[PID] = child(running=True)

        assert self.service._is_running(PID) is True

    def test_a_child_that_exited_is_not_running_and_is_let_go_of(self):
        self.service._children[PID] = child(running=False)

        assert self.service._is_running(PID) is False
        assert PID not in self.service._children

    def test_a_process_from_an_earlier_run_is_asked_with_a_signal(self):
        """
        There is no handle for it - this run did not start it - so the only
        question available is whether signal 0 lands.
        """
        with patch(f"{MODULE}.os.kill") as kill:
            assert self.service._is_running(PID) is True

        kill.assert_called_once_with(PID, 0)

    def test_a_process_from_an_earlier_run_that_is_gone_says_so(self):
        with patch(f"{MODULE}.os.kill", side_effect=ProcessLookupError):
            assert self.service._is_running(PID) is False


class TestWaitingForItToGo(ProcessFixture):
    """The grace period between asking and insisting."""

    def test_it_returns_as_soon_as_the_process_is_gone(self):
        with (
            patch.object(self.service, "_is_running", return_value=False),
            patch(f"{MODULE}.time.sleep") as sleep,
        ):
            assert self.service._wait_for_exit(PID, timeout=3.0) is True

        sleep.assert_not_called()

    def test_it_gives_up_at_the_deadline(self):
        """
        Rather than waiting out a search blocked on a Korail request that is
        never going to answer.
        """
        clock = iter([0.0, 0.0, 1.0, 4.0])

        with (
            patch.object(self.service, "_is_running", return_value=True),
            patch(f"{MODULE}.time.monotonic", side_effect=lambda: next(clock)),
            patch(f"{MODULE}.time.sleep"),
        ):
            assert self.service._wait_for_exit(PID, timeout=3.0) is False


class TestTheTerminationLadder(ProcessFixture):
    """
    SIGTERM, wait, SIGKILL.

    SIGTERM is a request, and the process it goes to spends nearly all of its
    life asleep between Korail requests.
    """

    def terminate(self, exits_on_term=True, owns=True):
        with (
            patch.object(self.service, "_owns_process", return_value=owns),
            patch.object(self.service, "_wait_for_exit", return_value=exits_on_term),
            patch(f"{MODULE}.os.kill") as kill,
        ):
            result = self.service._terminate_search_process(PID)

        self.signals = [call.args[1] for call in kill.call_args_list]
        return result

    def test_a_search_that_stops_when_asked_is_only_asked(self):
        import signal

        assert self.terminate() is True
        assert self.signals == [signal.SIGTERM]

    def test_a_search_that_ignores_the_request_is_killed(self):
        """
        A search left running keeps asking Korail for seats and reports what
        it finds to an endpoint that may no longer exist, so the reservation
        it wins expires unpaid with nobody told.
        """
        import signal

        assert self.terminate(exits_on_term=False) is True
        assert self.signals == [signal.SIGTERM, signal.SIGKILL]

    def test_a_pid_that_is_not_ours_is_not_signalled_at_all(self):
        assert self.terminate(owns=False) is False
        assert self.signals == []

    def test_the_placeholder_pid_is_not_a_process(self):
        """
        What a record carries when the search was never given one. Signalling
        it would be signalling whatever happens to be there.
        """
        with patch(f"{MODULE}.os.kill") as kill:
            assert self.service._terminate_search_process(self.service._NO_PROCESS) is False

        kill.assert_not_called()

    def test_a_process_that_died_between_the_check_and_the_signal_is_let_go_of(self):
        self.service._children[PID] = child()

        with (
            patch.object(self.service, "_owns_process", return_value=True),
            patch(f"{MODULE}.os.kill", side_effect=ProcessLookupError),
        ):
            assert self.service._terminate_search_process(PID) is False

        assert PID not in self.service._children

    def test_a_signal_that_cannot_be_sent_is_reported_rather_than_raised(self):
        """
        /cancel has several other things to put down after this one, and the
        app shutting down has the rest of its shutdown to get through.
        """
        with (
            patch.object(self.service, "_owns_process", return_value=True),
            patch(f"{MODULE}.os.kill", side_effect=PermissionError("not yours")),
        ):
            assert self.service._terminate_search_process(PID) is False

    def test_a_kill_that_cannot_be_sent_is_reported_too(self):
        with (
            patch.object(self.service, "_owns_process", return_value=True),
            patch.object(self.service, "_wait_for_exit", return_value=False),
            patch(f"{MODULE}.os.kill", side_effect=[None, PermissionError("not yours")]),
        ):
            assert self.service._terminate_search_process(PID) is False

    def test_a_process_already_gone_by_the_time_it_is_killed_is_still_stopped(self):
        with (
            patch.object(self.service, "_owns_process", return_value=True),
            patch.object(self.service, "_wait_for_exit", side_effect=[False, True]),
            patch(f"{MODULE}.os.kill", side_effect=[None, ProcessLookupError]),
        ):
            assert self.service._terminate_search_process(PID) is True


class TestNotLeavingProcessesBehind(ProcessFixture):
    """
    The bot runs for weeks and starts a process per search.

    A finished child stays in the process table until its parent picks up its
    exit status, so nothing may be left uncollected.
    """

    def test_a_finished_search_is_collected(self):
        self.service._children[PID] = child(running=False)

        self.service._reap_children()

        assert PID not in self.service._children

    def test_a_running_search_is_left_alone(self):
        self.service._children[PID] = child(running=True)

        self.service._reap_children()

        assert PID in self.service._children

    def test_letting_go_of_a_handle_collects_it_on_the_way_out(self):
        """
        Dropping it without polling would strand a process that has exited
        but not been collected: nothing would be left that ever could.
        """
        handle = child(running=False)
        self.service._children[PID] = handle

        self.service._forget_child(PID)

        assert PID not in self.service._children
        handle.poll.assert_called()

    def test_letting_go_of_something_we_never_held_is_not_an_error(self):
        """A record from an earlier run of the app, which owns no handles."""
        self.service._forget_child(PID)  # must not raise


class TestStoppingEverything(ProcessFixture):
    """Shutdown, which takes the running searches with it."""

    def test_every_search_is_stopped(self):
        self.service._children = {1: child(), 2: child()}

        with patch.object(self.service, "_terminate_search_process") as stop:
            self.service.shutdown()

        assert sorted(call.args[0] for call in stop.call_args_list) == [1, 2]

    def test_one_that_will_not_stop_does_not_hold_up_the_others(self):
        self.service._children = {1: child(), 2: child()}

        with patch.object(
            self.service, "_terminate_search_process", side_effect=[Exception("nope"), None]
        ) as stop:
            self.service.shutdown()

        assert stop.call_count == 2

    def test_the_watchdog_is_told_to_stop_looking(self):
        """
        Shutdown kills the searches and leaves their records for the next run
        to resume, which is indistinguishable from a crash to anything
        watching for one. Without this it announces every search as dead on
        the way out.
        """
        with patch.object(self.service, "_terminate_search_process"):
            self.service.shutdown()

        assert self.service.detect_dead_searches() == 0


@pytest.mark.parametrize("pid", [0, -1])
def test_a_nonsense_pid_is_never_signalled(pid):
    """
    Signal 0 and negative PIDs address process groups. Passing one of those
    to kill() would signal every process in the group.
    """
    service = ReservationService(Mock(spec=StorageInterface), Mock(spec=TelegramService))

    with patch(f"{MODULE}.os.kill") as kill, patch(f"{MODULE}.os.path.isdir", return_value=True):
        service._terminate_search_process(pid)

    assert not any(call.args[0] <= 0 for call in kill.call_args_list)
