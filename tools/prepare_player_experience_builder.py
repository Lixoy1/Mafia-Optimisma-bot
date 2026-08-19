from pathlib import Path

path = Path("tools/apply_player_experience_upgrade.py")
text = path.read_text(encoding="utf-8")

# The builder writes Python source code. Replacement blocks containing "\\n"
# must be raw literals inside the builder so those escapes reach the generated
# source instead of becoming physical line breaks inside quoted strings.
markers = [
    "    '''@router.message(Command(\"start\"), F.chat.type == \"private\")",
    "    '''    if callback.data == \"pm:profile\":",
    "    '''    async def _offer_last_word(self, bot: Bot, game: GameState, player: PlayerState) -> None:",
    "    '''            blocker_key = action_role_key(a)",
    "    '''        # A block also silences for the day unless a real, non-blocked heal reached the target.",
    "    '''        reward_enabled = len(game.players) >= self.settings.min_reward_players",
]
for marker in markers:
    text = text.replace(marker, marker.replace("'''", "r'''", 1))

# Preserve the useful onboarding promise from the previous release: once START
# confirms the reserved place, the player never has to return and press JOIN a
# second time for that registration.
text = text.replace(
    '"Место закреплено. Возвращайся в город — роль придёт сюда после старта 😎"',
    '"Место закреплено. Возвращайся в город — роль придёт сюда после старта 😎\\n"\n                    "Повторно нажимать «Присоединиться» не нужно."',
)

path.write_text(text, encoding="utf-8")
print("PLAYER EXPERIENCE BUILDER PREPARED")
