# Mafia Optimisma — Railway quick start

Исправленная версия для Railway.

## Что уже исправлено

- `Dockerfile` использует `python:3.11-slim`, поэтому `StrEnum` работает.
- `requirements.txt` содержит только реальные pip-зависимости:
  - `aiogram`
  - `python-dotenv`
  - `aiosqlite`
- `run.py` запускает async main через `asyncio.run(main())`.
- В `content.py` добавлены твои Telegram sticker `file_id`:
  - ночь
  - утро
  - голосование
  - победа мирных
  - победа мафии

## Railway Variables

Для тестов без Volume:

```env
BOT_TOKEN=твой_токен_бота
DATABASE_PATH=mafia_optimisma.sqlite3
NIGHT_SECONDS=45
DISCUSSION_SECONDS=45
VOTING_SECONDS=45
```

Если позже появится Railway Volume, поставь:

```env
DATABASE_PATH=/data/mafia_optimisma.sqlite3
```

и подключи Volume с mount path `/data`.

## Команды после деплоя

В группе:

```text
/start_reg
/join
/players
/start_game
```

Режимы:

```text
/game1 — Городской оптимизм
/game2 — Весёлый хаос
/game3 — Эпидемия улыбок
/game4 — Война улыбчивых кланов
```

## Важно

Каждый игрок должен открыть ЛС с ботом и нажать `/start`, иначе Telegram не даст боту отправить ему роль и кнопки.
