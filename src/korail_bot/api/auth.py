"""
Request authentication for the HTTP endpoints.

Two different callers reach this app:

* Telegram, which POSTs updates to /telebot and proves itself with the
  secret token registered via setWebhook.
* The background reservation processes, which call /telebot and
  /check_payment over loopback and prove themselves with a token generated
  at app start.

Neither endpoint is safe to leave open: the first lets anyone forge user
messages, the second lets anyone send arbitrary text through the bot.
"""

import hmac

from flask import request

from korail_bot.config.settings import settings
from korail_bot.utils.logger import get_logger

logger = get_logger(__name__)

_LOOPBACK_ADDRESSES = {"127.0.0.1", "::1", "localhost"}


def _matches(expected: str | None, provided: str | None) -> bool:
    """
    Compare two secrets without leaking their contents through timing.

    Compared as bytes: compare_digest raises on str inputs holding non-ASCII
    characters, and the provided value comes from an untrusted request.
    """
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def verify_telegram_request() -> bool:
    """
    Verify that a webhook POST really came from Telegram.

    Telegram echoes the secret registered with setWebhook in the
    X-Telegram-Bot-Api-Secret-Token header.

    Returns:
        True if the request is authentic
    """
    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token")

    if not _matches(settings.TELEGRAM_WEBHOOK_SECRET, provided):
        logger.warning(
            f"Rejected webhook request with invalid secret token from {request.remote_addr}"
        )
        return False

    return True


def verify_internal_request() -> bool:
    """
    Verify that a request came from one of our own background processes.

    Requires both a loopback source address and the per-run internal token,
    so neither a leaked token nor a spoofed source address is enough on
    its own.

    Returns:
        True if the request is authentic
    """
    if request.remote_addr not in _LOOPBACK_ADDRESSES:
        logger.warning(
            f"Rejected internal callback from non-loopback address {request.remote_addr}"
        )
        return False

    if not _matches(settings.INTERNAL_CALLBACK_TOKEN, request.args.get("token")):
        logger.warning(f"Rejected internal callback with invalid token from {request.remote_addr}")
        return False

    return True
