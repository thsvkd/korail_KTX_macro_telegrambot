"""Reservation orchestration service."""
import json
import subprocess
import signal
import os
from typing import Optional
from korail2 import TrainType, ReserveOption

from config.settings import settings
from models import TrainSearchParams, RunningReservation, UserSession
from storage.base import StorageInterface
from services.korail_service import KorailService
from services.telegram_service import TelegramService, MessageTemplates
from utils.logger import get_logger
from utils.privacy import mask_phone, mask_phones

logger = get_logger(__name__)


class ReservationService:
    """
    Service for managing train reservations.

    Orchestrates the reservation process including:
    - Starting background reservation processes
    - Managing running reservations
    - Cancelling reservations
    """

    def __init__(
        self,
        storage: StorageInterface,
        telegram_service: TelegramService
    ):
        """
        Initialize reservation service.

        Args:
            storage: Storage interface for state management
            telegram_service: Telegram service for notifications
        """
        self.storage = storage
        self.telegram = telegram_service

    def start_reservation_process(
        self,
        chat_id: int,
        username: str,
        password: str,
        search_params: TrainSearchParams,
        resumed: bool = False
    ) -> bool:
        """
        Start a background reservation process.

        Args:
            chat_id: Telegram chat ID
            username: Korail username
            password: Korail password
            search_params: Train search parameters
            resumed: True when picking up a search a restart interrupted, which
                     only changes what the user and subscribers are told

        Returns:
            True if process started successfully
        """
        try:
            # Credentials are deliberately absent from argv: anything passed on
            # a command line is world-readable through `ps` and /proc. They are
            # written to the child's stdin instead.
            arguments = [
                search_params.dep_date,
                search_params.src_locate,
                search_params.dst_locate,
                search_params.dep_time,
                search_params.train_type,
                search_params.special_option,
                str(chat_id),
                search_params.max_dep_time,
                str(search_params.passenger_count),
                search_params.seat_strategy
            ]

            # Start background process
            cmd = ['python', '-m', 'telegramBot.telebotBackProcess'] + arguments
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

            # Hand over credentials and close the pipe so the child stops waiting.
            credentials = json.dumps({"username": username, "password": password})
            try:
                proc.stdin.write(credentials.encode('utf-8') + b"\n")
                proc.stdin.flush()
            finally:
                proc.stdin.close()

            logger.info(
                f"Started reservation process for chat_id={chat_id}, pid={proc.pid}"
            )

            # Save running reservation, stamped with this run so that a later
            # start can tell whether the process is still around.
            reservation = RunningReservation(
                chat_id=chat_id,
                process_id=proc.pid,
                korail_id=username,
                search_params=search_params,
                run_id=settings.RUN_ID
            )
            self.storage.save_running_reservation(reservation)

            # Keep what a restart would need to log in again. Deleted as soon
            # as the search ends, whichever way it ends.
            if settings.RESUME_ON_RESTART:
                self.storage.save_resume_credentials(chat_id, username, password)

            # Update user session
            session = self.storage.get_user_session(chat_id)
            if session:
                session.process_id = proc.pid
                self.storage.save_user_session(session)

            if resumed:
                self.telegram.send_message(
                    chat_id,
                    MessageTemplates.RESERVATION_RESUMED.format(
                        srcLocate=search_params.src_locate,
                        dstLocate=search_params.dst_locate,
                        depDate=search_params.dep_date
                    )
                )
            else:
                # Notify subscribers
                self._notify_subscribers_start(username, search_params)

                # Send confirmation to user
                self.telegram.send_message(
                    chat_id, MessageTemplates.reservation_started()
                )

            return True

        except Exception as e:
            logger.error(f"Failed to start reservation process: {e}")
            return False

    # The placeholder PID used for reservations that never had a process.
    _NO_PROCESS = 9999999

    def _owns_process(self, pid: int) -> bool:
        """
        Check that a PID really belongs to one of our search processes.

        PIDs get recycled. A record left behind by an earlier run can point at
        a PID the kernel has since handed to something else entirely, and
        signalling that would kill an innocent process.

        Returns:
            True when the PID may be signalled
        """
        if not os.path.isdir("/proc"):
            # No way to verify here; keep the previous behaviour rather than
            # silently refusing to cancel anything.
            return True

        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                return b"telebotBackProcess" in handle.read()
        except (FileNotFoundError, ProcessLookupError):
            return False
        except OSError as e:
            logger.warning(f"Could not inspect process {pid}: {e}")
            return False

    def _terminate_search_process(self, pid: int) -> bool:
        """
        Stop a search process, if it is still ours to stop.

        Args:
            pid: Process ID recorded for the reservation

        Returns:
            True if a signal was delivered
        """
        if pid == self._NO_PROCESS:
            return False

        if not self._owns_process(pid):
            logger.info(
                f"Process {pid} is gone or no longer one of ours - not signalling it"
            )
            return False

        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(f"Killed process {pid}")
            return True
        except ProcessLookupError:
            logger.warning(f"Process {pid} not found")
        except OSError as e:
            logger.error(f"Failed to signal process {pid}: {e}")

        return False

    def reconcile_after_restart(self) -> dict:
        """
        Deal with searches left behind by a previous run of the application.

        The search runs in a child process, so restarting the app abandons it
        while its record stays in Redis - /status would report a search that
        nothing is performing. Every record from an earlier run is either
        resumed or cleaned up and reported.

        Returns:
            Counts keyed 'resumed', 'interrupted' and 'failed'
        """
        from telegramBot.messages import Messages

        summary = {'resumed': 0, 'interrupted': 0, 'failed': 0}

        try:
            reservations = self.storage.get_all_running_reservations()
        except Exception as e:
            logger.error(f"Could not read running reservations: {e}", exc_info=True)
            return summary

        stale = [r for r in reservations if r.is_stale(settings.RUN_ID)]
        if not stale:
            return summary

        logger.info(f"Found {len(stale)} reservation(s) left over from an earlier run")

        for reservation in stale:
            chat_id = reservation.chat_id
            try:
                # A previous run may have left the process alive - a bare
                # restart of the app does not kill its children.
                self._terminate_search_process(reservation.process_id)

                if self._resume(reservation):
                    summary['resumed'] += 1
                    continue

                self._abandon(reservation, Messages)
                summary['interrupted'] += 1
            except Exception as e:
                logger.error(
                    f"Failed to reconcile reservation for chat_id={chat_id}: {e}",
                    exc_info=True
                )
                summary['failed'] += 1

        logger.info(
            f"Restart recovery: {summary['resumed']} resumed, "
            f"{summary['interrupted']} interrupted, {summary['failed']} failed"
        )
        return summary

    def _resume(self, reservation: RunningReservation) -> bool:
        """
        Try to pick an interrupted search back up.

        Args:
            reservation: Record left behind by an earlier run

        Returns:
            True when a new search process was started
        """
        chat_id = reservation.chat_id

        if not settings.RESUME_ON_RESTART:
            return False

        # Random seating reserves one seat at a time. Restarting that search
        # from the beginning would try to book seats the user already holds,
        # so it is left to the user to decide.
        if self.storage.get_partial_reservations(chat_id):
            logger.info(
                f"Not resuming chat_id={chat_id}: seats are already reserved"
            )
            return False

        credentials = self.storage.get_resume_credentials(chat_id)
        if not credentials:
            logger.info(f"Not resuming chat_id={chat_id}: no usable credentials")
            return False

        username, password = credentials
        logger.info(f"Resuming interrupted search for chat_id={chat_id}")

        return self.start_reservation_process(
            chat_id=chat_id,
            username=username,
            password=password,
            search_params=reservation.search_params,
            resumed=True
        )

    def _abandon(self, reservation: RunningReservation, messages) -> None:
        """Tell the user their search is over and drop every trace of it."""
        chat_id = reservation.chat_id
        params = reservation.search_params

        if self.storage.get_partial_reservations(chat_id):
            text = messages.RESERVATION_INTERRUPTED_PARTIAL.format(
                paymentUrl=settings.KORAIL_PAYMENT_URL
            )
        else:
            text = messages.RESERVATION_INTERRUPTED.format(
                srcLocate=params.src_locate,
                dstLocate=params.dst_locate,
                depDate=params.dep_date
            )

        self.telegram.send_message(chat_id, text)

        self.storage.delete_running_reservation(chat_id)
        self.storage.delete_resume_credentials(chat_id)

        session = self.storage.get_user_session(chat_id)
        if session:
            session.reset()
            self.storage.save_user_session(session)

    def cancel_reservation(self, chat_id: int) -> bool:
        """
        Cancel a running reservation.

        Args:
            chat_id: Telegram chat ID

        Returns:
            True if cancelled successfully
        """
        try:
            # Get running reservation
            reservation = self.storage.get_running_reservation(chat_id)
            if not reservation:
                logger.warning(f"No running reservation found for chat_id={chat_id}")
                return False

            self._terminate_search_process(reservation.process_id)

            # Clean up storage
            self.storage.delete_running_reservation(chat_id)
            self.storage.delete_resume_credentials(chat_id)

            # Reset user session
            session = self.storage.get_user_session(chat_id)
            if session:
                session.reset()
                self.storage.save_user_session(session)

            # Notify
            self._notify_subscribers_end(reservation.korail_id)
            self.telegram.send_message(chat_id, MessageTemplates.reservation_cancelled())

            return True

        except Exception as e:
            logger.error(f"Error cancelling reservation: {e}")
            return False

    def cancel_all_reservations(self, admin_chat_id: int) -> int:
        """
        Cancel all running reservations (admin function).

        Args:
            admin_chat_id: Admin's chat ID for notification

        Returns:
            Number of reservations cancelled
        """
        reservations = self.storage.get_all_running_reservations()
        count = 0

        for reservation in reservations:
            try:
                self._terminate_search_process(reservation.process_id)
                self.storage.delete_resume_credentials(reservation.chat_id)

                # Notify user
                self.telegram.send_message(
                    reservation.chat_id,
                    "관리자에 의해 실행중이던 예약이 강제 종료됩니다. 꼬우면 관리자에게 연락하세요."
                )

                # Reset session
                session = self.storage.get_user_session(reservation.chat_id)
                if session:
                    session.reset()
                    self.storage.save_user_session(session)

                # Clean up
                self.storage.delete_running_reservation(reservation.chat_id)
                count += 1

            except Exception as e:
                logger.error(f"Error cancelling reservation {reservation.chat_id}: {e}")

        # Notify admin
        korail_ids = mask_phones(r.korail_id for r in reservations)
        self.telegram.send_message(
            admin_chat_id,
            f"총 {count}개의 진행중인 예약을 종료했습니다. 이용중이던 사용자 : {korail_ids}"
        )

        return count

    def get_status(self, chat_id: int) -> str:
        """
        Get reservation status for the requesting user.

        /status is open to every user, so it reports only the caller's own
        reservation plus an aggregate count. Other users' Korail IDs are
        phone numbers and are never disclosed here.

        Args:
            chat_id: Chat ID requesting status

        Returns:
            Status message
        """
        reservations = self.storage.get_all_running_reservations()
        total = len(reservations)
        mine = next((r for r in reservations if r.chat_id == chat_id), None)

        if not mine:
            return (
                "진행중인 예약이 없습니다.\n"
                f"(현재 서버 전체 실행중인 예약: {total}개)\n\n"
                "/start 를 입력하여 예약을 시작하세요."
            )

        params = mine.search_params
        return (
            "🔎 예약 검색이 진행중입니다.\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"출발일: {params.dep_date}\n"
            f"구간: {params.src_locate} → {params.dst_locate}\n"
            f"검색 시작 시각: {params.dep_time[:4]}\n"
            f"최대 출발 시각: {params.max_dep_time}\n"
            f"인원: {params.passenger_count}명\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"(현재 서버 전체 실행중인 예약: {total}개)\n\n"
            "중단하려면 /cancel 을 입력하세요."
        )

    def _notify_subscribers_start(self, username: str, params: TrainSearchParams) -> None:
        """Notify subscribers about reservation start."""
        subscribers = self.storage.get_all_subscribers()
        message = (
            f"{mask_phone(username)}의 {params.src_locate}에서 {params.dst_locate}로 "
            f"{params.dep_date}에 출발하는 열차 예약이 시작되었습니다."
        )
        self.telegram.send_to_multiple(subscribers, message)

    def _notify_subscribers_end(self, username: str) -> None:
        """Notify subscribers about reservation end."""
        subscribers = self.storage.get_all_subscribers()
        message = f"{mask_phone(username)}의 예약이 종료되었습니다."
        self.telegram.send_to_multiple(subscribers, message)
