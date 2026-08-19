from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def insert_before(path: str, marker: str, block: str) -> None:
    text = read(path)
    if block.strip() in text:
        return
    if marker not in text:
        raise RuntimeError(f"marker not found in {path}: {marker[:80]!r}")
    write(path, text.replace(marker, block + "\n\n" + marker, 1))


def replace_between(path: str, start: str, end: str, block: str) -> None:
    text = read(path)
    if block.strip() in text:
        return
    i = text.find(start)
    if i < 0:
        raise RuntimeError(f"start marker not found in {path}: {start[:80]!r}")
    j = text.find(end, i)
    if j < 0:
        raise RuntimeError(f"end marker not found in {path}: {end[:80]!r}")
    write(path, text[:i] + block + "\n\n" + text[j:])


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"source block not found in {path}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))


# ---------------------------------------------------------------------------
# Keyboards: profile/statistics + per-group notification choice.
# ---------------------------------------------------------------------------
insert_before(
    "mafia_optimisma/keyboards.py",
    "CONFIGURABLE_ROLE_KEYS = [",
    '''def post_game_keyboard(chat_id: int, notify_state: bool | None = None) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(text="👤 Профиль", callback_data="pm:profile"),
        InlineKeyboardButton(text="📊 Моя статистика", callback_data="pm:stats"),
    ]]
    if notify_state is True:
        rows.append([InlineKeyboardButton(
            text="🔕 Не звать на следующую игру",
            callback_data=f"notify:set:{chat_id}:0",
        )])
    elif notify_state is False:
        rows.append([InlineKeyboardButton(
            text="🔔 Позвать на следующую игру",
            callback_data=f"notify:set:{chat_id}:1",
        )])
    else:
        rows.append([
            InlineKeyboardButton(
                text="🔔 Позвать на следующую",
                callback_data=f"notify:set:{chat_id}:1",
            ),
            InlineKeyboardButton(
                text="🔕 Не звать",
                callback_data=f"notify:set:{chat_id}:0",
            ),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)''',
)


# ---------------------------------------------------------------------------
# Storage: explicit notification setter. Toggle command remains compatible.
# ---------------------------------------------------------------------------
insert_before(
    "mafia_optimisma/storage.py",
    "    async def toggle_notify(",
    '''    async def set_notify_enabled(
        self, chat_id: int, user_id: int, enabled: bool,
        name: str | None = None, username: str | None = None,
    ) -> bool:
        value = 1 if enabled else 0
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO chat_users
                    (chat_id, user_id, username, name, call_enabled, notify_enabled, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, strftime('%s','now'))
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, chat_users.username),
                    name = COALESCE(excluded.name, chat_users.name),
                    notify_enabled = excluded.notify_enabled,
                    updated_at = strftime('%s','now')
                """,
                (chat_id, user_id, username, name or str(user_id), value),
            )
            await db.commit()
        return bool(value)''',
)


# ---------------------------------------------------------------------------
# Private /start: contextual onboarding and playful repeated start.
# ---------------------------------------------------------------------------
replace_between(
    "mafia_optimisma/routers_private.py",
    '@router.message(Command("start"), F.chat.type == "private")',
    '@router.message(Command("menu"), F.chat.type == "private")',
    '''@router.message(Command("start"), F.chat.type == "private")
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
                    "Место закреплено. Возвращайся в город — роль придёт сюда после старта 😎"
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
    )''',
)


# ---------------------------------------------------------------------------
# Callback UX: result buttons and explicit per-group notification state.
# ---------------------------------------------------------------------------
replace_once(
    "mafia_optimisma/routers_callbacks.py",
    "    admin_time_values_keyboard, admin_timings_keyboard, shop_keyboard,\n)",
    "    admin_time_values_keyboard, admin_timings_keyboard, shop_keyboard, post_game_keyboard,\n)",
)

