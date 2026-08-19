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

# Pure winner preview: lets the morning renderer know whether a new day is real
# without triggering finish_game before the final night-event message is shown.
patch(
    "mafia_optimisma/engine.py",
    '''    async def check_win(self, bot: Bot, game: GameState) -> str | None:\n        if store.get(game.chat_id) is not game and game.phase != Phase.FINISHED:\n            return None''',
    '''    def _detect_winner_state(self, game: GameState) -> str | None:\n        alive = game.alive_players()\n        if not alive:\n            return "draw"\n        teams = Counter(role_team(p.role_key) for p in alive)\n        if game.mode == "virus" and teams.get("infected", 0) == len(alive):\n            return "infected"\n        if len(alive) == 1 and teams.get("maniac", 0) == 1:\n            return "maniac"\n        if teams.get("infected", 0):\n            return None\n        crime_mafia = teams.get("mafia", 0)\n        crime_yakuza = teams.get("yakuza", 0)\n        maniac = teams.get("maniac", 0)\n        if game.mode == "clans":\n            if crime_mafia == 0 and crime_yakuza == 0 and maniac == 0:\n                return "town"\n            if crime_mafia > 0 and crime_yakuza == 0 and crime_mafia >= len(alive) - crime_mafia:\n                return "mafia"\n            if crime_yakuza > 0 and crime_mafia == 0 and crime_yakuza >= len(alive) - crime_yakuza:\n                return "yakuza"\n            return None\n        if crime_mafia == 0 and maniac == 0:\n            return "town"\n        if crime_mafia > 0 and crime_mafia >= len(alive) - crime_mafia:\n            return "mafia"\n        return None\n\n    async def check_win(self, bot: Bot, game: GameState) -> str | None:\n        if store.get(game.chat_id) is not game and game.phase != Phase.FINISHED:\n            return None''',
    "pure winner preview",
)

# For a continuing game keep the familiar reference order:
# Day -> night result/death -> promotion -> living summary.
# For a winning night: night result -> victory, with NO fake new Day card.
patch(
    "mafia_optimisma/engine.py",
    '''            deaths, public_events = await self.resolve_night(bot, game)\n\n            # First publish only the events that actually happened during the night.\n            # Do NOT announce a new day yet: the final kill may already have created\n            # mafia parity / a town victory / a solo victory.\n            if public_events:\n                await self._safe_group(bot, game.chat_id, "\\n".join(public_events))\n            if deaths:\n                for p, reason in deaths:\n                    if not any(p.name in event for event in public_events):\n                        await self._safe_group(\n                            bot,\n                            game.chat_id,\n                            pick(GLOBAL["night_death"], name=player_link(p), role=role_title(p.role_key))\n                            + (f"\\n_{reason}_" if reason else ""),\n                        )\n            else:\n                await self._safe_group(bot, game.chat_id, pick(GLOBAL["no_deaths"]))\n\n            # Critical live-UX rule: if the night decided the game, finish RIGHT\n            # HERE. There must be no bogus «День 2», morning sticker or living list\n            # after the winning attack.\n            winner = await self.check_win(bot, game)\n            if winner:\n                return\n\n            # Only a genuinely continuing game gets a new day and last-word window.\n            promotions = self._inherit_roles(game)\n            discussion_seconds = self._duration(\n                game, "discussion_seconds", self.settings.discussion_seconds\n            )\n            await self._set_phase(game, Phase.DISCUSSION, discussion_seconds)\n            await send_phase_sticker(bot, game.chat_id, "morning")\n            await self._safe_group(\n                bot,\n                game.chat_id,\n                f"🏙 <b>День {game.day}, город просыпается.</b>\\n"\n                f"До начала голосования {discussion_seconds} секунд.",\n            )\n            for p, _reason in deaths:\n                game.pending_last_words.add(p.user_id)\n                await self._safe_pm(bot, p.user_id, pick(GLOBAL["last_word_prompt"]))''',
    '''            deaths, public_events = await self.resolve_night(bot, game)\n\n            winner_preview = self._detect_winner_state(game)\n            if winner_preview:\n                # The final night is rendered without inventing a morning that the\n                # city never reached. Show what happened, then the final screen.\n                if public_events:\n                    await self._safe_group(bot, game.chat_id, "\\n".join(public_events))\n                if deaths:\n                    for p, reason in deaths:\n                        if not any(p.name in event for event in public_events):\n                            await self._safe_group(\n                                bot,\n                                game.chat_id,\n                                pick(GLOBAL["night_death"], name=player_link(p), role=role_title(p.role_key))\n                                + (f"\\n_{reason}_" if reason else ""),\n                            )\n                else:\n                    await self._safe_group(bot, game.chat_id, pick(GLOBAL["no_deaths"]))\n                await self.check_win(bot, game)\n                return\n\n            # A continuing game gets the normal morning sequence.\n            promotions = self._inherit_roles(game)\n            discussion_seconds = self._duration(\n                game, "discussion_seconds", self.settings.discussion_seconds\n            )\n            await self._set_phase(game, Phase.DISCUSSION, discussion_seconds)\n            await send_phase_sticker(bot, game.chat_id, "morning")\n            await self._safe_group(\n                bot,\n                game.chat_id,\n                f"🏙 <b>День {game.day}, город просыпается.</b>\\n"\n                f"До начала голосования {discussion_seconds} секунд.",\n            )\n            if public_events:\n                await self._safe_group(bot, game.chat_id, "\\n".join(public_events))\n            if deaths:\n                for p, reason in deaths:\n                    if not any(p.name in event for event in public_events):\n                        await self._safe_group(\n                            bot,\n                            game.chat_id,\n                            pick(GLOBAL["night_death"], name=player_link(p), role=role_title(p.role_key))\n                            + (f"\\n_{reason}_" if reason else ""),\n                        )\n                    game.pending_last_words.add(p.user_id)\n                    await self._safe_pm(bot, p.user_id, pick(GLOBAL["last_word_prompt"]))\n            else:\n                await self._safe_group(bot, game.chat_id, pick(GLOBAL["no_deaths"]))''',
    "conditional morning ordering",
)

print("ROUND SETTINGS COMPAT APPLIED")
