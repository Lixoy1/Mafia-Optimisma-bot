from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .content import GLOBAL, MODES
from .engine import GameEngine, living_summary, pick
from .keyboards import join_keyboard, mode_keyboard
from .models import Phase
from .state import store

router = Router(name="group")
engine: GameEngine | None = None


def setup(game_engine: GameEngine) -> Router:
    global engine
    engine = game_engine
    return router


def is_group(message: Message) -> bool:
    return message.chat.type in {"group", "supergroup"}


@router.message(Command("start_reg"), F.chat.type.in_({"group", "supergroup"}))
async def start_registration(message: Message, command: CommandObject):
    assert engine
    mode = (command.args or "classic").strip().lower()
    if mode not in MODES:
        mode = "classic"
    game = store.create_or_reset(message.chat.id, message.chat.title or "чат", mode)
    await engine.public_registration_message(message.bot, game)
    await message.answer(f"🎲 Регистрация началась. Режим: **{MODES[mode]['emoji']} {MODES[mode]['name']}**", reply_markup=mode_keyboard(message.chat.id))


@router.message(Command("game1"), F.chat.type.in_({"group", "supergroup"}))
async def game_classic(message: Message):
    await _start_mode(message, "classic")


@router.message(Command("game2"), F.chat.type.in_({"group", "supergroup"}))
async def game_chaos(message: Message):
    await _start_mode(message, "chaos")


@router.message(Command("game3"), F.chat.type.in_({"group", "supergroup"}))
async def game_virus(message: Message):
    await _start_mode(message, "virus")


@router.message(Command("game4"), F.chat.type.in_({"group", "supergroup"}))
async def game_clans(message: Message):
    await _start_mode(message, "clans")


async def _start_mode(message: Message, mode: str):
    assert engine
    game = store.create_or_reset(message.chat.id, message.chat.title or "чат", mode)
    await engine.public_registration_message(message.bot, game)
    await message.answer(f"🎲 Регистрация началась. Режим: **{MODES[mode]['emoji']} {MODES[mode]['name']}**")


@router.message(Command("set_mode"), F.chat.type.in_({"group", "supergroup"}))
async def set_mode(message: Message):
    game = store.get(message.chat.id)
    if not game:
        await message.answer("Сначала начни регистрацию: /start_reg")
        return
    await message.answer("Выбери режим:", reply_markup=mode_keyboard(message.chat.id))


@router.message(Command("join"), F.chat.type.in_({"group", "supergroup"}))
async def join(message: Message):
    assert engine
    game = store.get(message.chat.id)
    if not game:
        game = store.create_or_reset(message.chat.id, message.chat.title or "чат", "classic")
        await engine.public_registration_message(message.bot, game)
    user = message.from_user
    if not user:
        return
    ok, text = await engine.add_player(game, user.id, user.full_name, user.username)
    await message.answer(text)


@router.message(Command("leave"), F.chat.type.in_({"group", "supergroup"}))
async def leave(message: Message):
    game = store.get(message.chat.id)
    user = message.from_user
    if not game or not user or user.id not in game.players:
        await message.answer("Ты не в игре.")
        return
    if game.phase != Phase.REGISTRATION:
        await message.answer("Игра уже началась. Выходить поздно, город всё видел.")
        return
    p = game.players.pop(user.id)
    store.user_to_chat.pop(user.id, None)
    await message.answer(f"💨 {escape(p.name)} не совладал(а) с эмоциями и покинул(а) город.")


@router.message(Command("players"), F.chat.type.in_({"group", "supergroup"}))
async def players(message: Message):
    game = store.get(message.chat.id)
    if not game:
        await message.answer("Активной регистрации нет.")
        return
    if game.phase == Phase.REGISTRATION:
        names = "\n".join(f"{i}. {escape(p.name)}" for i, p in enumerate(game.players.values(), 1)) or "пока пусто"
        await message.answer(f"👥 **Игроки:**\n{names}\n\nВсего: {len(game.players)}")
    else:
        await message.answer(living_summary(game))


@router.message(Command("start_game"), F.chat.type.in_({"group", "supergroup"}))
async def start_game(message: Message):
    assert engine
    game = store.get(message.chat.id)
    if not game:
        await message.answer("Нет регистрации. Начни: /start_reg")
        return
    if game.phase != Phase.REGISTRATION:
        await message.answer("Игра уже идёт.")
        return
    await engine.start_game(message.bot, game)


@router.message(Command("cancel_reg"), F.chat.type.in_({"group", "supergroup"}))
async def cancel_reg(message: Message):
    game = store.get(message.chat.id)
    if not game:
        await message.answer("Активной регистрации нет.")
        return
    store.remove_game(message.chat.id)
    await message.answer("🚫 Регистрация отменена. Город делает вид, что ничего не было.")


@router.message(Command("call"), F.chat.type.in_({"group", "supergroup"}))
async def call_players(message: Message):
    game = store.get(message.chat.id)
    if not game:
        await message.answer("Сначала начни регистрацию: /start_reg")
        return
    bot_name = (await message.bot.get_me()).first_name
    await message.answer(pick(GLOBAL["call_start"], bot_name=bot_name), reply_markup=join_keyboard(message.chat.id))
    await message.answer(pick(GLOBAL["call_end"]))


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def group_guard(message: Message):
    game = store.get(message.chat.id)
    user = message.from_user
    if not game or not user:
        return
    player = game.get_player(user.id)
    if not player or not player.alive:
        return
    if game.phase == Phase.NIGHT:
        try:
            await message.delete()
        except Exception:
            pass
    elif game.phase in {Phase.DISCUSSION, Phase.VOTING} and player.silenced:
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.bot.send_message(user.id, "🤐 Ты сегодня молчишь: последствия ночного визита.")
        except Exception:
            pass
