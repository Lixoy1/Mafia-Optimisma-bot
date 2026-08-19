from pathlib import Path


def replace_once(text: str, old: str, new: str, marker: str) -> str:
    if marker in text:
        return text
    if old not in text:
        raise SystemExit(f"hotfix target not found: {marker}")
    return text.replace(old, new, 1)


# 1) Enforce the live-game chat guard before command handlers/filters.
group_path = Path("mafia_optimisma/routers_group.py")
group = group_path.read_text(encoding="utf-8")

group = replace_once(
    group,
    "from aiogram import F, Router\n",
    "from aiogram import BaseMiddleware, F, Router\n",
    "BaseMiddleware, F, Router",
)

if "class LiveGameChatGuard(BaseMiddleware):" not in group:
    anchor = '''router = Router(name="group")\nengine: GameEngine | None = None\n\n\ndef setup(game_engine: GameEngine) -> Router:\n    global engine\n    engine = game_engine\n    return router\n'''
    replacement = '''router = Router(name="group")\nengine: GameEngine | None = None\n_guard_installed = False\n\n# During a live party only these operational commands may be sent by an admin\n# who is not a living player. Everything else from spectators/dead players is\n# removed by the outer middleware before a command handler can consume it.\nLIVE_ADMIN_COMMANDS = {\n    "settings", "set_mode", "stop", "cancel_reg", "extend",\n    "start", "start_game", "admin_notify",\n}\n\n\ndef _command_name(message: Message) -> str | None:\n    text = (getattr(message, "text", None) or "").strip()\n    if not text.startswith("/"):\n        return None\n    token = text.split(maxsplit=1)[0][1:]\n    return token.split("@", 1)[0].lower() or None\n\n\nasync def _delete_live_chat_message(message: Message, game, private_text: str) -> None:\n    global engine\n    try:\n        await message.delete()\n    except Exception:\n        # A Telegram bot cannot prevent a message from being sent; it can only\n        # delete it immediately. If the admin permission is missing, make that\n        # visible once per phase instead of silently pretending the guard works.\n        key = f"chat_guard_delete_failed:{game.phase.value}:{game.day}"\n        if not game.temp.get(key):\n            game.temp[key] = True\n            try:\n                await message.bot.send_message(\n                    game.chat_id,\n                    "⚠️ <b>Защита игрового чата не может удалить сообщения.</b>\\n"\n                    "Дайте боту права администратора → «Удаление сообщений». "\n                    "После этого зрители и выбывшие будут автоматически блокироваться в чате.",\n                )\n            except Exception:\n                pass\n            if engine is not None:\n                await engine.persist(game)\n        return\n\n    user = getattr(message, "from_user", None)\n    if user:\n        try:\n            await message.bot.send_message(user.id, private_text)\n        except Exception:\n            pass\n\n\nclass LiveGameChatGuard(BaseMiddleware):\n    async def __call__(self, handler, event: Message, data):\n        if getattr(getattr(event, "chat", None), "type", None) not in {"group", "supergroup"}:\n            return await handler(event, data)\n\n        game = store.get(event.chat.id)\n        user = getattr(event, "from_user", None)\n        if (\n            not game or not user or getattr(user, "is_bot", False)\n            or game.phase in {Phase.REGISTRATION, Phase.FINISHED}\n        ):\n            return await handler(event, data)\n\n        # Admin operational controls must remain usable even when that admin is\n        # only observing the current party. All other spectator messages/commands\n        # are rejected before ordinary handlers run.\n        command = _command_name(event)\n        if command in LIVE_ADMIN_COMMANDS and await is_chat_admin(event.bot, event.chat.id, user.id):\n            return await handler(event, data)\n\n        player = game.get_player(user.id)\n        if not player or not player.alive:\n            await _delete_live_chat_message(\n                event, game, "❌ Во время партии писать в игровой чат могут только живые участники игры.",\n            )\n            return None\n\n        if game.phase == Phase.NIGHT:\n            await _delete_live_chat_message(event, game, "❌ Ночью город спит — сообщения в группе закрыты.")\n            return None\n\n        if game.phase in {Phase.DISCUSSION, Phase.NOMINATION, Phase.VERDICT, Phase.RESOLVING} and player.silenced:\n            await _delete_live_chat_message(\n                event, game, "❌ Ночная Дива лишила тебя права говорить до конца дня.",\n            )\n            return None\n\n        return await handler(event, data)\n\n\ndef setup(game_engine: GameEngine) -> Router:\n    global engine, _guard_installed\n    engine = game_engine\n    if not _guard_installed:\n        # Outer middleware runs before filters and command handlers, so a\n        # spectator cannot bypass the game guard with /roles, /stats, etc.\n        router.message.outer_middleware(LiveGameChatGuard())\n        _guard_installed = True\n    return router\n'''
    if anchor not in group:
        raise SystemExit("hotfix target not found: group middleware anchor")
    group = group.replace(anchor, replacement, 1)

