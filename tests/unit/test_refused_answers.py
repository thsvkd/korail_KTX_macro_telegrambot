"""
An answer the bot cannot use must not take the buttons away with it.

The conversation is a chain of questions, and nearly every one of them is
asked with a keyboard. When an answer fails validation the step is re-asked -
and the re-ask has to carry the keyboard the question came with. Without it
the user is left looking at an error message, no buttons, and a chat whose
last usable control scrolled off the top; the only way forward is /cancel and
starting over from the phone number.

It is the same mistake on every step, it is invisible until somebody mistypes,
and each step re-offers its keyboard from its own line of code. So this walks
all of them.
"""

from unittest.mock import Mock

import pytest

from korail_bot.handlers.conversation_handler import ConversationHandler
from korail_bot.models import UserProgress, UserSession
from korail_bot.services import ReservationService, TelegramService
from korail_bot.services.access_service import AccessService
from korail_bot.storage.base import StorageInterface

CHAT_ID = 12345

#: Every step that validates a typed answer, with something it will refuse.
#: The answers are the shapes a person actually produces - a station that does
#: not exist, a time with 70 minutes in it, a count outside the range Korail
#: sells - rather than empty strings.
REFUSED = [
    ("_handle_start_confirmation", "아마도"),
    ("_handle_src_station_input", "서울역앞"),
    ("_handle_dst_station_input", "없는역"),
    ("_handle_dep_time_input", "0970"),
    ("_handle_max_dep_time_input", "2570"),
    ("_handle_train_type_input", "9"),
    ("_handle_special_option_input", "9"),
    ("_handle_passenger_count_input", "0"),
    ("_handle_seat_strategy_input", "9"),
    ("_handle_date_input", "20200101"),
]


class TestARefusedAnswerKeepsItsButtons:
    """The dead end this exists to prevent."""

    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.handler = ConversationHandler(
            self.storage,
            self.telegram,
            Mock(spec=ReservationService),
            Mock(spec=AccessService),
        )
        self.session = UserSession(chat_id=CHAT_ID, in_progress=True)
        self.session.last_action = UserProgress.STARTED
        self.session.train_info = {"srcLocate": "서울"}

    def refuse(self, step, answer):
        getattr(self.handler, step)(CHAT_ID, answer, self.session)
        return self.telegram.send_message.call_args

    @pytest.mark.parametrize(("step", "answer"), REFUSED, ids=[step for step, _ in REFUSED])
    def test_the_question_is_asked_again_with_its_keyboard(self, step, answer):
        call = self.refuse(step, answer)

        assert call is not None, "거절만 하고 아무 말도 하지 않았습니다"
        assert call.kwargs.get("reply_markup"), "버튼 없이 되물으면 막다른 길입니다"

    @pytest.mark.parametrize(("step", "answer"), REFUSED, ids=[step for step, _ in REFUSED])
    def test_the_refusal_says_something(self, step, answer):
        """
        An empty error would be a message the user cannot act on, sent
        instead of the answer they expected.
        """
        assert self.refuse(step, answer).args[1].strip()

    @pytest.mark.parametrize(("step", "answer"), REFUSED, ids=[step for step, _ in REFUSED])
    def test_the_step_does_not_move_on(self, step, answer):
        """
        Recording a refused answer would carry a station that does not exist
        into the search.
        """
        before = self.session.last_action

        self.refuse(step, answer)

        assert self.session.last_action == before

    def test_an_answer_it_can_use_does_move_the_step_on(self):
        """
        The positive control for the check above. Without it, a step that
        never recorded its progress at all would pass "does not move on" for
        the wrong reason.
        """
        self.handler._handle_src_station_input(CHAT_ID, "부산", self.session)

        assert self.session.last_action == UserProgress.SRC_LOCATE_INPUT_SUCCESS

    def test_the_destination_re_ask_still_excludes_the_departure(self):
        """
        The keyboard is rebuilt on the way back, and the one thing it has to
        keep is that 서울 → 서울 is not offered.
        """
        self.session.train_info = {"srcLocate": "서울"}

        markup = self.refuse("_handle_dst_station_input", "없는역").kwargs["reply_markup"]

        labels = [button["text"] for row in markup["inline_keyboard"] for button in row]
        assert "서울" not in labels

    def test_the_latest_time_re_ask_still_offers_no_limit(self):
        """
        "제한 없음" is only on this step's keyboard. Losing it on the re-ask
        would leave the user unable to pick the answer they were reaching
        for when they mistyped.
        """
        markup = self.refuse("_handle_max_dep_time_input", "2570").kwargs["reply_markup"]

        assert any("제한" in button["text"] for row in markup["inline_keyboard"] for button in row)
