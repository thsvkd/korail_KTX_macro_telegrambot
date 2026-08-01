"""
Tests for the inline keyboards.

A button carries the answer the typed flow expects, so the property that
matters is that every value a button can produce survives the validator that
guards its step. A button offering something validation then rejects is worse
than no button: the user tapped what they were shown and got told off for it.
"""

from datetime import datetime, timedelta

import pytest

from korail_bot.models import UserProgress
from korail_bot.telegramBot import keyboards
from korail_bot.utils.station_codes import FALLBACK_STATIONS
from korail_bot.utils.validators import InputValidator


def buttons_of(keyboard: dict) -> list[dict]:
    """Every button on a keyboard, rows flattened away."""
    return [button for row in keyboard["inline_keyboard"] for button in row]


def values_of(keyboard: dict, step: str) -> list[str]:
    """
    The answers a keyboard's buttons carry for one step, minus the escapes.

    Sentinels are dropped: "type it instead", "go back", "watch everything"
    are instructions to the handler rather than answers, so validating them
    against the step's validator would be checking the wrong thing. They are
    marked by a leading * precisely so they cannot be mistaken for answers.
    """
    values = []
    for button in buttons_of(keyboard):
        button_step, _, value = button["callback_data"].partition(":")
        if button_step == step and value != keyboards.MANUAL and not value.startswith("*"):
            values.append(value)
    return values


ALL_KEYBOARDS = {
    "start_confirm": keyboards.start_confirm_keyboard(),
    "date": keyboards.date_keyboard(),
    "src_station": keyboards.station_keyboard(keyboards.STEP_SRC_STATION),
    "dst_station": keyboards.station_keyboard(keyboards.STEP_DST_STATION),
    "dep_time": keyboards.time_keyboard(keyboards.STEP_DEP_TIME),
    "max_dep_time": keyboards.time_keyboard(keyboards.STEP_MAX_DEP_TIME, include_unlimited=True),
    "train_type": keyboards.train_type_keyboard(),
    "seat_option": keyboards.seat_option_keyboard(),
    "passenger_count": keyboards.passenger_count_keyboard(),
    "seat_strategy": keyboards.seat_strategy_keyboard(),
    "confirm": keyboards.confirm_keyboard(),
    "cancel_only": keyboards.cancel_only_keyboard(),
    "password": keyboards.password_keyboard(),
}

# The keyboards that must offer a way back, and the step each one's back
# button has to be filed under. A back button carrying the wrong step would be
# refused by the router as stale, which is the one failure mode that looks
# like the feature working right up until it does not.
BACK_STEPS = {
    "password": keyboards.STEP_PASSWORD,
    "src_station": keyboards.STEP_SRC_STATION,
    "dst_station": keyboards.STEP_DST_STATION,
    "dep_time": keyboards.STEP_DEP_TIME,
    "max_dep_time": keyboards.STEP_MAX_DEP_TIME,
    "train_type": keyboards.STEP_TRAIN_TYPE,
    "seat_option": keyboards.STEP_SEAT_OPTION,
    "passenger_count": keyboards.STEP_PASSENGER_COUNT,
    "seat_strategy": keyboards.STEP_SEAT_STRATEGY,
    "confirm": keyboards.STEP_CONFIRM,
}


