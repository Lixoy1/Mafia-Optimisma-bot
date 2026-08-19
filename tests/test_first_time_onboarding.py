import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mafia_optimisma.config import Settings
from mafia_optimisma.engine import GameEngine
from mafia_optimisma.models import GameState, Phase
from mafia_optimisma.state import store
from mafia_optimisma import routers_callbacks, routers_private


class MemoryStorage:
    def __init__(self):
        self.states = {}
    async def ensure_profile(self, user_id, name, username):
        return {"user_id": user_id, "name": name, "username": username}
    async def remember_chat_user(self, chat_id, user_id, name, username):
        return None
    async def save_game_state(self, game):
        self.states[game.chat_id] = game.to_dict()
    async def delete_game_state(self, chat_id):
        self.states.pop(chat_id, None)
    async def get_notify_users(self, chat_id):
        return []


class Sent:
    def __init__(self, message_id, chat_id, text, reply_markup=None):
        self.message_id = message_id
        self.chat_id = chat_id
        self.text = text
        self.reply_markup = reply_markup


class FakeBot:
    def __init__(self):
        self.messages = []
        self.ops = []
        self.next_id = 100
    async def send_message(self, chat_id, text, **kwargs):
        self.next_id += 1
        msg = Sent(self.next_id, chat_id, text, kwargs.get("reply_markup"))
        self.messages.append(msg)
        return msg
    async def send_chat_action(self, chat_id, action):
        return None
    async def get_me(self):
        return types.SimpleNamespace(username="optimisma_test_bot")
    async def pin_chat_message(self, chat_id, message_id, **kwargs):
        self.ops.append(("pin", chat_id, message_id))
    async def edit_message_text(self, text, chat_id, message_id, **kwargs):
        self.ops.append(("edit", chat_id, message_id, text))
        return Sent(message_id, chat_id, text, kwargs.get("reply_markup"))
    async def delete_message(self, chat_id, message_id):
        self.ops.append(("delete", chat_id, message_id))
    async def edit_message_reply_markup(self, chat_id, message_id, **kwargs):
        return None
    async def unpin_chat_message(self, chat_id, message_id, **kwargs):
        return None


class FakeCallback:
    def __init__(self, bot, game, user_id=77):
        self.bot = bot
        self.data = f"join:{game.session_id}:{game.chat_id}"
        self.from_user = types.SimpleNamespace(id=user_id, full_name="HOUSE", username="house")
        self.answers = []
    async def answer(self, text="", **kwargs):
        self.answers.append((text, kwargs))


class FakePmMessage:
    def __init__(self, bot, user_id=77):
        self.bot = bot
        self.from_user = types.SimpleNamespace(id=user_id, full_name="HOUSE", username="house")
        self.answers = []
    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return None


class Cmd:
    def __init__(self, args):
        self.args = args


class FirstTimeOnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store.games.clear()
        store.user_to_chat.clear()
        self.storage = MemoryStorage()
        self.engine = GameEngine(Settings("x", registration_seconds=90), self.storage)
        routers_callbacks.setup(self.engine)
        routers_private.setup(self.engine)
        self.bot = FakeBot()
        self.game = GameState(-100123456789, "Optimist City", mode="classic", phase=Phase.REGISTRATION)
        store.games[self.game.chat_id] = self.game

    async def asyncTearDown(self):
        for task in list(self.engine.tasks.values()) + list(self.engine.warning_tasks.values()):
            if not task.done():
                task.cancel()

    async def test_first_join_reserves_place_and_offers_deep_link(self):
        self.engine._probe_private_chat = AsyncMock(return_value=False)
        cb = FakeCallback(self.bot, self.game)
        await routers_callbacks.cb_join(cb)

        self.assertIn(77, self.game.players)
        self.assertEqual(store.user_to_chat.get(77), self.game.chat_id)
        self.assertIn(77, self.game.temp.get("_pending_pm_activation", []))

        activation_cards = [m for m in self.bot.messages if "Добро пожаловать" in m.text]
        self.assertEqual(len(activation_cards), 1)
        markup = activation_cards[0].reply_markup
        self.assertIsNotNone(markup)
        button = markup.inline_keyboard[0][0]
        self.assertIn("?start=join_", button.url)
        self.assertIn(str(self.game.chat_id), button.url)
        self.assertTrue(any("уже в списке" in text for text, _ in cb.answers))

        registration = self.engine.registration_text(self.game)
        self.assertIn("HOUSE", registration)
        self.assertIn("первый вход", registration)
        self.assertIn("Готовы: <b>0</b>", registration)

    async def test_start_deeplink_activates_existing_registration_without_second_join(self):
        self.engine._probe_private_chat = AsyncMock(return_value=False)
        cb = FakeCallback(self.bot, self.game)
        await routers_callbacks.cb_join(cb)
        prompt_id = int(self.game.temp["_activation_prompt_ids"]["77"])
        payload = f"join_{self.game.session_id}_{self.game.chat_id}"

        pm = FakePmMessage(self.bot)
        await routers_private.start_pm(pm, Cmd(payload))

        self.assertIn(77, self.game.players)
        self.assertNotIn(77, self.game.temp.get("_pending_pm_activation", []))
        self.assertNotIn("77", self.game.temp.get("_activation_prompt_ids", {}))
        self.assertIn(("delete", self.game.chat_id, prompt_id), self.bot.ops)
        self.assertTrue(any("Место" in text or "место" in text for text, _ in pm.answers))
        self.assertTrue(any("повторно" in text for text, _ in pm.answers))
        registration = self.engine.registration_text(self.game)
        self.assertNotIn("первый вход", registration)
        self.assertIn("Готовы: <b>1</b>", registration)

    async def test_repeat_player_needs_only_join(self):
        self.engine._probe_private_chat = AsyncMock(return_value=True)
        cb = FakeCallback(self.bot, self.game)
        await routers_callbacks.cb_join(cb)
        self.assertIn(77, self.game.players)
        self.assertEqual(self.game.temp.get("_pending_pm_activation", []), [])
        self.assertTrue(any(text == "Ты в игре!" for text, _ in cb.answers))
        self.assertFalse(any("Добро пожаловать" in m.text for m in self.bot.messages if m.chat_id == self.game.chat_id))


if __name__ == "__main__":
    unittest.main(verbosity=2)
