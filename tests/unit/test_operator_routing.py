"""
Which railway the search process ends up talking to.

The process is handed its instructions on argv and has to build the right
client from them. Two things have to hold at once: a search told to use SRT
must reach SR, and a search that says nothing - which is every search started
by a build older than this one, including ones being resumed right now - must
still reach Korail.
"""

import sys
from unittest.mock import Mock, patch

import pytest
from korail2 import ReserveOption, TrainType
from SRT import SeatType

from korail_bot.config.settings import settings
from korail_bot.models import Operator
from korail_bot.services import KorailService, SrtService
from korail_bot.telegramBot.telebotBackProcess import BackgroundReservationProcess

MODULE = "korail_bot.telegramBot.telebotBackProcess"

#: argv as the parent writes it, minus the program name.
BASE_ARGS = [
    "20991231",  # dep_date
    "수서",  # src_locate
    "부산",  # dst_locate
    "090000",  # dep_time
    "TrainType.KTX",  # train_type
    "ReserveOption.GENERAL_FIRST",  # special_option
    "555",  # chat_id
    "1800",  # max_dep_time
    "1",  # passenger_count
    "consecutive",  # seat_strategy
    "",  # train_numbers
]


def build(*extra_args):
    """
    Start a search process the way the parent does, with nothing real behind it.

    Everything the constructor reaches for outside itself is stubbed: Redis,
    Telegram, the reminder services and the credentials on stdin. What is left
    running is the part under test - reading argv and building a client.
    """
    argv = ["telebotBackProcess", *BASE_ARGS, *extra_args]
    credentials = '{"username": "010-1234-5678", "password": "pw"}\n'

    with (
        patch.object(sys, "argv", argv),
        patch.object(sys, "stdin", Mock(readline=Mock(return_value=credentials))),
        patch(f"{MODULE}.RedisStorage"),
        patch(f"{MODULE}.TelegramService"),
        patch(f"{MODULE}.PaymentReminderService"),
        patch(f"{MODULE}.MultiReservationReminderService"),
    ):
        return BackgroundReservationProcess()


class TestPickingTheRailway:
    def test_being_told_srt_reaches_sr(self):
        process = build("srt")

        assert process.operator is Operator.SRT
        assert isinstance(process.rail, SrtService)

    def test_being_told_korail_reaches_korail(self):
        process = build("korail")

        assert process.operator is Operator.KORAIL
        assert isinstance(process.rail, KorailService)

    def test_a_search_that_says_nothing_reaches_korail(self):
        """
        Every search started before there were two railways arrives this way,
        including one being resumed after this deploy. It is a Korail search.
        """
        process = build()

        assert process.operator is Operator.KORAIL
        assert isinstance(process.rail, KorailService)

    def test_an_unrecognisable_railway_still_starts_a_search(self):
        process = build("shinkansen")

        assert isinstance(process.rail, KorailService)


class TestWhatEachRailwayIsTold:
    def test_the_seat_preference_becomes_srs_own_enum(self):
        """
        The two clients spell the four preferences identically in two enums
        that know nothing of each other, so the name is what carries across.
        """
        process = build("srt")

        assert process.reserve_option is SeatType.GENERAL_FIRST

    def test_the_seat_preference_stays_korails_enum_for_korail(self):
        process = build("korail")

        assert process.reserve_option == ReserveOption.GENERAL_FIRST

    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            ("ReserveOption.GENERAL_ONLY", SeatType.GENERAL_ONLY),
            ("ReserveOption.SPECIAL_FIRST", SeatType.SPECIAL_FIRST),
            ("ReserveOption.SPECIAL_ONLY", SeatType.SPECIAL_ONLY),
            ("GENERAL_ONLY", SeatType.GENERAL_ONLY),
        ],
    )
    def test_every_preference_crosses_to_sr(self, stored, expected):
        """A favourite saved against Korail still means something on SR."""
        args = list(BASE_ARGS)
        args[5] = stored
        argv = ["telebotBackProcess", *args, "srt"]

        with (
            patch.object(sys, "argv", argv),
            patch.object(
                sys,
                "stdin",
                Mock(readline=Mock(return_value='{"username": "u", "password": "p"}\n')),
            ),
            patch(f"{MODULE}.RedisStorage"),
            patch(f"{MODULE}.TelegramService"),
            patch(f"{MODULE}.PaymentReminderService"),
            patch(f"{MODULE}.MultiReservationReminderService"),
        ):
            process = BackgroundReservationProcess()

        assert process.reserve_option is expected

    def test_the_seat_preference_reaches_the_srt_service_itself(self):
        """
        SR reports the two seat classes separately, so the service needs this
        while searching and not only while reserving.
        """
        args = list(BASE_ARGS)
        args[5] = "ReserveOption.SPECIAL_ONLY"
        argv = ["telebotBackProcess", *args, "srt"]

        with (
            patch.object(sys, "argv", argv),
            patch.object(
                sys,
                "stdin",
                Mock(readline=Mock(return_value='{"username": "u", "password": "p"}\n')),
            ),
            patch(f"{MODULE}.RedisStorage"),
            patch(f"{MODULE}.TelegramService"),
            patch(f"{MODULE}.PaymentReminderService"),
            patch(f"{MODULE}.MultiReservationReminderService"),
        ):
            process = BackgroundReservationProcess()

        assert process.rail._seat_type is SeatType.SPECIAL_ONLY

    def test_the_train_type_question_does_not_apply_to_sr(self):
        """SR runs SRT and nothing else, so the answer cannot be a filter."""
        process = build("srt")

        assert process.train_type == "SRT"

    def test_the_train_type_still_filters_for_korail(self):
        process = build("korail")

        assert process.train_type == TrainType.KTX


