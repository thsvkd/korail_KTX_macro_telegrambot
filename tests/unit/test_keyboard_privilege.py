"""
A keyboard is only as private as the chat it was sent to.

The operator's lists - pending access requests, approved users - arrive as
inline keyboards, and a Telegram message can be forwarded. Forwarded with its
buttons intact, so the press comes back to this bot carrying whatever chat
pressed it. Nothing about the callback says it came from the chat the list was
sent to.

Which makes the check on the way in the whole of the access control for those
screens: without it, anyone an operator forwards a message to can approve
their own account, or revoke everyone else's. The commands that open the lists
check; so must the buttons they put on screen.

The other half of this file is the opposite concern. Settling a keyboard -
writing the choice onto the message and taking the buttons away - is
presentation. It runs before the answer is dispatched, so a failure there must
never cost the user the choice they just made.
"""

from unittest.mock import Mock

import pytest

from korail_bot.handlers import TelegramUpdateProcessor
from korail_bot.services import PaymentReminderService, ReservationService, TelegramService
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot import keyboards

CHAT_ID = 12345
MESSAGE_ID = 777
HASH = "a1b2c3d4"


def press(data, text="목록", reply_markup=None):
    """A button press as the Bot API delivers one."""
    message = {"chat": {"id": CHAT_ID}, "message_id": MESSAGE_ID}
    if text is not None:
        message["text"] = text
    if reply_markup is not None:
        message["reply_markup"] = reply_markup
    return {"id": "q-1", "data": data, "message": message}


class ProcessorFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.is_developer.return_value = False
        self.storage.is_admin_authenticated.return_value = False
        self.telegram = Mock(spec=TelegramService)
        self.processor = TelegramUpdateProcessor(
            self.storage,
            self.telegram,
            Mock(spec=ReservationService),
            Mock(spec=PaymentReminderService),
        )
        self.command_handler = Mock()
        self.command_handler.may_administer.side_effect = lambda chat_id: (
            self.storage.is_developer(chat_id) or self.storage.is_admin_authenticated(chat_id)
        )
        self.processor.command_handler = self.command_handler


class TestTheOperatorsButtons(ProcessorFixture):
    """
    Checked on the way in, not only on the way out.

    A forwarded list keeps its buttons, and the press it produces is
    indistinguishable from one made in the chat the list was sent to.
    """

    @pytest.mark.parametrize(
        ("step", "value"),
        [
            (keyboards.STEP_APPROVE, f"{keyboards.APPROVE_YES}{HASH}"),
            (keyboards.STEP_APPROVE, f"{keyboards.APPROVE_NO}{HASH}"),
            (keyboards.STEP_USERS, f"{keyboards.USERS_REVOKE}{HASH}"),
        ],
        ids=["approve", "reject", "revoke"],
    )
    def test_an_ordinary_chat_cannot_press_them(self, step, value):
        self.processor.process_callback_query(press(f"{step}:{value}"))

        self.command_handler.handle_access_callback.assert_not_called()

    def test_the_buttons_are_taken_away_from_a_chat_that_may_not_use_them(self):
        """
        Leaving them would leave a forwarded message looking like a working
        control panel, inviting the next press.
        """
        self.processor.process_callback_query(
            press(f"{keyboards.STEP_APPROVE}:{keyboards.APPROVE_YES}{HASH}")
        )

        self.telegram.edit_message_reply_markup.assert_called_once()

    def test_the_press_is_still_acknowledged(self):
        """
        Telegram spins a progress indicator on the button until this arrives.
        A refusal that hangs looks like a bug rather than a refusal.
        """
        self.processor.process_callback_query(
            press(f"{keyboards.STEP_APPROVE}:{keyboards.APPROVE_YES}{HASH}")
        )

        self.telegram.answer_callback_query.assert_called_once()

    def test_a_developer_chat_may_press_them(self):
        self.storage.is_developer.return_value = True

        self.processor.process_callback_query(
            press(f"{keyboards.STEP_APPROVE}:{keyboards.APPROVE_YES}{HASH}")
        )

        self.command_handler.handle_access_callback.assert_called_once_with(
            CHAT_ID, MESSAGE_ID, keyboards.STEP_APPROVE, f"{keyboards.APPROVE_YES}{HASH}"
        )

    def test_a_password_session_may_press_them_too(self):
        """The same standing as the commands that open the lists."""
        self.storage.is_admin_authenticated.return_value = True

        self.processor.process_callback_query(
            press(f"{keyboards.STEP_USERS}:{keyboards.USERS_CLOSE}")
        )

        self.command_handler.handle_access_callback.assert_called_once()

    def test_the_users_list_is_guarded_by_the_same_check(self):
        self.processor.process_callback_query(
            press(f"{keyboards.STEP_USERS}:{keyboards.USERS_REVOKE}{HASH}")
        )

        self.command_handler.handle_access_callback.assert_not_called()


