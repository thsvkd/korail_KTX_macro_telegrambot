"""
The screens the operator decides who may use the bot from.

Access is granted and taken away by pressing buttons in a list, which means
the list is not a display - it is the control surface. Three properties carry
the weight, and none of them is visible from the happy path.

The list is walked in place. A press that pushed a new message would leave the
operator scrolling between three copies of the same list, pressing a stale
one. The person on the other end has to be told: an approval nobody hears
about is the same as no approval, and they are sitting on a phone number they
cannot use the bot with. And a request handled twice - by a second operator,
or by the same one on an older copy of the list - has to end in the honest
answer rather than a second approval.
"""

from datetime import datetime
from unittest.mock import Mock

import pytest

from korail_bot.handlers.command_handler import CommandHandler
from korail_bot.models import AccessRequest, ApprovedUser
from korail_bot.services import PaymentReminderService, ReservationService, TelegramService
from korail_bot.services.access_service import AccessService
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot import keyboards
from korail_bot.telegramBot.messages import Messages

OPERATOR = 6824596577
MESSAGE_ID = 4242
APPLICANT = 12345
HASH = "a1b2c3d4"


def request(phone_hash=HASH, chat_id=APPLICANT):
    return AccessRequest(
        phone_hash=phone_hash,
        chat_id=chat_id,
        masked_phone="010-****-5678",
        requested_at=datetime(2026, 8, 1, 9, 30),
    )


def approved(phone_hash=HASH):
    return ApprovedUser(phone_hash=phone_hash, masked_phone="010-****-5678")


class ScreenFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.handler = CommandHandler(
            self.storage,
            self.telegram,
            Mock(spec=ReservationService),
            Mock(spec=PaymentReminderService),
        )
        self.access = Mock(spec=AccessService)
        self.access.pending_requests.return_value = []
        self.access.approved_users.return_value = []
        self.handler.access = self.access

    def press(self, step, value, message_id=MESSAGE_ID):
        self.handler.handle_access_callback(OPERATOR, message_id, step, value)

    def edited(self):
        """The text the list was rewritten to, if it was rewritten."""
        if not self.telegram.edit_message_text.called:
            return None
        return self.telegram.edit_message_text.call_args.args[2]

    def keyboard(self):
        return self.telegram.edit_message_text.call_args.kwargs.get("reply_markup")

    def told(self, chat_id):
        """What was said to somebody other than the operator."""
        return [
            call.args[1]
            for call in self.telegram.send_message.call_args_list
            if call.args[0] == chat_id
        ]


class TestOpeningTheLists(ScreenFixture):
    """/approve and /users."""

    def test_no_requests_says_so_rather_than_showing_an_empty_list(self):
        self.handler.handle_approve(OPERATOR)

        assert self.telegram.send_message.call_args.args[1] == Messages.APPROVE_EMPTY
        assert "reply_markup" not in self.telegram.send_message.call_args.kwargs

    def test_the_requests_come_with_buttons(self):
        self.access.pending_requests.return_value = [request()]

        self.handler.handle_approve(OPERATOR)

        assert self.telegram.send_message.call_args.kwargs["reply_markup"]

    def test_nobody_approved_says_so(self):
        self.handler.handle_users(OPERATOR)

        assert Messages.USERS_EMPTY in self.telegram.send_message.call_args.args[1]

    def test_the_approved_users_come_with_buttons(self):
        self.access.approved_users.return_value = [approved()]

        self.handler.handle_users(OPERATOR)

        assert self.telegram.send_message.call_args.kwargs["reply_markup"]


class TestApproving(ScreenFixture):
    """The decision, and who hears about it."""

    def test_picking_a_request_asks_before_acting(self):
        """
        Approving is not undoable from this screen, and the buttons are small
        and next to each other.
        """
        self.storage.get_access_request.return_value = request()

        self.press(keyboards.STEP_APPROVE, f"{keyboards.APPROVE_PICK}{HASH}")

        assert "010-****-5678" in self.edited()
        self.access.approve.assert_not_called()

    def test_the_request_is_dated_so_a_stale_one_can_be_told_apart(self):
        self.storage.get_access_request.return_value = request()

        self.press(keyboards.STEP_APPROVE, f"{keyboards.APPROVE_PICK}{HASH}")

        assert "08월 01일" in self.edited()

    def test_approving_grants_access_and_says_who_did_it(self):
        self.access.approve.return_value = request()

        self.press(keyboards.STEP_APPROVE, f"{keyboards.APPROVE_YES}{HASH}")

        self.access.approve.assert_called_once_with(HASH, approved_by=OPERATOR)

    def test_the_person_who_asked_is_told_they_may_use_it(self):
        """
        An approval nobody hears about is the same as no approval: they are
        sitting on a number the bot has stopped answering for.
        """
        self.access.approve.return_value = request()

        self.press(keyboards.STEP_APPROVE, f"{keyboards.APPROVE_YES}{HASH}")

        assert Messages.ACCESS_APPROVED in self.told(APPLICANT)

    def test_rejecting_is_told_to_them_too(self):
        """Otherwise they wait indefinitely on an answer that already came."""
        self.access.reject.return_value = request()

        self.press(keyboards.STEP_APPROVE, f"{keyboards.APPROVE_NO}{HASH}")

        assert Messages.ACCESS_REJECTED in self.told(APPLICANT)

    @pytest.mark.parametrize("action", [keyboards.APPROVE_YES, keyboards.APPROVE_NO])
    def test_a_request_that_was_already_handled_is_not_handled_again(self, action):
        """
        Two operators, or one operator on an older copy of the list. The
        second press has to end in the truth rather than a second approval.
        """
        self.access.approve.return_value = None
        self.access.reject.return_value = None

        self.press(keyboards.STEP_APPROVE, f"{action}{HASH}")

        assert self.edited() == Messages.APPROVE_GONE
        assert self.told(APPLICANT) == []

    def test_a_request_that_vanished_before_being_opened_says_so(self):
        self.storage.get_access_request.return_value = None

        self.press(keyboards.STEP_APPROVE, f"{keyboards.APPROVE_PICK}{HASH}")

        assert self.edited() == Messages.APPROVE_GONE

    def test_going_back_returns_to_the_list(self):
        self.access.pending_requests.return_value = [request(), request("other")]

        self.press(keyboards.STEP_APPROVE, keyboards.APPROVE_BACK)

        assert "2" in self.edited()
        assert self.keyboard()

    def test_going_back_to_a_list_that_emptied_says_so(self):
        """Somebody else dealt with the last one while this screen was open."""
        self.access.pending_requests.return_value = []

        self.press(keyboards.STEP_APPROVE, keyboards.APPROVE_BACK)

        assert self.edited() == Messages.APPROVE_EMPTY

    def test_closing_takes_the_buttons_away(self):
        """
        Leaving them would leave a stale list that still looks pressable, and
        the next press would act on a request handled minutes ago.
        """
        self.press(keyboards.STEP_APPROVE, keyboards.APPROVE_CLOSE)

        assert self.telegram.edit_message_text.call_args.kwargs["reply_markup"] == (
            keyboards.empty_keyboard()
        )

    def test_a_press_from_some_other_build_is_ignored_quietly(self):
        self.press(keyboards.STEP_APPROVE, "*something")

        self.telegram.edit_message_text.assert_not_called()
        self.access.approve.assert_not_called()


