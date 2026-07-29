"""
Watching a chosen set of trains rather than a whole time window.

The search could only ever be given a time range, and took the first seat it
found anywhere in it. That is the right behaviour when any train will do and
the wrong one when only one will - a connection to make, someone meeting you.
So the user is shown what runs in the window and can tick the trains worth
waiting for.

Two things carry the weight. The list has to include sold-out trains, because
a train with seats needs no watching and the ones worth ticking are exactly
the ones an ordinary search leaves out. And an empty selection has to keep
meaning "the whole window", because that is what every search made before this
existed meant, and they have to go on meaning it after a restart.
"""

from unittest.mock import Mock, patch

import pytest

from korail_bot.handlers import ConversationHandler
from korail_bot.models import TrainSearchParams, UserCredentials, UserProgress, UserSession
from korail_bot.services import ReservationService, TelegramService
from korail_bot.services.korail_service import KorailService
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot import keyboards

CHAT_ID = 12345


def fake_train(train_no, dep="185800", arr="195000", name="KTX", has_seat=False):
    """A stand-in for a korail2 train, with only what the bot reads off one."""
    train = Mock()
    train.train_no = train_no
    train.dep_time = dep
    train.arr_time = arr
    train.train_type_name = name
    train.has_seat = Mock(return_value=has_seat)
    return train


def session_ready_to_pick() -> UserSession:
    """A session that has answered everything except which trains to watch."""
    session = UserSession(
        chat_id=CHAT_ID, in_progress=True, last_action=UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
    )
    session.credentials = UserCredentials(korail_id="010-1234-5678", korail_pw="pw")
    session.train_info = {
        "depDate": "20991231",
        "srcLocate": "서울",
        "dstLocate": "부산",
        "depTime": "090000",
        "maxDepTime": "1800",
        "trainType": "TrainType.KTX",
        "trainTypeShow": "KTX",
        "specialInfo": "ReserveOption.GENERAL_FIRST",
        "specialInfoShow": "GENERAL_FIRST",
        "passengerCount": 1,
        "seatStrategy": "consecutive",
        "seatStrategyShow": "연속 좌석",
    }
    return session


class TestTrainFilterInSearch:
    """The filter that makes a chosen watch actually narrower."""

    def make_service(self, found):
        service = KorailService()
        service._logged_in = True
        service._korail_instance = Mock()
        service._korail_instance.search_train.return_value = found
        return service

    def test_only_the_chosen_trains_come_back(self):
        service = self.make_service([fake_train("101"), fake_train("105"), fake_train("109")])

        trains = service.search_trains(
            "20991231", "서울", "부산", verbose=False, train_numbers=["105", "109"]
        )

        assert [train.train_no for train in trains] == ["105", "109"]

    def test_an_empty_selection_keeps_every_train(self):
        """
        The whole point of the default. Treating empty as "nothing matches"
        would turn every search that predates this feature into one that can
        never succeed.
        """
        found = [fake_train("101"), fake_train("105")]

        for selection in (None, []):
            trains = self.make_service(found).search_trains(
                "20991231", "서울", "부산", verbose=False, train_numbers=selection
            )
            assert len(trains) == 2

    def test_a_chosen_train_that_stops_running_simply_stops_appearing(self):
        """
        Timetables change and trains get cancelled. Nothing should blow up;
        the search just has less to watch.
        """
        service = self.make_service([fake_train("101")])

        trains = service.search_trains(
            "20991231", "서울", "부산", verbose=False, train_numbers=["999"]
        )

        assert trains == []

    def test_the_search_loop_asks_only_for_trains_it_could_reserve(self):
        """
        include_no_seats defaults off. A loop handed sold-out trains would
        attempt a reservation on every one of them, every pass.
        """
        service = self.make_service([])

        service.search_trains("20991231", "서울", "부산", verbose=False)

        _, kwargs = service._korail_instance.search_train.call_args
        assert kwargs["include_no_seats"] is False

    def test_listing_asks_for_sold_out_trains_too(self):
        """
        The inverse, and the reason the feature works at all: a sold-out
        train is precisely what someone wants to sit and watch.
        """
        service = self.make_service([])

        service.search_trains("20991231", "서울", "부산", verbose=False, include_no_seats=True)

        _, kwargs = service._korail_instance.search_train.call_args
        assert kwargs["include_no_seats"] is True


