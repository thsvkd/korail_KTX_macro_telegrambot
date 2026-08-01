"""
Telling the user that a search is still going.

A search can run for hours without a word, and from the outside that silence
is indistinguishable from a process that died. /notify turns on a periodic
"still looking" message.

Off unless asked for, which is the property most of this file is about: an
unwanted message every five minutes is worse than the silence it replaces, so
nothing here may report by default, and a Redis that cannot be read has to
mean quiet rather than noise.
"""

import time
from unittest.mock import Mock

import pytest

from korail_bot.config.settings import settings
from korail_bot.handlers.command_handler import CommandHandler
from korail_bot.services import PaymentReminderService, ReservationService, TelegramService
from korail_bot.services.korail_service import KorailService, SearchProgress
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot import keyboards
from korail_bot.telegramBot.telebotBackProcess import BackgroundReservationProcess
from korail_bot.utils.formatting import format_duration

CHAT_ID = 12345


class TestFormatDuration:
    """How long the search has been going, said the way a person would."""

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0, "1분 미만"),
            (59, "1분 미만"),
            (60, "1분"),
            (12 * 60, "12분"),
            (3600, "1시간"),
            (3 * 3600 + 12 * 60, "3시간 12분"),
            (25 * 3600, "25시간"),
        ],
    )
    def test_it_reads_the_way_it_is_said(self, seconds, expected):
        assert format_duration(seconds) == expected

    def test_a_clock_read_out_of_order_is_not_reported_as_negative_time(self):
        """Two clocks and a subtraction is all it takes."""
        assert format_duration(-500) == "1분 미만"


class TestWhatTheSearchReports:
    """KorailService hands over facts and nothing else."""

    def test_the_facts_reach_the_caller(self):
        service = KorailService(on_progress=(seen := []).append)
        service.begin_search()
        service._failure_streak = 3

        service.report_progress(1234)

        assert seen[0].attempts == 1234
        assert seen[0].failure_streak == 3
        assert seen[0].elapsed_seconds >= 0

    def test_a_service_nobody_is_listening_to_does_not_bother(self):
        """
        Every short-lived client the bot builds - the one that validates a
        password, the one that lists trains - goes through this path.
        """
        service = KorailService()

        service.report_progress(1)  # must not raise

    def test_a_reporting_failure_never_ends_the_search(self):
        """
        Telegram being down is not a reason to stop looking for a seat. The
        search is the thing the user asked for; the commentary is not.
        """

        def explode(_progress):
            raise RuntimeError("telegram is down")

        service = KorailService(on_progress=explode)

        service.report_progress(1)  # must not raise

    def test_elapsed_time_comes_off_a_monotonic_clock(self):
        """
        A wall clock that steps backwards - an NTP correction, a timezone
        change - would otherwise turn "3시간째" into nonsense.
        """
        service = KorailService(on_progress=(seen := []).append)
        service.begin_search()
        started = service._search_started_at

        service.report_progress(1)

        assert started <= time.monotonic()
        assert 0 <= seen[0].elapsed_seconds < 60

    def test_no_failures_reads_as_healthy(self):
        assert SearchProgress(attempts=1, elapsed_seconds=0, failure_streak=0).healthy
        assert not SearchProgress(attempts=1, elapsed_seconds=0, failure_streak=1).healthy