class TestTakingAccessAway(ScreenFixture):
    """/users, which is the same shape and the opposite decision."""

    def test_picking_a_user_asks_before_revoking(self):
        self.access.approved_users.return_value = [approved()]

        self.press(keyboards.STEP_USERS, f"{keyboards.USERS_PICK}{HASH}")

        assert "010-****-5678" in self.edited()
        self.access.revoke.assert_not_called()

    def test_revoking_takes_the_access_away(self):
        self.access.revoke.return_value = approved()

        self.press(keyboards.STEP_USERS, f"{keyboards.USERS_REVOKE}{HASH}")

        self.access.revoke.assert_called_once_with(HASH)
        assert "010-****-5678" in self.edited()

    def test_revoking_someone_already_revoked_says_so(self):
        self.access.revoke.return_value = None

        self.press(keyboards.STEP_USERS, f"{keyboards.USERS_REVOKE}{HASH}")

        assert self.edited() == Messages.USERS_REVOKE_GONE

    def test_opening_a_user_who_is_no_longer_there_says_so(self):
        self.access.approved_users.return_value = [approved("someone-else")]

        self.press(keyboards.STEP_USERS, f"{keyboards.USERS_PICK}{HASH}")

        assert self.edited() == Messages.USERS_REVOKE_GONE

    def test_going_back_returns_to_the_list(self):
        self.access.approved_users.return_value = [approved()]

        self.press(keyboards.STEP_USERS, keyboards.USERS_BACK)

        assert self.keyboard()

    def test_going_back_to_a_list_that_emptied_says_so(self):
        self.press(keyboards.STEP_USERS, keyboards.USERS_BACK)

        assert Messages.USERS_EMPTY in self.edited()

    def test_closing_takes_the_buttons_away(self):
        self.press(keyboards.STEP_USERS, keyboards.USERS_CLOSE)

        assert self.telegram.edit_message_text.call_args.kwargs["reply_markup"] == (
            keyboards.empty_keyboard()
        )

    def test_a_press_from_some_other_build_is_ignored_quietly(self):
        self.press(keyboards.STEP_USERS, "*something")

        self.telegram.edit_message_text.assert_not_called()


class TestTheListSurvivesTelegram(ScreenFixture):
    """
    A list that cannot be rewritten still has to deliver its outcome.

    The operator has just approved somebody. Losing the confirmation because
    the edit was refused would leave them pressing the button again.
    """

    def test_a_press_on_a_message_nobody_kept_track_of_sends_a_fresh_one(self):
        self.access.pending_requests.return_value = [request()]

        self.press(keyboards.STEP_APPROVE, keyboards.APPROVE_BACK, message_id=None)

        self.telegram.edit_message_text.assert_not_called()
        assert self.telegram.send_message.call_args.kwargs["reply_markup"]

    def test_an_edit_telegram_refuses_falls_back_to_a_new_message(self):
        self.telegram.edit_message_text.side_effect = Exception("message is too old")
        self.access.pending_requests.return_value = [request()]

        self.press(keyboards.STEP_APPROVE, keyboards.APPROVE_BACK)

        assert self.telegram.send_message.called

    def test_a_closing_message_gets_through_either_way(self):
        self.telegram.edit_message_text.side_effect = Exception("message is too old")
        self.access.approve.return_value = request()

        self.press(keyboards.STEP_APPROVE, f"{keyboards.APPROVE_YES}{HASH}")

        assert self.told(OPERATOR)
        assert Messages.ACCESS_APPROVED in self.told(APPLICANT)

    def test_closing_without_a_message_id_still_says_the_outcome(self):
        self.press(keyboards.STEP_APPROVE, keyboards.APPROVE_CLOSE, message_id=None)

        assert self.told(OPERATOR)
