"""
Finding out whether the payment actually happened.

"Payment complete" used to mean the user had sent the bot any message at all.
That is a claim, not a fact, and it was wrong in both directions: someone who
answered the reminder without paying was told the matter was settled and
quietly lost the seat, and someone who paid without answering was nagged until
the window closed.

Korail lists a reservation only while it is waiting to be paid for, so the
listing is enough to tell. Nothing here pays for anything - korail2 has no
payment call at all, and the check is one read-only listing.

The distinction that carries the weight is between "gone" and "could not
ask". Reading a failed request as a completed payment would put the guess
straight back, with more confidence behind it.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from korail2 import NoResultsError

from korail_bot.models import PaymentStatus
from korail_bot.services.korail_service import KorailService
from korail_bot.telegramBot.messages import Messages


class FakeClock:
    """
    A clock the test moves by sleeping, the way the real watch experiences it.

    Only now() is needed: the watch reads the current time and waits, and the
    waiting is what gets it to the deadline.
    """

    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def reservation(rsv_id="320260731221946", limit_date="20260730", limit_time="015648"):
    """A stand-in for a korail2 reservation, with the fields the watch reads."""
    rsv = Mock()
    rsv.rsv_id = rsv_id
    rsv.buy_limit_date = limit_date
    rsv.buy_limit_time = limit_time
    return rsv


class TestIsReservationOutstanding:
    """The read-only question the whole thing rests on."""

    def make_service(self, reservations=None, error=None):
        service = KorailService()
        service._logged_in = True
        service._korail_instance = Mock()
        if error is not None:
            service._korail_instance.reservations.side_effect = error
        else:
            service._korail_instance.reservations.return_value = reservations or []
        return service

    def test_a_listed_reservation_is_still_unpaid(self):
        service = self.make_service([reservation("111"), reservation("222")])

        assert service.is_reservation_outstanding("222") is True

    def test_a_reservation_that_dropped_off_the_list_is_settled(self):
        service = self.make_service([reservation("111")])

        assert service.is_reservation_outstanding("222") is False

    def test_an_empty_list_settles_it(self):
        assert self.make_service([]).is_reservation_outstanding("222") is False

    def test_no_reservations_at_all_is_an_answer_not_a_failure(self):
        """NoResultsError is how korail2 says the list is empty."""
        service = self.make_service(error=NoResultsError())

        assert service.is_reservation_outstanding("222") is False

    def test_the_id_is_compared_as_text(self):
        """
        Korail's numbers arrive as strings and are long enough to lose
        precision if anything ever treats one as an integer.
        """
        service = self.make_service([reservation(320260731221946)])

        assert service.is_reservation_outstanding("320260731221946") is True

    @pytest.mark.parametrize(
        "error", [ConnectionError("no route"), ValueError("bad json"), Exception("500")]
    )
    def test_korail_not_answering_is_not_a_payment(self, error):
        """
        The distinction the whole check exists for. None, never False - a
        failed request read as "gone" would announce a payment that may not
        have happened and stop the reminders for one that did not.
        """
        service = self.make_service(error=error)

        assert service.is_reservation_outstanding("222") is None

    def test_not_being_logged_in_is_not_a_payment_either(self):
        service = KorailService()

        assert service.is_reservation_outstanding("222") is None


class TestWatchPayment:
    """Sitting with the reservation until it resolves."""

    def setup_method(self):
        from korail_bot.telegramBot.telebotBackProcess import BackgroundReservationProcess

        self.process = BackgroundReservationProcess.__new__(BackgroundReservationProcess)
        self.process.chat_id = 12345
        self.process.rail = Mock()
        # Reading a reservation is not faked: which field holds the
        # reservation number, and which the payment deadline, is the
        # operator-specific translation this process depends on, and a Mock
        # would let a wrong reading through.
        self.process.rail.reservation_id = KorailService.reservation_id
        self.process.rail.payment_due = KorailService.payment_due
        self.process.telegram = Mock()
        self.process.storage = Mock()
        self.process.storage.get_payment_status.return_value = PaymentStatus(
            chat_id=12345, completed=False, reminder_active=True
        )

    def watch(self, outstanding_sequence, minutes_left=10.0):
        """
        Run the watch with a scripted set of answers from Korail.

        On a clock the test drives: sleeping is what moves the watch towards
        its deadline, so a stubbed-out sleep with a real clock would leave the
        loop spinning against a deadline that never arrives.
        """
        self.process.rail.is_reservation_outstanding.side_effect = outstanding_sequence
        clock = FakeClock(datetime(2026, 7, 30, 1, 46, 48))
        deadline = clock.now() + timedelta(minutes=minutes_left)

        with (
            patch.object(type(self.process), "_payment_deadline", return_value=deadline),
            patch("korail_bot.telegramBot.telebotBackProcess.datetime", clock),
            patch(
                "korail_bot.telegramBot.telebotBackProcess.time.sleep", side_effect=clock.advance
            ),
        ):
            self.process._watch_payment(reservation())
        self.clock = clock

    def sent(self):
        return [call.args[1] for call in self.process.telegram.send_message.call_args_list]

    def test_the_reservation_dropping_off_is_reported_as_paid(self):
        self.watch([True, True, False])

        assert Messages.PAYMENT_VERIFIED in self.sent()

    def test_the_watch_stops_as_soon_as_it_knows(self):
        self.watch([False, True, True])

        assert self.process.rail.is_reservation_outstanding.call_count == 1

    def test_a_reservation_still_listed_at_the_deadline_is_reported_as_lost(self):
        """
        The case the old guess got most wrong: the user said they had paid,
        so the reminders stopped, and nobody found out until the station.
        """
        self.watch([True] * 40)

        assert Messages.PAYMENT_EXPIRED_VERIFIED in self.sent()

    def test_being_told_it_expired_happens_even_if_the_user_claimed_otherwise(self):
        self.process.storage.get_payment_status.return_value = PaymentStatus(
            chat_id=12345, completed=True, reminder_active=False
        )

        self.watch([True] * 40)

        assert Messages.PAYMENT_EXPIRED_VERIFIED in self.sent()

    def test_a_confirmed_payment_is_not_announced_twice(self):
        """The user already said so, and they were right. Nothing to add."""
        self.process.storage.get_payment_status.return_value = PaymentStatus(
            chat_id=12345, completed=True, reminder_active=False
        )

        self.watch([False])

        assert self.sent() == []

    def test_korail_not_answering_does_not_end_the_watch(self):
        """
        A gap in the middle. The reservation is still there as far as anyone
        knows, so the watch keeps going rather than declaring either outcome.
        """
        self.watch([None, None, False])

        assert Messages.PAYMENT_VERIFIED in self.sent()
        assert self.process.rail.is_reservation_outstanding.call_count == 3

    def test_the_reminder_flag_is_cleared_either_way(self):
        """
        The reminder loop lives in the other process and reads this out of
        Redis. Left set, it goes on reminding about a settled reservation.
        """
        for sequence in ([False], [True] * 40):
            self.process.telegram.reset_mock()
            self.process.storage.reset_mock()
            status = PaymentStatus(chat_id=12345, completed=False, reminder_active=True)
            self.process.storage.get_payment_status.return_value = status

            self.watch(sequence)

            assert status.completed is True
            assert status.reminder_active is False
            self.process.storage.save_payment_status.assert_called_with(status)

    def test_a_reservation_with_no_number_is_not_watched(self):
        """Nothing to look for, and no basis for telling the user anything."""
        rsv = Mock()
        rsv.rsv_id = None

        self.process._watch_payment(rsv)

        assert self.process.telegram.send_message.call_count == 0


class TestPaymentDeadline:
    """Whose clock the watch runs on."""

    def setup_method(self):
        from korail_bot.telegramBot.telebotBackProcess import BackgroundReservationProcess

        self.process = BackgroundReservationProcess.__new__(BackgroundReservationProcess)
        # Which railway's names the deadline is read under. korail2 calls the
        # fields buy_limit_date/-time; SR calls them something else, and the
        # service is what knows which.
        self.process.rail = KorailService()

    def test_korails_own_deadline_is_used(self):
        """
        PAYMENT_TIMEOUT_MINUTES is this bot's idea of the window. Korail
        states the real one on the reservation, and theirs is the one that
        decides whether the seat is still there.
        """
        deadline = self.process._payment_deadline(reservation("1", "20260730", "015648"))

        assert deadline == datetime(2026, 7, 30, 1, 56, 48)

    @pytest.mark.parametrize(
        "rsv",
        [
            reservation("1", None, None),
            reservation("1", "not a date", "015648"),
            reservation("1", "20260730", "xx"),
        ],
    )
    def test_an_unreadable_deadline_falls_back_to_the_configured_window(self, rsv):
        """
        A reservation that arrives without one must not make the watch throw
        - the reservation is real and the user is waiting on it.
        """
        from korail_bot.config.settings import settings

        before = datetime.now() + timedelta(minutes=settings.PAYMENT_TIMEOUT_MINUTES)
        deadline = self.process._payment_deadline(rsv)

        assert deadline >= before - timedelta(seconds=5)


class TestReminderPacing:
    """How often the user's phone goes off while they are paying."""

    def test_one_reminder_a_minute(self):
        """
        Ten seconds meant sixty messages in a ten-minute window, arriving
        while the user is trying to type a card number into another app.
        """
        from korail_bot.config.settings import Settings

        assert Settings.PAYMENT_REMINDER_INTERVAL_SECONDS == 60

    def test_the_loop_stops_at_the_deadline_rather_than_past_it(self):
        """
        The bound used to overshoot by one interval. At ten seconds that was
        invisible; at a minute it holds back the "you lost the seat" message
        for a minute after the seat is gone.
        """
        from korail_bot.services.payment_reminder_service import PaymentReminderService

        service = PaymentReminderService.__new__(PaymentReminderService)
        service.storage = Mock()
        service.telegram = Mock()
        service.timeout_minutes = 10
        service.interval_seconds = 60
        service.storage.get_payment_status.return_value = PaymentStatus(
            chat_id=1, completed=False, reminder_active=True
        )

        with (
            patch("korail_bot.services.payment_reminder_service.time.sleep") as sleep,
            patch.object(service, "check_payment_completed", return_value=False),
        ):
            service._reminder_loop(1)

        assert sum(call.args[0] for call in sleep.call_args_list) == 600

    def test_the_last_tick_does_not_send_a_reminder_saying_zero_left(self):
        from korail_bot.services.payment_reminder_service import PaymentReminderService

        service = PaymentReminderService.__new__(PaymentReminderService)
        service.storage = Mock()
        service.telegram = Mock()
        service.timeout_minutes = 10
        service.interval_seconds = 60
        service.storage.get_payment_status.return_value = PaymentStatus(
            chat_id=1, completed=False, reminder_active=True
        )

        with (
            patch("korail_bot.services.payment_reminder_service.time.sleep"),
            patch.object(service, "check_payment_completed", return_value=False),
        ):
            service._reminder_loop(1)

        reminders = [
            call.args[1]
            for call in service.telegram.send_message.call_args_list
            if "리마인더" in call.args[1] and "종료" not in call.args[1]
        ]
        assert len(reminders) == 9
        assert Messages.PAYMENT_REMINDER_TIMEOUT in [
            call.args[1] for call in service.telegram.send_message.call_args_list
        ]
