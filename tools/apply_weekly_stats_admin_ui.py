from pathlib import Path


def replace_once(text: str, old: str, new: str, marker: str) -> str:
    if marker in text:
        return text
    if old not in text:
        raise SystemExit(f"weekly/ui hotfix target not found: {marker}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Engine: persist final team result before deleting the FINISHED snapshot.
# ---------------------------------------------------------------------------
engine_path = Path("mafia_optimisma/engine.py")
engine = engine_path.read_text(encoding="utf-8")
engine = replace_once(
    engine,
    "from .models import GameState, NightAction, Phase, PlayerState\n",
    "from .models import GameState, NightAction, Phase, PlayerState\nfrom .rankings import record_game_result\n",
    "from .rankings import record_game_result",
)

if "Ranking history is part of finalisation" not in engine:
    old = '''        if not rewards_ok:\n            # Keep the FINISHED snapshot. A local retry and, if needed, the next\n            # process startup can safely continue the missing rewards.\n            try:\n                await self.storage.save_game_state(game)\n            except Exception:\n                self.log.exception("Could not retain unfinished finalisation chat=%s", game.chat_id)\n            return False\n\n        try:\n            await self.storage.delete_game_state(game.chat_id)\n'''
    new = '''        if not rewards_ok:\n            # Keep the FINISHED snapshot. A local retry and, if needed, the next\n            # process startup can safely continue the missing rewards.\n            try:\n                await self.storage.save_game_state(game)\n            except Exception:\n                self.log.exception("Could not retain unfinished finalisation chat=%s", game.chat_id)\n            return False\n\n        # Ranking history is part of finalisation too. If this tiny idempotent\n        # write fails, keep the FINISHED snapshot so the retry can restore it;\n        # otherwise weekly/team statistics could silently lose a completed game.\n        try:\n            await record_game_result(self.storage, game, winner)\n        except Exception:\n            self.log.exception("Could not record game result chat=%s session=%s", game.chat_id, game.session_id)\n            try:\n                await self.storage.save_game_state(game)\n            except Exception:\n                pass\n            return False\n\n        try:\n            await self.storage.delete_game_state(game.chat_id)\n'''
    if old not in engine:
        raise SystemExit("weekly/ui hotfix target not found: ranking finalisation")
    engine = engine.replace(old, new, 1)
engine_path.write_text(engine, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main: initialise ranking tables, run hourly idempotent weekly-award checker,
# and expose week/stats commands. apply_live_game_rules_hotfix.py runs first.
# ---------------------------------------------------------------------------
main_path = Path("mafia_optimisma/main.py")
main = main_path.read_text(encoding="utf-8")
main = replace_once(
    main,
    "from .engine import GameEngine\n",
    "from .engine import GameEngine\nfrom .rankings import init_rankings, weekly_award_loop\n",
    "from .rankings import init_rankings, weekly_award_loop",
)

if 'BotCommand(command="week", description="Рейтинг текущей недели")' not in main:
    main = main.replace(
        '        BotCommand(command="mystats", description="Моя статистика"),\n',
        '        BotCommand(command="mystats", description="Моя статистика"),\n'
        '        BotCommand(command="week", description="Рейтинг текущей недели"),\n'
        '        BotCommand(command="stats", description="Полная статистика"),\n',
        1,
    )
    # Add weekly command to the public group menu immediately after /players.
    main = main.replace(
        '        BotCommand(command="players", description="Игроки и живые роли"),\n',
        '        BotCommand(command="players", description="Игроки и живые роли"),\n'
        '        BotCommand(command="week", description="Рейтинг недели"),\n',
        1,
    )

if "await init_rankings(storage)" not in main:
    main = main.replace(
        "    await storage.init()\n",
        "    await storage.init()\n    await init_rankings(storage)\n",
        1,
    )

if "weekly_task = asyncio.create_task(weekly_award_loop(bot, storage))" not in main:
    old = '''    logging.info("Mafia Optimisma started as @%s; restored games=%s", me.username, restored)\n    await dp.start_polling(bot)\n'''
    new = '''    logging.info("Mafia Optimisma started as @%s; restored games=%s", me.username, restored)\n    weekly_task = asyncio.create_task(weekly_award_loop(bot, storage))\n    try:\n        await dp.start_polling(bot)\n    finally:\n        weekly_task.cancel()\n        try:\n            await weekly_task\n        except asyncio.CancelledError:\n            pass\n'''
    if old not in main:
        raise SystemExit("weekly/ui hotfix target not found: polling loop")
    main = main.replace(old, new, 1)
main_path.write_text(main, encoding="utf-8")


# ---------------------------------------------------------------------------
# Keyboard: compact private admin panel instead of four giant mode rows.
# ---------------------------------------------------------------------------
kb_path = Path("mafia_optimisma/keyboards.py")
kb = kb_path.read_text(encoding="utf-8")
if "def admin_mode_keyboard(chat_id: int)" not in kb:
    start = kb.index("def admin_settings_keyboard(chat_id: int)")
    old_block = kb[start:]
    new_block = '''def admin_settings_keyboard(chat_id: int) -> InlineKeyboardMarkup:\n    return InlineKeyboardMarkup(inline_keyboard=[\n        [\n            InlineKeyboardButton(text="🎮 Режим игры", callback_data=f"admin:mode_menu:{chat_id}"),\n            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin:refresh:{chat_id}"),\n        ],\n        [\n            InlineKeyboardButton(text="▶️ Запустить", callback_data=f"admin:start:{chat_id}"),\n            InlineKeyboardButton(text="⏱ +30 сек", callback_data=f"admin:extend:{chat_id}"),\n        ],\n        [\n            InlineKeyboardButton(text="👥 Игроки", callback_data=f"admin:players:{chat_id}"),\n            InlineKeyboardButton(text="📣 Созыв", callback_data=f"admin:call:{chat_id}"),\n        ],\n        [\n            InlineKeyboardButton(text="🏆 Неделя", callback_data=f"admin:weekly:{chat_id}"),\n            InlineKeyboardButton(text="📊 Статистика", callback_data=f"admin:stats:{chat_id}"),\n        ],\n        [InlineKeyboardButton(text="🚫 Отменить регистрацию", callback_data=f"admin:cancel:{chat_id}")],\n    ])\n\n\ndef admin_mode_keyboard(chat_id: int) -> InlineKeyboardMarkup:\n    rows = []\n    for key, mode in MODES.items():\n        rows.append([InlineKeyboardButton(\n            text=f"{mode['emoji']} {mode['name']}",\n            callback_data=f"admin:mode:{chat_id}:{key}",\n        )])\n    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:refresh:{chat_id}")])\n    return InlineKeyboardMarkup(inline_keyboard=rows)\n'''
    kb = kb[:start] + new_block
kb_path.write_text(kb, encoding="utf-8")


# ---------------------------------------------------------------------------
# Group router: settings panel goes to PM only; /stats and /week are readable.
# apply_live_game_rules_hotfix.py has already installed LiveGameChatGuard.
# ---------------------------------------------------------------------------
group_path = Path("mafia_optimisma/routers_group.py")
group = group_path.read_text(encoding="utf-8")
group = replace_once(
    group,
    "from .models import Phase\n",
    "from .models import Phase\nfrom .rankings import current_week_leaderboard, full_statistics, render_current_week, render_full_statistics\n",
    "from .rankings import current_week_leaderboard",
)

old_settings = '''@router.message(Command("settings"), F.chat.type.in_({"group", "supergroup"}))\nasync def settings(message: Message):\n    await remember_sender(message)\n    if not await require_admin(message):\n        return\n    game = store.get(message.chat.id)\n    if game:\n        status = f"Текущий режим: {mode_line(game.mode)}\\nФаза: <code>{game.phase.value}</code>\\nИгроков: {len(game.players)}"\n    else:\n        status = "Активной регистрации нет. Выбери режим и запусти регистрацию."\n    await message.answer(\n        "⚙️ <b>Админ-панель Mafia Optimisma</b>\\n\\n"\n        f"{status}\\n\\n"\n        "Здесь можно выбрать режим, продлить регистрацию, сделать созыв, посмотреть игроков/статистику или запустить игру.",\n        reply_markup=admin_settings_keyboard(message.chat.id),\n    )\n'''
new_settings = '''@router.message(Command("settings"), F.chat.type.in_({"group", "supergroup"}))\nasync def settings(message: Message):\n    assert engine\n    user = message.from_user\n    if not user:\n        return\n    await remember_sender(message)\n    is_admin = await is_chat_admin(message.bot, message.chat.id, user.id)\n    try:\n        await message.delete()\n    except Exception:\n        pass\n    if not is_admin:\n        try:\n            await message.bot.send_message(user.id, "🔐 Настройки этой группы доступны только её владельцу и администраторам.")\n        except Exception:\n            pass\n        return\n\n    game = store.get(message.chat.id)\n    if game:\n        status = (\n            f"🎮 <b>Режим:</b> {mode_line(game.mode)}\\n"\n            f"🎬 <b>Состояние:</b> <code>{game.phase.value}</code>\\n"\n            f"👥 <b>Игроков:</b> {len(game.players)}"\n        )\n    else:\n        status = "🎬 <b>Состояние:</b> игра/регистрация сейчас не запущена"\n    panel = (\n        "⚙️ <b>Mafia Optimisma · Управление группой</b>\\n"\n        f"🏙 <b>Чат:</b> {escape(message.chat.title or 'Игровой чат')}\\n\\n"\n        f"{status}\\n\\n"\n        f"⏱ Регистрация: {engine.settings.registration_seconds} сек. · "\n        f"Ночь: {engine.settings.night_seconds} сек. · "\n        f"День: {engine.settings.discussion_seconds} сек.\\n\\n"\n        "Выбери действие ниже. Эта панель видна только тебе в ЛС."\n    )\n    sent = await engine._safe_pm(\n        message.bot, user.id, panel, reply_markup=admin_settings_keyboard(message.chat.id)\n    )\n    if sent is None:\n        notice = await engine._safe_group(\n            message.bot, message.chat.id,\n            f"⚠️ {escape(user.full_name)}, сначала открой ЛС с ботом и нажми /start, затем повтори /settings."\n        )\n        if notice:\n            async def cleanup():\n                import asyncio\n                await asyncio.sleep(8)\n                await engine._safe_delete(message.bot, message.chat.id, notice.message_id)\n            import asyncio\n            asyncio.create_task(cleanup())\n'''
if "Эта панель видна только тебе в ЛС" not in group:
    if old_settings not in group:
        raise SystemExit("weekly/ui hotfix target not found: group settings")
    group = group.replace(old_settings, new_settings, 1)

# Replace the old overall stats output with the clearer ranking + team breakdown.
old_stats = '''@router.message(Command("stats", "statistics"), F.chat.type.in_({"group", "supergroup"}))\nasync def stats(message: Message):\n    assert engine\n    await remember_sender(message)\n    rows = await engine.storage.top_profiles(10)\n    if not rows:\n        await message.answer("📊 Статистики пока нет. Сыграйте первую игру.")\n        return\n    lines = ["📊 <b>Топ игроков Mafia Optimisma</b>\\n"]\n    for i, row in enumerate(rows, 1):\n        name = escape(row.get("name") or row.get("username") or str(row["user_id"]))\n        lines.append(f"{i}. {name} — 🏆 {row['wins']} / 🎮 {row['games']} | 🌟 {row['level']} | 💵 {row['money']} | 💎 {row['gems']}")\n    await message.answer("\\n".join(lines))\n'''
new_stats = '''@router.message(Command("stats", "statistics"), F.chat.type.in_({"group", "supergroup"}))\nasync def stats(message: Message):\n    assert engine\n    await remember_sender(message)\n    top, counts, total = await full_statistics(engine.storage, 10)\n    await message.answer(render_full_statistics(top, counts, total))\n\n\n@router.message(Command("week", "weekly", "topweek"), F.chat.type.in_({"group", "supergroup"}))\nasync def weekly_stats(message: Message):\n    assert engine\n    await remember_sender(message)\n    rows, start, end = await current_week_leaderboard(engine.storage, 10)\n    await message.answer(render_current_week(rows, start, end))\n'''
if "async def weekly_stats(message: Message):" not in group:
    if old_stats not in group:
        raise SystemExit("weekly/ui hotfix target not found: group stats")
    group = group.replace(old_stats, new_stats, 1)
group_path.write_text(group, encoding="utf-8")


# ---------------------------------------------------------------------------
# Private router: stats/week are useful in PM too.
# ---------------------------------------------------------------------------
private_path = Path("mafia_optimisma/routers_private.py")
private = private_path.read_text(encoding="utf-8")
private = replace_once(
    private,
    "from .models import Phase\n",
    "from .models import Phase\nfrom .rankings import current_week_leaderboard, full_statistics, render_current_week, render_full_statistics\n",
    "from .rankings import current_week_leaderboard",
)

if "async def weekly_stats_pm(message: Message):" not in private:
    anchor = '''@router.message(Command("mystats"), F.chat.type == "private")\nasync def my_stats(message: Message):\n'''
    idx = private.index(anchor)
    # Insert global stats handlers immediately before personal /mystats.
    block = '''@router.message(Command("stats"), F.chat.type == "private")\nasync def stats_pm(message: Message):\n    assert engine\n    top, counts, total = await full_statistics(engine.storage, 10)\n    await message.answer(render_full_statistics(top, counts, total))\n\n\n@router.message(Command("week", "weekly", "topweek"), F.chat.type == "private")\nasync def weekly_stats_pm(message: Message):\n    assert engine\n    rows, start, end = await current_week_leaderboard(engine.storage, 10)\n    await message.answer(render_current_week(rows, start, end))\n\n\n'''
    private = private[:idx] + block + private[idx:]
private_path.write_text(private, encoding="utf-8")


# ---------------------------------------------------------------------------
# Callback router: private admin panel, mode submenu and ranking buttons.
# ---------------------------------------------------------------------------
cb_path = Path("mafia_optimisma/routers_callbacks.py")
cb = cb_path.read_text(encoding="utf-8")
cb = replace_once(
    cb,
    "from .keyboards import admin_settings_keyboard, shop_keyboard\n",
    "from .keyboards import admin_mode_keyboard, admin_settings_keyboard, shop_keyboard\n",
    "admin_mode_keyboard, admin_settings_keyboard",
)
cb = replace_once(
    cb,
    "from .models import NightAction, Phase, PlayerState\n",
    "from .models import NightAction, Phase, PlayerState\nfrom .rankings import current_week_leaderboard, full_statistics, render_current_week, render_full_statistics\n",
    "from .rankings import current_week_leaderboard",
)

if "async def _admin_panel_payload" not in cb:
    marker = '@router.callback_query(F.data.startswith("admin:"))\n'
    idx = cb.index(marker)
    helper = '''async def _admin_panel_payload(callback: CallbackQuery, chat_id: int):\n    assert engine\n    game = store.get(chat_id)\n    try:\n        chat = await callback.bot.get_chat(chat_id)\n        title = getattr(chat, "title", None) or "Игровой чат"\n    except Exception:\n        title = "Игровой чат"\n    if game:\n        status = (\n            f"🎮 <b>Режим:</b> {MODES[game.mode]['emoji']} <b>{MODES[game.mode]['name']}</b>\\n"\n            f"🎬 <b>Состояние:</b> <code>{game.phase.value}</code>\\n"\n            f"👥 <b>Игроков:</b> {len(game.players)}"\n        )\n    else:\n        status = "🎬 <b>Состояние:</b> игра/регистрация сейчас не запущена"\n    text = (\n        "⚙️ <b>Mafia Optimisma · Управление группой</b>\\n"\n        f"🏙 <b>Чат:</b> {escape(title)}\\n\\n"\n        f"{status}\\n\\n"\n        f"⏱ Регистрация: {engine.settings.registration_seconds} сек. · "\n        f"Ночь: {engine.settings.night_seconds} сек. · "\n        f"День: {engine.settings.discussion_seconds} сек.\\n\\n"\n        "Выбери действие ниже."\n    )\n    return text, admin_settings_keyboard(chat_id)\n\n\n'''
    cb = cb[:idx] + helper + cb[idx:]

# Insert new compact panel actions before existing admin:mode branch.
if 'if action == "mode_menu":' not in cb:
    anchor = '''    game = store.get(chat_id)\n\n    if action == "mode":\n'''
    replacement = '''    game = store.get(chat_id)\n\n    if action == "refresh":\n        text, markup = await _admin_panel_payload(callback, chat_id)\n        try:\n            await callback.message.edit_text(text, reply_markup=markup)\n        except Exception:\n            await callback.message.answer(text, reply_markup=markup)\n        await callback.answer()\n        return\n\n    if action == "mode_menu":\n        await callback.message.edit_text(\n            "🎮 <b>Режим игры</b>\\n\\nВыбери режим для текущей регистрации:",\n            reply_markup=admin_mode_keyboard(chat_id),\n        )\n        await callback.answer()\n        return\n\n    if action == "weekly":\n        rows, start, end = await current_week_leaderboard(engine.storage, 10)\n        await callback.message.answer(render_current_week(rows, start, end))\n        await callback.answer()\n        return\n\n    if action == "mode":\n'''
    if anchor not in cb:
        raise SystemExit("weekly/ui hotfix target not found: admin action anchor")
    cb = cb.replace(anchor, replacement, 1)

# When a mode is chosen from PM and no registration exists, resolve real group title.
old_title = '                title = callback.message.chat.title if callback.message else "чат"\n                game = store.create_or_reset(chat_id, title, mode)\n'
new_title = '''                try:\n                    target_chat = await callback.bot.get_chat(chat_id)\n                    title = getattr(target_chat, "title", None) or "чат"\n                except Exception:\n                    title = "чат"\n                game = store.create_or_reset(chat_id, title, mode)\n'''
if old_title in cb:
    cb = cb.replace(old_title, new_title, 1)

# Admin players and statistics stay in the private control conversation.
old_players_send = '            await callback.bot.send_message(chat_id, f"👥 <b>Игроки:</b>\\n{names}\\n\\nВсего: {len(game.players)}")\n'
new_players_send = '            await callback.message.answer(f"👥 <b>Игроки:</b>\\n{names}\\n\\nВсего: {len(game.players)}")\n'
cb = cb.replace(old_players_send, new_players_send, 1)
cb = cb.replace('            await callback.bot.send_message(chat_id, living_summary(game))\n', '            await callback.message.answer(living_summary(game))\n', 1)

if "top, counts, total = await full_statistics(engine.storage, 10)" not in cb:
    old_admin_stats = '''    if action == "stats":\n        rows = await engine.storage.top_profiles(10)\n        if not rows:\n            await callback.bot.send_message(chat_id, "📊 Статистики пока нет. Сыграйте первую игру.")\n        else:\n            lines = ["📊 <b>Топ игроков Mafia Optimisma</b>\\n"]\n            for i, row in enumerate(rows, 1):\n                name = escape(row.get("name") or row.get("username") or str(row["user_id"]))\n                lines.append(f"{i}. {name} — 🏆 {row['wins']} / 🎮 {row['games']} | 🌟 {row['level']}")\n            await callback.bot.send_message(chat_id, "\\n".join(lines))\n        await callback.answer()\n        return\n'''
    new_admin_stats = '''    if action == "stats":\n        top, counts, total = await full_statistics(engine.storage, 10)\n        await callback.message.answer(render_full_statistics(top, counts, total))\n        await callback.answer()\n        return\n'''
    if old_admin_stats not in cb:
        raise SystemExit("weekly/ui hotfix target not found: admin stats")
    cb = cb.replace(old_admin_stats, new_admin_stats, 1)
cb_path.write_text(cb, encoding="utf-8")

print("weekly rankings + private admin UI applied")
