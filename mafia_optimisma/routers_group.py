from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .admin import is_chat_admin
from .content import GLOBAL, MODES
from .engine import GameEngine, living_summary, pick
from .keyboards import admin_settings_keyboard, join_keyboard, mode_keyboard
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


async def remember_sender(message: Message) -> None:
    if not engine or not message.from_user or not is_group(message):
        return
    user = message.from_user
    await engine.storage.remember_chat_user(message.chat.id, user.id, user.full_name, user.username)


async def require_admin(message: Message) -> bool:
    if not message.from_user:
        return False
    ok = await is_chat_admin(message.bot, message.chat.id, message.from_user.id)
    if not ok:
        await message.answer("🔐 Эта команда доступна только администраторам чата.")
    return ok


def mode_line(mode: str) -> str:
    data = MODES[mode]
    return f"{data['emoji']} **{data['name']}**"


async def _start_mode(message: Message, mode: str):
    assert engine
    await remember_sender(message)
    game = store.create_or_reset(message.chat.id, message.chat.title or "чат", mode)
    await engine.begin_registration(message.bot, game)


@router.message(Command("start_reg"), F.chat.type.in_({"group", "supergroup"}))
async def start_registration(message: Message, command: CommandObject):
    assert engine
    await remember_sender(message)
    mode = (command.args or "classic").strip().lower()
    if mode not in MODES:
        mode = "classic"
    game = store.create_or_reset(message.chat.id, message.chat.title or "чат", mode)
    await engine.begin_registration(message.bot, game)


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


@router.message(Command("set_mode"), F.chat.type.in_({"group", "supergroup"}))
async def set_mode(message: Message):
    await remember_sender(message)
    if not await require_admin(message):
        return
    game = store.get(message.chat.id)
    if not game:
        await message.answer("Сначала начни регистрацию: /game1, /game2, /game3 или /game4")
        return
    await message.answer("🎮 Выбери режим:", reply_markup=mode_keyboard(message.chat.id))


@router.message(Command("settings"), F.chat.type.in_({"group", "supergroup"}))
async def settings(message: Message):
    await remember_sender(message)
    if not await require_admin(message):
        return
    game = store.get(message.chat.id)
    if game:
        status = f"Текущий режим: {mode_line(game.mode)}\nФаза: `{game.phase.value}`\nИгроков: {len(game.players)}"
    else:
        status = "Активной регистрации нет. Выбери режим и запусти регистрацию."
    await message.answer(
        "⚙️ **Админ-панель Mafia Optimisma**\n\n"
        f"{status}\n\n"
        "Здесь можно выбрать режим, продлить регистрацию, сделать созыв, посмотреть игроков/статистику или запустить игру.",
        reply_markup=admin_settings_keyboard(message.chat.id),
    )


@router.message(Command("join"), F.chat.type.in_({"group", "supergroup"}))
async def join(message: Message):
    assert engine
    await remember_sender(message)
    game = store.get(message.chat.id)
    if not game:
        game = store.create_or_reset(message.chat.id, message.chat.title or "чат", "classic")
        await engine.begin_registration(message.bot, game)
    user = message.from_user
    if not user:
        return
    try:
        await message.bot.send_message(user.id, "🙂 ЛС с ботом открыты. Теперь ты сможешь получить роль и ночные кнопки.")
    except Exception:
        await message.answer("⚠️ Сначала открой ЛС с ботом и нажми /start, потом возвращайся и жми кнопку «Присоединиться».")
        return
    ok, text = await engine.add_player(game, user.id, user.full_name, user.username)
    await engine.storage.remember_chat_user(message.chat.id, user.id, user.full_name, user.username)
    if ok:
        await engine.update_registration_message(message.bot, game)
    await message.answer(text)


@router.message(Command("leave"), F.chat.type.in_({"group", "supergroup"}))
async def leave(message: Message):
    await remember_sender(message)
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
    await engine.update_registration_message(message.bot, game)
    await message.answer(f"💨 {escape(p.name)} не совладал(а) с эмоциями и покинул(а) город.")


@router.message(Command("end_game", "endgame", "end"), F.chat.type.in_({"group", "supergroup"}))
async def end_game_exit(message: Message):
    """Player leaves the current game. /end_game or /end game."""
    assert engine
    await remember_sender(message)
    game = store.get(message.chat.id)
    user = message.from_user
    if not game or not user or user.id not in game.players:
        await message.answer("Ты не в игре.")
        return
    p = game.players[user.id]
    if game.phase == Phase.REGISTRATION:
        game.players.pop(user.id, None)
        store.user_to_chat.pop(user.id, None)
        await engine.update_registration_message(message.bot, game)
    else:
        if not p.alive:
            await message.answer("Ты уже выбыл(а) из игры.")
            return
        p.alive = False
    await message.answer(f"💥 {escape(p.name)} не выдержал(а) натиска мафии и вышел(а) из игры.")
    if game.phase != Phase.REGISTRATION:
        await engine.check_win(message.bot, game)


