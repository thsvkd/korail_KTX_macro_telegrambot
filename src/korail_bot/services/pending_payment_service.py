"""
Reservations that are booked but not yet paid for.

The bot's job ends with a seat held and a user told to pay for it, and until
now that was also the end of what the bot could say about it: /status talked
only about searches, so someone who had already caught a seat was told there
was nothing going on. And the one thing they might want to do about it -
change their mind and give the seat back - had no answer at all beyond doing
it on the railway's own site.

Both halves live here. Reading the pending reservations means putting two
records into one shape: a single booking is a PaymentStatus, a random-seating
run is a MultiReservationStatus with a row per seat. Cancelling one means
logging in again, because the process that held the seat is by then watching
it and the main app threw its credentials away.
"""

from dataclasses import dataclass
from datetime import datetime

from korail_bot.config.settings import settings
from korail_bot.models import (
    MultiReservationStatus,
    Operator,
    PaymentStatus,
    ReservationPaymentStatus,
)
from korail_bot.services.telegram_service import TelegramService
from korail_bot.storage.base import StorageInterface
from korail_bot.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PendingReservation:
    """One seat held and waiting to be paid for."""

    reservation_id: str | None
    train_info: str
    expires_at: datetime | None
    # Which of several seats this is, for a random-seating run. None when the
    # booking is a single reservation and there is nothing to number.
    seat_number: int | None = None

    def describe(self) -> str:
        """One line for /status, as short as it can be and still be useful."""
        seat = f"좌석 {self.seat_number}: " if self.seat_number is not None else ""
        train = self.train_info or "예약 정보 없음"
        deadline = f"\n   ⏳ 결제 기한: {self.expires_at:%H:%M}" if self.expires_at else ""
        number = f"\n   🎫 예약번호: {self.reservation_id}" if self.reservation_id else ""
        return f"{seat}{train}{number}{deadline}"


