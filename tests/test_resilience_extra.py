import asyncio
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_core import FakeBot, FakeStorage, Settings, GameEngine, GameState, NightAction, Phase, PlayerState, store


class FlakyBot(FakeBot):
    def __init__(self, fail_send=0, fail_edit=0, fail_delete=0, fail_unpin=0):
        super().__init__()
        self.fail_send = fail_send
        self.fail_edit = fail_edit
        self.fail_delete = fail_delete
        self.fail_unpin = fail_unpin

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail_send > 0:
            self.fail_send -= 1
            raise RuntimeError('transient send failure')
        return await super().send_message(chat_id, text, **kwargs)

    async def edit_message_reply_markup(self, chat_id, message_id, **kwargs):
        if self.fail_edit > 0:
            self.fail_edit -= 1
            raise RuntimeError('transient edit failure')
        return await super().edit_message_reply_markup(chat_id, message_id, **kwargs)

    async def delete_message(self, chat_id, message_id):
        if self.fail_delete > 0:
            self.fail_delete -= 1
            raise RuntimeError('transient delete failure')
        return await super().delete_message(chat_id, message_id)

    async def unpin_chat_message(self, chat_id, message_id, **kwargs):
        if self.fail_unpin > 0:
            self.fail_unpin -= 1
            raise RuntimeError('transient unpin failure')
        return await super().unpin_chat_message(chat_id, message_id, **kwargs)


class FlakyStorage(FakeStorage):
    def __init__(self, fail_consume=0, fail_save=0, fail_delete=0):
        super().__init__()
        self.fail_consume = fail_consume
        self.fail_save = fail_save
        self.fail_delete = fail_delete

    async def consume_item(self, user_id, item):
        if self.fail_consume > 0:
            self.fail_consume -= 1
            raise RuntimeError('transient db consume failure')
        return False

    async def save_game_state(self, game):
        if self.fail_save > 0:
            self.fail_save -= 1
            raise RuntimeError('transient db save failure')
        return await super().save_game_state(game)

    async def delete_game_state(self, chat_id):
        if self.fail_delete > 0:
            self.fail_delete -= 1
            raise RuntimeError('transient db delete failure')
        return await super().delete_game_state(chat_id)


class ResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store.games.clear()
        store.user_to_chat.clear()
        self.settings = Settings(
            'x', registration_seconds=0.02, registration_warning_seconds=0.01,
            night_seconds=0.02, discussion_seconds=0.02,
            nomination_seconds=0.02, verdict_seconds=0.02,
        )

    async def asyncTearDown(self):
        # Cancel any tasks left by engines reachable in store is not enough; tests
        # explicitly cancel their own engine before returning.
        pass

    async def test_old_timer_cannot_advance_new_phase(self):
        storage = FakeStorage(); bot = FakeBot(); engine = GameEngine(self.settings, storage)
        g = GameState(5001, 'x', phase=Phase.NIGHT, day=1)
        g.players = {1: PlayerState(1,'A', role_key='optimist'), 2: PlayerState(2,'B', role_key='carleone')}
        store.games[g.chat_id] = g
        calls = 0
        async def should_not_run():
            nonlocal calls
            calls += 1
        engine._arm_phase_timer(g, 0.01, should_not_run)
        await engine._set_phase(g, Phase.DISCUSSION, 1)
        await asyncio.sleep(0.03)
        self.assertEqual(calls, 0)
        engine.cancel_timer(g.chat_id)

    async def test_double_end_verdict_is_idempotent(self):
        storage = FakeStorage(); bot = FakeBot(); engine = GameEngine(self.settings, storage)
        g = GameState(5002, 'x', phase=Phase.VERDICT, day=1, started_at=time.time())
        g.players = {
            1: PlayerState(1,'Don', number=1, role_key='carleone'),
            2: PlayerState(2,'Town', number=2, role_key='optimist'),
            3: PlayerState(3,'Doc', number=3, role_key='surgeon'),
        }
        g.nominated_id = 2
        g.verdict_votes = {1: True, 3: True}
        store.games[g.chat_id] = g
        await asyncio.gather(engine.end_verdict(bot, g), engine.end_verdict(bot, g))
        # With Don vs Doc after town dies, mafia parity means a single finish.
        self.assertIsNone(store.get(g.chat_id))
        self.assertEqual(len(storage.rewards), 3)
        engine.cancel_timer(g.chat_id)

    async def test_ui_failures_do_not_block_registration_to_night(self):
        storage = FakeStorage(); bot = FlakyBot(fail_edit=10, fail_delete=10, fail_unpin=10)
        engine = GameEngine(self.settings, storage)
        g = GameState(5003, 'x', phase=Phase.REGISTRATION)
        g.players = {i: PlayerState(i,f'P{i}', number=i) for i in range(1,5)}
        store.games[g.chat_id] = g
        g.registration_message_id = 123
        g.pinned_message_id = 123
        await engine.start_game(bot, g)
        self.assertEqual(g.phase, Phase.NIGHT)
        self.assertEqual(g.day, 1)
        engine.cancel_timer(g.chat_id)

    async def test_transient_save_failure_does_not_freeze_in_memory_game(self):
        storage = FlakyStorage(fail_save=2); bot = FakeBot(); engine = GameEngine(self.settings, storage)
        g = GameState(5004, 'x', phase=Phase.RESOLVING)
        g.players = {
            1: PlayerState(1,'Don', number=1, role_key='carleone', initial_role_key='carleone'),
            2: PlayerState(2,'Doc', number=2, role_key='surgeon', initial_role_key='surgeon'),
            3: PlayerState(3,'A', number=3, role_key='optimist', initial_role_key='optimist'),
            4: PlayerState(4,'B', number=4, role_key='optimist', initial_role_key='optimist'),
        }
        store.games[g.chat_id] = g
        await engine.start_night(bot, g, allow_from_resolving=True)
        self.assertEqual(g.phase, Phase.NIGHT)
        engine.cancel_timer(g.chat_id)

    async def test_restore_resolving_win_finishes_instead_of_starting_night(self):
        storage = FakeStorage(); bot = FakeBot(); engine = GameEngine(self.settings, storage)
        g = GameState(5005, 'x', phase=Phase.RESOLVING, day=3, started_at=time.time()-10)
        g.players = {
            1: PlayerState(1,'Don', number=1, role_key='carleone', alive=True),
            2: PlayerState(2,'Town', number=2, role_key='optimist', alive=False),
        }
        g.temp['resume_action'] = 'check_win_then_start_night'
        await storage.save_game_state(g)
        count = await engine.restore_active_games(bot)
        self.assertEqual(count, 1)
        self.assertIsNone(store.get(g.chat_id))
        self.assertEqual(len(storage.rewards), 2)

    async def test_strict_item_consume_surfaces_persistent_db_failure(self):
        storage = FlakyStorage(fail_consume=5); engine = GameEngine(self.settings, storage)
        with self.assertRaises(RuntimeError):
            await engine._consume_item_strict(77, 'armor_piercing', attempts=3)
        # Three local attempts were made; the callback can now keep its prepared
        # item state intact instead of silently committing a normal attack.
        self.assertEqual(storage.fail_consume, 2)

    async def test_strict_item_consume_recovers_before_committing_action(self):
        class EventuallyConsumes(FlakyStorage):
            async def consume_item(self, user_id, item):
                if self.fail_consume > 0:
                    self.fail_consume -= 1
                    raise RuntimeError('transient db consume failure')
                return True
        storage = EventuallyConsumes(fail_consume=2); engine = GameEngine(self.settings, storage)
        self.assertTrue(await engine._consume_item_strict(77, 'armor_piercing', attempts=3))
        self.assertEqual(storage.fail_consume, 0)

    async def test_transient_consume_failure_can_retry_night_without_losing_heal(self):
        # This intentionally exercises a DB failure after in-memory night processing
        # has started. A robust resolver should be retryable without consuming the
        # doctor's one self-heal or silently changing the result.
        storage = FlakyStorage(fail_consume=1); bot = FakeBot(); engine = GameEngine(self.settings, storage)
        g = GameState(5006, 'x', phase=Phase.NIGHT, day=1)
        doc = PlayerState(1,'Doc', number=1, role_key='surgeon')
        diva = PlayerState(2,'Diva', number=2, role_key='night_diva')
        don = PlayerState(3,'Don', number=3, role_key='carleone')
        victim = PlayerState(4,'Victim', number=4, role_key='optimist')
        g.players = {p.user_id:p for p in (doc,diva,don,victim)}
        # Self heal is first validated; then Diva's perfume check triggers DB error.
        g.actions = {
            1: NightAction(1,'heal',1,actor_role_key='surgeon'),
            2: NightAction(2,'block_and_silence',4,actor_role_key='night_diva'),
            3: NightAction(3,'mafia_kill',1,actor_role_key='carleone'),
        }
        store.games[g.chat_id] = g
        deaths, _ = await engine.resolve_night(bot, g)
        self.assertEqual(doc.self_heals_used, 1)
        self.assertTrue(doc.alive, 'transient inventory failure must not cancel a valid self-heal')
        self.assertNotIn(doc.user_id, [p.user_id for p,_ in deaths])

    async def test_real_timers_keep_advancing_when_everyone_is_afk(self):
        storage = FakeStorage(); bot = FakeBot(); engine = GameEngine(self.settings, storage)
        g = GameState(5007, 'afk', phase=Phase.RESOLVING, started_at=time.time())
        g.players = {
            1: PlayerState(1,'Don', number=1, role_key='carleone', initial_role_key='carleone'),
            2: PlayerState(2,'Doc', number=2, role_key='surgeon', initial_role_key='surgeon'),
            3: PlayerState(3,'A', number=3, role_key='optimist', initial_role_key='optimist'),
            4: PlayerState(4,'B', number=4, role_key='optimist', initial_role_key='optimist'),
        }
        store.games[g.chat_id] = g
        await engine.start_night(bot, g, allow_from_resolving=True)
        # Nobody clicks anything. In ~0.2s several NIGHT->DAY->NOMINATION cycles
        # must complete automatically instead of sticking on a vote.
        await asyncio.sleep(0.22)
        self.assertGreaterEqual(g.day, 2)
        self.assertIn(g.phase, {Phase.NIGHT, Phase.DISCUSSION, Phase.NOMINATION, Phase.RESOLVING})
        engine.cancel_timer(g.chat_id)

    async def test_phase_timer_retries_one_failed_resolver(self):
        storage = FakeStorage(); bot = FakeBot(); engine = GameEngine(self.settings, storage)
        g = GameState(5008, 'retry', phase=Phase.NIGHT, day=1)
        store.games[g.chat_id] = g
        calls = 0
        async def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError('one-shot resolver failure')
            await engine._set_phase(g, Phase.DISCUSSION, 1)
        # Retry interval in production is 5 seconds; monkey-patch the arm method's
        # second invocation by using a direct equivalent short retry test would be
        # intrusive. Here assert the original timer survives the exception by
        # rearming a task rather than disappearing.
        engine._arm_phase_timer(g, 0.005, flaky)
        await asyncio.sleep(0.02)
        self.assertEqual(calls, 1)
        self.assertIn(g.chat_id, engine.tasks)
        self.assertFalse(engine.tasks[g.chat_id].done())
        engine.cancel_timer(g.chat_id)

    async def test_registration_timeout_with_too_few_players_cleans_up(self):
        storage = FakeStorage(); bot = FakeBot(); engine = GameEngine(self.settings, storage)
        g = GameState(5009, 'small')
        g.players = {1: PlayerState(1,'A',number=1), 2: PlayerState(2,'B',number=2)}
        store.games[g.chat_id] = g
        await engine.begin_registration(bot, g)
        mid = g.registration_message_id
        self.assertIsNotNone(mid)
        await asyncio.sleep(0.06)
        self.assertIsNone(store.get(g.chat_id))
        self.assertTrue(any(op[0]=='unpin' and op[2]==mid for op in bot.ops))
        self.assertTrue(any(op[0]=='delete' and op[2]==mid for op in bot.ops))

    async def test_finished_snapshot_delete_failure_does_not_resurrect_or_repay(self):
        storage = FlakyStorage(fail_delete=1); bot = FakeBot(); engine = GameEngine(self.settings, storage)
        g = GameState(5010, 'finish', phase=Phase.DISCUSSION, started_at=time.time()-3)
        g.players = {
            1: PlayerState(1,'Don',number=1,role_key='carleone',alive=True),
            2: PlayerState(2,'Town',number=2,role_key='optimist',alive=False),
        }
        store.games[g.chat_id]=g
        await engine.check_win(bot,g)
        self.assertEqual(store.get(g.chat_id).phase, Phase.FINISHED)
        first_rewards=len(storage.rewards)
        self.assertEqual(first_rewards,2)
        # FINISHED snapshot remains because deletion failed. Cancel the local retry
        # to simulate a hard process stop, then restore from SQLite-like storage.
        retry = engine.finalization_tasks.get(g.chat_id)
        if retry:
            retry.cancel()
        self.assertIn(g.chat_id, storage.states)
        store.games.clear(); store.user_to_chat.clear()
        engine2=GameEngine(self.settings,storage)
        restored=await engine2.restore_active_games(bot)
        self.assertEqual(restored,0)
        self.assertNotIn(g.chat_id, storage.states)
        self.assertEqual(len(storage.rewards), first_rewards)

    async def test_restart_completes_partial_rewards_without_double_paying(self):
        class PartialRewardFailure(FakeStorage):
            def __init__(self):
                super().__init__()
                self.failed_once = False
            async def reward_once(self, session_id, user_id, win, money, gems, xp):
                if user_id == 2 and not self.failed_once:
                    self.failed_once = True
                    raise RuntimeError('crash between player rewards')
                return await super().reward_once(session_id, user_id, win, money, gems, xp)

        storage=PartialRewardFailure(); bot=FakeBot(); engine=GameEngine(self.settings,storage)
        g=GameState(5012,'partial-rewards',phase=Phase.DISCUSSION,started_at=time.time()-3)
        g.players={
            1:PlayerState(1,'Don',number=1,role_key='carleone',alive=True),
            2:PlayerState(2,'Town',number=2,role_key='optimist',alive=False),
        }
        store.games[g.chat_id]=g
        await engine.check_win(bot,g)
        # User 1 committed; user 2 failed. The FINISHED snapshot must remain.
        self.assertEqual([r[0] for r in storage.rewards], [1])
        self.assertEqual(store.get(g.chat_id).phase, Phase.FINISHED)
        retry=engine.finalization_tasks.get(g.chat_id)
        if retry:
            retry.cancel()
        store.games.clear(); store.user_to_chat.clear()

        engine2=GameEngine(self.settings,storage)
        restored=await engine2.restore_active_games(bot)
        self.assertEqual(restored,0)
        self.assertIsNone(store.get(g.chat_id))
        # The already committed Don reward is not repeated; the missing player is
        # completed exactly once after restart.
        self.assertEqual([r[0] for r in storage.rewards], [1,2])
        self.assertEqual(len(storage.reward_events),2)

    async def test_group_send_failures_do_not_stop_night_to_day_transition(self):
        storage=FakeStorage(); bot=FlakyBot(fail_send=20); engine=GameEngine(self.settings,storage)
        g=GameState(5011,'telegram-down',phase=Phase.NIGHT,day=1,started_at=time.time())
        g.players={
            1:PlayerState(1,'Don',number=1,role_key='carleone'),
            2:PlayerState(2,'Doc',number=2,role_key='surgeon'),
            3:PlayerState(3,'A',number=3,role_key='optimist'),
            4:PlayerState(4,'B',number=4,role_key='optimist'),
        }
        store.games[g.chat_id]=g
        await engine.end_night(bot,g)
        self.assertEqual(g.phase,Phase.DISCUSSION)
        engine.cancel_timer(g.chat_id)


if __name__ == '__main__':
    unittest.main(verbosity=2)
