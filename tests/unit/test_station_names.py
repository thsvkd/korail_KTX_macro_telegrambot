"""
Knowing which station names are real.

Every search starts with two of these, and both are typed. Getting the list
wrong is expensive in both directions: a real station rejected means the user
cannot search at all, and there is nothing they can do about it from their end
except guess at spellings. A wrong one accepted means a search that never
finds anything, or worse, one that finds seats on a route they did not ask
for.

The list comes from Korail, which is the right source and an unreliable one.
So there are three tiers - Redis, then the API, then a list compiled into the
bot - and what this file mostly pins down is that every way the first two can
fail lands on the third rather than on an empty set. An empty set validates
nothing: it would refuse 서울.

The other half is the suggestion offered after a typo, which is the only thing
standing between a misspelling and a dead end.
"""

import json
from typing import ClassVar
from unittest.mock import Mock, patch

import pytest
import requests

from korail_bot.utils import station_codes
from korail_bot.utils.station_codes import (
    FALLBACK_STATIONS,
    REDIS_STATION_CACHE_KEY,
    StationManager,
    format_station_suggestions,
    get_similar_stations,
    is_valid_station,
)

MODULE = "korail_bot.utils.station_codes"


def api_response(stations, status_code=200):
    """What Korail's station endpoint answers with."""
    response = Mock(status_code=status_code)
    response.json.return_value = {"stns": {"stn": [{"stn_nm": name} for name in stations]}}
    return response


def manager(redis_client=None):
    """A StationManager with its Redis replaced, built without the singleton."""
    instance = object.__new__(StationManager)
    instance._redis_client = redis_client
    return instance


class TestTheFallbackList:
    """
    The list compiled into the bot, which is the floor everything lands on.

    It is reached whenever Korail cannot be asked, which on a home server
    happens for reasons that have nothing to do with Korail.
    """

    def test_it_is_not_empty(self):
        """An empty set validates nothing - it would refuse 서울."""
        assert FALLBACK_STATIONS

    @pytest.mark.parametrize("station", ["서울", "부산", "동대구", "광주송정", "용산"])
    def test_the_stations_people_actually_search_are_in_it(self, station):
        assert station in FALLBACK_STATIONS

    def test_the_names_carry_no_역_suffix(self):
        """
        Korail's own naming, and what every other part of the bot passes
        around. "서울역" would be rejected against a list that says "서울".
        """
        assert not any(name.endswith("역") for name in FALLBACK_STATIONS)


class TestAskingKorail:
    """The API tier, and every way it can fail to answer usefully."""

    def fetch(self, response=None, error=None):
        with patch(f"{MODULE}.requests.get") as get:
            if error is not None:
                get.side_effect = error
            else:
                get.return_value = response
            return manager()._fetch_stations_from_api()

    def test_the_stations_it_names_are_taken(self):
        stations = self.fetch(api_response(["서울", "부산", "동대구"]))

        assert stations == {"서울", "부산", "동대구"}

    def test_a_station_with_no_name_is_skipped_rather_than_kept_blank(self):
        response = Mock(status_code=200)
        response.json.return_value = {"stns": {"stn": [{"stn_nm": "서울"}, {"stn_nm": ""}, {}]}}

        with patch(f"{MODULE}.requests.get", return_value=response):
            assert manager()._fetch_stations_from_api() == {"서울"}

    @pytest.mark.parametrize(
        "outcome",
        [
            {"response": api_response([], status_code=500)},
            {"error": requests.exceptions.Timeout()},
            {"error": requests.exceptions.ConnectionError("no route")},
            {"error": RuntimeError("something else")},
        ],
        ids=["server-error", "timeout", "refused", "unexpected"],
    )
    def test_korail_not_answering_lands_on_the_compiled_list(self, outcome):
        assert self.fetch(**outcome) is FALLBACK_STATIONS

    @pytest.mark.parametrize(
        "body",
        [[], {"stns": []}, {"stns": {"stn": []}}, {"unexpected": "shape"}, "a string"],
        ids=["list", "stns-not-object", "no-stations", "wrong-keys", "not-json-object"],
    )
    def test_an_answer_it_cannot_read_lands_there_too(self, body):
        """
        The shape is Korail's to change, and they have not promised anyone it
        will not. Falling back beats refusing every station name.
        """
        response = Mock(status_code=200)
        response.json.return_value = body

        with patch(f"{MODULE}.requests.get", return_value=response):
            assert manager()._fetch_stations_from_api() is FALLBACK_STATIONS


class TestTheCache:
    """
    Redis in front of the API.

    The list changes about never, and asking Korail on every station a user
    types would be both slow and rude.
    """

    def test_a_cached_list_is_used(self):
        redis = Mock()
        redis.get.return_value = json.dumps(["서울", "부산"])

        assert manager(redis)._get_from_redis() == {"서울", "부산"}

    def test_nothing_cached_is_a_miss_rather_than_an_empty_list(self):
        redis = Mock()
        redis.get.return_value = None

        assert manager(redis)._get_from_redis() is None

    def test_a_cache_entry_that_cannot_be_read_is_a_miss(self):
        """
        Corrupt JSON, or a value written by a build that stored something
        else. Falling through to the API is the whole point of it being a
        cache.
        """
        redis = Mock()
        redis.get.return_value = "{not json"

        assert manager(redis)._get_from_redis() is None

    def test_no_redis_at_all_is_a_miss(self):
        assert manager(None)._get_from_redis() is None

    def test_a_fetched_list_is_written_back_with_an_expiry(self):
        redis = Mock()

        manager(redis)._save_to_redis({"서울", "부산"})

        key, ttl, payload = redis.setex.call_args.args
        assert key == REDIS_STATION_CACHE_KEY
        assert ttl > 0
        assert set(json.loads(payload)) == {"서울", "부산"}

    def test_a_write_that_fails_is_not_an_error(self):
        """The list was fetched; the cache is an optimisation."""
        redis = Mock()
        redis.setex.side_effect = Exception("redis is down")

        manager(redis)._save_to_redis({"서울"})  # must not raise

    def test_writing_without_redis_is_not_an_error(self):
        manager(None)._save_to_redis({"서울"})  # must not raise