class PendingPaymentService:
    """Reads, describes and gives back reservations awaiting payment."""

    def __init__(self, storage: StorageInterface, telegram_service: TelegramService):
        """
        Initialize the service.

        Args:
            storage: Storage interface
            telegram_service: Telegram messaging service
        """
        self.storage = storage
        self.telegram = telegram_service

    # ==================== Reading ====================

    def pending(self, chat_id: int) -> list[PendingReservation]:
        """
        Every seat this chat is holding unpaid, in the order they were taken.

        Args:
            chat_id: Telegram chat ID

        Returns:
            The pending reservations, empty when there are none
        """
        multi = self._multi_status(chat_id)
        if multi:
            return [
                PendingReservation(
                    reservation_id=r.reservation_id,
                    train_info=r.train_info,
                    expires_at=r.expires_at,
                    seat_number=r.seat_number,
                )
                for r in sorted(multi.reservations, key=lambda r: r.seat_number)
                if r.status == ReservationPaymentStatus.PENDING and not r.is_expired()
            ]

        single = self._single_status(chat_id)
        if single:
            return [
                PendingReservation(
                    reservation_id=single.reservation_id,
                    train_info=single.train_info,
                    expires_at=single.expires_at,
                )
            ]

        return []

    def describe(self, chat_id: int) -> str | None:
        """
        The block /status adds when there is a seat waiting to be paid for.

        Returns:
            The text, or None when there is nothing pending
        """
        from korail_bot.telegramBot.messages import Messages

        pending = self.pending(chat_id)
        if not pending:
            return None

        return Messages.PAYMENT_PENDING_STATUS.format(
            lines="\n".join(item.describe() for item in pending),
            paymentUrl=self._payment_url(self.operator(chat_id)),
        )

    def operator(self, chat_id: int) -> Operator:
        """Which railway is holding this chat's unpaid seats."""
        multi = self._multi_status(chat_id)
        if multi:
            return multi.rail_operator

        single = self._single_status(chat_id)
        if single:
            return single.rail_operator

        return Operator.KORAIL

    # ==================== Giving the seat back ====================

    def confirm_cancellation(self, chat_id: int) -> None:
        """Ask whether the seat should really go back, and offer the button."""
        from korail_bot.telegramBot import keyboards
        from korail_bot.telegramBot.messages import Messages

        pending = self.pending(chat_id)
        if not pending:
            self.telegram.send_message(chat_id, Messages.PAYMENT_CANCEL_NOTHING)
            return

        if self._still_booking(chat_id):
            self.telegram.send_message(chat_id, Messages.PAYMENT_CANCEL_MID_RUN)
            return

        self.telegram.send_message(
            chat_id,
            Messages.PAYMENT_CANCEL_CONFIRM.format(
                lines="\n".join(item.describe() for item in pending)
            ),
            reply_markup=keyboards.payment_cancel_keyboard(),
        )

    def cancel(self, chat_id: int) -> bool:
        """
        Give every unpaid seat this chat holds back to the railway, and say so.

        Nothing is recorded as cancelled that the railway did not confirm: a
        seat the bot failed to release is still booked, and telling someone
        otherwise would leave them with a reservation nobody is watching and a
        bill they think they do not owe.

        Args:
            chat_id: Telegram chat ID

        Returns:
            True when at least one reservation was given back
        """
        from korail_bot.telegramBot.messages import Messages

        pending = self.pending(chat_id)
        if not pending:
            self.telegram.send_message(chat_id, Messages.PAYMENT_CANCEL_NOTHING)
            return False

        if self._still_booking(chat_id):
            self.telegram.send_message(chat_id, Messages.PAYMENT_CANCEL_MID_RUN)
            return False

        operator = self.operator(chat_id)
        rail, refusal = self._sign_in(chat_id, operator)
        if rail is None:
            self._report_failure(chat_id, operator, refusal)
            return False

        cancelled, failed = [], []
        for item in pending:
            if item.reservation_id and rail.cancel_reservation(item.reservation_id):
                cancelled.append(item)
            else:
                failed.append(item)

        self._record_cancelled(chat_id, cancelled)

        if not cancelled:
            self._report_failure(chat_id, operator, Messages.PAYMENT_CANCEL_REFUSED)
            return False

        logger.info(f"Cancelled {len(cancelled)} pending reservation(s) for chat_id={chat_id}")
        self.telegram.send_message(
            chat_id,
            Messages.PAYMENT_CANCEL_DONE.format(
                lines="\n".join(item.describe() for item in cancelled)
            ),
        )

        if failed:
            # Partly done is the one outcome that must not read as done: some
            # of these seats are still booked in the user's name.
            self._report_failure(chat_id, operator, Messages.PAYMENT_CANCEL_REFUSED)

        return True

    # ==================== Internals ====================

    def _single_status(self, chat_id: int) -> PaymentStatus | None:
        """The single-booking record, if it is still waiting on a payment."""
        status = self.storage.get_payment_status(chat_id)
        if status and status.is_awaiting_payment():
            return status
        return None

    def _still_booking(self, chat_id: int) -> bool:
        """
        Whether a random-seating run is still taking seats.

        That run books one seat at a time and waits for each to be paid for
        before taking the next, so a seat given back here would be followed by
        the search taking another - and the user would be told their booking
        was cancelled while new reservations appeared in their name. Stopping
        the search is what /cancel is for, and it has to come first.
        """
        return self.storage.get_current_seat_index(chat_id) is not None

    def _multi_status(self, chat_id: int) -> MultiReservationStatus | None:
        """The random-seating record, if any of its seats are still unpaid."""
        status = self.storage.get_multi_reservation_status(chat_id)
        if status and status.get_pending_count() > 0:
            return status
        return None

    def _sign_in(self, chat_id: int, operator: Operator):
        """
        A logged-in client for the railway holding the seats.

        The search process that took them is by now only watching them, and
        the main app deleted the credentials it started that process with - so
        this logs in with the account the chat registered, which is the one
        the reservation was made under.

        Returns:
            (client, None) when logged in, or (None, reason) with the line to
            tell the user why not - "nothing to log in with" and "the railway
            said no" are different problems with different remedies.
        """
        from korail_bot.telegramBot.messages import Messages

        username, password = self._credentials(chat_id, operator)
        if not username or not password:
            logger.info(f"No {operator} account to cancel with for chat_id={chat_id}")
            return None, Messages.PAYMENT_CANCEL_NO_ACCOUNT

        rail = self._rail_service(operator)
        if not rail.login(username, password):
            logger.warning(f"Could not log in to {operator} to cancel for chat_id={chat_id}")
            return None, Messages.PAYMENT_CANCEL_LOGIN_FAILED

        return rail, None

    def _credentials(self, chat_id: int, operator: Operator) -> tuple[str | None, str | None]:
        """The login to cancel with: the registered account, or the fixed one."""
        account = self.storage.get_onboarded_account(chat_id, operator)
        if account:
            return account.korail_id, account.korail_pw

        # Only a developer chat books with the fixed account, so only a
        # developer chat can have a reservation to cancel with it.
        if self.storage.is_developer(chat_id) and settings.has_preconfigured_credentials(operator):
            return settings.preconfigured_credentials(operator)

        return None, None

    @staticmethod
    def _rail_service(operator: Operator):
        """A client for one railway. Nothing is carried over between calls."""
        from korail_bot.services.korail_service import KorailService
        from korail_bot.services.srt_service import SrtService

        return SrtService() if operator is Operator.SRT else KorailService()

    def _record_cancelled(self, chat_id: int, cancelled: list[PendingReservation]) -> None:
        """
        Write down that these seats went back.

        `completed` is set along with `cancelled` on the single record. It is
        what the reminder loop reads as "stop asking", and it is also what
        keeps the search process quiet: that process is watching the
        reservation and will see it disappear, which without this it would
        report as a payment going through.
        """
        if not cancelled:
            return

        numbers = {item.reservation_id for item in cancelled if item.reservation_id}

        multi = self.storage.get_multi_reservation_status(chat_id)
        if multi:
            for reservation in multi.reservations:
                if reservation.reservation_id in numbers:
                    reservation.status = ReservationPaymentStatus.CANCELLED
            multi.manually_stopped = True
            self.storage.save_multi_reservation_status(multi)

        status = self.storage.get_payment_status(chat_id)
        if status and status.reservation_id in numbers:
            status.cancelled = True
            status.completed = True
            status.reminder_active = False
            self.storage.save_payment_status(status)

    def _report_failure(self, chat_id: int, operator: Operator, reason: str) -> None:
        """Say what went wrong, and where the user can finish the job themselves."""
        from korail_bot.telegramBot.messages import Messages

        self.telegram.send_message(
            chat_id,
            Messages.PAYMENT_CANCEL_FAILED.format(
                reason=reason, paymentUrl=self._payment_url(operator)
            ),
        )

    @staticmethod
    def _payment_url(operator: Operator) -> str:
        """Where this railway takes payments."""
        if operator is Operator.SRT:
            return settings.SRT_PAYMENT_URL
        return settings.KORAIL_PAYMENT_URL
