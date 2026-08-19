from pathlib import Path

# Prepare the target callback block by stable semantic markers. This keeps copy
# edits in the admin panel from breaking the migration.
path = Path("mafia_optimisma/routers_callbacks.py")
text = path.read_text(encoding="utf-8")
start = '    if action == "chat_rules":\n'
end = '    if action == "misc":\n'
new = '''    if action == "chat_rules":
        cfg = await engine.storage.get_chat_settings(chat_id)
        await callback.message.edit_text(
            "🙊 <b>Чат во время игры</b>\\n\\n"
            "🔒 Базовые правила всегда активны: ночью город молчит, зрители и выбывшие не вмешиваются, "
            "а заблокированный игрок не говорит и не голосует.\\n\\n"
            "Дополнительная модерация применяется с <b>следующей регистрацией</b> и сохраняется при рестарте бота.\\n"
            "🔢 «№ + имя» меняет подписи кнопок выдвижения кандидата.",
            reply_markup=admin_chat_rules_keyboard(chat_id, cfg),
        )
        await callback.answer()
        return

    if action == "chat_toggle":
        feature = parts[3]
        defaults = {
            "block_profanity": False,
            "block_stickers": False,
            "block_links": False,
            "vote_show_numbers": True,
        }
        if feature not in defaults:
            await callback.answer("Неизвестная настройка чата.", show_alert=True)
            return
        cfg = await engine.storage.get_chat_settings(chat_id)
        new_value = not bool(cfg.get(feature, defaults[feature]))
        await engine.storage.set_chat_setting(chat_id, feature, new_value)
        cfg[feature] = new_value
        await callback.message.edit_text(
            "🙊 <b>Чат во время игры</b>\\n\\n"
            "🔒 Базовые правила всегда активны: ночью город молчит, зрители и выбывшие не вмешиваются, "
            "а заблокированный игрок не говорит и не голосует.\\n\\n"
            "Дополнительная модерация применяется с <b>следующей регистрацией</b> и сохраняется при рестарте бота.\\n"
            "🔢 «№ + имя» меняет подписи кнопок выдвижения кандидата.",
            reply_markup=admin_chat_rules_keyboard(chat_id, cfg),
        )
        await callback.answer("Настройка сохранена для следующей игры.")
        return
'''
if 'if action == "chat_toggle":' not in text:
    i = text.find(start)
    j = text.find(end, i + len(start)) if i >= 0 else -1
    if i < 0 or j < 0:
        raise RuntimeError("chat_rules/misc markers not found")
    text = text[:i] + new + "\n" + text[j:]
    path.write_text(text, encoding="utf-8")

# The second migration also contains some legacy exact-text replacements. Make
# those calls idempotent where the semantic preparer has already installed the
# target behavior, while keeping every unrelated missing marker strict.
builder_path = Path("tools/apply_chat_controls_and_items.py")
if builder_path.exists():
    builder = builder_path.read_text(encoding="utf-8")
    old_helper = '''    if old not in text:\n        raise RuntimeError(f"source block not found in {path}: {old[:160]!r}")\n'''
    new_helper = '''    if old not in text:\n        if (\n            path == "mafia_optimisma/routers_callbacks.py"\n            and 'if action == "chat_toggle":' in text\n            and "admin_chat_rules_keyboard(chat_id, cfg)" in text\n        ):\n            return\n        raise RuntimeError(f"source block not found in {path}: {old[:160]!r}")\n'''
    if new_helper not in builder:
        if old_helper not in builder:
            raise RuntimeError("chat-controls replace_once helper marker not found")
        builder = builder.replace(old_helper, new_helper, 1)

    # Player Experience deliberately generalized the silence message from Diva to
    # any blocking effect. Make the later moderation migration expect that real
    # post-UX wording instead of the old Diva-only copy.
    builder = builder.replace(
        'event, game, "❌ Ночная Дива лишила тебя права говорить до конца дня.",',
        'event, game, "🤐 Ночной эффект лишил тебя права говорить и голосовать до конца дня.",',
    )

    # Avoid false positives such as «хулиган». The common obscene roots are still
    # caught (e.g. «охуенно», «нахуй», etc.) without treating «хули-» as a root.
    builder = builder.replace(
        'ху(?:й|я|е|ё|и|ли)[а-яё]*',
        'ху(?:й|я|е|ё|и)[а-яё]*',
    )
    builder_path.write_text(builder, encoding="utf-8")

print("CHAT CONTROLS BUILDER PREPARED")
