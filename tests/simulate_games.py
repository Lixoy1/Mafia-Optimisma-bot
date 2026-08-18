"""Headless stress simulation for Mafia Optimisma v3.

Runs hundreds of complete Classic/Chaos games without Telegram. The goal is not
strategy quality; it is to prove that the state machine keeps advancing and every
party reaches FINISHED under aggressive deterministic daytime voting.
"""
from __future__ import annotations

import asyncio
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_core import (  # noqa: E402
    FakeBot,
    FakeStorage,
    Settings,
    GameEngine,
    GameState,
    NightAction,
    Phase,
    PlayerState,
    generate_roles,
    store,
)
from mafia_optimisma.engine import role_team  # noqa: E402
from mafia_optimisma.content import ROLES  # noqa: E402


def choose(seq):
    return random.choice(list(seq)) if seq else None


def build_actions(game: GameState) -> dict[int, NightAction]:
    alive = game.alive_players()
    actions: dict[int, NightAction] = {}
    for p in alive:
        role = ROLES[p.role_key or "optimist"]
        action = role.action_type
        others = [x for x in alive if x.user_id != p.user_id]
        if not others:
            continue

        def target_non_team(team: str):
            candidates = [x for x in others if role_team(x.role_key) != team]
            return choose(candidates)

        target = None
        action_type = None
        target2 = None
        if action in {"mafia_kill_leader", "mafia_kill_backup"}:
            target = choose(others) if game.mode == "chaos" else target_non_team("mafia")
            action_type = "mafia_kill"
        elif action in {"yakuza_kill_leader", "yakuza_kill_backup"}:
            target = target_non_team("yakuza")
            action_type = "yakuza_kill"
        elif action == "heal":
            candidates = list(alive)
            if p.self_heals_used >= 1:
                candidates = [x for x in candidates if x.user_id != p.user_id]
            target = choose(candidates)
            action_type = "heal"
        elif action == "check_or_shoot":
            target = choose(others)
            can_shoot = game.mode in {"chaos", "virus", "clans"} or (game.mode == "classic" and game.day >= 2)
            action_type = "shoot" if can_shoot and random.random() < 0.25 else "check"
        elif action == "block_and_silence":
            target = choose(others)
            action_type = "block_and_silence"
        elif action == "mafia_role_check":
            target = choose(others)
            action_type = "mafia_role_check"
        elif action == "bodyguard":
            target = choose(others)
            action_type = "bodyguard"
        elif action in {"watch_visitors", "mafia_watch_visitors", "yakuza_watch_visitors", "solo_kill"}:
            target = choose(others)
            action_type = action
        elif action == "mafia_mask":
            target = choose([x for x in alive if role_team(x.role_key) == "mafia"])
            action_type = "mafia_mask"
        elif action == "yakuza_mask":
            target = choose([x for x in alive if role_team(x.role_key) == "yakuza"])
            action_type = "yakuza_mask"
        elif action == "compare_clans":
            first = choose(others)
            remaining = [x for x in alive if first and x.user_id != first.user_id]
            second = choose(remaining)
            if first and second:
                target, target2, action_type = first, second.user_id, "compare_clans"
        elif action == "swap_roles":
            candidates = [x for x in alive if not x.swapped_once]
            if len(candidates) >= 2:
                first, second = random.sample(candidates, 2)
                target, target2, action_type = first, second.user_id, "swap_roles"

        if target and action_type:
            actions[p.user_id] = NightAction(
                actor_id=p.user_id,
                action_type=action_type,
                target_id=target.user_id,
                target2_id=target2,
                actor_role_key=p.role_key,
            )
    return actions


