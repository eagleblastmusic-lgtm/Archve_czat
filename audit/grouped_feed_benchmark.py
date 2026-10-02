"""Representative grouped-feed/index benchmark for an isolated source copy.

Invoke via ``python audit/regression_v43_grouped_fast.py --benchmark-440k``.
The guard prevents importing the app singleton or writing fixtures in a user's
checkout; the isolated regression runner sets the opt-in environment variable.
"""
from __future__ import annotations

import bisect
import json
import os
from pathlib import Path
import platform
import random
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def distribution_sample(rng, cdf):
    return bisect.bisect_left(cdf, rng.random())


def summary(samples):
    values = [round(float(value), 3) for value in samples]
    ordered = sorted(values)
    p50 = ordered[(len(ordered) - 1) // 2]
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95 + 0.999999) - 1)]
    return {"samples_ms": values, "p50_ms": p50, "p95_ms": p95}


def state():
    return {
        "lock": threading.RLock(), "leaders": [], "seen": set(),
        "last_pub": None, "last_key": None, "exhausted": False,
        "counts": None, "base_count": None, "post_block_count": None,
    }


def run_benchmark(row_count: int = 440_000, samples: int = 7):
    if os.environ.get("ARCHIVEBATE_AUDIT_ISOLATED") != "1":
        raise SystemExit(
            "Refusing benchmark outside the temporary regression checkout; "
            "run through audit/implementation_runner.py."
        )

    import catalog_service as catalog_module
    import fast_grouped_feed_v2 as fast
    from catalog_service import CatalogService

    rng = random.Random(20260924)
    author_count = 70_000
    weights = [1.0 / ((rank + 1) ** 1.08) for rank in range(author_count)]
    weight_total = sum(weights)
    cdf = []
    cumulative = 0.0
    for weight in weights:
        cumulative += weight / weight_total
        cdf.append(cumulative)
    cdf[-1] = 1.0

    with tempfile.TemporaryDirectory(prefix="archivebite_440k_") as td:
        db_path = Path(td) / "catalog.db"
        service = CatalogService(db_path=db_path, page_size=280)
        conn = service._get_conn()
        # Measure the candidate against the pre-change index set even when the
        # source copy already contains the production candidate definition.
        conn.execute("DROP INDEX IF EXISTS idx_cat_rev_author_source_id")
        now = time.time()
        conn.execute(
            "INSERT INTO revisions(revision,created_at,updated_at,complete,is_active,failed,video_count) "
            "VALUES(1,?,?,1,1,0,0)", (now, now)
        )
        insert_sql = """INSERT INTO catalog_items(
            canonical_key,source,video_id,author,author_clean,published_at,
            duration_seconds,duration_str,poster,url,preview_video,title,platform,raw_json,revision
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        insert_started = time.perf_counter()
        conn.execute("BEGIN IMMEDIATE")
        batch = []
        for idx in range(row_count):
            source = "archivebate" if idx % 5 < 3 else "camwhores"
            provider_id = f"v{idx:07d}"
            author_no = distribution_sample(rng, cdf)
            author = f"author_{author_no:05d}"
            author_clean = f"author{author_no:05d}"
            published = float(1_800_000_000 - idx)
            poster = f"https://fixture.invalid/{provider_id}.jpg"
            url = f"https://fixture.invalid/video/{provider_id}"
            video = {
                "id": provider_id, "source": source, "username": author,
                "author": author, "published_at": published,
                "date": f"2026-09-{1 + idx % 28:02d}T00:{idx % 60:02d}:00Z",
                "duration": "01:00", "poster": poster, "url": url,
                "title": f"Synthetic clip {idx}",
                "platform": "Camwhores" if source == "camwhores" else "Archivebate",
            }
            batch.append((
                f"{source}:id:{provider_id}", source, provider_id, author,
                author_clean, published, 60.0, "01:00", poster, url, "",
                video["title"], video["platform"],
                json.dumps(video, separators=(",", ":"), ensure_ascii=False),
                1,
            ))
            if len(batch) == 5000:
                conn.executemany(insert_sql, batch)
                batch.clear()
        if batch:
            conn.executemany(insert_sql, batch)
        conn.execute("UPDATE revisions SET video_count=? WHERE revision=1", (row_count,))
        conn.execute("ANALYZE catalog_items")
        conn.commit()
        import_ms = (time.perf_counter() - insert_started) * 1000
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

        blocked = [f"author_{i:05d}" for i in range(1000, 2950)]
        favorite_authors = [f"author_{i:05d}" for i in range(2950, 3250)]
        favorite_ids = [
            {"source": "archivebate", "provider_id": f"v{i:07d}"}
            for i in range(0, min(row_count, 1000), 2)
        ]
        scopes = {
            "exclude_fav_1950_blocked_300_authors_500_ids": {
                "source": "all", "author_filter": "exclude_fav",
                "blocked_models": blocked, "favorite_authors": favorite_authors,
                "favorite_ids": favorite_ids,
            },
            "no_favorites_1950_blocked": {
                "source": "all", "author_filter": "all",
                "blocked_models": blocked, "favorite_authors": [], "favorite_ids": [],
            },
        }
        fast.install()
        original_singleton = catalog_module.catalog_service
        catalog_module.catalog_service = service
        from fastapi.testclient import TestClient
        import main
        import fastapi.routing

        client = TestClient(main.app)
        report = {
            "dataset": {
                "rows": row_count, "sources": dict(conn.execute(
                    "SELECT source,COUNT(*) FROM catalog_items WHERE revision=1 GROUP BY source"
                ).fetchall()),
                "author_count": conn.execute(
                    "SELECT COUNT(DISTINCT author_clean) FROM catalog_items WHERE revision=1"
                ).fetchone()[0],
                "distribution": "Zipf-like exponent=1.08, deterministic seed 20260924",
                "revision": 1,
            },
            "runtime": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version},
            "base_indexes": [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='catalog_items' ORDER BY name"
            )],
            "initial_import_ms": round(import_ms, 3),
            "measurements": {},
            "index_variants": {},
        }

        def measure_components(scope):
            filtered = fast._scope(
                catalog_module, 1, scope["source"], scope["author_filter"],
                scope["blocked_models"], scope["favorite_authors"], scope["favorite_ids"],
            )
            timings = {key: [] for key in (
                "filtered_counts", "ensure_leaders", "selected_items",
                "enrichment", "json_serialization", "worker_dispatch_query",
                "worker_wait",
            )}
            from functools import partial
            from starlette.concurrency import run_in_threadpool

            def query_components():
                local_state = state()
                counts = fast._filtered_counts(conn, local_state, filtered["where"], filtered["params"])
                fast._ensure_leaders(conn, local_state, filtered["where"], filtered["params"], 16)
                leaders = local_state["leaders"][:16]
                items = fast._selected_items(
                    conn, 1, leaders, filtered["where"], filtered["params"],
                    scope["source"], scope["author_filter"],
                )
                return counts, items

            for _ in range(samples):
                local_state = state()
                started = time.perf_counter()
                fast._filtered_counts(conn, local_state, filtered["where"], filtered["params"])
                timings["filtered_counts"].append((time.perf_counter() - started) * 1000)

                local_state = state()
                started = time.perf_counter()
                fast._ensure_leaders(conn, local_state, filtered["where"], filtered["params"], 16)
                timings["ensure_leaders"].append((time.perf_counter() - started) * 1000)
                leaders = local_state["leaders"][:16]

                started = time.perf_counter()
                selected = fast._selected_items(
                    conn, 1, leaders, filtered["where"], filtered["params"],
                    scope["source"], scope["author_filter"],
                )
                timings["selected_items"].append((time.perf_counter() - started) * 1000)

                started = time.perf_counter()
                with patch.object(main.storage, "get_blocked_models", return_value=scope["blocked_models"]), \
                     patch.object(main.storage, "get_favorite_authors", return_value=scope["favorite_authors"]), \
                     patch.object(main.storage, "get_favorite_keys", return_value=scope["favorite_ids"]):
                    enriched = main._enrich_videos(
                        [dict(video) for video in selected],
                        author_filter=scope["author_filter"], source=scope["source"], group_authors="0",
                    )
                timings["enrichment"].append((time.perf_counter() - started) * 1000)

                started = time.perf_counter()
                json.dumps({"videos": enriched}, ensure_ascii=False, separators=(",", ":"))
                timings["json_serialization"].append((time.perf_counter() - started) * 1000)

                started = time.perf_counter()
                asyncio_result = __import__("asyncio").run(run_in_threadpool(query_components))
                timings["worker_dispatch_query"].append((time.perf_counter() - started) * 1000)
                assert asyncio_result[0] == fast._filtered_counts(
                    conn, state(), filtered["where"], filtered["params"]
                )

            # Instrument the same synchronous endpoint that serves /api/feed.
            path = (
                "/api/feed?page=1&source=all&group_authors=1&initial_items=16"
                f"&author_filter={scope['author_filter']}"
            )
            queue_samples = []
            original_worker = fastapi.routing.run_in_threadpool

            async def measured_worker(func, *args, **kwargs):
                submitted_at = time.perf_counter()

                def started_func():
                    queue_samples.append((time.perf_counter() - submitted_at) * 1000)
                    return func(*args, **kwargs)

                return await original_worker(started_func)

            expected = None
            api_samples = []
            with patch.object(main.storage, "get_blocked_models", return_value=scope["blocked_models"]), \
                 patch.object(main.storage, "get_favorite_authors", return_value=scope["favorite_authors"]), \
                 patch.object(main.storage, "get_favorite_keys", return_value=scope["favorite_ids"]), \
                 patch.object(fastapi.routing, "run_in_threadpool", new=measured_worker):
                for sample_index in range(samples):
                    fast.clear_cache()
                    started = time.perf_counter()
                    response = client.get(path)
                    api_samples.append((time.perf_counter() - started) * 1000)
                    if response.status_code != 200:
                        raise AssertionError(f"feed API returned {response.status_code}: {response.text[:500]}")
                    payload = response.json()
                    signature = {
                        "revision": payload.get("catalog_revision"),
                        "video_count": payload.get("video_count"),
                        "group_count": payload.get("group_count"),
                        "page_count": payload.get("page_count"),
                        "counts": payload.get("counts"),
                        "items": [(item.get("id"), item.get("group_count")) for item in payload.get("items", [])],
                    }
                    if expected is None:
                        expected = signature
                    elif signature != expected:
                        raise AssertionError("API counts/order/member leaders changed during same-index samples")
            timings["worker_wait"] = queue_samples
            timings["api_end_to_end"] = api_samples
            timings["signature"] = expected

            explain_sql = "EXPLAIN QUERY PLAN SELECT COUNT(*), " \
                "COUNT(DISTINCT CASE WHEN author_clean != '' AND author_clean != 'model' " \
                "THEN author_clean END), " \
                "COALESCE(SUM(CASE WHEN author_clean = '' OR author_clean = 'model' " \
                "THEN 1 ELSE 0 END),0) FROM catalog_items WHERE " + filtered["where"]
            timings["explain_query_plan"] = [
                str(row["detail"]) for row in conn.execute(explain_sql, filtered["params"])
            ]
            return {key: summary(value) for key, value in timings.items() if isinstance(value, list) and value and isinstance(value[0], (int, float))} | {
                "signature": expected,
                "explain_query_plan": timings["explain_query_plan"],
            }

        baseline_api = {}
        for scope_name, scope in scopes.items():
            metrics = measure_components(scope)
            baseline_api[scope_name] = metrics["signature"]
            report["measurements"][scope_name + ":baseline"] = metrics

        # Candidate variants differ only in leading filter-column order. Each
        # is compared to the exact same baseline table and query semantics.
        variants = {
            "revision_author_source_id": "revision, author_clean, source, video_id",
            "revision_source_author_id": "revision, source, author_clean, video_id",
        }
        base_db_bytes = db_path.stat().st_size if db_path.exists() else 0

        probe_count = min(5000, row_count)
        probe_rows = []
        for idx in range(probe_count):
            provider_id = f"probe{idx:06d}"
            source = "archivebate" if idx % 5 < 3 else "camwhores"
            author = f"probe_{idx % 1200:05d}"
            video = {"id": provider_id, "source": source, "username": author,
                     "published_at": float(1_700_000_000 - idx), "date": f"probe-{idx}"}
            probe_rows.append((
                f"{source}:id:{provider_id}", source, provider_id, author,
                f"probe{idx % 1200:05d}", float(1_700_000_000 - idx), 60.0,
                "01:00", "", "", "", "probe", "synthetic",
                json.dumps(video, separators=(",", ":"), ensure_ascii=False), 2,
            ))

        def measure_probe_import():
            started = time.perf_counter()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO revisions(revision,created_at,updated_at,complete,is_active,failed,video_count) "
                "VALUES(2,?,?,0,0,0,0)", (time.time(), time.time())
            )
            conn.executemany(insert_sql, probe_rows)
            conn.execute("UPDATE revisions SET video_count=? WHERE revision=2", (probe_count,))
            conn.commit()
            elapsed = (time.perf_counter() - started) * 1000
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM catalog_items WHERE revision=2")
            conn.execute("DELETE FROM revisions WHERE revision=2")
            conn.commit()
            return elapsed

        report["incremental_import_probe"] = {
            "rows": probe_count,
            "baseline_ms": round(measure_probe_import(), 3),
            "note": "same 5k synthetic batch inserted/deleted on the isolated 440k database",
        }
        for variant, columns in variants.items():
            index_name = "idx_benchmark_" + variant
            conn.execute(f"DROP INDEX IF EXISTS {index_name}")
            started = time.perf_counter()
            conn.execute(f"CREATE INDEX {index_name} ON catalog_items({columns})")
            index_ms = (time.perf_counter() - started) * 1000
            conn.execute("ANALYZE catalog_items")
            candidate_import_ms = measure_probe_import()
            checkpoint = tuple(conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone())
            index_size = db_path.stat().st_size if db_path.exists() else 0

            variant_result = {
                "index_create_ms": round(index_ms, 3),
                "database_bytes_before": base_db_bytes,
                "database_bytes_after": index_size,
                "database_byte_delta": index_size - base_db_bytes,
                "wal_bytes_after": Path(str(db_path) + "-wal").stat().st_size if Path(str(db_path) + "-wal").exists() else 0,
                "passive_checkpoint": checkpoint,
                "incremental_import_ms": round(candidate_import_ms, 3),
                "scopes": {},
            }
            for scope_name, scope in scopes.items():
                metrics = measure_components(scope)
                if metrics["signature"] != baseline_api[scope_name]:
                    raise AssertionError(f"{variant} changed counts or first-page identity/order for {scope_name}")
                variant_result["scopes"][scope_name] = metrics

            report["index_variants"][variant] = variant_result
            conn.execute(f"DROP INDEX {index_name}")
            conn.execute("ANALYZE catalog_items")

        client.close()
        catalog_module.catalog_service = original_singleton
        service.close()
        try:
            original_singleton.close()
        except Exception:
            pass
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_benchmark()
