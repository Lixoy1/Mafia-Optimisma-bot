import os
import tempfile
import unittest
from datetime import datetime, timezone

import aiosqlite

from mafia_optimisma.rankings import (
    WEEKLY_MIN_WINS,
    award_previous_week,
    current_week_bounds,
    full_statistics,
    init_rankings,
    previous_week_bounds,
    record_game_result,
    render_full_statistics,
    render_weekly_awards,
)
from mafia_optimisma.storage import Storage


class RankingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "rankings.sqlite3")
        self.storage = Storage(self.path)
        await self.storage.init()
        await init_rankings(self.storage)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _profile(self, user_id: int, name: str):
        await self.storage.ensure_profile(user_id, name, None)

    async def _reward_rows(self, user_id: int, games: int, wins: int, created_at: int):
        async with aiosqlite.connect(self.path) as db:
            for i in range(games):
                await db.execute(
                    """
                    INSERT INTO game_rewards
                        (session_id, user_id, win, money, gems, xp, level, level_up, created_at)
                    VALUES (?, ?, ?, 0, 0, 0, 1, 0, ?)
                    """,
                    (f"u{user_id}-g{i}", user_id, 1 if i < wins else 0, created_at),
                )
            await db.commit()

    async def test_week_is_monday_to_sunday_moscow_time(self):
        now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
        _, _, start, end = previous_week_bounds(now)
        self.assertEqual(start.strftime("%d.%m.%Y"), "10.08.2026")
        self.assertEqual((end - __import__('datetime').timedelta(days=1)).strftime("%d.%m.%Y"), "16.08.2026")
        self.assertEqual(start.weekday(), 0)

    async def test_dense_top3_ties_split_exact_prize_pools_once(self):
        start_ts, end_ts, start, end = previous_week_bounds()
        stamp = start_ts + 3600
        specs = [
            (1, "Lilya", 33, 18),
            (2, "Elena", 40, 18),
            (3, "Kira", 46, 15),
            (4, "Wwwww", 27, 12),
            (5, "Ludmila", 20, 12),
            (6, "Below threshold", 12, WEEKLY_MIN_WINS - 1),
        ]
        for user_id, name, games, wins in specs:
            await self._profile(user_id, name)
            await self._reward_rows(user_id, games, wins, stamp)

        rows, rstart, rend, applied = await award_previous_week(self.storage)
        self.assertTrue(applied)
        by_user = {row["user_id"]: row for row in rows}
        self.assertEqual(by_user[1]["rank"], 1)
        self.assertEqual(by_user[2]["rank"], 1)
        self.assertEqual(by_user[1]["money"] + by_user[2]["money"], 500)
        self.assertEqual(by_user[3]["rank"], 2)
        self.assertEqual(by_user[3]["money"], 300)
        self.assertEqual(by_user[4]["rank"], 3)
        self.assertEqual(by_user[5]["rank"], 3)
        self.assertEqual(by_user[4]["money"] + by_user[5]["money"], 200)
        self.assertNotIn(6, by_user)

        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT user_id, money FROM profiles WHERE user_id IN (1,2,3,4,5) ORDER BY user_id") as cur:
                money_before = dict(await cur.fetchall())
        self.assertEqual(money_before[1], 350)  # initial 100 + 250
        self.assertEqual(money_before[2], 350)
        self.assertEqual(money_before[3], 400)
        self.assertEqual(money_before[4], 200)
        self.assertEqual(money_before[5], 200)

        rows2, _, _, applied2 = await award_previous_week(self.storage)
        self.assertFalse(applied2)
        self.assertEqual(len(rows2), 5)
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT user_id, money FROM profiles WHERE user_id IN (1,2,3,4,5) ORDER BY user_id") as cur:
                money_after = dict(await cur.fetchall())
        self.assertEqual(money_before, money_after)

        text = render_weekly_awards(rows, rstart, rend)
        self.assertIn("ТОП-3", text)
        self.assertIn("18 побед", text)
        self.assertIn("делят 500", text)
        self.assertIn("#статистика", text)

    async def test_record_game_result_is_idempotent_and_full_stats_are_clear(self):
        for uid, name in ((11, "A"), (12, "B")):
            await self._profile(uid, name)
        # Profiles hold historical player totals.
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE profiles SET games=10, wins=6 WHERE user_id=11")
            await db.execute("UPDATE profiles SET games=20, wins=9 WHERE user_id=12")
            await db.commit()

        class Game:
            session_id = "result-1"
            chat_id = -1001
            started_at = 100
            finished_at = 200
            players = {11: object(), 12: object()}

        game = Game()
        await record_game_result(self.storage, game, "town")
        await record_game_result(self.storage, game, "town")

        class Game2:
            session_id = "result-2"
            chat_id = -1001
            started_at = 300
            finished_at = 400
            players = {11: object(), 12: object()}

        await record_game_result(self.storage, Game2(), "mafia")
        top, counts, total = await full_statistics(self.storage, 10)
        self.assertEqual(total, 2)
        self.assertEqual(counts["town"], 1)
        self.assertEqual(counts["mafia"], 1)
        self.assertEqual(top[0]["user_id"], 12)
        rendered = render_full_statistics(top, counts, total)
        self.assertIn("Полная статистика", rendered)
        self.assertIn("За это время сыграно", rendered)
        self.assertIn("Мафия", rendered)
        self.assertIn("Мирные жители", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
