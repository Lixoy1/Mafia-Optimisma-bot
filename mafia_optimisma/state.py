from __future__ import annotations

from dataclasses import dataclass, field

from .models import GameState


@dataclass
class GameStore:
    games: dict[int, GameState] = field(default_factory=dict)
    user_to_chat: dict[int, int] = field(default_factory=dict)

    def get(self, chat_id: int) -> GameState | None:
        return self.games.get(chat_id)

    def create_or_reset(self, chat_id: int, chat_title: str, mode: str = "classic") -> GameState:
        game = GameState(chat_id=chat_id, chat_title=chat_title, mode=mode)
        self.games[chat_id] = game
        return game

    def restore(self, game: GameState) -> None:
        self.games[game.chat_id] = game
        for uid in game.players:
            self.user_to_chat[uid] = game.chat_id

    def remember_user(self, user_id: int, chat_id: int) -> None:
        self.user_to_chat[user_id] = chat_id

    def game_by_user(self, user_id: int) -> GameState | None:
        chat_id = self.user_to_chat.get(user_id)
        return self.games.get(chat_id) if chat_id is not None else None

    def remove_game(self, chat_id: int) -> None:
        game = self.games.pop(chat_id, None)
        if game:
            for uid in list(game.players):
                # Do not wipe a mapping if the user has since been assigned elsewhere.
                if self.user_to_chat.get(uid) == chat_id:
                    self.user_to_chat.pop(uid, None)


store = GameStore()
