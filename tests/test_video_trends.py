import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from datetime import datetime, timezone

from scripts.update_trakt_video_trends import Item, MAX_ITEMS, TmdbRankingProvider, TraktClient, VideoRankingProvider, bayesian, build_all, calendar_source, dedupe, document, filter_new_release_events, generate_with_fallback, parse_iso_utc, publish, subtract_calendar_months, validate

class VideoTrendsTests(unittest.TestCase):
    NOW = datetime(2026, 7, 13, tzinfo=timezone.utc)

    def show_event(self, year, first_aired, season=1, episode=1, title="Show"):
        return {"first_aired": "2026-07-01T20:00:00Z", "episode": {"season": season, "number": episode}, "show": {"title": title, "year": year, "first_aired": first_aired, "ids": {"slug": title.lower().replace(" ", "-")}}}

    def movie_event(self, year, released, title="Movie"):
        return {"movie": {"title": title, "year": year, "released": released, "ids": {"slug": title.lower().replace(" ", "-")}}}

    def test_calendar_aware_cutoff(self):
        self.assertEqual(str(subtract_calendar_months(self.NOW.date())), "2026-03-13")
        self.assertEqual(str(subtract_calendar_months(datetime(2027, 1, 31, tzinfo=timezone.utc).date())), "2026-09-30")
        self.assertEqual(str(subtract_calendar_months(datetime(2024, 6, 30, tzinfo=timezone.utc).date())), "2024-02-29")

    def test_series_new_releases_require_absolute_premiere_in_window(self):
        rows = [
            self.show_event(2019, "2019-01-01T00:00:00Z", title="Old episode"),
            self.show_event(2024, "2024-01-01T00:00:00Z", season=2, title="New season"),
            self.show_event(2025, "2025-01-01T00:00:00Z", season=3, title="Season premiere"),
            self.show_event(2026, "2026-03-13T00:00:00Z", title="At cutoff"),
            self.show_event(2026, "2026-03-12T00:00:00Z", title="Before cutoff"),
            self.show_event(2026, "2026-06-01T00:00:00Z", title="New show"),
            self.show_event(2026, "2026-07-12T00:00:00Z", title="Yesterday"),
            self.show_event(2026, "2026-07-13T00:00:00Z", title="Today"),
            self.show_event(2026, "2026-08-01T00:00:00Z", title="Future show"),
            self.show_event(2026, None, title="Missing date"),
            self.show_event(2026, "2026-06-01T00:00:00Z", episode=2, title="Ordinary episode"),
        ]
        items, stats = filter_new_release_events(rows, "show", self.NOW)
        self.assertEqual([item.title for item in items], ["At cutoff", "New show", "Yesterday", "Today"])
        self.assertEqual(stats["validCandidates"], 4)
        self.assertGreaterEqual(stats["seasonPremieresExcluded"], 2)
        self.assertEqual(stats["ordinaryEpisodesExcluded"], 1)
        self.assertEqual(stats["olderThanFourMonthsExcluded"], 2)
        self.assertTrue(all(item.absolute_premiere and item.premiere_episode == "S01E01" for item in items))

    def test_movie_new_releases_use_four_calendar_month_window(self):
        rows = [self.movie_event(2026, "2026-03-13", "At cutoff"), self.movie_event(2026, "2026-03-12", "Before cutoff"), self.movie_event(2026, "2026-07-12", "Yesterday"), self.movie_event(2026, "2026-07-13", "Today"), self.movie_event(2026, "2026-08-01", "Future"), self.movie_event(2026, None, "Missing")]
        items, stats = filter_new_release_events(rows, "movie", self.NOW)
        self.assertEqual([item.title for item in items], ["At cutoff", "Yesterday", "Today"])
        self.assertEqual(stats["olderThanFourMonthsExcluded"], 1)
        self.assertEqual(stats["futureExcluded"], 1)
        self.assertEqual(stats["missingReleasedExcluded"], 1)

    def test_exact_utc_instant_for_series_and_movies(self):
        now = datetime(2026, 7, 13, 7, 48, 23, tzinfo=timezone.utc)
        rows = [
            self.show_event(2026, "2026-07-13T02:48:23.000Z", title="Five hours before"),
            self.show_event(2026, "2026-07-13T07:48:23Z", title="Exactly now"),
            self.show_event(2026, "2026-07-13T12:48:23.000Z", title="Five hours after"),
            self.show_event(2026, "2026-07-14T00:00:00Z", title="Tomorrow"),
            self.show_event(2026, "2026-07-13T09:00:00+02:00", title="Other timezone before"),
        ]
        items, stats = filter_new_release_events(rows, "show", now)
        self.assertEqual([x.title for x in items], ["Five hours before", "Exactly now", "Other timezone before"])
        self.assertEqual(stats["futureExcluded"], 2)
        movies, movie_stats = filter_new_release_events([
            self.movie_event(2026, "2026-07-13", "Date only today"),
            self.movie_event(2026, "2026-07-13T12:48:23Z", "Timestamp future"),
        ], "movie", now)
        self.assertEqual([x.title for x in movies], ["Date only today"])
        self.assertEqual(movie_stats["futureExcluded"], 1)

    def test_iso_parser_normalizes_aware_datetimes_to_utc(self):
        milliseconds = parse_iso_utc("2026-07-13T07:48:23.123Z")
        no_milliseconds = parse_iso_utc("2026-07-13T07:48:23Z")
        offset = parse_iso_utc("2026-07-13T09:48:23+02:00")
        self.assertEqual(milliseconds.instant.microsecond, 123000)
        self.assertEqual(no_milliseconds.instant, offset.instant)
        self.assertEqual(offset.instant.tzinfo, timezone.utc)
        self.assertFalse(offset.date_only)

    def test_generator_and_validator_reject_same_future_series_instant(self):
        now = datetime(2026, 7, 13, 7, 48, 23, tzinfo=timezone.utc)
        future_value = "2026-07-13T13:00:00.000Z"
        generated, stats = filter_new_release_events([self.show_event(2026, future_value, title="Future premiere")], "show", now)
        self.assertEqual(generated, [])
        self.assertEqual(stats["futureExcluded"], 1)
        future = Item("show", "Future premiere", 2026, {"trakt": 99}, first_aired=future_value, absolute_premiere=True, premiere_episode="S01E01")
        doc = document("show", "new_releases", [future], "2026-07-13T07:48:23Z", "series_absolute_premieres_4_calendar_months_v2")
        with self.assertRaises(ValueError): validate(doc, now)

    def test_previous_year_is_valid_when_inside_cross_year_window(self):
        january = datetime(2027, 1, 13, tzinfo=timezone.utc)
        movie_items, _ = filter_new_release_events([self.movie_event(2026, "2026-11-01", "Movie")], "movie", january)
        show_items, _ = filter_new_release_events([self.show_event(2026, "2026-11-01T00:00:00Z", title="Series")], "show", january)
        self.assertEqual([x.title for x in movie_items], ["Movie"])
        self.assertEqual([x.title for x in show_items], ["Series"])

    def test_calendar_chunks_cover_window_and_deduplicate(self):
        duplicate = self.movie_event(2026, "2026-05-01", "Duplicate")
        class Client:
            def __init__(self): self.calls = []
            def get(self, path, params=None): self.calls.append(path); return [duplicate]
        client = Client(); items, stats = calendar_source(client, "movie", self.NOW)
        self.assertEqual(len(client.calls), 4)
        self.assertIn("/2026-03-13/31", client.calls[0])
        self.assertIn("/2026-06-14/30", client.calls[-1])
        self.assertEqual(stats["fetchedFromTrakt"], 4)
        self.assertEqual(stats["deduplicatedCandidates"], 1)
        self.assertEqual(len(items), 1)

    def test_new_release_validator_rejects_old_or_future_items(self):
        old = Item("show", "Old", 2025, {"trakt": 1}, first_aired="2025-01-01")
        future = Item("movie", "Future", 2026, {"trakt": 2}, released="2026-08-01")
        mismatched = Item("movie", "Mismatch", 2025, {"trakt": 3}, released="2026-06-01")
        with self.assertRaises(ValueError): validate(document("show", "new_releases", [old], "2026-07-13T00:00:00Z", "recent_public_calendar_v1"), self.NOW)
        with self.assertRaises(ValueError): validate(document("movie", "new_releases", [future], "2026-07-13T00:00:00Z", "recent_public_calendar_v1"), self.NOW)
        with self.assertRaises(ValueError): validate(document("movie", "new_releases", [mismatched], "2026-07-13T00:00:00Z", "movie_new_releases_4_calendar_months_v2"), self.NOW)

    def test_new_release_document_caps_items_and_has_consecutive_ranks(self):
        items = [Item("movie", f"Film {i}", 2026, {"trakt": i + 1}, released="2026-06-01") for i in range(MAX_ITEMS + 5)]
        doc = document("movie", "new_releases", items, "2026-07-13T00:00:00Z", "movie_new_releases_4_calendar_months_v2")
        validate(doc, self.NOW)
        self.assertEqual(doc["itemCount"], MAX_ITEMS)
        self.assertEqual([x["rank"] for x in doc["items"]], list(range(1, MAX_ITEMS + 1)))
        self.assertEqual(doc["source"]["window"]["from"], "2026-03-13")

    def test_source_metadata_and_ranking_types(self):
        item = Item("show", "Show", 2026, {"trakt": 1})
        direct = document("show", "most_watched_weekly", [item], "2026-07-13T00:00:00Z")
        composite_doc = document("show", "top_rated", [item], "2026-07-13T00:00:00Z", "bayesian_weighted_rating_v1")
        self.assertEqual(direct["rankingType"], "trakt_official")
        self.assertEqual(direct["source"]["period"], "weekly")
        self.assertEqual(composite_doc["rankingType"], "pintu_composite")
        self.assertEqual(composite_doc["source"]["type"], "pintu_composite")
        premiere = Item("show", "Premiere", 2026, {"trakt": 2}, first_aired="2026-06-01T00:00:00Z", absolute_premiere=True, premiere_episode="S01E01")
        new_doc = document("show", "new_releases", [premiere], "2026-07-13T00:00:00Z", "series_absolute_premieres_4_calendar_months_v2")
        self.assertTrue(new_doc["source"]["absolutePremiereOnly"])
        self.assertEqual(new_doc["source"]["window"]["value"], 4)
        self.assertNotIn("anticipated", json.dumps(new_doc).lower())
        validate(new_doc, self.NOW)

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

