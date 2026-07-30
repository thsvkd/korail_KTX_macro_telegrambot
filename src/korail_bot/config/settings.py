"""
Application configuration management.

This module centralizes all configuration variables from environment
and provides type-safe access to settings throughout the application.
"""

import os
import secrets


def _digits(value: str) -> str:
    """Keep only the digits of a value, so phone formats stop mattering."""
    return "".join(character for character in value if character.isdigit())


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_ratio(name: str, default: float) -> float:
    """Read a ratio between 0 and 1, falling back on anything unusable."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(max(value, 0.0), 1.0)


def _internal_callback_token() -> str:
    """
    Token shared between the Flask app and its background reservation processes.

    Generated once per app start when not supplied. It is written back into
    os.environ so that subprocesses spawned by the app inherit it automatically.
    """
    token = os.environ.get("INTERNAL_CALLBACK_TOKEN")
    if not token:
        token = secrets.token_urlsafe(32)
        os.environ["INTERNAL_CALLBACK_TOKEN"] = token
    return token


class Settings:
    """Application settings loaded from environment variables."""

    # How the bot receives updates from Telegram.
    #
    # 'polling' pulls updates with getUpdates and therefore works on a host
    # with no public inbound address (a Raspberry Pi behind a router).
    # 'webhook' requires Telegram to reach a public HTTPS endpoint.
    RECEIVE_MODE: str = os.environ.get("RECEIVE_MODE", "polling").strip().lower()
    RECEIVE_MODES: tuple[str, ...] = ("polling", "webhook")

    # Telegram Bot Configuration
    TELEGRAM_BOT_TOKEN: str = os.environ.get("BOTTOKEN", "")
    TELEGRAM_API_BASE_URL: str = "https://api.telegram.org/bot{token}"
    # Shared secret sent by Telegram as the X-Telegram-Bot-Api-Secret-Token
    # header. Register it with scripts/set-webhook.sh. Only meaningful in
    # webhook mode - polling updates arrive over an outbound connection we
    # opened ourselves.
    TELEGRAM_WEBHOOK_SECRET: str = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

    # Long-poll parameters. The HTTP read timeout must exceed the long-poll
    # timeout, otherwise every idle poll would look like a network failure.
    TELEGRAM_POLL_TIMEOUT: int = 30
    TELEGRAM_POLL_REQUEST_TIMEOUT: int = 35

    # Korail Configuration
    # Credentials kept on the server. When both are set the bot logs in with
    # them instead of asking each user for a phone number and a password -
    # see has_preconfigured_korail_credentials().
    KORAIL_ADMIN_USER_ID: str | None = (os.environ.get("USERID") or "").strip() or None
    # Not stripped: a password may legitimately begin or end with a space.
    KORAIL_ADMIN_PASSWORD: str | None = os.environ.get("USERPW") or None
    KORAIL_SEARCH_INTERVAL: float = float(os.environ.get("SEARCH_INTERVAL", "1"))  # seconds
    # Every wait between Korail requests is drawn from
    # interval * (1 +/- jitter) instead of being a fixed number of seconds, so
    # the search does not knock on the door on a metronome. 0 disables it and
    # restores the old fixed interval.
    KORAIL_SEARCH_INTERVAL_JITTER: float = _env_ratio("SEARCH_INTERVAL_JITTER", 0.4)
    # Korail closes a login server-side after a while, and a search that runs
    # for hours has to renew it. Drawn afresh after every login rather than
    # kept as a fixed period, for the same reason the search interval is.
    # 0 stops renewing ahead of time and waits for Korail to reject the
    # session instead, which is when the client re-authenticates anyway.
    KORAIL_RELOGIN_INTERVAL: float = float(os.environ.get("RELOGIN_INTERVAL", "1800"))
    KORAIL_RELOGIN_INTERVAL_JITTER: float = _env_ratio("RELOGIN_INTERVAL_JITTER", 0.4)
    # A search that finds nothing and a search that could not ask both end up
    # with no trains, so a run of failures is the only thing that tells them
    # apart. How long a run has to get before the user hears about it:
    KORAIL_FAILURE_ALERT_THRESHOLD: int = int(
        os.environ.get("SEARCH_FAILURE_ALERT_THRESHOLD", "10")
    )
    # How long to wait before saying it again, while it is still failing.
    KORAIL_FAILURE_REALERT_SECONDS: float = float(
        os.environ.get("SEARCH_FAILURE_REALERT_SECONDS", "1800")
    )
    # Failures back the search off instead of keeping it at full rate: if
    # Korail is refusing us, asking every second is what makes it worse. The
    # wait doubles per failure, up to this multiple of the search interval.
    KORAIL_FAILURE_BACKOFF_CAP: float = float(os.environ.get("SEARCH_FAILURE_BACKOFF_CAP", "60"))
    # The mobile app build the client reports itself as. korail2 carries the
    # build that was current when it was packaged; this is the escape hatch
    # for when Korail stops serving that build and the library has not caught
    # up yet. Unset means whatever the library ships.
    KORAIL_APP_VERSION: str | None = os.environ.get("KORAIL_APP_VERSION") or None
    KORAIL_STATION_LIST_URL: str = "http://www.letskorail.com/ebizprd/stationKtxList.do"
    KORAIL_PAYMENT_URL: str = "https://www.letskorail.com/ebizprd/EbizPrdTicketpr13500W_pr13510.do?"

    # User Access Control
    ALLOW_LIST: list[str] = (
        os.environ.get("ALLOW_LIST", "").split(",") if os.environ.get("ALLOW_LIST") else []
    )

    # Payment Reminder Configuration
    PAYMENT_TIMEOUT_MINUTES: int = int(os.environ.get("PAYMENT_TIMEOUT_MINUTES", "10"))
    # One reminder a minute. At the old ten seconds a ten-minute payment
    # window meant sixty messages, which is a phone buzzing continuously
    # while someone is trying to type their card number into another app.
    PAYMENT_REMINDER_INTERVAL_SECONDS: int = int(os.environ.get("PAYMENT_REMINDER_INTERVAL", "60"))
    # How often the search process asks Korail whether the reservation it
    # just made is still unpaid. One request each time, against a search that
    # was making one every few seconds, so this is gentle by comparison.
    PAYMENT_VERIFY_INTERVAL_SECONDS: int = int(os.environ.get("PAYMENT_VERIFY_INTERVAL", "30"))

    # Flask Configuration
    # In polling mode the only caller of the HTTP API is the background
    # reservation process on loopback, so binding to every interface would
    # expose the app for nothing. An explicit FLASK_HOST still wins.
    FLASK_HOST: str = os.environ.get(
        "FLASK_HOST", "127.0.0.1" if RECEIVE_MODE == "polling" else "0.0.0.0"
    )
    FLASK_PORT: int = int(os.environ.get("FLASK_PORT", "8080"))
    # Defaults to False: the Werkzeug debugger is a remote code execution
    # surface and must never be enabled on a reachable deployment.
    FLASK_DEBUG: bool = _env_bool("FLASK_DEBUG", False)

    # Application Callback URLs (internal, loopback only)
    CALLBACK_BASE_URL: str = f"http://127.0.0.1:{FLASK_PORT}"
    INTERNAL_CALLBACK_TOKEN: str = _internal_callback_token()

    # Logging Configuration
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # Magic Admin Login String
    # Opt-in only: when unset, the shortcut that logs in with the operator's
    # own Korail account is disabled.
    ADMIN_MAGIC_STRING: str | None = os.environ.get("ADMIN_MAGIC_STRING") or None

    # Admin Command Authentication
    # Distinct from USERPW so that guessing it never yields a Korail password.
    # When unset, every admin command is refused.
    ADMIN_PASSWORD: str | None = os.environ.get("ADMIN_PASSWORD") or None
    ADMIN_MAX_AUTH_FAILURES: int = int(os.environ.get("ADMIN_MAX_AUTH_FAILURES", "5"))
    ADMIN_LOCKOUT_SECONDS: int = int(os.environ.get("ADMIN_LOCKOUT_SECONDS", "900"))
    ADMIN_SESSION_TTL_SECONDS: int = int(os.environ.get("ADMIN_SESSION_TTL_SECONDS", "3600"))

    # Credential Protection
    # Key material used to encrypt Korail credentials before they touch Redis.
    SESSION_SECRET: str | None = os.environ.get("SESSION_SECRET") or None
    # Sessions expire so that credentials never linger indefinitely.
    SESSION_TTL_SECONDS: int = int(os.environ.get("SESSION_TTL_SECONDS", "86400"))

    # Restart Recovery
    #
    # A search runs in a child process, so a restart of the app leaves the
    # stored reservation without anything actually searching. Resuming means
    # logging in to Korail again, which means keeping the password for as long
    # as the search is alive - encrypted, in a key of its own, deleted the
    # moment the search ends. Turn this off to be told about the interruption
    # instead of recovering from it.
    RESUME_ON_RESTART: bool = _env_bool("RESUME_ON_RESTART", True)
    # Backstop only: the credentials are deleted when the search finishes.
    RESUME_TTL_SECONDS: int = int(os.environ.get("RESUME_TTL_SECONDS", "259200"))

    # ==================== Scheduled searches ====================

    # How far ahead a search may be booked. Bounded by RESUME_TTL_SECONDS
    # because the login it will need is stored under that expiry: schedule
    # past it and the moment arrives with no way to log in.
    SCHEDULE_MAX_AHEAD_SECONDS: int = int(
        os.environ.get("SCHEDULE_MAX_AHEAD_SECONDS", str(RESUME_TTL_SECONDS))
    )
    # How long past its start time a missed schedule is still worth running.
    # Covers the app being restarted across the moment; beyond it the user
    # would be getting a search they asked for hours ago without warning.
    SCHEDULE_GRACE_SECONDS: int = int(os.environ.get("SCHEDULE_GRACE_SECONDS", "600"))
    # Longest the scheduler sleeps between checks. It normally sleeps exactly
    # until the next start time, so this only bounds how quickly a newly
    # booked search is noticed.
    SCHEDULE_POLL_SECONDS: float = float(os.environ.get("SCHEDULE_POLL_SECONDS", "30"))

    # ==================== Watching the search processes ====================

    # How long a freshly spawned search is given to prove it is alive.
    #
    # A search that cannot start at all - a broken interpreter, a missing
    # dependency - dies within milliseconds of exec. Waiting this long before
    # reporting success costs nothing on the path that works and is the only
    # thing standing between the user and a "search started" message about a
    # process that is already gone.
    PROCESS_START_GRACE_SECONDS: float = float(os.environ.get("PROCESS_START_GRACE_SECONDS", "1.0"))
    # How often the running searches are checked for still existing. A search
    # that dies is not noticed until the next pass, so this is roughly how
    # long the user waits to be told.
    WATCHDOG_POLL_SECONDS: float = float(os.environ.get("WATCHDOG_POLL_SECONDS", "30"))
    # How long a dead search's details are kept so the user can resume it.
    # Bounded by RESUME_TTL_SECONDS: past that the stored login is gone and
    # resuming would have nothing to log in with.
    DEAD_SEARCH_TTL_SECONDS: int = int(
        os.environ.get("DEAD_SEARCH_TTL_SECONDS", str(RESUME_TTL_SECONDS))
    )

    # Identifies this run of the application. Reservations carry it, so
    # anything left over from an earlier run is recognisable as abandoned.
    RUN_ID: str = secrets.token_hex(8)

    # Process Management
    RECURSION_LIMIT: int = 10**7

    # Redis Configuration
    REDIS_HOST: str = os.environ.get("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.environ.get("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.environ.get("REDIS_DB", "0"))
    REDIS_PASSWORD: str | None = os.environ.get("REDIS_PASSWORD")
    REDIS_DECODE_RESPONSES: bool = True
    REDIS_SOCKET_TIMEOUT: int = 5
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 5
    REDIS_MAX_CONNECTIONS: int = 50

    @classmethod
    def validate(cls) -> None:
        """Validate required settings."""
        if not cls.TELEGRAM_BOT_TOKEN:
            raise ValueError("BOTTOKEN environment variable is required")

        if cls.RECEIVE_MODE not in cls.RECEIVE_MODES:
            raise ValueError(
                f"RECEIVE_MODE must be one of "
                f"{', '.join(cls.RECEIVE_MODES)} (got '{cls.RECEIVE_MODE}')."
            )

        # Only webhook mode exposes an endpoint that a forged request could
        # reach; in polling mode the secret protects nothing.
        if cls.RECEIVE_MODE == "webhook" and not cls.TELEGRAM_WEBHOOK_SECRET:
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
            if cls.RESUME_ON_RESTART:
                messages.append(
                    "RESUME_ON_RESTART is enabled but SESSION_SECRET is not "
                    "set, so an interrupted search can never be resumed - the "
                    "stored credentials are unreadable after the restart that "
                    "would need them."
                )

        if not cls.ADMIN_PASSWORD:
            messages.append("ADMIN_PASSWORD is not set - all admin commands are disabled.")

        if cls.has_preconfigured_korail_credentials():
            messages.append(
                "USERID/USERPW are set - anyone who talks to the bot reserves "
                "with that Korail account, and ALLOW_LIST is not consulted "
                "because no phone number is ever typed. Unset them to go back "
                "to asking each user for their own credentials."
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
    def has_preconfigured_korail_credentials(cls) -> bool:
        """
        Whether the server can log in without asking the user anything.

        USERID/USERPW are the operator's own Korail account. When both are
        present the phone number and password prompts have a known answer,
        so the conversation skips straight past them.
        """
        return bool(cls.KORAIL_ADMIN_USER_ID and cls.KORAIL_ADMIN_PASSWORD)

    @classmethod
    def is_user_allowed(cls, phone_number: str) -> bool:
        """
        Check if user phone number is in allow list.

        Compared digit by digit so that '010-1234-5678' and '01012345678'
        are the same number, whichever form the list happens to use.
        """
        if not cls.ALLOW_LIST or cls.ALLOW_LIST == [""]:
            return True  # No restriction if ALLOW_LIST is empty

        allowed = {_digits(entry) for entry in cls.ALLOW_LIST if _digits(entry)}
        return _digits(phone_number) in allowed


# Singleton instance
settings = Settings()
