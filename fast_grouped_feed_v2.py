"""V4.3 grouped-feed fast path v2.

The grouped home view is latency-sensitive.  The previous implementation still
built a complete per-author summary (GROUP BY + ORDER BY) over the whole
~300k-row catalog before it could return the first 16 cards.  This version does
not materialize the full group list for first paint.

Instead it:
- scans the existing newest-first catalog index until it has the requested
  number of distinct group leaders,
- keeps that leader cursor in a tiny LRU for sequential pages / the 280-card
  follow-up request,
- computes global counts with one narrow aggregate (no raw_json / window rank),
- fetches raw_json only for the leaders actually returned,
- counts members only for those selected authors,
- leaves group members lazy behind the existing group-members endpoint.

Ungrouped views are delegated byte-for-byte to CatalogService.query_page's
original implementation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional

_CACHE_LIMIT = 8
_SCAN_CHUNK = 2048
_cache_lock = threading.RLock()
_projection_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def _clean_names(values) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values or []:
        clean = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def _scope(cs, revision: int, source: str, author_filter: str,
           blocked_models, favorite_authors, favorite_ids) -> Dict[str, Any]:
    clauses = ["revision = ?"]
    params: List[Any] = [revision]

    if source == "only-archivebate":
        clauses.append("source = 'archivebate'")
    elif source == "only-camwhores":
        clauses.append("source = 'camwhores'")

    blocked = _clean_names(blocked_models)
    fav_authors = _clean_names(favorite_authors)
    fav_keys = cs._normalize_favorite_keys(favorite_ids)

    if blocked:
        marks = ",".join("?" for _ in blocked)
        clauses.append(f"author_clean NOT IN ({marks})")
        params.extend(blocked)

    if author_filter == "exclude_fav":
        filters: List[str] = []
        if fav_authors:
            marks = ",".join("?" for _ in fav_authors)
            filters.append(f"author_clean NOT IN ({marks})")
            params.extend(fav_authors)
        if fav_keys:
            by_source: Dict[str, List[str]] = {}
            for key in fav_keys:
                by_source.setdefault(key.source, []).append(key.provider_id)
            for fav_source, ids in by_source.items():
                marks = ",".join("?" for _ in ids)
                filters.append(f"(source != ? OR video_id NOT IN ({marks}))")
                params.extend([fav_source, *ids])
        if filters:
            clauses.append(" AND ".join(filters))

    elif author_filter == "only_fav":
        filters: List[str] = []
        if fav_authors:
            marks = ",".join("?" for _ in fav_authors)
            filters.append(f"author_clean IN ({marks})")
            params.extend(fav_authors)
        if fav_keys:
            marks = ",".join("(?, ?)" for _ in fav_keys)
            filters.append(f"(source, video_id) IN (VALUES {marks})")
            for key in fav_keys:
                params.extend([key.source, key.provider_id])
        clauses.append(f"({' OR '.join(filters)})" if filters else "1 = 0")

    return {
        "where": " AND ".join(clauses),
        "params": params,
        "blocked": blocked,
        "fav_authors": fav_authors,
        "fav_keys": fav_keys,
    }


def _digest(revision: int, updated_at: float, source: str, author_filter: str,
            blocked: List[str], fav_authors: List[str], fav_keys) -> str:
    payload = {
        "revision": int(revision),
        "updated_at": float(updated_at or 0.0),
        "source": source,
        "author_filter": author_filter,
        "blocked": sorted(blocked),
        "fav_authors": sorted(fav_authors),
        "fav_keys": sorted(key.as_string() for key in fav_keys),
    }
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _projection_state(key: str) -> Dict[str, Any]:
    with _cache_lock:
        state = _projection_cache.get(key)
        if state is None:
            state = {
                "lock": threading.RLock(),
                "leaders": [],
                "seen": set(),
                "last_pub": None,
                "last_key": None,
                "exhausted": False,
                "counts": None,
                "base_count": None,
                "post_block_count": None,
            }
            _projection_cache[key] = state
        _projection_cache.move_to_end(key)
        while len(_projection_cache) > _CACHE_LIMIT:
            _projection_cache.popitem(last=False)
        return state


def _group_key(canonical_key: str, author_clean: str) -> str:
    if not author_clean or author_clean == "model":
        return canonical_key
    return author_clean


def _ensure_leaders(conn, state: Dict[str, Any], where: str, params: List[Any], needed: int) -> None:
    """Extend the newest-first unique leader list only as far as the caller needs."""
    with state["lock"]:
        while len(state["leaders"]) < needed and not state["exhausted"]:
            cursor_sql = ""
            cursor_params: List[Any] = []
            if state["last_pub"] is not None:
                cursor_sql = (
                    " AND (published_at < ? OR "
                    "(published_at = ? AND canonical_key > ?))"
                )
                cursor_params = [state["last_pub"], state["last_pub"], state["last_key"]]

            rows = conn.execute(
                f"""
                SELECT canonical_key, author_clean, published_at
                FROM catalog_items
                WHERE {where}{cursor_sql}
                ORDER BY published_at DESC, canonical_key ASC
                LIMIT ?
                """,
                params + cursor_params + [_SCAN_CHUNK],
            ).fetchall()

            if not rows:
                state["exhausted"] = True
                break

            for row in rows:
                canonical = str(row["canonical_key"])
                author = str(row["author_clean"] or "")
                key = _group_key(canonical, author)
                if key not in state["seen"]:
                    state["seen"].add(key)
                    state["leaders"].append({
                        "canonical_key": canonical,
                        "author_clean": author,
                        "group_key": key,
                    })

            last = rows[-1]
            state["last_pub"] = float(last["published_at"] or 0.0)
            state["last_key"] = str(last["canonical_key"])
            if len(rows) < _SCAN_CHUNK:
                state["exhausted"] = True


def _filtered_counts(conn, state: Dict[str, Any], where: str, params: List[Any]) -> Dict[str, int]:
    with state["lock"]:
        if state["counts"] is None:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS videos,
                    COUNT(DISTINCT CASE
                        WHEN author_clean != '' AND author_clean != 'model'
                        THEN author_clean END
                    ) AS normal_groups,
                    COALESCE(SUM(CASE
                        WHEN author_clean = '' OR author_clean = 'model'
                        THEN 1 ELSE 0 END
                    ), 0) AS special_groups
                FROM catalog_items
                WHERE {where}
                """,
                params,
            ).fetchone()
            state["counts"] = {
                "videos": int(row["videos"] or 0),
                "groups": int(row["normal_groups"] or 0) + int(row["special_groups"] or 0),
            }
        return dict(state["counts"])


