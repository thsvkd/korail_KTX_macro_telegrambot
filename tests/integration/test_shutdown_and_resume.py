"""
Integration tests for stopping the bot and starting it again.

Stopping the bot has to take its searches with it - they report back to an
HTTP endpoint that dies with it - while leaving behind enough in Redis to
pick them back up. These cover that round trip against a real Redis, so the
serialisation and the credential encryption are exercised for real: a search
that cannot be decrypted after the restart is a search that is never resumed.
"""
import signal

import pytest
from unittest.mock import Mock, patch

from config.settings import settings
from models import TrainSearchParams
from services import ReservationService, TelegramService
from storage import RedisStorage


FIRST_RUN = "run-before-the-restart"
SECOND_RUN = "run-after-the-restart"
CHAT_ID = 987654
USERNAME = "010-1234-5678"
PASSWORD = "korail-password"


@pytest.fixture
def search_params():
    return TrainSearchParams(
        dep_date="20991231",
        src_locate="서울",
        dst_locate="오송",
        dep_time="180000",
        max_dep_time="1930",
        train_type="TrainType.ALL",
        train_type_display="전체",
        special_option="ReserveOption.SPECIAL_FIRST",
        special_option_display="SPECIAL_FIRST",
        passenger_count=1,
        seat_strategy="consecutive"
    )


@pytest.fixture
def storage():
    storage = RedisStorage()
    yield storage
    storage.redis.flushdb()


def _service(storage):
    """A reservation service as a fresh run of the app would build it."""
    return ReservationService(storage, Mock(spec=TelegramService))


def _start(service, search_params, pid=424242):
    """Start a search with a stand-in for the child process."""
    with patch('subprocess.Popen') as popen:
        popen.return_value.pid = pid
        started = service.start_reservation_process(
            chat_id=CHAT_ID,
            username=USERNAME,
            password=PASSWORD,
            search_params=search_params
        )
    assert started is True
    return service._children[pid]


def _stop(service, child):
    """Shut the app down with the search still running."""
    signalled = []

    def kill(pid, sig):
        signalled.append(sig)
        child.poll.return_value = 0  # the search acts on it

    child.poll.return_value = None
    with patch.object(service, '_owns_process', return_value=True), \
         patch('os.kill', kill):
        service.shutdown()
    return signalled


class TestShutdownLeavesTheSearchRecoverable:
    """What the next start needs has to survive the stop."""

    def test_the_running_search_is_stopped(self, storage, search_params):
        service = _service(storage)
        with patch.object(settings, 'RUN_ID', FIRST_RUN):
            child = _start(service, search_params)

        assert _stop(service, child) == [signal.SIGTERM]

    def test_the_record_and_the_credentials_outlive_the_shutdown(
        self, storage, search_params
    ):
        service = _service(storage)
        with patch.object(settings, 'RUN_ID', FIRST_RUN):
            child = _start(service, search_params)

        _stop(service, child)

        assert storage.get_running_reservation(CHAT_ID) is not None
        assert storage.get_resume_credentials(CHAT_ID) == (USERNAME, PASSWORD)


