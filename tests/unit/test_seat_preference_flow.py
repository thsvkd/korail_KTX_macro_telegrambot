"""
Asking which seats will do, in the chat and in the Mini App.

The question only exists where it can be answered: SR reports the seat a
booking got before it is paid for, Korail does not. So the interesting cases
are the seams - a Korail search stepping over a question it was never asked,
"뒤로" walking back over the same gap, and a Mini App page that submits a
condition Korail cannot honour.
"""

from unittest.mock import Mock, patch

import pytest

from korail_bot.handlers import ConversationHandler
from korail_bot.models import (
    Operator,
    SeatPreference,
    UserCredentials,
    UserProgress,
    UserSession,
)
from korail_bot.services import ReservationService, TelegramService
from korail_bot.services.mini_app_service import MiniAppSubmission
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot import keyboards
from korail_bot.utils.validators import InputValidator

CHAT_ID = 12345


def session_at_the_seat_question(operator=Operator.SRT, **train_info) -> UserSession:
    """A session that has answered everything up to the seat condition."""
    session = UserSession(
        chat_id=CHAT_ID,
        in_progress=True,
        last_action=UserProgress.SEAT_STRATEGY_INPUT_SUCCESS,
    )
    session.credentials = UserCredentials(korail_id="010-1234-5678", korail_pw="pw")
    session.train_info = {
        "operator": str(operator),
        "depDate": "20991231",
        "srcLocate": "수서" if operator is Operator.SRT else "서울",
        "dstLocate": "부산",
        "depTime": "090000",
        "maxDepTime": "1800",
        "trainType": "SRT" if operator is Operator.SRT else "TrainType.KTX",
        "trainTypeShow": "SRT" if operator is Operator.SRT else "KTX",
        "specialInfo": "ReserveOption.GENERAL_FIRST",
        "specialInfoShow": "GENERAL_FIRST",
        "passengerCount": 1,
        "seatStrategy": "consecutive",
        "seatStrategyShow": "1명",
        **train_info,
    }
    return session


class FlowFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_or_create_app_session_start.return_value = "1700000000000"
        self.telegram = Mock(spec=TelegramService)
        self.telegram.send_and_get_id.return_value = 555
        self.handler = ConversationHandler(
            self.storage, self.telegram, Mock(spec=ReservationService)
        )

    def at(self, operator=Operator.SRT, **train_info):
        self.session = session_at_the_seat_question(operator, **train_info)
        self.storage.get_user_session.return_value = self.session
        return self.session

    def send(self, text):
        with patch.object(ConversationHandler, "_show_train_selection") as show:
            self.handler.handle_message(CHAT_ID, text)
        return show

    def last_message(self):
        return self.telegram.send_message.call_args.args[1]

    def last_keyboard(self):
        return self.telegram.send_message.call_args.kwargs.get("reply_markup")


class TestWhoGetsAsked(FlowFixture):
    """The question appears only where the answer can be acted on."""

    def test_an_sr_search_is_asked_which_seats_will_do(self):
        self.at(Operator.SRT)

        with patch.object(ConversationHandler, "_show_train_selection") as show:
            self.handler._ask_seat_preference(CHAT_ID, self.session)

        assert "원하시는 좌석" in self.last_message()
        show.assert_not_called()

    def test_a_korail_search_steps_straight_to_the_train_list(self):
        self.at(Operator.KORAIL)

        with patch.object(ConversationHandler, "_show_train_selection") as show:
            self.handler._ask_seat_preference(CHAT_ID, self.session)

        show.assert_called_once()
        assert self.session.last_action == UserProgress.SEAT_PREFERENCE_INPUT_SUCCESS

    def test_the_prompt_warns_that_a_narrow_condition_costs_time(self):
        """
        Learning this after four hours of waiting is learning it too late.
        """
        self.at(Operator.SRT)

        self.handler._ask_seat_preference(CHAT_ID, self.session)

        assert "오래 걸립니다" in self.last_message()


