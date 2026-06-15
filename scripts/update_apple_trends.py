import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jwt
import requests

REPO_RAW_BASE_URL = "https://raw.githubusercontent.com/pintuiptv/pintuiptv-privacy/main"
OUTPUT_DIR = Path("trends")
SECTIONS_DIR = OUTPUT_DIR / "sections"
STATE_DIR = OUTPUT_DIR / "state"
PREVIOUS_RANKINGS_PATH = STATE_DIR / "previous_rankings.json"

APPLE_API_BASE = "https://api.music.apple.com/v1/catalog"
APPLE_RSS_BASE = "https://rss.applemarketingtools.com/api/v2"

STOREFRONTS = [
{"id": "it", "label": "Italia", "language": "it-IT"},
{"id": "us", "label": "Stati Uniti", "language": "en-US"},
{"id": "gb", "label": "Regno Unito", "language": "en-GB"},
{"id": "fr", "label": "Francia", "language": "fr-FR"},
{"id": "de", "label": "Germania", "language": "de-DE"},
{"id": "es", "label": "Spagna", "language": "es-ES"},
{"id": "br", "label": "Brasile", "language": "pt-BR"},
{"id": "jp", "label": "Giappone", "language": "ja-JP"},
]

TYPES = ["songs"]
LIMIT = 100

GENRE_STOREFRONTS = [
{"id": "it", "label": "Italia", "language": "it-IT"},
]

TARGET_GENRES = [
{
"title": "Top Pop",
"aliases": ["pop"],
},
{
"title": "Top Dance",
"aliases": ["dance", "dance/elettronica", "dance/electronic", "electronic", "elettronica"],
},
{
"title": "Top Rock",
"aliases": ["rock"],
},
{
"title": "Top Hip-Hop/Rap",
"aliases": ["hip-hop/rap", "hip hop/rap", "hip-hop", "hip hop", "rap"],
},
{
"title": "Top R&B/Soul",
"aliases": ["r&b/soul", "rnb/soul", "r&b", "soul"],
},
{
"title": "Top Latin",
"aliases": ["latin", "latina", "latino", "música latina", "musica latina"],
},
]

def utc_now_iso() -> str:
return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def slugify(value: str) -> str:
value = value.lower().strip()
value = value.replace("_", "-")
value = re.sub(r"[^a-z0-9]+", "-", value)
value = re.sub(r"-+", "-", value)
return value.strip("-") or "unknown"

def normalize_text(value: Optional[str]) -> str:
if not value:
return ""
value = value.lower().strip()
value = re.sub(r"\s+", " ", value)
value = re.sub(r"[^\w\sàèéìòùáíóúäöüñç&/-]", "", value, flags=re.IGNORECASE)
return value

def normalized_track_key(item: Dict[str, Any]) -> str:
title = normalize_text(item.get("title"))
artist = normalize_text(item.get("artist"))
return f"{title}::{artist}"

def require_env(name: str) -> str:
value = os.environ.get(name)
if not value:
raise RuntimeError(f"Missing required environment variable: {name}")
return value

def build_apple_developer_token() -> str:
team_id = require_env("APPLE_TEAM_ID")
key_id = require_env("APPLE_KEY_ID")
private_key_base64 = require_env("APPLE_PRIVATE_KEY_BASE64")

```
private_key = base64.b64decode(private_key_base64).decode("utf-8")

now = datetime.now(timezone.utc)
payload = {
    "iss": team_id,
    "iat": int(now.timestamp()),
    "exp": int((now + timedelta(days=30)).timestamp()),
}
headers = {
    "alg": "ES256",
    "kid": key_id,
}

return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)
```

