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
# Persistent per-chat configuration. Stored settings affect the NEXT game only;
# an active GameState keeps a snapshot in temp['_chat_settings'].
# ---------------------------------------------------------------------------
patch(
    "mafia_optimisma/storage.py",
    '''            await db.execute(\n                """\n                CREATE TABLE IF NOT EXISTS item_events (''',
    '''            await db.execute(\n                """\n                CREATE TABLE IF NOT EXISTS chat_settings (\n                    chat_id INTEGER PRIMARY KEY,\n                    settings_json TEXT NOT NULL DEFAULT '{}',\n                    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))\n                )\n                """\n            )\n            await db.execute(\n                """\n                CREATE TABLE IF NOT EXISTS item_events (''',
    "chat_settings table",
)

patch(
    "mafia_optimisma/storage.py",
    '''    async def save_game_state(self, game) -> None:\n        """Persist one active game snapshot. The model owns JSON serialization."""''',
    '''    async def get_chat_settings(self, chat_id: int) -> dict[str, Any]:\n        async with aiosqlite.connect(self.path) as db:\n            db.row_factory = aiosqlite.Row\n            async with db.execute(\n                "SELECT settings_json FROM chat_settings WHERE chat_id = ?", (chat_id,)\n            ) as cur:\n                row = await cur.fetchone()\n        if not row:\n            return {}\n        try:\n            data = json.loads(row["settings_json"] or "{}")\n            return data if isinstance(data, dict) else {}\n        except Exception:\n            return {}\n\n    async def set_chat_settings(self, chat_id: int, settings: dict[str, Any]) -> None:\n        payload = json.dumps(settings or {}, ensure_ascii=False, separators=(",", ":"))\n        async with aiosqlite.connect(self.path) as db:\n            await db.execute(\n                """\n                INSERT INTO chat_settings (chat_id, settings_json, updated_at)\n                VALUES (?, ?, strftime('%s','now'))\n                ON CONFLICT(chat_id) DO UPDATE SET\n                    settings_json = excluded.settings_json,\n                    updated_at = strftime('%s','now')\n                """,\n                (chat_id, payload),\n            )\n            await db.commit()\n\n    async def set_chat_setting(self, chat_id: int, key: str, value: Any) -> dict[str, Any]:\n        settings = await self.get_chat_settings(chat_id)\n        if value is None:\n            settings.pop(key, None)\n        else:\n            settings[key] = value\n        await self.set_chat_settings(chat_id, settings)\n        return settings\n\n    async def reset_chat_settings(self, chat_id: int) -> None:\n        async with aiosqlite.connect(self.path) as db:\n            await db.execute("DELETE FROM chat_settings WHERE chat_id = ?", (chat_id,))\n            await db.commit()\n\n    async def save_game_state(self, game) -> None:\n        """Persist one active game snapshot. The model owns JSON serialization."""''',
    "chat settings storage API",
)

# ---------------------------------------------------------------------------
# Role thresholds: admins may override a role's minimum player count or disable it.
# Core roles (Optimist / Carleone / generic Torpedo) stay governed by the mode.
# ---------------------------------------------------------------------------
patch(
    "mafia_optimisma/engine.py",
    '''def generate_roles(mode: str, count: int) -> list[str]:\n    """Build a balanced role pack for the selected ruleset.''',
    '''def generate_roles(\n    mode: str, count: int, role_thresholds: dict[str, int] | None = None\n) -> list[str]:\n    """Build a balanced role pack for the selected ruleset.''',
    "generate_roles signature",
)

patch(
    "mafia_optimisma/engine.py",
    '''    if mode == "clans":\n        roles = [role for min_p, role in CLANS_SEQUENCE if count >= min_p]''',
    '''    role_thresholds = role_thresholds or {}\n\n    def unlocked(default_min: int, role_key: str) -> bool:\n        raw = role_thresholds.get(role_key)\n        if raw is None:\n            threshold = default_min\n        else:\n            try:\n                threshold = int(raw)\n            except (TypeError, ValueError):\n                threshold = default_min\n            if threshold <= 0:\n                return False\n        return count >= threshold\n\n    if mode == "clans":\n        roles = [role for min_p, role in CLANS_SEQUENCE if unlocked(min_p, role)]''',
    "role threshold resolver",
)

