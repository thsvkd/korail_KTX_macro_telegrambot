"""
Unit tests for how Korail credentials are handed to the background process.

Credentials must never appear in the child's argv: anything on a command
line is readable by every process on the host via `ps` or /proc.
"""
import json
from unittest.mock import Mock, patch

import pytest

from models import TrainSearchParams
from services.reservation_service import ReservationService
from storage.base import StorageInterface


USERNAME = "010-1234-5678"
PASSWORD = "sup3r-s3cret-pw"


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
        passenger_count=2,
        seat_strategy="random"
    )


@pytest.fixture
def service():
    storage = Mock(spec=StorageInterface)
    storage.get_all_subscribers.return_value = []
    storage.get_user_session.return_value = None
    return ReservationService(storage, Mock())


def _start(service, search_params):
    """Run start_reservation_process against a mocked Popen."""
    with patch('subprocess.Popen') as popen:
        process = Mock()
        process.pid = 4242
        popen.return_value = process

        success = service.start_reservation_process(
            chat_id=555,
            username=USERNAME,
            password=PASSWORD,
            search_params=search_params
        )

    return success, popen, process


class TestCredentialHandoff:
    """The password goes over stdin, never over argv."""

    def test_password_absent_from_argv(self, service, search_params):
        success, popen, _ = _start(service, search_params)

        assert success is True
        argv = popen.call_args[0][0]
        assert PASSWORD not in argv
        assert USERNAME not in argv
        # Nothing embedded in a longer argument either.
        assert not any(PASSWORD in str(arg) for arg in argv)
        assert not any(USERNAME in str(arg) for arg in argv)

    def test_search_parameters_still_in_argv(self, service, search_params):
        _, popen, _ = _start(service, search_params)

        argv = popen.call_args[0][0]
        assert argv[:3] == ['python', '-m', 'telegramBot.telebotBackProcess']
        # Order must match what telebotBackProcess reads.
        assert argv[3:] == [
            "20991231", "서울", "부산", "090000",
            "TrainType.KTX", "ReserveOption.GENERAL_FIRST",
            "555", "1800", "2", "random"
        ]

    def test_credentials_written_to_stdin(self, service, search_params):
        _, _, process = _start(service, search_params)

        process.stdin.write.assert_called_once()
        written = process.stdin.write.call_args[0][0]
        payload = json.loads(written.decode('utf-8'))

        assert payload == {"username": USERNAME, "password": PASSWORD}

    def test_stdin_is_closed_so_the_child_can_proceed(self, service, search_params):
        _, _, process = _start(service, search_params)

        process.stdin.close.assert_called_once()

    def test_stdin_is_a_pipe(self, service, search_params):
        import subprocess

        _, popen, _ = _start(service, search_params)

        assert popen.call_args[1]["stdin"] == subprocess.PIPE


class TestStatusPrivacy:
    """/status is public, so it must not leak other users' phone numbers."""

    def test_status_without_own_reservation_hides_others(self, service, search_params):
        other = Mock()
        other.chat_id = 999
        other.korail_id = "010-9999-8888"
        other.search_params = search_params
        service.storage.get_all_running_reservations.return_value = [other]

        status = service.get_status(chat_id=555)

        assert "010-9999-8888" not in status
        assert "9999" not in status
        assert "1개" in status

    def test_status_shows_own_reservation_details(self, service, search_params):
        mine = Mock()
        mine.chat_id = 555
        mine.korail_id = USERNAME
        mine.search_params = search_params
        service.storage.get_all_running_reservations.return_value = [mine]

        status = service.get_status(chat_id=555)

        assert "서울" in status
        assert "부산" in status
        assert "20991231" in status
        # Not even the caller's own number needs to be echoed back.
        assert USERNAME not in status

    def test_subscriber_notification_masks_the_phone_number(self, service, search_params):
        service.storage.get_all_subscribers.return_value = [1, 2]

        service._notify_subscribers_start(USERNAME, search_params)

        message = service.telegram.send_to_multiple.call_args[0][1]
        assert USERNAME not in message
        assert "010-****-5678" in message
