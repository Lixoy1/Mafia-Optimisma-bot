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
    registration_seconds: int = 60
    night_seconds: int = 45
    discussion_seconds: int = 45
    voting_seconds: int = 45

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "❌ BOT_TOKEN не найден!\n"
                "Создай файл .env и добавь BOT_TOKEN=ваш_токен"
            )
        return cls(
            bot_token=token,
            database_path=os.getenv("DATABASE_PATH", "mafia_optimisma.sqlite3"),
            registration_seconds=int(os.getenv("REGISTRATION_SECONDS", "60")),
            night_seconds=int(os.getenv("NIGHT_SECONDS", "45")),
            discussion_seconds=int(os.getenv("DISCUSSION_SECONDS", "45")),
            voting_seconds=int(os.getenv("VOTING_SECONDS", "45")),
        )


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
