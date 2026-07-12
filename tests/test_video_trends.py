import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts.update_trakt_video_trends import Item, TraktClient, bayesian, dedupe, document, publish, validate

class VideoTrendsTests(unittest.TestCase):
    def test_missing_secret(self):
        with self.assertRaises(ValueError): TraktClient("   ")

    def test_client_headers_and_request_cache(self):
        response = Mock(status_code=200, content=b"[]"); response.json.return_value = []
        session = Mock(); session.get.return_value = response
        client = TraktClient("mock-secret", session=session)
        self.assertEqual(client.get("/movies/trending"), [])
        self.assertEqual(client.get("/movies/trending"), [])
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(client.headers["User-Agent"], "PintuPlayer-Trends/1.0")

    def test_dedupe_and_bayesian(self):
        a = Item("movie", "A", 2026, {"trakt": 1}, rating=10, votes=2)
        b = Item("movie", "B", 2026, {"trakt": 2}, rating=8.7, votes=50000)
        self.assertEqual(len(dedupe([a, a])), 1)
        self.assertEqual(bayesian([a, b])[0].title, "B")

    def test_schema_and_atomic_publish(self):
        item = Item("movie", "Film", 2026, {"trakt": 1, "imdb": "tt1", "tmdb": 2, "slug": "film"})
        doc = document("movie", "trending", [item], "2026-07-12T00:00:00Z")
        validate(doc)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "video"
            publish({"movies/trending.json": doc, "index.json": {"schemaVersion": 1}}, output)
            self.assertEqual(json.loads((output / "movies/trending.json").read_text())["itemCount"], 1)

    def test_refresh_timestamp_changes_without_content_timestamp(self):
        item = Item("movie", "Film", 2026, {"trakt": 1})
        old = document("movie", "trending", [item], "2026-07-12T00:00:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "video"
            first = {"movies/trending.json": old, "index.json": {"schemaVersion": 1, "lastSuccessfulRefreshAt": "2026-07-12T00:00:00Z", "lastContentUpdateAt": "2026-07-12T00:00:00Z"}}
            publish(first, output)
            fresh = document("movie", "trending", [item], "2026-07-13T00:00:00Z")
            second = {"movies/trending.json": fresh, "index.json": {"schemaVersion": 1, "lastSuccessfulRefreshAt": "2026-07-13T00:00:00Z", "lastContentUpdateAt": "2026-07-13T00:00:00Z"}}
            publish(second, output)
            index = json.loads((output / "index.json").read_text())
            self.assertEqual(index["lastSuccessfulRefreshAt"], "2026-07-13T00:00:00Z")
            self.assertEqual(index["lastContentUpdateAt"], "2026-07-12T00:00:00Z")

if __name__ == "__main__": unittest.main()
