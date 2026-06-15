import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import jwt
import requests


REPO_RAW_BASE_URL = "https://raw.githubusercontent.com/pintuiptv/pintuiptv-privacy/main"
OUTPUT_DIR = Path("trends")
SECTIONS_DIR = OUTPUT_DIR / "sections"

APPLE_API_BASE = "https://api.music.apple.com/v1/catalog"

STOREFRONTS = [
    {
        "id": "it",
        "label": "Italia",
        "language": "it-IT",
    }
]

TYPES = ["songs"]
LIMIT = 100


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = value.replace("_", "-")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-") or "unknown"


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_apple_developer_token() -> str:
    team_id = require_env("APPLE_TEAM_ID")
    key_id = require_env("APPLE_KEY_ID")
    private_key_base64 = require_env("APPLE_PRIVATE_KEY_BASE64")

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


def normalize_artwork_url(artwork: Optional[Dict[str, Any]]) -> Optional[str]:
    if not artwork:
        return None

    url = artwork.get("url")
    if not url:
        return None

    return url.replace("{w}", "600").replace("{h}", "600")


def normalize_song_item(item: Dict[str, Any], rank: int, provider: str, storefront: str) -> Optional[Dict[str, Any]]:
    attrs = item.get("attributes") or {}

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


def get_chart_key(chart_obj: Dict[str, Any]) -> Optional[str]:
    chart_key = chart_obj.get("chart")
    if chart_key:
        return str(chart_key)

    attrs = chart_obj.get("attributes") or {}
    chart_key = attrs.get("chart")
    if chart_key:
        return str(chart_key)

    return None


def get_chart_name(chart_obj: Dict[str, Any]) -> Optional[str]:
    name = chart_obj.get("name")
    if name:
        return str(name)

    attrs = chart_obj.get("attributes") or {}
    name = attrs.get("name") or attrs.get("title")
    if name:
        return str(name)

    chart_key = get_chart_key(chart_obj)
    if chart_key:
        return chart_key.replace("-", " ").title()

    return None


def discover_apple_charts(token: str, storefront: str, language: str, item_type: str) -> List[Dict[str, Any]]:
    data = apple_get(
        f"/{storefront}/charts",
        token,
        params={
            "types": item_type,
            "limit": LIMIT,
            "l": language,
        },
    )

    results = data.get("results") or {}
    charts = results.get(item_type) or []

    if not isinstance(charts, list):
        return []

    return charts


def build_section_from_chart(
    chart_obj: Dict[str, Any],
    storefront: str,
    storefront_label: str,
    item_type: str,
    provider: str,
    updated_at: str,
) -> Optional[Dict[str, Any]]:
    chart_key = get_chart_key(chart_obj)
    chart_name = get_chart_name(chart_obj)

    if not chart_key or not chart_name:
        return None

    section_id = f"{provider}_{storefront}_{item_type}_{slugify(chart_key)}"

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
        return None

    section_file_name = f"{section_id}.json"
    items_url = f"{REPO_RAW_BASE_URL}/trends/sections/{section_file_name}"

    section_detail = {
        "version": 1,
        "id": section_id,
        "title": chart_name,
        "subtitle": f"Classifica Apple Music - {storefront_label}",
        "provider": provider,
        "storefront": storefront,
        "type": item_type,
        "chart": chart_key,
        "updatedAt": updated_at,
        "items": items,
    }

    section_index = {
        "id": section_id,
        "title": chart_name,
        "subtitle": f"Classifica Apple Music - {storefront_label}",
        "provider": provider,
        "storefront": storefront,
        "type": item_type,
        "chart": chart_key,
        "itemsUrl": items_url,
        "updatedAt": updated_at,
        "itemsCount": len(items),
    }

    section_path = SECTIONS_DIR / section_file_name
    section_path.write_text(
        json.dumps(section_detail, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return section_index


def main() -> None:
    token = build_apple_developer_token()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SECTIONS_DIR.mkdir(parents=True, exist_ok=True)

    updated_at = utc_now_iso()
    sections: List[Dict[str, Any]] = []

    for storefront in STOREFRONTS:
        storefront_id = storefront["id"]
        storefront_label = storefront["label"]
        language = storefront["language"]

        for item_type in TYPES:
            charts = discover_apple_charts(
                token=token,
                storefront=storefront_id,
                language=language,
                item_type=item_type,
            )

            for chart_obj in charts:
                section = build_section_from_chart(
                    chart_obj=chart_obj,
                    storefront=storefront_id,
                    storefront_label=storefront_label,
                    item_type=item_type,
                    provider="apple_music",
                    updated_at=updated_at,
                )
                if section:
                    sections.append(section)

    index = {
        "version": 1,
        "updatedAt": updated_at,
        "sourceLabel": "Metadati classifiche Apple Music",
        "sections": sections,
    }

    (OUTPUT_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Generated {len(sections)} trend sections.")


if __name__ == "__main__":
    main()
