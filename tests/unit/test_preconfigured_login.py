"""
Unit tests for logging in with the Korail account kept in the environment.

When USERID/USERPW are set, the two prompts that ask for a phone number and
a password have a known answer, so the conversation skips them. A wrong
password there must not strand the user: the prompts come back.
"""

from unittest.mock import Mock, patch

import pytest

from korail_bot.config.settings import Settings, settings
from korail_bot.handlers.conversation_handler import ConversationHandler
from korail_bot.models import Operator, UserProgress, UserSession
from korail_bot.services import ReservationService, TelegramService
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot.messages import Messages

CHAT_ID = 12345
ENV_USER_ID = "010-1234-5678"
ENV_PASSWORD = "env-korail-pw"
SRT_ENV_USER_ID = "1234567890"
SRT_ENV_PASSWORD = "env-srt-pw"


@pytest.fixture
def session():
    return UserSession(chat_id=CHAT_ID, in_progress=True, last_action=UserProgress.STARTED)


@pytest.fixture
def handler(session):
    storage = Mock(spec=StorageInterface)
    storage.get_user_session.return_value = session
    storage.get_or_create_app_session_start.return_value = 1700000000
    # Nothing registered: these tests are about the account in the
    # environment, and a Mock left to answer this would stand in for a
    # registration that would then be logged in with instead.
    storage.get_onboarded_account.return_value = None
    return ConversationHandler(storage, Mock(spec=TelegramService), Mock(spec=ReservationService))


def _with_env_credentials(user_id=ENV_USER_ID, password=ENV_PASSWORD):
    """Patch the class, not the singleton: the check reads class attributes."""
    return patch.multiple(Settings, KORAIL_ADMIN_USER_ID=user_id, KORAIL_ADMIN_PASSWORD=password)


def _with_srt_env_credentials(user_id=SRT_ENV_USER_ID, password=SRT_ENV_PASSWORD):
    """Patch the fixed SR account independently of the Korail one."""
    return patch.multiple(Settings, SRT_ADMIN_USER_ID=user_id, SRT_ADMIN_PASSWORD=password)


def _korail(login_succeeds: bool):
    """Patch KorailService so no request ever leaves the test."""
    patcher = patch("korail_bot.handlers.conversation_handler.KorailService")
    korail_class = patcher.start()
    korail_class.return_value.login.return_value = login_succeeds
    return patcher, korail_class


def _srt(login_succeeds: bool):
    """Patch SrtService so no request ever leaves the test."""
    patcher = patch("korail_bot.handlers.conversation_handler.SrtService")
    srt_class = patcher.start()
    srt_class.return_value.login.return_value = login_succeeds
    return patcher, srt_class


def _sent(handler):
    """Every message body the handler sent, in order."""
    return [call[0][1] for call in handler.telegram.send_message.call_args_list]


def _begin(handler, operator="korail"):
    """
    Answer the two questions that now come before the login.

    "Y" is met with "which railway?", which did not exist when the account in
    the environment was the only one there could be - and those credentials
    are Korail's, so that is what these answer. The messages produced along
    the way are cleared, so what a test reads afterwards is what the login
    said rather than the question before it.
    """
    handler.handle_message(CHAT_ID, "Y")
    handler.telegram.send_message.reset_mock()
    handler.handle_message(CHAT_ID, operator)


class TestPreconfiguredCredentialDetection:
    """Settings.has_preconfigured_korail_credentials()."""

    def test_both_set(self):
        with _with_env_credentials():
            assert settings.has_preconfigured_korail_credentials() is True

    def test_password_missing(self):
        with _with_env_credentials(password=None):
            assert settings.has_preconfigured_korail_credentials() is False

    def test_user_id_missing(self):
        with _with_env_credentials(user_id=None):
            assert settings.has_preconfigured_korail_credentials() is False

    def test_neither_set(self):
        with _with_env_credentials(user_id=None, password=None):
            assert settings.has_preconfigured_korail_credentials() is False

    def test_srt_pair_is_detected_independently(self):
        with _with_srt_env_credentials():
            assert settings.has_preconfigured_srt_credentials() is True
            assert settings.has_preconfigured_credentials(Operator.SRT) is True
            assert settings.preconfigured_credentials(Operator.SRT) == (
                SRT_ENV_USER_ID,
                SRT_ENV_PASSWORD,
            )

    def test_an_incomplete_srt_pair_is_not_usable(self):
        with _with_srt_env_credentials(password=None):
            assert settings.has_preconfigured_srt_credentials() is False


