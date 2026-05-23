from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Phase(StrEnum):
    IDLE = "idle"
    REGISTRATION = "registration"
    NIGHT = "night"
    DISCUSSION = "discussion"
    VOTING = "voting"
    FINISHED = "finished"


@dataclass
class PlayerState:
    user_id: int
    name: str
    username: str | None = None
    role_key: str | None = None
    alive: bool = True
    blocked: bool = False
    silenced: bool = False
    action_done: bool = False
    self_heals_used: int = 0
    checked_ids: set[int] = field(default_factory=set)
    swapped_once: bool = False


@dataclass
class NightAction:
    actor_id: int
    action_type: str
    target_id: int | None = None
    target2_id: int | None = None
    item: str | None = None


@dataclass
class GameState:
    chat_id: int
    chat_title: str
    mode: str = "classic"
    phase: Phase = Phase.IDLE
    day: int = 0
    players: dict[int, PlayerState] = field(default_factory=dict)
    actions: dict[int, NightAction] = field(default_factory=dict)
    votes: dict[int, int | None] = field(default_factory=dict)
    temp: dict[int, dict] = field(default_factory=dict)
    pinned_message_id: int | None = None
    pending_last_words: set[int] = field(default_factory=set)

    def alive_players(self) -> list[PlayerState]:
        return [p for p in self.players.values() if p.alive]

    def get_player(self, user_id: int) -> PlayerState | None:
        return self.players.get(user_id)

    def role_of(self, user_id: int) -> str | None:
        p = self.players.get(user_id)
        return p.role_key if p else None
