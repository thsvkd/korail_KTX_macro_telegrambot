"""
Tests for inline keyboard button presses.

Two things have to hold. A press must reach the same handler a typed answer
would, so buttons cannot grow a second, divergent state machine. And a press
on an old message must be refused - keyboards stay in the chat history
forever, every value they carry is a bare digit or date, and the step being
answered is the only thing that tells last week's "특실만" from this
minute's "4명".
"""

from unittest.mock import Mock

import pytest

from korail_bot.handlers import TelegramUpdateProcessor
from korail_bot.models import UserProgress, UserSession
from korail_bot.services import (
    PaymentReminderService,
    ReservationService,
    TelegramService,
)
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot import keyboards

CHAT_ID = 12345
MESSAGE_ID = 777


def callback_update(data, chat_id=CHAT_ID, message_id=MESSAGE_ID, reply_markup=None, text="질문"):
    """A callback_query update shaped the way the Bot API sends one."""
    message = {"message_id": message_id, "chat": {"id": chat_id}}
    if text is not None:
        message["text"] = text
    if reply_markup is not None:
        message["reply_markup"] = reply_markup

    return {
        "update_id": 1,
        "callback_query": {
            "id": "cbq-1",
            "from": {"id": chat_id},
            "message": message,
            "data": data,
        },
    }


