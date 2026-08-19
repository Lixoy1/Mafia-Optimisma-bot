import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace

from mafia_optimisma.config import Settings
from mafia_optimisma.engine import GameEngine
from mafia_optimisma.models import GameState, Phase, PlayerState
from mafia_optimisma.state import store
from mafia_optimisma import routers_group


class TinyStorage:
    def __init__(self):
        self.states = {}

    async def save_game_state(self, game):
        self.states[game.chat_id] = game.to_dict()

    async def get_notify_users(self, chat_id):
        return []

    async def ensure_profile(self, user_id, name, username):
        return {"user_id": user_id, "name": name, "username": username}


class Sent:
    def __init__(self, message_id, chat_id, text, reply_markup=None):
        self.message_id = message_id
        self.chat_id = chat_id
        self.text = text
        self.reply_markup = reply_markup


class FakeBot:
    def __init__(self, admin_ids=()):
        self.admin_ids = set(admin_ids)
        self.messages = []
        self._id = 100

    async def send_message(self, chat_id, text, **kwargs):
        self._id += 1
        msg = Sent(self._id, chat_id, text, kwargs.get("reply_markup"))
        self.messages.append(msg)
        return msg

    async def send_sticker(self, *args, **kwargs):
        return None

    async def send_chat_action(self, *args, **kwargs):
        return None

    async def get_me(self):
        return SimpleNamespace(username="test_bot", first_name="Test Bot")

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="administrator" if user_id in self.admin_ids else "member")


class FakeMessage:
    def __init__(self, bot, chat_id, user_id, text="hello"):
        self.bot = bot
        self.chat = SimpleNamespace(id=chat_id, type="supergroup")
        self.from_user = SimpleNamespace(id=user_id, is_bot=False, full_name=f"U{user_id}", username=None)
        self.text = text
        self.deleted = False

    async def delete(self):
        self.deleted = True


class LiveGameRulesHotfixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store.games.clear()
        store.user_to_chat.clear()
        self.storage = TinyStorage()
        self.engine = GameEngine(
            Settings(
                "x", registration_seconds=100, registration_warning_seconds=30,
                night_seconds=100, discussion_seconds=100,
                nomination_seconds=100, verdict_seconds=100,
            ),
            self.storage,
        )
        routers_group.engine = self.engine

    async def asyncTearDown(self):
        for task in list(self.engine.tasks.values()):
            if not task.done():
                task.cancel()

    async def test_spectator_message_is_deleted_before_handler(self):
        game = GameState(9101, "guard", mode="classic", phase=Phase.DISCUSSION)
        game.players[1] = PlayerState(1, "Player", number=1, role_key="optimist", initial_role_key="optimist")
        store.games[game.chat_id] = game
        bot = FakeBot()
        msg = FakeMessage(bot, game.chat_id, 99, "я зритель")
        called = False

        async def handler(event, data):
            nonlocal called
            called = True

        await routers_group.LiveGameChatGuard()(handler, msg, {})
        self.assertTrue(msg.deleted)
        self.assertFalse(called)

    async def test_living_player_message_passes(self):
        game = GameState(9102, "guard", mode="classic", phase=Phase.DISCUSSION)
        game.players[1] = PlayerState(1, "Player", number=1, role_key="optimist", initial_role_key="optimist")
        store.games[game.chat_id] = game
        bot = FakeBot()
        msg = FakeMessage(bot, game.chat_id, 1, "город, обсуждаем")
        called = False

        async def handler(event, data):
            nonlocal called
            called = True
            return "ok"

        result = await routers_group.LiveGameChatGuard()(handler, msg, {})
        self.assertFalse(msg.deleted)
        self.assertTrue(called)
        self.assertEqual(result, "ok")

    async def test_spectator_admin_can_use_settings_control(self):
        game = GameState(9103, "guard", mode="classic", phase=Phase.DISCUSSION)
        game.players[1] = PlayerState(1, "Player", number=1, role_key="optimist", initial_role_key="optimist")
        store.games[game.chat_id] = game
        bot = FakeBot(admin_ids={50})
        msg = FakeMessage(bot, game.chat_id, 50, "/settings")
        called = False

        async def handler(event, data):
            nonlocal called
            called = True

        await routers_group.LiveGameChatGuard()(handler, msg, {})
        self.assertFalse(msg.deleted)
        self.assertTrue(called)

    async def test_optimist_gets_role_reminder_again_on_second_night(self):
        game = GameState(9104, "night", mode="classic", phase=Phase.RESOLVING)
        game.players[1] = PlayerState(
            1, "Player", number=1, role_key="optimist", initial_role_key="optimist"
        )
        store.games[game.chat_id] = game
        store.remember_user(1, game.chat_id)
        bot = FakeBot()

        await self.engine.start_night(bot, game, allow_from_resolving=True)
        self.engine.cancel_timer(game.chat_id)
        game.phase = Phase.RESOLVING
        await self.engine.start_night(bot, game, allow_from_resolving=True)
        self.engine.cancel_timer(game.chat_id)

        private = [m.text for m in bot.messages if m.chat_id == 1]
        self.assertGreaterEqual(len(private), 2)
        self.assertIn("Ночной цикл №2", private[-1])
        self.assertIn("Оптимист", private[-1])
        self.assertTrue("сп" in private[-1].lower() or "действ" in private[-1].lower())

    async def test_settings_are_not_in_public_group_command_scope(self):
        source = Path("mafia_optimisma/main.py").read_text(encoding="utf-8")
        group_start = source.index("    group_commands = [")
        admin_start = source.index("    admin_commands = group_commands + [")
        group_block = source[group_start:admin_start]
        admin_block = source[admin_start:]
        self.assertNotIn('command="settings"', group_block)
        self.assertIn('command="settings"', admin_block)
        self.assertIn("BotCommandScopeAllChatAdministrators", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
