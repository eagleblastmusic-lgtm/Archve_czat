"""Controlled F12/U02 measurements; never profiles or mutates the user's library."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

if os.environ.get('ARCHIVEBATE_AUDIT_ISOLATED') != '1':
    raise SystemExit('Use implementation_runner.py for an isolated fixture')
sys.path.insert(0, str(Path.cwd()))
import requests
import config
with patch.object(config, 'get_archivebate_credentials', return_value=('', '')), \
     patch.object(requests.Session, 'request', side_effect=AssertionError('Provider IO forbidden')):
    import main
    from storage import UserStorage
    from catalog_service import CatalogService


def summary(samples):
    values = sorted(samples)
    return {'samples_ms': [round(v,3) for v in samples], 'p50_ms': round(statistics.median(values),3),
            'p95_ms': round(values[min(len(values)-1, int(len(values)*.95))],3)}


def timed(fn, samples=5):
    times=[]
    for _ in range(samples):
        started=time.perf_counter(); fn(); times.append((time.perf_counter()-started)*1000)
    return summary(times)


report={'environment': {'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
                       'OS': platform.platform(), 'CPU': platform.processor()}, 'account': [],
        'cold_definition': 'First operation on reopened store/SQLite reader; OS filesystem cache is not evicted.'}
with tempfile.TemporaryDirectory(prefix='account_search_profile_') as directory:
    root=Path(directory)
    for size in (3000, 10000):
        path=root/f'user_{size}.json'
        with UserStorage(str(path)) as seed:
            seed.data['favorites']=[{'source': 'archivebate' if i%2==0 else 'camwhores',
                'id': str(i) if i%2==0 else f'cw_{i}', 'username': f'author_{i%300}',
                'description': f'neutral item {i}', 'added_at': f'2026-10-{1+i%2:02}T00:00:00Z'} for i in range(size)]
            seed.save()
        with UserStorage(str(path)) as store, patch.object(main, 'storage', store):
            snapshot=store.projection_snapshot()
            first=timed(lambda: main._account_page('favorites',1,280),1)
            page=store.account_page('favorites',1,280)['videos']
            row={'size': size, 'cold_page': first, 'projection': timed(store.projection_snapshot),
                 'slice_sort_copy': timed(lambda: store.account_page('favorites',1,280)),
                 'enrichment': timed(lambda: main._enrich_videos(page, preferences=snapshot)),
                 'warm_page': timed(lambda: main._account_page('favorites',1,280))}
            async def concurrent():
                heartbeats=[]
                tasks=[asyncio.create_task(main.get_account_favorites(1,280)) for _ in range(4)]
                while not all(t.done() for t in tasks):
                    began=time.perf_counter(); await asyncio.sleep(.005)
                    heartbeats.append((time.perf_counter()-began)*1000)
                results=await asyncio.gather(*tasks)
                assert all(v['total']==size and v['count']==280 for v in results)
                return summary(heartbeats)
            row['four_parallel_requests_5ms_heartbeat']=asyncio.run(concurrent())
            report['account'].append(row)

    # Same scale as the existing 440k grouped benchmark; mixed sources, 70k
    # authors, variable-length metadata and rare/common substrings.
    db=root/'catalog.db'; svc=CatalogService(db_path=db)
    count=440000; conn=svc._get_conn(); now=time.time()
    conn.execute('INSERT INTO revisions(revision,created_at,updated_at,complete,is_active,failed,video_count) VALUES(1,?,?,1,1,0,?)',(now,now,count))
    insert='INSERT INTO catalog_items(canonical_key,source,video_id,author,author_clean,published_at,duration_seconds,duration_str,poster,url,preview_video,title,platform,raw_json,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
    conn.execute('BEGIN IMMEDIATE'); batch=[]
    for i in range(count):
        source='archivebate' if i%5<3 else 'camwhores'; ident=str(i); author=f'author_{i%70000}'
        title=f'Neutral fixture {i}' + (' special_rare_term' if i%5000==0 else '')
        raw={'id': ident if source=='archivebate' else f'cw_{ident}', 'source': source, 'username': author,
             'title': title, 'tags': ['neutral','fixture',f'tag{i%31}'], 'description': 'neutral metadata '*12,
             'duration': '1:35', 'published_at': 1800000000-i,
             'poster': f'https://fixture.invalid/{ident}.jpg', 'url': f'https://fixture.invalid/watch/{ident}'}
        batch.append((f'{source}:id:{ident}',source,ident,author,author.replace('_',''),1800000000-i,95,'1:35',raw['poster'],raw['url'],'',title,source,json.dumps(raw),1))
        if len(batch)==5000: conn.executemany(insert,batch); batch=[]
    if batch: conn.executemany(insert,batch)
    conn.execute('COMMIT'); conn.execute('ANALYZE')
    report['local_search']={'rows':count,'database_bytes':db.stat().st_size,
        'count_query_plan':[list(r) for r in conn.execute("EXPLAIN QUERY PLAN SELECT COUNT(*) FROM catalog_items WHERE revision=? AND lower(raw_json) LIKE ?",(1,'%neutral%'))],
        'page_query_plan':[list(r) for r in conn.execute("EXPLAIN QUERY PLAN SELECT raw_json FROM catalog_items WHERE revision=? AND lower(raw_json) LIKE ? ORDER BY published_at DESC,canonical_key ASC LIMIT 200",(1,'%neutral%'))], 'queries':[]}
    svc.close()
    index_started=time.perf_counter()
    svc=CatalogService(db_path=db)
    svc.search_local('special_rare_term',revision=1)
    report['local_search']['initial_index_and_search_ms']=round((time.perf_counter()-index_started)*1000,3)
    report['local_search']['candidate_query_plan']=[list(r) for r in svc._get_conn().execute(
        'EXPLAIN QUERY PLAN SELECT c.rowid FROM catalog_search_fts f JOIN catalog_items c ON c.rowid=f.rowid WHERE c.revision=? AND f.raw_json LIKE ? LIMIT 10001',
        (1,'%special_rare_term%'))]
    svc.close()
    for query in ('special_rare_term','neutral','missing_fixture_term','author_103'):
        svc=CatalogService(db_path=db)
        result=svc.search_local(query,revision=1)
        # Include count and page retrieval in each latency; all matches remain
        # exact existing substring semantics, including JSON metadata fields.
        svc.close(); svc=CatalogService(db_path=db)
        first=timed(lambda: svc.search_local(query,revision=1),1)
        warm=timed(lambda: svc.search_local(query,revision=1),5)
        report['local_search']['queries'].append({'query':query,'total':result['total'],'reader_cold':first,'warm':warm})
        svc.close()
Path('user_search_profile.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
