"""Base storage interface for application state management."""

from abc import ABC, abstractmethod

from korail_bot.models import (
    AccessRequest,
    ApprovedUser,
    DeadSearch,
    MultiReservationStatus,
    OnboardedAccount,
    PaymentStatus,
    RunningReservation,
    ScheduledSearch,
    UserSession,
)


class StorageInterface(ABC):
    """Abstract interface for application state storage."""

    # User Session Management
    @abstractmethod
    def get_user_session(self, chat_id: int) -> UserSession | None:
        """Get user session by chat ID."""
        pass

    @abstractmethod
    def save_user_session(self, session: UserSession) -> None:
        """Save or update user session."""
        pass

    @abstractmethod
    def delete_user_session(self, chat_id: int) -> None:
        """Delete user session."""
        pass

    @abstractmethod
    def get_all_user_sessions(self) -> list[UserSession]:
        """Get all user sessions."""
        pass

    # Running Reservation Management
    @abstractmethod
    def get_running_reservation(self, chat_id: int) -> RunningReservation | None:
        """Get running reservation by chat ID."""
        pass

    @abstractmethod
    def save_running_reservation(self, reservation: RunningReservation) -> None:
        """Save running reservation."""
        pass

    @abstractmethod
    def delete_running_reservation(self, chat_id: int) -> None:
        """Delete running reservation."""
        pass

    @abstractmethod
    def get_all_running_reservations(self) -> list[RunningReservation]:
        """Get all running reservations."""
        pass

    # Scheduled Search Management
    @abstractmethod
    def get_scheduled_search(self, chat_id: int) -> "ScheduledSearch | None":
        """Get the search waiting to start for a chat ID."""
        pass

    @abstractmethod
    def save_scheduled_search(self, search: "ScheduledSearch") -> None:
        """Save a search to be started later."""
        pass

    @abstractmethod
    def delete_scheduled_search(self, chat_id: int) -> None:
        """Forget a search that was waiting to start."""
        pass

    @abstractmethod
    def get_all_scheduled_searches(self) -> "list[ScheduledSearch]":
        """Every search waiting to start."""
        pass

    # Dead Search Management
    @abstractmethod
    def get_dead_search(self, chat_id: int) -> "DeadSearch | None":
        """Get the stopped search a chat has yet to deal with."""
        pass

    @abstractmethod
    def save_dead_search(self, search: "DeadSearch") -> None:
        """Keep a stopped search so the user can resume or discard it."""
        pass

    @abstractmethod
    def delete_dead_search(self, chat_id: int) -> None:
        """Forget a stopped search."""
        pass

    # Onboarded Account Management
    @abstractmethod
    def save_onboarded_account(self, account: "OnboardedAccount") -> None:
        """Store the Korail account a chat registered."""
        pass

    @abstractmethod
    def get_onboarded_account(self, chat_id: int) -> "OnboardedAccount | None":
        """Get the account a chat registered."""
        pass

    @abstractmethod
    def delete_onboarded_account(self, chat_id: int) -> None:
        """Forget a registered account."""
        pass

    # Trials, requests and approvals
    @abstractmethod
    def get_trial_count(self, phone_hash: str) -> int:
        """How many trial searches this number has used."""
        pass

    @abstractmethod
    def increment_trial_count(self, phone_hash: str) -> int:
        """Record one used trial search and return the new total."""
        pass

    @abstractmethod
    def save_access_request(self, request: "AccessRequest") -> None:
        """Record someone asking to keep using the bot."""
        pass

    @abstractmethod
    def get_access_request(self, phone_hash: str) -> "AccessRequest | None":
        """Get one pending request."""
        pass

    @abstractmethod
    def delete_access_request(self, phone_hash: str) -> None:
        """Forget a request."""
        pass

    @abstractmethod
    def get_all_access_requests(self) -> "list[AccessRequest]":
        """Every request still waiting on an answer."""
        pass

    @abstractmethod
    def save_approved_user(self, user: "ApprovedUser") -> None:
        """Record an approval."""
        pass

    @abstractmethod
    def is_approved(self, phone_hash: str) -> bool:
        """Whether this number has been approved."""
        pass

    @abstractmethod
    def delete_approved_user(self, phone_hash: str) -> None:
        """Withdraw an approval."""
        pass

    @abstractmethod
    def get_all_approved_users(self) -> "list[ApprovedUser]":
        """Everyone approved from the chat."""
        pass

    # Developer chats
    @abstractmethod
    def is_developer(self, chat_id: int) -> bool:
        """Whether this chat is in developer mode."""
        pass

    @abstractmethod
    def set_developer(self, chat_id: int, enabled: bool = True) -> None:
        """Turn developer mode on or off for a chat."""
        pass

    @abstractmethod
    def get_all_developers(self) -> list[int]:
        """Every chat in developer mode."""
        pass

    # Resume Credentials Management
    @abstractmethod
    def save_resume_credentials(self, chat_id: int, username: str, password: str) -> None:
        """Store the credentials needed to restart an interrupted search."""
        pass

    @abstractmethod
    def get_resume_credentials(self, chat_id: int) -> tuple | None:
        """Get (username, password) of an interrupted search, or None."""
        pass

    @abstractmethod
    def delete_resume_credentials(self, chat_id: int) -> None:
        """Forget the credentials of a search that is over."""
        pass

    # Korail Client Identity
    @abstractmethod
    def get_or_create_app_session_start(self, chat_id: int) -> str:
        """When this user's Korail app session began, in epoch milliseconds."""
        pass

    @abstractmethod
    def delete_app_session_start(self, chat_id: int) -> None:
        """Forget an app session, so the next search starts a new one."""
        pass

    # Payment Status Management
    @abstractmethod
    def get_payment_status(self, chat_id: int) -> PaymentStatus | None:
        """Get payment status by chat ID."""
        pass

    @abstractmethod
    def save_payment_status(self, status: PaymentStatus) -> None:
        """Save payment status."""
        pass

    @abstractmethod
    def delete_payment_status(self, chat_id: int) -> None:
        """Delete payment status."""
        pass

    # Subscriber Management
    @abstractmethod
    def add_subscriber(self, chat_id: int) -> None:
        """Add a subscriber for notifications."""
        pass

    @abstractmethod
    def remove_subscriber(self, chat_id: int) -> None:
        """Remove a subscriber."""
        pass

    @abstractmethod
    def get_all_subscribers(self) -> list[int]:
        """Get all subscriber chat IDs."""
        pass

    @abstractmethod
    def is_subscriber(self, chat_id: int) -> bool:
        """Check if chat ID is a subscriber."""
        pass

    # Admin Session Management
    @abstractmethod
    def is_admin_authenticated(self, chat_id: int) -> bool:
        """Check if chat ID is authenticated as admin."""
        pass

    @abstractmethod
    def set_admin_authenticated(self, chat_id: int, authenticated: bool = True) -> None:
        """Set admin authentication status for chat ID."""
        pass

    @abstractmethod
    def is_waiting_for_admin_password(self, chat_id: int) -> bool:
        """Check if user is waiting to enter admin password."""
        pass

    @abstractmethod
    def set_waiting_for_admin_password(self, chat_id: int, waiting: bool = True) -> None:
        """Set whether user is waiting to enter admin password."""
        pass

    @abstractmethod
    def register_admin_auth_failure(self, chat_id: int) -> int:
        """Record a failed admin password attempt and return the failure count."""
        pass

    @abstractmethod
    def get_admin_auth_failures(self, chat_id: int) -> int:
        """Get the number of recent failed admin password attempts."""
        pass

    @abstractmethod
    def get_admin_lockout_remaining(self, chat_id: int) -> int:
        """Get seconds remaining before failed attempts are forgotten."""
        pass

    @abstractmethod
    def clear_admin_auth_failures(self, chat_id: int) -> None:
        """Reset the failed admin password attempt counter."""
        pass

    @abstractmethod
    def get_pending_admin_command(self, chat_id: int) -> str | None:
        """Get pending admin command waiting for authentication."""
        pass

    @abstractmethod
    def set_pending_admin_command(self, chat_id: int, command: str | None) -> None:
        """Set pending admin command waiting for authentication."""
        pass

    # Multi-Reservation Status Management
    @abstractmethod
    def get_multi_reservation_status(self, chat_id: int) -> MultiReservationStatus | None:
        """Get multi-reservation status by chat ID."""
        pass

    @abstractmethod
    def save_multi_reservation_status(self, status: MultiReservationStatus) -> None:
        """Save multi-reservation status."""
        pass

    @abstractmethod
    def delete_multi_reservation_status(self, chat_id: int) -> None:
        """Delete multi-reservation status."""
        pass

    @abstractmethod
    def get_all_multi_reservation_statuses(self) -> list[MultiReservationStatus]:
        """Get all multi-reservation statuses."""
        pass

    # Partial Reservation Management (random seat allocation)
    #
    # Random seating books one seat at a time, so the seats already secured,
    # the seat currently being paid for, and the payment handshake between the
    # bot and its search process all have to outlive a single request.
    @abstractmethod
    def save_partial_reservation(
        self, chat_id: int, seat_index: int, reservation_data: dict
    ) -> None:
        """Record a seat that has been reserved."""
        pass

    @abstractmethod
    def get_partial_reservations(self, chat_id: int) -> list[dict]:
        """Get the seats reserved so far."""
        pass

    @abstractmethod
    def delete_partial_reservations(self, chat_id: int) -> None:
        """Forget the seats reserved so far."""
        pass

    @abstractmethod
    def get_current_seat_index(self, chat_id: int) -> int | None:
        """Get the seat currently awaiting payment, or None."""
        pass

    @abstractmethod
    def set_current_seat_index(self, chat_id: int, index: int | None) -> None:
        """Set the seat currently awaiting payment."""
        pass

    @abstractmethod
    def is_payment_ready(self, chat_id: int, seat_index: int) -> bool:
        """Check whether the user confirmed payment for a seat."""
        pass

    @abstractmethod
    def mark_payment_ready(self, chat_id: int, seat_index: int) -> None:
        """Record that the user confirmed payment for a seat."""
        pass

    @abstractmethod
    def wait_for_payment(self, chat_id: int, seat_index: int, timeout: int = 600) -> bool:
        """Block until payment for a seat is confirmed or the timeout passes."""
        pass

    # Debug Mode Management
    @abstractmethod
    def is_debug_mode(self) -> bool:
        """Check if global debug mode is enabled."""
        pass

    @abstractmethod
    def set_debug_mode(self, enabled: bool) -> None:
        """Enable or disable global debug mode."""
        pass
