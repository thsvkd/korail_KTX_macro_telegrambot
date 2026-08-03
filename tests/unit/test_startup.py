"""
What the app does once, on its way up.

app.py is a module that runs top to bottom when it is imported - which is what
a WSGI entry point has to be, and what makes it the one file no test executes.
So the parts of it with a decision in them were shipped on the strength of a
log line read after a deploy, which catches the deploy that just happened and
nothing else.

The menus are the case that matters. They are published before the bot is
doing anything for anyone, from a chat list that comes out of Redis, and every
way this can go wrong is quiet: the wrong list on the wrong chat, or a start
that fails over a menu.
"""

from unittest.mock import Mock, patch

from korail_bot.config.settings import settings
from korail_bot.services.telegram_service import TelegramService
from korail_bot.startup import publish_command_menus
from korail_bot.storage.base import StorageInterface
from korail_bot.telegramBot.messages import Messages

OPERATOR = 6824596577
ANOTHER_OPERATOR = 8570272839


class MenuFixture:
    def setup_method(self):
        self.storage = Mock(spec=StorageInterface)
        self.storage.get_all_developers.return_value = []
        self.telegram = Mock(spec=TelegramService)
        self.telegram.set_my_commands.return_value = True
        self.telegram.set_chat_menu_button.return_value = True
        self.telegram.reset_chat_menu_button.return_value = True
        self.telegram.get_bot_username.return_value = "rail_bot"

    def publish(self):
        return publish_command_menus(self.storage, self.telegram)

    def published(self):
        """Every list that went out, as (commands, chat_id)."""
        return [
            (call.args[0], call.kwargs.get("chat_id"))
            for call in self.telegram.set_my_commands.call_args_list
        ]


class TestPublishingTheMenus(MenuFixture):
    """Two lists, because Telegram shows the narrowest one that matches."""

    def test_everyone_gets_the_public_list(self):
        self.publish()

        assert (Messages.PUBLIC_COMMANDS, None) in self.published()

    def test_a_developer_chat_gets_the_operators_list_scoped_to_itself(self):
        self.storage.get_all_developers.return_value = [OPERATOR]

        assert self.publish() == 1
        assert (Messages.DEVELOPER_COMMANDS, OPERATOR) in self.published()

    def test_every_developer_chat_is_covered(self):
        self.storage.get_all_developers.return_value = [OPERATOR, ANOTHER_OPERATOR]

        assert self.publish() == 2

    def test_the_lists_are_republished_on_every_start(self):
        """
        Not only when developer mode is claimed. The list grows with the bot,
        and a chat that claimed the mode two releases ago would otherwise be
        left holding the menu of that release.
        """
        self.storage.get_all_developers.return_value = [OPERATOR]

        self.publish()
        self.publish()

        assert self.telegram.set_my_commands.call_count == 4

    def test_no_developers_leaves_only_the_public_list(self):
        assert self.publish() == 0
        assert self.published() == [(Messages.PUBLIC_COMMANDS, None)]

    def test_enabled_mini_app_replaces_the_default_chat_menu_with_an_app_button(self):
        with (
            patch.object(settings, "MINI_APP_URL", "https://example.test/app?source=bot"),
            patch.object(settings, "mini_app_enabled", return_value=True),
        ):
            self.publish()

        self.telegram.set_chat_menu_button.assert_called_once_with(
            "예약 열기",
            "https://example.test/app?source=bot&transport=start&bot=rail_bot",
        )
        self.telegram.reset_chat_menu_button.assert_not_called()

    def test_disabled_mini_app_restores_the_default_command_menu_button(self):
        with patch.object(settings, "mini_app_enabled", return_value=False):
            self.publish()

        self.telegram.reset_chat_menu_button.assert_called_once_with()
        self.telegram.set_chat_menu_button.assert_not_called()


class TestNothingHereIsWorthFailingAStartOver(MenuFixture):
    """
    All of this is a convenience arranged before the bot does any work.

    A start that fails on it costs the user every search the bot would have
    been running, to save them from typing a command they know.
    """

    def test_telegram_being_unreachable_does_not_stop_the_start(self):
        self.telegram.set_my_commands.return_value = False

        assert self.publish() == 0  # must not raise

    def test_a_refused_public_list_does_not_skip_the_operators(self):
        """
        They are separate calls to separate scopes, and the operator's menu
        is the one whose absence is noticed.
        """
        self.storage.get_all_developers.return_value = [OPERATOR]
        self.telegram.set_my_commands.side_effect = [False, True]

        assert self.publish() == 1

    def test_a_refused_operator_list_does_not_skip_the_next_operator(self):
        self.storage.get_all_developers.return_value = [OPERATOR, ANOTHER_OPERATOR]
        self.telegram.set_my_commands.side_effect = [True, False, True]

        assert self.publish() == 1
        assert (Messages.DEVELOPER_COMMANDS, ANOTHER_OPERATOR) in self.published()

    def test_redis_being_unreadable_still_leaves_everyone_a_menu(self):
        """
        The public list is already out by then, and it is the one that
        reaches every user of the bot.
        """
        self.storage.get_all_developers.side_effect = Exception("redis is down")

        assert self.publish() == 0
        assert self.published() == [(Messages.PUBLIC_COMMANDS, None)]
