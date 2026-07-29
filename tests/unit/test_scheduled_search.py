"""
Starting a search at a chosen time instead of straight away.

Tickets are not released evenly - holiday booking opens at an announced
minute, cancellations bunch up near departure - so a search that begins at the
right moment beats one that has been grinding since yesterday, and spends far
fewer requests getting there.

The waiting is done by a record in Redis rather than a sleeping process. A
process asleep until tomorrow morning dies with the next restart; a record is
picked up by whatever is running when the time comes.

Two bounds do the real work. A search cannot be booked past the expiry on the
stored login, because the moment would arrive with no way to log in. And a
schedule that came due while the app was down is dropped rather than run late,
because a search appearing hours after it was asked for is not what anyone
agreed to.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from korail_bot.config.settings import settings
from korail_bot.handlers import ConversationHandler
from korail_bot.models import ScheduledSearch, TrainSearchParams
from korail_bot.services import ReservationService, ScheduleError, TelegramService
from korail_bot.services.scheduled_search_service import ScheduledSearchService
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot.messages import Messages

CHAT_ID = 12345
NOW = datetime(2026, 7, 30, 12, 0, 0)


def params(dep_date="20991231", dep_time="090000", train_numbers=None):
    return TrainSearchParams(
        dep_date=dep_date,
        src_locate="서울",
        dst_locate="부산",
        dep_time=dep_time,
        max_dep_time="1800",
        train_numbers=train_numbers or [],
    )


@pytest.fixture
def service():
    storage = Mock(spec=StorageInterface)
    storage.get_scheduled_search.return_value = None
    return ScheduledSearchService(
        storage, Mock(spec=TelegramService), Mock(spec=ReservationService)
    )


class TestValidateStartTime:
    """Times the search cannot actually be run at."""

    def test_a_time_in_the_future_is_fine(self, service):
        service.validate_start_time(datetime.now() + timedelta(hours=2), params())

    def test_a_time_already_gone_is_refused(self, service):
        with pytest.raises(ScheduleError) as caught:
            service.validate_start_time(datetime.now() - timedelta(minutes=1), params())

        assert str(caught.value) == Messages.SCHEDULE_IN_THE_PAST

    def test_beyond_the_stored_logins_expiry_is_refused(self, service):
        """
        The bound that is not arbitrary. The password waits under the resume
        key's TTL, so a schedule booked past it arrives with nothing to log
        in with - a search that silently never happens.
        """
        too_far = datetime.now() + timedelta(seconds=settings.SCHEDULE_MAX_AHEAD_SECONDS + 3600)

        with pytest.raises(ScheduleError) as caught:
            service.validate_start_time(too_far, params())

        assert "일" in str(caught.value)

    def test_starting_after_the_train_has_left_is_refused(self, service):
        """
        Such a search can only ever find nothing, at full request rate, until
        somebody notices and cancels it.
        """
        tomorrow = datetime.now() + timedelta(days=1)
        departure = params(dep_date=tomorrow.strftime("%Y%m%d"), dep_time="090000")

        with pytest.raises(ScheduleError) as caught:
            service.validate_start_time(tomorrow.replace(hour=10, minute=0), departure)

        assert "출발" in str(caught.value)

    def test_starting_before_the_train_leaves_is_fine(self, service):
        tomorrow = datetime.now() + timedelta(days=1)
        departure = params(dep_date=tomorrow.strftime("%Y%m%d"), dep_time="230000")

        service.validate_start_time(tomorrow.replace(hour=8, minute=0), departure)

    def test_an_unreadable_departure_does_not_block_scheduling(self, service):
        """
        The date is validated long before this step. If it somehow is not
        readable here, that is not a reason to refuse the booking.
        """
        service.validate_start_time(
            datetime.now() + timedelta(hours=1), params(dep_date="nonsense")
        )


class TestScheduling:
    """Booking one."""

    def test_the_search_and_the_login_are_both_stored(self, service):
        start_at = datetime.now() + timedelta(hours=3)

        service.schedule(CHAT_ID, "010-1234-5678", "pw", params(), start_at)

        stored = service.storage.save_scheduled_search.call_args.args[0]
        assert stored.chat_id == CHAT_ID
        assert stored.start_at == start_at
        service.storage.save_resume_credentials.assert_called_once_with(
            CHAT_ID, "010-1234-5678", "pw"
        )

    def test_a_refused_time_stores_nothing(self, service):
        """
        Not even the password. Validation runs first for exactly this reason:
        a rejected booking must not leave a credential behind.
        """
        with pytest.raises(ScheduleError):
            service.schedule(CHAT_ID, "010-1234-5678", "pw", params(), datetime.now())

        service.storage.save_scheduled_search.assert_not_called()
        service.storage.save_resume_credentials.assert_not_called()

    def test_cancelling_removes_the_login_too(self, service):
        service.storage.get_scheduled_search.return_value = ScheduledSearch(
            chat_id=CHAT_ID, korail_id="x", search_params=params(), start_at=datetime.now()
        )

        assert service.cancel(CHAT_ID) is True
        service.storage.delete_scheduled_search.assert_called_once_with(CHAT_ID)
        service.storage.delete_resume_credentials.assert_called_once_with(CHAT_ID)

    def test_cancelling_nothing_says_so(self, service):
        assert service.cancel(CHAT_ID) is False
        service.storage.delete_scheduled_search.assert_not_called()


class TestFiring:
    """The moment arriving."""

    def scheduled(self, minutes_ago=0.0):
        return ScheduledSearch(
            chat_id=CHAT_ID,
            korail_id="010-1234-5678",
            search_params=params(),
            start_at=datetime.now() - timedelta(minutes=minutes_ago),
        )

    def test_a_due_search_is_started(self, service):
        service.storage.get_all_scheduled_searches.return_value = [self.scheduled()]
        service.storage.get_resume_credentials.return_value = ("010-1234-5678", "pw")

        service.tick()

        service.reservation.start_reservation_process.assert_called_once()
        assert service.reservation.start_reservation_process.call_args.kwargs["chat_id"] == CHAT_ID

    def test_the_record_goes_before_the_search_starts(self, service):
        """
        Whatever happens next, this schedule has been dealt with. Left in
        place through a failure it would be retried on every pass of the loop.
        """
        service.storage.get_all_scheduled_searches.return_value = [self.scheduled()]
        service.storage.get_resume_credentials.return_value = ("010-1234-5678", "pw")
        service.reservation.start_reservation_process.side_effect = Exception("boom")

        with pytest.raises(Exception, match="boom"):
            service.tick()

        service.storage.delete_scheduled_search.assert_called_once_with(CHAT_ID)

    def test_the_user_is_told_the_search_has_begun(self, service):
        service.storage.get_all_scheduled_searches.return_value = [self.scheduled()]
        service.storage.get_resume_credentials.return_value = ("010-1234-5678", "pw")

        service.tick()

        assert "서울" in service.telegram.send_message.call_args.args[1]

    def test_a_search_not_yet_due_is_left_alone(self, service):
        future = ScheduledSearch(
            chat_id=CHAT_ID,
            korail_id="x",
            search_params=params(),
            start_at=datetime.now() + timedelta(minutes=5),
        )
        service.storage.get_all_scheduled_searches.return_value = [future]

        service.tick()

        service.reservation.start_reservation_process.assert_not_called()
        service.storage.delete_scheduled_search.assert_not_called()

    def test_one_missed_by_more_than_the_grace_period_is_dropped(self, service):
        """
        The app was down when it came due. Starting it now would be a search
        the user asked for hours ago turning up unannounced - and possibly
        for a train that has already gone.
        """
        late = self.scheduled(minutes_ago=settings.SCHEDULE_GRACE_SECONDS / 60 + 30)
        service.storage.get_all_scheduled_searches.return_value = [late]

        service.tick()

        service.reservation.start_reservation_process.assert_not_called()
        assert (
            Messages.SCHEDULE_MISSED.split("{")[0]
            in service.telegram.send_message.call_args.args[1]
        )
        service.storage.delete_resume_credentials.assert_called_once_with(CHAT_ID)

    def test_one_missed_by_less_than_the_grace_period_still_runs(self, service):
        """A restart across the moment should not cost the user their search."""
        slightly_late = self.scheduled(minutes_ago=settings.SCHEDULE_GRACE_SECONDS / 60 - 1)
        service.storage.get_all_scheduled_searches.return_value = [slightly_late]
        service.storage.get_resume_credentials.return_value = ("010-1234-5678", "pw")

        service.tick()

        service.reservation.start_reservation_process.assert_called_once()

    def test_an_expired_login_is_reported_rather_than_silently_dropped(self, service):
        service.storage.get_all_scheduled_searches.return_value = [self.scheduled()]
        service.storage.get_resume_credentials.return_value = None

        service.tick()

        service.reservation.start_reservation_process.assert_not_called()
        assert service.telegram.send_message.call_args.args[1] == Messages.SCHEDULE_NO_CREDENTIALS


class TestPacing:
    """How long the loop waits between looks."""

    def test_nothing_scheduled_means_the_ordinary_poll(self, service):
        service.storage.get_all_scheduled_searches.return_value = []

        assert service.tick() == settings.SCHEDULE_POLL_SECONDS

    def test_it_sleeps_until_the_next_one_is_due(self, service):
        """
        Not a fixed poll. Someone waiting for booking to open at 07:00 means
        07:00, and a thirty-second poll would start them up to thirty seconds
        late - by which time the tickets are gone.
        """
        soon = ScheduledSearch(
            chat_id=CHAT_ID,
            korail_id="x",
            search_params=params(),
            start_at=datetime.now() + timedelta(seconds=4),
        )
        service.storage.get_all_scheduled_searches.return_value = [soon]

        assert 0 < service.tick() <= 5

    def test_it_never_sleeps_longer_than_the_poll_interval(self, service):
        """So a newly booked search is noticed without waiting for a distant one."""
        distant = ScheduledSearch(
            chat_id=CHAT_ID,
            korail_id="x",
            search_params=params(),
            start_at=datetime.now() + timedelta(days=1),
        )
        service.storage.get_all_scheduled_searches.return_value = [distant]

        assert service.tick() == settings.SCHEDULE_POLL_SECONDS

    def test_a_failing_pass_does_not_end_the_loop(self, service):
        """
        run() is the whole body of a thread. An escaping exception would
        leave every booked search waiting forever with nothing to say so.
        """
        service.storage.get_all_scheduled_searches.side_effect = Exception("redis down")
        service.stop()

        service.run()  # returns rather than raising


class TestParsingAStartTime:
    """What the user typed, or what a button carried."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("202608010700", datetime(2026, 8, 1, 7, 0)),
            ("0801 0700", datetime(2026, 8, 1, 7, 0)),
            ("08010700", datetime(2026, 8, 1, 7, 0)),
            ("08/01 07:00", datetime(2026, 8, 1, 7, 0)),
        ],
    )
    def test_the_written_out_forms(self, text, expected):
        assert ConversationHandler.parse_start_time(text, now=NOW) == expected

    def test_a_bare_time_later_today_means_today(self):
        assert ConversationHandler.parse_start_time("2200", now=NOW) == datetime(2026, 7, 30, 22, 0)

    def test_a_bare_time_already_past_means_tomorrow(self):
        """
        Typing "0700" at noon means tomorrow morning. Reading it as this
        morning would book a search for a moment that has gone, which
        validation then refuses - correct but useless.
        """
        assert ConversationHandler.parse_start_time("0700", now=NOW) == datetime(2026, 7, 31, 7, 0)

    def test_a_date_already_past_means_next_year(self):
        """Nobody books a search for a train that left in January."""
        assert ConversationHandler.parse_start_time("0105 0700", now=NOW) == datetime(
            2027, 1, 5, 7, 0
        )

    @pytest.mark.parametrize(
        "text", ["", "곧", "2569", "0230 0700", "20260231 0700", "070", "12345"]
    )
    def test_what_cannot_be_read_is_refused(self, text):
        """
        Includes times that look right but are not - 25:69, the 31st of
        February. A guess here would book a search for the wrong moment and
        say nothing.
        """
        assert ConversationHandler.parse_start_time(text, now=NOW) is None

    def test_a_button_value_round_trips(self):
        """Buttons carry the resolved moment, so it must parse back unchanged."""
        from korail_bot.telegramBot import keyboards

        keyboard = keyboards.schedule_keyboard(now=NOW)
        for row in keyboard["inline_keyboard"]:
            for button in row:
                step, _, value = button["callback_data"].partition(":")
                if step != keyboards.STEP_SCHEDULE or value.startswith("*") or not value.isdigit():
                    continue
                assert ConversationHandler.parse_start_time(value, now=NOW) is not None


