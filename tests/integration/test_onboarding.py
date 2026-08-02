"""
Integration tests for registering a Korail account once and reusing it.

The bot used to ask for a phone number and a password before every single
booking, and threw both away when the booking ended. That is safe and it is
also why nobody but the operator ever used one of these bots: handing your
Korail password to a chat window is a thing people will do once, not once a
week.

Registering moves the password from "present during one booking" to "present
until it expires or the user leaves", so these tests care as much about the
ways it is destroyed as the ways it is stored. Run against a real Redis so
the encryption round trip is exercised rather than asserted about a Mock.
"""

from unittest.mock import Mock, patch

import pytest

from korail_bot.config.settings import Settings
from korail_bot.handlers.command_handler import CommandHandler
from korail_bot.handlers.conversation_handler import ConversationHandler
from korail_bot.handlers.update_processor import TelegramUpdateProcessor
from korail_bot.models import OnboardedAccount, UserProgress, UserSession
from korail_bot.services import PaymentReminderService, ReservationService, TelegramService

CHAT_ID = 774411
PHONE = "010-1234-5678"
PASSWORD = "korail-password"


@pytest.fixture
def telegram():
    return Mock(spec=TelegramService)


@pytest.fixture
def conversation(storage, telegram):
    return ConversationHandler(storage, telegram, Mock(spec=ReservationService))


@pytest.fixture
def commands(storage, telegram, conversation):
    return CommandHandler(
        storage,
        telegram,
        Mock(spec=ReservationService),
        Mock(spec=PaymentReminderService),
        conversation_handler=conversation,
    )


def _operator_account():
    """
    USERID/USERPW set, the way a server-wide account is configured.

    Patched on the class, not the instance: the check that reads them is a
    classmethod and would look straight past an instance attribute.
    """
    return patch.multiple(
        Settings, KORAIL_ADMIN_USER_ID="010-0000-0000", KORAIL_ADMIN_PASSWORD="operator-pw"
    )


def _login(succeeds: bool):
    """Patch the Korail login the handlers perform."""
    korail = Mock()
    korail.login.return_value = succeeds
    return patch("korail_bot.handlers.conversation_handler.KorailService", return_value=korail)


def _register(storage, chat_id=CHAT_ID, phone=PHONE, password=PASSWORD):
    """Put an account in storage as a completed registration would."""
    storage.save_onboarded_account(
        OnboardedAccount(chat_id=chat_id, korail_id=phone, korail_pw=password)
    )


def _texts(telegram):
    """Every message body sent so far."""
    return [
        call.args[1] if len(call.args) > 1 else call.kwargs.get("text", "")
        for call in telegram.send_message.call_args_list
    ]


class TestRegisteringAnAccount:
    """A successful login is what registers the account."""

    def test_a_successful_login_is_stored(self, conversation, storage):
        session = UserSession(
            chat_id=CHAT_ID, in_progress=True, last_action=UserProgress.ID_INPUT_SUCCESS
        )
        session.credentials = Mock(korail_id=PHONE, korail_pw="")
        storage.save_user_session(session)

        with _login(True):
            conversation._handle_password_input(CHAT_ID, PASSWORD, session)

        account = storage.get_onboarded_account(CHAT_ID)
        assert account is not None
        assert account.korail_id == PHONE
        assert account.korail_pw == PASSWORD

    def test_a_failed_login_is_not_stored(self, conversation, storage):
        session = UserSession(
            chat_id=CHAT_ID, in_progress=True, last_action=UserProgress.ID_INPUT_SUCCESS
        )
        session.credentials = Mock(korail_id=PHONE, korail_pw="")
        storage.save_user_session(session)

        with _login(False):
            conversation._handle_password_input(CHAT_ID, "wrong-password", session)

        assert storage.get_onboarded_account(CHAT_ID) is None

    def test_the_password_is_not_readable_in_redis(self, storage):
        """
        The point of encrypting at rest. Anyone who can read the database
        should not thereby be able to log in to Korail as this user.
        """
        _register(storage)

        raw = storage.redis.get(f"user_credentials:{CHAT_ID}")
        assert raw is not None
        assert PASSWORD not in str(raw)
        assert PHONE not in str(raw)

    def test_it_survives_the_round_trip(self, storage):
        _register(storage)

        account = storage.get_onboarded_account(CHAT_ID)
        assert account.korail_id == PHONE
        assert account.korail_pw == PASSWORD
        assert account.onboarded_at is not None


