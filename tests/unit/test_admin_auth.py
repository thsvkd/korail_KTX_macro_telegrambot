"""
Unit tests for admin authentication.

The admin password is guessable over Telegram, so it is rate limited,
compared in constant time, and kept separate from the Korail password.
"""
from unittest.mock import Mock, patch

import pytest

from config.settings import settings
from handlers.command_handler import CommandHandler
from storage.base import StorageInterface


ADMIN_PASSWORD = "correct-admin-password"


@pytest.fixture
def handler():
    storage = Mock(spec=StorageInterface)
    storage.get_admin_auth_failures.return_value = 0
    storage.get_admin_lockout_remaining.return_value = 900
    storage.get_pending_admin_command.return_value = None
    storage.is_admin_authenticated.return_value = False

    return CommandHandler(storage, Mock(), Mock(), Mock())


def _sent_messages(handler):
    return [call[0][1] for call in handler.telegram.send_message.call_args_list]


class TestAdminPassword:
    """Password checking."""

    def test_correct_password_authenticates(self, handler):
        with patch.object(settings, 'ADMIN_PASSWORD', ADMIN_PASSWORD):
            assert handler.handle_admin_password(1, ADMIN_PASSWORD) is True

        handler.storage.set_admin_authenticated.assert_called_once_with(1, True)
        handler.storage.clear_admin_auth_failures.assert_called_once_with(1)

    def test_wrong_password_is_counted(self, handler):
        handler.storage.register_admin_auth_failure.return_value = 1

        with patch.object(settings, 'ADMIN_PASSWORD', ADMIN_PASSWORD):
            assert handler.handle_admin_password(1, "guess") is False

        handler.storage.register_admin_auth_failure.assert_called_once_with(1)
        handler.storage.set_admin_authenticated.assert_not_called()

    def test_non_ascii_password_attempt_does_not_raise(self, handler):
        """Users type Korean into this prompt; that must not blow up."""
        handler.storage.register_admin_auth_failure.return_value = 1

        with patch.object(settings, 'ADMIN_PASSWORD', ADMIN_PASSWORD):
            assert handler.handle_admin_password(1, "비밀번호") is False

        handler.storage.register_admin_auth_failure.assert_called_once_with(1)

    def test_non_ascii_password_can_be_correct(self, handler):
        with patch.object(settings, 'ADMIN_PASSWORD', "관리자비밀번호"):
            assert handler.handle_admin_password(1, "관리자비밀번호") is True

    def test_remaining_attempts_are_reported(self, handler):
        handler.storage.register_admin_auth_failure.return_value = 2

        with patch.object(settings, 'ADMIN_PASSWORD', ADMIN_PASSWORD):
            handler.handle_admin_password(1, "guess")

        expected_remaining = settings.ADMIN_MAX_AUTH_FAILURES - 2
        assert any(str(expected_remaining) in m for m in _sent_messages(handler))


class TestLockout:
    """Repeated failures stop the guessing channel."""

    def test_locked_out_attempt_is_refused_without_checking(self, handler):
        handler.storage.get_admin_auth_failures.return_value = \
            settings.ADMIN_MAX_AUTH_FAILURES

        with patch.object(settings, 'ADMIN_PASSWORD', ADMIN_PASSWORD):
            # Even the correct password is refused while locked out.
            assert handler.handle_admin_password(1, ADMIN_PASSWORD) is False

        handler.storage.set_admin_authenticated.assert_not_called()

    def test_locked_out_command_does_not_prompt(self, handler):
        handler.storage.get_admin_auth_failures.return_value = \
            settings.ADMIN_MAX_AUTH_FAILURES
        target = Mock()

        with patch.object(settings, 'ADMIN_PASSWORD', ADMIN_PASSWORD):
            handler._handle_admin_command(1, target, "/flushredis")

        target.assert_not_called()
        handler.storage.set_waiting_for_admin_password.assert_not_called()

    def test_lockout_message_reports_remaining_minutes(self, handler):
        handler.storage.get_admin_auth_failures.return_value = \
            settings.ADMIN_MAX_AUTH_FAILURES
        handler.storage.get_admin_lockout_remaining.return_value = 300

        with patch.object(settings, 'ADMIN_PASSWORD', ADMIN_PASSWORD):
            handler.handle_admin_password(1, "guess")

        assert any("5분" in m for m in _sent_messages(handler))


class TestAdminDisabled:
    """Without ADMIN_PASSWORD there is no admin surface."""

    def test_command_refused(self, handler):
        target = Mock()

        with patch.object(settings, 'ADMIN_PASSWORD', None):
            handler._handle_admin_command(1, target, "/broadcast")

        target.assert_not_called()
        handler.storage.set_waiting_for_admin_password.assert_not_called()

    def test_password_attempt_refused(self, handler):
        with patch.object(settings, 'ADMIN_PASSWORD', None):
            assert handler.handle_admin_password(1, "anything") is False

        handler.storage.set_admin_authenticated.assert_not_called()

    def test_authenticated_command_runs(self, handler):
        handler.storage.is_admin_authenticated.return_value = True
        target = Mock()

        with patch.object(settings, 'ADMIN_PASSWORD', ADMIN_PASSWORD):
            handler._handle_admin_command(1, target, "/allusers")

        target.assert_called_once_with(1)


class TestAdminSeparation:
    """The admin password must not be derived from the Korail password."""

    def test_admin_password_is_not_userpw(self):
        import os

        with patch.dict(os.environ, {"USERPW": "korail-pw", "ADMIN_PASSWORD": "admin-pw"}):
            # Reload so the class picks the patched environment up.
            import importlib

            import config.settings as settings_module
            importlib.reload(settings_module)

            assert settings_module.settings.ADMIN_PASSWORD == "admin-pw"
            assert settings_module.settings.KORAIL_ADMIN_PASSWORD == "korail-pw"

        # Restore module state for the rest of the session.
        import importlib

        import config.settings as settings_module
        importlib.reload(settings_module)
