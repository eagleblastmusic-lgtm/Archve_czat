from pathlib import Path

p = Path('catalog_service.py')
text = p.read_text(encoding='utf-8')

old = 'ARCHIVEBATE_EMPTY_END_THRESHOLD = 5\n'
new = 'ARCHIVEBATE_EMPTY_END_THRESHOLD = 5\nTRANSIENT_SOURCE_RETRY_DELAYS = (2.0, 8.0)\n'
assert text.count(old) == 1
text = text.replace(old, new, 1)

marker = '''    def _reopen_legacy_archivebate_cap_revision(self) -> Optional[int]:\n'''
insert = '''    @classmethod\n    def _is_recoverable_source_error(cls, error_value: Any) -> bool:\n        \"\"\"Return True for durable source failures that are safe to retry from the same cursor.\"\"\"\n        if cls._is_recoverable_page_limit_error(error_value):\n            return True\n        if not error_value:\n            return False\n        decoded = error_value\n        if isinstance(error_value, str):\n            try:\n                decoded = json.loads(error_value)\n            except (TypeError, ValueError, json.JSONDecodeError):\n                decoded = error_value\n\n        def recoverable(value: Any) -> bool:\n            text = str(value or \"\").lower()\n            transient_fragments = (\n                \"source did not return a successful response\",\n                \"read timed out\",\n                \"readtimeout\",\n                \"connecttimeout\",\n                \"connectionerror\",\n                \"max retries exceeded\",\n                \"remotedisconnected\",\n                \"connection reset\",\n                \"temporarily unavailable\",\n                \"too many requests\",\n            )\n            if any(fragment in text for fragment in transient_fragments):\n                return True\n            if re.search(r\"\\b429\\b\", text):\n                return True\n            return bool(re.search(r\"\\b5\\d\\d\\b\", text)) and any(\n                token in text for token in (\"http\", \"server\", \"response\", \"status\")\n            )\n\n        if isinstance(decoded, dict) and decoded:\n            return all(recoverable(value) for value in decoded.values())\n        return recoverable(decoded)\n\n'''
assert text.count(marker) == 1
text = text.replace(marker, insert + marker, 1)

text = text.replace(
    'if not bool(row["failed"]) or self._is_recoverable_page_limit_error(row["error"]):',
    'if not bool(row["failed"]) or self._is_recoverable_source_error(row["error"]):',
    1,
)
text = text.replace(
    'if not self._is_recoverable_page_limit_error(rev_row["error"]):',
    'if not self._is_recoverable_source_error(rev_row["error"]):',
    1,
)

old = '        consecutive_empty_pages: Dict[str, int] = {s: 0 for s in sources}\n'
new = old + '        transient_retry_counts: Dict[str, int] = {s: 0 for s in sources}\n'
assert text.count(old) == 1
text = text.replace(old, new, 1)

old = '''                    # Other errors remain real source failures and must not be confused with EOF.\n                    errors[source] = str(fetch_error)\n                    with self._lock:\n                        conn = self._get_conn()\n                        conn.execute(\n                            \"UPDATE source_runs SET failed = 1, complete = 0, error = ?, updated_at = ? WHERE revision = ? AND source = ?\",\n                            (str(fetch_error), time.time(), revision, source),\n                        )\n                    continue\n\n                if not batch:\n'''
new = '''                    error_text = str(fetch_error)\n                    if self._is_recoverable_source_error(error_text):\n                        retry_count = transient_retry_counts[source]\n                        if retry_count < len(TRANSIENT_SOURCE_RETRY_DELAYS):\n                            delay = float(TRANSIENT_SOURCE_RETRY_DELAYS[retry_count])\n                            transient_retry_counts[source] = retry_count + 1\n                            with self._lock:\n                                conn = self._get_conn()\n                                conn.execute(\n                                    \"UPDATE source_runs SET failed = 0, complete = 0, error = ?, \"\n                                    \"end_reason = 'transient_retry_pending', updated_at = ? \"\n                                    \"WHERE revision = ? AND source = ?\",\n                                    (error_text, time.time(), revision, source),\n                                )\n                                self._indexing_progress[\"source_progress\"][source] = {\n                                    \"cursor\": page,\n                                    \"items\": source_counts[source],\n                                    \"complete\": False,\n                                    \"retrying\": True,\n                                    \"retry_count\": transient_retry_counts[source],\n                                    \"retry_delay\": delay,\n                                    \"error\": error_text,\n                                }\n                            if self._indexing_stop.wait(delay):\n                                break\n                            continue\n\n                    # Non-transient errors, or transient errors exhausted in this process, are\n                    # persisted as failed. Recoverable transient failures can still resume from\n                    # the same durable cursor on the next application start.\n                    errors[source] = error_text\n                    with self._lock:\n                        conn = self._get_conn()\n                        conn.execute(\n                            \"UPDATE source_runs SET failed = 1, complete = 0, error = ?, updated_at = ? WHERE revision = ? AND source = ?\",\n                            (error_text, time.time(), revision, source),\n                        )\n                    continue\n\n                transient_retry_counts[source] = 0\n\n                if not batch:\n'''
assert text.count(old) == 1
text = text.replace(old, new, 1)

