from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


catalog = Path("catalog_service.py")

replace_once(
    catalog,
    '''    @staticmethod
    def _is_recoverable_page_limit_error(error_value: Any) -> bool:
        """Recognize revisions failed only because the pre-resume 1000-page cap was hit."""
        if not error_value:
            return False
        decoded = error_value
        if isinstance(error_value, str):
            try:
                decoded = json.loads(error_value)
            except (TypeError, ValueError, json.JSONDecodeError):
                return error_value.startswith("page_limit_exceeded:")
        if isinstance(decoded, dict) and decoded:
            return all(str(value).startswith("page_limit_exceeded:") for value in decoded.values())
        return False
''',
    '''    @staticmethod
    def _is_recoverable_page_limit_error(error_value: Any) -> bool:
        """Recognize legacy cap failures and the page-1001 5xx boundary probe as resumable.

        The page-1001 HTTP failure is retried by the new worker before it is classified as a
        source boundary. Older builds could already have persisted that one failure as terminal,
        so accept it once on upgrade and let the worker perform the qualified boundary probe.
        """
        if not error_value:
            return False
        decoded = error_value
        if isinstance(error_value, str):
            try:
                decoded = json.loads(error_value)
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded = error_value

        def recoverable(value: Any) -> bool:
            text = str(value or "")
            if text.startswith("page_limit_exceeded:"):
                return True
            return bool(re.search(r"\\b5\\d\\d Server Error\\b", text)) and "page=1001" in text

        if isinstance(decoded, dict) and decoded:
            return all(recoverable(value) for value in decoded.values())
        return recoverable(decoded)
''',
    "recoverable page-1001 failure",
)

replace_once(
    catalog,
    '''            source_error = {}
            if is_failed and revision_error:
                try:
                    decoded_error = json.loads(revision_error)
                    source_error = decoded_error if isinstance(decoded_error, dict) else {"catalog": revision_error}
                except (ValueError, TypeError):
                    source_error = {"catalog": revision_error}

            # Build dynamic WHERE clause
''',
    '''            source_error = {}
            if revision_error:
                try:
                    decoded_error = json.loads(revision_error)
                    source_error = decoded_error if isinstance(decoded_error, dict) else {"catalog": revision_error}
                except (ValueError, TypeError):
                    source_error = {"catalog": revision_error}

            limited_rows = conn.execute(
                "SELECT source, end_reason FROM source_runs "
                "WHERE revision = ? AND end_reason LIKE 'source_http_limit:%'",
                (rev,),
            ).fetchall()
            limited_sources = {
                str(row["source"]): str(row["end_reason"] or "")
                for row in limited_rows
            }
            catalog_limited = bool(limited_sources)

            # Build dynamic WHERE clause
''',
    "query limited-source metadata",
)

replace_once(
    catalog,
    '''                "source_error": {},
                "retryable": False,
            }
''',
    '''                "source_error": {},
                "retryable": False,
                "catalog_limited": False,
                "limited_sources": {},
            }
''',
    "empty query limited fields",
)

replace_once(
    catalog,
    '''            "source_error": source_error,
            "retryable": is_failed,
        }
''',
    '''            "source_error": source_error,
            "retryable": bool(source_error) and not is_complete,
            "catalog_limited": catalog_limited,
            "limited_sources": limited_sources,
        }
''',
    "query result limited fields",
)