class TestCallbackRouting:
    """Presses that should be acted on, and presses that should not."""

    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.processor = TelegramUpdateProcessor(
            self.storage,
            self.telegram,
            Mock(spec=ReservationService),
            Mock(spec=PaymentReminderService),
        )
        self.processor.conversation_handler = Mock()
        self.processor.command_handler = Mock()

    def at_progress(self, progress):
        """Put the stored session at a given point in the flow."""
        self.storage.get_user_session.return_value = UserSession(
            chat_id=CHAT_ID, in_progress=True, last_action=progress
        )

    # ---------- the press is acted on ----------

    def test_a_press_at_the_expected_step_is_handled_as_typed_input(self):
        """
        The value on the button is exactly what the user would have typed, so
        it goes to the conversation handler untouched.
        """
        self.at_progress(UserProgress.MAX_DEP_TIME_INPUT_SUCCESS)

        self.processor.process(callback_update(f"{keyboards.STEP_TRAIN_TYPE}:1"))

        self.processor.conversation_handler.handle_message.assert_called_once_with(CHAT_ID, "1")

    @pytest.mark.parametrize(
        ("step", "value"),
        [
            (keyboards.STEP_START_CONFIRM, "Y"),
            (keyboards.STEP_DATE, "20260801"),
            (keyboards.STEP_SRC_STATION, "서울"),
            (keyboards.STEP_DST_STATION, "부산"),
            (keyboards.STEP_DEP_TIME, "0800"),
            (keyboards.STEP_MAX_DEP_TIME, "2400"),
            (keyboards.STEP_TRAIN_TYPE, "2"),
            (keyboards.STEP_SEAT_OPTION, "3"),
            (keyboards.STEP_PASSENGER_COUNT, "4"),
            (keyboards.STEP_SEAT_STRATEGY, "1"),
            (keyboards.STEP_CONFIRM, "Y"),
        ],
    )
    def test_every_step_dispatches_its_value_when_the_session_is_there(self, step, value):
        self.at_progress(keyboards.STEP_PROGRESS[step])

        self.processor.process(callback_update(f"{step}:{value}"))

        self.processor.conversation_handler.handle_message.assert_called_once_with(CHAT_ID, value)

    def test_a_value_containing_a_colon_survives_intact(self):
        """
        Only the first colon separates step from value. Nothing offered today
        contains another, but splitting on all of them would quietly truncate
        the day something does.
        """
        self.at_progress(UserProgress.DATE_INPUT_SUCCESS)

        self.processor.process(callback_update(f"{keyboards.STEP_SRC_STATION}:서울:역"))

        self.processor.conversation_handler.handle_message.assert_called_once_with(
            CHAT_ID, "서울:역"
        )

    # ---------- the press is refused ----------

    def test_a_press_from_an_earlier_step_is_ignored(self):
        """
        The seat option keyboard's "특실만" carries "4". Pressed while the
        bot is asking how many passengers there are, dispatching it would
        book four seats - so it is refused, loudly enough for the user to
        understand why nothing happened.
        """
        self.at_progress(UserProgress.SPECIAL_INPUT_SUCCESS)  # asking for passenger count

        self.processor.process(callback_update(f"{keyboards.STEP_SEAT_OPTION}:4"))

        self.processor.conversation_handler.handle_message.assert_not_called()
        _, kwargs = self.telegram.answer_callback_query.call_args
        assert kwargs.get("show_alert") is True

    def test_a_press_from_a_later_step_is_ignored(self):
        self.at_progress(UserProgress.STARTED)

        self.processor.process(callback_update(f"{keyboards.STEP_CONFIRM}:Y"))

        self.processor.conversation_handler.handle_message.assert_not_called()

    def test_a_press_with_no_session_at_all_is_ignored(self):
        """A restart or a /cancel leaves the keyboards behind in the chat."""
        self.storage.get_user_session.return_value = None

        self.processor.process(callback_update(f"{keyboards.STEP_TRAIN_TYPE}:1"))

        self.processor.conversation_handler.handle_message.assert_not_called()

    def test_a_press_while_a_search_is_running_is_ignored(self):
        """
        FINDING_TICKET is not the expected state for any step, so the buttons
        from the flow that started the search go quiet once it has.
        """
        self.at_progress(UserProgress.FINDING_TICKET)

        self.processor.process(callback_update(f"{keyboards.STEP_CONFIRM}:Y"))

        self.processor.conversation_handler.handle_message.assert_not_called()

    def test_a_refused_press_has_its_buttons_taken_away(self):
        """Leaving them there invites the same press again."""
        self.at_progress(UserProgress.STARTED)

        self.processor.process(callback_update(f"{keyboards.STEP_CONFIRM}:Y"))

        self.telegram.edit_message_reply_markup.assert_called_once_with(
            CHAT_ID, MESSAGE_ID, {"inline_keyboard": []}
        )

    def test_an_unknown_step_is_refused(self):
        """An older or newer build of the bot could have sent it."""
        self.at_progress(UserProgress.STARTED)

        self.processor.process(callback_update("zz:1"))

        self.processor.conversation_handler.handle_message.assert_not_called()
        self.telegram.answer_callback_query.assert_called_once()

    def test_choosing_to_type_instead_dispatches_nothing(self):
        """
        The escape hatch on the date, station and time keyboards. There is no
        answer to record - the user is about to type one.
        """
        self.at_progress(UserProgress.PW_INPUT_SUCCESS)

        self.processor.process(callback_update(f"{keyboards.STEP_DATE}:{keyboards.MANUAL}"))

        self.processor.conversation_handler.handle_message.assert_not_called()
        self.telegram.edit_message_reply_markup.assert_called_once()

    # ---------- cancelling ----------

    def test_the_cancel_button_cancels_from_any_step(self):
        self.at_progress(UserProgress.DEP_TIME_INPUT_SUCCESS)

        self.processor.process(callback_update(f"{keyboards.STEP_CANCEL}:cancel"))

        self.processor.command_handler.handle_cancel.assert_called_once_with(CHAT_ID)

    def test_the_cancel_button_works_with_no_session(self):
        """
        Deliberately not subject to the staleness check: a user pressing
        cancel wants out, and telling them their exit button has expired
        would be absurd.
        """
        self.storage.get_user_session.return_value = None

        self.processor.process(callback_update(f"{keyboards.STEP_CANCEL}:cancel"))

        self.processor.command_handler.handle_cancel.assert_called_once_with(CHAT_ID)


