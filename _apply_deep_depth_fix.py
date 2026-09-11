from pathlib import Path

# Make deep crawling actually go deep. The previous scheduler sorted incomplete
# profiles by pages_scanned ASC, so a growing discovery queue kept feeding new
# zero/one-page profiles ahead of models that were already at page 2/3. That
# produced excellent breadth but starved historical depth and completion.
path = Path("deep_archivebate.py")
text = path.read_text(encoding="utf-8")
old = """                ORDER BY priority DESC, updated_at ASC, pages_scanned ASC, model_key ASC\n"""
new = """                ORDER BY priority DESC, pages_scanned DESC, updated_at ASC, model_key ASC\n"""
assert text.count(old) == 1, "deep crawl selection query changed"
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

# Add a regression that proves a progressed profile wins over a fresh shallow
# profile of the same priority. This guards against breadth-first starvation.
reg = Path("audit/regression_deep_archivebate.py")
r = reg.read_text(encoding="utf-8")
marker = '\nprint("PASS: Archivebate deep discovery is durable and historical profile items merge into the live catalog")\n'
assert r.count(marker) == 1, "deep regression footer changed"
extra = r'''

# Progressed profiles must be continued before fresh shallow profiles of the same
# priority, otherwise a continuously growing discovery queue starves page 2+ forever.
class SelectionScraper:
    def __init__(self):
        self._cache = {}
        self.profile_calls = []

    def get_archivebate_model_videos(self, username, page=1):
        self.profile_calls.append((username, page))
        return []


with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "catalog.db"
    catalog = CatalogService(db)
    deep = DeepArchivebateService(db, request_delay=0.01)
    conn = deep._get_conn()
    conn.execute(
        """
        INSERT INTO archivebate_models(
            model_key, username, profile_url, discovered_from, priority,
            first_seen, updated_at, next_page, pages_scanned, videos_found,
            empty_streak, repeated_signatures, crawl_complete
        ) VALUES('deepmodel', 'deep_model', '', 'test', 200, 1, 10, 4, 3, 60, 0, 0, 0)
        """
    )
    conn.execute(
        """
        INSERT INTO archivebate_models(
            model_key, username, profile_url, discovered_from, priority,
            first_seen, updated_at, next_page, pages_scanned, videos_found,
            empty_streak, repeated_signatures, crawl_complete
        ) VALUES('shallowmodel', 'shallow_model', '', 'test', 200, 1, 1, 1, 0, 0, 0, 0, 0)
        """
    )
    fake = SelectionScraper()
    assert deep.crawl_step(fake) is True
    assert fake.profile_calls[0] == ('deep_model', 4), fake.profile_calls
    deep.close()
    catalog.close()
'''
r = r.replace(marker, extra + marker, 1)
reg.write_text(r, encoding="utf-8")