class TestDescribeTrain:
    """Turning a korail2 train into something a button and Redis can hold."""

    def test_times_are_shown_as_clock_faces(self):
        described = ConversationHandler._describe_train(fake_train("101", "185800", "195000"))

        assert described["label"] == "18:58→19:50 KTX"
        assert described["no"] == "101"

    def test_a_train_with_no_seats_is_marked_sold_out(self):
        assert ConversationHandler._describe_train(fake_train("101", has_seat=False))["soldout"]
        assert not ConversationHandler._describe_train(fake_train("101", has_seat=True))["soldout"]

    def test_a_train_missing_the_fields_we_read_does_not_break_the_list(self):
        """
        korail2 fills these from a response we do not control. One odd train
        must not cost the user the whole list.
        """
        described = ConversationHandler._describe_train(object())

        assert described["no"] == ""
        assert "??:??" in described["label"]
        assert described["soldout"] is True

    def test_the_description_survives_a_round_trip_through_json(self):
        """It is stored on the session, which goes to Redis as JSON."""
        import json

        described = ConversationHandler._describe_train(fake_train("101"))

        assert json.loads(json.dumps(described)) == described


class TestShowingTheList:
    """Fetching the trains and putting them on screen."""

    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_or_create_app_session_start.return_value = "1700000000000"
        self.telegram = Mock(spec=TelegramService)
        self.telegram.send_and_get_id.return_value = 555
        self.reservation = Mock(spec=ReservationService)
        self.handler = ConversationHandler(self.storage, self.telegram, self.reservation)
        self.session = session_ready_to_pick()
        self.storage.get_user_session.return_value = self.session

    def show(self, trains):
        with (
            patch.object(KorailService, "login", return_value=True),
            patch.object(KorailService, "search_trains", return_value=trains) as search,
        ):
            self.handler._show_train_selection(CHAT_ID, self.session)
        return search

    def test_the_list_is_fetched_with_sold_out_trains_included(self):
        search = self.show([fake_train("101")])

        assert search.call_args.kwargs["include_no_seats"] is True

    def test_the_trains_are_stored_and_offered(self):
        self.show([fake_train("101"), fake_train("105")])

        assert [option["no"] for option in self.session.train_info["trainOptions"]] == [
            "101",
            "105",
        ]
        keyboard = self.telegram.send_and_get_id.call_args.kwargs["reply_markup"]
        data = [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]
        assert f"{keyboards.STEP_TRAIN_SELECT}:101" in data
        assert f"{keyboards.STEP_TRAIN_SELECT}:105" in data

    def test_nothing_is_ticked_to_begin_with(self):
        self.show([fake_train("101")])

        assert self.session.train_info["selectedTrains"] == []
        assert self.session.last_action == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS

    def test_the_message_id_is_kept_so_ticks_can_rewrite_it(self):
        self.show([fake_train("101")])

        assert self.session.train_info["trainListMessageId"] == 555

    def test_an_overlong_list_is_cut_and_says_so(self):
        """
        A busy corridor across a wide window returns more trains than a
        keyboard can carry. Cutting silently would read as a timetable with
        holes in it.
        """
        limit = ConversationHandler.MAX_TRAIN_OPTIONS
        self.show([fake_train(str(n)) for n in range(limit + 10)])

        assert len(self.session.train_info["trainOptions"]) == limit
        assert "앞의" in self.telegram.send_and_get_id.call_args.args[1]

    def test_korail_being_unreachable_does_not_strand_the_user(self):
        """
        Picking trains is an optional narrowing. Failing to offer it must
        leave the flow on the summary, not on a step with no way forward.
        """
        with patch.object(KorailService, "login", return_value=False):
            self.handler._show_train_selection(CHAT_ID, self.session)

        assert self.session.last_action == UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        assert self.session.train_info["selectedTrains"] == []

    def test_a_search_error_does_not_strand_the_user_either(self):
        with (
            patch.object(KorailService, "login", return_value=True),
            patch.object(KorailService, "search_trains", side_effect=Exception("Korail down")),
        ):
            self.handler._show_train_selection(CHAT_ID, self.session)

        assert self.session.last_action == UserProgress.TRAIN_SELECT_INPUT_SUCCESS

    def test_an_empty_timetable_moves_on_rather_than_offering_nothing(self):
        self.show([])

        assert self.session.last_action == UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        assert self.session.train_info["selectedTrains"] == []


