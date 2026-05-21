from __future__ import annotations

import asyncio
import random
from collections import Counter, defaultdict
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import Message

from .config import Settings
from .content import GLOBAL, MODES, ROLES, TEAMS
from .keyboards import night_action_keyboard, vote_keyboard, join_keyboard
from .models import GameState, NightAction, Phase, PlayerState
from .state import store
from .storage import Storage


def pick(items: list[str], **kwargs) -> str:
    text = random.choice(items) if items else ""
    return text.format(**kwargs)


def role_title(role_key: str | None) -> str:
    role = ROLES[role_key or "optimist"]
    return role.title


def role_team(role_key: str | None) -> str:
    return ROLES[role_key or "optimist"].team


def alive_by_team(game: GameState, team: str) -> list[PlayerState]:
    return [p for p in game.alive_players() if role_team(p.role_key) == team]


def is_crime_role(role_key: str | None) -> bool:
    return role_team(role_key) in {"mafia", "yakuza"}


CLASSIC_THRESHOLDS = [
    (4, "butcher"), (8, "fatalist"), (10, "wanderer"), (12, "night_diva"),
    (12, "breacher"), (13, "shield"), (14, "bomber"), (14, "shadow"),
    (15, "cadet"), (15, "lucky"), (16, "mercy_sister"), (17, "reporter"),
    (18, "alibi_master"), (18, "werewolf"),
]
CHAOS_THRESHOLDS = [
    (3, "surgeon"), (3, "tracker"), (4, "butcher"), (5, "joker"),
    (7, "bomber"), (8, "night_diva"), (9, "breacher"), (10, "wanderer"),
    (11, "lucky"), (12, "shadow"), (13, "fatalist"), (14, "cadet"),
    (15, "alibi_master"), (16, "werewolf"), (17, "shield"),
    (19, "mercy_sister"), (20, "reporter"),
]
VIRUS_THRESHOLDS = [
    (3, "surgeon"), (3, "tracker"), (4, "butcher"), (5, "bomber"),
    (7, "night_diva"), (8, "wanderer"), (9, "breacher"), (10, "lucky"),
    (11, "fatalist"), (12, "shadow"), (13, "carrier"), (14, "cadet"),
    (15, "alibi_master"), (16, "werewolf"), (17, "shield"),
    (19, "mercy_sister"), (20, "reporter"),
]
CLANS_SEQUENCE = [
    (12, "carleone"), (12, "sakura_emperor"), (12, "tracker"), (12, "night_diva"),
    (12, "breacher"), (12, "bonebreaker"), (12, "surgeon"), (12, "shadow"),
    (12, "shinobi"), (12, "wanderer"), (12, "samurai"), (12, "forger"),
    (13, "torpedo"), (14, "samurai"), (15, "cadet"), (16, "torpedo"),
    (17, "samurai"), (18, "mercy_sister"), (19, "torpedo"), (20, "samurai"),
    (21, "shield"), (22, "torpedo"), (23, "samurai"), (24, "cadet"),
    (25, "torpedo"), (26, "samurai"), (27, "bomber"), (28, "torpedo"),
    (29, "samurai"), (30, "lucky"),
]


