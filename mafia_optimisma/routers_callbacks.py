from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from .admin import is_chat_admin
from .content import GLOBAL, ITEMS, MODES, ROLES
from .engine import GameEngine, living_summary, pick, role_team, role_title
from .keyboards import admin_settings_keyboard, shop_keyboard
from .models import NightAction, Phase, PlayerState
from .protocol import decode_action, encode_action
from .state import store

router = Router(name="callbacks")
engine: GameEngine | None = None


def setup(game_engine: GameEngine) -> Router:
    global engine
    engine = game_engine
    return router


def call_mention(user: dict) -> str:
    username = user.get("username")
    return f"@{username}" if username else escape(user.get("name") or "Игрок")


def format_call_text(bot_name: str, users: list[dict]) -> str:
    mentions = [call_mention(u) for u in users]
    body = " ".join(mentions[:80]) if mentions else (
        "Пока некого звать: Telegram не даёт боту полный список участников чата. "
        "В созыв попадают те, кто уже взаимодействовал с ботом."
    )
    return pick(GLOBAL["call_start"], bot_name=bot_name) + "\n\n" + body + "\n\n" + pick(GLOBAL["call_end"])


def _allowed_night_actions(game, player: PlayerState) -> set[str]:
    action = ROLES[player.role_key or "optimist"].action_type
    if action in {"mafia_kill_leader", "mafia_kill_backup"}:
        return {"mafia_kill"}
    if action in {"yakuza_kill_leader", "yakuza_kill_backup"}:
        return {"yakuza_kill"}
    if action == "heal":
        return {"heal"}
    if action == "check_or_shoot":
        result = {"check"}
        if game.mode in {"chaos", "virus", "clans"} or (game.mode == "classic" and game.day >= 2):
            result.add("shoot")
        return result
    if action == "block_and_silence":
        return {"block_and_silence"}
    if action == "compare_clans":
        return {"report1", "compare_clans"}
    if action == "swap_roles":
        return {"swap1", "swap_roles"}
    if action in {
        "mafia_role_check", "yakuza_mask", "mafia_mask", "bodyguard",
        "watch_visitors", "mafia_watch_visitors", "yakuza_watch_visitors", "solo_kill",
    }:
        return {action}
    return set()


def _fresh_game(game, session: str, day: int | None = None) -> bool:
    if not game or game.session_id != session:
        return False
    if day is not None and game.day != day:
        return False
    return True


@router.callback_query(F.data.startswith("join:"))
async def cb_join(callback: CallbackQuery):
    assert engine
    try:
        _, session, chat_raw = callback.data.split(":", 2)
        chat_id = int(chat_raw)
    except (ValueError, AttributeError):
        await callback.answer("Старая кнопка регистрации.", show_alert=True)
        return

    user = callback.from_user

    # First tap is the registration. Telegram does not allow a bot to initiate
    # a private conversation, but that must not force the player to return and
    # press JOIN a second time after /start. Register first, then only use the PM
    # probe to tell the player whether they need to open the bot before game start.
    async with engine.lock_for(chat_id):
        game = store.get(chat_id)
        if not _fresh_game(game, session) or game.phase != Phase.REGISTRATION:
            await callback.answer("Эта регистрация уже закрыта. Используй новый закреп.", show_alert=True)
            return
        await engine.storage.remember_chat_user(chat_id, user.id, user.full_name, user.username)
        ok, text = await engine.add_player(game, user.id, user.full_name, user.username)

    if not ok:
        await callback.answer(text, show_alert=True)
        return

    await engine.update_registration_message(callback.bot, game)
    pm_open = True
    try:
        await callback.bot.send_chat_action(user.id, "typing")
    except Exception:
        pm_open = False

    if pm_open:
        await callback.answer("Ты в игре!")
        await engine._safe_pm(callback.bot, user.id, "✅ Ты зарегистрирован(а). Роль придёт сюда после старта партии.")
    else:
        await callback.answer(
            "✅ Ты уже зарегистрирован(а). До старта игры открой ЛС с ботом и нажми /start — повторно жать «Присоединиться» не нужно.",
            show_alert=True,
        )

