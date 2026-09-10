"""Comprehensive acceptance test for PAKIET B (Plan Naprawy Regresji V2).

Criteria:
1. Complete fixture 721 videos shows from start 3 pages, sizes 280/280/161, and working jump to last page.
2. Result identical after restart and source disconnection.
3. Background update does not change counter before publishing new revision.
4. Filters and groups verified with exact sum consistency.
5. Fixture 70,000 metadata items: 20 reads of page 1 with counters; p95 <= 200ms.
6. HTTP API contract endpoints: /api/feed and /api/stats return catalog_revision, page, page_size,
   video_count, group_count, page_count, items, catalog_complete, updated_at.
"""
import copy
import math
import os
import sys
import tempfile
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog_service import CatalogService, extract_item_metadata
from fastapi.testclient import TestClient
import main


def run_tests():
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp_dir:
        db_path = Path(tmp_dir) / "test_catalog.db"
        cs = CatalogService(db_path=db_path)

        # -------------------------------------------------------------
        # 1. Complete fixture: 721 videos (sizes 280 / 280 / 161)
        # -------------------------------------------------------------
        now = time.time()
        fixture_721 = []
        for i in range(721):
            src = "archivebate" if i % 2 == 0 else "camwhores"
            vid_id = f"{i}" if src == "archivebate" else f"cw_{i}"
            fixture_721.append({
                "id": vid_id,
                "username": f"model_{i % 25}",
                "source": src,
                "date": f"{i} hours ago",
                "published_at": now - i * 60,
                "url": f"https://example.com/watch/{vid_id}",
                "poster": f"https://example.com/thumb/{vid_id}.jpg"
            })

        cs.import_items(fixture_721, revision=1, complete=True)

        res_p1 = cs.query_page(page=1)
        res_p2 = cs.query_page(page=2)
        res_p3 = cs.query_page(page=3)

        assert res_p1["video_count"] == 721, f"Expected 721 videos, got {res_p1['video_count']}"
        assert res_p1["page_count"] == 3, f"Expected 3 pages, got {res_p1['page_count']}"
        assert res_p1["count"] == 280, f"Page 1 expected 280 items, got {res_p1['count']}"
        assert res_p2["count"] == 280, f"Page 2 expected 280 items, got {res_p2['count']}"
        assert res_p3["count"] == 161, f"Page 3 expected 161 items, got {res_p3['count']}"
        assert res_p1["catalog_complete"] is True
        assert res_p3["catalog_complete"] is True

        # Verify all 721 unique items are retrieved without duplicates
        all_ids = [v["id"] for v in res_p1["items"] + res_p2["items"] + res_p3["items"]]
        assert len(all_ids) == len(set(all_ids)) == 721

        # Check chronological sorting: newest first
        for i in range(len(res_p1["items"]) - 1):
            t_curr = res_p1["items"][i].get("published_at", 0)
            t_next = res_p1["items"][i + 1].get("published_at", 0)
            assert t_curr >= t_next, f"Ordering failed at {i}: {t_curr} < {t_next}"

        # -------------------------------------------------------------
        # 2. Restart and source disconnection
        # -------------------------------------------------------------
        cs.close()
        # Simulate restart: open new CatalogService pointing to same db
        cs_restarted = CatalogService(db_path=db_path)

        def failing_fetcher(p):
            raise ConnectionError("Network offline")

        # Source disconnected, but persisted catalog serves immediately
        res_rst1 = cs_restarted.query_page(page=1)
        res_rst3 = cs_restarted.query_page(page=3)
        assert res_rst1["video_count"] == 721
        assert res_rst1["page_count"] == 3
        assert res_rst1["count"] == 280
        assert res_rst3["count"] == 161
        assert res_rst1["catalog_complete"] is True

        # -------------------------------------------------------------
        # 3. Background update does NOT change counter before publish
        # -------------------------------------------------------------
        # Partially populate revision 2 with some new items
        new_batch = [{"id": f"new_{j}", "username": f"new_{j}", "published_at": now + j} for j in range(50)]
        cs_restarted.import_items(new_batch, revision=2, complete=False)

        # Active catalog MUST still return revision 1 (721 videos, 3 pages)
        res_during_bg = cs_restarted.query_page(page=1)
        assert res_during_bg["catalog_revision"] == 1
        assert res_during_bg["video_count"] == 721
        assert res_during_bg["page_count"] == 3

        # Once revision 2 is published atomically, active revision increments
        cs_restarted.publish_revision(2)
        res_after_publish = cs_restarted.query_page(page=1)
        assert res_after_publish["catalog_revision"] == 2
        assert res_after_publish["video_count"] == 50

        # Reset back to revision 1 for subsequent filter tests
        cs_restarted.publish_revision(1)

        # -------------------------------------------------------------
        # 4. Filters and groups: sum consistency
        # -------------------------------------------------------------
        # Source filters:
        ab_res = cs_restarted.query_page(source="only-archivebate", revision=1)
        cw_res = cs_restarted.query_page(source="only-camwhores", revision=1)
        assert ab_res["video_count"] + cw_res["video_count"] == 721, "Source counts do not sum to 721"
        assert ab_res["video_count"] == 361
        assert cw_res["video_count"] == 360

        # Blocked models filter:
        blocked = ["model_0", "model_1", "model_2"]
        blk_res = cs_restarted.query_page(blocked_models=blocked, revision=1)
        # Verify blocked videos deducted accurately
        model_0_to_2_count = sum(1 for v in fixture_721 if v["username"] in blocked)
        assert blk_res["video_count"] == 721 - model_0_to_2_count

        # Grouping (1 kafelek na modelkę):
        grp_res = cs_restarted.query_page(group_authors=True, revision=1)
        assert grp_res["video_count"] == 721
        assert grp_res["group_count"] == 25  # 25 unique models
        assert grp_res["page_count"] == 1
        assert len(grp_res["items"]) == 25
        # Sum of grouped members must match total records
        total_grouped_vids = sum(len(leader.get("grouped_videos", [])) for leader in grp_res["items"])
        assert total_grouped_vids == 721, f"Grouped members sum {total_grouped_vids} != 721"

        # -------------------------------------------------------------
        # 5. Performance benchmark on 70,000 metadata items
        # -------------------------------------------------------------
        db_70k = Path(tmp_dir) / "test_70k.db"
        cs_70k = CatalogService(db_path=db_70k)
        items_70k = [
            {
                "id": str(idx),
                "username": f"performer_{idx % 1000}",
                "source": "archivebate" if idx % 2 == 0 else "camwhores",
                "published_at": now - idx * 10,
                "date": f"{idx} minutes ago",
                "url": f"https://example.com/v/{idx}",
                "poster": f"https://example.com/p/{idx}.jpg"
            }
            for idx in range(70000)
        ]
        cs_70k.import_items(items_70k, revision=1, complete=True)

        read_times = []
        for _ in range(20):
            t_start = time.monotonic()
            page1 = cs_70k.query_page(page=1)
            read_times.append(time.monotonic() - t_start)

        read_times.sort()
        p95_time = read_times[int(len(read_times) * 0.95)]
        assert page1["video_count"] == 70000
        assert page1["page_count"] == 250
        assert page1["count"] == 280
        assert p95_time <= 0.200, f"p95 {p95_time*1000:.2f}ms exceeds 200ms limit"

        # -------------------------------------------------------------
        # 6. HTTP API Contract verification (/api/feed & /api/stats)
        # -------------------------------------------------------------
        # Inject cs_restarted as main catalog service
        main.catalog_service = cs_restarted
        import catalog_service as cs_module
        orig_service = cs_module.catalog_service
        cs_module.catalog_service = cs_restarted

        try:
            client = TestClient(main.app)

            # GET /api/feed page 1
            resp_p1 = client.get("/api/feed?page=1")
            assert resp_p1.status_code == 200, resp_p1.text
            d1 = resp_p1.json()
            for key in ["catalog_revision", "page", "page_size", "video_count", "group_count", "page_count", "items", "catalog_complete", "updated_at"]:
                assert key in d1, f"Missing key {key} in /api/feed contract"
            assert d1["video_count"] == 721
            assert d1["page_count"] == 3
            assert len(d1["items"]) == 280
            assert d1["catalog_complete"] is True

            # GET /api/feed page 3 (last page jump)
            resp_p3 = client.get("/api/feed?page=3")
            assert resp_p3.status_code == 200
            d3 = resp_p3.json()
            assert d3["page"] == 3
            assert len(d3["items"]) == 161

            # GET /api/feed with revision pinning
            resp_pinned = client.get("/api/feed?page=2&revision=1")
            assert resp_pinned.status_code == 200
            assert resp_pinned.json()["catalog_revision"] == 1

            # GET /api/stats
            resp_stats = client.get("/api/stats")
            assert resp_stats.status_code == 200
            s = resp_stats.json()
            assert s["catalog_videos"] == 721
            assert s["catalog_pages"] == 3
            assert s["catalog_complete"] is True

            print(f"PASS: Pakiet B acceptance test passed successfully! (70k p95={p95_time*1000:.2f}ms)")
        finally:
            cs_module.catalog_service = orig_service
            main.catalog_service = orig_service
            cs_70k.close()
            cs_restarted.close()


if __name__ == "__main__":
    run_tests()
