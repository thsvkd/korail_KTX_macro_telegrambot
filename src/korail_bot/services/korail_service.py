"""Korail API service wrapper."""

import random
import time

import requests
from korail2 import AdultPassenger, NoResultsError, ReserveOption, SoldOutError, TrainType
from korail2 import Korail as K2MKorail

from korail_bot.config.settings import settings
from korail_bot.utils.logger import get_logger
from korail_bot.utils.privacy import mask_phone

logger = get_logger(__name__)


class KorailService:
    """Service for interacting with Korail API."""

    def __init__(self, app_session_start: str | None = None):
        """
        Initialize Korail service.

        Args:
            app_session_start: When this user's app session began, in epoch
                               milliseconds. A search that a restart
                               interrupted passes back the value it started
                               with so it stays one session; None lets the
                               client stamp the moment it was built.
        """
        self._korail_instance: K2MKorail | None = None
        self._logged_in = False
        self._search_interval = settings.KORAIL_SEARCH_INTERVAL
        self._search_jitter = settings.KORAIL_SEARCH_INTERVAL_JITTER
        self._app_session_start = app_session_start
        self._username: str | None = None
        self._password: str | None = None
        self._relogin_interval = settings.KORAIL_RELOGIN_INTERVAL
        self._relogin_jitter = settings.KORAIL_RELOGIN_INTERVAL_JITTER
        self._relogin_due_at: float = 0.0
        self._relogin_count: int = 0

        # Log class methods to verify correct version is loaded
        logger.debug(
            f"KorailService initialized with methods: {[m for m in dir(self) if not m.startswith('_')]}"
        )

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

    def _schedule_next_relogin(self) -> None:
        """
        Decide when the session should be refreshed next.

        Drawn again after every login rather than kept as a fixed period: a
        search running for half a day would otherwise re-authenticate exactly
        on the half hour, every half hour. A base interval of 0 turns the
        refresh off, leaving the session to be renewed when Korail actually
        rejects it.
        """
        if self._relogin_interval <= 0:
            self._relogin_due_at = 0.0
            return

        delay = self._spread(self._relogin_interval, self._relogin_jitter)
        self._relogin_due_at = time.time() + delay
        logger.debug(f"🔄 Next session refresh in {delay:.0f}s")

    def _check_session_refresh(self):
        """Refresh the session before Korail gets around to expiring it."""
        if self._relogin_due_at and time.time() >= self._relogin_due_at:
            logger.debug("🔄 Session due for a refresh, re-logging in")
            self._relogin()

    @staticmethod
    def _spread(seconds: float, ratio: float) -> float:
        """
        Draw a value from seconds * (1 +/- ratio).

        A fixed interval makes the search a metronome: every request lands the
        same number of seconds after the previous one, a pattern no person
        browsing the site would ever produce. Drawing each wait uniformly from
        a band around the configured value keeps the average rate but removes
        that signature. With ratio at 0 the value is returned unchanged.
        """
        if ratio <= 0:
            return seconds

        spread = seconds * ratio
        return max(0.0, random.uniform(seconds - spread, seconds + spread))

    def jittered(self, seconds: float) -> float:
        """Spread a wait between requests over the configured search jitter."""
        return self._spread(seconds, self._search_jitter)

    def next_interval(self, multiplier: float = 1.0) -> float:
        """
        Draw the next wait between requests.

        Args:
            multiplier: Scales the base interval (e.g. 1.5 for a longer wait)

        Returns:
            The number of seconds to wait
        """
        return self.jittered(self._search_interval * multiplier)

    def wait_between_requests(self, multiplier: float = 1.0) -> float:
        """Sleep for a randomised interval and return how long it waited."""
        delay = self.next_interval(multiplier)
        time.sleep(delay)
        return delay

    def wait_seconds(self, seconds: float) -> float:
        """Sleep for a randomised version of a fixed wait, in seconds."""
        delay = self.jittered(seconds)
        time.sleep(delay)
        return delay

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

        Returns:
            List of available trains

        Raises:
            ValueError: If not logged in
        """
        if not self._logged_in or not self._korail_instance:
            raise ValueError("Must login before searching trains")

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
            logger.error(f"❌ Error searching trains: {e}", exc_info=True)
            return []

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

    def search_and_reserve_loop(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str = "000000",
        max_dep_time: str = "2400",
        train_type: TrainType = TrainType.KTX,
        reserve_option: ReserveOption = ReserveOption.GENERAL_FIRST,
        passenger_count: int = 1,
        seat_strategy: str = "consecutive",
        max_attempts: int | None = None,
    ):
        """
        Continuously search for trains and attempt reservation until successful.

        Args:
            dep_date: Departure date (YYYYMMDD)
            src_locate: Source station
            dst_locate: Destination station
            dep_time: Departure time (HHMMSS)
            max_dep_time: Maximum departure time (HHMM)
            train_type: Train type filter
            reserve_option: Reservation option
            passenger_count: Number of adult passengers
            seat_strategy: "consecutive" for seats together, "random" for separate seats
            max_attempts: Maximum attempts (None for infinite)

        Returns:
            Reservation object(s) when successful, None if max_attempts reached
        """
        if not self._logged_in:
            raise ValueError("Must login before searching")

        logger.info(
            f"Starting reservation loop: {src_locate} -> {dst_locate} "
            f"on {dep_date} at {dep_time} for {passenger_count} passengers ({seat_strategy} seating)"
        )

        if seat_strategy == "consecutive":
            return self._search_and_reserve_consecutive(
                dep_date,
                src_locate,
                dst_locate,
                dep_time,
                max_dep_time,
                train_type,
                reserve_option,
                passenger_count,
                max_attempts,
            )
        else:  # random
            return self._search_and_reserve_random(
                dep_date,
                src_locate,
                dst_locate,
                dep_time,
                max_dep_time,
                train_type,
                reserve_option,
                passenger_count,
                max_attempts,
            )

    def _search_and_reserve_consecutive(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str,
        max_dep_time: str,
        train_type: TrainType,
        reserve_option: ReserveOption,
        passenger_count: int,
        max_attempts: int | None,
    ):
        """Reserve seats consecutively (together)."""
        attempts = 0
        duplicate_notified = False

        logger.info(f"🔄 Starting consecutive seat search loop (passengers={passenger_count})")

        while True:
            attempts += 1
            if max_attempts and attempts > max_attempts:
                logger.warning(f"❌ Reached max attempts ({max_attempts}), stopping")
                return None

            if attempts % 1000 == 0:
                logger.info(
                    f"📊 Search attempt #{attempts} (still searching..., re-logins: {self._relogin_count})"
                )

            is_summary = attempts % 60 == 0

            if is_summary:
                logger.debug(f"━━━ Search attempt #{attempts} ━━━")

            self._check_session_refresh()

            # Search for trains
            trains = self.search_trains(
                dep_date,
                src_locate,
                dst_locate,
                dep_time,
                max_dep_time,
                train_type,
                passenger_count,
                verbose=is_summary,
            )

            if not trains:
                if is_summary:
                    logger.debug(f"📊 Attempt #{attempts}: no trains found, retrying...")
                self.wait_between_requests()
                continue

            # Try to reserve each train found (trains found = rare, always log)
            for idx, train in enumerate(trains, 1):
                logger.debug(f"🚂 Trying train {idx}/{len(trains)}")
                reservation = self.reserve_train(
                    train, option=reserve_option, passenger_count=passenger_count
                )

                if reservation == "DUPLICATE":
                    # Duplicate reservation detected
                    if not duplicate_notified:
                        # First time - raise exception to notify user once
                        duplicate_notified = True
                        logger.warning("⚠️ First duplicate detection - notifying user")
                        raise DuplicateReservationError("동일한 예약 내역이 존재합니다")
                    else:
                        # Already notified - just log and continue
                        logger.debug("Duplicate reservation still exists, continuing search...")
                elif reservation:
                    logger.info(f"🎉 CONSECUTIVE RESERVATION SUCCESS after {attempts} attempts!")
                    return reservation
                else:
                    logger.debug(f"Train {idx} failed (sold out or unavailable)")

            logger.debug(f"All {len(trains)} trains sold out in attempt #{attempts}")

            # Wait before next search
            self.wait_between_requests()

    def _search_and_reserve_random(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str,
        max_dep_time: str,
        train_type: TrainType,
        reserve_option: ReserveOption,
        passenger_count: int,
        max_attempts: int | None,
    ):
        """Reserve seats randomly (one at a time until target count reached)."""
        attempts = 0
        reservations = []
        target_count = passenger_count
        duplicate_notified = False

        logger.info(f"Random seating: will reserve {target_count} individual tickets")

        while len(reservations) < target_count:
            attempts += 1
            if max_attempts and attempts > max_attempts:
                logger.warning(f"Reached max attempts ({max_attempts}), stopping")
                # Cancel any partial reservations
                self._cancel_reservations(reservations)
                return None

            is_summary = attempts % 60 == 0

            self._check_session_refresh()

            # Search for trains (search for single passenger each time)
            trains = self.search_trains(
                dep_date,
                src_locate,
                dst_locate,
                dep_time,
                max_dep_time,
                train_type,
                passenger_count=1,
                verbose=is_summary,
            )

            if not trains:
                if is_summary:
                    logger.debug(f"📊 Attempt #{attempts}: no trains found, retrying...")
                self.wait_between_requests()
                continue

            # Try to reserve each train found (trains found = rare, always log)
            for train in trains:
                logger.debug(
                    f"Found train: {train}, attempting reservation "
                    f"({len(reservations) + 1}/{target_count})..."
                )

                # Reserve one seat at a time
                reservation = self.reserve_train(train, option=reserve_option, passenger_count=1)

                if reservation == "DUPLICATE":
                    # Duplicate reservation detected
                    if not duplicate_notified:
                        # First time - raise exception to notify user once
                        duplicate_notified = True
                        logger.warning("First duplicate detection - notifying user")
                        raise DuplicateReservationError("동일한 예약 내역이 존재합니다")
                    else:
                        # Already notified - just log and continue
                        logger.debug("Duplicate reservation still exists, continuing search...")
                elif reservation:
                    reservations.append(reservation)
                    current_count = len(reservations)
                    logger.info(
                        f"Reserved seat {current_count}/{target_count} (attempt #{attempts})"
                    )
                    logger.debug(f"Reservation details: {reservation}")

                    # Check if we've reached target
                    if current_count >= target_count:
                        logger.info(
                            f"All {target_count} seats reserved successfully! "
                            f"Total attempts: {attempts}"
                        )
                        # Return the first reservation as primary (for compatibility)
                        # Store all reservations in a custom attribute for later access
                        first_reservation = reservations[0]
                        first_reservation._all_reservations = reservations
                        first_reservation._is_random_allocation = True
                        first_reservation._total_seats = target_count
                        return first_reservation

                    # Add delay between individual reservations to avoid rate limit
                    # Use longer interval for safety
                    self.wait_between_requests(1.5)
                    break  # Found a train and reserved, restart search loop

                else:
                    logger.debug("Reservation failed, continuing search...")

            # Wait before next search attempt
            self.wait_between_requests()

        return reservations[0] if reservations else None

    def _cancel_reservations(self, reservations: list) -> None:
        """Cancel a list of reservations (cleanup for failed random allocation)."""
        if not reservations:
            return

        logger.warning(f"Cancelling {len(reservations)} partial reservations...")
        for reservation in reservations:
            try:
                # Note: korail2 API has a cancel method but we need to check if it's available
                logger.warning(f"Would cancel reservation: {reservation}")
                # self._korail_instance.cancel(reservation.rsv_id)
            except Exception as e:
                logger.error(f"Failed to cancel reservation: {e}")

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

    @property
    def is_logged_in(self) -> bool:
        """Check if currently logged in."""
        return self._logged_in


class DuplicateReservationError(Exception):
    """Raised when attempting to reserve a train that's already reserved."""

    pass
