from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from .content import ITEMS

DEFAULT_ITEMS = {key: 0 for key in ITEMS}


class Storage:
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        db_path = Path(self.path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    name TEXT NOT NULL,
                    money INTEGER NOT NULL DEFAULT 100,
                    gems INTEGER NOT NULL DEFAULT 0,
                    xp INTEGER NOT NULL DEFAULT 0,
                    level INTEGER NOT NULL DEFAULT 1,
                    games INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    items TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_users (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    name TEXT NOT NULL,
                    call_enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    PRIMARY KEY (chat_id, user_id)
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_chat_users_chat ON chat_users (chat_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_chat_users_updated ON chat_users (updated_at DESC)")
            await db.commit()

    async def ensure_profile(self, user_id: int, name: str, username: str | None) -> dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await self._fetch_profile_row(db, user_id)
            if row is None:
                await db.execute(
                    "INSERT INTO profiles (user_id, username, name, items) VALUES (?, ?, ?, ?)",
                    (user_id, username, name, json.dumps(DEFAULT_ITEMS, ensure_ascii=False)),
                )
            else:
                await db.execute(
                    "UPDATE profiles SET username = ?, name = ? WHERE user_id = ?",
                    (username, name, user_id),
                )
            await db.commit()
            row = await self._fetch_profile_row(db, user_id)
            return self._row_to_profile(row)

    async def get_profile(self, user_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await self._fetch_profile_row(db, user_id)
            return self._row_to_profile(row) if row else None

    async def buy_item(self, user_id: int, item_key: str) -> tuple[bool, str]:
        item = ITEMS[item_key]
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await self._fetch_profile_row(db, user_id)
            if not row:
                return False, "Сначала напиши /start боту в ЛС."
            p = self._row_to_profile(row)
            if p["money"] < item["money"] or p["gems"] < item["gems"]:
                return False, "Не хватает валюты."
            items = p["items"]
            items[item_key] = items.get(item_key, 0) + 1
            await db.execute(
                "UPDATE profiles SET money = ?, gems = ?, items = ? WHERE user_id = ?",
                (p["money"] - item["money"], p["gems"] - item["gems"], json.dumps(items, ensure_ascii=False), user_id),
            )
            await db.commit()
            return True, f"Куплено: {item['emoji']} {item['name']}"

    async def consume_item(self, user_id: int, item_key: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT items FROM profiles WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                return False
            items = json.loads(row["items"] or "{}")
            if items.get(item_key, 0) <= 0:
                return False
            items[item_key] -= 1
            await db.execute("UPDATE profiles SET items = ? WHERE user_id = ?", (json.dumps(items, ensure_ascii=False), user_id))
            await db.commit()
            return True

    async def reward(self, user_id: int, win: bool, money: int, gems: int, xp: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await self._fetch_profile_row(db, user_id)
            if not row:
                return None
            p = self._row_to_profile(row)
            new_xp = p["xp"] + xp
            new_level = p["level"]
            while new_xp >= new_level * 100:
                new_xp -= new_level * 100
                new_level += 1
                money += 80
            await db.execute(
                """
                UPDATE profiles
                SET money = ?, gems = ?, xp = ?, level = ?, games = games + 1, wins = wins + ?
                WHERE user_id = ?
                """,
                (p["money"] + money, p["gems"] + gems, new_xp, new_level, 1 if win else 0, user_id),
            )
            await db.commit()
            return {"money": money, "gems": gems, "xp": xp, "level": new_level, "level_up": new_level > p["level"]}

    async def remember_chat_user(self, chat_id: int, user_id: int, name: str, username: str | None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO chat_users (chat_id, user_id, username, name, call_enabled, updated_at)
                VALUES (?, ?, ?, ?, 1, strftime('%s','now'))
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    username = excluded.username,
                    name = excluded.name,
                    updated_at = strftime('%s','now')
                """,
                (chat_id, user_id, username, name),
            )
            await db.commit()

    async def set_call_enabled(self, chat_id: int, user_id: int, enabled: bool, name: str | None = None, username: str | None = None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO chat_users (chat_id, user_id, username, name, call_enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, strftime('%s','now'))
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, chat_users.username),
                    name = COALESCE(excluded.name, chat_users.name),
                    call_enabled = excluded.call_enabled,
                    updated_at = strftime('%s','now')
                """,
                (chat_id, user_id, username, name or str(user_id), 1 if enabled else 0),
            )
            await db.commit()

    async def get_callable_users(self, chat_id: int, limit: int = 80) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT chat_id, user_id, username, name, call_enabled
                FROM chat_users
                WHERE chat_id = ? AND call_enabled = 1
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (chat_id, limit),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def top_profiles(self, limit: int = 10) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT user_id, username, name, money, gems, xp, level, games, wins
                FROM profiles
                ORDER BY wins DESC, level DESC, xp DESC, games DESC
                LIMIT ?
                """,
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def _fetch_profile_row(self, db: aiosqlite.Connection, user_id: int):
        async with db.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone()

    def _row_to_profile(self, row: aiosqlite.Row) -> dict[str, Any]:
        data = dict(row)
        data["items"] = {**DEFAULT_ITEMS, **json.loads(data.get("items") or "{}")}
        return data
