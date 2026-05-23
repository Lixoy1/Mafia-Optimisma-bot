from __future__ import annotations

import asyncio
import random
from collections import Counter
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from .content import GLOBAL, MODES, ROLES, role_team, role_title
from .keyboards import join_keyboard, open_bot_keyboard, night_action_keyboard, vote_keyboard
from .models import GameState, NightAction, Phase, PlayerState
from .state import store
from .storage import Storage


class GameEngine:
    def __init__(self, settings, storage: Storage):
        self.settings = settings
        self.storage = storage
        self.tasks: dict[int, asyncio.Task] = {}

    # ====================== РЕГИСТРАЦИЯ ======================
    async def begin_registration(self, bot: Bot, game: GameState) -> None:
        msg = await bot.send_message(
            game.chat_id,
            "🎲 **Регистрация началась!**\n\nНажмите кнопку ниже, чтобы присоединиться.\nАвтостарт через 60 секунд.",
            reply_markup=join_keyboard(game.chat_id)
        )
        game.pinned_message_id = msg.message_id
        try:
            await bot.pin_chat_message(game.chat_id, msg.message_id, disable_notification=True)
        except Exception:
            pass
        self.schedule_registration(bot, game, seconds=60)

    async def _registration_timer(self, bot: Bot, game: GameState) -> None:
        remaining = 60
        while remaining > 0 and game.phase == Phase.REGISTRATION:
            if remaining == 30:
                await bot.send_message(game.chat_id, "⏳ **Осталось 30 секунд** до конца регистрации!")
            await asyncio.sleep(1)
            remaining -= 1
        if game.phase == Phase.REGISTRATION:
            await self._finish_registration(bot, game)

    async def _finish_registration(self, bot: Bot, game: GameState) -> None:
        min_players = MODES[game.mode]["min_players"]
        if len(game.players) < min_players:
            await bot.send_message(game.chat_id, f"❌ Недостаточно игроков. Нужно минимум {min_players}.")
            await self._cleanup_registration(bot, game)
            store.remove_game(game.chat_id)
            return

        for i in range(3, 0, -1):
            await bot.send_message(game.chat_id, f"🎲 Игра начнётся через **{i}**...")
            await asyncio.sleep(1)

        await self._cleanup_registration(bot, game)
        await bot.send_message(game.chat_id, "🌃 **Наступает ночь!**")
        game.phase = Phase.NIGHT
        await self.start_game(bot, game)

    async def _cleanup_registration(self, bot: Bot, game: GameState) -> None:
        if game.pinned_message_id:
            try:
                await bot.unpin_chat_message(game.chat_id, game.pinned_message_id)
                await bot.delete_message(game.chat_id, game.pinned_message_id)
            except Exception:
                pass
        game.pinned_message_id = None

    # ====================== НОЧЬ ======================
    async def start_night(self, bot: Bot, game: GameState) -> None:
        game.day += 1
        game.phase = Phase.NIGHT
        game.actions.clear()
        game.votes.clear()
        game.temp.clear()

        for p in game.alive_players():
            p.blocked = False
            p.silenced = False
            p.action_done = False

        self._inherit_roles(game)

        me = await bot.get_me()
        await bot.send_message(
            game.chat_id,
            "🌃 **Наступает ночь!**\nГород засыпает. Активные роли получают действия в ЛС.",
            reply_markup=open_bot_keyboard(me.username)
        )

        for player in game.alive_players():
            role = ROLES.get(player.role_key or "optimist")
            if not role or not role.has_night_action:
                continue
            kb = night_action_keyboard(game, player)
            try:
                prompt = random.choice(role.night_prompts) if role.night_prompts else "Сделайте свой ход."
                if kb:
                    await bot.send_message(player.user_id, f"🌙 **Ночь для {role.title}**\n{prompt}", reply_markup=kb)
                else:
                    await bot.send_message(player.user_id, f"🌙 **Ночь для {role.title}**\n{prompt}")
            except TelegramForbiddenError:
                pass

        self._schedule(game.chat_id, self.settings.night_seconds, self.end_night(bot, game))

    async def team_chat(self, bot: Bot, game: GameState, sender: PlayerState, text: str) -> bool:
        team = role_team(sender.role_key)
        if team not in {"mafia", "yakuza"} or game.phase != Phase.NIGHT or not sender.alive:
            return False
        sent = False
        for p in game.alive_players():
            if p.user_id != sender.user_id and role_team(p.role_key) == team:
                try:
                    await bot.send_message(p.user_id, f"📨 **{sender.name}** ({role_title(sender.role_key)}):\n{text}")
                    sent = True
                except Exception:
                    pass
        return sent

    # ====================== ГОЛОСОВАНИЕ ======================
    async def start_voting(self, bot: Bot, game: GameState) -> None:
        if game.phase != Phase.DISCUSSION:
            return
        game.phase = Phase.VOTING
        game.votes.clear()

        me = await bot.get_me()
        await bot.send_message(
            game.chat_id,
            "🗳 **Голосование началось!**\nВыберите, кого казнить.\nГолосование проходит в личных сообщениях.",
            reply_markup=open_bot_keyboard(me.username)
        )

        for player in game.alive_players():
            if player.silenced:
                game.votes[player.user_id] = None
                await self._safe_pm(bot, player.user_id, "💋 Ты сегодня молчишь и не можешь голосовать.")
                continue
            kb = vote_keyboard(game, player.user_id)
            await self._safe_pm(bot, player.user_id, "🗳 Выбери, за кого голосуешь:", reply_markup=kb)

        self._schedule(game.chat_id, self.settings.voting_seconds, self.end_voting(bot, game))

    async def end_voting(self, bot: Bot, game: GameState) -> None:
        if game.phase != Phase.VOTING:
            return
        game.phase = Phase.DISCUSSION

        for p in game.alive_players():
            if p.user_id not in game.votes:
                game.votes[p.user_id] = None

        counts = Counter(v for v in game.votes.values() if v is not None)

        await bot.send_message(game.chat_id, "🗳 **Голосование окончено**")

        if not counts:
            await bot.send_message(game.chat_id, pick(GLOBAL["vote_draw"]))
        else:
            total = sum(counts.values())
            lines = [f"🗳 **Результаты голосования ({total} голосов):**"]
            for target_id, count in counts.most_common():
                target = game.get_player(target_id)
                if target:
                    lines.append(f"➖ {escape(target.name)} — {count} голосов")
            await bot.send_message(game.chat_id, "\n".join(lines))

            max_votes = counts.most_common(1)[0][1]
            leaders = [uid for uid, c in counts.items() if c == max_votes]

            if len(leaders) == 1 and max_votes > len(game.alive_players()) / 2:
                victim = game.get_player(leaders[0])
                if victim:
                    if await self.storage.consume_item(victim.user_id, "day_shield"):
                        await bot.send_message(game.chat_id, f"⚖️ Солнечный иммунитет спас {victim.name} от казни.")
                    else:
                        victim.alive = False
                        await bot.send_message(
                            game.chat_id,
                            pick(GLOBAL["lynch"], name=escape(victim.name), role=role_title(victim.role_key))
                        )
                        game.pending_last_words.add(victim.user_id)
            else:
                await bot.send_message(game.chat_id, pick(GLOBAL["vote_draw"]))

        winner = await self.check_win(bot, game)
        if winner:
            return

        await self.start_night(bot, game)

    async def check_win(self, bot: Bot, game: GameState) -> bool:
        alive = game.alive_players()
        if not alive:
            await self.finish_game(bot, game, "draw")
            return True

        teams = Counter(role_team(p.role_key) for p in alive)

        if game.mode == "chaos" and len(alive) == 1:
            await self.finish_game(bot, game, "chaos", extra_winners=[alive[0].user_id])
            return True

        if game.mode == "virus" and teams.get("infected", 0) == len(alive):
            winners = [p.user_id for p in alive if p.role_key == "carrier"]
            await self.finish_game(bot, game, "infected", extra_winners=winners)
            return True

        mafia = teams.get("mafia", 0)
        yakuza = teams.get("yakuza", 0)
        town = teams.get("town", 0)
        maniac = teams.get("maniac", 0)

        if game.mode == "clans":
            if mafia == 0 and yakuza == 0:
                await self.finish_game(bot, game, "town")
                return True
            if mafia > 0 and yakuza == 0 and mafia >= town + maniac:
                await self.finish_game(bot, game, "mafia")
                return True
            if yakuza > 0 and mafia == 0 and yakuza >= town + maniac:
                await self.finish_game(bot, game, "yakuza")
                return True
        else:
            if mafia == 0 and maniac == 0:
                await self.finish_game(bot, game, "town")
                return True
            if mafia > 0 and mafia >= town + maniac:
                await self.finish_game(bot, game, "mafia")
                return True

        return False

    async def finish_game(self, bot: Bot, game: GameState, winner_type: str, extra_winners: list[int] | None = None) -> None:
        game.phase = Phase.FINISHED
        self.tasks.pop(game.chat_id, None)

        await bot.send_message(game.chat_id, "🏁 **Игра окончена!**")

        for p in game.players.values():
            win = p.user_id in (extra_winners or [])
            reward = await self.storage.reward(p.user_id, win, 80 if win else 20, 1 if win else 0, 50 if win else 20)
            if reward:
                text = f"🏁 Игра окончена!\nТы получил 💵 {reward['money']}, 💎 {reward['gems']}, опыт +{reward['xp']}."
                if reward["level_up"]:
                    text += f"\n🌟 Новый уровень: {reward['level']}!"
                await self._safe_pm(bot, p.user_id, text)

        store.remove_game(game.chat_id)

    async def _safe_pm(self, bot: Bot, user_id: int, text: str, **kwargs):
        try:
            await bot.send_message(user_id, text, **kwargs)
        except Exception:
            pass

    def _schedule(self, chat_id: int, seconds: int, coro):
        old = self.tasks.pop(chat_id, None)
        if old and not old.done():
            old.cancel()

        async def runner():
            try:
                await asyncio.sleep(seconds)
                await coro
            except asyncio.CancelledError:
                pass

        self.tasks[chat_id] = asyncio.create_task(runner())

    def schedule_registration(self, bot: Bot, game: GameState, seconds: int | None = None):
        delay = seconds or self.settings.registration_seconds
        self._schedule(game.chat_id, delay, self._registration_timer(bot, game))

    async def start_game(self, bot: Bot, game: GameState) -> None:
        game.phase = Phase.NIGHT
        await self.start_night(bot, game)

    async def end_night(self, bot: Bot, game: GameState) -> None:
        if game.phase != Phase.NIGHT:
            return
        game.phase = Phase.DISCUSSION
        await bot.send_message(game.chat_id, pick(GLOBAL["day_start"], day=game.day))
        winner = await self.check_win(bot, game)
        if winner:
            return
        await bot.send_message(game.chat_id, pick(GLOBAL["discussion"]))
        self._schedule(game.chat_id, self.settings.discussion_seconds, self.start_voting(bot, game))

    async def resolve_night(self, bot: Bot, game: GameState):
        return [], []

    def _inherit_roles(self, game: GameState):
        pass

    async def add_player(self, game: GameState, user_id: int, name: str, username: str | None) -> tuple[bool, str]:
        if user_id in game.players:
            return False, "Ты уже в игре!"
        game.players[user_id] = PlayerState(user_id=user_id, name=name, username=username)
        store.remember_user(user_id, game.chat_id)
        return True, f"✅ {name} присоединился к игре!"

    async def update_registration_message(self, bot: Bot, game: GameState) -> None:
        if not game.pinned_message_id:
            return
        try:
            names = "\n".join(f"• {p.name}" for p in game.players.values())
            text = f"🎲 **Регистрация ({len(game.players)}/{MODES[game.mode]['min_players']})**\n\n{names}"
            await bot.edit_message_text(text, chat_id=game.chat_id, message_id=game.pinned_message_id, reply_markup=join_keyboard(game.chat_id))
        except Exception:
            pass