class ReportingFixture:
    """
    A search process with only the parts the reporting decision touches.

    Built without __init__ on purpose: the real one reads argv and a line of
    credentials off stdin, and none of that has anything to do with whether a
    progress report is due.
    """

    def setup_method(self):
        self.process = object.__new__(BackgroundReservationProcess)
        self.process.chat_id = CHAT_ID
        self.process.storage = Mock(spec=StorageInterface)
        self.process.storage.get_progress_report_minutes.return_value = 0
        self.process.telegram = Mock(spec=TelegramService)
        self.process.src_locate = "서울"
        self.process.dst_locate = "부산"
        self.process.dep_date = "20991231"
        self.process.dep_time = "090000"
        self.process.max_dep_time = "1800"
        self.process.train_numbers = []
        self.process._reported_at = time.monotonic()
        self.process._report_minutes = 0
        self.process._report_minutes_read_at = None

    def set_preference(self, minutes):
        self.process.storage.get_progress_report_minutes.return_value = minutes
        # The preference is cached; a test changing it wants it read again.
        self.process._report_minutes_read_at = None

    def age(self, seconds):
        """Pretend the last report was that many seconds ago."""
        self.process._reported_at = time.monotonic() - seconds

    def tick(self, attempts=100, failure_streak=0):
        self.process._report_search_progress(
            SearchProgress(attempts=attempts, elapsed_seconds=600, failure_streak=failure_streak)
        )
        return self.process.telegram.send_message.call_args


class TestWhenAReportIsDue(ReportingFixture):
    """The throttle, which is nearly all this code does."""

    def test_nothing_is_sent_when_the_user_never_asked(self):
        self.age(3600)

        assert self.tick() is None

    def test_nothing_is_sent_before_the_interval_is_up(self):
        self.set_preference(5)
        self.age(4 * 60)

        assert self.tick() is None

    def test_a_report_goes_out_once_the_interval_has_passed(self):
        self.set_preference(5)
        self.age(5 * 60)

        assert self.tick() is not None

    def test_the_next_one_waits_a_full_interval_again(self):
        """Otherwise every remaining pass of the loop sends another."""
        self.set_preference(5)
        self.age(5 * 60)
        self.tick()
        self.process.telegram.send_message.reset_mock()

        assert self.tick() is None

    def test_the_first_report_is_not_immediate(self):
        """
        The user has just been told the search started. Repeating that back a
        second later says nothing.
        """
        self.set_preference(1)

        assert self.tick() is None

    def test_turning_it_off_mid_search_stops_the_reports(self):
        self.set_preference(5)
        self.age(5 * 60)
        self.tick()

        self.set_preference(0)
        self.age(5 * 60)
        self.process.telegram.send_message.reset_mock()

        assert self.tick() is None

    def test_the_preference_is_not_read_on_every_pass(self):
        """
        The loop runs about once a second for hours. Reading a Redis key each
        time would be the most expensive thing this feature does.
        """
        self.set_preference(5)
        for _ in range(50):
            self.tick()

        assert self.process.storage.get_progress_report_minutes.call_count == 1

    def test_redis_being_unreadable_means_quiet_not_noise(self):
        """
        The failure mode has to fall towards silence. Defaulting to "on" when
        the preference cannot be read would message users who never asked.
        """
        self.process.storage.get_progress_report_minutes.side_effect = Exception("redis is down")
        self.process._report_minutes_read_at = None
        self.age(3600)

        assert self.tick() is None


class TestWhatTheReportSays(ReportingFixture):
    """The message itself."""

    def report(self, **kwargs):
        self.set_preference(5)
        self.age(5 * 60)
        return self.tick(**kwargs).args[1]

    def test_it_carries_the_search_the_user_is_waiting_on(self):
        text = self.report()

        assert "서울" in text and "부산" in text
        assert "20991231" in text
        assert "0900" in text

    def test_it_says_how_long_and_how_many(self):
        text = self.report(attempts=4312)

        assert "10분째" in text
        assert "4,312" in text

    def test_a_healthy_search_says_so(self):
        assert "정상" in self.report()

    def test_a_search_that_cannot_reach_korail_says_that_instead(self):
        """
        The one case where the report carries news rather than reassurance,
        and the reason it is worth reading at all.
        """
        text = self.report(failure_streak=40)

        assert "응답 없음" in text
        assert "40" in text

    def test_a_narrowed_watch_names_the_trains(self):
        self.process.train_numbers = ["101", "105"]

        text = self.report()

        assert "101" in text and "105" in text

    def test_watching_the_whole_window_says_so(self):
        assert "시간대 전체" in self.report()

    def test_it_says_how_to_make_it_stop(self):
        """A recurring message that does not carry its own off switch is spam."""
        assert "/notify off" in self.report()


