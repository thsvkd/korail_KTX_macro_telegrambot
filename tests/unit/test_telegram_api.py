"""
The one place this bot talks to Telegram.

Every message a user ever sees goes through here, and the rest of the test
suite replaces it with a Mock - which means the payloads it builds were, until
now, asserted about nowhere at all. Telegram does not truncate or repair a bad
payload; it refuses the whole call and the message simply never arrives.

The other half of what this covers is that a refusal stays a refusal. Messages
go out from reminder threads and from the shutdown path, and an exception
escaping here would take the thread - or the shutdown - with it.
"""

from typing import ClassVar
from unittest.mock import Mock, patch

import pytest

from korail_bot.services.telegram_service import MESSAGE_TEXT_LIMIT, TelegramService

CHAT_ID = 12345


def response(ok=True, result=None, description=None, status_code=200):
    """What requests hands back, as far as this service reads it."""
    body = {"ok": ok}
    if result is not None:
        body["result"] = result
    if description is not None:
        body["description"] = description

    reply = Mock(status_code=status_code)
    reply.json.return_value = body
    return reply


class ServiceFixture:
    def setup_method(self):
        self.service = TelegramService("test-token")
        self.service.session = Mock()
        self.answer(response(result={}))

    def answer(self, reply):
        """What Telegram says next."""
        self.service.session.post.return_value = reply

    def refuse(self, error):
        """...or fails to say anything at all."""
        self.service.session.post.side_effect = error

    def called(self):
        return self.service.session.post.call_args

    def method(self):
        return self.called().args[0].rsplit("/", 1)[1]

    def payload(self):
        return self.called().kwargs["json"]


class TestHowTheCallIsMade(ServiceFixture):
    """The shape of every request, which is the same for all of them."""

    def test_the_payload_is_posted_as_json(self):
        """
        Not as query parameters: reply_markup is a nested object, and a
        keyboard flattened into a query string is a keyboard Telegram
        rejects.
        """
        self.service.send_message(CHAT_ID, "안녕하세요")

        assert "json" in self.called().kwargs
        assert self.method() == "sendMessage"

    def test_the_token_is_in_the_url_and_not_the_body(self):
        self.service.send_message(CHAT_ID, "안녕하세요")

        assert "test-token" in self.called().args[0]
        assert "test-token" not in str(self.payload())

    def test_a_call_that_hangs_is_given_up_on(self):
        """
        A send with no timeout can hold a reminder thread forever, and the
        reminders exist because the user has ten minutes to pay.
        """
        self.service.send_message(CHAT_ID, "안녕하세요")

        assert self.called().kwargs["timeout"] > 0


class TestSendMessage(ServiceFixture):
    """The call behind nearly every message this bot sends."""

    def test_the_text_reaches_the_chat(self):
        assert self.service.send_message(CHAT_ID, "안녕하세요") is True
        assert self.payload() == {"chat_id": CHAT_ID, "text": "안녕하세요"}

    def test_a_keyboard_rides_along_when_there_is_one(self):
        markup = {"inline_keyboard": [[{"text": "네", "callback_data": "y"}]]}

        self.service.send_message(CHAT_ID, "고르세요", reply_markup=markup)

        assert self.payload()["reply_markup"] == markup

    def test_markup_is_off_unless_asked_for(self):
        """
        Nearly every message this bot sends carries a station name or
        something a user typed. Turning markup on for those would mean a
        stray < costing the whole send.
        """
        self.service.send_message(CHAT_ID, "a < b")

        assert "parse_mode" not in self.payload()

    def test_markup_can_be_asked_for(self):
        self.service.send_message(CHAT_ID, "<b>공지</b>", parse_mode="HTML")

        assert self.payload()["parse_mode"] == "HTML"

    def test_a_rejected_message_is_reported_as_not_sent(self):
        """
        Telegram says no by answering 200 with ok:false, so the status code
        alone would read this as a success.
        """
        self.answer(response(ok=False, description="chat not found"))

        assert self.service.send_message(CHAT_ID, "안녕하세요") is False

    @pytest.mark.parametrize(
        "error",
        [ConnectionError("no route"), TimeoutError(), RuntimeError("something else")],
        ids=["refused", "timeout", "unexpected"],
    )
    def test_a_send_that_fails_stays_a_failed_send(self, error):
        """
        Everything, not just RequestException. This is called from reminder
        threads and from the shutdown path, where a raised exception costs
        more than the message did.
        """
        self.refuse(error)

        assert self.service.send_message(CHAT_ID, "안녕하세요") is False

    def test_a_body_that_is_not_json_is_survived_too(self):
        """A proxy or a captive portal answering instead of Telegram."""
        reply = Mock(status_code=200)
        reply.json.side_effect = ValueError("Expecting value")
        self.answer(reply)

        assert self.service.send_message(CHAT_ID, "안녕하세요") is False


