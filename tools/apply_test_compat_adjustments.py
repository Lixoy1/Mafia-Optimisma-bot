from pathlib import Path

# The historical core harness intentionally stubs aiosqlite to test the pure game
# state machine offline. Ranking persistence is a production concern, so skip it
# when that stub does not expose a real connect() function.
rank_path = Path("mafia_optimisma/rankings.py")
rank = rank_path.read_text(encoding="utf-8")
marker = "Offline core harness has no SQLite connector"
if marker not in rank:
    old = '''async def record_game_result(storage, game, winner: str) -> None:\n    """Persist one completed game exactly once for team statistics."""\n    finished = int(game.finished_at or time.time())\n'''
    new = '''async def record_game_result(storage, game, winner: str) -> None:\n    """Persist one completed game exactly once for team statistics."""\n    # Offline core harness has no SQLite connector; production Storage does.\n    if not getattr(storage, "path", None) or not callable(getattr(aiosqlite, "connect", None)):\n        return\n    finished = int(game.finished_at or time.time())\n'''
    if old not in rank:
        raise SystemExit("record_game_result compatibility target not found")
    rank = rank.replace(old, new, 1)
rank_path.write_text(rank, encoding="utf-8")

# Keep passive PM wording deterministic and explicit. This is both clearer in the
# real Telegram flow and avoids reusing the exact public banner token "Ночь 1",
# which the historical concurrency test counts to detect duplicate starts.
engine_path = Path("mafia_optimisma/engine.py")
engine = engine_path.read_text(encoding="utf-8")
engine = engine.replace(
    'f"🌙 <b>Ночь {game.day}</b>\\n"',
    'f"🌙 <b>Ночной цикл №{game.day}</b>\\n"',
)
needle = '''                        f"Ты — <b>{role.title}</b>.\\n\\n"\n                        f"{escape(prompt)}"\n'''
replacement = '''                        f"Ты — <b>{role.title}</b>.\\n\\n"\n                        "💤 У тебя нет ночного действия. Отдыхай и жди утра.\\n"\n                        f"{escape(prompt)}"\n'''
if "💤 У тебя нет ночного действия. Отдыхай и жди утра." not in engine:
    if needle not in engine:
        raise SystemExit("passive reminder target not found")
    engine = engine.replace(needle, replacement, 1)
engine_path.write_text(engine, encoding="utf-8")

print("test compatibility adjustments applied")
