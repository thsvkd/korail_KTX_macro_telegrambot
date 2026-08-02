"""
The /notify setting, against a real Redis.

It is a preference rather than session state, and that distinction is the
whole point: the booking flow resets the session every time it ends, and a
setting that disappeared with the booking it was made during would not be a
setting at all. These run against Redis because that is where the distinction
actually lives.
"""

from korail_bot.models import UserProgress, UserSession

CHAT_ID = 4242


class TestTheSetting:
    """Reading back what was written."""

    def test_a_chat_that_never_asked_gets_nothing(self, storage):
        """Silence is the default, so the absent key has to read as off."""
        assert storage.get_progress_report_minutes(999999) == 0

    def test_an_interval_survives_the_round_trip(self, storage):
        storage.set_progress_report_minutes(CHAT_ID, 15)

        assert storage.get_progress_report_minutes(CHAT_ID) == 15

    def test_zero_clears_it(self, storage):
        storage.set_progress_report_minutes(CHAT_ID, 15)
        storage.set_progress_report_minutes(CHAT_ID, 0)

        assert storage.get_progress_report_minutes(CHAT_ID) == 0

    def test_setting_it_again_replaces_it(self, storage):
        storage.set_progress_report_minutes(CHAT_ID, 5)
        storage.set_progress_report_minutes(CHAT_ID, 30)

        assert storage.get_progress_report_minutes(CHAT_ID) == 30

    def test_one_chat_does_not_set_it_for_another(self, storage):
        storage.set_progress_report_minutes(CHAT_ID, 5)

        assert storage.get_progress_report_minutes(CHAT_ID + 1) == 0

    def test_a_value_that_cannot_be_read_means_off(self, storage):
        """
        A malformed key must not become a search that messages the user every
        pass of its loop. Failing towards silence is the only safe direction.
        """
        storage.redis.set(f"progress_report:{CHAT_ID}", "가끔")

        assert storage.get_progress_report_minutes(CHAT_ID) == 0


class TestItOutlivesTheBooking:
    """Why it is not kept on the session."""

    def test_finishing_a_booking_does_not_forget_it(self, storage):
        storage.set_progress_report_minutes(CHAT_ID, 10)

        session = UserSession(
            chat_id=CHAT_ID, in_progress=True, last_action=UserProgress.FINDING_TICKET
        )
        session.reset()
        storage.save_user_session(session)

        assert storage.get_progress_report_minutes(CHAT_ID) == 10

    def test_it_does_not_quietly_expire(self, storage):
        """
        A preference that forgot itself after a while would be worse than one
        that was never offered - the user would have no way to tell.
        """
        storage.set_progress_report_minutes(CHAT_ID, 10)

        assert storage.redis.ttl(f"progress_report:{CHAT_ID}") == -1
