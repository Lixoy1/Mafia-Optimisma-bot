import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import test_core

from mafia_optimisma.keyboards import verdict_keyboard
from mafia_optimisma.models import GameState, Phase, PlayerState


class MutableVerdictTests(unittest.TestCase):
    def test_verdict_keyboard_has_execute_pardon_and_abstain(self):
        game = GameState(-1001, "city", phase=Phase.VERDICT, day=3, session_id="abc123")
        kb = verdict_keyboard(game)
        buttons = [button for row in kb.inline_keyboard for button in row]
        self.assertIn("👍 Казнить", [b.text for b in buttons])
        self.assertIn("👎 Помиловать", [b.text for b in buttons])
        self.assertIn("🤍 Воздержаться", [b.text for b in buttons])
        self.assertIn("verdict:abc123:-1001:3:abstain", [b.callback_data for b in buttons])

    def test_abstain_survives_game_state_roundtrip(self):
        game = GameState(1, "city", phase=Phase.VERDICT, day=2)
        game.verdict_votes = {10: True, 20: False, 30: None}
        restored = GameState.from_dict(game.to_dict())
        self.assertIs(restored.verdict_votes[10], True)
        self.assertIs(restored.verdict_votes[20], False)
        self.assertIsNone(restored.verdict_votes[30])

    def test_callback_allows_replacing_previous_choice(self):
        source = (ROOT / "mafia_optimisma" / "routers_callbacks.py").read_text(encoding="utf-8")
        section = source.split('@router.callback_query(F.data.startswith("verdict:"))', 1)[1].split('@router.callback_query(F.data.startswith("bomb:"))', 1)[0]
        self.assertIn('{"yes", "no", "abstain"}', section)
        self.assertIn('game.verdict_votes[voter.user_id] = current', section)
        self.assertIn('Решение изменено:', section)
        self.assertNotIn('Твой голос уже принят.', section)

    def test_result_counts_abstain_separately(self):
        source = (ROOT / "mafia_optimisma" / "engine.py").read_text(encoding="utf-8")
        self.assertIn('if value is True', source)
        self.assertIn('if value is False', source)
        self.assertIn('if value is None', source)
        self.assertIn('Воздержались', source)

    def test_no_vote_is_distinct_from_explicit_abstain(self):
        game = GameState(2, "city", phase=Phase.VERDICT, day=1)
        game.players = {1: PlayerState(1, "A", role_key="optimist"), 2: PlayerState(2, "B", role_key="optimist")}
        game.verdict_votes[1] = None
        self.assertIn(1, game.verdict_votes)
        self.assertNotIn(2, game.verdict_votes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
