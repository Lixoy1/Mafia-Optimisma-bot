import sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

class Magic:
    def __getattr__(self, name): return self
    def __call__(self, *a, **k): return self
    def __eq__(self, other): return self
    def __invert__(self): return self
    def in_(self, *a, **k): return self
    def startswith(self, *a, **k): return self

class Router:
    def __init__(self, *a, **k): pass
    def message(self, *a, **k):
        return lambda fn: fn
    def callback_query(self, *a, **k):
        return lambda fn: fn

aiogram = types.ModuleType('aiogram')
aiogram.Bot = type('Bot', (), {})
aiogram.Dispatcher = type('Dispatcher', (), {})
aiogram.Router = Router
aiogram.F = Magic()
sys.modules['aiogram'] = aiogram

client_default = types.ModuleType('aiogram.client.default')
client_default.DefaultBotProperties = type('DefaultBotProperties', (), {'__init__': lambda self,*a,**k: setattr(self,'__dict__',k)})
sys.modules['aiogram.client.default'] = client_default

enums = types.ModuleType('aiogram.enums')
enums.ParseMode = type('ParseMode', (), {'HTML': 'HTML'})
sys.modules['aiogram.enums'] = enums

filters = types.ModuleType('aiogram.filters')
filters.Command = type('Command', (), {'__init__': lambda self,*a,**k: None})
filters.CommandObject = type('CommandObject', (), {})
sys.modules['aiogram.filters'] = filters

exc = types.ModuleType('aiogram.exceptions')
exc.TelegramForbiddenError = type('TelegramForbiddenError', (Exception,), {})
exc.TelegramBadRequest = type('TelegramBadRequest', (Exception,), {})
sys.modules['aiogram.exceptions'] = exc

t = types.ModuleType('aiogram.types')
for name in ['Message','CallbackQuery','InlineKeyboardButton','InlineKeyboardMarkup','BotCommand','BotCommandScopeAllGroupChats','BotCommandScopeAllPrivateChats']:
    setattr(t, name, type(name, (), {'__init__': lambda self,*a,**k: setattr(self,'__dict__',k)}))
sys.modules['aiogram.types'] = t

sql = types.ModuleType('aiosqlite')
sql.Connection = object
sql.Row = object
sys.modules['aiosqlite'] = sql

dotenv = types.ModuleType('dotenv')
dotenv.load_dotenv = lambda: None
sys.modules['dotenv'] = dotenv

import mafia_optimisma.main
import mafia_optimisma.engine
import mafia_optimisma.routers_callbacks
import mafia_optimisma.routers_group
import mafia_optimisma.routers_private
print('SMOKE IMPORT OK')
