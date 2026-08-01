"""Korail API service wrapper.

Only the Korail-specific half lives here - logging in, asking korail2 for
trains, and reserving one. The search loop that drives those calls is in
:mod:`korail_bot.services.rail_service`, shared with SR.
"""

import requests
from korail2 import AdultPassenger, NoResultsError, ReserveOption, SoldOutError, TrainType
from korail2 import Korail as K2MKorail

from korail_bot.config.settings import settings
from korail_bot.services.rail_service import (
    DuplicateReservationError,
    RailService,
    SearchProgress,
    SearchUnavailableError,
)
from korail_bot.utils.logger import get_logger
from korail_bot.utils.privacy import mask_phone

logger = get_logger(__name__)

# Re-exported: these used to be defined here, and half the codebase imports
# them from this module. Moving them to rail_service.py is not a reason to
# make every caller say so.
__all__ = [
    "DuplicateReservationError",
    "KorailService",
    "SearchProgress",
    "SearchUnavailableError",
]


class KorailService(RailService):
    """Service for interacting with Korail API."""

    operator_name = "코레일"

    def __init__(self, *args, **kwargs):
        """
        Initialize Korail service.

        Takes the same arguments as :class:`RailService`.
        """
        super().__init__(*args, **kwargs)
        self._korail_instance: K2MKorail | None = None

        # Log class methods to verify correct version is loaded
        logger.debug(
            f"KorailService initialized with methods: {[m for m in dir(self) if not m.startswith('_')]}"
        )

    @property
    def default_train_type(self) -> TrainType:
        return TrainType.KTX

    @property
    def default_reserve_option(self) -> ReserveOption:
        return ReserveOption.GENERAL_FIRST

    def _build_client(self, username: str, password: str) -> K2MKorail:
        """
        Build a Korail client that belongs to this service alone.

        korail2 keeps its requests.Session on the class, so out of the box
        every client in a process shares one cookie jar - two users answering
        the password prompt at the same time would be handing each other their
        Korail session. Each client gets a session of its own here, carrying
        over the User-Agent the library set.

        Args:
            username: Korail username
            password: Korail password

        Returns:
            A client that has not logged in yet
        """
        client = K2MKorail(username, password, auto_login=False)

        user_agent = client._session.headers.get("User-Agent")
        client._session = requests.Session()
        if user_agent:
            client._session.headers.update({"User-Agent": user_agent})

        if settings.KORAIL_APP_VERSION:
            client._version = settings.KORAIL_APP_VERSION

        if self._app_session_start:
            # Every request carries when the app was started. A search the
            # bot restarted is the same session continuing, so it keeps the
            # timestamp it began with rather than announcing a fresh launch.
            client._engine.app_start_ts = self._app_session_start

        return client

    def login(self, username: str, password: str) -> bool:
        """
        Login to Korail with credentials.

        Args:
            username: Korail username (phone number in format 010-xxxx-xxxx)
            password: Korail password

        Returns:
            True if login successful, False otherwise
        """
        try:
            self._korail_instance = self._build_client(username, password)
            self._logged_in = self._korail_instance.login()

            if self._logged_in:
                self._username = username
                self._password = password
                self._schedule_next_relogin()
                logger.info(f"Korail login successful for user: {mask_phone(username)}")
            else:
                logger.warning(f"Korail login failed for user: {mask_phone(username)}")

            return self._logged_in
        except Exception as e:
            logger.error(f"Korail login error for user {mask_phone(username)}: {e}")
            return False

    def _relogin(self) -> bool:
        """Attempt to re-login with stored credentials after session expiry."""
        if not self._username or not self._password:
            logger.error("🔒 Cannot re-login: no stored credentials")
            return False

        logger.debug("🔄 Session expired, attempting re-login...")
        try:
            self._korail_instance = self._build_client(self._username, self._password)
            self._logged_in = self._korail_instance.login()
            if self._logged_in:
                self._relogin_count += 1
                logger.debug(f"✅ Re-login successful (total: {self._relogin_count})")
            else:
                logger.error("❌ Re-login failed")
            return self._logged_in
        except Exception as e:
            logger.error(f"❌ Re-login error: {e}")
            self._logged_in = False
            return False
        finally:
            # Whether or not it worked. A failed refresh that left the
            # deadline in the past would try again on every pass of the search
            # loop, which is a login attempt every couple of seconds; the
            # session is renewed on demand anyway when Korail rejects it.
            self._schedule_next_relogin()

    def search_trains(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str = "000000",
        max_dep_time: str = "2400",
        train_type: TrainType = TrainType.KTX,
        passenger_count: int = 1,
        verbose: bool = True,
        include_no_seats: bool = False,
        train_numbers: list[str] | None = None,
    ) -> list:
        """
        Search for available trains.

        Args:
            dep_date: Departure date (YYYYMMDD)
            src_locate: Source station name (without '역')
            dst_locate: Destination station name (without '역')
            dep_time: Departure time (HHMMSS)
            max_dep_time: Maximum departure time threshold (HHMM)
            train_type: Type of train to search for
            passenger_count: Number of adult passengers
            include_no_seats: Return sold-out trains too. Off for the search
                              loop, which only wants what it can reserve; on
                              for showing the user what runs in the window,
                              where the sold-out ones are the whole point.
            train_numbers: Keep only these Korail train numbers. None or empty
                           means every train in the window.

        Returns:
            List of available trains

        Raises:
            ValueError: If not logged in
        """
        if not self._logged_in or not self._korail_instance:
            raise ValueError("Must login before searching trains")

        if train_type is None:
            train_type = self.default_train_type

        try:
            # Create passenger list
            passengers = [AdultPassenger(passenger_count)]

            if verbose:
                logger.debug("🔍 Searching trains with parameters:")
                logger.debug(f"  dep_date: {dep_date} (type: {type(dep_date).__name__})")
                logger.debug(f"  src_locate: '{src_locate}' (type: {type(src_locate).__name__})")
                logger.debug(f"  dst_locate: '{dst_locate}' (type: {type(dst_locate).__name__})")
                logger.debug(f"  dep_time: {dep_time} (type: {type(dep_time).__name__})")
                logger.debug(f"  train_type: {train_type}")
                logger.debug(f"  passengers: {passengers} (count: {passenger_count})")
                logger.debug(f"  max_dep_time: {max_dep_time}")

            trains = self._korail_instance.search_train(
                src_locate,
                dst_locate,
                dep_date,
                dep_time,
                train_type=train_type,
                passengers=passengers,
                include_no_seats=include_no_seats,
            )

            if verbose:
                logger.debug(f"📋 Korail API returned {len(trains) if trains else 0} trains")

                # Log each train found with seat availability
                if trains:
                    for i, train in enumerate(trains, 1):
                        train_str = str(train)
                        logger.debug(f"  Train #{i}: {train_str}")

                        if hasattr(train, "seat_available"):
                            logger.debug(f"    Seats available: {train.seat_available}")
                        if hasattr(train, "general_seat"):
                            logger.debug(f"    General seats: {train.general_seat}")
                        if hasattr(train, "special_seat"):
                            logger.debug(f"    Special seats: {train.special_seat}")

            # Filter by max departure time
            if trains and max_dep_time != "2400":
                filtered_trains = []
                max_time = int(max_dep_time)

                if verbose:
                    logger.debug(f"🔧 Applying max_dep_time filter: {max_dep_time}")

                for train in trains:
                    dep_time_int = self._extract_departure_time(train)
                    if dep_time_int > 0 and dep_time_int < max_time:
                        filtered_trains.append(train)
                        if verbose:
                            logger.debug(f"  ✅ Kept: {dep_time_int} < {max_time}")
                    else:
                        if verbose:
                            logger.debug(f"  ❌ Filtered out: {dep_time_int} >= {max_time}")

                trains = filtered_trains
                if verbose:
                    logger.debug(f"📊 After filtering: {len(trains)} trains remain")

            # Narrow to the trains the user picked, if they picked any.
            #
            # Applied here rather than in the loops so both seat strategies get
            # it from one place, and so a train that stops running mid-search
            # simply stops appearing rather than needing to be noticed.
            if trains and train_numbers:
                wanted = set(train_numbers)
                trains = [train for train in trains if train.train_no in wanted]
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

        except NoResultsError:
            if verbose:
                logger.debug("No trains found for search criteria (NoResultsError)")
            return []
        except Exception as e:
            if type(e).__name__ == "NeedToLoginError":
                logger.debug(f"🔒 Session expired during search, re-logging in: {e}")
                if self._relogin():
                    return []  # Will retry on next loop iteration
                else:
                    raise
            # Anything else means the request did not get an answer we
            # understand. Raised rather than returned as an empty list: the
            # caller cannot act on what it cannot distinguish from a sold-out
            # train, and this used to be swallowed here, leaving a search that
            # had stopped working reporting itself as still looking.
            raise SearchUnavailableError(f"{type(e).__name__}: {e}") from e

    def reserve_train(
        self, train, option: ReserveOption = ReserveOption.GENERAL_FIRST, passenger_count: int = 1
    ):
        """
        Attempt to reserve a specific train.

        Args:
            train: Train object from search_trains()
            option: Reservation option (special seat preference)
            passenger_count: Number of adult passengers

        Returns:
            Reservation object if successful, None otherwise
            Returns "DUPLICATE" string if duplicate reservation detected
        """
        if not self._logged_in or not self._korail_instance:
            raise ValueError("Must login before reserving")

        if option is None:
            option = self.default_reserve_option

        try:
            # Create passenger list
            passengers = [AdultPassenger(passenger_count)]

            logger.debug("🎫 Attempting reservation:")
            logger.debug(f"  Train: {train}")
            logger.debug(f"  Option: {option}")
            logger.debug(f"  Passengers: {passenger_count}")

            reservation = self._korail_instance.reserve(train, passengers=passengers, option=option)

            if reservation:
                logger.info("🎉 RESERVATION SUCCESS!")
                logger.info(f"  Reservation details: {reservation}")
                if hasattr(reservation, "rsv_id"):
                    logger.info(f"  Reservation ID: {reservation.rsv_id}")
                return reservation
            else:
                logger.debug("Reservation returned None (no seats available)")
                return None

        except SoldOutError:
            logger.debug(f"Train sold out during reservation attempt: {train}")
            return None
        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__

            # Check for duplicate reservation error
            if "동일한 예약 내역" in error_msg or "WRR800029" in error_msg:
                # Return special value instead of raising exception
                logger.warning("⚠️ Duplicate reservation detected - will continue searching")
                logger.warning(f"  Error: {error_msg}")
                return "DUPLICATE"

            if error_type == "NeedToLoginError":
                logger.debug(f"🔒 Session expired during reservation, re-logging in: {error_msg}")
                if self._relogin():
                    return None  # Will retry on next loop iteration
                else:
                    raise

            logger.error(f"❌ Reservation error ({error_type}): {error_msg}")
            logger.error(f"  Train: {train}")
            logger.error(f"  Option: {option}")
            logger.error("  Full traceback:", exc_info=True)
            return None

    # ==================== Payment, observed rather than performed ====================
    #
    # The bot reserves; the user pays. Nothing here pays for anything, and
    # korail2 could not if it wanted to - it has no payment call at all. What
    # it does have is the list of reservations still waiting to be paid for,
    # which is enough to tell whether a payment happened without ever taking
    # part in one.

    def is_reservation_outstanding(self, rsv_id: str) -> bool | None:
        """
        Whether a reservation is still sitting unpaid.

        Korail lists a reservation until it is paid for, cancelled, or left
        to expire, at which point it drops off. Read-only, and the only way
        the bot can know a payment happened: until now "payment complete"
        meant the user had sent any message at all, which is a claim rather
        than a fact.

        Args:
            rsv_id: The reservation number to look for

        Returns:
            True while it is still unpaid, False once it is gone, and None
            when Korail could not be asked - which is not the same as gone,
            and must not be read as one.
        """
        if not self._logged_in or not self._korail_instance:
            return None

        try:
            reservations = self._korail_instance.reservations()
        except NoResultsError:
            # No reservations at all. An ordinary answer, not a failure.
            return False
        except Exception as e:
            logger.warning(f"Could not check whether {rsv_id} is still unpaid: {e}")
            return None

        return any(str(getattr(r, "rsv_id", "")) == str(rsv_id) for r in reservations)

    # Partial reservations are not cancelled here. korail2 has a cancel(), but
    # it sends a GET with its parameters in the body, which Korail ignores -
    # the call raises JSONDecodeError without cancelling anything - and it
    # asserts on a Reservation object where this would pass an id. Fixing both
    # is a change to make deliberately, not as a side effect of this one; the
    # inherited no-op logs what it would have cancelled. See
    # docs/payment-automation-poc.md.

    @staticmethod
    def reservation_id(reservation) -> str | None:
        """The reservation number, as korail2 names it."""
        rsv_id = getattr(reservation, "rsv_id", None)
        return str(rsv_id) if rsv_id else None

    @staticmethod
    def payment_due(reservation) -> tuple[str | None, str | None]:
        """When Korail stops holding this seat, as korail2 names it."""
        return (
            getattr(reservation, "buy_limit_date", None),
            getattr(reservation, "buy_limit_time", None),
        )

    @staticmethod
    def describe_train(train) -> dict:
        """Reduce a korail2 train to what the keyboard and the summary need."""
        return {
            "no": str(getattr(train, "train_no", "") or ""),
            "label": (
                f"{RailService._clock(getattr(train, 'dep_time', None))}→"
                f"{RailService._clock(getattr(train, 'arr_time', None))} "
                f"{getattr(train, 'train_type_name', None) or '열차'}"
            ),
            "soldout": not (hasattr(train, "has_seat") and train.has_seat()),
        }

    def _extract_departure_time(self, train) -> int:
        """
        Extract departure time from train object as HHMM integer.

        Args:
            train: Train object from korail2

        Returns:
            Departure time as integer (e.g., 944 for 09:44), 0 if extraction fails
        """
        try:
            # str(train) format: "[KTX] 4월 8일, 용산~광주송정(09:44~12:50), ..."
            # Use rsplit to handle station names with parentheses e.g. 울산(통도사)~서울(09:44~12:50)
            train_str = str(train)
            time_part = train_str.rsplit("(", 1)[1].split("~")[0]  # "09:44"
            time_str = "".join(time_part.split(":"))  # "0944"
            return int(time_str)
        except (IndexError, ValueError) as e:
            logger.error(f"Failed to extract departure time from train: {train}, error: {e}")
            return 0