replace_once(
    "mafia_optimisma/routers_callbacks.py",
    '''    if callback.data == "pm:profile":
        p = await engine.storage.ensure_profile(user.id, user.full_name, user.username)
        await callback.message.answer(engine.format_profile(p))
    elif callback.data == "pm:shop":''',
    '''    if callback.data == "pm:profile":
        p = await engine.storage.ensure_profile(user.id, user.full_name, user.username)
        await callback.message.answer(engine.format_profile(p))
    elif callback.data == "pm:stats":
        p = await engine.storage.ensure_profile(user.id, user.full_name, user.username)
        games = int(p.get("games", 0))
        wins = int(p.get("wins", 0))
        rate = (wins / games * 100) if games else 0.0
        await callback.message.answer(
            "📊 <b>Моя статистика</b>\n"
            f"🎮 Игры: {games}\n"
            f"🏆 Победы: {wins}\n"
            f"📈 Винрейт: {rate:.1f}%\n"
            f"🌟 Уровень: {p.get('level', 1)}"
        )
    elif callback.data == "pm:shop":''',
)

insert_before(
    "mafia_optimisma/routers_callbacks.py",
    '@router.callback_query(F.data.startswith("shop:"))',
    '''@router.callback_query(F.data.startswith("notify:set:"))
async def cb_notify_set(callback: CallbackQuery):
    assert engine
    try:
        _, _, chat_raw, value_raw = callback.data.split(":", 3)
        chat_id = int(chat_raw)
        enabled = value_raw == "1"
    except (ValueError, AttributeError):
        await callback.answer("Эта кнопка устарела.", show_alert=True)
        return
    user = callback.from_user
    await engine.storage.set_notify_enabled(
        chat_id, user.id, enabled, user.full_name, user.username
    )
    try:
        await callback.message.edit_reply_markup(
            reply_markup=post_game_keyboard(chat_id, enabled)
        )
    except Exception:
        pass
    await callback.answer(
        "🔔 Позову на следующую регистрацию в этой группе."
        if enabled else "🔕 Хорошо, из этой группы звать не буду.",
        show_alert=False,
    )''',
)


# ---------------------------------------------------------------------------
# Engine: one-shot last word, clearer status effects, rich personal final card.
# ---------------------------------------------------------------------------
replace_between(
    "mafia_optimisma/engine.py",
    "    async def handle_last_word(",
    "    def registration_text(",
    '''    async def _offer_last_word(self, bot: Bot, game: GameState, player: PlayerState) -> None:
        from .keyboards import post_game_keyboard
        game.pending_last_words.add(player.user_id)
        await self.persist(game)
        await self._safe_pm(
            bot,
            player.user_id,
            "💀 <b>Для тебя эта партия закончилась.</b>\n\n"
            "Но город ещё может услышать твой голос.\n"
            "✍️ Отправь сюда <b>одно</b> последнее сообщение — я передам его в игровой чат.\n\n"
            "После первой отправки право последнего слова закроется автоматически.",
            reply_markup=post_game_keyboard(game.chat_id),
        )

    async def handle_last_word(self, bot: Bot, message: Message, game: GameState, player: PlayerState) -> bool:
        if player.user_id not in game.pending_last_words:
            return False
        if game.phase not in {Phase.DISCUSSION, Phase.NOMINATION, Phase.VERDICT, Phase.RESOLVING}:
            game.pending_last_words.discard(player.user_id)
            await self.persist(game)
            return False
        game.pending_last_words.remove(player.user_id)
        text = (message.text or "").strip()[:600]
        await self.persist(game)
        if not text:
            await self._safe_pm(bot, player.user_id, "🕯 Последнее слово пропущено.")
            return True
        await self._safe_group(
            bot,
            game.chat_id,
            pick(GLOBAL["last_word_public"], name=player_link(player), text=escape(text)),
        )
        from .keyboards import post_game_keyboard
        await self._safe_pm(
            bot,
            player.user_id,
            "🕯 <b>Последнее слово принято.</b>\nГород услышал тебя. Теперь остаётся наблюдать за развязкой.",
            reply_markup=post_game_keyboard(game.chat_id),
        )
        return True''',
)

# Replace both night-death and lynch PM prompts with the richer one-shot helper.
engine_text = read("mafia_optimisma/engine.py")
old_death = '''                    game.pending_last_words.add(p.user_id)
                    await self._safe_pm(bot, p.user_id, pick(GLOBAL["last_word_prompt"]))'''
if old_death in engine_text:
    engine_text = engine_text.replace(old_death, '''                    await self._offer_last_word(bot, game, p)''')
