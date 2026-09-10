"""Offline diagnostics of the reviewed version, not regression expectations.

Run from the project root: python audit/performance_checks.py
No external HTTP, application startup, account mutations, or cache writes.
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
import requests

results = {}

with patch.object(requests.Session, "request", side_effect=AssertionError("External HTTP disabled")), \
     patch.object(config, "get_archivebate_credentials", return_value=("", "")):
    import main
    import camwhores
    from fastapi.testclient import TestClient

    # TestClient without a context manager does not run the app lifespan.
    client = TestClient(main.app)

    def cards(count):
        return [{"id": str(i), "username": f"author{i}"} for i in range(count)]

    def feed_call(group="0"):
        return main.get_videos(page=1, force_refresh=False, source="all",
                               author_filter="all", group_authors=group)

    with patch.object(main, "read_json_cache", return_value=(cards(24), time.time())), \
         patch.object(main, "_fetch_and_cache_home", return_value=cards(280)) as fetch, \
         patch.object(main, "_enrich_videos", side_effect=lambda videos, **kw: videos):
        response = feed_call()
        results["partial_fresh_cache"] = {"cached_items": 24, "blocking_fetches": fetch.call_count,
                                          "reported_state": response["cache_state"]}
        assert fetch.call_count == 1

    with patch.object(main, "read_json_cache", return_value=(cards(280), time.time())), \
         patch.object(main, "_enrich_videos", return_value=cards(20)), \
         patch.object(main.scraper, "get_home_videos", return_value=[]) as fetch:
        response = feed_call("1")
        results["full_fresh_cache_after_grouping"] = {
            "blocking_topups": fetch.call_count, "reported_state": response["cache_state"]}
        assert fetch.call_count == 1 and response["cache_state"] == "fresh"

    a = {"id": "a", "username": "synthetic_author", "duration": "10:00", "date": "2026-09-01"}
    b = {"id": "b", "username": "synthetic_author", "duration": "20:00", "date": "2026-09-01"}
    c = {"id": "c", "username": "synthetic_author", "duration": "10:00", "date": "2026-09-02"}
    results["dedup_identity"] = {
        "same_author_date_distinct_ids_remaining": len(camwhores.deduplicate_videos([a, b])),
        "same_author_duration_distinct_ids_remaining": len(camwhores.deduplicate_videos([a, c]))}
    assert list(results["dedup_identity"].values()) == [1, 1]
    timings = []
    for count in (280, 1000):
        samples = []
        for _ in range(3):
            started = time.perf_counter()
            assert len(camwhores.deduplicate_videos(cards(count))) == count
            samples.append(round((time.perf_counter() - started) * 1000, 2))
        timings.append({"items": count, "pair_comparisons": count * (count - 1) // 2,
                        "milliseconds": samples})
    results["dedup_synthetic_timing"] = timings

    raw = main.scraper.__class__(MagicMock())
    def source_page(page):
        return [{"id": str(page * 1000 + i), "username": f"author{page}x{i}"} for i in range(36)]
    with patch.object(raw, "_fetch_single_ab_home_page", side_effect=source_page), \
         patch("scraper.sort_videos_newest_first", side_effect=lambda xs: sorted(xs, key=lambda x: int(x["id"]), reverse=True)):
        first = raw.get_home_videos(1, "only-archivebate", "all", set(), set(), 280)
        second = raw.get_home_videos(2, "only-archivebate", "all", set(), set(), 280)
    omitted = {v["id"] for v in first[280:]} - {v["id"] for v in second}
    results["pagination_fixed_source_offsets"] = {
        "first_batch_items": len(first), "first_displayed_items": 280,
        "first_batch_remainder_absent_from_next_batch": len(omitted)}
    assert len(omitted) == 440

    fake_upstream = MagicMock()
    fake_upstream.status_code = 206
    fake_upstream.headers = {"Content-Type": "video/mp4", "Content-Length": "4096",
                             "Content-Range": "bytes 0-4095/8192"}
    fake_upstream.iter_content.return_value = iter([b"x" * 4096])
    with patch.object(main, "is_safe_remote_url", return_value=True), \
         patch.object(main, "_validated_session_get", return_value=fake_upstream):
        response = client.get("/api/video/stream?url=https://media.example/fixture.mp4",
                              headers={"Range": "bytes=0-4095", "Accept-Encoding": "gzip"})
    results["range_206_current_dependencies"] = {
        "status": response.status_code, "content_encoding": response.headers.get("content-encoding"),
        "content_range": response.headers.get("content-range"), "bytes": len(response.content)}
    assert response.status_code == 206 and "content-encoding" not in response.headers

    fake_upstream = MagicMock()
    fake_upstream.status_code = 416
    fake_upstream.headers = {"Content-Range": "bytes */8192"}
    with patch.object(main, "is_safe_remote_url", return_value=True), \
         patch.object(main, "_validated_session_get", return_value=fake_upstream):
        response = client.get("/api/video/stream?url=https://media.example/fixture.mp4",
                              headers={"Range": "bytes=9000-"})
    results["range_416_forwarding"] = {"status": response.status_code,
                                       "content_range": response.headers.get("content-range")}
    assert response.status_code == 502

    with patch.object(main, "is_safe_remote_url", return_value=True), \
         patch.object(main, "MEMORY_CACHE", {}), \
         patch.object(main.os.path, "exists", return_value=False), \
         patch.object(main, "_validated_session_get", side_effect=RuntimeError("fixture unavailable")):
        response = client.get("/api/thumb?url=https://media.example/fixture.jpg")
    results["thumbnail_failure"] = {"status": response.status_code,
                                     "type": response.headers.get("content-type"),
                                     "bytes": len(response.content)}
    assert response.status_code == 200 and response.headers["content-type"] == "image/gif"

    response = client.get("/static/app.js")
    results["static_cache"] = response.headers.get("cache-control")

    # The public storyboard route resolves details even if a board is ready.
    with patch.object(main, "_fetch_details_singleflight", return_value={}) as details, \
         patch.object(main, "start_storyboard", return_value={"status": "ready", "quality": "full", "created_at": 1}):
        main.storyboard_status_or_start(id="fixture", duration=60, force=False, request=None)
        results["ready_storyboard_details_resolution_calls"] = details.call_count
        assert details.call_count == 1

    client.close()

output = {"note": "Observed behavior of the audited version; timings are local synthetic measurements.",
          "results": results}
target = Path(__file__).with_name("performance_results.json")
target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(output, ensure_ascii=False, indent=2))
