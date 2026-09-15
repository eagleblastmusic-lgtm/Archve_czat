"""Regression coverage for the V4.3 grouped-feed v2 fast path."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from catalog_service import CatalogService
import fast_grouped_feed_v2


def make_video(idx: int, author: str, published: float, source: str = "archivebate"):
    return {
        "id": str(100000 + idx),
        "source": source,
        "username": author,
        "author": author,
        "published_at": published,
        "date": "1 minute ago",
        "duration": "01:00",
        "poster": f"https://example.invalid/{idx}.jpg",
        "url": f"https://example.invalid/video/{idx}",
        "title": f"video {idx}",
        "platform": "Archivebate",
    }


def main():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "catalog.db"
        svc = CatalogService(db_path=db, page_size=20)
        now = time.time()
        videos = []
        idx = 0

        for author_no in range(60):
            author = f"author_{author_no:02d}"
            for member in range(1 + (author_no % 7)):
                videos.append(make_video(idx, author, now - author_no * 100 - member))
                idx += 1
        for member in range(4):
            videos.append(make_video(idx, "Model", now - 10000 - member))
            idx += 1
        for member in range(8):
            videos.append(make_video(idx, f"cw_{member}", now - 20000 - member, source="camwhores"))
            idx += 1

        svc.import_items(videos, revision=1, complete=True, source="fixture")
        original = CatalogService.query_page
        kwargs = dict(
            page=1,
            page_size=20,
            source="only-archivebate",
            author_filter="exclude_fav",
            group_authors=True,
            revision=1,
            blocked_models=["author_03", "author_11"],
            favorite_authors=["author_04"],
            favorite_ids=[{"source": "archivebate", "provider_id": "100000"}],
            enrich_fn=None,
        )

        baseline = original(svc, **kwargs)
        fast_grouped_feed_v2.install()
        fast_grouped_feed_v2.clear_cache()
        fast = svc.query_page(**kwargs)

        assert fast["v43_grouped_fast_path"] == 2
        assert fast["video_count"] == baseline["video_count"], (fast["video_count"], baseline["video_count"])
        assert fast["group_count"] == baseline["group_count"], (fast["group_count"], baseline["group_count"])
        assert fast["page_count"] == baseline["page_count"]
        assert [v["id"] for v in fast["videos"]] == [v["id"] for v in baseline["videos"]]
        assert [v["group_count"] for v in fast["videos"]] == [v["group_count"] for v in baseline["videos"]]
        assert fast["counts"] == baseline["counts"]

        for video in fast["videos"]:
            assert video.get("revision") == 1
            if video.get("is_grouped"):
                assert video.get("group_members_lazy") is True
                assert video.get("grouped_videos") == []
                assert video.get("group_members_url")

        limited = svc.query_page(**{**kwargs, "item_limit": 16})
        assert len(limited["videos"]) == 16
        assert limited["video_count"] == fast["video_count"]
        assert limited["group_count"] == fast["group_count"]
        assert limited["page_count"] == fast["page_count"]
        assert limited["page_complete"] is False
        assert limited["v43_grouped_fast_path"] == 2

        # Page two must extend the cached newest-first leader cursor without changing ordering.
        fast_page_2 = svc.query_page(**{**kwargs, "page": 2})
        baseline_page_2 = original(svc, **{**kwargs, "page": 2})
        assert [v["id"] for v in fast_page_2["videos"]] == [v["id"] for v in baseline_page_2["videos"]]

        # Ungrouped mode is delegated to the original implementation.
        ungrouped = svc.query_page(page=1, page_size=20, source="only-archivebate", group_authors=False, revision=1)
        direct = original(svc, page=1, page_size=20, source="only-archivebate", group_authors=False, revision=1)
        assert [v["id"] for v in ungrouped["videos"]] == [v["id"] for v in direct["videos"]]

        svc.close()

    print("PASS V4.3 GROUPED FEED V2")


if __name__ == "__main__":
    main()
