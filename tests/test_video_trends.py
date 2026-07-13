import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from datetime import datetime, timezone

from scripts.update_trakt_video_trends import Item, TraktClient, bayesian, build_all, dedupe, document, filter_new_release_events, publish, validate

class VideoTrendsTests(unittest.TestCase):
    NOW = datetime(2026, 7, 13, tzinfo=timezone.utc)

    def show_event(self, year, first_aired, season=1, episode=1, title="Show"):
        return {"first_aired": "2026-07-01T20:00:00Z", "episode": {"season": season, "number": episode}, "show": {"title": title, "year": year, "first_aired": first_aired, "ids": {"trakt": year}}}

    def movie_event(self, year, released, title="Movie"):
        return {"movie": {"title": title, "year": year, "released": released, "ids": {"trakt": year}}}

    def test_series_new_releases_require_absolute_current_year_debut(self):
        rows = [
            self.show_event(2023, "2023-01-01T00:00:00Z", title="Old episode"),
            self.show_event(2024, "2024-01-01T00:00:00Z", season=2, title="New season"),
            self.show_event(2025, "2025-01-01T00:00:00Z", season=3, title="Season premiere"),
            self.show_event(2026, "2026-06-01T00:00:00Z", title="New show"),
            self.show_event(2026, "2026-08-01T00:00:00Z", title="Future show"),
            self.show_event(2026, None, title="Missing date"),
            self.show_event(2026, "2026-06-01T00:00:00Z", episode=2, title="Ordinary episode"),
        ]
        items, stats = filter_new_release_events(rows, "show", self.NOW)
        self.assertEqual([item.title for item in items], ["New show"])
        self.assertEqual(stats["included"], 1)
        self.assertGreaterEqual(stats["season_premiere"], 2)
        self.assertEqual(stats["ordinary_episode"], 1)

    def test_movie_new_releases_require_released_current_year_not_future(self):
        rows = [self.movie_event(2025, "2025-12-31", "Old"), self.movie_event(2026, "2026-06-01", "Released"), self.movie_event(2026, "2026-08-01", "Future")]
        items, stats = filter_new_release_events(rows, "movie", self.NOW)
        self.assertEqual([item.title for item in items], ["Released"])
        self.assertEqual(stats["old_year"], 1)
        self.assertEqual(stats["future"], 1)

    def test_new_release_validator_rejects_old_or_future_items(self):
        old = Item("show", "Old", 2025, {"trakt": 1}, first_aired="2025-01-01")
        future = Item("movie", "Future", 2026, {"trakt": 2}, released="2026-08-01")
        with self.assertRaises(ValueError): validate(document("show", "new_releases", [old], "2026-07-13T00:00:00Z", "recent_public_calendar_v1"), self.NOW)
        with self.assertRaises(ValueError): validate(document("movie", "new_releases", [future], "2026-07-13T00:00:00Z", "recent_public_calendar_v1"), self.NOW)

    def test_source_metadata_and_ranking_types(self):
        item = Item("show", "Show", 2026, {"trakt": 1})
        direct = document("show", "most_watched_weekly", [item], "2026-07-13T00:00:00Z")
        composite_doc = document("show", "top_rated", [item], "2026-07-13T00:00:00Z", "bayesian_weighted_rating_v1")
        self.assertEqual(direct["rankingType"], "trakt_official")
        self.assertEqual(direct["source"]["period"], "weekly")
        self.assertEqual(composite_doc["rankingType"], "pintu_composite")
        self.assertEqual(composite_doc["source"]["type"], "pintu_composite")

    def test_all_rankings_audit_with_mock_provider(self):
        movie = {"title": "Movie 2026", "year": 2026, "released": "2026-05-01", "rating": 8.5, "votes": 5000, "ids": {"trakt": 1}}
        old_movie = {"title": "Movie 2025", "year": 2025, "released": "2025-12-01", "rating": 9.0, "votes": 9000, "ids": {"trakt": 3}}
        future_movie = {"title": "Future movie", "year": 2026, "released": "2026-08-01", "rating": 9.0, "votes": 9000, "ids": {"trakt": 4}}
        show = {"title": "Show 2026", "year": 2026, "first_aired": "2026-04-01T00:00:00Z", "rating": 8.2, "votes": 4000, "ids": {"trakt": 2}}
        old_show = {"title": "Old trending show", "year": 2024, "first_aired": "2024-04-01T00:00:00Z", "rating": 9.0, "votes": 9000, "ids": {"trakt": 5}}
        class FakeClient:
            def get(self, path, params=None):
                if "calendars/all/movies" in path: return [{"movie": movie}]
                if "calendars/all/shows/premieres" in path: return [{"episode": {"season": 1, "number": 1}, "show": show}]
                if path.startswith("/movies"): return [{"movie": movie, "watchers": 100}, {"movie": old_movie, "watchers": 200}, {"movie": future_movie, "watchers": 300}]
                return [{"show": show, "watchers": 100}, {"show": old_show, "watchers": 200}]
        docs = build_all(FakeClient(), self.NOW)
        self.assertEqual(len([p for p in docs if p.startswith("movies/")]), 6)
        self.assertEqual(len([p for p in docs if p.startswith("series/")]), 6)
        self.assertEqual(docs["series/new_releases.json"]["items"][0]["year"], 2026)
        self.assertEqual(docs["series/new_releases.json"]["items"][0]["firstAired"], "2026-04-01T00:00:00Z")
        self.assertNotIn("Old trending show", [item["title"] for item in docs["series/new_releases.json"]["items"]])
        self.assertEqual([item["title"] for item in docs["movies/movies_of_the_year.json"]["items"]], ["Movie 2026"])
        for path, doc in docs.items():
            if path == "index.json": continue
            self.assertEqual(doc["rankingType"], "trakt_official" if doc["section"] in {"trending", "popular", "most_watched_weekly"} else "pintu_composite")
            self.assertIn("source", doc)
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
