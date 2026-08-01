"""
Walking back through the questions.

The flow asks eleven things in a row, and until this existed answering one of
them wrongly cost all eleven: there was no way back, so the only remedy was
/cancel and typing the lot again. A date off by a day is not a reason to
re-enter a phone number.

Two properties carry it. "뒤로" is taken before the routing, so every step
answers it the same way rather than a dozen handlers each remembering to.
And nothing is unwound: each step overwrites its own field on the way forward,
so a re-asked answer replaces the old one before anything reads it.
"""

from unittest.mock import Mock, patch

import pytest

from korail_bot.handlers import ConversationHandler
from korail_bot.models import UserCredentials, UserProgress, UserSession
from korail_bot.services import ReservationService, TelegramService
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot import keyboards

CHAT_ID = 12345


def session_at(progress: int, **train_info) -> UserSession:
    """A session sitting on one question, with the answers before it filled in."""
    session = UserSession(chat_id=CHAT_ID, in_progress=True, last_action=progress)
    session.credentials = UserCredentials(korail_id="010-1234-5678", korail_pw="pw")
    session.train_info = {
        "depDate": "20991231",
        "srcLocate": "서울",
        "dstLocate": "부산",
        "depTime": "090000",
        "maxDepTime": "1800",
        "trainType": "TrainType.KTX",
        "trainTypeShow": "KTX",
        "specialInfo": "ReserveOption.GENERAL_FIRST",
        "specialInfoShow": "GENERAL_FIRST",
        "passengerCount": 2,
        "seatStrategy": "consecutive",
        "seatStrategyShow": "연속 좌석",
        **train_info,
    }
    return session


class BackFixture:
    """A handler with the session it is walking backwards through."""

    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.handler = ConversationHandler(
            self.storage, self.telegram, Mock(spec=ReservationService)
        )

    def at(self, progress, **train_info):
        self.session = session_at(progress, **train_info)
        self.storage.get_user_session.return_value = self.session
        return self.session

    def back(self, text=keyboards.BACK):
        self.handler.handle_message(CHAT_ID, text)
        return self.session.last_action

    def last_message(self):
        return self.telegram.send_message.call_args.args[1]

    def last_keyboard(self):
        return self.telegram.send_message.call_args.kwargs.get("reply_markup")


class TestEveryStepStepsBack(BackFixture):
    """One question back, for every question that has one."""

    @pytest.mark.parametrize(
        ("here", "expected"),
        [
            (UserProgress.ID_INPUT_SUCCESS, UserProgress.START_ACCEPTED),
            (UserProgress.DATE_INPUT_SUCCESS, UserProgress.PW_INPUT_SUCCESS),
            (UserProgress.SRC_LOCATE_INPUT_SUCCESS, UserProgress.DATE_INPUT_SUCCESS),
            (UserProgress.DST_LOCATE_INPUT_SUCCESS, UserProgress.SRC_LOCATE_INPUT_SUCCESS),
            (UserProgress.DEP_TIME_INPUT_SUCCESS, UserProgress.DST_LOCATE_INPUT_SUCCESS),
            (UserProgress.MAX_DEP_TIME_INPUT_SUCCESS, UserProgress.DEP_TIME_INPUT_SUCCESS),
            (UserProgress.TRAIN_TYPE_INPUT_SUCCESS, UserProgress.MAX_DEP_TIME_INPUT_SUCCESS),
            (UserProgress.SPECIAL_INPUT_SUCCESS, UserProgress.TRAIN_TYPE_INPUT_SUCCESS),
            (UserProgress.PASSENGER_COUNT_INPUT_SUCCESS, UserProgress.SPECIAL_INPUT_SUCCESS),
        ],
    )
    def test_the_session_lands_on_the_previous_question(self, here, expected):
        self.at(here)

        assert self.back() == expected

    @pytest.mark.parametrize(
        ("here", "expected"),
        [
            (UserProgress.ID_INPUT_SUCCESS, "휴대전화번호"),
            (UserProgress.DATE_INPUT_SUCCESS, "출발 희망일"),
            (UserProgress.SRC_LOCATE_INPUT_SUCCESS, "출발역"),
            (UserProgress.DST_LOCATE_INPUT_SUCCESS, "도착역"),
            (UserProgress.DEP_TIME_INPUT_SUCCESS, "검색 시작 시각"),
            (UserProgress.MAX_DEP_TIME_INPUT_SUCCESS, "검색 종료 시각"),
            (UserProgress.TRAIN_TYPE_INPUT_SUCCESS, "열차 종류"),
            (UserProgress.SPECIAL_INPUT_SUCCESS, "좌석 종류"),
            (UserProgress.PASSENGER_COUNT_INPUT_SUCCESS, "인원수"),
        ],
    )
    def test_the_previous_question_is_asked_again(self, here, expected):
        """
        And asked as a question, not as "✅ 입력 완료". Telling someone they
        finished the step they just threw away reads as a bot that lost track.
        """
        self.at(here)
        self.back()

        assert expected in self.last_message()
        assert "이전 단계로 돌아왔습니다" in self.last_message()
        assert "입력 완료" not in self.last_message()

    @pytest.mark.parametrize(
        "here",
        [
            UserProgress.DATE_INPUT_SUCCESS,
            UserProgress.DST_LOCATE_INPUT_SUCCESS,
            UserProgress.TRAIN_TYPE_INPUT_SUCCESS,
            UserProgress.PASSENGER_COUNT_INPUT_SUCCESS,
        ],
    )
    def test_the_question_comes_back_with_its_buttons(self, here):
        """A re-asked question the user has to type is a step backwards twice."""
        self.at(here)
        self.back()

        assert self.last_keyboard()["inline_keyboard"]

    def test_the_step_the_answer_belongs_to_is_recorded(self):
        """
        The keyboard that comes back has to be filed under the step the
        session is now at, or the router refuses the next press as stale.
        """
        self.at(UserProgress.SRC_LOCATE_INPUT_SUCCESS)
        progress = self.back()

        steps = {
            button["callback_data"].partition(":")[0]
            for row in self.last_keyboard()["inline_keyboard"]
            for button in row
        }
        steps.discard(keyboards.STEP_CANCEL)
        assert all(keyboards.STEP_PROGRESS[step] == progress for step in steps)

    def test_the_arrival_keyboard_still_drops_the_departure_station(self):
        """The exclusion is not a property of walking forwards."""
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS, srcLocate="대전")
        self.back()

        values = [
            button["callback_data"].partition(":")[2]
            for row in self.last_keyboard()["inline_keyboard"]
            for button in row
        ]
        assert "대전" not in values

    def test_the_session_is_written_back(self):
        """A step back that lives only in memory is undone by the next read."""
        self.at(UserProgress.TRAIN_TYPE_INPUT_SUCCESS)
        self.back()

        self.storage.save_user_session.assert_called_with(self.session)


