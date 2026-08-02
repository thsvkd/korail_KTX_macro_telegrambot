"""
Picking a booking back up where it was left.

The flow asks a dozen questions, and people wander off in the middle of one -
a call, a stop, a phone put down. Every answer is on the session and lasts a
day, but /start used to write over the lot without a word, so coming back
meant answering all of them again.

Nothing new is stored for this. What changed is that /start asks.
"""

from unittest.mock import Mock, patch

import pytest

from korail_bot.handlers.command_handler import CommandHandler
from korail_bot.handlers.conversation_handler import ConversationHandler
from korail_bot.models import OnboardedAccount, UserCredentials, UserProgress, UserSession
from korail_bot.services import PaymentReminderService, ReservationService, TelegramService
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot import keyboards

CHAT_ID = 909090
PHONE = "010-1234-5678"
PASSWORD = "korail-password"


def draft_at(progress: int, **train_info) -> UserSession:
    """A session parked on one question, with the answers before it filled in."""
    session = UserSession(chat_id=CHAT_ID, in_progress=True, last_action=progress)
    session.credentials = UserCredentials(korail_id=PHONE, korail_pw=PASSWORD)
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
        "passengerCount": 2,
        "seatStrategy": "consecutive",
        "seatStrategyShow": "연속 좌석",
        **train_info,
    }
    return session


class DraftFixture:
    """A /start and the conversation behind it, over one stored session."""

    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.is_developer.return_value = False
        self.storage.get_onboarded_account.return_value = None
        self.telegram = Mock(spec=TelegramService)
        self.conversation = ConversationHandler(
            self.storage, self.telegram, Mock(spec=ReservationService)
        )
        self.commands = CommandHandler(
            self.storage,
            self.telegram,
            Mock(spec=ReservationService),
            Mock(spec=PaymentReminderService),
            conversation_handler=self.conversation,
        )

    def at(self, progress, **train_info):
        self.session = draft_at(progress, **train_info)
        self.storage.get_user_session.return_value = self.session
        return self.session

    def start(self):
        self.commands.handle_start(CHAT_ID)

    def answer(self, text):
        self.conversation.handle_message(CHAT_ID, text)

    def texts(self):
        return [call.args[1] for call in self.telegram.send_message.call_args_list]

    def last_text(self):
        return self.telegram.send_message.call_args.args[1]

    def last_keyboard(self):
        return self.telegram.send_message.call_args.kwargs.get("reply_markup")

    def register(self):
        """An account on file, and a login that works."""
        self.storage.get_onboarded_account.return_value = OnboardedAccount(
            chat_id=CHAT_ID, korail_id=PHONE, korail_pw=PASSWORD
        )
        rail = Mock()
        rail.login.return_value = True
        return patch("korail_bot.handlers.conversation_handler.KorailService", return_value=rail)


class TestStartAsksBeforeDiscarding(DraftFixture):
    """The whole point: a dozen answers are not thrown away in silence."""

    def test_a_half_finished_booking_is_offered_back(self):
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)

        self.start()

        assert self.session.last_action == UserProgress.RESUME_DRAFT_PENDING
        assert "진행하시던 예약" in self.last_text()

    def test_the_offer_shows_what_was_already_answered(self):
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)

        self.start()

        offer = self.last_text()
        assert "서울" in offer
        assert "부산" in offer
        assert "20991231" in offer

    def test_the_offer_names_the_question_it_stopped_at(self):
        self.at(UserProgress.MAX_DEP_TIME_INPUT_SUCCESS)

        self.start()

        assert "열차 종류" in self.last_text()

    def test_the_answers_are_left_alone_while_the_question_is_open(self):
        """Nothing is committed until the user says which way to go."""
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)

        self.start()

        assert self.session.train_info["srcLocate"] == "서울"

    def test_asking_twice_does_not_undo_the_asking(self):
        """/start pressed again must not destroy what /start offered to keep."""
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)
        self.start()

        self.start()

        assert self.session.last_action == UserProgress.RESUME_DRAFT_PENDING
        assert self.session.train_info["srcLocate"] == "서울"

        self.answer("Y")
        assert self.session.last_action == UserProgress.DEP_TIME_INPUT_SUCCESS

    @pytest.mark.parametrize(
        "progress",
        [
            UserProgress.DATE_INPUT_SUCCESS,
            UserProgress.SRC_LOCATE_INPUT_SUCCESS,
            UserProgress.DST_LOCATE_INPUT_SUCCESS,
            UserProgress.DEP_TIME_INPUT_SUCCESS,
            UserProgress.MAX_DEP_TIME_INPUT_SUCCESS,
            UserProgress.TRAIN_TYPE_INPUT_SUCCESS,
            UserProgress.SPECIAL_INPUT_SUCCESS,
            UserProgress.PASSENGER_COUNT_INPUT_SUCCESS,
            UserProgress.SEAT_STRATEGY_INPUT_SUCCESS,
            UserProgress.TRAIN_SELECT_INPUT_SUCCESS,
            UserProgress.SCHEDULE_INPUT_PENDING,
        ],
    )
    def test_every_answered_step_is_worth_offering(self, progress):
        self.at(progress)

        self.start()

        assert self.session.last_action == UserProgress.RESUME_DRAFT_PENDING