class TestWhatTheUserIsTold:
    def test_each_railway_is_named_the_way_the_user_would_say_it(self):
        assert build("srt").operator_name == "SRT"
        assert build("korail").operator_name == "코레일"

    def test_each_railway_sends_the_user_to_its_own_payment_page(self):
        """A Korail link on an SRT reservation is a seat lost to a wrong turn."""
        assert build("srt").payment_url == settings.SRT_PAYMENT_URL
        assert build("korail").payment_url == settings.KORAIL_PAYMENT_URL

    def test_the_two_payment_pages_are_not_the_same_page(self):
        assert settings.SRT_PAYMENT_URL != settings.KORAIL_PAYMENT_URL


class TestReadingAReservationBack:
    """
    The two clients file the same facts under different names. Everything
    downstream of a booking needs them, and gets them through the service.
    """

    def test_korails_reservation_number_is_found_where_korail2_puts_it(self):
        reservation = Mock(rsv_id="320260731221946")

        assert KorailService.reservation_id(reservation) == "320260731221946"

    def test_srs_reservation_number_is_found_where_sr_puts_it(self):
        reservation = Mock(reservation_number="123456789")

        assert SrtService.reservation_id(reservation) == "123456789"

    def test_a_number_that_arrived_as_an_integer_is_returned_as_text(self):
        """Long enough that anything treating one as an int loses precision."""
        assert KorailService.reservation_id(Mock(rsv_id=320260731221946)) == "320260731221946"
        assert SrtService.reservation_id(Mock(reservation_number=123456789)) == "123456789"

    def test_a_reservation_with_no_number_says_so(self):
        assert KorailService.reservation_id(Mock(spec=[])) is None
        assert SrtService.reservation_id(Mock(spec=[])) is None

    def test_korails_payment_deadline_is_read_from_its_own_fields(self):
        reservation = Mock(buy_limit_date="20260730", buy_limit_time="015648")

        assert KorailService.payment_due(reservation) == ("20260730", "015648")

    def test_srs_payment_deadline_is_read_from_its_own_fields(self):
        reservation = Mock(payment_date="20260802", payment_time="235959")

        assert SrtService.payment_due(reservation) == ("20260802", "235959")

    def test_a_reservation_that_states_no_deadline_says_so(self):
        assert KorailService.payment_due(Mock(spec=[])) == (None, None)
        assert SrtService.payment_due(Mock(spec=[])) == (None, None)

    def test_neither_reads_the_others_fields(self):
        """
        The failure this guards against is silent: a wrong reading gives None,
        the payment watch declines to run, and nobody is told the seat went.
        """
        korail_shaped = Mock(spec=["rsv_id", "buy_limit_date", "buy_limit_time"])
        korail_shaped.rsv_id = "111"
        korail_shaped.buy_limit_date = "20260730"
        korail_shaped.buy_limit_time = "015648"

        assert SrtService.reservation_id(korail_shaped) is None
        assert SrtService.payment_due(korail_shaped) == (None, None)
