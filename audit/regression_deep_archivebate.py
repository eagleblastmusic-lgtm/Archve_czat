"""Regression for durable Archivebate model discovery + deep-profile merge."""
import tempfile
from pathlib import Path

from catalog_service import CatalogService
from deep_archivebate import DeepArchivebateService


def video(video_id, username, date):
    return {
        "id": str(video_id),
        "url": f"https://archivebate.com/watch/{video_id}",
        "username": username,
        "date": date,
        "source": "archivebate",
        "platform": "Chaturbate",
        "poster": f"https://cdn.example/{video_id}.jpg",
    }


class FakeScraper:
    def __init__(self):
        self._cache = {}
        self.search_calls = []
        self.profile_calls = []

    def _fetch_search_profiles(self, prefix, page=1):
        self.search_calls.append((prefix, page))
        assert prefix == "a"
        assert page == 1
        return ([{"username": "ancient_new_model"}], 1, 1)

    def get_archivebate_model_videos(self, username, page=1):
        self.profile_calls.append((username, page))
        if username == "ancient_new_model" and page == 1:
            return [video(2, username, "01.01.2020")]
        if username == "recent_model" and page == 1:
            return [video(3, username, "02.02.2019")]
        return []


with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "catalog.db"
    catalog = CatalogService(db)
    catalog.import_items([video(1, "recent_model", "1 hour ago")], revision=1, complete=True)

    deep = DeepArchivebateService(db, request_delay=0.01)
    seeded = deep.seed_catalog_models()
    assert seeded == 1, seeded

    now = 1.0
    conn = deep._get_conn()
    conn.execute(
        "INSERT INTO archivebate_discovery_queue(prefix, depth, status, updated_at) VALUES('a', 1, 'pending', ?)",
        (now,),
    )

    fake = FakeScraper()
    assert deep.discovery_step(fake) is True
    status = deep.status()
    assert status["models_discovered"] == 2, status
    assert status["discovery"].get("done") == 1, status

    # Search-discovered models have higher priority, so the first profile page immediately
    # contributes an older video and a brand-new author to the live completed revision.
    assert deep.crawl_step(fake) is True
    grouped = catalog.query_page(source="only-archivebate", group_authors=True, revision=1)
    assert grouped["video_count"] == 2, grouped
    assert grouped["group_count"] == 2, grouped
    assert grouped["page_count"] == 1, grouped
    usernames = {item.get("username") for item in grouped["items"]}
    assert usernames == {"recent_model", "ancient_new_model"}, usernames

    ungrouped = catalog.query_page(source="only-archivebate", group_authors=False, revision=1)
    assert ungrouped["video_count"] == 2, ungrouped
    assert any(item.get("date") == "01.01.2020" for item in ungrouped["items"]), ungrouped

    # Three confirmed empty profile pages terminate a model; one empty page cannot do so.
    assert deep.crawl_step(fake) is True  # page 2 empty
    row = conn.execute("SELECT crawl_complete, empty_streak FROM archivebate_models WHERE model_key='ancientnewmodel'").fetchone()
    assert int(row["crawl_complete"]) == 0 and int(row["empty_streak"]) == 1, dict(row)
    assert deep.crawl_step(fake) is True  # page 3 empty
    assert deep.crawl_step(fake) is True  # page 4 empty -> complete
    row = conn.execute("SELECT crawl_complete, end_reason FROM archivebate_models WHERE model_key='ancientnewmodel'").fetchone()
    assert int(row["crawl_complete"]) == 1, dict(row)
    assert str(row["end_reason"]).startswith("consecutive_empty_pages:3:"), dict(row)

    # The catalog-seeded model then gets its own historical page merged without duplicating
    # the existing recent item.
    assert deep.crawl_step(fake) is True
    final = catalog.query_page(source="only-archivebate", revision=1)
    assert final["video_count"] == 3, final
    assert deep.status()["deep_items"] == 2, deep.status()

    deep.close()
    catalog.close()

print("PASS: Archivebate deep discovery is durable and historical profile items merge into the live catalog")
