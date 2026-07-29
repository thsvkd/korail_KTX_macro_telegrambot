"""
Flask application entry point for Korail KTX Telegram Bot.

This is the refactored version using the new service-oriented architecture.
"""

import atexit
import signal
import sys
import threading

from flask import Flask
from flask_cors import CORS
from flask_restful import Api
from werkzeug.serving import is_running_from_reloader

from korail_bot.api import PaymentCheckAPI, TelegramWebhook
from korail_bot.config.settings import settings
from korail_bot.handlers import TelegramUpdateProcessor
from korail_bot.services import (
    PaymentReminderService,
    ReservationService,
    TelegramPoller,
    TelegramService,
)
from korail_bot.storage.redis import RedisStorage
from korail_bot.utils.logger import LoggerFactory, get_logger

# Configure logging
logger = get_logger(__name__)

# Validate settings
try:
    settings.validate()
except ValueError as e:
    logger.error(f"Configuration error: {e}")
    sys.exit(1)

# Surface non-fatal misconfiguration (weak secrets, disabled protections)
for warning in settings.warnings():
    logger.warning(f"⚠️  {warning}")

# Set recursion limit
sys.setrecursionlimit(settings.RECURSION_LIMIT)

# Create Flask application
application = Flask(__name__)
CORS(application)
api = Api(application)

# Initialize storage (Redis)
try:
    storage = RedisStorage()
    logger.info("✅ Redis storage initialized successfully")
    # Restore debug mode from Redis
    if storage.is_debug_mode():
        LoggerFactory.set_log_level("DEBUG")
        logger.info("Debug mode restored from Redis - log level set to DEBUG")
except Exception as e:
    logger.error(f"❌ Failed to initialize Redis storage: {e}")
    logger.error("Please ensure Redis is running and accessible")
    sys.exit(1)

# Initialize services
telegram_service = TelegramService(settings.TELEGRAM_BOT_TOKEN)
reservation_service = ReservationService(storage, telegram_service)
payment_reminder_service = PaymentReminderService(storage, telegram_service)

# Configure API resources with dependency injection
api.add_resource(
    TelegramWebhook,
    "/telebot",
    resource_class_kwargs={
        "storage": storage,
        "telegram_service": telegram_service,
        "reservation_service": reservation_service,
        "payment_reminder_service": payment_reminder_service,
    },
)

api.add_resource(PaymentCheckAPI, "/check_payment", resource_class_kwargs={"storage": storage})

logger.info("=" * 60)
logger.info("Korail KTX Telegram Bot - Redis Version")
logger.info("=" * 60)
logger.info(f"Receive mode: {settings.RECEIVE_MODE}")
logger.info(f"Flask host: {settings.FLASK_HOST}")
logger.info(f"Flask port: {settings.FLASK_PORT}")
logger.info(f"Debug mode: {settings.FLASK_DEBUG}")
logger.info(f"Log level: {settings.LOG_LEVEL}")
logger.info(f"Redis: {settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}")
if settings.KORAIL_SEARCH_INTERVAL_JITTER > 0:
    _jitter_spread = settings.KORAIL_SEARCH_INTERVAL * settings.KORAIL_SEARCH_INTERVAL_JITTER
    logger.info(
        f"Search interval: {settings.KORAIL_SEARCH_INTERVAL}s "
        f"randomised over {settings.KORAIL_SEARCH_INTERVAL - _jitter_spread:.2f}"
        f"-{settings.KORAIL_SEARCH_INTERVAL + _jitter_spread:.2f}s "
        f"(jitter {settings.KORAIL_SEARCH_INTERVAL_JITTER:.0%})"
    )
else:
    logger.info(f"Search interval: {settings.KORAIL_SEARCH_INTERVAL}s (fixed)")
logger.info(f"Payment timeout: {settings.PAYMENT_TIMEOUT_MINUTES}min")
logger.info(f"Reminder interval: {settings.PAYMENT_REMINDER_INTERVAL_SECONDS}s")
if settings.RECEIVE_MODE == "webhook":
    logger.info("Webhook secret: configured")  # validate() guarantees this