patch(
    "mafia_optimisma/engine.py",
    '''        unlocked = [role for min_p, role in CLASSIC_THRESHOLDS if count >= min_p]\n        fixed_town = ["surgeon"] + (["tracker"] if count >= 6 else [])''',
    '''        unlocked_roles = [role for min_p, role in CLASSIC_THRESHOLDS if unlocked(min_p, role)]\n        fixed_town = []\n        if unlocked(3, "surgeon"):\n            fixed_town.append("surgeon")\n        if unlocked(6, "tracker"):\n            fixed_town.append("tracker")''',
    "classic configurable roles",
)
patch(
    "mafia_optimisma/engine.py",
    '''        unlocked = [role for min_p, role in CHAOS_THRESHOLDS if count >= min_p]\n        fixed_town = []''',
    '''        unlocked_roles = [role for min_p, role in CHAOS_THRESHOLDS if unlocked(min_p, role)]\n        fixed_town = []''',
    "chaos configurable roles",
)
patch(
    "mafia_optimisma/engine.py",
    '''        unlocked = [role for min_p, role in VIRUS_THRESHOLDS if count >= min_p]\n        fixed_town = []''',
    '''        unlocked_roles = [role for min_p, role in VIRUS_THRESHOLDS if unlocked(min_p, role)]\n        fixed_town = []''',
    "virus configurable roles",
)
patch(
    "mafia_optimisma/engine.py",
    '''        unlocked = []\n        fixed_town = ["surgeon"] + (["tracker"] if count >= 6 else [])\n\n    mafia_specials = [role for role in unlocked if role_team(role) == "mafia"]\n    non_mafia_unlocked = [role for role in unlocked if role_team(role) != "mafia"]''',
    '''        unlocked_roles = []\n        fixed_town = []\n        if unlocked(3, "surgeon"):\n            fixed_town.append("surgeon")\n        if unlocked(6, "tracker"):\n            fixed_town.append("tracker")\n\n    mafia_specials = [role for role in unlocked_roles if role_team(role) == "mafia"]\n    non_mafia_unlocked = [role for role in unlocked_roles if role_team(role) != "mafia"]''',
    "role list variable rename",
)

# Game-scoped config helpers.
patch(
    "mafia_optimisma/engine.py",
    '''    def lock_for(self, chat_id: int) -> asyncio.Lock:\n        lock = self.locks.get(chat_id)\n        if lock is None:\n            lock = asyncio.Lock()\n            self.locks[chat_id] = lock\n        return lock\n\n    async def persist(self, game: GameState) -> None:''',
    '''    def lock_for(self, chat_id: int) -> asyncio.Lock:\n        lock = self.locks.get(chat_id)\n        if lock is None:\n            lock = asyncio.Lock()\n            self.locks[chat_id] = lock\n        return lock\n\n    def _game_config(self, game: GameState | None) -> dict:\n        if not game:\n            return {}\n        raw = game.temp.get("_chat_settings", {})\n        return raw if isinstance(raw, dict) else {}\n\n    def _duration(self, game: GameState, key: str, fallback: int) -> int:\n        raw = self._game_config(game).get(key, fallback)\n        try:\n            value = int(raw)\n        except (TypeError, ValueError):\n            value = int(fallback)\n        return max(5, min(600, value))\n\n    def _feature(self, game: GameState | None, key: str, fallback: bool) -> bool:\n        raw = self._game_config(game).get(key, fallback)\n        if isinstance(raw, str):\n            return raw.strip().lower() not in {"0", "false", "off", "no"}\n        return bool(raw)\n\n    async def persist(self, game: GameState) -> None:''',
    "runtime chat settings helpers",
)

# Snapshot group settings when registration begins.
patch(
    "mafia_optimisma/engine.py",
    '''    async def begin_registration(self, bot: Bot, game: GameState) -> None:\n        await self._set_phase(game, Phase.REGISTRATION, self.settings.registration_seconds)\n        await self.public_registration_message(bot, game)''',
    '''    async def begin_registration(self, bot: Bot, game: GameState) -> None:\n        try:\n            game.temp["_chat_settings"] = await self.storage.get_chat_settings(game.chat_id)\n        except Exception:\n            self.log.exception("Could not load chat settings chat=%s", game.chat_id)\n            game.temp["_chat_settings"] = {}\n        registration_seconds = self._duration(\n            game, "registration_seconds", self.settings.registration_seconds\n        )\n        await self._set_phase(game, Phase.REGISTRATION, registration_seconds)\n        await self.public_registration_message(bot, game)''',
    "registration settings snapshot",
)
patch(
    "mafia_optimisma/engine.py",
    '''            game,\n            self.settings.registration_seconds,\n            lambda: self.auto_start_registration(bot, game),''',
    '''            game,\n            registration_seconds,\n            lambda: self.auto_start_registration(bot, game),''',
    "registration custom duration timer",
)

# Keep settings when per-night transient temp data is cleared.
patch(
    "mafia_optimisma/engine.py",
    '''            game.nominated_id = None\n            game.temp.clear()\n            game.armor_piercing_pending.clear()''',
    '''            game.nominated_id = None\n            chat_settings = dict(self._game_config(game))\n            game.temp.clear()\n            game.temp["_chat_settings"] = chat_settings\n            game.armor_piercing_pending.clear()''',
    "preserve settings across nights",
)

# Assignment reads role-threshold snapshot created at registration.
patch(
    "mafia_optimisma/engine.py",
    '''        base_roles = generate_roles(game.mode, len(game.players))''',
    '''        role_thresholds = self._game_config(game).get("role_thresholds", {})\n        if not isinstance(role_thresholds, dict):\n            role_thresholds = {}\n        base_roles = generate_roles(game.mode, len(game.players), role_thresholds)''',
    "role assignment thresholds",
)

# Per-chat phase timings.
patch(
    "mafia_optimisma/engine.py",
    '''            await self._set_phase(game, Phase.NIGHT, self.settings.night_seconds)\n\n            await send_phase_sticker(bot, game.chat_id, "night")''',
    '''            night_seconds = self._duration(game, "night_seconds", self.settings.night_seconds)\n            await self._set_phase(game, Phase.NIGHT, night_seconds)\n\n            await send_phase_sticker(bot, game.chat_id, "night")''',
    "custom night timing",
)
patch(
    "mafia_optimisma/engine.py",
    '''                f"До окончания ночи остается {self.settings.night_seconds} секунд.\\n\\n"''',
    '''                f"До окончания ночи остается {night_seconds} секунд.\\n\\n"''',
    "night timing text",
)
patch(
    "mafia_optimisma/engine.py",
    '''            self._arm_phase_timer(game, self.settings.night_seconds, lambda: self.end_night(bot, game))''',
    '''            self._arm_phase_timer(game, night_seconds, lambda: self.end_night(bot, game))''',
    "night timing timer",
)

