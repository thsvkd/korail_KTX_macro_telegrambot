"""
The endpoint that says whether a payment has been made.

The reminder loops run in the app; the reservation they are reminding about
was made by a search process that shares no memory with it. This is how the
one asks the other, and until now it was reached only over loopback by a
process no test starts.

Two things it must never do. It must not answer to anyone who asks - the
answer is per-user payment state, and the endpoint sits on a port. And it must
not answer "paid" when it does not know, because that is the reading that
stops the reminders on a seat the user still has to pay for.
"""

from unittest.mock import Mock

import pytest
from flask import Flask
from flask_restful import Api

from korail_bot.api import PaymentCheckAPI
from korail_bot.config.settings import settings
from korail_bot.models import PaymentStatus
from korail_bot.storage.base import StorageInterface

CHAT_ID = 12345


class CheckFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_payment_status.return_value = None

        app = Flask(__name__)
        Api(app).add_resource(
            PaymentCheckAPI, "/check_payment", resource_class_kwargs={"storage": self.storage}
        )
        self.client = app.test_client()

    def check(self, chat_id=CHAT_ID, token=None):
        params = {} if chat_id is None else {"chatId": chat_id}
        params["token"] = settings.INTERNAL_CALLBACK_TOKEN if token is None else token
        return self.client.get("/check_payment", query_string=params)


class TestWhoMayAsk(CheckFixture):
    """It exposes per-user payment state, so not everyone."""

    def test_an_internal_caller_is_answered(self):
        assert self.check().status_code == 200

    def test_a_caller_without_the_token_is_refused(self):
        response = self.check(token="")

        assert response.status_code == 403
        self.storage.get_payment_status.assert_not_called()

    def test_a_caller_with_the_wrong_token_is_refused(self):
        assert self.check(token="not-the-token").status_code == 403

    def test_being_refused_says_nothing_about_the_payment(self):
        """A 403 that leaked the answer would not be much of a 403."""
        assert b"completed" not in self.check(token="wrong").data


class TestWhatItAnswers(CheckFixture):
    """The answer itself, and what it means when it is not known."""

    def test_a_completed_payment_is_reported_as_completed(self):
        self.storage.get_payment_status.return_value = PaymentStatus(
            chat_id=CHAT_ID, completed=True, reminder_active=False
        )

        assert self.check().get_json() == {"completed": True}

    def test_an_outstanding_payment_is_reported_as_outstanding(self):
        self.storage.get_payment_status.return_value = PaymentStatus(
            chat_id=CHAT_ID, completed=False, reminder_active=True
        )

        assert self.check().get_json() == {"completed": False}

    def test_the_chat_id_is_looked_up_as_a_number(self):
        self.check()

        self.storage.get_payment_status.assert_called_once_with(CHAT_ID)

    def test_a_chat_with_no_payment_on_record_has_not_paid(self):
        """
        Not an error: the reminder starts before the record settles, and the
        honest answer to "has this been paid for" is no.
        """
        assert self.check().get_json() == {"completed": False}


class TestNotKnowingIsNotAYes(CheckFixture):
    """
    Every failure answers "not paid".

    The caller is a reminder loop, and the only thing it does with a yes is
    stop reminding. Answering yes because Redis was briefly unreadable would
    silently end the reminders on a seat the user still owes money for, and
    they would find out when the reservation expired.
    """

    def test_no_chat_id_is_not_a_yes(self):
        assert self.check(chat_id=None).get_json() == {"completed": False}

    @pytest.mark.parametrize("chat_id", ["", "abc", "12.5"])
    def test_a_chat_id_that_is_not_a_number_is_not_a_yes(self, chat_id):
        assert self.check(chat_id=chat_id).get_json() == {"completed": False}

    def test_redis_being_unreadable_is_not_a_yes(self):
        self.storage.get_payment_status.side_effect = Exception("redis is down")

        assert self.check().get_json() == {"completed": False}
