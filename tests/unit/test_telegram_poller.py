"""
Unit tests for the polling receive mode.

These run without Redis, Docker or a network: the HTTP session is replaced
by a scripted stand-in.
"""

import importlib
import json
import logging
import os
import threading
from unittest.mock import Mock, patch

import pytest
import requests

import korail_bot.config.settings
from korail_bot.config.settings import Settings
from korail_bot.services.telegram_poller import TelegramPoller


def make_response(status_code=200, payload=None):
    """Build a stand-in for a requests.Response."""
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload if payload is not None else {"ok": True, "result": []}
    return response


def updates_response(*updates):
    """Build a successful getUpdates response carrying the given updates."""
    return make_response(payload={"ok": True, "result": list(updates)})


def make_update(update_id, text="안녕하세요", chat_id=42):
    """Build a realistic Telegram message update."""
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id}, "text": text},
    }


def build_poller(processor=None):
    """
    Build a poller whose backoff is short enough for a test to sit through.

    The values are set on the instance so that they also apply to the reads
    inside the polling loop.
    """
    poller = TelegramPoller("test-token", processor or Mock())
    poller.INITIAL_BACKOFF_SECONDS = 0.01
    poller.MAX_BACKOFF_SECONDS = 0.04
    poller._backoff = poller.INITIAL_BACKOFF_SECONDS
    poller.session = Mock()
    return poller


def run_poller(poller, responses):
    """
    Run the polling loop over a scripted list of getUpdates outcomes.

    Each entry is either a response object or an exception to raise. Once the
    script is exhausted the loop is asked to stop, so run() returns after one
    more (empty, successful) poll.

    Returns:
        List of {'url', 'kwargs', 'backoff'} recorded per request, where
        'backoff' is the delay the poller would have used at that point
    """
    scripted = list(responses)
    calls = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs, "backoff": poller._backoff})

        if url.endswith("/deleteWebhook"):
            return make_response()

        if not scripted:
            poller.stop()
            return updates_response()

        outcome = scripted.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    poller.session.get.side_effect = fake_get
    poller.run()
    return calls


def polls(calls):
    """Keep only the getUpdates requests out of a recorded call list."""
    return [call for call in calls if call["url"].endswith("/getUpdates")]


class TestOffsetTracking:
    """The offset is what stops Telegram from redelivering old updates."""

    def test_offset_advances_past_processed_updates(self):
        processor = Mock()
        poller = build_poller(processor)

        calls = run_poller(poller, [updates_response(make_update(100), make_update(101))])

        assert processor.process.call_count == 2
        assert poller._offset == 102

        # The next poll acknowledges both updates.
        assert polls(calls)[1]["kwargs"]["params"]["offset"] == 102

    def test_first_poll_sends_no_offset(self):
        """
        A restart must not acknowledge updates it has not seen.

        Without an offset Telegram replays whatever is still pending, which
        is exactly the messages users sent while the bot was down.
        """
        poller = build_poller()

        calls = run_poller(poller, [updates_response(make_update(7))])

        assert "offset" not in polls(calls)[0]["kwargs"]["params"]

    def test_poll_parameters(self):
        poller = build_poller()

        calls = run_poller(poller, [])

        params = polls(calls)[0]["kwargs"]["params"]
        assert params["timeout"] == Settings.TELEGRAM_POLL_TIMEOUT
        # Telegram expects a JSON array. callback_query has to be on it:
        # a button press arrives as nothing else, and an omitted kind is one
        # Telegram never delivers - every inline keyboard in the bot would
        # look pressed and do nothing.
        assert json.loads(params["allowed_updates"]) == ["message", "callback_query"]

    def test_request_timeout_exceeds_the_long_poll(self):
        """An idle long poll must not look like a hung connection."""
        poller = build_poller()

        calls = run_poller(poller, [])

        assert polls(calls)[0]["kwargs"]["timeout"] > poller.poll_timeout


