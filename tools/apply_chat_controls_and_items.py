from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"source block not found in {path}: {old[:160]!r}")
    write(path, text.replace(old, new, 1))


def insert_before(path: str, marker: str, block: str) -> None:
    text = read(path)
    if block.strip() in text:
        return
    if marker not in text:
        raise RuntimeError(f"marker not found in {path}: {marker[:160]!r}")
    write(path, text.replace(marker, block + "\n\n" + marker, 1))


# ---------------------------------------------------------------------------
# Naming polish: keep the stable internal role key `carrier`, change only UI.
# ---------------------------------------------------------------------------
replace_once(
    "mafia_optimisma/content.py",
    '"infected": {"name": "Носители", "emoji": "🧟"},',
    '"infected": {"name": "Эпидемия", "emoji": "🧬"},',
)
replace_once(
    "mafia_optimisma/content.py",
    '''    "carrier": r("carrier", "Носитель", "🧟", "Заражённый", "infected", "infected", 75, "infect_visitors", False,
        "Заражает приходящих к нему игроков. Победа — когда все живые заражены.",
        ["Ты — 🧟 Носитель. Ты не охотишься. К тебе сами приходят.", "Ты — 🧟 источник плохих новостей и ещё худшей статистики."],
        ["Ты ждёшь гостей. Каждый визит может стать началом эпидемии."], [],
        ["🧟 Зараза коснулась {name}."]),''',
    '''    "carrier": r("carrier", "Инфицированный", "🧬", "Заражённый", "infected", "infected", 75, "infect_visitors", False,
        "Заражает активных посетителей. Победа Эпидемии — когда все выжившие инфицированы.",
        ["Ты — 🧬 Инфицированный. Ты не ищешь жертв — любопытные сами приходят слишком близко.", "Ты — 🧬 живой очаг эпидемии. Один визит может изменить весь город."],
        ["Оставайся незаметным. Каждый ночной посетитель рискует уйти уже другим."], [],
        ["🧬 Контакт с {name} изменил ход эпидемии."]),''',
)

# Shop descriptions: visible rules rather than mystery purchases.
replace_once(
    "mafia_optimisma/content.py",
    '''ITEMS = {
    "night_shield": {"name": "Ночной оберег", "emoji": "🛡", "money": 100, "gems": 0},
    "clean_papers": {"name": "Чистые документы", "emoji": "📂", "money": 150, "gems": 0},
    "antivirus": {"name": "Антивирус", "emoji": "📀", "money": 150, "gems": 0},
    "perfume": {"name": "Дымный парфюм", "emoji": "🧴", "money": 150, "gems": 0},
    "active_role": {"name": "Билет в движ", "emoji": "🎎", "money": 0, "gems": 1, "enabled": False},
    "armor_piercing": {"name": "Чёрная пуля", "emoji": "☠️", "money": 0, "gems": 1},
    "day_shield": {"name": "Солнечный иммунитет", "emoji": "⚖️", "money": 0, "gems": 1},
}''',
    '''ITEMS = {
    "night_shield": {"name": "Ночной оберег", "emoji": "🛡", "money": 100, "gems": 0,
        "description": "Автоматически спасает от одной обычной смертельной атаки ночью."},
    "clean_papers": {"name": "Чистые документы", "emoji": "📂", "money": 150, "gems": 0,
        "description": "При следующей проверке автоматически показывает тебя как 🙂 Оптимиста."},
    "antivirus": {"name": "Антивирус", "emoji": "📀", "money": 150, "gems": 0,
        "description": "Автоматически срывает следующую попытку Взломщика узнать твою роль."},
    "perfume": {"name": "Дымный парфюм", "emoji": "🧴", "money": 150, "gems": 0,
        "description": "Автоматически отменяет следующую ночную блокировку Дивы или Костолома."},
    "active_role": {"name": "Билет в движ", "emoji": "🎎", "money": 0, "gems": 1, "enabled": False,
        "description": "Предмет в разработке."},
    "armor_piercing": {"name": "Чёрная пуля", "emoji": "☠️", "money": 0, "gems": 1,
        "description": "Активируется вручную перед атакой и пробивает лечение, оберег и удачу."},
    "day_shield": {"name": "Солнечный иммунитет", "emoji": "⚖️", "money": 0, "gems": 1,
        "description": "Автоматически спасает от одной дневной казни."},
}''',
)

