"""
Telling the users when the bot they use has been updated.

A self-hosted bot is updated by whoever runs the server. The people using it
find out by noticing that something moved - a button that was not there, a
command nobody mentioned. This closes that gap.

The property that matters is restraint. An announcement that arrives on every
restart is not an announcement, it is a symptom, and it would train people to
ignore the one message the bot sends that is not about their booking.
"""

from unittest.mock import Mock, patch

import pytest

from korail_bot import __version__
from korail_bot.models import UserSession
from korail_bot.release_notes import NOTES, ReleaseNote, notes_for
from korail_bot.services import TelegramService
from korail_bot.services.release_announcer import ReleaseAnnouncer
from korail_bot.storage.base import StorageInterface

NEW = "9.9.9"


class AnnouncerFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_announced_version.return_value = "9.9.8"
        self.storage.get_all_developers.return_value = []
        self.storage.get_all_onboarded_chat_ids.return_value = []
        self.storage.get_all_user_sessions.return_value = []
        self.telegram = Mock(spec=TelegramService)
        self.telegram.send_to_multiple.return_value = 0
        self.announcer = ReleaseAnnouncer(self.storage, self.telegram, version=NEW)

    def sent_to(self):
        if not self.telegram.send_to_multiple.called:
            return None
        return self.telegram.send_to_multiple.call_args.args[0]

    def sent_text(self):
        return self.telegram.send_to_multiple.call_args.args[1]


class TestWhenItSpeaks(AnnouncerFixture):
    """Once, on a change, and not otherwise."""

    def test_a_new_version_is_announced(self):
        self.storage.get_all_developers.return_value = [1]

        assert self.announcer.announce() >= 0
        assert self.sent_to() == [1]

    def test_the_same_version_is_not_announced_again(self):
        """This is the restart case, and it is the whole point."""
        self.storage.get_announced_version.return_value = NEW
        self.storage.get_all_developers.return_value = [1]

        self.announcer.announce()

        self.telegram.send_to_multiple.assert_not_called()

    def test_a_deployment_with_no_record_is_treated_as_an_update(self):
        """
        The honest reading for the case this feature ships into: a bot that
        already had users and has just been updated to the version that can
        say so.
        """
        self.storage.get_announced_version.return_value = None
        self.storage.get_all_developers.return_value = [1]

        self.announcer.announce()

        assert self.sent_to() == [1]

    def test_the_version_is_recorded_before_anyone_is_told(self):
        """
        A crash halfway through costs the rest of the audience one notice.
        The other order would re-send the same announcement to everyone on
        every restart until a full pass succeeded, and a repeating update
        notice is far worse than a missed one.
        """
        order = []
        self.storage.get_all_developers.return_value = [1]
        self.storage.set_announced_version.side_effect = lambda *a, **k: order.append("record")
        self.telegram.send_to_multiple.side_effect = lambda *a, **k: order.append("send") or 1

        self.announcer.announce()

        assert order == ["record", "send"]

    def test_a_redis_that_cannot_be_read_says_nothing(self):
        """Not knowing whether this was announced is not a reason to announce."""
        self.storage.get_announced_version.side_effect = Exception("redis is down")
        self.storage.get_all_developers.return_value = [1]

        self.announcer.announce()

        self.telegram.send_to_multiple.assert_not_called()

    def test_a_version_that_cannot_be_recorded_is_not_announced(self):
        """
        Sending without recording is how a notice starts repeating. Better to
        skip this start and try again on the next one.
        """
        self.storage.set_announced_version.side_effect = Exception("redis is down")
        self.storage.get_all_developers.return_value = [1]

        self.announcer.announce()

        self.telegram.send_to_multiple.assert_not_called()

    def test_nobody_to_tell_is_not_an_error(self):
        """A fresh install. The version is still recorded, so it stays quiet."""
        assert self.announcer.announce() == 0
        self.storage.set_announced_version.assert_called_once_with(NEW)


class TestWhoIsTold(AnnouncerFixture):
    """Three overlapping sources, none of which is the answer alone."""

    def test_registered_users_are_told(self):
        """The users proper: a registration outlives its session by months."""
        self.storage.get_all_onboarded_chat_ids.return_value = [11, 22]

        assert self.announcer.audience() == [11, 22]

    def test_someone_mid_conversation_is_told_even_without_a_registration(self):
        self.storage.get_all_user_sessions.return_value = [UserSession(chat_id=33)]

        assert self.announcer.audience() == [33]

    def test_the_operator_hears_first(self):
        """They want to know their own deploy landed."""
        self.storage.get_all_developers.return_value = [7]
        self.storage.get_all_onboarded_chat_ids.return_value = [11]

        assert self.announcer.audience()[0] == 7

    def test_nobody_hears_twice(self):
        """The same chat is usually in all three."""
        self.storage.get_all_developers.return_value = [7]
        self.storage.get_all_onboarded_chat_ids.return_value = [7, 11]
        self.storage.get_all_user_sessions.return_value = [
            UserSession(chat_id=7),
            UserSession(chat_id=11),
        ]

        assert self.announcer.audience() == [7, 11]

    def test_one_unreadable_source_does_not_cost_the_others(self):
        self.storage.get_all_onboarded_chat_ids.side_effect = Exception("redis hiccup")
        self.storage.get_all_developers.return_value = [7]

        assert self.announcer.audience() == [7]


