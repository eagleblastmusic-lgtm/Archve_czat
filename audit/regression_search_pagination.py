from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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