class TestTicking:
    """Building up a selection, by button or by typing."""

    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.telegram.edit_message_reply_markup.return_value = True
        self.handler = ConversationHandler(
            self.storage, self.telegram, Mock(spec=ReservationService)
        )
        self.session = session_ready_to_pick()
        self.session.train_info["trainOptions"] = [
            {"no": "101", "label": "09:00→11:40 KTX", "soldout": True},
            {"no": "105", "label": "10:00→12:40 KTX", "soldout": True},
            {"no": "109", "label": "11:00→13:40 KTX", "soldout": False},
        ]
        self.session.train_info["selectedTrains"] = []
        self.session.train_info["trainListMessageId"] = 555
        self.storage.get_user_session.return_value = self.session

    def send(self, text):
        self.handler.handle_message(CHAT_ID, text)
        return self.session.train_info.get("selectedTrains")

    def test_a_press_ticks_a_train(self):
        assert self.send("101") == ["101"]

    def test_pressing_a_ticked_train_unticks_it(self):
        """
        The same button is the only thing on screen for that train, so it has
        to be the way back out as well as the way in.
        """
        self.send("101")
        assert self.send("101") == []

    def test_ticks_accumulate(self):
        self.send("101")
        assert self.send("109") == ["101", "109"]

    def test_ticking_leaves_the_question_open(self):
        """Several trains is the normal answer, so one tick cannot end it."""
        self.send("101")

        assert self.session.last_action == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS

    def test_a_tick_rewrites_the_list_instead_of_sending_another(self):
        self.send("101")

        self.telegram.edit_message_reply_markup.assert_called_once()
        self.telegram.send_and_get_id.assert_not_called()

    def test_the_rewritten_keyboard_shows_the_tick(self):
        self.send("105")

        keyboard = self.telegram.edit_message_reply_markup.call_args.args[2]
        labels = {b["callback_data"]: b["text"] for row in keyboard["inline_keyboard"] for b in row}
        assert labels[f"{keyboards.STEP_TRAIN_SELECT}:105"].startswith("☑️")
        assert labels[f"{keyboards.STEP_TRAIN_SELECT}:101"].startswith("⬜")

    def test_a_fresh_list_is_sent_when_the_old_one_cannot_be_edited(self):
        """
        Telegram refuses to edit a message past a certain age. A tick that
        appears to do nothing is worse than a duplicated list.
        """
        self.telegram.edit_message_reply_markup.return_value = False
        self.telegram.send_and_get_id.return_value = 999

        self.send("101")

        self.telegram.send_and_get_id.assert_called_once()
        assert self.session.train_info["trainListMessageId"] == 999

    @pytest.mark.parametrize("typed", ["101 109", "101,109", "101, 109"])
    def test_typing_several_numbers_sets_the_selection(self, typed):
        """A typed list is a statement of intent, not a run of toggles."""
        assert self.send(typed) == ["101", "109"]

    def test_a_number_that_is_not_on_the_list_is_refused(self):
        self.send("777")

        assert self.session.train_info["selectedTrains"] == []
        assert "777" in self.telegram.send_message.call_args.args[1]

    def test_one_bad_number_refuses_the_whole_typed_list(self):
        """
        Half-applying it would leave the user believing they had picked two
        trains when the search is watching one.
        """
        self.send("101 777")

        assert self.session.train_info["selectedTrains"] == []


