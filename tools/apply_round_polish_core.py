from pathlib import Path


def patch(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"{label}: source block not found in {path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# STORAGE: remember the previous role per user in this concrete group.
# ---------------------------------------------------------------------------
patch(
    "mafia_optimisma/storage.py",
    '''            try:\n                await db.execute(\n                    "ALTER TABLE chat_users ADD COLUMN notify_enabled INTEGER NOT NULL DEFAULT 0"\n                )\n            except Exception:\n                # Column already exists on upgraded databases.\n                pass\n            await db.execute(\n                """\n                CREATE TABLE IF NOT EXISTS game_sessions (''',
    '''            try:\n                await db.execute(\n                    "ALTER TABLE chat_users ADD COLUMN notify_enabled INTEGER NOT NULL DEFAULT 0"\n                )\n            except Exception:\n                # Column already exists on upgraded databases.\n                pass\n            try:\n                await db.execute("ALTER TABLE chat_users ADD COLUMN last_role TEXT")\n            except Exception:\n                # Column already exists on upgraded databases.\n                pass\n            await db.execute(\n                """\n                CREATE TABLE IF NOT EXISTS game_sessions (''',
    "chat_users.last_role migration",
)

patch(
    "mafia_optimisma/storage.py",
    '''    async def save_game_state(self, game) -> None:\n        """Persist one active game snapshot. The model owns JSON serialization."""''',
    '''    async def get_last_roles(self, chat_id: int, user_ids: list[int] | None = None) -> dict[int, str]:\n        params: list[object] = [chat_id]\n        where = "chat_id = ? AND last_role IS NOT NULL"\n        if user_ids:\n            marks = ",".join("?" for _ in user_ids)\n            where += f" AND user_id IN ({marks})"\n            params.extend(int(x) for x in user_ids)\n        async with aiosqlite.connect(self.path) as db:\n            db.row_factory = aiosqlite.Row\n            async with db.execute(\n                f"SELECT user_id, last_role FROM chat_users WHERE {where}", tuple(params)\n            ) as cur:\n                rows = await cur.fetchall()\n        return {int(row["user_id"]): str(row["last_role"]) for row in rows if row["last_role"]}\n\n    async def set_last_roles(self, chat_id: int, role_map: dict[int, str]) -> None:\n        if not role_map:\n            return\n        async with aiosqlite.connect(self.path) as db:\n            await db.executemany(\n                "UPDATE chat_users SET last_role = ?, updated_at = strftime('%s','now') "\n                "WHERE chat_id = ? AND user_id = ?",\n                [(str(role), chat_id, int(user_id)) for user_id, role in role_map.items()],\n            )\n            await db.commit()\n\n    async def save_game_state(self, game) -> None:\n        """Persist one active game snapshot. The model owns JSON serialization."""''',
    "last-role storage methods",
)

# ---------------------------------------------------------------------------
# ENGINE: anti-repeat assignment, correct win ordering, early night completion.
# ---------------------------------------------------------------------------
patch(
    "mafia_optimisma/engine.py",
    '''    def _assign_start_roles(self, game: GameState) -> None:\n        """Assign a complete fresh role pack before the start snapshot is persisted.\n\n        The assignment is intentionally done in memory as one step. If the process\n        dies before the following snapshot write, SQLite still contains the old\n        REGISTRATION state and the game can safely auto-start again. If the write\n        succeeds, a restored RESOLVING snapshot always has a complete role pack.\n        """\n        roles = generate_roles(game.mode, len(game.players))\n        players = list(game.players.values())\n        random.shuffle(players)\n        for p, role_key in zip(players, roles):''',
    '''    def _assign_start_roles(self, game: GameState, last_roles: dict[int, str] | None = None) -> None:\n        """Assign a complete fresh role pack with randomisation and anti-repeat.\n\n        Roles and players are independently shuffled.  When the same group plays\n        several rounds in a row we also minimise immediate role repeats, especially\n        special roles.  This is not a deterministic rotation: every candidate\n        assignment is still random, we simply choose the best of several shuffles.\n        """\n        base_roles = generate_roles(game.mode, len(game.players))\n        base_players = list(game.players.values())\n        last_roles = last_roles or {}\n\n        best_players = list(base_players)\n        best_roles = list(base_roles)\n        best_score = 10**9\n        for _ in range(96):\n            players = list(base_players)\n            roles = list(base_roles)\n            random.shuffle(players)\n            random.shuffle(roles)\n            score = 0\n            for player, role_key in zip(players, roles):\n                if last_roles.get(player.user_id) == role_key:\n                    # Repeating a special role is much more noticeable than being\n                    # an ordinary Optimist twice, so avoid it more aggressively.\n                    score += 5 if role_key != "optimist" else 1\n            if score < best_score:\n                best_score = score\n                best_players, best_roles = players, roles\n            if score == 0:\n                break\n\n        players, roles = best_players, best_roles\n        for p, role_key in zip(players, roles):''',
    "anti-repeat role assignment",
)

patch(
    "mafia_optimisma/engine.py",
    '''            game.temp["resume_action"] = "start_night"\n            self._assign_start_roles(game)\n            await self.persist(game)\n\n            task = self.tasks.pop(game.chat_id, None)''',
    '''            game.temp["resume_action"] = "start_night"\n            try:\n                last_roles = await self.storage.get_last_roles(\n                    game.chat_id, list(game.players.keys())\n                )\n            except Exception:\n                self.log.exception("Could not load previous roles chat=%s", game.chat_id)\n                last_roles = {}\n            self._assign_start_roles(game, last_roles)\n            await self.persist(game)\n            try:\n                await self.storage.set_last_roles(\n                    game.chat_id,\n                    {p.user_id: (p.role_key or "optimist") for p in game.players.values()},\n                )\n            except Exception:\n                # Variety memory is cosmetic; a DB hiccup must never block a start.\n                self.log.exception("Could not store previous roles chat=%s", game.chat_id)\n\n            task = self.tasks.pop(game.chat_id, None)''',
    "load/store previous role assignment",
)

old_end_night = '''    async def end_night(self, bot: Bot, game: GameState) -> None:\n        async with self.lock_for(game.chat_id):\n            if store.get(game.chat_id) is not game or game.phase != Phase.NIGHT:\n                return\n            await self._disable_pm_controls(bot, game.night_pm_message_ids)\n\n            deaths, public_events = await self.resolve_night(bot, game)\n            # Apply inheritance before the living-role summary, but announce it in\n            # the morning stream after deaths, matching the reference UI order.\n            promotions = self._inherit_roles(game)\n\n            await self._set_phase(game, Phase.DISCUSSION, self.settings.discussion_seconds)\n            await send_phase_sticker(bot, game.chat_id, "morning")\n            await self._safe_group(\n                bot,\n                game.chat_id,\n                f"🏙 <b>День {game.day}, город просыпается.</b>\\n"\n                f"До начала голосования {self.settings.discussion_seconds} секунд.",\n            )\n            if public_events:\n                await self._safe_group(bot, game.chat_id, "\\n".join(public_events))\n            if deaths:\n                for p, reason in deaths:\n                    # If a special death (e.g. bodyguard) wasn't already described\n                    # in the public event stream, provide a generic fallback.\n                    if not any(p.name in event for event in public_events):\n                        await self._safe_group(\n                            bot,\n                            game.chat_id,\n                            pick(GLOBAL["night_death"], name=player_link(p), role=role_title(p.role_key))\n                            + (f"\\n_{reason}_" if reason else ""),\n                        )\n                    game.pending_last_words.add(p.user_id)\n                    await self._safe_pm(bot, p.user_id, pick(GLOBAL["last_word_prompt"]))\n            else:\n                await self._safe_group(bot, game.chat_id, pick(GLOBAL["no_deaths"]))\n\n            await self._announce_promotions(bot, game, promotions)\n            await self._safe_group(bot, game.chat_id, living_summary(game, reveal_roles=True))\n            await self.persist(game)\n            winner = await self.check_win(bot, game)\n            if winner:\n                return\n            await self._safe_group(\n                bot,\n                game.chat_id,\n                f"💬 <b>Обсуждение началось.</b> До выдвижения кандидата — {self.settings.discussion_seconds} секунд.",\n            )\n            self._arm_phase_timer(game, self.settings.discussion_seconds, lambda: self.start_nomination(bot, game))\n'''
new_end_night = '''    async def end_night(self, bot: Bot, game: GameState) -> None:\n        async with self.lock_for(game.chat_id):\n            if store.get(game.chat_id) is not game or game.phase != Phase.NIGHT:\n                return\n            await self._disable_pm_controls(bot, game.night_pm_message_ids)\n\n            deaths, public_events = await self.resolve_night(bot, game)\n\n            # First publish only the events that actually happened during the night.\n            # Do NOT announce a new day yet: the final kill may already have created\n            # mafia parity / a town victory / a solo victory.\n            if public_events:\n                await self._safe_group(bot, game.chat_id, "\\n".join(public_events))\n            if deaths:\n                for p, reason in deaths:\n                    if not any(p.name in event for event in public_events):\n                        await self._safe_group(\n                            bot,\n                            game.chat_id,\n                            pick(GLOBAL["night_death"], name=player_link(p), role=role_title(p.role_key))\n                            + (f"\\n_{reason}_" if reason else ""),\n                        )\n            else:\n                await self._safe_group(bot, game.chat_id, pick(GLOBAL["no_deaths"]))\n\n            # Critical live-UX rule: if the night decided the game, finish RIGHT\n            # HERE. There must be no bogus «День 2», morning sticker or living list\n            # after the winning attack.\n            winner = await self.check_win(bot, game)\n            if winner:\n                return\n\n            # Only a genuinely continuing game gets a new day and last-word window.\n            promotions = self._inherit_roles(game)\n            await self._set_phase(game, Phase.DISCUSSION, self.settings.discussion_seconds)\n            await send_phase_sticker(bot, game.chat_id, "morning")\n            await self._safe_group(\n                bot,\n                game.chat_id,\n                f"🏙 <b>День {game.day}, город просыпается.</b>\\n"\n                f"До начала голосования {self.settings.discussion_seconds} секунд.",\n            )\n            for p, _reason in deaths:\n                game.pending_last_words.add(p.user_id)\n                await self._safe_pm(bot, p.user_id, pick(GLOBAL["last_word_prompt"]))\n\n            await self._announce_promotions(bot, game, promotions)\n            await self._safe_group(bot, game.chat_id, living_summary(game, reveal_roles=True))\n            await self.persist(game)\n            await self._safe_group(\n                bot,\n                game.chat_id,\n                f"💬 <b>Обсуждение началось.</b> До выдвижения кандидата — {self.settings.discussion_seconds} секунд.",\n            )\n            self._arm_phase_timer(game, self.settings.discussion_seconds, lambda: self.start_nomination(bot, game))\n'''
patch("mafia_optimisma/engine.py", old_end_night, new_end_night, "night winner before day")

# Do not print a living-player card after a daytime execution that already ended
# the game.  Keep the promotion mutation, but render it only if play continues.
patch(
    "mafia_optimisma/engine.py",
    '''            promotions = self._inherit_roles(game)\n            await self._announce_promotions(bot, game, promotions)\n            await self._safe_group(bot, game.chat_id, living_summary(game, reveal_roles=True))\n            if fatalist_wins:''',
    '''            promotions = self._inherit_roles(game)\n            if fatalist_wins:''',
    "defer verdict summary",
)
patch(
    "mafia_optimisma/engine.py",
    '''        if bomber:\n            await self.start_night(bot, game, allow_from_resolving=True)\n            return\n        winner = await self.check_win(bot, game)\n        if not winner:\n            await self.start_night(bot, game, allow_from_resolving=True)''',
    '''        if bomber:\n            await self._announce_promotions(bot, game, promotions)\n            await self._safe_group(bot, game.chat_id, living_summary(game, reveal_roles=True))\n            await self.start_night(bot, game, allow_from_resolving=True)\n            return\n        winner = await self.check_win(bot, game)\n        if winner:\n            return\n        await self._announce_promotions(bot, game, promotions)\n        await self._safe_group(bot, game.chat_id, living_summary(game, reveal_roles=True))\n        await self.start_night(bot, game, allow_from_resolving=True)''',
    "verdict winner before living summary",
)

# ---------------------------------------------------------------------------
# More varied, still-Optimist victory copy.
# ---------------------------------------------------------------------------
patch(
    "mafia_optimisma/content.py",
    '''    "win_town": [\n        "🎉 <b>Город победил!</b> Оптимисты выстояли, преступность отступила.",\n        "🏙 <b>Победа города!</b> Улицы снова безопасны. Почти.",\n    ],''',
    '''    "win_town": [\n        "🎉 <b>Город победил!</b> Оптимисты выстояли, преступность отступила.",\n        "🏙 <b>Победа города!</b> Улицы снова безопасны. Почти.",\n        "☀️ <b>Рассвет за Оптимистами!</b> Мафия закончилась раньше, чем вера в человечество.",\n        "😎 <b>Город выжил.</b> Подозревали всех, ошибались громко — но злодеев всё-таки вычислили.",\n        "🥂 <b>Оптимизм оказался сильнее мафии.</b> Сегодня кофе можно пить без проверки на яд.",\n        "🚓 <b>Преступность отменяется.</b> По крайней мере до следующей регистрации.",\n    ],''',
    "town win phrases",
)
patch(
    "mafia_optimisma/content.py",
    '''    "win_mafia": [\n        "🌑 <b>Семья Карлеоне победила!</b> Город улыбается, но уже по приказу.",\n        "🕴 <b>Мафия захватила город!</b> Теперь даже оптимизм платит дань.",\n    ],''',
    '''    "win_mafia": [\n        "🌑 <b>Семья Карлеоне победила!</b> Город улыбается, но уже по приказу.",\n        "🕴 <b>Мафия захватила город!</b> Теперь даже оптимизм платит дань.",\n        "🍷 <b>Карлеоне поднял бокал.</b> Свидетелей мало, вопросов ещё меньше.",\n        "😈 <b>Город перешёл под управление Семьи.</b> Оптимизм разрешён — по предварительной записи.",\n        "💼 <b>Мафия победила.</b> Новый мэр уже выбран. Вы его не выбирали.",\n        "🌘 <b>Семья выключила свет.</b> А вместе с ним — последние сомнения, кто здесь главный.",\n    ],''',
    "mafia win phrases",
)
patch(
    "mafia_optimisma/content.py",
    '''    "win_maniac": [\n        "🔪 <b>Потрошитель победил!</b> Он остался один, как и планировал.",\n        "⚰️ <b>Победа Потрошителя.</b> Свидетелей не осталось.",\n    ],''',
    '''    "win_maniac": [\n        "🔪 <b>Потрошитель победил!</b> Он остался один, как и планировал.",\n        "⚰️ <b>Победа Потрошителя.</b> Свидетелей не осталось.",\n        "🩸 <b>Один против города — и город проиграл.</b> Потрошитель забирает ночь себе.",\n        "🎭 <b>Финал сольного шоу.</b> Аплодировать, к сожалению, больше некому.",\n    ],''',
    "maniac win phrases",
)

print("ROUND POLISH CORE APPLIED")
