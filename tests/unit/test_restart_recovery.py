"""
Unit tests for restart recovery.

A search runs in a child process, so restarting the app abandons it while its
record survives in Redis. These cover resuming it, giving up on it safely, and
not signalling a PID that is no longer ours.

They run without Redis or a network.
"""

import contextlib
import signal
from unittest.mock import Mock, patch

import pytest

from korail_bot.config.settings import settings
from korail_bot.models import RunningReservation, TrainSearchParams
from korail_bot.services.reservation_service import ReservationService
from korail_bot.storage.base import StorageInterface

CURRENT_RUN = "run-current"
PREVIOUS_RUN = "run-previous"
USERNAME = "010-1234-5678"
PASSWORD = "korail-password"


@pytest.fixture
def search_params():
    return TrainSearchParams(
        dep_date="20991231",
        src_locate="서울",
        dst_locate="부산",
        dep_time="090000",
        max_dep_time="1800",
        train_type="TrainType.KTX",
        train_type_display="KTX",
        special_option="ReserveOption.GENERAL_FIRST",
        special_option_display="GENERAL_FIRST",
        passenger_count=1,
        seat_strategy="consecutive",
    )


@pytest.fixture
def service():
    storage = Mock(spec=StorageInterface)
    storage.get_all_running_reservations.return_value = []
    storage.get_partial_reservations.return_value = []
    storage.get_resume_credentials.return_value = None
    storage.get_user_session.return_value = None
    # Nothing running for this chat; otherwise the duplicate guard refuses to
    # start, because a bare Mock answers every lookup with a truthy Mock.
    storage.get_running_reservation.return_value = None
    return ReservationService(storage, Mock())


def make_reservation(search_params, run_id=PREVIOUS_RUN, pid=4242, chat_id=555):
    return RunningReservation(
        chat_id=chat_id,
        process_id=pid,
        korail_id=USERNAME,
        search_params=search_params,
        run_id=run_id,
    )


class TestStaleDetection:
    """A record is only abandoned if it came from an earlier run."""

    def test_record_from_this_run_is_not_stale(self, search_params):
        reservation = make_reservation(search_params, run_id=CURRENT_RUN)

        assert reservation.is_stale(CURRENT_RUN) is False

    def test_record_from_an_earlier_run_is_stale(self, search_params):
        assert make_reservation(search_params).is_stale(CURRENT_RUN) is True

    def test_record_predating_this_feature_is_stale(self, search_params):
        """Older records carry no run id at all."""
        assert make_reservation(search_params, run_id="").is_stale(CURRENT_RUN) is True

    def test_live_reservations_are_left_alone(self, service, search_params):
        service.storage.get_all_running_reservations.return_value = [
            make_reservation(search_params, run_id=CURRENT_RUN)
        ]

        with patch.object(settings, "RUN_ID", CURRENT_RUN):
            summary = service.reconcile_after_restart()

        assert summary == {"resumed": 0, "interrupted": 0, "failed": 0}
        service.storage.delete_running_reservation.assert_not_called()