class TestCallbackAcknowledgement:
    """Telegram spins the button until the query is answered."""

    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.processor = TelegramUpdateProcessor(
            self.storage,
            self.telegram,
            Mock(spec=ReservationService),
            Mock(spec=PaymentReminderService),
        )
        self.processor.conversation_handler = Mock()
        self.processor.command_handler = Mock()

    @pytest.mark.parametrize(
        "data",
        [
            f"{keyboards.STEP_TRAIN_TYPE}:1",  # accepted
            f"{keyboards.STEP_CONFIRM}:Y",  # stale
            "zz:1",  # unknown step
            f"{keyboards.STEP_CANCEL}:cancel",  # cancel
            f"{keyboards.STEP_DATE}:{keyboards.MANUAL}",  # typing instead
        ],
    )
    def test_every_outcome_answers_the_query(self, data):
        """
        Including the refusals. An unanswered query leaves a progress
        indicator on the button until Telegram times it out, which reads as
        a bot that has hung rather than one that said no.
        """
        self.storage.get_user_session.return_value = UserSession(
            chat_id=CHAT_ID, in_progress=True, last_action=UserProgress.MAX_DEP_TIME_INPUT_SUCCESS
        )

        self.processor.process(callback_update(data))

        self.telegram.answer_callback_query.assert_called_once()

    def test_a_query_with_no_id_is_dropped_rather_than_guessed_at(self):
        update = callback_update("tt:1")
        del update["callback_query"]["id"]

        self.processor.process(update)

        self.telegram.answer_callback_query.assert_not_called()
        self.processor.conversation_handler.handle_message.assert_not_called()

    def test_a_query_whose_message_is_gone_is_answered_not_crashed(self):
        """Telegram stops sending the message once it is too old to edit."""
        update = callback_update("tt:1")
        del update["callback_query"]["message"]

        self.processor.process(update)

        self.telegram.answer_callback_query.assert_called_once()
        self.processor.conversation_handler.handle_message.assert_not_called()

    def test_a_query_with_no_data_is_answered_and_ignored(self):
        update = callback_update("tt:1")
        del update["callback_query"]["data"]

        self.processor.process(update)

        self.telegram.answer_callback_query.assert_called_once()
        self.processor.conversation_handler.handle_message.assert_not_called()


class TestAnsweredQuestionIsRecorded:
    """What the chat looks like afterwards."""

    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.processor = TelegramUpdateProcessor(
            self.storage,
            self.telegram,
            Mock(spec=ReservationService),
            Mock(spec=PaymentReminderService),
        )
        self.processor.conversation_handler = Mock()
        self.storage.get_user_session.return_value = UserSession(
            chat_id=CHAT_ID, in_progress=True, last_action=UserProgress.MAX_DEP_TIME_INPUT_SUCCESS
        )

    def test_the_chosen_label_is_written_onto_the_question(self):
        """
        Otherwise the transcript is a column of questions with no answers,
        and the user cannot check what they picked three steps ago.
        """
        self.processor.process(
            callback_update(
                f"{keyboards.STEP_TRAIN_TYPE}:2",
                reply_markup=keyboards.train_type_keyboard(),
                text="열차 종류를 선택해주세요",
            )
        )

        args, kwargs = self.telegram.edit_message_text.call_args
        assert args[0] == CHAT_ID
        assert args[1] == MESSAGE_ID
        assert args[2].startswith("열차 종류를 선택해주세요")
        assert "🚂 모든 열차" in args[2]
        assert kwargs["reply_markup"] == {"inline_keyboard": []}

    def test_the_question_is_recorded_before_the_next_one_is_asked(self):
        """
        The answer belongs above the question that follows it. Edit after
        dispatch and the chat shows the next prompt over a question that
        still looks unanswered.
        """
        order = []
        self.telegram.edit_message_text.side_effect = lambda *a, **k: order.append("edit")
        self.processor.conversation_handler.handle_message.side_effect = lambda *a, **k: (
            order.append("dispatch")
        )

        self.processor.process(
            callback_update(
                f"{keyboards.STEP_TRAIN_TYPE}:2", reply_markup=keyboards.train_type_keyboard()
            )
        )

        assert order == ["edit", "dispatch"]

    def test_an_unlabelled_button_still_clears_the_keyboard(self):
        """
        The label is decoration. Failing to find one must not leave a live
        keyboard on an answered question.
        """
        self.processor.process(callback_update(f"{keyboards.STEP_TRAIN_TYPE}:2", reply_markup=None))

        _, kwargs = self.telegram.edit_message_text.call_args
        assert kwargs["reply_markup"] == {"inline_keyboard": []}

    def test_a_message_without_text_has_its_keyboard_stripped_instead(self):
        """editMessageText cannot turn a photo into a text message."""
        self.processor.process(callback_update(f"{keyboards.STEP_TRAIN_TYPE}:2", text=None))

        self.telegram.edit_message_text.assert_not_called()
        self.telegram.edit_message_reply_markup.assert_called_once()

    def test_the_press_is_still_dispatched_when_the_edit_fails(self):
        """
        Editing is cosmetic; the answer is not. A failed edit must not cost
        the user their choice.
        """
        self.telegram.edit_message_text.side_effect = Exception("Bad Request")

        self.processor.process(callback_update(f"{keyboards.STEP_TRAIN_TYPE}:2"))

        # process() swallows the exception, so the dispatch is what proves
        # the edit is not on the critical path.
        assert self.processor.conversation_handler.handle_message.called