class TestTicking(FlowFixture):
    """Columns toggle and leave the question open, the way trains do."""

    def test_a_column_is_ticked(self):
        self.at(Operator.SRT)

        self.send("A")

        assert self.session.train_info[ConversationHandler.SEAT_COLUMNS_KEY] == ["A"]
        assert self.session.last_action == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS

    def test_ticking_the_same_column_again_unticks_it(self):
        self.at(Operator.SRT, seatColumns=["A"])

        self.send("A")

        assert self.session.train_info[ConversationHandler.SEAT_COLUMNS_KEY] == []

    def test_columns_are_kept_in_reading_order_however_they_were_pressed(self):
        """The summary should say "A·D" whichever was tapped first."""
        self.at(Operator.SRT)

        self.send("D")
        self.send("A")

        assert self.session.train_info[ConversationHandler.SEAT_COLUMNS_KEY] == ["A", "D"]

    def test_a_typed_row_range_is_taken(self):
        self.at(Operator.SRT)

        self.send("1-15")

        assert self.session.train_info[ConversationHandler.SEAT_ROWS_KEY] == "1-15"

    def test_a_single_row_is_taken_as_a_range_of_one(self):
        self.at(Operator.SRT)

        self.send("7")

        assert self.session.train_info[ConversationHandler.SEAT_ROWS_KEY] == "7"

    def test_an_unreadable_row_range_leaves_the_question_up(self):
        self.at(Operator.SRT)

        self.send("앞쪽으로요")

        assert ConversationHandler.SEAT_ROWS_KEY not in self.session.train_info
        assert self.session.last_action == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        assert self.last_keyboard() is not None


class TestFinishing(FlowFixture):
    """Both ways out of the screen."""

    def test_done_keeps_the_condition_and_moves_on(self):
        self.at(Operator.SRT, seatColumns=["A", "D"], seatRows="1-15")

        show = self.send(keyboards.SEAT_PREFERENCE_DONE)

        assert self.session.train_info["seatPreference"] == "A,D:1-15"
        assert self.session.last_action == UserProgress.SEAT_PREFERENCE_INPUT_SUCCESS
        show.assert_called_once()

    def test_asking_for_any_seat_throws_the_ticks_away(self):
        self.at(Operator.SRT, seatColumns=["A"], seatRows="1-15")

        show = self.send(keyboards.SEAT_PREFERENCE_ANY)

        assert self.session.train_info["seatPreference"] == ""
        assert self.session.last_action == UserProgress.SEAT_PREFERENCE_INPUT_SUCCESS
        show.assert_called_once()

    def test_finishing_with_nothing_ticked_means_any_seat(self):
        self.at(Operator.SRT)

        self.send(keyboards.SEAT_PREFERENCE_DONE)

        assert self.session.train_info["seatPreference"] == ""

    def test_the_condition_reaches_the_search_parameters(self):
        session = self.at(Operator.SRT)
        session.train_info["seatPreference"] = "A,D:1-15"

        params = self.handler._build_search_params(session)

        assert params.seat_preference == "A,D:1-15"
        assert params.wants_specific_seats()
        assert params.seats_wanted == SeatPreference(columns=("A", "D"), row_min=1, row_max=15)


class TestTheSeatKeyboard:
    """What the screen offers."""

    def test_every_column_is_offered(self):
        keyboard = keyboards.seat_preference_keyboard()
        data = str(keyboard)

        for letter in ("A", "B", "C", "D"):
            assert f"{keyboards.STEP_SEAT_PREFERENCE}:{letter}" in data

    def test_ticked_columns_are_marked(self):
        keyboard = keyboards.seat_preference_keyboard(["A"])
        rows = keyboard["inline_keyboard"][0]

        assert rows[0]["text"].startswith("☑️")
        assert rows[1]["text"].startswith("⬜")

    def test_finishing_is_offered_only_once_there_is_something_to_finish(self):
        assert keyboards.SEAT_PREFERENCE_DONE not in str(keyboards.seat_preference_keyboard())
        assert keyboards.SEAT_PREFERENCE_DONE in str(keyboards.seat_preference_keyboard(["A"]))
        assert keyboards.SEAT_PREFERENCE_DONE in str(
            keyboards.seat_preference_keyboard(rows="1-15")
        )

    def test_asking_for_any_seat_is_always_offered(self):
        """It is the way out of a screen nobody wanted, and the default."""
        assert keyboards.SEAT_PREFERENCE_ANY in str(keyboards.seat_preference_keyboard())


