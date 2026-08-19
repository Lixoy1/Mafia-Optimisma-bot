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
        if db_path.parent and str(db_path.parent) != ".":
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
                    items TEXT NOT NULL
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
                    notify_enabled INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    PRIMARY KEY (chat_id, user_id)
                )
                """
            )
            try:
                await db.execute(
                    "ALTER TABLE chat_users ADD COLUMN notify_enabled INTEGER NOT NULL DEFAULT 0"
                )
            except Exception:
                # Column already exists on upgraded databases.
                pass
            try:
                await db.execute("ALTER TABLE chat_users ADD COLUMN last_role TEXT")
            except Exception:
                # Column already exists on upgraded databases.
                pass
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS game_sessions (
                    chat_id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_settings (
                    chat_id INTEGER PRIMARY KEY,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS item_events (
                    session_id TEXT NOT NULL,
                    day INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    item_key TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    consumed INTEGER NOT NULL,
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    PRIMARY KEY (session_id, day, user_id, item_key, event_key)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS game_rewards (
                    session_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    win INTEGER NOT NULL,
                    money INTEGER NOT NULL,
                    gems INTEGER NOT NULL,
                    xp INTEGER NOT NULL,
                    level INTEGER NOT NULL,
                    level_up INTEGER NOT NULL,
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    PRIMARY KEY (session_id, user_id)
                )
                """
            )
            await db.commit()

    async def _fetch_profile_row(self, db: aiosqlite.Connection, user_id: int):
        async with db.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone()

    async def ensure_profile(self, user_id: int, name: str, username: str | None) -> dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await self._fetch_profile_row(db, user_id)
            if row is None:
                await db.execute(
                    "INSERT INTO profiles (user_id, username, name, items) VALUES (?, ?, ?, ?)",
                    (user_id, username, name, json.dumps(DEFAULT_ITEMS, ensure_ascii=False)),
                )
                await db.commit()
                row = await self._fetch_profile_row(db, user_id)
            else:
                await db.execute("UPDATE profiles SET username = ?, name = ? WHERE user_id = ?", (username, name, user_id))
                await db.commit()
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
            await db.execute("BEGIN IMMEDIATE")
            row = await self._fetch_profile_row(db, user_id)
            if not row:
                await db.commit()
                return False, "Сначала напиши /start боту в ЛС."
            p = self._row_to_profile(row)
            if p["money"] < item["money"] or p["gems"] < item["gems"]:
                await db.commit()
                return False, "Не хватает валюты."
            items = p["items"]
            items[item_key] = items.get(item_key, 0) + 1
            await db.execute(
                "UPDATE profiles SET money = ?, gems = ?, items = ? WHERE user_id = ?",
                (p["money"] - item["money"], p["gems"] - item["gems"], json.dumps(items, ensure_ascii=False), user_id),
            )
            await db.commit()
            return True, f"✅ Куплено: {item['emoji']} {item['name']}"

    async def consume_item(self, user_id: int, item_key: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute("SELECT items FROM profiles WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                await db.commit()
                return False
            items = json.loads(row["items"] or "{}")
            if items.get(item_key, 0) <= 0:
                await db.commit()
                return False
            items[item_key] -= 1
            await db.execute("UPDATE profiles SET items = ? WHERE user_id = ?", (json.dumps(items, ensure_ascii=False), user_id))
            await db.commit()
            return True

    async def consume_item_once(
        self, session_id: str, day: int, user_id: int, item_key: str, event_key: str
    ) -> bool:
        """Idempotently consume an item for one concrete game event.

        The ledger row and inventory decrement are committed in the same SQLite
        transaction. If a container restarts after the decrement, replaying the
        same session/day/event returns the recorded result without decrementing
        the inventory a second time.
        """
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                """
                SELECT consumed FROM item_events
                WHERE session_id = ? AND day = ? AND user_id = ?
                  AND item_key = ? AND event_key = ?
                """,
                (session_id, int(day), user_id, item_key, event_key),
            ) as cur:
                previous = await cur.fetchone()
            if previous is not None:
                await db.commit()
                return bool(previous["consumed"])

            async with db.execute("SELECT items FROM profiles WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
            consumed = False
            if row:
                items = json.loads(row["items"] or "{}")
                if items.get(item_key, 0) > 0:
                    items[item_key] -= 1
                    await db.execute(
                        "UPDATE profiles SET items = ? WHERE user_id = ?",
                        (json.dumps(items, ensure_ascii=False), user_id),
                    )
                    consumed = True

            await db.execute(
                """
                INSERT INTO item_events (session_id, day, user_id, item_key, event_key, consumed)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, int(day), user_id, item_key, event_key, 1 if consumed else 0),
            )
            await db.commit()
            return consumed

    async def delete_item_events(self, session_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM item_events WHERE session_id = ?", (session_id,))
            await db.commit()

    async def reward_once(
        self, session_id: str, user_id: int, win: bool, money: int, gems: int, xp: int
    ) -> dict[str, Any] | None:
        """Apply a game reward at most once for this session/user.

        The profile update and reward ledger row are one SQLite transaction. A
        crash after committing cannot duplicate money/XP when FINISHED is replayed.
        """
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT * FROM game_rewards WHERE session_id = ? AND user_id = ?",
                (session_id, user_id),
            ) as cur:
                previous = await cur.fetchone()
            if previous is not None:
                await db.commit()
                return {
                    "money": int(previous["money"]),
                    "gems": int(previous["gems"]),
                    "xp": int(previous["xp"]),
                    "level": int(previous["level"]),
                    "level_up": bool(previous["level_up"]),
                    "already_applied": True,
                }

            row = await self._fetch_profile_row(db, user_id)
            if not row:
                await db.commit()
                return None
            p = self._row_to_profile(row)
            award_money = int(money)
            new_xp = p["xp"] + int(xp)
            new_level = p["level"]
            while new_xp >= new_level * 100:
                new_xp -= new_level * 100
                new_level += 1
                award_money += 80
            level_up = new_level > p["level"]
            await db.execute(
                """
                UPDATE profiles
                SET money = ?, gems = ?, xp = ?, level = ?, games = games + 1, wins = wins + ?
                WHERE user_id = ?
                """,
                (
                    p["money"] + award_money, p["gems"] + int(gems), new_xp, new_level,
                    1 if win else 0, user_id,
                ),
            )
            await db.execute(
                """
                INSERT INTO game_rewards
                    (session_id, user_id, win, money, gems, xp, level, level_up)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, user_id, 1 if win else 0, award_money, int(gems),
                    int(xp), new_level, 1 if level_up else 0,
                ),
            )
            await db.commit()
            return {
                "money": award_money, "gems": int(gems), "xp": int(xp),
                "level": new_level, "level_up": level_up, "already_applied": False,
            }

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

    async def set_notify_enabled(
        self, chat_id: int, user_id: int, enabled: bool,
        name: str | None = None, username: str | None = None,
    ) -> bool:
        value = 1 if enabled else 0
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO chat_users
                    (chat_id, user_id, username, name, call_enabled, notify_enabled, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, strftime('%s','now'))
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, chat_users.username),
                    name = COALESCE(excluded.name, chat_users.name),
                    notify_enabled = excluded.notify_enabled,
                    updated_at = strftime('%s','now')
                """,
                (chat_id, user_id, username, name or str(user_id), value),
            )
            await db.commit()
        return bool(value)

    async def toggle_notify(self, chat_id: int, user_id: int, name: str, username: str | None) -> bool:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT notify_enabled FROM chat_users WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ) as cur:
                row = await cur.fetchone()
            current = bool(row["notify_enabled"]) if row else False
            new_value = 0 if current else 1
            await db.execute(
                """
                INSERT INTO chat_users (chat_id, user_id, username, name, call_enabled, notify_enabled, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, strftime('%s','now'))
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    username = excluded.username,
                    name = excluded.name,
                    notify_enabled = excluded.notify_enabled,
                    updated_at = strftime('%s','now')
                """,
                (chat_id, user_id, username, name, new_value),
            )
            await db.commit()
            return bool(new_value)

    async def get_notify_users(self, chat_id: int, limit: int = 200) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT chat_id, user_id, username, name
                FROM chat_users
                WHERE chat_id = ? AND notify_enabled = 1
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

    async def get_last_roles(self, chat_id: int, user_ids: list[int] | None = None) -> dict[int, str]:
        params: list[object] = [chat_id]
        where = "chat_id = ? AND last_role IS NOT NULL"
        if user_ids:
            marks = ",".join("?" for _ in user_ids)
            where += f" AND user_id IN ({marks})"
            params.extend(int(x) for x in user_ids)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT user_id, last_role FROM chat_users WHERE {where}", tuple(params)
            ) as cur:
                rows = await cur.fetchall()
        return {int(row["user_id"]): str(row["last_role"]) for row in rows if row["last_role"]}

    async def set_last_roles(self, chat_id: int, role_map: dict[int, str]) -> None:
        if not role_map:
            return
        async with aiosqlite.connect(self.path) as db:
            await db.executemany(
                "UPDATE chat_users SET last_role = ?, updated_at = strftime('%s','now') "
                "WHERE chat_id = ? AND user_id = ?",
                [(str(role), chat_id, int(user_id)) for user_id, role in role_map.items()],
            )
            await db.commit()

    async def get_chat_settings(self, chat_id: int) -> dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT settings_json FROM chat_settings WHERE chat_id = ?", (chat_id,)
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return {}
        try:
            data = json.loads(row["settings_json"] or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    async def set_chat_settings(self, chat_id: int, settings: dict[str, Any]) -> None:
        payload = json.dumps(settings or {}, ensure_ascii=False, separators=(",", ":"))
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO chat_settings (chat_id, settings_json, updated_at)
                VALUES (?, ?, strftime('%s','now'))
                ON CONFLICT(chat_id) DO UPDATE SET
                    settings_json = excluded.settings_json,
                    updated_at = strftime('%s','now')
                """,
                (chat_id, payload),
            )
            await db.commit()

    async def set_chat_setting(self, chat_id: int, key: str, value: Any) -> dict[str, Any]:
        settings = await self.get_chat_settings(chat_id)
        if value is None:
            settings.pop(key, None)
        else:
            settings[key] = value
        await self.set_chat_settings(chat_id, settings)
        return settings

    async def reset_chat_settings(self, chat_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM chat_settings WHERE chat_id = ?", (chat_id,))
            await db.commit()

    async def save_game_state(self, game) -> None:
        """Persist one active game snapshot. The model owns JSON serialization."""
        payload = json.dumps(game.to_dict(), ensure_ascii=False, separators=(",", ":"))
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO game_sessions (chat_id, session_id, phase, state_json, updated_at)
                VALUES (?, ?, ?, ?, strftime('%s','now'))
                ON CONFLICT(chat_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    phase = excluded.phase,
                    state_json = excluded.state_json,
                    updated_at = strftime('%s','now')
                """,
                (game.chat_id, game.session_id, game.phase.value, payload),
            )
            await db.commit()

    async def delete_game_state(self, chat_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM game_sessions WHERE chat_id = ?", (chat_id,))
            await db.commit()

    async def load_game_states(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT state_json FROM game_sessions ORDER BY updated_at ASC") as cur:
                rows = await cur.fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                result.append(json.loads(row["state_json"]))
            except Exception:
                # A corrupt stale snapshot must not prevent the bot from booting.
                continue
        return result

    def _row_to_profile(self, row: aiosqlite.Row) -> dict[str, Any]:
        data = dict(row)
        data["items"] = {**DEFAULT_ITEMS, **json.loads(data.get("items") or "{}")}
        return data
