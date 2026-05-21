from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from .config import Settings
from .engine import GameEngine
from .storage import Storage
from . import routers_callbacks, routers_group, routers_private


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    storage = Storage(settings.database_path)
    await storage.init()

    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    engine = GameEngine(settings, storage)

    dp.include_router(routers_callbacks.setup(engine))
    dp.include_router(routers_group.setup(engine))
    dp.include_router(routers_private.setup(engine))

    me = await bot.get_me()
    logging.info("Mafia Optimisma started as @%s", me.username)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
