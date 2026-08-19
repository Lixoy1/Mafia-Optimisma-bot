import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from mafia_optimisma.config import Settings
from mafia_optimisma.engine import GameEngine
from mafia_optimisma.models import GameState, Phase, PlayerState
from mafia_optimisma.state import store
from mafia_optimisma.storage import Storage


class MemoryStorage:
    def __init__(self):
        self.states = {}

    async def save_game_state(self, game):
        self.states[game.chat_id] = game.to_dict()

    async def delete_game_state(self, chat_id):
        self.states.pop(chat_id, None)

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

    async def send_sticker(self, *args, **kwargs):
        return None

    async def edit_message_reply_markup(self, *args, **kwargs):
        return None

    async def delete_message(self, *args, **kwargs):
        return None


class RoundPolishCoreTests(unittest.TestCase):
    def setUp(self):
        store.games.clear()
        store.user_to_chat.clear()
        self.engine = GameEngine(
            Settings(
                "x", registration_seconds=90, night_seconds=45,
                discussion_seconds=45, nomination_seconds=30, verdict_seconds=20,
            ),
            MemoryStorage(),
        )

    def tearDown(self):
        for task in list(self.engine.tasks.values()):
            if not task.done():
                task.cancel()

    def test_special_role_is_not_stuck_to_same_player(self):
        game = GameState(701, "live", mode="classic")
        game.players = {
            1: PlayerState(1, "A", number=1),
            2: PlayerState(2, "B", number=2),
            3: PlayerState(3, "C", number=3),
            4: PlayerState(4, "D", number=4),
        }
        self.engine._assign_start_roles(game, {1: "carleone", 2: "surgeon"})
        self.assertNotEqual(game.players[1].role_key, "carleone")
        self.assertNotEqual(game.players[2].role_key, "surgeon")
        self.assertCountEqual(
            [p.role_key for p in game.players.values()],
            ["carleone", "surgeon", "optimist", "optimist"],
        )

    def test_winning_night_does_not_announce_fake_new_day(self):
        bot = FakeBot()
        game = GameState(702, "live", mode="classic", phase=Phase.NIGHT, day=2, started_at=time.time())
        game.players = {
            1: PlayerState(1, "Don", number=1, role_key="carleone", alive=True),
            2: PlayerState(2, "Town", number=2, role_key="optimist", alive=True),
        }
        store.games[game.chat_id] = game
        store.user_to_chat[1] = game.chat_id
        store.user_to_chat[2] = game.chat_id

        async def fake_resolve(_bot, _game):
            _game.players[2].alive = False
            return [(_game.players[2], "Ночная атака")], ["🔻 Ночной удар: Town"]

        async def fake_check(_bot, _game):
            return "mafia"

        self.engine.resolve_night = fake_resolve
        self.engine.check_win = fake_check
        asyncio.run(self.engine.end_night(bot, game))
        text = "\n".join(message for _, message, _ in bot.messages)
        self.assertIn("Ночной удар", text)
        self.assertNotIn("День 2", text)
        self.assertNotIn("город просыпается", text)


class LastRoleStorageTests(unittest.TestCase):
    def test_previous_role_roundtrip_is_per_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "roles.sqlite3")
            storage = Storage(path)

            async def scenario():
                await storage.init()
                await storage.remember_chat_user(10, 1, "A", None)
                await storage.remember_chat_user(10, 2, "B", None)
                await storage.remember_chat_user(11, 1, "A", None)
                await storage.set_last_roles(10, {1: "carleone", 2: "surgeon"})
                await storage.set_last_roles(11, {1: "optimist"})
                return (
                    await storage.get_last_roles(10, [1, 2]),
                    await storage.get_last_roles(11, [1]),
                )

            group10, group11 = asyncio.run(scenario())
            self.assertEqual(group10, {1: "carleone", 2: "surgeon"})
            self.assertEqual(group11, {1: "optimist"})


if __name__ == "__main__":
    unittest.main()