class NotifyFixture:
    """The command that sets all this up."""

    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_progress_report_minutes.return_value = 0
        self.telegram = Mock(spec=TelegramService)
        self.handler = CommandHandler(
            self.storage,
            self.telegram,
            Mock(spec=ReservationService),
            Mock(spec=PaymentReminderService),
        )

    def notify(self, args=""):
        self.handler.handle_notify(CHAT_ID, args)
        return self.telegram.send_message.call_args.args[1]

    def stored(self):
        self.storage.set_progress_report_minutes.assert_called_once()
        return self.storage.set_progress_report_minutes.call_args.args[1]


class TestTheNotifyCommand(NotifyFixture):
    """Reading what the user asked for."""

    def test_a_bare_command_shows_the_settings(self):
        text = self.notify()

        self.storage.set_progress_report_minutes.assert_not_called()
        assert "진행 상황 알림" in text
        assert self.telegram.send_message.call_args.kwargs["reply_markup"]

    def test_the_settings_screen_says_what_is_set_now(self):
        """A settings screen that hides the setting is a menu, not a screen."""
        self.storage.get_progress_report_minutes.return_value = 15

        assert "15분마다" in self.notify()

    @pytest.mark.parametrize("args", ["10", "10m", "10min", "10분", " 10 "])
    def test_a_number_of_minutes_is_taken_however_it_is_written(self, args):
        """Someone reading "10분마다" off a button will type the 분."""
        self.notify(args)

        assert self.stored() == 10

    @pytest.mark.parametrize("args", ["off", "OFF", "0", "끄기", "해제"])
    def test_turning_it_off(self, args):
        self.notify(args)

        assert self.stored() == 0

    def test_turning_it_on_uses_the_default_interval(self):
        self.notify("on")

        assert self.stored() == settings.PROGRESS_REPORT_DEFAULT_MINUTES

    def test_an_answer_that_makes_no_sense_is_refused_not_guessed_at(self):
        text = self.notify("가끔")

        self.storage.set_progress_report_minutes.assert_not_called()
        assert "가끔" in text

    def test_too_often_is_refused_rather_than_clamped(self, monkeypatch):
        """
        Clamping would leave the user believing one thing was set while
        another was. The bound is worth stating, not hiding.

        The floor is raised for this one: at the shipped minimum of a minute
        there is no positive interval below it to ask for.
        """
        monkeypatch.setattr(settings, "PROGRESS_REPORT_MIN_MINUTES", 5)

        text = self.notify("2")

        self.storage.set_progress_report_minutes.assert_not_called()
        assert "5" in text

    def test_too_rarely_is_refused_too(self):
        self.notify(str(settings.PROGRESS_REPORT_MAX_MINUTES + 1))

        self.storage.set_progress_report_minutes.assert_not_called()

    def test_the_minimum_is_allowed(self):
        """The bound is inclusive; the user picked it deliberately."""
        self.notify(str(settings.PROGRESS_REPORT_MIN_MINUTES))

        assert self.stored() == settings.PROGRESS_REPORT_MIN_MINUTES

    def test_turning_off_something_already_off_says_so(self):
        self.storage.get_progress_report_minutes.return_value = 0

        assert "이미" in self.notify("off")

    def test_turning_off_something_that_was_on_confirms_it(self):
        self.storage.get_progress_report_minutes.return_value = 5

        assert "껐습니다" in self.notify("off")


