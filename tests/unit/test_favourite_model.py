"""
What a saved search holds, and what it deliberately does not.

The date is the whole design decision. A journey someone takes often is the
same route, the same time of day, the same seat preferences - and a different
day every time. A favourite that remembered last month's date would be a trap
rather than a shortcut, and the trap would be silent: the flow would sail past
the one question whose old answer is certainly wrong.
"""

import json

import pytest

from korail_bot.models import FavouriteSearch
from korail_bot.models.favourite import MAX_NAME_LENGTH, new_favourite_id
from korail_bot.telegramBot import keyboards

CHAT_ID = 1

TRAIN_INFO = {
    "depDate": "20260810",
    "srcLocate": "서울",
    "dstLocate": "부산",
    "depTime": "090000",
    "maxDepTime": "1800",
    "trainType": "TrainType.KTX",
    "trainTypeShow": "KTX 계열만",
    "specialInfo": "ReserveOption.GENERAL_FIRST",
    "specialInfoShow": "일반실 우선",
    "passengerCount": 2,
    "seatStrategy": "consecutive",
    "seatStrategyShow": "연속 좌석",
    "selectedTrains": ["101", "105"],
    "trainListMessageId": 555,
}


class TestWhatIsSaved:
    """Everything the flow asks for, except the two things that go stale."""

    def test_the_date_is_not_saved(self):
        favourite = FavouriteSearch.from_train_info(CHAT_ID, TRAIN_INFO)

        assert "20260810" not in json.dumps(favourite.as_train_info())
        assert "depDate" not in favourite.as_train_info()

    def test_the_chosen_trains_are_not_saved(self):
        """
        That list is fetched fresh for whichever date is picked. A saved
        selection would name trains that may not run that day.
        """
        favourite = FavouriteSearch.from_train_info(CHAT_ID, TRAIN_INFO)

        assert "selectedTrains" not in favourite.as_train_info()
        assert "101" not in json.dumps(favourite.as_train_info())

    @pytest.mark.parametrize(
        "key",
        [
            "srcLocate",
            "dstLocate",
            "depTime",
            "maxDepTime",
            "trainType",
            "trainTypeShow",
            "specialInfo",
            "specialInfoShow",
            "passengerCount",
            "seatStrategy",
            "seatStrategyShow",
        ],
    )
    def test_everything_else_survives_the_round_trip(self, key):
        """
        The search is driven by these. One dropped on the way through would
        be a favourite that quietly books something else.
        """
        restored = FavouriteSearch.from_train_info(CHAT_ID, TRAIN_INFO).as_train_info()

        assert restored[key] == TRAIN_INFO[key]

    def test_a_loaded_favourite_needs_exactly_one_more_answer(self):
        """
        The date, and nothing else. If this ever grows a second gap, the
        flow that skips to the train list will skip past a question.
        """
        missing = set(TRAIN_INFO) - set(
            FavouriteSearch.from_train_info(CHAT_ID, TRAIN_INFO).as_train_info()
        )

        assert missing == {"depDate", "selectedTrains", "trainListMessageId"}


class TestNaming:
    """Names are for finding one in a list, not for filing it."""

    def test_it_names_itself_after_the_route(self):
        """A shortcut that demands a name before it can be saved is no shortcut."""
        assert FavouriteSearch.from_train_info(CHAT_ID, TRAIN_INFO).name == "서울 → 부산"

    def test_a_given_name_wins(self):
        favourite = FavouriteSearch.from_train_info(CHAT_ID, TRAIN_INFO, name="주말 부산행")

        assert favourite.name == "주말 부산행"

    def test_whitespace_is_not_a_name(self):
        favourite = FavouriteSearch.from_train_info(CHAT_ID, TRAIN_INFO, name="   ")

        assert favourite.name == "서울 → 부산"

    def test_a_long_name_is_cut_to_fit_a_button(self):
        favourite = FavouriteSearch.from_train_info(CHAT_ID, TRAIN_INFO, name="가" * 200)

        assert len(favourite.name) == MAX_NAME_LENGTH


class TestIdentity:
    """Ids travel in callback_data, which Telegram measures in bytes."""

    def test_ids_do_not_repeat(self):
        assert len({new_favourite_id() for _ in range(100)}) == 100

    def test_a_row_in_the_list_fits_the_callback_limit(self):
        favourites = [FavouriteSearch.from_train_info(CHAT_ID, TRAIN_INFO) for _ in range(3)]

        keyboard = keyboards.favourites_keyboard(favourites)

        for row in keyboard["inline_keyboard"]:
            for button in row:
                encoded = button["callback_data"].encode("utf-8")
                assert len(encoded) <= keyboards.CALLBACK_DATA_MAX_BYTES

    def test_every_action_in_the_detail_screen_fits_too(self):
        fav_id = new_favourite_id()

        for keyboard in (
            keyboards.favourite_detail_keyboard(fav_id),
            keyboards.favourite_delete_keyboard(fav_id),
        ):
            for row in keyboard["inline_keyboard"]:
                for button in row:
                    assert len(button["callback_data"].encode("utf-8")) <= (
                        keyboards.CALLBACK_DATA_MAX_BYTES
                    )

    def test_the_actions_are_told_apart_by_their_prefix(self):
        """
        They all carry the same id, so the prefix is the only thing between
        opening a favourite and deleting it.
        """
        prefixes = {
            keyboards.FAV_PICK,
            keyboards.FAV_START,
            keyboards.FAV_RENAME,
            keyboards.FAV_DELETE,
            keyboards.FAV_CONFIRM_DELETE,
        }

        assert len(prefixes) == 5


class TestHowItReads:
    """The lines the detail screen is built from."""

    def test_the_window_is_shown_as_a_clock(self):
        """Korail sends HHMMSS; nobody reads a time window in seconds."""
        assert FavouriteSearch.from_train_info(CHAT_ID, TRAIN_INFO).window == "09:00~18:00"

    def test_a_favourite_saved_without_times_still_reads_as_a_window(self):
        favourite = FavouriteSearch.from_train_info(
            CHAT_ID, {"srcLocate": "서울", "dstLocate": "부산"}
        )

        assert favourite.window == "00:00~24:00"

    def test_the_route_is_one_line(self):
        assert FavouriteSearch.from_train_info(CHAT_ID, TRAIN_INFO).route == "서울 → 부산"
