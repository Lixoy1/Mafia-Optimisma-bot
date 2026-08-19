from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Phase(StrEnum):
    REGISTRATION = "registration"
    NIGHT = "night"
    DISCUSSION = "discussion"
    NOMINATION = "nomination"
    VERDICT = "verdict"
    RESOLVING = "resolving"
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
    number: int = 0
    role_key: str | None = None
    initial_role_key: str | None = None
    alive: bool = True
    silenced: bool = False
    blocked: bool = False
    self_heals_used: int = 0
    infected_spread_count: int = 0
    swapped_once: bool = False
    # Bodyguard special victory rule: if the guard dies while intercepting an
    # attack, remember the player whose life was actually saved. A dead guard
    # wins only if that saved player ultimately wins.
    bodyguard_saved_id: int | None = None
    checked_ids: set[int] = field(default_factory=set)

    @property
    def mention(self) -> str:
        return f"@{self.username}" if self.username else self.name

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["checked_ids"] = sorted(self.checked_ids)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlayerState":
        payload = dict(data)
        payload["checked_ids"] = set(payload.get("checked_ids") or [])
        return cls(**payload)


@dataclass(slots=True)
class NightAction:
    actor_id: int
    action_type: str
    target_id: int | None = None
    target2_id: int | None = None
    item: str | None = None
    # Snapshot of the role when the action was submitted. This keeps an already
    # accepted action deterministic even if Joker/infection changes the role later.
    actor_role_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NightAction":
        return cls(**data)


