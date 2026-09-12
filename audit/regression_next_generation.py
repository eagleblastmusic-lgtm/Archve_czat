"""Offline acceptance checks for the Archivebite next-generation contracts.

The fixture files live directly under ``audit`` because the managed Windows
workspace can deny nested Python temporary directories. Nothing in this test
opens or mutates the real account store or catalog database.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "audit"
RUN = f"{os.getpid()}_{int(time.time() * 1000)}"
PRIMARY_STORE = AUDIT / f"nextgen_{RUN}_store.json"
LOCK_STORE = AUDIT / f"nextgen_{RUN}_lock.json"
LOCK_MARKER = AUDIT / f"nextgen_{RUN}_lock.ready"
CATALOG_DB = AUDIT / f"nextgen_{RUN}_catalog.db"
CATALOG_CASE_DB = AUDIT / f"nextgen_{RUN}_catalog_case.db"
FEED_DIR = AUDIT / f"nextgen_{RUN}_feed"

# Redirect module singletons before importing application modules.
os.environ["ARCHIVEBATE_USER_STORE"] = str(AUDIT / f"nextgen_{RUN}_global_store.json")
os.environ["ARCHIVEBATE_CATALOG_DB"] = str(CATALOG_DB)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

import catalog_service as catalog_mod
import config
import feed_service
import storage as storage_mod
from catalog_service import CatalogService
from fetch_contract import FetchResult


def video(source: str, provider_id: str, username: str = "fixture", published_at: float = 0.0) -> dict:
    public_id = f"cw_{provider_id}" if source == "camwhores" else provider_id
    return {
        "id": public_id,
        "source": source,
        "provider_id": provider_id,
        "username": username,
        "published_at": published_at,
        "date": "fixture",
        "url": f"https://example.invalid/{source}/{provider_id}",
        "poster": f"https://example.invalid/{source}/{provider_id}.jpg",
    }


def remove_generated_files() -> None:
    for path in (
        PRIMARY_STORE,
        Path(f"{PRIMARY_STORE}.last-good.bak"),
        Path(f"{PRIMARY_STORE}.recovery-source.bak"),
        LOCK_STORE,
        Path(f"{LOCK_STORE}.last-good.bak"),
        Path(f"{LOCK_STORE}.recovery-source.bak"),
        Path(f"{LOCK_STORE}.lock"),
        LOCK_MARKER,
        CATALOG_DB,
        Path(f"{CATALOG_DB}-wal"),
        Path(f"{CATALOG_DB}-shm"),
        CATALOG_CASE_DB,
        Path(f"{CATALOG_CASE_DB}-wal"),
        Path(f"{CATALOG_CASE_DB}-shm"),
        Path(os.environ["ARCHIVEBATE_USER_STORE"]),
        Path(f"{os.environ['ARCHIVEBATE_USER_STORE']}.lock"),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if FEED_DIR.exists():
        for child in FEED_DIR.iterdir():
            try:
                child.unlink()
            except OSError:
                pass
        try:
            FEED_DIR.rmdir()
        except OSError:
            pass


def test_identity_and_recovery() -> None:
    from video_identity import VideoKey

    assert VideoKey.from_video({"source": "archivebate", "id": "7"}, require_source=True).as_string() == "archivebate:id:7"
    assert VideoKey.from_video({"source": "camwhores", "id": "cw_7"}, require_source=True).as_string() == "camwhores:id:7"
    assert VideoKey.from_video({"id": "7"}, require_source=True) is None

    store = storage_mod.UserStorage(store_file=str(PRIMARY_STORE))
    ab = video("archivebate", "7", "same")
    cw = video("camwhores", "7", "same")
    store.toggle_favorite(ab)
    store.toggle_favorite(cw)
    store.record_history(ab)
    exported = store.export_snapshot()
    before_block = json.dumps(store.data, sort_keys=True)
    blocked = store.block_model("same", video_count=2)
    assert blocked["destructive"] is False and blocked["removed_videos"] == 0
    assert json.dumps(store.data, sort_keys=True) != before_block  # only projection/preferences changed
    assert len(store.get_favorites(include_blocked=False)) == 0
    assert len(store.get_favorites(include_blocked=True)) == 2
    assert len(store.get_history(include_blocked=True)) == 1
    assert store.unblock_model("same") is True
    assert len(store.get_favorites(include_blocked=False)) == 2
    store.close()

    original = b"{ definitely not json"
    PRIMARY_STORE.write_bytes(original)
    broken = storage_mod.UserStorage(store_file=str(PRIMARY_STORE))
    assert broken.health()["status"] == "recovery_required"
    try:
        broken.toggle_favorite(ab)
    except storage_mod.StoreRecoveryRequired:
        pass
    else:
        raise AssertionError("corrupt store acknowledged a mutation")
    assert PRIMARY_STORE.read_bytes() == original
    restored = broken.restore_snapshot(exported)
    assert restored["success"] is True and broken.health()["status"] == "ready"
    assert Path(f"{PRIMARY_STORE}.recovery-source.bak").exists()
    broken.close()
    print("PASS nextgen storage: scoped identities, non-destructive block, fail-safe recovery")


def test_cross_process_lock() -> None:
    child_code = (
        "import os,time; from pathlib import Path; import storage; "
        "Path(os.environ['ARCHIVEBATE_LOCK_MARKER']).write_text('ready', encoding='utf-8'); time.sleep(4)"
    )
    child_env = dict(os.environ)
    child_env["ARCHIVEBATE_USER_STORE"] = str(LOCK_STORE)
    child_env["ARCHIVEBATE_LOCK_MARKER"] = str(LOCK_MARKER)
    child = subprocess.Popen([sys.executable, "-c", child_code], cwd=ROOT, env=child_env)
    try:
        deadline = time.monotonic() + 3
        while not LOCK_MARKER.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert LOCK_MARKER.exists(), "lock holder did not start"
        try:
            storage_mod.UserStorage(store_file=str(LOCK_STORE))
        except storage_mod.StoreLockError:
            pass
        else:
            raise AssertionError("second process acquired the user-store lock")
    finally:
        child.terminate()
        child.wait(timeout=5)
    print("PASS nextgen lock: one writer across processes")


def test_catalog_revisions_and_lazy_groups() -> None:
    service = CatalogService(db_path=CATALOG_CASE_DB, page_size=40)
    items = [video("archivebate" if index % 2 == 0 else "camwhores", str(index), "big author" if index < 60 else f"model{index}", 10000 - index) for index in range(120)]
    service.import_items(items, revision=1, complete=True)
    first = service.query_page(page=1, revision=1)
    assert first["video_count"] == 120 and first["page_count"] == 3 and len(first["items"]) == 40
    ab_count = service.query_page(source="only-archivebate", revision=1)["video_count"]
    cw_count = service.query_page(source="only-camwhores", revision=1)["video_count"]
    assert ab_count + cw_count == 120
    grouped = service.query_page(group_authors=True, revision=1)
    big_leader = next(item for item in grouped["items"] if item.get("username") == "big author")
    assert big_leader["group_members_lazy"] is True and big_leader["grouped_videos"] == []
    members = service.query_group_members("bigauthor", page=2, page_size=25, revision=1)
    assert members["total"] == 60 and members["count"] == 25 and members["has_more"] is True
    local = service.search_local("big author", page=1, page_size=25, revision=1)
    assert local["scope"] == "local_catalog" and local["total"] == 60 and local["network_media_may_be_required"] is False
    assert service.search_local("%", page=1, page_size=25, revision=1)["total"] == 0
    before = service.query_page(page=1, revision=1)
    service.import_items([video("archivebate", "new", "new", 20000)], revision=2, complete=False)
    assert service.query_page(page=1)["catalog_revision"] == 1
    assert service.publish_revision(2) is True
    assert service.query_page(page=1)["catalog_revision"] == 2
    assert service.query_page(page=1, revision=1)["video_count"] == before["video_count"]
    assert service.publish_revision(1) is False

    entered = threading.Event()
    release = threading.Event()

    def hold_reader():
        with service._read_snapshot():
            entered.set()
            release.wait(2)

    reader = threading.Thread(target=hold_reader)
    reader.start()
    assert entered.wait(2)
    try:
        service.close(timeout=0.01)
    except RuntimeError:
        pass
    else:
        raise AssertionError("catalog close did not report an active reader")
    release.set()
    reader.join(timeout=3)
    service.close(timeout=3)
    try:
        service.build_revision_background({})
    except RuntimeError:
        pass
    else:
        raise AssertionError("catalog admitted a new indexing job after shutdown")
    # This is intentionally a real unlink, not an ignored cleanup error: on Windows it
    # proves that worker-thread readers released the SQLite handle before teardown.
    CATALOG_CASE_DB.unlink()
    assert not CATALOG_CASE_DB.exists()
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
            assert calls == [("lifecycle-video", "quick"), ("lifecycle-video", "full")]
            storyboard.demand("lifecycle-video", "consumer", active=False)
            storyboard.shutdown(timeout=3)
            assert storyboard._worker_thread is None

            # A clean shutdown must permit a later explicit start in the same process.
            storyboard.demand("lifecycle-restart", "consumer")
            storyboard.start("lifecycle-restart", 10, "fixture")
            storyboard._jobs.join()
            assert calls[-2:] == [("lifecycle-restart", "quick"), ("lifecycle-restart", "full")]
            storyboard.demand("lifecycle-restart", "consumer", active=False)
    finally:
        try:
            storyboard.shutdown(timeout=3)
        except Exception:
            pass

    supervisor = fast_scan.QuickScanSupervisor()
    started = threading.Event()

    def blocking_scan(cancel_event=None):
        started.set()
        cancel_event.wait(3)

    with patch.object(fast_scan, "run_full_quick_scan", side_effect=blocking_scan):
        first = supervisor.start()
        assert first["accepted"] is True and started.wait(2)
        second = supervisor.start()
        assert second["accepted"] is False and second["status"] == "already_running"
        assert supervisor.stop()["accepted"] is True
        supervisor._thread.join(timeout=3)
        assert supervisor.status()["status"] == "cancelled"
    supervisor.shutdown(timeout=3)
    print("PASS nextgen lifecycle: storyboard restart and idempotent/cancellable profile scan")


def test_typed_feed_and_config() -> None:
    assert FetchResult.error_result("slow", status="timeout").ok is False
    assert FetchResult.error_result("slow", status="timeout").retryable is True
    assert FetchResult.empty(end_reason="confirmed_end").ok is True

    FEED_DIR.mkdir(exist_ok=True)
    original_feed_dir = feed_service.FEED_CACHE_DIR
    feed_service.FEED_CACHE_DIR = FEED_DIR
    try:
        snapshot = feed_service.Snapshot(
            {"test": True},
            {"archivebate": lambda page: FetchResult.error_result("slow", status="timeout", source="archivebate", page=page)},
            lambda values: values,
            force=True,
        )
        outcome = snapshot._read_or_fetch_page("archivebate", 1)
        assert outcome.status == "timeout" and outcome.items == []
        empty_snapshot = feed_service.Snapshot(
            {"empty": True},
            {"archivebate": lambda page: FetchResult.empty(source="archivebate", page=page, end_reason="confirmed_end")},
            lambda values: values,
            force=True,
        )
        empty = empty_snapshot._read_or_fetch_page("archivebate", 1)
        assert empty.status == "confirmed_empty" and empty.ok is True
    finally:
        feed_service.FEED_CACHE_DIR = original_feed_dir

    env_file = AUDIT / f"nextgen_{RUN}.env"
    env_file.write_text('ARCHIVEBATE_EMAIL="person with spaces@example.invalid" # comment\nexport ARCHIVEBATE_PASSWORD=secret#fragment\n', encoding="utf-8")
    try:
        parsed = config._read_env_file(env_file)
        assert parsed == {"ARCHIVEBATE_EMAIL": "person with spaces@example.invalid", "ARCHIVEBATE_PASSWORD": "secret#fragment"}
    finally:
        env_file.unlink(missing_ok=True)
    print("PASS nextgen contracts: timeout is not EOF and quoted .env values parse correctly")


def test_local_mutation_gate() -> None:
    # Importing main is safe here because both process singletons were redirected above.
    import main

    catalog_mod.catalog_service.import_items([video("archivebate", "endpoint-1", "endpoint fixture", 100)], revision=1, complete=True)
    client = TestClient(main.app)
    assert client.post("/api/account/history/clear").status_code == 403
    assert client.post(
        "/api/account/history/clear",
        headers={"X-Archivebate-Mutation-Token": main.LOCAL_MUTATION_TOKEN, "Origin": "https://evil.invalid"},
    ).status_code == 403
    assert client.post(
        "/api/account/history/clear",
        headers={"X-Archivebate-Mutation-Token": main.LOCAL_MUTATION_TOKEN, "Origin": "http://testserver"},
    ).status_code == 200
    accepted = client.post("/api/account/history/clear", headers={"X-Archivebate-Mutation-Token": main.LOCAL_MUTATION_TOKEN})
    assert accepted.status_code == 200 and accepted.json()["success"] is True
    local_response = client.get("/api/search/local", params={"q": "endpoint fixture"})
    assert local_response.status_code == 200 and local_response.json()["scope"] == "local_catalog"
    jobs = client.get("/api/jobs")
    assert jobs.status_code == 200 and {"account_sync", "quick_scan", "deep_archivebate"} <= set(jobs.json()["jobs"])
    diagnostics = client.get("/api/diagnostics")
    assert diagnostics.status_code == 200
    diagnostics_json = diagnostics.json()
    assert diagnostics_json["runtime"]["code_root"] and "store_path" not in diagnostics_json["storage"]
    with patch.object(catalog_mod.catalog_service, "_indexing_progress", {"error": "GET https://provider.invalid/video"}):
        redacted = client.get("/api/diagnostics").json()
        assert redacted["catalog"]["indexing"]["error"] == "GET [redacted-url]"
        assert "provider.invalid" not in json.dumps(redacted)
    public_status = client.get("/api/status")
    assert public_status.status_code == 200 and "store_path" not in public_status.json()["store_health"]
    print("PASS nextgen security: local host/origin/token gate and redacted health")


def main() -> None:
    try:
        test_identity_and_recovery()
        test_cross_process_lock()
        test_catalog_revisions_and_lazy_groups()
        test_background_worker_lifecycle()
        test_typed_feed_and_config()
        test_local_mutation_gate()
    finally:
        try:
            storage_mod.storage.close()
        except Exception:
            pass
        remove_generated_files()


if __name__ == "__main__":
    main()
