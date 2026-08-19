from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from .admin import is_chat_admin
from .content import GLOBAL, ITEMS, MODES, ROLES
from .engine import GameEngine, living_summary, pick, player_link, role_team, role_title
from .keyboards import (
    admin_back_keyboard, admin_chat_rules_keyboard, admin_misc_keyboard, admin_mode_keyboard,
    admin_role_threshold_keyboard, admin_roles_keyboard, admin_settings_keyboard,
    admin_time_values_keyboard, admin_timings_keyboard, shop_keyboard, post_game_keyboard,
)
from .models import NightAction, Phase, PlayerState
from .rankings import current_week_leaderboard, full_statistics, render_current_week, render_full_statistics
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

    pm_state = await engine._probe_private_chat(callback.bot, user.id)
    if pm_state is False:
        pending = {
            int(uid) for uid in (game.temp.get("_pending_pm_activation") or [])
            if str(uid).lstrip("-").isdigit()
        }
        pending.add(user.id)
        game.temp["_pending_pm_activation"] = sorted(pending)
        await engine.persist(game)
        await engine.update_registration_message(callback.bot, game)

        username = None
        try:
            username = (await callback.bot.get_me()).username
        except Exception:
            pass
        markup = None
        if username:
            payload = f"join_{game.session_id}_{game.chat_id}"
            markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text="🚀 Открыть бота → START",
                url=f"https://t.me/{username}?start={payload}",
            )]])
        player = game.get_player(user.id)
        prompt = await engine._safe_group(
            callback.bot,
            game.chat_id,
            "🙂 <b>Добро пожаловать в Mafia Optimisma!</b>\n\n"
            f"{player_link(player) if player else escape(user.full_name)}, твоё место уже забронировано 🔐\n"
            "Это первый вход. Нажми кнопку ниже, в открывшемся ЛС нажми <b>START</b> — "
            "и место подтвердится автоматически.\n\n"
            "<i>Повторно «Присоединиться» нажимать не нужно. В следующих играх этого шага уже не будет.</i>",
            reply_markup=markup,
        )
        if prompt:
            prompts = dict(game.temp.get("_activation_prompt_ids") or {})
            prompts[str(user.id)] = prompt.message_id
            game.temp["_activation_prompt_ids"] = prompts
            await engine.persist(game)
        await callback.answer(
            "✅ Ты уже в списке. Для первой игры осталось одно касание: открой бота по кнопке и нажми START.",
            show_alert=True,
        )
    else:
        # Reachable or temporarily uncertain: registration remains valid. Only a
        # confirmed Telegram permission error may request first-time activation.
        pending = {
            int(uid) for uid in (game.temp.get("_pending_pm_activation") or [])
            if str(uid).lstrip("-").isdigit()
        }
        if user.id in pending:
            pending.discard(user.id)
            game.temp["_pending_pm_activation"] = sorted(pending)
            await engine.persist(game)
        await engine.update_registration_message(callback.bot, game)
        await callback.answer("Ты в игре!")
        if pm_state is True:
            await engine._safe_pm(callback.bot, user.id, "✅ Ты зарегистрирован(а). Роль придёт сюда после старта партии.")

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
    elif callback.data == "pm:shop":
        game = store.game_by_user(user.id)
        if game and game.phase not in {Phase.REGISTRATION, Phase.FINISHED}:
            await callback.message.answer("🛒 Магазин недоступен во время запущенной игры.")
        else:
            await callback.message.answer("🛒 Магазин усилений", reply_markup=shop_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("notify:set:"))
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
    )

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
                team_payload = f"{role_title(player.role_key)} {player_link(player)} выбрал(а) {player_link(target)}"
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
    if game_snapshot:
        await engine.maybe_finish_night_early(callback.bot, game_snapshot)

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
        game_snapshot = game

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
    await engine.maybe_finish_night_early(callback.bot, game_snapshot)

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
    nomination_prompt_id = None
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
            group_text = f"🤍 {player_link(voter)} <i>воздержался(ась) от выдвижения.</i>"
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
            group_text = (
                "🗳 <b>Голос принят</b>\n"
                f"{player_link(voter)}  →  🎯 {player_link(target)}"
            )
            answer_text = "Голос принят."
        nomination_prompt_id = game.nomination_pm_message_ids.pop(voter.user_id, None)
        await engine.persist(game)
        group_id = game.chat_id

    await callback.answer(answer_text)
    await engine._safe_delete(
        callback.bot, callback.from_user.id,
        nomination_prompt_id or getattr(callback.message, "message_id", None),
    )
    try:
        await callback.bot.send_message(group_id, group_text)
    except Exception:
        pass

