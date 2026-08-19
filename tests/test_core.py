import asyncio
import sys
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Offline aiogram stubs.
aiogram = types.ModuleType("aiogram")
class Bot: pass
class Magic:
    def __getattr__(self, name): return self
    def __call__(self, *a, **k): return self
    def startswith(self, *a, **k): return self
    def __eq__(self, other): return self
    def __invert__(self): return self
    def in_(self, *a, **k): return self
class Router:
    def __init__(self, *a, **k): pass
    def message(self, *a, **k): return lambda fn: fn
    def callback_query(self, *a, **k): return lambda fn: fn
aiogram.Bot = Bot
aiogram.Router = Router
aiogram.F = Magic()
sys.modules["aiogram"] = aiogram

exc = types.ModuleType("aiogram.exceptions")
class TelegramForbiddenError(Exception): pass
class TelegramBadRequest(Exception): pass
exc.TelegramForbiddenError = TelegramForbiddenError
exc.TelegramBadRequest = TelegramBadRequest
sys.modules["aiogram.exceptions"] = exc

types_mod = types.ModuleType("aiogram.types")
class Message: pass
class CallbackQuery: pass
class InlineKeyboardButton:
    def __init__(self, **kwargs): self.__dict__.update(kwargs)
class InlineKeyboardMarkup:
    def __init__(self, **kwargs): self.__dict__.update(kwargs)
types_mod.Message = Message
types_mod.CallbackQuery = CallbackQuery
types_mod.InlineKeyboardButton = InlineKeyboardButton
types_mod.InlineKeyboardMarkup = InlineKeyboardMarkup
sys.modules["aiogram.types"] = types_mod

aiosqlite = types.ModuleType("aiosqlite")
aiosqlite.Connection = object
sys.modules["aiosqlite"] = aiosqlite

dotenv = types.ModuleType("dotenv")
dotenv.load_dotenv = lambda: None
sys.modules["dotenv"] = dotenv

from mafia_optimisma.config import Settings
from mafia_optimisma.engine import GameEngine, generate_roles, living_summary
from mafia_optimisma.keyboards import night_action_keyboard, verdict_keyboard, vote_keyboard
from mafia_optimisma.models import GameState, NightAction, Phase, PlayerState
from mafia_optimisma.state import store


class FakeStorage:
    def __init__(self):
        self.states = {}
        self.rewards = []
        self.reward_events = {}

    async def ensure_profile(self, user_id, name, username):
        return {"user_id": user_id, "name": name, "username": username}

    async def consume_item(self, user_id, item):
        return False

    async def reward(self, user_id, win, money, gems, xp):
        self.rewards.append((user_id, win, money, gems, xp))
        return {"money": money, "gems": gems, "xp": xp, "level": 1, "level_up": False}

    async def reward_once(self, session_id, user_id, win, money, gems, xp):
        key = (session_id, user_id)
        if key in self.reward_events:
            result = dict(self.reward_events[key])
            result["already_applied"] = True
            return result
        result = await self.reward(user_id, win, money, gems, xp)
        result = dict(result)
        result["already_applied"] = False
        self.reward_events[key] = dict(result)
        return result

    async def save_game_state(self, game):
        self.states[game.chat_id] = game.to_dict()

    async def delete_game_state(self, chat_id):
        self.states.pop(chat_id, None)

    async def load_game_states(self):
        return list(self.states.values())

    async def get_profile(self, user_id):
        return {"items": {"armor_piercing": 0}}

    async def get_notify_users(self, chat_id):
        return []


class SentMessage:
    def __init__(self, message_id, chat_id, text, reply_markup=None):
        self.message_id = message_id
        self.chat_id = chat_id
        self.text = text
        self.reply_markup = reply_markup


