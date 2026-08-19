import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from mafia_optimisma.config import Settings
from mafia_optimisma.engine import GameEngine
from mafia_optimisma.keyboards import post_game_keyboard
from mafia_optimisma.models import GameState, Phase, PlayerState
from mafia_optimisma.storage import Storage


class MemoryStorage:
    def __init__(self):
        self.states = {}

    async def save_game_state(self, game):
        self.states[game.chat_id] = game.to_dict()


class FakeMessage:
    def __init__(self, text):
        self.text = text


class FakeSent:
    def __init__(self, message_id, chat_id, text, reply_markup=None):
        self.message_id = message_id
        self.chat_id = chat_id
        self.text = text
        self.reply_markup = reply_markup


class FakeBot:
    def __init__(self):
        self.messages = []
        self._id = 0

    async def send_message(self, chat_id, text, **kwargs):
        self._id += 1
        msg = FakeSent(self._id, chat_id, text, kwargs.get("reply_markup"))
        self.messages.append(msg)
        return msg


class PlayerExperienceTests(unittest.TestCase):
    def test_post_game_keyboard_has_profile_stats_and_group_notification(self):
        kb = post_game_keyboard(-100123, None)
        data = [button.callback_data for row in kb.inline_keyboard for button in row]
        self.assertIn("pm:profile", data)
        self.assertIn("pm:stats", data)
        self.assertIn("notify:set:-100123:1", data)
        self.assertIn("notify:set:-100123:0", data)

    def test_last_word_is_one_shot_and_confirms_in_private(self):
        async def run():
            engine = GameEngine(Settings("x"), MemoryStorage())
            bot = FakeBot()
            game = GameState(-1001, "Test City", phase=Phase.DISCUSSION, day=1)
            player = PlayerState(7, "Alex", role_key="optimist", alive=False)
            game.players[player.user_id] = player
            game.pending_last_words.add(player.user_id)
            first = await engine.handle_last_word(bot, FakeMessage("Проверяйте третьего"), game, player)
            second = await engine.handle_last_word(bot, FakeMessage("второе сообщение"), game, player)
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertNotIn(player.user_id, game.pending_last_words)
            group_texts = [m.text for m in bot.messages if m.chat_id == game.chat_id]
            pm_texts = [m.text for m in bot.messages if m.chat_id == player.user_id]
            self.assertTrue(any("Проверяйте третьего" in text for text in group_texts))
            self.assertTrue(any("Последнее слово принято" in text for text in pm_texts))
        asyncio.run(run())

    def test_explicit_group_notification_setting_roundtrips(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                storage = Storage(str(Path(tmp) / "db.sqlite3"))
                await storage.init()
                await storage.remember_chat_user(-1007, 77, "Seven", "seven")
                enabled = await storage.set_notify_enabled(-1007, 77, True, "Seven", "seven")
                self.assertTrue(enabled)
                users = await storage.get_notify_users(-1007)
                self.assertEqual([u["user_id"] for u in users], [77])
                enabled = await storage.set_notify_enabled(-1007, 77, False, "Seven", "seven")
                self.assertFalse(enabled)
                self.assertEqual(await storage.get_notify_users(-1007), [])
        asyncio.run(run())

    def test_death_offer_registers_last_word_and_has_followup_buttons(self):
        async def run():
            engine = GameEngine(Settings("x"), MemoryStorage())
            bot = FakeBot()
            game = GameState(-1008, "Optimist City", phase=Phase.DISCUSSION, day=2)
            player = PlayerState(88, "Victim", role_key="surgeon", alive=False)
            game.players[player.user_id] = player
            await engine._offer_last_word(bot, game, player)
            self.assertIn(player.user_id, game.pending_last_words)
            pm = bot.messages[-1]
            self.assertEqual(pm.chat_id, player.user_id)
            self.assertIn("одно", pm.text.lower())
            data = [b.callback_data for row in pm.reply_markup.inline_keyboard for b in row]
            self.assertIn(f"notify:set:{game.chat_id}:1", data)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main(verbosity=2)