def _simple_count(conn, where: str, params: List[Any]) -> int:
    return int(conn.execute(
        f"SELECT COUNT(*) AS cnt FROM catalog_items WHERE {where}", params
    ).fetchone()["cnt"] or 0)


def _selected_items(conn, revision: int, leaders: List[Dict[str, Any]],
                    where: str, params: List[Any]) -> List[Dict[str, Any]]:
    if not leaders:
        return []

    canonical_keys = [row["canonical_key"] for row in leaders]
    marks = ",".join("?" for _ in canonical_keys)
    raw_rows = conn.execute(
        f"SELECT canonical_key, raw_json FROM catalog_items "
        f"WHERE revision = ? AND canonical_key IN ({marks})",
        [revision, *canonical_keys],
    ).fetchall()
    raw_by_key = {str(row["canonical_key"]): str(row["raw_json"]) for row in raw_rows}

    normal_authors = sorted({
        row["author_clean"] for row in leaders
        if row["author_clean"] and row["author_clean"] != "model"
    })
    group_counts: Dict[str, int] = {}
    if normal_authors:
        author_marks = ",".join("?" for _ in normal_authors)
        count_rows = conn.execute(
            f"""
            SELECT author_clean, COUNT(*) AS cnt
            FROM catalog_items
            WHERE {where} AND author_clean IN ({author_marks})
            GROUP BY author_clean
            """,
            params + normal_authors,
        ).fetchall()
        group_counts = {str(row["author_clean"]): int(row["cnt"] or 0) for row in count_rows}

    items: List[Dict[str, Any]] = []
    for leader in leaders:
        raw = raw_by_key.get(leader["canonical_key"])
        if raw is None:
            continue
        video = json.loads(raw)
        author = leader["author_clean"]
        count = group_counts.get(author, 1) if author and author != "model" else 1
        video["revision"] = revision
        if count > 1 and author and author != "model":
            video["is_grouped"] = True
            video["group_count"] = count
            video["grouped_videos"] = []
            video["group_members_lazy"] = True
            video["group_members_url"] = f"/api/catalog/groups/{author}/members"
        else:
            video["is_grouped"] = False
            video["group_count"] = 1
            video["grouped_videos"] = [dict(video)]
            video["group_members_lazy"] = False
            video["group_members_url"] = None
        items.append(video)
    return items