class TestDeadSearchButtons:
    """
    The buttons offered when a search stopped on its own.

    Not part of the conversation: the conversation was reset when the search
    died, so there is no progress state for these to match and the staleness
    check that guards every other button would refuse them all. They are also
    genuinely not stale-able - a search stays dead until something is done
    about it, and the answer is as good an hour later as it was at the time.
    """

    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.reservation = Mock(spec=ReservationService)
        self.processor = TelegramUpdateProcessor(
            self.storage,
            self.telegram,
            self.reservation,
            Mock(spec=PaymentReminderService),
        )
        self.processor.conversation_handler = Mock()
        self.processor.command_handler = Mock()
        # No conversation in progress, which is the state a death leaves
        # behind and the one that would fail a progress check.
        self.storage.get_user_session.return_value = None

    def test_resume_starts_the_search_again(self):
        self.processor.process(callback_update(f"{keyboards.STEP_DEAD}:{keyboards.DEAD_RESUME}"))

        self.reservation.resume_dead_search.assert_called_once_with(CHAT_ID)

    def test_discard_drops_it(self):
        self.reservation.discard_dead_search.return_value = True

        self.processor.process(callback_update(f"{keyboards.STEP_DEAD}:{keyboards.DEAD_DISCARD}"))

        self.reservation.discard_dead_search.assert_called_once_with(CHAT_ID)
        assert "정리" in self.telegram.send_message.call_args.args[1]

    def test_discarding_one_already_gone_says_so(self):
        self.reservation.discard_dead_search.return_value = False

        self.processor.process(callback_update(f"{keyboards.STEP_DEAD}:{keyboards.DEAD_DISCARD}"))

        assert "이미 정리된" in self.telegram.send_message.call_args.args[1]

    def test_the_press_is_not_refused_as_stale(self):
        """
        The guard that protects the conversation must not reach these. With
        no session at all, a progress check would reject the press and the
        user would be left with a dead search and no way to act on it.
        """
        self.processor.process(callback_update(f"{keyboards.STEP_DEAD}:{keyboards.DEAD_RESUME}"))

        self.telegram.answer_callback_query.assert_called_once_with("cbq-1")
        self.reservation.resume_dead_search.assert_called_once()

    def test_the_conversation_never_sees_it(self):
        """These answer no question the conversation asked."""
        self.processor.process(callback_update(f"{keyboards.STEP_DEAD}:{keyboards.DEAD_DISCARD}"))

        self.processor.conversation_handler.handle_message.assert_not_called()