class TestTheStepsThatWereSkipped(BackFixture):
    """Going back has to skip whatever going forward skipped."""

    def test_one_passenger_is_never_sent_back_to_the_seat_strategy(self):
        """
        A single passenger is not asked how the seats should be arranged, so
        landing there on the way back would put a question on screen the user
        has never seen and cannot recognise.
        """
        self.at(UserProgress.SEAT_STRATEGY_INPUT_SUCCESS, passengerCount=1)

        assert self.back() == UserProgress.SPECIAL_INPUT_SUCCESS
        assert "인원수" in self.last_message()

    def test_two_passengers_are_sent_back_to_the_seat_strategy(self):
        self.at(UserProgress.SEAT_STRATEGY_INPUT_SUCCESS, passengerCount=2)

        assert self.back() == UserProgress.PASSENGER_COUNT_INPUT_SUCCESS
        assert "좌석 배치" in self.last_message()

    def test_a_session_with_no_passenger_count_is_treated_as_one(self):
        """Sessions written before the count existed outlive a deploy."""
        session = self.at(UserProgress.SEAT_STRATEGY_INPUT_SUCCESS)
        session.train_info.pop("passengerCount")

        assert self.back() == UserProgress.SPECIAL_INPUT_SUCCESS


class TestTheScreensThatRedrawThemselves(BackFixture):
    """
    The train list and the summary build their message from every answer so
    far, so going back to them is drawing them again rather than re-asking.
    """

    def test_the_summary_sends_the_user_back_to_the_train_list(self):
        self.at(UserProgress.TRAIN_SELECT_INPUT_SUCCESS)

        with (
            patch.object(ConversationHandler, "_show_train_selection") as show,
            patch.object(ConversationHandler, "_close_train_list"),
        ):
            progress = self.back()

        assert progress == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        show.assert_called_once()

    def test_the_schedule_prompt_sends_the_user_back_to_the_summary(self):
        self.at(UserProgress.SCHEDULE_INPUT_PENDING)

        assert self.back() == UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        assert "예약 정보 확인" in self.last_message()

    def test_leaving_the_train_list_takes_its_buttons_away(self):
        """
        The router leaves this keyboard alone while the list is being ticked -
        that is what makes ticking repeatable - so nobody else can clear it.
        """
        self.at(UserProgress.SEAT_STRATEGY_INPUT_SUCCESS, trainListMessageId=555)

        self.back()

        self.telegram.edit_message_reply_markup.assert_called_once_with(
            CHAT_ID, 555, {"inline_keyboard": []}
        )

    def test_a_list_that_cannot_be_cleared_does_not_cost_the_step(self):
        """Clearing it is cosmetic. The step back is not."""
        self.at(UserProgress.SEAT_STRATEGY_INPUT_SUCCESS, trainListMessageId=555)
        self.telegram.edit_message_reply_markup.side_effect = Exception("too old")

        assert self.back() == UserProgress.PASSENGER_COUNT_INPUT_SUCCESS


