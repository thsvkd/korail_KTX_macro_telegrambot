"""
Deciding whether the user still owes money.

The reminder loop's pacing is covered in test_payment_verification.py. What is
here is the question it asks on every pass - has this been paid for - and the
guard that stops two loops asking it about the same reservation at once.

Both fail in the same direction and it is the bad one. Reading "paid" out of a
failed Redis read or an unreachable endpoint stops the reminders on a seat the
user still has ten minutes to pay for, and they find out when the reservation
expires. Two loops running at once is the mirror image: two messages a minute
while somebody is trying to type a card number into another app.
"""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from korail_bot.config.settings import settings
from korail_bot.models import PaymentStatus
from korail_bot.services.payment_reminder_service import PaymentReminderService
from korail_bot.services.telegram_service import TelegramService
from korail_bot.storage.base import StorageInterface

MODULE = "korail_bot.services.payment_reminder_service"
CHAT_ID = 12345


def answer(completed):
    """What /check_payment answers with."""
    response = Mock()
    response.json.return_value = {"completed": completed}
    return response


class ReminderFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_payment_status.return_value = None
        self.telegram = Mock(spec=TelegramService)
        self.service = PaymentReminderService(self.storage, self.telegram)


class TestHasItBeenPaidFor(ReminderFixture):
    """
    Asked on every pass of the loop, and the only thing that stops it early.

    Two sources: what this app wrote down, and the endpoint the search process
    reports to. Either saying yes is enough - they are the same fact recorded
    by two processes that do not share memory.
    """

    def check(self, error=None, response=None):
        with patch(f"{MODULE}.requests.get") as get:
            if error is not None:
                get.side_effect = error
            else:
                get.return_value = response if response is not None else answer(False)
            return self.service.check_payment_completed(CHAT_ID)

    def test_a_payment_recorded_here_settles_it_without_asking_further(self):
        self.storage.get_payment_status.return_value = PaymentStatus(
            chat_id=CHAT_ID, completed=True, reminder_active=False
        )

        with patch(f"{MODULE}.requests.get") as get:
            assert self.service.check_payment_completed(CHAT_ID) is True

        get.assert_not_called()

    def test_the_endpoint_is_asked_when_nothing_is_recorded_here(self):
        assert self.check(response=answer(True)) is True

    def test_nothing_anywhere_means_it_is_still_owed(self):
        assert self.check(response=answer(False)) is False

    def test_it_proves_it_came_from_inside(self):
        """
        The endpoint answers with per-user payment state and sits on a port.
        """
        with patch(f"{MODULE}.requests.get", return_value=answer(False)) as get:
            self.service.check_payment_completed(CHAT_ID)

        assert get.call_args.kwargs["params"]["token"] == settings.INTERNAL_CALLBACK_TOKEN

    def test_the_call_is_given_up_on_rather_than_hanging_the_loop(self):
        with patch(f"{MODULE}.requests.get", return_value=answer(False)) as get:
            self.service.check_payment_completed(CHAT_ID)

        assert get.call_args.kwargs["timeout"] > 0

    @pytest.mark.parametrize(
        "error",
        [ConnectionError("no route"), TimeoutError(), ValueError("bad json"), Exception("500")],
        ids=["refused", "timeout", "unreadable", "unexpected"],
    )
    def test_not_being_able_to_ask_is_not_a_payment(self, error):
        """
        The direction that matters. Reading a failed request as "paid" would
        end the reminders on a seat the user still has minutes to pay for,
        and they would find out when it expired.
        """
        assert self.check(error=error) is False

    def test_redis_being_unreadable_is_not_a_payment_either(self):
        self.storage.get_payment_status.side_effect = Exception("redis is down")

        assert self.check() is False


class TestStartingTheReminders(ReminderFixture):
    """One loop per reservation, and no more than one."""

    def teardown_method(self):
        # The loop is a daemon thread; make sure it is not left sleeping
        # against the next test's mocks.
        self.storage.get_payment_status.return_value = PaymentStatus(
            chat_id=CHAT_ID, completed=True, reminder_active=False
        )

    def start(self):
        with patch.object(self.service, "_reminder_loop") as loop:
            self.service.start_reminders(CHAT_ID)
        return loop

    def test_a_fresh_reservation_gets_a_loop(self):
        loop = self.start()

        loop.assert_called_once_with(CHAT_ID)

    def test_the_reservation_is_written_down_as_awaiting_payment(self):
        """
        The loop, the /cancel path and the search process all read this. It
        has to exist before any of them looks.
        """
        self.start()

        written = self.storage.save_payment_status.call_args.args[0]
        assert written.chat_id == CHAT_ID
        assert written.completed is False
        assert written.reminder_active is True

    def test_a_reservation_already_being_reminded_about_does_not_get_a_second_loop(self):
        """
        Two loops means two messages a minute, from threads neither of which
        knows about the other.
        """
        self.storage.get_payment_status.return_value = PaymentStatus(
            chat_id=CHAT_ID, completed=False, reminder_active=True
        )

        loop = self.start()

        loop.assert_not_called()
        self.storage.save_payment_status.assert_not_called()

    def test_a_settled_reservation_can_be_reminded_about_again(self):
        """
        The next booking. A finished record must not lock the chat out of
        ever being reminded again.
        """
        self.storage.get_payment_status.return_value = PaymentStatus(
            chat_id=CHAT_ID, completed=True, reminder_active=False, created_at=datetime.now()
        )

        self.start().assert_called_once_with(CHAT_ID)


class TestStoppingThem(ReminderFixture):
    """
    How the loop is told, given it is asleep on a thread with no way in.

    It reads the flag out of Redis on its next pass.
    """

    def test_the_flag_comes_down(self):
        status = PaymentStatus(chat_id=CHAT_ID, completed=False, reminder_active=True)
        self.storage.get_payment_status.return_value = status

        self.service.deactivate_reminders(CHAT_ID)

        assert status.reminder_active is False
        self.storage.save_payment_status.assert_called_once_with(status)

    def test_the_payment_can_be_settled_at_the_same_time(self):
        """
        What /cancel does. Without it the loop reaches its deadline and warns
        the user about a reservation they have already dealt with.
        """
        status = PaymentStatus(chat_id=CHAT_ID, completed=False, reminder_active=True)
        self.storage.get_payment_status.return_value = status

        self.service.deactivate_reminders(CHAT_ID, completed=True)

        assert status.completed is True

    def test_stopping_it_by_default_says_nothing_about_the_payment(self):
        """
        The loop hitting its own timeout goes through here too, and that is
        precisely the case where nothing was paid.
        """
        status = PaymentStatus(chat_id=CHAT_ID, completed=False, reminder_active=True)
        self.storage.get_payment_status.return_value = status

        self.service.deactivate_reminders(CHAT_ID)

        assert status.completed is False

    def test_stopping_something_that_was_never_started_is_not_an_error(self):
        self.service.deactivate_reminders(CHAT_ID)

        self.storage.save_payment_status.assert_not_called()
