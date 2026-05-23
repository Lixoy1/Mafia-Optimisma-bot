from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from .admin import is_chat_admin
from .content import GLOBAL, MODES, ROLES, pick, role_title
from .engine import GameEngine
from .keyboards import admin_settings_keyboard, join_keyboard, open_bot_keyboard, shop_keyboard, vote_keyboard, night_action_keyboard
from .models import NightAction, Phase
from .state import store

router = Router(name="callbacks")
engine: GameEngine | None = None


def setup(game_engine: GameEngine) -> Router:
    global engine
    engine = game_engine
    return router


@router.callback_query(F.data.startswith("join:"))
async def cb_join(callback: CallbackQuery):
    assert engine
    chat_id = int(callback.data.split(":", 1)[1])
    game = store.get(chat_id)
    if not game or game.phase != Phase.REGISTRATION:
        await callback.answer("Регистрация уже закрыта.", show_alert=True)
        return

    user = callback.from_user
    if user.id in game.players:
        await callback.answer("Ты уже в игре!", show_alert=True)
        return

    await engine.storage.remember_chat_user(chat_id, user.id, user.full_name, user.username)
    ok, text = await engine.add_player(game, user.id, user.full_name, user.username)

    if ok:
        await engine.update_registration_message(callback.bot, game)
        await callback.answer("✅ Ты присоединился к игре!", show_alert=False)
        try:
            await callback.bot.send_message(user.id, "✅ Ты в игре! Когда начнётся ночь, кнопки действий придут сюда.")
        except Exception:
            pass
    else:
        await callback.answer(text, show_alert=True)


@router.callback_query(F.data.startswith("n:"))
async def cb_night(callback: CallbackQuery):
    assert engine
    data = callback.data.split(":")
    chat_id = int(data[1])
    action_type = data[2]
    target_id = int(data[3])

    game = store.get(chat_id)
    if not game or game.phase != Phase.NIGHT:
        await callback.answer("Сейчас не ночь.", show_alert=True)
        return

    player = game.get_player(callback.from_user.id)
    target = game.get_player(target_id)

    if not player or not target or not player.alive:
        await callback.answer("Действие недоступно.", show_alert=True)
        return

    if action_type == "heal" and target_id == player.user_id and player.self_heals_used >= 1:
        await callback.answer("🩺 Ты уже лечил себя этой игрой.", show_alert=True)
        return

    item = game.temp.get(player.user_id, {}).pop("armor_piercing", None)
    game.actions[player.user_id] = NightAction(
        actor_id=player.user_id,
        action_type=action_type,
        target_id=target_id,
        item=item
    )
    player.action_done = True

    await callback.answer("✅ Действие принято")
    await callback.message.edit_text(f"🌙 Ты выбрал: **{escape(target.name)}**", reply_markup=None)


@router.callback_query(F.data.startswith("n2:"))
async def cb_night_second(callback: CallbackQuery):
    data = callback.data.split(":")
    chat_id = int(data[1])
    step = data[2]
    first_id = int(data[3])
    second_id = int(data[4])

    game = store.get(chat_id)
    if not game or game.phase != Phase.NIGHT:
        await callback.answer("Сейчас не ночь.", show_alert=True)
        return

    player = game.get_player(callback.from_user.id)
    if not player or not player.alive:
        await callback.answer("Действие недоступно.", show_alert=True)
        return

    real_action = "swap_roles" if step == "swap2" else "compare_clans"
    game.actions[player.user_id] = NightAction(
        actor_id=player.user_id,
        action_type=real_action,
        target_id=first_id,
        target2_id=second_id
    )
    player.action_done = True

    await callback.answer("✅ Действие принято")
    first = game.get_player(first_id)
    second = game.get_player(second_id)
    await callback.message.edit_text(
        f"🌙 Выбраны: **{escape(first.name)}** и **{escape(second.name)}**",
        reply_markup=None
    )


@router.callback_query(F.data.startswith("item:"))
async def cb_item(callback: CallbackQuery):
    assert engine
    _, chat_id_raw, item_key = callback.data.split(":")
    chat_id = int(chat_id_raw)

    game = store.get(chat_id)
    if not game or game.phase != Phase.NIGHT:
        await callback.answer("Предмет можно использовать только ночью.", show_alert=True)
        return

    player = game.get_player(callback.from_user.id)
    if not player or not player.alive:
        await callback.answer("Ты не в игре.", show_alert=True)
        return

    if item_key == "armor_piercing":
        if await engine.storage.consume_item(player.user_id, "armor_piercing"):
            game.temp.setdefault(player.user_id, {})["armor_piercing"] = True
            await callback.answer("☠️ Чёрная пуля заряжена!", show_alert=True)
        else:
            await callback.answer("У тебя нет Чёрной пули.", show_alert=True)


@router.callback_query(F.data.startswith("vote:"))
async def cb_vote(callback: CallbackQuery):
    assert engine
    data = callback.data.split(":")
    chat_id = int(data[1])
    value = data[2]

    game = store.get(chat_id)
    if not game or game.phase != Phase.VOTING:
        await callback.answer("Голосование не активно.", show_alert=True)
        return

    voter = game.get_player(callback.from_user.id)
    if not voter or not voter.alive or voter.silenced:
        await callback.answer("Ты не можешь голосовать.", show_alert=True)
        return

    if value == "skip":
        game.votes[voter.user_id] = None
        await callback.answer("Голос пропущен")
    else:
        target_id = int(value)
        target = game.get_player(target_id)
        if not target or not target.alive:
            await callback.answer("Цель недоступна.", show_alert=True)
            return
        game.votes[voter.user_id] = target_id
        await callback.answer("Голос принят")

    await callback.message.edit_text("🗳 Твой голос учтён.", reply_markup=None)


