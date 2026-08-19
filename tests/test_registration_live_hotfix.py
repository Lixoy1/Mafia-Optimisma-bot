import asyncio
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_core import FakeBot, FakeStorage, Settings, GameEngine, GameState, Phase, PlayerState, store
from mafia_optimisma import routers_callbacks


class CallbackMessage:
    async def answer(self, text, **kwargs):
        return None
    async def edit_reply_markup(self, **kwargs):
        return None


class FakeCallback:
    def __init__(self, bot, data, user_id):
        self.bot = bot
        self.data = data
        self.from_user = type('User', (), {'id': user_id, 'full_name': f'U{user_id}', 'username': None})()
        self.message = CallbackMessage()
        self.answers = []
    async def answer(self, text='', **kwargs):
        self.answers.append((text, kwargs))


class JoinStorage(FakeStorage):
    async def remember_chat_user(self, chat_id, user_id, name, username):
        return None


class RegistrationLiveHotfixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store.games.clear()
        store.user_to_chat.clear()
        self.settings = Settings(
            'x', registration_seconds=0.01, registration_warning_seconds=0.005,
            night_seconds=100, discussion_seconds=100,
            nomination_seconds=100, verdict_seconds=100,
        )

    async def test_timer_autostarts_exactly_four_reachable_players(self):
        storage = FakeStorage(); bot = FakeBot(); engine = GameEngine(self.settings, storage)
        g = GameState(8201, 'timer-start', mode='classic')
        store.games[g.chat_id] = g
        for i in range(1, 5):
            g.players[i] = PlayerState(i, f'P{i}', number=i)
            store.remember_user(i, g.chat_id)
        await engine.begin_registration(bot, g)
        await asyncio.sleep(0.04)
        self.assertIs(store.get(g.chat_id), g)
        self.assertEqual(g.phase, Phase.NIGHT)
        self.assertEqual(g.day, 1)
        self.assertIsNone(g.registration_message_id)
        engine.cancel_timer(g.chat_id)

    async def test_first_join_is_kept_when_private_chat_is_closed(self):
        class ClosedPmBot(FakeBot):
            async def send_chat_action(self, chat_id, action):
                if chat_id == 11:
                    raise RuntimeError('bot cannot initiate PM')
                return None

        storage = JoinStorage(); bot = ClosedPmBot(); engine = GameEngine(self.settings, storage)
        routers_callbacks.setup(engine)
        g = GameState(8202, 'join', mode='classic', phase=Phase.REGISTRATION)
        store.games[g.chat_id] = g
        cb = FakeCallback(bot, f'join:{g.session_id}:{g.chat_id}', 11)
        await routers_callbacks.cb_join(cb)
        self.assertIn(11, g.players)
        self.assertEqual(store.user_to_chat.get(11), g.chat_id)
        self.assertTrue(any('повторно' in text for text, _ in cb.answers))

    async def test_timeout_removes_one_closed_pm_and_starts_with_four(self):
        class OneClosedPmBot(FakeBot):
            async def send_chat_action(self, chat_id, action):
                if chat_id == 5:
                    raise RuntimeError('bot cannot initiate PM')
                return None

        storage = FakeStorage(); bot = OneClosedPmBot(); engine = GameEngine(self.settings, storage)
        g = GameState(8203, 'pm-filter', mode='classic')
        store.games[g.chat_id] = g
        for i in range(1, 6):
            g.players[i] = PlayerState(i, f'P{i}', number=i)
            store.remember_user(i, g.chat_id)
        await engine.begin_registration(bot, g)
        await asyncio.sleep(0.04)
        self.assertIs(store.get(g.chat_id), g)
        self.assertEqual(g.phase, Phase.NIGHT)
        self.assertEqual(len(g.players), 4)
        self.assertNotIn(5, g.players)
        engine.cancel_timer(g.chat_id)

    async def test_timeout_with_too_few_reachable_players_closes_instead_of_hanging(self):
        class OneClosedPmBot(FakeBot):
            async def send_chat_action(self, chat_id, action):
                if chat_id == 4:
                    raise RuntimeError('bot cannot initiate PM')
                return None

        storage = FakeStorage(); bot = OneClosedPmBot(); engine = GameEngine(self.settings, storage)
        g = GameState(8204, 'pm-close', mode='classic')
        store.games[g.chat_id] = g
        for i in range(1, 5):
            g.players[i] = PlayerState(i, f'P{i}', number=i)
            store.remember_user(i, g.chat_id)
        await engine.begin_registration(bot, g)
        await asyncio.sleep(0.04)
        self.assertIsNone(store.get(g.chat_id))
        self.assertNotIn(g.chat_id, engine.tasks)
        self.assertTrue(any('Регистрация закрыта' in m.text for m in bot.messages))


if __name__ == '__main__':
    unittest.main(verbosity=2)
