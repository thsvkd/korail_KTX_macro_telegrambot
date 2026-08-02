"""
What the bot says "모든 열차" means.

The name reads like the more generous of the two options, and in one sense it
is: more trains means more chances at a seat. But the search takes the first
seat it finds, so on a corridor where a 무궁화호 runs alongside the KTX it can
book two extra hours of travel for someone who chose it expecting KTX.

That is a different failure from not getting a ticket, and the user can only
avoid it before they press the button - which is why the explanation belongs
on the question rather than in the README.
"""

import pytest

from korail_bot.handlers import ConversationHandler
from korail_bot.telegramBot import keyboards
from korail_bot.telegramBot.messages import Messages

#: The trains "모든 열차" drags in beyond the KTX family. Anyone picking it
#: expecting a fast train should have seen these named first.
SLOWER_TRAINS = ["무궁화호", "새마을", "누리로"]


class TestTheQuestionExplainsItself:
    """The prompt shown when the choice is made."""

    @pytest.mark.parametrize("train", SLOWER_TRAINS)
    def test_the_slower_trains_are_named(self, train):
        assert train in Messages.REQUEST_TRAIN_TYPE

    @pytest.mark.parametrize("train", ["KTX-산천", "KTX-이음"])
    def test_the_ktx_family_is_named_too(self, train):
        """
        "KTX만" hides KTX-산천 and KTX-이음, which are the trains most of the
        seats on that option actually are.
        """
        assert train in Messages.REQUEST_TRAIN_TYPE

    def test_the_catch_is_stated_not_implied(self):
        """
        The one thing a user cannot work out for themselves: the search takes
        whatever comes free first, so "모든 열차" is a decision about what
        they are willing to travel on, not only about odds.
        """
        assert "먼저" in Messages.REQUEST_TRAIN_TYPE

    def test_going_back_to_the_question_carries_the_warning_too(self):
        """
        Someone who came back here is reconsidering this exact choice. A
        shorter prompt is fine; a silent one is not.
        """
        assert "무궁화호" in Messages.ASK_AGAIN_TRAIN_TYPE


class TestTheButtonSaysItToo:
    """The label is what gets skimmed before the prompt gets read."""

    def label(self, value):
        return keyboards.button_label(
            keyboards.train_type_keyboard(), f"{keyboards.STEP_TRAIN_TYPE}:{value}"
        )

    def test_the_everything_button_names_what_everything_means(self):
        assert "무궁화호" in self.label("2")

    def test_the_ktx_button_does_not_claim_to_be_only_ktx(self):
        """It covers KTX-산천 and KTX-이음, so "KTX만" would be wrong."""
        assert self.label("1") == "🚅 KTX 계열만"


class TestTheSummarySaysWhatWasChosen:
    """
    What ends up on the confirmation screen and in /status.

    It used to read "ALL", which names a korail2 constant rather than a
    decision - and told a user about to start a search nothing about the
    무궁화호 they had just allowed it to book.
    """

    def choose(self, answer):
        from unittest.mock import Mock

        from korail_bot.models import UserProgress, UserSession
        from korail_bot.services import ReservationService, TelegramService
        from korail_bot.storage.base import StorageInterface

        storage = Mock(spec=StorageInterface)
        session = UserSession(
            chat_id=1, in_progress=True, last_action=UserProgress.MAX_DEP_TIME_INPUT_SUCCESS
        )
        storage.get_user_session.return_value = session

        handler = ConversationHandler(
            storage, Mock(spec=TelegramService), Mock(spec=ReservationService)
        )
        handler.handle_message(1, answer)
        return session.train_info

    def test_choosing_everything_says_so_in_words(self):
        info = self.choose("2")

        assert info["trainType"] == "TrainType.ALL"
        assert "무궁화호" in info["trainTypeShow"]

    def test_choosing_ktx_says_so_too(self):
        info = self.choose("1")

        assert info["trainType"] == "TrainType.KTX"
        assert "KTX" in info["trainTypeShow"]
