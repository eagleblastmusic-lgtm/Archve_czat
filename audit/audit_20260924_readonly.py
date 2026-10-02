"""Read-only catalog/cache inspection; no application singleton imports."""
import ast
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import statistics
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'audit/2026-09-24'
sys.path.insert(0, str(ROOT))
import fast_grouped_feed_v2 as fast
from video_identity import VideoKey

db = ROOT / 'data/catalog.db'
conn = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, isolation_level=None, timeout=2)
conn.row_factory = sqlite3.Row
conn.execute('PRAGMA query_only=ON')
report = {'database_bytes': db.stat().st_size,
          'wal_bytes': Path(str(db)+'-wal').stat().st_size,
          'sqlite':sqlite3.sqlite_version,
          'page_count':conn.execute('PRAGMA page_count').fetchone()[0],
          'freelist_count':conn.execute('PRAGMA freelist_count').fetchone()[0],
          'revisions':[dict(r) for r in conn.execute('SELECT revision,complete,is_active,failed,video_count FROM revisions')],
          'indexes':[dict(r) for r in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='catalog_items'")],
          'measurements':[]}
store=json.loads((ROOT/'data/user_store.json').read_text(encoding='utf-8-sig'))
report['collections']={k:len(store.get(k,[])) for k in ['favorites','history','following','blocked_models']}
keys=[key for v in store.get('favorites',[]) if (key:=VideoKey.from_video(v,require_source=True))]
authors=list({v.get('username','') for v in store.get('favorites',[]) if v.get('username')})
rev=conn.execute('SELECT revision FROM revisions WHERE is_active=1 ORDER BY revision DESC LIMIT 1').fetchone()
if rev is None:
    rev=conn.execute('SELECT revision FROM revisions ORDER BY revision DESC LIMIT 1').fetchone()
@contextmanager
def snapshot():
    conn.execute('BEGIN')
    try: yield conn
    finally: conn.execute('ROLLBACK')
fake=SimpleNamespace(page_size=280,_read_snapshot=snapshot,_indexing_progress={})
cs=SimpleNamespace(_normalize_favorite_keys=lambda values:values or [])
if rev:
    rev=rev[0]
    report['measured_revision']=rev
    report['rows_by_source']=[dict(r) for r in conn.execute('SELECT source,count(*) AS count FROM catalog_items WHERE revision=? GROUP BY source',(rev,))]
    for name,af,blocked,fav,ids in [('all','all',[],[],[]),
              ('blocked','all',store.get('blocked_models',[]),authors,keys),
              ('exclude_fav','exclude_fav',store.get('blocked_models',[]),authors,keys)]:
        fast._projection_cache.clear()
        for label,page,limit in [('first16_projection_cold',1,16),('first16_warm',1,16),('full280',1,None),('page2',2,16),('page10',10,16)]:
            start=time.perf_counter()
            data=fast._query_grouped(cs,fake,page=page,item_limit=limit,revision=rev,
                author_filter=af,blocked_models=blocked,favorite_authors=fav,favorite_ids=ids)
            report['measurements'].append({'scope':name,'phase':label,'ms':round((time.perf_counter()-start)*1000,2),
                'items':len(data['items']),'total_groups':data['group_count']})
    report['read_integrity_quick_check']=conn.execute('PRAGMA quick_check').fetchone()[0]
conn.close()
report['caches']={}
for name in ['thumbs_cache','details_cache','stream_cache','storyboard_cache','feed_cache']:
    sizes=[e.stat().st_size for e in os.scandir(ROOT/'data'/name) if e.is_file()]
    report['caches'][name]={'files':len(sizes),'bytes':sum(sizes),'median_bytes':statistics.median(sizes) if sizes else 0}

# Execute the exact normalization function, without importing main and its writers.
tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_normalize_video_details')
env={'storage':SimpleNamespace(is_favorite=lambda _:False),'re':re,'_resolve_clean_video_id':lambda x:x}
exec(compile(ast.Module(body=[node],type_ignores=[]),'main.py','exec'),env)
report['availability_counterexamples']={name:env['_normalize_video_details']('fixture',value)['availability']
    for name,value in {'empty_after_timeout':{},'embed_only':{'embed_url':'https://fixture.invalid/embed'},
                       'explicit_private':{'is_private':True},'direct_available':{'direct_url':'https://fixture.invalid/video'}}.items()}
(OUT/'readonly_metrics.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
