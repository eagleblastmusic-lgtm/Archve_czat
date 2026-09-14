"""Regression for Archivebite V4.1 revision concurrency + bounded Deep publication."""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

BOOT = Path(tempfile.mkdtemp(prefix="archivebite_v41_boot_"))
os.environ["ARCHIVEBATE_CATALOG_DB"] = str(BOOT / "bootstrap.db")
os.environ["DEEP_ARCHIVEBATE_PUBLISH_MIN_ITEMS"] = "5"
os.environ["DEEP_ARCHIVEBATE_PUBLISH_INTERVAL_SECONDS"] = "3600"
os.environ["ARCHIVEBATE_CATALOG_REVISIONS_KEEP"] = "2"

from catalog_service import CatalogService, allocate_revision_in_transaction
from deep_archivebate import DeepArchivebateService, deep_archivebate_service


def video(video_id: int, username: str = "model"):
    return {
        "id": str(video_id),
        "source": "archivebate",
        "url": f"https://archivebate.com/watch/{video_id}",
        "username": username,
        "date": "01.01.2020",
        "platform": "Chaturbate",
    }


try:
    deep_archivebate_service.close()
except Exception:
    pass

root = Path(tempfile.mkdtemp(prefix="archivebite_v41_reg_"))
try:
    db = root / "catalog.db"
    cat = CatalogService(db)
    cat.import_items([video(1, "base")], revision=1, complete=True)
    deep = DeepArchivebateService(db, request_delay=0.01)

    assert deep._store_videos_locked([video(i, f"m{i}") for i in range(10, 14)]) == 4
    revisions = deep._get_conn().execute(
        "SELECT revision FROM revisions WHERE complete=1 AND failed=0 ORDER BY revision"
    ).fetchall()
    assert [int(r["revision"]) for r in revisions] == [1], revisions

    assert deep._store_videos_locked([video(14, "m14")]) == 1
    revisions = deep._get_conn().execute(
        "SELECT revision, is_active FROM revisions WHERE complete=1 AND failed=0 ORDER BY revision"
    ).fetchall()
    assert len(revisions) == 2, revisions
    active = [int(r["revision"]) for r in revisions if r["is_active"]]
    assert len(active) == 1 and active[0] > 1, revisions

    allocated = []
    errors = []
    start_barrier = threading.Barrier(2)

    def allocate():
        conn = sqlite3.connect(str(db), timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            start_barrier.wait(timeout=5)
            conn.execute("BEGIN IMMEDIATE")
            rev = allocate_revision_in_transaction(conn)
            time.sleep(0.05)
            conn.execute("COMMIT")
            allocated.append(rev)
        except Exception as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            errors.append(exc)
        finally:
            conn.close()

    t1 = threading.Thread(target=allocate)
    t2 = threading.Thread(target=allocate)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert not errors, errors
    assert len(allocated) == 2 and len(set(allocated)) == 2, allocated

    conn = deep._get_conn()
    for rev in allocated:
        conn.execute("DELETE FROM revisions WHERE revision=?", (rev,))

    conn.execute("BEGIN IMMEDIATE")
    stale_rev = allocate_revision_in_transaction(conn)
    conn.execute(
        "INSERT INTO source_runs(revision,source,cursor,pages_scanned,items_found,complete,failed,error,updated_at) "
        "VALUES(?, 'archivebate', 1,0,0,0,0,NULL,?)",
        (stale_rev, time.time()),
    )
    conn.execute("COMMIT")
    cat.import_items([video(200, "stale")], revision=stale_rev, complete=False)

    conn.execute("BEGIN IMMEDIATE")
    newer_rev = allocate_revision_in_transaction(conn)
    conn.execute("COMMIT")
    cat.import_items([video(300, "newer")], revision=newer_rev, complete=True)
    assert cat.publish_revision(stale_rev) is False
    assert conn.execute("SELECT 1 FROM revisions WHERE revision=?", (stale_rev,)).fetchone() is None
    assert conn.execute("SELECT COUNT(*) AS c FROM catalog_items WHERE revision=?", (stale_rev,)).fetchone()["c"] == 0

    conn.execute("BEGIN IMMEDIATE")
    orphan_rev = allocate_revision_in_transaction(conn)
    conn.execute(
        "INSERT INTO source_runs(revision,source,cursor,pages_scanned,items_found,complete,failed,error,updated_at) "
        "VALUES(?, 'archivebate', 2,1,1,0,0,NULL,?)",
        (orphan_rev, time.time()),
    )
    conn.execute("COMMIT")
    cat.import_items([video(400, "orphan")], revision=orphan_rev, complete=False)

    conn.execute("BEGIN IMMEDIATE")
    final_rev = allocate_revision_in_transaction(conn)
    conn.execute("COMMIT")
    cat.import_items([video(500, "final")], revision=final_rev, complete=True)
    cat.mark_revision_failed(orphan_rev, "ReadTimeout: source did not return a successful response")
    assert conn.execute("SELECT 1 FROM revisions WHERE revision=?", (orphan_rev,)).fetchone() is None
    assert conn.execute("SELECT COUNT(*) AS c FROM catalog_items WHERE revision=?", (orphan_rev,)).fetchone()["c"] == 0

    deep.close()
    cat.close()
    print("PASS: V4.1 revision allocation, batching and stale/orphan cleanup")
finally:
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(BOOT, ignore_errors=True)
