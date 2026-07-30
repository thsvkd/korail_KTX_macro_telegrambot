"""
Integration tests for searches that stop without saying so.

A search ends by calling back, and the callback is what clears its record.
Death skips that: the process is gone, the record says a search is running,
and the user hears nothing at all. They are waiting on the bot for exactly
the hours it is supposed to be watching, so being told is the whole point.

Run against a real Redis, so the record surviving as something the user can
act on - resume or drop - is exercised for real rather than asserted about a
Mock.
"""

import subprocess
from unittest.mock import Mock, patch

import pytest

from korail_bot.config.settings import settings
from korail_bot.models import DeathCause, TrainSearchParams
from korail_bot.services import ReservationService, SearchWatchdogService, TelegramService
from korail_bot.storage import RedisStorage
from korail_bot.telegramBot import keyboards
from tests.fixtures.processes import make_alive

CHAT_ID = 5150
USERNAME = "010-1234-5678"
PASSWORD = "korail-password"
PID = 31337


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
        seat_strategy="consecutive",
    )


@pytest.fixture
def storage():
    storage = RedisStorage()
    storage.redis.flushdb()
    yield storage
    storage.redis.flushdb()


@pytest.fixture
def telegram():
    return Mock(spec=TelegramService)


@pytest.fixture
def service(storage, telegram):
    return ReservationService(storage, telegram)


def _dead_on_arrival(pid=PID):
    """A Popen stand-in for a process that exited the moment it was spawned."""
    process = Mock()
    process.pid = pid
    process.returncode = 1
    process.poll.return_value = 1
    # wait() returning rather than timing out is what an exit looks like.
    process.wait.return_value = 1
    return process


def _start(service, search_params, process=None, pid=PID):
    """Start a search against a stand-in for the child process."""
    with patch("subprocess.Popen") as popen:
        popen.return_value = process or make_alive(popen, pid)
        return service.start_reservation_process(
            chat_id=CHAT_ID, username=USERNAME, password=PASSWORD, search_params=search_params
        )


def _last_message(telegram):
    """The text and keyboard of the most recent message sent."""
    call = telegram.send_message.call_args
    text = call.args[1] if len(call.args) > 1 else call.kwargs["text"]
    return text, call.kwargs.get("reply_markup")


class TestASearchThatNeverStarts:
    """
    The failure this whole path exists for.

    Spawning with the wrong interpreter produced a process that died on
    ModuleNotFoundError within milliseconds, while the bot recorded a running
    search and told the user it had begun. The search never ran, and nothing
    said so for eight hours.
    """

    def test_starting_reports_failure(self, service, search_params):
        assert _start(service, search_params, process=_dead_on_arrival()) is False

    def test_no_running_search_is_recorded(self, service, storage, search_params):
        _start(service, search_params, process=_dead_on_arrival())

        assert storage.get_running_reservation(CHAT_ID) is None

    def test_the_user_is_not_told_the_search_began(self, service, telegram, search_params):
        _start(service, search_params, process=_dead_on_arrival())

        sent = " ".join(str(call) for call in telegram.send_message.call_args_list)
        assert "취소표가 나오면" not in sent

    def test_the_details_are_kept_for_the_user_to_act_on(self, service, storage, search_params):
        _start(service, search_params, process=_dead_on_arrival())

        dead = storage.get_dead_search(CHAT_ID)
        assert dead is not None
        assert dead.cause == DeathCause.START_FAILED
        assert dead.search_params.src_locate == "서울"
        assert dead.search_params.dst_locate == "부산"
        assert dead.search_params.passenger_count == 2

    def test_the_user_is_told_and_offered_a_way_out(self, service, telegram, search_params):
        _start(service, search_params, process=_dead_on_arrival())

        text, markup = _last_message(telegram)
        assert "멈췄" in text
        assert markup is not None
        answers = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
        assert f"{keyboards.STEP_DEAD}:{keyboards.DEAD_DISCARD}" in answers

    def test_the_interpreter_is_the_one_running_the_app(self, service, search_params):
        """
        The fix itself. Resolving "python" through PATH finds the system
        interpreter in a service started outside an activated virtualenv, and
        that one has no korail_bot to import.
        """
        import sys

        with patch("subprocess.Popen") as popen:
            make_alive(popen, PID)
            service.start_reservation_process(
                chat_id=CHAT_ID,
                username=USERNAME,
                password=PASSWORD,
                search_params=search_params,
            )

        assert popen.call_args.args[0][0] == sys.executable