@router.callback_query(F.data.startswith("mode:"))
async def cb_mode(callback: CallbackQuery):
    assert engine
    try:
        parts = callback.data.split(":")
        chat_id = int(parts[1])
        mode = parts[2]
    except (ValueError, IndexError, AttributeError):
        await callback.answer("Старая кнопка режима.", show_alert=True)
        return
    if not await is_chat_admin(callback.bot, chat_id, callback.from_user.id):
        await callback.answer("Только администратор чата может менять режим.", show_alert=True)
        return
    if mode not in MODES:
        await callback.answer("Неизвестный режим.", show_alert=True)
        return
    async with engine.lock_for(chat_id):
        game = store.get(chat_id)
        if not game or game.phase != Phase.REGISTRATION:
            await callback.answer("Режим можно менять только во время регистрации.", show_alert=True)
            return
        game.mode = mode
        await engine.persist(game)
    await engine.update_registration_message(callback.bot, game)
    await callback.answer("Режим изменён.")

@router.callback_query(F.data.startswith("pm:"))
async def cb_pm(callback: CallbackQuery):
    assert engine
    user = callback.from_user
    if callback.data == "pm:profile":
        p = await engine.storage.ensure_profile(user.id, user.full_name, user.username)
        await callback.message.answer(engine.format_profile(p))
    elif callback.data == "pm:shop":
        game = store.game_by_user(user.id)
        if game and game.phase not in {Phase.REGISTRATION, Phase.FINISHED}:
            await callback.message.answer("🛒 Магазин недоступен во время запущенной игры.")
        else:
            await callback.message.answer("🛒 Магазин усилений", reply_markup=shop_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("shop:"))
async def cb_shop(callback: CallbackQuery):
    assert engine
    game = store.game_by_user(callback.from_user.id)
    if game and game.phase not in {Phase.REGISTRATION, Phase.FINISHED}:
        await callback.answer("Магазин недоступен во время запущенной игры.", show_alert=True)
        return
    item_key = callback.data.split(":", 1)[1]
    if item_key not in ITEMS:
        await callback.answer("Неизвестный предмет.", show_alert=True)
        return
    if ITEMS[item_key].get("enabled", True) is False:
        await callback.answer("🎎 Этот предмет пока в разработке и не продаётся.", show_alert=True)
        return
    ok, msg = await engine.storage.buy_item(callback.from_user.id, item_key)
    await callback.answer(msg, show_alert=True)


@router.callback_query(F.data.startswith("item:"))
async def cb_item(callback: CallbackQuery):
    assert engine
    try:
        _, session, chat_raw, day_raw, item = callback.data.split(":", 4)
        chat_id, day = int(chat_raw), int(day_raw)
    except (ValueError, AttributeError):
        await callback.answer("Старая кнопка предмета.", show_alert=True)
        return

    async with engine.lock_for(chat_id):
        game = store.get(chat_id)
        if not _fresh_game(game, session, day) or game.phase != Phase.NIGHT:
            await callback.answer("Эта кнопка уже устарела.", show_alert=True)
            return
        player = game.get_player(callback.from_user.id)
        if not player or not player.alive:
            await callback.answer("Ты не в игре.", show_alert=True)
            return
        allowed = _allowed_night_actions(game, player)
        if item != "armor_piercing" or not (allowed & {"mafia_kill", "yakuza_kill", "solo_kill", "shoot"}):
            await callback.answer("Этот предмет сейчас нельзя использовать.", show_alert=True)
            return
        profile = await engine.storage.get_profile(player.user_id)
        if not profile or profile["items"].get("armor_piercing", 0) <= 0:
            await callback.answer("У тебя нет Чёрной пули.", show_alert=True)
            return
        game.armor_piercing_pending.add(player.user_id)
        await engine.persist(game)
    await callback.answer("☠️ Пуля подготовлена. Теперь выбери цель.", show_alert=True)

