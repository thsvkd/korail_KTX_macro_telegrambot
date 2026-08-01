"""Pytest configuration and shared fixtures.

The package is installed (editable) into the environment, so there is no
sys.path juggling here - `import korail_bot...` just works.

tests/integration and tests/e2e talk to a real Redis, which testcontainers
starts for them and which therefore needs a Docker daemon. tests/unit does
not touch Redis at all, so a run restricted to that directory skips the
container entirely and needs no Docker.
"""

import os
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_UNIT_DIR = _TESTS_DIR / "unit"

_redis_container = None


def _only_unit_tests(config) -> bool:
    """
    True when every path on the command line lives under tests/unit.

    Deliberately conservative: anything it cannot place - a bare `pytest`, a
    `-k` expression, a path outside tests/unit - counts as "might need Redis"
    and the container is started as before.
    """
    args = [arg for arg in config.args if not arg.startswith("-")]
    if not args:
        return False

    for arg in args:
        # Strip the '::TestClass::test_name' part of a node id.
        path = Path(arg.split("::", 1)[0]).resolve()
        if not path.is_relative_to(_UNIT_DIR):
            return False
    return True


def pytest_configure(config):
    """Set up the environment, and a Redis container when one is needed."""
    global _redis_container

    # Secrets the application expects. Set before any project module is
    # imported so that korail_bot.config.settings picks them up.
    os.environ.setdefault("BOTTOKEN", "test-bot-token")
    os.environ.setdefault("SESSION_SECRET", "test-session-secret")
    os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

    # A real USERID/USERPW inherited from the developer's environment would
    # make the bot skip the login prompts, so every test of those prompts
    # would exercise a different flow than it means to. Tests that want the
    # skip set it explicitly.
    os.environ.pop("USERID", None)
    os.environ.pop("USERPW", None)

    os.environ["REDIS_DB"] = "0"
    # The throwaway container runs without auth, and the unit tests never
    # connect at all. A REDIS_PASSWORD inherited from the developer's .env
    # would fail with "AUTH called without any password configured".
    os.environ.pop("REDIS_PASSWORD", None)

    if _only_unit_tests(config):
        # Nothing here opens a connection; these just keep settings importable.
        os.environ.setdefault("REDIS_HOST", "localhost")
        os.environ.setdefault("REDIS_PORT", "6379")
        return

    from testcontainers.redis import RedisContainer

    _redis_container = RedisContainer("redis:7-alpine")
    _redis_container.start()

    os.environ["REDIS_HOST"] = _redis_container.get_container_host_ip()
    os.environ["REDIS_PORT"] = str(_redis_container.get_exposed_port(6379))


def pytest_unconfigure(config):
    """Clean up the Redis container after all tests."""
    global _redis_container
    if _redis_container:
        _redis_container.stop()
        _redis_container = None


@pytest.fixture(scope="session")
def redis_container():
    """The running Redis container, or None when the run skipped it."""
    return _redis_container
