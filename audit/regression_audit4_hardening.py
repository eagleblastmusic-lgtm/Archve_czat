"""Audit-4 regression gates (isolated data, deterministic concurrency).

Covers scoped cancel, legacy FFmpeg epoch fencing, expired lease replays,
Deep auth recovery / discovery retries / fairness, and QUICK cache protection.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
import tempfile
import threading
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
import cache_store
import deep_archivebate
from deep_archivebate import DeepArchivebateService
from fetch_contract import FetchResult
import main
import player_qos_runtime as qos
import storyboard_service as legacy
import timeline_storyboard as ts
from video_identity import VideoKey


class FakeProcess:
    def __init__(self):
        self.terminated = False
    def poll(self):
        return 0 if self.terminated else None
    def terminate(self):
        self.terminated = True
    def wait(self, timeout=0):
        return 0
    def kill(self):
        self.terminated = True


def test_unscoped_cancel_cannot_touch_new_lease():
    from runtime_app import app
    key = VideoKey("archivebate", "audit4_cancel_scope")
    lease = ts.lease_manager.acquire(key, consumer_id="audit4_c", session_id="audit4_c_s1")
    assert lease.ok
    with TestClient(app) as client:
        response = client.post(
            "/api/runtime/v452/storyboard/cancel?id=audit4_cancel_scope&source=archivebate",
            headers={"X-Archivebate-Mutation-Token": main.LOCAL_MUTATION_TOKEN},
        )
        assert response.status_code == 200, response.text
        assert response.json()["cancelled"] == 0, response.json()
        assert not response.json()["ok"], response.json()
        assert ts.lease_manager.has_live_lease(key)
    ts.lease_manager.release(key, consumer_id="audit4_c", session_id="audit4_c_s1")


def test_delayed_a_cancel_never_kills_legacy_b():
    key = VideoKey("archivebate", "audit4_legacy_epoch")
    manager = ts.lease_manager
    a = manager.acquire(key, consumer_id="audit4_legacy", session_id="audit4_legacy_s1")
    assert a.ok
    manager.release(key, consumer_id="audit4_legacy", session_id="audit4_legacy_s1")
    entered = threading.Event()
    allow = threading.Event()
    old_mark = qos._mark_processes_preempted
    def paused_mark(**kwargs):
        entered.set()
        assert allow.wait(3)
        return old_mark(**kwargs)
    proc = FakeProcess()
    process_key = "audit4_legacy_process"
    results = []
    try:
        with patch.object(qos, "_mark_processes_preempted", side_effect=paused_mark):
            t = threading.Thread(target=lambda: results.append(
                qos.hard_cancel(key.provider_id, source=key.source, generation=a.epoch, clear_lease=False)))
            t.start()
            assert entered.wait(3)
            b = manager.acquire(key, consumer_id="audit4_legacy", session_id="audit4_legacy_s2")
            assert b.ok and b.epoch > a.epoch
            with legacy._active_process_lock:
                legacy._active_processes[process_key] = {
                    "video_key": legacy._key(key.provider_id), "qos_generation": b.epoch,
                    "proc": proc, "kind": "quick",
                }
            allow.set()
            t.join(4)
            assert not t.is_alive()
            assert not proc.terminated, "Session B was killed by stale A cancel"
    finally:
        allow.set()
        with legacy._active_process_lock:
            legacy._active_processes.pop(process_key, None)
            legacy._preempted_processes.discard(process_key)
        manager.release(key, consumer_id="audit4_legacy", session_id="audit4_legacy_s2")


def test_expired_sid_never_resurrects():
    manager = ts.ConsumerLeaseManager()
    key = VideoKey("archivebate", "audit4_expired")
    a = manager.acquire(key, consumer_id="audit4_c", session_id="audit4_c_s1")
    assert a.ok
    with manager._lock:
        manager._leases[key.as_string()]["audit4_c"]["expires"] = 0
        manager._last_prune = -100000
    b = manager.acquire(key, consumer_id="audit4_c", session_id="audit4_c_s2")
    assert b.ok and b.epoch > a.epoch
    stale = manager.acquire(key, consumer_id="audit4_c", session_id="audit4_c_s1")
    assert not stale.ok
    assert manager.diagnostic_snapshot(key)["details"]["audit4_c"]["session_id"] == "audit4_c_s2"


def test_deep_auth_resume_and_separate_scraper():
    with tempfile.TemporaryDirectory() as tmp:
        deep = DeepArchivebateService(db_path=Path(tmp) / "catalog.db")
        conn = deep._get_conn()
        now = time.time()
        conn.execute(
            "INSERT INTO archivebate_models(model_key,username,profile_url,discovered_from,priority,first_seen,updated_at) "
            "VALUES('audit4_auth','audit4_auth','https://archivebate.com/profile/audit4_auth','test',100,?,?)",
            (now,now),
        )
        class Failed:
            def get_archivebate_model_videos_result(self, username, page=1):
                return FetchResult.error_result("403 Forbidden", source="archivebate", page=page)
        class Good:
            def get_archivebate_model_videos_result(self, username, page=1):
                return FetchResult.success([], source="archivebate", page=page)
        deep.crawl_step(Failed())
        row = conn.execute("SELECT crawl_state,crawl_complete FROM archivebate_models WHERE model_key='audit4_auth'").fetchone()
        assert row["crawl_state"] == "auth_blocked" and row["crawl_complete"] == 0, dict(row)
        good = Good()
        info = deep.resume_authenticated(good)
        assert info["models_resumed"] == 1
        assert deep._scraper is good
        deep.crawl_step(good)
        row = conn.execute("SELECT crawl_state,empty_streak FROM archivebate_models WHERE model_key='audit4_auth'").fetchone()
        assert row["crawl_state"] == "pending" and row["empty_streak"] == 1, dict(row)
        deep.close()


def test_discovery_errors_retry_after_budget():
    with tempfile.TemporaryDirectory() as tmp:
        deep = DeepArchivebateService(db_path=Path(tmp) / "catalog.db")
        conn = deep._get_conn()
        conn.execute("INSERT INTO archivebate_discovery_queue(prefix,depth,status,updated_at) VALUES ('audit4',1,'pending',0)")
        class Bad:
            def fetch_search_profiles_result(self, prefix, page=1):
                return FetchResult.error_result("503 upstream unavailable", source="archivebate", page=page)
        for _ in range(9):
            conn.execute("UPDATE archivebate_discovery_queue SET retry_at=0 WHERE prefix='audit4'")
            assert deep.discovery_step(Bad())
        row = conn.execute("SELECT status,attempts,retry_at FROM archivebate_discovery_queue WHERE prefix='audit4'").fetchone()
        assert row["status"] == "retry" and row["retry_at"] > time.time(), dict(row)
        deep.close()


def test_fair_crawler_visits_two_profiles():
    with tempfile.TemporaryDirectory() as tmp:
        deep = DeepArchivebateService(db_path=Path(tmp) / "catalog.db")
        conn = deep._get_conn()
        now = time.time()
        for key, pages, stamp in [("many", 100, now-20), ("new", 0, now-10)]:
            conn.execute(
                "INSERT INTO archivebate_models(model_key,username,profile_url,discovered_from,priority,first_seen,updated_at,pages_scanned) "
                "VALUES(?,?,?,'test',100,?,?,?)",
                (key,key,f"https://archivebate.com/profile/{key}", now,stamp,pages),
            )
        observed = []
        class Good:
            def get_archivebate_model_videos_result(self, username, page=1):
                observed.append(username)
                return FetchResult.success([], source="archivebate", page=page)
        scraper = Good()
        deep.crawl_step(scraper)
        deep.crawl_step(scraper)
        assert observed == ["many","new"], observed
        deep.close()


def test_root_quick_file_protected():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        protected = cache_store.safe_cache_key("audit4_working_quick")
        old = cache_store.safe_cache_key("audit4_idle_quick")
        for k in (protected,old):
            (root / (k + ".v10.quick.123.jpg")).write_bytes(b"x"*128)
        stats = cache_store.trim_storyboard_cache_directory(root, max_bytes=145, protected_root_keys={protected})
        assert (root / (protected + ".v10.quick.123.jpg")).exists()
        assert not (root / (old + ".v10.quick.123.jpg")).exists()
        assert stats["remaining_bytes"] <= 145


if __name__ == "__main__":
    tests = [
        test_unscoped_cancel_cannot_touch_new_lease,
        test_delayed_a_cancel_never_kills_legacy_b,
        test_expired_sid_never_resurrects,
        test_deep_auth_resume_and_separate_scraper,
        test_discovery_errors_retry_after_budget,
        test_fair_crawler_visits_two_profiles,
        test_root_quick_file_protected,
    ]
    for fn in tests:
        fn()
        print("PASS",fn.__name__,flush=True)
    print(f"PASS {len(tests)}/{len(tests)} audit-4 regressions",flush=True)