class TestWhatIsNotADraft(DraftFixture):
    """An offer to resume something that holds no answers is a question about nothing."""

    def test_a_session_that_has_only_logged_in_is_not_offered(self):
        """The resume would land on the very question /start asks anyway."""
        session = self.at(UserProgress.PW_INPUT_SUCCESS)
        session.train_info = {}

        self.start()

        assert self.session.last_action != UserProgress.RESUME_DRAFT_PENDING

    def test_a_state_without_a_date_is_not_offered(self):
        """A progress state says which question is up, not that it was answered."""
        session = self.at(UserProgress.DATE_INPUT_SUCCESS)
        session.train_info.pop("depDate")

        self.start()

        assert self.session.last_action != UserProgress.RESUME_DRAFT_PENDING

    def test_a_running_search_is_not_a_draft(self):
        """It is not half-finished; it is happening."""
        self.at(UserProgress.FINDING_TICKET)

        self.start()

        assert self.session.last_action != UserProgress.RESUME_DRAFT_PENDING

    def test_a_registration_in_progress_is_not_a_draft(self):
        self.at(UserProgress.ONBOARDING_OVERWRITE_PENDING)

        self.start()

        assert self.session.last_action != UserProgress.RESUME_DRAFT_PENDING


class TestCarryingOn(DraftFixture):
    """Yes: back to the question it stopped at, with the answers intact."""

    def test_the_session_lands_back_on_its_own_question(self):
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)
        self.start()

        self.answer("Y")

        assert self.session.last_action == UserProgress.DEP_TIME_INPUT_SUCCESS

    def test_the_question_it_stopped_at_is_asked_again(self):
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)
        self.start()

        self.answer("Y")

        assert "검색 종료 시각" in self.last_text()

    def test_it_says_why_the_question_is_back(self):
        """Not "이전 단계로 돌아왔습니다" - nobody went back anywhere."""
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)
        self.start()

        self.answer("Y")

        assert "이어갑니다" in self.last_text()

    def test_the_answers_survive(self):
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)
        self.start()

        self.answer("Y")

        assert self.session.train_info["srcLocate"] == "서울"
        assert self.session.train_info["depDate"] == "20991231"

    def test_the_parked_question_is_not_left_behind_in_the_answers(self):
        """train_info is read back as the search's own parameters."""
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)
        self.start()

        self.answer("Y")

        assert self.conversation.DRAFT_PROGRESS_KEY not in self.session.train_info

    def test_the_summary_is_redrawn_when_that_is_where_it_stopped(self):
        self.at(UserProgress.TRAIN_SELECT_INPUT_SUCCESS)
        self.start()

        self.answer("Y")

        assert "이어갑니다" in self.last_text()
        assert self.last_keyboard() == keyboards.confirm_keyboard()

    def test_an_unreadable_answer_leaves_the_question_up(self):
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)
        self.start()

        self.answer("어쩌라고")

        assert self.session.last_action == UserProgress.RESUME_DRAFT_PENDING
        assert self.last_keyboard() == keyboards.resume_draft_keyboard()


class TestStartingOver(DraftFixture):
    """No: and then the answers really are gone."""

    def test_the_flow_starts_from_the_top(self):
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)
        self.start()

        self.answer("N")

        assert self.session.last_action == UserProgress.STARTED

    def test_the_old_answers_are_dropped(self):
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)
        self.start()

        self.answer("N")

        assert self.session.train_info == {}

    def test_a_favourite_shortcut_does_not_survive_into_the_new_booking(self):
        """
        The flag says "every question but the date is answered". Left behind,
        it would skip nine questions of a booking that answered none of them.
        """
        self.at(UserProgress.DATE_INPUT_SUCCESS, fromFavourite=True)
        self.start()

        self.answer("N")

        assert "fromFavourite" not in self.session.train_info


class TestTheLoginBehindAResumedDraft(DraftFixture):
    """A draft carried to the summary without a password fails at the last step."""

    def test_a_lost_password_is_fetched_from_the_registration(self):
        session = self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)
        self.start()
        session.credentials.korail_pw = ""

        with self.register():
            self.answer("Y")

        assert self.session.credentials.korail_pw == PASSWORD
        assert self.session.last_action == UserProgress.DEP_TIME_INPUT_SUCCESS

    def test_nothing_to_log_in_with_means_starting_over(self):
        session = self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)
        self.start()
        session.credentials.korail_pw = ""

        self.answer("Y")

        assert "로그인 정보가 없어" in " ".join(self.texts())
        assert self.session.last_action == UserProgress.STARTED

    def test_a_password_already_on_the_session_costs_no_login(self):
        """The common case: nothing was lost, so nothing is asked of Korail."""
        self.at(UserProgress.DEP_TIME_INPUT_SUCCESS)
        self.start()

        with patch("korail_bot.handlers.conversation_handler.KorailService") as korail:
            self.answer("Y")

        korail.assert_not_called()


class TestTheButtonIsTiedToItsQuestion:
    """Buttons stay pressable in the history forever; this one is no exception."""

    def test_the_resume_step_expects_the_resume_question(self):
        assert keyboards.STEP_PROGRESS[keyboards.STEP_RESUME] == UserProgress.RESUME_DRAFT_PENDING

    def test_both_answers_are_the_ones_the_flow_reads(self):
        buttons = [
            button["callback_data"]
            for row in keyboards.resume_draft_keyboard()["inline_keyboard"]
            for button in row
        ]
        assert buttons == [f"{keyboards.STEP_RESUME}:Y", f"{keyboards.STEP_RESUME}:N"]
