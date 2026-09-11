from pathlib import Path

# Durable resume: after page 1001 succeeds, one empty page above it is not trusted as EOF.
path = Path("audit/regression_catalog_resume.py")
text = path.read_text(encoding="utf-8")

old = '''        if page == 1002:\n            return []\n        raise AssertionError(f"unexpected page {page}")\n'''
new = '''        if 1002 <= page <= 1006:\n            return []\n        raise AssertionError(f"unexpected page {page}")\n'''
if text.count(old) != 1:
    raise SystemExit(f"catalog-resume fetch expectation patch expected 1 match, found {text.count(old)}")
text = text.replace(old, new, 1)

old = '''    assert calls == [1001, 1002], calls\n'''
new = '''    assert calls == [1001, 1002, 1003, 1004, 1005, 1006], calls\n'''
if text.count(old) != 1:
    raise SystemExit(f"catalog-resume call expectation patch expected 1 match, found {text.count(old)}")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

# Normal partial legacy-cursor regression: an isolated empty page at 38 is likewise a gap candidate.
path = Path("audit/regression_legacy_resume_cursor.py")
text = path.read_text(encoding="utf-8")

old = '''        if page == 38:\n            return []\n        raise AssertionError(page)\n'''
new = '''        if 38 <= page <= 42:\n            return []\n        raise AssertionError(page)\n'''
if text.count(old) != 1:
    raise SystemExit(f"legacy-resume fetch expectation patch expected 1 match, found {text.count(old)}")
text = text.replace(old, new, 1)

old = '''    assert calls == [37, 38], calls\n'''
new = '''    assert calls == [37, 38, 39, 40, 41, 42], calls\n'''
if text.count(old) != 1:
    raise SystemExit(f"legacy-resume call expectation patch expected 1 match, found {text.count(old)}")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

print("Updated durable and legacy resume regressions for Archivebate sparse EOF verification")