old = '''                    conn.execute(\n                        \"UPDATE source_runs SET cursor = ?, pages_scanned = pages_scanned + 1, items_found = items_found + ?, updated_at = ? WHERE revision = ? AND source = ?\",\n                        (cursors[source], len(batch), time.time(), revision, source),\n                    )\n'''
new = '''                    conn.execute(\n                        \"UPDATE source_runs SET cursor = ?, pages_scanned = pages_scanned + 1, items_found = items_found + ?, \"\n                        \"failed = 0, error = NULL, end_reason = NULL, updated_at = ? WHERE revision = ? AND source = ?\",\n                        (cursors[source], len(batch), time.time(), revision, source),\n                    )\n'''
assert text.count(old) == 1
text = text.replace(old, new, 1)

p.write_text(text, encoding='utf-8')

reg = Path('audit/regression_catalog_transient_resume.py')
reg.write_text(r'''import json
import tempfile
from pathlib import Path

from catalog_service import CatalogService


def video(video_id):
    return {
        "id": str(video_id),
        "url": f"https://www.camwhores.tv/videos/{video_id}/x/",
        "username": "resume_model",
        "date": "1 day ago",
        "source": "camwhores",
        "platform": "Camwhores.tv",
    }


with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "catalog.db"
    service = CatalogService(db)
    conn = service._get_conn()
    now = 1.0
    conn.execute("INSERT INTO revisions(revision,created_at,updated_at,complete,is_active,failed,video_count,error) VALUES(20,?,?,1,1,0,1,NULL)", (now, now))
    conn.execute("INSERT INTO revisions(revision,created_at,updated_at,complete,is_active,failed,video_count,error) VALUES(23,?,?,0,0,1,10,?)", (now, now, json.dumps({"camwhores":"Source did not return a successful response"})))
    conn.execute("INSERT INTO source_runs(revision,source,cursor,pages_scanned,items_found,complete,failed,error,end_reason,updated_at) VALUES(23,'archivebate',1001,1000,100,1,0,NULL,'source_page_limit:empty:1001',?)", (now,))
    conn.execute("INSERT INTO source_runs(revision,source,cursor,pages_scanned,items_found,complete,failed,error,end_reason,updated_at) VALUES(23,'camwhores',461,460,24681,0,1,'Source did not return a successful response',NULL,?)", (now,))

    assert service.get_resumable_revision() == 23

    calls = []
    def archivebate(page):
        raise AssertionError('completed Archivebate source must not be fetched')

    def camwhores(page):
        calls.append(page)
        if page == 461:
            return [video(999001)]
        return []

    rev = service.build_revision_background({'archivebate': archivebate, 'camwhores': camwhores})
    assert rev == 23
    service._indexing_thread.join(timeout=10)
    assert not service._indexing_thread.is_alive()
    assert calls == [461, 462, 463, 464, 465, 466], calls

    row = conn.execute("SELECT complete,is_active,failed,error FROM revisions WHERE revision=23").fetchone()
    assert dict(row) == {'complete': 1, 'is_active': 1, 'failed': 0, 'error': None}, dict(row)
    src = conn.execute("SELECT cursor,pages_scanned,complete,failed,error,end_reason FROM source_runs WHERE revision=23 AND source='camwhores'").fetchone()
    assert int(src['cursor']) == 467, dict(src)
    assert int(src['complete']) == 1 and int(src['failed']) == 0 and src['error'] is None, dict(src)
    assert str(src['end_reason']).startswith('consecutive_empty_pages:5:'), dict(src)
    service.close()

print('PASS: transient Camwhores failure resumes revision from the same durable cursor')
''', encoding='utf-8')

ci = Path('.github/workflows/ci.yml')
ci_text = ci.read_text(encoding='utf-8')
needle = '''      - name: Catalog durable resume regression\n        run: python audit/regression_catalog_resume.py\n'''
addition = needle + '''      - name: Catalog transient source resume regression\n        run: python audit/regression_catalog_transient_resume.py\n'''
assert ci_text.count(needle) == 1
ci.write_text(ci_text.replace(needle, addition, 1), encoding='utf-8')
