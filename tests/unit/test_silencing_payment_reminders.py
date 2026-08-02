"""
Turning the payment reminders off without lying about the payment.

Any message at all used to mean "I have paid". The reminders stopped and the
booking was recorded as settled, which was wrong in both directions: someone
who typed "잠깐" was told the matter was closed and quietly lost the seat, and
someone who paid without answering was nagged to the deadline.

Now the railway is asked and the answer is reported, so a message from the
user has nothing to add. What they might actually want - quiet - is its own
command, and it says only that.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock

from korail_bot.handlers import TelegramUpdateProcessor
from korail_bot.handlers.command_handler import CommandHandler
from korail_bot.models import (
    MultiReservationStatus,
    PaymentStatus,
    ReservationPaymentStatus,
    SingleReservationInfo,
)
from korail_bot.services import (
    PaymentReminderService,
    ReservationService,
    TelegramService,
)
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot.messages import Messages

CHAT_ID = 31337


def reminding() -> PaymentStatus:
    """A booking with the reminders running and nothing decided."""
    return PaymentStatus(
        chat_id=CHAT_ID,
        completed=False,
        reminder_active=True,
        reservation_id="320260731221946",
        expires_at=datetime.now() + timedelta(minutes=9),
    )


def booking(pending: bool = True) -> MultiReservationStatus:
    return MultiReservationStatus(
        chat_id=CHAT_ID,
        reservations=[
            SingleReservationInfo(
                reservation_id="111",
                reservation_obj=None,
                reserved_at=datetime.now(),
                expires_at=datetime.now() + timedelta(minutes=9),
                status=ReservationPaymentStatus.PENDING
                if pending
                else ReservationPaymentStatus.PAID,
                seat_number=1,
                train_info="[KTX 101] 좌석 1",
            )
        ],
        total_seats=1,
        seat_strategy="random",
        created_at=datetime.now(),
    )


class TestAMessageIsJustAMessage:
    """The guess is gone from the router."""

    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_payment_status.return_value = reminding()
        self.storage.get_multi_reservation_status.return_value = None
        self.storage.get_current_seat_index.return_value = None
        self.storage.get_user_session.return_value = None
        self.storage.is_waiting_for_admin_password.return_value = False
        self.storage.get_pending_favourite_rename.return_value = None
        self.storage.is_waiting_for_notify_input.return_value = False
        self.storage.is_developer.return_value = False
        self.telegram = Mock(spec=TelegramService)
        self.payment_reminder = Mock(spec=PaymentReminderService)
        self.processor = TelegramUpdateProcessor(
            self.storage,
            self.telegram,
            Mock(spec=ReservationService),
            self.payment_reminder,
        )

    def send(self, text):
        self.processor.process({"update_id": 1, "message": {"chat": {"id": CHAT_ID}, "text": text}})

    def test_typing_something_does_not_settle_the_payment(self):
        self.send("잠깐만요")

        self.storage.save_payment_status.assert_not_called()

    def test_typing_something_does_not_stop_the_reminders(self):
        self.send("결제했어요")

        self.payment_reminder.silence.assert_not_called()

    def test_typing_something_does_not_mark_the_seats_of_a_random_run_paid(self):
        self.storage.get_multi_reservation_status.return_value = booking()

        self.send("네")

        self.storage.save_multi_reservation_status.assert_not_called()


class TestNotifyOff:
    """The command that does what a message used to be read as asking for."""

    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_payment_status.return_value = None
        self.storage.get_multi_reservation_status.return_value = None
        self.telegram = Mock(spec=TelegramService)
        self.payment_reminder = Mock(spec=PaymentReminderService)
        self.payment_reminder.silence.return_value = False
        self.handler = CommandHandler(
            self.storage,
            self.telegram,
            Mock(spec=ReservationService),
            self.payment_reminder,
        )
        self.handler.multi_reminder = Mock()

    def replied(self):
        return self.telegram.send_message.call_args.args[1]

    def test_it_stops_the_reminders_on_a_single_booking(self):
        self.payment_reminder.silence.return_value = True

        self.handler.handle_notify_off(CHAT_ID)

        self.payment_reminder.silence.assert_called_once_with(CHAT_ID)
        assert self.replied() == Messages.NOTIFY_OFF_DONE

    def test_it_says_the_payment_is_still_being_watched(self):
        """Quiet is not the same as settled, and the difference is the point."""
        self.payment_reminder.silence.return_value = True

        self.handler.handle_notify_off(CHAT_ID)

        assert "계속 확인" in self.replied()

    def test_it_stops_the_reminders_of_a_random_run(self):
        self.storage.get_multi_reservation_status.return_value = booking()

        self.handler.handle_notify_off(CHAT_ID)

        self.handler.multi_reminder.stop_reminders.assert_called_once_with(CHAT_ID, manual=True)
        assert self.replied() == Messages.NOTIFY_OFF_DONE

    def test_a_settled_random_run_has_nothing_to_stop(self):
        self.storage.get_multi_reservation_status.return_value = booking(pending=False)

        self.handler.handle_notify_off(CHAT_ID)

        self.handler.multi_reminder.stop_reminders.assert_not_called()

    def test_nothing_to_stop_is_said_plainly(self):
        self.handler.handle_notify_off(CHAT_ID)

        assert self.replied() == Messages.NOTIFY_OFF_NOTHING

    def test_the_other_notification_is_named_when_there_was_nothing_to_stop(self):
        """/notify is a different noise from a different part of the bot."""
        self.handler.handle_notify_off(CHAT_ID)

        assert "/notify" in self.replied()

    def test_the_command_is_routed(self):
        assert self.handler.route_command(CHAT_ID, "/notify_off") is True
        assert self.telegram.send_message.called

    def test_it_is_not_swallowed_by_the_progress_report_command(self):
        """/notify takes an argument; /notify_off is not one of them."""
        self.handler.handle_notify = Mock()

        self.handler.route_command(CHAT_ID, "/notify_off")

        self.handler.handle_notify.assert_not_called()


class TestItIsOffered:
    """A command nobody is told about is a command nobody uses."""

    def test_the_menu_carries_it(self):
        assert any(entry["command"] == "notify_off" for entry in Messages.PUBLIC_COMMANDS)

    def test_the_help_carries_it(self):
        assert "/notify_off" in Messages.HELP

    def test_the_reminder_itself_carries_it(self):
        from korail_bot.services import MessageTemplates

        assert "/notify_off" in MessageTemplates.payment_reminder(5, 0)