@router.callback_query(F.data.startswith("n:"))
async def cb_night(callback: CallbackQuery):
    assert engine
    try:
        _, session, chat_raw, day_raw, action_token, target_raw = callback.data.split(":", 5)
        action = decode_action(action_token)
        chat_id, day, target_id = int(chat_raw), int(day_raw), int(target_raw)
    except (ValueError, AttributeError):
        await callback.answer("Старая ночная кнопка.", show_alert=True)
        return

    second_markup = None
    second_prompt = None
    target_name = None
    role_phrase = None
    team_payload = None
    player_snapshot = None
    game_snapshot = None

    async with engine.lock_for(chat_id):
        game = store.get(chat_id)
        if not _fresh_game(game, session, day) or game.phase != Phase.NIGHT:
            await callback.answer("Эта ночная кнопка уже устарела.", show_alert=True)
            return
        player = game.get_player(callback.from_user.id)
        target = game.get_player(target_id)
        if not player or not target or not player.alive or not target.alive:
            await callback.answer("Действие недоступно.", show_alert=True)
            return
        if action not in _allowed_night_actions(game, player):
            await callback.answer("Эта роль не может выполнять такое действие.", show_alert=True)
            return
        if player.user_id in game.actions:
            await callback.answer("❌ Ты уже выбрал(а) цель этой ночью!", show_alert=True)
            return
        if action == "mafia_kill" and role_team(target.role_key) == "mafia" and game.mode != "chaos":
            await callback.answer("Своих Семья не убивает в этом режиме.", show_alert=True)
            return
        if action == "yakuza_kill" and role_team(target.role_key) == "yakuza":
            await callback.answer("Своих Клан не убивает.", show_alert=True)
            return
        if action == "mafia_mask" and role_team(target.role_key) != "mafia":
            await callback.answer("Алиби можно выдать только члену Семьи.", show_alert=True)
            return
        if action == "yakuza_mask" and role_team(target.role_key) != "yakuza":
            await callback.answer("Подделка доступна только члену Клана.", show_alert=True)
            return
        if action == "heal" and target_id == player.user_id and player.self_heals_used >= 1:
            await callback.answer("Хирург может лечить себя только один раз за игру.", show_alert=True)
            return
        if action == "swap1" and target.swapped_once:
            await callback.answer("Этому игроку уже меняли роль в этой партии.", show_alert=True)
            return

        if action in {"report1", "swap1"}:
            next_action = "report2" if action == "report1" else "swap2"
            buttons = []
            for p in game.alive_players():
                if p.user_id == target_id:
                    continue
                if action == "swap1" and p.swapped_once:
                    continue
                data = f"n2:{session}:{chat_id}:{day}:{encode_action(next_action)}:{target_id}:{p.user_id}"
                buttons.append(InlineKeyboardButton(text=p.name[:28], callback_data=data))
            rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
            second_markup = InlineKeyboardMarkup(inline_keyboard=rows)
            second_prompt = "Выбери второго игрока:"
        else:
            kill_types = {"mafia_kill", "yakuza_kill", "solo_kill", "shoot"}
            item = None
            pending = player.user_id in game.armor_piercing_pending
            if pending and action in kill_types:
                try:
                    consumed = await engine._consume_game_item_strict(
                        game, player.user_id, "armor_piercing",
                        f"armor:{player.user_id}:{action}:{target_id}",
                    )
                except Exception:
                    # Keep the prepared bullet and do not commit a downgraded
                    # ordinary attack. The player can press the target again
                    # after the transient storage problem clears.
                    await callback.answer(
                        "⚠️ Не удалось списать Чёрную пулю. Она не потрачена — попробуй ещё раз.",
                        show_alert=True,
                    )
                    return
                if not consumed:
                    # Inventory really no longer contains the item. Clear the
                    # pending flag, but do not surprise the player with an
                    # ordinary shot instead of the armour-piercing one.
                    game.armor_piercing_pending.discard(player.user_id)
                    await engine.persist(game)
                    await callback.answer(
                        "☠️ Чёрная пуля больше недоступна. Выбери цель ещё раз для обычного хода.",
                        show_alert=True,
                    )
                    return
                item = "armor_piercing"
                game.armor_piercing_pending.discard(player.user_id)

            game.actions[player.user_id] = NightAction(
                actor_id=player.user_id,
                action_type=action,
                target_id=target_id,
                item=item,
                actor_role_key=player.role_key,
            )
            target_name = target.name
            role = ROLES[player.role_key or "optimist"]
            role_phrase = pick(role.chat_action_phrases) if role.chat_action_phrases else None
            if action in {"mafia_kill", "yakuza_kill"}:
                # Don and backup mafia may both submit a target, but the public
                # group should only learn that the faction acted once. Exact
                # choices remain visible only to teammates.
                marker = f"public_team_action:{action}"
                if game.temp.get(marker):
                    role_phrase = None
                else:
                    game.temp[marker] = True
                    role_phrase = (
                        "🕴 Семья Карлеоне выбрала жертву."
                        if action == "mafia_kill"
                        else "🎴 Клан Сакуры выбрал жертву."
                    )
                team_payload = f"{role_title(player.role_key)} {escape(player.name)} выбрал(а) {escape(target.name)}"
            await engine.persist(game)
            player_snapshot = player
            game_snapshot = game

    # UI work happens after releasing the state lock. If the phase ended in the
    # meantime, the callback itself is already committed (or, for step 1, the
    # generated step-2 buttons are session/day scoped and become harmless stale UI).
    if second_markup is not None:
        try:
            await callback.message.answer(second_prompt, reply_markup=second_markup)
        except Exception:
            pass
        await callback.answer("Первый выбран.")
        return

    await callback.answer("Действие принято.")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        await callback.message.answer(f"Вы выбрали <b>{escape(target_name or '')}</b>")
    except Exception:
        pass
    if role_phrase and game_snapshot:
        try:
            await callback.bot.send_message(game_snapshot.chat_id, role_phrase)
        except Exception:
            pass
    if team_payload and game_snapshot and player_snapshot:
        await engine._notify_team(callback.bot, game_snapshot, player_snapshot, team_payload, attribution=False)