# Core hotfix has already rewritten end_night before this generator runs.
patch(
    "mafia_optimisma/engine.py",
    '''            promotions = self._inherit_roles(game)\n            await self._set_phase(game, Phase.DISCUSSION, self.settings.discussion_seconds)\n            await send_phase_sticker(bot, game.chat_id, "morning")''',
    '''            promotions = self._inherit_roles(game)\n            discussion_seconds = self._duration(\n                game, "discussion_seconds", self.settings.discussion_seconds\n            )\n            await self._set_phase(game, Phase.DISCUSSION, discussion_seconds)\n            await send_phase_sticker(bot, game.chat_id, "morning")''',
    "custom discussion timing",
)
patch(
    "mafia_optimisma/engine.py",
    '''                f"До начала голосования {self.settings.discussion_seconds} секунд.",''',
    '''                f"До начала голосования {discussion_seconds} секунд.",''',
    "discussion timing text",
)
patch(
    "mafia_optimisma/engine.py",
    '''                f"💬 <b>Обсуждение началось.</b> До выдвижения кандидата — {self.settings.discussion_seconds} секунд.",\n            )\n            self._arm_phase_timer(game, self.settings.discussion_seconds, lambda: self.start_nomination(bot, game))''',
    '''                f"💬 <b>Обсуждение началось.</b> До выдвижения кандидата — {discussion_seconds} секунд.",\n            )\n            self._arm_phase_timer(game, discussion_seconds, lambda: self.start_nomination(bot, game))''',
    "discussion timer",
)

patch(
    "mafia_optimisma/engine.py",
    '''            await self._set_phase(game, Phase.NOMINATION, self.settings.nomination_seconds)\n            await send_phase_sticker(bot, game.chat_id, "voting")''',
    '''            nomination_seconds = self._duration(\n                game, "nomination_seconds", self.settings.nomination_seconds\n            )\n            await self._set_phase(game, Phase.NOMINATION, nomination_seconds)\n            await send_phase_sticker(bot, game.chat_id, "voting")''',
    "custom nomination timing",
)
patch(
    "mafia_optimisma/engine.py",
    '''                f"У города {self.settings.nomination_seconds} секунд, чтобы выбрать подозреваемого.\\n"''',
    '''                f"У города {nomination_seconds} секунд, чтобы выбрать подозреваемого.\\n"''',
    "nomination timing text",
)
patch(
    "mafia_optimisma/engine.py",
    '''            self._arm_phase_timer(game, self.settings.nomination_seconds, lambda: self.end_nomination(bot, game))''',
    '''            self._arm_phase_timer(game, nomination_seconds, lambda: self.end_nomination(bot, game))''',
    "nomination timer",
)

patch(
    "mafia_optimisma/engine.py",
    '''                await self._set_phase(game, Phase.VERDICT, self.settings.verdict_seconds)\n                from .keyboards import verdict_keyboard''',
    '''                verdict_seconds = self._duration(\n                    game, "verdict_seconds", self.settings.verdict_seconds\n                )\n                await self._set_phase(game, Phase.VERDICT, verdict_seconds)\n                from .keyboards import verdict_keyboard''',
    "custom verdict timing",
)
patch(
    "mafia_optimisma/engine.py",
    '''                    f"До конца решения — {self.settings.verdict_seconds} секунд.\\n\\n"''',
    '''                    f"До конца решения — {verdict_seconds} секунд.\\n\\n"''',
    "verdict timing text",
)
patch(
    "mafia_optimisma/engine.py",
    '''                self._arm_phase_timer(game, self.settings.verdict_seconds, lambda: self.end_verdict(bot, game))''',
    '''                self._arm_phase_timer(game, verdict_seconds, lambda: self.end_verdict(bot, game))''',
    "verdict timer",
)

# Protect private game messages from forwarding/copying when the admin enables it.
patch(
    "mafia_optimisma/engine.py",
    '''    async def _safe_pm(self, bot: Bot, user_id: int, text: str, **kwargs):\n        try:\n            return await bot.send_message(user_id, text, **kwargs)''',
    '''    async def _safe_pm(self, bot: Bot, user_id: int, text: str, **kwargs):\n        game = store.game_by_user(user_id)\n        if game and self._feature(game, "protect_private_content", False):\n            kwargs.setdefault("protect_content", True)\n        try:\n            return await bot.send_message(user_id, text, **kwargs)''',
    "protected private game content",
)

