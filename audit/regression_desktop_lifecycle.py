"""Opt-in native WebView2 acceptance on a sanitized checkout, with neutral media.

Uses the real desktop launcher, lifespan, SQLite readers, workers and FFmpeg.
Only fixture data sources and an isolated, hidden WebView profile are substituted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

if os.name != "nt" or os.environ.get("ARCHIVEBATE_AUDIT_ISOLATED") != "1":
    raise SystemExit("Requires Windows and implementation_runner.py isolation")
sys.path.insert(0, str(Path.cwd()))


def child(port, number):
    import asyncio
    import config
    import requests
    with patch.object(config, "get_archivebate_credentials", return_value=("", "")), \
         patch.object(requests.Session, "request", side_effect=AssertionError("Provider IO forbidden")):
        import runtime_app
        import main
        import desktop_app
        import storyboard_service as story
        from catalog_service import catalog_service
        from storage import UserStorage
        from deep_archivebate import deep_archivebate_service
        from fetch_contract import FetchResult
        from model_tags import model_tag_manager
        from fastapi.responses import FileResponse, StreamingResponse

        root = Path('audit') / f'tmp_native_{number}'
        root.mkdir(exist_ok=True)
        profile = root / 'webview_profile'; profile.mkdir(exist_ok=True)
        video = (root / 'neutral.mp4').resolve()
        subprocess.run([story.imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi',
                        '-i', 'color=c=blue:s=320x180:r=15', '-t', '35', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-y', str(video)], check=True)
        payload = video.read_bytes()
        base = f'http://127.0.0.1:{port}'
        @runtime_app.app.get('/fixture/slow.mp4')
        def slow_source():
            async def chunks():
                for offset in range(0, len(payload), 128):
                    yield payload[offset:offset+128]
                    await asyncio.sleep(.04)
            return StreamingResponse(chunks(), media_type='video/mp4')
        for route in runtime_app.app.routes:
            if getattr(route, 'path', '') == '/api/video/stream':
                route.dependant.call = lambda **kwargs: FileResponse(video, media_type='video/mp4')
        details = {'id': 'native_neutral', 'source': 'archivebate', 'username': 'neutral_fixture',
                   'title': 'Neutral Desktop fixture', 'duration': '0:35', 'availability': 'available',
                   'direct_url': 'https://fixture.invalid/neutral.mp4'}
        dummy_scraper = SimpleNamespace(fetch_search_profiles_result=lambda *a, **k: FetchResult.empty(meta={'total_models': 0, 'last_page': 1}))
        fetchers = {source: (lambda page, src=source: FetchResult.empty(source=src, page=page, has_more=False))
                    for source in ('archivebate', 'camwhores')}
        original_create = desktop_app.webview.create_window
        os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] += ' --disable-background-media-suspend --disable-renderer-backgrounding --disable-background-timer-throttling --disable-backgrounding-occluded-windows'
        original_start = desktop_app.webview.start
        original_popen = story.subprocess.Popen
        processes, failures, observation, media_responses = [], [], {}, []
        exercised = threading.Event()
        def track_process(*args, **kwargs):
            proc = original_popen(*args, **kwargs)
            command = args[0] if args else kwargs.get('args', [])
            if 'ffmpeg' in str(command[0]).lower(): processes.append(proc)
            return proc
        def exercise(window):
            if exercised.is_set(): return
            exercised.set()
            try:
                # WebView2 defers media requests while its native control is
                # hidden. Keep the form invisible to the user, but activate
                # the control so the real decoder and request path can run.
                from System import Action
                def invisible_form():
                    window.native.Opacity = 0
                    window.native.ShowInTaskbar = False
                window.native.Invoke(Action(invisible_form))
                window.show()
                deadline = time.monotonic() + 20
                while not window.evaluate_js('Boolean(window.ArchivebateAppContext && window.ArchivebateBlockedModels)'):
                    assert time.monotonic() < deadline, 'Desktop DOM startup timeout'
                    time.sleep(.05)
                observation.update(window.evaluate_js("""(()=>({
                  userAgent:navigator.userAgent,width:innerWidth,height:innerHeight,
                  pageLabel:document.querySelector('#pageJumpInput').getAttribute('aria-label'),
                  scriptsReady:!!ArchivebateVideoViews,accountBadge:document.querySelector('#accountStatusBadge').textContent
                }))()"""))
                print(json.dumps({'native_runtime':observation}),flush=True)
                main.storage.block_model('fixture_blocked')
                window.evaluate_js("document.querySelector('#navAccountBtn').focus();ArchivebateBlockedModels.showManager();")
                while not window.evaluate_js("Boolean(document.querySelector('#blockedModelsTitle'))"):
                    assert time.monotonic() < deadline
                    time.sleep(.05)
                assert window.evaluate_js("""(()=>{const d=document.querySelector('#blockedModelsTitle').closest('[role=dialog]');
                  document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true}));
                  return d.contains(document.activeElement) && document.activeElement.textContent==='Zamknij';})()""")
                assert window.evaluate_js("""(()=>{document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
                  return document.activeElement.id==='navAccountBtn';})()""")
                window.evaluate_js("ArchivebateVideoModal.open({id:'native_neutral',source:'archivebate',username:'neutral_fixture',duration:'0:35'});")
                window.evaluate_js("const v=document.querySelector('#modalVideo');v.muted=true;v.play().catch(()=>{});")
                deadline = time.monotonic() + 20
                while not window.evaluate_js("document.querySelector('#modalVideo').currentTime>.1"):
                    if time.monotonic() >= deadline:
                        diagnostic = window.evaluate_js("""(()=>{const v=document.querySelector('#modalVideo');return {
                          src:v.currentSrc,readyState:v.readyState,paused:v.paused,time:v.currentTime,
                          error:v.error?.message,codec:v.canPlayType('video/mp4; codecs="avc1.42E01E"'),loader:document.querySelector('#videoLoader').textContent,
                          detailsReady:!!ArchivebateAppContext.state.currentVideoDetails};})()""")
                        raise AssertionError(f'Native neutral video did not play: {diagnostic}; HTTP: {media_responses}')
                    time.sleep(.1)
                window.evaluate_js("const v=document.querySelector('#modalVideo');v.pause();ArchivebatePlayerQoS?.report(v,'pause',true);")
                story.start_segment('native_shutdown', 35, 0, base+'/fixture/slow.mp4', force=True)
                process_deadline = time.monotonic()+8
                while not processes and time.monotonic() < process_deadline: time.sleep(.05)
                assert processes, 'Acceptance must close with actual FFmpeg work'
                observation.update(native_media_played=True, keyboard_focus_in_native_DOM=True,
                                   active_ffmpeg_at_close=any(p.poll() is None for p in processes))
            except BaseException as exc:
                failures.append(repr(exc))
            finally:
                window.destroy()
        def hidden_window(*args, **kwargs):
            kwargs.update(hidden=True, width=960, height=640)
            window = original_create(*args, **kwargs)
            window.events.response_received += lambda response: media_responses.append(response.status_code) if '/api/video/stream' in response.url else None
            window.events.loaded += lambda: exercise(window)
            return window
        def isolated_start(**kwargs):
            return original_start(storage_path=str(profile.resolve()), **kwargs)
        with patch.object(main, '_catalog_fetchers', return_value=fetchers), \
             patch.object(main.scraper, 'clone_for_background', return_value=dummy_scraper), \
             patch.object(main, '_fetch_details_singleflight', return_value=details), \
             patch.object(desktop_app.webview, 'create_window', side_effect=hidden_window), \
             patch.object(desktop_app.webview, 'start', side_effect=isolated_start), \
             patch.object(story.subprocess, 'Popen', side_effect=track_process):
            started = time.monotonic()
            desktop_app.main(port=port)
        assert exercised.is_set() and not failures, failures
        assert main.storage._closed and catalog_service._conn is None
        assert catalog_service._active_readers == 0 and not catalog_service._reader_connections
        assert deep_archivebate_service._closed and not deep_archivebate_service._thread.is_alive()
        assert model_tag_manager._closed and not story._worker_threads
        assert all(p.poll() is not None for p in processes), 'FFmpeg child survived shutdown'
        assert not any(t.name in ('archivebite-desktop-server','archivebite-startup','archivebate-deep-crawler') for t in threading.enumerate())
        desktop_app.ensure_port_available(port=port)
        with UserStorage(main.storage.store_file) as reopened:
            assert reopened.is_model_blocked('fixture_blocked'), 'writer lock and durable state reopen after shutdown'
        observation.update(port_released=True, writer_lock_released=True, WAL_readers_closed=True,
                           producers_stopped=True, ffmpeg_exited=True, lifecycle_seconds=round(time.monotonic()-started,3))
        Path(f'desktop_native_{number}.json').write_text(json.dumps(observation, indent=2), encoding='utf-8')
        print(json.dumps(observation))


if '--child' in sys.argv:
    child(int(sys.argv[2]), int(sys.argv[3]))
else:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]
    report=[]
    for number in (1,2):
        result=subprocess.run([sys.executable,__file__,'--child',str(port),str(number)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=100)
        print(result.stdout, end='')
        assert result.returncode==0, result.stderr
        report.append(json.loads(Path(f'desktop_native_{number}.json').read_text(encoding='utf-8')))
    Path('desktop_lifecycle_result.json').write_text(json.dumps({'runs':report,'restart_same_port':True},indent=2),encoding='utf-8')
    print('PASS: native WebView2 startup, neutral playback, focus, own-server shutdown and restart')