class TestResume:
    """Picking an interrupted search back up."""

    def _reconcile(self, service, search_params, resume_enabled=True):
        service.storage.get_all_running_reservations.return_value = [
            make_reservation(search_params)
        ]
        with (
            patch.object(settings, "RUN_ID", CURRENT_RUN),
            patch.object(settings, "RESUME_ON_RESTART", resume_enabled),
            patch.object(service, "start_reservation_process", return_value=True) as start,
        ):
            summary = service.reconcile_after_restart()
        return summary, start

    def test_search_is_restarted_with_the_stored_credentials(self, service, search_params):
        service.storage.get_resume_credentials.return_value = (USERNAME, PASSWORD)

        summary, start = self._reconcile(service, search_params)

        assert summary["resumed"] == 1
        kwargs = start.call_args.kwargs
        assert kwargs["username"] == USERNAME
        assert kwargs["password"] == PASSWORD
        assert kwargs["search_params"] is search_params
        # Marked as a resume so the user is not told a new search began.
        assert kwargs["resumed"] is True

    def test_record_survives_a_resume(self, service, search_params):
        service.storage.get_resume_credentials.return_value = (USERNAME, PASSWORD)

        self._reconcile(service, search_params)

        service.storage.delete_resume_credentials.assert_not_called()

    def test_without_credentials_the_search_is_abandoned(self, service, search_params):
        service.storage.get_resume_credentials.return_value = None

        summary, start = self._reconcile(service, search_params)

        assert summary["interrupted"] == 1
        start.assert_not_called()
        service.storage.delete_running_reservation.assert_called_once_with(555)
        service.storage.delete_resume_credentials.assert_called_once_with(555)

    def test_disabled_recovery_abandons_the_search(self, service, search_params):
        service.storage.get_resume_credentials.return_value = (USERNAME, PASSWORD)

        summary, start = self._reconcile(service, search_params, resume_enabled=False)

        assert summary["interrupted"] == 1
        start.assert_not_called()

    def test_already_reserved_seats_are_never_rebooked(self, service, search_params):
        """
        Random seating books one seat at a time. Restarting that search from
        scratch would reserve seats the user already holds.
        """
        service.storage.get_resume_credentials.return_value = (USERNAME, PASSWORD)
        service.storage.get_partial_reservations.return_value = [
            {"seat_index": 0, "train_info": "KTX 101"}
        ]

        summary, start = self._reconcile(service, search_params)

        assert summary["interrupted"] == 1
        start.assert_not_called()

    def test_user_is_told_when_the_search_was_abandoned(self, service, search_params):
        service.storage.get_resume_credentials.return_value = None

        self._reconcile(service, search_params)

        message = service.telegram.send_message.call_args[0][1]
        assert "중단" in message
        assert "서울" in message and "부산" in message

    def test_partial_reservation_notice_points_at_payment(self, service, search_params):
        service.storage.get_resume_credentials.return_value = None
        service.storage.get_partial_reservations.return_value = [{"seat_index": 0}]

        self._reconcile(service, search_params)

        message = service.telegram.send_message.call_args[0][1]
        assert "중복" in message
        # The configured payment link, not a hardcoded domain: Korail has
        # already moved the site once, and what this test is about is that
        # the notice points somewhere the user can pay.
        assert settings.KORAIL_PAYMENT_URL in message

    def test_one_broken_record_does_not_stop_the_others(self, service, search_params):
        good = make_reservation(search_params, chat_id=111)
        service.storage.get_all_running_reservations.return_value = [
            make_reservation(search_params, chat_id=999),
            good,
        ]

        def credentials(chat_id):
            if chat_id == 999:
                raise RuntimeError("redis blew up")
            return None

        service.storage.get_resume_credentials.side_effect = credentials

        with patch.object(settings, "RUN_ID", CURRENT_RUN):
            summary = service.reconcile_after_restart()

        assert summary["failed"] == 1
        assert summary["interrupted"] == 1


class TestProcessSafety:
    """PIDs get recycled; signalling one blindly can kill a stranger."""

    def test_recorded_pid_of_another_program_is_not_signalled(self, service):
        with (
            patch("os.path.isdir", return_value=True),
            patch("builtins.open", side_effect=lambda *a, **k: _cmdline(b"/usr/bin/sshd\x00")),
            patch("os.kill") as kill,
        ):
            assert service._terminate_search_process(4242) is False

        kill.assert_not_called()

    def test_our_own_process_is_signalled(self, service):
        process = _FakeProcess()

        with _ours(), patch("os.kill", process.kill):
            assert service._terminate_search_process(4242) is True

        assert process.signals == [signal.SIGTERM]

    def test_dead_pid_is_not_signalled(self, service):
        with (
            patch("os.path.isdir", return_value=True),
            patch("builtins.open", side_effect=FileNotFoundError),
            patch("os.kill") as kill,
        ):
            assert service._terminate_search_process(4242) is False

        kill.assert_not_called()

    def test_placeholder_pid_is_ignored(self, service):
        with patch("os.kill") as kill:
            assert service._terminate_search_process(9999999) is False

        kill.assert_not_called()

    def test_without_proc_the_signal_is_still_sent(self, service):
        """On systems without /proc there is nothing to verify against."""
        process = _FakeProcess()

        with patch("os.path.isdir", return_value=False), patch("os.kill", process.kill):
            assert service._terminate_search_process(4242) is True

        assert process.signals == [signal.SIGTERM]