# ---------------------------------------------------------------------------
# Preserve per-game settings snapshot across Railway restarts.
# ---------------------------------------------------------------------------
replace_once(
    "mafia_optimisma/models.py",
    '''            elif isinstance(value, list) and all(isinstance(x, (str, int, float, bool, type(None))) for x in value):
                safe_temp[key] = value
''',
    '''            elif isinstance(value, list) and all(isinstance(x, (str, int, float, bool, type(None))) for x in value):
                safe_temp[key] = value
            elif key == "_chat_settings" and isinstance(value, dict):
                # Per-game admin rules are already JSON-backed settings. Keep the
                # snapshot with the game so a Railway restart cannot silently
                # change moderation, timing or voting UI mid-party.
                safe_temp[key] = value
''',
)

# ---------------------------------------------------------------------------
# Voting UI and admin chat-rules keyboard.
# ---------------------------------------------------------------------------
replace_once(
    "mafia_optimisma/keyboards.py",
    '''def vote_keyboard(game: GameState, voter_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for p in game.alive_players():
        if p.user_id == voter_id:
            continue
        buttons.append(InlineKeyboardButton(
            text=p.name[:28],
            callback_data=f"vote:{game.session_id}:{game.chat_id}:{game.day}:{p.user_id}",
        ))
''',
    '''def vote_keyboard(game: GameState, voter_id: int) -> InlineKeyboardMarkup:
    buttons = []
    cfg = game.temp.get("_chat_settings", {})
    show_numbers = bool(cfg.get("vote_show_numbers", True)) if isinstance(cfg, dict) else True
    for p in game.alive_players():
        if p.user_id == voter_id:
            continue
        label = f"№{p.number:02d} · {p.name[:22]}" if show_numbers and p.number else p.name[:28]
        buttons.append(InlineKeyboardButton(
            text=label,
            callback_data=f"vote:{game.session_id}:{game.chat_id}:{game.day}:{p.user_id}",
        ))
''',
)

insert_before(
    "mafia_optimisma/keyboards.py",
    "def admin_misc_keyboard(",
    '''def admin_chat_rules_keyboard(chat_id: int, values: dict | None = None) -> InlineKeyboardMarkup:
    values = values or {}
    options = [
        ("block_profanity", "🤬 Запретить мат", False),
        ("block_stickers", "🖼 Запретить стикеры", False),
        ("block_links", "🔗 Запретить ссылки", False),
        ("vote_show_numbers", "🔢 № + имя в голосовании", True),
    ]
    rows = []
    for key, label, default in options:
        enabled = bool(values.get(key, default))
        rows.append([InlineKeyboardButton(
            text=f"{'✅' if enabled else '⬜'} {label}",
            callback_data=f"admin:chat_toggle:{chat_id}:{key}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:refresh:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)''',
)