class ProviderFallbackTests(unittest.TestCase):
    NOW = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)

    class FakeProvider(VideoRankingProvider):
        def __init__(self, provider_id, failures=()): self.provider_id = provider_id; self.failures = set(failures); self.calls = []
        def build_media(self, media, now):
            self.calls.append(media)
            if media in self.failures: raise RuntimeError(f"{self.provider_id} HTTP 403 at {media}/trending")
            folder = "movies" if media == "movie" else "series"; generated = now.isoformat().replace("+00:00", "Z")
            result = {}
            for section in ("trending", "popular", "top_rated", "most_watched_weekly"):
                item = Item(media, f"{media} {section}", 2026, {"trakt": 1} if self.provider_id == "trakt" else {"tmdb": 1})
                algorithm = "bayesian_weighted_rating_v1" if self.provider_id == "trakt" and section == "top_rated" else (f"tmdb_official_{section}_v1" if self.provider_id == "tmdb" else None)
                result[f"{folder}/{section}.json"] = document(media, section, [item], generated, algorithm, provider=self.provider_id)
            return result

    def seed(self, output, provider="trakt"):
        docs = {}; generated = "2026-08-17T12:00:00Z"
        for media, folder in (("movie", "movies"), ("show", "series")):
            for section in ("trending", "popular", "top_rated", "most_watched_weekly"):
                algorithm = "bayesian_weighted_rating_v1" if provider == "trakt" and section == "top_rated" else (f"tmdb_official_{section}_v1" if provider == "tmdb" else None)
                docs[f"{folder}/{section}.json"] = document(media, section, [Item(media, "Cached", 2026, {provider: 9})], generated, algorithm, provider=provider)
        docs["index.json"] = {"schemaVersion": 1, "generatorVersion": "1.2.1", "provider": provider, "movieSections": 4, "seriesSections": 4,
                              "generatedAt": generated, "lastCheckedAt": generated, "lastSuccessfulRefreshAt": generated,
                              "lastContentUpdateAt": generated, "movies": [], "series": []}
        publish(docs, output)

    def test_trakt_success_never_calls_tmdb(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "video"; self.seed(output)
            trakt = self.FakeProvider("trakt"); tmdb = self.FakeProvider("tmdb")
            docs, report = generate_with_fallback(trakt, tmdb, output, self.NOW)
            self.assertEqual(trakt.calls, ["movie", "show"]); self.assertEqual(tmdb.calls, [])
            self.assertEqual(report["providers"], {"movie": "trakt", "show": "trakt"})
            self.assertEqual(docs["index.json"]["refreshStatus"], "success")

    def test_trakt_403_falls_back_atomically_to_tmdb(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "video"; self.seed(output)
            docs, report = generate_with_fallback(self.FakeProvider("trakt", {"movie", "show"}), self.FakeProvider("tmdb"), output, self.NOW)
            self.assertEqual(report["providers"], {"movie": "tmdb", "show": "tmdb"})
            for path, value in docs.items():
                if path.endswith(("trending.json", "popular.json", "top_rated.json", "most_watched_weekly.json")):
                    self.assertEqual(value["provider"], "tmdb"); self.assertEqual(value["rankingType"], "pintu_composite")

    def test_media_failures_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "video"; self.seed(output)
            _, report = generate_with_fallback(self.FakeProvider("trakt", {"movie"}), self.FakeProvider("tmdb"), output, self.NOW)
            self.assertEqual(report["providers"], {"movie": "tmdb", "show": "trakt"})

    def test_both_fail_preserves_last_valid_and_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "video"; self.seed(output, "tmdb")
            docs, report = generate_with_fallback(self.FakeProvider("trakt", {"movie", "show"}), self.FakeProvider("tmdb", {"movie", "show"}), output, self.NOW)
            publish(docs, output); docs["index.json"] = json.loads((output / "index.json").read_text())
            self.assertEqual(report["statuses"], {"movie": "stale_cache", "show": "stale_cache"})
            self.assertEqual(docs["index.json"]["refreshStatus"], "stale_cache")
            self.assertEqual(docs["index.json"]["lastSuccessfulRefreshAt"], "2026-08-17T12:00:00Z")
            self.assertEqual(docs["index.json"]["lastContentUpdateAt"], "2026-08-17T12:00:00Z")
            self.assertEqual(docs["movies/trending.json"]["provider"], "tmdb")

    def test_both_fail_without_last_valid_is_hard_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                generate_with_fallback(self.FakeProvider("trakt", {"movie"}), self.FakeProvider("tmdb", {"movie"}), Path(tmp) / "video", self.NOW)

    def test_tmdb_paginates_deduplicates_and_normalizes(self):
        class Client:
            def __init__(self): self.pages = []
            def get(self, path, params=None):
                self.pages.append(params["page"])
                rows = [{"id": 1228710, "title": "Film", "release_date": "2026-07-01", "vote_average": 8.2, "vote_count": 50, "popularity": 123.4}]
                if params["page"] == 2: rows = [{"id": 1228710, "title": "Duplicate"}, {"id": 2, "title": "Second", "release_date": "invalid"}]
                return {"results": rows, "total_pages": 2}
        client = Client(); items = TmdbRankingProvider(client)._items("movie", "popular")
        self.assertEqual(client.pages, [1, 2]); self.assertEqual([x.ids["tmdb"] for x in items], [1228710, 2])
        self.assertEqual(items[0].year, 2026); self.assertEqual(items[0].popularity, 123.4); self.assertIsNone(items[1].year)
        self.assertIn("Star Wars: The Mandalorian and Grogu", items[0].aliases)

if __name__ == "__main__": unittest.main()
