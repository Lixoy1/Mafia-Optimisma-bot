"""Exercise Storage SQL against real sqlite3 without external aiosqlite.

The production package is aiosqlite. The CI/container used for this rebuild does
not have it installed, so this tiny compatibility layer executes the same SQL on
Python's standard sqlite3 driver and exposes only the async surface Storage uses.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CursorProxy:
    def __init__(self, cursor: sqlite3.Cursor):
        self.cursor = cursor

    def __await__(self):
        async def done():
            return self
        return done().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.cursor.close()

    async def fetchone(self):
        return self.cursor.fetchone()

    async def fetchall(self):
        return self.cursor.fetchall()


class ConnectionProxy:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)

    @property
    def row_factory(self):
        return self.conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self.conn.row_factory = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commit()
        self.conn.close()

    def execute(self, sql, params=()):
        return CursorProxy(self.conn.execute(sql, params))

    async def commit(self):
        self.conn.commit()


aiosqlite = types.ModuleType("aiosqlite")
aiosqlite.Row = sqlite3.Row
aiosqlite.Connection = ConnectionProxy
aiosqlite.connect = lambda path: ConnectionProxy(path)
sys.modules["aiosqlite"] = aiosqlite

from mafia_optimisma.models import GameState, Phase, PlayerState
from mafia_optimisma.storage import Storage


async def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "mafia.sqlite3")
        storage = Storage(path)

        # init must be safe on a new and already-migrated DB.
        await storage.init()
        await storage.init()

        profile = await storage.ensure_profile(101, "Optimist", "optimist")
        assert profile["money"] == 100
        assert profile["items"]["night_shield"] == 0

        ok, _ = await storage.buy_item(101, "night_shield")
        assert ok
        profile = await storage.get_profile(101)
        assert profile["money"] == 0
        assert profile["items"]["night_shield"] == 1

        # Game-event consumption must be idempotent across resolver retries and
        # container restarts: the same event reports the original result but
        # decrements inventory only once.
        assert await storage.consume_item_once("sess-a", 1, 101, "night_shield", "attack:202") is True
        assert await storage.consume_item_once("sess-a", 1, 101, "night_shield", "attack:202") is True
        profile = await storage.get_profile(101)
        assert profile["items"]["night_shield"] == 0
        # A genuinely different event has no item left and records that fact.
        assert await storage.consume_item_once("sess-a", 1, 101, "night_shield", "attack:303") is False
        assert await storage.consume_item_once("sess-a", 1, 101, "night_shield", "attack:303") is False

        await storage.remember_chat_user(-1001, 101, "Optimist", "optimist")
        enabled = await storage.toggle_notify(-1001, 101, "Optimist", "optimist")
        assert enabled is True
        notify = await storage.get_notify_users(-1001)
        assert [row["user_id"] for row in notify] == [101]
        enabled = await storage.toggle_notify(-1001, 101, "Optimist", "optimist")
        assert enabled is False

        game = GameState(-1001, "Test Group", mode="classic", phase=Phase.VERDICT, day=2)
        game.players = {
            101: PlayerState(101, "Optimist", number=1, role_key="carleone", initial_role_key="carleone"),
            202: PlayerState(202, "Lena", number=2, role_key="surgeon", initial_role_key="surgeon"),
        }
        game.nominated_id = 202
        game.verdict_votes = {101: True}
        game.phase_deadline = 12345.0
        game.armor_piercing_pending = {101}
        await storage.save_game_state(game)

        states = await storage.load_game_states()
        assert len(states) == 1
        restored = GameState.from_dict(states[0])
        assert restored.chat_id == -1001
        assert restored.phase == Phase.VERDICT
        assert restored.nominated_id == 202
        assert restored.verdict_votes == {101: True}
        assert restored.players[101].number == 1
        assert restored.armor_piercing_pending == {101}

        await storage.delete_game_state(-1001)
        assert await storage.load_game_states() == []

        reward = await storage.reward_once("reward-sess", 101, True, 20, 0, 20)
        assert reward is not None and reward["already_applied"] is False
        replay = await storage.reward_once("reward-sess", 101, True, 20, 0, 20)
        assert replay is not None and replay["already_applied"] is True
        profile = await storage.get_profile(101)
        assert profile["games"] == 1
        assert profile["wins"] == 1
        assert profile["money"] == 20

        # Inspect schema directly to ensure the persistent-game and notification
        # columns exist after the migration path.
        conn = sqlite3.connect(path)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"profiles", "chat_users", "game_sessions", "item_events", "game_rewards"}.issubset(tables)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_users)")}
            assert "notify_enabled" in cols
            events = conn.execute(
                "SELECT event_key, consumed FROM item_events WHERE session_id = ? ORDER BY event_key",
                ("sess-a",),
            ).fetchall()
            assert events == [("attack:202", 1), ("attack:303", 0)]
            reward_rows = conn.execute(
                "SELECT user_id, win, money FROM game_rewards WHERE session_id = ?",
                ("reward-sess",),
            ).fetchall()
            assert reward_rows == [(101, 1, 20)]
        finally:
            conn.close()

        await storage.delete_item_events("sess-a")
        conn = sqlite3.connect(path)
        try:
            assert conn.execute(
                "SELECT COUNT(*) FROM item_events WHERE session_id = ?", ("sess-a",)
            ).fetchone()[0] == 0
        finally:
            conn.close()

    print("SQLITE STORAGE OK")


if __name__ == "__main__":
    asyncio.run(main())