# ---------------------------------------------------------------------------
# Admin callbacks for the new toggles.
# ---------------------------------------------------------------------------
replace_once(
    "mafia_optimisma/routers_callbacks.py",
    '''    admin_back_keyboard, admin_misc_keyboard, admin_mode_keyboard,
    admin_role_threshold_keyboard, admin_roles_keyboard, admin_settings_keyboard,
''',
    '''    admin_back_keyboard, admin_chat_rules_keyboard, admin_misc_keyboard, admin_mode_keyboard,
    admin_role_threshold_keyboard, admin_roles_keyboard, admin_settings_keyboard,
''',
)
replace_once(
    "mafia_optimisma/routers_callbacks.py",
    '''    if action == "chat_rules":
        await callback.message.edit_text(
            "🙊 <b>Чат во время игры</b>\n\n"
            "🔒 Ночью сообщения живых игроков удаляются.\n"
            "👻 Зрители и выбывшие не могут писать во время партии.\n"
            "💋 Игрок под действием Ночной Дивы молчит и не голосует днём.\n\n"
            "Эти правила являются частью игрового ядра и не отключаются — так рейтинг и партии остаются честными.",
            reply_markup=admin_back_keyboard(chat_id),
        )
        await callback.answer()
        return
''',
    '''    if action == "chat_rules":
        cfg = await engine.storage.get_chat_settings(chat_id)
        await callback.message.edit_text(
            "🙊 <b>Чат во время игры</b>\n\n"
            "🔒 Базовые правила всегда активны: ночью город молчит, зрители и выбывшие не вмешиваются, "
            "а заблокированный игрок не говорит и не голосует.\n\n"
            "Дополнительная модерация применяется с <b>следующей регистрацией</b> и сохраняется при рестарте бота.\n"
            "🔢 «№ + имя» меняет подписи кнопок выдвижения кандидата.",
            reply_markup=admin_chat_rules_keyboard(chat_id, cfg),
        )
        await callback.answer()
        return

    if action == "chat_toggle":
        feature = parts[3]
        defaults = {
            "block_profanity": False,
            "block_stickers": False,
            "block_links": False,
            "vote_show_numbers": True,
        }
        if feature not in defaults:
            await callback.answer("Неизвестная настройка чата.", show_alert=True)
            return
        cfg = await engine.storage.get_chat_settings(chat_id)
        new_value = not bool(cfg.get(feature, defaults[feature]))
        await engine.storage.set_chat_setting(chat_id, feature, new_value)
        cfg[feature] = new_value
        await callback.message.edit_text(
            "🙊 <b>Чат во время игры</b>\n\n"
            "🔒 Базовые правила всегда активны: ночью город молчит, зрители и выбывшие не вмешиваются, "
            "а заблокированный игрок не говорит и не голосует.\n\n"
            "Дополнительная модерация применяется с <b>следующей регистрацией</b> и сохраняется при рестарте бота.\n"
            "🔢 «№ + имя» меняет подписи кнопок выдвижения кандидата.",
            reply_markup=admin_chat_rules_keyboard(chat_id, cfg),
        )
        await callback.answer("Настройка сохранена для следующей игры.")
        return
''',
)

# ---------------------------------------------------------------------------
# Live chat moderation. Conservative profanity detector to avoid words such as
# "страхуй" being caught merely because they contain three suspicious letters.
# ---------------------------------------------------------------------------
replace_once(
    "mafia_optimisma/routers_group.py",
    "from html import escape\n",
    "from html import escape\nimport re\n",
)
insert_before(
    "mafia_optimisma/routers_group.py",
    "async def _delete_live_chat_message(",
    '''_PROFANITY_RE = re.compile(
    r"(?iu)(?<![а-яё])(?:"
    r"бля(?:д[ьи]?|ть)?|сука|сук[аи]|суч(?:ка|ий|ара)|"
    r"(?:о|на|за|по|вы|до|при|про|у|разъ|подъ)?ху(?:й|я|е|ё|и|ли)[а-яё]*|"
    r"(?:за|на|по|вы|про|пере|подъ|разъ|у)?[её]б[а-яё]*|"
    r"пизд[а-яё]*|мудак[а-яё]*|долбо[её]б[а-яё]*|гандон[а-яё]*|шлюх[а-яё]*"
    r")(?![а-яё])"
)
_LINK_RE = re.compile(
    r"(?iu)(?:https?://|www\\.|t\\.me/|telegram\\.me/|"
    r"(?<![\\w@])(?:[a-z0-9-]+\\.)+(?:com|ru|net|org|io|gg|me|app|dev|nl|de)(?:/|\\b))"
)


def _game_chat_feature(game, key: str, default: bool = False) -> bool:
    cfg = game.temp.get("_chat_settings", {}) if game else {}
    if not isinstance(cfg, dict):
        return default
    raw = cfg.get(key, default)
    if isinstance(raw, str):
        return raw.strip().lower() not in {"0", "false", "off", "no"}
    return bool(raw)


def _contains_profanity(text: str | None) -> bool:
    return bool(text and _PROFANITY_RE.search(text))


def _message_has_link(message: Message) -> bool:
    for entity in list(getattr(message, "entities", None) or []) + list(getattr(message, "caption_entities", None) or []):
        kind = str(getattr(entity, "type", "")).lower()
        if kind in {"url", "text_link"} or kind.endswith(".url") or kind.endswith(".text_link"):
            return True
    text = " ".join(filter(None, [getattr(message, "text", None), getattr(message, "caption", None)]))
    return bool(_LINK_RE.search(text))


def _moderation_reason(message: Message, game) -> str | None:
    if _game_chat_feature(game, "block_stickers", False) and getattr(message, "sticker", None) is not None:
        return "🖼 В этой партии администратор отключил стикеры в игровом чате."
    text = " ".join(filter(None, [getattr(message, "text", None), getattr(message, "caption", None)]))
    if _game_chat_feature(game, "block_links", False) and _message_has_link(message):
        return "🔗 В этой партии ссылки в игровом чате запрещены."
    if _game_chat_feature(game, "block_profanity", False) and _contains_profanity(text):
        return "🤬 В этой партии включён фильтр мата. Сообщение удалено — переформулируй без него."
    return None''',
)

