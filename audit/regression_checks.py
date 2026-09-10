"""Offline regression suite. Never starts lifespan or contacts providers."""
import sys, time, statistics, json, inspect
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests, config
with patch.object(requests.Session, 'request', side_effect=AssertionError('Network disabled')), patch.object(config, 'get_archivebate_credentials', return_value=('', '')):
    import main, camwhores
    cards = lambda n: [{'id': str(i), 'username': 'same', 'duration': '10:00', 'date': '2026-09-08'} for i in range(n)]
    results = {}
    samples=[]
    for _ in range(20):
        start=time.perf_counter()
        assert len(camwhores.deduplicate_videos(cards(1000))) == 1000
        samples.append((time.perf_counter()-start)*1000)
    results['dedup_1000_ms']={'p50': statistics.median(samples), 'p95': sorted(samples)[18]}
    assert len(camwhores.deduplicate_videos([{'id':'1'}, {'id':'1','source':'camwhores'}])) == 2
    assert len(camwhores.deduplicate_videos([{'id':'cw_1'}, {'id':'1','source':'camwhores'}])) == 1
    original={'id':'1'}
    assert camwhores.deduplicate_videos([original, {'id':'1','title':'title'}])[0]['title']=='title'
    assert 'title' not in original
    samples=[]
    for count in (0,24,280):
        for group in ('0','1'):
            for _ in range(20):
                with patch.object(main, 'read_json_cache', return_value=(cards(count),time.time())), patch.object(main, '_available_catalog_revision', return_value=None), patch.object(main, '_fetch_and_cache_home', side_effect=AssertionError('Blocking fetch')), patch.object(main.scraper, 'get_home_videos', side_effect=AssertionError('Blocking topup')), patch.object(main, '_enrich_videos', side_effect=lambda v,**kw:v[:24]):
                    start=time.perf_counter()
                    data=main.get_videos(1,False,'all','all',group)
                    samples.append((time.perf_counter()-start)*1000)
                    assert data['count']==min(24,count)
    results['cache_mock_ms']={'p50':statistics.median(samples),'p95':sorted(samples)[int(len(samples)*.95)-1]}
    with patch.object(main, 'is_safe_remote_url', return_value=True), patch.object(main, '_validated_session_get', side_effect=RuntimeError('fixture')), patch.object(main.os.path, 'exists', return_value=False):
        assert main.get_thumbnail_proxy('https://fixture.invalid/image.jpg').status_code==502
    for name in ('toggle_favorite','record_history','clear_history','sync_account'):
        assert not inspect.iscoroutinefunction(getattr(main,name))
    Path('audit/regression_results.json').write_text(json.dumps(results,indent=2))
    print(json.dumps(results,indent=2))
