"""
/status reporting a seat that is booked and unpaid, and the button beside it.

/status used to answer one question - "is it still looking?" - which is the
wrong question for someone whose search already ended in a seat. That seat is
the thing with a clock on it.

The button is routed the way the dead-search buttons are: before the staleness
check, because it answers nothing in the conversation and stays valid for as
long as the railway holds the seat.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock

from korail_bot.handlers import TelegramUpdateProcessor
from korail_bot.handlers.command_handler import CommandHandler
from korail_bot.models import Operator, PaymentStatus
from korail_bot.services import (
    PaymentReminderService,
    ReservationService,
    TelegramService,
)
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot import keyboards

CHAT_ID = 24680


def pending_status(**kwargs):
    return PaymentStatus(
        chat_id=CHAT_ID,
        completed=False,
        reminder_active=True,
        reservation_id="320260731221946",
        train_info="[KTX 101] 서울(09:00)->부산(11:40)",
        operator=Operator.KORAIL,
        expires_at=datetime.now() + timedelta(minutes=9),
        **kwargs,
    )


class TestStatusReportsIt:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_payment_status.return_value = None
        self.storage.get_multi_reservation_status.return_value = None
        self.telegram = Mock(spec=TelegramService)
        self.reservation = Mock(spec=ReservationService)
        self.reservation.get_status.return_value = "진행중인 예약이 없습니다."
        self.handler = CommandHandler(
            self.storage, self.telegram, self.reservation, Mock(spec=PaymentReminderService)
        )

    def replied(self):
        return self.telegram.send_message.call_args.args[1]

    def keyboard(self):
        return self.telegram.send_message.call_args.kwargs.get("reply_markup")

    def test_a_booked_seat_is_reported_beside_the_search(self):
        self.storage.get_payment_status.return_value = pending_status()

        self.handler.handle_status(CHAT_ID)

        assert "진행중인 예약이 없습니다." in self.replied()
        assert "결제를 기다리는 예약" in self.replied()

    def test_the_way_to_give_it_back_comes_with_it(self):
        self.storage.get_payment_status.return_value = pending_status()

        self.handler.handle_status(CHAT_ID)

        assert self.keyboard() == keyboards.payment_pending_keyboard()

    def test_nothing_booked_leaves_the_status_as_it_was(self):
        self.handler.handle_status(CHAT_ID)

        assert self.replied() == "진행중인 예약이 없습니다."
        assert self.keyboard() is None


class TestTheButtonIsRouted:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.processor = TelegramUpdateProcessor(
            self.storage,
            self.telegram,
            Mock(spec=ReservationService),
            Mock(spec=PaymentReminderService),
        )
        self.processor.command_handler.pending_payments = Mock()

    def press(self, value):
        self.processor.process_callback_query(
            {
                "id": "cbq-1",
                "from": {"id": CHAT_ID},
                "message": {"message_id": 5, "chat": {"id": CHAT_ID}, "text": "상태"},
                "data": f"{keyboards.STEP_PAY}:{value}",
            }
        )

    def test_asking_to_cancel_puts_the_question_first(self):
        self.press(keyboards.PAY_CANCEL)

        self.processor.command_handler.pending_payments.confirm_cancellation.assert_called_once_with(
            CHAT_ID
        )

    def test_confirming_gives_the_seat_back(self):
        self.press(keyboards.PAY_CONFIRM_CANCEL)

        self.processor.command_handler.pending_payments.cancel.assert_called_once_with(CHAT_ID)

    def test_keeping_it_cancels_nothing(self):
        self.press(keyboards.PAY_KEEP)

        self.processor.command_handler.pending_payments.cancel.assert_not_called()

    def test_the_press_survives_a_session_on_another_question(self):
        """
        It answers nothing in the conversation - the booking is over - so the
        staleness check that guards the flow's own buttons must not eat it.
        """
        self.storage.get_user_session.return_value = None

        self.press(keyboards.PAY_CONFIRM_CANCEL)

        self.processor.command_handler.pending_payments.cancel.assert_called_once_with(CHAT_ID)