class TestTheWatchdogNoticesADeadSearch:
    """A search that ran for a while and then vanished."""

    @pytest.fixture
    def running(self, service, storage, search_params):
        assert _start(service, search_params) is True
        assert storage.get_running_reservation(CHAT_ID) is not None
        return service

    def _vanish(self, service):
        """The process is gone, as /proc would report it."""
        service._children.clear()
        return patch.object(ReservationService, "_owns_process", return_value=False)

    def test_the_dead_search_is_found(self, running, storage):
        with self._vanish(running):
            assert running.detect_dead_searches() == 1

    def test_the_record_stops_claiming_a_search_is_running(self, running, storage):
        with self._vanish(running):
            running.detect_dead_searches()

        assert storage.get_running_reservation(CHAT_ID) is None

    def test_the_search_is_kept_as_one_that_died(self, running, storage, search_params):
        with self._vanish(running):
            running.detect_dead_searches()

        dead = storage.get_dead_search(CHAT_ID)
        assert dead is not None
        assert dead.cause == DeathCause.CRASHED
        assert dead.search_params.dep_date == search_params.dep_date

    def test_the_user_is_told_once_and_not_again(self, running, telegram):
        with self._vanish(running):
            running.detect_dead_searches()
            announcements = telegram.send_message.call_count

            # The record has moved out of the running searches, so a second
            # pass has nothing left to find. Without that the user would be
            # told every thirty seconds for as long as the bot is up.
            assert running.detect_dead_searches() == 0
            assert telegram.send_message.call_count == announcements

    def test_a_living_search_is_left_alone(self, running, storage, telegram):
        telegram.send_message.reset_mock()

        with patch.object(ReservationService, "_owns_process", return_value=True):
            assert running.detect_dead_searches() == 0

        assert storage.get_running_reservation(CHAT_ID) is not None
        assert storage.get_dead_search(CHAT_ID) is None
        telegram.send_message.assert_not_called()

    def test_the_watchdog_reports_what_it_found(self, running):
        watchdog = SearchWatchdogService(running)

        with self._vanish(running):
            assert watchdog.tick() == 1


class TestShutdownIsNotACrash:
    """
    Stopping the app kills the searches on purpose and leaves their records
    for the next run to resume. That is the same shape as a crash from the
    outside, and reporting it as one would move the records where the next
    run cannot find them - turning every restart into a lost search.
    """

    def test_a_shutdown_is_not_reported_as_a_death(self, service, storage, search_params):
        _start(service, search_params)

        with patch.object(ReservationService, "_owns_process", return_value=False):
            service.shutdown()
            assert service.detect_dead_searches() == 0

        assert storage.get_running_reservation(CHAT_ID) is not None
        assert storage.get_dead_search(CHAT_ID) is None


