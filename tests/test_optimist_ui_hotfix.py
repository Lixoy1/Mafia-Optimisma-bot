import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import test_core  # installs the offline aiogram/aiosqlite stubs

from mafia_optimisma.config import Settings
from mafia_optimisma.engine import GameEngine, living_summary, player_link
from mafia_optimisma.models import GameState, NightAction, Phase, PlayerState


class OptimistUiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.storage = test_core.FakeStorage()
        self.engine = GameEngine(Settings("x"), self.storage)
        self.bot = test_core.FakeBot()

    def test_player_link_is_clickable_and_html_safe(self):
        p = PlayerState(777, "A <B> & C", number=1, role_key="optimist")
        link = player_link(p)
        self.assertIn('href="tg://user?id=777"', link)
        self.assertIn("A &lt;B&gt; &amp; C", link)
        self.assertNotIn("A <B> & C", link)

    def test_living_summary_uses_clickable_rows_and_role_rows(self):
        game = GameState(1, "city", phase=Phase.NIGHT, day=1)
        game.players = {
            10: PlayerState(10, "First", number=1, role_key="optimist"),
            20: PlayerState(20, "Second", number=2, role_key="surgeon"),
        }
        text = living_summary(game)
        self.assertIn('tg://user?id=10', text)
        self.assertIn('tg://user?id=20', text)
        self.assertIn("<b>01</b> ·", text)
        self.assertIn("🎭 <b>Роли в городе</b>", text)
        self.assertIn("×1", text)

    async def test_nomination_and_verdict_phase_cleanup_deletes_remaining_pm_cards(self):
        mapping = {1: 101, 2: 202}
        await self.engine._delete_pm_controls(self.bot, mapping)
        self.assertEqual(mapping, {})
        self.assertIn(("delete", 1, 101), self.bot.ops)
        self.assertIn(("delete", 2, 202), self.bot.ops)

    async def test_doctor_save_is_a_public_aesthetic_event_with_profile_link(self):
        game = GameState(2, "city", mode="classic", phase=Phase.NIGHT, day=1)
        game.players = {
            1: PlayerState(1, "Boss", number=1, role_key="carleone", initial_role_key="carleone"),
            2: PlayerState(2, "Doctor", number=2, role_key="surgeon", initial_role_key="surgeon"),
            3: PlayerState(3, "Target", number=3, role_key="optimist", initial_role_key="optimist"),
        }
        game.actions = {
            1: NightAction(1, "mafia_kill", target_id=3, actor_role_key="carleone"),
            2: NightAction(2, "heal", target_id=3, actor_role_key="surgeon"),
        }
        deaths, events = await self.engine.resolve_night(self.bot, game)
        self.assertEqual(deaths, [])
        text = "\n".join(events)
        self.assertIn("Хирург успел вовремя", text)
        self.assertIn('tg://user?id=3', text)
        self.assertTrue(game.players[3].alive)

    def test_callback_source_deletes_vote_cards_immediately_after_choice(self):
        source = (ROOT / "mafia_optimisma" / "routers_callbacks.py").read_text(encoding="utf-8")
        self.assertIn("game.nomination_pm_message_ids.pop(voter.user_id, None)", source)
        self.assertIn("nomination_prompt_id or getattr(callback.message, \"message_id\", None)", source)
        self.assertIn("game.verdict_pm_message_ids.pop(voter.user_id, None)", source)
        self.assertIn("verdict_prompt_id or getattr(callback.message, \"message_id\", None)", source)

    def test_rankings_render_clickable_profile_links(self):
        source = (ROOT / "mafia_optimisma" / "rankings.py").read_text(encoding="utf-8")
        self.assertIn("def _profile_link", source)
        self.assertIn('tg://user?id=', source)
        self.assertIn("_profile_link(row)", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