replace_once(
    "mafia_optimisma/routers_group.py",
    '''        if game.phase in {Phase.DISCUSSION, Phase.NOMINATION, Phase.VERDICT, Phase.RESOLVING} and player.silenced:
            await _delete_live_chat_message(
                event, game, "❌ Ночная Дива лишила тебя права говорить до конца дня.",
            )
            return None

        return await handler(event, data)
''',
    '''        if game.phase in {Phase.DISCUSSION, Phase.NOMINATION, Phase.VERDICT, Phase.RESOLVING} and player.silenced:
            await _delete_live_chat_message(
                event, game, "🤐 Ночной эффект лишил тебя права говорить и голосовать до конца дня.",
            )
            return None

        moderation_reason = _moderation_reason(event, game)
        if moderation_reason:
            await _delete_live_chat_message(event, game, moderation_reason)
            return None

        return await handler(event, data)
''',
)

# The legacy catch-all guard remains as a second line of defence if middleware is
# ever not installed by an alternate entry point.
replace_once(
    "mafia_optimisma/routers_group.py",
    '''    if game.phase in {Phase.DISCUSSION, Phase.NOMINATION, Phase.VERDICT, Phase.RESOLVING} and player.silenced:
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.bot.send_message(user.id, "🤐 Ночной эффект ещё действует: до конца дня нельзя общаться и голосовать.")
        except Exception:
            pass
''',
    '''    if game.phase in {Phase.DISCUSSION, Phase.NOMINATION, Phase.VERDICT, Phase.RESOLVING} and player.silenced:
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.bot.send_message(user.id, "🤐 Ночной эффект ещё действует: до конца дня нельзя общаться и голосовать.")
        except Exception:
            pass
        return

    moderation_reason = _moderation_reason(message, game)
    if moderation_reason:
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.bot.send_message(user.id, moderation_reason)
        except Exception:
            pass
        return
''',
)