class TestScheduleKeyboard:
    """The times offered."""

    def make(self, now=NOW):
        from korail_bot.telegramBot import keyboards

        return keyboards.schedule_keyboard(now=now)

    def moments(self, now=NOW):
        from korail_bot.telegramBot import keyboards

        return [
            datetime.strptime(b["callback_data"].partition(":")[2], "%Y%m%d%H%M")
            for row in self.make(now)["inline_keyboard"]
            for b in row
            if b["callback_data"].startswith(f"{keyboards.STEP_SCHEDULE}:")
            and b["callback_data"].partition(":")[2].isdigit()
        ]

    def test_every_offered_time_is_in_the_future(self):
        """An hour that has already gone by today is not an offer."""
        assert all(moment > NOW for moment in self.moments())

    def test_late_in_the_day_the_evening_option_drops_off(self):
        """At 23:00 there is no such thing as "오늘 22:00"."""
        labels = [
            b["text"]
            for row in self.make(now=datetime(2026, 7, 30, 23, 0))["inline_keyboard"]
            for b in row
        ]
        assert not any(label.startswith("오늘") for label in labels)

    def test_the_offers_survive_a_month_boundary(self):
        """Built by adding days, so the end of a month must roll over."""
        assert all(
            moment > datetime(2026, 7, 31, 23, 30)
            for moment in self.moments(now=datetime(2026, 7, 31, 23, 30))
        )

    def test_going_back_is_offered(self):
        from korail_bot.telegramBot import keyboards

        data = [b["callback_data"] for row in self.make()["inline_keyboard"] for b in row]
        assert f"{keyboards.STEP_SCHEDULE}:{keyboards.SCHEDULE_BACK}" in data