class TestPuttingTheTiersTogether:
    """Which tier answers, and what gets written back."""

    def test_the_cache_answers_first(self):
        instance = manager(Mock())

        with (
            patch.object(instance, "_get_from_redis", return_value={"서울"}),
            patch.object(instance, "_fetch_stations_from_api") as fetch,
        ):
            assert instance.get_valid_stations() == {"서울"}

        fetch.assert_not_called()

    def test_a_miss_asks_korail_and_caches_the_answer(self):
        instance = manager(Mock())

        with (
            patch.object(instance, "_get_from_redis", return_value=None),
            patch.object(instance, "_fetch_stations_from_api", return_value={"서울", "부산"}),
            patch.object(instance, "_save_to_redis") as saved,
        ):
            instance.get_valid_stations()

        saved.assert_called_once_with({"서울", "부산"})

    def test_the_compiled_list_is_never_cached_as_if_it_came_from_korail(self):
        """
        Caching it would turn one bad minute for the network into a day of
        the bot refusing every station Korail has opened since this build.
        """
        instance = manager(Mock())

        with (
            patch.object(instance, "_get_from_redis", return_value=None),
            patch.object(instance, "_fetch_stations_from_api", return_value=FALLBACK_STATIONS),
            patch.object(instance, "_save_to_redis") as saved,
        ):
            instance.get_valid_stations()

        saved.assert_not_called()

    def test_a_forced_refresh_skips_the_cache(self):
        instance = manager(Mock())

        with (
            patch.object(instance, "_get_from_redis") as cached,
            patch.object(instance, "_fetch_stations_from_api", return_value={"서울"}),
            patch.object(instance, "_save_to_redis"),
        ):
            instance.get_valid_stations(force_refresh=True)

        cached.assert_not_called()

    def test_the_manager_is_one_object(self):
        """
        It holds a Redis connection. One per station name typed would be a
        connection per keystroke of the conversation.
        """
        assert StationManager() is StationManager()


class TestValidating:
    """The question the conversation actually asks."""

    def valid(self, name, stations={"서울", "부산", "동대구"}):  # noqa: B006
        with patch(f"{MODULE}.get_valid_stations", return_value=stations):
            return is_valid_station(name)

    def test_a_real_station_is_accepted(self):
        assert self.valid("서울") is True

    def test_something_that_is_not_a_station_is_refused(self):
        assert self.valid("서울역") is False

    def test_an_empty_answer_is_refused_without_asking_korail(self):
        with patch(f"{MODULE}.get_valid_stations") as stations:
            assert is_valid_station("") is False

        stations.assert_not_called()


class TestSuggestingWhatTheyMeant:
    """
    The only thing between a typo and a dead end.

    "그런 역은 없습니다" and nothing else leaves someone guessing at spellings
    of a name they are sure they know.
    """

    STATIONS: ClassVar = {"서울", "용산", "부산", "동대구", "광주송정", "울산(통도사)"}

    def similar(self, name, **kwargs):
        with patch(f"{MODULE}.get_valid_stations", return_value=self.STATIONS):
            return get_similar_stations(name, **kwargs)

    def test_a_name_that_is_already_right_needs_no_suggestion(self):
        assert self.similar("서울") == []

    def test_a_name_that_is_part_of_a_real_one_finds_it(self):
        assert "광주송정" in self.similar("광주")

    def test_a_name_that_contains_a_real_one_finds_it_too(self):
        """How "부산역" gets pointed at 부산."""
        assert "부산" in self.similar("부산역")

    def test_a_name_with_nothing_in_common_falls_back_to_the_first_letter(self):
        """
        Weak, and better than nothing: someone who typed 동해 gets the 동
        stations rather than an empty answer.
        """
        assert self.similar("동해") == ["동대구"]

    def test_nothing_is_suggested_for_nothing(self):
        assert self.similar("") == []

    def test_the_suggestions_are_capped(self):
        """A list of twenty stations is not a suggestion."""
        assert len(self.similar("ㅇ", max_results=2)) <= 2

    def test_the_same_station_is_not_suggested_twice(self):
        found = self.similar("부산")

        assert len(found) == len(set(found))

    def test_they_come_out_in_a_stable_order(self):
        """
        They are read out of a set. Without sorting, the same typo would
        produce a different list on every run.
        """
        assert self.similar("산") == sorted(self.similar("산"))


class TestSayingItToTheUser:
    """How a suggestion reads."""

    def test_one_candidate_is_offered_as_a_question(self):
        assert "동대구" in format_station_suggestions(["동대구"])

    def test_several_are_listed(self):
        text = format_station_suggestions(["서울", "용산"])

        assert "서울" in text and "용산" in text

    def test_nothing_to_suggest_adds_nothing(self):
        """
        It is appended to an error message. A trailing "비슷한 역: " with
        nothing after it is worse than no suggestion.
        """
        assert format_station_suggestions([]) == ""


def test_the_module_level_helpers_go_through_the_one_manager():
    """The functions the rest of the bot imports."""
    with patch.object(station_codes._station_manager, "get_valid_stations") as through:
        station_codes.get_valid_stations(force_refresh=True)

    through.assert_called_once_with(force_refresh=True)
