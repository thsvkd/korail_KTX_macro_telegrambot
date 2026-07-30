"""Command handler for Telegram bot commands."""

import hmac

from korail_bot.config.settings import settings
from korail_bot.models import UserProgress, UserSession
from korail_bot.services import (
    AccessService,
    MessageTemplates,
    PaymentReminderService,
    ReservationService,
    TelegramService,
)
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot import keyboards
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
        conversation_handler=None,
    ):
        """
        Initialize command handler.

        Args:
            storage: Storage interface
            telegram_service: Telegram messaging service
            reservation_service: Reservation service
            payment_reminder_service: Payment reminder service
            conversation_handler: Used by /start to log in with an account the
                chat registered earlier, which is the conversation's job and
                is done there. Optional so that tests exercising the plain
                commands do not have to build one.
        """
        self.storage = storage
        self.telegram = telegram_service
        self.reservation = reservation_service
        self.payment_reminder = payment_reminder_service
        self.conversation = conversation_handler
        # Shares the conversation's view of who is allowed, so an approval
        # granted here is visible to the gate immediately.
        self.access = (
            conversation_handler.access if conversation_handler else AccessService(storage)
        )

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

        # An account registered earlier means the two login questions have
        # already been answered, so /start goes straight to picking a date.
        # The operator's own account (USERID/USERPW) wins over a registration,
        # because that setting exists to make the bot use one account for
        # everyone regardless of what anyone registered.
        if self.conversation and not settings.has_preconfigured_korail_credentials():
            if self.conversation.resume_with_registered_account(chat_id, session):
                return

            # Nothing registered: say what registering is before asking for a
            # Korail password. The generic welcome does not mention that the
            # login is kept, and that is the part a user should agree to.
            self.telegram.send_message(
                chat_id,
                MessageTemplates.ONBOARDING_INTRO,
                reply_markup=keyboards.onboarding_start_keyboard(),
            )
            return

        # Send welcome message
        self.telegram.send_message(
            chat_id,
            MessageTemplates.welcome_message(
                skip_login_prompts=settings.has_preconfigured_korail_credentials()
            ),
            reply_markup=keyboards.start_confirm_keyboard(),
        )

    def handle_onboarding(self, chat_id: int) -> None:
        """
        Handle /onboarding (/init): register a Korail account.

        Separate from /start because re-registering is a thing people need to
        do on purpose - a changed Korail password, a different account - and
        /start deliberately skips straight past the login once one is stored.
        """
        logger.info(f"Handling /onboarding for chat_id={chat_id}")

        if settings.has_preconfigured_korail_credentials():
            # Registering would achieve nothing: the operator's account is
            # used for everyone and a stored one would never be consulted.
            self.telegram.send_message(chat_id, MessageTemplates.ONBOARDING_NOT_NEEDED)
            return

        session = self.storage.get_user_session(chat_id)
        if not session:
            session = UserSession(chat_id=chat_id, in_progress=False, last_action=UserProgress.INIT)

        existing = self.storage.get_onboarded_account(chat_id)
        if existing:
            session.in_progress = True
            session.last_action = UserProgress.ONBOARDING_OVERWRITE_PENDING
            self.storage.save_user_session(session)
            self.telegram.send_message(
                chat_id,
                MessageTemplates.ONBOARDING_ALREADY.format(
                    korailId=mask_phone(existing.korail_id),
                    onboardedAt=f"{existing.onboarded_at:%m월 %d일 %H:%M}",
                ),
                reply_markup=keyboards.onboarding_overwrite_keyboard(),
            )
            return

        session.in_progress = True
        session.last_action = UserProgress.STARTED
        self.storage.save_user_session(session)
        self.telegram.send_message(
            chat_id,
            MessageTemplates.ONBOARDING_INTRO,
            reply_markup=keyboards.onboarding_start_keyboard(),
        )

    def handle_logout(self, chat_id: int) -> None:
        """
        Handle /logout: forget the registered Korail account.

        Only the registration is dropped. A search that is already running
        keeps its own copy of the login and is left alone - stopping someone's
        search is what /cancel is for, and doing it here would be a surprise.
        """
        logger.info(f"Handling /logout for chat_id={chat_id}")

        if not self.storage.get_onboarded_account(chat_id):
            self.telegram.send_message(chat_id, MessageTemplates.LOGOUT_NOTHING)
            return

        self.storage.delete_onboarded_account(chat_id)

        session = self.storage.get_user_session(chat_id)
        if session:
            session.credentials = None
            session.reset()
            self.storage.save_user_session(session)

        self.telegram.send_message(chat_id, MessageTemplates.LOGOUT_DONE)

    # ==================== Approving people ====================

    def may_administer(self, chat_id: int) -> bool:
        """
        Whether this chat may use the operator's tools right now.

        Two ways in: developer mode, which is deliberate and lasts, and the
        password check, which is per-session. Callbacks are re-checked through
        this rather than trusting that a keyboard was only ever sent to an
        operator - a message can be forwarded, and the button would come back
        carrying whatever chat pressed it.
        """
        return self.storage.is_developer(chat_id) or self.storage.is_admin_authenticated(chat_id)

    def handle_approve(self, chat_id: int) -> None:
        """Handle /approve: show pending access requests as buttons."""
        logger.info(f"Handling /approve for chat_id={chat_id}")

        requests = self.access.pending_requests()
        if not requests:
            self.telegram.send_message(chat_id, MessageTemplates.APPROVE_EMPTY)
            return

        self.telegram.send_message(
            chat_id,
            MessageTemplates.APPROVE_LIST.format(count=len(requests)),
            reply_markup=keyboards.approve_list_keyboard(requests),
        )

    def handle_users(self, chat_id: int) -> None:
        """Handle /users: show approved users as buttons."""
        logger.info(f"Handling /users for chat_id={chat_id}")

        users = self.access.approved_users()
        if not users:
            self.telegram.send_message(chat_id, MessageTemplates.USERS_EMPTY)
            return

        self.telegram.send_message(
            chat_id,
            MessageTemplates.USERS_LIST.format(count=len(users)),
            reply_markup=keyboards.users_list_keyboard(users),
        )

    def handle_access_callback(
        self, chat_id: int, message_id: int | None, step: str, value: str
    ) -> None:
        """
        Act on a press in the approve or users list.

        Args:
            chat_id: The operator's chat
            message_id: The message the keyboard is on, so it can be updated
                        in place rather than pushing a new one per press
            step: Which list it came from
            value: The prefixed action, carrying a phone hash where needed
        """
        if step == keyboards.STEP_APPROVE:
            self._handle_approve_callback(chat_id, message_id, value)
        else:
            self._handle_users_callback(chat_id, message_id, value)

    def _handle_approve_callback(self, chat_id: int, message_id: int | None, value: str) -> None:
        """Act on a press in the pending-requests list."""
        if value == keyboards.APPROVE_CLOSE:
            self._close_list(chat_id, message_id, "닫았습니다.")
            return

        if value == keyboards.APPROVE_BACK:
            requests = self.access.pending_requests()
            if not requests:
                self._close_list(chat_id, message_id, MessageTemplates.APPROVE_EMPTY)
                return
            self._edit_list(
                chat_id,
                message_id,
                MessageTemplates.APPROVE_LIST.format(count=len(requests)),
                keyboards.approve_list_keyboard(requests),
            )
            return

        if value.startswith(keyboards.APPROVE_PICK):
            phone_hash = value[len(keyboards.APPROVE_PICK) :]
            request = self.storage.get_access_request(phone_hash)
            if not request:
                self._close_list(chat_id, message_id, MessageTemplates.APPROVE_GONE)
                return
            self._edit_list(
                chat_id,
                message_id,
                MessageTemplates.APPROVE_CONFIRM.format(
                    maskedPhone=request.masked_phone,
                    requestedAt=f"{request.requested_at:%m월 %d일 %H:%M}",
                ),
                keyboards.approve_decision_keyboard(phone_hash),
            )
            return

        if value.startswith(keyboards.APPROVE_YES):
            phone_hash = value[len(keyboards.APPROVE_YES) :]
            request = self.access.approve(phone_hash, approved_by=chat_id)
            if not request:
                self._close_list(chat_id, message_id, MessageTemplates.APPROVE_GONE)
                return
            self._close_list(
                chat_id,
                message_id,
                MessageTemplates.APPROVE_DONE.format(maskedPhone=request.masked_phone),
            )
            # The whole point of approving is that the person finds out.
            self.telegram.send_message(request.chat_id, MessageTemplates.ACCESS_APPROVED)
            return

        if value.startswith(keyboards.APPROVE_NO):
            phone_hash = value[len(keyboards.APPROVE_NO) :]
            request = self.access.reject(phone_hash)
            if not request:
                self._close_list(chat_id, message_id, MessageTemplates.APPROVE_GONE)
                return
            self._close_list(
                chat_id,
                message_id,
                MessageTemplates.APPROVE_REJECTED.format(maskedPhone=request.masked_phone),
            )
            self.telegram.send_message(request.chat_id, MessageTemplates.ACCESS_REJECTED)
            return

        logger.warning(f"Unknown approve action {value!r} from chat_id={chat_id}")

    def _handle_users_callback(self, chat_id: int, message_id: int | None, value: str) -> None:
        """Act on a press in the approved-users list."""
        if value == keyboards.USERS_CLOSE:
            self._close_list(chat_id, message_id, "닫았습니다.")
            return

        if value == keyboards.USERS_BACK:
            users = self.access.approved_users()
            if not users:
                self._close_list(chat_id, message_id, MessageTemplates.USERS_EMPTY)
                return
            self._edit_list(
                chat_id,
                message_id,
                MessageTemplates.USERS_LIST.format(count=len(users)),
                keyboards.users_list_keyboard(users),
            )
            return

        if value.startswith(keyboards.USERS_REVOKE):
            phone_hash = value[len(keyboards.USERS_REVOKE) :]
            user = self.access.revoke(phone_hash)
            if not user:
                self._close_list(chat_id, message_id, MessageTemplates.USERS_REVOKE_GONE)
                return
            self._close_list(
                chat_id,
                message_id,
                MessageTemplates.USERS_REVOKED.format(maskedPhone=user.masked_phone),
            )
            return

        if value.startswith(keyboards.USERS_PICK):
            phone_hash = value[len(keyboards.USERS_PICK) :]
            user = next(
                (u for u in self.access.approved_users() if u.phone_hash == phone_hash), None
            )
            if not user:
                self._close_list(chat_id, message_id, MessageTemplates.USERS_REVOKE_GONE)
                return
            self._edit_list(
                chat_id,
                message_id,
                MessageTemplates.USERS_REVOKE_CONFIRM.format(maskedPhone=user.masked_phone),
                keyboards.users_revoke_keyboard(phone_hash),
            )
            return

        logger.warning(f"Unknown users action {value!r} from chat_id={chat_id}")

    def _edit_list(self, chat_id: int, message_id: int | None, text: str, markup: dict) -> None:
        """Replace a list in place, so pressing through it does not spam."""
        if message_id is None:
            self.telegram.send_message(chat_id, text, reply_markup=markup)
            return
        try:
            self.telegram.edit_message_text(chat_id, message_id, text, reply_markup=markup)
        except Exception as e:
            logger.warning(f"Could not update the list on message {message_id}: {e}")
            self.telegram.send_message(chat_id, text, reply_markup=markup)

    def _close_list(self, chat_id: int, message_id: int | None, text: str) -> None:
        """Finish with a list: leave the outcome, take the buttons away."""
        if message_id is None:
            self.telegram.send_message(chat_id, text)
            return
        try:
            self.telegram.edit_message_text(
                chat_id, message_id, text, reply_markup=keyboards.empty_keyboard()
            )
        except Exception as e:
            logger.warning(f"Could not close the list on message {message_id}: {e}")
            self.telegram.send_message(chat_id, text)

    def handle_cancel(self, chat_id: int) -> None:
        """
        Handle /cancel command.

        Args:
            chat_id: Telegram chat ID
        """
        logger.info(f"Handling /cancel for chat_id={chat_id}")

        # A search booked for later has no process to stop, only a record -
        # and it has to go before anything else, or the schedule fires later
        # and starts the very search the user just cancelled.
        scheduled_cancelled = self._cancel_scheduled_search(chat_id)

        # A search that died is waiting on the user to resume or drop it.
        # /cancel is them dropping it, and leaving the record would have
        # /status go on reporting a stopped search after they cancelled it.
        # Taken before cancel_reservation, which reports "nothing running"
        # when it finds nothing - true, and not what happened here.
        discarded = self.reservation.discard_dead_search(chat_id)

        # Cancel any running reservation. Returns False when there was none,
        # having told the user so itself - unless a dead search was just
        # dropped, which is the answer to /cancel and gets its own reply.
        cancelled = False if discarded else self.reservation.cancel_reservation(chat_id)

        # The rest runs either way: /cancel is also how a user gets out of a
        # half-finished conversation, and that state outlives the search.

        # Reset user session
        session = self.storage.get_user_session(chat_id)
        if session:
            session.reset()
            self.storage.save_user_session(session)

        # Clear multi-reservation status (for random seating)
        self.storage.delete_multi_reservation_status(chat_id)
        logger.debug(f"Cleared multi-reservation status for chat_id={chat_id}")

        # Stop payment reminders through the service that owns them, rather
        # than writing the same two fields by hand from here.
        self.payment_reminder.deactivate_reminders(chat_id, completed=True)

        # Clear admin password waiting state if any
        self.storage.set_waiting_for_admin_password(chat_id, False)

        if cancelled:
            self.telegram.send_message(chat_id, "✅ 예약이 취소되었습니다.")
        elif discarded:
            self.telegram.send_message(chat_id, "✅ 멈춰 있던 검색을 정리했습니다.")
        elif scheduled_cancelled:
            # cancel_reservation stays quiet when there was no running search,
            # which for a booked-but-not-started one would be no reply at all.
            self.telegram.send_message(chat_id, "✅ 예약해둔 검색을 취소했습니다.")

    def _cancel_scheduled_search(self, chat_id: int) -> bool:
        """
        Drop a search that was waiting for its start time.

        Never raises: /cancel is how a user gets out of anything, and it has
        several other things to clean up after this one.
        """
        try:
            from korail_bot.services import ScheduledSearchService

            scheduler = ScheduledSearchService(self.storage, self.telegram, self.reservation)
            return scheduler.cancel(chat_id)
        except Exception as e:
            logger.error(f"Failed to cancel the scheduled search for chat_id={chat_id}: {e}")
            return False

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
        elif command in ("/onboarding", "/init"):
            self.handle_onboarding(chat_id)
        elif command == "/logout":
            self.handle_logout(chat_id)
        elif command == "/approve":
            self._handle_admin_command(chat_id, self.handle_approve, "/approve")
        elif command == "/users":
            self._handle_admin_command(chat_id, self.handle_users, "/users")
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

        # Developer mode is a standing grant, so it answers before the
        # password does - an operator should not be asked to authenticate in
        # the chat they deliberately marked as theirs.
        if self.storage.is_developer(chat_id) or self.storage.is_admin_authenticated(chat_id):
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
