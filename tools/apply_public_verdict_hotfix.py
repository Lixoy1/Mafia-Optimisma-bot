from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"{label}: source block not found in {path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


ENGINE = "mafia_optimisma/engine.py"
CALLBACKS = "mafia_optimisma/routers_callbacks.py"
CORE_TEST = "tests/test_core.py"
UI_TEST = "tests/test_optimist_ui_hotfix.py"

# User-facing phase timers are floats internally so accelerated tests can use
# fractions of a second. Human-facing text should still say 30, 45, 60 instead
# of 30.0, 45.0, 60.0.
replace_once(
    ENGINE,
    '''    def _feature(self, game: GameState | None, key: str, fallback: bool) -> bool:\n''',
    '''    @staticmethod\n    def _seconds_text(value: int | float) -> str:\n        try:\n            number = float(value)\n        except (TypeError, ValueError):\n            return str(value)\n        if number.is_integer():\n            return str(int(number))\n        return f"{number:g}"\n\n    def _feature(self, game: GameState | None, key: str, fallback: bool) -> bool:\n''',
    "seconds display helper",
)

for old, new, label in [
    ('f"До окончания ночи остается {night_seconds} секунд.\\n\\n"', 'f"До окончания ночи остается {self._seconds_text(night_seconds)} секунд.\\n\\n"', "night time display"),
    ('f"До начала голосования {discussion_seconds} секунд.\\n"', 'f"До начала голосования {self._seconds_text(discussion_seconds)} секунд.\\n"', "day time display"),
    ('f"💬 <b>Обсуждение началось.</b> До выдвижения кандидата — {discussion_seconds} секунд."', 'f"💬 <b>Обсуждение началось.</b> До выдвижения кандидата — {self._seconds_text(discussion_seconds)} секунд."', "discussion time display"),
    ('f"У города {nomination_seconds} секунд, чтобы выбрать подозреваемого.\\n"', 'f"У города {self._seconds_text(nomination_seconds)} секунд, чтобы выбрать подозреваемого.\\n"', "nomination time display"),
    ('f"До конца решения — {verdict_seconds} секунд.\\n\\n"', 'f"До конца решения — {self._seconds_text(verdict_seconds)} секунд.\\n\\n"', "verdict time display"),
]:
    replace_once(ENGINE, old, new, label)

# Verdict controls are public/shared. On restart, restore the keyboard on the
# existing group message, or recreate one group verdict card if it disappeared.
replace_once(
    ENGINE,
    '''    async def _ensure_restored_verdict_controls(self, bot: Bot, game: GameState) -> None:\n        from .keyboards import verdict_keyboard\n        candidate = game.get_player(game.nominated_id or 0)\n        if not candidate or not candidate.alive:\n            return\n        changed = False\n        for p in game.alive_players():\n            if p.user_id == candidate.user_id or p.silenced:\n                continue\n            if p.user_id in game.verdict_votes or p.user_id in game.verdict_pm_message_ids:\n                continue\n            msg = await self._safe_pm(\n                bot, p.user_id, f"⚖️ Казнить {escape(candidate.name)}?", reply_markup=verdict_keyboard(game)\n            )\n            if msg:\n                game.verdict_pm_message_ids[p.user_id] = msg.message_id\n                changed = True\n        if changed:\n            await self.persist(game)\n''',
    '''    async def _ensure_restored_verdict_controls(self, bot: Bot, game: GameState) -> None:\n        from .keyboards import verdict_keyboard\n        candidate = game.get_player(game.nominated_id or 0)\n        if not candidate or not candidate.alive:\n            return\n\n        # Compatibility with snapshots created by the old private-verdict build.\n        await self._delete_pm_controls(bot, game.verdict_pm_message_ids)\n        markup = verdict_keyboard(game)\n        if game.verdict_message_id:\n            try:\n                await bot.edit_message_reply_markup(\n                    chat_id=game.chat_id,\n                    message_id=game.verdict_message_id,\n                    reply_markup=markup,\n                )\n                await self.persist(game)\n                return\n            except Exception:\n                self.log.warning(\n                    "Could not restore verdict markup chat=%s message=%s; recreating card",\n                    game.chat_id, game.verdict_message_id,\n                )\n\n        msg = await self._safe_group(\n            bot,\n            game.chat_id,\n            f"⚖️ <b>Город решает судьбу</b> {player_link(candidate)}\\n"\n            "Голосование продолжается после перезапуска.\\n\\n"\n            "👍 Казнить или 👎 Помиловать?",\n            reply_markup=markup,\n        )\n        if msg:\n            game.verdict_message_id = msg.message_id\n        await self.persist(game)\n''',
    "restore group verdict controls",
)

