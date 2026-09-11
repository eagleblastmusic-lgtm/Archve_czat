from pathlib import Path

path = Path(__file__).resolve().parent / "apply_archivebate_empty_boundary_fix.py"
text = path.read_text(encoding="utf-8")
needle = 'page > 1000'
count = text.count(needle)
assert count == 2, f"expected 2 boundary conditions, found {count}"
path.write_text(text.replace(needle, 'page == 1001'), encoding="utf-8")
