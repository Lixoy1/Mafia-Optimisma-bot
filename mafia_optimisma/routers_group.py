from __future__ import annotations

from html import escape
import re

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .admin import is_chat_admin
from .content import GLOBAL, MODES, ROLES
from .engine import GameEngine, living_summary, pick
from .keyboards import admin_settings_keyboard, join_keyboard, mode_keyboard
from .models import Phase
from .rankings import current_week_leaderboard, full_statistics, render_current_week, render_full_statistics
from .state import store

router = Router(name="group")
engine: GameEngine | None = None
_guard_installed = False

# During a live party only these operational commands may be sent by an admin
# who is not a living player. Everything else from spectators/dead players is
# removed by the outer middleware before a command handler can consume it.
LIVE_ADMIN_COMMANDS = {
    "settings", "set_mode", "stop", "cancel_reg", "extend",
    "start", "start_game", "admin_notify",
}


def _command_name(message: Message) -> str | None:
    text = (getattr(message, "text", None) or "").strip()
    if not text.startswith("/"):
        return None
    token = text.split(maxsplit=1)[0][1:]
    return token.split("@", 1)[0].lower() or None


_PROFANITY_RE = re.compile(
    r"(?iu)(?<![а-яё])(?:"
    r"бля(?:д[ьи]?|ть)?|сука|сук[аи]|суч(?:ка|ий|ара)|"
    r"(?:о|на|за|по|вы|до|при|про|у|разъ|подъ)?ху(?:й|я|е|ё|и)[а-яё]*|"
    r"(?:за|на|по|вы|про|пере|подъ|разъ|у)?[её]б[а-яё]*|"
    r"пизд[а-яё]*|мудак[а-яё]*|долбо[её]б[а-яё]*|гандон[а-яё]*|шлюх[а-яё]*"
    r")(?![а-яё])"
)
_LINK_RE = re.compile(
    r"(?iu)(?:https?://|www\.|t\.me/|telegram\.me/|"
    r"(?<![\w@])(?:[a-z0-9-]+\.)+(?:com|ru|net|org|io|gg|me|app|dev|nl|de)(?:/|\b))"
)


def _game_chat_feature(game, key: str, default: bool = False) -> bool:
    cfg = game.temp.get("_chat_settings", {}) if game else {}
    if not isinstance(cfg, dict):
        return default
    raw = cfg.get(key, default)
    if isinstance(raw, str):
        return raw.strip().lower() not in {"0", "false", "off", "no"}
    return bool(raw)


def _contains_profanity(text: str | None) -> bool:
    return bool(text and _PROFANITY_RE.search(text))


def _message_has_link(message: Message) -> bool:
    for entity in list(getattr(message, "entities", None) or []) + list(getattr(message, "caption_entities", None) or []):
        kind = str(getattr(entity, "type", "")).lower()
        if kind in {"url", "text_link"} or kind.endswith(".url") or kind.endswith(".text_link"):
            return True
    text = " ".join(filter(None, [getattr(message, "text", None), getattr(message, "caption", None)]))
    return bool(_LINK_RE.search(text))


def _moderation_reason(message: Message, game) -> str | None:
    if _game_chat_feature(game, "block_stickers", False) and getattr(message, "sticker", None) is not None:
        return "🖼 В этой партии администратор отключил стикеры в игровом чате."
    text = " ".join(filter(None, [getattr(message, "text", None), getattr(message, "caption", None)]))
    if _game_chat_feature(game, "block_links", False) and _message_has_link(message):
        return "🔗 В этой партии ссылки в игровом чате запрещены."
    if _game_chat_feature(game, "block_profanity", False) and _contains_profanity(text):
        return "🤬 В этой партии включён фильтр мата. Сообщение удалено — переформулируй без него."
    return None