class TestMalformedUpdates:
    """One bad update must not cost us the rest of the batch."""

    def test_update_without_update_id_is_skipped(self):
        processor = Mock()
        poller = build_poller(processor)

        run_poller(
            poller,
            [
                updates_response(
                    make_update(1),
                    {"message": {"chat": {"id": 42}, "text": "no update_id"}},
                    make_update(3),
                )
            ],
        )

        processed_ids = [call.args[0]["update_id"] for call in processor.process.call_args_list]
        assert processed_ids == [1, 3]
        assert poller._offset == 4

    def test_non_dict_update_is_skipped(self):
        processor = Mock()
        poller = build_poller(processor)

        run_poller(poller, [updates_response("not an update", make_update(9))])

        assert processor.process.call_count == 1
        assert poller._offset == 10

    def test_batch_with_nothing_to_acknowledge_backs_off(self):
        """
        A batch where no update carries an update_id cannot be acknowledged,
        so Telegram hands back the same batch on the next call. Without a
        pause that spins the loop as fast as the network allows - measured at
        roughly 6000 requests per second before this was handled.
        """
        poller = build_poller()

        calls = polls(
            run_poller(
                poller,
                [
                    updates_response({"message": {"chat": {"id": 42}, "text": "no id"}}),
                ],
            )
        )

        assert len(calls) >= 2
        # The poll after the unusable batch waited, so the backoff had grown.
        assert calls[1]["backoff"] > poller.INITIAL_BACKOFF_SECONDS

    def test_empty_batch_does_not_back_off(self):
        """A long poll that simply timed out is not a failure."""
        poller = build_poller()

        calls = polls(run_poller(poller, [updates_response(), updates_response()]))

        assert all(call["backoff"] == poller.INITIAL_BACKOFF_SECONDS for call in calls)

    def test_failing_processor_does_not_abort_the_batch(self):
        processor = Mock()
        processor.process.side_effect = [RuntimeError("boom"), None]
        poller = build_poller(processor)

        run_poller(poller, [updates_response(make_update(1), make_update(2))])

        assert processor.process.call_count == 2
        # The poisonous update is acknowledged too - a redelivery would only
        # make it explode again, forever.
        assert poller._offset == 3


class TestErrorHandling:
    """The polling thread has to survive whatever Telegram does."""

    def test_http_error_backs_off_then_recovers(self):
        processor = Mock()
        poller = build_poller(processor)

        calls = run_poller(
            poller,
            [
                make_response(status_code=500),
                updates_response(make_update(1)),
            ],
        )

        recorded = polls(calls)
        # The failure grew the delay before the retry...
        assert recorded[1]["backoff"] > recorded[0]["backoff"]
        # ...and the successful retry reset it.
        assert recorded[2]["backoff"] == poller.INITIAL_BACKOFF_SECONDS
        assert processor.process.call_count == 1

    def test_backoff_grows_and_is_capped(self):
        poller = build_poller()

        calls = run_poller(poller, [make_response(status_code=500)] * 8)

        delays = [call["backoff"] for call in polls(calls)]
        assert delays[1] == pytest.approx(poller.INITIAL_BACKOFF_SECONDS * 2)
        # The last recorded poll is the one that stops the loop, so look at
        # the delay the eighth failure had to wait out.
        assert delays[7] == poller.MAX_BACKOFF_SECONDS

    def test_network_error_does_not_kill_the_loop(self):
        processor = Mock()
        poller = build_poller(processor)

        run_poller(
            poller,
            [
                requests.ConnectionError("no route to host"),
                requests.Timeout("read timed out"),
                updates_response(make_update(1)),
            ],
        )

        assert processor.process.call_count == 1

    def test_non_json_body_does_not_kill_the_loop(self):
        processor = Mock()
        poller = build_poller(processor)

        broken = make_response()
        broken.json.side_effect = ValueError("not json")

        run_poller(poller, [broken, updates_response(make_update(1))])

        assert processor.process.call_count == 1

    def test_api_level_error_does_not_kill_the_loop(self):
        processor = Mock()
        poller = build_poller(processor)

        run_poller(
            poller,
            [
                make_response(payload={"ok": False, "description": "Unauthorized"}),
                updates_response(make_update(1)),
            ],
        )

        assert processor.process.call_count == 1

    def test_conflict_is_reported_actionably(self, caplog):
        """409 means someone else is consuming this bot token."""
        poller = build_poller()

        with caplog.at_level(logging.ERROR):
            run_poller(poller, [make_response(status_code=409)])

        message = " ".join(record.getMessage() for record in caplog.records)
        assert "409" in message
        assert "one consumer per bot token" in message