old_lynch = '''                    game.pending_last_words.add(candidate.user_id)
                    await self._safe_group(
                        bot,
                        game.chat_id,
                        pick(GLOBAL["lynch"], name=player_link(candidate), role=role_title(candidate.role_key)),
                    )
                    await self._safe_pm(bot, candidate.user_id, pick(GLOBAL["last_word_prompt"]))'''
new_lynch = '''                    await self._safe_group(
                        bot,
                        game.chat_id,
                        pick(GLOBAL["lynch"], name=player_link(candidate), role=role_title(candidate.role_key)),
                    )
                    await self._offer_last_word(bot, game, candidate)'''
if old_lynch in engine_text:
    engine_text = engine_text.replace(old_lynch, new_lynch, 1)
write("mafia_optimisma/engine.py", engine_text)

replace_once(
    "mafia_optimisma/engine.py",
    '''            blocker_title = role_title(action_role_key(a))
            await self._safe_pm(bot, actor.user_id, f"Действие на {escape(target.name)} сработало.")
            await self._safe_pm(
                bot, target.user_id,
                f"🌙 У вас был(а) {blocker_title}: ваш ночной ход отменён."
            )''',
    '''            blocker_key = action_role_key(a)
            blocker_title = role_title(blocker_key)
            await self._safe_pm(bot, actor.user_id, f"✅ Действие на {escape(target.name)} сработало.")
            if blocker_key == "night_diva":
                effect_text = (
                    "💋 <b>Ночная Дива украла твою ночь.</b>\n"
                    "Твоё ночное действие отменено. Если Хирург не снимет последствия, "
                    "днём ты не сможешь говорить и голосовать."
                )
            elif blocker_key == "bonebreaker":
                effect_text = (
                    "💪 <b>Костолом сорвал твои планы.</b>\n"
                    "Твоё ночное действие отменено. Без помощи Хирурга днём придётся молчать "
                    "и пропустить голосование."
                )
            else:
                effect_text = f"🌙 <b>{blocker_title}</b> сорвал(а) твой ночной ход."
            await self._safe_pm(bot, target.user_id, effect_text)''',
)

replace_once(
    "mafia_optimisma/engine.py",
    '''        # A block also silences for the day unless a real, non-blocked heal reached the target.
        for a in executed_blocks:
            target = game.get_player(a.target_id or 0)
            if target and target.user_id not in healed:
                target.silenced = True''',
    '''        # A block also silences for the day unless a real, non-blocked heal reached the target.
        for a in executed_blocks:
            target = game.get_player(a.target_id or 0)
            if not target:
                continue
            if target.user_id in healed:
                target.silenced = False
                await self._safe_pm(
                    bot, target.user_id,
                    "🩺 <b>Хирург вернул тебя в строй.</b>\n"
                    "Последствия ночной блокировки сняты — днём можно говорить и голосовать."
                )
            else:
                target.silenced = True
                await self._safe_pm(
                    bot, target.user_id,
                    "🤐 <b>Последствия ночи остались до вечера.</b>\n"
                    "Сегодня в игровом чате нельзя говорить и участвовать в голосовании."
                )''',
)

replace_once(
    "mafia_optimisma/engine.py",
    'await self._safe_pm(bot, p.user_id, "💋 У тебя была Ночная Дива: сегодня ты не голосуешь.")',
    'await self._safe_pm(bot, p.user_id, "🤐 Ночной эффект всё ещё действует: сегодня ты не участвуешь в выдвижении кандидата.")',
)

# Clickable names on final public screen.
replace_once(
    "mafia_optimisma/engine.py",
    'lines += [f"{escape(p.name)} — {role_title(p.role_key)}" for p in winners] if winners else ["—"]',
    'lines += [f"{player_link(p)} — {role_title(p.role_key)}" for p in winners] if winners else ["—"]',
)
replace_once(
    "mafia_optimisma/engine.py",
    'lines += [f"{escape(p.name)} — {role_title(p.role_key)}" for p in others] if others else ["—"]',
    'lines += [f"{player_link(p)} — {role_title(p.role_key)}" for p in others] if others else ["—"]',
)

