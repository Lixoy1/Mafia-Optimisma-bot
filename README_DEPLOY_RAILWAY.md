# Деплой Mafia Optimisma на GitHub + Railway

## Что внутри

Проект запускается как Telegram-бот на `aiogram 3.x` в режиме long polling. Это значит, что отдельный домен и webhook не нужны: Railway просто держит долгоживущий Python-процесс.

## Переменные окружения Railway

Обязательные:

```env
BOT_TOKEN=токен_от_BotFather
DATABASE_PATH=/data/mafia_optimisma.sqlite3
```

Опциональные:

```env
NIGHT_SECONDS=45
DISCUSSION_SECONDS=45
VOTING_SECONDS=45
```

## Railway Volume

Для SQLite обязательно подключи Volume:

- Mount path: `/data`
- DATABASE_PATH: `/data/mafia_optimisma.sqlite3`

Без Volume база может потеряться после redeploy.

## Локальная проверка перед GitHub

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m compileall mafia_optimisma run.py
python run.py
```

## Команды GitHub

```bash
git init
git add .
git commit -m "Initial Mafia Optimisma bot"
git branch -M main
git remote add origin https://github.com/USERNAME/mafia-optimisma-bot.git
git push -u origin main
```

## Railway

1. New Project
2. Deploy from GitHub repo
3. Выбери репозиторий
4. В Variables добавь `BOT_TOKEN` и `DATABASE_PATH`
5. В Settings создай Volume `/data`
6. Deploy
7. Смотри Logs: должно быть `Mafia Optimisma started as @...`

## В Telegram

1. Добавь бота в группу
2. Выдай права администратора:
   - Delete messages
   - Pin messages
   - Send messages
3. У BotFather желательно отключить Privacy Mode через `/setprivacy` → Disable
4. В группе напиши `/start_reg`