# New verdict: one message in the game chat with two shared buttons. No private
# copies are sent to each player.
replace_once(
    ENGINE,
    '''                msg = await self._safe_group(\n                    bot,\n                    game.chat_id,\n                    f"⚖️ <b>Город решает судьбу</b> {player_link(candidate)}\\n"\n                    f"До конца решения — {self._seconds_text(verdict_seconds)} секунд.\\n\\n"\n                    "👍 Казнить или 👎 Помиловать?",\n                )\n                if msg:\n                    game.verdict_message_id = msg.message_id\n\n                game.verdict_pm_message_ids.clear()\n                for p in game.alive_players():\n                    if p.user_id == candidate.user_id or p.silenced:\n                        continue\n                    try:\n                        pm = await bot.send_message(\n                            p.user_id,\n                            f"⚖️ Казнить {escape(candidate.name)}?",\n                            reply_markup=verdict_keyboard(game),\n                        )\n                        if pm:\n                            game.verdict_pm_message_ids[p.user_id] = pm.message_id\n                    except Exception:\n                        continue\n                await self.persist(game)\n''',
    '''                await self._delete_pm_controls(bot, game.verdict_pm_message_ids)\n                msg = await self._safe_group(\n                    bot,\n                    game.chat_id,\n                    f"⚖️ <b>Город решает судьбу</b> {player_link(candidate)}\\n"\n                    f"До конца решения — {self._seconds_text(verdict_seconds)} секунд.\\n\\n"\n                    "👍 Казнить или 👎 Помиловать?",\n                    reply_markup=verdict_keyboard(game),\n                )\n                if msg:\n                    game.verdict_message_id = msg.message_id\n                await self.persist(game)\n''',
    "public verdict card",
)

# A verdict button now lives on one shared group message. Never delete the source
# message after one person's vote, otherwise the first voter would remove the
# buttons for the whole city.
replace_once(
    CALLBACKS,
    '''async def cb_verdict(callback: CallbackQuery):\n    assert engine\n    verdict_prompt_id = None\n''',
    '''async def cb_verdict(callback: CallbackQuery):\n    assert engine\n''',
    "remove private verdict prompt state",
)
replace_once(
    CALLBACKS,
    '''        game.verdict_votes[voter.user_id] = value == "yes"\n        verdict_prompt_id = game.verdict_pm_message_ids.pop(voter.user_id, None)\n        await engine.persist(game)\n\n    await callback.answer("👍 За казнь" if value == "yes" else "👎 За помилование")\n    await engine._safe_delete(\n        callback.bot, callback.from_user.id,\n        verdict_prompt_id or getattr(callback.message, "message_id", None),\n    )\n''',
    '''        game.verdict_votes[voter.user_id] = value == "yes"\n        await engine.persist(game)\n\n    await callback.answer("👍 За казнь" if value == "yes" else "👎 За помилование")\n''',
    "keep shared verdict card after vote",
)

# Update historical tests to the new public-verdict contract.
replace_once(
    CORE_TEST,
    '''            self.assertNotIn(2, g.verdict_pm_message_ids)\n            self.assertEqual(set(g.verdict_pm_message_ids), {1, 3, 4})\n''',
    '''            self.assertEqual(g.verdict_pm_message_ids, {})\n            self.assertIsNotNone(g.verdict_message_id)\n            verdict_messages = [\n                m for m in self.bot.messages\n                if m.chat_id == g.chat_id and m.reply_markup is not None\n            ]\n            self.assertTrue(verdict_messages)\n''',
    "core public verdict start assertion",
)
replace_once(
    CORE_TEST,
    '''            self.assertEqual(set(fixed.verdict_pm_message_ids), {1, 3})\n''',
    '''            self.assertEqual(fixed.verdict_pm_message_ids, {})\n            self.assertIsNotNone(fixed.verdict_message_id)\n            verdict_messages = [\n                m for m in self.bot.messages\n                if m.chat_id == g.chat_id and m.reply_markup is not None\n            ]\n            self.assertTrue(verdict_messages)\n''',
    "core restored public verdict assertion",
)
replace_once(
    UI_TEST,
    '''    def test_callback_source_deletes_vote_cards_immediately_after_choice(self):\n        source = (ROOT / "mafia_optimisma" / "routers_callbacks.py").read_text(encoding="utf-8")\n        self.assertIn("game.nomination_pm_message_ids.pop(voter.user_id, None)", source)\n        self.assertIn("nomination_prompt_id or getattr(callback.message, \\\"message_id\\\", None)", source)\n        self.assertIn("game.verdict_pm_message_ids.pop(voter.user_id, None)", source)\n        self.assertIn("verdict_prompt_id or getattr(callback.message, \\\"message_id\\\", None)", source)\n''',
    '''    def test_nomination_is_private_but_verdict_card_is_shared(self):\n        source = (ROOT / "mafia_optimisma" / "routers_callbacks.py").read_text(encoding="utf-8")\n        self.assertIn("game.nomination_pm_message_ids.pop(voter.user_id, None)", source)\n        self.assertIn("nomination_prompt_id or getattr(callback.message, \\\"message_id\\\", None)", source)\n        verdict_section = source.split('@router.callback_query(F.data.startswith("verdict:"))', 1)[1]\n        verdict_section = verdict_section.split('@router.callback_query(F.data.startswith("bomb:"))', 1)[0]\n        self.assertNotIn("game.verdict_pm_message_ids.pop", verdict_section)\n        self.assertNotIn("engine._safe_delete", verdict_section)\n''',
    "shared verdict callback assertion",
)

print("PUBLIC VERDICT + TIMER DISPLAY HOTFIX APPLIED")
