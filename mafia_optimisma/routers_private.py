from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .content import ITEMS, MODES, ROLES
from .engine import GameEngine
from .keyboards import shop_keyboard
from .models import Phase
from .rankings import current_week_leaderboard, full_statistics, render_current_week, render_full_statistics
from .state import store

router = Router(name="private")
engine: GameEngine | None = None


def setup(game_engine: GameEngine) -> Router:
    global engine
    engine = game_engine
    return router


@router.message(Command("start"), F.chat.type == "private")
async def start_pm(message: Message, command: CommandObject):
    assert engine
    user = message.from_user
    if not user:
        return
    await engine.storage.ensure_profile(user.id, user.full_name, user.username)

    linked_game = None
    args = (command.args or "").strip()
    if args.startswith("join_"):
        try:
            _, session, chat_raw = args.split("_", 2)
            candidate = store.get(int(chat_raw))
            if candidate and candidate.session_id == session and candidate.get_player(user.id):
                linked_game = candidate
        except (ValueError, AttributeError):
            linked_game = None
    if linked_game is None:
        linked_game = store.game_by_user(user.id)

    if linked_game is not None and linked_game.get_player(user.id):
        player = linked_game.get_player(user.id)
        if linked_game.phase == Phase.REGISTRATION:
            async with engine.lock_for(linked_game.chat_id):
                pending = {
                    int(uid) for uid in (linked_game.temp.get("_pending_pm_activation") or [])
                    if str(uid).lstrip("-").isdigit()
                }
                was_pending = user.id in pending
                pending.discard(user.id)
                linked_game.temp["_pending_pm_activation"] = sorted(pending)
                prompts = dict(linked_game.temp.get("_activation_prompt_ids") or {})
                prompt_id = prompts.pop(str(user.id), None)
                linked_game.temp["_activation_prompt_ids"] = prompts
                await engine.persist(linked_game)
            if prompt_id:
                try:
                    await engine._safe_delete(message.bot, linked_game.chat_id, int(prompt_id))
                except (TypeError, ValueError):
                    pass
            await engine.update_registration_message(message.bot, linked_game)
            if was_pending:
                await message.answer(
                    "🙂 <b>Добро пожаловать в Mafia Optimisma!</b>\n\n"
                    "✅ Личный игровой канал активирован.\n"
                    f"🎭 Ты присоединился(ась) к игре в <b>{linked_game.chat_title}</b>.\n\n"
                    "Место закреплено. Возвращайся в город — роль придёт сюда после старта 😎\n"
                    "И да: повторно нажимать «Присоединиться» не нужно."
                )
            else:
                await message.answer(
                    "😏 <b>Да в игре ты уже! Слышишь? В игре :)</b>\n\n"
                    f"🎭 Группа: <b>{linked_game.chat_title}</b>\n"
                    "Просто жди старта. Второе место одному Оптимисту не выдаём."
                )
            return

        role_line = ""
        if player and player.role_key:
            role_line = f"\n🎭 Твоя роль: <b>{ROLES[player.role_key].title}</b>"
        await message.answer(
            "🎲 <b>Ты уже участвуешь в партии Mafia Optimisma.</b>\n"
            f"🏙 Группа: <b>{linked_game.chat_title}</b>{role_line}\n\n"
            "Следи за этим ЛС: сюда приходят ночные действия, проверки и важные события."
        )
        return

    await message.answer(
        "🙂 <b>Добро пожаловать в Mafia Optimisma!</b>\n"
        "Здесь даже мафия улыбается перед выстрелом 😎\n\n"
        "🚀 Личный игровой канал активирован.\n"
        "Теперь найди регистрацию в групповом чате и нажми «➕ Присоединиться».\n"
        "После этого роли и ночные действия будут приходить сюда автоматически.\n\n"
        "Команды: /menu · /profile · /shop · /roles"
    )

@router.message(Command("menu"), F.chat.type == "private")
async def menu(message: Message):
    await message.answer(
        "📋 <b>Меню</b>\n\n"
        "👤 /profile — профиль\n"
        "🛒 /shop — магазин усилений\n"
        "📖 /roles — бестиарий ролей\n"
        "🎮 /modes — режимы\n"
        "💬 Ночью Семья, Клан Сакуры и связка Хирург/Сестра могут общаться через бота."
    )


