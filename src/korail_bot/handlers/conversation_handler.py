"""Conversation flow handler for reservation process."""

from datetime import datetime, timedelta

from korail2 import ReserveOption

from korail_bot.config.settings import settings
from korail_bot.models import (
    OnboardedAccount,
    TrainSearchParams,
    UserCredentials,
    UserProgress,
    UserSession,
)
from korail_bot.services import KorailService, MessageTemplates, ReservationService, TelegramService
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot import keyboards
from korail_bot.utils.logger import get_logger
from korail_bot.utils.privacy import mask_phone
from korail_bot.utils.validators import InputValidator

logger = get_logger(__name__)


class ConversationHandler:
    """Handles multi-step conversation flow for train reservation."""

    def __init__(
        self,
        storage: StorageInterface,
        telegram_service: TelegramService,
        reservation_service: ReservationService,
    ):
        """
        Initialize conversation handler.

        Args:
            storage: Storage interface
            telegram_service: Telegram messaging service
            reservation_service: Reservation service
        """
        self.storage = storage
        self.telegram = telegram_service
        self.reservation = reservation_service

    def handle_message(self, chat_id: int, text: str) -> None:
        """
        Handle user message based on current conversation state.

        Args:
            chat_id: Telegram chat ID
            text: User's message text
        """
        # Get user session
        session = self.storage.get_user_session(chat_id)
        if not session:
            logger.warning(f"No session found for chat_id={chat_id}")
            self.telegram.send_message(
                chat_id, "[진행중인 예약프로세스가 없습니다]\n/start 를 입력하여 작업을 시작하세요."
            )
            return

        # Check if already finding ticket
        if session.last_action == UserProgress.FINDING_TICKET:
            self._handle_already_processing(chat_id, session)
            return

        # Route to appropriate handler based on progress
        progress = session.last_action

        if progress == UserProgress.STARTED:
            self._handle_start_confirmation(chat_id, text, session)
        elif progress == UserProgress.START_ACCEPTED:
            self._handle_phone_input(chat_id, text, session)
        elif progress == UserProgress.ID_INPUT_SUCCESS:
            self._handle_password_input(chat_id, text, session)
        elif progress == UserProgress.PW_INPUT_SUCCESS:
            self._handle_date_input(chat_id, text, session)
        elif progress == UserProgress.DATE_INPUT_SUCCESS:
            self._handle_src_station_input(chat_id, text, session)
        elif progress == UserProgress.SRC_LOCATE_INPUT_SUCCESS:
            self._handle_dst_station_input(chat_id, text, session)
        elif progress == UserProgress.DST_LOCATE_INPUT_SUCCESS:
            self._handle_dep_time_input(chat_id, text, session)
        elif progress == UserProgress.DEP_TIME_INPUT_SUCCESS:
            self._handle_max_dep_time_input(chat_id, text, session)
        elif progress == UserProgress.MAX_DEP_TIME_INPUT_SUCCESS:
            self._handle_train_type_input(chat_id, text, session)
        elif progress == UserProgress.TRAIN_TYPE_INPUT_SUCCESS:
            self._handle_special_option_input(chat_id, text, session)
        elif progress == UserProgress.SPECIAL_INPUT_SUCCESS:
            self._handle_passenger_count_input(chat_id, text, session)
        elif progress == UserProgress.PASSENGER_COUNT_INPUT_SUCCESS:
            self._handle_seat_strategy_input(chat_id, text, session)
        elif progress == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS:
            self._handle_train_selection_input(chat_id, text, session)
        elif progress == UserProgress.TRAIN_SELECT_INPUT_SUCCESS:
            self._handle_final_confirmation(chat_id, text, session)
        elif progress == UserProgress.SCHEDULE_INPUT_PENDING:
            self._handle_schedule_input(chat_id, text, session)
        elif progress == UserProgress.ONBOARDING_OVERWRITE_PENDING:
            self._handle_onboarding_overwrite(chat_id, text, session)
        else:
            logger.error(f"Unknown progress state: {progress}")
            self.telegram.send_message(
                chat_id,
                "이상이 발생했습니다. /cancel 이나 /start 를 통해 다시 프로그램을 시작해주세요.",
            )

    def _handle_start_confirmation(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle initial start confirmation (Y/N)."""
        # Optional shortcut that logs in with the operator's own Korail
        # account. Disabled unless ADMIN_MAGIC_STRING is configured - a
        # value committed to the repository would let any reader use it.
        if settings.ADMIN_MAGIC_STRING and text == settings.ADMIN_MAGIC_STRING:
            self._handle_admin_login(chat_id, session)
            return

        is_yes, error = InputValidator.validate_yes_no(text)

        if is_yes is True:
            session.last_action = UserProgress.START_ACCEPTED
            self.storage.save_user_session(session)
            # Both prompts that follow have a known answer when the operator
            # put their Korail account in the environment, so skip them.
            if settings.has_preconfigured_korail_credentials():
                self._handle_preconfigured_login(chat_id, session)
                return
            # A phone number has to be typed, but leaving should not have to
            # be, so the cancel button follows the flow all the way through.
            self.telegram.send_message(
                chat_id,
                MessageTemplates.request_phone_number(),
                reply_markup=keyboards.cancel_only_keyboard(),
            )
        elif is_yes is False:
            session.reset()
            self.storage.save_user_session(session)
            from korail_bot.telegramBot.messages import Messages

            self.telegram.send_message(chat_id, Messages.CANCEL_START_CONFIRMATION)
        else:
            self.telegram.send_message(
                chat_id, error, reply_markup=keyboards.start_confirm_keyboard()
            )

    def _login_with_environment_credentials(self, chat_id: int, session: UserSession) -> str | None:
        """
        Log in with USERID/USERPW and record the result on the session.

        Returns:
            The Korail ID that was logged in with, or None when the login
            failed. The session is left untouched on failure so the caller
            decides what to do next.
        """
        # Korail wants the hyphenated form of a mobile number, the same way
        # the typed-in number is normalised. A member number or an e-mail is
        # left as it is.
        username = (
            InputValidator.normalize_phone_number(settings.KORAIL_ADMIN_USER_ID)
            or settings.KORAIL_ADMIN_USER_ID
        )
        password = settings.KORAIL_ADMIN_PASSWORD

        # Try login. The same app session the user's search will run under,
        # so validating a password does not look like a separate device.
        korail = KorailService(
            app_session_start=self.storage.get_or_create_app_session_start(chat_id)
        )
        if not korail.login(username, password):
            return None

        session.credentials = UserCredentials(korail_id=username, korail_pw=password)
        session.last_action = UserProgress.PW_INPUT_SUCCESS
        self.storage.save_user_session(session)
        return username

    def _handle_preconfigured_login(self, chat_id: int, session: UserSession) -> None:
        """Log in with the account from the environment instead of prompting."""
        username = self._login_with_environment_credentials(chat_id, session)

        if username:
            logger.info(f"Logged in with preconfigured credentials for chat_id={chat_id}")
            self.telegram.send_message(
                chat_id,
                MessageTemplates.preconfigured_login_success(username),
                reply_markup=keyboards.date_keyboard(),
            )
            return

        # A stale password in .env must not leave the user with nowhere to go,
        # so fall back to the prompts that were skipped. The session is
        # already at START_ACCEPTED, which is where the phone number is
        # expected.
        logger.warning(
            f"Preconfigured Korail login failed for chat_id={chat_id}; "
            f"falling back to manual credential entry"
        )
        from korail_bot.telegramBot.messages import Messages

        self.telegram.send_message(
            chat_id,
            Messages.PRECONFIGURED_LOGIN_FAILED,
            reply_markup=keyboards.cancel_only_keyboard(),
        )

    def _handle_admin_login(self, chat_id: int, session: UserSession) -> None:
        """Handle magic admin login."""
        if not settings.KORAIL_ADMIN_USER_ID or not settings.KORAIL_ADMIN_PASSWORD:
            session.reset()
            self.storage.save_user_session(session)
            from korail_bot.telegramBot.messages import Messages

            self.telegram.send_message(chat_id, Messages.ERROR_ADMIN_ENV)
            return

        if self._login_with_environment_credentials(chat_id, session):
            self.telegram.send_message(
                chat_id, MessageTemplates.login_success(), reply_markup=keyboards.date_keyboard()
            )
        else:
            session.reset()
            self.storage.save_user_session(session)
            from korail_bot.telegramBot.messages import Messages

            self.telegram.send_message(chat_id, Messages.ERROR_ADMIN_LOGIN)

    def _handle_onboarding_overwrite(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle the answer to 'you already have an account registered'."""
        is_yes, error = InputValidator.validate_yes_no(text)

        if is_yes is True:
            # Dropped before asking for the new one. Half-finished registration
            # would otherwise leave the old account in place while the user
            # believes they replaced it.
            self.storage.delete_onboarded_account(chat_id)
            session.credentials = None
            session.last_action = UserProgress.START_ACCEPTED
            self.storage.save_user_session(session)
            self.telegram.send_message(
                chat_id,
                MessageTemplates.request_phone_number(),
                reply_markup=keyboards.cancel_only_keyboard(),
            )
        elif is_yes is False:
            session.reset()
            self.storage.save_user_session(session)
            self.telegram.send_message(
                chat_id, "기존 등록을 그대로 두었습니다.\n/start 로 예약을 시작할 수 있습니다."
            )
        else:
            self.telegram.send_message(
                chat_id, error, reply_markup=keyboards.onboarding_overwrite_keyboard()
            )

    def _remember_account(self, chat_id: int, username: str, password: str) -> None:
        """
        Store a verified Korail login so the user does not type it again.

        Best effort: the booking the user is in the middle of matters more
        than the convenience of the next one, so a storage failure is logged
        and the flow carries on.
        """
        try:
            self.storage.save_onboarded_account(
                OnboardedAccount(chat_id=chat_id, korail_id=username, korail_pw=password)
            )
            logger.info(f"Registered Korail account for chat_id={chat_id}")
        except Exception as e:
            logger.error(f"Could not register the account for chat_id={chat_id}: {e}")

    def resume_with_registered_account(self, chat_id: int, session: UserSession) -> bool:
        """
        Log in with the account this chat registered earlier.

        Called instead of asking for a phone number and a password. The stored
        password is verified against Korail rather than trusted: people change
        their Korail password without telling the bot, and finding that out
        here is far better than finding it out from a search that never runs.

        Returns:
            True when the session is logged in and ready for the date step
        """
        account = self.storage.get_onboarded_account(chat_id)
        if not account:
            return False

        korail = KorailService(
            app_session_start=self.storage.get_or_create_app_session_start(chat_id)
        )
        if not korail.login(account.korail_id, account.korail_pw):
            # The registration is no longer usable, so it goes. Leaving it
            # would fail this same way on every /start from now on.
            logger.info(f"Registered account for chat_id={chat_id} no longer logs in")
            self.storage.delete_onboarded_account(chat_id)
            session.reset()
            session.in_progress = True
            session.last_action = UserProgress.STARTED
            self.storage.save_user_session(session)
            self.telegram.send_message(
                chat_id,
                MessageTemplates.ONBOARDING_STALE,
                reply_markup=keyboards.onboarding_start_keyboard(),
            )
            return True

        session.credentials = account.as_credentials()
        session.last_action = UserProgress.PW_INPUT_SUCCESS
        self.storage.save_user_session(session)

        logger.info(f"Logged in with the registered account for chat_id={chat_id}")
        self.telegram.send_message(
            chat_id,
            MessageTemplates.WELCOME_RETURNING.format(korailId=mask_phone(account.korail_id)),
            reply_markup=keyboards.date_keyboard(),
        )
        return True

    def _handle_phone_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle phone number input."""
        is_valid, error = InputValidator.validate_phone_number(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error + " 다시 입력 바랍니다.")
            return

        # Store the canonical form: Korail expects the hyphenated number, and
        # everything downstream (allow list, logs, masking) compares against it.
        text = InputValidator.normalize_phone_number(text) or text

        # Check allow list
        if not settings.is_user_allowed(text):
            # Notify subscribers
            subscribers = self.storage.get_all_subscribers()
            self.telegram.send_to_multiple(
                subscribers, f"{mask_phone(text)}가 구독자 목록에 없어서 실행에 실패했음."
            )

            session.reset()
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, MessageTemplates.not_in_allow_list())
            return

        # Save phone number
        if not session.credentials:
            session.credentials = UserCredentials(korail_id=text, korail_pw="")
        else:
            session.credentials.korail_id = text

        session.last_action = UserProgress.ID_INPUT_SUCCESS
        self.storage.save_user_session(session)
        self.telegram.send_message(chat_id, MessageTemplates.request_password())

    def _handle_password_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle password input and login."""
        # Validate password
        is_valid, error = InputValidator.validate_password(text)
        if not is_valid:
            self.telegram.send_message(
                chat_id,
                error + " 다시 입력 바랍니다.",
                reply_markup=keyboards.cancel_only_keyboard(),
            )
            return

        username = session.credentials.korail_id
        password = text

        # Update credentials
        session.credentials.korail_pw = password
        self.storage.save_user_session(session)

        # Try login. The same app session the user's search will run under,
        # so validating a password does not look like a separate device.
        korail = KorailService(
            app_session_start=self.storage.get_or_create_app_session_start(chat_id)
        )
        if korail.login(username, password):
            session.last_action = UserProgress.PW_INPUT_SUCCESS
            self.storage.save_user_session(session)

            # A login that works is worth keeping. Everything after this point
            # is the booking flow, which resets the session when it ends - so
            # the registration is written to a key of its own here, at the one
            # moment the password is known to be correct.
            self._remember_account(chat_id, username, password)

            self.telegram.send_message(
                chat_id, MessageTemplates.login_success(), reply_markup=keyboards.date_keyboard()
            )
        else:
            # Login failed - ask for retry. No buttons for the retry itself:
            # the answer is a password, a Y or an N, and only the first two
            # of those can be offered without putting a password on a button.
            self.telegram.send_message(
                chat_id,
                MessageTemplates.login_failure(username),
                reply_markup=keyboards.cancel_only_keyboard(),
            )
            # Don't change state - wait for retry input

    def _handle_date_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle departure date input."""
        is_valid, error = InputValidator.validate_date(text)

        if not is_valid:
            self.telegram.send_message(
                chat_id,
                f"{error}\n예매 희망일 8자를 입력해주십시오.\n(ex_ 20210124) <- 2021년 1월 24일",
                reply_markup=keyboards.date_keyboard(),
            )
            return

        session.train_info["depDate"] = text
        session.last_action = UserProgress.DATE_INPUT_SUCCESS
        self.storage.save_user_session(session)
        self.telegram.send_message(
            chat_id,
            MessageTemplates.request_departure_station(),
            reply_markup=keyboards.station_keyboard(keyboards.STEP_SRC_STATION),
        )

    def _handle_src_station_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle source station input."""
        is_valid, error = InputValidator.validate_station_name(text)

        if not is_valid:
            self.telegram.send_message(
                chat_id, error, reply_markup=keyboards.station_keyboard(keyboards.STEP_SRC_STATION)
            )
            return

        session.train_info["srcLocate"] = text
        session.last_action = UserProgress.SRC_LOCATE_INPUT_SUCCESS
        self.storage.save_user_session(session)
        # The departure station is dropped from the arrival keyboard: a train
        # from a station to itself is not something to make one tap away.
        self.telegram.send_message(
            chat_id,
            MessageTemplates.request_arrival_station(),
            reply_markup=keyboards.station_keyboard(keyboards.STEP_DST_STATION, exclude=text),
        )

    def _handle_dst_station_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle destination station input."""
        is_valid, error = InputValidator.validate_station_name(text)

        if not is_valid:
            self.telegram.send_message(
                chat_id,
                error,
                reply_markup=keyboards.station_keyboard(
                    keyboards.STEP_DST_STATION, exclude=session.train_info.get("srcLocate")
                ),
            )
            return

        session.train_info["dstLocate"] = text
        session.last_action = UserProgress.DST_LOCATE_INPUT_SUCCESS
        self.storage.save_user_session(session)

        from korail_bot.telegramBot.messages import Messages

        self.telegram.send_message(
            chat_id,
            Messages.REQUEST_DST_STATION,
            reply_markup=keyboards.time_keyboard(keyboards.STEP_DEP_TIME),
        )

    def _handle_dep_time_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle departure time input."""
        is_valid, error = InputValidator.validate_time(text)

        if not is_valid:
            self.telegram.send_message(
                chat_id, error, reply_markup=keyboards.time_keyboard(keyboards.STEP_DEP_TIME)
            )
            return

        session.train_info["depTime"] = text + "00"  # Add seconds
        session.last_action = UserProgress.DEP_TIME_INPUT_SUCCESS
        self.storage.save_user_session(session)

        from korail_bot.telegramBot.messages import Messages

        self.telegram.send_message(
            chat_id,
            Messages.REQUEST_DEP_TIME,
            reply_markup=keyboards.time_keyboard(
                keyboards.STEP_MAX_DEP_TIME, include_unlimited=True
            ),
        )

    def _handle_max_dep_time_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle max departure time input."""
        # Allow 2400 as special value
        if text == "2400":
            is_valid = True
        else:
            is_valid, error = InputValidator.validate_time(text)
            if not is_valid:
                self.telegram.send_message(
                    chat_id,
                    error,
                    reply_markup=keyboards.time_keyboard(
                        keyboards.STEP_MAX_DEP_TIME, include_unlimited=True
                    ),
                )
                return

        session.train_info["maxDepTime"] = text
        session.last_action = UserProgress.MAX_DEP_TIME_INPUT_SUCCESS
        self.storage.save_user_session(session)

        from korail_bot.telegramBot.messages import Messages

        self.telegram.send_message(
            chat_id, Messages.REQUEST_TRAIN_TYPE, reply_markup=keyboards.train_type_keyboard()
        )

    def _handle_train_type_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle train type selection."""
        is_valid, error = InputValidator.validate_train_type_choice(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error, reply_markup=keyboards.train_type_keyboard())
            return

        if text == "1":
            session.train_info["trainType"] = "TrainType.KTX"
            session.train_info["trainTypeShow"] = "KTX"
        else:
            session.train_info["trainType"] = "TrainType.ALL"
            session.train_info["trainTypeShow"] = "ALL"

        session.last_action = UserProgress.TRAIN_TYPE_INPUT_SUCCESS
        self.storage.save_user_session(session)

        from korail_bot.telegramBot.messages import Messages

        self.telegram.send_message(
            chat_id, Messages.REQUEST_SEAT_TYPE, reply_markup=keyboards.seat_option_keyboard()
        )

    def _handle_special_option_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle special seat option selection."""
        is_valid, error = InputValidator.validate_special_option_choice(text)

        if not is_valid:
            self.telegram.send_message(
                chat_id, error, reply_markup=keyboards.seat_option_keyboard()
            )
            return

        option_map = {
            "1": (ReserveOption.GENERAL_FIRST, "GENERAL_FIRST"),
            "2": (ReserveOption.GENERAL_ONLY, "GENERAL_ONLY"),
            "3": (ReserveOption.SPECIAL_FIRST, "SPECIAL_FIRST"),
            "4": (ReserveOption.SPECIAL_ONLY, "SPECIAL_ONLY"),
        }

        option, option_display = option_map[text]
        session.train_info["specialInfo"] = str(option)
        session.train_info["specialInfoShow"] = option_display

        session.last_action = UserProgress.SPECIAL_INPUT_SUCCESS
        self.storage.save_user_session(session)

        # Ask for passenger count
        from korail_bot.telegramBot.messages import Messages

        self.telegram.send_message(
            chat_id,
            Messages.REQUEST_PASSENGER_COUNT,
            reply_markup=keyboards.passenger_count_keyboard(),
        )

    def _handle_passenger_count_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle passenger count input."""
        # Validate input with enhanced validator
        is_valid, error = InputValidator.validate_passenger_count(text)

        if not is_valid:
            self.telegram.send_message(
                chat_id, error, reply_markup=keyboards.passenger_count_keyboard()
            )
            return

        count = int(text)

        # Save passenger count
        session.train_info["passengerCount"] = count
        session.last_action = UserProgress.PASSENGER_COUNT_INPUT_SUCCESS
        self.storage.save_user_session(session)

        # Ask for seat strategy if more than 1 passenger
        if count > 1:
            from korail_bot.telegramBot.messages import Messages

            self.telegram.send_message(
                chat_id,
                Messages.REQUEST_SEAT_STRATEGY.format(count=count),
                reply_markup=keyboards.seat_strategy_keyboard(),
            )
        else:
            # Single passenger, skip seat strategy
            session.train_info["seatStrategy"] = "consecutive"
            session.last_action = UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
            self.storage.save_user_session(session)
            self._show_train_selection(chat_id, session)

    def _handle_seat_strategy_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle seat strategy selection."""
        # Validate with enhanced validator
        is_valid, error = InputValidator.validate_seat_strategy_choice(text)

        if not is_valid:
            self.telegram.send_message(
                chat_id, error, reply_markup=keyboards.seat_strategy_keyboard()
            )
            return

        strategy = "consecutive" if text == "1" else "random"
        strategy_display = "연속 좌석" if text == "1" else "랜덤 배치"

        session.train_info["seatStrategy"] = strategy
        session.train_info["seatStrategyShow"] = strategy_display
        session.last_action = UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        self.storage.save_user_session(session)

        self._show_train_selection(chat_id, session)

    # ==================== Choosing which trains to watch ====================
    #
    # The search can watch every train in the time window, which is what it
    # always did, or a set the user picked out of that window. Picking is worth
    # having when only one train is any use - a connection to make, a meeting
    # to reach - and costs success rate the rest of the time, so the list says
    # so and watching everything stays one press away.

    #: How many trains a selection list will show. Korail can return well over
    #: this on a busy corridor across a wide window, and a keyboard that long
    #: is unreadable before it is unsendable.
    MAX_TRAIN_OPTIONS = 30

    def _fetch_train_options(self, chat_id: int, session: UserSession) -> list[dict] | None:
        """
        Ask Korail what runs in the chosen window.

        Sold-out trains are included deliberately: a train with seats left
        needs no watching, so the ones worth picking are exactly the ones an
        ordinary search would leave out.

        Returns:
            The trains, oldest first, or None when Korail could not be asked
        """
        info = session.train_info
        credentials = session.credentials
        if not credentials or not credentials.korail_id or not credentials.korail_pw:
            logger.warning(f"No credentials to list trains with for chat_id={chat_id}")
            return None

        korail = KorailService(
            app_session_start=self.storage.get_or_create_app_session_start(chat_id)
        )
        if not korail.login(credentials.korail_id, credentials.korail_pw):
            logger.warning(f"Could not log in to list trains for chat_id={chat_id}")
            return None

        try:
            trains = korail.search_trains(
                dep_date=info["depDate"],
                src_locate=info["srcLocate"],
                dst_locate=info["dstLocate"],
                dep_time=info["depTime"],
                max_dep_time=info["maxDepTime"],
                train_type=self._parse_train_type(info.get("trainType", "")),
                passenger_count=info.get("passengerCount", 1),
                verbose=False,
                include_no_seats=True,
            )
        except Exception as e:
            # Includes SearchUnavailableError. Whatever went wrong, the user
            # is mid-conversation and needs an answer rather than a traceback.
            logger.error(f"Could not list trains for chat_id={chat_id}: {e}")
            return None

        return [self._describe_train(train) for train in trains]

    @staticmethod
    def _parse_train_type(train_type_str: str):
        """Turn the stored 'TrainType.KTX' back into the enum korail2 wants."""
        from korail2 import TrainType

        return TrainType.KTX if "KTX" in train_type_str.upper() else TrainType.ALL

    @staticmethod
    def _describe_train(train) -> dict:
        """
        Reduce a korail2 train to what the keyboard and the summary need.

        Only strings and booleans: this goes into the session, which is
        serialised to Redis, and a korail2 object would not survive the trip.
        """

        def clock(value: str | None) -> str:
            # Korail sends HHMMSS; the seconds are always zero and never
            # interesting.
            return f"{value[:2]}:{value[2:4]}" if value and len(value) >= 4 else "??:??"

        return {
            "no": str(getattr(train, "train_no", "") or ""),
            "label": (
                f"{clock(getattr(train, 'dep_time', None))}→"
                f"{clock(getattr(train, 'arr_time', None))} "
                f"{getattr(train, 'train_type_name', None) or '열차'}"
            ),
            "soldout": not (hasattr(train, "has_seat") and train.has_seat()),
        }

    def _show_train_selection(self, chat_id: int, session: UserSession) -> None:
        """Fetch the trains for the window and offer them for ticking."""
        options = self._fetch_train_options(chat_id, session)

        from korail_bot.telegramBot.messages import Messages

        if options is None:
            # Korail could not be asked. Watching the whole window needs no
            # list, so the flow carries on there rather than dead-ending on a
            # step whose only purpose is an optional narrowing.
            self.telegram.send_message(chat_id, Messages.TRAIN_LIST_FAILED)
            self._finish_train_selection(chat_id, session, [])
            return

        if not options:
            self.telegram.send_message(chat_id, Messages.TRAIN_LIST_EMPTY)
            self._finish_train_selection(chat_id, session, [])
            return

        truncated = ""
        if len(options) > self.MAX_TRAIN_OPTIONS:
            logger.info(
                f"Showing {self.MAX_TRAIN_OPTIONS} of {len(options)} trains for chat_id={chat_id}"
            )
            truncated = Messages.SELECT_TRAINS_TRUNCATED.format(shown=self.MAX_TRAIN_OPTIONS)
            options = options[: self.MAX_TRAIN_OPTIONS]

        info = session.train_info
        info["trainOptions"] = options
        info["selectedTrains"] = []
        self.storage.save_user_session(session)

        message_id = self.telegram.send_and_get_id(
            chat_id,
            self._train_selection_text(session, len(options), truncated),
            reply_markup=keyboards.train_select_keyboard(options, []),
        )

        # Kept so a tick can rewrite this message instead of sending the whole
        # list again. Without it the chat grows a copy of the list per tick.
        info["trainListMessageId"] = message_id
        self.storage.save_user_session(session)

    @staticmethod
    def _train_selection_text(session: UserSession, count: int, truncated: str) -> str:
        """The prompt above the list of trains."""
        from korail_bot.telegramBot.messages import Messages

        info = session.train_info
        dep_time = info.get("depTime") or ""
        return Messages.SELECT_TRAINS.format(
            srcLocate=info.get("srcLocate", "N/A"),
            dstLocate=info.get("dstLocate", "N/A"),
            depDate=info.get("depDate", "N/A"),
            depTime=dep_time[:4] if dep_time else "N/A",
            maxDepTime=info.get("maxDepTime", "N/A"),
            count=count,
            truncated=truncated,
        )

    def _handle_train_selection_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """
        Tick trains, or finish ticking.

        Everything but the two sentinels is read as train numbers, so a press
        and a typed '101 105' end up in the same place.
        """
        info = session.train_info
        options = info.get("trainOptions") or []
        selected: list[str] = list(info.get("selectedTrains") or [])
        text = text.strip()

        if text == keyboards.TRAIN_SELECT_ALL or text in {"전체", "0"}:
            self._finish_train_selection(chat_id, session, [])
            return

        if text == keyboards.TRAIN_SELECT_DONE:
            # An empty selection here would silently become a whole-window
            # watch, which is a different search from the one the user thinks
            # they asked for. The button only appears once something is
            # ticked, so this is the typed path or a stale press.
            self._finish_train_selection(chat_id, session, selected)
            return

        if text == keyboards.TRAIN_SELECT_REFRESH:
            # Availability moves while the list is on screen; this is how the
            # user sees a train free up without restarting the flow.
            self._show_train_selection(chat_id, session)
            return

        available = {option["no"] for option in options}
        # Splitting on whitespace and commas: '101 105' and '101,105' are the
        # same intent, and a keyboard press is a single number either way.
        requested = [part for part in text.replace(",", " ").split() if part]
        unknown = [number for number in requested if number not in available]

        if not requested or unknown:
            from korail_bot.telegramBot.messages import Messages

            self.telegram.send_message(
                chat_id, Messages.TRAIN_SELECT_UNKNOWN.format(value=", ".join(unknown) or text)
            )
            return

        if len(requested) == 1:
            # One number is a press on that train's row, and a press on a
            # ticked train means untick it.
            number = requested[0]
            if number in selected:
                selected.remove(number)
            else:
                selected.append(number)
        else:
            # A typed list is a statement of what the selection should be,
            # not a series of toggles.
            selected = requested

        info["selectedTrains"] = selected
        self.storage.save_user_session(session)
        self._redraw_train_selection(chat_id, session, options, selected)

    def _redraw_train_selection(
        self, chat_id: int, session: UserSession, options: list[dict], selected: list[str]
    ) -> None:
        """Update the ticks in place, or send a fresh list if that is not possible."""
        message_id = session.train_info.get("trainListMessageId")
        keyboard = keyboards.train_select_keyboard(options, selected)

        if isinstance(message_id, int) and self.telegram.edit_message_reply_markup(
            chat_id, message_id, keyboard
        ):
            return

        # The message is gone, too old to edit, or was never recorded. A new
        # list is worse than an updated one but far better than a tick that
        # appears to do nothing.
        new_id = self.telegram.send_and_get_id(
            chat_id,
            self._train_selection_text(session, len(options), ""),
            reply_markup=keyboard,
        )
        session.train_info["trainListMessageId"] = new_id
        self.storage.save_user_session(session)

    def _finish_train_selection(
        self, chat_id: int, session: UserSession, selected: list[str]
    ) -> None:
        """Record which trains to watch and move on to the summary."""
        info = session.train_info
        info["selectedTrains"] = selected
        # The list has served its purpose, and it is the bulky part of a
        # session that is written to Redis on every step from here on.
        info.pop("trainOptions", None)
        info.pop("trainListMessageId", None)

        session.last_action = UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        self.storage.save_user_session(session)

        self._show_final_confirmation(chat_id, session)

    @staticmethod
    def _describe_watch(session: UserSession) -> str:
        """How the summary and the status line describe the watch."""
        selected = session.train_info.get("selectedTrains") or []
        if not selected:
            return "시간대 전체"
        return f"지정 열차 {len(selected)}개 ({', '.join(selected)}번)"

    def _show_final_confirmation(self, chat_id: int, session: UserSession) -> None:
        """
        Show final confirmation summary.

        Reads train_info defensively, the way _handle_already_processing does.
        A missing key used to raise KeyError out of the update handler, so the
        user got no reply at all and no indication of what to do next - the
        worst possible outcome for a summary screen. A session that reaches
        here without every field has already gone wrong somewhere earlier;
        showing what is known and letting the user answer beats going silent.
        """
        info = session.train_info
        dep_time = info.get("depTime") or ""

        from korail_bot.telegramBot.messages import Messages

        summary = Messages.CONFIRM_RESERVATION.format(
            depDate=info.get("depDate", "N/A"),
            srcLocate=info.get("srcLocate", "N/A"),
            dstLocate=info.get("dstLocate", "N/A"),
            depTime=dep_time[:4] if dep_time else "N/A",
            maxDepTime=info.get("maxDepTime", "N/A"),
            trainTypeShow=info.get("trainTypeShow", "N/A"),
            specialInfoShow=info.get("specialInfoShow", "N/A"),
            passengerCount=info.get("passengerCount", 1),
            seatStrategy=info.get("seatStrategyShow", "1명"),
            trainWatch=self._describe_watch(session),
        )
        self.telegram.send_message(chat_id, summary, reply_markup=keyboards.confirm_keyboard())

    # ==================== Booking a start time ====================
    #
    # Tickets are not released evenly - holiday booking opens at an announced
    # minute, cancellations bunch up near departure - so starting at a chosen
    # moment beats starting now and grinding. Optional: the summary still
    # offers "start now" first, and this is reached only by asking for it.

    def _show_schedule_prompt(self, chat_id: int, session: UserSession) -> None:
        """Ask when the search should begin."""
        from korail_bot.telegramBot.messages import Messages

        session.last_action = UserProgress.SCHEDULE_INPUT_PENDING
        self.storage.save_user_session(session)

        info = session.train_info
        self.telegram.send_message(
            chat_id,
            Messages.REQUEST_SCHEDULE.format(
                srcLocate=info.get("srcLocate", "N/A"),
                dstLocate=info.get("dstLocate", "N/A"),
                depDate=info.get("depDate", "N/A"),
            ),
            reply_markup=keyboards.schedule_keyboard(),
        )

    @staticmethod
    def parse_start_time(text: str, now: datetime | None = None) -> datetime | None:
        """
        Read a start time out of whatever the user typed or pressed.

        Buttons send the full YYYYMMDDHHMM, so the shorter forms exist for
        typing. A bare time means the next time the clock reads that - today
        if it is still to come, tomorrow otherwise - which is what someone
        typing "0700" at midnight means and what they mean at noon too.

        Args:
            text: The answer, as typed or as carried by a button
            now: The moment to resolve relative forms against

        Returns:
            The moment, or None when it could not be read
        """
        now = now or datetime.now()
        digits = text.replace(":", "").replace("-", "").replace("/", "").strip()
        parts = digits.split()
        digits = "".join(parts)

        if not digits.isdigit():
            return None

        try:
            if len(digits) == 4:  # HHMM
                candidate = now.replace(
                    hour=int(digits[:2]), minute=int(digits[2:]), second=0, microsecond=0
                )
                return candidate if candidate > now else candidate + timedelta(days=1)

            if len(digits) == 8:  # MMDDHHMM
                candidate = datetime(
                    now.year, int(digits[:2]), int(digits[2:4]), int(digits[4:6]), int(digits[6:])
                )
                # A date that has gone by means next year: nobody books a
                # search for a train that left in January by typing "0105".
                return candidate if candidate > now else candidate.replace(year=now.year + 1)

            if len(digits) == 12:  # YYYYMMDDHHMM
                return datetime.strptime(digits, "%Y%m%d%H%M")
        except ValueError:
            return None

        return None

    def _handle_schedule_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Book the search for the time the user gave, or say why not."""
        from korail_bot.telegramBot.messages import Messages

        text = text.strip()

        if text == keyboards.SCHEDULE_BACK:
            session.last_action = UserProgress.TRAIN_SELECT_INPUT_SUCCESS
            self.storage.save_user_session(session)
            self._show_final_confirmation(chat_id, session)
            return

        start_at = self.parse_start_time(text)
        if start_at is None:
            self.telegram.send_message(
                chat_id,
                Messages.SCHEDULE_UNPARSEABLE.format(value=text),
                reply_markup=keyboards.schedule_keyboard(),
            )
            return

        self._schedule_reservation(chat_id, session, start_at)

    def _schedule_reservation(self, chat_id: int, session: UserSession, start_at: datetime) -> None:
        """Store the search against its start time and step out of the conversation."""
        from korail_bot.services.scheduled_search_service import ScheduleError
        from korail_bot.telegramBot.messages import Messages

        credentials = session.credentials
        if not credentials or not credentials.korail_id or not credentials.korail_pw:
            # Nothing to log in with when the moment comes, so there is no
            # point storing a schedule. Starting now would still work - the
            # search process is handed the password directly - which is why
            # this is refused here rather than at the summary.
            logger.warning(f"Cannot schedule a search for chat_id={chat_id}: no credentials")
            self.telegram.send_message(chat_id, Messages.SCHEDULE_NO_CREDENTIALS)
            return

        search_params = self._build_search_params(session)
        scheduler = self._scheduler()

        try:
            scheduler.validate_start_time(start_at, search_params)
        except ScheduleError as e:
            # Every one of these has something specific to say, and the user
            # is still on the step, so the keyboard goes back with it.
            self.telegram.send_message(chat_id, str(e), reply_markup=keyboards.schedule_keyboard())
            return

        try:
            scheduler.schedule(
                chat_id=chat_id,
                username=credentials.korail_id,
                password=credentials.korail_pw,
                search_params=search_params,
                start_at=start_at,
            )
        except Exception as e:
            logger.error(f"Failed to schedule a search for chat_id={chat_id}: {e}", exc_info=True)
            self.telegram.send_message(chat_id, Messages.ERROR_RESERVATION_START_FAILED)
            return

        info = session.train_info
        dep_time = info.get("depTime") or ""
        self.telegram.send_message(
            chat_id,
            Messages.SCHEDULE_CONFIRMED.format(
                startAt=f"{start_at:%m월 %d일 %H:%M}",
                srcLocate=info.get("srcLocate", "N/A"),
                dstLocate=info.get("dstLocate", "N/A"),
                depDate=info.get("depDate", "N/A"),
                depTime=dep_time[:4] if dep_time else "N/A",
                maxDepTime=info.get("maxDepTime", "N/A"),
                trainWatch=self._describe_watch(session),
            ),
        )

        # The conversation is over; the schedule now lives in Redis and the
        # password with it. Resetting clears the copy held on the session.
        session.reset()
        self.storage.save_user_session(session)

    def _scheduler(self):
        """
        The scheduling service, built on demand.

        Not held on the handler: the running loop belongs to the application,
        and everything used here reads and writes Redis, so a second instance
        sees exactly the same schedules.
        """
        from korail_bot.services.scheduled_search_service import ScheduledSearchService

        return ScheduledSearchService(self.storage, self.telegram, self.reservation)

    def _build_search_params(self, session: UserSession) -> TrainSearchParams:
        """Collect the answers into the object a search is driven by."""
        info = session.train_info
        return TrainSearchParams(
            dep_date=info["depDate"],
            src_locate=info["srcLocate"],
            dst_locate=info["dstLocate"],
            dep_time=info["depTime"],
            max_dep_time=info["maxDepTime"],
            train_type=info["trainType"],
            train_type_display=info["trainTypeShow"],
            special_option=info["specialInfo"],
            special_option_display=info["specialInfoShow"],
            passenger_count=info.get("passengerCount", 1),
            seat_strategy=info.get("seatStrategy", "consecutive"),
            train_numbers=list(info.get("selectedTrains") or []),
        )

    def _handle_final_confirmation(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle final confirmation before starting reservation."""
        if text.strip() == keyboards.CONFIRM_SCHEDULE:
            self._show_schedule_prompt(chat_id, session)
            return

        is_yes, _error = InputValidator.validate_yes_no(text)

        if is_yes is True:
            # Start reservation process
            self._start_reservation(chat_id, session)
        elif is_yes is False:
            session.reset()
            self.storage.save_user_session(session)
            from korail_bot.telegramBot.messages import Messages

            self.telegram.send_message(chat_id, Messages.CANCELLED_BY_USER)
        else:
            from korail_bot.telegramBot.messages import Messages

            self.telegram.send_message(
                chat_id, Messages.ERROR_CONFIRM_INVALID, reply_markup=keyboards.confirm_keyboard()
            )

    def _start_reservation(self, chat_id: int, session: UserSession) -> None:
        """Start the reservation background process."""
        # Create search params
        search_params = self._build_search_params(session)

        # Update session
        session.last_action = UserProgress.FINDING_TICKET
        self.storage.save_user_session(session)

        # Start reservation
        username = session.credentials.korail_id
        password = session.credentials.korail_pw

        success = self.reservation.start_reservation_process(
            chat_id=chat_id, username=username, password=password, search_params=search_params
        )

        if success:
            # The background process now owns the password; there is no reason
            # to keep a copy at rest for the lifetime of the search.
            session.credentials.korail_pw = ""
            self.storage.save_user_session(session)

        if not success:
            logger.error(f"Failed to start reservation for chat_id={chat_id}")
            session.reset()
            self.storage.save_user_session(session)
            from korail_bot.telegramBot.messages import Messages

            self.telegram.send_message(chat_id, Messages.ERROR_RESERVATION_START_FAILED)

    def _handle_already_processing(self, chat_id: int, session: UserSession) -> None:
        """Handle message when reservation is already in progress."""
        info = session.train_info
        from korail_bot.telegramBot.messages import Messages

        message = Messages.ALREADY_RUNNING.format(
            depDate=info.get("depDate", "N/A"),
            srcLocate=info.get("srcLocate", "N/A"),
            dstLocate=info.get("dstLocate", "N/A"),
            depTime=info.get("depTime", "N/A")[:4] if info.get("depTime") else "N/A",
            trainTypeShow=info.get("trainTypeShow", "N/A"),
            specialInfoShow=info.get("specialInfoShow", "N/A"),
        )
        self.telegram.send_message(chat_id, message)
