from pathlib import Path

ROOT = Path.cwd()
p = ROOT / "audit" / "regression_audit_fixes.py"
text = p.read_text(encoding="utf-8")
old = '''# 6. Main account outcome is typed and async GETs use offload/coalescing helper.\nimport main\nmain.session.email = "configured@example.invalid"\nmain.session.password = "configured"\nmain.session.is_logged_in = False\nwith patch.object(main.session, "login", return_value=False):\n    outcome = main.sync_account_data()\nassert outcome["success"] is False and outcome["status"] == "auth_failed"\nimport inspect\nassert "await _ensure_account_synced()" in inspect.getsource(main.get_account_summary)\nassert "asyncio.to_thread(sync_account_data)" in inspect.getsource(main._ensure_account_synced)\nprint("PASS audit-fix 6: account sync exposes failure and async GET path offloads")\n'''
new = '''# 6. Main account outcome is typed and account GETs remain read-only.\nimport main\nmain.session.email = "configured@example.invalid"\nmain.session.password = "configured"\nmain.session.is_logged_in = False\nwith patch.object(main.session, "login", return_value=False):\n    outcome = main.sync_account_data()\nassert outcome["success"] is False and outcome["status"] == "auth_failed"\nimport inspect\nassert "await _ensure_account_synced()" not in inspect.getsource(main.get_account_summary)\nassert "asyncio.to_thread(sync_account_data)" in inspect.getsource(main._ensure_account_synced)\naccount_js_contract = (ROOT / "static/account.js").read_text(encoding="utf-8")\nassert "postJSON('/api/account/sync'" in account_js_contract\nprint("PASS audit-fix 6: account sync is explicit and GET summary is read-only")\n'''
if old not in text:
    raise SystemExit("audit-fix 6 legacy anchor missing")
text = text.replace(old, new, 1)
old = '''assert "pywebview" in (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()\nprint("PASS audit-fix 8: launch paths are non-destructive and desktop dependency is declared")\n'''
new = '''dependency_contract = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8").lower()\nassert "pywebview" in dependency_contract\nprint("PASS audit-fix 8: launch paths are non-destructive and desktop dependency is locked")\n'''
if old not in text:
    raise SystemExit("audit-fix 8 requirements anchor missing")
text = text.replace(old, new, 1)
p.write_text(text, encoding="utf-8", newline="\n")
for name in ("v42_legacy_failure.log", "v42_apply.log"):
    q = ROOT / name
    if q.exists():
        q.unlink()
print("V4.2 legacy regression expectations migrated")
