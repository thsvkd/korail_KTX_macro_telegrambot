"""
Routing of a single Telegram update, independent of how it was received.

Webhook and polling deliver exactly the same update objects, so the routing
lives here rather than in the Flask resource: TelegramWebhook.post() hands
over request.json, TelegramPoller hands over each getUpdates entry.
"""

from korail_bot.handlers.command_handler import CommandHandler
from korail_bot.handlers.conversation_handler import ConversationHandler
from korail_bot.services import (
    MultiReservationReminderService,
    PaymentReminderService,
    ReservationService,
    TelegramService,
)
from korail_bot.storage.base import StorageInterface
from korail_bot.utils.logger import get_logger

logger = get_logger(__name__)


class TelegramUpdateProcessor:
    """Dispatches Telegram updates to the command and conversation handlers."""

    def __init__(
        self,
        storage: StorageInterface,
        telegram_service: TelegramService,
        reservation_service: ReservationService,
        payment_reminder_service: PaymentReminderService,
    ):
        """
        Initialize the update processor.

        Args:
            storage: Storage interface
            telegram_service: Telegram messaging service
            reservation_service: Reservation service
            payment_reminder_service: Payment reminder service
        """
        self.storage = storage
        self.telegram = telegram_service
        self.reservation = reservation_service
        self.payment_reminder = payment_reminder_service

        # Initialize multi-reservation reminder service (singleton for thread tracking)
        self.multi_reminder = MultiReservationReminderService(storage, telegram_service)

        # Initialize handlers
        self.command_handler = CommandHandler(
            storage, telegram_service, reservation_service, payment_reminder_service
        )
        self.conversation_handler = ConversationHandler(
            storage, telegram_service, reservation_service
        )

    def process(self, update: dict) -> None:
        """
        Handle one Telegram update.

        Never raises: an update that cannot be processed must not stop the
        webhook from acknowledging it, nor the poller from reading the next
        one.

        Args:
            update: Telegram update object as delivered by the Bot API
        """
        try:
            # Ignore edited messages and chat member updates
            if "edited_message" in update or "my_chat_member" in update:
                return

            # Extract message
            try:
                message = update["message"]
                text = message["text"].strip()
                chat_id = int(message["chat"]["id"])
            except (KeyError, ValueError) as e:
                logger.error(f"Invalid message format: {e}")
                return

            logger.info(f"Received message from chat_id={chat_id}: {text}")

            # Get user session to check progress
            session = self.storage.get_user_session(chat_id)
            in_progress = session.in_progress if session else False
            progress_num = session.last_action if session else 0

            logger.debug(f"chat_id={chat_id}, in_progress={in_progress}, progress={progress_num}")

            # Check for payment reminder active state (single reservation)
            payment_status = self.storage.get_payment_status(chat_id)
            if payment_status and payment_status.reminder_active and not payment_status.completed:
                # User sent any non-command message during payment reminder
                if text and not text.startswith("/"):
                    self.payment_reminder.confirm_payment(chat_id)
                    return

            # Check for multi-reservation reminder active state (no current_seat_index set)
            # This handles the case when ALL seats are reserved but waiting for final payment
            multi_status = self.storage.get_multi_reservation_status(chat_id)
            if multi_status and multi_status.should_show_reminder():
                # Check if we're NOT in middle of random seating (no current_seat_index)
                current_seat = self.storage.get_current_seat_index(chat_id)
                if current_seat is None:
                    # All seats reserved, just waiting for payment confirmation
                    if text and not text.startswith("/"):
                        # Mark all as paid and stop reminders
                        self.multi_reminder.mark_all_paid(chat_id)

                        # Send confirmation
                        self.telegram.send_message(
                            chat_id, "✅ 결제 완료 확인!\n\n모든 좌석의 결제 알림이 중단되었습니다."
                        )
                        return

            # Handle /cancel command first (works in any state)
            if text == "/cancel":
                self.command_handler.handle_cancel(chat_id)
                return

            # Route commands BEFORE checking random seating state
            # This allows users to use /help, /status even during payment waiting
            if self.command_handler.is_command(text):
                self.command_handler.route_command(chat_id, text)
                return

            # Check if random seating in progress (waiting for payment confirmation)
            current_seat = self.storage.get_current_seat_index(chat_id)
            if current_seat is not None:  # Random seating in progress
                # ANY message confirms payment and proceeds to next seat
                logger.info(
                    f"Payment confirmed for seat {current_seat} by user message, chat_id={chat_id}"
                )

                # Mark payment ready for background process
                self.storage.mark_payment_ready(chat_id, current_seat)

                # DON'T stop reminders - they should continue running for remaining seats
                # The reminder service will automatically update when new seats are added
                logger.info("Payment confirmed, reminders will continue for remaining seats")

                # Send confirmation
                self.telegram.send_message(
                    chat_id,
                    f"✅ {current_seat + 1}번째 좌석 결제 확인!\n\n다음 좌석 예약을 시작합니다...",
                )

                return

            # Check if waiting for admin password (takes priority over everything)
            if self.storage.is_waiting_for_admin_password(chat_id):
                # User is waiting to enter admin password
                # Both outcomes are terminal: the handler already told the user.
                self.command_handler.handle_admin_password(chat_id, text)
                return

            # Handle conversation flow (non-command messages)
            if in_progress:
                # Handle conversation flow
                self.conversation_handler.handle_message(chat_id, text)
            else:
                # No active session and not a command
                self.telegram.send_message(
                    chat_id,
                    "[진행중인 예약프로세스가 없습니다]\n/start 를 입력하여 작업을 시작하세요.",
                )

        except Exception as e:
            logger.error(f"Error handling update: {e}", exc_info=True)
