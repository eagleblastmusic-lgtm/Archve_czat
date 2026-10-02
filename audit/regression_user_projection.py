"""Offline counterexamples for the 2026-10-02 user-state and local-scope audit."""
from __future__ import annotations

import asyncio
import html
import json
import re
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import requests

with patch.object(config, "get_archivebate_credentials", return_value=("", "")), patch.object(requests.Session, "request", side_effect=AssertionError("External HTTP disabled")):
    import main
    import storage as storage_mod
    import catalog_service as catalog_mod
    from storage import UserStorage
    from scraper import ArchivebateScraper
    from fastapi.testclient import TestClient


def video(ident="42", source="archivebate", username="fixture"):
    return {"id": f"cw_{ident}" if source == "camwhores" else ident, "provider_id": ident, "source": source,
            "username": username, "description": "needle", "published_at": 1}


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    with UserStorage(str(root / "user.json")) as store:
        entered, release, read_done = threading.Event(), threading.Event(), threading.Event()
        results, failures = [], []
        def failed_write(*args):
            entered.set()
            assert release.wait(3)
            raise OSError("fixture disk failure")
        def write():
            try:
                store.commit_favorite_toggle(video(), remote_required=True)
            except OSError:
                failures.append(True)
        def read():
            results.append(store.projection_snapshot())
            read_done.set()
        with patch.object(storage_mod, "atomic_write_json", side_effect=failed_write):
            writer = threading.Thread(target=write); writer.start()
            assert entered.wait(3)
            reader = threading.Thread(target=read); reader.start()
            assert not read_done.wait(.05), "reader must wait for commit or rollback"
            release.set(); writer.join(3); reader.join(3)
        assert failures and results[0]["favorites_count"] == 0 and results[0]["preferences_version"] == 0
        assert not store.data["remote_outbox"], "local choice and intent roll back together"

        add = store.commit_favorite_toggle({**video(), "desired": True}, remote_required=True)
        old_observation = {"archivebate:id:42": add["operation_id"]}
        remove = store.commit_favorite_toggle({**video(), "desired": False}, remote_required=True)
        assert store.set_remote_status(video(), "confirmed", operation_id=add["operation_id"]) is None
        store.set_remote_status(video(), "failed", operation_id=remove["operation_id"])
        store.merge_remote_data([video()], [], [], observed_intents=old_observation)
        assert not store.is_favorite(video()) and store.preferences_version == 2
        assert store.get_remote_intent(video())["status"] == "failed"
        current = {"archivebate:id:42": remove["operation_id"]}
        store.merge_remote_data([], [], [], observed_intents=current, favorites_complete=False)
        assert store.get_remote_intent(video())["status"] == "failed", "partial absence cannot confirm removal"
        store.merge_remote_data([], [], [], observed_intents=current)
        assert store.get_remote_intent(video())["status"] == "confirmed"
        store.merge_remote_data([video("43", username="new-author")], [], [])
        assert store.preferences_version == 3, "merge invalidates the preference projection"
        last_complete = store.projection_snapshot()["last_synced"]
        store.merge_remote_data([], [], [], favorites_complete=False, sync_complete=False)
        assert store.projection_snapshot()["last_synced"] == last_complete, "partial fetch cannot claim a full synchronization"
        snapshot = store.export_snapshot()
        for imported_version in (0, 3, 100):
            snapshot["data"]["preferences_version"] = imported_version
            before = store.preferences_version
            restored = store.restore_snapshot(snapshot)
            assert restored["preferences_version"] > max(before, imported_version)
        before = store.export_snapshot()
        with patch.object(storage_mod, "atomic_write_json", side_effect=OSError("restore write failure")):
            try:
                store.restore_snapshot(snapshot)
                assert False
            except OSError:
                pass
        assert store.export_snapshot()["data"] == before["data"]
        generation = store.projection_snapshot()["restore_generation"]
        store.restore_snapshot(snapshot)
        try:
            store.merge_remote_data([video("late")], [], [], expected_restore_generation=generation)
            assert False, "a pre-restore sync cannot publish into restored data"
        except RuntimeError:
            pass
    with UserStorage(str(root / "user.json")) as store:
        store.merge_remote_data([video()], [], [])
        assert not store.is_favorite(video()), "the removal intent survives restart"

        # The oldest of 1001 favorites and an overlapping provider ID in the other source.
        store.data["favorites"] = [video(str(i), username="Model") for i in range(1001)]
        store.save()
        with patch.object(main, "storage", store):
            client = TestClient(main.app)
            state = client.get("/api/account/favorites/state", params={"source": "archivebate", "provider_id": "0"}).json()
            assert state["is_favorite"] and state["total_favorites"] == 1001
            assert not client.get("/api/account/favorites/state", params={"source": "camwhores", "provider_id": "0"}).json()["is_favorite"]
            original_enrich = main._enrich_videos
            worker_ids = []
            def slow_enrich(*args, **kwargs):
                worker_ids.append(threading.get_ident()); time.sleep(.08)
                return original_enrich(*args, **kwargs)
            async def responsive():
                pending = asyncio.create_task(main.get_account_favorites(1, 280))
                started = time.perf_counter()
                await asyncio.sleep(.01)
                assert time.perf_counter() - started < .06, "ASGI heartbeat blocked by enrichment"
                page = await pending
                assert page["count"] == 280 and page["total"] == 1001
            with patch.object(main, "_enrich_videos", side_effect=slow_enrich):
                asyncio.run(responsive())
            assert worker_ids[0] != threading.get_ident()

        svc = catalog_mod.CatalogService(db_path=root / "catalog.db")
        try:
            items = [video("0", username="Model"), video("0", "camwhores", "Model"), video("2000", username="unliked")]
            svc.import_items(items, revision=1, complete=True)
            with patch.object(main, "storage", store), patch.object(catalog_mod, "catalog_service", svc):
                for author_filter, expected in (("all", 3), ("only_fav", 1), ("exclude_fav", 2)):
                    response = client.get("/api/search/local", params={"q": "needle", "author_filter": author_filter, "per_page": 1})
                    assert response.status_code == 200, response.text
                    payload = response.json()
                    assert payload["total"] == expected and payload["page_count"] == expected, payload
                assert client.get("/api/search/local", params={"q": "needle", "preferences_version": 0}).status_code == 409
        finally:
            svc.close()

    # The derived trigram index must preserve every literal substring, filter,
    # page count and published snapshot. Other catalog producers update it in
    # the same SQLite transaction through triggers, including rollback/delete.
    db = root / 'local-index.db'
    svc = catalog_mod.CatalogService(db_path=db)
    try:
        items = [{**video(str(i), username='Model' if i%2 else 'author'),
                  'description': text} for i,text in enumerate(['needle','Needle','100% literal_value',
                  'a quote " and backslash \\', 'Zażółć gęślą', 'a special_rare_term', 'abcde'])]
        svc.import_items(items, revision=1, complete=True)
        for query in ('needle','NEEDLE','%', '_','literal_value','"','\\','zażółć','abc','ab','missing', 'rare_term'):
            for author_filter in ('all','only_fav','exclude_fav'):
                options = dict(revision=1, page_size=2, blocked_models=['author'], author_filter=author_filter,
                               favorite_ids=[{'source':'archivebate','provider_id':'1'}])
                with patch.object(svc, '_ensure_local_search_index', return_value=False):
                    expected=svc.search_local(query, **options)
                actual=svc.search_local(query, **options)
                assert actual == expected, (query, author_filter, actual, expected)
        assert svc._local_search_index_ready
        svc.import_items([video('mutable')], revision=2, complete=False)
        assert svc.search_local('needle',revision=2)['total']==1
        svc.import_items([{**video('mutable'),'description':'changed'}], revision=2, complete=False)
        assert svc.search_local('needle',revision=2)['total']==0
        conn=svc._get_conn(); conn.execute('BEGIN IMMEDIATE')
        conn.execute('DELETE FROM catalog_items WHERE revision=2'); conn.execute('ROLLBACK')
        assert svc.search_local('changed',revision=2)['total']==1
        conn.execute('DELETE FROM catalog_items WHERE revision=2')
        assert svc.search_local('changed',revision=2)['total']==0
        svc.close(); svc=catalog_mod.CatalogService(db_path=db)
        assert svc.search_local('needle',revision=1)['total']==2
        svc._get_conn().execute("INSERT INTO catalog_search_fts(catalog_search_fts,rank) VALUES('integrity-check',1)")
    finally:
        svc.close()

    # Two tabs commit before an old remote response returns. Only the newest
    # operation may confirm its target, including a retry after a timeout.
    with UserStorage(str(root / "two-tabs.json")) as store:
        entered, release = threading.Event(), threading.Event()
        remote, responses = {"saved": False}, {}
        configured = MagicMock(email="fixture", password="fixture", is_logged_in=True)
        def delayed_remote(ident, desired):
            if desired:
                entered.set()
                assert release.wait(3)
            remote["saved"] = desired
            return {"status": "confirmed"}
        with patch.object(main, "storage", store), patch.object(main, "session", configured), \
             patch.object(main.scraper, "set_remote_save", side_effect=delayed_remote), \
             patch.object(main, "invalidate_feed_cache"):
            first = threading.Thread(target=lambda: responses.update(first=main.toggle_favorite({**video(), "desired": True})))
            second = threading.Thread(target=lambda: responses.update(second=main.toggle_favorite({**video(), "desired": False})))
            first.start(); assert entered.wait(3); second.start()
            deadline = time.monotonic() + 3
            while store.is_favorite(video()) and time.monotonic() < deadline:
                time.sleep(.005)
            assert not store.is_favorite(video()), "the second local commit need not wait for provider IO"
            release.set(); first.join(3); second.join(3)
            assert not first.is_alive() and not second.is_alive()
            assert responses["first"]["remote_state"] == "unknown"
            assert responses["second"]["remote_state"] == "confirmed"
            assert store.get_remote_intent(video())["operation_id"] == responses["second"]["operation_id"]
            assert not remote["saved"] and not store.is_favorite(video())
            with patch.object(main.scraper, "set_remote_save", return_value={"status": "unknown", "error": "timeout"}):
                assert main.toggle_favorite({**video(), "desired": True})["remote_state"] == "unknown"
            # An absolute target retry commits the same local choice and reaches
            # the desired remote state rather than blindly toggling it again.
            assert main.toggle_favorite({**video(), "desired": True})["remote_state"] == "confirmed"
            assert remote["saved"] and store.is_favorite(video())

    with patch.object(main, "is_safe_remote_url", side_effect=AssertionError("cache-only must not resolve peers")), patch.object(main, "_validated_session_get", side_effect=AssertionError("cache-only network")):
        assert main.get_thumbnail_proxy("https://example.invalid/cache-miss.jpg", cache_only=True).status_code == 404
        key = main.hashlib.sha256(b"https://example.invalid/cache-hit.jpg").hexdigest()
        with main.MEMORY_CACHE_LOCK:
            main._remember_thumbnail(key, b"fixture cached image", "image/jpeg")
        assert main.get_thumbnail_proxy("https://example.invalid/cache-hit.jpg", cache_only=True).headers["X-Cache"] == "RAM"