class TestTermination:
    """
    A search that will not stop has to be made to.

    It keeps asking Korail for seats and reports what it finds to an endpoint
    that dies with the app, so a reservation it wins would expire unpaid
    without the user ever being told.
    """

    def test_a_process_ignoring_sigterm_is_killed(self, service):
        process = _FakeProcess(dies_on=None)  # only SIGKILL gets through

        with (
            _ours(),
            patch("os.kill", process.kill),
            patch.object(service, "_TERMINATE_GRACE_SECONDS", 0.05),
        ):
            assert service._terminate_search_process(4242) is True

        assert process.signals == [signal.SIGTERM, signal.SIGKILL]
        assert 4242 in process.dead

    def test_a_process_that_stops_is_not_killed(self, service):
        process = _FakeProcess()

        with (
            _ours(),
            patch("os.kill", process.kill),
            patch.object(service, "_TERMINATE_GRACE_SECONDS", 0.05),
        ):
            service._terminate_search_process(4242)

        assert signal.SIGKILL not in process.signals

    def test_a_finished_child_is_never_killed(self, service):
        """
        A child that exited but has not been collected still answers kill(),
        so liveness is read off the process handle rather than the signal.
        Counting that zombie as alive would earn it a pointless SIGKILL.
        """
        service._children[4242] = _FakeChild(4242, returncode=0)
        process = _FakeProcess()

        with (
            _ours(),
            patch("os.kill", process.kill),
            patch.object(service, "_TERMINATE_GRACE_SECONDS", 5),
        ):
            service._terminate_search_process(4242)

        assert process.signals == [signal.SIGTERM]
        assert 4242 not in service._children

    def test_finished_children_are_collected(self, service):
        service._children[1] = _FakeChild(1, returncode=0)
        service._children[2] = _FakeChild(2, returncode=None)

        service._reap_children()

        assert list(service._children) == [2]


class TestShutdown:
    """Stopping the app stops the searches it started."""

    def _shutdown(self, service, process):
        with (
            _ours(),
            patch("os.kill", process.kill),
            patch.object(service, "_TERMINATE_GRACE_SECONDS", 0.05),
        ):
            service.shutdown()

    def test_every_running_search_is_stopped(self, service):
        process = _FakeProcess()
        service._children[4242] = process.child(4242)
        service._children[4243] = process.child(4243)

        self._shutdown(service, process)

        assert process.signals == [signal.SIGTERM, signal.SIGTERM]
        assert process.dead == {4242, 4243}

    def test_the_records_survive_so_the_searches_can_be_resumed(self, service):
        process = _FakeProcess()
        service._children[4242] = process.child(4242)

        self._shutdown(service, process)

        service.storage.delete_running_reservation.assert_not_called()
        service.storage.delete_resume_credentials.assert_not_called()

    def test_a_search_that_already_finished_is_not_signalled(self, service):
        service._children[4242] = _FakeChild(4242, returncode=0)

        with _ours(), patch("os.kill") as kill:
            service.shutdown()

        kill.assert_not_called()

    def test_nothing_to_stop_is_not_an_error(self, service):
        with patch("os.kill") as kill:
            service.shutdown()

        kill.assert_not_called()


class TestSearchIsolation:
    """The bot is the only thing that ends a search."""

    def test_the_search_runs_in_a_session_of_its_own(self, service, search_params):
        """
        Otherwise a Ctrl-C in the terminal running the bot reaches the search
        directly, killing it mid-request behind the bot's back.
        """
        with patch("subprocess.Popen") as popen:
            popen.return_value.pid = 4242
            service.start_reservation_process(
                chat_id=555, username=USERNAME, password=PASSWORD, search_params=search_params
            )

        assert popen.call_args.kwargs["start_new_session"] is True


class _cmdline:
    """Minimal stand-in for an open /proc/<pid>/cmdline file."""

    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.content


@contextlib.contextmanager
def _ours():
    """Make /proc report the PID under test as one of our search processes."""
    cmdline = b"python\x00-m\x00korail_bot.telegramBot.telebotBackProcess\x00"
    with (
        patch("os.path.isdir", return_value=True),
        patch("builtins.open", side_effect=lambda *a, **k: _cmdline(cmdline)),
    ):
        yield


class _FakeProcess:
    """
    Stands in for the processes signals are sent to, one entry per PID.

    Signal 0 is the liveness probe and never kills anything; `dies_on` is the
    signal these processes act on, or None for one that ignores everything
    SIGKILL does not enforce.
    """

    def __init__(self, dies_on=signal.SIGTERM):
        self.dies_on = dies_on
        self.dead = set()
        self.signals = []

    def kill(self, pid, sig):
        if pid in self.dead:
            raise ProcessLookupError(pid)
        if sig == 0:
            return
        self.signals.append(sig)
        if sig == signal.SIGKILL or sig == self.dies_on:
            self.dead.add(pid)

    def child(self, pid):
        """A Popen stand-in for this PID, which exits when the PID dies."""
        return _FakeChild(pid, process=self)


class _FakeChild:
    """Stand-in for the Popen handle on a search process."""

    def __init__(self, pid, returncode=None, process=None):
        self.pid = pid
        self._returncode = returncode
        self._process = process

    @property
    def returncode(self):
        return self.poll()

    def poll(self):
        if self._process is not None and self.pid in self._process.dead:
            return 0
        return self._returncode
