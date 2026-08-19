from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .content import ITEMS, MODES, ROLES
from .models import GameState, PlayerState
from .protocol import encode_action


def chunk_buttons(buttons: list[InlineKeyboardButton], width: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[i:i + width] for i in range(0, len(buttons), width)]


def join_keyboard(game: GameState) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Присоединиться", callback_data=f"join:{game.session_id}:{game.chat_id}")],
    ])


def open_bot_keyboard(bot_username: str | None) -> InlineKeyboardMarkup | None:
    if not bot_username:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Перейти в бота", url=f"https://t.me/{bot_username}")],
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
        buttons.append(InlineKeyboardButton(
            text=p.name[:32],
            callback_data=f"{prefix}:{game.session_id}:{game.chat_id}:{game.day}:{p.user_id}",
        ))
    return InlineKeyboardMarkup(inline_keyboard=chunk_buttons(buttons, width))


def night_action_keyboard(game: GameState, player: PlayerState) -> InlineKeyboardMarkup | None:
    role = ROLES[player.role_key or "optimist"]
    action = role.action_type
    rows: list[list[InlineKeyboardButton]] = []
    targets = game.alive_players()

    def target_buttons(
        action_name: str,
        include_self: bool = False,
        prefix: str = "",
        exclude_checked: bool = False,
        exclude_swapped: bool = False,
        team_only: str | None = None,
        exclude_team: str | None = None,
    ) -> list[InlineKeyboardButton]:
        res = []
        for t in targets:
            if not include_self and t.user_id == player.user_id:
                continue
            if exclude_checked and t.user_id in player.checked_ids:
                continue
            if exclude_swapped and t.swapped_once:
                continue
            target_team = ROLES[t.role_key or "optimist"].team
            if team_only and target_team != team_only:
                continue
            if exclude_team and target_team == exclude_team:
                continue
            data = f"n:{game.session_id}:{game.chat_id}:{game.day}:{encode_action(action_name)}:{t.user_id}"
            res.append(InlineKeyboardButton(text=f"{prefix}{t.name[:24]}", callback_data=data))
        return res

    if action in {"mafia_kill_leader", "mafia_kill_backup"}:
        # ANARCHY/chaos follows FriendlyFire=ON by default, matching the reference.
        rows = chunk_buttons(target_buttons(
            "mafia_kill", prefix="🔪 ", exclude_team=None if game.mode == "chaos" else "mafia"
        ), 2)
    elif action in {"yakuza_kill_leader", "yakuza_kill_backup"}:
        rows = chunk_buttons(target_buttons("yakuza_kill", prefix="🎴 ", exclude_team="yakuza"), 2)
    elif action == "heal":
        buttons = []
        for t in targets:
            if t.user_id == player.user_id and player.self_heals_used >= 1:
                continue
            data = f"n:{game.session_id}:{game.chat_id}:{game.day}:heal:{t.user_id}"
            buttons.append(InlineKeyboardButton(text=f"🩺 {t.name[:24]}", callback_data=data))
        rows = chunk_buttons(buttons, 2)
    elif action == "check_or_shoot":
        check_buttons = target_buttons("check", prefix="🔎 ", exclude_checked=True)
        if check_buttons:
            rows.append([InlineKeyboardButton(text="🔎 Проверить игрока", callback_data=f"noop:{game.session_id}:check")])
            rows += chunk_buttons(check_buttons, 1)
        else:
            rows.append([InlineKeyboardButton(text="✅ Все доступные игроки уже проверены", callback_data=f"noop:{game.session_id}:checked")])
        # In ordinary classic the Commissioner can shoot from Night 2; in
        # ANARCHY and special modes shooting is available immediately.
        if game.mode in {"chaos", "virus", "clans"} or (game.mode == "classic" and game.day >= 2):
            shoot_buttons = target_buttons("shoot", prefix="🔫 ")
            if shoot_buttons:
                rows.append([InlineKeyboardButton(text="🔫 Убить игрока", callback_data=f"noop:{game.session_id}:shoot")])
                rows += chunk_buttons(shoot_buttons, 1)
    elif action == "block_and_silence":
        # Critical fix: the engine expects block_and_silence, not "block".
        rows = chunk_buttons(target_buttons("block_and_silence", prefix="💋 "), 2)
    elif action in {"mafia_role_check", "yakuza_mask", "mafia_mask", "bodyguard", "watch_visitors", "mafia_watch_visitors", "yakuza_watch_visitors", "solo_kill"}:
        prefix_map = {
            "mafia_role_check": "💻 ",
            "yakuza_mask": "🎭 ",
            "mafia_mask": "⚖️ ",
            "bodyguard": "🛡 ",
            "watch_visitors": "🧥 ",
            "mafia_watch_visitors": "🕶 ",
            "yakuza_watch_visitors": "🥷 ",
            "solo_kill": "🔪 ",
        }
        team_only = "mafia" if action == "mafia_mask" else "yakuza" if action == "yakuza_mask" else None
        rows = chunk_buttons(target_buttons(action, prefix=prefix_map.get(action, ""), team_only=team_only), 2)
    elif action == "compare_clans":
        rows = chunk_buttons(target_buttons("report1", prefix="🗞 "), 2)
    elif action == "swap_roles":
        rows = chunk_buttons(target_buttons("swap1", include_self=True, prefix="🃏 ", exclude_swapped=True), 2)
    else:
        return None

    if not rows:
        return None

    bullet_allowed = action in {
        "mafia_kill_leader", "mafia_kill_backup",
        "yakuza_kill_leader", "yakuza_kill_backup", "solo_kill",
    } or (action == "check_or_shoot" and (game.mode in {"chaos", "virus", "clans"} or (game.mode == "classic" and game.day >= 2)))
    if bullet_allowed:
        rows.append([InlineKeyboardButton(
            text="☠️ Использовать Чёрную пулю",
            callback_data=f"item:{game.session_id}:{game.chat_id}:{game.day}:armor_piercing",
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def vote_keyboard(game: GameState, voter_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for p in game.alive_players():
        if p.user_id == voter_id:
            continue
        buttons.append(InlineKeyboardButton(
            text=p.name[:28],
            callback_data=f"vote:{game.session_id}:{game.chat_id}:{game.day}:{p.user_id}",
        ))
    rows = chunk_buttons(buttons, 2)
    rows.append([InlineKeyboardButton(
        text="⏭ Пропустить",
        callback_data=f"vote:{game.session_id}:{game.chat_id}:{game.day}:skip",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def verdict_keyboard(game: GameState) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="👍 Казнить",
                callback_data=f"verdict:{game.session_id}:{game.chat_id}:{game.day}:yes",
            ),
            InlineKeyboardButton(
                text="👎 Помиловать",
                callback_data=f"verdict:{game.session_id}:{game.chat_id}:{game.day}:no",
            ),
        ],
        [InlineKeyboardButton(
            text="🤍 Воздержаться",
            callback_data=f"verdict:{game.session_id}:{game.chat_id}:{game.day}:abstain",
        )],
    ])


def shop_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for key, item in ITEMS.items():
        if item.get("enabled", True) is False:
            rows.append([InlineKeyboardButton(
                text=f"{item['emoji']} {item['name']} — скоро",
                callback_data=f"noop:shop:{key}",
            )])
            continue
        price = []
        if item["money"]:
            price.append(f"{item['money']}💵")
        if item["gems"]:
            price.append(f"{item['gems']}💎")
        rows.append([InlineKeyboardButton(text=f"{item['emoji']} {item['name']} — {' + '.join(price)}", callback_data=f"shop:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


CONFIGURABLE_ROLE_KEYS = [
    "surgeon", "tracker", "fatalist", "wanderer", "night_diva", "breacher",
    "shield", "bomber", "shadow", "cadet", "lucky", "butcher",
    "mercy_sister", "reporter", "alibi_master", "werewolf", "joker", "carrier",
]


def admin_settings_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎭 Роли", callback_data=f"admin:roles:{chat_id}"),
            InlineKeyboardButton(text="⏱ Тайминги", callback_data=f"admin:timings:{chat_id}"),
        ],
        [
            InlineKeyboardButton(text="🙊 Чат игры", callback_data=f"admin:chat_rules:{chat_id}"),
            InlineKeyboardButton(text="🎮 Режимы игр", callback_data=f"admin:mode_menu:{chat_id}"),
        ],
        [InlineKeyboardButton(text="🛠 Разное", callback_data=f"admin:misc:{chat_id}")],
        [
            InlineKeyboardButton(text="▶️ Запустить", callback_data=f"admin:start:{chat_id}"),
            InlineKeyboardButton(text="⏱ +30 сек", callback_data=f"admin:extend:{chat_id}"),
        ],
        [
            InlineKeyboardButton(text="👥 Игроки", callback_data=f"admin:players:{chat_id}"),
            InlineKeyboardButton(text="📣 Созыв", callback_data=f"admin:call:{chat_id}"),
        ],
        [
            InlineKeyboardButton(text="🏆 Неделя", callback_data=f"admin:weekly:{chat_id}"),
            InlineKeyboardButton(text="📊 Статистика", callback_data=f"admin:stats:{chat_id}"),
        ],
        [InlineKeyboardButton(text="🔄 Обновить панель", callback_data=f"admin:refresh:{chat_id}")],
        [InlineKeyboardButton(text="♻️ Сбросить настройки", callback_data=f"admin:reset:{chat_id}")],
        [InlineKeyboardButton(text="🚫 Отменить регистрацию", callback_data=f"admin:cancel:{chat_id}")],
    ])


def admin_roles_keyboard(chat_id: int, overrides: dict | None = None) -> InlineKeyboardMarkup:
    overrides = overrides or {}
    rows = []
    for key in CONFIGURABLE_ROLE_KEYS:
        role = ROLES.get(key)
        if not role:
            continue
        value = overrides.get(key)
        suffix = ""
        if value is not None:
            try:
                ivalue = int(value)
                suffix = " · выкл" if ivalue <= 0 else f" · с {ivalue}"
            except Exception:
                pass
        rows.append([InlineKeyboardButton(
            text=f"{role.emoji} {role.title}{suffix}",
            callback_data=f"admin:role:{chat_id}:{key}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:refresh:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_role_threshold_keyboard(chat_id: int, role_key: str, selected=None) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(text="↩️ По режиму", callback_data=f"admin:role_set:{chat_id}:{role_key}:default"),
        InlineKeyboardButton(text="⬛ Выключить", callback_data=f"admin:role_set:{chat_id}:{role_key}:off"),
    ]]
    buttons = []
    for value in range(3, 31):
        mark = "✅" if str(selected) == str(value) else "▫️"
        buttons.append(InlineKeyboardButton(
            text=f"{mark} {value}", callback_data=f"admin:role_set:{chat_id}:{role_key}:{value}"
        ))
    rows += chunk_buttons(buttons, 4)
    rows.append([InlineKeyboardButton(text="⬅️ К ролям", callback_data=f"admin:roles:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_timings_keyboard(chat_id: int, values: dict) -> InlineKeyboardMarkup:
    fields = [
        ("registration_seconds", "🎟 Регистрация"),
        ("night_seconds", "🌃 Ночь"),
        ("discussion_seconds", "💬 Обсуждение"),
        ("nomination_seconds", "🗳 Выдвижение"),
        ("verdict_seconds", "⚖️ Вердикт"),
    ]
    rows = []
    for key, label in fields:
        rows.append([InlineKeyboardButton(
            text=f"{label} · {values[key]}с", callback_data=f"admin:time:{chat_id}:{key}"
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:refresh:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_time_values_keyboard(chat_id: int, field: str, selected: int) -> InlineKeyboardMarkup:
    values = [15, 20, 30, 45, 60, 90, 120, 180]
    buttons = [InlineKeyboardButton(
        text=("✅ " if selected == value else "▫️ ") + f"{value} сек",
        callback_data=f"admin:time_set:{chat_id}:{field}:{value}",
    ) for value in values]
    rows = chunk_buttons(buttons, 2)
    rows.append([InlineKeyboardButton(text="⬅️ К таймингам", callback_data=f"admin:timings:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_misc_keyboard(chat_id: int, protect: bool, early: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'✅' if protect else '⬜'} Защищённые ЛС",
            callback_data=f"admin:toggle:{chat_id}:protect_private_content",
        )],
        [InlineKeyboardButton(
            text=f"{'✅' if early else '⬜'} Быстрая ночь",
            callback_data=f"admin:toggle:{chat_id}:early_night_finish",
        )],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:refresh:{chat_id}")],
    ])


def admin_back_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:refresh:{chat_id}")]
    ])


def admin_mode_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    rows = []
    for key, mode in MODES.items():
        rows.append([InlineKeyboardButton(
            text=f"{mode['emoji']} {mode['name']}",
            callback_data=f"admin:mode:{chat_id}:{key}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:refresh:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