# All starting states converge, including a lost mutation response and retry.
for local in (False, True):
    for remote in (False, True):
        for desired in (False, True):
            state = {"saved": remote}
            session = MagicMock()
            def remote_page(*args, **kwargs):
                data = {"fingerprint": {"name": "save-video", "id": "fixture"}, "serverMemo": {"data": {"isSaved": state["saved"]}}}
                return MagicMock(status_code=200, url="https://archivebate.com/watch/42", text=f'<div wire:initial-data="{html.escape(json.dumps(data), quote=True)}"></div>')
            session.request.side_effect = remote_page
            def toggle(*args, **kwargs):
                state["saved"] = not state["saved"]
                return None  # Response lost; the subsequent observation is authoritative.
            session.call_livewire.side_effect = toggle
            scraper = ArchivebateScraper(session)
            assert scraper.set_remote_save("42", desired)["status"] == "confirmed"
            count = session.call_livewire.call_count
            assert count == int(remote != desired)
            assert scraper.set_remote_save("42", desired)["status"] == "confirmed"
            assert session.call_livewire.call_count == count
            assert state["saved"] == desired

# More than 15 account pages are followed to natural completion. Caps are partial.
session = MagicMock(is_logged_in=True)
def account_page(method, url, **kwargs):
    page = int(url.split("page=")[-1]) if "page=" in url else 1
    next_link = f'<a rel="next" href="?page={page+1}">Next</a>' if page < 18 else ""
    return MagicMock(status_code=200, url=url, text=f'<section class="video_item" data-id="{page}"></section>{next_link}')
session.request.side_effect = account_page
scraper = ArchivebateScraper(session)
with patch.object(scraper, "parse_video_card", side_effect=lambda section: video(re.search(r'data-id="([^"]+)"', section).group(1))):
    result = scraper.get_account_section_videos("watchlater", max_pages=None, strict=True, return_meta=True)
    assert result["complete"] and result["pages"] == 18 and len(result["items"]) == 18
    partial = scraper.get_account_section_videos("watchlater", max_pages=15, strict=True, return_meta=True)
    assert not partial["complete"] and partial["error"] == "account_page_limit"

print("PASS: committed snapshots; atomic favorite intents; stale-response CAS; tombstones/restart; restore generations; ASGI workers; identity lookup >1000; local filters/cache; remote target states/retry; account completeness")
