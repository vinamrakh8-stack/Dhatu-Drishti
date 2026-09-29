"""Scan uploaded CSV files, locate the selected mine and summarise the data.

Design rules:
* only files inside the configured upload directory are ever read,
* column detection is heuristic (no fixed schema assumed),
* malformed CSVs are skipped rather than crashing,
* nothing is invented: absent values are reported as unavailable.
"""

from __future__ import annotations

import logging
import math
import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import MAX_RAW_PREVIEW_ROWS, MAX_TREND_ROWS, UPLOAD_DIR
from .registry import mine_matches, normalise_name

log = logging.getLogger(__name__)

NA = "DATA NOT AVAILABLE"
UNAVAILABLE = "Not available"

MINE_COLUMNS = ["mine", "mine_name", "mine_name", "location", "field",
                "mining_field", "site", "lease", "project", "mine_site", "area",
                "colliery", "operation"]
DATE_COLUMNS = ["date", "day", "report_date", "period", "timestamp", "sample_date",
                "observation_date", "month", "year", "time"]
PRODUCTION_COLUMNS = ["production", "production_tonnes", "production_t", "prod",
                      "ore_produced", "ore_production", "output", "tonnes", "mt",
                      "total_production"]
GRADE_COLUMNS = ["ore_grade", "grade", "mn_grade", "assay", "content_percent"]
RESERVE_COLUMNS = ["reserve", "reserves", "reserve_tonnes", "remaining_reserves",
                   "estimated_reserve", "balance_reserves"]
EQUIPMENT_COLUMNS = ["equipment", "equipment_status", "machine", "asset",
                     "equipment_id", "machinery", "item"]
DOWNTIME_COLUMNS = ["downtime", "downtime_hours", "downtime_h", "breakdown",
                    "breakdown_hours", "outage_hours", "idle_hours", "availability"]
RAINFALL_COLUMNS = ["rainfall", "rain", "precipitation", "precip", "rainfall_mm",
                    "rain_mm", "rainfall_cm"]
TEMPERATURE_COLUMNS = ["temperature", "temp", "temperature_c", "temperature_2m",
                       "tmean_c", "tavg_c", "tmean", "tavg",
                       "land_surface_temperature", "lst", "tmax_c", "tmin_c"]
TMIN_COLUMNS = ["tmin_c", "tmin", "temperature_min", "min_temperature", "temp_min"]
TMAX_COLUMNS = ["tmax_c", "tmax", "temperature_max", "max_temperature", "temp_max"]
PRESSURE_COLUMNS = ["pressure_hpa", "pressure", "barometric_pressure", "air_pressure"]
CLOUD_COLUMNS = ["cloud_cover_pct", "cloud_cover", "cloud_pct", "cloudiness", "cloud"]
WIND_DIR_COLUMNS = ["wind_dir_deg", "wind_dir", "wind_direction", "windbearing"]
# Categorical sky/condition summary - "weather_code" is deliberately excluded
# because it is a numeric WMO code, not a readable condition.
CONDITION_COLUMNS = ["weather_label", "weather_condition", "weather_conditions",
                     "conditions", "condition", "sky_condition"]
HUMIDITY_COLUMNS = ["humidity", "relative_humidity", "rh", "relative_humidity_2m"]
WIND_COLUMNS = ["wind", "wind_speed", "wind_speed_10m", "windspeed"]
SOIL_COLUMNS = ["soil_moisture", "moisture", "sm", "volumetric_soil_water",
                "soil_water"]
VEGETATION_COLUMNS = ["vegetation_index", "ndvi", "vi", "crop_aspect"]
LATITUDE_COLUMNS = ["latitude", "lat"]
LONGITUDE_COLUMNS = ["longitude", "lon", "long", "lng"]


def _norm_col(name: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower())
    return re.sub(r"_+", "_", text).strip("_")


def _pick(columns: list[str], candidates: list[str]) -> str | None:
    for cand in candidates:
        if cand in columns:
            return cand
    # fall back to a loose "contains" match, longest candidate first
    for cand in sorted(candidates, key=len, reverse=True):
        for col in columns:
            if cand in col:
                return col
    return None


