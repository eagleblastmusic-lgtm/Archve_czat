"""
Pakiet C Acceptance & Regression Tests:
1. Symulowana odmowa połączenia (ConnectionError / WinError 10061) daje najwyżej JEDNO współdzielone odświeżenie.
2. Brak nieskończonych prób, poprawny kontrolowany błąd 502 i ochrona przed lawiną zapytań (cooldown).
3. Rozdzielenie TTL metadanych (24h) od ważności direct_url (30 min).
4. Walidacja identyfikatora autora bez zgadywania słów ze słownika ("hot", "squirt") i bez spekulacyjnych Livewire.
5. Koordynacja odtwarzacza (/api/playback/status, is_playback_active).
"""
import sys, time, threading
from pathlib import Path
from unittest.mock import patch, MagicMock
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests, config

with patch.object(requests.Session, 'request', side_effect=AssertionError('External HTTP disabled')), \
     patch.object(config, 'get_archivebate_credentials', return_value=('', '')):
    import main, scraper, client as client_mod
    from fastapi.testclient import TestClient

    client = TestClient(main.app)

    # 1. Symulowana odmowa połączenia: 10 współbieżnych zapytań daje najwyżej 1 odświeżenie resolvera
    refresh_calls = []
    fake_cache = {
        'id': 'test_video_1',
        'direct_url': 'http://127.0.0.1:9999/dead_stream.mp4',
        'direct_url_fetched_at': time.time(),
        'title': 'Test Video',
        'username': 'KnownAuthor'
    }

    def fake_refresh(video_id, force=False, rejected_url=None):
        refresh_calls.append((video_id, rejected_url))
        time.sleep(0.04)
        # Resolver zwraca ten sam niedostępny URL lub błąd
        return dict(fake_cache)

    with patch.object(main, 'read_json_cache', return_value=(fake_cache, time.time())), \
         patch.object(main, '_fetch_and_cache_details', side_effect=fake_refresh), \
         patch.object(main, '_validated_session_get', side_effect=requests.exceptions.ConnectionError("WinError 10061 Connection refused")), \
         patch.object(main, 'is_safe_remote_url', return_value=True):

        # Czyścimy stan awarii przed testem
        main._clear_host_failure("127.0.0.1:9999")
        main._clear_refresh_failure("test_video_1")

        with ThreadPoolExecutor(10) as pool:
            results = list(pool.map(
                lambda _: client.get('/api/video/stream', params={'id': 'test_video_1', 'url': fake_cache['direct_url']}),
                range(10)
            ))

        # Dokładnie jedno wywołanie odświeżenia resolvera dla 10 równoległych żądań Range/generatora!
        assert len(refresh_calls) == 1, f"Oczekiwano dokładnie 1 odświeżenia, otrzymano: {len(refresh_calls)}"
        # Wszystkie 10 zapytań otrzymało kontrolowany kod błędu 502 (Bad Gateway), żaden 500!
        for r in results:
            assert r.status_code == 502, f"Status: {r.status_code}, body: {r.text}"
            assert "odrzucił połączenie" in r.text or "niedostępny" in r.text or "Błąd połączenia" in r.text

        # Kolejne 10 zapytań w czasie cooldownu NIE uruchamia kolejnych cykli naprawy (współdzielenie awarii)
        refresh_calls.clear()
        with ThreadPoolExecutor(10) as pool:
            results2 = list(pool.map(
                lambda _: client.get('/api/video/stream', params={'id': 'test_video_1', 'url': fake_cache['direct_url']}),
                range(10)
            ))
        assert len(refresh_calls) == 0, f"Cooldown powinien zablokować kolejne odświeżenia, było: {len(refresh_calls)}"
        for r in results2:
            assert r.status_code == 502

    # 2. Rozdzielenie świeżości metadanych od direct_url
    old_cache = {
        'id': 'test_old_url',
        'direct_url': 'https://stream.invalid/old.mp4',
        'direct_url_fetched_at': time.time() - 3600, # 1h temu (stare)
        'title': 'Sample Video Title',
        'username': 'SampleModel'
    }
    fetch_calls = []
    def fake_fresh_fetch(vid, force=False, rejected_url=None):
        fetch_calls.append(vid)
        return {'id': vid, 'direct_url': 'https://stream.invalid/fresh.mp4', 'direct_url_fetched_at': time.time()}

    upstream_mock = MagicMock(status_code=200, headers={'Content-Type': 'video/mp4', 'Content-Length': '4'})
    upstream_mock.iter_content.return_value = iter([b'data'])

    with patch.object(main, 'read_json_cache', return_value=(old_cache, time.time() - 3600)), \
         patch.object(main, '_fetch_details_singleflight', side_effect=fake_fresh_fetch), \
         patch.object(main, '_validated_session_get', return_value=upstream_mock), \
         patch.object(main, 'is_safe_remote_url', return_value=True):

        res = client.get('/api/video/stream', params={'id': 'test_old_url'})
        assert res.status_code == 200
        # Ponieważ direct_url miał 3600s (>1800s), nastąpiło odświeżenie direct_url!
        assert len(fetch_calls) == 1

    # Brak URL jest krótkim stanem negatywnego cache'u: drugi player nie odpala
    # ponownie resolvera, ale wymuszony refresh może go wyczyścić.
    main._NO_STREAM_FAILURES.clear()
    with patch.object(main, 'read_json_cache', return_value=({'id': 'missing_stream', 'title': 'x'}, time.time())), \
         patch.object(main, '_fetch_details_singleflight', return_value={}) as missing_resolve, \
         patch.object(main, 'is_safe_remote_url', return_value=True):
        first_missing = client.get('/api/video/stream', params={'id': 'missing_stream'})
        second_missing = client.get('/api/video/stream', params={'id': 'missing_stream'})
        assert first_missing.status_code == second_missing.status_code == 400
        assert missing_resolve.call_count == 1

    # 3. Koordynacja odtwarzacza (/api/playback/status)
    res_busy = client.post('/api/playback/status', json={'is_busy': True, 'buffered_seconds': 1.5})
    assert res_busy.status_code == 200 and res_busy.json()['playback_busy'] is True
    assert main.is_playback_active() is True

    res_idle = client.post('/api/playback/status', json={'is_busy': False, 'buffered_seconds': 8.0})
    assert res_idle.status_code == 200 and res_idle.json()['playback_busy'] is False
    assert main.is_playback_active() is False

    # 4. Walidacja identyfikatora autora bez zgadywania słów ("hot", "squirt")
    details_with_title = {
        'id': 'cw_12345',
        'title': 'hot squirt teen solo show',
        'username': 'Model',
        'url': 'https://www.camwhores.tv/videos/12345/hot-squirt-teen/'
    }
    normalized = main._normalize_video_details('cw_12345', details_with_title)
    assert normalized['username'] == 'Model', f"Nie wolno zgadywać 'hot' lub 'squirt'! Otrzymano: {normalized['username']}"

    details_with_profile = {
        'id': 'cw_12345',
        'title': 'some title',
        'username': 'Model',
        'profile_url': 'https://www.camwhores.tv/search/real_model_name/'
    }
    normalized2 = main._normalize_video_details('cw_12345', details_with_profile)
    assert normalized2['username'] == 'real_model_name', f"Powinno wyodrębnić z profile_url, otrzymano: {normalized2['username']}"

    # 5. Blokada spekulacyjnych zapytań Livewire w scraperze dla nieprawidłowych nazw
    invalid_results = main.scraper.get_model_videos("hot")
    assert invalid_results == []
    invalid_results2 = main.scraper.get_model_videos("Model")
    assert invalid_results2 == []
    invalid_results3 = main.scraper.get_model_videos("bad name with spaces")
    assert invalid_results3 == []

print("PASS: Pakiet C backend - single shared refresh on connection refusal, cooldown circuit breaker, stream TTL separation, playback status, author validation")
