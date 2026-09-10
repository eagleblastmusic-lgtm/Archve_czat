"""Local feed acceptance tests, isolated from user data and HTTP."""
import sys, tempfile, time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import feed_service as f
with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, patch.object(f,'FEED_CACHE_DIR',tmp):
    def page(n):
        return [{'id':str(i),'username':str(i//2)} for i in range((n-1)*36,min(n*36,720))]
    snap=f.Snapshot({'test':'plain'},{'fixture':page},lambda x:x)
    collected=[]
    for p in (1,2,3):
        snap.subscribe(p)
        deadline=time.monotonic()+10
        while not snap.read(p)['complete'] and time.monotonic()<deadline:time.sleep(.01)
        data=snap.read(p);assert data['complete'],data
        snap.unsubscribe();collected.extend(v['id'] for v in data['videos'])
        while snap.building:time.sleep(.001)
    assert len(collected)==len(set(collected))==720, (len(collected),snap.read(3))
    restored=f.Snapshot({'test':'plain'},{'fixture':lambda n:(_ for _ in ()).throw(AssertionError('Network'))},lambda x:x)
    assert len(restored.read(1)['videos'])==280
    def grouped(items):
        groups={}
        for item in items:groups.setdefault(item['username'],[]).append(item['id'])
        return [{'id':author,'members':members} for author,members in groups.items()]
    for kind,enrich,expected in [('grouped',grouped,720),('filtered',lambda items:[v for v in items if int(v['id'])%2==0],360)]:
        snap=f.Snapshot({'test':kind},{'fixture':page},enrich)
        all_ids=[]
        for p in (1,2):
            snap.subscribe(p)
            deadline=time.monotonic()+10
            while not snap.read(p)['complete'] and time.monotonic()<deadline:time.sleep(.01)
            data=snap.read(p);assert data['complete']
            snap.unsubscribe()
            while snap.building:time.sleep(.001)
            for item in data['videos']:all_ids.extend(item.get('members',[item['id']]))
        assert len(all_ids)==len(set(all_ids))==expected,(kind,len(all_ids))
    def fast(n):time.sleep(.1);return [{'id':'fast'}] if n==1 else []
    def slow(n):time.sleep(2);return []
    snap=f.Snapshot({'test':'latency'},{'fast':fast,'slow':slow},lambda x:x)
    start=time.monotonic();snap.subscribe(1)
    while not snap.read(1)['videos'] and time.monotonic()-start<1:time.sleep(.01)
    assert snap.read(1)['videos'] and time.monotonic()-start<1
    snap.unsubscribe()
    while snap.building:time.sleep(.01)
print('PASS: 720 identities across 3 pages, grouping and filters, persisted snapshot, first batch before slow source')