replace_between(
    "mafia_optimisma/engine.py",
    "        reward_enabled = len(game.players) >= self.settings.min_reward_players\n",
    "        if not rewards_ok:\n",
    '''        reward_enabled = len(game.players) >= self.settings.min_reward_players
        rewards_ok = True
        reward_once = getattr(self.storage, "reward_once", None)
        sent_ids = {
            int(uid) for uid in (game.temp.get("final_pm_sent_ids") or [])
            if str(uid).lstrip("-").isdigit()
        }
        for p in game.players.values():
            win = p.user_id in winner_ids
            money = 20 if (reward_enabled and win) else 0
            xp = 20 if win else 5
            try:
                if reward_once is not None:
                    reward = await reward_once(game.session_id, p.user_id, win, money, 0, xp)
                else:
                    reward = await self.storage.reward(p.user_id, win, money, 0, xp)
            except Exception:
                rewards_ok = False
                self.log.exception("Reward write failed chat=%s user=%s", game.chat_id, p.user_id)
                continue
            if not reward:
                rewards_ok = False
                continue
            if p.user_id in sent_ids:
                continue

            profile = None
            try:
                profile = await self.storage.get_profile(p.user_id)
            except Exception:
                self.log.exception("Could not load final profile user=%s", p.user_id)
            games = int((profile or {}).get("games", 0))
            wins_count = int((profile or {}).get("wins", 0))
            winrate = (wins_count / games * 100) if games else 0.0
            balance = int((profile or {}).get("money", 0))
            level = int((profile or {}).get("level", reward.get("level", 1)))
            duration = max(
                0,
                int((game.finished_at or time.time()) - (game.started_at or game.finished_at or time.time())),
            )
            minutes, seconds = divmod(duration, 60)
            duration_text = f"{minutes} мин. {seconds} сек." if minutes else f"{seconds} сек."
            result_title = "🏆 <b>ПОБЕДА!</b>" if win else "🌘 <b>Партия окончена — сегодня без победы.</b>"
            flavor = (
                "Оптимизм окупился. Забирай результат и готовь алиби на следующую игру 😎"
                if win else
                "Город запомнил твой номер. В следующей партии история может быть совсем другой 🙂"
            )
            note = ""
            if not reward_enabled:
                note = f"\n<i>💵 Денежные награды включаются от {self.settings.min_reward_players} игроков.</i>"
            text = (
                f"{result_title}\n{flavor}\n\n"
                f"👤 <b>{escape(p.name)}</b>\n"
                f"🎭 Ты играл(а): <b>{role_title(p.role_key)}</b>\n"
                f"🏙 Группа: <b>{escape(game.chat_title)}</b>\n"
                f"⏱ Партия: {duration_text}\n\n"
                f"🎮 Игры: <b>{games}</b>\n"
                f"🏆 Победы: <b>{wins_count}</b>\n"
                f"📈 Винрейт: <b>{winrate:.1f}%</b>\n\n"
                f"💵 За партию: <b>+{int(reward.get('money', 0))}</b>\n"
                f"⭐ XP: <b>+{int(reward.get('xp', 0))}</b>\n"
                f"💰 Баланс: <b>{balance}</b>\n"
                f"🌟 Уровень: <b>{level}</b>{note}"
            )
            from .keyboards import post_game_keyboard
            sent = await self._safe_pm(
                bot, p.user_id, text, reply_markup=post_game_keyboard(game.chat_id)
            )
            if sent is not None:
                sent_ids.add(p.user_id)
                game.temp["final_pm_sent_ids"] = sorted(sent_ids)
                try:
                    await self.storage.save_game_state(game)
                except Exception:
                    self.log.exception("Could not persist final-PM marker chat=%s user=%s", game.chat_id, p.user_id)''',
)


# Group guard should not falsely blame every silence on the Diva: Kостолом uses
# the same gameplay state in clans mode.
replace_once(
    "mafia_optimisma/routers_group.py",
    'event, game, "❌ Ночная Дива лишила тебя права говорить до конца дня.",',
    'event, game, "🤐 Ночной эффект лишил тебя права говорить и голосовать до конца дня.",',
)
replace_once(
    "mafia_optimisma/routers_group.py",
    'await message.bot.send_message(user.id, "❌ У вас была Ночная Дива, вы не можете общаться в чате до конца дня!")',
    'await message.bot.send_message(user.id, "🤐 Ночной эффект ещё действует: до конца дня нельзя общаться и голосовать.")',
)

print("PLAYER EXPERIENCE UPGRADE APPLIED")
