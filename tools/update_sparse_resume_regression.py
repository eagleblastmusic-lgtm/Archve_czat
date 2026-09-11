from pathlib import Path

path = Path("audit/regression_catalog_resume.py")
text = path.read_text(encoding="utf-8")

old = '''        if page == 1002:\n            return []\n        raise AssertionError(f"unexpected page {page}")\n'''
new = '''        if 1002 <= page <= 1006:\n            return []\n        raise AssertionError(f"unexpected page {page}")\n'''
if text.count(old) != 1:
    raise SystemExit(f"fetch expectation patch expected 1 match, found {text.count(old)}")
text = text.replace(old, new, 1)

old = '''    assert calls == [1001, 1002], calls\n'''
new = '''    assert calls == [1001, 1002, 1003, 1004, 1005, 1006], calls\n'''
if text.count(old) != 1:
    raise SystemExit(f"call expectation patch expected 1 match, found {text.count(old)}")
text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("Updated durable resume regression for Archivebate sparse EOF verification")