# Optional TrueMafia-style fast night: once every player with a real night keyboard
# has submitted a final action, resolve immediately instead of idling to timeout.
patch(
    "mafia_optimisma/engine.py",
    '''    def _inherit_roles(self, game: GameState) -> list[tuple[PlayerState, str]]:''',
    '''    def _required_night_actor_ids(self, game: GameState) -> set[int]:\n        required: set[int] = set()\n        for player in game.alive_players():\n            if night_action_keyboard(game, player) is not None:\n                required.add(player.user_id)\n        if game.bomb_pending_for and not game.bomb_used:\n            bomber = game.get_player(game.bomb_pending_for)\n            if bomber and not bomber.alive and bomber.role_key == "bomber":\n                required.add(bomber.user_id)\n        return required\n\n    async def maybe_finish_night_early(self, bot: Bot, game: GameState) -> bool:\n        should_finish = False\n        async with self.lock_for(game.chat_id):\n            if store.get(game.chat_id) is not game or game.phase != Phase.NIGHT:\n                return False\n            if not self._feature(game, "early_night_finish", True):\n                return False\n            required = self._required_night_actor_ids(game)\n            completed = set(game.actions.keys())\n            if game.bomb_pending_for and game.bomb_used:\n                completed.add(game.bomb_pending_for)\n            if required and required.issubset(completed):\n                timer = self.tasks.get(game.chat_id)\n                if timer and timer is not asyncio.current_task() and not timer.done():\n                    timer.cancel()\n                should_finish = True\n        if should_finish:\n            await self.end_night(bot, game)\n        return should_finish\n\n    def _inherit_roles(self, game: GameState) -> list[tuple[PlayerState, str]]:''',
    "early night completion helper",
)

# ---------------------------------------------------------------------------
# Call early-night completion after every accepted FINAL night action.
# ---------------------------------------------------------------------------
patch(
    "mafia_optimisma/routers_callbacks.py",
    '''    if team_payload and game_snapshot and player_snapshot:\n        await engine._notify_team(callback.bot, game_snapshot, player_snapshot, team_payload, attribution=False)\n\n@router.callback_query(F.data.startswith("n2:"))''',
    '''    if team_payload and game_snapshot and player_snapshot:\n        await engine._notify_team(callback.bot, game_snapshot, player_snapshot, team_payload, attribution=False)\n    if game_snapshot:\n        await engine.maybe_finish_night_early(callback.bot, game_snapshot)\n\n@router.callback_query(F.data.startswith("n2:"))''',
    "finish night after single-target actions",
)
patch(
    "mafia_optimisma/routers_callbacks.py",
    '''        role_phrase = pick(role.chat_action_phrases) if role.chat_action_phrases else None\n        group_id = game.chat_id\n\n    await callback.answer("Действие принято.")''',
    '''        role_phrase = pick(role.chat_action_phrases) if role.chat_action_phrases else None\n        group_id = game.chat_id\n        game_snapshot = game\n\n    await callback.answer("Действие принято.")''',
    "capture two-target game",
)
patch(
    "mafia_optimisma/routers_callbacks.py",
    '''    if role_phrase:\n        try:\n            await callback.bot.send_message(group_id, role_phrase)\n        except Exception:\n            pass\n\n@router.callback_query(F.data.startswith("noop:"))''',
    '''    if role_phrase:\n        try:\n            await callback.bot.send_message(group_id, role_phrase)\n        except Exception:\n            pass\n    await engine.maybe_finish_night_early(callback.bot, game_snapshot)\n\n@router.callback_query(F.data.startswith("noop:"))''',
    "finish night after two-target actions",
)
patch(
    "mafia_optimisma/routers_callbacks.py",
    '''    try:\n        await callback.message.answer(f"Ты выбрал(а): <b>{escape(target_name or '')}</b>")\n    except Exception:\n        pass\n\nasync def _admin_panel_payload''',
    '''    try:\n        await callback.message.answer(f"Ты выбрал(а): <b>{escape(target_name or '')}</b>")\n    except Exception:\n        pass\n    await engine.maybe_finish_night_early(callback.bot, game)\n\nasync def _admin_panel_payload''',
    "finish night after bomber revenge",
)

