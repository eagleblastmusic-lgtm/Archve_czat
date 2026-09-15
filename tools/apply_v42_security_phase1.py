from __future__ import annotations

import ast
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path.cwd()
STAMP = time.strftime('%Y%m%d_%H%M%S')

TARGETS = [
    ROOT / 'main.py',
    ROOT / 'cache_store.py',
    ROOT / 'static' / 'video-prefetch.js',
    ROOT / 'static' / 'video-views.js',
    ROOT / 'static' / 'account.js',
    ROOT / '.github' / 'workflows' / 'ci.yml',
]
NEW_TEST = ROOT / 'audit' / 'regression_security_v42.py'


def fail(msg: str) -> None:
    raise RuntimeError(msg)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count < 1:
        fail(f'{label}: expected at least 1 anchor, found {count}')
    return text.replace(old, new, 1)


def backup(path: Path) -> Path:
    out = path.with_name(path.name + f'.bak_v42_{STAMP}')
    shutil.copy2(path, out)
    return out


def validate_python(path: Path, content: str) -> None:
    ast.parse(content, filename=str(path))


def main() -> int:
    for path in TARGETS:
        if not path.exists():
            fail(f'missing required file: {path}')
    if NEW_TEST.exists():
        fail(f'{NEW_TEST} already exists; refusing to overwrite')

    originals: dict[Path, str] = {}

    try:
        for path in TARGETS:
            originals[path] = path.read_text(encoding='utf-8')
            backup(path)

        path = ROOT / 'main.py'
        text = originals[path]
        text = replace_once(text, 'from fastapi.middleware.gzip import GZipMiddleware\n', 'from fastapi.middleware.gzip import GZipMiddleware\nfrom starlette.middleware.trustedhost import TrustedHostMiddleware\n', 'main import TrustedHostMiddleware')
        text = replace_once(text, 'app = FastAPI(title="Archivebate Video Browser", lifespan=lifespan)\n\n# Duże feedy JSON/CSS/JS są kompresowane; dla lokalnego UI zmniejsza to koszt kopiowania\n', 'app = FastAPI(title="Archivebate Video Browser", lifespan=lifespan)\n\n# V4.2: reject foreign Host headers on every request, not only mutations.\n# This is the primary browser-side DNS-rebinding barrier for the localhost app.\napp.add_middleware(\n    TrustedHostMiddleware,\n    allowed_hosts=["127.0.0.1", "localhost", "testserver"],\n)\n\n# Duże feedy JSON/CSS/JS są kompresowane; dla lokalnego UI zmniejsza to koszt kopiowania\n', 'main trusted host middleware')

        old_gate = '''@app.middleware("http")\nasync def local_mutation_security_gate(request: Request, call_next):\n    """Require same-local-origin and a per-process mutation token for state changes."""\n    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:\n        host = (request.headers.get("host") or "").split(":", 1)[0].strip().lower()\n        origin = request.headers.get("origin")\n        if host not in ALLOWED_LOCAL_HOSTS:\n            return JSONResponse({"success": False, "error": "foreign_host"}, status_code=403)\n        if origin and not _is_allowed_local_origin(origin, request):\n            return JSONResponse({"success": False, "error": "foreign_origin"}, status_code=403)\n        if request.headers.get("x-archivebate-mutation-token") != LOCAL_MUTATION_TOKEN:\n            return JSONResponse({"success": False, "error": "mutation_token_required"}, status_code=403)\n    return await call_next(request)\n'''
        new_gate = '''@app.middleware("http")\nasync def local_security_gate(request: Request, call_next):\n    """Reject foreign Host on every request; protect state changes with Origin + token."""\n    raw_host = (request.headers.get("host") or "").strip().lower()\n    host = raw_host\n    if raw_host.startswith("["):\n        host = raw_host.split("]", 1)[0].lstrip("[")\n    elif ":" in raw_host:\n        host = raw_host.rsplit(":", 1)[0]\n    if host not in ALLOWED_LOCAL_HOSTS:\n        return JSONResponse({"success": False, "error": "foreign_host"}, status_code=403)\n\n    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:\n        origin = request.headers.get("origin")\n        if origin and not _is_allowed_local_origin(origin, request):\n            return JSONResponse({"success": False, "error": "foreign_origin"}, status_code=403)\n        if request.headers.get("x-archivebate-mutation-token") != LOCAL_MUTATION_TOKEN:\n            return JSONResponse({"success": False, "error": "mutation_token_required"}, status_code=403)\n    return await call_next(request)\n'''
        text = replace_once(text, old_gate, new_gate, 'main local security gate')
        text = replace_once(text, '    if not is_safe_remote_url(url) and not (url.startswith("http://127.0.0.1:") or url.startswith("http://localhost:")):\n        raise HTTPException(status_code=400, detail="Niedozwolony adres strumienia")\n', '    if not is_safe_remote_url(url):\n        raise HTTPException(status_code=400, detail="Niedozwolony adres strumienia")\n', 'main stream localhost exception')
        text = replace_once(text, '    refresh_revision = None\n    if force_refresh:\n        refresh_revision = catalog_service.build_revision_background(_catalog_fetchers(), force=True)\n    available_rev = _available_catalog_revision(catalog_service)\n', '    refresh_revision = None\n    if force_refresh:\n        raise HTTPException(status_code=405, detail="Odświeżenie katalogu wymaga POST /api/catalog/refresh")\n    available_rev = _available_catalog_revision(catalog_service)\n', 'main /api/videos GET force refresh')
        text = replace_once(text, '    refresh_revision = None\n    if force_refresh:\n        refresh_revision = catalog_service.build_revision_background(_catalog_fetchers(), force=True)\n    available_rev = _available_catalog_revision(catalog_service)\n', '    refresh_revision = None\n    if force_refresh:\n        raise HTTPException(status_code=405, detail="Odświeżenie katalogu wymaga POST /api/catalog/refresh")\n    available_rev = _available_catalog_revision(catalog_service)\n', 'main /api/feed GET force refresh')
        text = replace_once(text, '@app.get("/api/catalog/groups/{author_key}/members")\ndef get_catalog_group_members(\n', '@app.post("/api/catalog/refresh")\ndef refresh_catalog():\n    """Start a standard catalog rebuild through a mutation-protected endpoint."""\n    from catalog_service import catalog_service\n    active_revision = _published_catalog_revision(catalog_service)\n    refresh_revision = catalog_service.build_revision_background(_catalog_fetchers(), force=True)\n    return {\n        "success": True,\n        "active_revision": active_revision,\n        "refresh_revision": refresh_revision,\n        "refresh_pending": bool(refresh_revision and refresh_revision != active_revision),\n    }\n\n\n@app.get("/api/catalog/groups/{author_key}/members")\ndef get_catalog_group_members(\n', 'main POST catalog refresh endpoint')

        details_old = '''@app.get("/api/video/details")\ndef get_video_details(id: str = Query(...), force_refresh: bool = Query(False)):\n    """Detale z persistent cache i puli wątków; zapobiega blokowaniu pętli asyncio FastAPI."""\n    clean_id = _resolve_clean_video_id(id)\n    cached, metadata_mtime, stream_mtime = _read_video_caches(clean_id)\n    metadata_age = cache_age_seconds(metadata_mtime)\n    stream_age = cache_age_seconds(cached.get("direct_url_fetched_at") or stream_mtime) if isinstance(cached, dict) else float("inf")\n\n    if force_refresh:\n        _clear_no_stream(clean_id)\n    if isinstance(cached, dict) and cached and not force_refresh and metadata_age <= DETAILS_CACHE_STALE_SECONDS:\n        details = cached\n        if metadata_age > DETAILS_CACHE_FRESH_SECONDS or (cached.get("direct_url") and stream_age > STREAM_URL_FRESH_SECONDS):\n            _refresh_details_in_background(clean_id)\n    elif force_refresh:\n        details = _fetch_details_singleflight(clean_id, force=True)\n    else:\n        details = _fetch_details_singleflight(clean_id)\n\n    return _normalize_video_details(clean_id, details)\n'''
        details_new = '''@app.get("/api/video/details")\ndef get_video_details(id: str = Query(...), force_refresh: bool = Query(False)):\n    """Read cached/fresh details without allowing a state-changing force refresh over GET."""\n    if force_refresh:\n        raise HTTPException(status_code=405, detail="Wymuszone odświeżenie wymaga POST /api/video/details/refresh")\n    clean_id = _resolve_clean_video_id(id)\n    cached, metadata_mtime, stream_mtime = _read_video_caches(clean_id)\n    metadata_age = cache_age_seconds(metadata_mtime)\n    stream_age = cache_age_seconds(cached.get("direct_url_fetched_at") or stream_mtime) if isinstance(cached, dict) else float("inf")\n\n    if isinstance(cached, dict) and cached and metadata_age <= DETAILS_CACHE_STALE_SECONDS:\n        details = cached\n        if metadata_age > DETAILS_CACHE_FRESH_SECONDS or (cached.get("direct_url") and stream_age > STREAM_URL_FRESH_SECONDS):\n            _refresh_details_in_background(clean_id)\n    else:\n        details = _fetch_details_singleflight(clean_id)\n\n    return _normalize_video_details(clean_id, details)\n\n\n@app.post("/api/video/details/refresh")\ndef refresh_video_details(id: str = Query(...)):\n    """Force provider/cache refresh only through the mutation security gate."""\n    clean_id = _resolve_clean_video_id(id)\n    _clear_no_stream(clean_id)\n    details = _fetch_details_singleflight(clean_id, force=True)\n    return _normalize_video_details(clean_id, details)\n'''
        text = replace_once(text, details_old, details_new, 'main details GET/POST split')
        validate_python(path, text)
        path.write_text(text, encoding='utf-8', newline='\n')

        path = ROOT / 'cache_store.py'
        text = originals[path]
        text = replace_once(text, '        if parsed.scheme not in ("http", "https") or not parsed.hostname:\n            return False\n        host = parsed.hostname.lower().rstrip(".")\n', '        if parsed.scheme not in ("http", "https") or not parsed.hostname:\n            return False\n        if parsed.username is not None or parsed.password is not None:\n            return False\n        try:\n            port = parsed.port\n        except ValueError:\n            return False\n        if port is not None and not (1 <= int(port) <= 65535):\n            return False\n        host = parsed.hostname.lower().rstrip(".")\n', 'cache_store URL userinfo/port validation')
        validate_python(path, text)
        path.write_text(text, encoding='utf-8', newline='\n')

        path = ROOT / 'static' / 'video-prefetch.js'
        text = originals[path]
        text = replace_once(text, "  const api = global.ArchivebateAPI || {\n    getJSON: (url, opts) => fetch(url, opts).then(r => r.json())\n  };\n", "  const api = global.ArchivebateAPI || {\n    getJSON: (url, opts) => fetch(url, opts).then(r => r.json()),\n    postJSON: (url, body, opts = {}) => fetch(url, {\n      ...opts,\n      method: 'POST',\n      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },\n      body: JSON.stringify(body ?? {})\n    }).then(r => r.json())\n  };\n", 'video-prefetch API fallback')
        text = replace_once(text, "    const request = api.getJSON(\n      `/api/video/details?id=${encodeURIComponent(videoId)}&force_refresh=true`,\n      { timeoutMs: 12000, signal }\n    ).then(fresh => {\n", "    const request = api.postJSON(\n      `/api/video/details/refresh?id=${encodeURIComponent(videoId)}`,\n      {},\n      { timeoutMs: 12000, signal }\n    ).then(fresh => {\n", 'video-prefetch details force refresh POST')
        path.write_text(text, encoding='utf-8', newline='\n')

        path = ROOT / 'static' / 'video-views.js'
        text = originals[path]
        text = replace_once(text, "      const params = `page=${page}&source=${src}&author_filter=${af}&group_authors=${grp}${force ? '&force_refresh=true' : ''}${revParam}${initialItemsParam}`;\n      const data = await api().getJSON(`/api/feed?${params}${snapshot ? `&snapshot_id=${encodeURIComponent(snapshot)}` : ''}`, { timeoutMs: HOME_FEED_HANDSHAKE_TIMEOUT_MS, signal: controller.signal });\n", "      if (force) {\n        await api().postJSON('/api/catalog/refresh', {}, { timeoutMs: HOME_FEED_HANDSHAKE_TIMEOUT_MS, signal: controller.signal });\n        if (generation !== state.viewGeneration) return;\n      }\n      const params = `page=${page}&source=${src}&author_filter=${af}&group_authors=${grp}${revParam}${initialItemsParam}`;\n      const data = await api().getJSON(`/api/feed?${params}${snapshot ? `&snapshot_id=${encodeURIComponent(snapshot)}` : ''}`, { timeoutMs: HOME_FEED_HANDSHAKE_TIMEOUT_MS, signal: controller.signal });\n", 'video-views catalog force refresh POST')
        path.write_text(text, encoding='utf-8', newline='\n')

        path = ROOT / 'static' / 'account.js'
        text = originals[path]
        text = replace_once(text, "    try {\n      const summary = await ArchivebateAPI.getJSON('/api/account/summary', { timeoutMs: 120000 });\n      updateUserStatus({ ...baseStatus, ...summary });\n", "    try {\n      const summary = (!baseStatus.last_synced && baseStatus.logged_in)\n        ? await ArchivebateAPI.postJSON('/api/account/sync', {}, { timeoutMs: 120000 })\n        : await ArchivebateAPI.getJSON('/api/account/summary', { timeoutMs: 120000 });\n      updateUserStatus({ ...baseStatus, ...summary });\n", 'account bootstrap explicit POST sync')
        path.write_text(text, encoding='utf-8', newline='\n')

        NEW_TEST.parent.mkdir(parents=True, exist_ok=True)
        test_content = '''import os\nimport sys\nfrom pathlib import Path\nfrom unittest.mock import patch\n\nROOT = Path(__file__).resolve().parents[1]\nsys.path.insert(0, str(ROOT))\n\nos.environ.setdefault("ARCHIVEBATE_USER_STORE", str(ROOT / "audit" / f"v42_{os.getpid()}_store.json"))\nos.environ.setdefault("ARCHIVEBATE_CATALOG_DB", str(ROOT / "audit" / f"v42_{os.getpid()}_catalog.db"))\n\nfrom fastapi.testclient import TestClient\nimport cache_store\nimport main\n\nassert cache_store.is_safe_remote_url("http://127.0.0.1:9999/internal") is False\nassert cache_store.is_safe_remote_url("http://localhost:9999/internal") is False\nassert cache_store.is_safe_remote_url("http://user:pass@example.com/file") is False\nprint("PASS v4.2 security 1: local/private and credential-bearing URLs are rejected")\n\nclient = TestClient(main.app)\nforeign = client.get("/api/status", headers={"host": "attacker.invalid"})\nassert foreign.status_code == 403, foreign.text\nprint("PASS v4.2 security 2: foreign Host is rejected on GET")\nlocal = client.get("/api/status", headers={"host": "testserver"})\nassert local.status_code == 200, local.text\nprint("PASS v4.2 security 3: trusted local/test Host still works")\nstream_local = client.get("/api/video/stream", params={"url": "http://127.0.0.1:65534/internal"}, headers={"host": "testserver"})\nassert stream_local.status_code == 400, stream_local.text\nprint("PASS v4.2 security 4: stream proxy cannot target localhost")\nwith patch.object(main, "_fetch_details_singleflight") as fetch_details:\n    details_get = client.get("/api/video/details", params={"id": "fixture", "force_refresh": "true"}, headers={"host": "testserver"})\n    assert details_get.status_code == 405, details_get.text\n    fetch_details.assert_not_called()\nprint("PASS v4.2 security 5: force-refresh GET has no provider/cache side effect")\nrefresh_without_token = client.post("/api/video/details/refresh", params={"id": "fixture"}, json={}, headers={"host": "testserver"})\nassert refresh_without_token.status_code == 403, refresh_without_token.text\nprint("PASS v4.2 security 6: refresh POST requires mutation token")\nwith patch.object(main, "_fetch_details_singleflight", return_value={"id": "fixture", "source": "archivebate"}):\n    refresh_with_token = client.post("/api/video/details/refresh", params={"id": "fixture"}, json={}, headers={"host": "testserver", "x-archivebate-mutation-token": main.LOCAL_MUTATION_TOKEN})\n    assert refresh_with_token.status_code == 200, refresh_with_token.text\nprint("PASS v4.2 security 7: token-protected refresh POST remains functional")\nwith patch.object(main, "_published_catalog_revision", return_value=10), patch.object(main, "_catalog_fetchers", return_value={"archivebate": lambda page: []}), patch.object(main, "_available_catalog_revision", return_value=10), patch("catalog_service.catalog_service.build_revision_background", return_value=11) as rebuild:\n    catalog_get = client.get("/api/feed", params={"page": 1, "force_refresh": "true"}, headers={"host": "testserver"})\n    assert catalog_get.status_code == 405, catalog_get.text\n    rebuild.assert_not_called()\nprint("PASS v4.2 security 8: catalog refresh cannot be triggered by GET")\nprint("PASS V4.2 security regression")\n'''
        validate_python(NEW_TEST, test_content)
        NEW_TEST.write_text(test_content, encoding='utf-8', newline='\n')

        path = ROOT / '.github' / 'workflows' / 'ci.yml'
        text = originals[path]
        text = replace_once(text, '      - name: Consolidated audit-fix regressions\n        run: python audit/regression_audit_fixes.py\n', '      - name: Consolidated audit-fix regressions\n        run: python audit/regression_audit_fixes.py\n      - name: V4.2 local security regression\n        run: python audit/regression_security_v42.py\n', 'ci linux v42 security')
        text = replace_once(text, '      - name: Consolidated audit-fix regression\n        run: python audit/regression_audit_fixes.py\n', '      - name: Consolidated audit-fix regression\n        run: python audit/regression_audit_fixes.py\n      - name: V4.2 local security regression\n        run: python audit/regression_security_v42.py\n', 'ci windows v42 security')
        path.write_text(text, encoding='utf-8', newline='\n')

        for py in (ROOT / 'main.py', ROOT / 'cache_store.py', NEW_TEST):
            validate_python(py, py.read_text(encoding='utf-8'))

        print('ARCHIVEBITE V4.2 SECURITY PHASE 1 — PATCH ZASTOSOWANY')
        print('AST: OK')
        print('Zmiany: Host gate na wszystkich requestach, localhost stream block, POST-only refresh, regresja V4.2 + CI.')
        print('Nie uruchamiaj jeszcze aplikacji produkcyjnej przed verifierem.')
        return 0
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        for path, src in originals.items():
            try:
                path.write_text(src, encoding='utf-8', newline='\n')
            except Exception:
                pass
        try:
            if NEW_TEST.exists():
                NEW_TEST.unlink()
        except Exception:
            pass
        print('Rollback źródeł wykonany.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
