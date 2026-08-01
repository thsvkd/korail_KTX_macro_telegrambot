"""
Unit tests for which railway a search belongs to.

The bot ran against one company for its whole life, so nothing it stored says
which one. Those records did not stop existing when a second was added, and
every one of them is a Korail search - a user waiting on a search started
yesterday must not have it come back as an SRT one, or fail to come back at
all. Most of what is here is about that.
"""

import pytest

from korail_bot.models import (
    KORAIL_MAJOR_STATIONS,
    SRT_MAJOR_STATIONS,
    SRT_STATIONS,
    FavouriteSearch,
    Operator,
    TrainSearchParams,
)
from korail_bot.telegramBot import keyboards


class TestReadingOneBack:
    """
    Operator.parse is the single place that decides what a stored value meant,
    so that the dozen places that read one do not each get to have an opinion.
    """

    def test_a_missing_operator_is_korail(self):
        """Every record written before there were two is a Korail search."""
        assert Operator.parse(None) is Operator.KORAIL

    def test_an_empty_operator_is_korail(self):
        assert Operator.parse("") is Operator.KORAIL

    def test_srt_is_read_back_as_srt(self):
        assert Operator.parse("srt") is Operator.SRT

    def test_korail_is_read_back_as_korail(self):
        assert Operator.parse("korail") is Operator.KORAIL

    def test_case_and_padding_do_not_matter(self):
        assert Operator.parse("  SRT ") is Operator.SRT

    def test_an_operator_survives_a_round_trip_through_itself(self):
        assert Operator.parse(Operator.SRT) is Operator.SRT

    def test_an_unrecognised_operator_falls_back_rather_than_raising(self):
        """
        A name nobody knows is a bug somewhere else. Refusing to run would
        strand a search the user is waiting on instead of fixing it.
        """
        assert Operator.parse("shinkansen") is Operator.KORAIL

    def test_a_number_is_not_mistaken_for_an_operator(self):
        assert Operator.parse(7) is Operator.KORAIL


class TestWhatEachOperatorOffers:
    def test_each_is_named_the_way_the_user_would_say_it(self):
        assert Operator.KORAIL.display_name == "코레일"
        assert Operator.SRT.display_name == "SRT"

    def test_korail_is_worth_asking_which_kind_of_train(self):
        assert Operator.KORAIL.offers_train_types is True

    def test_sr_runs_one_kind_so_there_is_nothing_to_ask(self):
        """A choice with one answer is not a choice."""
        assert Operator.SRT.offers_train_types is False

    def test_each_offers_its_own_stations(self):
        assert Operator.SRT.major_stations == SRT_MAJOR_STATIONS
        assert Operator.KORAIL.major_stations == KORAIL_MAJOR_STATIONS


class TestWhichStationsAreServed:
    def test_sr_stops_at_its_own_stations(self):
        assert Operator.SRT.serves("수서") is True

    def test_sr_does_not_stop_at_seoul_station(self):
        """The one people get wrong: SRT leaves from 수서, not 서울."""
        assert Operator.SRT.serves("서울") is False

    def test_surrounding_space_does_not_change_the_answer(self):
        assert Operator.SRT.serves("  부산 ") is True

    def test_korail_declines_to_answer(self):
        """
        Its list is fetched and cached elsewhere. None means "ask there",
        which is not the same as False and must not be read as one.
        """
        assert Operator.KORAIL.serves("서울") is None
        assert Operator.KORAIL.serves("존재하지않는역") is None

    def test_every_srt_button_is_a_station_sr_actually_stops_at(self):
        """A button that fails validation would be worse than no button."""
        for station in SRT_MAJOR_STATIONS:
            assert station in SRT_STATIONS, station

    def test_the_srt_buttons_do_not_repeat_a_station(self):
        assert len(set(SRT_MAJOR_STATIONS)) == len(SRT_MAJOR_STATIONS)

    def test_the_old_name_for_gyeongju_is_still_recognised(self):
        """SR's own table carries both, and a saved search may hold either."""
        assert Operator.SRT.serves("신경주") is True
        assert Operator.SRT.serves("경주") is True

    def test_korails_button_list_is_the_one_the_keyboard_uses(self):
        """Two lists that must agree are one list. This checks they still are."""
        assert KORAIL_MAJOR_STATIONS == keyboards.MAJOR_STATIONS


