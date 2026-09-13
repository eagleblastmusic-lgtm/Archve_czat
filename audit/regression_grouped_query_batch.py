"""Regression: grouped catalog pages must not execute one SQL query per author."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog_service import CatalogService


service = CatalogService(":memory:")
items = []
for author_idx in range(600):
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

enrichment_batches = []
def enrich(rows):
    enrichment_batches.append(len(rows))
    return [{**row, "enriched": True} for row in rows]

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
    enrich_fn=enrich,
)
conn.set_trace_callback(None)

selects = [s for s in statements if s.lstrip().upper().startswith(("SELECT", "WITH"))]
assert result["count"] == 280, result["count"]
assert result["group_count"] == 600, result["group_count"]
assert all(v.get("group_count") == 3 for v in result["videos"]), result["videos"][:2]
assert all(len(v.get("grouped_videos") or []) == 3 for v in result["videos"]), result["videos"][:2]
member_batch_selects = [
    statement
    for statement in selects
    if "ROW_NUMBER() OVER" in statement.upper()
    and "PARTITION BY AUTHOR_CLEAN" in statement.upper()
]
assert len(selects) <= 8, f"grouped page used {len(selects)} SELECT/WITH statements; query budget regressed"
assert len(member_batch_selects) == 1, (
    "grouped members must be loaded by exactly one batch query; "
    f"observed {len(member_batch_selects)} member queries"
)
assert len(enrichment_batches) == 2, enrichment_batches
assert all(v['enriched'] and all(m['enriched'] for m in v['grouped_videos']) for v in result['items'])
page2 = service.query_page(page=2, group_authors=True, enrich_fn=enrich)
assert page2['count'] == 280 and page2['page_complete']
assert not ({v['username'] for v in result['items']} & {v['username'] for v in page2['items']})

# A real library can exceed SQLite's OR-expression depth. Membership must
# remain source scoped and combine correctly with favorite authors.
favorites = [{'source': 'archivebate', 'provider_id': f'missing_{i}'} for i in range(1100)]
favorites += [{'source': 'archivebate', 'provider_id': '0_0'},
              {'source': 'camwhores', 'provider_id': '1_0'}]
only = service.query_page(author_filter='only_fav', favorite_ids=favorites, favorite_authors=['author_002'])
# Count SQLite work instead of wall time: adding nonmatching favorites must
# not turn the exclusion path into a catalog-rows x favorites scan.
vm_steps = [0]
def progress():
    vm_steps[0] += 1000
    return 0
conn.set_progress_handler(progress, 1000)
service.query_page(author_filter='exclude_fav', favorite_ids=favorites[-2:], favorite_authors=['author_002'])
small_steps = vm_steps[0]
vm_steps[0] = 0
excluded = service.query_page(author_filter='exclude_fav', favorite_ids=favorites, favorite_authors=['author_002'])
conn.set_progress_handler(None, 0)
assert vm_steps[0] < small_steps * 3, (small_steps, vm_steps[0])
assert {v['id'] for v in only['items']} == {'0_0', '2_0', '2_1', '2_2'}
assert excluded['video_count'] == len(items) - 4

service.close()
print(f"PASS: grouped catalog page uses a bounded batch query ({len(selects)} SELECT/WITH statements for 280 authors)")
