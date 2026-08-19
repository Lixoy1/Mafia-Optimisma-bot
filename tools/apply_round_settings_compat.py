from pathlib import Path


def patch(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"{label}: source block not found in {path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# Admin UI only offers sane 15..180 second values, but the engine deliberately
# accepts tiny positive durations for accelerated resilience tests.
patch(
    "mafia_optimisma/engine.py",
    '''    def _duration(self, game: GameState, key: str, fallback: int) -> int:\n        raw = self._game_config(game).get(key, fallback)\n        try:\n            value = int(raw)\n        except (TypeError, ValueError):\n            value = int(fallback)\n        return max(5, min(600, value))''',
    '''    def _duration(self, game: GameState, key: str, fallback: int | float) -> float:\n        raw = self._game_config(game).get(key, fallback)\n        try:\n            value = float(raw)\n        except (TypeError, ValueError):\n            value = float(fallback)\n        return max(0.01, min(600.0, value))''',
    "sub-second test timings",
)

# New storage capabilities are optional for lightweight fake storages used by
# regression tests and third-party integrations. Absence must be quiet, not logged
# as a stack trace on every registration/game start.
patch(
    "mafia_optimisma/engine.py",
    '''        try:\n            game.temp["_chat_settings"] = await self.storage.get_chat_settings(game.chat_id)\n        except Exception:\n            self.log.exception("Could not load chat settings chat=%s", game.chat_id)\n            game.temp["_chat_settings"] = {}''',
    '''        settings_loader = getattr(self.storage, "get_chat_settings", None)\n        if settings_loader is None:\n            game.temp["_chat_settings"] = {}\n        else:\n            try:\n                game.temp["_chat_settings"] = await settings_loader(game.chat_id)\n            except Exception:\n                self.log.exception("Could not load chat settings chat=%s", game.chat_id)\n                game.temp["_chat_settings"] = {}''',
    "optional chat settings storage",
)

patch(
    "mafia_optimisma/engine.py",
    '''            try:\n                last_roles = await self.storage.get_last_roles(\n                    game.chat_id, list(game.players.keys())\n                )\n            except Exception:\n                self.log.exception("Could not load previous roles chat=%s", game.chat_id)\n                last_roles = {}\n            self._assign_start_roles(game, last_roles)\n            await self.persist(game)\n            try:\n                await self.storage.set_last_roles(\n                    game.chat_id,\n                    {p.user_id: (p.role_key or "optimist") for p in game.players.values()},\n                )\n            except Exception:\n                # Variety memory is cosmetic; a DB hiccup must never block a start.\n                self.log.exception("Could not store previous roles chat=%s", game.chat_id)''',
    '''            last_role_loader = getattr(self.storage, "get_last_roles", None)\n            if last_role_loader is None:\n                last_roles = {}\n            else:\n                try:\n                    last_roles = await last_role_loader(\n                        game.chat_id, list(game.players.keys())\n                    )\n                except Exception:\n                    self.log.exception("Could not load previous roles chat=%s", game.chat_id)\n                    last_roles = {}\n            self._assign_start_roles(game, last_roles)\n            await self.persist(game)\n            last_role_writer = getattr(self.storage, "set_last_roles", None)\n            if last_role_writer is not None:\n                try:\n                    await last_role_writer(\n                        game.chat_id,\n                        {p.user_id: (p.role_key or "optimist") for p in game.players.values()},\n                    )\n                except Exception:\n                    # Variety memory is cosmetic; a DB hiccup must never block a start.\n                    self.log.exception("Could not store previous roles chat=%s", game.chat_id)''',
    "optional last role storage",
)

print("ROUND SETTINGS COMPAT APPLIED")
