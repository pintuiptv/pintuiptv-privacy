#!/usr/bin/env python3
"""Generate provider-agnostic Pintu video trend documents from public Trakt data."""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api.trakt.tv"
SCHEMA_VERSION = 1
GENERATOR_VERSION = "1.1.0"
USER_AGENT = "PintuPlayer-Trends/1.0"
MAX_ITEMS = 100
MIN_VOTES = 500
OUTPUT = Path("trends/video")
TTL = {"trending": 43200, "popular": 43200, "new_releases": 43200, "top_rated": 86400, "most_watched_weekly": 43200, "movies_of_the_year": 86400, "shows_of_the_moment": 43200}
DIRECT = {"trending", "popular", "most_watched_weekly"}
ENDPOINTS = {
    ("movie", "trending"): "/movies/trending", ("movie", "popular"): "/movies/popular",
    ("movie", "most_watched_weekly"): "/movies/watched/weekly",
    ("show", "trending"): "/shows/trending", ("show", "popular"): "/shows/popular",
    ("show", "most_watched_weekly"): "/shows/watched/weekly",
}

def positive_int(value: Any) -> int | None:
    try: value = int(value)
    except (TypeError, ValueError): return None
    return value if value > 0 else None

def number(value: Any) -> float | None:
    try: value = float(value)
    except (TypeError, ValueError): return None
    return value if math.isfinite(value) and value >= 0 else None

def valid_imdb(value: Any) -> str | None:
    value = str(value or "").strip().lower()
    return value if re.fullmatch(r"tt\d+", value) else None

@dataclass
class Item:
    media: str; title: str; year: int | None; ids: dict[str, Any]
    released: str | None = None; first_aired: str | None = None
    rating: float | None = None; votes: int | None = None; watchers: int | None = None
    plays: int | None = None; source_positions: dict[str, int] = field(default_factory=dict)
    score: float | None = None

    @property
    def key(self) -> tuple:
        for name in ("trakt", "imdb", "tmdb"):
            if self.ids.get(name): return name, self.ids[name]
        if self.ids.get("slug"): return "slug", self.ids["slug"], self.year
        return "title", re.sub(r"\W+", " ", self.title.lower()).strip(), self.year

def normalize(raw: dict[str, Any], media: str, source: str, position: int, date_hint: str | None = None) -> Item | None:
    obj = raw.get("movie" if media == "movie" else "show", raw)
    if not isinstance(obj, dict): return None
    title = str(obj.get("title") or "").strip()
    if not title: return None
    ids_raw = obj.get("ids") if isinstance(obj.get("ids"), dict) else {}
    ids = {"trakt": positive_int(ids_raw.get("trakt")), "slug": str(ids_raw.get("slug") or "").strip() or None, "imdb": valid_imdb(ids_raw.get("imdb")), "tmdb": positive_int(ids_raw.get("tmdb"))}
    released = obj.get("released") if media == "movie" else None
    first_aired = obj.get("first_aired") if media == "show" else None
    if date_hint: released, first_aired = (date_hint, None) if media == "movie" else (None, date_hint)
    return Item(media, title, positive_int(obj.get("year")), ids, released, first_aired, number(obj.get("rating")), positive_int(obj.get("votes")), positive_int(raw.get("watchers") or raw.get("watcher_count") or obj.get("watchers")), positive_int(raw.get("plays") or obj.get("plays")), {source: position})

class TraktClient:
    def __init__(self, client_id: str, session: requests.Session | None = None, sleeper=time.sleep):
        if not client_id.strip(): raise ValueError("TRAKT_CLIENT_ID is missing")
        self.session = session or requests.Session(); self.sleeper = sleeper; self.cache: dict[tuple, Any] = {}
        self.headers = {"Content-Type": "application/json", "trakt-api-version": "2", "trakt-api-key": client_id.strip(), "User-Agent": USER_AGENT}

    def get(self, path: str, params: dict[str, Any] | None = None) -> list[Any]:
        params = params or {}; key = (path, tuple(sorted(params.items())))
        if key in self.cache: return self.cache[key]
        for attempt in range(3):
            try:
                response = self.session.get(BASE_URL + path, params=params, headers=self.headers, timeout=(5, 15))
                if 200 <= response.status_code < 300:
                    data = response.json() if response.content else []
                    if not isinstance(data, list): raise RuntimeError(f"unexpected JSON root for {path}")
                    self.cache[key] = data; return data
                if response.status_code in (400, 401, 403, 404): raise RuntimeError(f"Trakt HTTP {response.status_code} at {path}")
                if response.status_code == 429:
                    wait = min(30, positive_int(response.headers.get("Retry-After")) or 2 ** attempt)
                elif response.status_code >= 500: wait = 2 ** attempt
                else: raise RuntimeError(f"Trakt HTTP {response.status_code} at {path}")
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt == 2: raise RuntimeError(f"Trakt network failure at {path}") from exc
                wait = 2 ** attempt
            if attempt == 2: raise RuntimeError(f"Trakt request failed at {path}")
            self.sleeper(wait + random.random() / 10)
        raise AssertionError("unreachable")

