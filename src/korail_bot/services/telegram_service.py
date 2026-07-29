"""Telegram messaging service."""

import requests

from korail_bot.config.settings import settings
from korail_bot.telegramBot.messages import Messages as MessageTemplates
from korail_bot.utils.logger import get_logger

# MessageTemplates is a deprecated alias for the Messages class, kept so the
# call sites in handlers/ and services/ keep working. Listing it here marks it
# as a deliberate re-export rather than an unused import.
__all__ = ["MessageTemplates", "TelegramService"]

logger = get_logger(__name__)


class TelegramService:
    """Service for sending messages via Telegram Bot API."""

    def __init__(self, bot_token: str | None = None):
        """
        Initialize Telegram service.

        Args:
            bot_token: Telegram bot token (defaults to settings.TELEGRAM_BOT_TOKEN)
        """
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self.base_url = settings.TELEGRAM_API_BASE_URL.format(token=self.bot_token)
        self.session = requests.session()

    def send_message(self, chat_id: int, text: str) -> bool:
        """
        Send a text message to a Telegram chat.

        Args:
            chat_id: Telegram chat ID
            text: Message text to send

        Returns:
            True if message was sent successfully, False otherwise
        """
        try:
            url = f"{self.base_url}/sendMessage"
            params = {"chat_id": chat_id, "text": text}
            response = self.session.get(url, params=params)
            response.raise_for_status()
            logger.info(f"Message sent to chat_id={chat_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message to chat_id={chat_id}: {e}")
            return False

    def send_to_multiple(self, chat_ids: list[int], text: str) -> int:
        """
        Send a message to multiple chats.

        Args:
            chat_ids: List of Telegram chat IDs
            text: Message text to send

        Returns:
            Number of successful sends
        """
        success_count = 0
        for chat_id in chat_ids:
            if self.send_message(chat_id, text):
                success_count += 1
        return success_count
