import unittest
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from mafia_optimisma.protocol import ACTION_CODES, encode_action, decode_action


class CallbackProtocolTests(unittest.TestCase):
    def test_action_codes_are_unique(self):
        self.assertEqual(len(ACTION_CODES), len(set(ACTION_CODES.values())))

    def test_all_compact_actions_roundtrip(self):
        for action, token in ACTION_CODES.items():
            with self.subTest(action=action, token=token):
                self.assertEqual(encode_action(action), token)
                self.assertEqual(decode_action(token), action)

    def test_legacy_full_action_token_is_backward_compatible(self):
        for action in ACTION_CODES:
            with self.subTest(action=action):
                self.assertEqual(decode_action(action), action)


if __name__ == '__main__':
    unittest.main(verbosity=2)
