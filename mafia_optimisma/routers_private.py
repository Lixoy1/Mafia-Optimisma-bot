from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from .engine import GameEngine
from .keyboards import shop_keyboard
from .state import store

router = Router(name="private")
engine: GameEngine | None = None


def setup(game_engine: GameEngine) -> Router:
    global engine
    engine = game_engine
    return router


@router.message(F.text == "/start")
async def start_private(message: Message):
    await message.answer(
        "🎲 **Добро пожаловать в Mafia Optimisma!**\n\n"
        "Выбери действие:",
        reply_markup=shop_keyboard()
    )


@router.message(F.text == "/profile")
async def profile(message: Message):
    if not engine:
        return
    p = await engine.storage.ensure_profile(message.from_user.id, message.from_user.full_name, message.from_user.username)
    text = (
        f"👤 **{p['name']}**\n"
        f"💵 {p['money']} | 💎 {p['gems']} | 🌟 Уровень {p['level']}\n"
        f"🏆 Побед: {p['wins']} | 🎮 Игр: {p['games']}"
    )
    await message.answer(text)


@router.message(F.text == "/shop")
async def shop(message: Message):
    game = store.game_by_user(message.from_user.id)
    if game and game.phase in {"night", "discussion", "voting"}:
        await message.answer("🛒 Магазин закрыт во время игры.")
        return
    await message.answer("🛒 **Магазин усилений**", reply_markup=shop_keyboard())