# Remove the old spectator-command escape hatch from the last-resort handler.
group = group.replace(
    '''    if not player or not player.alive:\n        if message.text and message.text.startswith("/"):\n            return\n        try:\n            await message.delete()\n''',
    '''    if not player or not player.alive:\n        try:\n            await message.delete()\n''',
    1,
)
group_path.write_text(group, encoding="utf-8")


# 2) Every living role gets a fresh private night message. Passive roles get an
# explicit role reminder instead of a context-free "sleep" phrase.
engine_path = Path("mafia_optimisma/engine.py")
engine = engine_path.read_text(encoding="utf-8")
if "Passive roles still receive a fresh role reminder every night" not in engine:
    old = '''            game.night_pm_message_ids.clear()\n            for p in game.alive_players():\n                role = ROLES[p.role_key or "optimist"]\n                kb = night_action_keyboard(game, p)\n                text = random.choice(role.night_prompts)\n                try:\n                    msg = await bot.send_message(p.user_id, text, reply_markup=kb) if kb else await bot.send_message(p.user_id, text)\n                    if kb and msg:\n                        game.night_pm_message_ids[p.user_id] = msg.message_id\n                except Exception:\n                    continue\n'''
    new = '''            game.night_pm_message_ids.clear()\n            for p in game.alive_players():\n                role = ROLES[p.role_key or "optimist"]\n                kb = night_action_keyboard(game, p)\n                prompt = random.choice(role.night_prompts) if role.night_prompts else "Этой ночью у тебя нет отдельного действия."\n                if kb:\n                    text = prompt\n                else:\n                    # Passive roles still receive a fresh role reminder every night.\n                    # This also makes the PM useful when the user opens it via the\n                    # group's «Перейти в бота» button on Night 2+.\n                    text = (\n                        f"🌙 <b>Ночь {game.day}</b>\\n"\n                        f"Ты — <b>{role.title}</b>.\\n\\n"\n                        f"{escape(prompt)}"\n                    )\n                msg = await self._safe_pm(bot, p.user_id, text, reply_markup=kb)\n                if kb and msg:\n                    game.night_pm_message_ids[p.user_id] = msg.message_id\n'''
    if old not in engine:
        raise SystemExit("hotfix target not found: night PM loop")
    engine = engine.replace(old, new, 1)
engine_path.write_text(engine, encoding="utf-8")


# 3) Hide settings from ordinary group command menus and publish the admin menu
# in Telegram's administrators-only command scope. Runtime handlers already
# validate creator/administrator status; this makes the UI match the rule too.
main_path = Path("mafia_optimisma/main.py")
main = main_path.read_text(encoding="utf-8")
main = replace_once(
    main,
    "from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats\n",
    "from aiogram.types import (\n    BotCommand, BotCommandScopeAllChatAdministrators,\n    BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats,\n)\n",
    "BotCommandScopeAllChatAdministrators",
)

if "admin_commands = group_commands + [" not in main:
    main = main.replace(
        '        BotCommand(command="stop", description="Отменить регистрацию"),\n',
        '',
        1,
    )
    main = main.replace(
        '        BotCommand(command="settings", description="Настройки игры"),\n',
        '',
        1,
    )
    old = '''    ]\n    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())\n    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())\n'''
    new = '''    ]\n    admin_commands = group_commands + [\n        BotCommand(command="settings", description="Настройки игры"),\n        BotCommand(command="set_mode", description="Сменить режим регистрации"),\n        BotCommand(command="stop", description="Отменить регистрацию"),\n        BotCommand(command="admin_notify", description="Админский созыв игроков"),\n    ]\n    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())\n    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())\n    await bot.set_my_commands(admin_commands, scope=BotCommandScopeAllChatAdministrators())\n'''
    if old not in main:
        raise SystemExit("hotfix target not found: bot command scopes")
    main = main.replace(old, new, 1)
main_path.write_text(main, encoding="utf-8")

print("live game rules hotfix applied")
