import asyncio
import ast
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import test_core  # installs offline dependency stubs

from mafia_optimisma.config import Settings
from mafia_optimisma.engine import GameEngine, living_summary
from mafia_optimisma.models import GameState, Phase, PlayerState


class OutputFormattingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.storage = test_core.FakeStorage()
        self.engine = GameEngine(Settings('x'), self.storage)
        self.bot = test_core.FakeBot()

    def test_living_summary_escapes_html_sensitive_player_name(self):
        g = GameState(1, 'chat', phase=Phase.NIGHT, day=1)
        g.players = {1: PlayerState(1, 'A <B> & C', number=1, role_key='optimist')}
        text = living_summary(g)
        self.assertIn('A &lt;B&gt; &amp; C', text)
        self.assertNotIn('A <B> & C', text)
        self.assertIn('<b>Живые игроки</b>', text)
        self.assertIn('tg://user?id=1', text)

    async def test_role_card_uses_html_and_escapes_teammate_name(self):
        g = GameState(2, 'chat', phase=Phase.RESOLVING, day=0)
        g.players = {
            1: PlayerState(1, 'Boss', number=1, role_key='carleone'),
            2: PlayerState(2, '<Torpedo & Co>', number=2, role_key='torpedo'),
        }
        await self.engine._send_roles(self.bot, g)
        boss_msg = next(m.text for m in self.bot.messages if m.chat_id == 1)
        self.assertIn('<b>Ты — 🤵🏻 Карлеоне!</b>', boss_msg)
        self.assertIn('<i>Глава Семьи Карлеоне.', boss_msg)
        self.assertIn('&lt;Torpedo &amp; Co&gt;', boss_msg)

    async def test_team_chat_escapes_user_supplied_html(self):
        g = GameState(3, 'chat', phase=Phase.NIGHT, day=1)
        g.players = {
            1: PlayerState(1, '<Boss>', number=1, role_key='carleone'),
            2: PlayerState(2, 'Mate', number=2, role_key='torpedo'),
        }
        ok = await self.engine.team_chat(self.bot, g, g.players[1], '<b>не HTML</b> & hello')
        self.assertTrue(ok)
        text = self.bot.messages[-1].text
        self.assertIn('&lt;Boss&gt;', text)
        self.assertIn('&lt;b&gt;не HTML&lt;/b&gt; &amp; hello', text)

    async def test_last_word_escapes_html(self):
        g = GameState(4, 'chat', phase=Phase.DISCUSSION, day=1)
        p = PlayerState(1, '<Dead>', number=1, role_key='optimist', alive=False)
        g.players = {1: p}
        g.pending_last_words = {1}
        msg = types.SimpleNamespace(text='<i>последнее</i> & слово')
        handled = await self.engine.handle_last_word(self.bot, msg, g, p)
        self.assertTrue(handled)
        public = next(m.text for m in self.bot.messages if m.chat_id == g.chat_id)
        self.assertIn('&lt;Dead&gt;', public)
        self.assertIn('&lt;i&gt;последнее&lt;/i&gt; &amp; слово', public)
        private = [m.text for m in self.bot.messages if m.chat_id == p.user_id]
        self.assertTrue(any('Последнее слово принято' in text for text in private))

    def test_legacy_markdown_markers_are_gone_from_user_text_literals(self):
        for name in ['content.py', 'engine.py', 'routers_group.py', 'routers_private.py', 'routers_callbacks.py']:
            source = (ROOT / 'mafia_optimisma' / name).read_text()
            tree = ast.parse(source)
            bad = [
                node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and '**' in node.value
            ]
            self.assertEqual(bad, [], name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
