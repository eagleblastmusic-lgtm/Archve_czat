from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(rel, old, new):
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{rel}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1. Normalize legacy/local user-store schema instead of replacing defaults with
# a narrower remote-only/older payload.
replace_once(
    "storage.py",
    '''        self.data: Dict[str, Any] = {
            "favorites": [],
            "history": [],
            "following": [],
            "last_synced": None
        }
''',
    '''        self.data: Dict[str, Any] = {
            "favorites": [],
            "history": [],
            "following": [],
            "last_synced": None,
            "blocked_models": [],
            "blocked_model_video_counts": {},
            "blocked_videos_total": 0,
        }
''',
)
replace_once(
    "storage.py",
    '''                    if isinstance(loaded, dict):
                        self.data = loaded
''',
    '''                    if isinstance(loaded, dict):
                        # Zachowaj pełny lokalny schemat także dla starszych plików,
                        # które nie miały jeszcze pól blokowania.
                        normalized = dict(self.data)
                        normalized.update(loaded)
                        self.data = normalized
''',
)

# 2. Make every account-sync outcome a complete UI status payload.
replace_once(
    "main.py",
    '''def _account_counts() -> dict:
    return {
        "favorites_count": len(storage.get_favorites()),
        "history_count": len(storage.get_history()),
        "following_count": len(storage.get_following()),
        "last_synced": storage.data.get("last_synced"),
    }
''',
    '''def _account_counts() -> dict:
    return {
        "email": session.email,
        "logged_in": session.is_logged_in,
        "account_configured": bool(session.email and session.password),
        "favorites_count": len(storage.get_favorites()),
        "history_count": len(storage.get_history()),
        "following_count": len(storage.get_following()),
        "last_synced": storage.data.get("last_synced"),
        "favorite_authors": storage.get_favorite_authors(),
    }
''',
)
replace_once(
    "main.py",
    '''    return {
        "email": session.email,
        "logged_in": session.is_logged_in,
        "favorites_count": len(storage.data.get("favorites", [])),
        "history_count": len(storage.data.get("history", [])),
        "following_count": len(storage.data.get("following", [])),
        "last_synced": storage.data.get("last_synced")
    }
''',
    '''    return {
        **_account_counts(),
        **storage.get_blocked_stats(),
    }
''',
)

# 3. An SSE opened on an old partial revision must move to a newly published
# complete revision instead of ending with a false source_error.
replace_once(
    "main.py",
    '''        async def catalog_events():
            previous = None
            deadline = time.monotonic() + 120
            idle_since = None
            while time.monotonic() < deadline:
                current_rev = rev_to_check
                data = await asyncio.to_thread(
''',
    '''        async def catalog_events():
            previous = None
            deadline = time.monotonic() + 120
            idle_since = None
            current_rev = rev_to_check
            while time.monotonic() < deadline:
                # A partial revision may have been the best local snapshot when
                # the request started. Once a newer revision is atomically
                # published, follow it instead of keeping the stream pinned to
                # a stale partial generation until it reports source_error.
                published_rev = _published_catalog_revision(catalog_service)
                if (
                    published_rev is not None
                    and published_rev != current_rev
                    and not catalog_service.is_revision_complete(current_rev)
                ):
                    current_rev = published_rev
                    idle_since = None
                data = await asyncio.to_thread(
''',
)

# 4. Refresh UI counters after account bootstrap/sync; do not leave the first
# zero-valued render visible after durable account data arrives.
replace_once(
    "static/account.js",
    '''  let updateBlockedModelsCount;
  let userStatusRetryCount = 0;
''',
    '''  let updateBlockedModelsCount;
  let userStatusRetryCount = 0;
  let accountBootstrapStarted = false;
''',
)
replace_once(
    "static/account.js",
    '''  async function initUserStatus() {
''',
    '''  async function refreshAccountBootstrap(baseStatus) {
    if (accountBootstrapStarted || !baseStatus || (!baseStatus.logged_in && !baseStatus.last_synced)) return;
    accountBootstrapStarted = true;
    try {
      const summary = await ArchivebateAPI.getJSON('/api/account/summary', { timeoutMs: 120000 });
      updateUserStatus({ ...baseStatus, ...summary });
      if (global.ArchivebateHomeStats && typeof global.ArchivebateHomeStats.update === 'function') {
        await global.ArchivebateHomeStats.update();
      }
    } catch (e) {
      accountBootstrapStarted = false;
    }
  }

  async function initUserStatus() {
''',
)
replace_once(
    "static/account.js",
    '''      updateUserStatus(data);

      if (data.account_configured && !data.logged_in && !data.login_error && userStatusRetryCount < 5) {
''',
    '''      updateUserStatus(data);
      void refreshAccountBootstrap(data);

      if (data.account_configured && !data.logged_in && !data.login_error && userStatusRetryCount < 5) {
''',
)
replace_once(
    "static/account.js",
    '''        updateUserStatus(data);
        if (state.mode === 'favorites') loadFavorites(1);
''',
    '''        updateUserStatus(data);
        if (global.ArchivebateHomeStats && typeof global.ArchivebateHomeStats.update === 'function') {
          await global.ArchivebateHomeStats.update();
        }
        if (state.mode === 'favorites') loadFavorites(1);
''',
)