class TestKeyboardShape:
    """Structural rules the Bot API imposes on every keyboard."""

    @pytest.mark.parametrize("name", sorted(ALL_KEYBOARDS))
    def test_callback_data_fits_in_the_64_byte_limit(self, name):
        """
        Telegram measures callback_data after UTF-8 encoding.

        A Korean station name costs three bytes a character, so the margin is
        smaller than the character counts suggest. Over the limit the whole
        sendMessage is refused and the user gets no prompt at all.
        """
        for button in buttons_of(ALL_KEYBOARDS[name]):
            encoded = button["callback_data"].encode("utf-8")
            assert len(encoded) <= keyboards.CALLBACK_DATA_MAX_BYTES, (
                f"{name}: {button['callback_data']!r} is {len(encoded)} bytes"
            )

    @pytest.mark.parametrize("name", sorted(ALL_KEYBOARDS))
    def test_every_button_has_a_label_and_a_payload(self, name):
        for button in buttons_of(ALL_KEYBOARDS[name]):
            assert button["text"], f"{name}: a button with no label"
            assert ":" in button["callback_data"], f"{name}: {button['callback_data']!r}"

    @pytest.mark.parametrize("name", sorted(ALL_KEYBOARDS))
    def test_no_empty_rows(self, name):
        """An empty row renders as a gap the user cannot press."""
        for row in ALL_KEYBOARDS[name]["inline_keyboard"]:
            assert row, f"{name}: empty row"

    @pytest.mark.parametrize("name", sorted(ALL_KEYBOARDS))
    def test_every_step_used_is_one_the_router_knows(self, name):
        """
        A step missing from STEP_PROGRESS has no expected state, so the router
        refuses the press. A keyboard could otherwise ship dead buttons.
        """
        known = set(keyboards.STEP_PROGRESS) | {keyboards.STEP_CANCEL}
        for button in buttons_of(ALL_KEYBOARDS[name]):
            step = button["callback_data"].partition(":")[0]
            assert step in known, f"{name}: unknown step {step!r}"

    def test_leaving_is_always_one_press_away(self):
        """
        Every keyboard except the two-way confirmations offers a way out.

        start_confirm and confirm are excluded because their own "no" button
        is that way out.
        """
        for name, keyboard in ALL_KEYBOARDS.items():
            if name in {"start_confirm", "confirm"}:
                continue
            steps = {b["callback_data"].partition(":")[0] for b in buttons_of(keyboard)}
            assert keyboards.STEP_CANCEL in steps, f"{name}: no way to cancel"


class TestGoingBackIsOffered:
    """
    Every question with one behind it can be walked back to.

    The flow asks eleven things in a row. Without this, one wrong answer costs
    all eleven - the only remedy was /cancel and doing the whole thing again.
    """

    @pytest.mark.parametrize("name", sorted(BACK_STEPS))
    def test_the_keyboard_carries_a_back_button_for_its_own_step(self, name):
        data = {b["callback_data"] for b in buttons_of(ALL_KEYBOARDS[name])}
        assert f"{BACK_STEPS[name]}:{keyboards.BACK}" in data

    @pytest.mark.parametrize("name", ["start_confirm", "date", "cancel_only"])
    def test_the_steps_with_nothing_behind_them_offer_no_way_back(self, name):
        """
        The welcome message, the first booking question, and the phone number
        that follows a "yes, go ahead". A button that could only apologise is
        worse than no button.
        """
        values = {b["callback_data"].partition(":")[2] for b in buttons_of(ALL_KEYBOARDS[name])}
        assert keyboards.BACK not in values

    def test_the_sentinel_cannot_be_mistaken_for_an_answer(self):
        """
        Buttons and typing share one state machine, so this value passes
        through the same validators every real answer does.
        """
        assert keyboards.BACK.startswith("*")
        assert not keyboards.BACK[1:].isdigit()


class TestStepProgressMapping:
    """The table that tells a fresh press from a stale one."""

    def test_every_step_maps_to_a_distinct_progress_state(self):
        """
        Two steps sharing a state would make each other's buttons look fresh,
        which is exactly the confusion the mapping exists to prevent.
        """
        states = list(keyboards.STEP_PROGRESS.values())
        assert len(states) == len(set(states))

    def test_cancel_is_deliberately_not_in_the_mapping(self):
        """Cancelling is valid at every step, so it has no expected state."""
        assert keyboards.STEP_CANCEL not in keyboards.STEP_PROGRESS

    def test_confirm_is_the_last_step_before_the_search_starts(self):
        assert keyboards.STEP_PROGRESS[keyboards.STEP_CONFIRM] == (
            UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        )

    def test_train_selection_comes_between_seat_strategy_and_confirmation(self):
        """
        Which trains to watch is asked once every other parameter is known,
        so the list shown is the list the search would actually cover.
        """
        assert keyboards.STEP_PROGRESS[keyboards.STEP_TRAIN_SELECT] == (
            UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        )

    def test_only_train_selection_repeats(self):
        """
        Every other step closes its question when answered. Marking one
        repeatable by mistake would leave its keyboard live after the answer.
        """
        assert set(keyboards.REPEATABLE_STEPS) == {keyboards.STEP_TRAIN_SELECT}

    def test_repeatable_steps_are_real_steps(self):
        assert set(keyboards.STEP_PROGRESS).issuperset(keyboards.REPEATABLE_STEPS)


