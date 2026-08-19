from pathlib import Path

path = Path("mafia_optimisma/routers_private.py")
text = path.read_text(encoding="utf-8")
old = '"Место закреплено. Возвращайся в город — роль придёт сюда после старта 😎"'
new = '"Место закреплено. Возвращайся в город — роль придёт сюда после старта 😎\\n"\n                    "Повторно нажимать «Присоединиться» не нужно."'
if new not in text:
    if old not in text:
        raise RuntimeError("activation text marker not found")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("PLAYER EXPERIENCE TEXT FIX APPLIED")
