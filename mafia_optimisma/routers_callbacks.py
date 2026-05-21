from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from .admin import is_chat_admin
from .content import GLOBAL, MODES, ROLES
from .engine import GameEngine, pick, role_title
from .keyboards import admin_settings_keyboard, join_keyboard, shop_keyboard
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
    user = callback.from_user
    if not game:
        await callback.answer("Регистрация не найдена.", show_alert=True)
        return
    await engine.storage.remember_chat_user(chat_id, user.id, user.full_name, user.username)
    ok, text = await engine.add_player(game, user.id, user.full_name, user.username)
    await callback.answer("Ты в игре!" if ok else text, show_alert=not ok)
    if ok:
        await engine.update_registration_message(callback.bot, game)
        await callback.bot.send_message(chat_id, text)


@router.callback_query(F.data.startswith("mode:"))
async def cb_mode(callback: CallbackQuery):
    parts = callback.data.split(":")
    chat_id = int(parts[1])
    mode = parts[2]
    if not await is_chat_admin(callback.bot, chat_id, callback.from_user.id):
        await callback.answer("Только администратор чата может менять режим.", show_alert=True)
        return
    game = store.get(chat_id)
    if not game or game.phase != Phase.REGISTRATION:
        await callback.answer("Режим можно менять только во время регистрации.", show_alert=True)
        return
    if mode not in MODES:
        await callback.answer("Неизвестный режим.", show_alert=True)
        return
    game.mode = mode
    await callback.answer("Режим изменён.")
    await callback.bot.send_message(chat_id, f"🎮 Режим установлен: **{MODES[mode]['emoji']} {MODES[mode]['name']}**")


@router.callback_query(F.data.startswith("pm:"))
async def cb_pm(callback: CallbackQuery):
    assert engine
    user = callback.from_user
    if callback.data == "pm:profile":
        p = await engine.storage.ensure_profile(user.id, user.full_name, user.username)
        await callback.message.answer(engine.format_profile(p), reply_markup=shop_keyboard())
    elif callback.data == "pm:shop":
        await callback.message.answer("🛒 Магазин усилений", reply_markup=shop_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("shop:"))
async def cb_shop(callback: CallbackQuery):
    assert engine
    item_key = callback.data.split(":", 1)[1]
    ok, msg = await engine.storage.buy_item(callback.from_user.id, item_key)
    await callback.answer(msg, show_alert=True)


@router.callback_query(F.data.startswith("item:"))
async def cb_item(callback: CallbackQuery):
    assert engine
    _, chat_id_raw, item = callback.data.split(":")
    chat_id = int(chat_id_raw)
    game = store.get(chat_id)
    if not game or game.phase != Phase.NIGHT:
        await callback.answer("Сейчас предмет использовать нельзя.", show_alert=True)
        return
    player = game.get_player(callback.from_user.id)
    if not player or not player.alive:
        await callback.answer("Ты не в игре.", show_alert=True)
        return
    if item == "armor_piercing":
        if await engine.storage.consume_item(player.user_id, "armor_piercing"):
            game.temp.setdefault(player.user_id, {})["armor_piercing"] = True
            await callback.answer("☠️ Чёрная пуля заряжена для следующего убийства/выстрела.", show_alert=True)
        else:
            await callback.answer("У тебя нет Чёрной пули.", show_alert=True)


