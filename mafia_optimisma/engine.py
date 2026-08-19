from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import Counter, defaultdict
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import Message

from .config import Settings
from .content import GLOBAL, MODES, ROLES, TEAMS, STICKERS
from .keyboards import night_action_keyboard, vote_keyboard, join_keyboard, open_bot_keyboard
from .models import GameState, NightAction, Phase, PlayerState
from .rankings import record_game_result
from .state import store
from .storage import Storage



async def send_phase_sticker(bot: Bot, chat_id: int, key: str) -> None:
    """Safely send Telegram sticker/animated sticker by file_id for phase mood."""
    sticker_id = STICKERS.get(key)
    if not sticker_id:
        return
    try:
        await bot.send_sticker(chat_id, sticker_id)
    except TelegramBadRequest:
        # Sticker file_id can be invalid for this bot/account; game must continue anyway.
        return
    except Exception:
        return

def pick(items: list[str], **kwargs) -> str:
    text = random.choice(items) if items else ""
    return text.format(**kwargs)


def role_title(role_key: str | None) -> str:
    role = ROLES[role_key or "optimist"]
    return role.title


def role_team(role_key: str | None) -> str:
    return ROLES[role_key or "optimist"].team


def player_link(player: PlayerState) -> str:
    """Safe clickable Telegram profile link for public/private game messages."""
    return f'<a href="tg://user?id={int(player.user_id)}">{escape(player.name)}</a>'


def player_link_by_id(game: GameState, user_id: int) -> str:
    player = game.get_player(int(user_id))
    return player_link(player) if player else "—"


def alive_by_team(game: GameState, team: str) -> list[PlayerState]:
    return [p for p in game.alive_players() if role_team(p.role_key) == team]


def is_crime_role(role_key: str | None) -> bool:
    return role_team(role_key) in {"mafia", "yakuza"}


