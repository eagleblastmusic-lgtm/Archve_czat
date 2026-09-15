"""Local V4.2 live-soak verifier.

Run this against the real local Archivebite instance after pulling the
v4.2-security-runtime-hardening branch. It is intentionally read-only: it does
not trigger refreshes, mutate account state, or write to the catalog database.

Example:
    python audit/live_soak_v42.py --minutes 10 --base-url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "catalog.db"
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
}


def fail(message: str) -> None:
    raise RuntimeError(message)


def request(base_url: str, path: str, *, timeout: float = 5.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(base_url.rstrip("/") + path, method="GET")
    try:
        with opener.open(req, timeout=timeout) as response:
            return int(response.status), dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), dict(exc.headers.items()), exc.read()


def check_http(base_url: str) -> dict:
    status, headers, body = request(base_url, "/")
    if status != 200:
        fail(f"GET / returned {status}")
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    for key, expected in SECURITY_HEADERS.items():
        if lowered.get(key) != expected:
            fail(f"missing/invalid security header {key}: {lowered.get(key)!r}")
    csp = lowered.get("content-security-policy", "")
    if "default-src 'self'" not in csp or "frame-ancestors 'none'" not in csp:
        fail("Content-Security-Policy is missing expected local-app protections")

    refresh_status, _, _ = request(base_url, "/api/catalog/refresh")
    if refresh_status not in {404, 405}:
        fail(f"mutating catalog refresh is still reachable by GET ({refresh_status})")

    parsed = urlsplit(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=5)
    try:
        conn.request("GET", "/", headers={"Host": "evil.invalid"})
        response = conn.getresponse()
        foreign_host_status = int(response.status)
        response.read()
    finally:
        conn.close()
    if foreign_host_status < 400:
        fail(f"foreign Host header was accepted ({foreign_host_status})")

    return {
        "root_status": status,
        "get_refresh_status": refresh_status,
        "foreign_host_status": foreign_host_status,
        "body_bytes": len(body),
    }


def connect_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        fail(f"catalog database not found: {db_path}")
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def check_db(db_path: Path) -> dict:
    conn = connect_db(db_path)
    try:
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        if str(quick).lower() != "ok":
            fail(f"SQLite quick_check failed: {quick}")

        if not table_exists(conn, "catalog_revisions") or not table_exists(conn, "catalog_items"):
            fail("catalog schema is missing catalog_revisions/catalog_items")

        rows = conn.execute(
            """
            SELECT r.revision, r.complete, r.is_active, r.failed, r.video_count,
                   COUNT(i.canonical_key) AS physical_rows
            FROM catalog_revisions r
            LEFT JOIN catalog_items i ON i.revision = r.revision
            GROUP BY r.revision
            ORDER BY r.revision DESC
            LIMIT 8
            """
        ).fetchall()
        if not rows:
            fail("catalog has no revisions")

        complete = [row for row in rows if int(row["complete"] or 0) == 1 and int(row["failed"] or 0) == 0]
        active = [row for row in complete if int(row["is_active"] or 0) == 1]
        if len(active) != 1:
            fail(f"expected exactly one active healthy revision, found {len(active)}")
        if len(complete) > 2:
            fail(f"more than two healthy complete revisions retained in recent set: {len(complete)}")

        for row in complete:
            expected = int(row["video_count"] or 0)
            physical = int(row["physical_rows"] or 0)
            if expected != physical:
                fail(f"revision {row['revision']} video_count={expected} but physical_rows={physical}")

        incomplete = conn.execute(
            "SELECT COUNT(*) FROM catalog_revisions WHERE complete=0 AND failed=0"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM catalog_revisions WHERE failed=1"
        ).fetchone()[0]

        total_items = int(conn.execute("SELECT COUNT(*) FROM catalog_items").fetchone()[0])
        complete_total = int(sum(int(row["physical_rows"] or 0) for row in complete))
        if incomplete == 0 and failed == 0 and total_items != complete_total:
            fail(
                f"catalog_items contains unexpected rows: total={total_items}, "
                f"healthy_complete_rows={complete_total}"
            )

        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        freelist = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])

        deep_items = None
        if table_exists(conn, "archivebate_deep_items"):
            deep_items = int(conn.execute("SELECT COUNT(*) FROM archivebate_deep_items").fetchone()[0])

        queue = {}
        if table_exists(conn, "archivebate_deep_discovery_queue"):
            cols = {row[1] for row in conn.execute("PRAGMA table_info(archivebate_deep_discovery_queue)")}
            if "status" in cols:
                for row in conn.execute(
                    "SELECT COALESCE(status, 'NULL') AS status, COUNT(*) AS n "
                    "FROM archivebate_deep_discovery_queue GROUP BY status ORDER BY status"
                ):
                    queue[str(row["status"])] = int(row["n"])

        return {
            "active_revision": int(active[0]["revision"]),
            "active_rows": int(active[0]["physical_rows"] or 0),
            "healthy_complete_revisions": len(complete),
            "incomplete_revisions": int(incomplete),
            "failed_revisions": int(failed),
            "catalog_items": total_items,
            "deep_items": deep_items,
            "deep_queue": queue,
            "allocated_mb": round(page_count * page_size / 1024 / 1024, 1),
            "freelist_mb": round(freelist * page_size / 1024 / 1024, 1),
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only V4.2 local live-soak verifier")
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--db",
        default=os.getenv("ARCHIVEBATE_CATALOG_DB", str(DEFAULT_DB)),
        help="Path to catalog.db (defaults to ARCHIVEBATE_CATALOG_DB or data/catalog.db)",
    )
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    deadline = time.monotonic() + max(0.0, args.minutes) * 60.0
    interval = max(1.0, args.interval)
    samples = []

    print(f"V4.2 live soak: {args.minutes:g} min, interval {interval:g}s")
    print(f"HTTP: {args.base_url}")
    print(f"DB:   {db_path}")

    while True:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        http_state = check_http(args.base_url)
        db_state = check_db(db_path)
        sample = {"time": stamp, "http": http_state, "db": db_state}
        samples.append(sample)
        print(json.dumps(sample, ensure_ascii=False, sort_keys=True))

        if time.monotonic() >= deadline:
            break
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))

    first = samples[0]["db"]
    last = samples[-1]["db"]
    active_growth = int(last["active_rows"]) - int(first["active_rows"])
    db_growth = float(last["allocated_mb"]) - float(first["allocated_mb"])
    free_growth = float(last["freelist_mb"]) - float(first["freelist_mb"])

    print(
        "PASS V4.2 LIVE SOAK | "
        f"samples={len(samples)} active_rev={last['active_revision']} "
        f"active_rows_delta={active_growth:+d} allocated_mb_delta={db_growth:+.1f} "
        f"freelist_mb_delta={free_growth:+.1f}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL V4.2 LIVE SOAK: {exc}", file=sys.stderr)
        raise SystemExit(1)