class TestTheNotifyKeyboard(NotifyFixture):
    """Pressing an interval instead of typing one."""

    def buttons(self, current=0):
        return [
            button
            for row in keyboards.notify_keyboard(current)["inline_keyboard"]
            for button in row
        ]

    def test_every_offered_interval_is_one_the_command_would_accept(self):
        """A button that gets refused is worse than no button."""
        for value in self.intervals():
            assert (
                settings.PROGRESS_REPORT_MIN_MINUTES
                <= int(value)
                <= settings.PROGRESS_REPORT_MAX_MINUTES
            )

    def intervals(self, current=0):
        """
        The values that are actually intervals.

        "Turn it off" and "let me type one" are instructions to the handler
        rather than answers, so validating them against the range would be
        checking the wrong thing.
        """
        sentinels = {keyboards.NOTIFY_OFF, keyboards.MANUAL}
        return [
            value
            for button in self.buttons(current)
            if (value := button["callback_data"].partition(":")[2]) not in sentinels
        ]

    def test_typing_an_interval_is_offered_too(self):
        """
        The keyboard carries round numbers. Someone who wants seven minutes
        should not have to know that /notify 7 was a thing they could type.
        """
        data = {b["callback_data"] for b in self.buttons()}

        assert f"{keyboards.STEP_NOTIFY}:{keyboards.MANUAL}" in data

    def test_choosing_to_type_opens_a_reply_box(self):
        """
        A screen that ends with "type the value" is only usable if the client
        puts a cursor where the value goes.
        """
        self.handler.handle_notify_callback(CHAT_ID, keyboards.MANUAL)

        self.storage.set_waiting_for_notify_input.assert_called_once_with(CHAT_ID)
        self.storage.set_progress_report_minutes.assert_not_called()
        assert self.telegram.send_message.call_args.kwargs["reply_markup"]["force_reply"]

    def test_the_typed_interval_is_taken(self):
        self.handler.handle_notify_input(CHAT_ID, "7")

        assert self.stored() == 7

    def test_it_stops_listening_afterwards(self):
        """Otherwise the next message becomes an interval too."""
        self.handler.handle_notify_input(CHAT_ID, "7")

        self.storage.set_waiting_for_notify_input.assert_called_with(CHAT_ID, False)

    def test_it_stops_listening_even_when_the_value_makes_no_sense(self):
        """
        The reply box is gone by the time the refusal is sent, so leaving the
        flag set would silently claim whatever is typed next.
        """
        self.handler.handle_notify_input(CHAT_ID, "가끔")

        self.storage.set_progress_report_minutes.assert_not_called()
        self.storage.set_waiting_for_notify_input.assert_called_with(CHAT_ID, False)

    def test_a_placeholder_stays_inside_telegrams_limit(self):
        """Over 64 characters and the whole sendMessage is refused."""
        markup = keyboards.force_reply("가" * 200)

        assert len(markup["input_field_placeholder"]) <= 64

    def test_the_callback_data_fits_telegrams_limit(self):
        for button in self.buttons():
            encoded = button["callback_data"].encode("utf-8")
            assert len(encoded) <= keyboards.CALLBACK_DATA_MAX_BYTES

    def test_the_interval_in_force_is_ticked(self):
        labels = {b["callback_data"]: b["text"] for b in self.buttons(current=15)}

        assert labels[f"{keyboards.STEP_NOTIFY}:15"].startswith("✅")
        assert not labels[f"{keyboards.STEP_NOTIFY}:5"].startswith("✅")

    def test_off_is_ticked_when_nothing_is_set(self):
        labels = {b["callback_data"]: b["text"] for b in self.buttons(current=0)}

        assert labels[f"{keyboards.STEP_NOTIFY}:{keyboards.NOTIFY_OFF}"].startswith("✅")

    def test_a_press_sets_the_interval(self):
        self.handler.handle_notify_callback(CHAT_ID, "15")

        assert self.stored() == 15

    def test_the_off_button_turns_it_off(self):
        self.handler.handle_notify_callback(CHAT_ID, keyboards.NOTIFY_OFF)

        assert self.stored() == 0

    def test_a_value_from_some_other_build_is_ignored_quietly(self):
        self.handler.handle_notify_callback(CHAT_ID, "*something")

        self.storage.set_progress_report_minutes.assert_not_called()
