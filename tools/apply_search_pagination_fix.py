from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_exact(path: Path, old: str, new: str, count: int = 1):
    text = path.read_text(encoding="utf-8")
    found = text.count(old)
    if found < count:
        raise SystemExit(f"Expected at least {count} occurrences in {path}, found {found}: {old[:80]!r}")
    text = text.replace(old, new, count)
    path.write_text(text, encoding="utf-8")


# 1) Search-mode filter toggles must re-run the active search, not only change labels.
filters = ROOT / "static" / "filters.js"
old_reload = """        if (state.mode === 'home') {\n          triggerLoadHomeVideos(1);\n        }\n"""
new_reload = """        if (state.mode === 'home') {\n          triggerLoadHomeVideos(1);\n        } else if (state.mode === 'search') {\n          if (_deps.performSearch) _deps.performSearch(state.currentQuery, 1);\n          else if (typeof global.performSearch === 'function') global.performSearch(state.currentQuery, 1);\n        }\n"""
replace_exact(filters, old_reload, new_reload, count=2)

# 2) Deterministic grouped Archivebate search pagination.
scraper = ROOT / "scraper.py"
text = scraper.read_text(encoding="utf-8")

old = """        clean_q = query.replace(\"#\", \"\").strip()\n        if not clean_q:\n            return {\"query\": query, \"page\": 1, \"last_page\": 1, \"total_profiles\": 0, \"total_videos\": 0, \"profiles\": [], \"videos\": []}\n\n        cache_key = f\"search:{clean_q.lower()}:{source}:{author_filter}:g{group_authors}:p{page}\"\n"""
new = """        clean_q = query.replace(\"#\", \"\").strip()\n        if not clean_q:\n            return {\"query\": query, \"page\": 1, \"last_page\": 1, \"total_profiles\": 0, \"total_videos\": 0, \"profiles\": [], \"videos\": []}\n\n        grouped_search = group_authors in (True, \"1\", \"true\", \"True\")\n        grouped_archivebate_page_size = 40\n        cache_key = f\"search:{clean_q.lower()}:{source}:{author_filter}:g{group_authors}:p{page}\"\n"""
if old not in text:
    raise SystemExit("search_query grouped-search anchor missing")
text = text.replace(old, new, 1)

old = '                "videos": cached["videos"][:per_page]\n'
new = '                "videos": cached["videos"] if grouped_search else cached["videos"][:per_page]\n'
if old not in text:
    raise SystemExit("search_query cache slice anchor missing")
text = text.replace(old, new, 1)

meta_anchor = """            if author_filter == \"only_fav\" and fav_authors_clean:\n                est_total = min(est_total, max(len(fav_authors_clean) * 35, 35))\n                est_last_page = max(1, math.ceil(est_total / 280))\n\n            meta = {\n"""
meta_repl = """            if author_filter == \"only_fav\" and fav_authors_clean:\n                est_total = min(est_total, max(len(fav_authors_clean) * 35, 35))\n                est_last_page = max(1, math.ceil(est_total / 280))\n            if grouped_search and source == \"only-archivebate\":\n                # W widoku 1/autora stroną jest porcja autorów, nie sztuczna estymacja filmów.\n                est_last_page = max(1, math.ceil(len(all_profiles) / grouped_archivebate_page_size))\n\n            meta = {\n"""
if meta_anchor not in text:
    raise SystemExit("search_query grouped meta anchor missing")
text = text.replace(meta_anchor, meta_repl, 1)

old = '            ab_model_batch = 16 if source == "only-archivebate" else 8\n'
new = '            ab_model_batch = grouped_archivebate_page_size if (grouped_search and source == "only-archivebate") else (16 if source == "only-archivebate" else 8)\n'
if old not in text:
    raise SystemExit("ab_model_batch anchor missing")
text = text.replace(old, new, 1)

