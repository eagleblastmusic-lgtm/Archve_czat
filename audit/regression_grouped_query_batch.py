"""Regression: grouped catalog pages must not execute one SQL query per author."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog_service import CatalogService


service = CatalogService(":memory:")
items = []
for author_idx in range(300):
    for video_idx in range(3):
        items.append({
            "id": f"{author_idx}_{video_idx}",
            "username": f"author_{author_idx:03d}",
            "source": "archivebate",
            "date": f"{video_idx + 1} hour",
            "poster": f"https://example.invalid/{author_idx}_{video_idx}.jpg",
            "url": f"https://archivebate.com/watch/{author_idx}_{video_idx}",
        })

service.import_items(items, revision=1, complete=True, source="archivebate")
conn = service._get_conn()
statements = []
conn.set_trace_callback(statements.append)

result = service.query_page(
    page=1,
    page_size=280,
    source="only-archivebate",
    author_filter="all",
    group_authors=True,
    revision=1,
    blocked_models=[],
    favorite_authors=[],
    favorite_ids=[],
    enrich_fn=lambda rows: rows,
)
conn.set_trace_callback(None)

selects = [s for s in statements if s.lstrip().upper().startswith(("SELECT", "WITH"))]
assert result["count"] == 280, result["count"]
assert result["group_count"] == 300, result["group_count"]
assert all(v.get("group_count") == 3 for v in result["videos"]), result["videos"][:2]
assert all(len(v.get("grouped_videos") or []) == 3 for v in result["videos"]), result["videos"][:2]
assert len(selects) <= 6, f"grouped page used {len(selects)} SELECT/WITH statements; N+1 query likely returned"

service.close()
print(f"PASS: grouped catalog page uses a bounded batch query ({len(selects)} SELECT/WITH statements for 280 authors)")
