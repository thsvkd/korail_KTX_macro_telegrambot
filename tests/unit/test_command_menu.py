"""
Who sees which commands.

There used to be one list for everyone, which forced a choice: put the
operator's tools in it and advertise /flushredis to every user, or leave them
out and have the operator type from memory. It left them out, so the person
who runs the bot was the one person its menu did not serve.

Telegram will scope a list to a single chat, so both can be true. The
constraint that makes this delicate is that a chat-scoped list *replaces* the
default rather than adding to it - a developer chat that got only the admin
commands would lose /start.
"""

from unittest.mock import Mock

import pytest

from korail_bot.handlers.command_handler import CommandHandler
from korail_bot.services import PaymentReminderService, ReservationService, TelegramService
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot.messages import Messages

CHAT_ID = 4242


def names(commands):
    return [entry["command"] for entry in commands]


class TestTheTwoLists:
    """What each list has to contain."""

    def test_the_public_list_hides_the_operators_tools(self):
        """
        The default list goes to everyone. /flushredis in it would tell every
        user which door wipes the server.
        """
        assert not set(names(Messages.PUBLIC_COMMANDS)) & set(names(Messages.ADMIN_COMMANDS))

    def test_the_developer_list_carries_the_public_commands_too(self):
        """
        Telegram shows the narrowest matching list instead of merging them,
        so an admin-only list would cost the operator /start.
        """
        assert set(names(Messages.PUBLIC_COMMANDS)) <= set(names(Messages.DEVELOPER_COMMANDS))
        assert set(names(Messages.ADMIN_COMMANDS)) <= set(names(Messages.DEVELOPER_COMMANDS))

    def test_no_command_is_listed_twice(self):
        """A duplicate would show up twice in the menu."""
        listed = names(Messages.DEVELOPER_COMMANDS)

        assert len(listed) == len(set(listed))

    @pytest.mark.parametrize(
        "commands", [Messages.PUBLIC_COMMANDS, Messages.ADMIN_COMMANDS], ids=["public", "admin"]
    )
    def test_every_entry_is_a_command_the_bot_answers(self, commands):
        """
        A menu entry that routes to "알 수 없는 명령어" is worse than no
        entry: the user picked it from a list the bot published.
        """
        handler_source = _route_command_source()
        for name in names(commands):
            assert f'"/{name}"' in handler_source, f"/{name} 는 라우팅되지 않습니다"

    @pytest.mark.parametrize(
        "commands", [Messages.PUBLIC_COMMANDS, Messages.ADMIN_COMMANDS], ids=["public", "admin"]
    )
    def test_every_entry_has_a_description(self, commands):
        for entry in commands:
            assert entry["description"].strip()

    @pytest.mark.parametrize(
        "commands", [Messages.PUBLIC_COMMANDS, Messages.ADMIN_COMMANDS], ids=["public", "admin"]
    )
    def test_telegram_would_accept_the_names(self, commands):
        """Lower-case, digits and underscores, 1-32 characters."""
        for name in names(commands):
            assert 1 <= len(name) <= 32
            assert name.replace("_", "").isalnum()
            assert name.islower() or name.isdigit()


def _route_command_source() -> str:
    """The body of route_command, for checking that a menu entry goes somewhere."""
    import inspect

    return inspect.getsource(CommandHandler.route_command)


class MenuFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.is_developer.return_value = False
        self.storage.is_admin_authenticated.return_value = False
        self.telegram = Mock(spec=TelegramService)
        self.handler = CommandHandler(
            self.storage,
            self.telegram,
            Mock(spec=ReservationService),
            Mock(spec=PaymentReminderService),
        )

    def help_text(self):
        self.handler.handle_help(CHAT_ID)
        return self.telegram.send_message.call_args.args[1]


class TestPublishingTheMenu(MenuFixture):
    """Which list lands on a chat, and when."""

    def test_a_developer_chat_gets_its_own_list(self):
        self.storage.is_developer.return_value = True

        self.handler.publish_command_menu(CHAT_ID)

        self.telegram.set_my_commands.assert_called_once_with(
            Messages.DEVELOPER_COMMANDS, chat_id=CHAT_ID
        )

    def test_an_ordinary_chat_has_its_list_removed_rather_than_replaced(self):
        """
        Falling back to the default is the point. Publishing the public list
        per chat would work today and rot the day the default one changes.
        """
        self.handler.publish_command_menu(CHAT_ID)

        self.telegram.delete_my_commands.assert_called_once_with(CHAT_ID)
        self.telegram.set_my_commands.assert_not_called()

    def test_a_password_session_does_not_earn_the_menu(self):
        """
        It expires on its own, and a menu that quietly went stale would offer
        /flushredis to a chat that would then be asked to authenticate.
        """
        self.storage.is_admin_authenticated.return_value = True

        self.handler.publish_command_menu(CHAT_ID)

        self.telegram.set_my_commands.assert_not_called()

    def test_telegram_being_unreachable_is_not_fatal(self):
        """
        This runs inside /devoff. A failed menu update must not make giving up
        developer mode look like it failed.
        """
        self.storage.is_developer.return_value = True
        self.telegram.set_my_commands.side_effect = Exception("telegram is down")

        self.handler.publish_command_menu(CHAT_ID)  # must not raise

    def test_giving_up_developer_mode_takes_the_menu_with_it(self):
        """Otherwise the operator's menu outlives the mode it belonged to."""
        self.storage.is_developer.side_effect = [True, False]

        self.handler.handle_devoff(CHAT_ID)

        self.telegram.delete_my_commands.assert_called_once_with(CHAT_ID)


class TestHelp(MenuFixture):
    """The same split, in the text a user can ask for."""

    def test_an_ordinary_chat_sees_only_what_it_can_use(self):
        text = self.help_text()

        assert "/start" in text
        assert "/flushredis" not in text

    def test_a_developer_chat_sees_the_operators_tools(self):
        self.storage.is_developer.return_value = True

        text = self.help_text()

        assert "/start" in text
        assert "/flushredis" in text

    def test_a_password_session_sees_them_too(self):
        """
        Unlike the menu: this is asked for in the moment, and the moment is
        one where those commands really do work.
        """
        self.storage.is_admin_authenticated.return_value = True

        assert "/approve" in self.help_text()

    def test_the_new_commands_are_in_it(self):
        """A command with no menu entry and no help line is undiscoverable."""
        text = self.help_text()

        assert "/fav" in text
        assert "/notify" in text