@router.callback_query(F.data.startswith("verdict:"))
async def cb_verdict(callback: CallbackQuery):
    assert engine
    verdict_prompt_id = None
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
        if value not in {"yes", "no", "abstain"}:
            await callback.answer("Неизвестный вариант.", show_alert=True)
            return
        had_previous = voter.user_id in game.verdict_votes
        previous = game.verdict_votes.get(voter.user_id)
        current = {"yes": True, "no": False, "abstain": None}[value]
        game.verdict_votes[voter.user_id] = current
        await engine.persist(game)

    label = {"yes": "👍 Казнить", "no": "👎 Помиловать", "abstain": "🤍 Воздержаться"}[value]
    if had_previous and previous != current:
        await callback.answer(f"Решение изменено: {label}")
    elif had_previous:
        await callback.answer(f"Твой выбор уже: {label}")
    else:
        await callback.answer(f"Выбор принят: {label}")

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
    await engine.maybe_finish_night_early(callback.bot, game)

async def _admin_panel_payload(callback: CallbackQuery, chat_id: int):
    assert engine
    game = store.get(chat_id)
    try:
        chat = await callback.bot.get_chat(chat_id)
        title = getattr(chat, "title", None) or "Игровой чат"
    except Exception:
        title = "Игровой чат"
    try:
        cfg = await engine.storage.get_chat_settings(chat_id)
    except Exception:
        cfg = {}
    timing = {
        "registration_seconds": int(cfg.get("registration_seconds", engine.settings.registration_seconds)),
        "night_seconds": int(cfg.get("night_seconds", engine.settings.night_seconds)),
        "discussion_seconds": int(cfg.get("discussion_seconds", engine.settings.discussion_seconds)),
        "nomination_seconds": int(cfg.get("nomination_seconds", engine.settings.nomination_seconds)),
        "verdict_seconds": int(cfg.get("verdict_seconds", engine.settings.verdict_seconds)),
    }
    if game:
        status = (
            f"🎮 <b>Режим:</b> {MODES[game.mode]['emoji']} <b>{MODES[game.mode]['name']}</b>\n"
            f"🎬 <b>Состояние:</b> <code>{game.phase.value}</code> · 👥 {len(game.players)}"
        )
    else:
        status = "🎬 <b>Состояние:</b> игра сейчас не запущена"
    text = (
        "⚙️ <b>Mafia Optimisma · Настройки</b>\n"
        f"🏙 <b>Чат:</b> {escape(title)}\n\n"
        f"{status}\n\n"
        "⏱ <b>Текущие правила для следующей игры</b>\n"
        f"Регистрация {timing['registration_seconds']}с · Ночь {timing['night_seconds']}с · "
        f"День {timing['discussion_seconds']}с\n\n"
        "ℹ️ Настройки не меняют уже начавшуюся партию. Изменения применятся со следующей игры.\n\n"
        "Выбери раздел ниже."
    )
    return text, admin_settings_keyboard(chat_id)


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

    if action == "refresh":
        text, markup = await _admin_panel_payload(callback, chat_id)
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception:
            await callback.message.answer(text, reply_markup=markup)
        await callback.answer()
        return

    if action == "roles":
        cfg = await engine.storage.get_chat_settings(chat_id)
        overrides = cfg.get("role_thresholds", {})
        if not isinstance(overrides, dict):
            overrides = {}
        await callback.message.edit_text(
            "🎭 <b>Роли</b>\n\n"
            "Выбери роль. Можно оставить порог «по режиму», включить её от конкретного "
            "числа игроков или полностью отключить.\n\n"
            "Изменения работают только со следующей партии.",
            reply_markup=admin_roles_keyboard(chat_id, overrides),
        )
        await callback.answer()
        return

    if action == "role":
        role_key = parts[3]
        role = ROLES.get(role_key)
        if not role:
            await callback.answer("Неизвестная роль.", show_alert=True)
            return
        cfg = await engine.storage.get_chat_settings(chat_id)
        overrides = cfg.get("role_thresholds", {})
        if not isinstance(overrides, dict):
            overrides = {}
        selected = overrides.get(role_key)
        state = "по правилам режима" if selected is None else (
            "выключена" if int(selected) <= 0 else f"от {int(selected)} игроков"
        )
        await callback.message.edit_text(
            f"{role.emoji} <b>{role.title}</b>\n\n"
            f"Сейчас: <b>{state}</b>.\n"
            "От скольких игроков включать эту роль?",
            reply_markup=admin_role_threshold_keyboard(chat_id, role_key, selected),
        )
        await callback.answer()
        return

    if action == "role_set":
        role_key, raw = parts[3], parts[4]
        if role_key not in ROLES:
            await callback.answer("Неизвестная роль.", show_alert=True)
            return
        cfg = await engine.storage.get_chat_settings(chat_id)
        overrides = cfg.get("role_thresholds", {})
        if not isinstance(overrides, dict):
            overrides = {}
        overrides = dict(overrides)
        if raw == "default":
            overrides.pop(role_key, None)
        elif raw == "off":
            overrides[role_key] = 0
        else:
            value = max(3, min(30, int(raw)))
            overrides[role_key] = value
        await engine.storage.set_chat_setting(chat_id, "role_thresholds", overrides)
        selected = overrides.get(role_key)
        role = ROLES[role_key]
        state = "по правилам режима" if selected is None else (
            "выключена" if int(selected) <= 0 else f"от {int(selected)} игроков"
        )
        await callback.message.edit_text(
            f"{role.emoji} <b>{role.title}</b>\n\nСейчас: <b>{state}</b>.\n"
            "От скольких игроков включать эту роль?",
            reply_markup=admin_role_threshold_keyboard(chat_id, role_key, selected),
        )
        await callback.answer("Настройка сохранена для следующей игры.")
        return

    if action == "timings":
        cfg = await engine.storage.get_chat_settings(chat_id)
        values = {
            "registration_seconds": int(cfg.get("registration_seconds", engine.settings.registration_seconds)),
            "night_seconds": int(cfg.get("night_seconds", engine.settings.night_seconds)),
            "discussion_seconds": int(cfg.get("discussion_seconds", engine.settings.discussion_seconds)),
            "nomination_seconds": int(cfg.get("nomination_seconds", engine.settings.nomination_seconds)),
            "verdict_seconds": int(cfg.get("verdict_seconds", engine.settings.verdict_seconds)),
        }
        await callback.message.edit_text(
            "⏱ <b>Тайминги</b>\n\nВыбери фазу, время которой хочешь изменить.",
            reply_markup=admin_timings_keyboard(chat_id, values),
        )
        await callback.answer()
        return

    if action == "time":
        field = parts[3]
        allowed = {
            "registration_seconds", "night_seconds", "discussion_seconds",
            "nomination_seconds", "verdict_seconds",
        }
        if field not in allowed:
            await callback.answer("Неизвестный тайминг.", show_alert=True)
            return
        cfg = await engine.storage.get_chat_settings(chat_id)
        selected = int(cfg.get(field, getattr(engine.settings, field)))
        labels = {
            "registration_seconds": "🎟 Регистрация", "night_seconds": "🌃 Ночь",
            "discussion_seconds": "💬 Обсуждение", "nomination_seconds": "🗳 Выдвижение",
            "verdict_seconds": "⚖️ Вердикт",
        }
        await callback.message.edit_text(
            f"{labels[field]}\n\nСейчас: <b>{selected} секунд</b>. Выбери новое время:",
            reply_markup=admin_time_values_keyboard(chat_id, field, selected),
        )
        await callback.answer()
        return

    if action == "time_set":
        field, raw = parts[3], parts[4]
        allowed = {
            "registration_seconds", "night_seconds", "discussion_seconds",
            "nomination_seconds", "verdict_seconds",
        }
        if field not in allowed:
            await callback.answer("Неизвестный тайминг.", show_alert=True)
            return
        value = max(15, min(180, int(raw)))
        await engine.storage.set_chat_setting(chat_id, field, value)
        await callback.message.edit_text(
            f"⏱ <b>Сохранено: {value} секунд</b>\n\nНастройка начнёт действовать со следующей игры.",
            reply_markup=admin_time_values_keyboard(chat_id, field, value),
        )
        await callback.answer("Сохранено.")
        return

    if action == "chat_rules":
        cfg = await engine.storage.get_chat_settings(chat_id)
        await callback.message.edit_text(
            "🙊 <b>Чат во время игры</b>\n\n"
            "🔒 Базовые правила всегда активны: ночью город молчит, зрители и выбывшие не вмешиваются, "
            "а заблокированный игрок не говорит и не голосует.\n\n"
            "Дополнительная модерация применяется с <b>следующей регистрацией</b> и сохраняется при рестарте бота.\n"
            "🔢 «№ + имя» меняет подписи кнопок выдвижения кандидата.",
            reply_markup=admin_chat_rules_keyboard(chat_id, cfg),
        )
        await callback.answer()
        return

    if action == "chat_toggle":
        feature = parts[3]
        defaults = {
            "block_profanity": False,
            "block_stickers": False,
            "block_links": False,
            "vote_show_numbers": True,
        }
        if feature not in defaults:
            await callback.answer("Неизвестная настройка чата.", show_alert=True)
            return
        cfg = await engine.storage.get_chat_settings(chat_id)
        new_value = not bool(cfg.get(feature, defaults[feature]))
        await engine.storage.set_chat_setting(chat_id, feature, new_value)
        cfg[feature] = new_value
        await callback.message.edit_text(
            "🙊 <b>Чат во время игры</b>\n\n"
            "🔒 Базовые правила всегда активны: ночью город молчит, зрители и выбывшие не вмешиваются, "
            "а заблокированный игрок не говорит и не голосует.\n\n"
            "Дополнительная модерация применяется с <b>следующей регистрацией</b> и сохраняется при рестарте бота.\n"
            "🔢 «№ + имя» меняет подписи кнопок выдвижения кандидата.",
            reply_markup=admin_chat_rules_keyboard(chat_id, cfg),
        )
        await callback.answer("Настройка сохранена для следующей игры.")
        return

    if action == "misc":
        cfg = await engine.storage.get_chat_settings(chat_id)
        protect = bool(cfg.get("protect_private_content", False))
        early = bool(cfg.get("early_night_finish", True))
        await callback.message.edit_text(
            "🛠 <b>Разное</b>\n\n"
            "🛡 <b>Защищённые ЛС</b> — игровые сообщения нельзя пересылать/копировать.\n"
            "⚡ <b>Быстрая ночь</b> — если все активные роли уже сделали ход, утро наступает сразу.",
            reply_markup=admin_misc_keyboard(chat_id, protect, early),
        )
        await callback.answer()
        return

    if action == "toggle":
        feature = parts[3]
        if feature not in {"protect_private_content", "early_night_finish"}:
            await callback.answer("Неизвестная настройка.", show_alert=True)
            return
        cfg = await engine.storage.get_chat_settings(chat_id)
        default = False if feature == "protect_private_content" else True
        new_value = not bool(cfg.get(feature, default))
        await engine.storage.set_chat_setting(chat_id, feature, new_value)
        cfg[feature] = new_value
        await callback.message.edit_text(
            "🛠 <b>Разное</b>\n\n"
            "🛡 <b>Защищённые ЛС</b> — игровые сообщения нельзя пересылать/копировать.\n"
            "⚡ <b>Быстрая ночь</b> — если все активные роли уже сделали ход, утро наступает сразу.",
            reply_markup=admin_misc_keyboard(
                chat_id, bool(cfg.get("protect_private_content", False)),
                bool(cfg.get("early_night_finish", True)),
            ),
        )
        await callback.answer("Настройка сохранена для следующей игры.")
        return

    if action == "reset":
        await engine.storage.reset_chat_settings(chat_id)
        text, markup = await _admin_panel_payload(callback, chat_id)
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception:
            await callback.message.answer(text, reply_markup=markup)
        await callback.answer("Настройки сброшены.")
        return

    if action == "mode_menu":
        await callback.message.edit_text(
            "🎮 <b>Режим игры</b>\n\nВыбери режим для текущей регистрации:",
            reply_markup=admin_mode_keyboard(chat_id),
        )
        await callback.answer()
        return

    if action == "weekly":
        rows, start, end = await current_week_leaderboard(engine.storage, 10)
        await callback.message.answer(render_current_week(rows, start, end))
        await callback.answer()
        return

    if action == "mode":
        mode = parts[3]
        if mode not in MODES:
            await callback.answer("Неизвестный режим.", show_alert=True)
            return
        async with engine.lock_for(chat_id):
            game = store.get(chat_id)
            if not game:
                try:
                    target_chat = await callback.bot.get_chat(chat_id)
                    title = getattr(target_chat, "title", None) or "чат"
                except Exception:
                    title = "чат"
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
            await callback.message.answer(f"👥 <b>Игроки:</b>\n{names}\n\nВсего: {len(game.players)}")
        else:
            await callback.message.answer(living_summary(game))
        await callback.answer()
        return

    if action == "stats":
        top, counts, total = await full_statistics(engine.storage, 10)
        await callback.message.answer(render_full_statistics(top, counts, total))
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
