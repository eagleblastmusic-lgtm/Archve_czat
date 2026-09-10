"""Bounded, demand-driven feed snapshots with durable raw source-page cache."""
import copy
import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from cache_store import FEED_CACHE_DIR, atomic_write_json, read_json_cache
from camwhores import deduplicate_videos

PAGE_SIZE = 280
_source_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='feed-source')
_snapshot_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='feed-snapshot')
_guard = threading.RLock()
_snapshots = OrderedDict()
_page_locks = [threading.Lock() for _ in range(64)]


class Snapshot:
    def __init__(self, spec, fetchers, enrich, force=False):
        self.force = force
        self.id = uuid.uuid4().hex
        self.spec = spec
        self.fetchers = fetchers
        self.enrich = enrich
        self.path = Path(FEED_CACHE_DIR)/('snapshot_v1_'+hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()+'.json')
        self.raw = []
        self.items = []
        self.cursor = {source: 1 for source in fetchers}
        self.ended = set()
        self.errors = {}
        self.revision = 0
        self.lock = threading.RLock()
        self.building = False
        self.subscribers = 0
        self.target = 0
        self.touched = time.monotonic()
        saved, _ = read_json_cache(str(self.path))
        if not force and isinstance(saved,dict) and saved.get('schema_version')==1 and time.time()-saved.get('fetched_at',0)<3600:
            self.raw=saved['items']
            self.cursor=saved['cursor']
            self.ended=set(saved['ended'])
            self.items=self.enrich(copy.deepcopy(self.raw))

    def read(self, page):
        with self.lock:
            self.touched = time.monotonic()
            start = (page-1)*PAGE_SIZE
            exhausted = len(self.ended) == len(self.fetchers)
            stopped = len(self.ended) + len(self.errors) == len(self.fetchers)
            count = len(self.items)
            has_more = not stopped or count > page*PAGE_SIZE
            last_p = max(page+(1 if has_more else 0),(count+PAGE_SIZE-1)//PAGE_SIZE)
            vids = copy.deepcopy(self.items[start:start+PAGE_SIZE])
            is_cat_complete = exhausted and not self.errors
            return {
                'snapshot_id': self.id,
                'revision': self.revision,
                'catalog_revision': self.revision,
                'page': page,
                'page_size': PAGE_SIZE,
                'video_count': count,
                'group_count': count,
                'page_count': last_p,
                'items': vids,
                'videos': vids,
                'count': len(vids),
                'target_count': PAGE_SIZE,
                'known_count': count,
                'has_more': has_more,
                'catalog_complete': is_cat_complete,
                'complete': stopped or count >= page * PAGE_SIZE,
                'retryable': bool(self.errors),
                'source_error': dict(self.errors),
                'last_page': last_p,
                'total_videos': count,
                'total_is_estimate': not exhausted,
                'updated_at': time.time(),
                'indexing_progress': {
                    'building': self.building,
                    'subscribers': self.subscribers,
                    'ended': list(self.ended),
                    'errors': dict(self.errors),
                }
            }

    def _page(self, source, page):
        with _page_locks[hash((source,page)) % len(_page_locks)]:
            return self._read_or_fetch_page(source,page)

    def _read_or_fetch_page(self,source,page):
        path = Path(FEED_CACHE_DIR)/f'raw_v1_{source}_{page}.json'
        cached, _ = read_json_cache(str(path))
        if not self.force and isinstance(cached,dict) and cached.get('schema_version')==1 and time.time()-cached.get('fetched_at',0)<3600:
            return cached['items']
        items = self.fetchers[source](page)
        if not isinstance(items,list):
            raise RuntimeError('Invalid source response')
        atomic_write_json(str(path),{'schema_version':1,'fetched_at':time.time(),'items':items,'complete':True,'has_more':bool(items),'source_error':None})
        return items

    def run(self):
        goal_at_start = self.target
        try:
            # Bounded work per subscription; strong filters return progress.
            for _ in range(80):
                # Ustąpienie aktywnemu odtwarzaczowi podczas startu i buforowania wideo (Pakiet C, punkt 6)
                try:
                    import main
                    if main.is_playback_active():
                        time.sleep(0.3)
                        if main.is_playback_active():
                            time.sleep(0.4)
                except Exception:
                    pass
                with self.lock:
                    if self.subscribers<=0 or len(self.items)>=self.target: return
                    sources = [s for s in self.fetchers if s not in self.ended and s not in self.errors]
                    if not sources: return
                    pages = [(s,self.cursor[s]) for s in sources]
                pending = {_source_pool.submit(self._page,s,p):s for s,p in pages}
                # Freeze arrival order inside this snapshot; never re-sort prior items.
                for future in as_completed(pending):
                    source = pending[future]
                    try:
                        batch = future.result()
                    except Exception as exc:
                        with self.lock:
                            self.errors[source]=str(exc)
                            self.revision+=1
                        continue
                    with self.lock:
                        self.cursor[source]+=1
                        if not batch: self.ended.add(source)
                        self.raw=deduplicate_videos(self.raw+batch)
                        self.items=self.enrich(copy.deepcopy(self.raw))
                        self.revision+=1
                        saved={'schema_version':1,'fetched_at':time.time(),'items':list(self.raw),'cursor':dict(self.cursor),'ended':list(self.ended)}
                    atomic_write_json(str(self.path),saved)
        finally:
            with self.lock:
                self.building=False
                if self.subscribers>0 and self.target>goal_at_start and len(self.items)<self.target and len(self.ended)+len(self.errors)<len(self.fetchers):
                    self.building=True
                    _snapshot_pool.submit(self.run)

    def subscribe(self,page):
        with self.lock:
            self.subscribers+=1
            self.target=max(self.target,page*PAGE_SIZE)
            if not self.building:
                self.building=True
                _snapshot_pool.submit(self.run)

    def unsubscribe(self):
        with self.lock: self.subscribers=max(0,self.subscribers-1)


def get_snapshot(spec,fetchers,enrich,snapshot_id=None,force=False):
    spec_key=json.dumps(spec,sort_keys=True)
    with _guard:
        if snapshot_id:
            found=_snapshots.get(snapshot_id)
            if found and found.spec==spec: return found
            raise KeyError('Snapshot expired or preferences changed')
        if not force:
            for found in reversed(list(_snapshots.values())):
                if found.spec==spec and time.monotonic()-found.touched<3600: return found
        for key, old in list(_snapshots.items()):
            if len(_snapshots)<32: break
            if not old.building and not old.subscribers: del _snapshots[key]
        if len(_snapshots)>=32: raise RuntimeError('Too many active snapshots')
        snapshot=Snapshot(spec,fetchers,enrich,force=force)
        _snapshots[snapshot.id]=snapshot
        return snapshot
