import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import aiosqlite

from mafia_optimisma.config import Settings
from mafia_optimisma.content import ITEMS, ROLES, TEAMS
from mafia_optimisma.engine import GameEngine
from mafia_optimisma.keyboards import admin_chat_rules_keyboard, vote_keyboard
from mafia_optimisma.models import GameState, NightAction, Phase, PlayerState
from mafia_optimisma.routers_group import _contains_profanity, _message_has_link, _moderation_reason
from mafia_optimisma.storage import Storage


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


class DummyMessage:
    def __init__(self, text=None, caption=None, sticker=None, entities=None, caption_entities=None):
        self.text = text
        self.caption = caption
        self.sticker = sticker
        self.entities = entities or []
        self.caption_entities = caption_entities or []


async def grant_item(storage: Storage, user_id: int, item_key: str, count: int = 1) -> None:
    async with aiosqlite.connect(storage.path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT items FROM profiles WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        items = json.loads(row["items"] or "{}")
        items[item_key] = count
        await db.execute(
            "UPDATE profiles SET items = ? WHERE user_id = ?",
            (json.dumps(items, ensure_ascii=False), user_id),
        )
        await db.commit()


class ChatControlsAndItemsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self.tmp.name) / "game.sqlite3"))
        await self.storage.init()
        self.engine = GameEngine(Settings("x"), self.storage)
        self.bot = FakeBot()

    async def asyncTearDown(self):
        for task in list(self.engine.tasks.values()) + list(self.engine.warning_tasks.values()):
            if not task.done():
                task.cancel()
        self.tmp.cleanup()

    async def ensure_users(self, *players):
        for p in players:
            await self.storage.ensure_profile(p.user_id, p.name, p.username)

    def test_chat_rules_keyboard_exposes_all_four_admin_toggles(self):
        kb = admin_chat_rules_keyboard(-1001, {
            "block_profanity": True,
            "block_stickers": False,
            "block_links": True,
            "vote_show_numbers": True,
        })
        buttons = [b for row in kb.inline_keyboard for b in row]
        data = {getattr(b, "callback_data", None) for b in buttons}
        self.assertIn("admin:chat_toggle:-1001:block_profanity", data)
        self.assertIn("admin:chat_toggle:-1001:block_stickers", data)
        self.assertIn("admin:chat_toggle:-1001:block_links", data)
        self.assertIn("admin:chat_toggle:-1001:vote_show_numbers", data)
        labels = [b.text for b in buttons]
        self.assertTrue(any("✅" in x and "мат" in x.lower() for x in labels))
        self.assertTrue(any("⬜" in x and "стикер" in x.lower() for x in labels))

    def test_vote_buttons_can_show_stable_number_plus_name(self):
        game = GameState(-1002, "city", phase=Phase.NOMINATION, day=2)
        game.players = {
            1: PlayerState(1, "Alpha", number=1, role_key="optimist"),
            2: PlayerState(2, "Bravo", number=2, role_key="optimist"),
            3: PlayerState(3, "Charlie", number=3, role_key="optimist"),
        }
        game.temp["_chat_settings"] = {"vote_show_numbers": True}
        kb = vote_keyboard(game, 1)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        self.assertIn("№02 · Bravo", labels)
        self.assertIn("№03 · Charlie", labels)

        game.temp["_chat_settings"] = {"vote_show_numbers": False}
        kb = vote_keyboard(game, 1)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        self.assertIn("Bravo", labels)
        self.assertFalse(any(label.startswith("№") for label in labels))

    def test_chat_settings_snapshot_survives_game_state_roundtrip(self):
        game = GameState(-1003, "city", phase=Phase.DISCUSSION, day=3)
        game.temp["_chat_settings"] = {
            "block_profanity": True,
            "block_stickers": True,
            "block_links": False,
            "vote_show_numbers": True,
            "role_thresholds": {"surgeon": 6, "bomber": 0},
        }
        restored = GameState.from_dict(game.to_dict())
        self.assertEqual(restored.temp["_chat_settings"], game.temp["_chat_settings"])

    def test_profanity_filter_is_conservative(self):
        self.assertTrue(_contains_profanity("блядь, опять ночь"))
        self.assertTrue(_contains_profanity("это охуенно подозрительно"))
        self.assertTrue(_contains_profanity("ну и мудак"))
        self.assertFalse(_contains_profanity("застрахуй автомобиль"))
        self.assertFalse(_contains_profanity("хулиган убежал"))

    def test_link_and_sticker_moderation(self):
        game = GameState(-1004, "city", phase=Phase.DISCUSSION, day=1)
        game.temp["_chat_settings"] = {
            "block_links": True,
            "block_stickers": True,
            "block_profanity": True,
        }
        self.assertTrue(_message_has_link(DummyMessage(text="смотри https://example.com/x")))
        self.assertTrue(_message_has_link(DummyMessage(text="t.me/test_channel")))
        self.assertIsNotNone(_moderation_reason(DummyMessage(sticker=object()), game))
        self.assertIn("ссылки", _moderation_reason(DummyMessage(text="www.example.com"), game))
        self.assertIn("мата", _moderation_reason(DummyMessage(text="сука"), game))
        self.assertIsNone(_moderation_reason(DummyMessage(text="обычное сообщение"), game))

    def test_infected_role_has_polished_public_name_without_key_migration(self):
        self.assertIn("carrier", ROLES)
        self.assertEqual(ROLES["carrier"].name, "Инфицированный")
        self.assertEqual(TEAMS["infected"]["name"], "Эпидемия")

    def test_shop_items_have_visible_mechanics_descriptions(self):
        for key in ["night_shield", "clean_papers", "antivirus", "perfume", "armor_piercing", "day_shield"]:
            self.assertTrue(ITEMS[key].get("description"), key)

    async def test_shop_double_tap_cannot_buy_two_items_with_money_for_one(self):
        user = PlayerState(11, "Buyer")
        await self.ensure_users(user)
        async with aiosqlite.connect(self.storage.path) as db:
            await db.execute("UPDATE profiles SET money = 150 WHERE user_id = ?", (user.user_id,))
            await db.commit()
        results = await asyncio.gather(
            self.storage.buy_item(user.user_id, "clean_papers"),
            self.storage.buy_item(user.user_id, "clean_papers"),
        )
        self.assertEqual(sum(1 for ok, _ in results if ok), 1)
        profile = await self.storage.get_profile(user.user_id)
        self.assertEqual(profile["money"], 0)
        self.assertEqual(profile["items"]["clean_papers"], 1)

    async def test_concurrent_item_consume_cannot_spend_one_copy_twice(self):
        user = PlayerState(12, "Consumer")
        await self.ensure_users(user)
        await grant_item(self.storage, user.user_id, "perfume", 1)
        results = await asyncio.gather(
            self.storage.consume_item(user.user_id, "perfume"),
            self.storage.consume_item(user.user_id, "perfume"),
        )
        self.assertEqual(results.count(True), 1)
        profile = await self.storage.get_profile(user.user_id)
        self.assertEqual(profile["items"]["perfume"], 0)

    async def test_clean_papers_lie_to_checker_and_tell_target_the_story(self):
        cop = PlayerState(21, "Cop", role_key="tracker")
        mafia = PlayerState(22, "Mafia", role_key="torpedo")
        await self.ensure_users(cop, mafia)
        await grant_item(self.storage, mafia.user_id, "clean_papers", 1)
        game = GameState(-1021, "city", mode="classic", phase=Phase.NIGHT, day=2)
        game.players = {cop.user_id: cop, mafia.user_id: mafia}
        game.actions = {
            cop.user_id: NightAction(cop.user_id, "check", mafia.user_id, actor_role_key="tracker")
        }
        await self.engine.resolve_night(self.bot, game)
        cop_text = "\n".join(m.text for m in self.bot.messages if m.chat_id == cop.user_id)
        mafia_text = "\n".join(m.text for m in self.bot.messages if m.chat_id == mafia.user_id)
        self.assertIn("Оптимист", cop_text)
        self.assertNotIn("Торпеда", cop_text)
        self.assertIn("Чистые документы", mafia_text)
        self.assertIn("уверен", mafia_text)
        profile = await self.storage.get_profile(mafia.user_id)
        self.assertEqual(profile["items"]["clean_papers"], 0)

    async def test_antivirus_blocks_hacker_and_notifies_both_sides(self):
        hacker = PlayerState(31, "Hacker", role_key="breacher")
        target = PlayerState(32, "Target", role_key="tracker")
        await self.ensure_users(hacker, target)
        await grant_item(self.storage, target.user_id, "antivirus", 1)
        game = GameState(-1031, "city", mode="classic", phase=Phase.NIGHT, day=2)
        game.players = {hacker.user_id: hacker, target.user_id: target}
        game.actions = {
            hacker.user_id: NightAction(hacker.user_id, "mafia_role_check", target.user_id, actor_role_key="breacher")
        }
        await self.engine.resolve_night(self.bot, game)
        hacker_text = "\n".join(m.text for m in self.bot.messages if m.chat_id == hacker.user_id)
        target_text = "\n".join(m.text for m in self.bot.messages if m.chat_id == target.user_id)
        self.assertIn("Доступ закрыт", hacker_text)
        self.assertIn("Антивирус", target_text)
        self.assertNotIn("Ищейка", hacker_text)

    async def test_perfume_cancels_block_and_tells_blocker_it_failed(self):
        diva = PlayerState(41, "Diva", role_key="night_diva")
        target = PlayerState(42, "Target", role_key="tracker")
        await self.ensure_users(diva, target)
        await grant_item(self.storage, target.user_id, "perfume", 1)
        game = GameState(-1041, "city", phase=Phase.NIGHT, day=2)
        game.players = {diva.user_id: diva, target.user_id: target}
        game.actions = {
            diva.user_id: NightAction(diva.user_id, "block_and_silence", target.user_id, actor_role_key="night_diva")
        }
        await self.engine.resolve_night(self.bot, game)
        self.assertFalse(target.blocked)
        self.assertFalse(target.silenced)
        diva_text = "\n".join(m.text for m in self.bot.messages if m.chat_id == diva.user_id)
        target_text = "\n".join(m.text for m in self.bot.messages if m.chat_id == target.user_id)
        self.assertIn("Цель исчезла", diva_text)
        self.assertIn("Дымный парфюм", target_text)

    async def test_black_bullet_bypasses_heal_and_night_shield_without_consuming_shield(self):
        killer = PlayerState(51, "Killer", role_key="carleone")
        doc = PlayerState(52, "Doc", role_key="surgeon")
        target = PlayerState(53, "Target", role_key="optimist")
        await self.ensure_users(killer, doc, target)
        await grant_item(self.storage, target.user_id, "night_shield", 1)
        game = GameState(-1051, "city", phase=Phase.NIGHT, day=1)
        game.players = {p.user_id: p for p in [killer, doc, target]}
        game.actions = {
            killer.user_id: NightAction(killer.user_id, "mafia_kill", target.user_id, item="armor_piercing", actor_role_key="carleone"),
            doc.user_id: NightAction(doc.user_id, "heal", target.user_id, actor_role_key="surgeon"),
        }
        deaths, _ = await self.engine.resolve_night(self.bot, game)
        self.assertFalse(target.alive)
        self.assertIn(target.user_id, [p.user_id for p, _ in deaths])
        profile = await self.storage.get_profile(target.user_id)
        self.assertEqual(profile["items"]["night_shield"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
