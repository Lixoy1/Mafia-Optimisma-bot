import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_core import FakeBot, FakeStorage, Settings, GameEngine, GameState, Phase, PlayerState, store

from mafia_optimisma import routers_callbacks


class CallbackMessage:
    def __init__(self):
        self.answers=[]
        self.disabled=0
    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
    async def edit_reply_markup(self, **kwargs):
        self.disabled += 1


class FakeCallback:
    def __init__(self, bot, data, user_id):
        self.bot=bot
        self.data=data
        self.from_user=type('User',(),{'id':user_id,'full_name':f'U{user_id}','username':None})()
        self.message=CallbackMessage()
        self.answers=[]
    async def answer(self, text='', **kwargs):
        self.answers.append((text,kwargs))


class FailingBulletStorage(FakeStorage):
    def __init__(self, fail_count, eventual=True):
        super().__init__()
        self.fail_count=fail_count
        self.eventual=eventual
        self.calls=0
    async def consume_item(self, user_id, item):
        self.calls += 1
        if self.fail_count > 0:
            self.fail_count -= 1
            raise RuntimeError('sqlite busy')
        return self.eventual


class CallbackResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store.games.clear(); store.user_to_chat.clear()
        self.settings=Settings('x',registration_seconds=10,registration_warning_seconds=3,night_seconds=10,discussion_seconds=10,nomination_seconds=10,verdict_seconds=10)
        self.bot=FakeBot()

    async def test_black_bullet_db_failure_keeps_pending_and_does_not_commit_normal_kill(self):
        storage=FailingBulletStorage(fail_count=5)
        engine=GameEngine(self.settings,storage); routers_callbacks.setup(engine)
        g=GameState(7101,'cb',phase=Phase.NIGHT,day=1)
        don=PlayerState(1,'Don',role_key='carleone'); town=PlayerState(2,'Town',role_key='optimist')
        g.players={1:don,2:town}; g.armor_piercing_pending.add(1); store.games[g.chat_id]=g
        cb=FakeCallback(self.bot,f'n:{g.session_id}:{g.chat_id}:{g.day}:mafia_kill:2',1)
        await routers_callbacks.cb_night(cb)
        self.assertNotIn(1,g.actions)
        self.assertIn(1,g.armor_piercing_pending)
        self.assertEqual(storage.calls,3)
        self.assertTrue(any('не потрачена' in text for text,_ in cb.answers))

    async def test_black_bullet_transient_db_failure_retries_then_commits_armored_attack(self):
        storage=FailingBulletStorage(fail_count=2,eventual=True)
        engine=GameEngine(self.settings,storage); routers_callbacks.setup(engine)
        g=GameState(7102,'cb',phase=Phase.NIGHT,day=1)
        don=PlayerState(1,'Don',role_key='carleone'); town=PlayerState(2,'Town',role_key='optimist')
        g.players={1:don,2:town}; g.armor_piercing_pending.add(1); store.games[g.chat_id]=g
        cb=FakeCallback(self.bot,f'n:{g.session_id}:{g.chat_id}:{g.day}:mafia_kill:2',1)
        await routers_callbacks.cb_night(cb)
        self.assertIn(1,g.actions)
        self.assertEqual(g.actions[1].item,'armor_piercing')
        self.assertNotIn(1,g.armor_piercing_pending)
        self.assertEqual(storage.calls,3)
        self.assertTrue(any(text=='Действие принято.' for text,_ in cb.answers))

    async def test_black_bullet_missing_after_prepare_does_not_downgrade_silently(self):
        storage=FailingBulletStorage(fail_count=0,eventual=False)
        engine=GameEngine(self.settings,storage); routers_callbacks.setup(engine)
        g=GameState(7103,'cb',phase=Phase.NIGHT,day=1)
        don=PlayerState(1,'Don',role_key='carleone'); town=PlayerState(2,'Town',role_key='optimist')
        g.players={1:don,2:town}; g.armor_piercing_pending.add(1); store.games[g.chat_id]=g
        cb=FakeCallback(self.bot,f'n:{g.session_id}:{g.chat_id}:{g.day}:mafia_kill:2',1)
        await routers_callbacks.cb_night(cb)
        self.assertNotIn(1,g.actions)
        self.assertNotIn(1,g.armor_piercing_pending)
        self.assertTrue(any('обычного хода' in text for text,_ in cb.answers))


if __name__=='__main__':
    unittest.main(verbosity=2)