# ---------------------------------------------------------------------------
# Store: purchase/consume are now serialized transactions; this closes races
# when users double-tap shop or premium-item buttons.
# ---------------------------------------------------------------------------
replace_once(
    "mafia_optimisma/storage.py",
    '''    async def buy_item(self, user_id: int, item_key: str) -> tuple[bool, str]:
        item = ITEMS[item_key]
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await self._fetch_profile_row(db, user_id)
            if not row:
                return False, "Сначала напиши /start боту в ЛС."
            p = self._row_to_profile(row)
            if p["money"] < item["money"] or p["gems"] < item["gems"]:
                return False, "Не хватает валюты."
            items = p["items"]
            items[item_key] = items.get(item_key, 0) + 1
            await db.execute(
                "UPDATE profiles SET money = ?, gems = ?, items = ? WHERE user_id = ?",
                (p["money"] - item["money"], p["gems"] - item["gems"], json.dumps(items, ensure_ascii=False), user_id),
            )
            await db.commit()
            return True, f"Куплено: {item['emoji']} {item['name']}"

    async def consume_item(self, user_id: int, item_key: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT items FROM profiles WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                return False
            items = json.loads(row["items"] or "{}")
            if items.get(item_key, 0) <= 0:
                return False
            items[item_key] -= 1
            await db.execute("UPDATE profiles SET items = ? WHERE user_id = ?", (json.dumps(items, ensure_ascii=False), user_id))
            await db.commit()
            return True
''',
    '''    async def buy_item(self, user_id: int, item_key: str) -> tuple[bool, str]:
        item = ITEMS[item_key]
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            row = await self._fetch_profile_row(db, user_id)
            if not row:
                await db.commit()
                return False, "Сначала напиши /start боту в ЛС."
            p = self._row_to_profile(row)
            if p["money"] < item["money"] or p["gems"] < item["gems"]:
                await db.commit()
                return False, "Не хватает валюты."
            items = p["items"]
            items[item_key] = items.get(item_key, 0) + 1
            await db.execute(
                "UPDATE profiles SET money = ?, gems = ?, items = ? WHERE user_id = ?",
                (p["money"] - item["money"], p["gems"] - item["gems"], json.dumps(items, ensure_ascii=False), user_id),
            )
            await db.commit()
            return True, f"✅ Куплено: {item['emoji']} {item['name']}"

    async def consume_item(self, user_id: int, item_key: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute("SELECT items FROM profiles WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                await db.commit()
                return False
            items = json.loads(row["items"] or "{}")
            if items.get(item_key, 0) <= 0:
                await db.commit()
                return False
            items[item_key] -= 1
            await db.execute("UPDATE profiles SET items = ? WHERE user_id = ?", (json.dumps(items, ensure_ascii=False), user_id))
            await db.commit()
            return True
''',
)

# ---------------------------------------------------------------------------
# Shop descriptions in PM.
# ---------------------------------------------------------------------------
replace_once(
    "mafia_optimisma/routers_private.py",
    '''        text.append(f"{item['emoji']} <b>{item['name']}</b> — {' + '.join(price)}")
''',
    '''        text.append(
            f"{item['emoji']} <b>{item['name']}</b> — {' + '.join(price)}\n"
            f"<i>{item.get('description', '')}</i>"
        )
''',
)

# ---------------------------------------------------------------------------
# Item resolution: precise, cinematic feedback for both sides.
# ---------------------------------------------------------------------------
replace_once(
    "mafia_optimisma/engine.py",
    '''            if await self._consume_game_item_safe(
                game, target.user_id, "perfume", f"block:{a.actor_id}:{a.target_id}"
            ):
                await self._safe_pm(bot, target.user_id, "🧴 Дымный парфюм защитил тебя от ночной блокировки.")
                continue
''',
    '''            if await self._consume_game_item_safe(
                game, target.user_id, "perfume", f"block:{a.actor_id}:{a.target_id}"
            ):
                await self._safe_pm(
                    bot, target.user_id,
                    "🧴 <b>Дымный парфюм растворил чужой план.</b>\n"
                    "Кто-то пытался сорвать твой ночной ход, но блокировка не сработала."
                )
                await self._safe_pm(
                    bot, actor.user_id,
                    "🌫 <b>Цель исчезла в дыме.</b> Блокировка сорвалась — ночной ход цели остался активен."
                )
                continue
''',
)