@router.callback_query(F.data.startswith("n2:"))
async def cb_night_second(callback: CallbackQuery):
    assert engine
    try:
        _, session, chat_raw, day_raw, action_token, first_raw, second_raw = callback.data.split(":", 6)
        action = decode_action(action_token)
        chat_id, day, first_id, second_id = int(chat_raw), int(day_raw), int(first_raw), int(second_raw)
    except (ValueError, AttributeError):
        await callback.answer("Старая кнопка.", show_alert=True)
        return

    names = None
    role_phrase = None
    async with engine.lock_for(chat_id):
        game = store.get(chat_id)
        if not _fresh_game(game, session, day) or game.phase != Phase.NIGHT:
            await callback.answer("Эта кнопка уже устарела.", show_alert=True)
            return
        player = game.get_player(callback.from_user.id)
        first, second = game.get_player(first_id), game.get_player(second_id)
        if not player or not first or not second or not player.alive or not first.alive or not second.alive or first_id == second_id:
            await callback.answer("Действие недоступно.", show_alert=True)
            return
        real_action = "compare_clans" if action == "report2" else "swap_roles" if action == "swap2" else ""
        if not real_action or real_action not in _allowed_night_actions(game, player):
            await callback.answer("Эта роль не может выполнить действие.", show_alert=True)
            return
        if real_action == "swap_roles" and (first.swapped_once or second.swapped_once):
            await callback.answer("Одному из этих игроков уже меняли роль в этой партии.", show_alert=True)
            return
        if player.user_id in game.actions:
            await callback.answer("❌ Ты уже сделал(а) ход этой ночью!", show_alert=True)
            return
        game.actions[player.user_id] = NightAction(
            actor_id=player.user_id, action_type=real_action,
            target_id=first_id, target2_id=second_id, actor_role_key=player.role_key,
        )
        await engine.persist(game)
        names = (first.name, second.name)
        role = ROLES[player.role_key or "optimist"]
        role_phrase = pick(role.chat_action_phrases) if role.chat_action_phrases else None
        group_id = game.chat_id

    await callback.answer("Действие принято.")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        await callback.message.answer(
            f"Вы меняете роли/сравниваете игроков <b>{escape(names[0])}</b> и <b>{escape(names[1])}</b>"
        )
    except Exception:
        pass
    if role_phrase:
        try:
            await callback.bot.send_message(group_id, role_phrase)
        except Exception:
            pass

@router.callback_query(F.data.startswith("noop:"))
async def cb_noop(callback: CallbackQuery):
    await callback.answer("Выбери игрока ниже.")