class TestSettlingTheQuestion(ProcessorFixture):
    """
    Writing the answer onto the message that asked it.

    Leaves the chat readable after the fact - a transcript of questions with
    no answers is not much of a transcript - and stops the same question being
    answered twice.
    """

    def settle(self, data, **kwargs):
        self.processor._settle_keyboard(CHAT_ID, MESSAGE_ID, press(data, **kwargs)["message"], data)

    def keyboard_with(self, label, data):
        return {"inline_keyboard": [[{"text": label, "callback_data": data}]]}

    def test_the_chosen_answer_is_written_onto_the_question(self):
        self.settle(
            "st:1", text="좌석을 고르세요", reply_markup=self.keyboard_with("특실만", "st:1")
        )

        written = self.telegram.edit_message_text.call_args.args[2]
        assert "좌석을 고르세요" in written
        assert "특실만" in written

    def test_the_buttons_come_off(self):
        self.settle("st:1", reply_markup=self.keyboard_with("특실만", "st:1"))

        assert self.telegram.edit_message_text.call_args.kwargs["reply_markup"] == (
            keyboards.empty_keyboard()
        )

    def test_a_press_whose_button_is_not_in_the_markup_leaves_the_text_alone(self):
        """
        An older build's keyboard. The buttons still have to come off; there
        is just nothing to name.
        """
        self.settle("st:9", text="질문", reply_markup=self.keyboard_with("특실만", "st:1"))

        assert self.telegram.edit_message_text.call_args.args[2] == "질문"

    def test_a_message_with_no_text_only_loses_its_buttons(self):
        """
        A photo or a sticker with a keyboard on it. editMessageText would be
        refused for the whole call.
        """
        self.settle("st:1", text=None)

        self.telegram.edit_message_text.assert_not_called()
        self.telegram.edit_message_reply_markup.assert_called_once()

    def test_a_message_nobody_kept_track_of_is_left_alone(self):
        self.processor._settle_keyboard(CHAT_ID, None, {"text": "질문"}, "st:1")

        self.telegram.edit_message_text.assert_not_called()

    def test_telegram_refusing_the_edit_does_not_cost_the_answer(self):
        """
        The property this whole path is contained for. Settling runs before
        the answer is dispatched, so a raised exception here would take the
        user's choice with it - and the choice is the part that matters.
        """
        self.telegram.edit_message_text.side_effect = Exception("message is too old")

        self.processor.process_callback_query(
            press(f"{keyboards.STEP_CANCEL}:1", reply_markup=self.keyboard_with("취소", "cx:1"))
        )

        self.command_handler.handle_cancel.assert_called_once_with(CHAT_ID)

    def test_a_keyboard_that_cannot_be_removed_is_not_an_error_either(self):
        self.telegram.edit_message_reply_markup.side_effect = Exception("message is too old")

        self.processor._remove_keyboard(CHAT_ID, MESSAGE_ID)  # must not raise

    def test_removing_a_keyboard_from_nothing_is_not_an_error(self):
        self.processor._remove_keyboard(CHAT_ID, None)

        self.telegram.edit_message_reply_markup.assert_not_called()


class TestReadingAButtonLabel:
    """Where the label written onto the question comes from."""

    def test_it_comes_off_the_markup_telegram_sent_back(self):
        """
        Rather than a second table of labels, which could drift out of step
        with the keyboards themselves.
        """
        markup = {"inline_keyboard": [[{"text": "특실만", "callback_data": "st:1"}]]}

        assert keyboards.button_label(markup, "st:1") == "특실만"

    @pytest.mark.parametrize(
        "markup",
        [
            None,
            "not a dict",
            {},
            {"inline_keyboard": None},
            {"inline_keyboard": ["not a row"]},
            {"inline_keyboard": [["not a button"]]},
            {"inline_keyboard": [[{"callback_data": "st:1"}]]},
            {"inline_keyboard": [[{"text": 5, "callback_data": "st:1"}]]},
        ],
    )
    def test_markup_it_cannot_read_yields_no_label_rather_than_raising(self, markup):
        """
        It is read from an untrusted payload, on the presentational path, in
        front of the dispatch that matters.
        """
        assert keyboards.button_label(markup, "st:1") is None
