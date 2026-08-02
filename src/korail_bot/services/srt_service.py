"""SR (SRT) API service wrapper.

The SRT half of the bot. Same shape as :class:`KorailService` - the search
loop in :mod:`korail_bot.services.rail_service` drives both and cannot tell
them apart - but the differences underneath are real and worth naming:

- **Searching does not need a login.** SR answers the timetable request to
  anyone. The bot logs in anyway, because reserving does need it and a session
  that goes stale mid-search must be noticed, not discovered at the one moment
  a seat was there to take.
- **Sold-out trains come back from the same call.** korail2 has the server
  filter them out; SR returns everything and says of each whether it can be
  booked, so ``available_only`` is applied here.
- **The library's ``login()`` raises instead of returning False**, and one of
  the things it can raise is SR having blocked this IP for abnormal access -
  which is the search loop's own doing, and the one failure it must never
  answer by trying again.
- **``SRTDuplicateError`` is declared but never raised.** A duplicate booking
  arrives as an ordinary ``SRTResponseError`` and has to be recognised from
  its message.
"""

from typing import Any

from SRT import SRT, Adult, SeatType
from SRT.errors import SRTError, SRTLoginError, SRTNotLoggedInError, SRTResponseError
from SRT.netfunnel import NetFunnelHelper

from korail_bot.services.rail_service import RailService, SearchUnavailableError
from korail_bot.utils.logger import get_logger
from korail_bot.utils.privacy import mask_phone

logger = get_logger(__name__)

#: SR's answer when it has decided this address is a robot. Retrying is the
#: exact wrong response - it is what produced the block - so this is raised
#: past the loop rather than counted as one more failed request.
IP_BLOCKED_MARKER = "Your IP Address Blocked due to abnormal access."

#: Phrases that mean "this account already holds this booking". SR has no
#: error code for it and no dedicated exception, so the message is all there
#: is. Matched loosely, and every unmatched refusal is logged in full so the
#: list can be extended from what SR actually said.
DUPLICATE_MARKERS = (
    "이미 예약",
    "예약된 열차",
    "중복",
)

#: Phrases that mean the seat went to someone else between the search and the
#: reservation. Ordinary, expected, and the reason the loop exists.
SOLD_OUT_MARKERS = (
    "잔여석",
    "매진",
    "좌석이 없",
    "예약가능한 좌석",
)

#: Phrases that mean the session is no longer good. SR does not tell the
#: client its cookie has expired - ``is_login`` stays True and the request
#: simply fails - so an expired session has to be recognised here.
SESSION_EXPIRED_MARKERS = (
    "로그인",
    "다시 접속",
    "세션",
)


class SrtBlockedError(SRTError):
    """Raised when SR has blocked this address for abnormal access."""

    def __init__(self, msg: str = IP_BLOCKED_MARKER):
        super().__init__(msg)


