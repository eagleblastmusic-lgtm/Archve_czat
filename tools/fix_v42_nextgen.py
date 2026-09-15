from pathlib import Path

p = Path("audit/regression_next_generation.py")
text = p.read_text(encoding="utf-8")
old1 = '            assert calls == [("lifecycle-video", "quick"), ("lifecycle-video", "full")]\n'
new1 = '            assert calls == [("lifecycle-video", "quick")]\n            assert storyboard.runtime_stats()["auto_full_upgrade"] is False\n'
old2 = '            assert calls[-2:] == [("lifecycle-restart", "quick"), ("lifecycle-restart", "full")]\n'
new2 = '            assert calls[-1:] == [("lifecycle-restart", "quick")]\n'
if old1 not in text or old2 not in text:
    raise SystemExit("next-generation storyboard anchors missing")
text = text.replace(old1, new1, 1).replace(old2, new2, 1)
p.write_text(text, encoding="utf-8", newline="\n")
print("V4.2 next-generation storyboard expectations migrated")