class TestStartingWithARegisteredAccount:
    """What registering buys: /start stops asking."""

    def test_the_login_questions_are_skipped(self, commands, storage, telegram):
        _register(storage)

        with _login(True):
            commands.handle_start(CHAT_ID)

        session = storage.get_user_session(CHAT_ID)
        assert session.last_action == UserProgress.PW_INPUT_SUCCESS
        assert "전화번호" not in " ".join(_texts(telegram))

    def test_the_session_carries_the_stored_login(self, commands, storage):
        _register(storage)

        with _login(True):
            commands.handle_start(CHAT_ID)

        session = storage.get_user_session(CHAT_ID)
        assert session.credentials.korail_id == PHONE
        assert session.credentials.korail_pw == PASSWORD

    def test_the_number_is_masked_in_the_greeting(self, commands, storage, telegram):
        """The chat transcript outlives the booking; the number should not."""
        _register(storage)

        with _login(True):
            commands.handle_start(CHAT_ID)

        sent = " ".join(_texts(telegram))
        assert PHONE not in sent
        assert "5678" in sent

    def test_a_chat_with_nothing_registered_is_asked_to_register(self, commands, storage, telegram):
        commands.handle_start(CHAT_ID)

        assert "계정 등록" in " ".join(_texts(telegram))
        assert storage.get_user_session(CHAT_ID).last_action == UserProgress.STARTED

    def test_an_ordinary_chat_ignores_the_server_account(self, commands, storage, telegram):
        """
        USERID/USERPW is for development, not for making everyone book with
        the operator's Korail account. An ordinary chat uses what it
        registered, whatever is in the environment.
        """
        _register(storage)

        with _operator_account(), _login(True):
            commands.handle_start(CHAT_ID)

        session = storage.get_user_session(CHAT_ID)
        assert session.last_action == UserProgress.PW_INPUT_SUCCESS
        assert session.credentials.korail_id == PHONE
        assert "서버에 설정" not in " ".join(_texts(telegram))

    def test_a_developer_chat_uses_the_server_account(self, commands, storage, telegram):
        _register(storage)
        storage.set_developer(CHAT_ID, True)

        with _operator_account():
            commands.handle_start(CHAT_ID)

        # The preconfigured welcome, and the registration is left unused.
        assert "서버에 설정" in " ".join(_texts(telegram))
        assert storage.get_user_session(CHAT_ID).last_action == UserProgress.STARTED


class TestAStoredLoginThatStoppedWorking:
    """People change their Korail password without telling the bot."""

    def test_the_dead_registration_is_dropped(self, commands, storage):
        _register(storage)

        with _login(False):
            commands.handle_start(CHAT_ID)

        assert storage.get_onboarded_account(CHAT_ID) is None

    def test_the_user_is_asked_to_register_again(self, commands, storage, telegram):
        _register(storage)

        with _login(False):
            commands.handle_start(CHAT_ID)

        sent = " ".join(_texts(telegram))
        assert "로그인하지 못했습니다" in sent
        assert storage.get_user_session(CHAT_ID).last_action == UserProgress.STARTED


