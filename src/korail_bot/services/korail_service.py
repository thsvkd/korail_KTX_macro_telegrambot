"""Korail API service wrapper."""

import random
import time
from collections.abc import Callable

import requests
from korail2 import AdultPassenger, NoResultsError, ReserveOption, SoldOutError, TrainType
from korail2 import Korail as K2MKorail

from korail_bot.config.settings import settings
from korail_bot.utils.logger import get_logger
from korail_bot.utils.privacy import mask_phone

logger = get_logger(__name__)


class KorailService:
    """Service for interacting with Korail API."""

    def __init__(
        self,
        app_session_start: str | None = None,
        on_status: Callable[[str], None] | None = None,
    ):
        """
        Initialize Korail service.

        Args:
            app_session_start: When this user's app session began, in epoch
                               milliseconds. A search that a restart
                               interrupted passes back the value it started
                               with so it stays one session; None lets the
                               client stamp the moment it was built.
            on_status: Called with a message for the user when the search
                       stops being able to reach Korail, and again when it
                       recovers. A callback rather than a TelegramService so
                       that this class keeps knowing nothing about Telegram;
                       None means nobody is told, which is right for the
                       short-lived clients that only log in and stop.
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
        self._on_status = on_status
        self._failure_streak: int = 0
        self._failure_since: float = 0.0
        self._failure_alerted_at: float = 0.0
        self._failure_threshold = settings.KORAIL_FAILURE_ALERT_THRESHOLD
        self._failure_realert = settings.KORAIL_FAILURE_REALERT_SECONDS
        self._failure_backoff_cap = settings.KORAIL_FAILURE_BACKOFF_CAP

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

    # ==================== Reachability tracking ====================
    #
    # Korail answering "no trains" and Korail not answering at all both leave
    # the loop with nothing to reserve. Told apart, the first is the whole
    # point of the search and the second is worth waking the user for; run
    # together, as they were, a blocked search looks exactly like a sold-out
    # one and reports itself as healthy forever.

    def _announce(self, message: str) -> None:
        """Pass a message to the user, if anyone is listening."""
        if not self._on_status:
            return
        try:
            self._on_status(message)
        except Exception as e:
            # Telling the user failed. That must not end the search.
            logger.error(f"Failed to deliver search status message: {e}", exc_info=True)

    def note_search_failure(self, error: Exception) -> float:
        """
        Record a failed request and say how much to slow down.

        Args:
            error: What went wrong, for the log and the user-facing message

        Returns:
            A multiplier for the next wait between requests
        """
        self._failure_streak += 1
        now = time.time()
        if self._failure_streak == 1:
            self._failure_since = now

        logger.error(
            f"❌ Korail request failed ({self._failure_streak} in a row): "
            f"{type(error).__name__}: {error}",
            exc_info=self._failure_streak == 1,
        )

        due = self._failure_streak >= self._failure_threshold and (
            self._failure_alerted_at == 0.0
            or now - self._failure_alerted_at >= self._failure_realert
        )
        if due:
            self._failure_alerted_at = now
            minutes = int((now - self._failure_since) // 60)
            self._announce(
                f"⚠️ 코레일 응답을 받지 못하고 있습니다\n\n"
                f"연속 {self._failure_streak}회 실패"
                f"{f' (약 {minutes}분째)' if minutes else ''}.\n"
                f"마지막 오류: {type(error).__name__}\n\n"
                f"검색은 계속하되 요청 간격을 늘려 재시도합니다.\n"
                f"코레일 점검 중이거나 접속이 차단되었을 수 있습니다.\n\n"
                f"💡 중단하려면 /cancel 을 사용하세요."
            )

        # Doubling per failure, capped: at the default 1s interval this walks
        # 1s, 2s, 4s ... up to a minute between attempts. Shifting instead of
        # ** so a long outage cannot produce a float overflow.
        multiplier = float(1 << min(self._failure_streak - 1, 20))
        return min(multiplier, self._failure_backoff_cap)

    def note_search_success(self) -> None:
        """Record that Korail answered, and say so if it had stopped."""
        if self._failure_streak == 0:
            return

        recovered_from = self._failure_streak
        alerted = self._failure_alerted_at != 0.0
        self._failure_streak = 0
        self._failure_since = 0.0
        self._failure_alerted_at = 0.0

        logger.info(f"✅ Korail is answering again after {recovered_from} failed request(s)")
        # Only worth a message if the silence was reported in the first place.
        if alerted:
            self._announce(
                "✅ 코레일 응답이 정상으로 돌아왔습니다\n\n원래 간격으로 검색을 계속합니다."
            )

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
        train_numbers: list[str] | None = None,
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
            train_numbers: Watch only these Korail train numbers; empty or
                           None watches every train in the time window

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
                train_numbers,
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
                train_numbers,
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
        train_numbers: list[str] | None = None,
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
            try:
                trains = self.search_trains(
                    dep_date,
                    src_locate,
                    dst_locate,
                    dep_time,
                    max_dep_time,
                    train_type,
                    passenger_count,
                    verbose=is_summary,
                    train_numbers=train_numbers,
                )
            except SearchUnavailableError as e:
                self.wait_between_requests(self.note_search_failure(e))
                continue

            self.note_search_success()

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
        train_numbers: list[str] | None = None,
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
            try:
                trains = self.search_trains(
                    dep_date,
                    src_locate,
                    dst_locate,
                    dep_time,
                    max_dep_time,
                    train_type,
                    passenger_count=1,
                    verbose=is_summary,
                    train_numbers=train_numbers,
                )
            except SearchUnavailableError as e:
                self.wait_between_requests(self.note_search_failure(e))
                continue

            self.note_search_success()

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


class SearchUnavailableError(Exception):
    """
    Raised when a search could not be carried out at all.

    Distinct from finding no trains, which is an ordinary answer and comes
    back as an empty list. This one means Korail refused, timed out, or
    replied with something the client could not read - a state the search
    cannot fix by asking again immediately, and that the user should hear
    about if it persists.
    """

    pass
