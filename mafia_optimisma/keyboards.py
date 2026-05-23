from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .content import ITEMS, MODES, ROLES
from .models import GameState, PlayerState


def chunk_buttons(buttons: list[InlineKeyboardButton], width: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[i:i + width] for i in range(0, len(buttons), width)]


def join_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Присоединиться к игре", callback_data=f"join:{chat_id}")
    ]])


def open_bot_keyboard(bot_username: str | None) -> InlineKeyboardMarkup | None:
    if not bot_username:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🤖 Перейти в бота", url=f"https://t.me/{bot_username}")
    ]])


def night_action_keyboard(game: GameState, player: PlayerState) -> InlineKeyboardMarkup | None:
    role = ROLES.get(player.role_key or "optimist")
    if not role or not role.has_night_action:
        return None

    action = role.action_type
    targets = game.alive_players()

    def btn(text: str, action_name: str, target_id: int) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"n:{game.chat_id}:{action_name}:{target_id}")

    rows = []

    if action in {"mafia_kill_leader", "mafia_kill_backup"}:
        rows = chunk_buttons([btn(f"🔪 {t.name[:22]}", "mafia_kill", t.user_id) for t in targets], 2)
    elif action in {"yakuza_kill_leader", "yakuza_kill_backup"}:
        rows = chunk_buttons([btn(f"🎴 {t.name[:22]}", "yakuza_kill", t.user_id) for t in targets], 2)
    elif action == "heal":
        buttons = [btn(f"🩺 {t.name[:22]}", "heal", t.user_id) for t in targets 
                   if not (t.user_id == player.user_id and player.self_heals_used >= 1)]
        rows = chunk_buttons(buttons, 2)
    elif action == "check_or_shoot":
        if game.mode != "clans":
            check_btns = [btn(f"🔎 {t.name[:22]}", "check", t.user_id) for t in targets if t.user_id not in player.checked_ids]
            if check_btns:
                rows.append([InlineKeyboardButton(text="🔎 Проверить игрока", callback_data=f"noop:{game.chat_id}:check")])
                rows += chunk_buttons(check_btns, 1)
        if game.mode in {"chaos", "virus", "clans"}:
            shoot_btns = [btn(f"🔫 {t.name[:22]}", "shoot", t.user_id) for t in targets]
            if shoot_btns:
                rows.append([InlineKeyboardButton(text="🔫 Убить игрока", callback_data=f"noop:{game.chat_id}:shoot")])
                rows += chunk_buttons(shoot_btns, 1)
    elif action == "block_and_silence":
        rows = chunk_buttons([btn(f"💋 {t.name[:22]}", "block", t.user_id) for t in targets], 2)
    elif action == "swap_roles":
        rows = chunk_buttons([btn(f"🃏 {t.name[:22]}", "swap1", t.user_id) for t in targets], 2)

    if action in {"mafia_kill_leader", "mafia_kill_backup", "yakuza_kill_leader", "yakuza_kill_backup", "solo_kill", "check_or_shoot"}:
        rows.append([InlineKeyboardButton(text="☠️ Использовать Чёрную пулю", callback_data=f"item:{game.chat_id}:armor_piercing")])

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def vote_keyboard(game: GameState, voter_id: int) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=p.name[:28], callback_data=f"vote:{game.chat_id}:{p.user_id}") 
               for p in game.alive_players() if p.user_id != voter_id]
    rows = chunk_buttons(buttons, 2)
    rows.append([InlineKeyboardButton(text="⏭ Пропустить голосование", callback_data=f"vote:{game.chat_id}:skip")])
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
