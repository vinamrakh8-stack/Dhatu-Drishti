"""Weather collection for a mine location (Open-Meteo, no API key).

Historical observations and forward-looking forecasts are collected separately
so the report can distinguish observations from model output.  Every section
fails soft: a network problem yields ``{"available": False}`` rather than an
invented value.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from .config import WEATHER_CACHE_TTL_SECONDS, weather_timeout

log = logging.getLogger(__name__)

NA = "DATA NOT AVAILABLE"
UNAVAILABLE = "Not available"

_cache: dict[str, dict] = {}


def _get(url: str, params: dict, ttl: int = WEATHER_CACHE_TTL_SECONDS) -> dict | None:
    import time

    key = url + "|" + str(sorted(params.items()))
    cached = _cache.get(key)
    if cached and cached["expires"] > time.time():
        return cached["value"]
    try:
        resp = requests.get(url, params=params, timeout=weather_timeout())
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - weather is best-effort
        log.warning("weather request failed: %s", exc)
        return None
    _cache[key] = {"value": data, "expires": time.time() + ttl}
    return data


def _rounded(values, digits=1) -> list[float | None]:
    return [None if v is None else round(float(v), digits) for v in values]


def _mean(values) -> float | None:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 2)


def _mx(values) -> float | None:
    clean = [float(v) for v in values if v is not None]
    return round(max(clean), 2) if clean else None


def _total(values) -> float | None:
    """Sum of observed values; a genuine 0.0 total is preserved, only an
    entirely empty series reports ``None``."""
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return round(sum(clean), 1)


def _fmt(value) -> str:
    """Render a number or the unavailable marker without truthiness traps."""
    return UNAVAILABLE if value is None else str(value)



def collect_weather(lat: float, lon: float, history_days: int = 90,
                    forecast_days: int = 7) -> dict[str, Any]:
    """Return ``historical`` (observed) and ``forecast`` (modelled) blocks."""
    if lat is None or lon is None:
        return {
            "available": False,
            "status": NA,
            "reason": "No coordinates available for this mine.",
            "historical": {"available": False, "status": NA},
            "forecast": {"available": False, "status": NA},
        }

    historical = _historical(lat, lon, history_days)
    forecast = _forecast(lat, lon, forecast_days)
    return {
        "available": bool(historical.get("available") or forecast.get("available")),
        "status": "AVAILABLE" if (historical.get("available") or forecast.get("available")) else NA,
        "historical": historical,
        "forecast": forecast,
    }


def _historical(lat: float, lon: float, days: int) -> dict:
    from datetime import date, timedelta

    daily = (
        "precipitation_sum,temperature_2m_max,temperature_2m_min,temperature_2m_mean,"
        "relative_humidity_2m_mean,wind_speed_10m_max"
    )
    today = date.today()
    data = None
    # The reanalysis archive has no data for the current day, and sometimes
    # lags by a few days. Back the window off until the service accepts it.
    for backoff in (1, 3, 7, 14, 30):
        end = today - timedelta(days=backoff)
        start = end - timedelta(days=days)
        data = _get(
            "https://archive-api.open-meteo.com/v1/archive",
            {
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "daily": daily,
                "timezone": "Asia/Kolkata",
            },
        )
        if data and isinstance(data.get("daily"), dict):
            break
    if not data or not isinstance(data.get("daily"), dict):
        return {"available": False, "status": NA, "reason": "Archive service unavailable."}

    block = data["daily"]
    rain = block.get("precipitation_sum") or []
    tmax = block.get("temperature_2m_max") or []
    tmin = block.get("temperature_2m_min") or []
    tmean = block.get("temperature_2m_mean") or []
    hum = block.get("relative_humidity_2m_mean") or []
    wind = block.get("wind_speed_10m_max") or []

    wet_days = sum(1 for v in rain if v is not None and float(v) >= 2.5)
    periods = [d for d in (block.get("time") or []) if d]
    return {
        "available": bool(rain or tmean),
        "status": "AVAILABLE",
        "kind": "historical_observation",
        "label": "Historical Weather Data",
        "range": f"{periods[0]} to {periods[-1]}" if periods else UNAVAILABLE,
        "records": len(periods),
        "rainfall_total_mm": _total(rain) if rain else None,
        "rainfall_mean_mm": _mean(rain),
        "rainfall_max_day_mm": _mx(rain),
        "wet_days": wet_days,
        "temperature_mean_c": _mean(tmean),
        "temperature_max_c": _mx(tmax),
        "temperature_min_c": _mn(tmin),
        "humidity_mean_percent": _mean(hum),
        "wind_max_kmh": _mx(wind),
        "series": [
            {"date": d, "precipitation_mm": (None if p is None else round(float(p), 1)),
             "temperature_max_c": (None if t is None else round(float(t), 1))}
            for d, p, t in list(zip(periods, rain, tmax))[-30:]
        ],
    }


def _mn(values) -> float | None:
    clean = [float(v) for v in values if v is not None]
    return round(min(clean), 2) if clean else None


def _forecast(lat: float, lon: float, days: int) -> dict:
    data = _get(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,wind_gusts_10m",
            "daily": ("precipitation_sum,temperature_2m_max,temperature_2m_min,"
                      "wind_speed_10m_max,precipitation_probability_max"),
            "hourly": "soil_moisture_0_to_1cm,precipitation",
            "forecast_days": days,
            "timezone": "Asia/Kolkata",
        },
        ttl=1800,
    )
    if not data or not isinstance(data.get("daily"), dict):
        return {"available": False, "status": NA, "reason": "Forecast service unavailable."}

    daily = data["daily"]
    hourly = data.get("hourly") or {}
    current = data.get("current") or {}

    rain = daily.get("precipitation_sum") or []
    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    wind = daily.get("wind_speed_10m_max") or []
    prob = daily.get("precipitation_probability_max") or []
    soil = [v for v in (hourly.get("soil_moisture_0_to_1cm") or []) if v is not None]
    days_list = daily.get("time") or []

    heavy = [i for i, v in enumerate(rain) if v is not None and float(v) >= 20.0]
    return {
        "available": bool(rain or current),
        "status": "AVAILABLE",
        "kind": "forecast",
        "label": "Forecast Weather Data",
        "range": f"{days_list[0]} to {days_list[-1]}" if days_list else UNAVAILABLE,
        "records": len(days_list),
        "current": {
            "temperature_2m": current.get("temperature_2m"),
            "relative_humidity_2m": current.get("relative_humidity_2m"),
            "precipitation_mm": current.get("precipitation"),
            "wind_speed_10m": current.get("wind_speed_10m"),
            "wind_gusts_10m": current.get("wind_gusts_10m"),
            "time": current.get("time"),
        },
        "rainfall_total_mm": _total(rain) if rain else None,
        "rainfall_max_day_mm": _mx(rain),
        "heavy_rain_days": len(heavy),
        "heavy_rain_threshold_mm": 20.0,
        "temperature_max_c": _mx(tmax),
        "temperature_min_c": _mn(tmin),
        "wind_max_kmh": _mx(wind),
        "precipitation_probability_max_percent": _mx(prob),
        "soil_moisture_mean": _mean(soil),
        "soil_moisture_max": _mx(soil),
        "series": [
            {
                "date": d,
                "precipitation_mm": (None if p is None else round(float(p), 1)),
                "temperature_max_c": (None if t is None else round(float(t), 1)),
                "temperature_min_c": (None if lo is None else round(float(lo), 1)),
                "precipitation_probability_percent": (None if pr is None else round(float(pr))),
            }
            for d, p, t, lo, pr in zip(days_list, rain, tmax, tmin, prob)
        ],
    }


def compact_weather_block(weather: dict) -> str:
    """Render the weather sections as compact prompt text."""
    if not weather.get("available"):
        return NA

    lines: list[str] = []
    hist = weather.get("historical") or {}
    fcst = weather.get("forecast") or {}

    if hist.get("available"):
        lines.append("HISTORICAL WEATHER DATA (observed):")
        lines.append(f"- Period: {hist.get('range', UNAVAILABLE)} ({hist.get('records', 0)} days)")
        lines.append("- Total rainfall: " + _fmt(hist.get('rainfall_total_mm')) + " mm")
        lines.append("- Mean daily rainfall: " + _fmt(hist.get('rainfall_mean_mm')) + " mm")
        lines.append("- Wettest day: " + _fmt(hist.get('rainfall_max_day_mm')) + " mm; wet days: " + str(hist.get('wet_days', UNAVAILABLE)))
        lines.append("- Mean temperature: " + _fmt(hist.get('temperature_mean_c')) + " deg C; range "
                      + _fmt(hist.get('temperature_min_c')) + " to " + _fmt(hist.get('temperature_max_c')) + " deg C")
        lines.append("- Mean humidity: " + _fmt(hist.get('humidity_mean_percent')) + " %")
        lines.append("- Max wind: " + _fmt(hist.get('wind_max_kmh')) + " km/h")
    else:
        lines.append(f"HISTORICAL WEATHER DATA: {NA}")

    lines.append("")
    if fcst.get("available"):
        cur = fcst.get("current") or {}
        lines.append("FORECAST WEATHER DATA (model output, not observation):")
        lines.append(f"- Period: {fcst.get('range', UNAVAILABLE)}")
        lines.append(f"- Current: {cur.get('temperature_2m') or '?'} deg C, humidity {cur.get('relative_humidity_2m') or '?'} %, wind {cur.get('wind_speed_10m') or '?'} km/h")
        lines.append("- Forecast total rainfall: " + _fmt(fcst.get('rainfall_total_mm')) + " mm over " + str(fcst.get('records', 0)) + " days")
        lines.append("- Wettest forecast day: " + _fmt(fcst.get('rainfall_max_day_mm')) + " mm; days >= 20 mm: " + str(fcst.get('heavy_rain_days', 0)))
        lines.append(f"- Forecast temperature range: {fcst.get('temperature_min_c') or '?'} to {fcst.get('temperature_max_c') or '?'} deg C")
        lines.append("- Max wind: " + _fmt(fcst.get('wind_max_kmh')) + " km/h")
        soil = fcst.get("soil_moisture_mean")
        lines.append(f"- Soil moisture (0-1 cm, hourly mean): {soil if soil is not None else UNAVAILABLE}")
    else:
        lines.append(f"FORECAST WEATHER DATA: {NA}")

    return "\n".join(lines)
