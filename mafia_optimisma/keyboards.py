from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .content import ITEMS, MODES, ROLES
from .models import GameState, PlayerState


def chunk_buttons(buttons: list[InlineKeyboardButton], width: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[i:i + width] for i in range(0, len(buttons), width)]


def join_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Присоединиться", callback_data=f"join:{chat_id}")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="pm:profile"), InlineKeyboardButton(text="🛒 Магазин", callback_data="pm:shop")],
    ])


def mode_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    rows = []
    for key, mode in MODES.items():
        rows.append([InlineKeyboardButton(text=f"{mode['emoji']} {mode['name']}", callback_data=f"mode:{chat_id}:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def players_keyboard(game: GameState, prefix: str, exclude_id: int | None = None, include_self: bool = False, width: int = 2) -> InlineKeyboardMarkup:
    buttons = []
    for p in game.alive_players():
        if not include_self and p.user_id == exclude_id:
            continue
        buttons.append(InlineKeyboardButton(text=p.name[:32], callback_data=f"{prefix}:{game.chat_id}:{p.user_id}"))
    return InlineKeyboardMarkup(inline_keyboard=chunk_buttons(buttons, width))


def night_action_keyboard(game: GameState, player: PlayerState) -> InlineKeyboardMarkup | None:
    role = ROLES[player.role_key or "optimist"]
    action = role.action_type
    rows: list[list[InlineKeyboardButton]] = []
    targets = game.alive_players()

    def target_buttons(action_name: str, include_self: bool = False) -> list[InlineKeyboardButton]:
        res = []
        for t in targets:
            if not include_self and t.user_id == player.user_id:
                continue
            res.append(InlineKeyboardButton(text=t.name[:28], callback_data=f"n:{game.chat_id}:{action_name}:{t.user_id}"))
        return res

    if action in {"mafia_kill_leader", "mafia_kill_backup"}:
        rows = chunk_buttons(target_buttons("mafia_kill"), 2)
    elif action in {"yakuza_kill_leader", "yakuza_kill_backup"}:
        rows = chunk_buttons(target_buttons("yakuza_kill"), 2)
    elif action == "heal":
        rows = chunk_buttons(target_buttons("heal", include_self=True), 2)
    elif action == "check_or_shoot":
        if game.mode != "clans":
            rows.append([InlineKeyboardButton(text="🔎 Проверить", callback_data=f"noop:{game.chat_id}:check")])
            rows += chunk_buttons(target_buttons("check"), 2)
        if game.mode in {"chaos", "virus", "clans"}:
            rows.append([InlineKeyboardButton(text="🔫 Выстрел", callback_data=f"noop:{game.chat_id}:shoot")])
            rows += chunk_buttons(target_buttons("shoot"), 2)
    elif action == "block_and_silence":
        rows = chunk_buttons(target_buttons("block"), 2)
    elif action in {"mafia_role_check", "yakuza_mask", "mafia_mask", "bodyguard", "watch_visitors", "mafia_watch_visitors", "yakuza_watch_visitors", "solo_kill"}:
        rows = chunk_buttons(target_buttons(action), 2)
    elif action == "compare_clans":
        rows = chunk_buttons(target_buttons("report1"), 2)
    elif action == "swap_roles":
        rows = chunk_buttons(target_buttons("swap1", include_self=True), 2)
    else:
        return None

    if action in {"mafia_kill_leader", "mafia_kill_backup", "yakuza_kill_leader", "yakuza_kill_backup", "solo_kill", "check_or_shoot"}:
        rows.append([InlineKeyboardButton(text="☠️ Использовать Чёрную пулю", callback_data=f"item:{game.chat_id}:armor_piercing")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def vote_keyboard(game: GameState, voter_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for p in game.alive_players():
        if p.user_id == voter_id:
            continue
        buttons.append(InlineKeyboardButton(text=p.name[:28], callback_data=f"vote:{game.chat_id}:{p.user_id}"))
    rows = chunk_buttons(buttons, 2)
    rows.append([InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"vote:{game.chat_id}:skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def shop_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for key, item in ITEMS.items():
        price = []
        if item["money"]:
            price.append(f"{item['money']}💵")
        if item["gems"]:
            price.append(f"{item['gems']}💎")
        rows.append([InlineKeyboardButton(text=f"{item['emoji']} {item['name']} — {' + '.join(price)}", callback_data=f"shop:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_settings_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏙 Городской оптимизм", callback_data=f"admin:mode:{chat_id}:classic")],
        [InlineKeyboardButton(text="🌪 Весёлый хаос", callback_data=f"admin:mode:{chat_id}:chaos")],
        [InlineKeyboardButton(text="🧟 Эпидемия улыбок", callback_data=f"admin:mode:{chat_id}:virus")],
        [InlineKeyboardButton(text="🌸 Война улыбчивых кланов", callback_data=f"admin:mode:{chat_id}:clans")],
        [
            InlineKeyboardButton(text="▶️ Запустить", callback_data=f"admin:start:{chat_id}"),
            InlineKeyboardButton(text="⏱ +30 сек", callback_data=f"admin:extend:{chat_id}"),
        ],
        [
            InlineKeyboardButton(text="📣 Созыв", callback_data=f"admin:call:{chat_id}"),
            InlineKeyboardButton(text="👥 Игроки", callback_data=f"admin:players:{chat_id}"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data=f"admin:stats:{chat_id}"),
            InlineKeyboardButton(text="🚫 Отменить", callback_data=f"admin:cancel:{chat_id}"),
        ],
    ])