class TestStartConfirmationWithEnvCredentials:
    """The 'Y' answer at the start of the conversation."""

    def test_login_happens_without_prompting(self, handler, session):
        patcher, korail_class = _korail(login_succeeds=True)
        try:
            with _with_env_credentials():
                _begin(handler)
        finally:
            patcher.stop()

        korail_class.return_value.login.assert_called_once_with(ENV_USER_ID, ENV_PASSWORD)
        assert session.last_action == UserProgress.PW_INPUT_SUCCESS
        assert session.credentials.korail_id == ENV_USER_ID
        assert session.credentials.korail_pw == ENV_PASSWORD

    def test_departure_date_is_asked_next(self, handler):
        patcher, _ = _korail(login_succeeds=True)
        try:
            with _with_env_credentials():
                _begin(handler)
        finally:
            patcher.stop()

        messages = _sent(handler)
        assert len(messages) == 1
        assert "출발 희망일" in messages[0]
        assert "휴대전화번호" not in messages[0]

    def test_account_is_masked_in_the_reply(self, handler):
        patcher, _ = _korail(login_succeeds=True)
        try:
            with _with_env_credentials():
                _begin(handler)
        finally:
            patcher.stop()

        message = _sent(handler)[0]
        assert ENV_USER_ID not in message
        assert "010-****-5678" in message

    def test_unhyphenated_user_id_is_normalised(self, handler, session):
        patcher, korail_class = _korail(login_succeeds=True)
        try:
            with _with_env_credentials(user_id="01012345678"):
                _begin(handler)
        finally:
            patcher.stop()

        # Korail expects the hyphenated form, whichever way .env spells it.
        korail_class.return_value.login.assert_called_once_with("010-1234-5678", ENV_PASSWORD)
        assert session.credentials.korail_id == "010-1234-5678"

    def test_preapproved_users_is_not_consulted(self, handler):
        """No phone number is typed, so there is nothing to filter on."""
        patcher, _ = _korail(login_succeeds=True)
        try:
            with (
                _with_env_credentials(),
                patch.object(Settings, "PREAPPROVED_USERS", ["010-9999-9999"]),
            ):
                _begin(handler)
        finally:
            patcher.stop()

        assert "구독" not in "".join(_sent(handler))

    def test_without_env_credentials_the_phone_is_still_asked(self, handler, session):
        patcher, korail_class = _korail(login_succeeds=True)
        try:
            with _with_env_credentials(user_id=None, password=None):
                _begin(handler)
        finally:
            patcher.stop()

        korail_class.return_value.login.assert_not_called()
        assert session.last_action == UserProgress.START_ACCEPTED
        assert "휴대전화번호" in _sent(handler)[0]

    def test_srt_uses_its_own_fixed_account(self, handler, session):
        patcher, srt_class = _srt(login_succeeds=True)
        try:
            with _with_srt_env_credentials():
                _begin(handler, "srt")
        finally:
            patcher.stop()

        srt_class.return_value.login.assert_called_once_with(SRT_ENV_USER_ID, SRT_ENV_PASSWORD)
        assert session.last_action == UserProgress.PW_INPUT_SUCCESS
        assert session.credentials.korail_id == SRT_ENV_USER_ID
        assert session.credentials.korail_pw == SRT_ENV_PASSWORD
        assert session.train_info["operator"] == "srt"
        assert "SRT 계정" in _sent(handler)[0]

    def test_srt_does_not_reuse_the_korail_fixed_account(self, handler, session):
        patcher, srt_class = _srt(login_succeeds=True)
        try:
            with (
                _with_env_credentials(),
                _with_srt_env_credentials(user_id=None, password=None),
            ):
                _begin(handler, "srt")
        finally:
            patcher.stop()

        srt_class.return_value.login.assert_not_called()
        assert session.last_action == UserProgress.START_ACCEPTED
        assert "휴대전화번호" in _sent(handler)[0]


class TestPreconfiguredLoginFailure:
    """A stale password in .env must not end the conversation."""

    def test_falls_back_to_the_phone_prompt(self, handler, session):
        patcher, _ = _korail(login_succeeds=False)
        try:
            with _with_env_credentials():
                _begin(handler)
        finally:
            patcher.stop()

        assert session.last_action == UserProgress.START_ACCEPTED
        message = _sent(handler)[-1]
        assert message == Messages.PRECONFIGURED_LOGIN_FAILED.format(operator="코레일")
        assert "휴대전화번호" in message

    def test_no_credentials_are_recorded(self, handler, session):
        patcher, _ = _korail(login_succeeds=False)
        try:
            with _with_env_credentials():
                _begin(handler)
        finally:
            patcher.stop()

        assert session.credentials is None

    def test_the_typed_number_still_works_afterwards(self, handler, session):
        patcher, _ = _korail(login_succeeds=False)
        try:
            with _with_env_credentials(), patch.object(Settings, "PREAPPROVED_USERS", []):
                _begin(handler)
                handler.telegram.send_message.reset_mock()
                handler.handle_message(CHAT_ID, "01098765432")
        finally:
            patcher.stop()

        assert session.last_action == UserProgress.ID_INPUT_SUCCESS
        assert session.credentials.korail_id == "010-9876-5432"
        assert "비밀번호" in _sent(handler)[0]


class TestWelcomeMessage:
    """The listed steps have to match what is actually asked."""

    def test_lists_the_login_step_by_default(self):
        assert "코레일 로그인 정보" in Messages.welcome_message()

    def test_drops_the_login_step_when_preconfigured(self):
        message = Messages.welcome_message(skip_login_prompts=True)
        assert "코레일 로그인 정보" not in message
        assert "1. 출발 희망일" in message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
