"""Mine catalogue, name normalisation and coordinate resolution.

The catalogue mirrors the state/district/mine selector used by the AI Analysis
page.  Coordinates only exist for mines whose positions have been verified from
primary sources; anything else is resolved through OpenStreetMap Nominatim at
request time (cached) and, failing that, at district granularity.  If neither
works the coordinates are reported as unavailable rather than guessed.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata

import requests

from .config import WEATHER_CACHE_TTL_SECONDS

log = logging.getLogger(__name__)

# state -> district -> [mine label, ...]
CATALOG: dict[str, dict[str, list[str]]] = {
    "Maharashtra": {
        "Bhandara": [
            "Chikla Mine (MOIL)",
            "Dongri Buzurg Mine (MOIL)",
        ],
        "Nagpur": [
            "Beldongri Mine (MOIL)",
            "Gumgaon Mine (MOIL)",
            "Kandri Mine (MOIL)",
            "Munsar Mine (MOIL)",
            "New Satak Mine (MOIL)",
            "Old Satak Mine (MOIL)",
            "Pali Manganese Mine",
            "Kothulna Manganese Mine",
            "Kirnapur Manganese Mine",
            "Kawatha Manganese Mine",
            "Kachurwahi\u2013Wadegaon Manganese Mine",
            "Parseoni Manganese Mine",
        ],
    },
    "Madhya Pradesh": {
        "Balaghat": [
            "Balaghat Mine (MOIL)",
            "Ukwa Mine (MOIL)",
            "Tirodi Mine (MOIL)",
            "Sitapatore\u2013Sukli Mine (MOIL)",
            "Netra Manganese Mine",
            "Ramrama Manganese Mine",
            "Ghondi Manganese Mine",
        ],
        "Chhindwara": [
            "Kachidana Manganese Mine",
            "Palaspani Manganese Mine",
        ],
        "Jabalpur": [
            "Silua Jhansi Manganese Mine",
            "Jhansi Silua Manganese Mine",
        ],
        "Alirajpur": [
            "Jamli Choti Manganese Mine",
        ],
        "Jhabua": [
            "Kajli Dongri Manganese Mine",
        ],
    },
}

# Verified coordinates (primary sources: Mindat locality records, MOIL/EC lease
# documents, OSM).  Keyed by the normalised mine token.
VERIFIED_COORDS: dict[str, tuple[float, float, str]] = {
    "balaghat": (21.8497, 80.2267, "mindat"),
    "bharveli": (21.8500, 80.2331, "mindat"),
    "tirodi": (21.6856, 79.7192, "mindat"),
    "ukwa": (21.9742, 80.4664, "mindat"),
    "sitasaongi": (21.5322, 79.7467, "mindat"),
    "gumgaon": (21.4000, 78.9830, "ec_document"),
    "beldongri": (21.3403, 79.2922, "mindat"),
    "chikla": (21.5431, 79.7539, "mindat"),
    "kandri": (21.4130, 79.2670, "mindat"),
    "dongri buzurg": (21.5486, 79.6828, "mindat"),
    "munsar": (21.4014, 79.2808, "mindat"),
    "sitapatore sukli": (21.7000, 79.6667, "ec_document"),
    "sitapatore": (21.7000, 79.6667, "ec_document"),
    "netra": (21.8644, 79.9808, "mindat"),
    "ramrama": (21.8667, 79.9331, "mindat"),
    "ghondi": (21.9188, 80.4184, "nominatim"),
}

_STOPWORDS = {
    "mine", "mines", "mining", "manganese", "moil", "ore", "project",
    "deposit", "the", "of", "at", "near", "and", "area", "block",
}

_punct_re = re.compile(r"[^a-z0-9]+")
_paren_re = re.compile(r"\([^)]*\)")
_ctrl_re = re.compile(r"[\x00-\x1f\x7f]")

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


def clean_mine_name(raw: str, limit: int = 120) -> str:
    """Sanitise a client supplied mine name (length + control chars)."""
    if raw is None:
        return ""
    text = unicodedata.normalize("NFKC", str(raw))
    text = _ctrl_re.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def normalise_name(raw: str) -> str:
    """``'Sitapatore\u2013Sukli Mine (MOIL)'`` -> ``'sitapatore sukli'``."""
    if not raw:
        return ""
    text = unicodedata.normalize("NFKD", str(raw))
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = _paren_re.sub(" ", text)
    text = text.lower()
    text = _punct_re.sub(" ", text)
    tokens = [t for t in text.split() if t and t not in _STOPWORDS]
    return " ".join(tokens)


def _tokens(normalised: str) -> set[str]:
    return set(normalised.split())


def mine_matches(candidate: str, target_normalised: str) -> bool:
    """Return True when a cell/filename value plausibly refers to the mine."""
    cand = normalise_name(candidate)
    if not cand or not target_normalised:
        return False
    if cand == target_normalised:
        return True
    a, b = _tokens(cand), _tokens(target_normalised)
    if not a or not b:
        return False
    # One side is a subset of the other ("Sitapatore" vs "Sitapatore Sukli").
    if a <= b or b <= a:
        return True
    # Require most of the target's tokens to be present to limit false hits.
    return len(b & a) / len(b) >= 0.75


def known_mine_names() -> list[str]:
    names: list[str] = []
    for districts in CATALOG.values():
        for mines in districts.values():
            names.extend(mines)
    return names


def add_catalog_mine(state: str, district: str, mine: str) -> None:
    """Register a mine discovered inside an uploaded dataset."""
    if not (state and district and mine):
        return
    CATALOG.setdefault(state, {}).setdefault(district, [])
    if mine not in CATALOG[state][district]:
        CATALOG[state][district].append(mine)


def _cached_get(url: str, *, params: dict | None = None, timeout: float) -> dict | None:
    key = url + "|" + json.dumps(params or {}, sort_keys=True)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit["expires"] > now:
            return hit["value"]
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        value = resp.json()
    except Exception as exc:  # noqa: BLE001 - network/parse failures are non-fatal
        log.warning("geocode request failed: %s", exc)
        return None
    with _cache_lock:
        _cache[key] = {"value": value, "expires": now + WEATHER_CACHE_TTL_SECONDS}
    return value


def _nominatim(query: str, timeout: float, viewbox: tuple[float, float, float, float] | None = None):
    params = {
        "format": "jsonv2",
        "limit": 1,
        "countrycodes": "in",
        "accept-language": "en",
        "q": query,
    }
    if viewbox:
        left, bottom, right, top = viewbox
        params["viewbox"] = f"{left},{top},{right},{bottom}"
        params["bounded"] = 1
    data = _cached_get(
        "https://nominatim.openstreetmap.org/search",
        params=params,
        timeout=timeout,
    )
    if isinstance(data, list) and data:
        first = data[0]
        try:
            return float(first["lat"]), float(first["lon"]), str(first.get("display_name", ""))
        except (KeyError, TypeError, ValueError):
            return None
    return None


# Rough state bounding boxes used to keep district fallbacks inside the state.
_STATE_VIEWBOX = {
    "madhya pradesh": (74.0, 21.0, 84.0, 27.0),
    "maharashtra": (72.6, 15.6, 80.9, 22.5),
}


def resolve_coordinates(
    mine_name: str,
    state: str = "",
    district: str = "",
    timeout: float = 8.0,
) -> dict:
    """Resolve mine coordinates without ever inventing a value.

    Order: verified registry -> mine-level geocode -> district-level geocode ->
    unavailable.
    """
    target = normalise_name(mine_name)

    for token, (lat, lon, source) in VERIFIED_COORDS.items():
        if target == token or (target and (target in token or token in target)):
            return {
                "latitude": lat,
                "longitude": lon,
                "precision": "mine",
                "source": f"verified:{source}",
                "label": mine_name,
            }

    place = _nominatim(f"{mine_name}, India", timeout)
    if place:
        return {
            "latitude": place[0],
            "longitude": place[1],
            "precision": "mine",
            "source": "nominatim",
            "label": place[2],
        }

    if district:
        query = f"{district} district, {state}, India" if state else f"{district}, India"
        viewbox = _STATE_VIEWBOX.get(state.lower())
        place = _nominatim(query, timeout, viewbox)
        if place:
            return {
                "latitude": place[0],
                "longitude": place[1],
                "precision": "district",
                "source": "nominatim",
                "label": place[2],
                "note": "Approximate location: district centroid. Exact mine coordinates not available.",
            }

    return {
        "latitude": None,
        "longitude": None,
        "precision": "unavailable",
        "source": None,
        "label": None,
        "note": "Coordinates not available for this mine.",
    }
