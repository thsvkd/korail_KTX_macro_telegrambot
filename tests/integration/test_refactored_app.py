"""Integration tests for refactored application."""

from datetime import datetime, timedelta

import pytest

from korail_bot.config.settings import settings
from korail_bot.handlers import CommandHandler, ConversationHandler
from korail_bot.models import UserCredentials, UserProgress, UserSession
from korail_bot.services import (
    PaymentReminderService,
    ReservationService,
    TelegramService,
)
from korail_bot.storage import RedisStorage


class TestRefactoredArchitecture:
    """Test the refactored architecture."""

    def setup_method(self):
        """Set up test fixtures."""
        self.storage = RedisStorage()
        self.telegram = TelegramService("test_token")
        self.reservation = ReservationService(self.storage, self.telegram)
        self.payment_reminder = PaymentReminderService(self.storage, self.telegram)

    def teardown_method(self):
        """Clean up after each test."""
        # Flush Redis database after each test
        self.storage.redis.flushdb()

    def test_storage_user_session(self):
        """Test storage can save and retrieve user sessions."""
        session = UserSession(chat_id=12345, in_progress=True, last_action=UserProgress.STARTED)

        self.storage.save_user_session(session)
        retrieved = self.storage.get_user_session(12345)

        assert retrieved is not None
        assert retrieved.chat_id == 12345
        assert retrieved.in_progress is True
        assert retrieved.last_action == UserProgress.STARTED

    def test_storage_payment_status(self):
        """Test storage can manage payment status."""
        from korail_bot.models import PaymentStatus

        status = PaymentStatus(chat_id=12345, completed=False)
        self.storage.save_payment_status(status)

        retrieved = self.storage.get_payment_status(12345)
        assert retrieved is not None
        assert retrieved.completed is False

        # Update status
        retrieved.completed = True
        self.storage.save_payment_status(retrieved)

        updated = self.storage.get_payment_status(12345)
        assert updated.completed is True

    def test_command_handler_initialization(self):
        """Test command handler can be initialized."""
        handler = CommandHandler(
            self.storage, self.telegram, self.reservation, self.payment_reminder
        )

        assert handler is not None
        assert handler.storage == self.storage

    def test_conversation_handler_initialization(self):
        """Test conversation handler can be initialized."""
        handler = ConversationHandler(self.storage, self.telegram, self.reservation)

        assert handler is not None
        assert handler.storage == self.storage

    def test_user_session_reset(self):
        """Test user session can be reset."""
        session = UserSession(
            chat_id=12345, in_progress=True, last_action=UserProgress.FINDING_TICKET
        )
        session.credentials = UserCredentials(korail_id="010-1234-5678", korail_pw="password")
        session.train_info = {"depDate": "20230101"}

        session.reset()

        assert session.in_progress is False
        assert session.last_action == 0
        assert session.train_info == {}
        assert session.process_id == 9999999

    def test_settings_validation(self):
        """Test settings validation."""
        # Settings should have required attributes
        assert hasattr(settings, "TELEGRAM_BOT_TOKEN")
        assert hasattr(settings, "PAYMENT_TIMEOUT_MINUTES")
        assert hasattr(settings, "KORAIL_SEARCH_INTERVAL")

    def test_input_validators(self):
        """Test input validators."""
        from korail_bot.utils.validators import InputValidator

        # Phone number validation
        valid, error = InputValidator.validate_phone_number("010-1234-5678")
        assert valid is True

        # Hyphens are optional. This used to assert False, which contradicted
        # both the shipped behaviour and
        # test_validators.py::test_valid_phone_without_hyphens.
        valid, error = InputValidator.validate_phone_number("01012345678")
        assert valid is True

        valid, error = InputValidator.validate_phone_number("010-12-5678")
        assert valid is False

        # Date validation. A relative date, because booking is capped at a
        # year ahead - the hardcoded "20991231" this used to assert as valid
        # is past that cap and past what Korail sells.
        next_week = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")
        valid, error = InputValidator.validate_date(next_week)
        assert valid is True

        valid, error = InputValidator.validate_date("20991231")
        assert valid is False  # Beyond the one-year booking window

        valid, error = InputValidator.validate_date("20200101")
        assert valid is False  # Past date

        valid, error = InputValidator.validate_date("invalid")
        assert valid is False

        # Time validation
        valid, error = InputValidator.validate_time("1430")
        assert valid is True

        valid, error = InputValidator.validate_time("2560")
        assert valid is False  # Invalid hour

    def test_message_templates(self):
        """Test message templates exist."""
        from korail_bot.services.telegram_service import MessageTemplates

        welcome = MessageTemplates.welcome_message()
        assert isinstance(welcome, str)
        assert len(welcome) > 0

        help_msg = MessageTemplates.help_message()
        assert isinstance(help_msg, str)
        assert "/start" in help_msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
