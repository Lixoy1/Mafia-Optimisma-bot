from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .content import ITEMS, MODES, ROLES
from .engine import GameEngine
from .keyboards import shop_keyboard
from .models import Phase
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
    await message.answer(
        "🎲 <b>Mafia Optimisma</b>\n"
        "Даже в мафии есть место оптимизму!\n\n"
        "Команды:\n"
        "/menu — меню\n"
        "/profile — профиль\n"
        "/shop — магазин\n"
        "/roles — роли\n\n"
        "Чтобы войти в игру, нажми кнопку «Присоединиться» в групповом чате."
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
        text.append(f"{item['emoji']} <b>{item['name']}</b> — {' + '.join(price)}")
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