class TestASearchesOperator:
    def test_a_search_that_does_not_say_is_a_korail_search(self):
        params = TrainSearchParams(
            dep_date="20990101", src_locate="서울", dst_locate="부산", dep_time="080000"
        )

        assert params.rail_operator is Operator.KORAIL

    def test_an_srt_search_says_so(self):
        params = TrainSearchParams(
            dep_date="20990101",
            src_locate="수서",
            dst_locate="부산",
            dep_time="080000",
            operator=Operator.SRT,
        )

        assert params.rail_operator is Operator.SRT

    def test_an_srt_search_from_a_station_sr_does_not_serve_is_refused(self):
        params = TrainSearchParams(
            dep_date="20990101",
            src_locate="서울",
            dst_locate="부산",
            dep_time="080000",
            operator=Operator.SRT,
        )

        valid, error = params.validate()

        assert valid is False
        assert "서울" in error

    def test_an_srt_search_to_a_station_sr_does_not_serve_is_refused(self):
        params = TrainSearchParams(
            dep_date="20990101",
            src_locate="수서",
            dst_locate="강릉",
            dep_time="080000",
            operator=Operator.SRT,
        )

        valid, error = params.validate()

        assert valid is False
        assert "강릉" in error

    def test_an_srt_search_between_srt_stations_is_fine(self):
        params = TrainSearchParams(
            dep_date="20990101",
            src_locate="수서",
            dst_locate="부산",
            dep_time="080000",
            operator=Operator.SRT,
        )

        assert params.validate() == (True, None)

    def test_a_korail_search_is_not_held_to_srs_station_list(self):
        """Korail stops at 서울, and this check must not be the thing that says otherwise."""
        params = TrainSearchParams(
            dep_date="20990101", src_locate="서울", dst_locate="강릉", dep_time="080000"
        )

        assert params.validate() == (True, None)

    @pytest.mark.parametrize("field", ["dep_date", "dep_time"])
    def test_the_older_checks_still_run_first(self, field):
        """A malformed date must not be reported as a station problem."""
        values = {
            "dep_date": "20990101",
            "src_locate": "수서",
            "dst_locate": "부산",
            "dep_time": "080000",
            "operator": Operator.SRT,
        }
        values[field] = "nonsense"

        valid, error = TrainSearchParams(**values).validate()

        assert valid is False
        assert "서지 않습니다" not in error


class TestAFavouritesOperator:
    def test_a_favourite_saved_before_there_were_two_is_a_korail_one(self):
        favourite = FavouriteSearch(
            chat_id=1,
            fav_id="abcd",
            name="출근",
            src_locate="서울",
            dst_locate="부산",
            dep_time="080000",
            max_dep_time="1000",
            train_type="",
            train_type_display="",
            special_option="",
            special_option_display="",
        )

        assert favourite.rail_operator is Operator.KORAIL

    def test_the_operator_survives_the_trip_through_a_session(self):
        """
        Saved from the summary screen, used to start a search later. 수서→부산
        is a different journey depending on the answer, so it cannot be
        guessed at when the favourite is used.
        """
        info = {
            "srcLocate": "수서",
            "dstLocate": "부산",
            "depTime": "080000",
            "maxDepTime": "1000",
            "operator": "srt",
        }

        favourite = FavouriteSearch.from_train_info(chat_id=1, info=info)

        assert favourite.rail_operator is Operator.SRT
        assert favourite.as_train_info()["operator"] == "srt"

    def test_a_session_that_never_said_produces_a_korail_favourite(self):
        favourite = FavouriteSearch.from_train_info(
            chat_id=1, info={"srcLocate": "서울", "dstLocate": "부산"}
        )

        assert favourite.rail_operator is Operator.KORAIL
        assert favourite.as_train_info()["operator"] == "korail"