async def _delete_live_chat_message(message: Message, game, private_text: str) -> None:
    global engine
    try:
        await message.delete()
    except Exception:
        # A Telegram bot cannot prevent a message from being sent; it can only
        # delete it immediately. If the admin permission is missing, make that
        # visible once per phase instead of silently pretending the guard works.
        key = f"chat_guard_delete_failed:{game.phase.value}:{game.day}"
        if not game.temp.get(key):
            game.temp[key] = True
            try:
                await message.bot.send_message(
                    game.chat_id,
                    "⚠️ <b>Защита игрового чата не может удалить сообщения.</b>\n"
                    "Дайте боту права администратора → «Удаление сообщений». "
                    "После этого зрители и выбывшие будут автоматически блокироваться в чате.",
                )
            except Exception:
                pass
            if engine is not None:
                await engine.persist(game)
        return

    user = getattr(message, "from_user", None)
    if user:
        try:
            await message.bot.send_message(user.id, private_text)
        except Exception:
            pass


class LiveGameChatGuard(BaseMiddleware):
    async def __call__(self, handler, event: Message, data):
        if getattr(getattr(event, "chat", None), "type", None) not in {"group", "supergroup"}:
            return await handler(event, data)

        game = store.get(event.chat.id)
        user = getattr(event, "from_user", None)
        if (
            not game or not user or getattr(user, "is_bot", False)
            or game.phase in {Phase.REGISTRATION, Phase.FINISHED}
        ):
            return await handler(event, data)

        # Admin operational controls must remain usable even when that admin is
        # only observing the current party. All other spectator messages/commands
        # are rejected before ordinary handlers run.
        command = _command_name(event)
        if command in LIVE_ADMIN_COMMANDS and await is_chat_admin(event.bot, event.chat.id, user.id):
            return await handler(event, data)

        player = game.get_player(user.id)
        if not player or not player.alive:
            await _delete_live_chat_message(
                event, game, "❌ Во время партии писать в игровой чат могут только живые участники игры.",
            )
            return None

        if game.phase == Phase.NIGHT:
            await _delete_live_chat_message(event, game, "❌ Ночью город спит — сообщения в группе закрыты.")
            return None

        if game.phase in {Phase.DISCUSSION, Phase.NOMINATION, Phase.VERDICT, Phase.RESOLVING} and player.silenced:
            await _delete_live_chat_message(
                event, game, "🤐 Ночной эффект лишил тебя права говорить и голосовать до конца дня.",
            )
            return None

        moderation_reason = _moderation_reason(event, game)
        if moderation_reason:
            await _delete_live_chat_message(event, game, moderation_reason)
            return None

        return await handler(event, data)


def setup(game_engine: GameEngine) -> Router:
    global engine, _guard_installed
    engine = game_engine
    if not _guard_installed:
        # Outer middleware runs before filters and command handlers, so a
        # spectator cannot bypass the game guard with /roles, /stats, etc.
        router.message.outer_middleware(LiveGameChatGuard())
        _guard_installed = True
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
    return f"{data['emoji']} <b>{data['name']}</b>"


async def _start_mode(message: Message, mode: str):
    assert engine
    await remember_sender(message)
    async with engine.lock_for(message.chat.id):
        current = store.get(message.chat.id)
        if current:
            if current.phase == Phase.REGISTRATION:
                response = "🎲 Регистрация уже идёт. Для смены режима используй /set_mode, либо сначала /stop."
            else:
                response = "⛔ В этом чате уже идёт партия. Новая регистрация не может стереть текущую игру."
            game = None
        else:
            response = None
            game = store.create_or_reset(message.chat.id, message.chat.title or "чат", mode)
            await engine.begin_registration(message.bot, game)
    if response:
        await message.answer(response)


@router.message(Command("start_reg"), F.chat.type.in_({"group", "supergroup"}))
async def start_registration(message: Message, command: CommandObject):
    assert engine
    await remember_sender(message)
    mode = (command.args or "classic").strip().lower()
    if mode not in MODES:
        mode = "classic"
    await _start_mode(message, mode)


@router.message(Command("game", "game1"), F.chat.type.in_({"group", "supergroup"}))
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
        await message.answer("Сначала начни регистрацию: /game, /game2, /game3 или /game4")
        return
    await message.answer("🎮 Выбери режим:", reply_markup=mode_keyboard(message.chat.id))