@router.callback_query(F.data.startswith("vote:"))
async def cb_vote(callback: CallbackQuery):
    assert engine
    try:
        _, session, chat_raw, day_raw, value = callback.data.split(":", 4)
        chat_id, day = int(chat_raw), int(day_raw)
    except (ValueError, AttributeError):
        await callback.answer("Старая кнопка голосования.", show_alert=True)
        return

    group_text = None
    async with engine.lock_for(chat_id):
        game = store.get(chat_id)
        if not _fresh_game(game, session, day) or game.phase != Phase.NOMINATION:
            await callback.answer("Это голосование уже окончено.", show_alert=True)
            return
        voter = game.get_player(callback.from_user.id)
        if not voter or not voter.alive:
            await callback.answer("Ты не голосуешь.", show_alert=True)
            return
        if voter.silenced:
            await callback.answer("Ты сегодня молчишь и не голосуешь.", show_alert=True)
            return
        if voter.user_id in game.votes:
            await callback.answer("Твой голос уже принят.", show_alert=True)
            return
        if value == "skip":
            game.votes[voter.user_id] = None
            await engine.persist(game)
            group_text = pick(GLOBAL["vote_skip"], name=escape(voter.name))
            answer_text = "Ты решил(а) ни за кого не голосовать."
        else:
            try:
                target_id = int(value)
            except ValueError:
                await callback.answer("Цель недоступна.", show_alert=True)
                return
            target = game.get_player(target_id)
            if not target or not target.alive or target_id == voter.user_id:
                await callback.answer("Цель недоступна.", show_alert=True)
                return
            game.votes[voter.user_id] = target_id
            await engine.persist(game)
            group_text = pick(GLOBAL["vote_cast"], voter=escape(voter.name), target=escape(target.name))
            answer_text = "Голос принят."
        group_id = game.chat_id

    await callback.answer(answer_text)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        await callback.bot.send_message(group_id, group_text)
    except Exception:
        pass

@router.callback_query(F.data.startswith("verdict:"))
async def cb_verdict(callback: CallbackQuery):
    assert engine
    try:
        _, session, chat_raw, day_raw, value = callback.data.split(":", 4)
        chat_id, day = int(chat_raw), int(day_raw)
    except (ValueError, AttributeError):
        await callback.answer("Старая кнопка решения.", show_alert=True)
        return

    async with engine.lock_for(chat_id):
        game = store.get(chat_id)
        if not _fresh_game(game, session, day) or game.phase != Phase.VERDICT:
            await callback.answer("Это решение уже завершено.", show_alert=True)
            return
        voter = game.get_player(callback.from_user.id)
        candidate = game.get_player(game.nominated_id or 0)
        if not voter or not voter.alive or voter.silenced or not candidate:
            await callback.answer("Ты не участвуешь в этом решении.", show_alert=True)
            return
        if voter.user_id == candidate.user_id:
            await callback.answer("Обвиняемый не голосует за собственный приговор.", show_alert=True)
            return
        if voter.user_id in game.verdict_votes:
            await callback.answer("Твой голос уже принят.", show_alert=True)
            return
        if value not in {"yes", "no"}:
            await callback.answer("Неизвестный вариант.", show_alert=True)
            return
        game.verdict_votes[voter.user_id] = value == "yes"
        await engine.persist(game)

    await callback.answer("👍 За казнь" if value == "yes" else "👎 За помилование")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

@router.callback_query(F.data.startswith("bomb:"))
async def cb_bomb(callback: CallbackQuery):
    assert engine
    try:
        _, session, chat_raw, day_raw, target_raw = callback.data.split(":", 4)
        chat_id, day, target_id = int(chat_raw), int(day_raw), int(target_raw)
    except (ValueError, AttributeError):
        await callback.answer("Старая кнопка Подрывника.", show_alert=True)
        return

    target_name = None
    async with engine.lock_for(chat_id):
        game = store.get(chat_id)
        if not _fresh_game(game, session, day) or game.phase != Phase.NIGHT:
            await callback.answer("Эта кнопка уже устарела.", show_alert=True)
            return
        bomber = game.get_player(callback.from_user.id)
        target = game.get_player(target_id)
        if (
            not bomber or bomber.role_key != "bomber" or bomber.alive
            or game.bomb_pending_for != bomber.user_id or game.bomb_used
            or not target or not target.alive
        ):
            await callback.answer("Недоступно.", show_alert=True)
            return
        game.bomb_used = True
        game.temp["bomb_target_id"] = target.user_id
        target_name = target.name
        await engine.persist(game)

    await callback.answer("💣 Цель мести выбрана.")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        await callback.message.answer(f"Ты выбрал(а): <b>{escape(target_name or '')}</b>")
    except Exception:
        pass