class TestFinishing:
    """Leaving the list, and what the search is told."""

    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.telegram = Mock(spec=TelegramService)
        self.reservation = Mock(spec=ReservationService)
        self.reservation.start_reservation_process.return_value = True
        self.handler = ConversationHandler(self.storage, self.telegram, self.reservation)
        self.session = session_ready_to_pick()
        self.session.train_info["trainOptions"] = [
            {"no": "101", "label": "09:00→11:40 KTX", "soldout": True},
            {"no": "105", "label": "10:00→12:40 KTX", "soldout": True},
        ]
        self.session.train_info["selectedTrains"] = ["101", "105"]
        self.session.train_info["trainListMessageId"] = 555
        self.storage.get_user_session.return_value = self.session

    def test_done_keeps_the_selection_and_moves_to_the_summary(self):
        self.handler.handle_message(CHAT_ID, keyboards.TRAIN_SELECT_DONE)

        assert self.session.train_info["selectedTrains"] == ["101", "105"]
        assert self.session.last_action == UserProgress.TRAIN_SELECT_INPUT_SUCCESS

    @pytest.mark.parametrize("answer", [keyboards.TRAIN_SELECT_ALL, "전체", "0"])
    def test_watching_everything_clears_the_selection(self, answer):
        self.handler.handle_message(CHAT_ID, answer)

        assert self.session.train_info["selectedTrains"] == []
        assert self.session.last_action == UserProgress.TRAIN_SELECT_INPUT_SUCCESS

    def test_the_list_is_dropped_from_the_session_once_it_is_done_with(self):
        """
        It is the bulky part of a record that is rewritten on every step from
        here on, and it has no reader left.
        """
        self.handler.handle_message(CHAT_ID, keyboards.TRAIN_SELECT_DONE)

        assert "trainOptions" not in self.session.train_info
        assert "trainListMessageId" not in self.session.train_info

    def test_refresh_asks_korail_again(self):
        """Availability moves while the list sits on screen."""
        with (
            patch.object(KorailService, "login", return_value=True),
            patch.object(KorailService, "search_trains", return_value=[]) as search,
        ):
            self.handler.handle_message(CHAT_ID, keyboards.TRAIN_SELECT_REFRESH)

        search.assert_called_once()

    def test_the_summary_says_which_trains_are_being_watched(self):
        self.handler.handle_message(CHAT_ID, keyboards.TRAIN_SELECT_DONE)

        summary = self.telegram.send_message.call_args.args[1]
        assert "101" in summary and "105" in summary

    def test_the_summary_says_so_when_the_whole_window_is_watched(self):
        self.handler.handle_message(CHAT_ID, keyboards.TRAIN_SELECT_ALL)

        assert "시간대 전체" in self.telegram.send_message.call_args.args[1]

    def test_the_selection_reaches_the_search(self):
        self.handler.handle_message(CHAT_ID, keyboards.TRAIN_SELECT_DONE)
        self.session.last_action = UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        self.handler.handle_message(CHAT_ID, "Y")

        params = self.reservation.start_reservation_process.call_args.kwargs["search_params"]
        assert params.train_numbers == ["101", "105"]
        assert params.watches_specific_trains()

    def test_watching_everything_reaches_the_search_as_no_filter(self):
        self.handler.handle_message(CHAT_ID, keyboards.TRAIN_SELECT_ALL)
        self.session.last_action = UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        self.handler.handle_message(CHAT_ID, "Y")

        params = self.reservation.start_reservation_process.call_args.kwargs["search_params"]
        assert params.train_numbers == []
        assert not params.watches_specific_trains()


class TestCrossingTheProcessBoundary:
    """The search runs elsewhere, and a restart rebuilds it from Redis."""

    def test_the_numbers_go_to_the_child_as_one_argument(self):
        from korail_bot.services.reservation_service import ReservationService as Real

        service = Real(Mock(spec=StorageInterface), Mock(spec=TelegramService))
        params = TrainSearchParams(
            dep_date="20991231",
            src_locate="서울",
            dst_locate="부산",
            dep_time="090000",
            train_numbers=["101", "105"],
        )

        with (
            patch("korail_bot.services.reservation_service.subprocess.Popen") as popen,
            patch.object(Real, "_may_start", return_value=True),
        ):
            popen.return_value.pid = 4242
            service.start_reservation_process(CHAT_ID, "010-1234-5678", "pw", params)

        assert popen.call_args.args[0][-1] == "101,105"

    def test_no_selection_crosses_as_an_empty_argument(self):
        """
        The slot has to be there either way - the child reads it by position,
        and a missing argument would shift nothing but would leave the child
        guessing.
        """
        from korail_bot.services.reservation_service import ReservationService as Real

        service = Real(Mock(spec=StorageInterface), Mock(spec=TelegramService))
        params = TrainSearchParams(
            dep_date="20991231", src_locate="서울", dst_locate="부산", dep_time="090000"
        )

        with (
            patch("korail_bot.services.reservation_service.subprocess.Popen") as popen,
            patch.object(Real, "_may_start", return_value=True),
        ):
            popen.return_value.pid = 4242
            service.start_reservation_process(CHAT_ID, "010-1234-5678", "pw", params)

        assert popen.call_args.args[0][-1] == ""
