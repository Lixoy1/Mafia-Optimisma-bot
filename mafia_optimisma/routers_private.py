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
        "🎲 **Mafia Optimisma**\n"
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
        "📋 **Меню**\n\n"
        "👤 /profile — профиль\n"
        "🛒 /shop — магазин усилений\n"
        "📖 /roles — бестиарий ролей\n"
        "🎮 /modes — режимы\n"
        "💬 Ночью мафия и клан Сакуры могут писать сюда для командного чата."
    )


@router.message(Command("profile"), F.chat.type == "private")
async def profile(message: Message):
    assert engine
    user = message.from_user
    if not user:
        return
    p = await engine.storage.ensure_profile(user.id, user.full_name, user.username)
    await message.answer(engine.format_profile(p))


@router.message(Command("shop"), F.chat.type == "private")
async def shop(message: Message):
    assert engine
    user = message.from_user
    if user:
        game = store.game_by_user(user.id)
        if game and game.phase in {Phase.NIGHT, Phase.DISCUSSION, Phase.VOTING}:
            await message.answer("🛒 Магазин недоступен во время запущенной игры. Покупки открыты до регистрации/старта и после окончания партии.")
            return
    text = ["🛒 **Магазин усилений**\n"]
    for item in ITEMS.values():
        price = []
        if item["money"]:
            price.append(f"{item['money']}💵")
        if item["gems"]:
            price.append(f"{item['gems']}💎")
        text.append(f"{item['emoji']} **{item['name']}** — {' + '.join(price)}")
    await message.answer("\n".join(text), reply_markup=shop_keyboard())


@router.message(Command("roles"), F.chat.type == "private")
async def roles(message: Message):
    lines = ["📖 **Бестиарий Mafia Optimisma**\n"]
    for role in ROLES.values():
        lines.append(f"{role.title} — {role.short_description}")
    text = "\n".join(lines)
    for i in range(0, len(text), 3900):
        await message.answer(text[i:i + 3900])


@router.message(Command("modes"), F.chat.type == "private")
async def modes(message: Message):
    lines = ["🎮 **Режимы**\n"]
    for key, mode in MODES.items():
        lines.append(f"{mode['emoji']} **{mode['name']}** — команда в чате: /game{list(MODES).index(key)+1}")
    await message.answer("\n".join(lines))


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
    if await engine.team_chat(message.bot, game, player, message.text):
        await message.answer("📨 Сообщение отправлено союзникам.")
        return
    await message.answer("Сообщение принято. Если сейчас ночь и у тебя есть команда, я перешлю его союзникам.")
