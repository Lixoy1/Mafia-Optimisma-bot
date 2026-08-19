import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import test_core  # installs offline aiogram/aiosqlite stubs

from mafia_optimisma.config import Settings
from mafia_optimisma.engine import GameEngine
from mafia_optimisma.models import GameState, Phase, PlayerState
from mafia_optimisma.state import store


class PublicVerdictHotfixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store.games.clear()
        store.user_to_chat.clear()
        self.storage = test_core.FakeStorage()
        self.engine = GameEngine(
            Settings(
                "x",
                nomination_seconds=30,
                verdict_seconds=20,
                discussion_seconds=45,
                night_seconds=60,
            ),
            self.storage,
        )
        self.bot = test_core.FakeBot()

    async def asyncTearDown(self):
        for task in list(self.engine.tasks.values()) + list(self.engine.warning_tasks.values()):
            if not task.done():
                task.cancel()

    def test_seconds_text_hides_trailing_zero(self):
        self.assertEqual(self.engine._seconds_text(30.0), "30")
        self.assertEqual(self.engine._seconds_text(45), "45")
        self.assertEqual(self.engine._seconds_text(0.25), "0.25")

    async def test_nomination_message_says_30_not_30_point_zero(self):
        game = GameState(8101, "city", mode="classic", phase=Phase.DISCUSSION, day=1)
        game.players = {
            1: PlayerState(1, "A", number=1, role_key="carleone"),
            2: PlayerState(2, "B", number=2, role_key="surgeon"),
            3: PlayerState(3, "C", number=3, role_key="optimist"),
        }
        store.games[game.chat_id] = game
        await self.engine.start_nomination(self.bot, game)
        group_text = "\n".join(m.text for m in self.bot.messages if m.chat_id == game.chat_id)
        self.assertIn("У города 30 секунд", group_text)
        self.assertNotIn("30.0 секунд", group_text)

    async def test_verdict_buttons_are_on_one_group_message_not_in_pm(self):
        game = GameState(8102, "city", mode="classic", phase=Phase.RESOLVING, day=1)
        game.started_at = time.time()
        game.nominated_id = 2
        game.players = {
            1: PlayerState(1, "A", number=1, role_key="carleone"),
            2: PlayerState(2, "B", number=2, role_key="surgeon"),
            3: PlayerState(3, "C", number=3, role_key="optimist"),
        }
        store.games[game.chat_id] = game
        await self.engine.start_verdict(self.bot, game)

        self.assertEqual(game.phase, Phase.VERDICT)
        self.assertEqual(game.verdict_pm_message_ids, {})
        group_cards = [
            m for m in self.bot.messages
            if m.chat_id == game.chat_id and m.reply_markup is not None
        ]
        self.assertEqual(len(group_cards), 1)
        texts = [
            button.text
            for row in group_cards[0].reply_markup.inline_keyboard
            for button in row
        ]
        self.assertEqual(texts, ["👍 Казнить", "👎 Помиловать"])
        self.assertIn("20 секунд", group_cards[0].text)
        self.assertNotIn("20.0 секунд", group_cards[0].text)
        private_verdict_cards = [
            m for m in self.bot.messages
            if m.chat_id in {1, 2, 3} and m.reply_markup is not None
        ]
        self.assertEqual(private_verdict_cards, [])

    def test_verdict_callback_does_not_delete_shared_group_card(self):
        source = (ROOT / "mafia_optimisma" / "routers_callbacks.py").read_text(encoding="utf-8")
        verdict_section = source.split('@router.callback_query(F.data.startswith("verdict:"))', 1)[1]
        verdict_section = verdict_section.split('@router.callback_query(F.data.startswith("bomb:"))', 1)[0]
        self.assertNotIn("game.verdict_pm_message_ids.pop", verdict_section)
        self.assertNotIn("engine._safe_delete", verdict_section)


if __name__ == "__main__":
    unittest.main(verbosity=2)