class TestOnboardingCommand:
    """/onboarding is how someone re-registers on purpose."""

    def test_it_starts_registration_when_nothing_is_stored(self, commands, storage, telegram):
        commands.handle_onboarding(CHAT_ID)

        assert "계정 등록" in " ".join(_texts(telegram))
        assert storage.get_user_session(CHAT_ID).last_action == UserProgress.STARTED

    def test_it_confirms_before_replacing_an_account(
        self, commands, conversation, storage, telegram
    ):
        """
        Asked once the railway is known, not before. Whether there is a
        registration to replace depends on which one - a chat may be
        registered with Korail and not with SR.
        """
        _register(storage)

        commands.handle_onboarding(CHAT_ID)
        conversation.handle_message(CHAT_ID, "Y")
        conversation.handle_message(CHAT_ID, "korail")

        assert "이미 등록된" in " ".join(_texts(telegram))
        assert (
            storage.get_user_session(CHAT_ID).last_action
            == UserProgress.ONBOARDING_OVERWRITE_PENDING
        )
        # Still there: nothing is destroyed before the user answers.
        assert storage.get_onboarded_account(CHAT_ID) is not None

    def test_registering_with_one_railway_leaves_the_other_alone(
        self, commands, conversation, storage, telegram
    ):
        """
        The whole reason the question moved. A Korail registration is not a
        reason to stop someone registering with SR - and must survive them
        doing it.
        """
        _register(storage)

        commands.handle_onboarding(CHAT_ID)
        conversation.handle_message(CHAT_ID, "Y")
        conversation.handle_message(CHAT_ID, "srt")

        # No registration with SR yet, so nothing to confirm - straight to the
        # number - and the Korail one is untouched.
        assert "이미 등록된" not in " ".join(_texts(telegram))
        assert "휴대전화번호" in " ".join(_texts(telegram))
        assert storage.get_onboarded_account(CHAT_ID) is not None

    def test_confirming_drops_the_old_account_and_asks_for_a_number(
        self, commands, conversation, storage, telegram
    ):
        _register(storage)
        commands.handle_onboarding(CHAT_ID)

        session = storage.get_user_session(CHAT_ID)
        conversation._handle_onboarding_overwrite(CHAT_ID, "Y", session)

        assert storage.get_onboarded_account(CHAT_ID) is None
        assert storage.get_user_session(CHAT_ID).last_action == UserProgress.START_ACCEPTED

    def test_declining_keeps_the_old_account(self, commands, conversation, storage):
        _register(storage)
        commands.handle_onboarding(CHAT_ID)

        session = storage.get_user_session(CHAT_ID)
        conversation._handle_onboarding_overwrite(CHAT_ID, "N", session)

        assert storage.get_onboarded_account(CHAT_ID) is not None

    def test_a_developer_chat_is_told_registration_is_pointless_for_that_railway(
        self, commands, conversation, storage, telegram
    ):
        storage.set_developer(CHAT_ID, True)

        with _operator_account():
            commands.handle_onboarding(CHAT_ID)
            conversation.handle_message(CHAT_ID, "Y")
            conversation.handle_message(CHAT_ID, "korail")

        assert "서버에 설정된 코레일 계정" in " ".join(_texts(telegram))

    def test_an_ordinary_chat_registers_even_with_a_server_account(
        self, commands, storage, telegram
    ):
        with _operator_account():
            commands.handle_onboarding(CHAT_ID)

        assert "계정 등록" in " ".join(_texts(telegram))


class TestLogout:
    """Leaving has to be as easy as arriving."""

    def test_it_deletes_the_account(self, commands, storage):
        _register(storage)

        commands.handle_logout(CHAT_ID)

        assert storage.get_onboarded_account(CHAT_ID) is None

    def test_it_says_so_when_there_was_nothing(self, commands, telegram):
        commands.handle_logout(CHAT_ID)

        assert "등록된 코레일 계정이 없습니다" in " ".join(_texts(telegram))


class TestLeavingTheBot:
    """
    Blocking the bot is the clearest statement a user can make that they are
    done with it, and Telegram gives exactly one notice of it.
    """

    @pytest.fixture
    def processor(self, storage, telegram):
        return TelegramUpdateProcessor(
            storage, telegram, Mock(spec=ReservationService), Mock(spec=PaymentReminderService)
        )

    def _update(self, status):
        return {
            "update_id": 1,
            "my_chat_member": {
                "chat": {"id": CHAT_ID},
                "new_chat_member": {"status": status},
            },
        }

    @pytest.mark.parametrize("status", ["kicked", "left"])
    def test_blocking_or_leaving_drops_the_account(self, processor, storage, status):
        _register(storage)

        processor.process(self._update(status))

        assert storage.get_onboarded_account(CHAT_ID) is None

    def test_coming_back_leaves_the_account_alone(self, processor, storage):
        _register(storage)

        processor.process(self._update("member"))

        assert storage.get_onboarded_account(CHAT_ID) is not None

    def test_a_malformed_update_is_survived(self, processor):
        """process() must never raise: the poller has to read the next update."""
        processor.process({"update_id": 1, "my_chat_member": {"chat": {}}})