# 5. Add source-native regression coverage for the three defects.
reg = ROOT / "audit/regression_audit_fixes.py"
text = reg.read_text(encoding="utf-8")
marker = 'print("PASS audit-fix 9: stale diagnostics segregated from release evidence")\n'
if text.count(marker) != 1:
    raise SystemExit("regression marker missing or duplicated")
extra = r'''

# 10. Older stores are schema-normalized and account sync preserves local-only block fields.
import json as _json
with tempfile.TemporaryDirectory() as td, patch.object(storage_mod, "STORE_FILE", str(Path(td) / "user_store.json")):
    Path(storage_mod.STORE_FILE).write_text(_json.dumps({
        "favorites": [{"id": "fav-1", "username": "alpha"}],
        "history": [{"id": "hist-1", "username": "beta"}],
        "following": [{"id": "fol-1", "username": "gamma"}],
        "last_synced": "legacy",
    }), encoding="utf-8")
    legacy_store = storage_mod.UserStorage()
    assert legacy_store.data["blocked_models"] == []
    assert legacy_store.data["blocked_model_video_counts"] == {}
    assert legacy_store.data["blocked_videos_total"] == 0
    legacy_store.data["blocked_models"] = ["KeepBlocked"]
    legacy_store.data["blocked_model_video_counts"] = {"keepblocked": 7}
    legacy_store.data["blocked_videos_total"] = 7
    legacy_store.merge_remote_data([], [], [])
    assert legacy_store.get_blocked_models() == ["KeepBlocked"]
    assert legacy_store.get_blocked_stats()["blocked_videos_total"] == 7
print("PASS audit-fix 10: legacy user-store schema is normalized and local-only block state survives sync")

# 11. Account outcomes contain a complete UI status contract and bootstrap refresh is wired.
main.session.email = "configured@example.invalid"
main.session.password = "configured"
main.session.is_logged_in = True
account_counts = main._account_counts()
for required in ("email", "logged_in", "account_configured", "favorites_count", "history_count", "following_count", "last_synced", "favorite_authors"):
    assert required in account_counts, required
account_js = (ROOT / "static/account.js").read_text(encoding="utf-8")
assert "/api/account/summary" in account_js
assert "refreshAccountBootstrap" in account_js
assert "ArchivebateHomeStats.update" in account_js
print("PASS audit-fix 11: account bootstrap refreshes complete UI counters after sync")

# 12. A feed stream pinned to an incomplete revision switches to a newly published revision.
class _PublishedRevisionFixture:
    _indexing_progress = {"is_indexing": False}

    def get_active_revision(self):
        return 2

    def is_revision_complete(self, revision):
        return revision == 2

    def query_page(self, **kwargs):
        revision = kwargs.get("revision")
        complete = revision == 2
        return {
            "catalog_revision": revision,
            "revision": revision,
            "snapshot_id": str(revision),
            "updated_at": float(revision),
            "video_count": 1 if complete else 0,
            "group_count": 1 if complete else 0,
            "page_count": 1,
            "catalog_complete": complete,
            "complete": complete,
            "indexing_progress": {"is_indexing": False},
            "items": [{"id": "published"}] if complete else [],
            "videos": [{"id": "published"}] if complete else [],
            "source_error": {},
        }

fixture_service = _PublishedRevisionFixture()
with patch.object(catalog_mod, "catalog_service", fixture_service):
    response = main.progressive_feed_stream(snapshot_id="1", page=1, revision=1)

    async def _first_stream_item():
        async for item in response.body_iterator:
            return item
        raise AssertionError("stream yielded no item")

    first = asyncio.run(_first_stream_item())
    if isinstance(first, bytes):
        first = first.decode("utf-8")
    assert '"catalog_revision": 2' in first, first
    assert '"catalog_complete": true' in first.lower(), first
print("PASS audit-fix 12: partial feed stream follows the newly published catalog revision")
'''
reg.write_text(text.replace(marker, marker + extra, 1), encoding="utf-8")

print("runtime state hotfix applied")
