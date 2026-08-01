"""
Reminding somebody to pay for four seats booked minutes apart.

Random allocation books one seat at a time, so a group of four ends up with
four reservations and four deadlines, the first of which expires while the
last is still being searched for. One reminder saying "pay within 10 minutes"
is wrong for three of them.

What this has to get right is what it shows. A list that keeps repeating seats
already paid for is a list people stop reading, and the seat about to expire
is the one that has to be at the top of it.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from korail_bot.models import (
    MultiReservationStatus,
    ReservationPaymentStatus,
    SingleReservationInfo,
)
from korail_bot.services.multi_reservation_reminder_service import (
    MultiReservationReminderService,
)
from korail_bot.services.telegram_service import TelegramService
from korail_bot.storage.base import StorageInterface

MODULE = "korail_bot.services.multi_reservation_reminder_service"
CHAT_ID = 12345


def seat(number, minutes_left=10, status=ReservationPaymentStatus.PENDING):
    """One reserved seat with a deadline of its own."""
    now = datetime.now()
    return SingleReservationInfo(
        reservation_id=f"32026081512000{number}",
        reservation_obj=Mock(),
        reserved_at=now,
        expires_at=now + timedelta(minutes=minutes_left),
        status=status,
        seat_number=number,
        train_info=f"[KTX] 서울~부산 좌석 {number}",
    )


def booking(*seats, manually_stopped=False):
    return MultiReservationStatus(
        chat_id=CHAT_ID,
        reservations=list(seats),
        total_seats=len(seats),
        seat_strategy="random",
        created_at=datetime.now(),
        manually_stopped=manually_stopped,
    )


class ReminderFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.service = MultiReservationReminderService(self.storage, self.telegram)

    def having(self, status):
        self.storage.get_multi_reservation_status.return_value = status
        return status


class TestWhatTheReminderSays(ReminderFixture):
    """The message, which is the whole product of this service."""

    def message(self, status):
        return self.service._generate_reminder_message(status)

    def test_every_seat_is_listed(self):
        text = self.message(booking(seat(1), seat(2), seat(3)))

        assert "좌석 1" in text and "좌석 2" in text and "좌석 3" in text

    def test_the_seat_closest_to_expiring_is_singled_out(self):
        """
        With four deadlines running at once, "pay them" is not actionable.
        Which one to pay first is.
        """
        text = self.message(booking(seat(1, minutes_left=9), seat(2, minutes_left=2)))

        assert "좌석 2: ⚠️ 가장 급함!" in text
        assert "좌석 1: ⏳" in text

    def test_a_seat_already_paid_for_is_shown_as_settled(self):
        text = self.message(booking(seat(1, status=ReservationPaymentStatus.PAID), seat(2)))

        assert "좌석 1: ✅ 결제 완료" in text

    def test_a_seat_that_ran_out_of_time_says_so(self):
        text = self.message(booking(seat(1, minutes_left=-1), seat(2)))

        assert "좌석 1: ❌ 시간 만료" in text

    def test_a_cancelled_seat_says_so_too(self):
        text = self.message(booking(seat(1, status=ReservationPaymentStatus.CANCELLED)))

        assert "좌석 1: 🚫 취소됨" in text

    def test_the_seats_are_listed_in_order(self):
        """
        They are booked minutes apart and arrive in whatever order Redis
        hands them back. A list that reshuffles between reminders cannot be
        read at a glance.
        """
        text = self.message(booking(seat(3), seat(1), seat(2)))

        assert text.index("좌석 1") < text.index("좌석 2") < text.index("좌석 3")

    def test_it_counts_up_where_things_stand(self):
        text = self.message(
            booking(
                seat(1, status=ReservationPaymentStatus.PAID),
                seat(2),
                seat(3, minutes_left=-1),
            )
        )

        assert "대기 1개" in text and "완료 1개" in text and "만료 1개" in text

    def test_something_still_owed_carries_the_payment_link(self):
        assert "🔗 결제" in self.message(booking(seat(1)))

    def test_nothing_owed_carries_no_link(self):
        """The reminder is over; the link is an invitation to pay again."""
        text = self.message(booking(seat(1, status=ReservationPaymentStatus.PAID)))

        assert "🔗 결제" not in text

    def test_the_time_left_is_shown_per_seat(self):
        assert "남은 시간" in self.message(booking(seat(1)))


class TestWhenItStopsReminding(ReminderFixture):
    """The loop, and every way out of it."""

    def loop(self):
        with patch(f"{MODULE}.time.sleep") as sleep:
            self.service._reminder_loop(CHAT_ID)
        return sleep

    def sent(self):
        return [call.args[1] for call in self.telegram.send_message.call_args_list]

    def test_it_reminds_while_something_is_still_owed(self):
        self.storage.get_multi_reservation_status.side_effect = [
            booking(seat(1)),
            booking(seat(1, status=ReservationPaymentStatus.PAID)),
        ]

        self.loop()

        assert len(self.sent()) == 1

    def test_the_record_being_deleted_ends_it(self):
        """How /cancel and a finished booking both look from in here."""
        self.storage.get_multi_reservation_status.return_value = None

        self.loop()

        assert self.sent() == []

    def test_the_user_saying_stop_ends_it(self):
        self.having(booking(seat(1), manually_stopped=True))

        self.loop()

        assert self.sent() == []

    def test_everything_being_paid_for_ends_it(self):
        self.having(booking(seat(1, status=ReservationPaymentStatus.PAID)))

        self.loop()

        assert self.sent() == []

    def test_everything_expiring_ends_it(self):
        """There is nothing left to remind anyone to do."""
        self.having(booking(seat(1, minutes_left=-1)))

        self.loop()

        assert self.sent() == []

    def test_a_seat_that_ran_out_is_written_down_as_expired(self):
        """
        So the next reminder shows it as expired rather than counting down
        into negative numbers, and so the app agrees with the message.
        """
        status = self.having(booking(seat(1, minutes_left=-1), seat(2)))
        self.storage.get_multi_reservation_status.side_effect = [status, None]

        self.loop()

        assert status.reservations[0].status == ReservationPaymentStatus.EXPIRED
        self.storage.save_multi_reservation_status.assert_called_with(status)

    def test_nothing_is_written_down_when_nothing_changed(self):
        """A write per reminder, for hours, for no reason."""
        self.storage.get_multi_reservation_status.side_effect = [booking(seat(1)), None]

        self.loop()

        self.storage.save_multi_reservation_status.assert_not_called()

    def test_it_waits_between_reminders(self):
        self.storage.get_multi_reservation_status.side_effect = [booking(seat(1)), None]

        sleep = self.loop()

        sleep.assert_called_once_with(self.service.interval)

    def test_a_failure_mid_loop_ends_the_thread_quietly(self):
        """
        It is the whole body of a background thread. A traceback here reaches
        nobody, and the user is mid-payment.
        """
        self.storage.get_multi_reservation_status.side_effect = Exception("redis is down")

        self.loop()  # must not raise


class TestStartingAndStopping(ReminderFixture):
    """Who owns the thread."""

    def teardown_method(self):
        for thread in self.service.reminder_threads.values():
            thread.join(timeout=5)

    def test_nothing_to_remind_about_starts_no_thread(self):
        self.having(None)

        self.service.start_reminders(CHAT_ID)

        assert CHAT_ID not in self.service.reminder_threads

    def test_a_booking_gets_a_thread(self):
        self.having(booking(seat(1, status=ReservationPaymentStatus.PAID)))

        self.service.start_reminders(CHAT_ID)

        assert CHAT_ID in self.service.reminder_threads

    def test_starting_twice_does_not_get_two_reminders_a_minute(self):
        self.having(booking(seat(1)))
        running = Mock()
        running.is_alive.return_value = True
        self.service.reminder_threads[CHAT_ID] = running

        self.service.start_reminders(CHAT_ID)

        assert self.service.reminder_threads[CHAT_ID] is running

    def test_a_finished_thread_can_be_replaced(self):
        self.having(booking(seat(1, status=ReservationPaymentStatus.PAID)))
        finished = Mock()
        finished.is_alive.return_value = False
        self.service.reminder_threads[CHAT_ID] = finished

        self.service.start_reminders(CHAT_ID)

        assert self.service.reminder_threads[CHAT_ID] is not finished

    def test_the_user_saying_stop_is_written_down(self):
        """
        The loop lives on a thread with no way to be interrupted, so this is
        how it is told: it reads the flag on its next pass.
        """
        status = self.having(booking(seat(1)))

        self.service.stop_reminders(CHAT_ID)

        assert status.manually_stopped is True
        self.storage.save_multi_reservation_status.assert_called_once_with(status)

    def test_a_timeout_is_not_written_down_as_the_user_giving_up(self):
        """
        The distinction is worth keeping: one means they dealt with it, the
        other means they never saw it.
        """
        status = self.having(booking(seat(1)))

        self.service.stop_reminders(CHAT_ID, manual=False)

        assert status.manually_stopped is False
        self.storage.save_multi_reservation_status.assert_not_called()

    def test_stopping_something_that_is_not_there_is_not_an_error(self):
        self.having(None)

        self.service.stop_reminders(CHAT_ID)  # must not raise


class TestMarkingSeatsPaid(ReminderFixture):
    """What the app calls when it learns a seat has been settled."""

    def test_one_seat_can_be_marked(self):
        status = self.having(booking(seat(1), seat(2)))

        assert self.service.mark_seat_paid(CHAT_ID, 2) is True
        assert status.reservations[1].status == ReservationPaymentStatus.PAID

    def test_a_seat_that_is_not_there_is_reported_rather_than_invented(self):
        self.having(booking(seat(1)))

        assert self.service.mark_seat_paid(CHAT_ID, 9) is False

    def test_marking_a_seat_on_a_booking_that_is_gone_is_reported(self):
        self.having(None)

        assert self.service.mark_seat_paid(CHAT_ID, 1) is False

    def test_the_lot_can_be_marked_at_once(self):
        """What the user pressing "결제 완료" means."""
        status = self.having(booking(seat(1), seat(2)))

        assert self.service.mark_all_paid(CHAT_ID) is True
        assert all(r.status == ReservationPaymentStatus.PAID for r in status.reservations)

    def test_marking_the_lot_stops_the_reminders(self):
        status = self.having(booking(seat(1)))

        self.service.mark_all_paid(CHAT_ID)

        assert status.manually_stopped is True

    def test_a_seat_that_already_expired_is_not_marked_as_paid(self):
        """It was not paid for. Saying so would be the reminder lying."""
        status = self.having(booking(seat(1, status=ReservationPaymentStatus.EXPIRED)))

        self.service.mark_all_paid(CHAT_ID)

        assert status.reservations[0].status == ReservationPaymentStatus.EXPIRED

    def test_marking_a_booking_that_is_gone_is_reported(self):
        self.having(None)

        assert self.service.mark_all_paid(CHAT_ID) is False


class TestTheBookingItself:
    """
    The record the reminders are generated from.

    Its own tests because the counting is where an off-by-one shows up as a
    reminder that never stops.
    """

    def test_an_expired_seat_stops_counting_as_pending(self):
        status = booking(seat(1, minutes_left=-1), seat(2))

        assert status.get_pending_count() == 1
        assert status.get_expired_count() == 1

    def test_a_booking_with_nothing_pending_needs_no_reminder(self):
        assert not booking(seat(1, status=ReservationPaymentStatus.PAID)).should_show_reminder()

    def test_a_booking_the_user_stopped_needs_no_reminder(self):
        assert not booking(seat(1), manually_stopped=True).should_show_reminder()

    def test_the_most_urgent_seat_is_the_one_expiring_first(self):
        status = booking(seat(1, minutes_left=9), seat(2, minutes_left=3))

        assert status.get_most_urgent_reservation().seat_number == 2

    def test_an_expired_seat_is_never_the_most_urgent(self):
        """It is not urgent; it is over."""
        status = booking(seat(1, minutes_left=-5), seat(2, minutes_left=9))

        assert status.get_most_urgent_reservation().seat_number == 2

    def test_nothing_pending_has_nothing_urgent(self):
        assert booking(seat(1, minutes_left=-1)).get_most_urgent_reservation() is None

    def test_the_time_left_reads_the_way_it_is_said(self):
        assert "분" in seat(1).get_remaining_minutes_display()

    def test_a_seat_out_of_time_says_so_rather_than_counting_backwards(self):
        assert seat(1, minutes_left=-1).get_remaining_minutes_display() == "만료됨"

    def test_a_settled_seat_has_no_time_left_to_report(self):
        assert seat(1, status=ReservationPaymentStatus.PAID).get_remaining_seconds() == 0

    def test_marking_them_all_expired_leaves_the_settled_ones_alone(self):
        status = booking(seat(1), seat(2, status=ReservationPaymentStatus.PAID))

        status.mark_all_expired()

        assert status.reservations[0].status == ReservationPaymentStatus.EXPIRED
        assert status.reservations[1].status == ReservationPaymentStatus.PAID

    @pytest.mark.parametrize("seat_number", [1, 2])
    def test_a_seat_can_be_found_by_its_number(self, seat_number):
        status = booking(seat(1), seat(2))

        assert status.mark_reservation_paid(seat_number) is True
