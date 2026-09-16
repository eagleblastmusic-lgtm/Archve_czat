"""V4.3 grouped-feed fast path.

The normal catalog query is intentionally kept untouched for ungrouped views.  This
module installs a narrow grouped-mode replacement that avoids carrying ``raw_json``
through a full-catalog window function and caches the small per-author summary for a
stable catalog/filter projection.  Group members are loaded lazily by the existing
UI endpoint instead of inflating the 280-card feed response.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

_SUMMARY_CACHE_LIMIT = 8
_summary_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_summary_cache_lock = threading.RLock()


def _clean_names(values) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        clean = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _filter_digest(revision: int, updated_at: float, source: str, author_filter: str,
                   blocked: List[str], favorite_authors: List[str], favorite_keys) -> str:
    payload = {
        "revision": int(revision),
        "updated_at": float(updated_at or 0.0),
        "source": source,
        "author_filter": author_filter,
        "blocked": sorted(blocked),
        "favorite_authors": sorted(favorite_authors),
        "favorite_keys": sorted(key.as_string() for key in favorite_keys),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    with _summary_cache_lock:
        value = _summary_cache.get(key)
        if value is None:
            return None
        _summary_cache.move_to_end(key)
        return value


def _cache_put(key: str, value: Dict[str, Any]) -> None:
    with _summary_cache_lock:
        _summary_cache[key] = value
        _summary_cache.move_to_end(key)
        while len(_summary_cache) > _SUMMARY_CACHE_LIMIT:
            _summary_cache.popitem(last=False)


def _build_scope(cs, revision: int, source: str, author_filter: str,
                 blocked_models, favorite_authors, favorite_ids, alias: str = ""):
    p = f"{alias}." if alias else ""
    base_clauses = [f"{p}revision = ?"]
    base_params: List[Any] = [revision]
    if source == "only-archivebate":
        base_clauses.append(f"{p}source = 'archivebate'")
    elif source == "only-camwhores":
        base_clauses.append(f"{p}source = 'camwhores'")

    blocked = _clean_names(blocked_models)
    fav_authors = _clean_names(favorite_authors)
    fav_keys = cs._normalize_favorite_keys(favorite_ids)

    clauses = list(base_clauses)
    params: List[Any] = list(base_params)
    if blocked:
        placeholders = ",".join("?" for _ in blocked)
        clauses.append(f"{p}author_clean NOT IN ({placeholders})")
        params.extend(blocked)

    post_block_clauses = list(clauses)
    post_block_params = list(params)

    if author_filter == "exclude_fav":
        conds: List[str] = []
        if fav_authors:
            placeholders = ",".join("?" for _ in fav_authors)
            conds.append(f"{p}author_clean NOT IN ({placeholders})")
            params.extend(fav_authors)
        if fav_keys:
            ids_by_source: Dict[str, List[str]] = {}
            for key in fav_keys:
                ids_by_source.setdefault(key.source, []).append(key.provider_id)
            for fav_source, ids in ids_by_source.items():
                placeholders = ",".join("?" for _ in ids)
                conds.append(f"({p}source != ? OR {p}video_id NOT IN ({placeholders}))")
                params.extend([fav_source, *ids])
        if conds:
            clauses.append(" AND ".join(conds))
    elif author_filter == "only_fav":
        conds: List[str] = []
        if fav_authors:
            placeholders = ",".join("?" for _ in fav_authors)
            conds.append(f"{p}author_clean IN ({placeholders})")
            params.extend(fav_authors)
        if fav_keys:
            placeholders = ",".join("(?, ?)" for _ in fav_keys)
            conds.append(f"({p}source, {p}video_id) IN (VALUES {placeholders})")
            for key in fav_keys:
                params.extend([key.source, key.provider_id])
        clauses.append(f"({' OR '.join(conds)})" if conds else "1 = 0")

    return {
        "base_sql": " AND ".join(base_clauses),
        "base_params": base_params,
        "post_block_sql": " AND ".join(post_block_clauses),
        "post_block_params": post_block_params,
        "where_sql": " AND ".join(clauses),
        "params": params,
        "blocked": blocked,
        "favorite_authors": fav_authors,
        "favorite_keys": fav_keys,
    }


def _summary_rows(conn, where_sql: str, params: List[Any]) -> List[Dict[str, Any]]:
    # The old grouped query ranked every catalog row twice and carried raw_json
    # through the window.  Aggregate only narrow indexed columns here; raw JSON is
    # fetched solely for the 16/280 leaders that are actually returned.
    group_key = "CASE WHEN author_clean = '' OR author_clean = 'model' THEN canonical_key ELSE author_clean END"
    rows = conn.execute(
        f"""
        SELECT {group_key} AS group_key,
               MAX(CASE WHEN author_clean = '' OR author_clean = 'model' THEN 1 ELSE 0 END) AS special,
               COUNT(*) AS grp_cnt,
               MAX(published_at) AS pub
        FROM catalog_items
        WHERE {where_sql}
        GROUP BY {group_key}
        ORDER BY pub DESC, group_key ASC
        """,
        params,
    ).fetchall()
    return [
        {
            "group_key": str(row["group_key"]),
            "special": bool(row["special"]),
            "grp_cnt": int(row["grp_cnt"]),
            "pub": float(row["pub"] or 0.0),
        }
        for row in rows
    ]


def _leader_rows(conn, scope, page_groups: List[Dict[str, Any]], revision: int) -> List[Dict[str, Any]]:
    if not page_groups:
        return []

    by_order: Dict[int, Dict[str, Any]] = {}
    normal = [(idx, row) for idx, row in enumerate(page_groups) if not row["special"]]
    special = [(idx, row) for idx, row in enumerate(page_groups) if row["special"]]

    if normal:
        values_sql = ",".join("(?, ?, ?, ?)" for _ in normal)
        values_params: List[Any] = []
        for idx, row in normal:
            values_params.extend([idx, row["group_key"], row["pub"], row["grp_cnt"]])

        aliased = _build_scope(
            __import__("catalog_service"),
            revision,
            scope["source"],
            scope["author_filter"],
            scope["blocked_models"],
            scope["favorite_authors_input"],
            scope["favorite_ids"],
            alias="c",
        )
        normal_rows = conn.execute(
            f"""
            WITH selected(ord, group_key, pub, grp_cnt) AS (VALUES {values_sql}),
            keys AS (
                SELECT s.ord, s.grp_cnt, MIN(c.canonical_key) AS leader_key
                FROM selected s
                JOIN catalog_items c
                  ON c.revision = ?
                 AND c.author_clean = s.group_key
                 AND c.published_at = s.pub
                WHERE {aliased['where_sql']}
                GROUP BY s.ord, s.grp_cnt
            )
            SELECT k.ord, k.grp_cnt, c.raw_json, c.canonical_key, c.author_clean
            FROM keys k
            JOIN catalog_items c
              ON c.revision = ? AND c.canonical_key = k.leader_key
            ORDER BY k.ord ASC
            """,
            values_params + [revision] + aliased["params"] + [revision],
        ).fetchall()
        for row in normal_rows:
            by_order[int(row["ord"])] = {
                "raw_json": row["raw_json"],
                "canonical_key": row["canonical_key"],
                "author_clean": row["author_clean"],
                "grp_cnt": int(row["grp_cnt"]),
            }

    if special:
        placeholders = ",".join("?" for _ in special)
        special_keys = [row["group_key"] for _, row in special]
        fetched = conn.execute(
            f"SELECT canonical_key, author_clean, raw_json FROM catalog_items "
            f"WHERE revision = ? AND canonical_key IN ({placeholders})",
            [revision, *special_keys],
        ).fetchall()
        fetched_by_key = {str(row["canonical_key"]): row for row in fetched}
        for idx, summary in special:
            row = fetched_by_key.get(summary["group_key"])
            if row is not None:
                by_order[idx] = {
                    "raw_json": row["raw_json"],
                    "canonical_key": row["canonical_key"],
                    "author_clean": row["author_clean"],
                    "grp_cnt": int(summary["grp_cnt"]),
                }

    return [by_order[idx] for idx in sorted(by_order)]


def _query_grouped(cs, self, *, page: int = 1, page_size: Optional[int] = None,
                   source: str = "all", author_filter: str = "all",
                   revision: Optional[int] = None, blocked_models=None,
                   favorite_authors=None, favorite_ids=None, enrich_fn=None,
                   item_limit: Optional[int] = None) -> Dict[str, Any]:
    ps = page_size or self.page_size
    materialize_limit = None if item_limit is None else max(1, min(int(item_limit), ps))

    with self._read_snapshot() as conn:
        rev = revision if revision is not None else self._select_revision_from_conn(conn)
        if rev is None:
            return self._empty_query_page(page, ps)

        rev_info = conn.execute("SELECT * FROM revisions WHERE revision = ?", (rev,)).fetchone()
        is_failed = bool(rev_info["failed"]) if rev_info else False
        is_complete = bool(rev_info["complete"]) and not is_failed if rev_info else False
        revision_error = str(rev_info["error"] or "") if rev_info else ""
        updated_at = float(rev_info["updated_at"] or 0.0) if rev_info else 0.0
        source_error: Dict[str, Any] = {}
        if revision_error:
            try:
                decoded = json.loads(revision_error)
                source_error = decoded if isinstance(decoded, dict) else {"catalog": revision_error}
            except (TypeError, ValueError):
                source_error = {"catalog": revision_error}

        limited_rows = conn.execute(
            "SELECT source, end_reason FROM source_runs WHERE revision = ? "
            "AND (end_reason LIKE 'source_http_limit:%' OR end_reason LIKE 'source_page_limit:%')",
            (rev,),
        ).fetchall()
        limited_sources = {str(r["source"]): str(r["end_reason"] or "") for r in limited_rows}

        base_scope = _build_scope(cs, rev, source, "all", None, None, None)
        post_block_scope = _build_scope(cs, rev, source, "all", blocked_models, None, None)
        filtered_scope = _build_scope(cs, rev, source, author_filter, blocked_models, favorite_authors, favorite_ids)

        base_count = int(conn.execute(
            f"SELECT COUNT(*) AS cnt FROM catalog_items WHERE {base_scope['where_sql']}",
            base_scope["params"],
        ).fetchone()["cnt"] or 0)
        post_block_count = int(conn.execute(
            f"SELECT COUNT(*) AS cnt FROM catalog_items WHERE {post_block_scope['where_sql']}",
            post_block_scope["params"],
        ).fetchone()["cnt"] or 0)

        cache_key = _filter_digest(
            rev, updated_at, source, author_filter,
            filtered_scope["blocked"], filtered_scope["favorite_authors"], filtered_scope["favorite_keys"],
        )
        cached = _cache_get(cache_key)
        if cached is None:
            summaries = _summary_rows(conn, filtered_scope["where_sql"], filtered_scope["params"])
            cached = {
                "summaries": summaries,
                "total_videos": sum(row["grp_cnt"] for row in summaries),
            }
            _cache_put(cache_key, cached)
        summaries = cached["summaries"]
        total_videos = int(cached["total_videos"])
        total_groups = len(summaries)
        page_count = max(1, math.ceil(total_groups / ps))
        offset = max(0, (page - 1) * ps)
        limit = materialize_limit or ps
        selected = summaries[offset:offset + limit]

        leader_scope = {
            "source": source,
            "author_filter": author_filter,
            "blocked_models": blocked_models,
            "favorite_authors_input": favorite_authors,
            "favorite_ids": favorite_ids,
        }
        leader_rows = _leader_rows(conn, leader_scope, selected, rev)

        items: List[Dict[str, Any]] = []
        for row in leader_rows:
            video = json.loads(row["raw_json"])
            grp_cnt = int(row["grp_cnt"])
            author_clean = str(row["author_clean"] or "")
            video["revision"] = rev
            if grp_cnt > 1 and author_clean and author_clean != "model":
                video["is_grouped"] = True
                video["group_count"] = grp_cnt
                # The card UI already supports this lazy contract.  Keeping only the
                # leader makes the full 280-card SSE payload small and avoids another
                # full-catalog member window query on every feed request.
                video["grouped_videos"] = []
                video["group_members_lazy"] = True
                video["group_members_url"] = f"/api/catalog/groups/{author_clean}/members"
            else:
                video["is_grouped"] = False
                video["group_count"] = 1
                video["grouped_videos"] = [dict(video)]
                video["group_members_lazy"] = False
                video["group_members_url"] = None
            items.append(video)

        if enrich_fn:
            items = enrich_fn(items)

    page_total = total_groups
    expected_page_items = max(0, min(ps, page_total - max(0, (page - 1) * ps)))
    page_complete = materialize_limit is None or len(items) >= expected_page_items
    has_more = page < page_count

    return {
        "catalog_revision": rev,
        "page": page,
        "page_size": ps,
        "video_count": total_videos,
        "group_count": total_groups,
        "page_count": page_count,
        "items": items,
        "videos": items,
        "catalog_complete": is_complete,
        "catalog_state": "failed" if is_failed else ("complete" if is_complete else "partial"),
        "updated_at": updated_at,
        "indexing_progress": self._indexing_progress,
        "count": len(items),
        "target_count": ps,
        "page_item_limit": materialize_limit,
        "page_complete": page_complete,
        "known_count": total_groups,
        "has_more": has_more,
        "complete": is_complete,
        "last_page": page_count,
        "total_videos": total_videos,
        "total_is_estimate": not is_complete,
        "snapshot_id": str(rev),
        "revision": rev,
        "source_error": source_error,
        "retryable": bool(source_error) and not is_complete,
        "catalog_limited": bool(limited_sources),
        "limited_sources": limited_sources,
        "projection": {
            "scope": "catalog",
            "revision": rev,
            "preferences_version": None,
            "identity": "source:provider_id",
            "accuracy": "exact" if is_complete else "partial",
            "source": source,
            "grouped": True,
        },
        "counts": {
            "base_count": base_count,
            "visible_count": total_videos,
            "hidden_by_block": max(0, base_count - post_block_count),
            "hidden_by_filter": max(0, post_block_count - total_videos),
            "accuracy": "exact" if is_complete else "partial",
        },
    }


def install() -> None:
    """Install the grouped fast path once for the current Python process."""
    import catalog_service as cs

    cls = cs.CatalogService
    if getattr(cls, "_v43_grouped_fast_installed", False):
        return
    original = cls.query_page

    def query_page(self, page=1, page_size=None, source="all", author_filter="all",
                   group_authors=False, revision=None, blocked_models=None,
                   favorite_authors=None, favorite_ids=None, enrich_fn=None,
                   item_limit=None):
        if not group_authors:
            return original(
                self,
                page=page,
                page_size=page_size,
                source=source,
                author_filter=author_filter,
                group_authors=False,
                revision=revision,
                blocked_models=blocked_models,
                favorite_authors=favorite_authors,
                favorite_ids=favorite_ids,
                enrich_fn=enrich_fn,
                item_limit=item_limit,
            )
        return _query_grouped(
            cs,
            self,
            page=page,
            page_size=page_size,
            source=source,
            author_filter=author_filter,
            revision=revision,
            blocked_models=blocked_models,
            favorite_authors=favorite_authors,
            favorite_ids=favorite_ids,
            enrich_fn=enrich_fn,
            item_limit=item_limit,
        )

    cls.query_page = query_page
    cls._v43_grouped_fast_installed = True
    cls._v43_grouped_fast_original = original


def clear_cache() -> None:
    with _summary_cache_lock:
        _summary_cache.clear()