class TestSendAndGetId(ServiceFixture):
    """
    For the messages that get rewritten in place.

    The train list is one message the user ticks items off. Editing it needs
    the message id; without it every tick would send a fresh copy of the list.
    """

    def test_the_new_messages_id_comes_back(self):
        self.answer(response(result={"message_id": 77}))

        assert self.service.send_and_get_id(CHAT_ID, "열차 목록") == 77

    def test_a_failed_send_has_no_id(self):
        self.answer(response(ok=False, description="blocked by user"))

        assert self.service.send_and_get_id(CHAT_ID, "열차 목록") is None

    def test_an_answer_without_an_id_is_not_guessed_at(self):
        self.answer(response(result={}))

        assert self.service.send_and_get_id(CHAT_ID, "열차 목록") is None

    def test_a_keyboard_rides_along_here_too(self):
        markup = {"inline_keyboard": []}
        self.answer(response(result={"message_id": 1}))

        self.service.send_and_get_id(CHAT_ID, "열차 목록", reply_markup=markup)

        assert self.payload()["reply_markup"] == markup


class TestSendToMultiple(ServiceFixture):
    """Broadcasts: the release announcement, and the operator's notices."""

    def test_it_counts_the_ones_that_landed(self):
        self.service.session.post.side_effect = [
            response(result={}),
            response(ok=False, description="blocked by user"),
            response(result={}),
        ]

        assert self.service.send_to_multiple([1, 2, 3], "공지") == 2

    def test_one_blocked_chat_does_not_stop_the_rest(self):
        self.service.session.post.side_effect = [
            ConnectionError("no route"),
            response(result={}),
        ]

        assert self.service.send_to_multiple([1, 2], "공지") == 1

    def test_markup_carries_through_to_every_chat(self):
        self.service.send_to_multiple([1, 2], "<b>공지</b>", parse_mode="HTML")

        for call in self.service.session.post.call_args_list:
            assert call.kwargs["json"]["parse_mode"] == "HTML"

    def test_nobody_to_tell_is_not_an_error(self):
        assert self.service.send_to_multiple([], "공지") == 0
        self.service.session.post.assert_not_called()


class TestAnswerCallbackQuery(ServiceFixture):
    """
    Acknowledging a button press.

    Required, and required promptly: until it arrives the client keeps a
    progress indicator spinning on the button, and Telegram gives up on the
    query after a few seconds.
    """

    def test_a_bare_acknowledgement_carries_only_the_query(self):
        assert self.service.answer_callback_query("q-1") is True
        assert self.payload() == {"callback_query_id": "q-1"}

    def test_a_notice_can_be_shown_with_it(self):
        self.service.answer_callback_query("q-1", "이미 지난 질문입니다")

        assert self.payload()["text"] == "이미 지난 질문입니다"

    def test_something_worth_stopping_for_can_be_a_dialog(self):
        self.service.answer_callback_query("q-1", "확인하세요", show_alert=True)

        assert self.payload()["show_alert"] is True

    def test_an_empty_notice_is_left_off_rather_than_sent_blank(self):
        self.service.answer_callback_query("q-1", "")

        assert "text" not in self.payload()

    def test_a_refused_acknowledgement_is_reported(self):
        self.answer(response(ok=False, description="query is too old"))

        assert self.service.answer_callback_query("q-1") is False