replace_once(
    "mafia_optimisma/engine.py",
    '''            if a.action_type in {"check", "mafia_role_check"} and target:
                await self._safe_pm(bot, target.user_id, "🔎 Кто-то заинтересовался твоей ролью.")
                if a.action_type == "mafia_role_check" and await self._consume_game_item_safe(
                    game, target.user_id, "antivirus", f"antivirus:{a.actor_id}:{a.action_type}:{a.target_id}"
                ):
                    await self._safe_pm(bot, actor.user_id, "📀 Взлом сорвался: у цели сработал Антивирус.")
                    continue
                shown = role_title(target.role_key)
                if await self._consume_game_item_safe(
                    game, target.user_id, "clean_papers", f"papers:{a.actor_id}:{a.action_type}:{a.target_id}"
                ):
                    shown = role_title("optimist")
''',
    '''            if a.action_type in {"check", "mafia_role_check"} and target:
                if a.action_type == "mafia_role_check" and await self._consume_game_item_safe(
                    game, target.user_id, "antivirus", f"antivirus:{a.actor_id}:{a.action_type}:{a.target_id}"
                ):
                    await self._safe_pm(
                        bot, target.user_id,
                        "📀 <b>Кто-то полез в твоё досье.</b>\n"
                        "Антивирус захлопнул дверь перед Взломщиком — настоящую роль он не получил."
                    )
                    await self._safe_pm(
                        bot, actor.user_id,
                        "📀 <b>Доступ закрыт.</b> Антивирус цели уничтожил след запроса — роль осталась неизвестна."
                    )
                    continue
                shown = role_title(target.role_key)
                papers_used = await self._consume_game_item_safe(
                    game, target.user_id, "clean_papers", f"papers:{a.actor_id}:{a.action_type}:{a.target_id}"
                )
                if papers_used:
                    shown = role_title("optimist")
                    await self._safe_pm(
                        bot, target.user_id,
                        "📂 <b>Кто-то заинтересовался твоей ролью.</b>\n"
                        "Но «Чистые документы» уже лежали на столе. Проверяющий уверен, что ты — 🙂 <b>Оптимист</b>."
                    )
                else:
                    await self._safe_pm(bot, target.user_id, "🔎 Кто-то заинтересовался твоей ролью.")
''',
)

replace_once(
    "mafia_optimisma/engine.py",
    '''                await self._safe_pm(bot, target.user_id, "🛡 Ночной оберег спас тебя от смерти.")
                continue
''',
    '''                await self._safe_pm(
                    bot, target.user_id,
                    "🛡 <b>Ночной оберег принял удар на себя.</b>\nТы должен(на) был(а) погибнуть, но этой ночью смерть прошла мимо."
                )
                await self._safe_pm(bot, actor.user_id, "🛡 Удар погас в защите цели. Эта атака не убила её.")
                continue
''',
)

replace_once(
    "mafia_optimisma/engine.py",
    '''                if await self._consume_game_item_safe(
                    game, candidate.user_id, "day_shield", f"verdict:{game.day}:{candidate.user_id}"
                ):
                    await self._safe_group(bot, game.chat_id, f"☀️ <b>Солнечный иммунитет</b>\n{player_link(candidate)} избежал(а) казни.")
''',
    '''                if await self._consume_game_item_safe(
                    game, candidate.user_id, "day_shield", f"verdict:{game.day}:{candidate.user_id}"
                ):
                    await self._safe_group(
                        bot, game.chat_id,
                        f"☀️ <b>Солнечный иммунитет сорвал приговор</b>\n{player_link(candidate)} уже стоял(а) у края — но казнь отменена."
                    )
                    await self._safe_pm(
                        bot, candidate.user_id,
                        "☀️ <b>Тебя приговорили, но Солнечный иммунитет сработал.</b>\nПредмет потрачен. Ты остаёшься в игре."
                    )
''',
)

# Role-name hardcoded infection messages.
for old, new in [
    ("🩺 Тебя вылечили. Теперь ты 🙂 Оптимист.", "🩺 Терапия сработала. Инфекция отступила — теперь ты 🙂 Оптимист."),
    ("🧟 Ты заразился(ась). Твоя новая роль: Носитель.", "🧬 Контакт оказался заразным. Твоя новая роль: 🧬 Инфицированный."),
    ("🧟 После ночного визита ты стал(а) Носителем.", "🧬 После ночного визита всё изменилось. Теперь ты — 🧬 Инфицированный."),
]:
    text = read("mafia_optimisma/engine.py")
    if old in text:
        write("mafia_optimisma/engine.py", text.replace(old, new))

# Internal logs are not player-facing but keep terminology coherent.
text = read("mafia_optimisma/engine.py")
text = text.replace("Носитель {carrier.name} вылечен", "Инфицированный {carrier.name} вылечен")
write("mafia_optimisma/engine.py", text)

print("CHAT CONTROLS + ITEM AUDIT APPLIED")
