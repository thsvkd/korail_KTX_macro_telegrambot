"""
Confirming a payment from the app, for the payments nothing else watches.

The search process that takes a seat stays behind and watches it, and does it
better than this can: it is already logged in. But it does not exist for a
random-seating run, and it does not survive a restart - and in both cases the
user is left paying into silence while the reminders keep coming.

Two properties carry this. Only one watcher at a time, decided by a claim in
Redis rather than by hoping, because two watchers means the same payment
announced twice. And "could not ask" is never read as "the seat is gone",
which is the same rule the in-process watcher lives by.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from korail_bot.models import (
    MultiReservationStatus,
    OnboardedAccount,
    Operator,
    PaymentStatus,
    ReservationPaymentStatus,
    SingleReservationInfo,
)
from korail_bot.services import TelegramService
from korail_bot.services.payment_watchdog_service import PaymentWatchdogService
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot.messages import Messages

CHAT_ID = 4242
PHONE = "010-1234-5678"
PASSWORD = "korail-password"
MODULE = "korail_bot.services.payment_watchdog_service"
RSV = "320260731221946"


def single(**kwargs):
    """A single booking, still waiting on an answer from the railway."""
    return PaymentStatus(
        chat_id=CHAT_ID,
        completed=kwargs.pop("completed", False),
        reminder_active=True,
        reservation_id=kwargs.pop("reservation_id", RSV),
        train_info="[KTX 101] 서울(09:00)->부산(11:40)",
        operator=kwargs.pop("operator", Operator.KORAIL),
        expires_at=kwargs.pop("expires_at", datetime.now() + timedelta(minutes=9)),
        **kwargs,
    )


def seat(number, reservation_id, status=ReservationPaymentStatus.PENDING, minutes=9):
    return SingleReservationInfo(
        reservation_id=reservation_id,
        reservation_obj=None,
        reserved_at=datetime.now(),
        expires_at=datetime.now() + timedelta(minutes=minutes),
        status=status,
        seat_number=number,
        train_info=f"[KTX 101] 좌석 {number}",
    )


def multi(seats, operator=Operator.KORAIL):
    return MultiReservationStatus(
        chat_id=CHAT_ID,
        reservations=seats,
        total_seats=len(seats),
        seat_strategy="random",
        created_at=datetime.now(),
        operator=operator,
    )


class WatchdogFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_all_payment_statuses.return_value = []
        self.storage.get_all_multi_reservation_statuses.return_value = []
        self.storage.get_payment_status.return_value = None
        self.storage.get_multi_reservation_status.return_value = None
        self.storage.get_current_seat_index.return_value = None
        self.storage.claim_payment_watch.return_value = True
        self.storage.is_developer.return_value = False
        self.storage.get_onboarded_account.return_value = OnboardedAccount(
            chat_id=CHAT_ID, korail_id=PHONE, korail_pw=PASSWORD
        )
        self.telegram = Mock(spec=TelegramService)
        self.watchdog = PaymentWatchdogService(self.storage, self.telegram)

        self.rail = Mock()
        self.rail.login.return_value = True

    def has(self, status=None, booking=None):
        """Put the records the watchdog reads in place."""
        if status is not None:
            self.storage.get_all_payment_statuses.return_value = [status]
            self.storage.get_payment_status.return_value = status
        if booking is not None:
            self.storage.get_all_multi_reservation_statuses.return_value = [booking]
            self.storage.get_multi_reservation_status.return_value = booking

    def answers(self, *outstanding):
        """What the railway says, in order, about each reservation asked about."""
        self.rail.is_reservation_outstanding.side_effect = list(outstanding)

    def tick(self):
        with patch.object(PaymentWatchdogService, "_rail_service", return_value=self.rail):
            return self.watchdog.tick()

    def sent(self):
        return [call.args[1] for call in self.telegram.send_message.call_args_list]


class TestASingleBooking(WatchdogFixture):
    """The case the search process would have handled, had it lived."""

    def test_a_reservation_that_dropped_off_is_reported_as_paid(self):
        self.has(status=single())
        self.answers(False)

        assert self.tick() == 1
        assert Messages.PAYMENT_VERIFIED in self.sent()

    def test_the_record_is_settled_so_the_reminders_stop(self):
        status = single()
        self.has(status=status)
        self.answers(False)

        self.tick()

        assert status.completed is True
        assert status.reminder_active is False
        self.storage.save_payment_status.assert_called_once_with(status)

    def test_a_reservation_still_listed_is_left_alone(self):
        status = single()
        self.has(status=status)
        self.answers(True)

        assert self.tick() == 0
        assert self.sent() == []
        assert status.completed is False

    def test_still_listed_at_the_deadline_is_reported_as_lost(self):
        self.has(status=single(expires_at=datetime.now() - timedelta(seconds=1)))
        self.answers(True)

        assert self.tick() == 1
        assert Messages.PAYMENT_EXPIRED_VERIFIED in self.sent()

    def test_not_being_able_to_ask_is_not_an_answer(self):
        """The rule the whole thing rests on: silence is not "the seat is gone"."""
        status = single()
        self.has(status=status)
        self.answers(None)

        assert self.tick() == 0
        assert self.sent() == []
        assert status.completed is False

    def test_a_record_with_no_reservation_number_is_nothing_to_ask_about(self):
        self.has(status=single(reservation_id=None))

        assert self.tick() == 0
        self.rail.is_reservation_outstanding.assert_not_called()

    def test_a_settled_record_is_not_asked_about_again(self):
        self.has(status=single(completed=True))

        assert self.tick() == 0
        self.rail.is_reservation_outstanding.assert_not_called()

    def test_a_cancelled_record_is_not_asked_about_again(self):
        """It went back on purpose; its disappearance is not a payment."""
        self.has(status=single(cancelled=True))

        assert self.tick() == 0
        self.rail.is_reservation_outstanding.assert_not_called()


class TestARandomSeatingRun(WatchdogFixture):
    """The case nothing watched at all until now."""

    def test_each_seat_is_reported_as_it_settles(self):
        self.has(booking=multi([seat(1, "111"), seat(2, "222")]))
        self.answers(False, True)

        assert self.tick() == 1
        assert "좌석 1번 결제가 확인" in " ".join(self.sent())

    def test_the_seat_that_settled_is_written_down(self):
        booking = multi([seat(1, "111"), seat(2, "222")])
        self.has(booking=booking)
        self.answers(False, True)

        self.tick()

        assert booking.reservations[0].status == ReservationPaymentStatus.PAID
        assert booking.reservations[1].status == ReservationPaymentStatus.PENDING

    def test_a_seat_past_its_deadline_is_reported_as_lost(self):
        self.has(booking=multi([seat(1, "111", minutes=-1)]))
        self.answers(True)

        assert self.tick() == 1
        assert "좌석 1번은 결제 기한이 지나" in " ".join(self.sent())

    def test_seats_already_settled_are_not_asked_about(self):
        self.has(booking=multi([seat(1, "111", ReservationPaymentStatus.PAID), seat(2, "222")]))
        self.answers(True)

        self.tick()

        self.rail.is_reservation_outstanding.assert_called_once_with("222")

    def test_a_confirmed_payment_lets_the_run_take_the_next_seat(self):
        """
        That run waits to be told the seat was paid for, and being told used
        to mean the user sending a message. Now the payment itself says so.
        """
        self.has(booking=multi([seat(1, "111")]))
        self.storage.get_current_seat_index.return_value = 0
        self.answers(False)

        self.tick()

        self.storage.mark_payment_ready.assert_called_once_with(CHAT_ID, 0)

    def test_a_different_seat_settling_does_not_release_the_one_being_waited_on(self):
        """Releasing it would move the run past a seat nobody has paid for."""
        self.has(booking=multi([seat(1, "111"), seat(2, "222")]))
        self.storage.get_current_seat_index.return_value = 1
        self.answers(False, True)

        self.tick()

        self.storage.mark_payment_ready.assert_not_called()


class TestOnlyOneWatcher(WatchdogFixture):
    """Two watchers means the same payment announced twice."""

    def test_a_payment_somebody_else_is_watching_is_left_to_them(self):
        self.has(status=single())
        self.storage.claim_payment_watch.return_value = False

        assert self.tick() == 0
        self.rail.is_reservation_outstanding.assert_not_called()
        assert self.sent() == []

    def test_the_claim_is_made_under_this_process_name(self):
        self.has(status=single())
        self.answers(False)

        self.tick()

        chat_id, owner, _ttl = self.storage.claim_payment_watch.call_args.args
        assert chat_id == CHAT_ID
        assert owner == self.watchdog.owner

    def test_the_claim_is_given_up_once_there_is_nothing_left_to_watch(self):
        self.has(status=single())
        self.answers(False)

        self.tick()

        self.storage.release_payment_watch.assert_called_once_with(CHAT_ID, self.watchdog.owner)

    def test_the_claim_is_kept_while_a_seat_is_still_unsettled(self):
        self.has(booking=multi([seat(1, "111"), seat(2, "222")]))
        self.answers(False, True)

        self.tick()

        self.storage.release_payment_watch.assert_not_called()


class TestLoggingIn(WatchdogFixture):
    """This watcher has to log in, which the one in the search process does not."""

    def test_the_registered_account_is_what_it_logs_in_with(self):
        self.has(status=single())
        self.answers(False)

        self.tick()

        self.rail.login.assert_called_once_with(PHONE, PASSWORD)

    def test_the_railway_holding_the_seat_is_the_one_asked(self):
        self.has(status=single(operator=Operator.SRT))
        self.answers(False)

        with patch.object(PaymentWatchdogService, "_rail_service") as build:
            build.return_value = self.rail
            self.watchdog.tick()

        build.assert_called_once_with(Operator.SRT)

    def test_the_login_is_kept_between_passes(self):
        """At this cadence a login per pass would dwarf the listing it is for."""
        self.has(status=single())
        self.rail.is_reservation_outstanding.side_effect = [True, True]

        self.tick()
        self.tick()

        assert self.rail.login.call_count == 1

    def test_a_login_is_let_go_of_once_the_payment_is_gone(self):
        """
        Settled by the other watcher, so it simply stops appearing. Nothing
        else would ever drop the client it was checked with.
        """
        self.has(status=single())
        self.answers(True)
        self.tick()

        self.storage.get_all_payment_statuses.return_value = []
        self.tick()

        assert self.watchdog._clients == {}
        self.storage.release_payment_watch.assert_called_with(CHAT_ID, self.watchdog.owner)

    def test_nothing_to_log_in_with_settles_nothing(self):
        self.has(status=single())
        self.storage.get_onboarded_account.return_value = None

        assert self.tick() == 0
        assert self.sent() == []

    def test_a_login_that_fails_settles_nothing(self):
        status = single()
        self.has(status=status)
        self.rail.login.return_value = False

        assert self.tick() == 0
        assert status.completed is False


class TestThePassKeepsGoing(WatchdogFixture):
    """A watchdog that dies quietly is worse than none, because it is trusted."""

    def test_one_chat_failing_does_not_strand_the_others(self):
        other = PaymentStatus(
            chat_id=CHAT_ID + 1, reservation_id="999", expires_at=datetime.now() + timedelta(1)
        )
        self.storage.get_all_payment_statuses.return_value = [single(), other]
        self.storage.get_payment_status.side_effect = lambda chat_id: (
            single() if chat_id == CHAT_ID else other
        )
        self.rail.is_reservation_outstanding.side_effect = [RuntimeError("network"), False]

        assert self.tick() == 1

    def test_a_failing_pass_does_not_stop_the_loop(self):
        self.watchdog.tick = Mock(side_effect=RuntimeError("boom"))
        self.watchdog._stop_event.set()

        self.watchdog.run()  # must return rather than raise


class TestHowOftenItLooks(WatchdogFixture):
    """Quick where someone is waiting, slow where nobody is."""

    def test_an_outstanding_payment_is_checked_at_the_verify_interval(self):
        from korail_bot.config.settings import settings

        self.has(status=single())
        self.answers(True)

        self.tick()

        assert self.watchdog._next_pass_in() == settings.PAYMENT_VERIFY_INTERVAL_SECONDS

    def test_nothing_pending_backs_off(self):
        """Scanning Redis every few seconds for nothing is a cost with no reader."""
        from korail_bot.config.settings import settings

        self.tick()

        assert self.watchdog._next_pass_in() >= settings.WATCHDOG_POLL_SECONDS

    def test_a_payment_somebody_else_watches_still_counts_as_busy(self):
        """It is the one this has to take over within seconds if they die."""
        from korail_bot.config.settings import settings

        self.has(status=single())
        self.storage.claim_payment_watch.return_value = False

        self.tick()

        assert self.watchdog._next_pass_in() == settings.PAYMENT_VERIFY_INTERVAL_SECONDS