# ---------------------------------------------------------------------------
# Private admin menu, inspired by the clean category structure in the references.
# ---------------------------------------------------------------------------
old_admin_keyboards = '''def admin_settings_keyboard(chat_id: int) -> InlineKeyboardMarkup:\n    return InlineKeyboardMarkup(inline_keyboard=[\n        [\n            InlineKeyboardButton(text="🎮 Режим игры", callback_data=f"admin:mode_menu:{chat_id}"),\n            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin:refresh:{chat_id}"),\n        ],\n        [\n            InlineKeyboardButton(text="▶️ Запустить", callback_data=f"admin:start:{chat_id}"),\n            InlineKeyboardButton(text="⏱ +30 сек", callback_data=f"admin:extend:{chat_id}"),\n        ],\n        [\n            InlineKeyboardButton(text="👥 Игроки", callback_data=f"admin:players:{chat_id}"),\n            InlineKeyboardButton(text="📣 Созыв", callback_data=f"admin:call:{chat_id}"),\n        ],\n        [\n            InlineKeyboardButton(text="🏆 Неделя", callback_data=f"admin:weekly:{chat_id}"),\n            InlineKeyboardButton(text="📊 Статистика", callback_data=f"admin:stats:{chat_id}"),\n        ],\n        [InlineKeyboardButton(text="🚫 Отменить регистрацию", callback_data=f"admin:cancel:{chat_id}")],\n    ])\n\n\ndef admin_mode_keyboard(chat_id: int) -> InlineKeyboardMarkup:'''
new_admin_keyboards = '''CONFIGURABLE_ROLE_KEYS = [\n    "surgeon", "tracker", "fatalist", "wanderer", "night_diva", "breacher",\n    "shield", "bomber", "shadow", "cadet", "lucky", "butcher",\n    "mercy_sister", "reporter", "alibi_master", "werewolf", "joker", "carrier",\n]\n\n\ndef admin_settings_keyboard(chat_id: int) -> InlineKeyboardMarkup:\n    return InlineKeyboardMarkup(inline_keyboard=[\n        [\n            InlineKeyboardButton(text="🎭 Роли", callback_data=f"admin:roles:{chat_id}"),\n            InlineKeyboardButton(text="⏱ Тайминги", callback_data=f"admin:timings:{chat_id}"),\n        ],\n        [\n            InlineKeyboardButton(text="🙊 Чат игры", callback_data=f"admin:chat_rules:{chat_id}"),\n            InlineKeyboardButton(text="🎮 Режимы игр", callback_data=f"admin:mode_menu:{chat_id}"),\n        ],\n        [InlineKeyboardButton(text="🛠 Разное", callback_data=f"admin:misc:{chat_id}")],\n        [\n            InlineKeyboardButton(text="▶️ Запустить", callback_data=f"admin:start:{chat_id}"),\n            InlineKeyboardButton(text="⏱ +30 сек", callback_data=f"admin:extend:{chat_id}"),\n        ],\n        [\n            InlineKeyboardButton(text="👥 Игроки", callback_data=f"admin:players:{chat_id}"),\n            InlineKeyboardButton(text="📣 Созыв", callback_data=f"admin:call:{chat_id}"),\n        ],\n        [\n            InlineKeyboardButton(text="🏆 Неделя", callback_data=f"admin:weekly:{chat_id}"),\n            InlineKeyboardButton(text="📊 Статистика", callback_data=f"admin:stats:{chat_id}"),\n        ],\n        [InlineKeyboardButton(text="🔄 Обновить панель", callback_data=f"admin:refresh:{chat_id}")],\n        [InlineKeyboardButton(text="♻️ Сбросить настройки", callback_data=f"admin:reset:{chat_id}")],\n        [InlineKeyboardButton(text="🚫 Отменить регистрацию", callback_data=f"admin:cancel:{chat_id}")],\n    ])\n\n\ndef admin_roles_keyboard(chat_id: int, overrides: dict | None = None) -> InlineKeyboardMarkup:\n    overrides = overrides or {}\n    rows = []\n    for key in CONFIGURABLE_ROLE_KEYS:\n        role = ROLES.get(key)\n        if not role:\n            continue\n        value = overrides.get(key)\n        suffix = ""\n        if value is not None:\n            try:\n                ivalue = int(value)\n                suffix = " · выкл" if ivalue <= 0 else f" · с {ivalue}"\n            except Exception:\n                pass\n        rows.append([InlineKeyboardButton(\n            text=f"{role.emoji} {role.title}{suffix}",\n            callback_data=f"admin:role:{chat_id}:{key}",\n        )])\n    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:refresh:{chat_id}")])\n    return InlineKeyboardMarkup(inline_keyboard=rows)\n\n\ndef admin_role_threshold_keyboard(chat_id: int, role_key: str, selected=None) -> InlineKeyboardMarkup:\n    rows = [[\n        InlineKeyboardButton(text="↩️ По режиму", callback_data=f"admin:role_set:{chat_id}:{role_key}:default"),\n        InlineKeyboardButton(text="⬛ Выключить", callback_data=f"admin:role_set:{chat_id}:{role_key}:off"),\n    ]]\n    buttons = []\n    for value in range(3, 31):\n        mark = "✅" if str(selected) == str(value) else "▫️"\n        buttons.append(InlineKeyboardButton(\n            text=f"{mark} {value}", callback_data=f"admin:role_set:{chat_id}:{role_key}:{value}"\n        ))\n    rows += chunk_buttons(buttons, 4)\n    rows.append([InlineKeyboardButton(text="⬅️ К ролям", callback_data=f"admin:roles:{chat_id}")])\n    return InlineKeyboardMarkup(inline_keyboard=rows)\n\n\ndef admin_timings_keyboard(chat_id: int, values: dict) -> InlineKeyboardMarkup:\n    fields = [\n        ("registration_seconds", "🎟 Регистрация"),\n        ("night_seconds", "🌃 Ночь"),\n        ("discussion_seconds", "💬 Обсуждение"),\n        ("nomination_seconds", "🗳 Выдвижение"),\n        ("verdict_seconds", "⚖️ Вердикт"),\n    ]\n    rows = []\n    for key, label in fields:\n        rows.append([InlineKeyboardButton(\n            text=f"{label} · {values[key]}с", callback_data=f"admin:time:{chat_id}:{key}"\n        )])\n    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:refresh:{chat_id}")])\n    return InlineKeyboardMarkup(inline_keyboard=rows)\n\n\ndef admin_time_values_keyboard(chat_id: int, field: str, selected: int) -> InlineKeyboardMarkup:\n    values = [15, 20, 30, 45, 60, 90, 120, 180]\n    buttons = [InlineKeyboardButton(\n        text=("✅ " if selected == value else "▫️ ") + f"{value} сек",\n        callback_data=f"admin:time_set:{chat_id}:{field}:{value}",\n    ) for value in values]\n    rows = chunk_buttons(buttons, 2)\n    rows.append([InlineKeyboardButton(text="⬅️ К таймингам", callback_data=f"admin:timings:{chat_id}")])\n    return InlineKeyboardMarkup(inline_keyboard=rows)\n\n\ndef admin_misc_keyboard(chat_id: int, protect: bool, early: bool) -> InlineKeyboardMarkup:\n    return InlineKeyboardMarkup(inline_keyboard=[\n        [InlineKeyboardButton(\n            text=f"{'✅' if protect else '⬜'} Защищённые ЛС",\n            callback_data=f"admin:toggle:{chat_id}:protect_private_content",\n        )],\n        [InlineKeyboardButton(\n            text=f"{'✅' if early else '⬜'} Быстрая ночь",\n            callback_data=f"admin:toggle:{chat_id}:early_night_finish",\n        )],\n        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:refresh:{chat_id}")],\n    ])\n\n\ndef admin_back_keyboard(chat_id: int) -> InlineKeyboardMarkup:\n    return InlineKeyboardMarkup(inline_keyboard=[\n        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:refresh:{chat_id}")]\n    ])\n\n\ndef admin_mode_keyboard(chat_id: int) -> InlineKeyboardMarkup:'''
patch("mafia_optimisma/keyboards.py", old_admin_keyboards, new_admin_keyboards, "expanded admin keyboards")