class TestResumingADeadSearch:
    """The button that starts the same search over."""

    @pytest.fixture
    def dead(self, service, storage, search_params):
        _start(service, search_params, process=_dead_on_arrival())
        assert storage.get_dead_search(CHAT_ID) is not None
        return service

    def test_the_search_starts_again_on_the_same_terms(self, dead, storage, search_params):
        with patch("subprocess.Popen") as popen:
            make_alive(popen, 424242)
            assert dead.resume_dead_search(CHAT_ID) is True

        argv = popen.call_args.args[0]
        assert search_params.dep_date in argv
        assert search_params.src_locate in argv
        assert search_params.dst_locate in argv

        running = storage.get_running_reservation(CHAT_ID)
        assert running is not None
        assert running.process_id == 424242

    def test_the_dead_record_is_gone_once_resumed(self, dead, storage):
        with patch("subprocess.Popen") as popen:
            make_alive(popen, 424242)
            dead.resume_dead_search(CHAT_ID)

        assert storage.get_dead_search(CHAT_ID) is None

    def test_the_user_is_not_told_a_restart_did_it(self, dead, telegram):
        """
        Resuming after a restart and resuming because the user pressed a
        button are both resumptions, and explaining one as the other is a
        small lie they have no way to check.
        """
        with patch("subprocess.Popen") as popen:
            make_alive(popen, 424242)
            dead.resume_dead_search(CHAT_ID)

        text, _ = _last_message(telegram)
        assert "다시 시작했습니다" in text
        assert "서버가 재시작" not in text

    def test_resuming_a_search_that_is_already_gone_says_so(self, service, storage, telegram):
        assert service.resume_dead_search(CHAT_ID) is False

        text, _ = _last_message(telegram)
        assert "이미 정리된" in text

    def test_a_search_with_no_stored_login_is_not_offered_a_resume(
        self, service, storage, telegram, search_params
    ):
        with patch.object(settings, "RESUME_ON_RESTART", False):
            _start(service, search_params, process=_dead_on_arrival())

        dead = storage.get_dead_search(CHAT_ID)
        assert dead is not None
        assert dead.resumable is False

        _, markup = _last_message(telegram)
        answers = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
        assert f"{keyboards.STEP_DEAD}:{keyboards.DEAD_RESUME}" not in answers


class TestDiscardingADeadSearch:
    """The button that lets it go."""

    @pytest.fixture
    def dead(self, service, storage, search_params):
        _start(service, search_params, process=_dead_on_arrival())
        return service

    def test_the_record_is_dropped(self, dead, storage):
        assert dead.discard_dead_search(CHAT_ID) is True
        assert storage.get_dead_search(CHAT_ID) is None

    def test_the_stored_login_goes_with_it(self, dead, storage):
        dead.discard_dead_search(CHAT_ID)

        assert storage.get_resume_credentials(CHAT_ID) is None

    def test_discarding_nothing_reports_nothing(self, service):
        assert service.discard_dead_search(CHAT_ID) is False


class TestStatusTellsTheTruth:
    """
    /status reading "search running" for a search that stopped is the lie
    that kept the user waiting in the first place.
    """

    def test_a_dead_search_is_reported_as_stopped(self, service, storage, search_params):
        _start(service, search_params, process=_dead_on_arrival())

        status = service.get_status(CHAT_ID)

        assert "멈춰" in status
        assert "진행중" not in status
        assert "서울" in status
        assert "부산" in status

    def test_a_running_search_is_still_reported_as_running(self, service, search_params):
        _start(service, search_params)

        status = service.get_status(CHAT_ID)

        assert "진행중" in status


class TestStartingOverClearsTheDeadSearch:
    """A new search for the same chat settles the old one by existing."""

    def test_a_new_search_drops_the_dead_one(self, service, storage, search_params):
        _start(service, search_params, process=_dead_on_arrival())
        assert storage.get_dead_search(CHAT_ID) is not None

        _start(service, search_params, pid=777)

        assert storage.get_dead_search(CHAT_ID) is None
        assert storage.get_running_reservation(CHAT_ID) is not None


class TestTheGracePeriodIsHonoured:
    """
    The check waits before believing a process died, because a search that
    started properly is still running when the wait runs out.
    """

    def test_a_process_that_outlives_the_grace_period_is_accepted(
        self, service, storage, search_params
    ):
        process = Mock()
        process.pid = PID
        process.returncode = None
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired(cmd="python", timeout=1.0)

        with patch("subprocess.Popen") as popen:
            popen.return_value = process
            started = service.start_reservation_process(
                chat_id=CHAT_ID,
                username=USERNAME,
                password=PASSWORD,
                search_params=search_params,
            )

        assert started is True
        assert storage.get_running_reservation(CHAT_ID) is not None
        assert process.wait.call_args.kwargs["timeout"] == settings.PROCESS_START_GRACE_SECONDS