class TestWebhookRemoval:
    """getUpdates is refused with 409 while a webhook is registered."""

    def test_delete_webhook_precedes_the_first_poll(self):
        poller = build_poller()

        calls = run_poller(poller, [updates_response()])

        assert calls[0]["url"].endswith("/deleteWebhook")
        # Pending updates are real user messages - keep them.
        assert calls[0]["kwargs"]["params"]["drop_pending_updates"] == "false"
        assert calls[1]["url"].endswith("/getUpdates")

    def test_failing_delete_webhook_does_not_stop_polling(self):
        processor = Mock()
        poller = build_poller(processor)
        scripted = [updates_response(make_update(1))]

        def fake_get(url, **kwargs):
            if url.endswith("/deleteWebhook"):
                raise requests.ConnectionError("no route to host")
            if not scripted:
                poller.stop()
                return updates_response()
            return scripted.pop(0)

        poller.session.get.side_effect = fake_get
        poller.run()

        assert processor.process.call_count == 1


class TestLifecycle:
    """start() and stop() manage a daemon thread."""

    def test_stop_terminates_the_loop(self):
        poller = build_poller()
        polled = threading.Event()

        def fake_get(url, **kwargs):
            if url.endswith("/getUpdates"):
                polled.set()
            return updates_response()

        poller.session.get.side_effect = fake_get

        poller.start()
        assert polled.wait(timeout=5), "the poller never called getUpdates"

        poller.stop()
        poller._thread.join(timeout=5)

        assert not poller._thread.is_alive()

    def test_thread_is_a_daemon(self):
        """The process must not hang on shutdown waiting for the poller."""
        poller = build_poller()
        poller.session.get.side_effect = lambda url, **kwargs: updates_response()

        poller.start()
        try:
            assert poller._thread.daemon is True
        finally:
            poller.stop()
            poller._thread.join(timeout=5)

    def test_start_is_idempotent(self):
        poller = build_poller()
        poller.session.get.side_effect = lambda url, **kwargs: updates_response()

        poller.start()
        try:
            first_thread = poller._thread
            poller.start()
            assert poller._thread is first_thread
        finally:
            poller.stop()
            poller._thread.join(timeout=5)


class TestSettings:
    def test_missing_bot_token_is_still_rejected(self):
        with patch.object(Settings, "TELEGRAM_BOT_TOKEN", ""):
            with pytest.raises(ValueError, match="BOTTOKEN"):
                Settings.validate()


class TestFlaskHostDefault:
    """The internal callback server stays on loopback unless overridden."""

    @staticmethod
    def _reload_with(env):
        """
        Read FLASK_HOST as a fresh import of config.settings would see it.

        The module is reloaded a second time on the way out so that the rest
        of the suite keeps the environment it started with.
        """
        original = {key: os.environ.get(key) for key in env}
        try:
            for key, value in env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            return importlib.reload(korail_bot.config.settings).Settings.FLASK_HOST
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            importlib.reload(korail_bot.config.settings)

    def test_default_binds_to_loopback(self):
        host = self._reload_with({"FLASK_HOST": None})

        assert host == "127.0.0.1"

    def test_explicit_value_wins(self):
        host = self._reload_with({"FLASK_HOST": "192.0.2.10"})

        assert host == "192.0.2.10"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