@dataclass(slots=True)
class GameState:
    chat_id: int
    chat_title: str
    mode: str = "classic"
    phase: Phase = Phase.REGISTRATION
    day: int = 0
    session_id: str = field(default_factory=lambda: secrets.token_hex(5))
    phase_version: int = 0
    phase_started_at: float | None = None
    phase_deadline: float | None = None
    started_at: float | None = None
    finished_at: float | None = None

    players: dict[int, PlayerState] = field(default_factory=dict)
    actions: dict[int, NightAction] = field(default_factory=dict)

    # Stage 1 voting: voter -> nominated target or None (skip).
    votes: dict[int, int | None] = field(default_factory=dict)
    # Stage 2 voting: voter -> True (execute) / False (pardon) / None (abstain).
    # Missing key means the player has not voted at all.
    verdict_votes: dict[int, bool | None] = field(default_factory=dict)
    nominated_id: int | None = None

    temp: dict[Any, Any] = field(default_factory=dict)
    pending_last_words: set[int] = field(default_factory=set)

    # Important UI messages. Active controls must be disabled/deleted when their
    # phase closes; history messages are intentionally not stored here.
    registration_message_id: int | None = None
    registration_warning_id: int | None = None
    nomination_message_id: int | None = None
    verdict_message_id: int | None = None
    pinned_message_id: int | None = None
    night_pm_message_ids: dict[int, int] = field(default_factory=dict)
    nomination_pm_message_ids: dict[int, int] = field(default_factory=dict)
    verdict_pm_message_ids: dict[int, int] = field(default_factory=dict)

    bomb_pending_for: int | None = None
    bomb_used: bool = False
    armor_piercing_pending: set[int] = field(default_factory=set)

    def alive_players(self) -> list[PlayerState]:
        return [p for p in self.players.values() if p.alive]

    def dead_players(self) -> list[PlayerState]:
        return [p for p in self.players.values() if not p.alive]

    def get_player(self, user_id: int) -> PlayerState | None:
        return self.players.get(user_id)

    def role_of(self, user_id: int) -> str | None:
        p = self.players.get(user_id)
        return p.role_key if p else None

    def next_player_number(self) -> int:
        if not self.players:
            return 1
        return max((p.number for p in self.players.values()), default=0) + 1

    def to_dict(self) -> dict[str, Any]:
        # temp can contain non-JSON keys/objects in the legacy engine. Persist only
        # string-keyed primitive values that are safe to restore.
        safe_temp: dict[str, Any] = {}
        for key, value in self.temp.items():
            if not isinstance(key, str):
                continue
            if value is None or isinstance(value, (str, int, float, bool)):
                safe_temp[key] = value
            elif isinstance(value, list) and all(isinstance(x, (str, int, float, bool, type(None))) for x in value):
                safe_temp[key] = value
            elif key == "_chat_settings" and isinstance(value, dict):
                # Per-game admin rules are already JSON-backed settings. Keep the
                # snapshot with the game so a Railway restart cannot silently
                # change moderation, timing or voting UI mid-party.
                safe_temp[key] = value

        return {
            "chat_id": self.chat_id,
            "chat_title": self.chat_title,
            "mode": self.mode,
            "phase": self.phase.value,
            "day": self.day,
            "session_id": self.session_id,
            "phase_version": self.phase_version,
            "phase_started_at": self.phase_started_at,
            "phase_deadline": self.phase_deadline,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "players": {str(uid): p.to_dict() for uid, p in self.players.items()},
            "actions": {str(uid): a.to_dict() for uid, a in self.actions.items()},
            "votes": {str(uid): target for uid, target in self.votes.items()},
            "verdict_votes": {str(uid): value for uid, value in self.verdict_votes.items()},
            "nominated_id": self.nominated_id,
            "temp": safe_temp,
            "pending_last_words": sorted(self.pending_last_words),
            "registration_message_id": self.registration_message_id,
            "registration_warning_id": self.registration_warning_id,
            "nomination_message_id": self.nomination_message_id,
            "verdict_message_id": self.verdict_message_id,
            "pinned_message_id": self.pinned_message_id,
            "night_pm_message_ids": {str(k): v for k, v in self.night_pm_message_ids.items()},
            "nomination_pm_message_ids": {str(k): v for k, v in self.nomination_pm_message_ids.items()},
            "verdict_pm_message_ids": {str(k): v for k, v in self.verdict_pm_message_ids.items()},
            "bomb_pending_for": self.bomb_pending_for,
            "bomb_used": self.bomb_used,
            "armor_piercing_pending": sorted(self.armor_piercing_pending),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameState":
        game = cls(
            chat_id=int(data["chat_id"]),
            chat_title=data.get("chat_title") or "чат",
            mode=data.get("mode") or "classic",
            phase=Phase(data.get("phase") or Phase.REGISTRATION.value),
            day=int(data.get("day") or 0),
            session_id=data.get("session_id") or secrets.token_hex(5),
            phase_version=int(data.get("phase_version") or 0),
            phase_started_at=data.get("phase_started_at"),
            phase_deadline=data.get("phase_deadline"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
        )
        game.players = {
            int(uid): PlayerState.from_dict(payload)
            for uid, payload in (data.get("players") or {}).items()
        }
        game.actions = {
            int(uid): NightAction.from_dict(payload)
            for uid, payload in (data.get("actions") or {}).items()
        }
        game.votes = {
            int(uid): (None if target is None else int(target))
            for uid, target in (data.get("votes") or {}).items()
        }
        game.verdict_votes = {
            int(uid): (None if value is None else bool(value))
            for uid, value in (data.get("verdict_votes") or {}).items()
        }
        game.nominated_id = data.get("nominated_id")
        if game.nominated_id is not None:
            game.nominated_id = int(game.nominated_id)
        game.temp = dict(data.get("temp") or {})
        game.pending_last_words = set(int(x) for x in (data.get("pending_last_words") or []))
        game.registration_message_id = data.get("registration_message_id")
        game.registration_warning_id = data.get("registration_warning_id")
        game.nomination_message_id = data.get("nomination_message_id")
        game.verdict_message_id = data.get("verdict_message_id")
        game.pinned_message_id = data.get("pinned_message_id")
        game.night_pm_message_ids = {int(k): int(v) for k, v in (data.get("night_pm_message_ids") or {}).items()}
        game.nomination_pm_message_ids = {int(k): int(v) for k, v in (data.get("nomination_pm_message_ids") or {}).items()}
        game.verdict_pm_message_ids = {int(k): int(v) for k, v in (data.get("verdict_pm_message_ids") or {}).items()}
        game.bomb_pending_for = data.get("bomb_pending_for")
        game.bomb_used = bool(data.get("bomb_used", False))
        game.armor_piercing_pending = set(int(x) for x in (data.get("armor_piercing_pending") or []))
        return game
