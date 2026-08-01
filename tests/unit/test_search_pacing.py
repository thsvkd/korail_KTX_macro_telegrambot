"""
Unit tests for the randomised pacing of Korail requests.

The search loop used to sleep for exactly SEARCH_INTERVAL seconds between
requests, so every request landed on a metronome. These tests pin down the
band each wait is drawn from, that the average rate is preserved, and that
setting the jitter to 0 brings the old fixed interval back.
"""

import os
from unittest.mock import patch

import pytest

from korail_bot.config.settings import Settings, _env_ratio
from korail_bot.services.korail_service import KorailService


def make_service(interval=3.0, jitter=0.4):
    """A KorailService with pacing set explicitly, no login involved."""
    service = KorailService()
    service._search_interval = interval
    service._search_jitter = jitter
    return service


def test_waits_stay_inside_the_configured_band():
    service = make_service(interval=3.0, jitter=0.4)

    waits = [service.next_interval() for _ in range(500)]

    assert all(1.8 <= wait <= 4.2 for wait in waits)


def test_waits_are_not_all_the_same():
    """The point of the change: consecutive requests are not evenly spaced."""
    service = make_service(interval=3.0, jitter=0.4)

    waits = [service.next_interval() for _ in range(20)]

    assert len(set(waits)) > 1


def test_average_wait_keeps_the_configured_rate():
    """Randomising the spacing must not quietly speed up or slow down the search."""
    service = make_service(interval=3.0, jitter=0.4)

    waits = [service.next_interval() for _ in range(2000)]
    average = sum(waits) / len(waits)

    assert average == pytest.approx(3.0, abs=0.1)


def test_zero_jitter_restores_the_fixed_interval():
    service = make_service(interval=3.0, jitter=0.0)

    assert [service.next_interval() for _ in range(10)] == [3.0] * 10


def test_multiplier_scales_the_band():
    """The longer wait between individual reservations is randomised too."""
    service = make_service(interval=2.0, jitter=0.5)

    waits = [service.next_interval(1.5) for _ in range(500)]

    assert all(1.5 <= wait <= 4.5 for wait in waits)
    assert sum(waits) / len(waits) == pytest.approx(3.0, abs=0.15)


def test_wait_is_never_negative():
    """A jitter of 1 reaches down to zero, and must not overshoot past it."""
    service = make_service(interval=1.0, jitter=1.0)

    assert all(service.next_interval() >= 0 for _ in range(500))


def test_wait_between_requests_sleeps_for_what_it_returns():
    service = make_service(interval=3.0, jitter=0.4)

    with patch("korail_bot.services.rail_service.time.sleep") as sleep:
        delay = service.wait_between_requests()

    sleep.assert_called_once_with(delay)
    assert 1.8 <= delay <= 4.2


def test_wait_seconds_randomises_a_fixed_wait():
    """The duplicate-reservation retry waits around 10s, not exactly 10s."""
    service = make_service(interval=3.0, jitter=0.4)

    with patch("korail_bot.services.rail_service.time.sleep") as sleep:
        delay = service.wait_seconds(10)

    sleep.assert_called_once_with(delay)
    assert 6.0 <= delay <= 14.0


def test_service_takes_its_pacing_from_settings():
    service = KorailService()

    assert service._search_interval == Settings.KORAIL_SEARCH_INTERVAL
    assert service._search_jitter == Settings.KORAIL_SEARCH_INTERVAL_JITTER


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("0.25", 0.25),
        ("0", 0.0),
        ("1", 1.0),
        ("2.5", 1.0),  # clamped: a jitter above 1 would ask for negative waits
        ("-0.5", 0.0),  # clamped
        ("", 0.4),  # unset in practice
        ("nonsense", 0.4),
    ],
)
def test_jitter_setting_is_clamped_to_a_usable_ratio(raw, expected):
    with patch.dict(os.environ, {"SEARCH_INTERVAL_JITTER": raw}):
        assert _env_ratio("SEARCH_INTERVAL_JITTER", 0.4) == expected


def test_jitter_setting_defaults_when_absent():
    environment = {
        key: value for key, value in os.environ.items() if key != "SEARCH_INTERVAL_JITTER"
    }

    with patch.dict(os.environ, environment, clear=True):
        assert _env_ratio("SEARCH_INTERVAL_JITTER", 0.4) == 0.4
