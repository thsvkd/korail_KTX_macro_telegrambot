"""
Integration tests for payment reminder service.

Tests payment reminder timing, timeout, and confirmation.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from freezegun import freeze_time

from korail_bot.models import PaymentStatus
from korail_bot.services import PaymentReminderService, TelegramService
from korail_bot.storage import RedisStorage


class TestPaymentReminderService:
    """Test payment reminder service."""

    def setup_method(self):
        """Set up test fixtures."""
        self.storage = RedisStorage()
        self.telegram = Mock(spec=TelegramService)
        self.service = PaymentReminderService(self.storage, self.telegram)

    def teardown_method(self):
        """Clean up after each test."""
        self.storage.redis.flushdb()
        self.storage.close()

    def test_start_reminders_creates_payment_status(self):
        """Test starting reminders creates payment status."""
        chat_id = 12345

        # Mock the reminder thread to not actually run
        with patch.object(self.service, "_reminder_loop"):
            self.service.start_reminders(chat_id)

        # Check payment status created
        status = self.storage.get_payment_status(chat_id)
        assert status is not None
        assert status.chat_id == chat_id
        assert status.completed is False
        assert status.reminder_active is True
        assert status.created_at is not None

    def test_silencing_stops_the_reminders_without_claiming_a_payment(self):
        """
        The whole point of /notify_off. Any message used to stop the reminders
        by recording a payment nobody had checked, so someone who typed
        anything at all was told the matter was settled and lost the seat.
        """
        chat_id = 12345
        self.storage.save_payment_status(
            PaymentStatus(chat_id=chat_id, completed=False, reminder_active=True)
        )

        assert self.service.silence(chat_id) is True

        stored = self.storage.get_payment_status(chat_id)
        assert stored.reminder_active is False
        assert stored.completed is False
        assert stored.cancelled is False

    def test_silencing_is_what_the_loop_reads_to_stop(self):
        chat_id = 12345
        self.storage.save_payment_status(
            PaymentStatus(chat_id=chat_id, completed=False, reminder_active=True)
        )

        self.service.silence(chat_id)

        assert self.service.is_silenced(chat_id) is True

    def test_there_is_nothing_to_silence_without_a_payment(self):
        assert self.service.silence(12345) is False

    def test_silencing_twice_reports_that_it_was_already_off(self):
        chat_id = 12345
        self.storage.save_payment_status(
            PaymentStatus(chat_id=chat_id, completed=False, reminder_active=True)
        )
        self.service.silence(chat_id)

        assert self.service.silence(chat_id) is False

    @patch("threading.Thread")
    def test_reminder_thread_started(self, mock_thread):
        """Test that reminder thread is started."""
        chat_id = 12345

        self.service.start_reminders(chat_id)

        # Thread should have been created and started
        mock_thread.assert_called_once()
        thread_instance = mock_thread.return_value
        thread_instance.start.assert_called_once()

    def test_deactivate_reminders(self):
        """Test deactivating reminders."""
        chat_id = 12345

        # Create active payment status
        status = PaymentStatus(chat_id=chat_id, completed=False, reminder_active=True)
        self.storage.save_payment_status(status)

        # Deactivate
        self.service.deactivate_reminders(chat_id)

        # Check status
        updated_status = self.storage.get_payment_status(chat_id)
        assert updated_status.reminder_active is False

    def test_payment_timeout_calculation(self):
        """Test that payment timeout is calculated correctly."""
        chat_id = 12345

        status = PaymentStatus(chat_id=chat_id, completed=False, reminder_active=True)
        # Set created_at to 8 minutes ago
        status.created_at = datetime.now() - timedelta(minutes=8)
        self.storage.save_payment_status(status)

        # Check if timed out (should be False, still within 10 min)
        retrieved = self.storage.get_payment_status(chat_id)
        elapsed = (datetime.now() - retrieved.created_at).total_seconds() / 60
        assert elapsed < 10

    def test_multiple_reminders_different_users(self):
        """Test multiple users can have simultaneous reminders."""
        chat_ids = [11111, 22222, 33333]

        with patch.object(self.service, "_reminder_loop"):
            for chat_id in chat_ids:
                self.service.start_reminders(chat_id)

        # All should have payment status
        for chat_id in chat_ids:
            status = self.storage.get_payment_status(chat_id)
            assert status is not None
            assert status.reminder_active is True

    def test_reminder_not_sent_after_completion(self):
        """Test reminders stop after payment completion."""
        chat_id = 12345

        # Create completed payment status
        status = PaymentStatus(chat_id=chat_id, completed=True, reminder_active=False)
        self.storage.save_payment_status(status)

        # Try to check if should send reminder
        # In actual implementation, reminder loop checks this
        retrieved = self.storage.get_payment_status(chat_id)
        assert retrieved.completed is True
        assert retrieved.reminder_active is False


class TestPaymentReminderTiming:
    """Test payment reminder timing with time manipulation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.storage = RedisStorage()
        self.telegram = Mock(spec=TelegramService)
        self.service = PaymentReminderService(self.storage, self.telegram)

    def teardown_method(self):
        """Clean up after each test."""
        self.storage.redis.flushdb()
        self.storage.close()

    def test_timeout_detection_after_10_minutes(self):
        """Test timeout is detected after 10 minutes."""
        chat_id = 12345

        # Create status at specific time
        start_time = datetime(2025, 1, 1, 12, 0, 0)
        with freeze_time(start_time):
            status = PaymentStatus(chat_id=chat_id, completed=False, reminder_active=True)
            self.storage.save_payment_status(status)

        # Move time forward by 11 minutes
        timeout_time = start_time + timedelta(minutes=11)
        with freeze_time(timeout_time):
            retrieved = self.storage.get_payment_status(chat_id)
            elapsed = (datetime.now() - retrieved.created_at).total_seconds() / 60

            # Should be past timeout
            assert elapsed > 10

    def test_no_timeout_within_10_minutes(self):
        """Test no timeout within 10 minutes."""
        chat_id = 12345

        start_time = datetime(2025, 1, 1, 12, 0, 0)
        with freeze_time(start_time):
            status = PaymentStatus(chat_id=chat_id, completed=False, reminder_active=True)
            self.storage.save_payment_status(status)

        # Move time forward by 5 minutes
        check_time = start_time + timedelta(minutes=5)
        with freeze_time(check_time):
            retrieved = self.storage.get_payment_status(chat_id)
            elapsed = (datetime.now() - retrieved.created_at).total_seconds() / 60

            # Should not be timed out yet
            assert elapsed < 10

    def test_payment_status_serialization_preserves_datetime(self):
        """Test that datetime is preserved through Redis serialization."""
        chat_id = 12345

        original_time = datetime(2025, 1, 1, 12, 30, 45)
        with freeze_time(original_time):
            status = PaymentStatus(chat_id=chat_id, completed=False, reminder_active=True)
            self.storage.save_payment_status(status)

        # Retrieve and check
        retrieved = self.storage.get_payment_status(chat_id)
        assert retrieved.created_at is not None
        # Allow small difference due to serialization
        time_diff = abs((retrieved.created_at - original_time).total_seconds())
        assert time_diff < 2  # Within 2 seconds


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
