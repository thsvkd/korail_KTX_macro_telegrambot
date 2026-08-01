"""
The commands only the person running the server can use.

They are the ones with the least reason to be tested and the most to lose by
being wrong: /flushredis deletes every session and every running search on the
box, /broadcast writes to every user at once, and both are one keystroke away
from the commands beside them in the operator's menu.

What is checked here is mostly the shape of the blast radius - that the
destructive one reports what it destroyed rather than failing silently, that
the broadcast reaches everybody or says how many it did not, and that /cancel
puts down every one of the several things it is the only way out of.
"""

from unittest.mock import Mock, patch

import pytest

from korail_bot.handlers.command_handler import CommandHandler
from korail_bot.models import UserCredentials, UserSession
from korail_bot.services import PaymentReminderService, ReservationService, TelegramService
from korail_bot.storage.base import StorageInterface
from korail_bot.storage.redis import RedisStorage

CHAT_ID = 6824596577
MODULE = "korail_bot.handlers.command_handler"


class OperatorFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_all_user_sessions.return_value = []
        self.storage.get_user_session.return_value = None
        self.telegram = Mock(spec=TelegramService)
        self.reservation = Mock(spec=ReservationService)
        self.payment_reminder = Mock(spec=PaymentReminderService)
        self.handler = CommandHandler(
            self.storage, self.telegram, self.reservation, self.payment_reminder
        )

    def replied(self):
        return self.telegram.send_message.call_args.args[1]


class TestStatus(OperatorFixture):
    """/status, which is the answer to "is it still looking"."""

    def test_it_reports_what_the_reservation_service_knows(self):
        self.reservation.get_status.return_value = "🔍 검색 중입니다"

        self.handler.handle_status(CHAT_ID)

        assert self.replied() == "🔍 검색 중입니다"
        self.reservation.get_status.assert_called_once_with(CHAT_ID)


class TestDebugLogging(OperatorFixture):
    """
    /debug_on and /debug_off.

    The level is written to Redis as well as set, because the searches run in
    child processes started later and read it from there.
    """

    def test_turning_it_on_writes_it_down_and_sets_it(self):
        with patch(f"{MODULE}.LoggerFactory") as factory:
            self.handler.handle_debug_on(CHAT_ID)

        self.storage.set_debug_mode.assert_called_once_with(True)
        factory.set_log_level.assert_called_once_with("DEBUG")

    def test_turning_it_off_puts_the_level_back(self):
        with patch(f"{MODULE}.LoggerFactory") as factory:
            self.handler.handle_debug_off(CHAT_ID)

        self.storage.set_debug_mode.assert_called_once_with(False)
        factory.set_log_level.assert_called_once_with("INFO")

    def test_the_operator_is_told_how_to_undo_it(self):
        """
        Debug logging left on fills a disk on a Raspberry Pi in a few days.
        """
        with patch(f"{MODULE}.LoggerFactory"):
            self.handler.handle_debug_on(CHAT_ID)

        assert "/debug_off" in self.replied()


class TestListingUsers(OperatorFixture):
    """/allusers."""

    def test_registered_users_are_named_by_their_masked_number(self):
        self.storage.get_all_user_sessions.return_value = [
            UserSession(
                chat_id=1,
                credentials=UserCredentials(korail_id="010-1234-5678", korail_pw="secret"),
            )
        ]

        self.handler.handle_all_users(CHAT_ID)

        assert "010-1234-5678" not in self.replied()
        assert "5678" in self.replied()

    def test_someone_mid_conversation_is_named_by_their_chat(self):
        """They have not given a number yet, and they are still a user."""
        self.storage.get_all_user_sessions.return_value = [UserSession(chat_id=77)]

        self.handler.handle_all_users(CHAT_ID)

        assert "chat_77" in self.replied()

    def test_nobody_at_all_is_reported_as_nobody(self):
        self.handler.handle_all_users(CHAT_ID)

        assert "총 0명" in self.replied()


class TestBroadcast(OperatorFixture):
    """/broadcast, which writes to every user of the bot at once."""

    def setup_method(self):
        super().setup_method()
        self.storage.get_all_user_sessions.return_value = [
            UserSession(chat_id=1),
            UserSession(chat_id=2),
        ]
        self.telegram.send_to_multiple.return_value = 2

    def test_the_message_reaches_everyone(self):
        self.handler.handle_broadcast(CHAT_ID, "점검 예정입니다")

        self.telegram.send_to_multiple.assert_called_once_with([1, 2], "점검 예정입니다")

    def test_nobody_to_broadcast_to_is_not_an_error(self):
        self.storage.get_all_user_sessions.return_value = []

        self.handler.handle_broadcast(CHAT_ID, "점검 예정입니다")

        self.telegram.send_to_multiple.assert_called_once_with([], "점검 예정입니다")