CLASSIC_THRESHOLDS = [
    (8, "fatalist"), (10, "wanderer"), (12, "night_diva"),
    (12, "breacher"), (13, "shield"), (14, "bomber"), (14, "shadow"),
    (15, "cadet"), (15, "lucky"), (16, "butcher"), (16, "mercy_sister"), (17, "reporter"),
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


def generate_roles(
    mode: str, count: int, role_thresholds: dict[str, int] | None = None
) -> list[str]:
    """Build a balanced role pack for the selected ruleset.

    Crime specialists (Hacker/Spy/Lawyer in our names) occupy mafia faction
    slots instead of being added on top of generic Torpedoes. Otherwise classic
    games around 12-18 players can start at, or dangerously close to, mafia
    parity before anybody has made a move.
    """
    role_thresholds = role_thresholds or {}

    def unlocked(default_min: int, role_key: str) -> bool:
        raw = role_thresholds.get(role_key)
        if raw is None:
            threshold = default_min
        else:
            try:
                threshold = int(raw)
            except (TypeError, ValueError):
                threshold = default_min
            if threshold <= 0:
                return False
        return count >= threshold

    if mode == "clans":
        roles = [role for min_p, role in CLANS_SEQUENCE if unlocked(min_p, role)]
        if count % 3 != 0 and count >= 12:
            roles.append("butcher")
        return (roles[:count] + ["optimist"] * count)[:count]

    mafia_count = max(1, count // 3)

    if mode == "classic":
        unlocked_roles = [role for min_p, role in CLASSIC_THRESHOLDS if unlocked(min_p, role)]
        fixed_town = []
        if unlocked(3, "surgeon"):
            fixed_town.append("surgeon")
        if unlocked(6, "tracker"):
            fixed_town.append("tracker")
    elif mode == "chaos":
        unlocked_roles = [role for min_p, role in CHAOS_THRESHOLDS if unlocked(min_p, role)]
        fixed_town = []
    elif mode == "virus":
        unlocked_roles = [role for min_p, role in VIRUS_THRESHOLDS if unlocked(min_p, role)]
        fixed_town = []
    else:
        unlocked_roles = []
        fixed_town = []
        if unlocked(3, "surgeon"):
            fixed_town.append("surgeon")
        if unlocked(6, "tracker"):
            fixed_town.append("tracker")

    mafia_specials = [role for role in unlocked_roles if role_team(role) == "mafia"]
    non_mafia_unlocked = [role for role in unlocked_roles if role_team(role) != "mafia"]

    # Mafia specialists replace ordinary Torpedoes inside the faction quota.
    # Carleone always occupies one slot. If a future ruleset ever unlocks more
    # specialists than its quota can hold, keep the earliest documented ones.
    specialist_slots = max(0, mafia_count - 1)
    mafia_specials = mafia_specials[:specialist_slots]
    generic_mafia = max(0, mafia_count - 1 - len(mafia_specials))
    roles = ["carleone"] + ["torpedo"] * generic_mafia + mafia_specials

    roles += fixed_town
    roles += non_mafia_unlocked

    # Keep one copy of each special role; only Torpedo/Optimist are repeatable.
    unique_roles: list[str] = []
    for role in roles:
        if role not in unique_roles or role in {"torpedo", "optimist"}:
            unique_roles.append(role)
    roles = unique_roles[:count]
    roles += ["optimist"] * (count - len(roles))
    random.shuffle(roles)
    return roles

def living_summary(game: GameState, reveal_roles: bool = True) -> str:
    """Compact Optimist UI: stable slots, clickable players and one role per row."""
    alive = sorted(game.alive_players(), key=lambda x: (x.number or 10**9, x.user_id))
    lines = ["👥 <b>Живые игроки</b>", "━━━━━━━━━━━━"]
    for p in alive:
        number = p.number or 0
        lines.append(f"<b>{number:02d}</b> · {player_link(p)}")
    if reveal_roles:
        counts = Counter(role_title(p.role_key) for p in alive)
        if counts:
            lines += ["", "🎭 <b>Роли в городе</b>"]
            for role, count in counts.items():
                lines.append(f"• {role}  ×{count}")
    lines += ["", f"🌆 <b>В игре:</b> {len(alive)}"]
    return "\n".join(lines)



class GameEngine:
    def __init__(self, settings: Settings, storage: Storage):
        self.settings = settings
        self.storage = storage
        self.tasks: dict[int, asyncio.Task] = {}
        self.warning_tasks: dict[int, asyncio.Task] = {}
        self.finalization_tasks: dict[int, asyncio.Task] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.log = logging.getLogger("mafia_optimisma.engine")

    def lock_for(self, chat_id: int) -> asyncio.Lock:
        lock = self.locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[chat_id] = lock
        return lock

    def _game_config(self, game: GameState | None) -> dict:
        if not game:
            return {}
        raw = game.temp.get("_chat_settings", {})
        return raw if isinstance(raw, dict) else {}

    def _duration(self, game: GameState, key: str, fallback: int | float) -> float:
        raw = self._game_config(game).get(key, fallback)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = float(fallback)
        return max(0.01, min(600.0, value))

    def _feature(self, game: GameState | None, key: str, fallback: bool) -> bool:
        raw = self._game_config(game).get(key, fallback)
        if isinstance(raw, str):
            return raw.strip().lower() not in {"0", "false", "off", "no"}
        return bool(raw)

    async def persist(self, game: GameState) -> None:
        if game.phase == Phase.FINISHED:
            return
        try:
            await self.storage.save_game_state(game)
        except Exception:
            # Persistence failures are serious, but they must not freeze the current
            # in-memory party. Railway logs will make the problem visible.
            self.log.exception("Could not persist game chat=%s", game.chat_id)

    async def _cleanup_item_events(self, session_id: str) -> None:
        cleanup = getattr(self.storage, "delete_item_events", None)
        if cleanup is None:
            return
        try:
            await cleanup(session_id)
        except Exception:
            # This ledger is only replay protection. Cleanup failure must never
            # block a phase transition or resurrect a finished party.
            self.log.exception("Could not cleanup item event ledger session=%s", session_id)

    async def restore_active_games(self, bot: Bot) -> int:
        """Restore unfinished parties after a Railway/container restart.

        A restored expired phase is resolved immediately; otherwise its timer is
        re-armed for the remaining duration. This is the key anti-hang mechanism.
        """
        restored = 0
        for payload in await self.storage.load_game_states():
            try:
                game = GameState.from_dict(payload)
            except Exception:
                self.log.exception("Broken persisted game snapshot")
                continue
            if game.phase == Phase.FINISHED:
                winner = game.temp.get("final_winner")
                raw_ids = game.temp.get("final_winner_ids") or []
                if winner:
                    store.restore(game)
                    ok = await self._complete_finished_game(
                        bot, game, str(winner), {int(x) for x in raw_ids}
                    )
                    if not ok:
                        self._arm_finalization_retry(bot, game)
                        restored += 1
                else:
                    # Compatibility with an old FINISHED snapshot from builds that
                    # did not persist finalisation metadata. It was already treated
                    # as complete by those builds, so only discard the stale row.
                    await self.storage.delete_game_state(game.chat_id)
                    await self._cleanup_item_events(game.session_id)
                continue
            store.restore(game)
            restored += 1
            await self._resume_game(bot, game)
        return restored

    async def _ensure_restored_night_controls(self, bot: Bot, game: GameState) -> None:
        """Re-send missing active-role controls after an interrupted NIGHT render.

        A crash can happen after the NIGHT snapshot was written but before every
        private keyboard was sent/persisted. Re-sending a missing keyboard is safe:
        callbacks are session/day scoped and only one final action is accepted.
        """
        changed = False
        for p in game.alive_players():
            if p.user_id in game.actions or p.user_id in game.night_pm_message_ids:
                continue
            kb = night_action_keyboard(game, p)
            if not kb:
                continue
            role = ROLES[p.role_key or "optimist"]
            msg = await self._safe_pm(bot, p.user_id, random.choice(role.night_prompts), reply_markup=kb)
            if msg:
                game.night_pm_message_ids[p.user_id] = msg.message_id
                changed = True

        if game.bomb_pending_for and not game.bomb_used and game.bomb_pending_for not in game.night_pm_message_ids:
            from .keyboards import players_keyboard
            bomber = game.get_player(game.bomb_pending_for)
            if bomber and not bomber.alive and bomber.role_key == "bomber":
                msg = await self._safe_pm(
                    bot, bomber.user_id,
                    "💣 Наступила ночь. Выбери одного игрока, которого заберёшь с собой:",
                    reply_markup=players_keyboard(game, "bomb", exclude_id=bomber.user_id),
                )
                if msg:
                    game.night_pm_message_ids[bomber.user_id] = msg.message_id
                    changed = True
        if changed:
            await self.persist(game)

    async def _ensure_restored_nomination_controls(self, bot: Bot, game: GameState) -> None:
        changed = False
        for p in game.alive_players():
            if p.silenced or p.user_id in game.votes or p.user_id in game.nomination_pm_message_ids:
                continue
            msg = await self._safe_pm(
                bot, p.user_id, "🗳 Выбери, кого выдвигаешь на городской суд:",
                reply_markup=vote_keyboard(game, p.user_id),
            )
            if msg:
                game.nomination_pm_message_ids[p.user_id] = msg.message_id
                changed = True
        if changed:
            await self.persist(game)

    async def _ensure_restored_verdict_controls(self, bot: Bot, game: GameState) -> None:
        from .keyboards import verdict_keyboard
        candidate = game.get_player(game.nominated_id or 0)
        if not candidate or not candidate.alive:
            return
        changed = False
        for p in game.alive_players():
            if p.user_id == candidate.user_id or p.silenced:
                continue
            if p.user_id in game.verdict_votes or p.user_id in game.verdict_pm_message_ids:
                continue
            msg = await self._safe_pm(
                bot, p.user_id, f"⚖️ Казнить {escape(candidate.name)}?", reply_markup=verdict_keyboard(game)
            )
            if msg:
                game.verdict_pm_message_ids[p.user_id] = msg.message_id
                changed = True
        if changed:
            await self.persist(game)

    async def _resume_game(self, bot: Bot, game: GameState) -> None:
        remaining = 0.0
        if game.phase_deadline is not None:
            remaining = max(0.0, game.phase_deadline - time.time())

        # If a restart happened while we were closing a phase, re-running its
        # resolver is safe because every resolver validates phase/session state.
        if game.phase == Phase.REGISTRATION:
            # A crash may happen after REGISTRATION was persisted but before the
            # pinned card was rendered. Recreate it instead of leaving an invisible
            # registration that can only be discovered by guessing commands.
            if not game.registration_message_id:
                await self.public_registration_message(bot, game)
            else:
                game.pinned_message_id = game.registration_message_id
                try:
                    await bot.pin_chat_message(
                        game.chat_id, game.registration_message_id, disable_notification=True
                    )
                except Exception:
                    # It may already be pinned or an admin may have removed it.
                    # Registration state/timer must continue either way.
                    pass
            self._arm_registration_warning(bot, game)
            self._arm_phase_timer(game, remaining, lambda: self.auto_start_registration(bot, game))
        elif game.phase == Phase.NIGHT:
            await self._ensure_restored_night_controls(bot, game)
            self._arm_phase_timer(game, remaining, lambda: self.end_night(bot, game))
        elif game.phase == Phase.DISCUSSION:
            # end_night persists DISCUSSION before the victory check. If Railway
            # dies in that tiny window, recovery must finish the already-won game
            # instead of opening a bogus nomination.
            winner = await self.check_win(bot, game)
            if not winner and store.get(game.chat_id) is game:
                self._arm_phase_timer(game, remaining, lambda: self.start_nomination(bot, game))
        elif game.phase == Phase.NOMINATION:
            await self._ensure_restored_nomination_controls(bot, game)
            self._arm_phase_timer(game, remaining, lambda: self.end_nomination(bot, game))
        elif game.phase == Phase.VERDICT:
            await self._ensure_restored_verdict_controls(bot, game)
            self._arm_phase_timer(game, remaining, lambda: self.end_verdict(bot, game))
        elif game.phase == Phase.RESOLVING:
            resume = game.temp.get("resume_action")
            await self._safe_group(bot, game.chat_id, "⚙️ Игра восстановлена после перезапуска. Продолжаем партию.")
            if resume == "start_night" and game.players and not all(p.role_key for p in game.players.values()):
                # Defensive repair for a snapshot from an older/broken build that
                # persisted RESOLVING before all roles were assigned.
                self._assign_start_roles(game)
                await self.persist(game)
            if resume == "start_verdict" and game.nominated_id:
                await self.start_verdict(bot, game)
            elif resume == "finish_fatalist":
                await self.finish_game(bot, game, "suicide")
            elif resume == "ask_bomber":
                bomber = game.get_player(int(game.temp.get("bomber_user_id") or 0))
                if bomber and not bomber.alive and bomber.role_key == "bomber":
                    await self._ask_bomber_revenge(bot, game, bomber)
                else:
                    winner = await self.check_win(bot, game)
                    if not winner:
                        await self.start_night(bot, game, allow_from_resolving=True)
            elif resume == "continue_after_bomb":
                if remaining > 0:
                    self._arm_phase_timer(game, remaining, lambda: self._continue_after_bomb(bot, game))
                else:
                    await self._continue_after_bomb(bot, game)
            elif resume == "check_win_then_start_night":
                winner = await self.check_win(bot, game)
                if not winner:
                    await self.start_night(bot, game, allow_from_resolving=True)
            else:
                await self.start_night(bot, game, allow_from_resolving=True)

    async def _set_phase(self, game: GameState, phase: Phase, seconds: int | float | None) -> None:
        now = time.time()
        game.phase = phase
        game.phase_version += 1
        game.phase_started_at = now
        game.phase_deadline = None if seconds is None else now + max(0, float(seconds))
        await self.persist(game)

    def _arm_phase_timer(self, game: GameState, seconds: int | float, coro_factory) -> None:
        old = self.tasks.pop(game.chat_id, None)
        current = asyncio.current_task()
        if old and old is not current and not old.done():
            old.cancel()

        expected_phase = game.phase
        expected_version = game.phase_version
        delay = max(0.0, float(seconds))

        async def runner():
            try:
                await asyncio.sleep(delay)
                current_game = store.get(game.chat_id)
                if current_game is not game:
                    return
                if game.phase != expected_phase or game.phase_version != expected_version:
                    return
                try:
                    await coro_factory()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Never allow one Telegram/API exception to kill the only timer
                    # that can advance the party. Retry the same phase shortly.
                    self.log.exception(
                        "Phase timeout failed chat=%s phase=%s version=%s; retrying",
                        game.chat_id, expected_phase, expected_version,
                    )
                    if store.get(game.chat_id) is game and game.phase == expected_phase and game.phase_version == expected_version:
                        self._arm_phase_timer(game, 5, coro_factory)
            except asyncio.CancelledError:
                return
            finally:
                # Do not leave completed timers in the registry. If the phase
                # transition armed a new timer, preserve that newer task.
                task = asyncio.current_task()
                if self.tasks.get(game.chat_id) is task:
                    self.tasks.pop(game.chat_id, None)

        self.tasks[game.chat_id] = asyncio.create_task(runner())

    def _arm_registration_warning(self, bot: Bot, game: GameState) -> None:
        old = self.warning_tasks.pop(game.chat_id, None)
        if old and not old.done():
            old.cancel()
        if game.phase != Phase.REGISTRATION or game.phase_deadline is None:
            return
        delay = game.phase_deadline - time.time() - self.settings.registration_warning_seconds
        if delay <= 0 and game.registration_warning_id:
            return
        if delay < 0:
            delay = 0
        expected_version = game.phase_version

        async def runner():
            try:
                await asyncio.sleep(delay)
                if store.get(game.chat_id) is not game or game.phase != Phase.REGISTRATION or game.phase_version != expected_version:
                    return
                msg = await self._safe_group(
                    bot,
                    game.chat_id,
                    f"⏳ До конца регистрации: {self.settings.registration_warning_seconds} секунд.",
                    reply_markup=join_keyboard(game),
                )
                if msg:
                    game.registration_warning_id = msg.message_id
                    await self.persist(game)
            except asyncio.CancelledError:
                return
            except Exception:
                self.log.exception("Registration warning failed chat=%s", game.chat_id)

        self.warning_tasks[game.chat_id] = asyncio.create_task(runner())

    async def _safe_group(self, bot: Bot, chat_id: int, text: str, **kwargs):
        try:
            return await bot.send_message(chat_id, text, **kwargs)
        except Exception:
            self.log.exception("Telegram send_message failed chat=%s", chat_id)
            return None

    async def _consume_game_item_strict(
        self, game: GameState, user_id: int, item_key: str, event_key: str, attempts: int = 3
    ) -> bool:
        """Strict, idempotent item consumption for one game event when supported."""
        consume_once = getattr(self.storage, "consume_item_once", None)
        attempts = max(1, int(attempts))
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                if consume_once:
                    return await consume_once(game.session_id, game.day, user_id, item_key, event_key)
                return await self.storage.consume_item(user_id, item_key)
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts:
                    break
                self.log.warning(
                    "Game item consume transient failure chat=%s day=%s user=%s item=%s event=%s attempt=%s/%s",
                    game.chat_id, game.day, user_id, item_key, event_key, attempt, attempts,
                )
                await asyncio.sleep(0.05 * attempt)
        assert last_exc is not None
        raise last_exc

    async def _consume_game_item_safe(
        self, game: GameState, user_id: int, item_key: str, event_key: str, attempts: int = 3
    ) -> bool:
        try:
            return await self._consume_game_item_strict(game, user_id, item_key, event_key, attempts)
        except Exception:
            self.log.exception(
                "Game item consume failed chat=%s day=%s user=%s item=%s event=%s; continuing without item",
                game.chat_id, game.day, user_id, item_key, event_key,
            )
            return False

    async def _consume_item_strict(self, user_id: int, item_key: str, attempts: int = 3) -> bool:
        """Consume an item with retries, but surface a persistent DB failure.

        This is used while accepting a user callback (for example a prepared
        Black Bullet).  If storage is temporarily unavailable we must *not*
        silently downgrade the already chosen premium action to an ordinary
        action.  The caller can keep the UI/pending state intact and let the
        player retry.
        """
        attempts = max(1, int(attempts))
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await self.storage.consume_item(user_id, item_key)
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts:
                    break
                self.log.warning(
                    "Inventory strict consume transient failure user=%s item=%s attempt=%s/%s",
                    user_id, item_key, attempt, attempts,
                )
                await asyncio.sleep(0.05 * attempt)
        assert last_exc is not None
        raise last_exc

    async def _consume_item_safe(self, user_id: int, item_key: str, attempts: int = 3) -> bool:
        """Consume an inventory item without letting transient SQLite errors corrupt a phase.

        Night resolution mutates in-memory role state as it proceeds. Letting a temporary
        database exception escape halfway through would make a timer retry the resolver
        against a partially-mutated game. Retrying the small SQLite operation locally keeps
        the phase deterministic and prevents that class of half-resolved night.
        """
        attempts = max(1, int(attempts))
        for attempt in range(1, attempts + 1):
            try:
                return await self.storage.consume_item(user_id, item_key)
            except Exception:
                if attempt >= attempts:
                    self.log.exception(
                        "Inventory consume failed user=%s item=%s after %s attempts; continuing without item",
                        user_id, item_key, attempts,
                    )
                    return False
                self.log.warning(
                    "Inventory consume transient failure user=%s item=%s attempt=%s/%s",
                    user_id, item_key, attempt, attempts,
                )
                await asyncio.sleep(0.05 * attempt)
        return False

    async def _safe_disable(self, bot: Bot, chat_id: int, message_id: int | None) -> None:
        if not message_id:
            return
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        except Exception:
            return

    async def _safe_delete(self, bot: Bot, chat_id: int, message_id: int | None) -> None:
        if not message_id:
            return
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            return

    async def _safe_unpin(self, bot: Bot, chat_id: int, message_id: int | None) -> None:
        if not message_id:
            return
        try:
            await bot.unpin_chat_message(chat_id, message_id)
        except Exception:
            return

    async def _disable_pm_controls(self, bot: Bot, mapping: dict[int, int]) -> None:
        for user_id, message_id in list(mapping.items()):
            await self._safe_disable(bot, user_id, message_id)
        mapping.clear()

    async def _delete_pm_controls(self, bot: Bot, mapping: dict[int, int]) -> None:
        """Delete short-lived voting cards so PM history does not become cluttered."""
        for user_id, message_id in list(mapping.items()):
            await self._safe_delete(bot, user_id, message_id)
        mapping.clear()

    async def begin_registration(self, bot: Bot, game: GameState) -> None:
        settings_loader = getattr(self.storage, "get_chat_settings", None)
        if settings_loader is None:
            game.temp["_chat_settings"] = {}
        else:
            try:
                game.temp["_chat_settings"] = await settings_loader(game.chat_id)
            except Exception:
                self.log.exception("Could not load chat settings chat=%s", game.chat_id)
                game.temp["_chat_settings"] = {}
        registration_seconds = self._duration(
            game, "registration_seconds", self.settings.registration_seconds
        )
        await self._set_phase(game, Phase.REGISTRATION, registration_seconds)
        await self.public_registration_message(bot, game)
        # Optional per-chat subscription: users explicitly enabling /notify get a
        # quiet private heads-up when a new registration opens.
        try:
            subscribers = await self.storage.get_notify_users(game.chat_id)
        except Exception:
            subscribers = []
        for user in subscribers:
            await self._safe_pm(
                bot,
                int(user["user_id"]),
                f"🔔 В чате «{escape(game.chat_title)}» началась регистрация: "
                f"{MODES[game.mode]['emoji']} {MODES[game.mode]['name']}.\n"
                "Зайди в группу и нажми «Присоединиться» в закрепе.",
            )
        self._arm_registration_warning(bot, game)
        self._arm_phase_timer(
            game,
            registration_seconds,
            lambda: self.auto_start_registration(bot, game),
        )

    async def extend_registration(self, bot: Bot, game: GameState, seconds: int = 30) -> bool:
        async with self.lock_for(game.chat_id):
            if store.get(game.chat_id) is not game or game.phase != Phase.REGISTRATION:
                return False
            warning = self.warning_tasks.pop(game.chat_id, None)
            if warning and not warning.done():
                warning.cancel()
            await self._safe_delete(bot, game.chat_id, game.registration_warning_id)
            game.registration_warning_id = None
            remaining = max(0.0, (game.phase_deadline or time.time()) - time.time())
            total = remaining + seconds
            # /extend literally adds time to the current registration; it must never
            # shorten a registration when pressed early.
            await self._set_phase(game, Phase.REGISTRATION, total)
            await self.update_registration_message(bot, game)
            self._arm_registration_warning(bot, game)
            self._arm_phase_timer(game, total, lambda: self.auto_start_registration(bot, game))
            return True

    async def cancel_game(self, bot: Bot, chat_id: int) -> bool:
        lock = self.lock_for(chat_id)
        async with lock:
            game = store.get(chat_id)
            if not game or game.phase != Phase.REGISTRATION:
                return False
            task = self.tasks.pop(chat_id, None)
            if task and task is not asyncio.current_task() and not task.done():
                task.cancel()
            warning = self.warning_tasks.pop(chat_id, None)
            if warning and not warning.done():
                warning.cancel()
            await self.close_registration_ui(bot, game)
            await self.storage.delete_game_state(chat_id)
            store.remove_game(chat_id)
        # Do not remove the lock while another waiter may still hold a reference to
        # it. Keeping one small lock per used chat is safer than creating split locks.
        return True

    def cancel_timer(self, chat_id: int) -> None:
        task = self.tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    async def auto_start_registration(self, bot: Bot, game: GameState) -> None:
        async with self.lock_for(game.chat_id):
            current = store.get(game.chat_id)
            if current is not game or game.phase != Phase.REGISTRATION:
                return
            min_players = MODES[game.mode]["min_players"]
            if len(game.players) < min_players:
                await self.close_registration_ui(bot, game)
                await self._safe_group(
                    bot,
                    game.chat_id,
                    f"⏳ Закончилось время регистрации. Для режима "
                    f"<b>{MODES[game.mode]['emoji']} {MODES[game.mode]['name']}</b> "
                    f"нужно минимум {min_players} игрока(ов). Сейчас: {len(game.players)}.",
                )
                await self.storage.delete_game_state(game.chat_id)
                store.remove_game(game.chat_id)
                return
        await self.start_game(bot, game, drop_unreachable=True)

    async def add_player(self, game: GameState, user_id: int, name: str, username: str | None) -> tuple[bool, str]:
        if game.phase != Phase.REGISTRATION:
            return False, "Регистрация уже закрыта."
        current_game = store.get(game.chat_id)
        if current_game is not game:
            return False, "Эта регистрация уже закончилась."
        if user_id in game.players:
            return False, "❌ Ты уже зарегистрирован(а) в этой игре."
        other = store.game_by_user(user_id)
        if other is not None and other is not game and other.phase != Phase.FINISHED:
            return False, "Ты уже участвуешь в другой активной игре."
        await self.storage.ensure_profile(user_id, name, username)
        game.players[user_id] = PlayerState(
            user_id=user_id,
            name=name,
            username=username,
            number=game.next_player_number(),
        )
        store.remember_user(user_id, game.chat_id)
        await self.persist(game)
        return True, f"✅ Ты успешно зарегистрирован(а) в игре «{escape(game.chat_title)}»."

    def _assign_start_roles(self, game: GameState, last_roles: dict[int, str] | None = None) -> None:
        """Assign a complete fresh role pack with randomisation and anti-repeat.

        Roles and players are independently shuffled.  When the same group plays
        several rounds in a row we also minimise immediate role repeats, especially
        special roles.  This is not a deterministic rotation: every candidate
        assignment is still random, we simply choose the best of several shuffles.
        """
        role_thresholds = self._game_config(game).get("role_thresholds", {})
        if not isinstance(role_thresholds, dict):
            role_thresholds = {}
        base_roles = generate_roles(game.mode, len(game.players), role_thresholds)
        base_players = list(game.players.values())
        last_roles = last_roles or {}

        best_players = list(base_players)
        best_roles = list(base_roles)
        best_score = 10**9
        for _ in range(96):
            players = list(base_players)
            roles = list(base_roles)
            random.shuffle(players)
            random.shuffle(roles)
            score = 0
            for player, role_key in zip(players, roles):
                if last_roles.get(player.user_id) == role_key:
                    # Repeating a special role is much more noticeable than being
                    # an ordinary Optimist twice, so avoid it more aggressively.
                    score += 5 if role_key != "optimist" else 1
            if score < best_score:
                best_score = score
                best_players, best_roles = players, roles
            if score == 0:
                break

        players, roles = best_players, best_roles
        for p, role_key in zip(players, roles):
            p.role_key = role_key
            p.initial_role_key = role_key
            p.alive = True
            p.silenced = False
            p.blocked = False
            p.checked_ids.clear()
            p.self_heals_used = 0
            p.infected_spread_count = 0
            p.swapped_once = False
            p.bodyguard_saved_id = None
        game.pending_last_words.clear()
        game.bomb_pending_for = None
        game.bomb_used = False
        game.day = 0
        game.started_at = game.started_at or time.time()

    async def start_game(self, bot: Bot, game: GameState, drop_unreachable: bool = False) -> None:
        async with self.lock_for(game.chat_id):
            if store.get(game.chat_id) is not game or game.phase != Phase.REGISTRATION:
                return
            min_players = MODES[game.mode]["min_players"]
            if len(game.players) < min_players:
                await self._safe_group(
                    bot,
                    game.chat_id,
                    f"Для режима <b>{MODES[game.mode]['emoji']} {MODES[game.mode]['name']}</b> "
                    f"нужно минимум {min_players} игрока(ов). Сейчас: {len(game.players)}.",
                )
                return

            unreachable: list[PlayerState] = []
            for p in game.players.values():
                try:
                    await bot.send_chat_action(p.user_id, "typing")
                except Exception:
                    unreachable.append(p)
            if unreachable:
                names = ", ".join(escape(p.name) for p in unreachable)
                if not drop_unreachable:
                    await self._safe_group(bot, game.chat_id, f"⚠️ Не могу начать: откройте ЛС с ботом и нажмите /start — {names}.")
                    return

                # A registration timeout must never hang forever because one
                # participant never opened the bot PM. Remove only unreachable
                # registrations and continue when enough reachable players remain.
                for p in unreachable:
                    game.players.pop(p.user_id, None)
                    if store.user_to_chat.get(p.user_id) == game.chat_id:
                        store.user_to_chat.pop(p.user_id, None)
                await self.persist(game)
                await self._safe_group(
                    bot, game.chat_id,
                    f"⚠️ Не смог отправить роль: {names}. "
                    "Эти игроки исключены из текущей регистрации, потому что ЛС с ботом не открыты.",
                )
                if len(game.players) < min_players:
                    await self.close_registration_ui(bot, game)
                    await self._safe_group(
                        bot, game.chat_id,
                        f"⏳ Регистрация закрыта. После проверки ЛС осталось {len(game.players)} "
                        f"игрока(ов), а нужно минимум {min_players}.",
                    )
                    await self.storage.delete_game_state(game.chat_id)
                    store.remove_game(game.chat_id)
                    return

            # Close registration *logically* in memory before touching Telegram
            # UI. We intentionally do not persist the intermediate RESOLVING state
            # until the whole role pack has been assigned. A crash before that write
            # therefore restores the last safe REGISTRATION snapshot instead of a
            # half-started game with missing roles.
            now = time.time()
            game.phase = Phase.RESOLVING
            game.phase_version += 1
            game.phase_started_at = now
            game.phase_deadline = None
            game.temp["resume_action"] = "start_night"
            last_role_loader = getattr(self.storage, "get_last_roles", None)
            if last_role_loader is None:
                last_roles = {}
            else:
                try:
                    last_roles = await last_role_loader(
                        game.chat_id, list(game.players.keys())
                    )
                except Exception:
                    self.log.exception("Could not load previous roles chat=%s", game.chat_id)
                    last_roles = {}
            self._assign_start_roles(game, last_roles)
            await self.persist(game)
            last_role_writer = getattr(self.storage, "set_last_roles", None)
            if last_role_writer is not None:
                try:
                    await last_role_writer(
                        game.chat_id,
                        {p.user_id: (p.role_key or "optimist") for p in game.players.values()},
                    )
                except Exception:
                    # Variety memory is cosmetic; a DB hiccup must never block a start.
                    self.log.exception("Could not store previous roles chat=%s", game.chat_id)

            task = self.tasks.pop(game.chat_id, None)
            current_task = asyncio.current_task()
            if task and task is not current_task and not task.done():
                task.cancel()
            warning = self.warning_tasks.pop(game.chat_id, None)
            if warning and not warning.done():
                warning.cancel()
            await self.close_registration_ui(bot, game)

            await self._send_roles(bot, game)
            await self._safe_group(bot, game.chat_id, living_summary(game, reveal_roles=True))
            await self._safe_group(
                bot,
                game.chat_id,
                f"🎲 <b>Началась игра в Mafia Optimisma</b>\n"
                f"Режим: {MODES[game.mode]['emoji']} <b>{MODES[game.mode]['name']}</b>",
            )

        await self.start_night(bot, game, allow_from_resolving=True)

    async def _send_roles(self, bot: Bot, game: GameState) -> None:
        for p in game.players.values():
            role = ROLES[p.role_key or "optimist"]
            teammates = [
                x for x in game.players.values()
                if x.user_id != p.user_id
                and role_team(x.role_key) == role.team
                and role.team in {"mafia", "yakuza"}
            ]
            text = (
                f"<b>Ты — {role.title}!</b>\n\n"
                f"{escape(random.choice(role.private_intro))}\n\n"
                f"<i>{escape(role.short_description)}</i>"
            )
            if teammates:
                text += "\n\n<b>Твои союзники:</b>\n" + "\n".join(
                    f"{player_link(m)} — {role_title(m.role_key)}" for m in teammates
                )
            await self._safe_pm(bot, p.user_id, text)

    async def start_night(self, bot: Bot, game: GameState, allow_from_resolving: bool = False) -> None:
        async with self.lock_for(game.chat_id):
            if store.get(game.chat_id) is not game or game.phase == Phase.FINISHED:
                return
            if game.phase == Phase.REGISTRATION:
                return
            if game.phase == Phase.RESOLVING and not allow_from_resolving:
                return

            # Last words are valid only during the day in which the player died.
            game.pending_last_words.clear()
            game.day += 1
            game.actions.clear()
            game.votes.clear()
            game.verdict_votes.clear()
            game.nominated_id = None
            chat_settings = dict(self._game_config(game))
            game.temp.clear()
            game.temp["_chat_settings"] = chat_settings
            game.armor_piercing_pending.clear()
            for p in game.alive_players():
                p.blocked = False
                p.silenced = False

            promotions = self._inherit_roles(game)
            await self._announce_promotions(bot, game, promotions)
            night_seconds = self._duration(game, "night_seconds", self.settings.night_seconds)
            await self._set_phase(game, Phase.NIGHT, night_seconds)

            await send_phase_sticker(bot, game.chat_id, "night")
            me = None
            try:
                me = await bot.get_me()
            except Exception:
                pass
            await self._safe_group(
                bot,
                game.chat_id,
                f"🌃 <b>Ночь {game.day}, город засыпает.</b>\n"
                f"До окончания ночи остается {night_seconds} секунд.\n\n"
                "Ночные действия — в личных сообщениях с ботом.",
                reply_markup=open_bot_keyboard(getattr(me, "username", None)),
            )

            game.night_pm_message_ids.clear()
            for p in game.alive_players():
                role = ROLES[p.role_key or "optimist"]
                kb = night_action_keyboard(game, p)
                prompt = random.choice(role.night_prompts) if role.night_prompts else "Этой ночью у тебя нет отдельного действия."
                if kb:
                    text = prompt
                else:
                    # Passive roles still receive a fresh role reminder every night.
                    # This also makes the PM useful when the user opens it via the
                    # group's «Перейти в бота» button on Night 2+.
                    text = (
                        f"🌙 <b>Ночной цикл №{game.day}</b>\n"
                        f"Ты — <b>{role.title}</b>.\n\n"
                        "💤 У тебя нет ночного действия. Отдыхай и жди утра.\n"
                        f"{escape(prompt)}"
                    )
                msg = await self._safe_pm(bot, p.user_id, text, reply_markup=kb)
                if kb and msg:
                    game.night_pm_message_ids[p.user_id] = msg.message_id

            # A lynched Подрывник takes revenge during the following ordinary NIGHT,
            # not in a separate 20-second pseudo-phase. His one revenge control is
            # part of the same night's control lifecycle and expires with the night.
            if game.bomb_pending_for and not game.bomb_used:
                from .keyboards import players_keyboard
                bomber = game.get_player(game.bomb_pending_for)
                if bomber and not bomber.alive and bomber.role_key == "bomber":
                    msg = await self._safe_pm(
                        bot, bomber.user_id,
                        "💣 Тебя казнили. Наступила ночь — выбери, кого забрать с собой:",
                        reply_markup=players_keyboard(game, "bomb", exclude_id=bomber.user_id),
                    )
                    if msg:
                        game.night_pm_message_ids[bomber.user_id] = msg.message_id
            await self.persist(game)
            self._arm_phase_timer(game, night_seconds, lambda: self.end_night(bot, game))

    def _required_night_actor_ids(self, game: GameState) -> set[int]:
        required: set[int] = set()
        for player in game.alive_players():
            if night_action_keyboard(game, player) is not None:
                required.add(player.user_id)
        if game.bomb_pending_for and not game.bomb_used:
            bomber = game.get_player(game.bomb_pending_for)
            if bomber and not bomber.alive and bomber.role_key == "bomber":
                required.add(bomber.user_id)
        return required

    async def maybe_finish_night_early(self, bot: Bot, game: GameState) -> bool:
        should_finish = False
        async with self.lock_for(game.chat_id):
            if store.get(game.chat_id) is not game or game.phase != Phase.NIGHT:
                return False
            if not self._feature(game, "early_night_finish", True):
                return False
            required = self._required_night_actor_ids(game)
            completed = set(game.actions.keys())
            if game.bomb_pending_for and game.bomb_used:
                completed.add(game.bomb_pending_for)
            if required and required.issubset(completed):
                timer = self.tasks.get(game.chat_id)
                if timer and timer is not asyncio.current_task() and not timer.done():
                    timer.cancel()
                should_finish = True
        if should_finish:
            await self.end_night(bot, game)
        return should_finish

    def _inherit_roles(self, game: GameState) -> list[tuple[PlayerState, str]]:
        promotions: list[tuple[PlayerState, str]] = []
        has_surgeon = any(p.alive and p.role_key == "surgeon" for p in game.players.values())
        if not has_surgeon:
            for p in game.players.values():
                if p.alive and p.role_key == "mercy_sister":
                    p.role_key = "surgeon"
                    promotions.append((p, "surgeon"))
                    break
        has_tracker = any(p.alive and p.role_key == "tracker" for p in game.players.values())
        if not has_tracker:
            for p in game.players.values():
                if p.alive and p.role_key == "cadet":
                    p.role_key = "tracker"
                    promotions.append((p, "tracker"))
                    break
        has_carleone = any(p.alive and p.role_key == "carleone" for p in game.players.values())
        if not has_carleone:
            for p in game.players.values():
                if p.alive and p.role_key == "torpedo":
                    p.role_key = "carleone"
                    promotions.append((p, "carleone"))
                    break
        has_emperor = any(p.alive and p.role_key == "sakura_emperor" for p in game.players.values())
        if not has_emperor:
            for p in game.players.values():
                if p.alive and p.role_key == "samurai":
                    p.role_key = "sakura_emperor"
                    promotions.append((p, "sakura_emperor"))
                    break
        return promotions

    async def _announce_promotions(self, bot: Bot, game: GameState, promotions: list[tuple[PlayerState, str]]) -> None:
        if not promotions:
            return
        for p, new_role in promotions:
            await self._safe_pm(bot, p.user_id, f"🔄 <b>Ваша новая роль: {role_title(new_role)}</b>")
            if new_role == "carleone":
                await self._safe_group(bot, game.chat_id, f"🕴 Мафия стала {role_title(new_role)}.")
            elif new_role == "sakura_emperor":
                await self._safe_group(bot, game.chat_id, f"🎴 Самурай стал {role_title(new_role)}.")
        await self.persist(game)

    async def end_night(self, bot: Bot, game: GameState) -> None:
        async with self.lock_for(game.chat_id):
            if store.get(game.chat_id) is not game or game.phase != Phase.NIGHT:
                return
            await self._disable_pm_controls(bot, game.night_pm_message_ids)

            deaths, public_events = await self.resolve_night(bot, game)

            winner_preview = self._detect_winner_state(game)
            if winner_preview:
                # The final night is rendered without inventing a morning that the
                # city never reached. Show what happened, then the final screen.
                if public_events:
                    await self._safe_group(bot, game.chat_id, "\n".join(public_events))
                if deaths:
                    for p, reason in deaths:
                        if not any(p.name in event for event in public_events):
                            await self._safe_group(
                                bot,
                                game.chat_id,
                                pick(GLOBAL["night_death"], name=player_link(p), role=role_title(p.role_key))
                                + (f"\n_{reason}_" if reason else ""),
                            )
                else:
                    await self._safe_group(bot, game.chat_id, pick(GLOBAL["no_deaths"]))
                await self.check_win(bot, game)
                return

            # A continuing game gets the normal morning sequence.
            promotions = self._inherit_roles(game)
            discussion_seconds = self._duration(
                game, "discussion_seconds", self.settings.discussion_seconds
            )
            await self._set_phase(game, Phase.DISCUSSION, discussion_seconds)
            await send_phase_sticker(bot, game.chat_id, "morning")
            await self._safe_group(
                bot,
                game.chat_id,
                f"🏙 <b>День {game.day}, город просыпается.</b>\n"
                f"До начала голосования {discussion_seconds} секунд.",
            )
            if public_events:
                await self._safe_group(bot, game.chat_id, "\n".join(public_events))
            if deaths:
                for p, reason in deaths:
                    if not any(p.name in event for event in public_events):
                        await self._safe_group(
                            bot,
                            game.chat_id,
                            pick(GLOBAL["night_death"], name=player_link(p), role=role_title(p.role_key))
                            + (f"\n_{reason}_" if reason else ""),
                        )
                    game.pending_last_words.add(p.user_id)
                    await self._safe_pm(bot, p.user_id, pick(GLOBAL["last_word_prompt"]))
            else:
                await self._safe_group(bot, game.chat_id, pick(GLOBAL["no_deaths"]))

            await self._announce_promotions(bot, game, promotions)
            await self._safe_group(bot, game.chat_id, living_summary(game, reveal_roles=True))
            await self.persist(game)
            await self._safe_group(
                bot,
                game.chat_id,
                f"💬 <b>Обсуждение началось.</b> До выдвижения кандидата — {discussion_seconds} секунд.",
            )
            self._arm_phase_timer(game, discussion_seconds, lambda: self.start_nomination(bot, game))

    async def resolve_night(self, bot: Bot, game: GameState) -> tuple[list[tuple[PlayerState, str]], list[str]]:
        """Resolve one night deterministically enough for a social-deduction game.

        Important rule: blocking is resolved before derived protection sets are built.
        Therefore a blocked doctor/bodyguard/mask really does lose the action.
        """
        actions = [
            a for a in game.actions.values()
            if (actor := game.get_player(a.actor_id)) is not None and actor.alive
        ]

        # Validate the one-use self-heal, but do not consume it yet. A Diva/
        # Bonebreaker can cancel the doctor's whole action; in that case the only
        # self-heal must remain available for a later night. The temp marker makes
        # the resolver idempotent if the same NIGHT is retried after a transient
        # failure: a heal already consumed in this exact night stays valid without
        # incrementing the counter twice.
        valid_actions: list[NightAction] = []
        for a in actions:
            actor = game.get_player(a.actor_id)
            if a.action_type == "heal" and actor and a.target_id == actor.user_id:
                marker = game.temp.get(f"self_heal_consumed:{actor.user_id}")
                if actor.self_heals_used >= 1 and marker != game.day:
                    continue
            valid_actions.append(a)
        actions = valid_actions
        logs: list[str] = []

        def action_role_key(a: NightAction) -> str:
            return a.actor_role_key or game.role_of(a.actor_id) or "optimist"

        def action_priority(a: NightAction) -> int:
            return ROLES[action_role_key(a)].priority or 999

        # Blocks are processed first. A lower-priority blocker can itself be stopped
        # by a higher-priority block that already resolved.
        executed_blocks: list[NightAction] = []
        for a in sorted((x for x in actions if x.action_type == "block_and_silence"), key=action_priority):
            actor = game.get_player(a.actor_id)
            target = game.get_player(a.target_id or 0)
            if not actor or not target or actor.blocked:
                continue
            if await self._consume_game_item_safe(
                game, target.user_id, "perfume", f"block:{a.actor_id}:{a.target_id}"
            ):
                await self._safe_pm(bot, target.user_id, "🧴 Дымный парфюм защитил тебя от ночной блокировки.")
                continue
            target.blocked = True
            executed_blocks.append(a)
            blocker_title = role_title(action_role_key(a))
            await self._safe_pm(bot, actor.user_id, f"Действие на {escape(target.name)} сработало.")
            await self._safe_pm(
                bot, target.user_id,
                f"🌙 У вас был(а) {blocker_title}: ваш ночной ход отменён."
            )

        # Every non-block action from a blocked actor is cancelled.
        effective_actions = executed_blocks + [
            a for a in actions
            if a.action_type != "block_and_silence"
            and (actor := game.get_player(a.actor_id)) is not None
            and not actor.blocked
        ]

        # Consume self-heal only after blocks have been resolved.
        for a in effective_actions:
            actor = game.get_player(a.actor_id)
            if a.action_type != "heal" or not actor or a.target_id != actor.user_id:
                continue
            marker_key = f"self_heal_consumed:{actor.user_id}"
            if game.temp.get(marker_key) != game.day:
                actor.self_heals_used += 1
                game.temp[marker_key] = game.day

        healed = {a.target_id for a in effective_actions if a.action_type == "heal" and a.target_id}
        protected_by = {a.target_id: a.actor_id for a in effective_actions if a.action_type == "bodyguard" and a.target_id}
        masks = {a.target_id: a.action_type for a in effective_actions if a.action_type in {"mafia_mask", "yakuza_mask"} and a.target_id}

        # A block also silences for the day unless a real, non-blocked heal reached the target.
        for a in executed_blocks:
            target = game.get_player(a.target_id or 0)
            if target and target.user_id not in healed:
                target.silenced = True

        # Only effective visits are visible to watchers / infection mechanics.
        visits: dict[int, list[int]] = defaultdict(list)
        for a in effective_actions:
            if a.target_id and a.action_type not in {"compare_clans"}:
                visits[a.target_id].append(a.actor_id)

        # Werewolf/Перевёртыш transformation is reactive and takes precedence over
        # the triggering check/shot/mafia attack. A transform caused by a lethal
        # visit consumes that lethal action; bodyguard/items do not prevent it.
        consumed_lethal_actions: set[tuple[int, str, int | None]] = set()
        for wolf in list(game.alive_players()):
            if wolf.role_key != "werewolf":
                continue
            incoming = [a for a in effective_actions if a.target_id == wolf.user_id]
            for a in sorted(incoming, key=action_priority):
                visitor_role = action_role_key(a)
                if visitor_role in {"carleone", "torpedo", "breacher"}:
                    wolf.role_key = "torpedo"
                    if a.action_type == "mafia_kill":
                        consumed_lethal_actions.add((a.actor_id, a.action_type, a.target_id))
                    await self._safe_pm(
                        bot, wolf.user_id,
                        "🐺 Визит Семьи изменил тебя. Твоя новая роль: 🕴 Торпеда."
                    )
                    break
                if visitor_role == "tracker":
                    wolf.role_key = "cadet"
                    if a.action_type == "shoot":
                        consumed_lethal_actions.add((a.actor_id, a.action_type, a.target_id))
                    await self._safe_pm(
                        bot, wolf.user_id,
                        "🐺 Ищейка разбудил твою истинную сторону. Твоя новая роль: 👮 Стажёр."
                    )
                    break
                if visitor_role == "surgeon" and a.action_type == "heal":
                    wolf.role_key = "mercy_sister"
                    await self._safe_pm(
                        bot, wolf.user_id,
                        "🐺 Лечение изменило тебя. Твоя новая роль: 👩‍⚕️ Сестра Милосердия."
                    )
                    break

        # Assistants receive the information promised by their role descriptions.
        for a in effective_actions:
            actor = game.get_player(a.actor_id)
            target = game.get_player(a.target_id or 0)
            if not actor or not target:
                continue
            if a.action_type == "heal" and action_role_key(a) == "surgeon":
                for helper in game.alive_players():
                    if helper.role_key == "mercy_sister":
                        await self._safe_pm(bot, helper.user_id, f"👩‍⚕️ Хирург этой ночью лечит: {escape(target.name)}.")
            if a.action_type in {"check", "shoot"} and action_role_key(a) == "tracker":
                for helper in game.alive_players():
                    if helper.role_key == "cadet":
                        await self._safe_pm(bot, helper.user_id, f"👮 Ищейка этой ночью выбрал: {escape(target.name)}.")

        # Informational and transformation actions.
        for a in sorted(effective_actions, key=action_priority):
            actor = game.get_player(a.actor_id)
            if not actor:
                continue
            target = game.get_player(a.target_id or 0)
            role = ROLES[action_role_key(a)]
            if a.action_type in {"check", "mafia_role_check"} and target:
                await self._safe_pm(bot, target.user_id, "🔎 Кто-то заинтересовался твоей ролью.")
                if a.action_type == "mafia_role_check" and await self._consume_game_item_safe(
                    game, target.user_id, "antivirus", f"antivirus:{a.actor_id}:{a.action_type}:{a.target_id}"
                ):
                    await self._safe_pm(bot, actor.user_id, "📀 Взлом сорвался: у цели сработал Антивирус.")
                    continue
                shown = role_title(target.role_key)
                if await self._consume_game_item_safe(
                    game, target.user_id, "clean_papers", f"papers:{a.actor_id}:{a.action_type}:{a.target_id}"
                ):
                    shown = role_title("optimist")
                # Мастер Алиби fools the Commissioner's town check; the Sakura
                # Фальсификатор fools the Mafia hacker check. They are not a
                # universal disguise against every investigative role.
                mask = masks.get(target.user_id)
                if a.action_type == "check" and mask == "mafia_mask" and role_team(target.role_key) == "mafia":
                    shown = role_title("optimist")
                if a.action_type == "mafia_role_check" and mask == "yakuza_mask" and role_team(target.role_key) == "yakuza":
                    shown = role_title("optimist")
                msg = random.choice(role.result_phrases).format(name=escape(target.name), role=shown)
                if a.action_type == "check" and action_role_key(a) == "tracker":
                    actor.checked_ids.add(target.user_id)
                await self._safe_pm(bot, actor.user_id, msg)
                await self._notify_team(bot, game, actor, msg)
            elif a.action_type in {"watch_visitors", "mafia_watch_visitors", "yakuza_watch_visitors"} and target:
                names = [escape(game.players[uid].name) for uid in visits.get(target.user_id, []) if uid in game.players and uid != actor.user_id]
                text = random.choice(role.result_phrases).format(name=escape(target.name), visitors=", ".join(names) if names else "никто")
                await self._safe_pm(bot, actor.user_id, text)
                await self._notify_team(bot, game, actor, text)
            elif a.action_type == "compare_clans" and target and a.target2_id:
                t2 = game.get_player(a.target2_id)
                if t2:
                    same = ROLES[target.role_key or "optimist"].clan == ROLES[t2.role_key or "optimist"].clan
                    text = random.choice(role.result_phrases).format(
                        name1=escape(target.name), name2=escape(t2.name),
                        same_clan_result="один клан" if same else "разные кланы",
                    )
                    await self._safe_pm(bot, actor.user_id, text)
            elif a.action_type == "swap_roles" and target and a.target2_id:
                t2 = game.get_player(a.target2_id)
                if t2 and target.user_id != t2.user_id and not target.swapped_once and not t2.swapped_once:
                    target.role_key, t2.role_key = t2.role_key, target.role_key
                    target.swapped_once = True
                    t2.swapped_once = True
                    await self._safe_pm(bot, actor.user_id, f"🃏 Роли {escape(target.name)} и {escape(t2.name)} поменяны местами.")
                    await self._safe_pm(bot, target.user_id, f"🃏 Твоя новая роль: {role_title(target.role_key)}")
                    await self._safe_pm(bot, t2.user_id, f"🃏 Твоя новая роль: {role_title(t2.role_key)}")

        await self._resolve_infection_and_werewolves(bot, game, visits, healed, logs)

        # Pick exactly one team kill for mafia/yakuza. We use the actor's role snapshot,
        # so a Joker swap later in the same night cannot make the action disappear.
        kill_actions: list[NightAction] = []
        mafia_leader = [a for a in effective_actions if a.action_type == "mafia_kill" and action_role_key(a) == "carleone"]
        mafia_backup = [a for a in effective_actions if a.action_type == "mafia_kill" and action_role_key(a) == "torpedo"]
        yakuza_leader = [a for a in effective_actions if a.action_type == "yakuza_kill" and action_role_key(a) == "sakura_emperor"]
        yakuza_backup = [a for a in effective_actions if a.action_type == "yakuza_kill" and action_role_key(a) == "samurai"]
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
        public_events: list[str] = []
        saved_by_heal: set[int] = set()
        attacked_ids: set[int] = set()
        protection_announced: set[tuple[str, int]] = set()
        protection_announced: set[tuple[str, int]] = set()

        def attack_public_text(a: NightAction, target: PlayerState) -> str:
            attacker_role = action_role_key(a)
            verb = {
                "solo_kill": "устроил ночной кошмар",
                "shoot": "открыл огонь",
                "yakuza_kill": "нанёс удар",
            }.get(a.action_type, "атаковал")
            return (
                f"🔻 <b>Ночной удар</b>\n"
                f"{role_title(attacker_role)} {verb}: {player_link(target)}\n"
                f"🎭 Роль цели: <b>{role_title(target.role_key)}</b>"
            )

        for a in kill_actions:
            if not a.target_id:
                continue
            if (a.actor_id, a.action_type, a.target_id) in consumed_lethal_actions:
                continue
            target = game.get_player(a.target_id)
            actor = game.get_player(a.actor_id)
            if not target or not actor:
                continue
            # A target killed by an earlier attack can still be hit by another
            # independent killer in the same night. We record every successful
            # attack, but death itself is applied exactly once.
            if not target.alive and target.user_id not in dead_ids:
                continue
            attacked_ids.add(target.user_id)
            armor = a.item == "armor_piercing"

            guard_id = protected_by.get(target.user_id)
            if guard_id and guard_id != target.user_id:
                guard = game.get_player(guard_id)
                if guard and guard.alive and guard.user_id not in dead_ids:
                    guard.alive = False
                    guard.bodyguard_saved_id = target.user_id
                    dead_ids.add(guard.user_id)
                    deaths.append((guard, f"Защищал(а) {target.name}"))
                    public_events.append(
                        "🛡 <b>Защита сработала</b>\n"
                        f"{player_link(guard)} прикрыл(а) {player_link(target)} и погиб(ла)."
                    )
                    await self._safe_pm(bot, guard.user_id, f"🛡 Ты погиб(ла), защищая {escape(target.name)}.")
                    continue

            if not armor and target.user_id in healed:
                saved_by_heal.add(target.user_id)
                if ("heal", target.user_id) not in protection_announced:
                    protection_announced.add(("heal", target.user_id))
                    public_events.append(
                        "🩺 <b>Хирург успел вовремя</b>\n"
                        f"{player_link(target)} пережил(а) ночное нападение."
                    )
                await self._safe_pm(bot, actor.user_id, "🩺 Цель пережила нападение.")
                continue
            if not armor and await self._consume_game_item_safe(
                game, target.user_id, "night_shield", f"night_shield:{a.actor_id}:{a.action_type}:{a.target_id}"
            ):
                if ("shield", target.user_id) not in protection_announced:
                    protection_announced.add(("shield", target.user_id))
                    public_events.append(
                        "🛡 <b>Ночной оберег вспыхнул</b>\n"
                        f"{player_link(target)} пережил(а) смертельную атаку."
                    )
                await self._safe_pm(bot, target.user_id, "🛡 Ночной оберег спас тебя от смерти.")
                continue
            if not armor and target.role_key == "lucky" and random.randint(1, 100) <= 75:
                if ("lucky", target.user_id) not in protection_announced:
                    protection_announced.add(("lucky", target.user_id))
                    public_events.append(
                        "🍀 <b>Фортуна улыбнулась</b>\n"
                        f"{player_link(target)} чудом избежал(а) гибели."
                    )
                await self._safe_pm(bot, target.user_id, "🍀 Сегодня удача спасла тебя от смерти.")
                continue

            public_events.append(attack_public_text(a, target))
            if target.user_id not in dead_ids:
                target.alive = False
                dead_ids.add(target.user_id)
                deaths.append((target, ""))

        # A lynched Подрывник chooses during this NIGHT. Revenge is resolved at
        # morning together with the other night events. It is a direct revenge
        # effect, not a normal attack, so ordinary healing/bodyguard/items do not
        # intercept it. If the chosen target already died this night, the revenge
        # is simply spent.
        bomb_target_raw = game.temp.get("bomb_target_id")
        if bomb_target_raw is not None:
            try:
                bomb_target = game.get_player(int(bomb_target_raw))
            except (TypeError, ValueError):
                bomb_target = None
            if bomb_target and bomb_target.alive:
                bomb_target.alive = False
                dead_ids.add(bomb_target.user_id)
                deaths.append((bomb_target, "Месть Подрывника"))
                public_events.append(
                    "💥 <b>Последний сюрприз Подрывника</b>\n"
                    f"Подрывник забрал с собой {player_link(bomb_target)} — <b>{role_title(bomb_target.role_key)}</b>"
                )
        # Revenge expires at the end of this night whether the bomber chose or not.
        game.bomb_pending_for = None

        # Doctor feedback in the reference bot is sent to the patient, not only
        # to the doctor. One heal can save against multiple ordinary attacks.
        for a in effective_actions:
            if a.action_type != "heal" or not a.target_id:
                continue
            patient = game.get_player(a.target_id)
            if not patient or not patient.alive:
                continue
            if patient.user_id in saved_by_heal:
                await self._safe_pm(bot, patient.user_id, "🩺 Вас хотели убить, но Хирург спас вас.")
            else:
                await self._safe_pm(bot, patient.user_id, "🩺 К вам приходил Хирург, но помощь вам не потребовалась.")

        # Keep internal transform/infection logs private; the second return value
        # is now deliberately the public morning event stream.
        return deaths, public_events

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
                            await self._safe_pm(bot, carrier.user_id, "🩺 Тебя вылечили. Теперь ты 🙂 Оптимист.")
                            logs.append(f"Носитель {carrier.name} вылечен")
                        else:
                            visitor.role_key = "carrier"
                            carrier.infected_spread_count += 1
                            await self._safe_pm(bot, visitor.user_id, "🧟 Ты заразился(ась). Твоя новая роль: Носитель.")
                            logs.append(f"{visitor.name} заражён")
                    elif ROLES[visitor.role_key or "optimist"].action_type != "compare_clans":
                        chance = 25 if visitor.user_id in healed else 75
                        if random.randint(1, 100) <= chance:
                            visitor.role_key = "carrier"
                            carrier.infected_spread_count += 1
                            await self._safe_pm(bot, visitor.user_id, "🧟 После ночного визита ты стал(а) Носителем.")
                            logs.append(f"{visitor.name} заражён")
        for p in game.alive_players():
            if p.role_key != "werewolf":
                continue
            visitors = visits.get(p.user_id, [])
            for vid in visitors:
                visitor_role = game.role_of(vid)
                if visitor_role in {"carleone", "torpedo", "breacher"}:
                    p.role_key = "torpedo"
                    await self._safe_pm(bot, p.user_id, "🐺 Ты превратился(ась) в 🕴 Торпеду и теперь играешь за Семью Карлеоне.")
                    logs.append(f"{p.name} превратился в Торпеду")
                    break
                if visitor_role in {"tracker"}:
                    p.role_key = "cadet"
                    await self._safe_pm(bot, p.user_id, "🐺 Ты превратился(ась) в 👮 Стажёра.")
                    logs.append(f"{p.name} превратился в Стажёра")
                    break
                if visitor_role == "surgeon":
                    p.role_key = "mercy_sister"
                    await self._safe_pm(bot, p.user_id, "🐺 Ты превратился(ась) в 👩‍⚕️ Сестру Милосердия.")
                    logs.append(f"{p.name} превратился в Сестру Милосердия")
                    break

    async def start_nomination(self, bot: Bot, game: GameState) -> None:
        async with self.lock_for(game.chat_id):
            if store.get(game.chat_id) is not game or game.phase != Phase.DISCUSSION:
                return
            game.votes.clear()
            game.nominated_id = None
            nomination_seconds = self._duration(
                game, "nomination_seconds", self.settings.nomination_seconds
            )
            await self._set_phase(game, Phase.NOMINATION, nomination_seconds)
            await send_phase_sticker(bot, game.chat_id, "voting")
            me = None
            try:
                me = await bot.get_me()
            except Exception:
                pass
            msg = await self._safe_group(
                bot,
                game.chat_id,
                f"🗳 <b>Выдвижение кандидата</b>\n"
                f"У города {nomination_seconds} секунд, чтобы выбрать подозреваемого.\n"
                "Можно проголосовать или отказаться от выбора.",
                reply_markup=open_bot_keyboard(getattr(me, "username", None)),
            )
            if msg:
                game.nomination_message_id = msg.message_id

            game.nomination_pm_message_ids.clear()
            for p in game.alive_players():
                if p.silenced:
                    game.votes[p.user_id] = None
                    await self._safe_pm(bot, p.user_id, "💋 У тебя была Ночная Дива: сегодня ты не голосуешь.")
                    continue
                try:
                    pm = await bot.send_message(
                        p.user_id,
                        "🗳 Выбери, кого выдвигаешь на городской суд:",
                        reply_markup=vote_keyboard(game, p.user_id),
                    )
                    if pm:
                        game.nomination_pm_message_ids[p.user_id] = pm.message_id
                except Exception:
                    continue
            await self.persist(game)
            self._arm_phase_timer(game, nomination_seconds, lambda: self.end_nomination(bot, game))

    async def end_nomination(self, bot: Bot, game: GameState) -> None:
        next_step = "night"
        async with self.lock_for(game.chat_id):
            if store.get(game.chat_id) is not game or game.phase != Phase.NOMINATION:
                return
            await self._delete_pm_controls(bot, game.nomination_pm_message_ids)
            await self._safe_disable(bot, game.chat_id, game.nomination_message_id)
            await self._safe_delete(bot, game.chat_id, game.nomination_message_id)
            game.nomination_message_id = None
            for p in game.alive_players():
                if p.user_id not in game.votes:
                    game.votes[p.user_id] = None

            counts = Counter(v for v in game.votes.values() if v is not None)
            if not counts:
                await self._safe_group(bot, game.chat_id, "🤷 <b>Игроки не определились с выбором.</b>")
                game.nominated_id = None
                game.temp["resume_action"] = "start_night"
                await self._set_phase(game, Phase.RESOLVING, None)
            else:
                max_votes = max(counts.values())
                leaders = [uid for uid, count in counts.items() if count == max_votes]
                if len(leaders) != 1:
                    names = [player_link(game.get_player(uid)) for uid in leaders if game.get_player(uid)]
                    await self._safe_group(
                        bot,
                        game.chat_id,
                        "🤷 <b>Игроки не определились с выбором.</b>\n"
                        + ("Равенство голосов: " + ", ".join(names) if names else ""),
                    )
                    game.nominated_id = None
                    game.temp["resume_action"] = "start_night"
                    await self._set_phase(game, Phase.RESOLVING, None)
                else:
                    game.nominated_id = leaders[0]
                    candidate = game.get_player(game.nominated_id)
                    if not candidate or not candidate.alive:
                        game.nominated_id = None
                        game.temp["resume_action"] = "start_night"
                        await self._set_phase(game, Phase.RESOLVING, None)
                    else:
                        next_step = "verdict"
                        game.temp["resume_action"] = "start_verdict"
                        await self._set_phase(game, Phase.RESOLVING, None)
            await self.persist(game)

        if next_step == "verdict" and game.nominated_id:
            await self.start_verdict(bot, game)
        else:
            winner = await self.check_win(bot, game)
            if not winner:
                await self.start_night(bot, game, allow_from_resolving=True)

    async def start_verdict(self, bot: Bot, game: GameState) -> None:
        async with self.lock_for(game.chat_id):
            if store.get(game.chat_id) is not game:
                return
            if game.phase not in {Phase.RESOLVING, Phase.NOMINATION}:
                return
            candidate = game.get_player(game.nominated_id or 0)
            if not candidate or not candidate.alive:
                game.temp["resume_action"] = "start_night"
                await self._set_phase(game, Phase.RESOLVING, None)
                go_night = True
            else:
                go_night = False
                game.verdict_votes.clear()
                game.temp.pop("resume_action", None)
                verdict_seconds = self._duration(
                    game, "verdict_seconds", self.settings.verdict_seconds
                )
                await self._set_phase(game, Phase.VERDICT, verdict_seconds)
                from .keyboards import verdict_keyboard
                msg = await self._safe_group(
                    bot,
                    game.chat_id,
                    f"⚖️ <b>Город решает судьбу</b> {player_link(candidate)}\n"
                    f"До конца решения — {verdict_seconds} секунд.\n\n"
                    "👍 Казнить или 👎 Помиловать?",
                )
                if msg:
                    game.verdict_message_id = msg.message_id

                game.verdict_pm_message_ids.clear()
                for p in game.alive_players():
                    if p.user_id == candidate.user_id or p.silenced:
                        continue
                    try:
                        pm = await bot.send_message(
                            p.user_id,
                            f"⚖️ Казнить {escape(candidate.name)}?",
                            reply_markup=verdict_keyboard(game),
                        )
                        if pm:
                            game.verdict_pm_message_ids[p.user_id] = pm.message_id
                    except Exception:
                        continue
                await self.persist(game)
                self._arm_phase_timer(game, verdict_seconds, lambda: self.end_verdict(bot, game))

        if go_night:
            await self.start_night(bot, game, allow_from_resolving=True)

    async def end_verdict(self, bot: Bot, game: GameState) -> None:
        bomber: PlayerState | None = None
        fatalist_wins = False
        async with self.lock_for(game.chat_id):
            if store.get(game.chat_id) is not game or game.phase != Phase.VERDICT:
                return
            await self._delete_pm_controls(bot, game.verdict_pm_message_ids)
            await self._safe_disable(bot, game.chat_id, game.verdict_message_id)
            await self._safe_delete(bot, game.chat_id, game.verdict_message_id)
            game.verdict_message_id = None
            candidate = game.get_player(game.nominated_id or 0)

            yes_voters = [uid for uid, value in game.verdict_votes.items() if value]
            no_voters = [uid for uid, value in game.verdict_votes.items() if not value]
            yes_names = "\n".join(f"• {player_link(game.get_player(uid))}" for uid in yes_voters if game.get_player(uid)) or "• —"
            no_names = "\n".join(f"• {player_link(game.get_player(uid))}" for uid in no_voters if game.get_player(uid)) or "• —"
            await self._safe_group(
                bot,
                game.chat_id,
                "⚖️ <b>Вердикт города</b>\n"
                "━━━━━━━━━━━━\n"
                f"👍 <b>За казнь — {len(yes_voters)}</b>\n{yes_names}\n\n"
                f"👎 <b>За помилование — {len(no_voters)}</b>\n{no_names}",
            )

            executed = bool(candidate and candidate.alive and len(yes_voters) > len(no_voters) and len(yes_voters) > 0)
            if executed and candidate:
                if await self._consume_game_item_safe(
                    game, candidate.user_id, "day_shield", f"verdict:{game.day}:{candidate.user_id}"
                ):
                    await self._safe_group(bot, game.chat_id, f"☀️ <b>Солнечный иммунитет</b>\n{player_link(candidate)} избежал(а) казни.")
                else:
                    candidate.alive = False
                    game.pending_last_words.add(candidate.user_id)
                    await self._safe_group(
                        bot,
                        game.chat_id,
                        pick(GLOBAL["lynch"], name=player_link(candidate), role=role_title(candidate.role_key)),
                    )
                    await self._safe_pm(bot, candidate.user_id, pick(GLOBAL["last_word_prompt"]))
                    if candidate.role_key == "fatalist":
                        fatalist_wins = True
                    elif candidate.role_key == "bomber":
                        bomber = candidate
            elif candidate:
                await self._safe_group(bot, game.chat_id, f"🕊 <b>Город помиловал</b> {player_link(candidate)}.")

            promotions = self._inherit_roles(game)
            if fatalist_wins:
                game.temp["resume_action"] = "finish_fatalist"
            elif bomber:
                # Revenge is offered in the next normal NIGHT. Persist the dead
                # bomber id outside temp because start_night clears transient data.
                game.bomb_pending_for = bomber.user_id
                game.bomb_used = False
                game.temp["resume_action"] = "start_night"
            else:
                # The verdict may have created a win condition. Persist an explicit
                # recovery instruction before checking it so a Railway restart can
                # never skip the final result and start another night.
                game.temp["resume_action"] = "check_win_then_start_night"
            await self._set_phase(game, Phase.RESOLVING, None)
            await self.persist(game)

        if fatalist_wins:
            await self.finish_game(bot, game, "suicide")
            return
        if bomber:
            await self._announce_promotions(bot, game, promotions)
            await self._safe_group(bot, game.chat_id, living_summary(game, reveal_roles=True))
            await self.start_night(bot, game, allow_from_resolving=True)
            return
        winner = await self.check_win(bot, game)
        if winner:
            return
        await self._announce_promotions(bot, game, promotions)
        await self._safe_group(bot, game.chat_id, living_summary(game, reveal_roles=True))
        await self.start_night(bot, game, allow_from_resolving=True)

    async def _ask_bomber_revenge(self, bot: Bot, game: GameState, bomber: PlayerState) -> None:
        # Transitional implementation: revenge is a short resolving sub-phase.
        # The v3 role-interaction milestone can move the explosion fully into the
        # following night without changing the main state machine.
        from .keyboards import players_keyboard
        async with self.lock_for(game.chat_id):
            if store.get(game.chat_id) is not game:
                return
            game.bomb_pending_for = bomber.user_id
            game.bomb_used = False
            game.temp.pop("bomber_user_id", None)
            game.temp["resume_action"] = "continue_after_bomb"
            await self._set_phase(game, Phase.RESOLVING, 20)
            await self._safe_pm(
                bot,
                bomber.user_id,
                "💣 Тебя казнили. Выбери, кого забрать с собой:",
                reply_markup=players_keyboard(game, "bomb", exclude_id=bomber.user_id),
            )
            await self.persist(game)
            self._arm_phase_timer(game, 20, lambda: self._continue_after_bomb(bot, game))

    async def _continue_after_bomb(self, bot: Bot, game: GameState) -> None:
        async with self.lock_for(game.chat_id):
            if store.get(game.chat_id) is not game:
                return
            game.bomb_pending_for = None
            game.temp["resume_action"] = "check_win_then_start_night"
            await self._set_phase(game, Phase.RESOLVING, None)
        winner = await self.check_win(bot, game)
        if not winner:
            await self.start_night(bot, game, allow_from_resolving=True)

    def _detect_winner_state(self, game: GameState) -> str | None:
        alive = game.alive_players()
        if not alive:
            return "draw"
        teams = Counter(role_team(p.role_key) for p in alive)
        if game.mode == "virus" and teams.get("infected", 0) == len(alive):
            return "infected"
        if len(alive) == 1 and teams.get("maniac", 0) == 1:
            return "maniac"
        if teams.get("infected", 0):
            return None
        crime_mafia = teams.get("mafia", 0)
        crime_yakuza = teams.get("yakuza", 0)
        maniac = teams.get("maniac", 0)
        if game.mode == "clans":
            if crime_mafia == 0 and crime_yakuza == 0 and maniac == 0:
                return "town"
            if crime_mafia > 0 and crime_yakuza == 0 and crime_mafia >= len(alive) - crime_mafia:
                return "mafia"
            if crime_yakuza > 0 and crime_mafia == 0 and crime_yakuza >= len(alive) - crime_yakuza:
                return "yakuza"
            return None
        if crime_mafia == 0 and maniac == 0:
            return "town"
        if crime_mafia > 0 and crime_mafia >= len(alive) - crime_mafia:
            return "mafia"
        return None

    async def check_win(self, bot: Bot, game: GameState) -> str | None:
        if store.get(game.chat_id) is not game and game.phase != Phase.FINISHED:
            return None
        alive = game.alive_players()
        if not alive:
            await self.finish_game(bot, game, "draw")
            return "draw"

        teams = Counter(role_team(p.role_key) for p in alive)

        # Virus is its own faction victory: all surviving players are infected.
        if game.mode == "virus" and teams.get("infected", 0) == len(alive):
            # Once every survivor is infected, the surviving infected faction wins.
            # A converted Carrier does not need to personally spread the virus to
            # qualify; the reference rule is faction-wide, not a personal quota.
            winners = [p.user_id for p in alive if p.role_key == "carrier"]
            await self.finish_game(bot, game, "infected", extra_winners=winners)
            return "infected"

        # Maniac wins only as the sole survivor. The real ANARCHY log shows that
        # the game continues after mafia dies while Maniac is still alive.
        if len(alive) == 1 and teams.get("maniac", 0) == 1:
            await self.finish_game(bot, game, "maniac")
            return "maniac"

        # An active infected faction prevents ordinary town/crime resolution.
        if teams.get("infected", 0):
            return None

        crime_mafia = teams.get("mafia", 0)
        crime_yakuza = teams.get("yakuza", 0)
        maniac = teams.get("maniac", 0)

        if game.mode == "clans":
            if crime_mafia == 0 and crime_yakuza == 0 and maniac == 0:
                await self.finish_game(bot, game, "town")
                return "town"
            if crime_mafia > 0 and crime_yakuza == 0 and crime_mafia >= len(alive) - crime_mafia:
                await self.finish_game(bot, game, "mafia")
                return "mafia"
            if crime_yakuza > 0 and crime_mafia == 0 and crime_yakuza >= len(alive) - crime_yakuza:
                await self.finish_game(bot, game, "yakuza")
                return "yakuza"
            return None

        # Classic/Chaos: town wins only when both mafia and Maniac are gone.
        if crime_mafia == 0 and maniac == 0:
            await self.finish_game(bot, game, "town")
            return "town"
        # Mafia parity is compared with *all* non-mafia survivors, including
        # neutral roles. This avoids premature victories.
        if crime_mafia > 0 and crime_mafia >= len(alive) - crime_mafia:
            await self.finish_game(bot, game, "mafia")
            return "mafia"
        return None

    def _winner_ids_for(
        self, game: GameState, winner: str, extra_winners: list[int] | None = None
    ) -> set[int]:
        if winner == "draw":
            return set()
        if extra_winners is not None:
            return set(extra_winners)
        if winner == "suicide":
            return {p.user_id for p in game.players.values() if p.role_key == "fatalist"}

        # Reference behavior: ordinary faction victory belongs to survivors,
        # not every player who started on that faction and died earlier.
        winner_ids = {
            p.user_id for p in game.players.values()
            if p.alive and role_team(p.role_key) == winner
        }
        # Dead Bodyguard is the explicit exception: he wins when the player he
        # actually saved is among the eventual winners.
        for p in game.players.values():
            # bodyguard_saved_id is written only by an actually executed
            # interception, so use the performed deed rather than the player's
            # possibly Joker-swapped final role.
            if p.alive or not p.bodyguard_saved_id:
                continue
            if p.bodyguard_saved_id in winner_ids:
                winner_ids.add(p.user_id)
        return winner_ids

    def _arm_finalization_retry(self, bot: Bot, game: GameState) -> None:
        previous = self.finalization_tasks.get(game.chat_id)
        if previous and not previous.done():
            return

        async def runner() -> None:
            try:
                while store.get(game.chat_id) is game and game.phase == Phase.FINISHED:
                    await asyncio.sleep(5)
                    winner = game.temp.get("final_winner")
                    raw_ids = game.temp.get("final_winner_ids") or []
                    if not winner:
                        return
                    ok = await self._complete_finished_game(
                        bot, game, str(winner), {int(x) for x in raw_ids}
                    )
                    if ok:
                        return
            except asyncio.CancelledError:
                return
            except Exception:
                self.log.exception("FINISHED retry crashed chat=%s", game.chat_id)

        self.finalization_tasks[game.chat_id] = asyncio.create_task(runner())

    async def _complete_finished_game(
        self, bot: Bot, game: GameState, winner: str, winner_ids: set[int]
    ) -> bool:
        """Finish UI/stats safely; may be called again after a process restart."""
        await self._disable_pm_controls(bot, game.night_pm_message_ids)
        await self._disable_pm_controls(bot, game.nomination_pm_message_ids)
        await self._disable_pm_controls(bot, game.verdict_pm_message_ids)
        await self._safe_disable(bot, game.chat_id, game.nomination_message_id)
        await self._safe_disable(bot, game.chat_id, game.verdict_message_id)
        await self.close_registration_ui(bot, game, persist_after=False)

        # Public final screen is cosmetic; remember a successful send to avoid an
        # intentional duplicate when only reward finalisation needs a retry.
        if not game.temp.get("final_message_sent"):
            if winner == "draw":
                header = "🏁 <b>Игра окончена без победителя.</b>"
            elif winner == "suicide":
                header = pick(GLOBAL.get("win_suicide", ["🪦 Фаталист добился своего!"]))
            else:
                header = pick(GLOBAL.get(f"win_{winner}", ["🏆 Игра окончена!"]))

            if winner == "town":
                await send_phase_sticker(bot, game.chat_id, "win_town")
            elif winner == "mafia":
                await send_phase_sticker(bot, game.chat_id, "win_mafia")

            duration = max(
                0,
                int((game.finished_at or time.time()) - (game.started_at or game.finished_at or time.time())),
            )
            minutes, seconds = divmod(duration, 60)
            duration_text = f"{minutes} мин. {seconds} сек." if minutes else f"{seconds} сек."
            lines = [header, "", "<b>Победившие игроки:</b>"]
            winners = [p for p in game.players.values() if p.user_id in winner_ids]
            lines += [f"{escape(p.name)} — {role_title(p.role_key)}" for p in winners] if winners else ["—"]
            lines += ["", "<b>Остальные игроки:</b>"]
            others = [p for p in game.players.values() if p.user_id not in winner_ids]
            lines += [f"{escape(p.name)} — {role_title(p.role_key)}" for p in others] if others else ["—"]
            lines += ["", f"⏱ <b>Игра длилась:</b> {duration_text}"]
            sent = await self._safe_group(bot, game.chat_id, "\n".join(lines))
            if sent is not None:
                game.temp["final_message_sent"] = True
                try:
                    await self.storage.save_game_state(game)
                except Exception:
                    self.log.exception("Could not persist final-message marker chat=%s", game.chat_id)

        reward_enabled = len(game.players) >= self.settings.min_reward_players
        rewards_ok = True
        reward_once = getattr(self.storage, "reward_once", None)
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
            # If this reward was committed before a crash, do not spam its PM on
            # replay. The money/XP is already correct thanks to reward_once.
            if reward.get("already_applied"):
                continue
            if reward_enabled and win:
                text = (
                    f"🏆 За победу в Mafia Optimisma тебе начислено {reward['money']} 💵.\n"
                    "Нажми /profile, чтобы посмотреть аккаунт."
                )
            elif not reward_enabled:
                text = f"💵 В партиях меньше {self.settings.min_reward_players} игроков денежная награда не начисляется."
            else:
                text = "🏁 Партия окончена. Статистика сохранена."
            if reward["level_up"]:
                text += f"\n🌟 Новый уровень: {reward['level']}!"
            await self._safe_pm(bot, p.user_id, text)

        if not rewards_ok:
            # Keep the FINISHED snapshot. A local retry and, if needed, the next
            # process startup can safely continue the missing rewards.
            try:
                await self.storage.save_game_state(game)
            except Exception:
                self.log.exception("Could not retain unfinished finalisation chat=%s", game.chat_id)
            return False

        # Ranking history is part of finalisation too. If this tiny idempotent
        # write fails, keep the FINISHED snapshot so the retry can restore it;
        # otherwise weekly/team statistics could silently lose a completed game.
        try:
            await record_game_result(self.storage, game, winner)
        except Exception:
            self.log.exception("Could not record game result chat=%s session=%s", game.chat_id, game.session_id)
            try:
                await self.storage.save_game_state(game)
            except Exception:
                pass
            return False

        try:
            await self.storage.delete_game_state(game.chat_id)
        except Exception:
            # Rewards are idempotent, so retrying deletion/finalisation is safe.
            self.log.exception("Could not delete finished snapshot chat=%s", game.chat_id)
            return False

        await self._cleanup_item_events(game.session_id)
        store.remove_game(game.chat_id)
        retry = self.finalization_tasks.pop(game.chat_id, None)
        if retry and retry is not asyncio.current_task() and not retry.done():
            retry.cancel()
        return True

    async def finish_game(
        self, bot: Bot, game: GameState, winner: str, extra_winners: list[int] | None = None
    ) -> None:
        if game.phase == Phase.FINISHED:
            stored_winner = game.temp.get("final_winner")
            raw_ids = game.temp.get("final_winner_ids") or []
            if stored_winner:
                ok = await self._complete_finished_game(
                    bot, game, str(stored_winner), {int(x) for x in raw_ids}
                )
                if not ok:
                    self._arm_finalization_retry(bot, game)
            return

        winner_ids = self._winner_ids_for(game, winner, extra_winners)
        game.phase = Phase.FINISHED
        game.phase_version += 1
        game.phase_deadline = None
        game.finished_at = time.time()
        game.temp["final_winner"] = winner
        game.temp["final_winner_ids"] = sorted(winner_ids)
        game.temp["final_message_sent"] = False

        # Persist finalisation intent before any reward. Even if this write fails,
        # reward_once makes a later replay financially idempotent.
        try:
            await self.storage.save_game_state(game)
        except Exception:
            self.log.exception("Could not persist FINISHED snapshot chat=%s", game.chat_id)

        task = self.tasks.pop(game.chat_id, None)
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
        warning = self.warning_tasks.pop(game.chat_id, None)
        if warning and not warning.done():
            warning.cancel()

        ok = await self._complete_finished_game(bot, game, winner, winner_ids)
        if not ok:
            self._arm_finalization_retry(bot, game)

    async def _notify_team(
        self, bot: Bot, game: GameState, actor: PlayerState, text: str, *, attribution: bool = True
    ) -> None:
        team = role_team(actor.role_key)
        if team not in {"mafia", "yakuza"}:
            return
        payload = f"📨 {escape(actor.name)}: {text}" if attribution else text
        for p in game.alive_players():
            if p.user_id != actor.user_id and role_team(p.role_key) == team:
                await self._safe_pm(bot, p.user_id, payload)

    async def team_chat(self, bot: Bot, game: GameState, sender: PlayerState, text: str) -> bool:
        if game.phase != Phase.NIGHT or not sender.alive:
            return False

        team = role_team(sender.role_key)
        if team in {"mafia", "yakuza"}:
            recipients = [
                p for p in game.alive_players()
                if p.user_id != sender.user_id and role_team(p.role_key) == team
            ]
            prefix = "Сообщник"
        elif sender.role_key in {"surgeon", "mercy_sister"}:
            # Reference rule: Doctor and Medical Sister can communicate through
            # the bot while they are both alive. Keep this channel separate from
            # the town at large.
            partner_role = "mercy_sister" if sender.role_key == "surgeon" else "surgeon"
            recipients = [
                p for p in game.alive_players()
                if p.user_id != sender.user_id and p.role_key == partner_role
            ]
            prefix = "Напарник"
        else:
            return False

        sent = False
        for p in recipients:
            await self._safe_pm(bot, p.user_id, f"{prefix} {escape(sender.name)}: {escape(text)}")
            sent = True
        return sent

    async def _safe_pm(self, bot: Bot, user_id: int, text: str, **kwargs):
        game = store.game_by_user(user_id)
        if game and self._feature(game, "protect_private_content", False):
            kwargs.setdefault("protect_content", True)
        try:
            return await bot.send_message(user_id, text, **kwargs)
        except Exception:
            self.log.exception("Telegram private send failed user=%s", user_id)
            return None

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
            return True
        await self._safe_group(
            bot,
            game.chat_id,
            pick(GLOBAL["last_word_public"], name=escape(player.name), text=escape(text)),
        )
        return True

    def registration_text(self, game: GameState) -> str:
        mode = MODES[game.mode]
        if game.phase_deadline is not None:
            remaining = max(0, int(round(game.phase_deadline - time.time())))
        else:
            remaining = 0
        lines = [
            "🎭 <b>MAFIA OPTIMISMA — СБОР ГОРОДА</b>",
            "",
            f"Режим: {mode['emoji']} <b>{mode['name']}</b>",
            f"⏳ До конца регистрации: <b>{remaining} сек.</b>",
            "",
            "👥 <b>Зарегистрированы:</b>",
        ]
        if game.players:
            for p in sorted(game.players.values(), key=lambda x: (x.number or 10**9, x.user_id)):
                tag = f"@{p.username}" if p.username else escape(p.name)
                lines.append(f"{p.number}) {tag}")
        else:
            lines.append("— пока никого")
        lines += [
            "",
            f"👤 Всего: <b>{len(game.players)}</b>",
            f"Минимум для старта: <b>{MODES[game.mode]['min_players']}</b>",
            "",
            "Нажми кнопку ниже, чтобы войти в игру.",
        ]
        return "\n".join(lines)

    async def public_registration_message(self, bot: Bot, game: GameState) -> None:
        msg = await self._safe_group(
            bot,
            game.chat_id,
            self.registration_text(game),
            reply_markup=join_keyboard(game),
        )
        if not msg:
            return
        game.registration_message_id = msg.message_id
        game.pinned_message_id = msg.message_id
        try:
            await bot.pin_chat_message(game.chat_id, msg.message_id, disable_notification=True)
        except Exception:
            self.log.exception("Could not pin registration chat=%s", game.chat_id)
        await self.persist(game)

    async def update_registration_message(self, bot: Bot, game: GameState) -> None:
        message_id = game.registration_message_id or game.pinned_message_id
        if not message_id:
            await self.public_registration_message(bot, game)
            return
        try:
            await bot.edit_message_text(
                self.registration_text(game),
                chat_id=game.chat_id,
                message_id=message_id,
                reply_markup=join_keyboard(game),
            )
        except TelegramBadRequest:
            # "message is not modified", an admin deleting it, etc. must never
            # affect the phase timer or the game state.
            return
        except Exception:
            self.log.exception("Could not update registration card chat=%s", game.chat_id)

    async def close_registration_ui(self, bot: Bot, game: GameState, persist_after: bool = True) -> None:
        """Disable, unpin and remove the active registration card.

        The order deliberately closes the callback first. Even if unpin/delete
        fails, registration is already logically closed by the state machine.
        """
        message_id = game.registration_message_id or game.pinned_message_id
        await self._safe_disable(bot, game.chat_id, message_id)
        await self._safe_unpin(bot, game.chat_id, message_id)
        await self._safe_delete(bot, game.chat_id, game.registration_warning_id)
        await self._safe_delete(bot, game.chat_id, message_id)
        game.registration_message_id = None
        game.registration_warning_id = None
        game.pinned_message_id = None
        if persist_after and game.phase != Phase.FINISHED:
            await self.persist(game)

    async def process_noop(self, bot: Bot, chat_id: int) -> None:
        return

    def format_profile(self, profile: dict) -> str:
        item_lines = []
        from .content import ITEMS
        for key, item in ITEMS.items():
            item_lines.append(f"{item['emoji']} {item['name']}: {profile['items'].get(key, 0)}")
        return (
            f"👤 <b>Профиль</b>\n"
            f"🆔 ID: <code>{profile['user_id']}</code>\n"
            f"💵 Деньги: {profile['money']}\n"
            f"💎 Камни: {profile['gems']}\n"
            f"🌟 Уровень: {profile['level']} | XP: {profile['xp']}\n"
            f"🎮 Игры: {profile['games']} | Победы: {profile['wins']}\n\n"
            + "\n".join(item_lines)
        )
