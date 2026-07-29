"""Pytest configuration and shared fixtures."""
import os
import sys
from pathlib import Path
import pytest
from testcontainers.redis import RedisContainer

# Add src/ to Python path so tests can import project modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Start Redis container before any imports
_redis_container = None


def pytest_configure(config):
    """Set up Redis container before test collection."""
    global _redis_container

    # Secrets the application expects. Set before any project module is
    # imported so that config.settings picks them up.
    os.environ.setdefault("BOTTOKEN", "test-bot-token")
    os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret")
    os.environ.setdefault("SESSION_SECRET", "test-session-secret")
    os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

    # A real USERID/USERPW inherited from .env (pipenv loads it automatically)
    # would make the bot skip the login prompts, so every test of those
    # prompts would exercise a different flow than it means to. Tests that
    # want the skip set it explicitly.
    os.environ.pop("USERID", None)
    os.environ.pop("USERPW", None)

    _redis_container = RedisContainer("redis:7-alpine")
    _redis_container.start()

    # Set environment variables
    os.environ["REDIS_HOST"] = _redis_container.get_container_host_ip()
    os.environ["REDIS_PORT"] = str(_redis_container.get_exposed_port(6379))
    os.environ["REDIS_DB"] = "0"
    # The throwaway container runs without auth. A REDIS_PASSWORD inherited
    # from .env (pipenv loads it automatically) would make every connection
    # fail with "AUTH called without any password configured".
    os.environ.pop("REDIS_PASSWORD", None)


def pytest_unconfigure(config):
    """Clean up Redis container after all tests."""
    global _redis_container
    if _redis_container:
        _redis_container.stop()


@pytest.fixture(scope="session")
def redis_container():
    """Get the running Redis container."""
    return _redis_container