old = """            if page == 1:\n                models_for_page = all_profiles[:ab_model_batch]\n            else:\n                start_m = (page - 1) * ab_model_batch\n                end_m = start_m + ab_model_batch\n                if end_m <= len(all_profiles):\n                    models_for_page = all_profiles[start_m:end_m]\n                else:\n                    p_models, _, _ = self._fetch_search_profiles(clean_q, page=page)\n                    p_models = [p for p in p_models if not storage.is_model_blocked(p.get(\"username\"))]\n                    if author_filter == \"exclude_fav\":\n                        p_models = [p for p in p_models if re.sub(r'[^a-z0-9]', '', str(p.get(\"username\", \"\")).lower()) not in fav_authors_clean]\n                    elif author_filter == \"only_fav\":\n                        p_models = [p for p in p_models if re.sub(r'[^a-z0-9]', '', str(p.get(\"username\", \"\")).lower()) in fav_authors_clean]\n                    models_for_page = p_models[:ab_model_batch] if p_models else all_profiles[:ab_model_batch]\n\n            if models_for_page:\n                with ThreadPoolExecutor(max_workers=ab_model_batch) as executor:\n"""
new = """            if page == 1:\n                models_for_page = all_profiles[:ab_model_batch]\n            else:\n                start_m = (page - 1) * ab_model_batch\n                end_m = start_m + ab_model_batch\n                if start_m < len(all_profiles):\n                    # Ostatnia częściowa strona ma użyć pozostałych profili zamiast\n                    # ponownie wpadać w zdalną stronę / fallback strony pierwszej.\n                    models_for_page = all_profiles[start_m:end_m]\n                else:\n                    p_models, _, _ = self._fetch_search_profiles(clean_q, page=page)\n                    p_models = [p for p in p_models if not storage.is_model_blocked(p.get(\"username\"))]\n                    if author_filter == \"exclude_fav\":\n                        p_models = [p for p in p_models if re.sub(r'[^a-z0-9]', '', str(p.get(\"username\", \"\")).lower()) not in fav_authors_clean]\n                    elif author_filter == \"only_fav\":\n                        p_models = [p for p in p_models if re.sub(r'[^a-z0-9]', '', str(p.get(\"username\", \"\")).lower()) in fav_authors_clean]\n                    # Brak danych dla strony N oznacza brak tej strony; nigdy nie\n                    # wracaj do all_profiles[:N], bo to duplikuje stronę 1.\n                    models_for_page = p_models[:ab_model_batch]\n\n            if models_for_page:\n                with ThreadPoolExecutor(max_workers=min(16, max(1, len(models_for_page)))) as executor:\n"""
if old not in text:
    raise SystemExit("models_for_page anchor missing")
text = text.replace(old, new, 1)

old = '        final_videos = sorted_vids[:per_page]\n'
new = '        final_videos = sorted_vids if grouped_search else sorted_vids[:per_page]\n'
if old not in text:
    raise SystemExit("final_videos slice anchor missing")
text = text.replace(old, new, 1)

# Stream path: same grouped semantics for page 1.
old = """        clean_q = query.replace(\"#\", \"\").strip()\n        if not clean_q:\n            yield {\"type\": \"done\", \"total_videos\": 0, \"total_profiles\": 0, \"last_page\": 1}\n            return\n\n        cache_key = f\"search:{clean_q.lower()}:{source}:{author_filter}:g{group_authors}:p1\"\n"""
new = """        clean_q = query.replace(\"#\", \"\").strip()\n        if not clean_q:\n            yield {\"type\": \"done\", \"total_videos\": 0, \"total_profiles\": 0, \"last_page\": 1}\n            return\n\n        grouped_search = group_authors in (True, \"1\", \"true\", \"True\")\n        grouped_archivebate_page_size = 40\n        cache_key = f\"search:{clean_q.lower()}:{source}:{author_filter}:g{group_authors}:p1\"\n"""
if old not in text:
    raise SystemExit("search_query_stream grouped-search anchor missing")
text = text.replace(old, new, 1)

