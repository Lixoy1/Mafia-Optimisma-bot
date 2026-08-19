from pathlib import Path


def replace_once(text: str, old: str, new: str, marker: str) -> str:
    if marker in text:
        return text
    if old not in text:
        raise SystemExit(f"optimist UI hotfix target not found: {marker}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Engine UI: clickable Telegram profiles, cleaner summaries/events, and delete
# ephemeral PM voting cards instead of leaving dead keyboards in chat history.
# ---------------------------------------------------------------------------
engine_path = Path("mafia_optimisma/engine.py")
engine = engine_path.read_text(encoding="utf-8")

if "def player_link(player: PlayerState) -> str:" not in engine:
    anchor = '''def role_team(role_key: str | None) -> str:\n    return ROLES[role_key or "optimist"].team\n\n\n'''
    helper = '''def role_team(role_key: str | None) -> str:\n    return ROLES[role_key or "optimist"].team\n\n\ndef player_link(player: PlayerState) -> str:\n    """Safe clickable Telegram profile link for public/private game messages."""\n    return f'<a href="tg://user?id={int(player.user_id)}">{escape(player.name)}</a>'\n\n\ndef player_link_by_id(game: GameState, user_id: int) -> str:\n    player = game.get_player(int(user_id))\n    return player_link(player) if player else "—"\n\n\n'''
    if anchor not in engine:
        raise SystemExit("optimist UI hotfix target not found: player link helper")
    engine = engine.replace(anchor, helper, 1)

old_summary = '''def living_summary(game: GameState, reveal_roles: bool = True) -> str:\n    """Public Black-Mafia-style summary: stable player numbers + role counts.\n\n    We reveal how many living copies of each role remain, never who owns them.\n    Player numbers are assigned at registration and are never renumbered.\n    """\n    lines = ["\\n<b>Живые игроки:</b>"]\n    for p in sorted(game.alive_players(), key=lambda x: (x.number or 10**9, x.user_id)):\n        number = p.number or 0\n        lines.append(f"{number}) {escape(p.name)}")\n    if reveal_roles:\n        counts = Counter(role_title(p.role_key) for p in game.alive_players())\n        if counts:\n            lines.append("\\n<b>Роли:</b> " + "; ".join(f"{role} — {count}" for role, count in counts.items()))\n    lines.append(f"\\n<b>Кол-во игроков:</b> {len(game.alive_players())}")\n    return "\\n".join(lines)\n'''
new_summary = '''def living_summary(game: GameState, reveal_roles: bool = True) -> str:\n    """Compact Optimist UI: stable slots, clickable players and one role per row."""\n    alive = sorted(game.alive_players(), key=lambda x: (x.number or 10**9, x.user_id))\n    lines = ["👥 <b>Живые игроки</b>", "━━━━━━━━━━━━"]\n    for p in alive:\n        number = p.number or 0\n        lines.append(f"<b>{number:02d}</b> · {player_link(p)}")\n    if reveal_roles:\n        counts = Counter(role_title(p.role_key) for p in alive)\n        if counts:\n            lines += ["", "🎭 <b>Роли в городе</b>"]\n            for role, count in counts.items():\n                lines.append(f"• {role}  ×{count}")\n    lines += ["", f"🌆 <b>В игре:</b> {len(alive)}"]\n    return "\\n".join(lines)\n'''
if "Compact Optimist UI" not in engine:
    if old_summary not in engine:
        raise SystemExit("optimist UI hotfix target not found: living summary")
    engine = engine.replace(old_summary, new_summary, 1)

if "async def _delete_pm_controls" not in engine:
    anchor = '''    async def _disable_pm_controls(self, bot: Bot, mapping: dict[int, int]) -> None:\n        for user_id, message_id in list(mapping.items()):\n            await self._safe_disable(bot, user_id, message_id)\n        mapping.clear()\n\n'''
    replacement = '''    async def _disable_pm_controls(self, bot: Bot, mapping: dict[int, int]) -> None:\n        for user_id, message_id in list(mapping.items()):\n            await self._safe_disable(bot, user_id, message_id)\n        mapping.clear()\n\n    async def _delete_pm_controls(self, bot: Bot, mapping: dict[int, int]) -> None:\n        """Delete short-lived voting cards so PM history does not become cluttered."""\n        for user_id, message_id in list(mapping.items()):\n            await self._safe_delete(bot, user_id, message_id)\n        mapping.clear()\n\n'''
    if anchor not in engine:
        raise SystemExit("optimist UI hotfix target not found: pm cleanup helper")
    engine = engine.replace(anchor, replacement, 1)

# Teammates and public fallback deaths use clickable people too.
engine = engine.replace(
    '                    f"{escape(m.name)} — {role_title(m.role_key)}" for m in teammates\n',
    '                    f"{player_link(m)} — {role_title(m.role_key)}" for m in teammates\n',
    1,
)
engine = engine.replace(
    '                            pick(GLOBAL["night_death"], name=escape(p.name), role=role_title(p.role_key))\n',
    '                            pick(GLOBAL["night_death"], name=player_link(p), role=role_title(p.role_key))\n',
    1,
)

# Morning attack/event cards: keep the same information contract, but make it
# much easier to scan and tap a player profile.
old_attack = '''        def attack_public_text(a: NightAction, target: PlayerState) -> str:\n            attacker_role = action_role_key(a)\n            if a.action_type == "solo_kill":\n                return f"{role_title(attacker_role)} устроил ночной кошмар для {escape(target.name)} — {role_title(target.role_key)}"\n            if a.action_type == "shoot":\n                return f"{role_title(attacker_role)} выстрелил в {escape(target.name)} — {role_title(target.role_key)}"\n            if a.action_type == "yakuza_kill":\n                return f"{role_title(attacker_role)} расправился с {escape(target.name)} — {role_title(target.role_key)}"\n            return f"{role_title(attacker_role)} беспощадно убил {escape(target.name)} — {role_title(target.role_key)}"\n'''
new_attack = '''        def attack_public_text(a: NightAction, target: PlayerState) -> str:\n            attacker_role = action_role_key(a)\n            verb = {\n                "solo_kill": "устроил ночной кошмар",\n                "shoot": "открыл огонь",\n                "yakuza_kill": "нанёс удар",\n            }.get(a.action_type, "атаковал")\n            return (\n                f"🔻 <b>Ночной удар</b>\\n"\n                f"{role_title(attacker_role)} {verb}: {player_link(target)}\\n"\n                f"🎭 Роль цели: <b>{role_title(target.role_key)}</b>"\n            )\n'''
if "🔻 <b>Ночной удар</b>" not in engine:
    if old_attack not in engine:
        raise SystemExit("optimist UI hotfix target not found: attack card")
    engine = engine.replace(old_attack, new_attack, 1)

engine = engine.replace(
    '        saved_by_heal: set[int] = set()\n        attacked_ids: set[int] = set()\n',
    '        saved_by_heal: set[int] = set()\n        attacked_ids: set[int] = set()\n        protection_announced: set[tuple[str, int]] = set()\n',
    1,
)

old_guard = '''                    public_events.append(\n                        f"{role_title(guard.role_key)} погиб, защищая {escape(target.name)}"\n                    )\n'''
new_guard = '''                    public_events.append(\n                        "🛡 <b>Защита сработала</b>\\n"\n                        f"{player_link(guard)} прикрыл(а) {player_link(target)} и погиб(ла)."\n                    )\n'''
if old_guard in engine:
    engine = engine.replace(old_guard, new_guard, 1)

old_protection = '''            if not armor and target.user_id in healed:\n                saved_by_heal.add(target.user_id)\n                await self._safe_pm(bot, actor.user_id, "🩺 Цель пережила нападение.")\n                continue\n            if not armor and await self._consume_game_item_safe(\n                game, target.user_id, "night_shield", f"night_shield:{a.actor_id}:{a.action_type}:{a.target_id}"\n            ):\n                await self._safe_pm(bot, target.user_id, "🛡 Ночной оберег спас тебя от смерти.")\n                continue\n            if not armor and target.role_key == "lucky" and random.randint(1, 100) <= 75:\n                await self._safe_pm(bot, target.user_id, "🍀 Сегодня удача спасла тебя от смерти.")\n                continue\n'''
new_protection = '''            if not armor and target.user_id in healed:\n                saved_by_heal.add(target.user_id)\n                if ("heal", target.user_id) not in protection_announced:\n                    protection_announced.add(("heal", target.user_id))\n                    public_events.append(\n                        "🩺 <b>Хирург успел вовремя</b>\\n"\n                        f"{player_link(target)} пережил(а) ночное нападение."\n                    )\n                await self._safe_pm(bot, actor.user_id, "🩺 Цель пережила нападение.")\n                continue\n            if not armor and await self._consume_game_item_safe(\n                game, target.user_id, "night_shield", f"night_shield:{a.actor_id}:{a.action_type}:{a.target_id}"\n            ):\n                if ("shield", target.user_id) not in protection_announced:\n                    protection_announced.add(("shield", target.user_id))\n                    public_events.append(\n                        "🛡 <b>Ночной оберег вспыхнул</b>\\n"\n                        f"{player_link(target)} пережил(а) смертельную атаку."\n                    )\n                await self._safe_pm(bot, target.user_id, "🛡 Ночной оберег спас тебя от смерти.")\n                continue\n            if not armor and target.role_key == "lucky" and random.randint(1, 100) <= 75:\n                if ("lucky", target.user_id) not in protection_announced:\n                    protection_announced.add(("lucky", target.user_id))\n                    public_events.append(\n                        "🍀 <b>Фортуна улыбнулась</b>\\n"\n                        f"{player_link(target)} чудом избежал(а) гибели."\n                    )\n                await self._safe_pm(bot, target.user_id, "🍀 Сегодня удача спасла тебя от смерти.")\n                continue\n'''
if "🛡 <b>Ночной оберег вспыхнул</b>" not in engine:
    if old_protection not in engine:
        raise SystemExit("optimist UI hotfix target not found: protection cards")
    engine = engine.replace(old_protection, new_protection, 1)

engine = engine.replace(
    '                    f"💣 Подрывник забрал с собой {escape(bomb_target.name)} — {role_title(bomb_target.role_key)}"\n',
    '                    "💥 <b>Последний сюрприз Подрывника</b>\\n"\n                    f"Взрыв зацепил {player_link(bomb_target)} — <b>{role_title(bomb_target.role_key)}</b>"\n',
    1,
)

# Nomination/verdict controls are ephemeral: remove every unclicked PM card when
# the phase closes. Players who clicked are removed from the mapping immediately.
engine = engine.replace(
    '            await self._disable_pm_controls(bot, game.nomination_pm_message_ids)\n',
    '            await self._delete_pm_controls(bot, game.nomination_pm_message_ids)\n',
    1,
)
engine = engine.replace(
    '            await self._disable_pm_controls(bot, game.verdict_pm_message_ids)\n',
    '            await self._delete_pm_controls(bot, game.verdict_pm_message_ids)\n',
    1,
)

# Tie/verdict/public judgement cards get clickable names and cleaner rows.
engine = engine.replace(
    '                    names = [escape(game.get_player(uid).name) for uid in leaders if game.get_player(uid)]\n',
    '                    names = [player_link(game.get_player(uid)) for uid in leaders if game.get_player(uid)]\n',
    1,
)
engine = engine.replace(
    '                    f"⚖️ <b>Город решает судьбу {escape(candidate.name)}.</b>\\n"\n',
    '                    f"⚖️ <b>Город решает судьбу</b> {player_link(candidate)}\\n"\n',
    1,
)
engine = engine.replace(
    '            yes_names = ", ".join(escape(game.get_player(uid).name) for uid in yes_voters if game.get_player(uid)) or "—"\n            no_names = ", ".join(escape(game.get_player(uid).name) for uid in no_voters if game.get_player(uid)) or "—"\n',
    '            yes_names = "\\n".join(f"• {player_link(game.get_player(uid))}" for uid in yes_voters if game.get_player(uid)) or "• —"\n            no_names = "\\n".join(f"• {player_link(game.get_player(uid))}" for uid in no_voters if game.get_player(uid)) or "• —"\n',
    1,
)
old_verdict_result = '''                f"🗳 <b>Результат голосования:</b>\\n"\n                f"👍 Да ({len(yes_voters)}): {yes_names}\\n"\n                f"👎 Нет ({len(no_voters)}): {no_names}",\n'''
new_verdict_result = '''                "⚖️ <b>Вердикт города</b>\\n"\n                "━━━━━━━━━━━━\\n"\n                f"👍 <b>За казнь — {len(yes_voters)}</b>\\n{yes_names}\\n\\n"\n                f"👎 <b>За помилование — {len(no_voters)}</b>\\n{no_names}",\n'''
if "⚖️ <b>Вердикт города</b>" not in engine:
    if old_verdict_result not in engine:
        raise SystemExit("optimist UI hotfix target not found: verdict result")
    engine = engine.replace(old_verdict_result, new_verdict_result, 1)
engine = engine.replace(
    'await self._safe_group(bot, game.chat_id, f"⚖️ Солнечный иммунитет спас {escape(candidate.name)} от казни.")',
    'await self._safe_group(bot, game.chat_id, f"☀️ <b>Солнечный иммунитет</b>\\n{player_link(candidate)} избежал(а) казни.")',
    1,
)
engine = engine.replace(
    '                        pick(GLOBAL["lynch"], name=escape(candidate.name), role=role_title(candidate.role_key)),\n',
    '                        pick(GLOBAL["lynch"], name=player_link(candidate), role=role_title(candidate.role_key)),\n',
    1,
)
engine = engine.replace(
    '                await self._safe_group(bot, game.chat_id, f"🕊 <b>{escape(candidate.name)} помилован(а).</b>")\n',
    '                await self._safe_group(bot, game.chat_id, f"🕊 <b>Город помиловал</b> {player_link(candidate)}.")\n',
    1,
)

engine_path.write_text(engine, encoding="utf-8")


# ---------------------------------------------------------------------------
# Callback UI: accepted nomination/verdict cards vanish immediately. Group vote
# feed uses profile links rather than flat plain text.
# ---------------------------------------------------------------------------
cb_path = Path("mafia_optimisma/routers_callbacks.py")
cb = cb_path.read_text(encoding="utf-8")
cb = cb.replace(
    "from .engine import GameEngine, living_summary, pick, role_team, role_title\n",
    "from .engine import GameEngine, living_summary, pick, player_link, role_team, role_title\n",
    1,
)

# Nomination: pop the prompt id before persistence and delete the card outside lock.
if "nomination_prompt_id = None" not in cb:
    cb = cb.replace(
        '    group_text = None\n    async with engine.lock_for(chat_id):\n',
        '    group_text = None\n    nomination_prompt_id = None\n    async with engine.lock_for(chat_id):\n',
        1,
    )
    cb = cb.replace(
        '            group_text = pick(GLOBAL["vote_skip"], name=escape(voter.name))\n            answer_text = "Ты решил(а) ни за кого не голосовать."\n',
        '            group_text = f"🤍 {player_link(voter)} <i>воздержался(ась) от выдвижения.</i>"\n            answer_text = "Ты решил(а) ни за кого не голосовать."\n',
        1,
    )
    cb = cb.replace(
        '            group_text = pick(GLOBAL["vote_cast"], voter=escape(voter.name), target=escape(target.name))\n            answer_text = "Голос принят."\n        group_id = game.chat_id\n',
        '            group_text = (\n                "🗳 <b>Голос принят</b>\\n"\n                f"{player_link(voter)}  →  🎯 {player_link(target)}"\n            )\n            answer_text = "Голос принят."\n        nomination_prompt_id = game.nomination_pm_message_ids.pop(voter.user_id, None)\n        await engine.persist(game)\n        group_id = game.chat_id\n',
        1,
    )
    # Remove the earlier persists inside branches; persistence now happens once after prompt pop.
    cb = cb.replace(
        '            game.votes[voter.user_id] = None\n            await engine.persist(game)\n',
        '            game.votes[voter.user_id] = None\n',
        1,
    )
    cb = cb.replace(
        '            game.votes[voter.user_id] = target_id\n            await engine.persist(game)\n',
        '            game.votes[voter.user_id] = target_id\n',
        1,
    )
    cb = cb.replace(
        '''    try:\n        await callback.message.edit_reply_markup(reply_markup=None)\n    except Exception:\n        pass\n    try:\n        await callback.bot.send_message(group_id, group_text)\n''',
        '''    await engine._safe_delete(\n        callback.bot, callback.from_user.id,\n        nomination_prompt_id or getattr(callback.message, "message_id", None),\n    )\n    try:\n        await callback.bot.send_message(group_id, group_text)\n''',
        1,
    )

# Verdict: same immediate-delete lifecycle.
if "verdict_prompt_id = None" not in cb:
    verdict_anchor = '''@router.callback_query(F.data.startswith("verdict:"))\nasync def cb_verdict(callback: CallbackQuery):\n    assert engine\n'''
    cb = cb.replace(
        verdict_anchor,
        verdict_anchor + '    verdict_prompt_id = None\n',
        1,
    )
    cb = cb.replace(
        '        game.verdict_votes[voter.user_id] = value == "yes"\n        await engine.persist(game)\n\n    await callback.answer("👍 За казнь" if value == "yes" else "👎 За помилование")\n    try:\n        await callback.message.edit_reply_markup(reply_markup=None)\n    except Exception:\n        pass\n',
        '        game.verdict_votes[voter.user_id] = value == "yes"\n        verdict_prompt_id = game.verdict_pm_message_ids.pop(voter.user_id, None)\n        await engine.persist(game)\n\n    await callback.answer("👍 За казнь" if value == "yes" else "👎 За помилование")\n    await engine._safe_delete(\n        callback.bot, callback.from_user.id,\n        verdict_prompt_id or getattr(callback.message, "message_id", None),\n    )\n',
        1,
    )

# Team action payloads are private but benefit from the same tappable profile UI.
cb = cb.replace(
    '                team_payload = f"{role_title(player.role_key)} {escape(player.name)} выбрал(а) {escape(target.name)}"\n',
    '                team_payload = f"{role_title(player.role_key)} {player_link(player)} выбрал(а) {player_link(target)}"\n',
    1,
)
cb_path.write_text(cb, encoding="utf-8")


# ---------------------------------------------------------------------------
# Rankings: names in weekly/full TOPs are clickable too.
# ---------------------------------------------------------------------------
rank_path = Path("mafia_optimisma/rankings.py")
rank = rank_path.read_text(encoding="utf-8")
if "def _profile_link(row:" not in rank:
    anchor = '''def _pct(value: float) -> str:\n    text = f"{value:.2f}".rstrip("0").rstrip(".")\n    return text or "0"\n\n\n'''
    replacement = '''def _pct(value: float) -> str:\n    text = f"{value:.2f}".rstrip("0").rstrip(".")\n    return text or "0"\n\n\ndef _profile_link(row: dict[str, Any]) -> str:\n    name = escape(str(row.get("name") or row.get("username") or row.get("user_id") or "Игрок"))\n    return f'<a href="tg://user?id={int(row["user_id"])}">{name}</a>'\n\n\n'''
    if anchor not in rank:
        raise SystemExit("optimist UI hotfix target not found: ranking link helper")
    rank = rank.replace(anchor, replacement, 1)
rank = rank.replace(
    '            f"{escape(str(row[\'name\']))} ({_pct(float(row[\'win_rate\']))} %)"\n',
    '            f"{_profile_link(row)} ({_pct(float(row[\'win_rate\']))} %)"\n',
)
rank = rank.replace(
    '                    f"{escape(str(row[\'name\']))} — {row[\'money\']} 💵" for row in group\n',
    '                    f"{_profile_link(row)} — {row[\'money\']} 💵" for row in group\n',
)
rank = rank.replace(
    '            f"{i}) {marker} {escape(str(row[\'name\']))} "\n',
    '            f"{i}) {marker} {_profile_link(row)} "\n',
)
rank = rank.replace(
    '                f"{i}) {escape(str(row[\'name\']))} ({_pct(float(row[\'win_rate\']))} %) "\n',
    '                f"{i}) {_profile_link(row)} ({_pct(float(row[\'win_rate\']))} %) "\n',
)
rank_path.write_text(rank, encoding="utf-8")

print("polished Optimist UI + ephemeral voting controls applied")
