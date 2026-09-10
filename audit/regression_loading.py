"""Existing-database upgrade and playback cache regression; no provider access."""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
import config

with patch.object(requests.Session, 'request', side_effect=AssertionError('External HTTP disabled')), patch.object(config, 'get_archivebate_credentials', return_value=('', '')):
    import main
    import catalog_service as catalog
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
        db = Path(tmp) / 'legacy.db'
        service = catalog.CatalogService(db)
        service.import_items([{'id': 'old', 'username': 'fixture'}], 1, complete=True)
        service.import_items([{'id': 'published', 'username': 'fixture'}], 2, complete=True)
        service.import_items([{'id': 'partial', 'username': 'fixture'}], 3)
        service.close()
        with sqlite3.connect(db) as conn:
            conn.execute('ALTER TABLE revisions DROP COLUMN is_active')
            before = conn.execute('SELECT * FROM catalog_items ORDER BY revision').fetchall()
        conn.close()
        service = catalog.CatalogService(db)
        assert service.get_active_revision() == 2
        assert service.query_page()['items'][0]['id'] == 'published'
        with sqlite3.connect(db) as conn:
            assert before == conn.execute('SELECT * FROM catalog_items ORDER BY revision').fetchall()
            assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        conn.close()
        with patch.object(catalog, 'catalog_service', service):
            client = TestClient(main.app)
            for url in ('/api/feed', '/api/stats', '/api/blocked_models'):
                assert client.get(url).status_code == 200, url
        service.close()
        service = catalog.CatalogService(db)
        assert service.get_active_revision() == 2, 'Migration must be idempotent'
        service.close()

        # Exact legacy startup state from the report: no revisions or items.
        empty = Path(tmp) / 'empty.db'
        service = catalog.CatalogService(empty)
        service.close()
        with sqlite3.connect(empty) as conn:
            conn.execute('ALTER TABLE revisions DROP COLUMN is_active')
        conn.close()
        service = catalog.CatalogService(empty)
        assert service.get_active_revision() is None
        assert service.query_page()['count'] == 0
        service.close()

    # Exercise real singleflight rather than mocking away the stale-cache bug.
    now = time.time()
    cached = {'id': 'ttl-fixture', 'direct_url': 'https://fixture.invalid/old', 'direct_url_fetched_at': now - 3600}
    fresh = {**cached, 'direct_url': 'https://fixture.invalid/new', 'direct_url_fetched_at': now}
    upstream = MagicMock(status_code=206, headers={'Content-Type': 'video/mp4', 'Content-Range': 'bytes 0-3/4', 'Content-Length': '4'})
    upstream.iter_content.return_value = iter([b'test'])
    with patch.object(main, 'read_json_cache', return_value=(cached, now - 3600)), patch.object(main, '_fetch_and_cache_details', return_value=fresh) as resolve, patch.object(main, '_validated_session_get', return_value=upstream) as get, patch.object(main, 'is_safe_remote_url', return_value=True):
        result = TestClient(main.app).get('/api/video/stream?id=ttl-fixture', headers={'Range': 'bytes=0-3'})
        assert result.status_code == 206 and result.content == b'test'
        resolve.assert_called_once_with('ttl-fixture')
        assert get.call_args.args[1] == fresh['direct_url']

print('PASS: legacy empty/populated migration, preserved items, restart, feed/stats/blocked endpoints, stale URL refreshed before connection')
