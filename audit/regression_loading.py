"""Existing-database upgrade and playback cache regression; no provider access."""
import os
import sqlite3
import sys
import tempfile
import time
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch


# The regression must never allocate a TemporaryDirectory next to the live
# application data.  Use a per-process fixture directory under audit/ instead;
# the runner copies this whole source tree before importing the test.
_FIXTURE_ROOT = Path(__file__).resolve().parent / f"isolated_loading_{os.getpid()}"
_FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)


class _FixedTemporaryDirectory:
    _counter = 0

    def __init__(self, *args, **kwargs):
        del args, kwargs
        self.path = _FIXTURE_ROOT / f"fixture_{type(self)._counter}"
        type(self)._counter += 1
        self.path.mkdir(parents=True, exist_ok=True)

    def __enter__(self):
        return str(self.path)

    def __exit__(self, exc_type, exc, tb):
        return False


tempfile.TemporaryDirectory = _FixedTemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
import config

with patch.object(requests.Session, 'request', side_effect=AssertionError('External HTTP disabled')), patch.object(config, 'get_archivebate_credentials', return_value=('', '')):
    import main
    import catalog_service as catalog
    from fastapi.testclient import TestClient

    # A completed catalog takes the direct SSE branch. Its SQL/enrichment
    # must run off-loop so thumbnail and playback requests can be dispatched.
    async def check_direct_stream_thread():
        loop_thread = threading.get_ident()
        def query(**kwargs):
            assert threading.get_ident() != loop_thread, 'completed feed blocks the event loop'
            return {'items': [], 'catalog_complete': True, 'page_complete': True}
        with patch.object(catalog.catalog_service, 'is_revision_complete', return_value=True), patch.object(catalog.catalog_service, 'query_page', side_effect=query):
            response = main.progressive_feed_stream(
                snapshot_id='1', page=1, revision=1,
                preferences_version=main.storage.preferences_version,
            )
            event = await anext(response.body_iterator)
            assert '"type": "complete"' in event
            await response.body_iterator.aclose()
    asyncio.run(check_direct_stream_thread())

    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
        db = Path(tmp) / 'legacy.db'
        service = catalog.CatalogService(db)
        service.import_items([{'id': 'old', 'username': 'fixture', 'source': 'archivebate'}], 1, complete=True)
        service.import_items([{'id': 'published', 'username': 'fixture', 'source': 'archivebate'}], 2, complete=True)
        service.import_items([{'id': 'partial', 'username': 'fixture', 'source': 'archivebate'}], 3)
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

    # Status is local-only and must stay responsive even when the block
    # projection contains the size seen in the live store. The old per-item
    # scan made each library collection O(items * blocked_models).
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as status_tmp:
        status_store = main.storage.__class__(
            store_file=Path(status_tmp) / 'status.json',
            data_dir=status_tmp,
            acquire_lock=False,
        )
        blocked = [f'blocked_{i}' for i in range(1941)]
        status_store.data['blocked_models'] = blocked
        status_store.data['favorites'] = [
            {'id': f'fav_{i}', 'source': 'archivebate', 'username': 'blocked_0' if i == 0 else f'fav_{i}'}
            for i in range(710)
        ]
        status_store.data['history'] = [
            {'id': f'hist_{i}', 'source': 'archivebate', 'username': 'blocked_1' if i == 0 else f'hist_{i}'}
            for i in range(624)
        ]
        status_store.data['following'] = [
            {'id': f'follow_{i}', 'source': 'archivebate', 'username': f'follow_{i}'}
            for i in range(758)
        ]
        original_store = main.storage
        main.storage = status_store
        try:
            started = time.perf_counter()
            response = TestClient(main.app).get('/api/status')
            elapsed = time.perf_counter() - started
            assert response.status_code == 200
            status_data = response.json()
            assert status_data['favorites_count'] == 709
            assert status_data['history_count'] == 623
            assert status_data['following_count'] == 758
            assert elapsed < 1.0, f'local status filtering is too slow: {elapsed:.3f}s'
        finally:
            main.storage = original_store
            status_store.close()

    # Provider reads use an explicit evidence contract. Network failures,
    # parser misses, CDN/embed failures and generic HTTP responses stay unknown.
    def provider_response(status, body, url='https://archivebate.com/watch/evidence-fixture'):
        response = MagicMock()
        response.status_code = status
        response.text = body
        response.url = url
        return response

    for status, body in ((403, 'Access denied'), (429, 'Rate limited'), (500, 'Origin error'), (404, 'Not Found')):
        main.scraper._details_cache = {}
        with patch.object(main.scraper.session, 'request', return_value=provider_response(status, body)):
            result = main.scraper.get_video_details('evidence-fixture')
        assert result['availability'] == 'unknown', (status, result)
        assert result['retryable'] is True, (status, result)

    # Camwhores details use the same narrow evidence contract through the real
    # provider resolver, including generic HTTP errors and challenge/parser
    # failures that must remain visible as unknown.
    cw_scraper = main.camwhores_scraper
    for status, body, expected, reason in (
        (404, 'This video has been deleted', 'unavailable', 'source_page_not_found'),
        (404, 'Not Found', 'unknown', 'http_404'),
        (429, 'Just a moment... cf-chl- challenge', 'unknown', 'provider_challenge'),
        (200, '<html><body>unrecognized page</body></html>', 'unknown', 'no_direct_stream'),
        (200, '<html><body>Private video. Log in</body></html>', 'private', 'private_marker'),
    ):
        cw_scraper._details_cache = {}
        response = provider_response(status, body, 'https://www.camwhores.tv/videos/fixture/video/')
        with patch.object(cw_scraper.session, 'get', return_value=response):
            actual = cw_scraper.get_video_details('cw_fixture')
        assert actual['availability'] == expected, (status, body, actual)
        assert actual['availability_reason'] == reason, (status, body, actual)
        if expected == 'unavailable':
            assert actual['retryable'] is False
        elif expected == 'unknown':
            assert actual['retryable'] is True

    main.scraper._details_cache = {}
    with patch.object(main.scraper.session, 'request', side_effect=requests.Timeout('timeout')):
        timed_out = main.scraper.get_video_details('timeout-fixture')
    assert timed_out['availability'] == 'unknown' and timed_out['availability_reason'] == 'request_error'

    main.scraper._details_cache = {}
    with patch.object(main.scraper.session, 'request', return_value=provider_response(200, '<html><body>provider shell</body></html>')):
        parser_miss = main.scraper.get_video_details('parser-fixture')
    assert parser_miss['availability'] == 'unknown' and parser_miss['availability_reason'] == 'unrecognized_page'

    main.scraper._details_cache = {}
    with patch.object(main.scraper.session, 'request', side_effect=[
        provider_response(200, '<html><iframe src="https://mixdrop.co/e/embed-only"></iframe></html>'),
        provider_response(503, 'temporary CDN failure', 'https://mixdrop.co/e/embed-only'),
    ]):
        embed_only = main.scraper.get_video_details('embed-fixture')
    assert embed_only['availability'] == 'unknown' and embed_only['availability_reason'] == 'embed_without_direct_stream'

    main.scraper._details_cache = {}
    with patch.object(main.scraper.session, 'request', return_value=provider_response(200, '<html>Private video. Please log in.</html>')):
        private = main.scraper.get_video_details('private-fixture')
    assert private['availability'] == 'private' and private['is_private']

    main.scraper._details_cache = {}
    with patch.object(main.scraper.session, 'request', return_value=provider_response(404, 'This video has been deleted')):
        removed = main.scraper.get_video_details('removed-fixture')
    assert removed['availability'] == 'unavailable'
    assert removed['availability_reason'] == 'source_page_not_found' and removed['retryable'] is False

    # Source watch page can remain HTTP 200 after Mixdrop removes its file.
    missing_body = "<h2>WE ARE SORRY</h2><p>We can&#39;t find the video you are looking for.</p>"
    for status, body, host, missing in [
        (200, missing_body, 'mxdrop.top', True),
        (200, missing_body + '<script>captcha()</script>', 'mxdrop.top', True),
        (404, missing_body, 'mixdrop.ag', True),
        (403, missing_body, 'mxdrop.top', False),
        (503, missing_body, 'mxdrop.top', False),
        (200, missing_body + ' captcha', 'mxdrop.top', False),
        (200, missing_body, 'untrusted.invalid', False),
    ]:
        main.scraper._details_cache = {}
        with patch.object(main.scraper.session, 'request', side_effect=[
            provider_response(200, '<iframe src="https://mixdrop.ag/e/file"></iframe>'),
            provider_response(status, body, f'https://{host}/e/file'),
        ]):
            detail = main.scraper.get_video_details('removed-embed-fixture')
        normalized = main._normalize_video_details('removed-embed-fixture', detail)
        assert (normalized['availability'] == 'unavailable') is missing, normalized
        if missing:
            assert normalized['availability_reason'] == 'embed_file_not_found'
            with patch.object(main, '_read_video_caches', return_value=(None, 0, 0)), \
                 patch.object(main, '_fetch_details_singleflight', return_value=detail):
                main._NO_STREAM_FAILURES.clear()
                response = TestClient(main.app).get('/api/video/stream?id=removed-embed-fixture')
                assert response.status_code == 410, response.text

    assert main._normalize_video_details('legacy-empty', {})['availability'] == 'unknown'
    assert main._normalize_video_details('legacy-embed', {'embed_url': 'https://fixture.invalid/embed'})['availability'] == 'unknown'
    assert main._normalize_video_details('legacy-negative', {'availability': 'unavailable'})['availability'] == 'unknown'
    assert main._normalize_video_details('private-contract', {'is_private': True})['availability'] == 'private'
    assert main._normalize_video_details('direct-contract', {'direct_url': 'https://fixture.invalid/video.mp4'})['availability'] == 'available'
    assert main._normalize_video_details('archive-source-contract', {})['source'] == 'archivebate'
    assert main._normalize_video_details('cw_camwhores-contract', {})['source'] == 'camwhores'
    confirmed = main._normalize_video_details('removed-contract', {
        'availability': 'unavailable', 'availability_reason': 'source_page_not_found',
        'checked_at': time.time(), 'retryable': False,
    })
    assert confirmed['availability'] == 'unavailable'

    # Missing streams retry on the short resolver cooldown; the metadata TTL
    # must not turn one empty read into six hours of certainty.
    main._NO_STREAM_FAILURES.clear()
    cached_no_stream = {'id': 'short-retry', 'availability': 'unknown', 'availability_reason': 'no_direct_stream'}
    now = time.time()
    with patch.object(main, '_read_video_caches', return_value=(cached_no_stream, now, None)), \
         patch.object(main, '_fetch_and_cache_details', return_value=cached_no_stream) as retry_resolver:
        main._record_no_stream('short-retry')
        assert main._fetch_details_singleflight('short-retry')['availability'] == 'unknown'
        retry_resolver.assert_not_called()
        main._NO_STREAM_FAILURES['short-retry'] = time.time() - main.NO_STREAM_FAILURE_COOLDOWN_SECONDS - 1
        assert main._fetch_details_singleflight('short-retry')['availability'] == 'unknown'
        retry_resolver.assert_called_once_with('short-retry')

    # A transient read must preserve descriptive metadata but cannot carry a
    # stale stream URL or stale negative evidence into the new cache document.
    previous_details = {
        'id': 'transient-cache', 'username': 'KnownName',
        'direct_url': 'https://fixture.invalid/expired.mp4',
        'proxy_stream_url': '/api/video/stream?id=transient-cache',
        'availability': 'unavailable', 'availability_reason': 'source_page_not_found',
        'checked_at': now - 300, 'retryable': False,
    }
    transient_details = {
        'id': 'transient-cache', 'source': 'archivebate',
        'availability': 'unknown', 'availability_reason': 'request_error',
        'checked_at': time.time(), 'retryable': True,
    }
    with patch.object(main, '_read_video_caches', return_value=(previous_details, now - 300, None)), \
         patch.object(main.scraper, 'get_video_details', return_value=transient_details), \
         patch.object(main, '_details_cache_path', return_value='details-fixture.json'), \
         patch.object(main, '_stream_cache_path', return_value='stream-fixture.json'), \
         patch.object(main, 'atomic_write_json'), patch.object(main.os, 'remove'):
        merged = main._fetch_and_cache_details('transient-cache')
    assert merged['username'] == 'KnownName'
    assert merged['availability'] == 'unknown' and merged['retryable'] is True
    assert 'direct_url' not in merged and 'proxy_stream_url' not in merged

    # A provider URL rejected by the stream path must not be returned from a
    # forced retry even when metadata cache otherwise looks current.
    rejected = 'https://fixture.invalid/rejected.mp4'
    replacement = 'https://fixture.invalid/replacement.mp4'
    with patch.object(main, '_read_video_caches', return_value=({
        'id': 'rejected-fixture', 'direct_url': rejected,
        'direct_url_fetched_at': time.time(),
    }, time.time(), time.time())), \
         patch.object(main, '_fetch_and_cache_details', return_value={
             'id': 'rejected-fixture', 'direct_url': replacement, 'availability': 'available'
         }) as rejected_resolver:
        refreshed = main._fetch_details_singleflight('rejected-fixture', force=True, rejected_url=rejected)
        assert refreshed['direct_url'] == replacement
        rejected_resolver.assert_called_once_with('rejected-fixture')

    # Overlapping forced retries share the resolver result, including failure;
    # a new call after completion remains a new provider read.
    main._DETAILS_FETCH_OUTCOMES.clear()
    overlap_started = threading.Event()
    allow_overlap_finish = threading.Event()
    overlap_calls = []
    def overlap_resolver(video_id):
        overlap_calls.append(video_id)
        overlap_started.set()
        assert allow_overlap_finish.wait(3), 'test did not release resolver'
        return {'id': video_id, 'direct_url': 'https://fixture.invalid/overlap.mp4', 'availability': 'available'}
    with patch.object(main, '_read_video_caches', return_value=({}, None, None)), \
         patch.object(main, '_fetch_and_cache_details', side_effect=overlap_resolver):
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(main._fetch_details_singleflight, 'overlap-force', True)
            assert overlap_started.wait(2)
            second_started = threading.Event()
            def second_force():
                second_started.set()
                return main._fetch_details_singleflight('overlap-force', True)
            second = pool.submit(second_force)
            assert second_started.wait(2)
            time.sleep(0.03)
            allow_overlap_finish.set()
            assert first.result(timeout=3)['direct_url'].endswith('overlap.mp4')
            assert second.result(timeout=3)['direct_url'].endswith('overlap.mp4')
        assert overlap_calls == ['overlap-force'], overlap_calls

        allow_overlap_finish.set()
        assert main._fetch_details_singleflight('overlap-force', force=True)['direct_url'].endswith('overlap.mp4')
        assert len(overlap_calls) == 2, 'a deliberate retry after completion must perform a fresh read'

    main._DETAILS_FETCH_OUTCOMES.clear()
    failure_calls = []
    failure_entered = threading.Event()
    release_failure = threading.Event()
    def failed_resolver(video_id):
        failure_calls.append(video_id)
        failure_entered.set()
        assert release_failure.wait(3)
        raise requests.Timeout('provider timeout')
    with patch.object(main, '_read_video_caches', return_value=({}, None, None)), \
         patch.object(main, '_fetch_and_cache_details', side_effect=failed_resolver):
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(main._fetch_details_singleflight, 'overlap-failure', True)
            assert failure_entered.wait(2)
            second = pool.submit(main._fetch_details_singleflight, 'overlap-failure', True)
            time.sleep(0.03)
            release_failure.set()
            assert first.result(timeout=3)['availability'] == 'unknown'
            assert second.result(timeout=3)['availability'] == 'unknown'
        assert failure_calls == ['overlap-failure'], failure_calls

    # A still-present watch page may point at a deleted CDN file. Expired links
    # and authentication/transient failures must not become removal evidence.
    for name, codes, replacement_url, missing in [
        ('gone-same', [404, 404], 'https://fixture.invalid/old.mp4', True),
        ('gone-new', [404, 410], 'https://fixture.invalid/new.mp4', True),
        ('expired-live', [404, 206], 'https://fixture.invalid/new.mp4', False),
        ('auth', [403], 'https://fixture.invalid/old.mp4', False),
        ('cdn-transient', [404, 503], 'https://fixture.invalid/new.mp4', False),
        ('cdn-auth', [404, 403], 'https://fixture.invalid/new.mp4', False),
    ]:
        fixture_id = f'probe-{name}'
        main._clear_refresh_failure(fixture_id)
        data = {'id': fixture_id, 'direct_url': 'https://fixture.invalid/old.mp4',
                'direct_url_fetched_at': time.time(), 'availability': 'available'}
        responses = [MagicMock(status_code=code, headers={'Content-Type': 'video/mp4'}) for code in codes]
        def record_missing(video_id, fresh):
            data.clear()
            data.update(id=video_id, availability='unavailable', availability_reason='stream_file_not_found',
                        stream_missing_confirmations=2, retryable=False, checked_at=time.time())
        with patch.object(main, '_read_video_caches', side_effect=lambda _: (dict(data), time.time(), time.time())), \
             patch.object(main, 'is_safe_remote_url', return_value=True), \
             patch.object(main, '_validated_session_get', side_effect=responses) as upstream_get, \
             patch.object(main, '_fetch_details_singleflight', return_value={**data, 'direct_url': replacement_url}), \
             patch.object(main, '_remember_missing_stream', side_effect=record_missing) as remember:
            availability = main.check_video_availability(fixture_id, force=False)
        assert (availability['availability'] == 'unavailable') is missing, (name, availability)
        assert remember.call_count == int(missing)
        for response in responses:
            response.close.assert_called()
            response.iter_content.assert_not_called()  # Even Range-ignoring servers transfer no body.
        assert upstream_get.call_args.kwargs['headers']['Range'] == 'bytes=0-0'
    assert main._normalize_video_details('forged', {'availability':'unavailable', 'availability_reason':'stream_file_not_found',
        'retryable':False, 'checked_at':time.time()})['availability'] == 'unknown'

    # The local author index renders a profile without waiting for either provider.
    with patch.object(catalog.catalog_service, 'query_group_members', return_value={'items':[{'id':'local-profile','username':'fixture'}], 'count':1}), \
         patch.object(main.scraper, 'get_model_videos', side_effect=AssertionError('cached profile must not fetch provider')):
        local_profile = main.get_model_videos('fixture', page=1, cached_only=True)
        assert local_profile['videos'][0]['id'] == 'local-profile'

    gate_client = TestClient(main.app)
    get_force = gate_client.get('/api/video/details?id=gate-fixture&force_refresh=true')
    assert get_force.status_code == 405
    post_denied = gate_client.post('/api/video/details/refresh?id=gate-fixture', json={})
    assert post_denied.status_code == 403
    with patch.object(main, '_fetch_details_singleflight', return_value={
        'id': 'gate-fixture', 'direct_url': 'https://fixture.invalid/gate.mp4', 'availability': 'available'
    }) as gated_resolver:
        post_allowed = gate_client.post(
            '/api/video/details/refresh?id=gate-fixture',
            json={}, headers={'x-archivebate-mutation-token': main.LOCAL_MUTATION_TOKEN},
        )
        assert post_allowed.status_code == 200 and post_allowed.json()['availability'] == 'available'
        gated_resolver.assert_called_once_with('gate-fixture', force=True)

print('PASS: catalog migration, provider availability, short retries, shared forced resolution and the GET/POST refresh security contract')
