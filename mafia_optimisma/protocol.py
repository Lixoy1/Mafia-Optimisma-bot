"""Compact callback protocol tokens.

Telegram limits callback_data to 64 bytes. Role/action names remain descriptive in
Python; only the wire token is compact. Decoder intentionally accepts an unknown
full token unchanged for backward compatibility with buttons from older builds.
"""
ACTION_CODES = {
    "mafia_kill": "mk",
    "yakuza_kill": "yk",
    "heal": "h",
    "check": "c",
    "shoot": "s",
    "block_and_silence": "bs",
    "mafia_role_check": "mc",
    "yakuza_mask": "ym",
    "mafia_mask": "mm",
    "bodyguard": "bg",
    "watch_visitors": "wv",
    "mafia_watch_visitors": "mw",
    "yakuza_watch_visitors": "yw",
    "solo_kill": "sk",
    "report1": "r1",
    "report2": "r2",
    "swap1": "j1",
    "swap2": "j2",
}
CODE_ACTIONS = {value: key for key, value in ACTION_CODES.items()}


def encode_action(action: str) -> str:
    return ACTION_CODES.get(action, action)


def decode_action(token: str) -> str:
    return CODE_ACTIONS.get(token, token)
