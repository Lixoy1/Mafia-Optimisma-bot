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
        for task in list(self.engine.tasks.values()) + list(self.engine.warning_tasks.values()) + list(self.engine.finalization_tasks.values()):
            if not task.done():
                task.cancel()
        store.games.clear()
        store.user_to_chat.clear()

    def run_async(self, coro):
        return asyncio.run(coro)

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
        self.assertIsNone(self.engine.detect_winner(g))
        g.players[3].alive = False
        self.assertEqual(self.engine.detect_winner(g), "town")

    def test_anarchy_town_wins_after_mafia_and_maniac_are_gone(self):
        g = GameState(11, "chaos", mode="chaos", phase=Phase.DISCUSSION)
        g.players = {
            1: PlayerState(1, "Cop", role_key="tracker"),
            2: PlayerState(2, "Wanderer", role_key="wanderer"),
            3: PlayerState(3, "Mafia", role_key="carleone", alive=False),
            4: PlayerState(4, "Maniac", role_key="butcher", alive=False),
        }
        self.assertEqual(self.engine.detect_winner(g), "town")

    def test_block_keyboard_uses_engine_action_name(self):
        g = GameState(12, "x", phase=Phase.NIGHT, day=1)
        g.players = {
            1: PlayerState(1, "Diva", role_key="night_diva"),
            2: PlayerState(2, "Target", role_key="optimist"),
        }
        kb = night_action_keyboard(g, g.players[1])
        data = [button.callback_data for row in kb.inline_keyboard for button in row if getattr(button, "callback_data", None)]
        self.assertTrue(any(":b:" in value or "block_and_silence" in value for value in data))

    def test_blocked_doctor_does_not_heal(self):
        async def scenario():
            g = GameState(13, "x", phase=Phase.NIGHT, day=1)
            g.players = {
                1: PlayerState(1, "Diva", role_key="night_diva"),
                2: PlayerState(2, "Doctor", role_key="surgeon"),
                3: PlayerState(3, "Victim", role_key="optimist"),
                4: PlayerState(4, "Don", role_key="carleone"),
            }
            g.actions = {
                1: NightAction(1, "block_and_silence", target_id=2, actor_role_key="night_diva"),
                2: NightAction(2, "heal", target_id=3, actor_role_key="surgeon"),
                4: NightAction(4, "mafia_kill", target_id=3, actor_role_key="carleone"),
            }
            deaths, _ = await self.engine.resolve_night(self.bot, g)
            self.assertFalse(g.players[3].alive)
            self.assertTrue(any(p.user_id == 3 for p, _ in deaths))
        self.run_async(scenario())

    def test_callback_data_stays_within_telegram_limit(self):
        g = GameState(-1001234567890, "x", phase=Phase.NIGHT, day=123)
        g.session_id = "abcdef1234567890abcdef1234567890"
        roles = ["carleone", "surgeon", "tracker", "night_diva", "reporter", "joker"]
        for idx, role in enumerate(roles, 1):
            g.players[idx] = PlayerState(10**12 + idx, role, role_key=role)
        for p in g.players.values():
            kb = night_action_keyboard(g, p)
            if not kb:
                continue
            for row in kb.inline_keyboard:
                for button in row:
                    if getattr(button, "callback_data", None):
                        self.assertLessEqual(len(button.callback_data.encode("utf-8")), 64, button.callback_data)

    def test_checked_player_is_told_someone_was_interested(self):
        async def scenario():
            g = GameState(14, "x", phase=Phase.NIGHT, day=1)
            g.players = {
                1: PlayerState(1, "Tracker", role_key="tracker"),
                2: PlayerState(2, "Target", role_key="optimist"),
            }
            g.actions = {1: NightAction(1, "check", target_id=2, actor_role_key="tracker")}
            await self.engine.resolve_night(self.bot, g)
            target_messages = [m.text for m in self.bot.messages if m.chat_id == 2]
            self.assertTrue(any("заинтересовался" in text for text in target_messages))
        self.run_async(scenario())

    def test_fatalist_counts_against_mafia_parity(self):
        g = GameState(15, "classic", phase=Phase.DISCUSSION)
        g.players = {
            1: PlayerState(1, "Don", role_key="carleone"),
            2: PlayerState(2, "Town", role_key="optimist"),
            3: PlayerState(3, "Fatal", role_key="fatalist"),
        }
        self.assertIsNone(self.engine.detect_winner(g))

    def test_two_killers_can_hit_same_victim_and_both_attacks_are_reported(self):
        async def scenario():
            g = GameState(16, "chaos", mode="chaos", phase=Phase.NIGHT, day=1)
            g.players = {
                1: PlayerState(1, "Don", role_key="carleone"),
                2: PlayerState(2, "Butcher", role_key="butcher"),
                3: PlayerState(3, "Victim", role_key="optimist"),
            }
            g.actions = {
                1: NightAction(1, "mafia_kill", target_id=3, actor_role_key="carleone"),
                2: NightAction(2, "solo_kill", target_id=3, actor_role_key="butcher"),
            }
            deaths, events = await self.engine.resolve_night(self.bot, g)
            self.assertEqual(len([p for p, _ in deaths if p.user_id == 3]), 1)
            self.assertEqual(sum("Victim" in e for e in events), 2)
        self.run_async(scenario())

    def test_werewolf_mafia_attack_transforms_instead_of_killing(self):
        async def scenario():
            g = GameState(17, "x", phase=Phase.NIGHT, day=1)
            g.players = {
                1: PlayerState(1, "Don", role_key="carleone"),
                2: PlayerState(2, "Wolf", role_key="werewolf"),
            }
            g.actions = {1: NightAction(1, "mafia_kill", target_id=2, actor_role_key="carleone")}
            deaths, _ = await self.engine.resolve_night(self.bot, g)
            self.assertEqual(g.players[2].role_key, "torpedo")
            self.assertTrue(g.players[2].alive)
            self.assertFalse(any(p.user_id == 2 for p, _ in deaths))
        self.run_async(scenario())

    def test_werewolf_commissioner_shot_transforms_instead_of_killing(self):
        async def scenario():
            g = GameState(18, "x", phase=Phase.NIGHT, day=2)
            g.players = {
                1: PlayerState(1, "Tracker", role_key="tracker"),
                2: PlayerState(2, "Wolf", role_key="werewolf"),
            }
            g.actions = {1: NightAction(1, "shoot", target_id=2, actor_role_key="tracker")}
            deaths, _ = await self.engine.resolve_night(self.bot, g)
            self.assertEqual(g.players[2].role_key, "cadet")
            self.assertTrue(g.players[2].alive)
            self.assertFalse(any(p.user_id == 2 for p, _ in deaths))
        self.run_async(scenario())

    def test_self_heal_is_consumed_only_once(self):
        async def scenario():
            g = GameState(19, "x", phase=Phase.NIGHT, day=1)
            doc = PlayerState(1, "Doctor", role_key="surgeon")
            g.players = {1: doc, 2: PlayerState(2, "Other", role_key="optimist")}
            g.actions = {1: NightAction(1, "heal", target_id=1, actor_role_key="surgeon")}
            await self.engine.resolve_night(self.bot, g)
            self.assertEqual(doc.self_heals_used, 1)
            await self.engine.resolve_night(self.bot, g)
            self.assertEqual(doc.self_heals_used, 1)
        self.run_async(scenario())

    def test_unique_nominee_starts_separate_verdict_and_excludes_candidate(self):
        async def scenario():
            g = GameState(20, "x", phase=Phase.NOMINATION, day=1)
            g.players = {
                1: PlayerState(1, "A", role_key="optimist"),
                2: PlayerState(2, "B", role_key="optimist"),
                3: PlayerState(3, "C", role_key="carleone"),
            }
            store.games[g.chat_id] = g
            for uid in g.players:
                store.remember_user(uid, g.chat_id)
            g.votes = {1: 3, 2: 3, 3: None}
            await self.engine.end_nomination(self.bot, g)
            self.assertEqual(g.phase, Phase.VERDICT)
            self.assertEqual(g.nominated_id, 3)
            self.assertNotIn(3, g.verdict_pm_message_ids)
            self.engine.cancel_timer(g.chat_id)
        self.run_async(scenario())

    def test_no_nomination_votes_cannot_hang_and_moves_to_next_night(self):
        async def scenario():
            g = GameState(21, "x", phase=Phase.NOMINATION, day=1)
            g.players = {
                1: PlayerState(1, "A", role_key="optimist"),
                2: PlayerState(2, "B", role_key="optimist"),
                3: PlayerState(3, "C", role_key="carleone"),
            }
            store.games[g.chat_id] = g
            for uid in g.players:
                store.remember_user(uid, g.chat_id)
            await self.engine.end_nomination(self.bot, g)
            self.assertEqual(g.phase, Phase.NIGHT)
            self.assertEqual(g.day, 2)
            self.engine.cancel_timer(g.chat_id)
        self.run_async(scenario())

    def test_verdict_yes_executes_and_always_advances(self):
        async def scenario():
            g = GameState(22, "x", phase=Phase.VERDICT, day=1)
            g.players = {
                1: PlayerState(1, "Don", role_key="carleone"),
                2: PlayerState(2, "Town1", role_key="optimist"),
                3: PlayerState(3, "Town2", role_key="optimist"),
            }
            g.nominated_id = 2
            g.verdict_votes = {1: True, 3: True}
            store.games[g.chat_id] = g
            for uid in g.players:
                store.remember_user(uid, g.chat_id)
            await self.engine.end_verdict(self.bot, g)
            self.assertFalse(g.players[2].alive)
            self.assertNotEqual(g.phase, Phase.VERDICT)
            if store.get(g.chat_id) is g:
                self.engine.cancel_timer(g.chat_id)
        self.run_async(scenario())

    def test_verdict_no_pardons_and_advances(self):
        async def scenario():
            g = GameState(23, "x", phase=Phase.VERDICT, day=1)
            g.players = {
                1: PlayerState(1, "Don", role_key="carleone"),
                2: PlayerState(2, "Town1", role_key="optimist"),
                3: PlayerState(3, "Town2", role_key="optimist"),
            }
            g.nominated_id = 2
            g.verdict_votes = {1: False, 3: True}
            store.games[g.chat_id] = g
            for uid in g.players:
                store.remember_user(uid, g.chat_id)
            await self.engine.end_verdict(self.bot, g)
            self.assertTrue(g.players[2].alive)
            self.assertNotEqual(g.phase, Phase.VERDICT)
            if store.get(g.chat_id) is g:
                self.engine.cancel_timer(g.chat_id)
        self.run_async(scenario())

    def test_player_cannot_join_two_active_games_and_numbers_are_stable(self):
        async def scenario():
            g1 = GameState(24, "one", phase=Phase.REGISTRATION)
            g2 = GameState(25, "two", phase=Phase.REGISTRATION)
            store.games[g1.chat_id] = g1
            store.games[g2.chat_id] = g2
            ok1, _ = await self.engine.add_player(g1, 100, "U", None)
            ok2, _ = await self.engine.add_player(g2, 100, "U", None)
            self.assertTrue(ok1)
            self.assertFalse(ok2)
            self.assertEqual(g1.players[100].number, 1)
            g1.players[200] = PlayerState(200, "V", number=2)
            g1.players.pop(100)
            self.assertEqual(g1.next_player_number(), 3)
        self.run_async(scenario())

    def test_state_roundtrip_preserves_phase_votes_actions_and_ui_ids(self):
        g = GameState(26, "state", phase=Phase.VERDICT, day=4)
        g.players = {
            1: PlayerState(1, "A", number=3, role_key="tracker", initial_role_key="optimist"),
            2: PlayerState(2, "B", number=7, role_key="carleone", initial_role_key="torpedo"),
        }
        g.actions = {1: NightAction(1, "check", target_id=2, actor_role_key="tracker")}
        g.votes = {1: 2, 2: None}
        g.verdict_votes = {1: True}
        g.nominated_id = 2
        g.phase_deadline = 123.45
        g.registration_message_id = 1000
        g.registration_warning_id = 1001
        g.night_pm_message_ids = {1: 2001}
        g.nomination_pm_message_ids = {1: 2002}
        g.verdict_pm_message_ids = {1: 2003}
        restored = GameState.from_dict(g.to_dict())
        self.assertEqual(restored.phase, Phase.VERDICT)
        self.assertEqual(restored.day, 4)
        self.assertEqual(restored.players[1].number, 3)
        self.assertEqual(restored.players[1].initial_role_key, "optimist")
        self.assertEqual(restored.actions[1].actor_role_key, "tracker")
        self.assertEqual(restored.votes[2], None)
        self.assertEqual(restored.verdict_votes[1], True)
        self.assertEqual(restored.phase_deadline, 123.45)
        self.assertEqual(restored.registration_message_id, 1000)
        self.assertEqual(restored.registration_warning_id, 1001)
        self.assertEqual(restored.night_pm_message_ids[1], 2001)
        self.assertEqual(restored.nomination_pm_message_ids[1], 2002)
        self.assertEqual(restored.verdict_pm_message_ids[1], 2003)

    def test_registration_card_is_pinned_then_disabled_unpinned_and_deleted(self):
        async def scenario():
            g = GameState(27, "reg", phase=Phase.REGISTRATION)
            store.games[g.chat_id] = g
            await self.engine.public_registration_message(self.bot, g)
            mid = g.registration_message_id
            self.assertIsNotNone(mid)
            self.assertIn(("pin", g.chat_id, mid), self.bot.ops)
            await self.engine.close_registration_ui(self.bot, g)
            self.assertIn(("disable", g.chat_id, mid), self.bot.ops)
            self.assertIn(("unpin", g.chat_id, mid), self.bot.ops)
            self.assertIn(("delete", g.chat_id, mid), self.bot.ops)
            self.assertIsNone(g.registration_message_id)
        self.run_async(scenario())

    def test_real_start_closes_registration_and_reaches_night(self):
        async def scenario():
            g = GameState(28, "start", phase=Phase.REGISTRATION)
            for i in range(1, 5):
                g.players[i] = PlayerState(i, f"P{i}", number=i)
                store.remember_user(i, g.chat_id)
            store.games[g.chat_id] = g
            await self.engine.public_registration_message(self.bot, g)
            mid = g.registration_message_id
            await self.engine.start_game(self.bot, g)
            self.assertEqual(g.phase, Phase.NIGHT)
            self.assertEqual(g.day, 1)
            self.assertIn(("unpin", g.chat_id, mid), self.bot.ops)
            self.assertIn(("delete", g.chat_id, mid), self.bot.ops)
            self.engine.cancel_timer(g.chat_id)
        self.run_async(scenario())

    def test_extend_registration_adds_time_instead_of_shortening_it(self):
        async def scenario():
            g = GameState(29, "extend", phase=Phase.REGISTRATION)
            store.games[g.chat_id] = g
            g.phase_deadline = time.time() + 90
            before = g.phase_deadline
            ok = await self.engine.extend_registration(self.bot, g, 30)
            self.assertTrue(ok)
            self.assertGreaterEqual(g.phase_deadline, before + 29)
            self.engine.cancel_timer(g.chat_id)
        self.run_async(scenario())

    def test_expired_restored_discussion_advances_instead_of_hanging(self):
        async def scenario():
            g = GameState(30, "restore", phase=Phase.DISCUSSION, day=1)
            g.players = {
                1: PlayerState(1, "Don", role_key="carleone"),
                2: PlayerState(2, "A", role_key="optimist"),
                3: PlayerState(3, "B", role_key="optimist"),
                4: PlayerState(4, "C", role_key="surgeon"),
            }
            g.phase_deadline = time.time() - 1
            await self.storage.save_game_state(g)
            restored_engine = GameEngine(self.engine.settings, self.storage)
            count = await restored_engine.restore_active_games(self.bot)
            self.assertEqual(count, 1)
            await asyncio.sleep(0.02)
            fixed = store.get(g.chat_id)
            self.assertIsNotNone(fixed)
            self.assertIn(fixed.phase, {Phase.NOMINATION, Phase.VERDICT, Phase.NIGHT, Phase.RESOLVING})
            for task in list(restored_engine.tasks.values()):
                if not task.done():
                    task.cancel()
        self.run_async(scenario())

    def test_expired_restored_verdict_resolves_and_advances(self):
        async def scenario():
            g = GameState(31, "restore", phase=Phase.VERDICT, day=1)
            g.players = {
                1: PlayerState(1, "Don", role_key="carleone"),
                2: PlayerState(2, "A", role_key="optimist"),
                3: PlayerState(3, "B", role_key="optimist"),
                4: PlayerState(4, "C", role_key="surgeon"),
            }
            g.nominated_id = 2
            g.verdict_votes = {1: False, 3: False, 4: False}
            g.phase_deadline = time.time() - 1
            await self.storage.save_game_state(g)
            restored_engine = GameEngine(self.engine.settings, self.storage)
            count = await restored_engine.restore_active_games(self.bot)
            self.assertEqual(count, 1)
            await asyncio.sleep(0.02)
            fixed = store.get(g.chat_id)
            self.assertIsNotNone(fixed)
            self.assertNotEqual(fixed.phase, Phase.VERDICT)
            for task in list(restored_engine.tasks.values()):
                if not task.done():
                    task.cancel()
        self.run_async(scenario())

    def test_last_word_right_expires_when_next_night_starts(self):
        async def scenario():
            g = GameState(32, "lastword", phase=Phase.RESOLVING, day=1)
            g.players = {
                1: PlayerState(1, "Dead", role_key="optimist", alive=False),
                2: PlayerState(2, "Don", role_key="carleone"),
                3: PlayerState(3, "Town", role_key="optimist"),
                4: PlayerState(4, "Doc", role_key="surgeon"),
            }
            g.pending_last_words = {1}
            store.games[g.chat_id] = g
            await self.engine.start_night(self.bot, g, allow_from_resolving=True)
            self.assertEqual(g.pending_last_words, set())
            self.engine.cancel_timer(g.chat_id)
        self.run_async(scenario())

    def test_small_test_game_records_stats_but_pays_no_money(self):
        async def scenario():
            g = GameState(33, "reward", phase=Phase.RESOLVING)
            g.players = {
                1: PlayerState(1, "Don", role_key="carleone"),
                2: PlayerState(2, "Town", role_key="optimist", alive=False),
                3: PlayerState(3, "Doc", role_key="surgeon", alive=False),
                4: PlayerState(4, "Town2", role_key="optimist", alive=False),
            }
            store.games[g.chat_id] = g
            await self.engine.finish_game(self.bot, g, "mafia")
            self.assertTrue(self.storage.rewards)
            self.assertTrue(all(money == 0 for _, _, money, _, _ in self.storage.rewards))
        self.run_async(scenario())

    def test_restored_nomination_recreates_missing_controls(self):
        async def scenario():
            g = GameState(34, "resume-nom", phase=Phase.NOMINATION, day=2)
            g.phase_deadline = time.time() + 100
            g.players = {
                1: PlayerState(1, "A", role_key="optimist"),
                2: PlayerState(2, "B", role_key="optimist"),
                3: PlayerState(3, "C", role_key="carleone"),
                4: PlayerState(4, "D", role_key="surgeon"),
            }
            g.votes = {1: 2}
            g.nomination_pm_message_ids = {2: 999}
            await self.storage.save_game_state(g)
            restored_engine = GameEngine(self.engine.settings, self.storage)
            await restored_engine.restore_active_games(self.bot)
            fixed = store.get(g.chat_id)
            self.assertIn(3, fixed.nomination_pm_message_ids)
            self.assertIn(4, fixed.nomination_pm_message_ids)
            self.assertNotIn(1, fixed.nomination_pm_message_ids)
            for task in list(restored_engine.tasks.values()):
                if not task.done():
                    task.cancel()
        self.run_async(scenario())

    def test_restored_verdict_recreates_missing_controls_except_candidate(self):
        async def scenario():
            g = GameState(35, "resume-verdict", phase=Phase.VERDICT, day=2)
            g.phase_deadline = time.time() + 100
            g.players = {
                1: PlayerState(1, "A", role_key="optimist"),
                2: PlayerState(2, "B", role_key="optimist"),
                3: PlayerState(3, "C", role_key="carleone"),
                4: PlayerState(4, "D", role_key="surgeon"),
            }
            g.nominated_id = 2
            g.verdict_votes = {1: True}
            await self.storage.save_game_state(g)
            restored_engine = GameEngine(self.engine.settings, self.storage)
            await restored_engine.restore_active_games(self.bot)
            fixed = store.get(g.chat_id)
            self.assertNotIn(1, fixed.verdict_pm_message_ids)
            self.assertNotIn(2, fixed.verdict_pm_message_ids)
            self.assertIn(3, fixed.verdict_pm_message_ids)
            self.assertIn(4, fixed.verdict_pm_message_ids)
            for task in list(restored_engine.tasks.values()):
                if not task.done():
                    task.cancel()
        self.run_async(scenario())

    def test_restored_night_recreates_missing_private_controls(self):
        async def scenario():
            g = GameState(36, "resume-night", phase=Phase.NIGHT, day=2)
            g.phase_deadline = time.time() + 100
            g.players = {
                1: PlayerState(1, "Don", role_key="carleone"),
                2: PlayerState(2, "Doc", role_key="surgeon"),
                3: PlayerState(3, "Town", role_key="optimist"),
                4: PlayerState(4, "Town2", role_key="optimist"),
            }
            g.actions = {1: NightAction(1, "mafia_kill", target_id=3, actor_role_key="carleone")}
            await self.storage.save_game_state(g)
            restored_engine = GameEngine(self.engine.settings, self.storage)
            await restored_engine.restore_active_games(self.bot)
            fixed = store.get(g.chat_id)
            self.assertNotIn(1, fixed.night_pm_message_ids)
            self.assertIn(2, fixed.night_pm_message_ids)
            for task in list(restored_engine.tasks.values()):
                if not task.done():
                    task.cancel()
        self.run_async(scenario())

    def test_restored_half_started_snapshot_repairs_roles_and_resumes_night(self):
        async def scenario():
            g = GameState(37, "half", mode="classic", phase=Phase.RESOLVING, day=0)
            g.temp["resume_action"] = "start_night"
            for i in range(1, 5):
                g.players[i] = PlayerState(i, f"P{i}", number=i)
            await self.storage.save_game_state(g)
            restored_engine = GameEngine(self.engine.settings, self.storage)
            await restored_engine.restore_active_games(self.bot)
            fixed = store.get(g.chat_id)
            self.assertIsNotNone(fixed)
            self.assertEqual(fixed.phase, Phase.NIGHT)
            self.assertEqual(fixed.day, 1)
            self.assertTrue(all(p.role_key for p in fixed.players.values()))
            self.assertTrue(all(p.initial_role_key == p.role_key for p in fixed.players.values()))
            for task in list(restored_engine.tasks.values()) + list(restored_engine.warning_tasks.values()):
                if not task.done():
                    task.cancel()
        self.run_async(scenario())

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
        self.run_async(scenario())

    def test_registration_cleanup_failure_cannot_block_game_start(self):
        class CleanupFailBot(FakeBot):
            async def edit_message_reply_markup(self, *args, **kwargs):
                raise RuntimeError("disable failed")
            async def unpin_chat_message(self, *args, **kwargs):
                raise RuntimeError("unpin failed")
            async def delete_message(self, *args, **kwargs):
                raise RuntimeError("delete failed")
        async def scenario():
            g = GameState(299, "cleanup", mode="classic", phase=Phase.REGISTRATION)
            for i in range(1, 5):
                g.players[i] = PlayerState(i, f"P{i}", number=i)
            store.games[g.chat_id] = g
            bot = CleanupFailBot()
            await self.engine.public_registration_message(bot, g)
            await self.engine.start_game(bot, g)
            self.assertEqual(g.phase, Phase.NIGHT)
            self.assertEqual(g.day, 1)
            self.engine.cancel_timer(g.chat_id)
        self.run_async(scenario())


if __name__ == "__main__":
    unittest.main(verbosity=2)
