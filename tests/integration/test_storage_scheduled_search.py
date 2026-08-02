"""
Scheduled searches surviving the trip through Redis.

A booking is a promise to do something at a time that has not arrived yet, so
the record has to outlive whatever happens in between - including a restart,
which is the whole reason it is a record and not a sleeping process. The unit
tests use a mocked store and so prove nothing about serialisation; this is
where a booking would actually be lost.
"""

from datetime import datetime, timedelta

import pytest

from korail_bot.config.settings import settings
from korail_bot.models import ScheduledSearch, TrainSearchParams
from korail_bot.storage import RedisStorage

CHAT_ID = 987654


def search(chat_id=CHAT_ID, minutes_ahead=90, train_numbers=None):
    return ScheduledSearch(
        chat_id=chat_id,
        korail_id="010-1234-5678",
        search_params=TrainSearchParams(
            dep_date="20991231",
            src_locate="서울",
            dst_locate="울산(통도사)",
            dep_time="090000",
            max_dep_time="1800",
            train_type="TrainType.KTX",
            train_type_display="KTX",
            special_option="ReserveOption.SPECIAL_ONLY",
            special_option_display="SPECIAL_ONLY",
            passenger_count=3,
            seat_strategy="random",
            train_numbers=train_numbers if train_numbers is not None else ["101", "105"],
        ),
        start_at=datetime.now().replace(microsecond=0) + timedelta(minutes=minutes_ahead),
    )


class TestScheduledSearchRoundTrip:
    """What goes in comes back out."""

    def setup_method(self):
        self.storage = RedisStorage()

    def teardown_method(self):
        self.storage.redis.flushdb()
        self.storage.close()

    def test_every_field_survives(self):
        original = search()

        self.storage.save_scheduled_search(original)
        restored = self.storage.get_scheduled_search(CHAT_ID)

        assert restored.chat_id == original.chat_id
        assert restored.korail_id == original.korail_id
        assert restored.start_at == original.start_at
        assert restored.created_at == original.created_at

    def test_the_search_parameters_survive_in_full(self):
        """
        Every one of them, because the search that eventually runs is built
        from this and nothing else. A field dropped here is a search that
        quietly looks for something other than what was asked for.
        """
        original = search()

        self.storage.save_scheduled_search(original)
        restored = self.storage.get_scheduled_search(CHAT_ID)

        assert restored.search_params == original.search_params

    def test_a_whole_window_watch_stays_a_whole_window_watch(self):
        original = search(train_numbers=[])

        self.storage.save_scheduled_search(original)

        assert self.storage.get_scheduled_search(CHAT_ID).search_params.train_numbers == []

    def test_nothing_scheduled_reads_back_as_nothing(self):
        assert self.storage.get_scheduled_search(CHAT_ID) is None

    def test_deleting_removes_it(self):
        self.storage.save_scheduled_search(search())

        self.storage.delete_scheduled_search(CHAT_ID)

        assert self.storage.get_scheduled_search(CHAT_ID) is None

    def test_each_chat_gets_its_own(self):
        self.storage.save_scheduled_search(search(chat_id=1))
        self.storage.save_scheduled_search(search(chat_id=2))

        found = {s.chat_id for s in self.storage.get_all_scheduled_searches()}

        assert found == {1, 2}

    def test_listing_them_does_not_pick_up_other_records(self):
        """
        The scan is by key prefix, and the keyspace has running reservations
        and sessions in it that start with similar words.
        """
        self.storage.save_scheduled_search(search())
        self.storage.save_resume_credentials(CHAT_ID, "010-1234-5678", "pw")

        assert len(self.storage.get_all_scheduled_searches()) == 1


class TestExpiry:
    """A booking nobody ever collects."""

    def setup_method(self):
        self.storage = RedisStorage()

    def teardown_method(self):
        self.storage.redis.flushdb()
        self.storage.close()

    def test_it_outlives_its_own_start_time(self):
        """
        By the grace period at least, or a restart across the moment would
        find the record already gone.
        """
        self.storage.save_scheduled_search(search(minutes_ahead=10))

        ttl = self.storage.redis.ttl(f"scheduled_search:{CHAT_ID}")

        assert ttl > 10 * 60

    def test_it_does_not_live_forever(self):
        """
        Nobody wants a search they booked three days ago starting on its own.
        The record is deleted when it fires; this is for the one that never
        does because the app was down at the time.
        """
        self.storage.save_scheduled_search(search(minutes_ahead=10))

        ttl = self.storage.redis.ttl(f"scheduled_search:{CHAT_ID}")

        assert ttl <= 10 * 60 + settings.SCHEDULE_GRACE_SECONDS

    def test_one_already_due_still_gets_a_usable_lifetime(self):
        """
        seconds_until_due is zero for a schedule saved at or past its time,
        and a zero TTL would have Redis reject the write outright.
        """
        self.storage.save_scheduled_search(search(minutes_ahead=-60))

        assert self.storage.get_scheduled_search(CHAT_ID) is not None
        assert self.storage.redis.ttl(f"scheduled_search:{CHAT_ID}") > 0


class TestSharedSearchParamSerialisation:
    """
    The three records that carry search parameters agree on how.

    They used to each spell out their own field list, which is how the
    running reservation quietly fell two fields behind the user session.
    """

    def setup_method(self):
        self.storage = RedisStorage()

    def teardown_method(self):
        self.storage.redis.flushdb()
        self.storage.close()

    def test_a_running_reservation_keeps_every_field(self):
        from korail_bot.models import RunningReservation

        params = search().search_params
        self.storage.save_running_reservation(
            RunningReservation(
                chat_id=CHAT_ID, process_id=1, korail_id="010-1234-5678", search_params=params
            )
        )

        assert self.storage.get_running_reservation(CHAT_ID).search_params == params

    def test_a_user_session_keeps_every_field(self):
        from korail_bot.models import UserSession

        params = search().search_params
        session = UserSession(chat_id=CHAT_ID, in_progress=True, last_action=1)
        session.search_params = params
        self.storage.save_user_session(session)

        assert self.storage.get_user_session(CHAT_ID).search_params == params

    @pytest.mark.parametrize("missing", ["train_numbers", "max_dep_time", "seat_strategy"])
    def test_a_record_written_before_a_field_existed_still_reads(self, missing):
        """
        Records outlive the code that wrote them. A stored search a deploy
        makes unreadable is a search somebody is still waiting on.
        """
        stored = self.storage._serialize_search_params(search().search_params)
        del stored[missing]

        restored = self.storage._deserialize_search_params(stored)

        assert restored is not None
        assert restored.src_locate == "서울"
