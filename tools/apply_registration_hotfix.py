from pathlib import Path


def replace_once(text: str, old: str, new: str, marker: str) -> str:
    if marker in text:
        return text
    if old not in text:
        raise SystemExit(f"hotfix target not found: {marker}")
    return text.replace(old, new, 1)


engine_path = Path("mafia_optimisma/engine.py")
engine = engine_path.read_text(encoding="utf-8")

engine = replace_once(
    engine,
    "        await self.start_game(bot, game)\n\n    async def add_player",
    "        await self.start_game(bot, game, drop_unreachable=True)\n\n    async def add_player",
    "drop_unreachable=True",
)
engine = replace_once(
    engine,
    "    async def start_game(self, bot: Bot, game: GameState) -> None:\n",
    "    async def start_game(self, bot: Bot, game: GameState, drop_unreachable: bool = False) -> None:\n",
    "drop_unreachable: bool = False",
)
engine = replace_once(
    engine,
    '''            if unreachable:\n                names = ", ".join(escape(p.name) for p in unreachable)\n                await self._safe_group(bot, game.chat_id, f"⚠️ Не могу начать: откройте ЛС с ботом и нажмите /start — {names}.")\n                return\n''',
    '''            if unreachable:\n                names = ", ".join(escape(p.name) for p in unreachable)\n                if not drop_unreachable:\n                    await self._safe_group(bot, game.chat_id, f"⚠️ Не могу начать: откройте ЛС с ботом и нажмите /start — {names}.")\n                    return\n\n                # A registration timeout must never hang forever because one\n                # participant never opened the bot PM. Remove only unreachable\n                # registrations and continue when enough reachable players remain.\n                for p in unreachable:\n                    game.players.pop(p.user_id, None)\n                    if store.user_to_chat.get(p.user_id) == game.chat_id:\n                        store.user_to_chat.pop(p.user_id, None)\n                await self.persist(game)\n                await self._safe_group(\n                    bot, game.chat_id,\n                    f"⚠️ Не смог отправить роль: {names}. "\n                    "Эти игроки исключены из текущей регистрации, потому что ЛС с ботом не открыты.",\n                )\n                if len(game.players) < min_players:\n                    await self.close_registration_ui(bot, game)\n                    await self._safe_group(\n                        bot, game.chat_id,\n                        f"⏳ Регистрация закрыта. После проверки ЛС осталось {len(game.players)} "\n                        f"игрока(ов), а нужно минимум {min_players}.",\n                    )\n                    await self.storage.delete_game_state(game.chat_id)\n                    store.remove_game(game.chat_id)\n                    return\n''',
    "A registration timeout must never hang forever",
)

# Fix the exact start_game timer cleanup without touching cancel_game, which
# already has the correct current-task guard.
start_idx = engine.index("    async def start_game(")
night_idx = engine.index("    async def _send_roles", start_idx)
start_block = engine[start_idx:night_idx]
if "current_task = asyncio.current_task()" not in start_block:
    old = '''            task = self.tasks.pop(game.chat_id, None)\n            if task and not task.done():\n                task.cancel()\n'''
    new = '''            task = self.tasks.pop(game.chat_id, None)\n            current_task = asyncio.current_task()\n            if task and task is not current_task and not task.done():\n                task.cancel()\n'''
    if old not in start_block:
        raise SystemExit("hotfix target not found: start_game self-cancel")
    start_block = start_block.replace(old, new, 1)
    engine = engine[:start_idx] + start_block + engine[night_idx:]

# Completed timers should not remain registered after a terminal transition.
if "Do not leave completed timers in the registry" not in engine:
    old = '''            except asyncio.CancelledError:\n                return\n\n        self.tasks[game.chat_id] = asyncio.create_task(runner())\n'''
    new = '''            except asyncio.CancelledError:\n                return\n            finally:\n                # Do not leave completed timers in the registry. If the phase\n                # transition armed a new timer, preserve that newer task.\n                task = asyncio.current_task()\n                if self.tasks.get(game.chat_id) is task:\n                    self.tasks.pop(game.chat_id, None)\n\n        self.tasks[game.chat_id] = asyncio.create_task(runner())\n'''
    if old not in engine:
        raise SystemExit("hotfix target not found: timer registry cleanup")
    engine = engine.replace(old, new, 1)

engine_path.write_text(engine, encoding="utf-8")

callbacks_path = Path("mafia_optimisma/routers_callbacks.py")
callbacks = callbacks_path.read_text(encoding="utf-8")
if "First tap is the registration" not in callbacks:
    old = '''    user = callback.from_user\n    # Telegram PM access is checked before taking the game lock; a slow network\n    # request must not block the registration timer for the whole chat.\n    try:\n        await callback.bot.send_chat_action(user.id, "typing")\n    except Exception:\n        await callback.answer("Сначала открой ЛС с ботом и нажми /start.", show_alert=True)\n        return\n\n    async with engine.lock_for(chat_id):\n        game = store.get(chat_id)\n        if not _fresh_game(game, session) or game.phase != Phase.REGISTRATION:\n            await callback.answer("Эта регистрация уже закрыта. Используй новый закреп.", show_alert=True)\n            return\n        await engine.storage.remember_chat_user(chat_id, user.id, user.full_name, user.username)\n        ok, text = await engine.add_player(game, user.id, user.full_name, user.username)\n\n    await callback.answer("Ты в игре!" if ok else text, show_alert=not ok)\n    if ok:\n        # The pinned registration card is the single source of truth in the\n        # group. Joining edits that card instead of adding chat spam.\n        await engine.update_registration_message(callback.bot, game)\n        await engine._safe_pm(callback.bot, user.id, "✅ Ты зарегистрирован(а). Роль придёт сюда после старта партии.")\n'''
    new = '''    user = callback.from_user\n\n    # First tap is the registration. Telegram does not allow a bot to initiate\n    # a private conversation, but that must not force the player to return and\n    # press JOIN a second time after /start. Register first, then only use the PM\n    # probe to tell the player whether they need to open the bot before game start.\n    async with engine.lock_for(chat_id):\n        game = store.get(chat_id)\n        if not _fresh_game(game, session) or game.phase != Phase.REGISTRATION:\n            await callback.answer("Эта регистрация уже закрыта. Используй новый закреп.", show_alert=True)\n            return\n        await engine.storage.remember_chat_user(chat_id, user.id, user.full_name, user.username)\n        ok, text = await engine.add_player(game, user.id, user.full_name, user.username)\n\n    if not ok:\n        await callback.answer(text, show_alert=True)\n        return\n\n    await engine.update_registration_message(callback.bot, game)\n    pm_open = True\n    try:\n        await callback.bot.send_chat_action(user.id, "typing")\n    except Exception:\n        pm_open = False\n\n    if pm_open:\n        await callback.answer("Ты в игре!")\n        await engine._safe_pm(callback.bot, user.id, "✅ Ты зарегистрирован(а). Роль придёт сюда после старта партии.")\n    else:\n        await callback.answer(\n            "✅ Ты уже зарегистрирован(а). До старта игры открой ЛС с ботом и нажми /start — повторно жать «Присоединиться» не нужно.",\n            show_alert=True,\n        )\n'''
    if old not in callbacks:
        raise SystemExit("hotfix target not found: cb_join")
    callbacks = callbacks.replace(old, new, 1)
callbacks_path.write_text(callbacks, encoding="utf-8")

print("registration hotfix applied")
