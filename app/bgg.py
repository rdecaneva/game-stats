"""Look up game details on BoardGameGeek (XML API2).

The API needs a bearer token, read from the BGG_API_TOKEN environment variable.
Without one, or if the request fails, lookups return None and pages fall back
to showing just the game name.
"""

import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache

API_URL = "https://boardgamegeek.com/xmlapi2/thing?id={id}"


@dataclass(frozen=True)
class BggInfo:
    thumbnail: str | None
    year: str | None
    designers: tuple[str, ...]


SEARCH_URL = "https://boardgamegeek.com/xmlapi2/search?type=boardgame&query={query}"


def configured() -> bool:
    return bool(os.environ.get("BGG_API_TOKEN"))


def _get(url: str) -> ET.Element | None:
    """GET an XML API2 URL with the token. Returns None when unconfigured or on failure."""
    token = os.environ.get("BGG_API_TOKEN")
    if not token:
        return None
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "User-Agent": "game-stats"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return ET.fromstring(response.read())
    except (urllib.error.URLError, TimeoutError, ET.ParseError):
        return None


def search(query: str) -> list[dict]:
    """Board games matching query, as [{id, name, year}]. Empty when unavailable."""
    root = _get(SEARCH_URL.format(query=urllib.parse.quote(query)))
    if root is None:
        return []
    results = []
    for item in root.findall("item")[:20]:
        name = item.find("name")
        year = item.find("yearpublished")
        results.append({
            "id": int(item.get("id", 0)),
            "name": name.get("value", "") if name is not None else "",
            "year": year.get("value") if year is not None else None,
        })
    return results


@lru_cache(maxsize=64)
def fetch(bgg_id: int) -> BggInfo | None:
    root = _get(API_URL.format(id=bgg_id))
    if root is None:
        return None

    item = root.find("item")
    if item is None:
        return None
    thumbnail = item.findtext("thumbnail")
    year = item.find("yearpublished")
    designers = tuple(
        link.get("value", "")
        for link in item.findall("link")
        if link.get("type") == "boardgamedesigner" and link.get("value")
    )
    return BggInfo(
        thumbnail=thumbnail or None,
        year=year.get("value") if year is not None else None,
        designers=designers,
    )