@router.message(Command("profile"), F.chat.type == "private")
async def profile(message: Message):
    assert engine
    user = message.from_user
    if not user:
        return
    p = await engine.storage.ensure_profile(user.id, user.full_name, user.username)
    await message.answer(engine.format_profile(p))


@router.message(Command("stats"), F.chat.type == "private")
async def stats_pm(message: Message):
    assert engine
    top, counts, total = await full_statistics(engine.storage, 10)
    await message.answer(render_full_statistics(top, counts, total))


@router.message(Command("week", "weekly", "topweek"), F.chat.type == "private")
async def weekly_stats_pm(message: Message):
    assert engine
    rows, start, end = await current_week_leaderboard(engine.storage, 10)
    await message.answer(render_current_week(rows, start, end))


@router.message(Command("mystats"), F.chat.type == "private")
async def my_stats(message: Message):
    assert engine
    user = message.from_user
    if not user:
        return
    p = await engine.storage.ensure_profile(user.id, user.full_name, user.username)
    games = int(p.get("games", 0))
    wins = int(p.get("wins", 0))
    rate = (wins / games * 100) if games else 0.0
    await message.answer(
        f"📊 <b>Моя статистика</b>\n"
        f"🎮 Игры: {games}\n"
        f"🏆 Победы: {wins}\n"
        f"📈 Винрейт: {rate:.1f}%\n"
        f"🌟 Уровень: {p.get('level', 1)}"
    )


@router.message(Command("shop"), F.chat.type == "private")
async def shop(message: Message):
    assert engine
    user = message.from_user
    if user:
        game = store.game_by_user(user.id)
        if game and game.phase not in {Phase.REGISTRATION, Phase.FINISHED}:
            await message.answer("🛒 Магазин недоступен во время запущенной игры. Покупки открыты до регистрации/старта и после окончания партии.")
            return
    text = ["🛒 <b>Магазин усилений</b>\n"]
    for item in ITEMS.values():
        if item.get("enabled", True) is False:
            text.append(f"{item['emoji']} <b>{item['name']}</b> — скоро")
            continue
        price = []
        if item["money"]:
            price.append(f"{item['money']}💵")
        if item["gems"]:
            price.append(f"{item['gems']}💎")
        text.append(
            f"{item['emoji']} <b>{item['name']}</b> — {' + '.join(price)}\n"
            f"<i>{item.get('description', '')}</i>"
        )
    await message.answer("\n".join(text), reply_markup=shop_keyboard())


@router.message(Command("roles"), F.chat.type == "private")
async def roles(message: Message):
    lines = ["📖 <b>Бестиарий Mafia Optimisma</b>\n"]
    for role in ROLES.values():
        lines.append(f"{role.title} — {role.short_description}")
    text = "\n".join(lines)
    for i in range(0, len(text), 3900):
        await message.answer(text[i:i + 3900])


@router.message(Command("modes"), F.chat.type == "private")
async def modes(message: Message):
    lines = ["🎮 <b>Режимы</b>\n"]
    for key, mode in MODES.items():
        command = "/game" if key == "classic" else f"/game{list(MODES).index(key)+1}"
        lines.append(f"{mode['emoji']} <b>{mode['name']}</b> — команда в чате: {command}")
    await message.answer("\n".join(lines))


@router.message(Command("game", "game1", "game2", "game3", "game4", "extend", "stop"), F.chat.type == "private")
async def group_only_commands(message: Message):
    await message.answer(
        "🎮 Эти команды запускаются в игровом групповом чате.\n\n"
        "/game — Городской оптимизм\n"
        "/game2 — Весёлый хаос\n"
        "/game3 — Эпидемия улыбок\n"
        "/game4 — Война улыбчивых кланов"
    )


@router.message(F.chat.type == "private")
async def private_text(message: Message):
    assert engine
    user = message.from_user
    if not user or not message.text:
        return
    game = store.game_by_user(user.id)
    if not game:
        await message.answer("Я тебя услышал 🙂 Но сейчас ты не числишься в активной игре.")
        return
    player = game.get_player(user.id)
    if not player:
        return
    if await engine.handle_last_word(message.bot, message, game, player):
        return
    if not player.alive:
        await message.answer("❌ Вы не в игре.")
        return
    if await engine.team_chat(message.bot, game, player, message.text):
        await message.answer("📨 Сообщение отправлено союзникам.")
        return
    await message.answer("Сообщение принято. Если сейчас ночь и у тебя есть команда, я перешлю его союзникам.")
