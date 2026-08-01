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
from korail_bot.services import (
    AccessService,
    KorailService,
    MessageTemplates,
    ReservationService,
    TelegramService,
)
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot import keyboards
from korail_bot.utils.crypto import identity_hash
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
        access_service: AccessService | None = None,
    ):
        """
        Initialize conversation handler.

        Args:
            storage: Storage interface
            telegram_service: Telegram messaging service
            reservation_service: Reservation service
            access_service: Decides who may run a search. Built from the
                storage when not supplied, so callers that do not care about
                access control do not have to construct one.
        """
        self.storage = storage
        self.telegram = telegram_service
        self.reservation = reservation_service
        self.access = access_service or AccessService(storage)

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

        # "뒤로" answers no question, so it is taken before the routing below
        # rather than in a dozen handlers that would each have to remember it.
        if self._wants_to_go_back(text, session):
            self._handle_back(chat_id, session)
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

    def uses_server_account(self, chat_id: int) -> bool:
        """
        Whether this chat logs in with USERID/USERPW instead of its own.

        Only developer chats do. The setting exists so that whoever runs the
        bot can point it at a fixed account for development and testing - not
        so that everyone who finds the bot books with the operator's Korail
        account, which is what it used to mean and which made the bot unsafe
        to share the moment those two values were filled in.

        Everyone else registers their own account, whatever is in the
        environment.
        """
        return settings.has_preconfigured_korail_credentials() and self.storage.is_developer(
            chat_id
        )

    def _handle_start_confirmation(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle initial start confirmation (Y/N)."""
        is_yes, error = InputValidator.validate_yes_no(text)

        if is_yes is True:
            session.last_action = UserProgress.START_ACCEPTED
            self.storage.save_user_session(session)
            # Both prompts that follow have a known answer when a developer
            # chat has been pointed at a fixed account, so skip them.
            if self.uses_server_account(chat_id):
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
        # everything downstream (approvals, logs, masking) compares against it.
        text = InputValidator.normalize_phone_number(text) or text

        # No gate here any more. Whether this number may run a search is
        # decided when one is about to start, because that is what costs
        # something - and refusing at the password prompt would mean telling
        # someone they are not welcome only after they typed a password.

        # Save phone number
        if not session.credentials:
            session.credentials = UserCredentials(korail_id=text, korail_pw="")
        else:
            session.credentials.korail_id = text

        session.last_action = UserProgress.ID_INPUT_SUCCESS
        self.storage.save_user_session(session)
        # A way back to the number as well as out: a typo in it is only
        # discovered from here, when the login fails.
        self.telegram.send_message(
            chat_id,
            MessageTemplates.request_password(),
            reply_markup=keyboards.password_keyboard(),
        )

    def _handle_password_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle password input and login."""
        # Validate password
        is_valid, error = InputValidator.validate_password(text)
        if not is_valid:
            self.telegram.send_message(
                chat_id,
                error + " 다시 입력 바랍니다.",
                reply_markup=keyboards.password_keyboard(),
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
            # Login failed - ask for retry. No button for the retry itself:
            # the answer is a password, and that cannot go on one. What can is
            # the way back to the phone number, which is the other thing that
            # is commonly wrong when a login fails.
            self.telegram.send_message(
                chat_id,
                MessageTemplates.login_failure(username),
                reply_markup=keyboards.password_keyboard(),
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

        # trainType is what the search is driven by; trainTypeShow is only
        # ever read back to the user, on the summary and in /status. It used
        # to carry "ALL", which names the korail2 constant rather than the
        # choice - and left the summary saying nothing about the 무궁화호 the
        # search had just been allowed to book.
        if text == "1":
            session.train_info["trainType"] = "TrainType.KTX"
            session.train_info["trainTypeShow"] = "KTX 계열만"
        else:
            session.train_info["trainType"] = "TrainType.ALL"
            session.train_info["trainTypeShow"] = "모든 열차 (무궁화호 포함)"

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

        # Ticks survive the list being fetched again - by the refresh button,
        # or by coming back to it from the summary. Losing them would mean a
        # refresh silently undoing the work it was pressed to preserve. A
        # train that has stopped running drops out with its row.
        available = {option["no"] for option in options}
        selected = [number for number in (info.get("selectedTrains") or []) if number in available]

        info["trainOptions"] = options
        info["selectedTrains"] = selected
        self.storage.save_user_session(session)

        message_id = self.telegram.send_and_get_id(
            chat_id,
            self._train_selection_text(session, len(options), truncated),
            reply_markup=keyboards.train_select_keyboard(options, selected),
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
        self._close_train_list(chat_id, session)
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

    # ==================== Going back a step ====================
    #
    # The flow asks eleven questions in a row, and answering one of them wrongly
    # used to cost all eleven: there was no way back, so the only remedy was
    # /cancel and typing the lot again. Every question that has one behind it
    # now offers a way to it.
    #
    # Nothing is unwound on the way back. Each step writes its own field before
    # it advances, so the answer being re-asked is overwritten before anything
    # reads it, and the ones further along are re-asked in turn on the way
    # forward. Clearing them here would only make a half-walked flow harder to
    # reason about.

    #: Where "뒤로" leads, keyed by the progress the session sits at while the
    #: question is on screen. Absent means the question has nothing behind it.
    BACK_TARGETS = {  # noqa: RUF012 - a constant table, not per-instance state
        # The password prompt: back to the phone number, which is where a
        # mistyped one has to be fixed.
        UserProgress.ID_INPUT_SUCCESS: UserProgress.START_ACCEPTED,
        UserProgress.DATE_INPUT_SUCCESS: UserProgress.PW_INPUT_SUCCESS,
        UserProgress.SRC_LOCATE_INPUT_SUCCESS: UserProgress.DATE_INPUT_SUCCESS,
        UserProgress.DST_LOCATE_INPUT_SUCCESS: UserProgress.SRC_LOCATE_INPUT_SUCCESS,
        UserProgress.DEP_TIME_INPUT_SUCCESS: UserProgress.DST_LOCATE_INPUT_SUCCESS,
        UserProgress.MAX_DEP_TIME_INPUT_SUCCESS: UserProgress.DEP_TIME_INPUT_SUCCESS,
        UserProgress.TRAIN_TYPE_INPUT_SUCCESS: UserProgress.MAX_DEP_TIME_INPUT_SUCCESS,
        UserProgress.SPECIAL_INPUT_SUCCESS: UserProgress.TRAIN_TYPE_INPUT_SUCCESS,
        UserProgress.PASSENGER_COUNT_INPUT_SUCCESS: UserProgress.SPECIAL_INPUT_SUCCESS,
        UserProgress.SEAT_STRATEGY_INPUT_SUCCESS: UserProgress.PASSENGER_COUNT_INPUT_SUCCESS,
        UserProgress.TRAIN_SELECT_INPUT_SUCCESS: UserProgress.SEAT_STRATEGY_INPUT_SUCCESS,
        UserProgress.SCHEDULE_INPUT_PENDING: UserProgress.TRAIN_SELECT_INPUT_SUCCESS,
    }

    @staticmethod
    def _wants_to_go_back(text: str, session: UserSession) -> bool:
        """
        Whether this is a request for the previous question.

        The sentinel is what the button carries. The typed word is taken too,
        because someone who has been pressing "◀️ 뒤로" will eventually type
        it - with one exception. At the password prompt anything typed is a
        password, and reading one as a command would walk the user back a step
        instead of logging them in.
        """
        text = text.strip()
        if session.last_action == UserProgress.ID_INPUT_SUCCESS:
            return text == keyboards.BACK
        return text in (keyboards.BACK, "뒤로")

    def _handle_back(self, chat_id: int, session: UserSession) -> None:
        """Put the question before the one on screen back up."""
        from korail_bot.telegramBot.messages import Messages

        here = session.last_action
        target = self.BACK_TARGETS.get(here)

        # A single passenger is never asked how the seats should be arranged,
        # so going back past the train list has to skip the question that was
        # skipped on the way in. Landing on one the user has never seen would
        # make "뒤로" look like it had gone somewhere at random.
        if (
            target == UserProgress.PASSENGER_COUNT_INPUT_SUCCESS
            and (session.train_info.get("passengerCount") or 1) <= 1
        ):
            target = UserProgress.SPECIAL_INPUT_SUCCESS

        if target is None:
            # The first question of the flow, or a state with no question
            # behind it at all. Saying so beats a button that does nothing.
            self.telegram.send_message(chat_id, Messages.BACK_AT_THE_START)
            return

        if here == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS:
            # Leaving the train list, whose keyboard the router deliberately
            # leaves alone while it is being ticked.
            self._close_train_list(chat_id, session)

        session.last_action = target
        self.storage.save_user_session(session)
        self._reask(chat_id, session, target)

    def _reask(self, chat_id: int, session: UserSession, progress: int) -> None:
        """
        Ask the question belonging to a progress state.

        Only ever called on the way back, so it says so. The prompts the flow
        uses going forward all open with "✅ … 입력 완료", which is the wrong
        thing to tell someone who just threw that answer away.
        """
        from korail_bot.telegramBot.messages import Messages

        # These two build their message out of every answer so far and know
        # how to send it, so going back to them is just drawing them again.
        if progress == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS:
            self._show_train_selection(chat_id, session)
            return
        if progress == UserProgress.TRAIN_SELECT_INPUT_SUCCESS:
            self._show_final_confirmation(chat_id, session)
            return

        info = session.train_info
        prompts = {
            UserProgress.START_ACCEPTED: (
                Messages.BACK_TO_PHONE,
                keyboards.cancel_only_keyboard(),
            ),
            UserProgress.PW_INPUT_SUCCESS: (
                Messages.BACK_TO_DATE,
                keyboards.date_keyboard(),
            ),
            UserProgress.DATE_INPUT_SUCCESS: (
                Messages.BACK_TO_SRC_STATION,
                keyboards.station_keyboard(keyboards.STEP_SRC_STATION),
            ),
            UserProgress.SRC_LOCATE_INPUT_SUCCESS: (
                Messages.BACK_TO_DST_STATION,
                keyboards.station_keyboard(
                    keyboards.STEP_DST_STATION, exclude=info.get("srcLocate")
                ),
            ),
            UserProgress.DST_LOCATE_INPUT_SUCCESS: (
                Messages.BACK_TO_DEP_TIME,
                keyboards.time_keyboard(keyboards.STEP_DEP_TIME),
            ),
            UserProgress.DEP_TIME_INPUT_SUCCESS: (
                Messages.BACK_TO_MAX_DEP_TIME,
                keyboards.time_keyboard(keyboards.STEP_MAX_DEP_TIME, include_unlimited=True),
            ),
            UserProgress.MAX_DEP_TIME_INPUT_SUCCESS: (
                Messages.BACK_TO_TRAIN_TYPE,
                keyboards.train_type_keyboard(),
            ),
            UserProgress.TRAIN_TYPE_INPUT_SUCCESS: (
                Messages.BACK_TO_SEAT_OPTION,
                keyboards.seat_option_keyboard(),
            ),
            UserProgress.SPECIAL_INPUT_SUCCESS: (
                Messages.BACK_TO_PASSENGER_COUNT,
                keyboards.passenger_count_keyboard(),
            ),
            UserProgress.PASSENGER_COUNT_INPUT_SUCCESS: (
                Messages.BACK_TO_SEAT_STRATEGY.format(count=info.get("passengerCount", 1)),
                keyboards.seat_strategy_keyboard(),
            ),
        }

        text, keyboard = prompts[progress]
        self.telegram.send_message(chat_id, text, reply_markup=keyboard)

    def _close_train_list(self, chat_id: int, session: UserSession) -> None:
        """
        Take the buttons off the list of trains.

        The router leaves this one keyboard alone while the list is on screen -
        that is what makes ticking repeatable - so leaving the list is the only
        moment it can be cleared. Cosmetic, and contained accordingly: a
        failure here must not cost the user the step they asked for.
        """
        message_id = session.train_info.get("trainListMessageId")
        if not isinstance(message_id, int):
            return

        try:
            self.telegram.edit_message_reply_markup(chat_id, message_id, keyboards.empty_keyboard())
        except Exception as e:
            logger.warning(f"Could not close the train list for chat_id={chat_id}: {e}")

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

        # "뒤로" never reaches here: it is taken in handle_message, the same
        # way it is on every other step.
        text = text.strip()

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
        username = session.credentials.korail_id
        password = session.credentials.korail_pw

        # The gate goes here rather than earlier: what costs the operator is a
        # process asking Korail for seats every few seconds, and this is where
        # one begins. Asking questions is free, and charging someone for a
        # summary screen they backed out of would be indefensible.
        decision = self.access.evaluate(username, is_developer=self._is_developer(chat_id))
        if not decision.allowed:
            self._offer_access_request(chat_id, session, username, decision)
            return

        # Create search params
        search_params = self._build_search_params(session)

        # Update session
        session.last_action = UserProgress.FINDING_TICKET
        self.storage.save_user_session(session)

        success = self.reservation.start_reservation_process(
            chat_id=chat_id, username=username, password=password, search_params=search_params
        )

        if success:
            # Charged only once the search is really running. A refusal - the
            # duplicate guard, a process that died on startup - must not cost
            # an allowance for a search that never happened.
            self.access.consume(username, decision)
            if decision.counts_against_trial and decision.limit >= 0:
                self.telegram.send_message(
                    chat_id,
                    MessageTemplates.TRIAL_REMAINING.format(
                        used=decision.used + 1,
                        limit=decision.limit,
                    ),
                )
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

    def _is_developer(self, chat_id: int) -> bool:
        """Whether this chat is in developer mode, and so has no limits."""
        return self.storage.is_developer(chat_id)

    def _offer_access_request(
        self, chat_id: int, session: UserSession, username: str, decision
    ) -> None:
        """
        Tell the user their trial is over, and offer to ask the operator.

        The dead end is the thing to avoid here. Someone who tried the bot,
        liked it, and hit the wall should be one button away from being let
        in - not reading an instruction to contact a stranger by some means
        the bot never mentions.
        """
        session.reset()
        self.storage.save_user_session(session)

        already_pending = bool(self.storage.get_access_request(identity_hash(username)))
        self.telegram.send_message(
            chat_id,
            MessageTemplates.TRIAL_EXHAUSTED.format(used=decision.used, limit=decision.limit),
            reply_markup=keyboards.access_request_keyboard(pending=already_pending),
        )

    def request_access(self, chat_id: int) -> None:
        """
        Act on the 'ask the operator' button.

        The number comes from the registered account rather than the session,
        which has been reset by the time this is pressed - and which is the
        right source anyway: it is the Korail account being asked about.
        """
        account = self.storage.get_onboarded_account(chat_id)
        if not account:
            self.telegram.send_message(chat_id, MessageTemplates.ACCESS_REQUEST_NO_ACCOUNT)
            return

        request = self.access.request_access(chat_id, account.korail_id)
        if not request:
            self.telegram.send_message(chat_id, MessageTemplates.ACCESS_REQUEST_ALREADY)
            return

        self.telegram.send_message(chat_id, MessageTemplates.ACCESS_REQUEST_SENT)
        self._notify_operators(request)

    def _notify_operators(self, request) -> None:
        """
        Tell every developer chat that someone is waiting.

        All of them rather than one: an operator who is asleep should not be
        the reason a request sits unanswered, and there is no way to tell from
        here which of them is awake.
        """
        operators = self.storage.get_all_developers()
        if not operators:
            logger.warning(
                f"Access request from {request.masked_phone} has nobody to notify - "
                f"no chat is in developer mode"
            )
            return

        self.telegram.send_to_multiple(
            operators,
            MessageTemplates.ACCESS_REQUEST_NOTICE.format(
                maskedPhone=request.masked_phone,
                requestedAt=f"{request.requested_at:%m월 %d일 %H:%M}",
            ),
        )

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
