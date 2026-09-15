from pathlib import Path
import multiprocessing as mp
import os
import sqlite3
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage import LocalStore
from catalog_service import CatalogService
from video_identity import VideoKey

TMP_ROOT = Path(tempfile.mkdtemp(prefix="archivebate_nextgen_"))
STORE_CASE_PATH = TMP_ROOT / "user_store.json"
CATALOG_CASE_ROOT = TMP_ROOT / "catalog"
CATALOG_CASE_DB = CATALOG_CASE_ROOT / "catalog.db"


def make_video(video_id: str, username: str = "alice", *, source: str = "archivebate", date: str = "2026-01-01") -> dict:
    return {
        "id": video_id,
        "source": source,
        "url": f"https://example.invalid/{source}/{video_id}",
        "username": username,
        "date": date,
        "duration": "01:00",
        "duration_seconds": 60,
        "views": "100",
        "poster": f"https://example.invalid/{video_id}.jpg",
        "poster_direct": f"https://example.invalid/{video_id}.jpg",
        "preview_video": None,
        "timeline_prefix": None,
        "timeline_count": 0,
        "platform": source,
        "tags": ["Female"],
    }


def _writer_process(path: str, prefix: str, count: int) -> None:
    store = LocalStore(path=path)
    for index in range(count):
        store.add_history(make_video(f"{prefix}-{index}"))


def test_storage_identity_and_recovery() -> None:
    path = STORE_CASE_PATH
    store = LocalStore(path=path)
    archive = make_video("same", source="archivebate")
    cam = make_video("same", source="camwhores")
    store.add_favorite(archive)
    store.add_favorite(cam)
    favorites = store.get_favorites()
    keys = {VideoKey.from_video(item, require_source=True).as_string() for item in favorites}
    assert keys == {"archivebate:same", "camwhores:same"}

    store.add_history(make_video("blocked-1", username="blocked"))
    store.block_model("blocked")
    assert not store.get_history(include_blocked=False)
    assert store.get_history(include_blocked=True)

    # Corruption must fail closed and preserve the corrupt file instead of
    # silently overwriting it with defaults.
    path.write_text("{ broken", encoding="utf-8")
    broken = LocalStore(path=path)
    health = broken.health()
    assert health["recovery_required"] is True
    before = path.read_text(encoding="utf-8")
    try:
        broken.add_history(make_video("should-not-write"))
    except Exception:
        pass
    assert path.read_text(encoding="utf-8") == before
    print("PASS nextgen storage: scoped identities, non-destructive block, fail-safe recovery")