class TestTheRowRangeValidator:
    """What may be typed where a row range is expected."""

    @pytest.mark.parametrize("text", ["1-15", "7", " 3 - 9 ", "1~15", "1–15", "99"])
    def test_readable_ranges_are_accepted(self, text):
        assert InputValidator.validate_seat_row_range(text) is None

    @pytest.mark.parametrize(
        ("text", "because"),
        [
            ("", "빈 입력"),
            ("앞쪽", "숫자가 아님"),
            ("0-5", "좌석은 1번부터"),
            ("1-100", "세 자리는 오타"),
            ("15-1", "앞이 뒤보다 큼"),
            ("1-2-3", "범위가 아님"),
        ],
    )
    def test_unreadable_ranges_are_refused_with_a_reason(self, text, because):
        assert InputValidator.validate_seat_row_range(text), because

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("1-15", (1, 15)), ("7", (7, 7)), ("1~15", (1, 15)), (" 3 - 9 ", (3, 9))],
    )
    def test_a_valid_range_is_read_the_same_way_it_was_checked(self, text, expected):
        assert InputValidator.parse_seat_row_range(text) == expected


class TestTheMiniAppContract:
    """The same condition, arriving from the page instead of the chat."""

    @staticmethod
    def submission(operator="srt", seat_preference="A,D:1-15", **extra):
        import json

        from tests.unit.test_mini_app import future_date

        return MiniAppSubmission.parse(
            json.dumps(
                {
                    "v": 1,
                    "action": "prepare_search",
                    "operator": operator,
                    "dep_date": future_date(),
                    "src_station": "수서" if operator == "srt" else "서울",
                    "dst_station": "부산",
                    "dep_time": "0700",
                    "max_dep_time": "1200",
                    "train_type": "1",
                    "seat_option": "1",
                    "passenger_count": "1",
                    "seat_strategy": "1",
                    "seat_preference": seat_preference,
                    **extra,
                },
                ensure_ascii=False,
            )
        )

    def test_an_sr_submission_carries_the_condition_through(self):
        submission = self.submission()

        assert submission.seat_preference == "A,D:1-15"
        assert submission.as_train_info()["seatPreference"] == "A,D:1-15"

    def test_a_korail_submission_has_its_condition_dropped(self):
        """
        A stale page could still show the controls. Refusing would be a dead
        end for something the user cannot fix, so the condition is discarded.
        """
        submission = self.submission(operator="korail")

        assert submission.seat_preference == ""
        assert submission.as_train_info()["seatPreference"] == ""

    @pytest.mark.parametrize("text", ["", "쓰레기", "Z,Q:", "consecutive"])
    def test_a_condition_that_cannot_be_read_means_any_seat(self, text):
        assert self.submission(seat_preference=text).seat_preference == ""

    def test_a_page_that_predates_the_field_still_parses(self):
        import json

        from tests.unit.test_mini_app import future_date

        submission = MiniAppSubmission.parse(
            json.dumps(
                {
                    "v": 1,
                    "action": "prepare_search",
                    "operator": "srt",
                    "dep_date": future_date(),
                    "src_station": "수서",
                    "dst_station": "부산",
                    "dep_time": "0700",
                    "max_dep_time": "1200",
                    "train_type": "1",
                    "seat_option": "1",
                    "passenger_count": "1",
                    "seat_strategy": "1",
                },
                ensure_ascii=False,
            )
        )

        assert submission.seat_preference == ""