class TestWhatItSays(AnnouncerFixture):
    """The message."""

    def test_it_names_the_version(self):
        assert f"v{NEW}" in self.announcer.message()

    def test_a_release_with_notes_carries_them(self):
        version = next(iter(NOTES))
        announcer = ReleaseAnnouncer(self.storage, self.telegram, version=version)

        assert notes_for(version).headline in announcer.message()

    def test_a_release_without_notes_still_says_what_it_is(self):
        """The fallback, not the intent - but it must not be an empty message."""
        message = self.announcer.message()

        assert notes_for(NEW) is None
        assert message.strip()
        assert "{headline}" not in message

    def test_it_points_somewhere_to_look(self):
        assert "/help" in self.announcer.message()


class TestTheFold(AnnouncerFixture):
    """
    Keeping the interruption small.

    A release with four features and a paragraph each would otherwise be a
    wall of text arriving unasked. The headline lands in the chat; the rest
    sits behind a fold for whoever wants it.
    """

    def message(self, headline="• 짧게", detail=""):
        with patch.dict(NOTES, {NEW: ReleaseNote(headline=headline, detail=detail)}):
            return ReleaseAnnouncer(self.storage, self.telegram, version=NEW).message()

    def test_the_headline_is_shown_outright(self):
        message = self.message(headline="• 무언가 생겼습니다")

        assert "• 무언가 생겼습니다" in message.split("<blockquote")[0]

    def test_the_detail_is_folded_away(self):
        message = self.message(detail="아주 긴 설명")

        assert "<blockquote expandable>" in message
        assert "아주 긴 설명" in message.split("<blockquote expandable>")[1]

    def test_a_release_with_nothing_to_hide_grows_no_fold(self):
        """A box that opens onto nothing is worse than no box."""
        message = self.message(detail="   ")

        assert "blockquote" not in message
        assert "• 짧게" in message

    def test_it_is_sent_as_markup(self):
        """The fold has no other spelling in the Bot API."""
        self.storage.get_all_developers.return_value = [1]

        self.announcer.announce()

        assert self.telegram.send_to_multiple.call_args.kwargs["parse_mode"] == "HTML"

    def test_markup_in_a_note_cannot_take_the_announcement_down(self):
        """
        Notes are written by hand. One stray < would otherwise make Telegram
        refuse the whole send rather than render it oddly.
        """
        message = self.message(headline="• a < b 인 경우", detail="x & y")

        assert "&lt;" in message
        assert "&amp;" in message
        # The tags the template itself puts there survive.
        assert "<blockquote expandable>" in message


class TestTheReleaseNotes:
    """The entries themselves, which are written by hand every release."""

    def test_the_running_version_has_notes(self):
        """
        Bumping the version without writing the notes ships an announcement
        that names a number and says nothing.
        """
        assert notes_for(__version__), f"v{__version__} 의 릴리스 노트가 없습니다"

    @pytest.mark.parametrize("version", sorted(NOTES))
    def test_every_entry_has_a_headline(self, version):
        """The fold is optional; the part that lands in the chat is not."""
        assert NOTES[version].headline.strip()

    @pytest.mark.parametrize("version", sorted(NOTES))
    def test_the_headline_stays_short_enough_to_read_unasked(self, version):
        """
        This is the part that interrupts someone. Past a handful of lines it
        stops being a headline and the fold below it stops meaning anything.
        """
        lines = [line for line in NOTES[version].headline.splitlines() if line.strip()]

        assert len(lines) <= 5, f"v{version}: 요약이 {len(lines)}줄입니다"

    @pytest.mark.parametrize("version", sorted(NOTES))
    def test_the_notes_are_written_for_the_user_not_the_author(self, version):
        """
        No module names, no refactors. The reader books train tickets and can
        only act on what they can see in the chat.
        """
        internals = ["refactor", "리팩", ".py", "커밋", "Redis", "korail_bot"]
        written = NOTES[version].headline + NOTES[version].detail
        for word in internals:
            assert word not in written, f"v{version}: '{word}' 은 사용자의 말이 아닙니다"


class TestRunningInTheBackground(AnnouncerFixture):
    """One HTTP call per user, at the moment the bot is trying to come up."""

    def test_an_announcement_due_gets_a_thread(self):
        self.storage.get_all_developers.return_value = [1]

        thread = self.announcer.announce_in_background()

        assert thread is not None
        thread.join(timeout=5)
        assert self.sent_to() == [1]

    def test_nothing_due_starts_nothing(self):
        self.storage.get_announced_version.return_value = NEW

        assert self.announcer.announce_in_background() is None

    def test_the_thread_does_not_hold_the_process_open(self):
        """Shutting the bot down must not wait on a message to a blocked chat."""
        self.storage.get_all_developers.return_value = [1]

        thread = self.announcer.announce_in_background()

        assert thread.daemon
        thread.join(timeout=5)
