from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_path: str = "mafia_optimisma.sqlite3"
    registration_seconds: int = 90
    registration_warning_seconds: int = 30
    night_seconds: int = 60
    discussion_seconds: int = 45
    nomination_seconds: int = 30
    verdict_seconds: int = 20
    min_reward_players: int = 6

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Заполни BOT_TOKEN в .env или переменной окружения")
        return cls(
            bot_token=token,
            database_path=os.getenv("DATABASE_PATH", "mafia_optimisma.sqlite3"),
            registration_seconds=int(os.getenv("REGISTRATION_SECONDS", "90")),
            registration_warning_seconds=int(os.getenv("REGISTRATION_WARNING_SECONDS", "30")),
            night_seconds=int(os.getenv("NIGHT_SECONDS", "60")),
            discussion_seconds=int(os.getenv("DISCUSSION_SECONDS", "45")),
            nomination_seconds=int(os.getenv("NOMINATION_SECONDS", "30")),
            verdict_seconds=int(os.getenv("VERDICT_SECONDS", "20")),
            min_reward_players=int(os.getenv("MIN_REWARD_PLAYERS", "6")),
        )


BASE_DIR = Path(__file__).resolve().parent.parent
