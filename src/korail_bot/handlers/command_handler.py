"""Command handler for Telegram bot commands."""

import hmac

from korail_bot.config.settings import settings
from korail_bot.models import UserProgress, UserSession
from korail_bot.services import (
    MessageTemplates,
    PaymentReminderService,
    ReservationService,
    TelegramService,
)
from korail_bot.storage.base import StorageInterface
from korail_bot.utils.logger import LoggerFactory, get_logger
from korail_bot.utils.privacy import mask_phone

logger = get_logger(__name__)


class CommandHandler:
    """Handles Telegram bot commands like /start, /cancel, etc."""

    def __init__(
        self,
        storage: StorageInterface,
        telegram_service: TelegramService,
        reservation_service: ReservationService,
        payment_reminder_service: PaymentReminderService,
    ):
        """
        Initialize command handler.

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

    def handle_start(self, chat_id: int) -> None:
        """
        Handle /start command.

        Args:
            chat_id: Telegram chat ID
        """
        logger.info(f"Handling /start for chat_id={chat_id}")

        # Get or create user session
        session = self.storage.get_user_session(chat_id)
        if not session:
            session = UserSession(chat_id=chat_id, in_progress=False, last_action=UserProgress.INIT)
            self.storage.save_user_session(session)

        # Update session state
        session.in_progress = True
        session.last_action = UserProgress.STARTED
        self.storage.save_user_session(session)

        # Send welcome message
        self.telegram.send_message(
            chat_id,
            MessageTemplates.welcome_message(
                skip_login_prompts=settings.has_preconfigured_korail_credentials()
            ),
        )

    def handle_cancel(self, chat_id: int) -> None:
        """
        Handle /cancel command.

        Args:
            chat_id: Telegram chat ID
        """
        logger.info(f"Handling /cancel for chat_id={chat_id}")

        # Cancel any running reservation
        self.reservation.cancel_reservation(chat_id)

        # Reset user session
        session = self.storage.get_user_session(chat_id)
        if session:
            session.reset()
            self.storage.save_user_session(session)

        # Clear multi-reservation status (for random seating)
        self.storage.delete_multi_reservation_status(chat_id)
        logger.debug(f"Cleared multi-reservation status for chat_id={chat_id}")

        # Clear payment status and deactivate reminders
        payment_status = self.storage.get_payment_status(chat_id)
        if payment_status:
            payment_status.completed = True
            payment_status.reminder_active = False
            self.storage.save_payment_status(payment_status)
            logger.debug(f"Cleared payment status for chat_id={chat_id}")

        # Clear admin password waiting state if any
        self.storage.set_waiting_for_admin_password(chat_id, False)

        # Send cancellation message
        self.telegram.send_message(chat_id, "✅ 예약이 취소되었습니다.")

    def handle_subscribe(self, chat_id: int) -> None:
        """
        Handle /subscribe command.

        Args:
            chat_id: Telegram chat ID
        """
        logger.info(f"Handling /subscribe for chat_id={chat_id}")

        if self.storage.is_subscriber(chat_id):
            message = "이미 구독했습니다."
        else:
            self.storage.add_subscriber(chat_id)
            message = "열차 이용정보 구독 설정이 완료되었습니다."

        self.telegram.send_message(chat_id, message)

    def handle_status(self, chat_id: int) -> None:
        """
        Handle /status command.

        Args:
            chat_id: Telegram chat ID
        """
        logger.info(f"Handling /status for chat_id={chat_id}")

        status_message = self.reservation.get_status(chat_id)
        self.telegram.send_message(chat_id, status_message)

    def handle_debug_on(self, chat_id: int) -> None:
        """
        Handle /debug_on command.

        Args:
            chat_id: Telegram chat ID
        """
        logger.info(f"Handling /debug_on for chat_id={chat_id}")
        self.storage.set_debug_mode(True)
        LoggerFactory.set_log_level("DEBUG")
        self.telegram.send_message(
            chat_id,
            "🐛 디버그 로그가 활성화되었습니다.\n\n"
            "서버 로그 레벨이 DEBUG로 전환되었습니다.\n"
            "새로 시작하는 예약 검색부터 상세 로그가 출력됩니다.\n"
            "/debug_off로 비활성화할 수 있습니다.",
        )

    def handle_debug_off(self, chat_id: int) -> None:
        """
        Handle /debug_off command.

        Args:
            chat_id: Telegram chat ID
        """
        logger.info(f"Handling /debug_off for chat_id={chat_id}")
        self.storage.set_debug_mode(False)
        LoggerFactory.set_log_level("INFO")
        self.telegram.send_message(
            chat_id,
            "✅ 디버그 로그가 비활성화되었습니다.\n\n서버 로그 레벨이 INFO로 복원되었습니다.",
        )

    def handle_cancel_all(self, chat_id: int) -> None:
        """
        Handle /cancelall command (admin only).

        Args:
            chat_id: Telegram chat ID
        """
        logger.info(f"Handling /cancelall for chat_id={chat_id}")

        # This is an admin command - in production, you'd want to check permissions
        count = self.reservation.cancel_all_reservations(chat_id)
        logger.info(f"Cancelled {count} reservations by admin chat_id={chat_id}")

    def handle_all_users(self, chat_id: int) -> None:
        """
        Handle /allusers command (admin only).

        Args:
            chat_id: Telegram chat ID
        """
        logger.info(f"Handling /allusers for chat_id={chat_id}")

        sessions = self.storage.get_all_user_sessions()
        user_ids = []

        for session in sessions:
            if session.credentials:
                user_ids.append(mask_phone(session.credentials.korail_id))
            else:
                user_ids.append(f"chat_{session.chat_id}")

        message = f"총 {len(user_ids)}명의 유저가 있습니다 : {user_ids}"
        self.telegram.send_message(chat_id, message)

    def handle_broadcast(self, chat_id: int, message: str) -> None:
        """
        Handle /broadcast command (admin only).

        Args:
            chat_id: Admin chat ID
            message: Message to broadcast
        """
        logger.info(f"Handling /broadcast for chat_id={chat_id}")

        # Get all user chat IDs
        sessions = self.storage.get_all_user_sessions()
        all_chat_ids = [s.chat_id for s in sessions]

        if message:
            sent_count = self.telegram.send_to_multiple(all_chat_ids, message)
            logger.info(f"Broadcast sent to {sent_count}/{len(all_chat_ids)} users")
        else:
            # Default fun message if no message provided
            self.telegram.send_to_multiple(all_chat_ids, "앙 기모띠")

    def handle_flush_redis(self, chat_id: int) -> None:
        """
        Handle /flushredis command (admin only).

        WARNING: This will delete ALL Redis data including all user sessions,
        running reservations, and payment statuses.

        Args:
            chat_id: Admin chat ID
        """
        logger.warning(f"Handling /flushredis for chat_id={chat_id}")

        try:
            # Check if storage is Redis-based
            if not hasattr(self.storage, "flush_all"):
                self.telegram.send_message(chat_id, "❌ 현재 스토리지는 Redis가 아닙니다.")
                return

            # Flush all data
            deleted_count = self.storage.flush_all()

            message = f"✅ Redis 메모리가 초기화되었습니다.\n삭제된 키: {deleted_count}개"
            self.telegram.send_message(chat_id, message)
            logger.warning(
                f"Redis flushed by admin chat_id={chat_id}, deleted {deleted_count} keys"
            )

        except Exception as e:
            logger.error(f"Failed to flush Redis: {e}")
            self.telegram.send_message(chat_id, f"❌ Redis 초기화 실패: {e!s}")

    def handle_help(self, chat_id: int) -> None:
        """
        Handle /help command.

        Args:
            chat_id: Telegram chat ID
        """
        logger.info(f"Handling /help for chat_id={chat_id}")
        self.telegram.send_message(chat_id, MessageTemplates.help_message())

    def handle_unknown_command(self, chat_id: int, command: str) -> None:
        """
        Handle unknown commands.

        Args:
            chat_id: Telegram chat ID
            command: Unknown command text
        """
        logger.warning(f"Unknown command '{command}' from chat_id={chat_id}")
        self.telegram.send_message(
            chat_id,
            f"알 수 없는 명령어입니다: {command}\n\n"
            f"📌 사용 가능한 명령어:\n"
            f"/start - 예약 시작\n"
            f"/cancel - 예약 취소\n"
            f"/status - 상태 확인\n"
            f"/help - 도움말\n\n"
            f"🔧 관리자 명령어는 별도로 문의하세요.",
        )

    def is_command(self, text: str) -> bool:
        """
        Check if text is a command.

        Args:
            text: Message text

        Returns:
            True if text starts with '/'
        """
        return text and text.startswith("/")

    def route_command(self, chat_id: int, text: str) -> bool:
        """
        Route command to appropriate handler.

        Args:
            chat_id: Telegram chat ID
            text: Command text

        Returns:
            True if command was handled, False otherwise
        """
        if not self.is_command(text):
            return False

        # Parse command and arguments
        parts = text.split(" ", 1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # Public commands
        if command == "/start":
            self.handle_start(chat_id)
        elif command == "/cancel":
            self.handle_cancel(chat_id)
        elif command == "/status":
            self.handle_status(chat_id)
        elif command == "/help":
            self.handle_help(chat_id)
        # Admin commands - require authentication
        elif command == "/subscribe":
            self._handle_admin_command(chat_id, self.handle_subscribe, "/subscribe")
        elif command == "/cancelall":
            self._handle_admin_command(chat_id, self.handle_cancel_all, "/cancelall")
        elif command == "/allusers":
            self._handle_admin_command(chat_id, self.handle_all_users, "/allusers")
        elif command == "/broadcast":
            self._handle_admin_command(
                chat_id, lambda cid: self.handle_broadcast(cid, args), f"/broadcast {args}"
            )
        elif command == "/flushredis":
            self._handle_admin_command(chat_id, self.handle_flush_redis, "/flushredis")
        # Debug commands - admin only
        elif command == "/debug_on":
            self._handle_admin_command(chat_id, self.handle_debug_on, "/debug_on")
        elif command == "/debug_off":
            self._handle_admin_command(chat_id, self.handle_debug_off, "/debug_off")
        else:
            self.handle_unknown_command(chat_id, command)

        return True

    def _is_locked_out(self, chat_id: int) -> bool:
        """Check whether admin authentication is currently blocked."""
        return self.storage.get_admin_auth_failures(chat_id) >= settings.ADMIN_MAX_AUTH_FAILURES

    def _send_lockout_message(self, chat_id: int) -> None:
        """Tell the user how long the lockout lasts."""
        from korail_bot.telegramBot.messages import Messages

        remaining_seconds = self.storage.get_admin_lockout_remaining(chat_id)
        remaining_minutes = max(1, -(-remaining_seconds // 60))  # round up
        self.telegram.send_message(
            chat_id, Messages.ADMIN_AUTH_LOCKED.format(remaining_minutes=remaining_minutes)
        )

    def _handle_admin_command(self, chat_id: int, handler_func, command_name: str = "") -> None:
        """
        Handle admin command with authentication check.

        Args:
            chat_id: Telegram chat ID
            handler_func: Function to call if authenticated
            command_name: Name of the command for tracking
        """
        from korail_bot.telegramBot.messages import Messages

        # No admin password configured means no admin surface at all.
        if not settings.ADMIN_PASSWORD:
            self.telegram.send_message(chat_id, Messages.ADMIN_DISABLED)
            logger.warning(
                f"Admin command {command_name} refused for chat_id={chat_id}: "
                f"ADMIN_PASSWORD is not configured"
            )
            return

        if self.storage.is_admin_authenticated(chat_id):
            # Already authenticated, execute command
            handler_func(chat_id)
            return

        if self._is_locked_out(chat_id):
            self._send_lockout_message(chat_id)
            logger.warning(
                f"Admin command {command_name} refused for chat_id={chat_id}: "
                f"locked out after repeated failures"
            )
            return

        # Request password and mark as waiting
        self.storage.set_waiting_for_admin_password(chat_id, True)
        self.storage.set_pending_admin_command(chat_id, command_name)
        self.telegram.send_message(chat_id, Messages.ADMIN_AUTH_REQUIRED)
        logger.info(f"Admin authentication required for chat_id={chat_id}, command={command_name}")

    def handle_admin_password(self, chat_id: int, password: str) -> bool:
        """
        Handle admin password input.

        Attempts are rate limited: after ADMIN_MAX_AUTH_FAILURES failures the
        chat is locked out for ADMIN_LOCKOUT_SECONDS, which turns an unlimited
        online guessing channel into a bounded one.

        Args:
            chat_id: Telegram chat ID
            password: Password attempt

        Returns:
            True if authenticated successfully
        """
        from korail_bot.telegramBot.messages import Messages

        # Get pending command before clearing state
        pending_command = self.storage.get_pending_admin_command(chat_id)

        # Clear waiting state
        self.storage.set_waiting_for_admin_password(chat_id, False)
        self.storage.set_pending_admin_command(chat_id, None)

        if not settings.ADMIN_PASSWORD:
            self.telegram.send_message(chat_id, Messages.ADMIN_DISABLED)
            return False

        if self._is_locked_out(chat_id):
            self._send_lockout_message(chat_id)
            logger.warning(f"Admin authentication blocked (locked out): chat_id={chat_id}")
            return False

        # Constant-time comparison so the password cannot be recovered by
        # timing individual characters. Compared as bytes because
        # compare_digest rejects str inputs containing non-ASCII characters,
        # and users do type Korean into this prompt.
        if hmac.compare_digest(password.encode("utf-8"), settings.ADMIN_PASSWORD.encode("utf-8")):
            self.storage.clear_admin_auth_failures(chat_id)
            self.storage.set_admin_authenticated(chat_id, True)
            self.telegram.send_message(chat_id, Messages.ADMIN_AUTH_SUCCESS)
            logger.info(f"Admin authenticated: chat_id={chat_id}")

            # Execute pending command if exists
            if pending_command:
                logger.info(f"Executing pending admin command: {pending_command}")
                self.route_command(chat_id, pending_command)

            return True

        failures = self.storage.register_admin_auth_failure(chat_id)
        remaining = settings.ADMIN_MAX_AUTH_FAILURES - failures
        logger.warning(
            f"Admin authentication failed: chat_id={chat_id}, "
            f"failures={failures}/{settings.ADMIN_MAX_AUTH_FAILURES}"
        )

        if remaining <= 0:
            self._send_lockout_message(chat_id)
        else:
            self.telegram.send_message(
                chat_id,
                Messages.ADMIN_AUTH_FAILED_REMAINING.format(
                    remaining=remaining,
                    lockout_minutes=max(1, settings.ADMIN_LOCKOUT_SECONDS // 60),
                ),
            )

        return False