@dataclass
class CsvFileResult:
    filename: str
    ok: bool
    error: str | None = None
    rows: int = 0
    columns: list[str] = field(default_factory=list)
    mine_column: str | None = None
    matched_mines: list[str] = field(default_factory=list)


def upload_files() -> list[Path]:
    if not UPLOAD_DIR.is_dir():
        return []
    return sorted(
        (p for p in UPLOAD_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def list_upload_info() -> list[dict]:
    info = []
    for path in upload_files():
        try:
            stat = path.stat()
            info.append({
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
        except OSError:
            continue
    return info


def _drop_empty_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Remove columns that carry no values.

    Export tools often emit a spacer column (``,,``) which pandas names
    ``Unnamed: n``.  Keeping it would silently inflate the missing-value count
    and distort data quality.
    """
    if df is None or df.empty or df.shape[1] < 2:
        return df
    drop = [c for c in df.columns if df[c].isna().all()]
    if drop and len(drop) < df.shape[1]:
        df = df.drop(columns=drop)
    return df


def _read_csv(path: Path) -> pd.DataFrame:
    """Read a CSV defensively, tolerating ragged rows."""
    return _drop_empty_columns(_read_csv_raw(path))


def _read_csv_raw(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for kwargs in ({"on_bad_lines": "skip"}, {"on_bad_lines": "warn"}):
        try:
            return pd.read_csv(path, **kwargs)
        except TypeError:  # older pandas without on_bad_lines
            try:
                return pd.read_csv(path)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise last_error or ValueError("unreadable CSV")


_DMYSHAPE = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$")


def _looks_dayfirst(values: pd.Series) -> bool:
    """Infer day-first ordering from unambiguous values in the column.

    ``29-09-2025`` proves day-first (29 cannot be a month).  ISO dates such as
    ``2025-09-29`` do not match this shape and leave the flag at its default.
    """
    text = values.astype(str).str.strip()
    parts = text.str.extract(_DMYSHAPE)
    if parts.empty or parts[0].isna().all():
        return False
    first = pd.to_numeric(parts[0], errors="coerce")
    second = pd.to_numeric(parts[1], errors="coerce")
    first_over_12 = bool((first > 12).fillna(False).any())
    second_over_12 = bool((second > 12).fillna(False).any())
    return first_over_12 and not second_over_12


def _coerce_dates(series: pd.Series) -> pd.Series:
    dayfirst = _looks_dayfirst(series)
    with warnings.catch_warnings():
        # pandas warns about day-first strings under a dayfirst=False default;
        # the flag above is derived from the data, so the warning is noise.
        warnings.simplefilter("ignore", UserWarning)
        try:
            return pd.to_datetime(series, errors="coerce", dayfirst=dayfirst,
                                  format="mixed")
        except (TypeError, ValueError):
            return pd.to_datetime(series, errors="coerce", dayfirst=dayfirst)


def _number(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace(r"[^0-9.\-]", "", regex=True)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def _cardinal(degrees: float) -> str:
    """Map a bearing in degrees to a 16-point compass label."""
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    index = int((float(degrees) % 360) / 22.5 + 0.5) % 16
    return names[index]


def _stat(value) -> float | None:
    if value is None:
        return None
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val):
        return None
    return round(val, 3)


def _trend(values: list[float]) -> tuple[str, str]:
    """Classify direction from the first vs second half of the series."""
    series = [v for v in values if v is not None]
    if len(series) < 4:
        return UNAVAILABLE, "Insufficient records to establish a trend."
    mid = len(series) // 2
    first = float(np.mean(series[:mid]))
    second = float(np.mean(series[mid:]))
    if first == 0:
        return UNAVAILABLE, "Baseline production is zero; relative trend undefined."
    change = (second - first) / abs(first)
    if abs(change) < 0.05:
        direction = "STABLE"
    elif change > 0:
        direction = "INCREASE"
    else:
        direction = "DECREASE"
    evidence = (
        f"Mean of first {mid} records = {first:.2f}; mean of last "
        f"{len(series) - mid} records = {second:.2f} "
        f"({change * 100:+.1f}%)."
    )
    return direction, evidence


def _available(value, suffix: str = "") -> str:
    return f"{value}{suffix}" if value not in (None, "", []) else UNAVAILABLE


def collect_for_mine(mine_name: str) -> dict:
    """Return every CSV fact we can substantiate for ``mine_name``."""
    target = normalise_name(mine_name)
    files: list[CsvFileResult] = []
    frames: list[pd.DataFrame] = []
    column_source = "none"

    for path in upload_files():
        result = CsvFileResult(filename=path.name, ok=False)
        try:
            df = _read_csv(path)
        except Exception as exc:  # noqa: BLE001 - report, never propagate
            result.error = "File could not be parsed as CSV."
            log.warning("skipping malformed upload %s: %s", path.name, exc)
            files.append(result)
            continue

        if df is None or df.empty:
            result.error = "File contains no data rows."
            files.append(result)
            continue

        df.columns = [_norm_col(c) for c in df.columns]
        # drop accidental duplicate column names created by normalisation
        df = df.loc[:, ~pd.Index(df.columns).duplicated()]
        result.ok = True
        result.rows = int(len(df))
        result.columns = list(df.columns)

        mine_col = _pick(list(df.columns), MINE_COLUMNS)
        matched = pd.DataFrame()
        if mine_col:
            result.mine_column = mine_col
            mask = df[mine_col].astype(str).map(lambda v: mine_matches(v, target))
            matched = df[mask]
            matched_mines = sorted({str(v) for v in df.loc[mask, mine_col].unique()})
            result.matched_mines = matched_mines[:10]
            if not matched.empty:
                column_source = "mine column"
        else:
            # No mine column: fall back to the file name.
            if mine_matches(path.stem, target):
                matched = df
                column_source = "filename"
                result.mine_column = None

        files.append(result)
        if not matched.empty:
            matched = matched.copy()
            matched.attrs["source_file"] = path.name
            matched.attrs["mine_column"] = mine_col or "filename"
            frames.append(matched)

    if not frames:
        return {
            "available": False,
            "status": NA,
            "message": "No uploaded dataset contains records for this mine.",
            "files_scanned": [f.__dict__ for f in files],
            "mine_column_source": "none",
        }

    merged = pd.concat(frames, ignore_index=True, sort=False)
    merged.attrs["mine_column_source"] = column_source
    return _summarise(merged, mine_name, files, column_source)


def _summarise(df: pd.DataFrame, mine_name: str, files: list[CsvFileResult],
               column_source: str) -> dict:
    columns = list(df.columns)

    date_col = _pick(columns, DATE_COLUMNS)
    prod_col = _pick(columns, PRODUCTION_COLUMNS)
    grade_col = _pick(columns, GRADE_COLUMNS)
    reserve_col = _pick(columns, RESERVE_COLUMNS)
    equip_col = _pick(columns, EQUIPMENT_COLUMNS)
    down_col = _pick(columns, DOWNTIME_COLUMNS)
    rain_col = _pick(columns, RAINFALL_COLUMNS)
    temp_col = _pick(columns, TEMPERATURE_COLUMNS)
    hum_col = _pick(columns, HUMIDITY_COLUMNS)
    wind_col = _pick(columns, WIND_COLUMNS)
    tmin_col = _pick(columns, TMIN_COLUMNS)
    tmax_col = _pick(columns, TMAX_COLUMNS)
    press_col = _pick(columns, PRESSURE_COLUMNS)
    cloud_col = _pick(columns, CLOUD_COLUMNS)
    wind_dir_col = _pick(columns, WIND_DIR_COLUMNS)
    cond_col = _pick(columns, CONDITION_COLUMNS)
    soil_col = _pick(columns, SOIL_COLUMNS)
    veg_col = _pick(columns, VEGETATION_COLUMNS)
    lat_col = _pick(columns, LATITUDE_COLUMNS)
    lon_col = _pick(columns, LONGITUDE_COLUMNS)

    dates = _coerce_dates(df[date_col]) if date_col else pd.Series(dtype="datetime64[ns]")
    production = _number(df[prod_col]) if prod_col else pd.Series(dtype=float)

    date_range = UNAVAILABLE
    latest_date = None
    if date_col and dates.notna().any():
        first, last = dates.min(), dates.max()
        date_range = f"{first.date().isoformat()} to {last.date().isoformat()}"
        latest_date = last.date().isoformat()

    prod_values: list[float] = []
    latest_production = None
    average_production = volatility = None
    if prod_col:
        prod_values = [None if pd.isna(v) else float(v) for v in production.tolist()]
        finite = production.dropna()
        if not finite.empty:
            average_production = _stat(finite.mean())
            volatility = _stat(finite.std(ddof=0)) if len(finite) > 1 else 0.0
            latest_production = _stat(finite.iloc[-1])

    direction, trend_evidence = _trend(prod_values) if prod_values else (UNAVAILABLE, NA)

    missing_cells = int(df.isna().sum().sum())
    total_cells = int(df.shape[0] * max(df.shape[1], 1))

    # Equipment / downtime
    equipment_risk = UNAVAILABLE
    if equip_col:
        statuses = df[equip_col].astype(str).str.strip()
        statuses = statuses[statuses.str.lower().ne("nan")]
        if not statuses.empty:
            bad_kw = ("fail", "down", "fault", "idle", "break", "outage", "stop",
                      "halt", "poor", "unavailable")
            bad = int(statuses.str.lower().str.contains("|".join(bad_kw)).sum())
            equipment_risk = (
                f"{bad} of {len(statuses)} equipment records flagged as degraded."
                if bad else
                f"0 of {len(statuses)} equipment records flagged as degraded."
            )

    downtime_total = None
    if down_col:
        downtime_total = _stat(_number(df[down_col]).sum())

    reserve_total = None
    reserve_note = UNAVAILABLE
    if reserve_col:
        res = _number(df[reserve_col]).dropna()
        if not res.empty:
            reserve_total = _stat(res.max())
            reserve_note = f"Maximum reported reserve value: {reserve_total}."

    # CSV-observed weather / environment
    csv_weather: dict[str, Any] = {}
    for label, col, unit in (
        ("rainfall", rain_col, "mm"),
        ("temperature", temp_col, "deg C"),
        ("humidity", hum_col, "%"),
        ("wind", wind_col, "km/h"),
        ("pressure", press_col, "hPa"),
        ("cloud_cover", cloud_col, "%"),
        ("soil_moisture", soil_col, ""),
        ("vegetation_index", veg_col, ""),
    ):
        if col:
            series = _number(df[col]).dropna()
            if not series.empty:
                csv_weather[label] = {
                    "mean": _stat(series.mean()),
                    "max": _stat(series.max()),
                    "min": _stat(series.min()),
                    "records": int(len(series)),
                    "unit": unit,
                    "column": col,
                }

    # Files that split the daily range into tmin/tmax keep their own extremes.
    if ("temperature" in csv_weather and tmin_col and tmax_col
            and tmin_col != temp_col and tmax_col != temp_col):
        tmin_series = _number(df[tmin_col]).dropna()
        tmax_series = _number(df[tmax_col]).dropna()
        if not tmin_series.empty and not tmax_series.empty:
            csv_weather["temperature"]["observed_min"] = _stat(tmin_series.min())
            csv_weather["temperature"]["observed_max"] = _stat(tmax_series.max())
            csv_weather["temperature"]["mean_daily_low"] = _stat(tmin_series.mean())
            csv_weather["temperature"]["mean_daily_high"] = _stat(tmax_series.mean())

    if wind_dir_col:
        degrees = _number(df[wind_dir_col]).dropna()
        degrees = degrees[(degrees >= 0) & (degrees <= 360)]
        if len(degrees) >= 3:
            radians = np.deg2rad(degrees)
            mean_deg = float(np.rad2deg(
                np.arctan2(np.sin(radians).mean(), np.cos(radians).mean())) % 360)
            csv_weather["wind_direction"] = {
                "mean": round(mean_deg, 1),
                "cardinal": _cardinal(mean_deg),
                "records": int(len(degrees)),
                "unit": "deg",
                "column": wind_dir_col,
            }

    if cond_col:
        labels = df[cond_col].astype(str).str.strip()
        labels = labels[(labels.str.lower() != "nan") & (labels != "")]
        if not labels.empty:
            counts = labels.value_counts().head(5)
            csv_weather["conditions"] = {
                "top": [f"{name} ({count} days)" for name, count in counts.items()],
                "distinct": int(labels.nunique()),
                "records": int(len(labels)),
                "unit": "",
                "column": cond_col,
            }

    # Coordinates recorded in the data itself
    csv_coords = None
    if lat_col and lon_col:
        lat = _number(df[lat_col]).dropna()
        lon = _number(df[lon_col]).dropna()
        if not lat.empty and not lon.empty:
            csv_coords = {"latitude": _stat(lat.iloc[-1]), "longitude": _stat(lon.iloc[-1]),
                          "source": "csv"}

    trend_rows = []
    if prod_col:
        working = pd.DataFrame({"date": dates, "production": production})
        working = working.dropna(subset=["production"])
        if date_col and dates.notna().any():
            working = working.sort_values("date")
        tail = working.tail(MAX_TREND_ROWS)
        for _, row in tail.iterrows():
            label = (row["date"].date().isoformat()
                     if date_col and not pd.isna(row["date"]) else "")
            trend_rows.append({"date": label, "production": _stat(row["production"])})
        if trend_rows:
            # "Latest" must follow chronology, not the physical row order.
            latest_production = trend_rows[-1]["production"]

    preview_cols = [c for c in (date_col, prod_col, grade_col, reserve_col, equip_col)
                    if c][:MAX_RAW_PREVIEW_ROWS]
    preview = []
    if preview_cols:
        for _, row in df[preview_cols].head(MAX_RAW_PREVIEW_ROWS).iterrows():
            preview.append({c: (None if pd.isna(row[c]) else str(row[c])) for c in preview_cols})

    def fmt(value, unit=""):
        return f"{value}{unit}" if value is not None else UNAVAILABLE

    return {
        "available": True,
        "status": "AVAILABLE",
        "mine_column_source": column_source,
        "files_scanned": [f.__dict__ for f in files],
        "quality": {
            "records": int(len(df)),
            "columns_detected": len(columns),
            "date_range": date_range,
            "latest_record": latest_date or UNAVAILABLE,
            "missing_values": missing_cells,
            "missing_percent": round(100 * missing_cells / total_cells, 1) if total_cells else 0.0,
        },
        "production": {
            "available": prod_col is not None,
            "column": prod_col or None,
            "unit": "tonnes" if prod_col else None,
            "latest": latest_production,
            "average": average_production,
            "volatility_std": volatility,
            "direction": direction,
            "evidence": trend_evidence,
            "series": trend_rows,
        },
        "grade": {"available": grade_col is not None,
                  "average": _stat(_number(df[grade_col]).mean()) if grade_col else None},
        "reserve": {"available": reserve_total is not None, "value": reserve_total,
                    "note": reserve_note},
        "equipment": {"available": equip_col is not None, "summary": equipment_risk,
                      "downtime_total": downtime_total,
                      "downtime_unit": "hours" if downtime_total is not None else None},
        "csv_weather": csv_weather,
        "csv_coordinates": csv_coords,
        "other_columns": [c for c in columns if c not in {
            date_col, prod_col, grade_col, reserve_col, equip_col, down_col,
            rain_col, temp_col, hum_col, wind_col, soil_col, veg_col, lat_col, lon_col,
            tmin_col, tmax_col, press_col, cloud_col, wind_dir_col, cond_col,
            *_MINE_SET(),
        }],
        "preview": preview,
    }


def _MINE_SET() -> set[str]:
    return set(MINE_COLUMNS)


def available_mine_names() -> list[str]:
    """Mine names actually observed in uploaded datasets (for the selector)."""
    names: set[str] = set()
    for path in upload_files():
        try:
            df = _read_csv(path)
            if df is None or df.empty:
                continue
            df.columns = [_norm_col(c) for c in df.columns]
            mine_col = _pick(list(df.columns), MINE_COLUMNS)
            if mine_col:
                names.update(str(v).strip() for v in df[mine_col].dropna().unique() if str(v).strip())
        except Exception:  # noqa: BLE001
            continue
    return sorted(names)
