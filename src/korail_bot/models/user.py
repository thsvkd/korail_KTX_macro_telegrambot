"""User data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from korail_bot.models.operator import Operator

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
    A railway account a chat registered once and reuses from then on.

    Held apart from the session because the session is reset at the end of
    every booking, and a registration that disappeared with the booking it was
    made for would not be a registration at all. Encrypted at rest, given an
    expiry, and deleted the moment the user logs out or blocks the bot.

    One per railway: Korail and SR are separate companies with separate
    logins, and a chat may well have registered with both. The fields are
    still called korail_id and korail_pw because that is what every caller
    and every stored record already says; renaming them is a change worth
    making on its own rather than in passing.
    """

    chat_id: int
    korail_id: str
    korail_pw: str
    operator: str = Operator.KORAIL
    onboarded_at: datetime = field(default_factory=lambda: datetime.now())

    @property
    def rail_operator(self) -> Operator:
        """The railway this account is with, however it was stored."""
        return Operator.parse(self.operator)

    def as_credentials(self) -> UserCredentials:
        """The same account in the shape the conversation flow expects."""
        return UserCredentials(korail_id=self.korail_id, korail_pw=self.korail_pw)


@dataclass
class AccessRequest:
    """
    Someone asking to keep using the bot after their trial ran out.

    Keyed by a hash of the Korail number rather than the chat, for the same
    reason the trial count is: a new Telegram account costs nothing, so a
    per-chat request would be a queue anyone could jump by starting over.
    The masked number is carried along so the operator can tell who this is
    without the real one being stored.
    """

    phone_hash: str
    chat_id: int
    masked_phone: str
    requested_at: datetime = field(default_factory=lambda: datetime.now())


@dataclass
class ApprovedUser:
    """A number the operator has allowed to use the bot without limit."""

    phone_hash: str
    masked_phone: str
    approved_at: datetime = field(default_factory=lambda: datetime.now())
    # Which chat approved it, for the audit trail. 0 when the approval came
    # from PREAPPROVED_USERS rather than from someone pressing a button.
    approved_by: int = 0


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
    # Waiting on which railway to search. Asked first, before the login,
    # because Korail and SR are separate companies with separate accounts -
    # the answer decides which one the chat is about to log in to.
    #
    # Numbered last for the reason the two above it are: these numbers are
    # stored in Redis, and renumbering would move every session that outlived
    # a deploy to a different question than the one it was answering.
    OPERATOR_INPUT_PENDING = 18