class TestNothingBehindIt(BackFixture):
    """The questions that open the flow."""

    @pytest.mark.parametrize(
        "here",
        [
            UserProgress.STARTED,
            # Which railway is the first question of the flow now; the one
            # behind it is "shall we begin?", answered by getting here.
            UserProgress.OPERATOR_INPUT_PENDING,
            UserProgress.PW_INPUT_SUCCESS,
        ],
    )
    def test_the_user_is_told_rather_than_ignored(self, here):
        """
        A button that silently does nothing reads as a broken bot. These
        states offer no back button, so this is a typed "뒤로" or a press on
        a keyboard from an older build.
        """
        self.at(here)

        assert self.back() == here
        assert "더 돌아갈 단계가 없습니다" in self.last_message()


class TestHowItIsAskedFor(BackFixture):
    """The sentinel a button carries, and the word a person types."""

    def test_typing_the_word_works_too(self):
        """Someone who has been pressing the button will eventually type it."""
        self.at(UserProgress.TRAIN_TYPE_INPUT_SUCCESS)

        assert self.back("뒤로") == UserProgress.MAX_DEP_TIME_INPUT_SUCCESS

    def test_surrounding_whitespace_does_not_hide_it(self):
        self.at(UserProgress.TRAIN_TYPE_INPUT_SUCCESS)

        assert self.back("  뒤로  ") == UserProgress.MAX_DEP_TIME_INPUT_SUCCESS

    def test_at_the_password_prompt_the_typed_word_is_a_password(self):
        """
        Anything typed there is a password. Reading one as a command would
        walk the user back a step instead of logging them in - and would tell
        anyone watching that this particular password is special.
        """
        self.at(UserProgress.ID_INPUT_SUCCESS)

        # Two characters, so the password validator turns it away - which is
        # the proof that it was read as one rather than as a command.
        progress = self.back("뒤로")

        assert progress == UserProgress.ID_INPUT_SUCCESS
        assert "비밀번호" in self.last_message()
        assert "휴대전화번호" not in self.last_message()

    def test_the_button_still_works_at_the_password_prompt(self):
        """Which is why it is a button there and not a word."""
        self.at(UserProgress.ID_INPUT_SUCCESS)

        assert self.back() == UserProgress.START_ACCEPTED
        assert "휴대전화번호" in self.last_message()


class TestWalkingBackAndForward(BackFixture):
    """What the answers look like after a round trip."""

    def test_the_re_asked_answer_replaces_the_old_one(self):
        """
        Nothing is unwound on the way back; the step overwrites its own field
        on the way forward. This is the property that makes that safe.
        """
        self.at(UserProgress.SRC_LOCATE_INPUT_SUCCESS, srcLocate="서울")

        self.back()
        with patch("korail_bot.utils.station_codes.is_valid_station", return_value=True):
            self.handler.handle_message(CHAT_ID, "대전")

        assert self.session.train_info["srcLocate"] == "대전"
        # And carries on from there, back onto the question it left.
        assert self.session.last_action == UserProgress.SRC_LOCATE_INPUT_SUCCESS

    def test_the_answers_further_along_are_asked_again_in_turn(self):
        """
        Going back two steps and forward two ends up where it started, having
        passed through every question in between.
        """
        self.at(UserProgress.SPECIAL_INPUT_SUCCESS)  # asking for passenger count

        self.back()  # back to the seat option
        self.handler.handle_message(CHAT_ID, "2")  # 일반실만

        assert self.session.train_info["specialInfoShow"] == "GENERAL_ONLY"
        assert self.session.last_action == UserProgress.SPECIAL_INPUT_SUCCESS


class TestTheTablesAgree:
    """The two halves of the feature have to describe the same flow."""

    def test_every_target_can_be_asked_again(self):
        """
        A target with no prompt behind it would raise out of the update
        handler, which reaches the user as silence.
        """
        storage = Mock(spec=StorageInterface)
        handler = ConversationHandler(
            storage, Mock(spec=TelegramService), Mock(spec=ReservationService)
        )

        for target in set(ConversationHandler.BACK_TARGETS.values()):
            session = session_at(target)
            with (
                patch.object(ConversationHandler, "_show_train_selection"),
                patch.object(ConversationHandler, "_show_final_confirmation"),
            ):
                handler._reask(CHAT_ID, session, target)

    def test_no_step_leads_back_to_itself(self):
        """A loop would leave "뒤로" as a button that redraws the screen."""
        for here, target in ConversationHandler.BACK_TARGETS.items():
            assert here != target

    def test_every_step_goes_backwards(self):
        """
        Progress numbers run in flow order except where the comment on
        UserProgress says otherwise, so this holds for all but the two states
        that were appended rather than inserted.
        """
        appended = {
            UserProgress.TRAIN_SELECT_INPUT_SUCCESS,
            UserProgress.SCHEDULE_INPUT_PENDING,
            # Its target, OPERATOR_INPUT_PENDING, was appended for the same
            # reason: it comes first in the conversation and last in the
            # numbering, because the numbers are stored in Redis.
            UserProgress.START_ACCEPTED,
        }
        for here, target in ConversationHandler.BACK_TARGETS.items():
            if here in appended:
                continue
            assert target < here
