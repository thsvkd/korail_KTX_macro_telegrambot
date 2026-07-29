"""Telegram messaging service."""

from typing import Any

import requests

from korail_bot.config.settings import settings
from korail_bot.telegramBot.messages import Messages as MessageTemplates
from korail_bot.utils.logger import get_logger

# MessageTemplates is a deprecated alias for the Messages class, kept so the
# call sites in handlers/ and services/ keep working. Listing it here marks it
# as a deliberate re-export rather than an unused import.
__all__ = ["MessageTemplates", "TelegramService"]

logger = get_logger(__name__)

# Telegram truncates nothing - it refuses the whole call over this.
MESSAGE_TEXT_LIMIT = 4096


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

    def _call(self, method: str, payload: dict[str, Any]) -> dict | None:
        """
        Invoke one Bot API method.

        Posted as JSON rather than sent as query parameters: reply_markup is a
        nested object, and a keyboard flattened into a query string is a
        keyboard Telegram rejects.

        Args:
            method: Bot API method name
            payload: Request body

        Returns:
            The API's result field, or None when the call did not succeed
        """
        try:
            response = self.session.post(f"{self.base_url}/{method}", json=payload, timeout=10)
            body = response.json()
        except ValueError as e:
            logger.error(f"{method} returned a non-JSON body: {e}")
            return None
        except Exception as e:
            # Deliberately everything, not just RequestException. Messages go
            # out from reminder threads and from the shutdown path, and a
            # failed send there must stay a failed send rather than take the
            # thread - or the shutdown - down with it.
            logger.error(f"{method} failed: {e}")
            return None

        if not body.get("ok"):
            # Carries Telegram's own description, which says what was wrong
            # with the payload - far more use than the status code.
            logger.error(f"{method} was rejected: {body.get('description')}")
            return None

        result = body.get("result")
        return result if isinstance(result, dict) else {}

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> bool:
        """
        Send a text message to a Telegram chat.

        Args:
            chat_id: Telegram chat ID
            text: Message text to send
            reply_markup: Optional inline keyboard to attach

        Returns:
            True if message was sent successfully, False otherwise
        """
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        if self._call("sendMessage", payload) is None:
            return False

        logger.info(f"Message sent to chat_id={chat_id}")
        return True

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

    def answer_callback_query(
        self, callback_query_id: str, text: str | None = None, show_alert: bool = False
    ) -> bool:
        """
        Acknowledge a button press.

        Required, and required promptly: until this arrives the client keeps
        a progress indicator on the button, and Telegram gives up on the
        query after a few seconds. Sent even when the press is refused - a
        button that appears to hang is worse than one that says no.

        Args:
            callback_query_id: The id from the callback_query update
            text: Optional notice to show the user
            show_alert: Show it as a dialog rather than a toast

        Returns:
            True when Telegram accepted the acknowledgement
        """
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True

        return self._call("answerCallbackQuery", payload) is not None

    def edit_message_text(
        self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None
    ) -> bool:
        """
        Replace the text, and optionally the keyboard, of a message already sent.

        Args:
            chat_id: Telegram chat ID
            message_id: The message to rewrite
            text: The new text
            reply_markup: New keyboard; pass an empty one to remove it

        Returns:
            True when Telegram accepted the edit
        """
        if len(text) > MESSAGE_TEXT_LIMIT:
            logger.warning(
                f"Not editing message {message_id}: the new text is over "
                f"Telegram's {MESSAGE_TEXT_LIMIT} character limit"
            )
            return False

        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        return self._call("editMessageText", payload) is not None

    def edit_message_reply_markup(self, chat_id: int, message_id: int, reply_markup: dict) -> bool:
        """
        Change only a message's keyboard, leaving its text alone.

        Args:
            chat_id: Telegram chat ID
            message_id: The message whose keyboard changes
            reply_markup: The new keyboard; an empty one removes it

        Returns:
            True when Telegram accepted the edit
        """
        return (
            self._call(
                "editMessageReplyMarkup",
                {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
            )
            is not None
        )

    def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        """
        Publish the command list Telegram shows in its menu button.

        Turns the commands from something to be remembered and typed into
        something to be picked from a list.

        Args:
            commands: Entries of {"command": ..., "description": ...}

        Returns:
            True when Telegram accepted the list
        """
        return self._call("setMyCommands", {"commands": commands}) is not None
