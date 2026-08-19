from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

import aiosqlite

MSK = timezone(timedelta(hours=3))
WEEKLY_MIN_WINS = 10
WEEKLY_PRIZE_POOLS = {1: 500, 2: 300, 3: 200}

WINNER_LABELS = {
    "town": ("🏙", "Мирные жители"),
    "mafia": ("🕴", "Мафия"),
    "maniac": ("🔪", "Маньяк"),
    "infected": ("🧟", "Заражённые"),
    "yakuza": ("🌸", "Клан Сакуры"),
    "suicide": ("🪦", "Фаталист"),
    "draw": ("🤝", "Ничья"),
}


def _pct(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def previous_week_bounds(now: datetime | None = None) -> tuple[int, int, datetime, datetime]:
    local = (now or datetime.now(MSK)).astimezone(MSK)
    current_monday = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = current_monday - timedelta(days=7)
    end = current_monday
    return int(start.timestamp()), int(end.timestamp()), start, end


def current_week_bounds(now: datetime | None = None) -> tuple[int, int, datetime, datetime]:
    local = (now or datetime.now(MSK)).astimezone(MSK)
    start = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(days=7)
    return int(start.timestamp()), int(end.timestamp()), start, end


def week_range_text(start: datetime, end: datetime) -> str:
    last_day = end - timedelta(days=1)
    return f"{start:%d.%m.%Y}–{last_day:%d.%m.%Y}"


async def init_rankings(storage) -> None:
    async with aiosqlite.connect(storage.path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS game_results (
                session_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                winner TEXT NOT NULL,
                player_count INTEGER NOT NULL,
                started_at INTEGER NOT NULL,
                finished_at INTEGER NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_award_runs (
                week_start INTEGER PRIMARY KEY,
                week_end INTEGER NOT NULL,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_awards (
                week_start INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                wins INTEGER NOT NULL,
                games INTEGER NOT NULL,
                money INTEGER NOT NULL,
                awarded_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (week_start, user_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_announcements (
                week_start INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                announced_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (week_start, chat_id)
            )
            """
        )
        await db.commit()


async def record_game_result(storage, game, winner: str) -> None:
    """Persist one completed game exactly once for team statistics."""
    finished = int(game.finished_at or time.time())
    started = int(game.started_at or game.finished_at or time.time())
    async with aiosqlite.connect(storage.path) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO game_results
                (session_id, chat_id, winner, player_count, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                game.session_id,
                int(game.chat_id),
                str(winner),
                len(game.players),
                started,
                finished,
            ),
        )
        await db.commit()


async def _leaderboard(storage, start_ts: int, end_ts: int, limit: int = 100) -> list[dict[str, Any]]:
    async with aiosqlite.connect(storage.path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
                r.user_id,
                COALESCE(p.name, p.username, CAST(r.user_id AS TEXT)) AS name,
                p.username AS username,
                COUNT(*) AS games,
                SUM(CASE WHEN r.win = 1 THEN 1 ELSE 0 END) AS wins
            FROM game_rewards r
            LEFT JOIN profiles p ON p.user_id = r.user_id
            WHERE r.created_at >= ? AND r.created_at < ?
            GROUP BY r.user_id
            ORDER BY wins DESC, games ASC, name COLLATE NOCASE ASC
            LIMIT ?
            """,
            (int(start_ts), int(end_ts), int(limit)),
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        games = int(item.get("games") or 0)
        wins = int(item.get("wins") or 0)
        item["games"] = games
        item["wins"] = wins
        item["win_rate"] = (wins / games * 100.0) if games else 0.0
        result.append(item)
    return result


async def current_week_leaderboard(storage, limit: int = 10) -> tuple[list[dict[str, Any]], datetime, datetime]:
    start_ts, end_ts, start, end = current_week_bounds()
    return await _leaderboard(storage, start_ts, end_ts, limit), start, end


async def previous_week_leaderboard(storage, limit: int = 100) -> tuple[list[dict[str, Any]], datetime, datetime]:
    start_ts, end_ts, start, end = previous_week_bounds()
    return await _leaderboard(storage, start_ts, end_ts, limit), start, end


async def award_previous_week(storage) -> tuple[list[dict[str, Any]], datetime, datetime, bool]:
    """Award dense TOP-3 places atomically. Returns rows, range, newly_applied."""
    start_ts, end_ts, start, end = previous_week_bounds()
    candidates = await _leaderboard(storage, start_ts, end_ts, 500)
    eligible = [row for row in candidates if row["wins"] >= WEEKLY_MIN_WINS]

    # Dense places: 18/18 wins = place 1, 15 wins = place 2, 12 wins = place 3.
    groups: list[list[dict[str, Any]]] = []
    for wins in sorted({row["wins"] for row in eligible}, reverse=True)[:3]:
        groups.append([row for row in eligible if row["wins"] == wins])

    async with aiosqlite.connect(storage.path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT 1 FROM weekly_award_runs WHERE week_start = ?",
            (start_ts,),
        ) as cur:
            already = await cur.fetchone()
        if already is not None:
            await db.commit()
            return await get_week_awards(storage, start_ts), start, end, False

        awarded_rows: list[dict[str, Any]] = []
        for rank, group in enumerate(groups, 1):
            pool = WEEKLY_PRIZE_POOLS[rank]
            ordered = sorted(group, key=lambda x: (str(x.get("name") or "").casefold(), x["user_id"]))
            share, remainder = divmod(pool, len(ordered))
            for index, row in enumerate(ordered):
                money = share + (1 if index < remainder else 0)
                await db.execute(
                    "UPDATE profiles SET money = money + ? WHERE user_id = ?",
                    (money, int(row["user_id"])),
                )
                await db.execute(
                    """
                    INSERT INTO weekly_awards
                        (week_start, user_id, rank, wins, games, money)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        start_ts,
                        int(row["user_id"]),
                        rank,
                        int(row["wins"]),
                        int(row["games"]),
                        money,
                    ),
                )
                awarded_rows.append({**row, "rank": rank, "money": money})

        await db.execute(
            "INSERT INTO weekly_award_runs (week_start, week_end) VALUES (?, ?)",
            (start_ts, end_ts),
        )
        await db.commit()
    return awarded_rows, start, end, True


async def get_week_awards(storage, week_start: int) -> list[dict[str, Any]]:
    async with aiosqlite.connect(storage.path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
                a.user_id, a.rank, a.wins, a.games, a.money,
                COALESCE(p.name, p.username, CAST(a.user_id AS TEXT)) AS name,
                p.username AS username
            FROM weekly_awards a
            LEFT JOIN profiles p ON p.user_id = a.user_id
            WHERE a.week_start = ?
            ORDER BY a.rank ASC, a.wins DESC, name COLLATE NOCASE ASC
            """,
            (int(week_start),),
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        games = int(item.get("games") or 0)
        wins = int(item.get("wins") or 0)
        item["win_rate"] = (wins / games * 100.0) if games else 0.0
        result.append(item)
    return result


def render_weekly_awards(rows: list[dict[str, Any]], start: datetime, end: datetime) -> str:
    lines = [
        "🏆 <b>Итоги недели Mafia Optimisma</b>",
        f"ТОП-3 прошлой недели — от {WEEKLY_MIN_WINS} побед ({week_range_text(start, end)}).",
        "Награды уже зачислены на балансы профилей! 💵",
        "",
    ]
    if not rows:
        lines += [
            f"На этой неделе никто не набрал минимальные {WEEKLY_MIN_WINS} побед.",
            "Следующая неделя уже началась — всё ещё впереди 🙂",
        ]
        return "\n".join(lines)

    by_rank: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_rank[int(row["rank"])].append(row)

    for rank in sorted(by_rank):
        group = by_rank[rank]
        names = ", ".join(
            f"{escape(str(row['name']))} ({_pct(float(row['win_rate']))} %)"
            for row in group
        )
        wins = int(group[0]["wins"])
        pool = WEEKLY_PRIZE_POOLS[rank]
        if len(group) == 1:
            lines.append(
                f"{rank}) {names} — <b>{wins} побед</b>, получает {group[0]['money']} денег 💵"
            )
        else:
            amounts = {int(row["money"]) for row in group}
            if len(amounts) == 1:
                amount = next(iter(amounts))
                lines.append(
                    f"{rank}) {names} — <b>{wins} побед</b>, получают по {amount} денег 💵 "
                    f"(делят {pool} денег 💵 между собой)"
                )
            else:
                split = ", ".join(
                    f"{escape(str(row['name']))} — {row['money']} 💵" for row in group
                )
                lines.append(
                    f"{rank}) {names} — <b>{wins} побед</b>, делят {pool} денег 💵: {split}"
                )
    lines += ["", "#статистика"]
    return "\n".join(lines)


def render_current_week(rows: list[dict[str, Any]], start: datetime, end: datetime) -> str:
    lines = [
        "🏁 <b>Текущий рейтинг недели</b>",
        f"Период: {week_range_text(start, end)}",
        f"Для награды нужно минимум {WEEKLY_MIN_WINS} побед.",
        "",
    ]
    if not rows:
        lines.append("На этой неделе ещё нет завершённых игр.")
        return "\n".join(lines)
    for i, row in enumerate(rows, 1):
        marker = "🏆" if row["wins"] >= WEEKLY_MIN_WINS else "▫️"
        lines.append(
            f"{i}) {marker} {escape(str(row['name']))} "
            f"({_pct(float(row['win_rate']))} %) — {row['wins']} побед / {row['games']} игр"
        )
    return "\n".join(lines)


async def full_statistics(storage, limit: int = 10) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    async with aiosqlite.connect(storage.path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT user_id, username, name, games, wins
            FROM profiles
            WHERE games > 0
            ORDER BY wins DESC, (CASE WHEN games > 0 THEN CAST(wins AS REAL) / games ELSE 0 END) DESC,
                     games DESC, name COLLATE NOCASE ASC
            LIMIT ?
            """,
            (int(limit),),
        ) as cur:
            rows = await cur.fetchall()
        async with db.execute(
            "SELECT winner, COUNT(*) AS amount FROM game_results GROUP BY winner"
        ) as cur:
            winner_rows = await cur.fetchall()
    top = []
    for row in rows:
        item = dict(row)
        games = int(item.get("games") or 0)
        wins = int(item.get("wins") or 0)
        item["win_rate"] = (wins / games * 100.0) if games else 0.0
        top.append(item)
    counts = {str(row["winner"]): int(row["amount"]) for row in winner_rows}
    total = sum(counts.values())
    return top, counts, total


def render_full_statistics(top: list[dict[str, Any]], counts: dict[str, int], total: int) -> str:
    lines = [
        "📊 <b>Полная статистика Mafia Optimisma</b>",
        "<i>Эти данные не участвуют в награждении недельного ТОП-3.</i>",
        "",
    ]
    if top:
        for i, row in enumerate(top, 1):
            lines.append(
                f"{i}) {escape(str(row['name']))} ({_pct(float(row['win_rate']))} %) "
                f"— <b>{row['wins']} побед</b> / {row['games']} игр"
            )
    else:
        lines.append("Пока нет завершённых игр.")

    lines += ["", f"🎮 <b>За это время сыграно:</b> {total} игр"]
    for key in ("mafia", "town", "maniac", "infected", "yakuza", "suicide", "draw"):
        amount = int(counts.get(key, 0))
        if key == "suicide" and amount == 0:
            continue
        emoji, label = WINNER_LABELS[key]
        percent = (amount / total * 100.0) if total else 0.0
        lines.append(f"{emoji} {label} — {amount} ({_pct(percent)} %)")
    return "\n".join(lines)


async def _week_chat_ids(storage, start_ts: int, end_ts: int) -> list[int]:
    async with aiosqlite.connect(storage.path) as db:
        async with db.execute(
            """
            SELECT DISTINCT chat_id FROM game_results
            WHERE finished_at >= ? AND finished_at < ?
            ORDER BY chat_id
            """,
            (int(start_ts), int(end_ts)),
        ) as cur:
            rows = await cur.fetchall()
    return [int(row[0]) for row in rows]


async def announce_previous_week(bot, storage) -> bool:
    rows, start, end, newly_applied = await award_previous_week(storage)
    start_ts, end_ts, _, _ = previous_week_bounds()
    chat_ids = await _week_chat_ids(storage, start_ts, end_ts)
    if not chat_ids:
        return newly_applied
    text = render_weekly_awards(rows, start, end)

    for chat_id in chat_ids:
        async with aiosqlite.connect(storage.path) as db:
            async with db.execute(
                "SELECT 1 FROM weekly_announcements WHERE week_start = ? AND chat_id = ?",
                (start_ts, chat_id),
            ) as cur:
                done = await cur.fetchone()
            if done is not None:
                continue
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            continue
        async with aiosqlite.connect(storage.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO weekly_announcements (week_start, chat_id) VALUES (?, ?)",
                (start_ts, chat_id),
            )
            await db.commit()
    return newly_applied


async def weekly_award_loop(bot, storage, interval_seconds: int = 3600) -> None:
    """Idempotent hourly check; only one award transaction can exist per week."""
    while True:
        try:
            await announce_previous_week(bot, storage)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The next hourly pass retries safely thanks to the DB ledgers.
            pass
        await asyncio.sleep(max(60, int(interval_seconds)))