replace_once(
    catalog,
    '''                try:
                    batch = fetchers[source](page)
                    if not isinstance(batch, list):
                        raise RuntimeError(f"Source {source} returned non-list on page {page}")
                except Exception as exc:
                    # An error on a page does NOT mean end of source; record error and stop this source run
                    errors[source] = str(exc)
                    with self._lock:
                        conn = self._get_conn()
                        conn.execute(
                            "UPDATE source_runs SET failed = 1, error = ?, updated_at = ? WHERE revision = ? AND source = ?",
                            (str(exc), time.time(), revision, source),
                        )
                    continue
''',
    '''                batch = None
                fetch_error = None
                # A single 5xx is not proof of a pagination boundary. Retry the exact page
                # several times before deciding whether this is transient or a stable source cap.
                for attempt in range(1, 5):
                    try:
                        batch = fetchers[source](page)
                        if not isinstance(batch, list):
                            raise RuntimeError(f"Source {source} returned non-list on page {page}")
                        fetch_error = None
                        break
                    except Exception as exc:
                        fetch_error = exc
                        if attempt < 4:
                            time.sleep(0.35 * attempt)

                if fetch_error is not None:
                    response = getattr(fetch_error, "response", None)
                    status_code = getattr(response, "status_code", None)
                    # Archivebate's public home pagination currently accepts 1..1000 and returns
                    # a persistent server error beyond that boundary. Four consecutive 5xx probes
                    # after page 1000 are recorded as an upstream access limit, not as a clean EOF.
                    if (
                        source == "archivebate"
                        and page > 1000
                        and isinstance(status_code, int)
                        and 500 <= status_code < 600
                    ):
                        ended.add(source)
                        reason = f"source_http_limit:{status_code}:{page}"
                        with self._lock:
                            conn = self._get_conn()
                            conn.execute(
                                "UPDATE source_runs SET complete = 1, failed = 0, error = NULL, end_reason = ?, updated_at = ? "
                                "WHERE revision = ? AND source = ?",
                                (reason, time.time(), revision, source),
                            )
                            self._indexing_progress["source_progress"][source] = {
                                "cursor": page,
                                "items": source_counts[source],
                                "complete": True,
                                "limited": True,
                                "limit_page": page - 1,
                                "end_reason": reason,
                            }
                        continue

                    # Other errors remain real source failures and must not be confused with EOF.
                    errors[source] = str(fetch_error)
                    with self._lock:
                        conn = self._get_conn()
                        conn.execute(
                            "UPDATE source_runs SET failed = 1, complete = 0, error = ?, updated_at = ? WHERE revision = ? AND source = ?",
                            (str(fetch_error), time.time(), revision, source),
                        )
                    continue
''',
    "retry and classify page boundary",
)

main = Path("main.py")
replace_once(
    main,
    '''        "blocked_videos": deducted,
        "catalog_complete": res["catalog_complete"],
        "updated_at": res["updated_at"]
    }
''',
    '''        "blocked_videos": deducted,
        "catalog_complete": res["catalog_complete"],
        "catalog_limited": res.get("catalog_limited", False),
        "limited_sources": res.get("limited_sources", {}),
        "updated_at": res["updated_at"]
    }
''',
    "catalog stats limited metadata",
)

replace_once(
    main,
    '''        "catalog_complete": catalog_stats.get("catalog_complete", False),
        "updated_at": catalog_stats.get("updated_at", 0),
        "archivebate_pages": 1000,
''',
    '''        "catalog_complete": catalog_stats.get("catalog_complete", False),
        "catalog_limited": catalog_stats.get("catalog_limited", False),
        "limited_sources": catalog_stats.get("limited_sources", {}),
        "updated_at": catalog_stats.get("updated_at", 0),
        "archivebate_pages": 1000,
''',
    "system stats limited metadata",
)

home_stats = Path("static/home-stats.js")
replace_once(
    home_stats,
    '''    return totalPages
      ? `Gotowe • ${totalPages.toLocaleString('pl-PL')} stron katalogu`
      : 'Gotowe';
''',
    '''    if (data.catalog_limited) {
      return totalPages
        ? `Gotowe do limitu źródła • ${totalPages.toLocaleString('pl-PL')} stron katalogu`
        : 'Gotowe do limitu źródła';
    }

    return totalPages
      ? `Gotowe • ${totalPages.toLocaleString('pl-PL')} stron katalogu`
      : 'Gotowe';
''',
    "limited status label",
)