def _query_grouped(cs, self, *, page: int = 1, page_size: Optional[int] = None,
                   source: str = "all", author_filter: str = "all",
                   revision: Optional[int] = None, blocked_models=None,
                   favorite_authors=None, favorite_ids=None, enrich_fn=None,
                   item_limit: Optional[int] = None) -> Dict[str, Any]:
    ps = int(page_size or self.page_size)
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

        filtered = _scope(
            cs, rev, source, author_filter,
            blocked_models, favorite_authors, favorite_ids,
        )
        projection_key = _digest(
            rev, updated_at, source, author_filter,
            filtered["blocked"], filtered["fav_authors"], filtered["fav_keys"],
        )
        state = _projection_state(projection_key)

        counts = _filtered_counts(conn, state, filtered["where"], filtered["params"])
        total_videos = counts["videos"]
        total_groups = counts["groups"]
        page_count = max(1, math.ceil(total_groups / ps))

        offset = max(0, (int(page) - 1) * ps)
        limit = materialize_limit or ps
        _ensure_leaders(conn, state, filtered["where"], filtered["params"], offset + limit)
        selected = state["leaders"][offset:offset + limit]
        items = _selected_items(conn, rev, selected, filtered["where"], filtered["params"])

        if enrich_fn:
            items = enrich_fn(items)

        # Base/post-block counters are not on the critical first-16 path.  They
        # are filled exactly by the full 280-card follow-up and then cached.
        if materialize_limit is not None:
            base_count = total_videos
            post_block_count = total_videos
        else:
            with state["lock"]:
                if state["base_count"] is None:
                    base = _scope(cs, rev, source, "all", None, None, None)
                    state["base_count"] = _simple_count(conn, base["where"], base["params"])
                if state["post_block_count"] is None:
                    post = _scope(cs, rev, source, "all", blocked_models, None, None)
                    state["post_block_count"] = _simple_count(conn, post["where"], post["params"])
                base_count = int(state["base_count"])
                post_block_count = int(state["post_block_count"])

    expected_page_items = max(0, min(ps, total_groups - max(0, (int(page) - 1) * ps)))
    page_complete = materialize_limit is None or len(items) >= expected_page_items

    return {
        "catalog_revision": rev,
        "page": int(page),
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
        "has_more": int(page) < page_count,
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
        "v43_grouped_fast_path": 2,
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
    """Install v2 once.  It can safely supersede the older V4.3 wrapper."""
    import catalog_service as cs

    cls = cs.CatalogService
    if getattr(cls, "_v43_grouped_fast_v2_installed", False):
        return

    # If v1 is already installed, its saved original is the real ungrouped
    # implementation.  Otherwise use the current method directly.
    original = getattr(cls, "_v43_grouped_fast_original", cls.query_page)

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
    cls._v43_grouped_fast_v2_installed = True
    cls._v43_grouped_fast_v2_original = original


def clear_cache() -> None:
    with _cache_lock:
        _projection_cache.clear()