@router.callback_query(F.data.startswith("admin:"))
async def cb_admin(callback: CallbackQuery):
    assert engine
    parts = callback.data.split(":")
    action = parts[1]
    chat_id = int(parts[2])
    if not await is_chat_admin(callback.bot, chat_id, callback.from_user.id):
        await callback.answer("Только администратор чата.", show_alert=True)
        return
    game = store.get(chat_id)

    if action == "mode":
        mode = parts[3]
        if mode not in MODES:
            await callback.answer("Неизвестный режим.", show_alert=True)
            return
        async with engine.lock_for(chat_id):
            game = store.get(chat_id)
            if not game:
                title = callback.message.chat.title if callback.message else "чат"
                game = store.create_or_reset(chat_id, title, mode)
                await engine.begin_registration(callback.bot, game)
                update_card = False
            elif game.phase == Phase.REGISTRATION:
                game.mode = mode
                await engine.persist(game)
                update_card = True
            else:
                await callback.answer("Нельзя менять режим во время партии.", show_alert=True)
                return
        if update_card:
            await engine.update_registration_message(callback.bot, game)
        await callback.answer("Режим установлен.")
        return

    if action == "start":
        if not game or game.phase != Phase.REGISTRATION:
            await callback.answer("Нет активной регистрации.", show_alert=True)
            return
        await callback.answer("Запускаю игру.")
        await engine.start_game(callback.bot, game)
        return

    if action == "extend":
        if not game or game.phase != Phase.REGISTRATION:
            await callback.answer("Нет активной регистрации.", show_alert=True)
            return
        extended = await engine.extend_registration(callback.bot, game, 30)
        await callback.answer("Регистрация продлена." if extended else "Регистрация уже закрылась.", show_alert=not extended)
        return

    if action == "call":
        bot_name = (await callback.bot.get_me()).first_name
        users = await engine.storage.get_callable_users(chat_id)
        await callback.bot.send_message(chat_id, format_call_text(bot_name, users))
        await callback.answer("Созыв отправлен.")
        return

    if action == "players":
        if not game:
            await callback.answer("Активной игры нет.", show_alert=True)
            return
        if game.phase == Phase.REGISTRATION:
            names = "\n".join(
                f"{i}. @{p.username}" if p.username else f"{i}. {escape(p.name)}"
                for i, p in enumerate(game.players.values(), 1)
            ) or "пока пусто"
            await callback.bot.send_message(chat_id, f"👥 <b>Игроки:</b>\n{names}\n\nВсего: {len(game.players)}")
        else:
            await callback.bot.send_message(chat_id, living_summary(game))
        await callback.answer()
        return

    if action == "stats":
        rows = await engine.storage.top_profiles(10)
        if not rows:
            await callback.bot.send_message(chat_id, "📊 Статистики пока нет. Сыграйте первую игру.")
        else:
            lines = ["📊 <b>Топ игроков Mafia Optimisma</b>\n"]
            for i, row in enumerate(rows, 1):
                name = escape(row.get("name") or row.get("username") or str(row["user_id"]))
                lines.append(f"{i}. {name} — 🏆 {row['wins']} / 🎮 {row['games']} | 🌟 {row['level']}")
            await callback.bot.send_message(chat_id, "\n".join(lines))
        await callback.answer()
        return

    if action == "cancel":
        if not game or game.phase != Phase.REGISTRATION:
            await callback.answer("Отменить можно только регистрацию, не идущую партию.", show_alert=True)
            return
        cancelled = await engine.cancel_game(callback.bot, chat_id)
        if cancelled:
            await callback.bot.send_message(chat_id, "🚫 Регистрация отменена администратором.")
        await callback.answer("Регистрация отменена." if cancelled else "Регистрация уже закрылась.", show_alert=not cancelled)
        return

    await callback.answer("Неизвестное действие.", show_alert=True)
