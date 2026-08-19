from pathlib import Path

path = Path("mafia_optimisma/routers_private.py")
text = path.read_text(encoding="utf-8")
old = '"Место закреплено. Возвращайся в город — роль придёт сюда после старта 😎"'
new = '"Место закреплено. Возвращайся в город — роль придёт сюда после старта 😎\\n"\n                    "И да: повторно нажимать «Присоединиться» не нужно."'
old_capitalized = '"Место закреплено. Возвращайся в город — роль придёт сюда после старта 😎\\n"\n                    "Повторно нажимать «Присоединиться» не нужно."'
if new not in text:
    if old_capitalized in text:
        text = text.replace(old_capitalized, new, 1)
    elif old in text:
        text = text.replace(old, new, 1)
    else:
        raise RuntimeError("activation text marker not found")
path.write_text(text, encoding="utf-8")
print("PLAYER EXPERIENCE TEXT FIX APPLIED")
