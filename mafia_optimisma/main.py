import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from .config import Settings
from .engine import GameEngine
from .storage import Storage
from . import routers_callbacks, routers_group, routers_private


async def setup_bot_commands(bot: Bot) -> None:
    private_commands = [
        BotCommand(command="start", description="Запустить меню бота"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="profile", description="Профиль, баланс и предметы"),
        BotCommand(command="shop", description="Магазин усилений"),
        BotCommand(command="roles", description="Бестиарий ролей"),
        BotCommand(command="modes", description="Режимы игры"),
    ]
    group_commands = [
        BotCommand(command="game1", description="Городской оптимизм"),
        BotCommand(command="game2", description="Весёлый хаос"),
        BotCommand(command="game3", description="Эпидемия улыбок"),
        BotCommand(command="game4", description="Война улыбчивых кланов"),
        BotCommand(command="extend", description="Продлить регистрацию на 30 секунд"),
        BotCommand(command="start", description="Запустить игру"),
        BotCommand(command="join", description="Присоединиться к игре"),
        BotCommand(command="leave", description="Выйти из регистрации"),
        BotCommand(command="end_game", description="Выйти из текущей игры"),
        BotCommand(command="settings", description="Админ-панель и настройки"),
        BotCommand(command="stats", description="Статистика игроков"),
        BotCommand(command="call", description="Созвать участников чата"),
        BotCommand(command="unreg", description="Не упоминать меня в созыве"),
        BotCommand(command="players", description="Список игроков"),
        BotCommand(command="cancel_reg", description="Отменить регистрацию"),
    ]
    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    settings = Settings.from_env()
    storage = Storage(settings.database_path)
    await storage.init()

    bot = Bot(settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    engine = GameEngine(settings, storage)

    dp.include_router(routers_callbacks.setup(engine))
    dp.include_router(routers_group.setup(engine))
    dp.include_router(routers_private.setup(engine))

    await setup_bot_commands(bot)

    me = await bot.get_me()
    logging.info("🚀 Mafia Optimisma запущен как @%s", me.username)

    try:
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logging.info("👋 Бот остановлен gracefully")
    except Exception as e:
        logging.exception("❌ Критическая ошибка при работе бота")


if __name__ == "__main__":
    asyncio.run(main())