old = """            cached = self._cache[cache_key]\n            meta = self._cache[meta_cache_key]\n            yield {\n"""
new = """            cached = self._cache[cache_key]\n            meta = self._cache[meta_cache_key]\n            cached_page_videos = cached[\"videos\"] if grouped_search else cached[\"videos\"][:280]\n            yield {\n"""
# There is only one occurrence in the stream function after the previous query cache branch.
idx = text.find(old, text.find("def search_query_stream"))
if idx < 0:
    raise SystemExit("stream cached anchor missing")
text = text[:idx] + text[idx:].replace(old, new, 1)
text = text.replace('                "videos": cached["videos"][:280],\n                "total_so_far": len(cached["videos"][:280])\n', '                "videos": cached_page_videos,\n                "total_so_far": len(cached_page_videos)\n', 1)
text = text.replace('                "all_sorted_videos": cached["videos"][:280]\n', '                "all_sorted_videos": cached_page_videos\n', 1)

stream_meta_anchor = """        if author_filter == \"only_fav\" and fav_authors_clean:\n            est_total = min(est_total, max(len(fav_authors_clean) * 35, 35))\n            est_last_page = max(1, math.ceil(est_total / 280))\n\n        # Emitujemy profile natychmiast (0.1 - 0.2s) wraz z oszacowaną liczbą filmów i stron!\n"""
stream_meta_repl = """        if author_filter == \"only_fav\" and fav_authors_clean:\n            est_total = min(est_total, max(len(fav_authors_clean) * 35, 35))\n            est_last_page = max(1, math.ceil(est_total / 280))\n        if grouped_search and source == \"only-archivebate\":\n            est_last_page = max(1, math.ceil(len(all_profiles) / grouped_archivebate_page_size))\n\n        # Emitujemy profile natychmiast (0.1 - 0.2s) wraz z oszacowaną liczbą filmów i stron!\n"""
if stream_meta_anchor not in text:
    raise SystemExit("stream grouped meta anchor missing")
text = text.replace(stream_meta_anchor, stream_meta_repl, 1)

old = '        target_profiles = all_profiles[:16] if source == "only-archivebate" else all_profiles[:8]\n'
new = '        target_profile_count = grouped_archivebate_page_size if (grouped_search and source == "only-archivebate") else (16 if source == "only-archivebate" else 8)\n        target_profiles = all_profiles[:target_profile_count]\n'
if old not in text:
    raise SystemExit("stream target_profiles anchor missing")
text = text.replace(old, new, 1)

old = '        page_1_vids = sorted_all[:280]\n'
new = '        page_1_vids = sorted_all if grouped_search else sorted_all[:280]\n'
if old not in text:
    raise SystemExit("stream page_1 slice anchor missing")
text = text.replace(old, new, 1)

scraper.write_text(text, encoding="utf-8")

# 3) Frontend regression: source/author toggles rerun an active search on page 1.
filter_test = ROOT / "audit" / "regression_search_filters.cjs"
filter_test.write_text(r'''const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function classList() { return { add() {}, remove() {} }; }
function button() {
  return {
    classList: classList(),
    handler: null,
    addEventListener(name, fn) { if (name === 'click') this.handler = fn; },
    click() { if (this.handler) this.handler(); }
  };
}

const state = {
  mode: 'search', currentQuery: '#trans', currentPage: 4,
  sourceFilter: 'all', authorFilter: 'all', groupByAuthor: false
};
const sourceBtn = button();
const authorBtn = button();
const dom = {
  toggleCamwhoresBtn: sourceBtn,
  toggleAuthorFilterBtn: authorBtn,
  toggleGroupBtn: null,
  camwhoresToggleLabel: { innerText: '' }, sourceToggleIcon: { className: '' },
  authorFilterLabel: { innerText: '' }, authorFilterIcon: { className: '' }
};
const calls = [];
const window = {
  ArchivebateAppContext: { state, dom },
  localStorage: { setItem() {} },
  document: { body: { classList: classList() } }
};
const ctx = { window, globalThis: window, document: window.document, localStorage: window.localStorage, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('static/filters.js', 'utf8'), ctx);
window.ArchivebateFilters.init({
  showToast() {}, loadHomeVideos() {}, performSearch: (q, p) => calls.push([q, p])
});

sourceBtn.click();
assert.equal(state.sourceFilter, 'only-camwhores');
assert.deepEqual(calls, [['#trans', 1]]);

authorBtn.click();
assert.equal(state.authorFilter, 'only_fav');
assert.deepEqual(calls, [['#trans', 1], ['#trans', 1]]);
console.log('PASS: search source/author filters rerun active query from page 1');
''', encoding="utf-8")

