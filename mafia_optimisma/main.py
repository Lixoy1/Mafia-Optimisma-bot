from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommand, BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats,
)

from .config import Settings
from .engine import GameEngine
from .rankings import init_rankings, weekly_award_loop
from .storage import Storage
from . import routers_callbacks, routers_group, routers_private


async def setup_bot_commands(bot: Bot) -> None:
    """Telegram slash-command menus for private chats and groups."""
    private_commands = [
        BotCommand(command="start", description="Запустить меню бота"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="profile", description="Профиль, баланс и предметы"),
        BotCommand(command="shop", description="Магазин усилений"),
        BotCommand(command="roles", description="Бестиарий ролей"),
        BotCommand(command="modes", description="Режимы игры"),
        BotCommand(command="mystats", description="Моя статистика"),
        BotCommand(command="week", description="Рейтинг текущей недели"),
        BotCommand(command="stats", description="Полная статистика"),
    ]
    group_commands = [
        BotCommand(command="game", description="Городской оптимизм"),
        BotCommand(command="game2", description="Весёлый хаос"),
        BotCommand(command="game3", description="Эпидемия улыбок"),
        BotCommand(command="game4", description="Война улыбчивых кланов"),
        BotCommand(command="extend", description="Продлить регистрацию на 30 секунд"),
        BotCommand(command="start", description="Закончить регистрацию и начать"),
        BotCommand(command="leave", description="Выйти из регистрации"),
        BotCommand(command="players", description="Игроки и живые роли"),
        BotCommand(command="week", description="Рейтинг недели"),
        BotCommand(command="roles", description="Описание ролей"),
        BotCommand(command="stats", description="Статистика игроков"),
        BotCommand(command="mystats", description="Моя статистика"),
        BotCommand(command="notify", description="Уведомления о новых играх"),
        BotCommand(command="gogame", description="Позвать последних игроков"),
    ]
    admin_commands = group_commands + [
        BotCommand(command="settings", description="Настройки игры"),
        BotCommand(command="set_mode", description="Сменить режим регистрации"),
        BotCommand(command="stop", description="Отменить регистрацию"),
        BotCommand(command="admin_notify", description="Админский созыв игроков"),
    ]
    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
    await bot.set_my_commands(admin_commands, scope=BotCommandScopeAllChatAdministrators())


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    storage = Storage(settings.database_path)
    await storage.init()
    await init_rankings(storage)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    # The project uses long polling. Remove a webhook left by an older hosting setup,
    # otherwise Telegram will reject getUpdates/polling.
    await bot.delete_webhook(drop_pending_updates=False)
    dp = Dispatcher()
    engine = GameEngine(settings, storage)

    dp.include_router(routers_callbacks.setup(engine))
    dp.include_router(routers_group.setup(engine))
    dp.include_router(routers_private.setup(engine))

    await setup_bot_commands(bot)
    restored = await engine.restore_active_games(bot)
    me = await bot.get_me()
    logging.info("Mafia Optimisma started as @%s; restored games=%s", me.username, restored)
    weekly_task = asyncio.create_task(weekly_award_loop(bot, storage))
    try:
        await dp.start_polling(bot)
    finally:
        weekly_task.cancel()
        try:
            await weekly_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