def generate_roles(mode: str, count: int) -> list[str]:
    if mode == "clans":
        roles = [role for min_p, role in CLANS_SEQUENCE if count >= min_p]
        if count % 3 != 0 and count >= 12:
            roles.append("butcher")
        return (roles[:count] + ["optimist"] * count)[:count]

    mafia_count = max(1, count // 3)
    roles = ["carleone"] + ["torpedo"] * max(0, mafia_count - 1)

    if mode == "classic":
        roles += ["surgeon", "tracker"]
        roles += [role for min_p, role in CLASSIC_THRESHOLDS if count >= min_p]
    elif mode == "chaos":
        roles += [role for min_p, role in CHAOS_THRESHOLDS if count >= min_p]
    elif mode == "virus":
        roles += [role for min_p, role in VIRUS_THRESHOLDS if count >= min_p]
    else:
        roles += ["surgeon", "tracker"]

    roles = roles[:count]
    roles += ["optimist"] * (count - len(roles))
    random.shuffle(roles)
    return roles


def living_summary(game: GameState, reveal_roles: bool = True) -> str:
    lines = ["\n**Живые игроки**"]
    for idx, p in enumerate(game.players.values(), start=1):
        if p.alive:
            lines.append(f"{idx}. {escape(p.name)}")
    if reveal_roles:
        counter = Counter(role_title(p.role_key) for p in game.alive_players())
        teams = defaultdict(list)
        for p in game.alive_players():
            teams[role_team(p.role_key)].append(role_title(p.role_key))
        lines.append("\n👥 **Из них:**")
        for team, role_names in teams.items():
            c = Counter(role_names)
            roles_text = ", ".join(f"{r} ({n})" if n > 1 else r for r, n in c.items())
            lines.append(f"{TEAMS[team]['emoji']} {len(role_names)}: {roles_text}")
    lines.append(f"❤ **Всего живых:** {len(game.alive_players())}")
    return "\n".join(lines)


class GameEngine:
    def __init__(self, settings: Settings, storage: Storage):
        self.settings = settings
        self.storage = storage
        self.tasks: dict[int, asyncio.Task] = {}

    async def add_player(self, game: GameState, user_id: int, name: str, username: str | None) -> tuple[bool, str]:
        if game.phase != Phase.REGISTRATION:
            return False, "Регистрация уже закрыта."
        if user_id in game.players:
            return False, "Ты уже в игре."
        await self.storage.ensure_profile(user_id, name, username)
        game.players[user_id] = PlayerState(user_id=user_id, name=name, username=username)
        store.remember_user(user_id, game.chat_id)
        return True, f"🙂 {escape(name)} присоединился к игре!"

    async def start_game(self, bot: Bot, game: GameState) -> None:
        min_players = MODES[game.mode]["min_players"]
        if len(game.players) < min_players:
            await bot.send_message(game.chat_id, f"Для режима **{MODES[game.mode]['name']}** нужно минимум {min_players} игроков.")
            return
        roles = generate_roles(game.mode, len(game.players))
        players = list(game.players.values())
        random.shuffle(players)
        for p, role_key in zip(players, roles):
            p.role_key = role_key
            p.alive = True
            p.silenced = False
            p.blocked = False
        game.day = 0
        game.phase = Phase.NIGHT
        await self._send_roles(bot, game)
        await bot.send_message(game.chat_id, f"🎲 Игра началась! Режим: **{MODES[game.mode]['emoji']} {MODES[game.mode]['name']}**")
        await self.start_night(bot, game)

    async def _send_roles(self, bot: Bot, game: GameState) -> None:
        for p in game.players.values():
            role = ROLES[p.role_key or "optimist"]
            teammates = [x for x in game.players.values() if x.user_id != p.user_id and role_team(x.role_key) == role.team and role.team in {"mafia", "yakuza"}]
            text = f"**Ты — {role.title}!**\n\n{random.choice(role.private_intro)}\n\n_{role.short_description}_"
            if teammates:
                text += "\n\n👥 Твои союзники:\n" + "\n".join(f"• {m.name} — {role_title(m.role_key)}" for m in teammates)
            try:
                await bot.send_message(p.user_id, text)
            except TelegramForbiddenError:
                await bot.send_message(game.chat_id, f"⚠️ {p.name}, открой ЛС с ботом, иначе ты не получишь роль и кнопки действий.")

    async def start_night(self, bot: Bot, game: GameState) -> None:
        game.day += 1
        game.phase = Phase.NIGHT
        game.actions.clear()
        game.votes.clear()
        game.temp.clear()
        for p in game.alive_players():
            p.blocked = False
            p.silenced = False
        self._inherit_roles(game)
        await bot.send_message(game.chat_id, pick(GLOBAL["night_start"]) + living_summary(game) + f"\n\n*Спать осталось {self.settings.night_seconds} сек.*")
        for p in game.alive_players():
            role = ROLES[p.role_key or "optimist"]
            kb = night_action_keyboard(game, p)
            try:
                if kb:
                    await bot.send_message(p.user_id, random.choice(role.night_prompts), reply_markup=kb)
                else:
                    await bot.send_message(p.user_id, random.choice(role.night_prompts))
            except TelegramForbiddenError:
                pass
        self._schedule(game.chat_id, self.settings.night_seconds, self.end_night(bot, game))

    def _inherit_roles(self, game: GameState) -> None:
        has_surgeon = any(p.alive and p.role_key == "surgeon" for p in game.players.values())
        if not has_surgeon:
            for p in game.players.values():
                if p.alive and p.role_key == "mercy_sister":
                    p.role_key = "surgeon"
                    break
        has_tracker = any(p.alive and p.role_key == "tracker" for p in game.players.values())
        if not has_tracker:
            for p in game.players.values():
                if p.alive and p.role_key == "cadet":
                    p.role_key = "tracker"
                    break
        has_carleone = any(p.alive and p.role_key == "carleone" for p in game.players.values())
        if not has_carleone:
            for p in game.players.values():
                if p.alive and p.role_key == "torpedo":
                    p.role_key = "carleone"
                    break
        has_emperor = any(p.alive and p.role_key == "sakura_emperor" for p in game.players.values())
        if not has_emperor:
            for p in game.players.values():
                if p.alive and p.role_key == "samurai":
                    p.role_key = "sakura_emperor"
                    break

    def _schedule(self, chat_id: int, seconds: int, coro) -> None:
        old = self.tasks.pop(chat_id, None)
        if old and not old.done():
            old.cancel()

        async def runner():
            try:
                await asyncio.sleep(seconds)
                await coro
            except asyncio.CancelledError:
                try:
                    coro.close()
                except Exception:
                    pass
                return

        self.tasks[chat_id] = asyncio.create_task(runner())

    async def end_night(self, bot: Bot, game: GameState) -> None:
        if game.phase != Phase.NIGHT:
            return
        deaths, logs = await self.resolve_night(bot, game)
        game.phase = Phase.DISCUSSION
        await bot.send_message(game.chat_id, pick(GLOBAL["day_start"], day=game.day))
        if deaths:
            for p, reason in deaths:
                await bot.send_message(game.chat_id, pick(GLOBAL["night_death"], name=escape(p.name), role=role_title(p.role_key)) + (f"\n_{reason}_" if reason else ""))
                game.pending_last_words.add(p.user_id)
                try:
                    await bot.send_message(p.user_id, pick(GLOBAL["last_word_prompt"]))
                except TelegramForbiddenError:
                    pass
        else:
            await bot.send_message(game.chat_id, pick(GLOBAL["no_deaths"]))
        for log in logs:
            if log:
                await bot.send_message(game.chat_id, log)
        winner = await self.check_win(bot, game)
        if winner:
            return
        await bot.send_message(game.chat_id, pick(GLOBAL["discussion"]) + f"\n\n*Обсуждение: {self.settings.discussion_seconds} сек.*")
        self._schedule(game.chat_id, self.settings.discussion_seconds, self.start_voting(bot, game))

    async def resolve_night(self, bot: Bot, game: GameState) -> tuple[list[tuple[PlayerState, str]], list[str]]:
        actions = [a for a in game.actions.values() if game.get_player(a.actor_id) and game.get_player(a.actor_id).alive]
        visits: dict[int, list[int]] = defaultdict(list)
        for a in actions:
            if a.target_id and ROLES[game.role_of(a.actor_id) or "optimist"].action_type != "compare_clans":
                visits[a.target_id].append(a.actor_id)

        healed = {a.target_id for a in actions if a.action_type == "heal" and a.target_id}
        protected_by = {a.target_id: a.actor_id for a in actions if a.action_type == "bodyguard" and a.target_id}
        masks = {a.target_id: a.action_type for a in actions if a.action_type in {"mafia_mask", "yakuza_mask"} and a.target_id}
        logs: list[str] = []

        # Blocks first.
        for a in sorted(actions, key=lambda x: ROLES[game.role_of(x.actor_id) or "optimist"].priority or 999):
            actor = game.get_player(a.actor_id)
            target = game.get_player(a.target_id or 0)
            if not actor or not target or a.action_type != "block_and_silence":
                continue
            if await self.storage.consume_item(target.user_id, "perfume"):
                logs.append(f"🧴 {target.name} избежал(а) ночного отвлечения благодаря Дымному парфюму.")
                continue
            target.blocked = True
            if target.user_id not in healed:
                target.silenced = True
            logs.append(random.choice(ROLES[actor.role_key or 'night_diva'].chat_action_phrases))

        effective_actions = []
        for a in actions:
            actor = game.get_player(a.actor_id)
            if not actor or actor.blocked:
                continue
            effective_actions.append(a)

        # Informational actions.
        for a in effective_actions:
            actor = game.get_player(a.actor_id)
            if not actor:
                continue
            target = game.get_player(a.target_id or 0)
            role = ROLES[actor.role_key or "optimist"]
            if a.action_type in {"check", "mafia_role_check"} and target:
                if a.action_type == "mafia_role_check" and await self.storage.consume_item(target.user_id, "antivirus"):
                    await self._safe_pm(bot, actor.user_id, "📀 Взлом сорвался: у цели сработал Антивирус.")
                    continue
                shown = role_title(target.role_key)
                if await self.storage.consume_item(target.user_id, "clean_papers"):
                    shown = role_title("optimist")
                if masks.get(target.user_id) in {"mafia_mask", "yakuza_mask"} and is_crime_role(target.role_key):
                    shown = role_title("optimist")
                msg = random.choice(role.result_phrases).format(name=target.name, role=shown)
                await self._safe_pm(bot, actor.user_id, msg)
                await self._notify_team(bot, game, actor, msg)
            elif a.action_type in {"watch_visitors", "mafia_watch_visitors", "yakuza_watch_visitors"} and target:
                names = [game.players[uid].name for uid in visits.get(target.user_id, []) if uid in game.players]
                text = random.choice(role.result_phrases).format(name=target.name, visitors=", ".join(names) if names else "никто")
                await self._safe_pm(bot, actor.user_id, text)
                await self._notify_team(bot, game, actor, text)
            elif a.action_type == "compare_clans" and target and a.target2_id:
                t2 = game.get_player(a.target2_id)
                if t2:
                    same = ROLES[target.role_key or "optimist"].clan == ROLES[t2.role_key or "optimist"].clan
                    text = random.choice(role.result_phrases).format(name1=target.name, name2=t2.name, same_clan_result="один клан" if same else "разные кланы")
                    await self._safe_pm(bot, actor.user_id, text)
            elif a.action_type == "swap_roles" and target and a.target2_id:
                t2 = game.get_player(a.target2_id)
                if t2 and not target.swapped_once and not t2.swapped_once:
                    target.role_key, t2.role_key = t2.role_key, target.role_key
                    target.swapped_once = True
                    t2.swapped_once = True
                    logs.append(random.choice(role.result_phrases).format(name1=target.name, name2=t2.name))
                    await self._safe_pm(bot, target.user_id, f"🃏 Твоя новая роль: {role_title(target.role_key)}")
                    await self._safe_pm(bot, t2.user_id, f"🃏 Твоя новая роль: {role_title(t2.role_key)}")

        # Infection and werewolf transforms.
        await self._resolve_infection_and_werewolves(bot, game, visits, healed, logs)

        # Kills.
        kill_actions: list[NightAction] = []
        mafia_leader = [a for a in effective_actions if a.action_type == "mafia_kill" and game.role_of(a.actor_id) == "carleone"]
        mafia_backup = [a for a in effective_actions if a.action_type == "mafia_kill" and game.role_of(a.actor_id) in {"torpedo"}]
        yakuza_leader = [a for a in effective_actions if a.action_type == "yakuza_kill" and game.role_of(a.actor_id) == "sakura_emperor"]
        yakuza_backup = [a for a in effective_actions if a.action_type == "yakuza_kill" and game.role_of(a.actor_id) in {"samurai"}]
        if mafia_leader:
            kill_actions.append(mafia_leader[-1])
        elif mafia_backup:
            kill_actions.append(mafia_backup[-1])
        if yakuza_leader:
            kill_actions.append(yakuza_leader[-1])
        elif yakuza_backup:
            kill_actions.append(yakuza_backup[-1])
        kill_actions += [a for a in effective_actions if a.action_type in {"solo_kill", "shoot"}]

        deaths: list[tuple[PlayerState, str]] = []
        dead_ids: set[int] = set()
        for a in kill_actions:
            if not a.target_id:
                continue
            target = game.get_player(a.target_id)
            actor = game.get_player(a.actor_id)
            if not target or not actor or not target.alive or target.user_id in dead_ids:
                continue
            armor = a.item == "armor_piercing"
            guard_id = protected_by.get(target.user_id)
            if guard_id and guard_id != target.user_id:
                guard = game.get_player(guard_id)
                if guard and guard.alive and guard.user_id not in dead_ids:
                    guard.alive = False
                    dead_ids.add(guard.user_id)
                    deaths.append((guard, f"🛡 Он закрыл собой {target.name}."))
                    continue
            if not armor and target.user_id in healed:
                logs.append(f"🩺 {target.name} был(а) спасён(а) Хирургом.")
                continue
            if not armor and await self.storage.consume_item(target.user_id, "night_shield"):
                logs.append(f"🛡 Ночной оберег спас {target.name} от смерти.")
                continue
            if not armor and target.role_key == "lucky" and random.randint(1, 100) <= 75:
                logs.append(f"🍀 {target.name} должен был погибнуть, но удача сказала: «Не сегодня»." )
                continue
            target.alive = False
            dead_ids.add(target.user_id)
            deaths.append((target, f"Ходил: {role_title(actor.role_key)}"))
        return deaths, logs

    async def _resolve_infection_and_werewolves(self, bot: Bot, game: GameState, visits: dict[int, list[int]], healed: set[int], logs: list[str]) -> None:
        if game.mode == "virus":
            carriers = [p for p in game.alive_players() if p.role_key == "carrier"]
            for carrier in carriers:
                for visitor_id in visits.get(carrier.user_id, []):
                    visitor = game.get_player(visitor_id)
                    if not visitor or not visitor.alive or visitor.role_key == "carrier":
                        continue
                    if visitor.role_key == "surgeon":
                        if random.randint(1, 100) <= 75:
                            carrier.role_key = "optimist"
                            logs.append(f"🩺 Хирург вылечил Носителя {carrier.name}. Теперь он Оптимист.")
                        else:
                            visitor.role_key = "carrier"
                            carrier.infected_spread_count += 1
                            logs.append(f"🧟 Лечение сорвалось: {visitor.name} стал(а) Носителем.")
                    elif ROLES[visitor.role_key or "optimist"].action_type != "compare_clans":
                        chance = 25 if visitor.user_id in healed else 75
                        if random.randint(1, 100) <= chance:
                            visitor.role_key = "carrier"
                            carrier.infected_spread_count += 1
                            logs.append(f"🧟 {visitor.name} заразился(ась) после визита к Носителю.")
        for p in game.alive_players():
            if p.role_key != "werewolf":
                continue
            visitors = visits.get(p.user_id, [])
            for vid in visitors:
                visitor_role = game.role_of(vid)
                if visitor_role in {"carleone", "torpedo", "breacher"}:
                    p.role_key = "torpedo"
                    logs.append(f"🐺 {p.name} стал(а) частью Семьи Карлеоне.")
                    break
                if visitor_role in {"tracker"}:
                    p.role_key = "cadet"
                    logs.append(f"🐺 {p.name} стал(а) Стажёром.")
                    break
                if visitor_role == "surgeon":
                    p.role_key = "mercy_sister"
                    logs.append(f"🐺 {p.name} стал(а) Сестрой Милосердия.")
                    break

    async def start_voting(self, bot: Bot, game: GameState) -> None:
        if game.phase != Phase.DISCUSSION:
            return
        game.phase = Phase.VOTING
        game.votes.clear()
        await bot.send_message(game.chat_id, pick(GLOBAL["voting_start"], seconds=self.settings.voting_seconds))
        for p in game.alive_players():
            if p.silenced:
                game.votes[p.user_id] = None
                await self._safe_pm(bot, p.user_id, "💋 Ты не можешь голосовать: последствия ночного визита ещё не прошли.")
                continue
            await self._safe_pm(bot, p.user_id, "🗳 Выбери, против кого голосуешь:", reply_markup=vote_keyboard(game, p.user_id))
        self._schedule(game.chat_id, self.settings.voting_seconds, self.end_voting(bot, game))

    async def end_voting(self, bot: Bot, game: GameState) -> None:
        if game.phase != Phase.VOTING:
            return
        game.phase = Phase.DISCUSSION
        alive = game.alive_players()
        for p in alive:
            if p.user_id not in game.votes:
                game.votes[p.user_id] = None
        counts = Counter(v for v in game.votes.values() if v is not None)
        await bot.send_message(game.chat_id, "🗳 **Голосование окончено**")
        if not counts:
            await bot.send_message(game.chat_id, pick(GLOBAL["vote_draw"]))
        else:
            total_votes = sum(counts.values())
            lines = [f"🗳 **Результаты (голосов – {total_votes}):**"]
            for target_id, count in counts.most_common():
                target = game.get_player(target_id)
                if target:
                    lines.append(f"➖ {escape(target.name)} – {count} голосов ({count / max(1, total_votes) * 100:.2f}%)")
            await bot.send_message(game.chat_id, "\n".join(lines))
            max_votes = counts.most_common(1)[0][1]
            leaders = [uid for uid, c in counts.items() if c == max_votes]
            victim = game.get_player(leaders[0]) if len(leaders) == 1 else None
            if victim and max_votes > len(alive) / 2:
                if await self.storage.consume_item(victim.user_id, "day_shield"):
                    await bot.send_message(game.chat_id, f"⚖️ Солнечный иммунитет спас {victim.name} от казни.")
                else:
                    victim.alive = False
                    await bot.send_message(game.chat_id, pick(GLOBAL["lynch"], name=escape(victim.name), role=role_title(victim.role_key)))
                    game.pending_last_words.add(victim.user_id)
                    if victim.role_key == "fatalist":
                        await self.finish_game(bot, game, "suicide")
                        return
                    if victim.role_key == "bomber":
                        await self._ask_bomber_revenge(bot, game, victim)
                        return
            else:
                await bot.send_message(game.chat_id, pick(GLOBAL["vote_draw"]))
        winner = await self.check_win(bot, game)
        if winner:
            return
        await self.start_night(bot, game)

    async def _ask_bomber_revenge(self, bot: Bot, game: GameState, bomber: PlayerState) -> None:
        from .keyboards import players_keyboard
        await self._safe_pm(bot, bomber.user_id, "💣 Тебя казнили. Выбери, кого забрать с собой:", reply_markup=players_keyboard(game, "bomb", exclude_id=bomber.user_id))
        self._schedule(game.chat_id, 20, self._continue_after_bomb(bot, game))

    async def _continue_after_bomb(self, bot: Bot, game: GameState) -> None:
        winner = await self.check_win(bot, game)
        if winner:
            return
        await self.start_night(bot, game)

    async def check_win(self, bot: Bot, game: GameState) -> str | None:
        alive = game.alive_players()
        if not alive:
            await self.finish_game(bot, game, "draw")
            return "draw"
        teams = Counter(role_team(p.role_key) for p in alive)
        if game.mode == "chaos" and len(alive) == 1:
            await self.finish_game(bot, game, "chaos", extra_winners=[alive[0].user_id])
            return "chaos"
        if game.mode == "virus" and teams.get("infected", 0) and teams["infected"] == len(alive):
            winners = [p.user_id for p in alive if p.role_key == "carrier" and p.infected_spread_count > 0] or [p.user_id for p in alive if p.role_key == "carrier"]
            await self.finish_game(bot, game, "infected", extra_winners=winners)
            return "infected"
        if teams.get("maniac", 0) == 1 and len(alive) == 1:
            await self.finish_game(bot, game, "maniac")
            return "maniac"
        if teams.get("infected", 0):
            return None
        crime_mafia = teams.get("mafia", 0)
        crime_yakuza = teams.get("yakuza", 0)
        town = teams.get("town", 0)
        maniac = teams.get("maniac", 0)
        if game.mode == "clans":
            if crime_mafia == 0 and crime_yakuza == 0:
                await self.finish_game(bot, game, "town")
                return "town"
            if crime_mafia > 0 and crime_yakuza == 0 and crime_mafia >= town + maniac:
                await self.finish_game(bot, game, "mafia")
                return "mafia"
            if crime_yakuza > 0 and crime_mafia == 0 and crime_yakuza >= town + maniac:
                await self.finish_game(bot, game, "yakuza")
                return "yakuza"
            return None
        if crime_mafia == 0 and maniac == 0:
            await self.finish_game(bot, game, "town")
            return "town"
        if crime_mafia > 0 and crime_mafia >= town + maniac:
            await self.finish_game(bot, game, "mafia")
            return "mafia"
        return None

    async def finish_game(self, bot: Bot, game: GameState, winner: str, extra_winners: list[int] | None = None) -> None:
        game.phase = Phase.FINISHED
        task = self.tasks.pop(game.chat_id, None)
        if task and not task.done():
            task.cancel()
        if winner == "draw":
            header = "🏁 Игра окончена без победителя. Город слишком устал."
            winner_ids: set[int] = set()
        elif extra_winners is not None:
            header = pick(GLOBAL.get(f"win_{winner}", ["🏆 Игра окончена!"]))
            winner_ids = set(extra_winners)
        else:
            header = pick(GLOBAL.get(f"win_{winner}", ["🏆 Игра окончена!"]))
            winner_ids = {p.user_id for p in game.players.values() if role_team(p.role_key) == winner}
        lines = [header, "", "**Победители:**"]
        winners = [p for p in game.players.values() if p.user_id in winner_ids]
        if winners:
            lines += [f"• {escape(p.name)} — **{role_title(p.role_key)}**" for p in winners]
        else:
            lines.append("—")
        lines += ["", "**Другие игроки:**"]
        lines += [f"• {escape(p.name)} — **{role_title(p.role_key)}**" for p in game.players.values() if p.user_id not in winner_ids]
        await bot.send_message(game.chat_id, "\n".join(lines))
        for p in game.players.values():
            win = p.user_id in winner_ids
            reward = await self.storage.reward(p.user_id, win, 80 if win else 20, 1 if win else 0, 50 if win else 20)
            if reward:
                text = f"🏁 Игра окончена!\nТы получил 💵 {reward['money']}, 💎 {reward['gems']}, опыт +{reward['xp']}."
                if reward["level_up"]:
                    text += f"\n🌟 Новый уровень: {reward['level']}!"
                await self._safe_pm(bot, p.user_id, text)
        store.remove_game(game.chat_id)

    async def _notify_team(self, bot: Bot, game: GameState, actor: PlayerState, text: str) -> None:
        team = role_team(actor.role_key)
        if team not in {"mafia", "yakuza"}:
            return
        for p in game.alive_players():
            if p.user_id != actor.user_id and role_team(p.role_key) == team:
                await self._safe_pm(bot, p.user_id, f"📨 {actor.name}: {text}")

    async def team_chat(self, bot: Bot, game: GameState, sender: PlayerState, text: str) -> bool:
        team = role_team(sender.role_key)
        if team not in {"mafia", "yakuza"} or game.phase != Phase.NIGHT or not sender.alive:
            return False
        sent = False
        for p in game.alive_players():
            if p.user_id != sender.user_id and role_team(p.role_key) == team:
                await self._safe_pm(bot, p.user_id, f"📨 {sender.name}: {text}")
                sent = True
        return sent

    async def _safe_pm(self, bot: Bot, user_id: int, text: str, **kwargs) -> None:
        try:
            await bot.send_message(user_id, text, **kwargs)
        except TelegramForbiddenError:
            pass

    async def handle_last_word(self, bot: Bot, message: Message, game: GameState, player: PlayerState) -> bool:
        if player.user_id not in game.pending_last_words:
            return False
        game.pending_last_words.remove(player.user_id)
        text = (message.text or "").strip()[:600]
        if not text:
            return True
        await bot.send_message(game.chat_id, pick(GLOBAL["last_word_public"], name=escape(player.name), text=escape(text)))
        return True

    async def public_registration_message(self, bot: Bot, game: GameState) -> None:
        msg = await bot.send_message(game.chat_id, pick(GLOBAL["registration_pin"]), reply_markup=join_keyboard(game.chat_id))
        game.pinned_message_id = msg.message_id
        try:
            await bot.pin_chat_message(game.chat_id, msg.message_id, disable_notification=True)
        except TelegramBadRequest:
            pass

    async def process_noop(self, bot: Bot, chat_id: int) -> None:
        return

    def format_profile(self, profile: dict) -> str:
        item_lines = []
        from .content import ITEMS
        for key, item in ITEMS.items():
            item_lines.append(f"{item['emoji']} {item['name']}: {profile['items'].get(key, 0)}")
        return (
            f"👤 **Профиль**\n"
            f"🆔 ID: `{profile['user_id']}`\n"
            f"💵 Деньги: {profile['money']}\n"
            f"💎 Камни: {profile['gems']}\n"
            f"🌟 Уровень: {profile['level']} | XP: {profile['xp']}\n"
            f"🎮 Игры: {profile['games']} | Победы: {profile['wins']}\n\n"
            + "\n".join(item_lines)
        )