def apple_get(path: str, token: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
url = f"{APPLE_API_BASE}{path}"
response = requests.get(
url,
headers={
"Authorization": f"Bearer {token}",
"Accept": "application/json",
},
params=params or {},
timeout=30,
)
response.raise_for_status()
return response.json()

def http_get_json(url: str) -> Dict[str, Any]:
response = requests.get(
url,
headers={
"Accept": "application/json",
"User-Agent": "PintuTrendsBot/1.0",
},
timeout=30,
)
response.raise_for_status()
return response.json()

def normalize_artwork_url(artwork: Optional[Dict[str, Any]]) -> Optional[str]:
if not artwork:
return None

```
url = artwork.get("url")
if not url:
    return None

return url.replace("{w}", "600").replace("{h}", "600")
```

def normalize_song_item(
item: Dict[str, Any],
rank: int,
provider: str,
storefront: str,
) -> Optional[Dict[str, Any]]:
attrs = item.get("attributes") or {}

```
title = attrs.get("name")
artist = attrs.get("artistName")
album = attrs.get("albumName")
artwork_url = normalize_artwork_url(attrs.get("artwork"))
source_url = attrs.get("url")

previews = attrs.get("previews") or []
preview_url = None

if previews and isinstance(previews, list):
    first_preview = previews[0] or {}
    preview_url = first_preview.get("url")

provider_track_id = item.get("id")

if not title or not artist:
    return None

return {
    "rank": rank,
    "title": title,
    "artist": artist,
    "album": album,
    "artworkUrl": artwork_url,
    "provider": provider,
    "storefront": storefront,
    "providerTrackId": provider_track_id,
    "sourceUrl": source_url,
    "previewUrl": preview_url,
}
```

def normalize_rss_song_item(
item: Dict[str, Any],
rank: int,
provider: str,
storefront: str,
) -> Optional[Dict[str, Any]]:
title = item.get("name")
artist = item.get("artistName")
album = item.get("collectionName") or item.get("albumName")
artwork_url = (
item.get("artworkUrl100")
or item.get("artworkUrl")
or item.get("artworkUrl60")
)
source_url = item.get("url")
provider_track_id = item.get("id")
release_date = item.get("releaseDate")

```
if artwork_url:
    artwork_url = re.sub(r"/\d+x\d+bb\.", "/600x600bb.", artwork_url)

if not title or not artist:
    return None

return {
    "rank": rank,
    "title": title,
    "artist": artist,
    "album": album,
    "artworkUrl": artwork_url,
    "provider": provider,
    "storefront": storefront,
    "providerTrackId": provider_track_id,
    "sourceUrl": source_url,
    "previewUrl": None,
    "releaseDate": release_date,
}
```

def get_chart_key(chart_obj: Dict[str, Any]) -> Optional[str]:
chart_key = chart_obj.get("chart")
if chart_key:
return str(chart_key)

```
attrs = chart_obj.get("attributes") or {}
chart_key = attrs.get("chart")
if chart_key:
    return str(chart_key)

return None
```

def get_chart_name(chart_obj: Dict[str, Any]) -> Optional[str]:
name = chart_obj.get("name")
if name:
return str(name)

```
attrs = chart_obj.get("attributes") or {}
name = attrs.get("name") or attrs.get("title")
if name:
    return str(name)

chart_key = get_chart_key(chart_obj)
if chart_key:
    return chart_key.replace("-", " ").title()

return None
```

def discover_apple_charts(
token: str,
storefront: str,
language: str,
item_type: str,
chart: Optional[str] = None,
genre: Optional[str] = None,
) -> List[Dict[str, Any]]:
params: Dict[str, Any] = {
"types": item_type,
"limit": LIMIT,
"l": language,
}

```
if chart:
    params["chart"] = chart

if genre:
    params["genre"] = genre

data = apple_get(
    f"/{storefront}/charts",
    token,
    params=params,
)

results = data.get("results") or {}
charts = results.get(item_type) or []

if not isinstance(charts, list):
    return []

return charts
```

def discover_apple_genres(token: str, storefront: str, language: str) -> List[Dict[str, Any]]:
data = apple_get(
f"/{storefront}/genres",
token,
params={
"limit": 200,
"l": language,
},
)

```
genres = data.get("data") or []
if not isinstance(genres, list):
    return []

return genres
```

def clean_item_for_output(item: Dict[str, Any], rank: int) -> Dict[str, Any]:
cleaned = {key: value for key, value in item.items() if not key.startswith("_")}
cleaned["rank"] = rank
return cleaned

def section_items_url(section_file_name: str) -> str:
return f"{REPO_RAW_BASE_URL}/trends/sections/{section_file_name}"

def write_section(
section_id: str,
title: str,
subtitle: str,
provider: str,
storefront: str,
item_type: str,
chart: str,
updated_at: str,
items: List[Dict[str, Any]],
extra_fields: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
if not items:
return None

```
normalized_items = [
    clean_item_for_output(item, rank=index)
    for index, item in enumerate(items[:LIMIT], start=1)
]

section_file_name = f"{section_id}.json"
items_url = section_items_url(section_file_name)

common_fields: Dict[str, Any] = {
    "version": 1,
    "id": section_id,
    "title": title,
    "subtitle": subtitle,
    "provider": provider,
    "storefront": storefront,
    "type": item_type,
    "chart": chart,
    "updatedAt": updated_at,
}

if extra_fields:
    common_fields.update(extra_fields)

section_detail = {
    **common_fields,
    "items": normalized_items,
}

section_index = {
    "id": section_id,
    "title": title,
    "subtitle": subtitle,
    "provider": provider,
    "storefront": storefront,
    "type": item_type,
    "chart": chart,
    "itemsUrl": items_url,
    "updatedAt": updated_at,
    "itemsCount": len(normalized_items),
}

if extra_fields:
    section_index.update(extra_fields)

section_path = SECTIONS_DIR / section_file_name
section_path.write_text(
    json.dumps(section_detail, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

return section_index
```

def build_section_from_chart(
chart_obj: Dict[str, Any],
storefront: str,
storefront_label: str,
item_type: str,
provider: str,
updated_at: str,
title_override: Optional[str] = None,
subtitle_override: Optional[str] = None,
section_id_override: Optional[str] = None,
extra_fields: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], Optional[str]]:
chart_key = get_chart_key(chart_obj)
chart_name = get_chart_name(chart_obj)

```
if not chart_key or not chart_name:
    return None, [], None

section_id = section_id_override or f"{provider}_{storefront}_{item_type}_{slugify(chart_key)}"

items_raw = chart_obj.get("data") or []
items: List[Dict[str, Any]] = []

if isinstance(items_raw, list):
    for index, raw_item in enumerate(items_raw, start=1):
        normalized = normalize_song_item(
            raw_item,
            rank=index,
            provider=provider,
            storefront=storefront,
        )
        if normalized:
            items.append(normalized)

if not items:
    return None, [], chart_key

display_title = title_override or f"{chart_name} - {storefront_label}"
subtitle = subtitle_override or f"Classifica Apple Music - {storefront_label}"

section_index = write_section(
    section_id=section_id,
    title=display_title,
    subtitle=subtitle,
    provider=provider,
    storefront=storefront,
    item_type=item_type,
    chart=chart_key,
    updated_at=updated_at,
    items=items,
    extra_fields=extra_fields,
)

return section_index, items, chart_key
```

def match_target_genres(genres: List[Dict[str, Any]]) -> List[Dict[str, str]]:
matched: List[Dict[str, str]] = []
used_genre_ids = set()

```
normalized_genres: List[Dict[str, str]] = []
for genre in genres:
    genre_id = genre.get("id")
    attrs = genre.get("attributes") or {}
    genre_name = attrs.get("name")

    if not genre_id or not genre_name:
        continue

    normalized_genres.append(
        {
            "id": str(genre_id),
            "name": str(genre_name),
            "normalizedName": normalize_text(str(genre_name)),
        }
    )

for target in TARGET_GENRES:
    target_title = target["title"]
    aliases = [normalize_text(alias) for alias in target["aliases"]]

    found = None
    for genre in normalized_genres:
        if genre["id"] in used_genre_ids:
            continue

        genre_name = genre["normalizedName"]
        if any(alias == genre_name or alias in genre_name for alias in aliases):
            found = genre
            break

    if found:
        used_genre_ids.add(found["id"])
        matched.append(
            {
                "id": found["id"],
                "name": found["name"],
                "title": target_title,
            }
        )

return matched
```

def build_global_items(country_top_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
scores: Dict[str, Dict[str, Any]] = {}

```
for item in country_top_items:
    key = normalized_track_key(item)
    if not key or key == "::":
        continue

    rank = item.get("rank")
    if not isinstance(rank, int):
        try:
            rank = int(rank)
        except Exception:
            rank = LIMIT

    score = max(1, 101 - rank)

    if key not in scores:
        scores[key] = {
            "score": 0,
            "bestRank": rank,
            "countries": set(),
            "item": item,
        }

    scores[key]["score"] += score
    scores[key]["bestRank"] = min(scores[key]["bestRank"], rank)

    storefront = item.get("storefront")
    if storefront:
        scores[key]["countries"].add(storefront)

    if rank < scores[key]["bestRank"]:
        scores[key]["item"] = item

ranked = sorted(
    scores.values(),
    key=lambda value: (
        value["score"],
        len(value["countries"]),
        -value["bestRank"],
    ),
    reverse=True,
)

output: List[Dict[str, Any]] = []
for index, record in enumerate(ranked[:LIMIT], start=1):
    base_item = dict(record["item"])
    original_provider = base_item.get("provider")
    base_item["rank"] = index
    base_item["provider"] = "pintu_trends"
    base_item["originalProvider"] = original_provider
    base_item["chart"] = "derived-global"
    base_item["storefront"] = "global"
    base_item["_score"] = int(record["score"])
    base_item["_countriesCount"] = len(record["countries"])
    output.append(base_item)

return output
```

def load_previous_rankings() -> Dict[str, Any]:
if not PREVIOUS_RANKINGS_PATH.exists():
return {}

```
try:
    return json.loads(PREVIOUS_RANKINGS_PATH.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"Warning: unable to read previous rankings: {exc}")
    return {}
```

def save_previous_rankings(updated_at: str, global_items: List[Dict[str, Any]]) -> None:
tracks: Dict[str, Any] = {}

```
for item in global_items:
    key = normalized_track_key(item)
    if not key or key == "::":
        continue

    tracks[key] = {
        "rank": item.get("rank"),
        "score": item.get("_score", 0),
        "title": item.get("title"),
        "artist": item.get("artist"),
    }

payload = {
    "updatedAt": updated_at,
    "tracks": tracks,
}

STATE_DIR.mkdir(parents=True, exist_ok=True)
PREVIOUS_RANKINGS_PATH.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
```

def build_viral_items(global_items: List[Dict[str, Any]], previous_rankings: Dict[str, Any]) -> List[Dict[str, Any]]:
previous_tracks = previous_rankings.get("tracks") or {}
viral_candidates: List[Dict[str, Any]] = []

```
for item in global_items:
    key = normalized_track_key(item)
    if not key or key == "::":
        continue

    current_rank = item.get("rank")
    if not isinstance(current_rank, int):
        try:
            current_rank = int(current_rank)
        except Exception:
            current_rank = LIMIT

    previous = previous_tracks.get(key)
    current_base_score = max(1, 101 - current_rank)

    if not previous:
        viral_score = 250 + current_base_score
    else:
        previous_rank = previous.get("rank")
        try:
            previous_rank = int(previous_rank)
        except Exception:
            previous_rank = LIMIT

        improvement = previous_rank - current_rank
        if improvement > 0:
            viral_score = current_base_score + improvement * 10
        else:
            viral_score = 0

    if viral_score <= 0:
        continue

    viral_item = dict(item)
    viral_item["provider"] = "pintu_trends"
    viral_item["storefront"] = "global"
    viral_item["chart"] = "derived-viral"
    viral_item["_viralScore"] = viral_score
    viral_candidates.append(viral_item)

viral_candidates.sort(
    key=lambda value: (
        value.get("_viralScore", 0),
        value.get("_score", 0),
    ),
    reverse=True,
)

output: List[Dict[str, Any]] = []
for index, item in enumerate(viral_candidates[:LIMIT], start=1):
    item_copy = dict(item)
    item_copy["rank"] = index
    output.append(item_copy)

return output
```

def fetch_new_releases_from_apple_rss(updated_at: str) -> Optional[Dict[str, Any]]:
# Apple Marketing Tools RSS può cambiare feed disponibili nel tempo.
# Proviamo più endpoint e, se non disponibili, saltiamo la sezione senza far fallire la Action.
candidate_urls = [
f"{APPLE_RSS_BASE}/it/music/new-music/{LIMIT}/songs.json",
f"{APPLE_RSS_BASE}/it/music/new-releases/{LIMIT}/songs.json",
f"{APPLE_RSS_BASE}/it/music/latest/{LIMIT}/songs.json",
]

```
for url in candidate_urls:
    try:
        print(f"Trying Apple RSS new releases: {url}")
        data = http_get_json(url)
        feed = data.get("feed") or {}
        results = feed.get("results") or []

        if not isinstance(results, list) or not results:
            continue

        items: List[Dict[str, Any]] = []
        for index, raw_item in enumerate(results, start=1):
            normalized = normalize_rss_song_item(
                raw_item,
                rank=index,
                provider="apple_music_rss",
                storefront="it",
            )
            if normalized:
                items.append(normalized)

        if not items:
            continue

        section = write_section(
            section_id="apple_rss_it_new_music_songs",
            title="Nuove uscite - Italia",
            subtitle="Nuove uscite da Apple Music - Italia",
            provider="apple_music_rss",
            storefront="it",
            item_type="songs",
            chart="new-music",
            updated_at=updated_at,
            items=items,
        )

        if section:
            print(f"Generated Apple RSS new releases section with {len(items)} items.")
            return section

    except Exception as exc:
        print(f"Warning: Apple RSS endpoint failed: {url} - {exc}")

print("Warning: no Apple RSS new releases section generated.")
return None
```

def main() -> None:
token = build_apple_developer_token()

```
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SECTIONS_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)

previous_rankings = load_previous_rankings()

for old_file in SECTIONS_DIR.glob("*.json"):
    old_file.unlink()

updated_at = utc_now_iso()
sections: List[Dict[str, Any]] = []
country_top_items: List[Dict[str, Any]] = []

print("Starting Apple Music trends generation...")

for storefront in STOREFRONTS:
    storefront_id = storefront["id"]
    storefront_label = storefront["label"]
    language = storefront["language"]

    print(f"Fetching Apple Music charts for {storefront_label} ({storefront_id})...")

    for item_type in TYPES:
        try:
            charts = discover_apple_charts(
                token=token,
                storefront=storefront_id,
                language=language,
                item_type=item_type,
            )
        except Exception as exc:
            print(f"Warning: failed to fetch charts for {storefront_label}: {exc}")
            continue

        print(f"Found {len(charts)} chart(s) for {storefront_label}.")

        for chart_obj in charts:
            section, items, chart_key = build_section_from_chart(
                chart_obj=chart_obj,
                storefront=storefront_id,
                storefront_label=storefront_label,
                item_type=item_type,
                provider="apple_music",
                updated_at=updated_at,
            )

            if section:
                sections.append(section)
                print(f"Generated section: {section['title']} ({section['itemsCount']} items)")

            if chart_key == "most-played":
                country_top_items.extend(items)

print("Generating Apple Music genre sections...")

for storefront in GENRE_STOREFRONTS:
    storefront_id = storefront["id"]
    storefront_label = storefront["label"]
    language = storefront["language"]

    try:
        genres = discover_apple_genres(
            token=token,
            storefront=storefront_id,
            language=language,
        )
        matched_genres = match_target_genres(genres)
    except Exception as exc:
        print(f"Warning: failed to fetch genres for {storefront_label}: {exc}")
        matched_genres = []

    print(f"Matched {len(matched_genres)} target genre(s) for {storefront_label}.")

    for genre in matched_genres:
        genre_id = genre["id"]
        genre_name = genre["name"]
        genre_title = genre["title"]

        try:
            charts = discover_apple_charts(
                token=token,
                storefront=storefront_id,
                language=language,
                item_type="songs",
                chart="most-played",
                genre=genre_id,
            )
        except Exception as exc:
            print(f"Warning: failed to fetch genre chart {genre_name} for {storefront_label}: {exc}")
            continue

        generated_for_genre = False

        for chart_obj in charts:
            section_id = f"apple_music_{storefront_id}_songs_most-played_genre_{slugify(genre_name)}"

            section, items, _ = build_section_from_chart(
                chart_obj=chart_obj,
                storefront=storefront_id,
                storefront_label=storefront_label,
                item_type="songs",
                provider="apple_music",
                updated_at=updated_at,
                title_override=f"{genre_title} - {storefront_label}",
                subtitle_override=f"Classifica Apple Music {genre_name} - {storefront_label}",
                section_id_override=section_id,
                extra_fields={
                    "genre": genre_name,
                    "genreId": genre_id,
                },
            )

            if section:
                sections.append(section)
                generated_for_genre = True
                print(f"Generated genre section: {section['title']} ({section['itemsCount']} items)")
                break

        if not generated_for_genre:
            print(f"Warning: no genre section generated for {genre_name} - {storefront_label}")

print("Generating derived Top Global section...")

global_items = build_global_items(country_top_items)

if global_items:
    global_section = write_section(
        section_id="derived_global_top_songs",
        title="Top Global",
        subtitle="Classifica globale derivata dalle classifiche Apple Music per paese",
        provider="pintu_trends",
        storefront="global",
        item_type="songs",
        chart="derived-global",
        updated_at=updated_at,
        items=global_items,
    )

    if global_section:
        sections.append(global_section)
        print(f"Generated derived section: Top Global ({global_section['itemsCount']} items)")
else:
    print("Warning: no Top Global section generated.")

print("Generating derived Viral section...")

viral_items = build_viral_items(global_items, previous_rankings)

if viral_items:
    viral_section = write_section(
        section_id="derived_viral_songs",
        title="Viral",
        subtitle="Brani in crescita rispetto all’aggiornamento precedente",
        provider="pintu_trends",
        storefront="global",
        item_type="songs",
        chart="derived-viral",
        updated_at=updated_at,
        items=viral_items,
    )

    if viral_section:
        sections.append(viral_section)
        print(f"Generated derived section: Viral ({viral_section['itemsCount']} items)")
else:
    print("Warning: no Viral section generated.")

print("Generating Apple RSS new releases section...")

new_releases_section = fetch_new_releases_from_apple_rss(updated_at)
if new_releases_section:
    sections.append(new_releases_section)

if global_items:
    save_previous_rankings(updated_at, global_items)

sections.sort(
    key=lambda section: (
        0 if section["id"] == "apple_music_it_songs_most-played" else
        1 if section["id"] == "derived_global_top_songs" else
        2 if section["id"] == "derived_viral_songs" else
        3 if section["id"].startswith("apple_music_it_songs_most-played_genre_") else
        4,
        section.get("title", ""),
    )
)

index = {
    "version": 1,
    "updatedAt": updated_at,
    "sourceLabel": "Metadati classifiche Apple Music e categorie derivate Pintu Trends",
    "sections": sections,
}

(OUTPUT_DIR / "index.json").write_text(
    json.dumps(index, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print(f"Generated {len(sections)} trend sections.")

if not sections:
    raise RuntimeError("No trend sections generated.")
```

if **name** == "**main**":
main()
