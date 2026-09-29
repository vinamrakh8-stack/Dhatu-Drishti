"""Geographical context for a mine: coordinates, elevation and terrain.

Terrain values are derived from an openly published elevation model, so they
are labelled as derived measurements rather than field observations.  Anything
that cannot be measured is reported as unavailable.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import requests

from .config import weather_timeout

log = logging.getLogger(__name__)

NA = "DATA NOT AVAILABLE"
UNAVAILABLE = "Not available"

_GRID_STEP = 0.004  # ~440 m


def _elevations(lat: float, lon: float) -> list[dict] | None:
    lats, lons, labels = [], [], []
    offsets = (-_GRID_STEP, 0.0, _GRID_STEP)
    for dy in offsets:
        for dx in offsets:
            lats.append(round(lat + dy, 6))
            lons.append(round(lon + dx, 6))
            labels.append((dy, dx))
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/elevation",
            params={"latitude": ",".join(map(str, lats)),
                    "longitude": ",".join(map(str, lons))},
            timeout=weather_timeout(),
        )
        resp.raise_for_status()
        values = resp.json().get("elevation")
    except Exception as exc:  # noqa: BLE001
        log.warning("elevation lookup failed: %s", exc)
        return None
    if not isinstance(values, list) or len(values) != len(labels):
        return None
    return [{"dy": dy, "dx": dx, "elevation": float(v)}
            for v, (dy, dx) in zip(values, labels) if v is not None]


def _slope_deg(grid: list[dict]) -> float | None:
    """Steepest local slope (degrees) across the sampled neighbourhood."""
    lookup = {(g["dy"], g["dx"]): g["elevation"] for g in grid}
    centre = lookup.get((0.0, 0.0))
    if centre is None:
        return None
    deg_to_m = 111_320.0
    steepest = 0.0
    for (dy, dx), elevation in lookup.items():
        if dy == 0.0 and dx == 0.0:
            continue
        run = math.hypot(dy * deg_to_m, dx * deg_to_m)
        if run <= 0:
            continue
        steepest = max(steepest, abs(elevation - centre) / run)
    return round(math.degrees(math.atan(steepest)), 1)


def collect_geography(coords: dict, mine_name: str, csv_geo: dict | None = None) -> dict[str, Any]:
    lat = coords.get("latitude")
    lon = coords.get("longitude")

    base: dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "coordinate_precision": coords.get("precision", "unavailable"),
        "coordinate_source": coords.get("source"),
        "coordinate_label": coords.get("label"),
        "coordinate_note": coords.get("note"),
        "elevation_m": UNAVAILABLE,
        "slope_deg": UNAVAILABLE,
        "terrain_ruggedness_m": UNAVAILABLE,
        "land_surface_temperature": UNAVAILABLE,
        "soil_moisture": UNAVAILABLE,
        "vegetation_index": UNAVAILABLE,
        "geological_note": UNAVAILABLE,
        "available": lat is not None,
    }

    if csv_geo:
        if csv_geo.get("soil_moisture") is not None:
            base["soil_moisture"] = csv_geo["soil_moisture"]
        if csv_geo.get("vegetation_index") is not None:
            base["vegetation_index"] = csv_geo["vegetation_index"]
        if csv_geo.get("lst") is not None:
            base["land_surface_temperature"] = csv_geo["lst"]

    if lat is None or lon is None:
        base["status"] = NA
        return base

    grid = _elevations(float(lat), float(lon))
    if grid:
        centre = next((g["elevation"] for g in grid if g["dy"] == 0 and g["dx"] == 0), None)
        if centre is not None:
            base["elevation_m"] = round(centre, 1)
        elevations = [g["elevation"] for g in grid]
        spread = max(elevations) - min(elevations)
        base["terrain_ruggedness_m"] = round(spread, 1)
        slope = _slope_deg(grid)
        if slope is not None:
            base["slope_deg"] = slope
        base["terrain_source"] = "Open-Meteo elevation model (derived)"

    base["status"] = "AVAILABLE" if base["available"] else NA
    return base


def compact_geo_block(geo: dict) -> str:
    if not geo.get("available"):
        return NA
    lines = [
        f"- Latitude/Longitude: {geo.get('latitude')}, {geo.get('longitude')}",
        f"- Coordinate precision: {geo.get('coordinate_precision')}"
        + (f" ({geo.get('coordinate_note')})" if geo.get("coordinate_note") else ""),
        f"- Elevation: {geo.get('elevation_m')} m",
        f"- Maximum local slope: {geo.get('slope_deg')} deg (derived from elevation model)",
        f"- Terrain ruggedness (elevation range over ~1 km): {geo.get('terrain_ruggedness_m')} m",
        f"- Soil moisture: {geo.get('soil_moisture')}",
        f"- Vegetation index: {geo.get('vegetation_index')}",
        f"- Land surface temperature: {geo.get('land_surface_temperature')}",
        f"- Geological information: {geo.get('geological_note')}",
    ]
    return "\n".join(lines)