class TestValuesPassTheirValidators:
    """The core property: a button never offers what validation would reject."""

    def test_date_buttons_are_all_accepted(self):
        for value in values_of(keyboards.date_keyboard(), keyboards.STEP_DATE):
            is_valid, error = InputValidator.validate_date(value)
            assert is_valid, f"{value}: {error}"

    def test_date_buttons_start_today_and_run_forward(self):
        values = values_of(keyboards.date_keyboard(), keyboards.STEP_DATE)
        assert len(values) == keyboards.DATE_QUICK_DAYS
        assert values == sorted(values)
        assert values[0] == datetime.now().strftime("%Y%m%d")

    def test_date_buttons_do_not_reach_past_the_one_year_booking_window(self):
        """validate_date refuses anything more than a year out."""
        last = values_of(keyboards.date_keyboard(), keyboards.STEP_DATE)[-1]
        assert last < (datetime.now() + timedelta(days=365)).strftime("%Y%m%d")

    def test_date_buttons_are_still_valid_at_the_turn_of_a_year(self):
        """
        Built from the local clock, so a December keyboard has to roll into
        January rather than offering the 32nd.
        """
        values = values_of(
            keyboards.date_keyboard(today=datetime(2026, 12, 28)), keyboards.STEP_DATE
        )
        assert "20270101" in values
        assert values[-1] == "20270105"

    @pytest.mark.parametrize(
        ("step", "unlimited"),
        [(keyboards.STEP_DEP_TIME, False), (keyboards.STEP_MAX_DEP_TIME, True)],
    )
    def test_time_buttons_are_all_accepted(self, step, unlimited):
        for value in values_of(keyboards.time_keyboard(step, include_unlimited=unlimited), step):
            if value == "2400":
                # The handler special-cases this one; the validator does not.
                continue
            is_valid, error = InputValidator.validate_time(value)
            assert is_valid, f"{value}: {error}"

    def test_the_time_keyboard_covers_every_hour_of_the_day(self):
        values = values_of(
            keyboards.time_keyboard(keyboards.STEP_DEP_TIME), keyboards.STEP_DEP_TIME
        )
        assert values == [f"{hour:02d}00" for hour in range(24)]

    def test_only_the_closing_time_offers_no_limit(self):
        """
        2400 bounds a search that should not be bounded. As an opening time
        it would mean "no trains at all".
        """
        opening = keyboards.time_keyboard(keyboards.STEP_DEP_TIME)
        closing = keyboards.time_keyboard(keyboards.STEP_MAX_DEP_TIME, include_unlimited=True)
        assert "2400" not in values_of(opening, keyboards.STEP_DEP_TIME)
        assert "2400" in values_of(closing, keyboards.STEP_MAX_DEP_TIME)

    def test_train_type_buttons_are_all_accepted(self):
        for value in values_of(keyboards.train_type_keyboard(), keyboards.STEP_TRAIN_TYPE):
            is_valid, error = InputValidator.validate_train_type_choice(value)
            assert is_valid, f"{value}: {error}"

    def test_seat_option_buttons_are_all_accepted(self):
        values = values_of(keyboards.seat_option_keyboard(), keyboards.STEP_SEAT_OPTION)
        assert values == ["1", "2", "3", "4"]
        for value in values:
            is_valid, error = InputValidator.validate_special_option_choice(value)
            assert is_valid, f"{value}: {error}"

    def test_passenger_count_buttons_are_all_accepted(self):
        values = values_of(keyboards.passenger_count_keyboard(), keyboards.STEP_PASSENGER_COUNT)
        assert values == [str(count) for count in range(1, 10)]
        for value in values:
            is_valid, error = InputValidator.validate_passenger_count(value)
            assert is_valid, f"{value}: {error}"

    def test_seat_strategy_buttons_are_all_accepted(self):
        for value in values_of(keyboards.seat_strategy_keyboard(), keyboards.STEP_SEAT_STRATEGY):
            is_valid, error = InputValidator.validate_seat_strategy_choice(value)
            assert is_valid, f"{value}: {error}"

    @pytest.mark.parametrize(
        ("keyboard", "step"),
        [
            (keyboards.start_confirm_keyboard(), keyboards.STEP_START_CONFIRM),
            (keyboards.confirm_keyboard(), keyboards.STEP_CONFIRM),
        ],
    )
    def test_confirmation_buttons_are_read_as_yes_and_no(self, keyboard, step):
        answers = [InputValidator.validate_yes_no(value)[0] for value in values_of(keyboard, step)]
        assert answers == [True, False]