def dedupe(items: list[Item]) -> list[Item]:
    out: dict[tuple, Item] = {}
    for item in items:
        old = out.get(item.key)
        if old is None: out[item.key] = item; continue
        for field_name in ("year", "released", "first_aired", "rating", "votes", "watchers", "plays"):
            if getattr(old, field_name) is None: setattr(old, field_name, getattr(item, field_name))
        old.source_positions.update({k: min(v, old.source_positions.get(k, v)) for k, v in item.source_positions.items()})
    return list(out.values())

def rank_score(position: int | None, length: int) -> float:
    return 0.0 if position is None or length <= 0 else max(0.0, 1.0 - (position - 1) / length)

def bayesian(items: list[Item], minimum: int = MIN_VOTES) -> list[Item]:
    valid = [x for x in items if x.rating is not None and x.votes is not None and x.votes >= min(minimum, max(50, max((i.votes or 0 for i in items), default=0) // 20))]
    if not valid: return []
    mean = sum(x.rating or 0 for x in valid) / len(valid)
    for x in valid:
        v = x.votes or 0; x.score = (v / (v + minimum)) * (x.rating or 0) + (minimum / (v + minimum)) * mean
    return sorted(valid, key=lambda x: (-(x.score or 0), -(x.votes or 0), -(x.rating or 0), x.title.lower()))

def composite(items: list[Item], weights: dict[str, float], algorithm: str) -> list[Item]:
    assert abs(sum(weights.values()) - 1) < 1e-9
    lengths = {name: max((p for x in items for key, p in x.source_positions.items() if key == name), default=1) for name in weights}
    for item in items:
        item.score = sum(weight * rank_score(item.source_positions.get(name), lengths[name]) for name, weight in weights.items())
    return sorted(items, key=lambda x: (-(x.score or 0), x.title.lower()))

def fetch_source(client: TraktClient, media: str, section: str) -> list[Item]:
    raw = client.get(ENDPOINTS[(media, section)], {"page": 1, "limit": 100, "extended": "full"})
    return [item for i, row in enumerate(raw, 1) if (item := normalize(row, media, section, i))]

def _utc_date(value: Any):
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError): return None

def filter_new_release_events(raw: list[Any], media: str, now: datetime) -> tuple[list[Item], dict[str, int]]:
    stats = {"candidates": len(raw), "included": 0, "old_year": 0, "future": 0, "missing_date": 0, "season_premiere": 0, "ordinary_episode": 0}
    result: list[Item] = []; today = now.date(); first_day = today.replace(month=1, day=1)
    for position, row in enumerate(raw, 1):
        if not isinstance(row, dict): stats["missing_date"] += 1; continue
        obj = row.get("movie" if media == "movie" else "show")
        if not isinstance(obj, dict): stats["missing_date"] += 1; continue
        if media == "show":
            episode = row.get("episode") if isinstance(row.get("episode"), dict) else {}
            season = positive_int(episode.get("season")); number_value = positive_int(episode.get("number"))
            if season != 1:
                stats["season_premiere"] += 1; continue
            if number_value != 1:
                stats["ordinary_episode"] += 1; continue
            date_value = _utc_date(obj.get("first_aired"))
        else:
            date_value = _utc_date(obj.get("released"))
        if date_value is None: stats["missing_date"] += 1; continue
        if positive_int(obj.get("year")) != now.year or date_value < first_day:
            stats["old_year"] += 1; continue
        if date_value > today: stats["future"] += 1; continue
        item = normalize(row, media, "new_releases", position)
        if item is None: stats["missing_date"] += 1; continue
        result.append(item); stats["included"] += 1
    return dedupe(result), stats

def calendar_source(client: TraktClient, media: str, now: datetime) -> tuple[list[Item], dict[str, int]]:
    start = datetime(now.year, 1, 1, tzinfo=timezone.utc); days = (now.date() - start.date()).days + 1
    path = f"/calendars/all/{'movies' if media == 'movie' else 'shows/premieres'}/{start.date().isoformat()}/{days}"
    raw = client.get(path, {"extended": "full"})
    return filter_new_release_events(raw, media, now)

def item_json(item: Item, rank: int, composite_rank: bool) -> dict[str, Any]:
    return {"rank": rank, "title": item.title, "year": item.year, "released": item.released, "firstAired": item.first_aired, "watchers": item.watchers, "plays": item.plays, "rating": item.rating, "votes": item.votes, "score": round(item.score, 6) if composite_rank and item.score is not None else None, "ids": item.ids}

def source_metadata(media: str, section: str, algorithm: str | None) -> dict[str, Any]:
    noun = "movies" if media == "movie" else "shows"
    if section in DIRECT:
        endpoint = f"{noun}/watched" if section == "most_watched_weekly" else f"{noun}/{section}"
        return {"provider": "trakt", "type": "official", "endpoint": endpoint, "period": "weekly" if section == "most_watched_weekly" else None}
    inputs = [f"{noun}/trending", f"{noun}/watched/weekly", f"{noun}/popular"]
    if section == "new_releases": inputs = [f"calendars/all/{'movies' if media == 'movie' else 'shows/premieres'}"]
    return {"provider": "trakt", "type": "pintu_composite", "inputs": inputs, "algorithm": algorithm}

def document(media: str, section: str, items: list[Item], generated: str, algorithm: str | None = None, quality: dict[str, int] | None = None) -> dict[str, Any]:
    composite_rank = section not in DIRECT
    payload = [item_json(item, rank, composite_rank) for rank, item in enumerate(items[:MAX_ITEMS], 1)]
    return {"schemaVersion": SCHEMA_VERSION, "generatorVersion": GENERATOR_VERSION, "provider": "trakt", "rankingType": "pintu_composite" if composite_rank else "trakt_official", "algorithm": algorithm, "source": source_metadata(media, section, algorithm), "quality": quality or {"candidates": len(items), "included": len(payload)}, "mediaType": media, "section": section, "generatedAt": generated, "sourceUpdatedAt": None, "ttlSeconds": TTL[section], "itemCount": len(payload), "items": payload}

def validate(doc: dict[str, Any], now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    if doc.get("schemaVersion") != 1 or doc.get("provider") != "trakt" or doc.get("mediaType") not in ("movie", "show"): raise ValueError("invalid document header")
    items = doc.get("items");
    if not isinstance(items, list) or doc.get("itemCount") != len(items) or len(items) > 100: raise ValueError("invalid items")
    if [x.get("rank") for x in items] != list(range(1, len(items) + 1)): raise ValueError("invalid ranks")
    if any(not str(x.get("title") or "").strip() for x in items): raise ValueError("empty title")
    if doc["rankingType"] == "pintu_composite" and not doc.get("algorithm"): raise ValueError("missing composite algorithm")
    if not isinstance(doc.get("source"), dict) or doc["source"].get("provider") != "trakt": raise ValueError("missing source metadata")
    expected_ranking = "trakt_official" if doc.get("section") in DIRECT else "pintu_composite"
    expected_source_type = "official" if doc.get("section") in DIRECT else "pintu_composite"
    if doc.get("rankingType") != expected_ranking or doc["source"].get("type") != expected_source_type: raise ValueError("inconsistent ranking source")
    if doc.get("section") == "most_watched_weekly" and doc["source"].get("period") != "weekly": raise ValueError("missing weekly period")
    if doc.get("section") in ("new_releases", "movies_of_the_year"):
        date_field = "released" if doc["mediaType"] == "movie" else "firstAired"
        for item in items:
            value = _utc_date(item.get(date_field))
            if item.get("year") != now.year or value is None or value.year != now.year or value > now.date(): raise ValueError("semantically invalid current-year item")
    serialized = json.dumps(doc)
    if any(term in serialized for term in ("trakt-api-key", "Authorization", "TRAKT_CLIENT_ID", "client_secret", "access_token")): raise ValueError("sensitive output")

def validate_index(index: dict[str, Any], now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    if index.get("schemaVersion") != 1 or index.get("provider") != "trakt": raise ValueError("invalid index header")
    movies = index.get("movies"); series = index.get("series")
    if not isinstance(movies, list) or not isinstance(series, list): raise ValueError("invalid index sections")
    if index.get("movieSections") != len(movies) or index.get("seriesSections") != len(series): raise ValueError("invalid section counts")
    for field_name in ("generatedAt", "lastSuccessfulRefreshAt", "lastContentUpdateAt"):
        raw = index.get(field_name)
        try: value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError as exc: raise ValueError(f"invalid {field_name}") from exc
        if value.tzinfo is None or value > now + timedelta(minutes=10): raise ValueError(f"invalid {field_name}")
    if index["lastSuccessfulRefreshAt"] != index["generatedAt"]: raise ValueError("refresh timestamp mismatch")

def build_all(client: TraktClient, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    now = now or datetime.now(timezone.utc); generated = now.isoformat(timespec="seconds").replace("+00:00", "Z"); result = {}
    for media, folder in (("movie", "movies"), ("show", "series")):
        sources = {name: fetch_source(client, media, name) for name in DIRECT}
        pool = dedupe(sum(sources.values(), [])); top = bayesian(pool)
        new, new_quality = calendar_source(client, media, now)
        sections: dict[str, tuple[list[Item], str | None]] = {name: (values, None) for name, values in sources.items()}
        sections["new_releases"] = (sorted(new, key=lambda x: (x.released or x.first_aired or "", x.title), reverse=True), "recent_public_calendar_v1")
        sections["top_rated"] = (top, "bayesian_weighted_rating_v1")
        if media == "movie":
            year_items = [x for x in pool + new if x.year == now.year and _utc_date(x.released) is not None and datetime(now.year, 1, 1, tzinfo=timezone.utc).date() <= _utc_date(x.released) <= now.date()]
            sections["movies_of_the_year"] = (composite(dedupe(year_items), {"trending": .35, "most_watched_weekly": .25, "popular": .20, "top_rated": .20}, "movies_of_the_year_v1"), "movies_of_the_year_v1")
        else:
            sections["shows_of_the_moment"] = (composite(pool, {"trending": .40, "most_watched_weekly": .30, "popular": .15, "top_rated": .15}, "shows_of_the_moment_v1"), "shows_of_the_moment_v1")
        for section, (items, algorithm) in sections.items(): result[f"{folder}/{section}.json"] = document(media, section, items, generated, algorithm, new_quality if section == "new_releases" else None)
    entries = lambda folder: [{"section": path.rsplit("/", 1)[1][:-5], "path": path, "rankingType": doc["rankingType"], "ttlSeconds": doc["ttlSeconds"], "itemCount": doc["itemCount"]} for path, doc in result.items() if path.startswith(folder + "/")]
    movies = entries("movies"); series = entries("series")
    result["index.json"] = {"schemaVersion": 1, "generatorVersion": GENERATOR_VERSION, "provider": "trakt", "movieSections": len(movies), "seriesSections": len(series), "generatedAt": generated, "lastSuccessfulRefreshAt": generated, "lastContentUpdateAt": generated, "minimumAppSchemaVersion": 1, "movies": movies, "series": series}
    for path, doc in result.items():
        if path != "index.json": validate(doc, now)
    validate_index(result["index.json"], now)
    return result

def publish(documents: dict[str, dict[str, Any]], output: Path = OUTPUT) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    index = documents["index.json"]
    previous_index = None
    try:
        previous_index = json.loads((output / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass

    def logical(data: dict[str, Any]) -> dict[str, Any]:
        ignored = {"generatedAt", "lastSuccessfulRefreshAt", "lastContentUpdateAt", "sourceUpdatedAt"}
        return {key: value for key, value in data.items() if key not in ignored}

    changed = False
    for path, data in documents.items():
        if path == "index.json":
            continue
        try:
            previous = json.loads((output / path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
        if previous is None or logical(previous) != logical(data):
            changed = True
            break
    if not changed and previous_index and previous_index.get("lastContentUpdateAt"):
        index["lastContentUpdateAt"] = previous_index["lastContentUpdateAt"]
    with tempfile.TemporaryDirectory(prefix="video-trends-", dir=output.parent) as temp:
        root = Path(temp)
        for path, data in documents.items():
            target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        backup = output.with_name(output.name + ".previous")
        if backup.exists(): shutil.rmtree(backup)
        if output.exists(): output.rename(backup)
        try: root.rename(output)
        except Exception:
            if backup.exists(): backup.rename(output)
            raise
        if backup.exists(): shutil.rmtree(backup)

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--validate-only", action="store_true"); parser.add_argument("--output-dir", type=Path, default=OUTPUT); args = parser.parse_args()
    if args.validate_only:
        for path in args.output_dir.rglob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"));
            if path.name != "index.json": validate(data)
            else: validate_index(data)
        print("[VIDEO_TRENDS] validation passed"); return 0
    client_id = os.environ.get("TRAKT_CLIENT_ID", "")
    if not client_id.strip(): print("[VIDEO_TRENDS] error: TRAKT_CLIENT_ID is missing"); return 2
    try: documents = build_all(TraktClient(client_id)); publish(documents, args.output_dir)
    except Exception as exc: print(f"[VIDEO_TRENDS] generation failed: {exc}"); return 1
    print(f"[VIDEO_TRENDS] generated documents={len(documents)}"); return 0

if __name__ == "__main__": raise SystemExit(main())