@router.message(Command("settings"), F.chat.type.in_({"group", "supergroup"}))
async def settings(message: Message):
    assert engine
    user = message.from_user
    if not user:
        return
    await remember_sender(message)
    is_admin = await is_chat_admin(message.bot, message.chat.id, user.id)
    try:
        await message.delete()
    except Exception:
        pass
    if not is_admin:
        try:
            await message.bot.send_message(user.id, "🔐 Настройки этой группы доступны только её владельцу и администраторам.")
        except Exception:
            pass
        return

    game = store.get(message.chat.id)
    if game:
        status = (
            f"🎮 <b>Режим:</b> {mode_line(game.mode)}\n"
            f"🎬 <b>Состояние:</b> <code>{game.phase.value}</code>\n"
            f"👥 <b>Игроков:</b> {len(game.players)}"
        )
    else:
        status = "🎬 <b>Состояние:</b> игра/регистрация сейчас не запущена"
    try:
        cfg = await engine.storage.get_chat_settings(message.chat.id)
    except Exception:
        cfg = {}
    reg = int(cfg.get("registration_seconds", engine.settings.registration_seconds))
    night = int(cfg.get("night_seconds", engine.settings.night_seconds))
    day = int(cfg.get("discussion_seconds", engine.settings.discussion_seconds))
    panel = (
        "⚙️ <b>Mafia Optimisma · Настройки</b>\n"
        f"🏙 <b>Чат:</b> {escape(message.chat.title or 'Игровой чат')}\n\n"
        f"{status}\n\n"
        f"⏱ Следующая игра: регистрация {reg}с · ночь {night}с · день {day}с\n\n"
        "ℹ️ Настройки, изменённые во время партии, применяются только к следующей игре.\n\n"
        "Выбери раздел ниже. Эта панель видна только администраторам в ЛС."
    )
    sent = await engine._safe_pm(
        message.bot, user.id, panel, reply_markup=admin_settings_keyboard(message.chat.id)
    )
    if sent is None:
        notice = await engine._safe_group(
            message.bot, message.chat.id,
            f"⚠️ {escape(user.full_name)}, сначала открой ЛС с ботом и нажми /start, затем повтори /settings."
        )
        if notice:
            async def cleanup():
                import asyncio
                await asyncio.sleep(8)
                await engine._safe_delete(message.bot, message.chat.id, notice.message_id)
            import asyncio
            asyncio.create_task(cleanup())


@router.message(Command("join"), F.chat.type.in_({"group", "supergroup"}))
async def join(message: Message):
    assert engine
    await remember_sender(message)
    user = message.from_user
    if not user:
        return
    try:
        await message.bot.send_chat_action(user.id, "typing")
    except Exception:
        await message.answer("⚠️ Сначала открой ЛС с ботом и нажми /start, потом возвращайся и жми кнопку «Присоединиться».")
        return

    async with engine.lock_for(message.chat.id):
        game = store.get(message.chat.id)
        if not game:
            game = store.create_or_reset(message.chat.id, message.chat.title or "чат", "classic")
            await engine.begin_registration(message.bot, game)
        if game.phase != Phase.REGISTRATION:
            ok, text = False, "Регистрация уже закрыта."
        else:
            ok, text = await engine.add_player(game, user.id, user.full_name, user.username)
            await engine.storage.remember_chat_user(message.chat.id, user.id, user.full_name, user.username)
    if ok:
        await engine.update_registration_message(message.bot, game)
    await message.answer(text)


@router.message(Command("leave"), F.chat.type.in_({"group", "supergroup"}))
async def leave(message: Message):
    assert engine
    await remember_sender(message)
    user = message.from_user
    if not user:
        return
    async with engine.lock_for(message.chat.id):
        game = store.get(message.chat.id)
        if not game or user.id not in game.players:
            response = "Ты не в игре."
            p = None
        elif game.phase != Phase.REGISTRATION:
            response = "Игра уже началась. Выходить поздно, город всё видел."
            p = None
        else:
            p = game.players.pop(user.id)
            if store.user_to_chat.get(user.id) == message.chat.id:
                store.user_to_chat.pop(user.id, None)
            await engine.persist(game)
            response = None
    if p:
        await engine.update_registration_message(message.bot, game)
        await message.answer(f"💨 {escape(p.name)} не совладал(а) с эмоциями и покинул(а) город.")
    else:
        await message.answer(response)