class TestStationKeyboard:
    """Station buttons name real stations, and never a pointless journey."""

    @pytest.mark.parametrize("station", keyboards.MAJOR_STATIONS)
    def test_every_offered_station_exists(self, station):
        """
        Checked against the offline snapshot rather than is_valid_station,
        which reaches for the live Korail list. A name that is in the snapshot
        is one the bot can validate even with the network down.
        """
        assert station in FALLBACK_STATIONS

    @pytest.mark.parametrize("station", keyboards.MAJOR_STATIONS)
    def test_every_offered_station_passes_name_validation(self, station):
        """
        Guards the rules that have nothing to do with the station list: no
        '역' suffix, a length bound, a character set. '울산(통도사)' has
        brackets in it, so this is not academic.
        """
        from unittest.mock import patch

        with patch("korail_bot.utils.station_codes.is_valid_station", return_value=True):
            is_valid, error = InputValidator.validate_station_name(station)
        assert is_valid, f"{station}: {error}"

    def test_the_arrival_keyboard_drops_the_departure_station(self):
        keyboard = keyboards.station_keyboard(keyboards.STEP_DST_STATION, exclude="서울")
        assert "서울" not in values_of(keyboard, keyboards.STEP_DST_STATION)
        assert "부산" in values_of(keyboard, keyboards.STEP_DST_STATION)

    def test_excluding_a_station_that_is_not_offered_changes_nothing(self):
        """The departure station is often typed, and often not on the list."""
        keyboard = keyboards.station_keyboard(keyboards.STEP_DST_STATION, exclude="가평")
        assert len(values_of(keyboard, keyboards.STEP_DST_STATION)) == len(keyboards.MAJOR_STATIONS)


class TestButtonLabel:
    """Reading back what was pressed, for the record left in the chat."""

    def test_finds_the_label_for_the_pressed_button(self):
        keyboard = keyboards.train_type_keyboard()
        assert keyboards.button_label(keyboard, f"{keyboards.STEP_TRAIN_TYPE}:2") == "🚂 모든 열차"

    def test_returns_none_when_the_data_is_not_on_the_keyboard(self):
        assert keyboards.button_label(keyboards.train_type_keyboard(), "tt:9") is None

    @pytest.mark.parametrize("markup", [None, {}, "not a dict", {"inline_keyboard": None}])
    def test_survives_markup_that_is_missing_or_malformed(self, markup):
        """
        Telegram omits reply_markup for a message that has none, and the
        label is decoration - failing to find one must not cost the press.
        """
        assert keyboards.button_label(markup, "tt:1") is None

    def test_survives_a_row_that_is_not_a_list(self):
        assert keyboards.button_label({"inline_keyboard": ["nonsense"]}, "tt:1") is None