class SrtService(RailService):
    """Service for interacting with SR's API."""

    operator_name = "SRT"

    def __init__(self, *args, seat_type: SeatType = SeatType.GENERAL_FIRST, **kwargs):
        """
        Initialize the SRT service.

        Args:
            seat_type: Which class of seat the search is for. Held on the
                       instance because it is needed while *searching*, not
                       just while reserving: SR reports general and special
                       availability separately, and a search for "일반실만"
                       must not stop on a train that has only a special seat
                       left. Everything else is as :class:`RailService`.
        """
        super().__init__(*args, **kwargs)
        self._srt_instance: SRT | None = None
        self._seat_type = seat_type
        # One helper across re-logins. It caches the netfunnel key SR hands
        # out, and building a fresh client for every session refresh would
        # otherwise queue for a new one each time.
        self._netfunnel = NetFunnelHelper()

    @property
    def default_train_type(self) -> str:
        """
        SR runs one kind of train.

        The loop passes a train type around because Korail has several; here
        it is a label, never a filter.
        """
        return "SRT"

    @property
    def default_reserve_option(self) -> SeatType:
        return self._seat_type

    def _build_client(self, username: str, password: str) -> SRT:
        """
        Build an SRT client that has not logged in yet.

        Args:
            username: SR membership number, email, or phone number
            password: SR password

        Returns:
            A client that has not logged in yet
        """
        return SRT(
            username,
            password,
            auto_login=False,
            verbose=False,
            netfunnel_helper=self._netfunnel,
        )

    def login(self, username: str, password: str) -> bool:
        """
        Login to SR with credentials.

        Args:
            username: SR membership number, email, or phone number
            password: SR password

        Returns:
            True if login successful, False otherwise

        Raises:
            SrtBlockedError: If SR has blocked this address. Not a wrong
                             password and not something a retry can fix, so it
                             is raised rather than reported as a plain failure.
        """
        try:
            self._srt_instance = self._build_client(username, password)
            self._srt_instance.login()
            self._logged_in = self._srt_instance.is_login

            if self._logged_in:
                self._username = username
                self._password = password
                self._schedule_next_relogin()
                logger.info(f"SRT login successful for user: {mask_phone(username)}")
            else:
                logger.warning(f"SRT login failed for user: {mask_phone(username)}")

            return self._logged_in
        except SRTLoginError as e:
            self._logged_in = False
            if IP_BLOCKED_MARKER in str(e):
                logger.error(f"🚫 SR has blocked this address: {e}")
                raise SrtBlockedError(str(e)) from e
            logger.error(f"SRT login failed for user {mask_phone(username)}: {e}")
            return False
        except Exception as e:
            logger.error(f"SRT login error for user {mask_phone(username)}: {e}")
            return False

    def _relogin(self) -> bool:
        """Attempt to re-login with stored credentials after session expiry."""
        if not self._username or not self._password:
            logger.error("🔒 Cannot re-login: no stored credentials")
            return False

        logger.debug("🔄 Session expired, attempting re-login...")
        try:
            self._srt_instance = self._build_client(self._username, self._password)
            self._srt_instance.login()
            self._logged_in = self._srt_instance.is_login
            if self._logged_in:
                self._relogin_count += 1
                logger.debug(f"✅ Re-login successful (total: {self._relogin_count})")
            else:
                logger.error("❌ Re-login failed")
            return self._logged_in
        except SRTLoginError as e:
            self._logged_in = False
            if IP_BLOCKED_MARKER in str(e):
                logger.error(f"🚫 SR has blocked this address: {e}")
                raise SrtBlockedError(str(e)) from e
            logger.error(f"❌ Re-login failed: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Re-login error: {e}")
            self._logged_in = False
            return False
        finally:
            # Whether or not it worked, for the same reason Korail's does: a
            # deadline left in the past turns every pass of the search loop
            # into a login attempt.
            self._schedule_next_relogin()

    @staticmethod
    def _time_limit(max_dep_time: str) -> str | None:
        """
        Translate the bot's departure cutoff into SR's.

        The bot says HHMM and means "leaving before this"; SR takes HHMMSS and
        means "leaving at or before this". A cutoff of 1200 therefore becomes
        115959 rather than 120000, which would let the 12:00 train through.
        "2400" is the bot's way of saying there is no cutoff.

        Args:
            max_dep_time: Cutoff as HHMM, or "2400" for none

        Returns:
            The cutoff as HHMMSS, or None when there is none
        """
        if not max_dep_time or max_dep_time == "2400":
            return None

        second_before = int(max_dep_time[:2]) * 3600 + int(max_dep_time[2:4]) * 60 - 1
        if second_before < 0:
            # A cutoff of midnight leaves nothing to catch.
            return "000000"
        return (
            f"{second_before // 3600:02d}{second_before % 3600 // 60:02d}{second_before % 60:02d}"
        )

    def _wanted_seat(self, train, seat_type: SeatType) -> bool:
        """Whether this train has the class of seat the search is for."""
        if seat_type == SeatType.GENERAL_ONLY:
            return train.general_seat_available()
        if seat_type == SeatType.SPECIAL_ONLY:
            return train.special_seat_available()
        # Either preference takes whatever is going.
        return train.seat_available()

    def search_trains(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str = "000000",
        max_dep_time: str = "2400",
        train_type: Any = None,
        passenger_count: int = 1,
        verbose: bool = True,
        include_no_seats: bool = False,
        train_numbers: list[str] | None = None,
    ) -> list:
        """
        Search for SRT trains.

        Args:
            dep_date: Departure date (YYYYMMDD)
            src_locate: Source station name
            dst_locate: Destination station name
            dep_time: Departure time (HHMMSS)
            max_dep_time: Maximum departure time threshold (HHMM)
            train_type: Ignored - SR runs one kind of train
            passenger_count: Number of adult passengers. SR prices and seats
                             the search for one regardless; the count matters
                             at reservation time.
            verbose: Log the request and what came back
            include_no_seats: Return sold-out trains too. Off for the search
                              loop, which only wants what it can reserve; on
                              for showing the user what runs in the window,
                              where the sold-out ones are the whole point.
            train_numbers: Keep only these SRT train numbers. None or empty
                           means every train in the window.

        Returns:
            List of trains

        Raises:
            ValueError: If not logged in
            SearchUnavailableError: If SR could not be asked at all
        """
        if not self._logged_in or not self._srt_instance:
            raise ValueError("Must login before searching trains")

        time_limit = self._time_limit(max_dep_time)

        if verbose:
            logger.debug("🔍 Searching SRT trains with parameters:")
            logger.debug(f"  dep_date: {dep_date}")
            logger.debug(f"  src_locate: '{src_locate}'")
            logger.debug(f"  dst_locate: '{dst_locate}'")
            logger.debug(f"  dep_time: {dep_time}")
            logger.debug(f"  max_dep_time: {max_dep_time} -> time_limit {time_limit}")
            logger.debug(f"  seat_type: {self._seat_type}")
            logger.debug(f"  passengers: {passenger_count}")

        try:
            trains = self._srt_instance.search_train(
                src_locate,
                dst_locate,
                dep_date,
                dep_time,
                time_limit=time_limit,
                # Filtered here instead: SR calls a train available when
                # *either* class has a seat, which is not the question when
                # the user asked for one of them.
                available_only=False,
            )
        except SRTNotLoggedInError as e:
            logger.debug(f"🔒 Session expired during search, re-logging in: {e}")
            if self._relogin():
                return []  # Will retry on next loop iteration
            raise SearchUnavailableError(f"{type(e).__name__}: {e}") from e
        except SRTResponseError as e:
            if self._looks_expired(str(e)):
                logger.debug(f"🔒 Session looks expired during search: {e}")
                if self._relogin():
                    return []
            raise SearchUnavailableError(f"{type(e).__name__}: {e}") from e
        except Exception as e:
            # Same rule as Korail's: an answer we could not read is not the
            # same as no seats, and the loop must be able to tell them apart.
            raise SearchUnavailableError(f"{type(e).__name__}: {e}") from e

        if verbose:
            logger.debug(f"📋 SR returned {len(trains)} train(s)")

        if not include_no_seats:
            before = len(trains)
            trains = [train for train in trains if self._wanted_seat(train, self._seat_type)]
            if verbose:
                logger.debug(f"📊 {len(trains)} of {before} have a {self._seat_type.name} seat")

        # Narrow to the trains the user picked, if they picked any.
        if trains and train_numbers:
            wanted = set(train_numbers)
            trains = [train for train in trains if train.train_number in wanted]
            if verbose:
                logger.debug(
                    f"🎯 Watching {len(wanted)} chosen train(s): {len(trains)} of them "
                    f"are in this result"
                )

        if verbose:
            logger.debug(
                f"✅ Search complete: {len(trains)} trains available "
                f"({src_locate}→{dst_locate} on {dep_date})"
            )
        return trains

    @staticmethod
    def _looks_expired(message: str) -> bool:
        """Whether a refusal reads like the session rather than the request."""
        return any(marker in message for marker in SESSION_EXPIRED_MARKERS)

    @staticmethod
    def _looks_duplicate(message: str) -> bool:
        """Whether a refusal reads like this account already holds the booking."""
        return any(marker in message for marker in DUPLICATE_MARKERS)

    @staticmethod
    def _looks_sold_out(message: str) -> bool:
        """Whether a refusal reads like the seat went to someone else."""
        return any(marker in message for marker in SOLD_OUT_MARKERS)

    def reserve_train(self, train, option: Any = None, passenger_count: int = 1):
        """
        Attempt to reserve a specific train.

        Args:
            train: Train object from search_trains()
            option: SeatType to book with; the service's own if not given
            passenger_count: Number of adult passengers

        Returns:
            Reservation object if successful, None otherwise.
            Returns "DUPLICATE" string if duplicate reservation detected.
        """
        if not self._logged_in or not self._srt_instance:
            raise ValueError("Must login before reserving")

        if option is None:
            option = self._seat_type

        try:
            logger.debug("🎫 Attempting SRT reservation:")
            logger.debug(f"  Train: {train}")
            logger.debug(f"  Option: {option}")
            logger.debug(f"  Passengers: {passenger_count}")

            reservation = self._srt_instance.reserve(
                train,
                passengers=[Adult(passenger_count)],
                special_seat=option,
            )

            if reservation:
                logger.info("🎉 RESERVATION SUCCESS!")
                logger.info(f"  Reservation details: {reservation}")
                logger.info(f"  Reservation number: {reservation.reservation_number}")
                return reservation

            logger.debug("Reservation returned nothing (no seats available)")
            return None

        except SRTNotLoggedInError as e:
            logger.debug(f"🔒 Session expired during reservation, re-logging in: {e}")
            self._relogin()
            return None  # Will retry on next loop iteration
        except SRTResponseError as e:
            message = str(e)

            if self._looks_duplicate(message):
                logger.warning("⚠️ Duplicate reservation detected - will continue searching")
                logger.warning(f"  SR said: {message}")
                return "DUPLICATE"

            if self._looks_sold_out(message):
                logger.debug(f"Train sold out during reservation attempt: {message}")
                return None

            if self._looks_expired(message):
                logger.debug(f"🔒 Session looks expired during reservation: {message}")
                self._relogin()
                return None

            # Logged in full and at warning level on purpose: this is how the
            # marker lists above get extended. A refusal nobody recognises is
            # treated as the recoverable kind, because the alternative is
            # ending a search that may have been one attempt from a seat.
            logger.warning(f"❓ Unrecognised SR refusal, treating as retryable: {message}")
            return None
        except Exception as e:
            logger.error(f"❌ Reservation error ({type(e).__name__}): {e}")
            logger.error(f"  Train: {train}")
            logger.error("  Full traceback:", exc_info=True)
            return None

    # ==================== Payment, observed rather than performed ====================
    #
    # As with Korail, the bot reserves and the user pays. SR's client does
    # have a card call; it is never used here, and this file does not import
    # it. What is used is the reservation list, which says of each booking
    # whether it has been paid for - a plainer signal than Korail's, where a
    # paid reservation is one that has stopped being listed.

    def is_reservation_outstanding(self, rsv_id: str) -> bool | None:
        """
        Whether a reservation is still sitting unpaid.

        Args:
            rsv_id: The reservation number to look for

        Returns:
            True while it is still unpaid, False once it is paid or gone, and
            None when SR could not be asked - which is not the same as gone,
            and must not be read as one.
        """
        if not self._logged_in or not self._srt_instance:
            return None

        try:
            reservations = self._srt_instance.get_reservations()
        except SRTNotLoggedInError:
            return None
        except Exception as e:
            logger.warning(f"Could not check whether {rsv_id} is still unpaid: {e}")
            return None

        for reservation in reservations:
            if str(reservation.reservation_number) == str(rsv_id):
                return not reservation.paid

        # Not in the list at all: cancelled, or left to expire.
        return False

    @staticmethod
    def reservation_id(reservation) -> str | None:
        """The reservation number, as SR names it."""
        number = getattr(reservation, "reservation_number", None)
        return str(number) if number else None

    @staticmethod
    def payment_due(reservation) -> tuple[str | None, str | None]:
        """
        When SR stops holding this seat.

        SR calls these payment_date and payment_time, and states them on every
        reservation it hands back - including ones already paid for, where
        they are the deadline that was met rather than one still running.
        """
        return (
            getattr(reservation, "payment_date", None),
            getattr(reservation, "payment_time", None),
        )

    @staticmethod
    def describe_train(train) -> dict:
        """
        Reduce an SR train to what the keyboard and the summary need.

        "Sold out" here means neither class has a seat, which is the same
        question the search loop asks - a train with only a special seat left
        is not sold out, it is a train the user may or may not want.
        """
        return {
            "no": str(getattr(train, "train_number", "") or ""),
            "label": (
                f"{RailService._clock(getattr(train, 'dep_time', None))}→"
                f"{RailService._clock(getattr(train, 'arr_time', None))} "
                f"{getattr(train, 'train_name', None) or 'SRT'}"
            ),
            "soldout": not (hasattr(train, "seat_available") and train.seat_available()),
        }

    def cancel_reservation(self, rsv_id: str) -> bool:
        """
        Give one unpaid reservation back to SR.

        SR's client takes a reservation number as readily as a reservation
        object, so unlike Korail's this needs no lookup and no workaround.

        Args:
            rsv_id: The reservation number to give back

        Returns:
            True when SR confirmed it, False for every other outcome
        """
        if not self._logged_in or not self._srt_instance:
            logger.warning(f"Not logged in - cannot cancel {rsv_id}")
            return False

        try:
            cancelled = bool(self._srt_instance.cancel(rsv_id))
        except Exception as e:
            logger.error(f"SR refused to cancel {rsv_id}: {type(e).__name__}: {e}")
            return False

        logger.info(f"Cancelled reservation {rsv_id}")
        return cancelled

    def _cancel_reservations(self, reservations: list) -> None:
        """
        Give back the seats a random-seating run took but could not finish.

        SR's client can cancel outright, so unlike Korail's these do not have
        to be left to expire. Each is attempted on its own - one refusal must
        not strand the rest.
        """
        if not reservations:
            return

        if not self._logged_in or not self._srt_instance:
            super()._cancel_reservations(reservations)
            return

        logger.warning(f"Cancelling {len(reservations)} partial reservation(s)...")
        for reservation in reservations:
            try:
                self._srt_instance.cancel(reservation)
                logger.info(f"  cancelled: {reservation.reservation_number}")
            except Exception as e:
                # Reached while giving up on a search that has already gone
                # wrong. It cannot be the thing that raises. An uncancelled
                # reservation is not lost either - unpaid, SR reclaims it.
                logger.error(f"  could not cancel: {type(e).__name__}: {e}")