def test_storage_cross_process_lock() -> None:
    path = TMP_ROOT / "concurrent_store.json"
    processes = [
        mp.Process(target=_writer_process, args=(str(path), "a", 15)),
        mp.Process(target=_writer_process, args=(str(path), "b", 15)),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    store = LocalStore(path=path)
    history = store.get_history(include_blocked=True)
    ids = {item["id"] for item in history}
    assert {f"a-{i}" for i in range(15)}.issubset(ids)
    assert {f"b-{i}" for i in range(15)}.issubset(ids)
    print("PASS nextgen lock: one writer across processes")


def test_catalog_revision_and_group_pages() -> None:
    CATALOG_CASE_ROOT.mkdir(parents=True, exist_ok=True)
    service = CatalogService(root=CATALOG_CASE_ROOT, db_path=CATALOG_CASE_DB)
    try:
        first = service.build_revision_from_videos(
            [make_video("a1", "alice"), make_video("a2", "alice"), make_video("b1", "bob")],
            source="fixture",
        )
        assert first["published"] is True
        rev1 = first["revision"]
        meta1 = service.get_revision_meta(rev1)
        assert meta1["complete"] is True and meta1["is_active"] is True
        assert meta1["video_count"] == 3

        grouped = service.query_feed(page=1, per_page=10, group_authors=True, revision=rev1)
        assert len(grouped["items"]) == 2
        alice = next(item for item in grouped["items"] if item["username"] == "alice")
        assert alice["group_count"] == 2
        assert alice["group_members_lazy"] is True
        page = service.query_group_members(alice["group_key"], revision=rev1, page=1, per_page=1)
        assert page["total"] == 2 and len(page["items"]) == 1 and page["has_more"] is True

        second = service.build_revision_from_videos(
            [make_video("a1", "alice"), make_video("c1", "carol")],
            source="fixture",
        )
        assert second["published"] is True
        rev2 = second["revision"]
        assert rev2 > rev1
        assert service.get_revision_meta(rev1)["is_active"] is False
        assert service.get_revision_meta(rev2)["is_active"] is True
        old = service.query_feed(page=1, per_page=10, revision=rev1)
        new = service.query_feed(page=1, per_page=10, revision=rev2)
        assert {item["id"] for item in old["items"]} == {"a1", "a2", "b1"}
        assert {item["id"] for item in new["items"]} == {"a1", "c1"}

        # reader lifecycle must drain cleanly
        service.close(timeout=3)
        assert service._active_readers == 0
    finally:
        try:
            service.close(timeout=3)
        except Exception:
            pass
    print("PASS nextgen catalog: immutable revisions, exact counts, lazy group pages, reader drain")


def test_background_worker_lifecycle() -> None:
    import fast_scan
    import storyboard_service as storyboard

    calls = []
    try:
        with patch.object(storyboard, "_cached_variant", return_value=None), patch.object(
            storyboard, "_build_variant", side_effect=lambda video_id, duration, source_url, quality: calls.append((video_id, quality)) or {"quality": quality}
        ):
            storyboard.start("lifecycle-no-demand", 10, "fixture")
            storyboard._jobs.join()
            assert calls == []

            storyboard.demand("lifecycle-video", "consumer")
            storyboard.start("lifecycle-video", 10, "fixture")
            storyboard._jobs.join()
            assert calls == [("lifecycle-video", "quick")]
            assert storyboard.runtime_stats()["auto_full_upgrade"] is False
            storyboard.demand("lifecycle-video", "consumer", active=False)
            storyboard.shutdown(timeout=3)
            # V4.3 uses a bounded worker pool instead of the legacy singleton.
            assert storyboard._worker_threads == []

            # A clean shutdown must permit a later explicit start in the same process.
            storyboard.demand("lifecycle-restart", "consumer")
            storyboard.start("lifecycle-restart", 10, "fixture")
            storyboard._jobs.join()
            assert calls[-1:] == [("lifecycle-restart", "quick")]
            storyboard.demand("lifecycle-restart", "consumer", active=False)
    finally:
        try:
            storyboard.shutdown(timeout=3)
        except Exception:
            pass

    # quick scan lifecycle keeps explicit joinable workers too
    supervisor = fast_scan.QuickScanSupervisor()
    with patch.object(supervisor, "_scan_worker", side_effect=lambda *args, **kwargs: None):
        result = supervisor.start([])
        assert result["success"] is True
        supervisor.shutdown(timeout=3)
    print("PASS nextgen lifecycle: workers drain/restart cleanly")


def test_http_routes_and_asset_versioning() -> None:
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        static = client.get("/static/app.js")
        assert static.status_code == 200
        assert static.headers["cache-control"] == "no-cache"

        root = client.get("/")
        assert root.status_code == 200
        assert "?v=" in root.text
        assert "archivebate-mutation-token" in root.text

        status = client.get("/api/status")
        assert status.status_code == 200
        assert "store_health" in status.json()

        # GET is read-only; mutation endpoint exists separately.
        summary = client.get("/api/account/summary")
        assert summary.status_code == 200
        token_match = __import__("re").search(r'name="archivebate-mutation-token" content="([^"]+)"', root.text)
        assert token_match
        token = token_match.group(1)
        sync = client.post("/api/account/sync", headers={"X-Archivebate-Mutation-Token": token})
        assert sync.status_code == 200
    print("PASS nextgen HTTP: versioned assets, health, explicit account mutation")


def cleanup() -> None:
    try:
        import shutil
        shutil.rmtree(TMP_ROOT, ignore_errors=True)
    except Exception:
        pass


def main() -> None:
    try:
        test_storage_identity_and_recovery()
        test_storage_cross_process_lock()
        test_catalog_revision_and_group_pages()
        test_background_worker_lifecycle()
        test_http_routes_and_asset_versioning()
        print("PASS NEXT-GENERATION REGRESSION")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
