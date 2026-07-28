"""Telegram webhook API endpoint."""
from flask import request, make_response
from flask_restful import Resource

from api.auth import verify_internal_request, verify_telegram_request
from storage.base import StorageInterface
from services import TelegramService, ReservationService, PaymentReminderService
from handlers import TelegramUpdateProcessor
from utils.logger import get_logger
from utils.privacy import mask_phone

logger = get_logger(__name__)


class TelegramWebhook(Resource):
    """
    Flask-RESTful resource for handling Telegram webhook callbacks.

    This replaces the old Index class from telebotApiHandler.py
    """

    def __init__(
        self,
        storage: StorageInterface,
        telegram_service: TelegramService,
        reservation_service: ReservationService,
        payment_reminder_service: PaymentReminderService,
        **kwargs
    ):
        """
        Initialize webhook handler.

        Args:
            storage: Storage interface
            telegram_service: Telegram messaging service
            reservation_service: Reservation service
            payment_reminder_service: Payment reminder service
        """
        super().__init__(**kwargs)
        self.storage = storage
        self.telegram = telegram_service
        self.reservation = reservation_service
        self.payment_reminder = payment_reminder_service

        # The routing itself is shared with the poller, so it lives outside
        # this resource and knows nothing about Flask.
        self.processor = TelegramUpdateProcessor(
            storage, telegram_service, reservation_service, payment_reminder_service
        )
        # The GET callback needs the same reminder service instance the
        # processor uses, so that its thread bookkeeping stays consistent.
        self.multi_reminder = self.processor.multi_reminder

    def post(self):
        """
        Handle POST request from Telegram webhook.

        This is called when users send messages to the bot.
        """
        # Reject anything that cannot prove it came from Telegram, otherwise
        # anyone reaching this port could impersonate any chat_id.
        if not verify_telegram_request():
            return make_response("Forbidden", 403)

        try:
            update = request.json
        except Exception as e:
            # A body we cannot parse will not become parseable on a retry, so
            # acknowledge it instead of making Telegram resend it forever.
            logger.error(f"Malformed webhook body: {e}")
            return make_response("OK")

        self.processor.process(update)

        # Always OK: Telegram retries anything else, and a retry cannot fix a
        # failure that happened while handling the update.
        return make_response("OK")

    def get(self):
        """
        Handle GET request for callbacks from background processes.

        This is used by the background reservation process to notify
        the bot about reservation results. It can send arbitrary text to
        arbitrary chats, so it is restricted to our own processes.
        """
        if not verify_internal_request():
            return make_response("Forbidden", 403)

        try:
            # Extract parameters
            chat_id = request.args.get('chatId')
            msg = request.args.get('msg')
            status = request.args.get('status')
            is_multi = request.args.get('isMulti', '0')
            total_seats = request.args.get('totalSeats', '1')
            seat_strategy = request.args.get('seatStrategy', 'consecutive')

            if not all([chat_id, msg, status]):
                logger.warning("Incomplete callback parameters")
                return make_response("OK")

            chat_id = int(chat_id)
            is_multi = (is_multi == '1')
            total_seats = int(total_seats)

            logger.info(
                f"Callback from background process: chat_id={chat_id}, status={status}, "
                f"is_multi={is_multi}, total_seats={total_seats}, seat_strategy={seat_strategy}"
            )

            # Send message to user
            self.telegram.send_message(chat_id, msg)

            # Handle different status codes
            # status=0: Complete success (all reservations done)
            # status=1: Error/failure
            # status=2: Partial success (random seating intermediate notification)

            if str(status) == "2":
                # Partial reservation notification (random seating)
                logger.info(f"Partial reservation notification for chat_id={chat_id}")

                # Check if multi-reservation status exists and start reminders if needed
                multi_status = self.storage.get_multi_reservation_status(chat_id)
                if multi_status:
                    # Start multi-reservation reminders (checks for duplicates internally)
                    self.multi_reminder.start_reminders(chat_id)

                # Message already sent above, no further action needed
                # User will send payment confirmation which will be handled by POST webhook
                return make_response("OK")

            # If reservation successful (status == 0)
            if str(status) == "0":
                logger.info(f"Reservation successful for chat_id={chat_id}")

                # Reset user session
                session = self.storage.get_user_session(chat_id)
                if session:
                    session.reset()
                    self.storage.save_user_session(session)

                # Start appropriate payment reminders
                # For random seating, multi-reminder is already running (started on first seat)
                # Don't start duplicate reminder service
                if seat_strategy == "random":
                    logger.info(f"Random seating complete - multi-reminder already running for chat_id={chat_id}")
                    # Multi-reservation reminder was started on first partial callback (status=2)
                    # It will continue until all seats are paid or expired
                elif is_multi:
                    logger.info(f"Starting multi-reservation reminders for chat_id={chat_id}")
                    # Consecutive seating with multiple passengers
                    # TODO: This path may need multi-reminder support in the future
                    self.payment_reminder.start_reminders(chat_id)
                else:
                    logger.info(f"Starting single payment reminders for chat_id={chat_id}")
                    self.payment_reminder.start_reminders(chat_id)

                # Clean up running reservation
                self.storage.delete_running_reservation(chat_id)

                # Notify subscribers
                subscribers = self.storage.get_all_subscribers()
                if session and session.credentials:
                    user_id = mask_phone(session.credentials.korail_id)
                    self.telegram.send_to_multiple(
                        subscribers,
                        f"{user_id}의 예약이 종료되었습니다."
                    )

            return make_response("OK")

        except Exception as e:
            logger.error(f"Error handling callback: {e}", exc_info=True)
            return make_response("OK")
