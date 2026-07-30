"""
Integration tests for claiming a chat as the operator's.

The magic string exists so that whoever runs the bot can mark their own chat
and get the tools without typing a password every hour. It is typed as an
ordinary message, anywhere, which is convenient for the operator and is also
the reason it needs care: a correct guess is a standing grant.

Failed guesses cannot be counted here - every ordinary message that is not
the magic string would look like one - so the defences are length (warned
about at startup) and the fact that a successful claim is announced to the
operators who were already there.
"""

from unittest.mock import Mock, patch

import pytest

from korail_bot.config.settings import Settings, settings
from korail_bot.handlers.update_processor import TelegramUpdateProcessor
from korail_bot.services import PaymentReminderService, ReservationService, TelegramService
from korail_bot.storage import RedisStorage

MAGIC = "a-long-enough-magic-string"
CHAT_ID = 4242
OTHER_OPERATOR = 777


@pytest.fixture
def storage():
    storage = RedisStorage()
    storage.redis.flushdb()
    yield storage
    storage.redis.flushdb()


@pytest.fixture
def telegram():
    return Mock(spec=TelegramService)


@pytest.fixture
def processor(storage, telegram):
    return TelegramUpdateProcessor(
        storage, telegram, Mock(spec=ReservationService), Mock(spec=PaymentReminderService)
    )


def _message(text, chat_id=CHAT_ID):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


def _texts(telegram):
    return [
        call.args[1] if len(call.args) > 1 else call.kwargs.get("text", "")
        for call in telegram.send_message.call_args_list
    ]


def _magic(value=MAGIC):
    return patch.object(Settings, "ADMIN_MAGIC_STRING", value)


class TestClaimingAChat:
    """Typing the magic string."""

    def test_it_turns_the_chat_into_a_developer_chat(self, processor, storage):
        with _magic():
            processor.process(_message(MAGIC))

        assert storage.is_developer(CHAT_ID) is True

    def test_it_works_mid_conversation(self, processor, storage):
        """
        The point of "anywhere": an operator should not have to walk back to
        the welcome screen to claim a chat they are already using.
        """
        from korail_bot.models import UserProgress, UserSession

        storage.save_user_session(
            UserSession(
                chat_id=CHAT_ID,
                in_progress=True,
                last_action=UserProgress.DATE_INPUT_SUCCESS,
            )
        )

        with _magic():
            processor.process(_message(MAGIC))

        assert storage.is_developer(CHAT_ID) is True

    def test_the_message_does_not_reach_the_conversation(self, processor, storage):
        """It means 'this chat is mine', not an answer to the question on screen."""
        processor.conversation_handler = Mock()

        with _magic():
            processor.process(_message(MAGIC))

        processor.conversation_handler.handle_message.assert_not_called()

    def test_an_ordinary_message_is_unaffected(self, processor, storage):
        with _magic():
            processor.process(_message("서울"))

        assert storage.is_developer(CHAT_ID) is False

    def test_it_is_disabled_when_no_magic_string_is_configured(self, processor, storage):
        with patch.object(Settings, "ADMIN_MAGIC_STRING", None):
            processor.process(_message(MAGIC))
            processor.process(_message(""))

        assert storage.is_developer(CHAT_ID) is False

    def test_claiming_twice_says_so(self, processor, storage, telegram):
        with _magic():
            processor.process(_message(MAGIC))
            processor.process(_message(MAGIC))

        assert any("이미 개발자 모드" in text for text in _texts(telegram))

    def test_the_operators_already_here_are_told(self, processor, storage, telegram):
        """
        A successful guess cannot be prevented or counted, so it is made
        impossible to do quietly.
        """
        storage.set_developer(OTHER_OPERATOR, True)

        with _magic():
            processor.process(_message(MAGIC))

        recipients = [call.args[0] for call in telegram.send_to_multiple.call_args_list]
        assert [OTHER_OPERATOR] in recipients

    def test_the_first_operator_has_nobody_to_tell(self, processor, telegram):
        with _magic():
            processor.process(_message(MAGIC))

        telegram.send_to_multiple.assert_not_called()


class TestGivingItUp:
    """/devoff."""

    def test_it_takes_developer_mode_away(self, processor, storage):
        storage.set_developer(CHAT_ID, True)

        processor.process(_message("/devoff"))

        assert storage.is_developer(CHAT_ID) is False

    def test_it_says_so_when_the_chat_was_not_a_developer(self, processor, storage, telegram):
        processor.process(_message("/devoff"))

        assert any("개발자 모드가 아닙니다" in text for text in _texts(telegram))


class TestWhatDeveloperModeBuys:
    """The tools, without a password."""

    def test_admin_commands_skip_the_password(self, processor, storage, telegram):
        storage.set_developer(CHAT_ID, True)

        with patch.object(settings, "ADMIN_PASSWORD", "some-admin-password"):
            processor.process(_message("/approve"))

        # Answered rather than challenged.
        assert not any("비밀번호" in text for text in _texts(telegram))
        assert any("승인" in text for text in _texts(telegram))

    def test_an_ordinary_chat_is_still_challenged(self, processor, storage, telegram):
        with patch.object(settings, "ADMIN_PASSWORD", "some-admin-password"):
            processor.process(_message("/approve"))

        assert any("비밀번호" in text for text in _texts(telegram))


class TestTheStartupWarning:
    """A short secret is the whole attack surface here."""

    def test_a_short_magic_string_is_warned_about(self):
        with patch.object(Settings, "ADMIN_MAGIC_STRING", "hunter2"):
            assert any("ADMIN_MAGIC_STRING" in w for w in settings.warnings())

    def test_a_long_one_is_not(self):
        with patch.object(Settings, "ADMIN_MAGIC_STRING", MAGIC):
            assert not any("ADMIN_MAGIC_STRING" in w for w in settings.warnings())

    def test_no_magic_string_is_not_warned_about(self):
        with patch.object(Settings, "ADMIN_MAGIC_STRING", None):
            assert not any("ADMIN_MAGIC_STRING" in w for w in settings.warnings())