class FakeBot:
    def __init__(self):
        self.messages = []
        self.ops = []
        self._message_id = 100

    async def send_message(self, chat_id, text, **kwargs):
        self._message_id += 1
        msg = SentMessage(self._message_id, chat_id, text, kwargs.get("reply_markup"))
        self.messages.append(msg)
        return msg

    async def send_sticker(self, *args, **kwargs):
        return None

    async def pin_chat_message(self, chat_id, message_id, **kwargs):
        self.ops.append(("pin", chat_id, message_id))

    async def unpin_chat_message(self, chat_id, message_id, **kwargs):
        self.ops.append(("unpin", chat_id, message_id))

    async def delete_message(self, chat_id, message_id):
        self.ops.append(("delete", chat_id, message_id))

    async def edit_message_reply_markup(self, chat_id, message_id, **kwargs):
        self.ops.append(("disable", chat_id, message_id))

    async def edit_message_text(self, text, chat_id, message_id, **kwargs):
        self.ops.append(("edit", chat_id, message_id))
        return SentMessage(message_id, chat_id, text, kwargs.get("reply_markup"))

    async def send_chat_action(self, chat_id, action):
        return None

    async def get_me(self):
        return types.SimpleNamespace(username="test_bot", first_name="Test Bot")


class CoreTests(unittest.TestCase):
    def setUp(self):
        store.games.clear()
        store.user_to_chat.clear()
        self.storage = FakeStorage()
        self.engine = GameEngine(
            Settings(
                "x",
                registration_seconds=100,
                registration_warning_seconds=30,
                night_seconds=100,
                discussion_seconds=100,
                nomination_seconds=100,
                verdict_seconds=100,
            ),
            self.storage,
        )
        self.bot = FakeBot()

    def tearDown(self):
        for task in list(self.engine.tasks.values()) + list(self.engine.warning_tasks.values()):
            if not task.done():
                task.cancel()

    def run(self, result=None):
        return super().run(result)

    def test_classic_four_roles(self):
        roles = generate_roles("classic", 4)
        self.assertCountEqual(roles, ["carleone", "surgeon", "optimist", "optimist"])

    def test_public_summary_shows_role_counts_and_stable_numbers(self):
        g = GameState(1, "t")
        g.players = {
            1: PlayerState(1, "A", number=2, role_key="carleone"),
            2: PlayerState(2, "B", number=5, role_key="surgeon"),
        }
        text = living_summary(g)
        self.assertIn('<b>02</b> · <a href="tg://user?id=1">A</a>', text)
        self.assertIn('<b>05</b> · <a href="tg://user?id=2">B</a>', text)
        self.assertIn("Карлеоне  ×1", text)
        self.assertIn("Хирург  ×1", text)
        self.assertNotIn("A — 🤵", text)

    def test_anarchy_continues_after_mafia_dies_while_maniac_alive(self):
        g = GameState(10, "chaos", mode="chaos", phase=Phase.DISCUSSION)
        g.players = {
            1: PlayerState(1, "Cop", role_key="tracker"),
            2: PlayerState(2, "Wanderer", role_key="wanderer"),
            3: PlayerState(3, "Maniac", role_key="butcher"),
        }
        store.games[g.chat_id] = g
        result = asyncio.run(self.engine.check_win(self.bot, g))
        self.assertIsNone(result)
        self.assertIs(store.get(g.chat_id), g)

    def test_anarchy_town_wins_after_mafia_and_maniac_are_gone(self):
        g = GameState(11, "chaos", mode="chaos", phase=Phase.DISCUSSION, started_at=time.time())
        g.players = {
            1: PlayerState(1, "Cop", role_key="tracker"),
            2: PlayerState(2, "Wanderer", role_key="wanderer"),
        }
        store.games[g.chat_id] = g
        result = asyncio.run(self.engine.check_win(self.bot, g))
        self.assertEqual(result, "town")
        self.assertIsNone(store.get(g.chat_id))

    def test_fatalist_counts_against_mafia_parity(self):
        g = GameState(12, "classic", mode="classic", phase=Phase.DISCUSSION)
        g.players = {
            1: PlayerState(1, "M", role_key="carleone"),
            2: PlayerState(2, "T", role_key="optimist"),
            3: PlayerState(3, "F", role_key="fatalist"),
        }
        store.games[g.chat_id] = g
        result = asyncio.run(self.engine.check_win(self.bot, g))
        self.assertIsNone(result)

    def test_blocked_doctor_does_not_heal(self):
        g = GameState(13, "classic", mode="classic", phase=Phase.NIGHT, day=1)
        diva = PlayerState(1, "Diva", role_key="night_diva")
        doctor = PlayerState(2, "Doc", role_key="surgeon")
        mafia = PlayerState(3, "Don", role_key="carleone")
        victim = PlayerState(4, "Victim", role_key="optimist")
        g.players = {p.user_id: p for p in [diva, doctor, mafia, victim]}
        g.actions = {
            1: NightAction(1, "block_and_silence", 2, actor_role_key="night_diva"),
            2: NightAction(2, "heal", 4, actor_role_key="surgeon"),
            3: NightAction(3, "mafia_kill", 4, actor_role_key="carleone"),
        }
        store.games[g.chat_id] = g
        deaths, _ = asyncio.run(self.engine.resolve_night(self.bot, g))
        self.assertTrue(doctor.blocked)
        self.assertTrue(doctor.silenced)
        self.assertFalse(victim.alive)
        self.assertIn(victim.user_id, [p.user_id for p, _ in deaths])

    def test_player_cannot_join_two_active_games_and_numbers_are_stable(self):
        g1 = GameState(201, "one", mode="classic", phase=Phase.REGISTRATION)
        g2 = GameState(202, "two", mode="classic", phase=Phase.REGISTRATION)
        store.games[g1.chat_id] = g1
        store.games[g2.chat_id] = g2
        ok1, _ = asyncio.run(self.engine.add_player(g1, 42, "Player", "p"))
        ok_other, _ = asyncio.run(self.engine.add_player(g1, 43, "Other", None))
        ok2, msg2 = asyncio.run(self.engine.add_player(g2, 42, "Player", "p"))
        self.assertTrue(ok1 and ok_other)
        self.assertEqual(g1.players[42].number, 1)
        self.assertEqual(g1.players[43].number, 2)
        g1.players.pop(42)
        store.user_to_chat.pop(42, None)
        ok3, _ = asyncio.run(self.engine.add_player(g1, 44, "Third", None))
        self.assertTrue(ok3)
        self.assertEqual(g1.players[44].number, 3)
        self.assertFalse(ok2)
        self.assertIn("другой активной игре", msg2)

    def test_self_heal_is_consumed_only_once(self):
        g = GameState(203, "classic", mode="classic", phase=Phase.NIGHT, day=1)
        doctor = PlayerState(1, "Doc", role_key="surgeon")
        mafia = PlayerState(2, "Don", role_key="carleone")
        g.players = {1: doctor, 2: mafia}
        g.actions = {
            1: NightAction(1, "heal", 1, actor_role_key="surgeon"),
            2: NightAction(2, "mafia_kill", 1, actor_role_key="carleone"),
        }
        store.games[g.chat_id] = g
        deaths, _ = asyncio.run(self.engine.resolve_night(self.bot, g))
        self.assertEqual(doctor.self_heals_used, 1)
        self.assertTrue(doctor.alive)
        self.assertEqual(deaths, [])
        # A real second night has a new day token and fresh transient state.
        # Re-running the resolver with the same day is an idempotent retry, not a
        # second opportunity to heal.
        g.day = 2
        g.temp.clear()
        g.actions = {
            1: NightAction(1, "heal", 1, actor_role_key="surgeon"),
            2: NightAction(2, "mafia_kill", 1, actor_role_key="carleone"),
        }
        deaths, _ = asyncio.run(self.engine.resolve_night(self.bot, g))
        self.assertFalse(doctor.alive)
        self.assertEqual(doctor.self_heals_used, 1)
        self.assertEqual([p.user_id for p, _ in deaths], [1])

    def test_callback_data_stays_within_telegram_limit(self):
        g = GameState(-1001234567890, "x", mode="clans", phase=Phase.NIGHT, day=123, session_id="abcdefghij")
        roles = ["carleone", "surgeon", "tracker", "night_diva", "breacher", "shield", "shadow", "reporter", "alibi_master", "joker", "butcher", "sakura_emperor", "samurai", "shinobi", "forger"]
        for i, role in enumerate(roles, 1):
            g.players[10_000_000_000 + i] = PlayerState(10_000_000_000 + i, "P" + str(i), role_key=role)
        for p in g.players.values():
            kb = night_action_keyboard(g, p)
            if kb:
                for row in kb.inline_keyboard:
                    for button in row:
                        data = getattr(button, "callback_data", None)
                        if data:
                            self.assertLessEqual(len(data.encode("utf-8")), 64, data)
        for kb in (vote_keyboard(g, next(iter(g.players))), verdict_keyboard(g)):
            for row in kb.inline_keyboard:
                for button in row:
                    self.assertLessEqual(len(button.callback_data.encode("utf-8")), 64, button.callback_data)

    def test_block_keyboard_uses_engine_action_name(self):
        g = GameState(99, "x", mode="classic", phase=Phase.NIGHT, day=1, session_id="abcdef")
        diva = PlayerState(1, "Diva", role_key="night_diva")
        target = PlayerState(2, "Target", role_key="optimist")
        g.players = {1: diva, 2: target}
        kb = night_action_keyboard(g, diva)
        data = [b.callback_data for row in kb.inline_keyboard for b in row if getattr(b, "callback_data", None)]
        from mafia_optimisma.protocol import decode_action
        tokens = [x.split(":", 5)[4] for x in data if x.startswith("n:")]
        self.assertIn("block_and_silence", [decode_action(x) for x in tokens])



    def test_restored_half_started_snapshot_repairs_roles_and_resumes_night(self):
        async def scenario():
            g = GameState(296, "repair", mode="classic", phase=Phase.RESOLVING)
            g.temp["resume_action"] = "start_night"
            for i in range(1, 5):
                g.players[i] = PlayerState(i, f"P{i}", number=i, role_key=None)
            await self.storage.save_game_state(g)
            restored_engine = GameEngine(self.engine.settings, self.storage)
            restored = await restored_engine.restore_active_games(self.bot)
            self.assertEqual(restored, 1)
            fixed = store.get(g.chat_id)
            self.assertIsNotNone(fixed)
            self.assertEqual(fixed.phase, Phase.NIGHT)
            self.assertEqual(fixed.day, 1)
            self.assertTrue(all(p.role_key for p in fixed.players.values()))
            self.assertTrue(all(p.initial_role_key == p.role_key for p in fixed.players.values()))
            for task in list(restored_engine.tasks.values()) + list(restored_engine.warning_tasks.values()):
                if not task.done():
                    task.cancel()
        asyncio.run(scenario())

    def test_concurrent_start_requests_only_start_one_game(self):
        async def scenario():
            g = GameState(298, "concurrent", mode="classic", phase=Phase.REGISTRATION)
            for i in range(1, 5):
                g.players[i] = PlayerState(i, f"P{i}", number=i)
            store.games[g.chat_id] = g
            await asyncio.gather(
                self.engine.start_game(self.bot, g),
                self.engine.start_game(self.bot, g),
            )
            self.assertEqual(g.phase, Phase.NIGHT)
            self.assertEqual(g.day, 1)
            starts = [m for m in self.bot.messages if "Началась игра" in m.text]
            nights = [m for m in self.bot.messages if "Ночь 1" in m.text]
            self.assertEqual(len(starts), 1)
            self.assertEqual(len(nights), 1)
        asyncio.run(scenario())

    def test_registration_cleanup_failure_cannot_block_game_start(self):
        class CleanupFailBot(FakeBot):
            async def edit_message_reply_markup(self, *args, **kwargs):
                raise RuntimeError("disable failed")
            async def unpin_chat_message(self, *args, **kwargs):
                raise RuntimeError("unpin failed")
            async def delete_message(self, *args, **kwargs):
                raise RuntimeError("delete failed")

        async def scenario():
            bot = CleanupFailBot()
            g = GameState(297, "cleanup", mode="classic", phase=Phase.REGISTRATION)
            for i in range(1, 5):
                g.players[i] = PlayerState(i, f"P{i}", number=i)
            store.games[g.chat_id] = g
            # A real registration has a pinned message id; make cleanup actually
            # exercise all three failing Telegram operations.
            g.registration_message_id = 555
            g.pinned_message_id = 555
            await self.engine.start_game(bot, g)
            self.assertEqual(g.phase, Phase.NIGHT)
            self.assertEqual(g.day, 1)
            self.assertIsNone(g.registration_message_id)
            self.assertTrue(any("Началась игра" in m.text for m in bot.messages))
        asyncio.run(scenario())

    def test_real_start_closes_registration_and_reaches_night(self):
        async def scenario():
            g = GameState(299, "start", mode="classic", phase=Phase.REGISTRATION)
            for i in range(1, 5):
                g.players[i] = PlayerState(i, f"P{i}", number=i)
            store.games[g.chat_id] = g
            await self.engine.start_game(self.bot, g)
            self.assertEqual(g.phase, Phase.NIGHT)
            self.assertEqual(g.day, 1)
            self.assertIsNotNone(g.started_at)
            self.assertTrue(all(p.role_key for p in g.players.values()))
            self.assertTrue(all(p.initial_role_key == p.role_key for p in g.players.values()))
        asyncio.run(scenario())

    def test_state_roundtrip_preserves_phase_votes_actions_and_ui_ids(self):
        g = GameState(300, "persist", mode="chaos", phase=Phase.VERDICT, day=4, session_id="abc123")
        g.phase_version = 9
        g.phase_started_at = 100.0
        g.phase_deadline = 150.0
        g.started_at = 50.0
        g.players = {1: PlayerState(1, "A", number=7, role_key="tracker", initial_role_key="optimist", checked_ids={2, 3})}
        g.actions = {1: NightAction(1, "check", 2, actor_role_key="tracker")}
        g.votes = {1: 2}
        g.verdict_votes = {1: True}
        g.nominated_id = 2
        g.registration_message_id = 777
        g.night_pm_message_ids = {1: 888}
        g.armor_piercing_pending = {1}
        restored = GameState.from_dict(g.to_dict())
        self.assertEqual(restored.phase, Phase.VERDICT)
        self.assertEqual(restored.phase_version, 9)
        self.assertEqual(restored.players[1].number, 7)
        self.assertEqual(restored.players[1].checked_ids, {2, 3})
        self.assertEqual(restored.actions[1].action_type, "check")
        self.assertEqual(restored.verdict_votes, {1: True})
        self.assertEqual(restored.night_pm_message_ids, {1: 888})
        self.assertEqual(restored.armor_piercing_pending, {1})

    def test_extend_registration_adds_time_instead_of_shortening_it(self):
        async def scenario():
            g = GameState(399, "group", mode="classic")
            store.games[g.chat_id] = g
            await self.engine.begin_registration(self.bot, g)
            before = g.phase_deadline
            await self.engine.extend_registration(self.bot, g, 30)
            self.assertGreaterEqual(g.phase_deadline - before, 29.0)
        asyncio.run(scenario())

    def test_registration_card_is_pinned_then_disabled_unpinned_and_deleted(self):
        async def scenario():
            g = GameState(400, "group", mode="classic")
            store.games[g.chat_id] = g
            await self.engine.begin_registration(self.bot, g)
            mid = g.registration_message_id
            self.assertIsNotNone(mid)
            self.assertIn(("pin", g.chat_id, mid), self.bot.ops)
            await self.engine.close_registration_ui(self.bot, g)
            self.assertIn(("disable", g.chat_id, mid), self.bot.ops)
            self.assertIn(("unpin", g.chat_id, mid), self.bot.ops)
            self.assertIn(("delete", g.chat_id, mid), self.bot.ops)
            self.assertIsNone(g.registration_message_id)
        asyncio.run(scenario())

    def test_no_nomination_votes_cannot_hang_and_moves_to_next_night(self):
        async def scenario():
            g = GameState(500, "g", mode="classic", phase=Phase.NOMINATION, day=1, started_at=time.time())
            g.players = {
                1: PlayerState(1, "Don", number=1, role_key="carleone"),
                2: PlayerState(2, "A", number=2, role_key="optimist"),
                3: PlayerState(3, "Doc", number=3, role_key="surgeon"),
            }
            store.games[g.chat_id] = g
            await self.engine.end_nomination(self.bot, g)
            self.assertEqual(g.phase, Phase.NIGHT)
            self.assertEqual(g.day, 2)
            self.assertTrue(any("не определились" in m.text for m in self.bot.messages))
        asyncio.run(scenario())

    def test_unique_nominee_starts_separate_verdict_and_excludes_candidate(self):
        async def scenario():
            g = GameState(501, "g", mode="classic", phase=Phase.NOMINATION, day=1, started_at=time.time())
            g.players = {
                1: PlayerState(1, "Don", number=1, role_key="carleone"),
                2: PlayerState(2, "A", number=2, role_key="optimist"),
                3: PlayerState(3, "B", number=3, role_key="surgeon"),
                4: PlayerState(4, "C", number=4, role_key="optimist"),
            }
            g.votes = {1: 2, 3: 2, 4: None}
            store.games[g.chat_id] = g
            await self.engine.end_nomination(self.bot, g)
            self.assertEqual(g.phase, Phase.VERDICT)
            self.assertEqual(g.nominated_id, 2)
            self.assertNotIn(2, g.verdict_pm_message_ids)
            self.assertEqual(set(g.verdict_pm_message_ids), {1, 3, 4})
        asyncio.run(scenario())

    def test_verdict_yes_executes_and_always_advances(self):
        async def scenario():
            g = GameState(502, "g", mode="classic", phase=Phase.VERDICT, day=1, started_at=time.time())
            g.players = {
                1: PlayerState(1, "Don", number=1, role_key="carleone"),
                2: PlayerState(2, "A", number=2, role_key="optimist"),
                3: PlayerState(3, "Doc", number=3, role_key="surgeon"),
                4: PlayerState(4, "B", number=4, role_key="optimist"),
            }
            g.nominated_id = 2
            g.verdict_votes = {1: True, 3: True, 4: False}
            store.games[g.chat_id] = g
            await self.engine.end_verdict(self.bot, g)
            self.assertFalse(g.players[2].alive)
            self.assertEqual(g.phase, Phase.NIGHT)
            self.assertEqual(g.day, 2)
        asyncio.run(scenario())

    def test_verdict_no_pardons_and_advances(self):
        async def scenario():
            g = GameState(503, "g", mode="classic", phase=Phase.VERDICT, day=1, started_at=time.time())
            g.players = {
                1: PlayerState(1, "Don", number=1, role_key="carleone"),
                2: PlayerState(2, "A", number=2, role_key="optimist"),
                3: PlayerState(3, "Doc", number=3, role_key="surgeon"),
                4: PlayerState(4, "B", number=4, role_key="optimist"),
            }
            g.nominated_id = 2
            g.verdict_votes = {1: False, 3: True, 4: False}
            store.games[g.chat_id] = g
            await self.engine.end_verdict(self.bot, g)
            self.assertTrue(g.players[2].alive)
            self.assertEqual(g.phase, Phase.NIGHT)
        asyncio.run(scenario())

    def test_expired_restored_discussion_advances_instead_of_hanging(self):
        async def scenario():
            g = GameState(600, "restore", mode="classic", phase=Phase.DISCUSSION, day=1, started_at=time.time())
            g.phase_version = 3
            g.phase_deadline = time.time() - 1
            g.players = {
                1: PlayerState(1, "Don", number=1, role_key="carleone"),
                2: PlayerState(2, "A", number=2, role_key="optimist"),
                3: PlayerState(3, "Doc", number=3, role_key="surgeon"),
            }
            await self.storage.save_game_state(g)
            restored_engine = GameEngine(self.engine.settings, self.storage)
            count = await restored_engine.restore_active_games(self.bot)
            self.assertEqual(count, 1)
            await asyncio.sleep(0.02)
            restored = store.get(600)
            self.assertIsNotNone(restored)
            self.assertEqual(restored.phase, Phase.NOMINATION)
            for task in list(restored_engine.tasks.values()) + list(restored_engine.warning_tasks.values()):
                if not task.done():
                    task.cancel()
        asyncio.run(scenario())


    def test_two_killers_can_hit_same_victim_and_both_attacks_are_reported(self):
        g = GameState(700, "chaos", mode="chaos", phase=Phase.NIGHT, day=2)
        don = PlayerState(1, "Don", role_key="carleone")
        maniac = PlayerState(2, "Maniac", role_key="butcher")
        victim = PlayerState(3, "Victim", role_key="surgeon")
        g.players = {1: don, 2: maniac, 3: victim}
        g.actions = {
            1: NightAction(1, "mafia_kill", 3, actor_role_key="carleone"),
            2: NightAction(2, "solo_kill", 3, actor_role_key="butcher"),
        }
        store.games[g.chat_id] = g
        deaths, events = asyncio.run(self.engine.resolve_night(self.bot, g))
        self.assertEqual([p.user_id for p, _ in deaths], [3])
        self.assertEqual(len([e for e in events if "Victim" in e]), 2)
        self.assertFalse(victim.alive)

    def test_werewolf_mafia_attack_transforms_instead_of_killing(self):
        g = GameState(701, "classic", mode="classic", phase=Phase.NIGHT, day=2)
        don = PlayerState(1, "Don", role_key="carleone")
        wolf = PlayerState(2, "Wolf", role_key="werewolf")
        town = PlayerState(3, "Town", role_key="optimist")
        g.players = {1: don, 2: wolf, 3: town}
        g.actions = {1: NightAction(1, "mafia_kill", 2, actor_role_key="carleone")}
        store.games[g.chat_id] = g
        deaths, events = asyncio.run(self.engine.resolve_night(self.bot, g))
        self.assertTrue(wolf.alive)
        self.assertEqual(wolf.role_key, "torpedo")
        self.assertEqual(deaths, [])
        self.assertFalse(any("Wolf" in e for e in events))

    def test_werewolf_commissioner_shot_transforms_instead_of_killing(self):
        g = GameState(702, "classic", mode="classic", phase=Phase.NIGHT, day=2)
        tracker = PlayerState(1, "Cop", role_key="tracker")
        wolf = PlayerState(2, "Wolf", role_key="werewolf")
        don = PlayerState(3, "Don", role_key="carleone")
        g.players = {1: tracker, 2: wolf, 3: don}
        g.actions = {1: NightAction(1, "shoot", 2, actor_role_key="tracker")}
        store.games[g.chat_id] = g
        deaths, events = asyncio.run(self.engine.resolve_night(self.bot, g))
        self.assertTrue(wolf.alive)
        self.assertEqual(wolf.role_key, "cadet")
        self.assertEqual(deaths, [])
        self.assertFalse(any("Wolf" in e for e in events))

    def test_patient_is_told_when_doctor_saves_them(self):
        g = GameState(703, "classic", mode="classic", phase=Phase.NIGHT, day=1)
        doctor = PlayerState(1, "Doc", role_key="surgeon")
        don = PlayerState(2, "Don", role_key="carleone")
        victim = PlayerState(3, "Patient", role_key="optimist")
        g.players = {1: doctor, 2: don, 3: victim}
        g.actions = {
            1: NightAction(1, "heal", 3, actor_role_key="surgeon"),
            2: NightAction(2, "mafia_kill", 3, actor_role_key="carleone"),
        }
        store.games[g.chat_id] = g
        deaths, _ = asyncio.run(self.engine.resolve_night(self.bot, g))
        self.assertEqual(deaths, [])
        self.assertTrue(victim.alive)
        patient_texts = [m.text for m in self.bot.messages if m.chat_id == 3]
        self.assertTrue(any("Хирург спас" in t for t in patient_texts), patient_texts)

    def test_checked_player_is_told_someone_was_interested(self):
        g = GameState(704, "classic", mode="classic", phase=Phase.NIGHT, day=1)
        tracker = PlayerState(1, "Cop", role_key="tracker")
        target = PlayerState(2, "Target", role_key="carleone")
        g.players = {1: tracker, 2: target}
        g.actions = {1: NightAction(1, "check", 2, actor_role_key="tracker")}
        store.games[g.chat_id] = g
        asyncio.run(self.engine.resolve_night(self.bot, g))
        target_texts = [m.text for m in self.bot.messages if m.chat_id == 2]
        self.assertTrue(any("заинтересовался" in t for t in target_texts), target_texts)

    def test_last_word_right_expires_when_next_night_starts(self):
        async def scenario():
            g = GameState(705, "classic", mode="classic", phase=Phase.RESOLVING, day=1, started_at=time.time())
            g.players = {
                1: PlayerState(1, "Don", role_key="carleone"),
                2: PlayerState(2, "Town", role_key="optimist"),
                3: PlayerState(3, "Doc", role_key="surgeon"),
            }
            g.pending_last_words.add(99)
            store.games[g.chat_id] = g
            await self.engine.start_night(self.bot, g, allow_from_resolving=True)
            self.assertEqual(g.pending_last_words, set())
        asyncio.run(scenario())

    def test_small_test_game_records_stats_but_pays_no_money(self):
        async def scenario():
            g = GameState(706, "classic", mode="classic", phase=Phase.DISCUSSION, started_at=time.time() - 20)
            g.players = {
                1: PlayerState(1, "Town", role_key="optimist"),
                2: PlayerState(2, "Doc", role_key="surgeon"),
            }
            store.games[g.chat_id] = g
            await self.engine.finish_game(self.bot, g, "town")
            self.assertTrue(self.storage.rewards)
            self.assertTrue(all(entry[2] == 0 for entry in self.storage.rewards))
        asyncio.run(scenario())


    def test_restored_night_recreates_missing_private_controls(self):
        async def scenario():
            g = GameState(708, "restore-night", mode="classic", phase=Phase.NIGHT, day=1)
            g.phase_version = 3
            g.phase_deadline = time.time() + 60
            g.players = {
                1: PlayerState(1, "Don", number=1, role_key="carleone"),
                2: PlayerState(2, "Doc", number=2, role_key="surgeon"),
                3: PlayerState(3, "Town", number=3, role_key="optimist"),
                4: PlayerState(4, "Town2", number=4, role_key="optimist"),
            }
            await self.storage.save_game_state(g)
            restored_engine = GameEngine(self.engine.settings, self.storage)
            await restored_engine.restore_active_games(self.bot)
            fixed = store.get(g.chat_id)
            self.assertEqual(fixed.phase, Phase.NIGHT)
            self.assertIn(1, fixed.night_pm_message_ids)
            self.assertIn(2, fixed.night_pm_message_ids)
            self.assertNotIn(3, fixed.night_pm_message_ids)
            for task in list(restored_engine.tasks.values()) + list(restored_engine.warning_tasks.values()):
                if not task.done(): task.cancel()
        asyncio.run(scenario())

    def test_restored_nomination_recreates_missing_controls(self):
        async def scenario():
            g = GameState(709, "restore-nom", mode="classic", phase=Phase.NOMINATION, day=2)
            g.phase_version = 4
            g.phase_deadline = time.time() + 60
            g.players = {
                1: PlayerState(1, "A", number=1, role_key="carleone"),
                2: PlayerState(2, "B", number=2, role_key="surgeon"),
                3: PlayerState(3, "C", number=3, role_key="optimist", silenced=True),
            }
            await self.storage.save_game_state(g)
            restored_engine = GameEngine(self.engine.settings, self.storage)
            await restored_engine.restore_active_games(self.bot)
            fixed = store.get(g.chat_id)
            self.assertEqual(set(fixed.nomination_pm_message_ids), {1, 2})
            for task in list(restored_engine.tasks.values()) + list(restored_engine.warning_tasks.values()):
                if not task.done(): task.cancel()
        asyncio.run(scenario())

    def test_restored_verdict_recreates_missing_controls_except_candidate(self):
        async def scenario():
            g = GameState(710, "restore-verdict", mode="classic", phase=Phase.VERDICT, day=2)
            g.phase_version = 4
            g.phase_deadline = time.time() + 60
            g.nominated_id = 2
            g.players = {
                1: PlayerState(1, "A", number=1, role_key="carleone"),
                2: PlayerState(2, "Candidate", number=2, role_key="surgeon"),
                3: PlayerState(3, "C", number=3, role_key="optimist"),
            }
            await self.storage.save_game_state(g)
            restored_engine = GameEngine(self.engine.settings, self.storage)
            await restored_engine.restore_active_games(self.bot)
            fixed = store.get(g.chat_id)
            self.assertEqual(set(fixed.verdict_pm_message_ids), {1, 3})
            for task in list(restored_engine.tasks.values()) + list(restored_engine.warning_tasks.values()):
                if not task.done(): task.cancel()
        asyncio.run(scenario())

    def test_expired_restored_verdict_resolves_and_advances(self):
        async def scenario():
            g = GameState(707, "restore", mode="classic", phase=Phase.VERDICT, day=1, started_at=time.time())
            g.phase_version = 5
            g.phase_deadline = time.time() - 1
            g.players = {
                1: PlayerState(1, "Don", number=1, role_key="carleone"),
                2: PlayerState(2, "Candidate", number=2, role_key="optimist"),
                3: PlayerState(3, "Doc", number=3, role_key="surgeon"),
                4: PlayerState(4, "Town", number=4, role_key="optimist"),
            }
            g.nominated_id = 2
            g.verdict_votes = {1: True, 3: True}
            await self.storage.save_game_state(g)
            restored_engine = GameEngine(self.engine.settings, self.storage)
            await restored_engine.restore_active_games(self.bot)
            await asyncio.sleep(0.02)
            restored = store.get(707)
            self.assertIsNotNone(restored)
            self.assertFalse(restored.players[2].alive)
            self.assertEqual(restored.phase, Phase.NIGHT)
            for task in list(restored_engine.tasks.values()) + list(restored_engine.warning_tasks.values()):
                if not task.done():
                    task.cancel()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main(verbosity=2)