@router.callback_query(F.data.startswith("shop:"))
async def cb_shop(callback: CallbackQuery):
    assert engine
    game = store.game_by_user(callback.from_user.id)
    if game and game.phase in {Phase.NIGHT, Phase.DISCUSSION, Phase.VOTING}:
        await callback.answer("🛒 Магазин закрыт во время игры.", show_alert=True)
        return

    item_key = callback.data.split(":", 1)[1]
    ok, msg = await engine.storage.buy_item(callback.from_user.id, item_key)
    await callback.answer(msg, show_alert=True)


@router.callback_query(F.data.startswith("pm:shop"))
async def cb_pm_shop(callback: CallbackQuery):
    game = store.game_by_user(callback.from_user.id)
    if game and game.phase in {Phase.NIGHT, Phase.DISCUSSION, Phase.VOTING}:
        await callback.answer("🛒 Магазин закрыт во время игры.", show_alert=True)
        return
    await callback.message.answer("🛒 **Магазин усилений**", reply_markup=shop_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("admin:"))
async def cb_admin(callback: CallbackQuery):
    assert engine
    parts = callback.data.split(":")
    action = parts[1]
    chat_id = int(parts[2])
    if not await is_chat_admin(callback.bot, chat_id, callback.from_user.id):
        await callback.answer("Только администратор чата.", show_alert=True)
        return

    game = store.get(chat_id)

    if action == "mode":
        mode = parts[3]
        if mode not in MODES:
            await callback.answer("Неизвестный режим.", show_alert=True)
            return
        if not game or game.phase != Phase.REGISTRATION:
            game = store.create_or_reset(chat_id, callback.message.chat.title if callback.message else "чат", mode)
            await engine.begin_registration(callback.bot, game)
        else:
            game.mode = mode
        await callback.answer("Режим установлен.")
        await callback.bot.send_message(chat_id, f"🎮 Режим установлен: **{MODES[mode]['emoji']} {MODES[mode]['name']}**", reply_markup=admin_settings_keyboard(chat_id))
        return

    if action == "start":
        if not game:
            await callback.answer("Сначала выбери режим /game1 или через настройки.", show_alert=True)
            return
        if game.phase != Phase.REGISTRATION:
            await callback.answer("Игра уже идёт.", show_alert=True)
            return
        await callback.answer("Запускаю игру.")
        await engine.start_game(callback.bot, game)
        return

    if action == "extend":
        if not game or game.phase != Phase.REGISTRATION:
            await callback.answer("Нет активной регистрации.", show_alert=True)
            return
        await callback.answer("Регистрация продлена.")
        engine.schedule_registration(callback.bot, game, 30)
        await engine.update_registration_message(callback.bot, game)
        await callback.bot.send_message(chat_id, "⏱ Регистрация продлена на 30 секунд. Таймер автостарта запущен заново.")
        return

    if action == "call":
        bot_name = (await callback.bot.get_me()).first_name
        users = await engine.storage.get_callable_users(chat_id)
        await callback.answer("Созыв отправлен.")
        await callback.bot.send_message(chat_id, pick(GLOBAL["call_start"], bot_name=bot_name) + "\n\n" + " ".join([f"@{u['username']}" if u.get("username") else escape(u["name"]) for u in users[:80]]) + "\n\n" + pick(GLOBAL["call_end"]))
        return

    if action == "players":
        if not game:
            await callback.answer("Активной игры нет.", show_alert=True)
            return
        if game.phase == Phase.REGISTRATION:
            names = "\n".join(f"{i}. @{p.username}" if p.username else f"{i}. {escape(p.name)}" for i, p in enumerate(game.players.values(), 1)) or "пока пусто"
            await callback.bot.send_message(chat_id, f"👥 **Игроки:**\n{names}\n\nВсего: {len(game.players)}")
        else:
            from .engine import living_summary
            await callback.bot.send_message(chat_id, living_summary(game))
        await callback.answer()
        return

    if action == "stats":
        rows = await engine.storage.top_profiles(10)
        if not rows:
            await callback.bot.send_message(chat_id, "📊 Статистики пока нет. Сыграйте первую игру.")
        else:
            lines = ["📊 **Топ игроков Mafia Optimisma**\n"]
            for i, row in enumerate(rows, 1):
                name = escape(row.get("name") or row.get("username") or str(row["user_id"]))
                lines.append(f"{i}. {name} — 🏆 {row['wins']} / 🎮 {row['games']} | 🌟 {row['level']} | 💵 {row['money']} | 💎 {row['gems']}")
            await callback.bot.send_message(chat_id, "\n".join(lines))
        await callback.answer()
        return

    if action == "cancel":
        if not game:
            await callback.answer("Активной регистрации нет.", show_alert=True)
            return
        store.remove_game(chat_id)
        await callback.answer("Регистрация отменена.")
        await callback.bot.send_message(chat_id, "🚫 Регистрация отменена администратором.")
        return

    await callback.answer("Неизвестное действие.", show_alert=True)
