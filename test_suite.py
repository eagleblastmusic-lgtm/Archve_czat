import unittest
import os
from fastapi.testclient import TestClient
from main import app
from scraper import parse_date_to_sort_seconds, sort_videos_newest_first

class TestArchivebateApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_status(self):
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 200)

    def test_chronological_sorting(self):
        vids = [
            {"id": "100", "date": "5 hours ago • 02.09.2026"},
            {"id": "101", "date": "2 hours ago • 02.09.2026"},
            {"id": "102", "date": "8 hours ago • 02.09.2026"},
            {"id": "103", "date": "6 hours ago • 02.09.2026"},
            {"id": "104", "date": "20 minutes ago • 02.09.2026"},
            {"id": "105", "date": "1 day ago • 01.09.2026"},
        ]
        sorted_vids = sort_videos_newest_first(vids)
        expected_ids = ["104", "101", "100", "103", "102", "105"]
        actual_ids = [v["id"] for v in sorted_vids]
        self.assertEqual(actual_ids, expected_ids)

    @unittest.skipUnless(os.getenv("ARCHIVEBATE_NETWORK_TESTS") == "1", "External source test; opt in explicitly")
    def test_search_endpoint(self):
        r = self.client.get("/api/search?q=trans&page=1")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("videos", data)

if __name__ == "__main__":
    unittest.main()
