"""
Saved searches, from the button that creates one to the search it starts.

Someone who takes the same journey often answers the same nine questions every
time. A favourite is all of those answers except the date - the one that is
different every trip, and the one a saved search must never pretend to know.

These go through the update processor rather than calling the handlers, because
the parts most likely to break are the seams: a save that quietly closes the
summary and costs the user their start button, a rename that swallows the next
message an hour later, a loaded favourite that asks the eight questions it was
supposed to skip.
"""

from unittest.mock import Mock, patch

import pytest

from korail_bot.handlers.update_processor import TelegramUpdateProcessor
from korail_bot.models import (
    FavouriteSearch,
    OnboardedAccount,
    UserCredentials,
    UserProgress,
    UserSession,
)
from korail_bot.services import PaymentReminderService, ReservationService, TelegramService
from korail_bot.services.korail_service import KorailService
from korail_bot.telegramBot import keyboards

CHAT_ID = 55
MESSAGE_ID = 10


@pytest.fixture
def telegram():
    return Mock(spec=TelegramService)


@pytest.fixture
def processor(storage, telegram):
    return TelegramUpdateProcessor(
        storage, telegram, Mock(spec=ReservationService), Mock(spec=PaymentReminderService)
    )


def at_summary(storage) -> UserSession:
    """A session that has answered everything and is looking at the summary."""
    session = UserSession(
        chat_id=CHAT_ID, in_progress=True, last_action=UserProgress.TRAIN_SELECT_INPUT_SUCCESS
    )
    session.credentials = UserCredentials(korail_id="010-1111-2222", korail_pw="pw")
    session.train_info = {
        "depDate": "20260810",
        "srcLocate": "서울",
        "dstLocate": "부산",
        "depTime": "090000",
        "maxDepTime": "1800",
        "trainType": "TrainType.KTX",
        "trainTypeShow": "KTX 계열만",
        "specialInfo": "ReserveOption.GENERAL_FIRST",
        "specialInfoShow": "일반실 우선",
        "passengerCount": 2,
        "seatStrategy": "consecutive",
        "seatStrategyShow": "연속 좌석",
        "selectedTrains": [],
    }
    storage.save_user_session(session)
    return session


def press(data):
    return {
        "update_id": 1,
        "callback_query": {
            "id": "q",
            "from": {"id": CHAT_ID},
            "message": {"message_id": MESSAGE_ID, "chat": {"id": CHAT_ID}, "text": "요약"},
            "data": data,
        },
    }


def typed(text):
    return {"update_id": 2, "message": {"chat": {"id": CHAT_ID}, "text": text}}


def texts(telegram):
    """Everything the bot said, whether it was sent fresh or edited in place."""
    said = [call.args[1] for call in telegram.send_message.call_args_list]
    said += [call.args[2] for call in telegram.edit_message_text.call_args_list]
    return said


def save_one(processor, storage) -> FavouriteSearch:
    """Save the summary, and hand back the favourite that just appeared."""
    at_summary(storage)
    processor.process(press(f"{keyboards.STEP_CONFIRM}:{keyboards.CONFIRM_SAVE_FAVOURITE}"))
    # The list is oldest first, so the new one is at the end.
    return storage.get_favourites(CHAT_ID)[-1]