@router.message(Command("end_game", "endgame", "end"), F.chat.type.in_({"group", "supergroup"}))
async def end_game_exit(message: Message):
    """Hidden compatibility command for voluntarily leaving a party."""
    assert engine
    await remember_sender(message)
    user = message.from_user
    if not user:
        return
    check_after = False
    update_registration = False
    async with engine.lock_for(message.chat.id):
        game = store.get(message.chat.id)
        if not game or user.id not in game.players:
            response, p = "Ты не в игре.", None
        else:
            p = game.players[user.id]
            if game.phase == Phase.REGISTRATION:
                game.players.pop(user.id, None)
                if store.user_to_chat.get(user.id) == message.chat.id:
                    store.user_to_chat.pop(user.id, None)
                update_registration = True
                response = None
            elif not p.alive:
                response, p = "Ты уже выбыл(а) из игры.", None
            else:
                p.alive = False
                game.pending_last_words.discard(user.id)
                check_after = True
                response = None
            if p:
                await engine.persist(game)
    if not p:
        await message.answer(response)
        return
    if update_registration:
        await engine.update_registration_message(message.bot, game)
    await message.answer(f"💥 {escape(p.name)} не выдержал(а) натиска мафии и вышел(а) из игры.")
    if check_after:
        await engine.check_win(message.bot, game)


@router.message(Command("players"), F.chat.type.in_({"group", "supergroup"}))
async def players(message: Message):
    await remember_sender(message)
    game = store.get(message.chat.id)
    if not game:
        await message.answer("Активной регистрации нет.")
        return
    if game.phase == Phase.REGISTRATION:
        names = "\n".join(
            f"{p.number}. @{p.username}" if p.username else f"{p.number}. {escape(p.name)}"
            for p in sorted(game.players.values(), key=lambda x: (x.number or 10**9, x.user_id))
        ) or "пока пусто"
        await message.answer(f"👥 <b>Игроки:</b>\n{names}\n\nВсего: {len(game.players)}")
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
        await message.answer("Нет регистрации. Начни: /game, /game2, /game3 или /game4")
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
    extended = await engine.extend_registration(message.bot, game, 30)
    await message.answer("⏱ Регистрация продлена на 30 секунд." if extended else "Регистрация уже закрылась.")


@router.message(Command("cancel_reg", "stop"), F.chat.type.in_({"group", "supergroup"}))
async def cancel_reg(message: Message):
    assert engine
    await remember_sender(message)
    if not await require_admin(message):
        return
    game = store.get(message.chat.id)
    if not game or game.phase != Phase.REGISTRATION:
        await message.answer("Активной регистрации нет. Идущую партию этой командой отменить нельзя.")
        return
    cancelled = await engine.cancel_game(message.bot, message.chat.id)
    await message.answer(
        "🚫 Регистрация отменена. Город делает вид, что ничего не было."
        if cancelled else "Регистрация уже закрылась."
    )


def call_mention(user: dict) -> str:
    username = user.get("username")
    if username:
        return f"@{username}"
    return escape(user.get("name") or "Игрок")


def format_call_text(bot_name: str, users: list[dict]) -> str:
    mentions = [call_mention(u) for u in users]
    if mentions:
        body = " ".join(mentions[:30])
    else:
        body = "Пока некого звать: Telegram не даёт боту полный список участников чата. В созыв попадают те, кто уже писал в чате, нажимал кнопку регистрации или использовал команды бота."
    return pick(GLOBAL["call_start"], bot_name=bot_name) + "\n\n" + body + "\n\n" + pick(GLOBAL["call_end"])