# Callbacks import the new menus.
patch(
    "mafia_optimisma/routers_callbacks.py",
    '''from .keyboards import admin_mode_keyboard, admin_settings_keyboard, shop_keyboard''',
    '''from .keyboards import (\n    admin_back_keyboard, admin_misc_keyboard, admin_mode_keyboard,\n    admin_role_threshold_keyboard, admin_roles_keyboard, admin_settings_keyboard,\n    admin_time_values_keyboard, admin_timings_keyboard, shop_keyboard,\n)''',
    "admin keyboard imports",
)

old_panel = '''async def _admin_panel_payload(callback: CallbackQuery, chat_id: int):\n    assert engine\n    game = store.get(chat_id)\n    try:\n        chat = await callback.bot.get_chat(chat_id)\n        title = getattr(chat, "title", None) or "Игровой чат"\n    except Exception:\n        title = "Игровой чат"\n    if game:\n        status = (\n            f"🎮 <b>Режим:</b> {MODES[game.mode]['emoji']} <b>{MODES[game.mode]['name']}</b>\\n"\n            f"🎬 <b>Состояние:</b> <code>{game.phase.value}</code>\\n"\n            f"👥 <b>Игроков:</b> {len(game.players)}"\n        )\n    else:\n        status = "🎬 <b>Состояние:</b> игра/регистрация сейчас не запущена"\n    text = (\n        "⚙️ <b>Mafia Optimisma · Управление группой</b>\\n"\n        f"🏙 <b>Чат:</b> {escape(title)}\\n\\n"\n        f"{status}\\n\\n"\n        f"⏱ Регистрация: {engine.settings.registration_seconds} сек. · "\n        f"Ночь: {engine.settings.night_seconds} сек. · "\n        f"День: {engine.settings.discussion_seconds} сек.\\n\\n"\n        "Выбери действие ниже."\n    )\n    return text, admin_settings_keyboard(chat_id)'''
new_panel = '''async def _admin_panel_payload(callback: CallbackQuery, chat_id: int):\n    assert engine\n    game = store.get(chat_id)\n    try:\n        chat = await callback.bot.get_chat(chat_id)\n        title = getattr(chat, "title", None) or "Игровой чат"\n    except Exception:\n        title = "Игровой чат"\n    try:\n        cfg = await engine.storage.get_chat_settings(chat_id)\n    except Exception:\n        cfg = {}\n    timing = {\n        "registration_seconds": int(cfg.get("registration_seconds", engine.settings.registration_seconds)),\n        "night_seconds": int(cfg.get("night_seconds", engine.settings.night_seconds)),\n        "discussion_seconds": int(cfg.get("discussion_seconds", engine.settings.discussion_seconds)),\n        "nomination_seconds": int(cfg.get("nomination_seconds", engine.settings.nomination_seconds)),\n        "verdict_seconds": int(cfg.get("verdict_seconds", engine.settings.verdict_seconds)),\n    }\n    if game:\n        status = (\n            f"🎮 <b>Режим:</b> {MODES[game.mode]['emoji']} <b>{MODES[game.mode]['name']}</b>\\n"\n            f"🎬 <b>Состояние:</b> <code>{game.phase.value}</code> · 👥 {len(game.players)}"\n        )\n    else:\n        status = "🎬 <b>Состояние:</b> игра сейчас не запущена"\n    text = (\n        "⚙️ <b>Mafia Optimisma · Настройки</b>\\n"\n        f"🏙 <b>Чат:</b> {escape(title)}\\n\\n"\n        f"{status}\\n\\n"\n        "⏱ <b>Текущие правила для следующей игры</b>\\n"\n        f"Регистрация {timing['registration_seconds']}с · Ночь {timing['night_seconds']}с · "\n        f"День {timing['discussion_seconds']}с\\n\\n"\n        "ℹ️ Настройки не меняют уже начавшуюся партию. Изменения применятся со следующей игры.\\n\\n"\n        "Выбери раздел ниже."\n    )\n    return text, admin_settings_keyboard(chat_id)'''
patch("mafia_optimisma/routers_callbacks.py", old_panel, new_panel, "admin panel payload")

