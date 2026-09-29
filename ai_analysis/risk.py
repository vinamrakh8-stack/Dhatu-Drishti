"""Deterministic risk-zone model used for the map overlays.

The overlay colours are *model output*, never a claim that the satellite
basemap itself proves a risk.  Each zone score is a weighted combination of
measurable inputs:

* rainfall   - forecast intensity/volume (Open-Meteo forecast)
* terrain    - local slope derived from an elevation model
* drainage   - relative depression of the zone (run-off accumulation proxy)
* disruption - reported downtime, degraded equipment, falling production

Components without data are dropped and the remaining weights are
renormalised, so a missing dataset can never silently inflate or deflate a
score.  When nothing measurable is available the zone is UNKNOWN (grey).
"""

from __future__ import annotations

import math
from typing import Any

from .config import weather_timeout

NA = "DATA NOT AVAILABLE"
UNAVAILABLE = "Not available"

HIGH_THRESHOLD = 0.66
MEDIUM_THRESHOLD = 0.34

ZONE_OFFSETS = (
    ("core", "Mine core", 0.0, 0.0),
    ("north", "North sector", 0.006, 0.0),
    ("east", "East sector", 0.0, 0.006),
    ("south", "South sector", -0.006, 0.0),
    ("west", "West sector", 0.0, -0.006),
)
_GRID_STEP = 0.003
_GRID_RADIUS = 3  # 7x7 grid spanning +/- 0.009 deg (~1 km)

WEIGHTS = {
    "rainfall": 0.40,
    "terrain": 0.30,
    "drainage": 0.15,
    "disruption": 0.15,
}


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def _fetch_grid(lat: float, lon: float) -> dict[tuple[float, float], float] | None:
    import requests

    lats, lons = [], []
    steps = [i * _GRID_STEP for i in range(-_GRID_RADIUS, _GRID_RADIUS + 1)]
    for dy in steps:
        for dx in steps:
            lats.append(round(lat + dy, 6))
            lons.append(round(lon + dx, 6))
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/elevation",
            params={"latitude": ",".join(map(str, lats)),
                    "longitude": ",".join(map(str, lons))},
            timeout=weather_timeout(),
        )
        resp.raise_for_status()
        values = resp.json().get("elevation")
    except Exception:  # noqa: BLE001 - terrain may simply be unavailable
        return None
    if not isinstance(values, list) or len(values) != len(lats):
        return None
    grid: dict[tuple[float, float], float] = {}
    i = 0
    for dy in steps:
        for dx in steps:
            value = values[i]
            i += 1
            if value is not None:
                grid[(round(dy, 6), round(dx, 6))] = float(value)
    return grid


def _local_slope(grid: dict, dy: float, dx: float) -> float | None:
    centre = grid.get((round(dy, 6), round(dx, 6)))
    if centre is None:
        return None
    deg_to_m = 111_320.0
    steepest = 0.0
    for step in (-_GRID_STEP, 0.0, _GRID_STEP):
        for step_x in (-_GRID_STEP, 0.0, _GRID_STEP):
            if step == 0.0 and step_x == 0.0:
                continue
            key = (round(dy + step, 6), round(dx + step_x, 6))
            neighbour = grid.get(key)
            if neighbour is None:
                continue
            run = math.hypot(step * deg_to_m, step_x * deg_to_m)
            steepest = max(steepest, abs(neighbour - centre) / run)
    return round(math.degrees(math.atan(steepest)), 1)


def _weather_scores(weather: dict) -> tuple[dict[str, float] | None, list[str]]:
    fc = (weather or {}).get("forecast") or {}
    hist = (weather or {}).get("historical") or {}
    if not fc.get("available"):
        return None, []
    score = 0.0
    parts: list[float] = []
    notes: list[str] = []

    max_day = fc.get("rainfall_max_day_mm")
    if max_day is not None:
        parts.append(0.7 * _clip(float(max_day) / 50.0))
        notes.append(f"wettest forecast day {max_day} mm")
    total = fc.get("rainfall_total_mm")
    if total is not None:
        parts.append(0.3 * _clip(float(total) / 120.0))
        notes.append(f"forecast period total {total} mm")

    if parts:
        score = sum(parts)
        heavy = fc.get("heavy_rain_days") or 0
        if heavy:
            notes.append(f"{heavy} day(s) forecast at or above 20 mm")
        soil = fc.get("soil_moisture_mean")
        if soil is not None and float(soil) >= 0.35:
            score = min(1.0, score + 0.1)
            notes.append(f"elevated soil moisture ({soil})")
        if hist.get("available") and hist.get("rainfall_max_day_mm") is not None:
            notes.append(f"historical wettest day {hist['rainfall_max_day_mm']} mm")
        return round(score, 3), notes
    return None, []


def _disruption_score(mine_data: dict) -> tuple[float | None, list[str]]:
    if not mine_data.get("available"):
        return None, []
    parts: list[tuple[float, float]] = []
    notes: list[str] = []

    equipment = mine_data.get("equipment") or {}
    summary = str(equipment.get("summary") or "")
    if " of " in summary:
        try:
            bad = float(summary.split(" of ", 1)[0])
            total = float(summary.split(" of ", 1)[1].split(" ", 1)[0])
            if total > 0:
                ratio = bad / total
                parts.append((0.5, _clip(ratio)))
                notes.append(f"{int(bad)}/{int(total)} equipment records degraded")
        except (ValueError, IndexError):
            pass

    downtime = (mine_data.get("equipment") or {}).get("downtime_total")
    if downtime is not None:
        parts.append((0.3, _clip(float(downtime) / 160.0)))
        notes.append(f"total reported downtime {downtime} h")

    direction = (mine_data.get("production") or {}).get("direction")
    if direction == "DECREASE":
        parts.append((0.2, 1.0))
        notes.append("production trend is DECREASING")
    elif direction in ("STABLE", "INCREASE"):
        notes.append(f"production trend is {direction}")

    if not parts:
        return None, notes
    weight = sum(w for w, _ in parts)
    score = sum(w * s for w, s in parts) / weight
    return round(score, 3), notes


