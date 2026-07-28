"""
Unit tests for restart recovery.

A search runs in a child process, so restarting the app abandons it while its
record survives in Redis. These cover resuming it, giving up on it safely, and
not signalling a PID that is no longer ours.

They run without Redis or a network.
"""
import os
from unittest.mock import Mock, patch

import pytest

from config.settings import settings
from models import RunningReservation, TrainSearchParams
from services.reservation_service import ReservationService
from storage.base import StorageInterface


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
        seat_strategy="consecutive"
    )


@pytest.fixture
def service():
    storage = Mock(spec=StorageInterface)
    storage.get_all_running_reservations.return_value = []
    storage.get_partial_reservations.return_value = []
    storage.get_resume_credentials.return_value = None
    storage.get_user_session.return_value = None
    storage.get_all_subscribers.return_value = []
    return ReservationService(storage, Mock())


def make_reservation(search_params, run_id=PREVIOUS_RUN, pid=4242, chat_id=555):
    return RunningReservation(
        chat_id=chat_id,
        process_id=pid,
        korail_id=USERNAME,
        search_params=search_params,
        run_id=run_id
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

        with patch.object(settings, 'RUN_ID', CURRENT_RUN):
            summary = service.reconcile_after_restart()

        assert summary == {'resumed': 0, 'interrupted': 0, 'failed': 0}
        service.storage.delete_running_reservation.assert_not_called()


class TestResume:
    """Picking an interrupted search back up."""

    def _reconcile(self, service, search_params, resume_enabled=True):
        service.storage.get_all_running_reservations.return_value = [
            make_reservation(search_params)
        ]
        with patch.object(settings, 'RUN_ID', CURRENT_RUN), \
             patch.object(settings, 'RESUME_ON_RESTART', resume_enabled), \
             patch.object(service, 'start_reservation_process', return_value=True) as start:
            summary = service.reconcile_after_restart()
        return summary, start

    def test_search_is_restarted_with_the_stored_credentials(self, service, search_params):
        service.storage.get_resume_credentials.return_value = (USERNAME, PASSWORD)

        summary, start = self._reconcile(service, search_params)

        assert summary['resumed'] == 1
        kwargs = start.call_args.kwargs
        assert kwargs['username'] == USERNAME
        assert kwargs['password'] == PASSWORD
        assert kwargs['search_params'] is search_params
        # Marked as a resume so the user is not told a new search began.
        assert kwargs['resumed'] is True

    def test_record_survives_a_resume(self, service, search_params):
        service.storage.get_resume_credentials.return_value = (USERNAME, PASSWORD)

        self._reconcile(service, search_params)

        service.storage.delete_resume_credentials.assert_not_called()

    def test_without_credentials_the_search_is_abandoned(self, service, search_params):
        service.storage.get_resume_credentials.return_value = None

        summary, start = self._reconcile(service, search_params)

        assert summary['interrupted'] == 1
        start.assert_not_called()
        service.storage.delete_running_reservation.assert_called_once_with(555)
        service.storage.delete_resume_credentials.assert_called_once_with(555)

    def test_disabled_recovery_abandons_the_search(self, service, search_params):
        service.storage.get_resume_credentials.return_value = (USERNAME, PASSWORD)

        summary, start = self._reconcile(service, search_params, resume_enabled=False)

        assert summary['interrupted'] == 1
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

        assert summary['interrupted'] == 1
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
        assert "letskorail" in message

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

        with patch.object(settings, 'RUN_ID', CURRENT_RUN):
            summary = service.reconcile_after_restart()

        assert summary['failed'] == 1
        assert summary['interrupted'] == 1


class TestProcessSafety:
    """PIDs get recycled; signalling one blindly can kill a stranger."""

    def test_recorded_pid_of_another_program_is_not_signalled(self, service):
        with patch('os.path.isdir', return_value=True), \
             patch('builtins.open', side_effect=lambda *a, **k: _cmdline(b"/usr/bin/sshd\x00")), \
             patch('os.kill') as kill:
            assert service._terminate_search_process(4242) is False

        kill.assert_not_called()

    def test_our_own_process_is_signalled(self, service):
        with patch('os.path.isdir', return_value=True), \
             patch('builtins.open',
                   side_effect=lambda *a, **k: _cmdline(b"python\x00-m\x00telegramBot.telebotBackProcess\x00")), \
             patch('os.kill') as kill:
            assert service._terminate_search_process(4242) is True

        kill.assert_called_once()

    def test_dead_pid_is_not_signalled(self, service):
        with patch('os.path.isdir', return_value=True), \
             patch('builtins.open', side_effect=FileNotFoundError), \
             patch('os.kill') as kill:
            assert service._terminate_search_process(4242) is False

        kill.assert_not_called()

    def test_placeholder_pid_is_ignored(self, service):
        with patch('os.kill') as kill:
            assert service._terminate_search_process(9999999) is False

        kill.assert_not_called()

    def test_without_proc_the_signal_is_still_sent(self, service):
        """On systems without /proc there is nothing to verify against."""
        with patch('os.path.isdir', return_value=False), patch('os.kill') as kill:
            assert service._terminate_search_process(4242) is True

        kill.assert_called_once()


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
