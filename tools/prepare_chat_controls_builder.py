from pathlib import Path

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
print("CHAT CONTROLS BUILDER PREPARED")