@router.message(Command("call", "gogame"), F.chat.type.in_({"group", "supergroup"}))
async def call_players(message: Message):
    assert engine
    await remember_sender(message)
    bot_name = (await message.bot.get_me()).first_name
    users = await engine.storage.get_callable_users(message.chat.id, limit=30)
    await message.answer(format_call_text(bot_name, users))


@router.message(Command("admin_notify"), F.chat.type.in_({"group", "supergroup"}))
async def admin_notify(message: Message):
    assert engine
    await remember_sender(message)
    if not await require_admin(message):
        return
    bot_name = (await message.bot.get_me()).first_name
    users = await engine.storage.get_callable_users(message.chat.id, limit=30)
    await message.answer(format_call_text(bot_name, users))


@router.message(Command("notify"), F.chat.type.in_({"group", "supergroup"}))
async def notify(message: Message):
    assert engine
    user = message.from_user
    if not user:
        return
    enabled = await engine.storage.toggle_notify(
        message.chat.id, user.id, user.full_name, user.username
    )
    if enabled:
        await message.answer(f"🔔 {escape(user.full_name)}, уведомления о новых регистрациях включены.")
    else:
        await message.answer(f"🔕 {escape(user.full_name)}, уведомления о новых регистрациях отключены.")


@router.message(Command("roles"), F.chat.type.in_({"group", "supergroup"}))
async def roles_group(message: Message):
    lines = ["📖 <b>Бестиарий Mafia Optimisma</b>\n"]
    for role in ROLES.values():
        lines.append(f"{role.title} — {role.short_description}")
    text = "\n".join(lines)
    for i in range(0, len(text), 3900):
        await message.answer(text[i:i + 3900])


@router.message(Command("mystats"), F.chat.type.in_({"group", "supergroup"}))
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
        f"📊 <b>Статистика {escape(user.full_name)}</b>\n"
        f"🎮 Игры: {games}\n"
        f"🏆 Победы: {wins}\n"
        f"📈 Винрейт: {rate:.1f}%\n"
        f"🌟 Уровень: {p.get('level', 1)}"
    )


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
    top, counts, total = await full_statistics(engine.storage, 10)
    await message.answer(render_full_statistics(top, counts, total))


@router.message(Command("week", "weekly", "topweek"), F.chat.type.in_({"group", "supergroup"}))
async def weekly_stats(message: Message):
    assert engine
    await remember_sender(message)
    rows, start, end = await current_week_leaderboard(engine.storage, 10)
    await message.answer(render_current_week(rows, start, end))


@router.message(F.text.lower().in_({"статистика", "/статистика"}), F.chat.type.in_({"group", "supergroup"}))
async def stats_ru_text(message: Message):
    await stats(message)


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def group_guard(message: Message):
    assert engine
    user = message.from_user
    if user:
        await engine.storage.remember_chat_user(
            message.chat.id, user.id, user.full_name, user.username
        )
    game = store.get(message.chat.id)
    if not game or not user or getattr(user, "is_bot", False):
        return
    if game.phase in {Phase.REGISTRATION, Phase.FINISHED}:
        return

    player = game.get_player(user.id)
    # During a live party the game chat belongs to active players. Dead players
    # and spectators cannot influence the discussion. Commands are left alone so
    # admins can still use operational controls.
    if not player or not player.alive:
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.bot.send_message(user.id, "❌ Вы не в игре, вам запрещено разговаривать!")
        except Exception:
            pass
        return

    if game.phase == Phase.NIGHT:
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.bot.send_message(user.id, "❌ Ночью все спят и молчат!")
        except Exception:
            pass
        return

    if game.phase in {Phase.DISCUSSION, Phase.NOMINATION, Phase.VERDICT, Phase.RESOLVING} and player.silenced:
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.bot.send_message(user.id, "🤐 Ночной эффект ещё действует: до конца дня нельзя общаться и голосовать.")
        except Exception:
            pass
        return

    moderation_reason = _moderation_reason(message, game)
    if moderation_reason:
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.bot.send_message(user.id, moderation_reason)
        except Exception:
            pass
        return