@router.callback_query(F.data.startswith("n:"))
async def cb_night(callback: CallbackQuery):
    assert engine
    data = callback.data.split(":")
    chat_id = int(data[1])
    action = data[2]
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

    if action in {"report1", "swap1"}:
        game.temp.setdefault(player.user_id, {})[action] = target_id
        next_action = "report2" if action == "report1" else "swap2"
        buttons = []
        for p in game.alive_players():
            if p.user_id == target_id and next_action == "swap2":
                continue
            buttons.append(InlineKeyboardButton(text=p.name[:28], callback_data=f"n2:{chat_id}:{next_action}:{target_id}:{p.user_id}"))
        rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        await callback.message.answer("Выбери второго игрока:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await callback.answer("Первый выбран.")
        return

    item = "armor_piercing" if game.temp.get(player.user_id, {}).pop("armor_piercing", False) else None
    game.actions[player.user_id] = NightAction(actor_id=player.user_id, action_type=action, target_id=target_id, item=item)
    await callback.answer("Действие принято.")
    await callback.message.answer(f"Ты выбрал(а): **{escape(target.name)}**")
    role = ROLES[player.role_key or "optimist"]
    if role.chat_action_phrases:
        await callback.bot.send_message(game.chat_id, pick(role.chat_action_phrases))


@router.callback_query(F.data.startswith("n2:"))
async def cb_night_second(callback: CallbackQuery):
    data = callback.data.split(":")
    chat_id = int(data[1])
    action = data[2]
    first_id = int(data[3])
    second_id = int(data[4])
    game = store.get(chat_id)
    if not game or game.phase != Phase.NIGHT:
        await callback.answer("Сейчас не ночь.", show_alert=True)
        return
    player = game.get_player(callback.from_user.id)
    first = game.get_player(first_id)
    second = game.get_player(second_id)
    if not player or not first or not second or not player.alive:
        await callback.answer("Действие недоступно.", show_alert=True)
        return
    real_action = "compare_clans" if action == "report2" else "swap_roles"
    game.actions[player.user_id] = NightAction(actor_id=player.user_id, action_type=real_action, target_id=first_id, target2_id=second_id)
    await callback.answer("Действие принято.")
    await callback.message.answer(f"Выбраны: **{first.name}** и **{second.name}**")


@router.callback_query(F.data.startswith("noop:"))
async def cb_noop(callback: CallbackQuery):
    await callback.answer("Выбери игрока ниже.")


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
    if not voter or not voter.alive:
        await callback.answer("Ты не голосуешь.", show_alert=True)
        return
    if voter.silenced:
        await callback.answer("Ты сегодня молчишь и не голосуешь.", show_alert=True)
        return
    if value == "skip":
        game.votes[voter.user_id] = None
        await callback.answer("Ты пропустил(а) голосование.")
        await callback.bot.send_message(game.chat_id, pick(GLOBAL["vote_skip"], name=escape(voter.name)))
    else:
        target_id = int(value)
        target = game.get_player(target_id)
        if not target or not target.alive:
            await callback.answer("Цель недоступна.", show_alert=True)
            return
        game.votes[voter.user_id] = target_id
        await callback.answer("Голос принят.")
        await callback.bot.send_message(game.chat_id, pick(GLOBAL["vote_cast"], voter=escape(voter.name), target=escape(target.name)))


@router.callback_query(F.data.startswith("bomb:"))
async def cb_bomb(callback: CallbackQuery):
    _, chat_id_raw, target_raw = callback.data.split(":")
    chat_id = int(chat_id_raw)
    target_id = int(target_raw)
    game = store.get(chat_id)
    if not game:
        await callback.answer("Игра не найдена.", show_alert=True)
        return
    target = game.get_player(target_id)
    bomber = game.get_player(callback.from_user.id)
    if not target or not bomber or bomber.role_key != "bomber":
        await callback.answer("Недоступно.", show_alert=True)
        return
    if target.alive:
        target.alive = False
        await callback.bot.send_message(chat_id, f"💣 Подрывник забрал с собой {escape(target.name)}. Роль: **{role_title(target.role_key)}**")
    await callback.answer("Бум.")


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
            await engine.public_registration_message(callback.bot, game)
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
        await engine.update_registration_message(callback.bot, game)
        await callback.bot.send_message(chat_id, "⏱ Регистрация продлена на 30 секунд. Успевайте войти!", reply_markup=join_keyboard(chat_id))
        return

    if action == "call":
        bot_name = (await callback.bot.get_me()).first_name
        users = await engine.storage.get_callable_users(chat_id)
        mentions = []
        for user in users[:50]:
            username = user.get("username")
            mentions.append(f"@{username}" if username else escape(user.get("name") or "Игрок"))
        text = " ".join(mentions) if mentions else "Пока некого звать: игроки должны хотя бы раз написать боту или войти через /join."
        await callback.answer("Созыв отправлен.")
        await callback.bot.send_message(chat_id, pick(GLOBAL["call_start"], bot_name=bot_name) + "\n\n" + text, reply_markup=join_keyboard(chat_id))
        await callback.bot.send_message(chat_id, pick(GLOBAL["call_end"]))
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