class TestFlushRedis(OperatorFixture):
    """
    /flushredis, which deletes every session and every search record there is.

    It cannot be undone and it cannot be partial, so the one thing it owes the
    operator is an honest count of what went.
    """

    def test_it_says_how_much_it_deleted(self):
        self.storage = Mock(spec=RedisStorage)
        self.storage.flush_all.return_value = 137
        self.handler.storage = self.storage

        self.handler.handle_flush_redis(CHAT_ID)

        assert "137" in self.replied()

    def test_a_storage_that_cannot_be_flushed_says_so_rather_than_pretending(self):
        """
        Mock(spec=StorageInterface) has no flush_all, which is exactly the
        case: the interface does not promise one.
        """
        self.handler.handle_flush_redis(CHAT_ID)

        assert "❌" in self.replied()

    def test_a_flush_that_failed_is_reported_as_failed(self):
        """
        The operator is about to assume the box is clean. Being wrong about
        that is how a stale search record outlives the flush meant to remove
        it.
        """
        self.storage = Mock(spec=RedisStorage)
        self.storage.flush_all.side_effect = Exception("redis is down")
        self.handler.storage = self.storage

        self.handler.handle_flush_redis(CHAT_ID)

        assert "실패" in self.replied()


class TestCancelAll(OperatorFixture):
    """/cancelall."""

    def test_it_goes_through_the_service_that_owns_the_searches(self):
        self.reservation.cancel_all_reservations.return_value = 3

        self.handler.handle_cancel_all(CHAT_ID)

        self.reservation.cancel_all_reservations.assert_called_once_with(CHAT_ID)


class TestCancel(OperatorFixture):
    """
    /cancel, which is the way out of everything.

    Not an operator command, but the one that touches the most state: a
    running search, a booked-but-not-started one, a half-finished
    conversation, and the two "the next thing you type is the answer" flags
    that nothing else can clear.
    """

    def setup_method(self):
        super().setup_method()
        self.reservation.discard_dead_search.return_value = False
        self.reservation.cancel_reservation.return_value = False
        self.scheduler = Mock()
        self.scheduler.cancel.return_value = False

    def cancel(self):
        with patch(f"{MODULE}.ScheduledSearchService", return_value=self.scheduler, create=True):
            self.handler.handle_cancel(CHAT_ID)

    def replies(self):
        return [call.args[1] for call in self.telegram.send_message.call_args_list]

    def test_a_running_search_is_stopped(self):
        self.reservation.cancel_reservation.return_value = True

        self.cancel()

        assert any("취소되었습니다" in reply for reply in self.replies())

    def test_a_search_booked_for_later_is_dropped(self):
        """
        cancel_reservation stays quiet when there is no running search, so
        without this the user gets no answer at all.
        """
        self.scheduler.cancel.return_value = True

        self.cancel()

        assert any("예약해둔 검색" in reply for reply in self.replies())

    def test_a_search_that_had_already_died_is_tidied_away(self):
        self.reservation.discard_dead_search.return_value = True

        self.cancel()

        assert any("멈춰 있던 검색" in reply for reply in self.replies())
        self.reservation.cancel_reservation.assert_not_called()

    def test_a_half_finished_conversation_is_reset(self):
        session = UserSession(chat_id=CHAT_ID, in_progress=True)
        self.storage.get_user_session.return_value = session

        self.cancel()

        assert session.in_progress is False
        self.storage.save_user_session.assert_called_once_with(session)

    @pytest.mark.parametrize(
        ("method", "expected"),
        [
            ("set_pending_favourite_rename", (CHAT_ID, None)),
            ("set_waiting_for_notify_input", (CHAT_ID, False)),
            ("set_waiting_for_admin_password", (CHAT_ID, False)),
        ],
    )
    def test_every_state_that_claims_the_next_message_is_let_go_of(self, method, expected):
        """
        Each of these means "whatever you type next is the answer". /cancel is
        the only way out of them - anything else typed would be taken as the
        answer - so leaving one set here strands the user.
        """
        self.cancel()

        getattr(self.storage, method).assert_called_once_with(*expected)

    def test_the_payment_reminders_are_stopped_through_their_own_service(self):
        self.cancel()

        self.payment_reminder.deactivate_reminders.assert_called_once_with(CHAT_ID, completed=True)

    def test_a_scheduler_that_cannot_be_reached_does_not_stop_the_rest(self):
        """
        /cancel has several other things to put down after this one, and it is
        the command a stuck user reaches for.
        """
        with patch(
            f"{MODULE}.ScheduledSearchService", side_effect=Exception("redis is down"), create=True
        ):
            self.handler.handle_cancel(CHAT_ID)

        self.storage.set_waiting_for_notify_input.assert_called_once_with(CHAT_ID, False)