@router.message(Command("players"), F.chat.type.in_({"group", "supergroup"}))
async def players(message: Message):
    await remember_sender(message)
    game = store.get(message.chat.id)
    if not game:
        await message.answer("Активной регистрации нет.")
        return
    if game.phase == Phase.REGISTRATION:
        names = "\n".join(f"{i}. @{p.username}" if p.username else f"{i}. {escape(p.name)}" for i, p in enumerate(game.players.values(), 1)) or "пока пусто"
        await message.answer(f"👥 **Игроки:**\n{names}\n\nВсего: {len(game.players)}")
    else:
        await message.answer(living_summary(game))


@router.message(Command("start_game"), F.chat.type.in_({"group", "supergroup"}))
async def start_game_command(message: Message):
    await start_game_from_message(message)


@router.message(Command("start"), F.chat.type.in_({"group", "supergroup"}))
async def start_game_alias(message: Message):
    await start_game_from_message(message)


async def start_game_from_message(message: Message):
    assert engine
    await remember_sender(message)
    game = store.get(message.chat.id)
    if not game:
        await message.answer("Нет регистрации. Начни: /game1, /game2, /game3 или /game4")
        return
    if game.phase != Phase.REGISTRATION:
        await message.answer("Игра уже идёт.")
        return
    await engine.start_game(message.bot, game)


@router.message(Command("extend"), F.chat.type.in_({"group", "supergroup"}))
async def extend_registration(message: Message):
    assert engine
    await remember_sender(message)
    game = store.get(message.chat.id)
    if not game or game.phase != Phase.REGISTRATION:
        await message.answer("Сейчас нет активной регистрации, которую можно продлить.")
        return
    engine.schedule_registration(message.bot, game, 30)
    await engine.update_registration_message(message.bot, game)
    await message.answer("⏱ Регистрация продлена на 30 секунд. Таймер автостарта запущен заново.")


@router.message(Command("cancel_reg"), F.chat.type.in_({"group", "supergroup"}))
async def cancel_reg(message: Message):
    await remember_sender(message)
    game = store.get(message.chat.id)
    if not game:
        await message.answer("Активной регистрации нет.")
        return
    store.remove_game(message.chat.id)
    await message.answer("🚫 Регистрация отменена. Город делает вид, что ничего не было.")


def call_mention(user: dict) -> str:
    username = user.get("username")
    if username:
        return f"@{username}"
    return escape(user.get("name") or "Игрок")


def format_call_text(bot_name: str, users: list[dict]) -> str:
    mentions = [call_mention(u) for u in users]
    if mentions:
        body = " ".join(mentions[:80])
    else:
        body = "Пока некого звать: Telegram не даёт боту полный список участников чата. В созыв попадают те, кто уже писал в чате, нажимал кнопку регистрации или использовал команды бота."
    return pick(GLOBAL["call_start"], bot_name=bot_name) + "\n\n" + body + "\n\n" + pick(GLOBAL["call_end"])


@router.message(Command("call"), F.chat.type.in_({"group", "supergroup"}))
async def call_players(message: Message):
    assert engine
    await remember_sender(message)
    bot_name = (await message.bot.get_me()).first_name
    users = await engine.storage.get_callable_users(message.chat.id)
    await message.answer(format_call_text(bot_name, users))


@router.message(Command("unreg"), F.chat.type.in_({"group", "supergroup"}))
async def unreg_call(message: Message):
    assert engine
    user = message.from_user
    if not user:
        return
    await engine.storage.set_call_enabled(message.chat.id, user.id, False, user.full_name, user.username)
    await message.answer(f"🔕 {escape(user.full_name)}, больше не буду упоминать тебя в созыве /call.")


@router.message(Command("reg"), F.chat.type.in_({"group", "supergroup"}))
async def reg_call(message: Message):
    assert engine
    user = message.from_user
    if not user:
        return
    await engine.storage.set_call_enabled(message.chat.id, user.id, True, user.full_name, user.username)
    await message.answer(f"🔔 {escape(user.full_name)}, снова буду упоминать тебя в созыве /call.")


@router.message(Command("stats", "statistics"), F.chat.type.in_({"group", "supergroup"}))
async def stats(message: Message):
    assert engine
    await remember_sender(message)
    rows = await engine.storage.top_profiles(10)
    if not rows:
        await message.answer("📊 Статистики пока нет. Сыграйте первую игру.")
        return
    lines = ["📊 **Топ игроков Mafia Optimisma**\n"]
    for i, row in enumerate(rows, 1):
        name = escape(row.get("name") or row.get("username") or str(row["user_id"]))
        lines.append(f"{i}. {name} — 🏆 {row['wins']} / 🎮 {row['games']} | 🌟 {row['level']} | 💵 {row['money']} | 💎 {row['gems']}")
    await message.answer("\n".join(lines))


@router.message(F.text.lower().in_({"статистика", "/статистика"}), F.chat.type.in_({"group", "supergroup"}))
async def stats_ru_text(message: Message):
    await stats(message)


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def group_guard(message: Message):
    assert engine
    if message.from_user:
        await engine.storage.remember_chat_user(message.chat.id, message.from_user.id, message.from_user.full_name, message.from_user.username)
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
