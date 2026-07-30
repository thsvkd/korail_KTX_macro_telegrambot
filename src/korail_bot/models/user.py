"""User data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from korail_bot.models.reservation import TrainSearchParams


@dataclass
class UserCredentials:
    """Korail login credentials."""

    korail_id: str  # Phone number format: 010-xxxx-xxxx
    korail_pw: str


@dataclass
class OnboardedAccount:
    """
    The Korail account a chat registered once and reuses from then on.

    Held apart from the session because the session is reset at the end of
    every booking, and a registration that disappeared with the booking it was
    made for would not be a registration at all. Encrypted at rest, given an
    expiry, and deleted the moment the user logs out or blocks the bot.
    """

    chat_id: int
    korail_id: str
    korail_pw: str
    onboarded_at: datetime = field(default_factory=lambda: datetime.now())

    def as_credentials(self) -> UserCredentials:
        """The same account in the shape the conversation flow expects."""
        return UserCredentials(korail_id=self.korail_id, korail_pw=self.korail_pw)


@dataclass
class UserSession:
    """User session data for conversation flow."""

    chat_id: int
    in_progress: bool = False
    last_action: int = 0  # Progress state (0-12)
    credentials: UserCredentials | None = None
    train_info: dict = field(default_factory=dict)
    process_id: int = 9999999  # PID of background reservation process
    search_params: Optional["TrainSearchParams"] = None  # Search parameters for train reservation

    def reset(self) -> None:
        """
        Reset user session to initial state.

        Credentials are dropped as well: a finished or cancelled flow has no
        further use for the user's Korail password, and keeping it around only
        widens the window in which it can leak.
        """
        self.in_progress = False
        self.last_action = 0
        self.train_info = {}
        self.process_id = 9999999
        self.search_params = None
        if self.credentials:
            self.credentials.korail_pw = ""


@dataclass
class UserProgress:
    """Represents user's progress in the reservation flow."""

    # Progress state constants
    INIT = 0
    STARTED = 1
    START_ACCEPTED = 2
    ID_INPUT_SUCCESS = 3
    PW_INPUT_SUCCESS = 4
    DATE_INPUT_SUCCESS = 5
    SRC_LOCATE_INPUT_SUCCESS = 6
    DST_LOCATE_INPUT_SUCCESS = 7
    DEP_TIME_INPUT_SUCCESS = 8
    MAX_DEP_TIME_INPUT_SUCCESS = 9
    TRAIN_TYPE_INPUT_SUCCESS = 10
    SPECIAL_INPUT_SUCCESS = 11
    PASSENGER_COUNT_INPUT_SUCCESS = 12  # New: passenger count selection
    SEAT_STRATEGY_INPUT_SUCCESS = 13  # New: seat allocation strategy
    FINDING_TICKET = 14  # Updated from 12 to 14
    # Which trains to watch, chosen from the list for the time window.
    #
    # Numbered after FINDING_TICKET rather than inserted at 14, even though it
    # comes before it in the conversation: these numbers are stored in Redis,
    # and renumbering would move every session that outlived a deploy to a
    # different question than the one it was answering.
    TRAIN_SELECT_INPUT_SUCCESS = 15
    # Waiting for the time at which the search should begin. Only reached by
    # asking for it from the summary - the default is still to start now.
    SCHEDULE_INPUT_PENDING = 16
    # Waiting on whether to replace an account that is already registered.
    # Registering again is rare and destructive - it throws away a working
    # login - so it is confirmed rather than done on the way past.
    ONBOARDING_OVERWRITE_PENDING = 17