class TestEditingAMessage(ServiceFixture):
    """Rewriting what has already been sent, rather than sending it again."""

    def test_the_text_is_replaced(self):
        assert self.service.edit_message_text(CHAT_ID, 77, "새 내용") is True
        assert self.method() == "editMessageText"
        assert self.payload() == {"chat_id": CHAT_ID, "message_id": 77, "text": "새 내용"}

    def test_the_keyboard_can_be_replaced_with_it(self):
        markup = {"inline_keyboard": []}

        self.service.edit_message_text(CHAT_ID, 77, "새 내용", reply_markup=markup)

        assert self.payload()["reply_markup"] == markup

    def test_text_over_telegrams_limit_is_not_even_attempted(self):
        """
        Telegram truncates nothing - it refuses the whole call. Refusing it
        here costs a round trip and says why in the log.
        """
        assert self.service.edit_message_text(CHAT_ID, 77, "가" * (MESSAGE_TEXT_LIMIT + 1)) is False
        self.service.session.post.assert_not_called()

    def test_text_exactly_at_the_limit_is_allowed(self):
        assert self.service.edit_message_text(CHAT_ID, 77, "a" * MESSAGE_TEXT_LIMIT) is True

    def test_a_keyboard_can_be_changed_on_its_own(self):
        """
        For settling a question: the answer stays on screen, the buttons
        stop being pressable.
        """
        assert self.service.edit_message_reply_markup(CHAT_ID, 77, {}) is True
        assert self.method() == "editMessageReplyMarkup"
        assert self.payload()["reply_markup"] == {}

    def test_an_edit_telegram_refuses_is_reported(self):
        """
        The commonest one by far is "message is not modified", which happens
        whenever a user presses the button that is already ticked.
        """
        self.answer(response(ok=False, description="message is not modified"))

        assert self.service.edit_message_text(CHAT_ID, 77, "같은 내용") is False


class TestTheCommandMenu(ServiceFixture):
    """Publishing the list Telegram shows behind its menu button."""

    COMMANDS: ClassVar = [{"command": "start", "description": "예매 시작"}]

    def test_the_default_list_has_no_scope(self):
        """Which is what makes it the one every chat falls back to."""
        assert self.service.set_my_commands(self.COMMANDS) is True
        assert self.method() == "setMyCommands"
        assert self.payload() == {"commands": self.COMMANDS}

    def test_a_chats_own_list_names_the_chat(self):
        self.service.set_my_commands(self.COMMANDS, chat_id=CHAT_ID)

        assert self.payload()["scope"] == {"type": "chat", "chat_id": CHAT_ID}

    def test_a_chats_list_can_be_taken_away(self):
        """
        How a chat stops being a developer chat. Deleting it rather than
        overwriting it with the public list is what makes the chat fall back
        to the default, so it keeps up when the default changes.
        """
        assert self.service.delete_my_commands(CHAT_ID) is True
        assert self.method() == "deleteMyCommands"
        assert self.payload() == {"scope": {"type": "chat", "chat_id": CHAT_ID}}

    def test_telegram_being_unreachable_is_reported_rather_than_raised(self):
        """This runs at startup and inside /devoff; neither may fail on it."""
        self.refuse(ConnectionError("no route"))

        assert self.service.set_my_commands(self.COMMANDS) is False
        assert self.service.delete_my_commands(CHAT_ID) is False


class TestTheMiniAppMenuButton(ServiceFixture):
    """The persistent button beside the chat input, separate from commands."""

    def test_a_web_app_becomes_the_default_chat_menu_button(self):
        assert (
            self.service.set_chat_menu_button(
                "예약 열기", "https://example.test/app?transport=start"
            )
            is True
        )
        assert self.method() == "setChatMenuButton"
        assert self.payload() == {
            "menu_button": {
                "type": "web_app",
                "text": "예약 열기",
                "web_app": {"url": "https://example.test/app?transport=start"},
            }
        }

    def test_the_default_command_button_can_be_restored(self):
        assert self.service.reset_chat_menu_button() is True
        assert self.method() == "setChatMenuButton"
        assert self.payload() == {"menu_button": {"type": "default"}}

    def test_the_bot_username_can_be_discovered_without_exposing_the_token(self):
        self.answer(response(result={"id": 1, "username": "rail_bot"}))

        assert self.service.get_bot_username() == "rail_bot"
        assert self.method() == "getMe"
        assert self.payload() == {}


class TestTheServiceItself:
    """Construction, which decides where the calls go."""

    def test_the_token_it_is_given_is_the_one_it_uses(self):
        service = TelegramService("a-different-token")

        assert "a-different-token" in service.base_url

    def test_no_token_falls_back_to_the_configured_one(self):
        from korail_bot.config.settings import settings

        assert TelegramService().bot_token == settings.TELEGRAM_BOT_TOKEN

    def test_one_session_is_reused_for_every_call(self):
        """
        A fresh connection per message is a TLS handshake per message, and
        the reminder loop sends one a minute for ten minutes.
        """
        service = TelegramService("test-token")

        with patch.object(service.session, "post") as post:
            post.return_value = response(result={})
            service.send_message(CHAT_ID, "하나")
            service.send_message(CHAT_ID, "둘")

        assert post.call_count == 2
