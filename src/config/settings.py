"""
Application configuration management.

This module centralizes all configuration variables from environment
and provides type-safe access to settings throughout the application.
"""
import os
import secrets
from typing import Optional


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('true', '1', 'yes', 'on')


def _internal_callback_token() -> str:
    """
    Token shared between the Flask app and its background reservation processes.

    Generated once per app start when not supplied. It is written back into
    os.environ so that subprocesses spawned by the app inherit it automatically.
    """
    token = os.environ.get('INTERNAL_CALLBACK_TOKEN')
    if not token:
        token = secrets.token_urlsafe(32)
        os.environ['INTERNAL_CALLBACK_TOKEN'] = token
    return token


class Settings:
    """Application settings loaded from environment variables."""

    # Telegram Bot Configuration
    TELEGRAM_BOT_TOKEN: str = os.environ.get('BOTTOKEN', '')
    TELEGRAM_API_BASE_URL: str = "https://api.telegram.org/bot{token}"
    # Shared secret sent by Telegram as the X-Telegram-Bot-Api-Secret-Token
    # header. Register it with scripts/set-webhook.sh.
    TELEGRAM_WEBHOOK_SECRET: str = os.environ.get('TELEGRAM_WEBHOOK_SECRET', '')

    # Korail Configuration
    KORAIL_ADMIN_USER_ID: Optional[str] = os.environ.get('USERID')
    KORAIL_ADMIN_PASSWORD: Optional[str] = os.environ.get('USERPW')
    KORAIL_SEARCH_INTERVAL: int = int(os.environ.get('SEARCH_INTERVAL', '1'))  # seconds
    KORAIL_STATION_LIST_URL: str = "http://www.letskorail.com/ebizprd/stationKtxList.do"
    KORAIL_PAYMENT_URL: str = "https://www.letskorail.com/ebizprd/EbizPrdTicketpr13500W_pr13510.do?"

    # User Access Control
    ALLOW_LIST: list[str] = os.environ.get('ALLOW_LIST', '').split(',') if os.environ.get('ALLOW_LIST') else []

    # Payment Reminder Configuration
    PAYMENT_TIMEOUT_MINUTES: int = int(os.environ.get('PAYMENT_TIMEOUT_MINUTES', '10'))
    PAYMENT_REMINDER_INTERVAL_SECONDS: int = int(os.environ.get('PAYMENT_REMINDER_INTERVAL', '10'))

    # Flask Configuration
    FLASK_HOST: str = os.environ.get('FLASK_HOST', '0.0.0.0')
    FLASK_PORT: int = int(os.environ.get('FLASK_PORT', '8080'))
    # Defaults to False: the Werkzeug debugger is a remote code execution
    # surface and must never be enabled on a reachable deployment.
    FLASK_DEBUG: bool = _env_bool('FLASK_DEBUG', False)

    # Application Callback URLs (internal, loopback only)
    CALLBACK_BASE_URL: str = f"http://127.0.0.1:{FLASK_PORT}"
    INTERNAL_CALLBACK_TOKEN: str = _internal_callback_token()

    # Logging Configuration
    LOG_LEVEL: str = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FORMAT: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # Magic Admin Login String
    # Opt-in only: when unset, the shortcut that logs in with the operator's
    # own Korail account is disabled.
    ADMIN_MAGIC_STRING: Optional[str] = os.environ.get('ADMIN_MAGIC_STRING') or None

    # Admin Command Authentication
    # Distinct from USERPW so that guessing it never yields a Korail password.
    # When unset, every admin command is refused.
    ADMIN_PASSWORD: Optional[str] = os.environ.get('ADMIN_PASSWORD') or None
    ADMIN_MAX_AUTH_FAILURES: int = int(os.environ.get('ADMIN_MAX_AUTH_FAILURES', '5'))
    ADMIN_LOCKOUT_SECONDS: int = int(os.environ.get('ADMIN_LOCKOUT_SECONDS', '900'))
    ADMIN_SESSION_TTL_SECONDS: int = int(os.environ.get('ADMIN_SESSION_TTL_SECONDS', '3600'))

    # Credential Protection
    # Key material used to encrypt Korail credentials before they touch Redis.
    SESSION_SECRET: Optional[str] = os.environ.get('SESSION_SECRET') or None
    # Sessions expire so that credentials never linger indefinitely.
    SESSION_TTL_SECONDS: int = int(os.environ.get('SESSION_TTL_SECONDS', '86400'))

    # Process Management
    RECURSION_LIMIT: int = 10**7

    # Redis Configuration
    REDIS_HOST: str = os.environ.get('REDIS_HOST', 'localhost')
    REDIS_PORT: int = int(os.environ.get('REDIS_PORT', '6379'))
    REDIS_DB: int = int(os.environ.get('REDIS_DB', '0'))
    REDIS_PASSWORD: Optional[str] = os.environ.get('REDIS_PASSWORD')
    REDIS_DECODE_RESPONSES: bool = True
    REDIS_SOCKET_TIMEOUT: int = 5
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 5
    REDIS_RETRY_ON_TIMEOUT: bool = True
    REDIS_MAX_CONNECTIONS: int = 50

    @classmethod
    def validate(cls) -> None:
        """Validate required settings."""
        if not cls.TELEGRAM_BOT_TOKEN:
            raise ValueError("BOTTOKEN environment variable is required")

        if not cls.TELEGRAM_WEBHOOK_SECRET:
            raise ValueError(
                "TELEGRAM_WEBHOOK_SECRET environment variable is required. "
                "Without it the /telebot webhook accepts forged updates from "
                "anyone who can reach it. Generate one with "
                "'scripts/gen-secrets.sh' and register it with "
                "'scripts/set-webhook.sh'."
            )

    @classmethod
    def warnings(cls) -> list[str]:
        """Return non-fatal configuration warnings to surface at startup."""
        messages = []

        if not cls.SESSION_SECRET:
            messages.append(
                "SESSION_SECRET is not set - credentials are encrypted with an "
                "ephemeral key, so stored sessions become unreadable after a "
                "restart. Generate one with 'scripts/gen-secrets.sh'."
            )

        if not cls.ADMIN_PASSWORD:
            messages.append(
                "ADMIN_PASSWORD is not set - all admin commands are disabled."
            )

        if not cls.REDIS_PASSWORD:
            messages.append(
                "REDIS_PASSWORD is not set - make sure Redis is not reachable "
                "from outside the container network."
            )

        if cls.FLASK_DEBUG:
            messages.append(
                "FLASK_DEBUG is enabled - the Werkzeug debugger allows remote "
                "code execution. Never enable this on a reachable host."
            )

        return messages

    @classmethod
    def is_user_allowed(cls, phone_number: str) -> bool:
        """Check if user phone number is in allow list."""
        if not cls.ALLOW_LIST or cls.ALLOW_LIST == ['']:
            return True  # No restriction if ALLOW_LIST is empty
        return phone_number in cls.ALLOW_LIST


# Singleton instance
settings = Settings()