async def one_game(mode: str, player_count: int, seed: int) -> tuple[int, str]:
    random.seed(seed)
    storage = FakeStorage()
    settings = Settings(
        "x",
        registration_seconds=999,
        registration_warning_seconds=30,
        night_seconds=999,
        discussion_seconds=999,
        nomination_seconds=999,
        verdict_seconds=999,
    )
    engine = GameEngine(settings, storage)
    bot = FakeBot()
    chat_id = 1_000_000 + seed
    game = GameState(chat_id, f"sim-{seed}", mode=mode, phase=Phase.NIGHT, day=0, started_at=time.time())
    roles = generate_roles(mode, player_count)
    for i, role in enumerate(roles, 1):
        game.players[i] = PlayerState(i, f"P{i}", number=i, role_key=role, initial_role_key=role)
    store.games[chat_id] = game
    for uid in game.players:
        store.user_to_chat[uid + seed * 1000] = chat_id  # irrelevant isolation

    # Enter night using the real state-machine function so day numbering and
    # per-phase resets are tested too.
    game.phase = Phase.RESOLVING
    await engine.start_night(bot, game, allow_from_resolving=True)

    for turn in range(1, player_count + 12):
        if store.get(chat_id) is None or game.phase == Phase.FINISHED:
            return turn, "finished"
        if game.phase != Phase.NIGHT:
            raise AssertionError(f"game {seed}: expected NIGHT, got {game.phase}")

        game.actions = build_actions(game)
        await engine.persist(game)
        engine.cancel_timer(chat_id)
        await engine.end_night(bot, game)
        if store.get(chat_id) is None:
            return turn, "night_win"
        engine.cancel_timer(chat_id)
        if game.phase != Phase.DISCUSSION:
            raise AssertionError(f"game {seed}: expected DISCUSSION, got {game.phase}")

        await engine.start_nomination(bot, game)
        engine.cancel_timer(chat_id)
        alive = game.alive_players()
        if len(alive) <= 1:
            await engine.check_win(bot, game)
            if store.get(chat_id) is None:
                return turn, "single_survivor"

        candidate = choose(alive)
        if candidate:
            game.votes = {
                p.user_id: (None if p.user_id == candidate.user_id or p.silenced else candidate.user_id)
                for p in alive
            }
        await engine.persist(game)
        await engine.end_nomination(bot, game)
        engine.cancel_timer(chat_id)
        if store.get(chat_id) is None:
            return turn, "nomination_win"

        if game.phase == Phase.VERDICT:
            candidate = game.get_player(game.nominated_id or 0)
            game.verdict_votes = {
                p.user_id: True
                for p in game.alive_players()
                if candidate and p.user_id != candidate.user_id and not p.silenced
            }
            await engine.persist(game)
            await engine.end_verdict(bot, game)
            engine.cancel_timer(chat_id)
            if game.bomb_pending_for and game.phase == Phase.NIGHT:
                # New v3 behavior: revenge is part of the following ordinary night.
                targets = game.alive_players()
                if targets:
                    game.temp["bomb_target_id"] = choose(targets).user_id
                    game.bomb_used = True
                    await engine.persist(game)
        if store.get(chat_id) is None:
            return turn, "day_win"
        if game.phase != Phase.NIGHT:
            raise AssertionError(f"game {seed}: day did not advance, phase={game.phase}")

    raise AssertionError(
        f"game {seed} mode={mode} players={player_count} did not finish; "
        f"phase={game.phase}, alive={[(p.name,p.role_key) for p in game.alive_players()]}"
    )


async def main():
    store.games.clear()
    store.user_to_chat.clear()
    total = 0
    max_turns = 0
    outcomes = {}
    seed = 1000
    for mode, games, low, high in [
        ("classic", 500, 4, 18),
        ("chaos", 500, 4, 20),
        ("virus", 250, 4, 20),
        ("clans", 250, 12, 24),
    ]:
        for i in range(games):
            n = low + (i % (high - low + 1))
            turns, outcome = await one_game(mode, n, seed)
            seed += 1
            total += 1
            max_turns = max(max_turns, turns)
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
    print(f"SIMULATION OK: {total} games, max_turns={max_turns}, outcomes={outcomes}")


if __name__ == "__main__":
    asyncio.run(main())