def build_risk_model(coords: dict, weather: dict, mine_data: dict) -> dict[str, Any]:
    lat, lon = coords.get("latitude"), coords.get("longitude")

    weather_score, weather_notes = _weather_scores(weather)
    disruption_score, disruption_notes = _disruption_score(mine_data)

    zones: list[dict] = []
    grid = _fetch_grid(float(lat), float(lon)) if lat is not None and lon is not None else None
    centre_elev = grid.get((0.0, 0.0)) if grid else None

    available: set[str] = set()
    if weather_score is not None:
        available.add("rainfall")
    if disruption_score is not None:
        available.add("disruption")

    terrain_available = grid is not None
    drainage_available = bool(grid and centre_elev is not None)

    for zone_id, label, dy, dx in ZONE_OFFSETS:
        components: dict[str, tuple[float, str]] = {}
        notes: list[str] = list(weather_notes) + list(disruption_notes)

        slope = _local_slope(grid, dy, dx) if terrain_available else None
        if slope is not None:
            components["terrain"] = (_clip(slope / 25.0), f"local slope {slope} deg")

        if drainage_available:
            elevation = grid.get((round(dy, 6), round(dx, 6)))
            if elevation is not None:
                drop = centre_elev - elevation
                components["drainage"] = (
                    _clip(drop / 15.0),
                    f"{round(drop, 1)} m below mine core" if drop > 0
                    else "at or above mine core elevation",
                )

        if weather_score is not None:
            components["rainfall"] = (weather_score, "; ".join(weather_notes) or "forecast rainfall")
        if disruption_score is not None:
            components["disruption"] = (disruption_score, "; ".join(disruption_notes) or "reported disruption")

        if not components:
            zones.append({
                "id": zone_id,
                "label": label,
                "latitude": round(lat + dy, 6) if lat is not None else None,
                "longitude": round(lon + dx, 6) if lon is not None else None,
                "radius_m": 350,
                "level": "UNKNOWN",
                "score": None,
                "factors": ["No measurable rainfall, terrain or operational data available."],
            })
            continue

        weights = {k: WEIGHTS[k] for k in components}
        total_weight = sum(weights.values())
        score = sum(components[k][0] * weights[k] for k in components) / total_weight
        if score >= HIGH_THRESHOLD:
            level = "HIGH"
        elif score >= MEDIUM_THRESHOLD:
            level = "MEDIUM"
        else:
            level = "LOW"

        factors = [components[k][1] for k in components]
        zones.append({
            "id": zone_id,
            "label": label,
            "latitude": round(lat + dy, 6) if lat is not None else None,
            "longitude": round(lon + dx, 6) if lon is not None else None,
            "radius_m": 350,
            "level": level,
            "score": round(score, 3),
            "factors": factors,
            "components_used": sorted(components.keys()),
        })

    scored = [z["score"] for z in zones if z["score"] is not None]
    if not scored:
        overall_level, overall_score = "UNKNOWN", None
        overall_factors = ["Insufficient measurable inputs for a risk classification."]
    else:
        overall_score = round(sum(scored) / len(scored), 3)
        overall_level = ("HIGH" if overall_score >= HIGH_THRESHOLD
                         else "MEDIUM" if overall_score >= MEDIUM_THRESHOLD else "LOW")
        overall_factors = sorted({f for z in zones for f in z.get("factors", [])})

    return {
        "available": bool(scored),
        "status": "AVAILABLE" if scored else NA,
        "overall_risk_level": overall_level,
        "overall_score": overall_score,
        "zones": zones,
        "components_used": sorted(
            available
            | ({"terrain"} if terrain_available else set())
            | ({"drainage"} if drainage_available else set())
        ),
        "method": (
            "Weighted score over rainfall (0.40), terrain slope (0.30), "
            "relative drainage position (0.15) and operational disruption (0.15). "
            "Components without data are dropped and weights renormalised."
        ),
        "thresholds": {
            "HIGH": f">= {HIGH_THRESHOLD}",
            "MEDIUM": f">= {MEDIUM_THRESHOLD}",
            "LOW": f"< {MEDIUM_THRESHOLD}",
            "UNKNOWN": "no measurable inputs",
        },
        "factors": overall_factors[:8],
        "disclaimer": (
            "Risk zones are model-derived estimates computed from weather, "
            "elevation and operational inputs. They are not direct observations "
            "from the satellite basemap and do not guarantee future conditions."
        ),
    }


def compact_risk_block(risk: dict) -> str:
    if not risk.get("available"):
        return NA
    lines = [
        f"- Model overall risk: {risk.get('overall_risk_level')} "
        f"(score {risk.get('overall_score')})",
        f"- Method: {risk.get('method')}",
    ]
    for zone in risk.get("zones", []):
        lines.append(
            f"- {zone['label']}: {zone['level']}"
            + (f" (score {zone['score']})" if zone.get("score") is not None else "")
            + " | " + "; ".join(zone.get("factors", []))
        )
    return "\n".join(lines)
