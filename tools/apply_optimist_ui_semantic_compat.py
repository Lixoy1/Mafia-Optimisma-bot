from pathlib import Path

path = Path("mafia_optimisma/engine.py")
text = path.read_text(encoding="utf-8")
old = '''                    "💥 <b>Последний сюрприз Подрывника</b>\\n"\n                    f"Взрыв зацепил {player_link(bomb_target)} — <b>{role_title(bomb_target.role_key)}</b>"\n'''
new = '''                    "💥 <b>Последний сюрприз Подрывника</b>\\n"\n                    f"Подрывник забрал с собой {player_link(bomb_target)} — <b>{role_title(bomb_target.role_key)}</b>"\n'''
if old in text:
    text = text.replace(old, new, 1)
elif "Подрывник забрал с собой {player_link(bomb_target)}" not in text:
    raise SystemExit("Bomber polished UI target not found")
path.write_text(text, encoding="utf-8")
print("Optimist UI bomber semantic contract preserved")