# New admin actions are inserted after refresh.
patch(
    "mafia_optimisma/routers_callbacks.py",
    '''    if action == "mode_menu":\n        await callback.message.edit_text(''',
    '''    if action == "roles":\n        cfg = await engine.storage.get_chat_settings(chat_id)\n        overrides = cfg.get("role_thresholds", {})\n        if not isinstance(overrides, dict):\n            overrides = {}\n        await callback.message.edit_text(\n            "🎭 <b>Роли</b>\\n\\n"\n            "Выбери роль. Можно оставить порог «по режиму», включить её от конкретного "\n            "числа игроков или полностью отключить.\\n\\n"\n            "Изменения работают только со следующей партии.",\n            reply_markup=admin_roles_keyboard(chat_id, overrides),\n        )\n        await callback.answer()\n        return\n\n    if action == "role":\n        role_key = parts[3]\n        role = ROLES.get(role_key)\n        if not role:\n            await callback.answer("Неизвестная роль.", show_alert=True)\n            return\n        cfg = await engine.storage.get_chat_settings(chat_id)\n        overrides = cfg.get("role_thresholds", {})\n        if not isinstance(overrides, dict):\n            overrides = {}\n        selected = overrides.get(role_key)\n        state = "по правилам режима" if selected is None else (\n            "выключена" if int(selected) <= 0 else f"от {int(selected)} игроков"\n        )\n        await callback.message.edit_text(\n            f"{role.emoji} <b>{role.title}</b>\\n\\n"\n            f"Сейчас: <b>{state}</b>.\\n"\n            "От скольких игроков включать эту роль?",\n            reply_markup=admin_role_threshold_keyboard(chat_id, role_key, selected),\n        )\n        await callback.answer()\n        return\n\n    if action == "role_set":\n        role_key, raw = parts[3], parts[4]\n        if role_key not in ROLES:\n            await callback.answer("Неизвестная роль.", show_alert=True)\n            return\n        cfg = await engine.storage.get_chat_settings(chat_id)\n        overrides = cfg.get("role_thresholds", {})\n        if not isinstance(overrides, dict):\n            overrides = {}\n        overrides = dict(overrides)\n        if raw == "default":\n            overrides.pop(role_key, None)\n        elif raw == "off":\n            overrides[role_key] = 0\n        else:\n            value = max(3, min(30, int(raw)))\n            overrides[role_key] = value\n        await engine.storage.set_chat_setting(chat_id, "role_thresholds", overrides)\n        selected = overrides.get(role_key)\n        role = ROLES[role_key]\n        state = "по правилам режима" if selected is None else (\n            "выключена" if int(selected) <= 0 else f"от {int(selected)} игроков"\n        )\n        await callback.message.edit_text(\n            f"{role.emoji} <b>{role.title}</b>\\n\\nСейчас: <b>{state}</b>.\\n"\n            "От скольких игроков включать эту роль?",\n            reply_markup=admin_role_threshold_keyboard(chat_id, role_key, selected),\n        )\n        await callback.answer("Настройка сохранена для следующей игры.")\n        return\n\n    if action == "timings":\n        cfg = await engine.storage.get_chat_settings(chat_id)\n        values = {\n            "registration_seconds": int(cfg.get("registration_seconds", engine.settings.registration_seconds)),\n            "night_seconds": int(cfg.get("night_seconds", engine.settings.night_seconds)),\n            "discussion_seconds": int(cfg.get("discussion_seconds", engine.settings.discussion_seconds)),\n            "nomination_seconds": int(cfg.get("nomination_seconds", engine.settings.nomination_seconds)),\n            "verdict_seconds": int(cfg.get("verdict_seconds", engine.settings.verdict_seconds)),\n        }\n        await callback.message.edit_text(\n            "⏱ <b>Тайминги</b>\\n\\nВыбери фазу, время которой хочешь изменить.",\n            reply_markup=admin_timings_keyboard(chat_id, values),\n        )\n        await callback.answer()\n        return\n\n    if action == "time":\n        field = parts[3]\n        allowed = {\n            "registration_seconds", "night_seconds", "discussion_seconds",\n            "nomination_seconds", "verdict_seconds",\n        }\n        if field not in allowed:\n            await callback.answer("Неизвестный тайминг.", show_alert=True)\n            return\n        cfg = await engine.storage.get_chat_settings(chat_id)\n        selected = int(cfg.get(field, getattr(engine.settings, field)))\n        labels = {\n            "registration_seconds": "🎟 Регистрация", "night_seconds": "🌃 Ночь",\n            "discussion_seconds": "💬 Обсуждение", "nomination_seconds": "🗳 Выдвижение",\n            "verdict_seconds": "⚖️ Вердикт",\n        }\n        await callback.message.edit_text(\n            f"{labels[field]}\\n\\nСейчас: <b>{selected} секунд</b>. Выбери новое время:",\n            reply_markup=admin_time_values_keyboard(chat_id, field, selected),\n        )\n        await callback.answer()\n        return\n\n    if action == "time_set":\n        field, raw = parts[3], parts[4]\n        allowed = {\n            "registration_seconds", "night_seconds", "discussion_seconds",\n            "nomination_seconds", "verdict_seconds",\n        }\n        if field not in allowed:\n            await callback.answer("Неизвестный тайминг.", show_alert=True)\n            return\n        value = max(15, min(180, int(raw)))\n        await engine.storage.set_chat_setting(chat_id, field, value)\n        await callback.message.edit_text(\n            f"⏱ <b>Сохранено: {value} секунд</b>\\n\\nНастройка начнёт действовать со следующей игры.",\n            reply_markup=admin_time_values_keyboard(chat_id, field, value),\n        )\n        await callback.answer("Сохранено.")\n        return\n\n    if action == "chat_rules":\n        await callback.message.edit_text(\n            "🙊 <b>Чат во время игры</b>\\n\\n"\n            "🔒 Ночью сообщения живых игроков удаляются.\\n"\n            "👻 Зрители и выбывшие не могут писать во время партии.\\n"\n            "💋 Игрок под действием Ночной Дивы молчит и не голосует днём.\\n\\n"\n            "Эти правила являются частью игрового ядра и не отключаются — так рейтинг и партии остаются честными.",\n            reply_markup=admin_back_keyboard(chat_id),\n        )\n        await callback.answer()\n        return\n\n    if action == "misc":\n        cfg = await engine.storage.get_chat_settings(chat_id)\n        protect = bool(cfg.get("protect_private_content", False))\n        early = bool(cfg.get("early_night_finish", True))\n        await callback.message.edit_text(\n            "🛠 <b>Разное</b>\\n\\n"\n            "🛡 <b>Защищённые ЛС</b> — игровые сообщения нельзя пересылать/копировать.\\n"\n            "⚡ <b>Быстрая ночь</b> — если все активные роли уже сделали ход, утро наступает сразу.",\n            reply_markup=admin_misc_keyboard(chat_id, protect, early),\n        )\n        await callback.answer()\n        return\n\n    if action == "toggle":\n        feature = parts[3]\n        if feature not in {"protect_private_content", "early_night_finish"}:\n            await callback.answer("Неизвестная настройка.", show_alert=True)\n            return\n        cfg = await engine.storage.get_chat_settings(chat_id)\n        default = False if feature == "protect_private_content" else True\n        new_value = not bool(cfg.get(feature, default))\n        await engine.storage.set_chat_setting(chat_id, feature, new_value)\n        cfg[feature] = new_value\n        await callback.message.edit_text(\n            "🛠 <b>Разное</b>\\n\\n"\n            "🛡 <b>Защищённые ЛС</b> — игровые сообщения нельзя пересылать/копировать.\\n"\n            "⚡ <b>Быстрая ночь</b> — если все активные роли уже сделали ход, утро наступает сразу.",\n            reply_markup=admin_misc_keyboard(\n                chat_id, bool(cfg.get("protect_private_content", False)),\n                bool(cfg.get("early_night_finish", True)),\n            ),\n        )\n        await callback.answer("Настройка сохранена для следующей игры.")\n        return\n\n    if action == "reset":\n        await engine.storage.reset_chat_settings(chat_id)\n        text, markup = await _admin_panel_payload(callback, chat_id)\n        try:\n            await callback.message.edit_text(text, reply_markup=markup)\n        except Exception:\n            await callback.message.answer(text, reply_markup=markup)\n        await callback.answer("Настройки сброшены.")\n        return\n\n    if action == "mode_menu":\n        await callback.message.edit_text(''',
    "admin settings action handlers",
)

