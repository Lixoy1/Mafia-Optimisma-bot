from pathlib import Path

# Keep the historical regression suite intact in the repository; during the UI
# staging run only, update assertions whose *text contract* intentionally changed.
core_path = Path("tests/test_core.py")
core = core_path.read_text(encoding="utf-8")
old = '''        self.assertIn("2) A", text)\n        self.assertIn("5) B", text)\n        self.assertIn("Карлеоне — 1", text)\n        self.assertIn("Хирург — 1", text)\n'''
new = '''        self.assertIn('<b>02</b> · <a href="tg://user?id=1">A</a>', text)\n        self.assertIn('<b>05</b> · <a href="tg://user?id=2">B</a>', text)\n        self.assertIn("Карлеоне  ×1", text)\n        self.assertIn("Хирург  ×1", text)\n'''
if old in core:
    core = core.replace(old, new, 1)
elif 'tg://user?id=1' not in core:
    raise SystemExit("Optimist UI core assertion target not found")
core_path.write_text(core, encoding="utf-8")

fmt_path = Path("tests/test_output_formatting.py")
fmt = fmt_path.read_text(encoding="utf-8")
fmt = fmt.replace(
    "        self.assertIn('<b>Живые игроки:</b>', text)\n",
    "        self.assertIn('<b>Живые игроки</b>', text)\n        self.assertIn('tg://user?id=1', text)\n",
    1,
)
fmt_path.write_text(fmt, encoding="utf-8")

print("Optimist UI legacy assertions updated for staging run")
