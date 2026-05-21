from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Phase(StrEnum):
    REGISTRATION = "registration"
    NIGHT = "night"
    DISCUSSION = "discussion"
    VOTING = "voting"
    FINISHED = "finished"


@dataclass(slots=True)
class RoleConfig:
    key: str
    name: str
    emoji: str
    old_role: str
    team: str
    clan: str
    priority: int | None
    action_type: str
    has_night_action: bool
    short_description: str
    private_intro: list[str]
    night_prompts: list[str]
    chat_action_phrases: list[str]
    result_phrases: list[str]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return f"{self.emoji} {self.name}"


@dataclass(slots=True)
class PlayerState:
    user_id: int
    name: str
    username: str | None = None
    role_key: str | None = None
    alive: bool = True
    silenced: bool = False
    blocked: bool = False
    self_heals_used: int = 0
    infected_spread_count: int = 0
    swapped_once: bool = False

    @property
    def mention(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.name


@dataclass(slots=True)
class NightAction:
    actor_id: int
    action_type: str
    target_id: int | None = None
    target2_id: int | None = None
    item: str | None = None


@dataclass(slots=True)
class GameState:
    chat_id: int
    chat_title: str
    mode: str = "classic"
    phase: Phase = Phase.REGISTRATION
    day: int = 0
    players: dict[int, PlayerState] = field(default_factory=dict)
    actions: dict[int, NightAction] = field(default_factory=dict)
    votes: dict[int, int | None] = field(default_factory=dict)
    temp: dict[int, dict[str, Any]] = field(default_factory=dict)
    pending_last_words: set[int] = field(default_factory=set)
    pinned_message_id: int | None = None
    task_name: str | None = None

    def alive_players(self) -> list[PlayerState]:
        return [p for p in self.players.values() if p.alive]

    def dead_players(self) -> list[PlayerState]:
        return [p for p in self.players.values() if not p.alive]

    def get_player(self, user_id: int) -> PlayerState | None:
        return self.players.get(user_id)

    def role_of(self, user_id: int) -> str | None:
        p = self.players.get(user_id)
        return p.role_key if p else None
