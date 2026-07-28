"""
Flask application entry point for Korail KTX Telegram Bot.

This is the refactored version using the new service-oriented architecture.
"""
import sys
from flask import Flask
from flask_restful import Api
from flask_cors import CORS
from werkzeug.serving import is_running_from_reloader

from config.settings import settings
from storage.redis import RedisStorage
from services import (
    TelegramService,
    KorailService,
    ReservationService,
    PaymentReminderService,
    TelegramPoller
)
from handlers import TelegramUpdateProcessor
from api import TelegramWebhook, PaymentCheckAPI
from utils.logger import get_logger, LoggerFactory

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
    '/telebot',
    resource_class_kwargs={
        'storage': storage,
        'telegram_service': telegram_service,
        'reservation_service': reservation_service,
        'payment_reminder_service': payment_reminder_service
    }
)

api.add_resource(
    PaymentCheckAPI,
    '/check_payment',
    resource_class_kwargs={
        'storage': storage
    }
)

logger.info("="*60)
logger.info("Korail KTX Telegram Bot - Redis Version")
logger.info("="*60)
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
if settings.RECEIVE_MODE == 'webhook':
    logger.info("Webhook secret: configured")  # validate() guarantees this
else:
    logger.info("Updates: pulled with getUpdates (no public endpoint needed)")
logger.info(f"Admin commands: {'enabled' if settings.ADMIN_PASSWORD else 'disabled'}")
logger.info(
    f"Admin magic login: {'enabled' if settings.ADMIN_MAGIC_STRING else 'disabled'}"
)
logger.info(
    f"Resume on restart: {'enabled' if settings.RESUME_ON_RESTART else 'disabled'}"
)
logger.info("="*60)

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

poller = None
if settings.RECEIVE_MODE == 'polling' and not is_running_from_reloader():
    poller = TelegramPoller(
        settings.TELEGRAM_BOT_TOKEN,
        TelegramUpdateProcessor(
            storage,
            telegram_service,
            reservation_service,
            payment_reminder_service
        )
    )
    poller.start()

if __name__ == '__main__':
    logger.info("Starting Flask application...")
    application.run(
        debug=settings.FLASK_DEBUG,
        host=settings.FLASK_HOST,
        port=settings.FLASK_PORT,
        threaded=True
    )