class TestRestartResumesTheSearch:
    """A search interrupted by a restart is picked back up."""

    def _restart(self, storage, search_params, resume_enabled=True):
        """Stop one run of the app, then reconcile as the next one."""
        first = _service(storage)
        with patch.object(settings, 'RUN_ID', FIRST_RUN):
            child = _start(first, search_params)
        _stop(first, child)

        second = _service(storage)
        with patch.object(settings, 'RUN_ID', SECOND_RUN), \
             patch.object(settings, 'RESUME_ON_RESTART', resume_enabled), \
             patch.object(second, '_owns_process', return_value=False), \
             patch('subprocess.Popen') as popen:
            popen.return_value.pid = 525252
            summary = second.reconcile_after_restart()
        return summary, second, popen

    def test_the_search_starts_again_with_the_same_parameters(
        self, storage, search_params
    ):
        summary, service, popen = self._restart(storage, search_params)

        assert summary == {'resumed': 1, 'interrupted': 0, 'failed': 0}
        argv = popen.call_args.args[0]
        assert search_params.dep_date in argv
        assert search_params.src_locate in argv
        assert search_params.dst_locate in argv
        assert str(CHAT_ID) in argv

    def test_the_credentials_survive_the_round_trip_through_redis(
        self, storage, search_params
    ):
        """
        They are encrypted on the way in, and a search whose credentials no
        longer decrypt can only be abandoned.
        """
        _, _, popen = self._restart(storage, search_params)

        written = popen.return_value.stdin.write.call_args.args[0]
        assert USERNAME in written.decode()
        assert PASSWORD in written.decode()

    def test_the_record_now_belongs_to_the_new_run(self, storage, search_params):
        self._restart(storage, search_params)

        reservation = storage.get_running_reservation(CHAT_ID)
        assert reservation.run_id == SECOND_RUN
        assert reservation.process_id == 525252

    def test_the_user_is_told_the_search_resumed_rather_than_started(
        self, storage, search_params
    ):
        _, service, _ = self._restart(storage, search_params)

        message = service.telegram.send_message.call_args.args[1]
        assert "다시 시작" in message
        assert search_params.src_locate in message
        # A resume is not news for the subscriber list.
        service.telegram.send_to_multiple.assert_not_called()

    def test_recovery_can_be_turned_off(self, storage, search_params):
        summary, _, popen = self._restart(
            storage, search_params, resume_enabled=False
        )

        assert summary == {'resumed': 0, 'interrupted': 1, 'failed': 0}
        popen.assert_not_called()
        assert storage.get_running_reservation(CHAT_ID) is None
        assert storage.get_resume_credentials(CHAT_ID) is None


class TestAppSessionOutlivesTheRestart:
    """
    The Korail client stamps every request with when its app was started.

    A search lives in a child process the bot restarts, and a restart of the
    bot is not the user relaunching their app.
    """

    def test_a_resumed_search_keeps_the_app_session_it_began_with(
        self, storage, search_params
    ):
        first = _service(storage)
        with patch.object(settings, 'RUN_ID', FIRST_RUN):
            child = _start(first, search_params)
        # What the search process stamps its requests with.
        before = storage.get_or_create_app_session_start(CHAT_ID)
        _stop(first, child)

        second = _service(storage)
        with patch.object(settings, 'RUN_ID', SECOND_RUN), \
             patch.object(second, '_owns_process', return_value=False), \
             patch('subprocess.Popen') as popen:
            popen.return_value.pid = 525252
            summary = second.reconcile_after_restart()

        assert summary['resumed'] == 1
        assert storage.get_or_create_app_session_start(CHAT_ID) == before

    def test_the_same_user_is_handed_the_same_session_back(self, storage):
        first = storage.get_or_create_app_session_start(CHAT_ID)

        assert storage.get_or_create_app_session_start(CHAT_ID) == first

    def test_one_user_ending_their_search_leaves_the_others_alone(self, storage):
        mine = storage.get_or_create_app_session_start(CHAT_ID)
        storage.get_or_create_app_session_start(CHAT_ID + 1)

        storage.delete_app_session_start(CHAT_ID + 1)

        assert storage.redis.get(f"app_session_start:{CHAT_ID + 1}") is None
        assert storage.get_or_create_app_session_start(CHAT_ID) == mine

    def test_ending_the_search_ends_the_app_session(self, storage, search_params):
        service = _service(storage)
        with patch.object(settings, 'RUN_ID', FIRST_RUN):
            _start(service, search_params)
        storage.get_or_create_app_session_start(CHAT_ID)

        with patch.object(service, '_owns_process', return_value=False):
            assert service.cancel_reservation(CHAT_ID) is True

        assert storage.redis.get(f"app_session_start:{CHAT_ID}") is None
