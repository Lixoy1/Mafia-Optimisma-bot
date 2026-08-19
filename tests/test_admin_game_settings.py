import asyncio
import tempfile
import unittest
from pathlib import Path

from mafia_optimisma.config import Settings
from mafia_optimisma.engine import GameEngine, generate_roles
from mafia_optimisma.models import GameState, NightAction, Phase, PlayerState
from mafia_optimisma.state import store
from mafia_optimisma.storage import Storage


class MemoryStorage:
    async def save_game_state(self, game):
        return None

    async def consume_item(self, user_id, item):
        return False

    async def get_notify_users(self, chat_id):
        return []


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return type("M", (), {"message_id": len(self.messages)})()


class AdminGameSettingsTests(unittest.TestCase):
    def setUp(self):
        store.games.clear()
        store.user_to_chat.clear()
        self.engine = GameEngine(Settings("x"), MemoryStorage())

    def tearDown(self):
        for task in list(self.engine.tasks.values()):
            if not task.done():
                task.cancel()

    def test_role_can_be_disabled_or_unlocked_earlier(self):
        disabled = generate_roles("chaos", 8, {"night_diva": 0})
        self.assertNotIn("night_diva", disabled)
        early = generate_roles("chaos", 8, {"night_diva": 5})
        self.assertIn("night_diva", early)

    def test_runtime_timing_snapshot_is_read_from_game(self):
        game = GameState(801, "cfg")
        game.temp["_chat_settings"] = {"night_seconds": 27}
        self.assertEqual(self.engine._duration(game, "night_seconds", 60), 27)
        # Bad values are clamped, so an admin cannot accidentally create a zero timer.
        game.temp["_chat_settings"]["night_seconds"] = 0
        self.assertEqual(self.engine._duration(game, "night_seconds", 60), 5)

    def test_protected_private_content_flag_is_applied(self):
        bot = FakeBot()
        game = GameState(802, "cfg", phase=Phase.NIGHT)
        game.players = {1: PlayerState(1, "A", role_key="optimist")}
        game.temp["_chat_settings"] = {"protect_private_content": True}
        store.games[game.chat_id] = game
        store.user_to_chat[1] = game.chat_id
        asyncio.run(self.engine._safe_pm(bot, 1, "secret"))
        self.assertTrue(bot.messages[-1][2].get("protect_content"))

    def test_night_can_end_as_soon_as_all_active_roles_moved(self):
        bot = FakeBot()
        game = GameState(803, "cfg", mode="classic", phase=Phase.NIGHT, day=1)
        game.players = {
            1: PlayerState(1, "Don", role_key="carleone"),
            2: PlayerState(2, "Doc", role_key="surgeon"),
            3: PlayerState(3, "A", role_key="optimist"),
            4: PlayerState(4, "B", role_key="optimist"),
        }
        game.temp["_chat_settings"] = {"early_night_finish": True}
        game.actions = {
            1: NightAction(1, "mafia_kill", target_id=3, actor_role_key="carleone"),
            2: NightAction(2, "heal", target_id=3, actor_role_key="surgeon"),
        }
        store.games[game.chat_id] = game
        called = []

        async def fake_end(_bot, _game):
            called.append(True)

        self.engine.end_night = fake_end
        result = asyncio.run(self.engine.maybe_finish_night_early(bot, game))
        self.assertTrue(result)
        self.assertEqual(called, [True])

    def test_fast_night_can_be_disabled_for_next_game(self):
        bot = FakeBot()
        game = GameState(804, "cfg", mode="classic", phase=Phase.NIGHT, day=1)
        game.players = {
            1: PlayerState(1, "Don", role_key="carleone"),
            2: PlayerState(2, "Doc", role_key="surgeon"),
            3: PlayerState(3, "A", role_key="optimist"),
            4: PlayerState(4, "B", role_key="optimist"),
        }
        game.temp["_chat_settings"] = {"early_night_finish": False}
        game.actions = {
            1: NightAction(1, "mafia_kill", target_id=3, actor_role_key="carleone"),
            2: NightAction(2, "heal", target_id=3, actor_role_key="surgeon"),
        }
        store.games[game.chat_id] = game
        self.assertFalse(asyncio.run(self.engine.maybe_finish_night_early(bot, game)))


class PersistentChatSettingsTests(unittest.TestCase):
    def test_settings_roundtrip_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(str(Path(tmp) / "settings.sqlite3"))

            async def scenario():
                await storage.init()
                self.assertEqual(await storage.get_chat_settings(99), {})
                await storage.set_chat_setting(99, "night_seconds", 30)
                await storage.set_chat_setting(99, "protect_private_content", True)
                before = await storage.get_chat_settings(99)
                await storage.reset_chat_settings(99)
                after = await storage.get_chat_settings(99)
                return before, after

            before, after = asyncio.run(scenario())
            self.assertEqual(before["night_seconds"], 30)
            self.assertTrue(before["protect_private_content"])
            self.assertEqual(after, {})


if __name__ == "__main__":
    unittest.main()
