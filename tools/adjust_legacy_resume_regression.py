from pathlib import Path

path = Path(__file__).resolve().parents[1] / "audit" / "regression_legacy_resume_cursor.py"
text = path.read_text(encoding="utf-8")
old = '''    assert calls == [1001], calls
    stats = restarted.get_revision_stats(21)
    assert stats["complete"] is True and stats["failed"] is False, stats
    restarted.close()
'''
new = '''    assert calls == [1001, 1001, 1001, 1001], calls
    stats = restarted.get_revision_stats(21)
    assert stats["complete"] is True and stats["failed"] is False, stats
    page = restarted.query_page(revision=21)
    assert page["catalog_limited"] is True, page
    assert page["limited_sources"]["archivebate"] == "source_page_limit:empty:1001", page
    restarted.close()
'''
assert old in text, "legacy regression anchor missing"
path.write_text(text.replace(old, new, 1), encoding="utf-8")