def a_bookable_date() -> str:
    """
    A date the validator will accept.

    It refuses the past and anything more than a year out, so a fixed string
    would either be stale or rejected depending on when the suite is run.
    """
    from datetime import datetime, timedelta

    return (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")


class TestSavingFromTheSummary:
    """The one screen where every answer is already on display."""

    def test_the_summary_is_saved_with_everything_but_the_date(self, processor, storage):
        favourite = save_one(processor, storage)

        assert favourite.src_locate == "서울"
        assert favourite.dst_locate == "부산"
        assert favourite.dep_time == "090000"
        assert favourite.max_dep_time == "1800"
        assert favourite.passenger_count == 2
        assert "20260810" not in str(favourite)

    def test_it_is_named_after_the_route_without_being_asked(self, processor, storage):
        """
        Being made to type a name before a shortcut can be saved would cost
        more than the shortcut saves.
        """
        assert save_one(processor, storage).name == "서울 → 부산"

    def test_the_summary_keeps_its_buttons(self, processor, storage, telegram):
        """
        The question on that screen is still "start this search, or not?".
        Settling the keyboard would take the start button away from someone
        who had only asked for a bookmark.
        """
        save_one(processor, storage)

        telegram.edit_message_text.assert_not_called()
        telegram.edit_message_reply_markup.assert_not_called()

    def test_the_booking_is_left_exactly_where_it_was(self, processor, storage):
        save_one(processor, storage)

        assert storage.get_user_session(CHAT_ID).last_action == (
            UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        )

    def test_saving_twice_saves_two(self, processor, storage):
        """
        Same route, different time window is a different journey. Merging by
        route would silently overwrite one of them.
        """
        save_one(processor, storage)
        save_one(processor, storage)

        assert len(storage.get_favourites(CHAT_ID)) == 2

    def test_a_half_answered_session_is_refused(self, processor, storage, telegram):
        session = at_summary(storage)
        session.train_info.pop("dstLocate")
        storage.save_user_session(session)

        processor.process(press(f"{keyboards.STEP_CONFIRM}:{keyboards.CONFIRM_SAVE_FAVOURITE}"))

        assert storage.get_favourites(CHAT_ID) == []
        assert any("저장할 수 없습니다" in text for text in texts(telegram))

    def test_the_limit_is_refused_rather_than_made_room_for(self, processor, storage, telegram):
        """
        Which of their saved journeys to drop is not a decision to take on
        someone's behalf.
        """
        from korail_bot.config.settings import settings

        for _ in range(settings.MAX_FAVOURITES):
            save_one(processor, storage)
        telegram.reset_mock()

        save_one_more = press(f"{keyboards.STEP_CONFIRM}:{keyboards.CONFIRM_SAVE_FAVOURITE}")
        at_summary(storage)
        processor.process(save_one_more)

        assert len(storage.get_favourites(CHAT_ID)) == settings.MAX_FAVOURITES
        assert any("까지 저장할 수 있습니다" in text for text in texts(telegram))


class TestTheList:
    """/fav, and walking through it."""

    def test_an_empty_list_explains_how_to_fill_it(self, processor, telegram):
        """A screen that only says "없습니다" leaves the reader nowhere."""
        processor.process(typed("/fav"))

        said = "\n".join(texts(telegram))
        assert "즐겨찾기에 저장" in said

    def test_the_list_offers_what_was_saved(self, processor, storage, telegram):
        favourite = save_one(processor, storage)
        telegram.reset_mock()

        processor.process(typed("/fav"))

        keyboard = telegram.send_message.call_args.kwargs["reply_markup"]
        data = [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]
        assert f"{keyboards.STEP_FAV}:{keyboards.FAV_PICK}{favourite.fav_id}" in data

    def test_picking_one_shows_what_it_holds(self, processor, storage, telegram):
        favourite = save_one(processor, storage)
        telegram.reset_mock()

        processor.process(press(f"{keyboards.STEP_FAV}:{keyboards.FAV_PICK}{favourite.fav_id}"))

        detail = "\n".join(texts(telegram))
        assert "서울 → 부산" in detail
        assert "09:00~18:00" in detail
        assert "날짜는 저장하지 않습니다" in detail

    def test_the_list_is_walked_in_place(self, processor, storage, telegram):
        """Pressing through it must not leave a column of copies in the chat."""
        favourite = save_one(processor, storage)
        processor.process(typed("/fav"))
        telegram.reset_mock()

        processor.process(press(f"{keyboards.STEP_FAV}:{keyboards.FAV_PICK}{favourite.fav_id}"))

        telegram.edit_message_text.assert_called_once()
        telegram.send_message.assert_not_called()

    def test_a_press_on_one_that_is_gone_says_so(self, processor, storage, telegram):
        """Deleted from another device, or from a list left open."""
        favourite = save_one(processor, storage)
        storage.delete_favourite(CHAT_ID, favourite.fav_id)
        telegram.reset_mock()

        processor.process(press(f"{keyboards.STEP_FAV}:{keyboards.FAV_PICK}{favourite.fav_id}"))

        assert any("이미 지워진" in text for text in texts(telegram))


class TestDeleting:
    """Confirmed, because rebuilding one means answering nine questions."""

    def test_the_delete_button_asks_first(self, processor, storage, telegram):
        favourite = save_one(processor, storage)
        telegram.reset_mock()

        processor.process(press(f"{keyboards.STEP_FAV}:{keyboards.FAV_DELETE}{favourite.fav_id}"))

        assert storage.get_favourite(CHAT_ID, favourite.fav_id) is not None
        assert any("지울까요" in text for text in texts(telegram))

    def test_confirming_deletes_it(self, processor, storage, telegram):
        favourite = save_one(processor, storage)
        telegram.reset_mock()

        processor.process(
            press(f"{keyboards.STEP_FAV}:{keyboards.FAV_CONFIRM_DELETE}{favourite.fav_id}")
        )

        assert storage.get_favourites(CHAT_ID) == []
        assert any("지웠습니다" in text for text in texts(telegram))

    def test_the_other_favourites_are_left_alone(self, processor, storage):
        first = save_one(processor, storage)
        second = save_one(processor, storage)

        processor.process(
            press(f"{keyboards.STEP_FAV}:{keyboards.FAV_CONFIRM_DELETE}{first.fav_id}")
        )

        assert [f.fav_id for f in storage.get_favourites(CHAT_ID)] == [second.fav_id]


class TestRenaming:
    """The one part of /fav that needs a typed answer."""

    def test_the_next_message_becomes_the_name(self, processor, storage, telegram):
        favourite = save_one(processor, storage)
        processor.process(press(f"{keyboards.STEP_FAV}:{keyboards.FAV_RENAME}{favourite.fav_id}"))
        telegram.reset_mock()

        processor.process(typed("주말 부산행"))

        assert storage.get_favourite(CHAT_ID, favourite.fav_id).name == "주말 부산행"
        assert any("바꿨습니다" in text for text in texts(telegram))

    def test_it_stops_listening_afterwards(self, processor, storage):
        """Otherwise the message after that becomes a name too."""
        favourite = save_one(processor, storage)
        processor.process(press(f"{keyboards.STEP_FAV}:{keyboards.FAV_RENAME}{favourite.fav_id}"))
        processor.process(typed("주말 부산행"))

        processor.process(typed("아무 말"))

        assert storage.get_favourite(CHAT_ID, favourite.fav_id).name == "주말 부산행"

    def test_cancel_is_the_way_out(self, processor, storage):
        """
        And the only one: anything else typed while waiting would be taken
        as the name.
        """
        favourite = save_one(processor, storage)
        processor.process(press(f"{keyboards.STEP_FAV}:{keyboards.FAV_RENAME}{favourite.fav_id}"))

        processor.process(typed("/cancel"))

        assert storage.get_pending_favourite_rename(CHAT_ID) is None
        assert storage.get_favourite(CHAT_ID, favourite.fav_id).name == "서울 → 부산"

    def test_an_overlong_name_is_cut_rather_than_refused(self, processor, storage):
        """It goes on a button, and a label that wraps is not a label."""
        from korail_bot.models.favourite import MAX_NAME_LENGTH

        favourite = save_one(processor, storage)
        processor.process(press(f"{keyboards.STEP_FAV}:{keyboards.FAV_RENAME}{favourite.fav_id}"))

        processor.process(typed("가" * 200))

        assert len(storage.get_favourite(CHAT_ID, favourite.fav_id).name) == MAX_NAME_LENGTH

    def test_a_booking_in_progress_is_not_disturbed(self, processor, storage):
        """
        Renaming is not a step of the booking flow, which is why the pending
        rename is held nowhere near the session.
        """
        favourite = save_one(processor, storage)
        processor.process(press(f"{keyboards.STEP_FAV}:{keyboards.FAV_RENAME}{favourite.fav_id}"))

        processor.process(typed("주말 부산행"))

        assert storage.get_user_session(CHAT_ID).last_action == (
            UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        )


class TestStartingFromOne:
    """The point of the whole thing."""

    def register(self, storage):
        storage.save_onboarded_account(
            OnboardedAccount(chat_id=CHAT_ID, korail_id="010-1111-2222", korail_pw="pw")
        )

    def start(self, processor, storage, favourite):
        with patch.object(KorailService, "login", return_value=True):
            processor.process(
                press(f"{keyboards.STEP_FAV}:{keyboards.FAV_START}{favourite.fav_id}")
            )
        return storage.get_user_session(CHAT_ID)

    def test_the_answers_are_loaded_and_only_the_date_is_asked(self, processor, storage, telegram):
        favourite = save_one(processor, storage)
        self.register(storage)
        telegram.reset_mock()

        session = self.start(processor, storage, favourite)

        assert session.last_action == UserProgress.PW_INPUT_SUCCESS
        assert session.train_info["srcLocate"] == "서울"
        assert session.train_info["passengerCount"] == 2
        assert "출발 희망일" in "\n".join(texts(telegram))

    def test_the_date_is_the_last_question_not_the_first(self, processor, storage, telegram):
        """
        The eight questions after it have been answered, and asking them
        anyway would make the shortcut no shortcut at all.
        """
        favourite = save_one(processor, storage)
        self.register(storage)
        self.start(processor, storage, favourite)
        telegram.reset_mock()

        date = a_bookable_date()
        with (
            patch.object(KorailService, "login", return_value=True),
            patch.object(KorailService, "search_trains", return_value=[]),
        ):
            processor.process(typed(date))

        session = storage.get_user_session(CHAT_ID)
        assert session.train_info["depDate"] == date
        # An empty timetable carries straight on to the summary, which is the
        # step after picking trains - so the eight questions were skipped.
        assert session.last_action == UserProgress.TRAIN_SELECT_INPUT_SUCCESS
        assert any("예약 정보 확인" in text for text in texts(telegram))

    def test_a_chat_with_no_registered_account_is_told_plainly(self, processor, storage, telegram):
        """
        Not dropped into the registration flow: they asked to run a saved
        search, and a phone number prompt reads as the favourite having failed.
        """
        favourite = save_one(processor, storage)
        telegram.reset_mock()

        self.start(processor, storage, favourite)

        assert any("등록되어 있지 않습니다" in text for text in texts(telegram))

    def test_a_search_already_running_is_not_replaced(self, processor, storage, telegram):
        favourite = save_one(processor, storage)
        self.register(storage)
        session = storage.get_user_session(CHAT_ID)
        session.last_action = UserProgress.FINDING_TICKET
        storage.save_user_session(session)
        telegram.reset_mock()

        self.start(processor, storage, favourite)

        assert storage.get_user_session(CHAT_ID).last_action == UserProgress.FINDING_TICKET
        assert any("이미 예약이 진행 중" in text for text in texts(telegram))


class TestLeavingTheBot:
    """What a user takes with them when they go."""

    def test_blocking_the_bot_drops_the_saved_searches(self, processor, storage):
        """
        Nothing secret in them - two stations and a time window - but they are
        a record of where someone travels, and they did not ask for it to be
        kept after they left.
        """
        save_one(processor, storage)

        processor.process(
            {
                "update_id": 9,
                "my_chat_member": {
                    "chat": {"id": CHAT_ID},
                    "new_chat_member": {"status": "kicked"},
                },
            }
        )

        assert storage.get_favourites(CHAT_ID) == []