# 4) Backend regression: grouped AB pages are distinct, full-sized author batches, and final partial page is preserved.
search_test = ROOT / "audit" / "regression_search_pagination.py"
search_test.write_text(r'''from unittest.mock import patch

from scraper import ArchivebateScraper


class DummySession:
    pass


scraper = ArchivebateScraper(DummySession())
profiles = [{"username": f"model{i:03d}", "platform": "Chaturbate"} for i in range(85)]


def fake_profiles(query, page=1):
    # Page 1 exposes the locally known profile universe. Later remote pages are
    # deliberately empty to prove there is no fallback to page-1 authors.
    if page == 1:
        return list(profiles), len(profiles), 1
    return [], len(profiles), 1


def fake_model_videos(username, page=1):
    return [{
        "id": f"{username}-video",
        "username": username,
        "source": "archivebate",
        "platform": "Chaturbate",
        "date": "1 day ago",
        "poster": "https://example.invalid/x.jpg",
    }]


scraper._fetch_search_profiles = fake_profiles
scraper.get_archivebate_model_videos = fake_model_videos

with patch('scraper.storage.get_favorite_authors', return_value=[]), \
     patch('scraper.storage.get_blocked_models', return_value=[]), \
     patch('scraper.storage.get_favorites', return_value=[]), \
     patch('scraper.storage.is_model_blocked', return_value=False), \
     patch('scraper.storage.data', {"favorites": []}), \
     patch('model_tags.model_tag_manager.get_models_with_tag', return_value=[]):
    p1 = scraper.search_query('#trans', page=1, source='only-archivebate', group_authors='1')
    p2 = scraper.search_query('#trans', page=2, source='only-archivebate', group_authors='1')
    p3 = scraper.search_query('#trans', page=3, source='only-archivebate', group_authors='1')

assert p1['last_page'] == 3
assert p2['last_page'] == 3
assert p3['last_page'] == 3
assert len(p1['videos']) == 40
assert len(p2['videos']) == 40
assert len(p3['videos']) == 5

a1 = {v['username'] for v in p1['videos']}
a2 = {v['username'] for v in p2['videos']}
a3 = {v['username'] for v in p3['videos']}
assert a1.isdisjoint(a2)
assert a1.isdisjoint(a3)
assert a2.isdisjoint(a3)
assert a1 == {f"model{i:03d}" for i in range(0, 40)}
assert a2 == {f"model{i:03d}" for i in range(40, 80)}
assert a3 == {f"model{i:03d}" for i in range(80, 85)}

# Re-read from cache must preserve grouped raw videos instead of slicing before
# the API layer groups them by author.
p2_cached = scraper.search_query('#trans', page=2, source='only-archivebate', group_authors='1')
assert {v['username'] for v in p2_cached['videos']} == a2

print('PASS: grouped Archivebate search pagination is deterministic and non-repeating')
''', encoding="utf-8")

# 5) Permanently qualify both regressions in normal CI.
ci = ROOT / ".github" / "workflows" / "ci.yml"
ci_text = ci.read_text(encoding="utf-8")
anchor = """      - name: Pagination regression\n        run: node audit/regression_pagination.cjs\n"""
insert = """      - name: Pagination regression\n        run: node audit/regression_pagination.cjs\n      - name: Search filter regression\n        run: node audit/regression_search_filters.cjs\n      - name: Search backend pagination regression\n        run: python audit/regression_search_pagination.py\n"""
if anchor not in ci_text:
    raise SystemExit("CI pagination anchor missing")
ci.write_text(ci_text.replace(anchor, insert, 1), encoding="utf-8")

print("Applied deterministic search pagination/filter patch")
