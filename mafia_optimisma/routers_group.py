from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .admin import is_chat_admin
from .content import GLOBAL, MODES
from .engine import GameEngine, living_summary, pick, role_team
from .keyboards import admin_settings_keyboard, join_keyboard, mode_keyboard
from .models import Phase
from .state import store

router = Router(name="group")
engine: GameEngine | None = None


def setup(game_engine: GameEngine) -> Router:
    global engine
    engine = game_engine
    return router


async def remember_sender(message: Message) -> None:
    if not message.from_user or message.chat.type not in {"group", "supergroup"}:
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


@router.message(Command("game1", "game2", "game3", "game4"))
async def start_game_mode(message: Message):
    await remember_sender(message)
    mode_map = {"game1": "classic", "game2": "chaos", "game3": "virus", "game4": "clans"}
    mode = mode_map.get(message.text[1:], "classic")
    game = store.create_or_reset(message.chat.id, message.chat.title or "чат", mode)
    await engine.begin_registration(message.bot, game)
    await message.answer(f"🎮 Регистрация в **{MODES[mode]['emoji']} {MODES[mode]['name']}** началась!")


@router.message(Command("extend"))
async def extend_registration(message: Message):
    await remember_sender(message)
    game = store.get(message.chat.id)
    if not game or game.phase != Phase.REGISTRATION:
        await message.answer("Сейчас нет активной регистрации.")
        return
    engine.schedule_registration(message.bot, game, seconds=30)
    await engine.update_registration_message(message.bot, game)
    await message.answer("⏱ Регистрация продлена на 30 секунд.")


@router.message(Command("join"))
async def join(message: Message):
    await remember_sender(message)
    game = store.get(message.chat.id)
    if not game:
        game = store.create_or_reset(message.chat.id, message.chat.title or "чат", "classic")
        await engine.begin_registration(message.bot, game)
    await message.answer("Нажми кнопку «➕ Присоединиться» в закреплённом сообщении.")


@router.message(Command("leave"))
async def leave(message: Message):
    await remember_sender(message)
    game = store.get(message.chat.id)
    user = message.from_user
    if not game or not user or user.id not in game.players:
        await message.answer("Ты не в игре.")
        return
    if game.phase != Phase.REGISTRATION:
        await message.answer("Игра уже началась. Выходить поздно.")
        return
    p = game.players.pop(user.id)
    store.user_to_chat.pop(user.id, None)
    await engine.update_registration_message(message.bot, game)
    await message.answer(f"💨 {escape(p.name)} покинул город.")


@router.message(Command("end_game", "endgame", "end"))
async def end_game_exit(message: Message):
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
            await message.answer("Ты уже выбыл.")
            return
        p.alive = False
    await message.answer(f"💥 {escape(p.name)} не выдержал натиска мафии и вышел из игры.")
    if game.phase != Phase.REGISTRATION:
        await engine.check_win(message.bot, game)


@router.message(Command("call"))
async def call_players(message: Message):
    assert engine
    await remember_sender(message)
    bot_name = (await message.bot.get_me()).first_name
    users = await engine.storage.get_callable_users(message.chat.id)
    if not users:
        await message.answer("Пока некого звать. Участники появляются после того, как нажмут кнопку или напишут в чат.")
        return
    mentions = [f"@{u['username']}" if u.get("username") else escape(u["name"]) for u in users]
    text = pick(GLOBAL["call_start"], bot_name=bot_name) + "\n\n" + " ".join(mentions[:80]) + "\n\n" + pick(GLOBAL["call_end"])
    await message.answer(text)


@router.message(Command("unreg"))
async def unreg_call(message: Message):
    assert engine
    user = message.from_user
    if not user:
        return
    await engine.storage.set_call_enabled(message.chat.id, user.id, False)
    await message.answer(f"🔕 {escape(user.full_name)}, больше не буду упоминать тебя в /call.")


@router.message(Command("reg"))
async def reg_call(message: Message):
    assert engine
    user = message.from_user
    if not user:
        return
    await engine.storage.set_call_enabled(message.chat.id, user.id, True)
    await message.answer(f"🔔 {escape(user.full_name)}, снова буду упоминать тебя в /call.")


@router.message(Command("stats", "statistics"))
async def stats(message: Message):
    assert engine
    await remember_sender(message)
    rows = await engine.storage.top_profiles(10)
    if not rows:
        await message.answer("📊 Статистики пока нет.")
        return
    lines = ["📊 **Топ игроков Mafia Optimisma**\n"]
    for i, row in enumerate(rows, 1):
        name = escape(row.get("name") or row.get("username") or str(row["user_id"]))
        lines.append(f"{i}. {name} — 🏆 {row['wins']} / 🎮 {row['games']} | 🌟 {row['level']}")
    await message.answer("\n".join(lines))


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def group_guard(message: Message):
    assert engine
    if message.from_user:
        await engine.storage.remember_chat_user(message.chat.id, message.from_user.id, message.from_user.full_name, message.from_user.username)

    game = store.get(message.chat.id)
    if not game or not message.from_user:
        return
    player = game.get_player(message.from_user.id)
    if not player or not player.alive:
        return

    if game.phase == Phase.NIGHT or (game.phase in {Phase.DISCUSSION, Phase.VOTING} and player.silenced):
        try:
            await message.delete()
        except Exception:
            pass
        if player.silenced:
            try:
                await message.bot.send_message(message.from_user.id, "🤐 Ты сегодня молчишь.")
            except Exception:
                pass


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def team_chat_handler(message: Message):
    assert engine
    if not message.from_user or not message.text:
        return
    game = store.get(message.chat.id)
    if not game or game.phase != Phase.NIGHT:
        return
    player = game.get_player(message.from_user.id)
    if not player or not player.alive:
        return
    team = role_team(player.role_key)
    if team not in {"mafia", "yakuza"}:
        return
    sent = await engine.team_chat(message.bot, game, player, message.text)
    if sent:
        await message.answer("✅ Сообщение отправлено команде.")
    else:
        await message.answer("В твоей команде пока никого нет.")