else:
    logger.info("Updates: pulled with getUpdates (no public endpoint needed)")
logger.info(f"Admin commands: {'enabled' if settings.ADMIN_PASSWORD else 'disabled'}")
logger.info(f"Admin magic login: {'enabled' if settings.ADMIN_MAGIC_STRING else 'disabled'}")
logger.info(
    "Korail login: "
    + (
        "USERID/USERPW from the environment (users are not asked)"
        if settings.has_preconfigured_korail_credentials()
        else "asked from each user"
    )
)
logger.info(f"Resume on restart: {'enabled' if settings.RESUME_ON_RESTART else 'disabled'}")
logger.info("=" * 60)

# In polling mode the bot pulls its own updates instead of waiting for
# Telegram to reach us. Flask still runs either way: the background
# reservation processes report their results over loopback to /telebot and
# /check_payment.
#
# The Flask reloader executes this module in two processes, which would give
# the bot token two competing consumers and earn a 409 from Telegram.
# A search lives in a child process, so anything recorded by a previous run of
# this app is now unattended. Resume it or tell the user, before new updates
# start arriving and /status has a chance to lie about it.
if not is_running_from_reloader():
    reservation_service.reconcile_after_restart()

    # Publish the command menu. Best effort: Telegram being unreachable at
    # startup means the menu keeps whatever it had, which is not a reason to
    # refuse to start a bot whose searches run over a different connection.
    from korail_bot.telegramBot.messages import Messages

    if telegram_service.set_my_commands(Messages.BOT_COMMANDS):
        logger.info("Command menu published to Telegram")
    else:
        logger.warning("Could not publish the command menu - the previous one stays in place")

poller = None
if settings.RECEIVE_MODE == "polling" and not is_running_from_reloader():
    poller = TelegramPoller(
        settings.TELEGRAM_BOT_TOKEN,
        TelegramUpdateProcessor(
            storage, telegram_service, reservation_service, payment_reminder_service
        ),
    )
    poller.start()


_shutdown_lock = threading.Lock()
_shutdown_done = False


def shutdown() -> None:
    """
    Stop polling and take the running searches down with the app.

    Runs at most once, from whichever of the signal handler, the exit hook or
    the main loop gets here first.
    """
    global _shutdown_done
    with _shutdown_lock:
        if _shutdown_done:
            return
        _shutdown_done = True

    logger.info("Shutting down...")

    if poller:
        poller.stop()

    try:
        reservation_service.shutdown()
    except Exception as e:
        logger.error(f"Error while stopping search processes: {e}", exc_info=True)

    logger.info("Shutdown complete")


def _handle_stop_signal(signum, _frame):
    """
    Leave the main loop so the exit path runs.

    Without a handler here SIGTERM is fatal on the spot, and the searches this
    process started are left behind with nowhere to report to. In a container
    it is worse than that: the app is PID 1, and PID 1 ignores every signal it
    has not explicitly asked for - 'docker stop' would wait out its whole
    timeout and then SIGKILL the app.
    """
    logger.info(f"Received {signal.Signals(signum).name}")
    raise SystemExit(0)


# The reloader runs this module in two processes; only the one that owns the
# poller and the search processes should be tearing anything down.
if not is_running_from_reloader():
    atexit.register(shutdown)
    try:
        signal.signal(signal.SIGTERM, _handle_stop_signal)
        signal.signal(signal.SIGINT, _handle_stop_signal)
    except ValueError:
        # Only the main thread may install handlers. A WSGI server that
        # imports this module from a worker thread does its own signal
        # handling; the exit hook above still runs when it tears the worker
        # down.
        logger.debug("Not the main thread - leaving signal handling alone")

if __name__ == "__main__":
    logger.info("Starting Flask application...")
    try:
        application.run(
            debug=settings.FLASK_DEBUG,
            host=settings.FLASK_HOST,
            port=settings.FLASK_PORT,
            threaded=True,
        )
    finally:
        shutdown()
