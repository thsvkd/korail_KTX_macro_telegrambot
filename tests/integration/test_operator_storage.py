"""
Records outlive the code that wrote them.

Everything Redis holds about a search was written before there were two
railways, and none of it says which one. A deploy that made those records
unreadable - or read them back as SRT searches - would strand people who are
waiting on a search started yesterday. These put the old shapes into Redis by
hand and check what comes back out.
"""

import json

from korail_bot.models import FavouriteSearch, Operator, RunningReservation, TrainSearchParams


def a_search(**overrides) -> TrainSearchParams:
    values = {
        "dep_date": "20990101",
        "src_locate": "수서",
        "dst_locate": "부산",
        "dep_time": "080000",
        "max_dep_time": "1200",
    }
    values.update(overrides)
    return TrainSearchParams(**values)


def a_favourite(**overrides) -> FavouriteSearch:
    values = {
        "chat_id": 1,
        "fav_id": "abcd1234",
        "name": "출근",
        "src_locate": "수서",
        "dst_locate": "부산",
        "dep_time": "080000",
        "max_dep_time": "1200",
        "train_type": "",
        "train_type_display": "",
        "special_option": "",
        "special_option_display": "",
    }
    values.update(overrides)
    return FavouriteSearch(**values)


class TestASearchRemembersItsRailway:
    def test_an_srt_search_comes_back_as_an_srt_search(self, storage):
        storage.save_running_reservation(
            RunningReservation(
                chat_id=1,
                process_id=999,
                korail_id="010-1234-5678",
                search_params=a_search(operator=Operator.SRT),
            )
        )

        read_back = storage.get_running_reservation(1)

        assert read_back.search_params.rail_operator is Operator.SRT

    def test_a_korail_search_comes_back_as_a_korail_search(self, storage):
        storage.save_running_reservation(
            RunningReservation(
                chat_id=1,
                process_id=999,
                korail_id="010-1234-5678",
                search_params=a_search(src_locate="서울", operator=Operator.KORAIL),
            )
        )

        read_back = storage.get_running_reservation(1)

        assert read_back.search_params.rail_operator is Operator.KORAIL

    def test_a_record_written_before_there_were_two_is_a_korail_search(self, storage):
        """
        The shape this repository wrote until today: no operator field at all.
        Someone is waiting on this search right now.
        """
        storage.redis.set(
            "running_reservation:1",
            json.dumps(
                {
                    "chat_id": 1,
                    "process_id": 999,
                    "korail_id": "010-1234-5678",
                    "run_id": "",
                    "started_at": "2026-08-01T09:00:00",
                    "search_params": {
                        "dep_date": "20990101",
                        "src_locate": "서울",
                        "dst_locate": "부산",
                        "dep_time": "080000",
                        "max_dep_time": "1200",
                        "train_type": "TrainType.KTX",
                        "train_type_display": "KTX",
                        "special_option": "ReserveOption.GENERAL_FIRST",
                        "special_option_display": "GENERAL_FIRST",
                        "passenger_count": 1,
                        "seat_strategy": "consecutive",
                        "train_numbers": [],
                    },
                }
            ),
        )

        read_back = storage.get_running_reservation(1)

        assert read_back is not None
        assert read_back.search_params.rail_operator is Operator.KORAIL
        assert read_back.search_params.src_locate == "서울"

    def test_a_record_holding_something_unrecognisable_still_reads(self, storage):
        """Better a Korail search than a search that cannot be read at all."""
        storage.save_running_reservation(
            RunningReservation(
                chat_id=1,
                process_id=999,
                korail_id="010-1234-5678",
                search_params=a_search(operator="shinkansen"),
            )
        )

        read_back = storage.get_running_reservation(1)

        assert read_back.search_params.rail_operator is Operator.KORAIL


class TestAFavouriteRemembersItsRailway:
    def test_an_srt_favourite_comes_back_as_one(self, storage):
        storage.save_favourite(a_favourite(operator=Operator.SRT))

        read_back = storage.get_favourites(1)

        assert [f.rail_operator for f in read_back] == [Operator.SRT]

    def test_a_favourite_saved_before_there_were_two_is_a_korail_one(self, storage):
        storage.redis.set(
            "favourite:1:abcd1234",
            json.dumps(
                {
                    "name": "출근",
                    "src_locate": "서울",
                    "dst_locate": "부산",
                    "dep_time": "080000",
                    "max_dep_time": "1200",
                    "train_type": "TrainType.KTX",
                    "train_type_display": "KTX",
                    "special_option": "ReserveOption.GENERAL_FIRST",
                    "special_option_display": "GENERAL_FIRST",
                    "passenger_count": 1,
                    "seat_strategy": "consecutive",
                    "seat_strategy_display": "",
                    "created_at": "2026-07-01T09:00:00",
                }
            ),
        )

        read_back = storage.get_favourites(1)

        assert len(read_back) == 1
        assert read_back[0].rail_operator is Operator.KORAIL
        assert read_back[0].name == "출근"

    def test_starting_a_search_from_an_srt_favourite_keeps_the_railway(self, storage):
        """The favourite's whole job is to answer the questions again."""
        storage.save_favourite(a_favourite(operator=Operator.SRT))

        favourite = storage.get_favourites(1)[0]
        info = favourite.as_train_info()

        assert Operator.parse(info["operator"]) is Operator.SRT