# Group /settings remains only a doorway; make its initial private message explain
# the richer settings instead of displaying only global env timing values.
patch(
    "mafia_optimisma/routers_group.py",
    '''    panel = (\n        "⚙️ <b>Mafia Optimisma · Управление группой</b>\\n"\n        f"🏙 <b>Чат:</b> {escape(message.chat.title or 'Игровой чат')}\\n\\n"\n        f"{status}\\n\\n"\n        f"⏱ Регистрация: {engine.settings.registration_seconds} сек. · "\n        f"Ночь: {engine.settings.night_seconds} сек. · "\n        f"День: {engine.settings.discussion_seconds} сек.\\n\\n"\n        "Выбери действие ниже. Эта панель видна только тебе в ЛС."\n    )''',
    '''    try:\n        cfg = await engine.storage.get_chat_settings(message.chat.id)\n    except Exception:\n        cfg = {}\n    reg = int(cfg.get("registration_seconds", engine.settings.registration_seconds))\n    night = int(cfg.get("night_seconds", engine.settings.night_seconds))\n    day = int(cfg.get("discussion_seconds", engine.settings.discussion_seconds))\n    panel = (\n        "⚙️ <b>Mafia Optimisma · Настройки</b>\\n"\n        f"🏙 <b>Чат:</b> {escape(message.chat.title or 'Игровой чат')}\\n\\n"\n        f"{status}\\n\\n"\n        f"⏱ Следующая игра: регистрация {reg}с · ночь {night}с · день {day}с\\n\\n"\n        "ℹ️ Настройки, изменённые во время партии, применяются только к следующей игре.\\n\\n"\n        "Выбери раздел ниже. Эта панель видна только администраторам в ЛС."\n    )''',
    "group settings private panel",
)

print("ADMIN GAME SETTINGS APPLIED")
