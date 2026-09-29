"""Prompt construction for the mining intelligence report.

Only compact, factual summaries are placed in the prompt - raw CSV content is
never forwarded.  Missing datasets are passed as an explicit sentinel so the
model cannot paper over the gap with plausible-sounding numbers.
"""

from __future__ import annotations

NA = "DATA NOT AVAILABLE"

SYSTEM_INSTRUCTION = """You are a mining intelligence analysis engine.

Analyze ONLY the factual data provided below.

IMPORTANT RULES:

1. Do not invent data.
2. Do not add external facts that are not present in the supplied dataset.
3. Do not assume missing values.
4. If information is unavailable, explicitly write "Data not available".
5. Do not fabricate geological measurements.
6. Do not fabricate weather observations.
7. Distinguish historical observations from forecasts.
8. Base every numerical conclusion on the supplied data.
9. Do not provide generic mining information unless it directly helps interpret the supplied data.
10. Give concise, evidence-based conclusions.
11. Clearly separate observed data, derived analysis, and prediction.
12. Predictions are estimates, not guaranteed outcomes.

Return the result as valid JSON only, with exactly this schema:

{
  "mine": "",
  "overview": "",
  "operational_condition": "",
  "production_trend": {"direction": "", "evidence": ""},
  "reserve_status": "",
  "equipment_risk": "",
  "weather_impact": "",
  "geographic_risk": "",
  "risk_level": "LOW",
  "risk_factors": [],
  "recommended_actions": [],
  "short_term_prediction": {"direction": "", "risk": "", "forecast": "", "confidence": ""},
  "data_limitations": []
}

Allowed values:
- production_trend.direction: "INCREASE", "STABLE", "DECREASE" or "Data not available"
- risk_level: "LOW", "MEDIUM", "HIGH" or "Data not available"
- short_term_prediction.direction: "INCREASE", "STABLE", "DECREASE" or "Data not available"
- short_term_prediction.risk: "LOW", "MEDIUM", "HIGH" or "Data not available"

Do not wrap the JSON in Markdown fences and do not add commentary outside it.
"""


def _section(title: str, body: str) -> str:
    return f"{title}:\n{body.strip() if body and body.strip() else NA}"


def build_user_prompt(
    *,
    mine_name: str,
    instruction: str,
    location_data: str,
    production_data: str,
    reserve_data: str,
    equipment_data: str,
    weather_data: str,
    geographical_data: str,
    satellite_data: str,
    risk_data: str,
    other_data: str,
    max_chars: int,
) -> str:
    blocks = [
        _section("MINE", mine_name),
        _section("LOCATION", location_data),
        _section("HISTORICAL PRODUCTION", production_data),
        _section("RESERVE DATA", reserve_data),
        _section("EQUIPMENT DATA", equipment_data),
        _section("WEATHER DATA", weather_data),
        _section("GEOGRAPHICAL DATA", geographical_data),
        _section("SATELLITE/REMOTE-SENSING DATA", satellite_data),
        _section("RISK MODEL OUTPUT (derived, not an observation)", risk_data),
        _section("OTHER RELEVANT DATA", other_data),
    ]
    if instruction:
        blocks.insert(1, f"ADDITIONAL ANALYSIS REQUEST FROM THE USER:\n{instruction.strip()}")

    blocks.append(
        "Generate the following structured analysis:\n"
        "1. Mine Overview\n2. Current Operational Condition\n3. Historical Production Trend\n"
        "4. Reserve Status\n5. Equipment/Downtime Risk\n6. Weather Impact\n"
        "7. Geographic/Environmental Risk\n8. Current Risk Level\n9. Major Risk Factors\n"
        "10. Recommended Operational Actions\n11. Short-Term Production Outlook\n"
        "12. Mining Prediction for the Upcoming Days\n13. Confidence Level\n14. Data Limitations\n\n"
        "For short-term prediction analyze the latest available historical trends and "
        "available environmental/weather information, and return: expected production "
        "direction, expected risk, likely operational issues, relevant environmental "
        "factors, expected trend over the next several days, and confidence level.\n"
        "Do NOT invent exact future production numbers unless the supplied dataset is "
        "sufficient to mathematically support such a forecast.\n"
        "List every dataset section above that was marked DATA NOT AVAILABLE inside "
        "data_limitations."
    )

    prompt = "\n\n".join(blocks)
    if len(prompt) > max_chars:
        prompt = prompt[:max_chars] + "\n\n[INPUT TRUNCATED TO FIT LIMITS]"
    return prompt


def production_block(mine_data: dict) -> str:
    if not mine_data.get("available"):
        return NA
    q = mine_data["quality"]
    p = mine_data["production"]
    lines = [
        f"- Source: uploaded dataset(s); mine identified via "
        f"{mine_data.get('mine_column_source', 'unknown')} match",
        f"- Records: {q.get('records')}",
        f"- Date range: {q.get('date_range')}",
        f"- Latest available record: {q.get('latest_record')}",
        f"- Missing values: {q.get('missing_values')} ({q.get('missing_percent')}%)",
        f"- Production column: {p.get('column') or NA}",
        f"- Latest production: {p.get('latest') if p.get('latest') is not None else NA}",
        f"- Average production: {p.get('average') if p.get('average') is not None else NA}",
        f"- Production volatility (std dev): {p.get('volatility_std') if p.get('volatility_std') is not None else NA}",
        f"- Production trend: {p.get('direction')}",
        f"- Trend evidence: {p.get('evidence')}",
    ]
    if p.get("series"):
        lines.append("- Records (date, production):")
        for row in p["series"]:
            lines.append(f"    {row['date'] or 'no date'}: {row['production']}")
    if mine_data.get("preview"):
        lines.append("- Sample of source rows:")
        for row in mine_data["preview"]:
            lines.append("    " + ", ".join(f"{k}={v}" for k, v in row.items()))
    return "\n".join(lines)


def reserve_block(mine_data: dict) -> str:
    if not mine_data.get("available") or not mine_data.get("reserve", {}).get("available"):
        return NA
    reserve = mine_data["reserve"]
    grade = mine_data.get("grade") or {}
    lines = [f"- {reserve.get('note')}"]
    if grade.get("available") and grade.get("average") is not None:
        lines.append(f"- Average ore grade from dataset: {grade['average']}")
    return "\n".join(lines)


def equipment_block(mine_data: dict) -> str:
    if not mine_data.get("available") or not mine_data.get("equipment", {}).get("available"):
        return NA
    equip = mine_data["equipment"]
    lines = [f"- {equip.get('summary')}"]
    if equip.get("downtime_total") is not None:
        lines.append(f"- Total reported downtime: {equip.get('downtime_total')} "
                     f"{equip.get('downtime_unit')}")
    return "\n".join(lines)


def observed_weather_block(mine_data: dict) -> str:
    """Weather actually recorded in the uploaded rows for this mine.

    Distinct from ``compact_weather_block``, which reports the Open-Meteo
    archive and forecast for the coordinate.
    """
    if not mine_data.get("available"):
        return NA
    csv_weather = mine_data.get("csv_weather") or {}
    variables = [
        ("temperature", "Air temperature", " deg C"),
        ("humidity", "Relative humidity", " %"),
        ("rainfall", "Rainfall", " mm"),
        ("wind", "Wind speed", " km/h"),
        ("pressure", "Station pressure", " hPa"),
        ("cloud_cover", "Cloud cover", " %"),
    ]
    lines: list[str] = []
    for key, label, unit in variables:
        entry = csv_weather.get(key)
        if not entry:
            continue
        if not lines:
            lines.append("WEATHER RECORDED IN THE UPLOADED DATASET (observed, mine-specific):")
        row = (f"- {label}: mean {entry.get('mean')}{unit}, "
               f"range {entry.get('min')} to {entry.get('max')}{unit}")
        if key == "temperature":
            extras = []
            if entry.get("mean_daily_low") is not None:
                extras.append(f"mean daily low {entry['mean_daily_low']} deg C")
            if entry.get("mean_daily_high") is not None:
                extras.append(f"mean daily high {entry['mean_daily_high']} deg C")
            if extras:
                row += " (" + ", ".join(extras) + ")"
        row += f" over {entry.get('records')} records"
        lines.append(row)

    wind_dir = csv_weather.get("wind_direction")
    if wind_dir:
        if not lines:
            lines.append("WEATHER RECORDED IN THE UPLOADED DATASET (observed, mine-specific):")
        lines.append(f"- Prevailing wind direction: {wind_dir.get('cardinal')} "
                     f"({wind_dir.get('mean')} deg mean bearing, "
                     f"{wind_dir.get('records')} records)")

    conditions = csv_weather.get("conditions")
    if conditions and conditions.get("top"):
        if not lines:
            lines.append("WEATHER RECORDED IN THE UPLOADED DATASET (observed, mine-specific):")
        lines.append(f"- Reported sky/condition labels (top of {conditions.get('distinct')} "
                     f"distinct over {conditions.get('records')} records): "
                     + "; ".join(conditions["top"]))

    if not lines:
        return (NA + "\nNo temperature, humidity, rainfall, wind, pressure or cloud columns "
                     "were found in the uploaded rows for this mine.")
    lines.append("")
    lines.append("These are measured values from the mine's own dataset (not model output). "
                 "Use them as the mine-specific basis for the Weather Impact and Short-Term "
                 "Outlook sections, and quote them rather than restating only the "
                 "coordinate-level archive. Do not quote any value that is not listed above.")
    return "\n".join(lines)


def unattributed_files_block(mine_data: dict) -> str:
    """Files that were scanned but could not be tied to a mine."""
    scanned = mine_data.get("files_scanned") or []
    names = [f.get("filename") for f in scanned
             if not f.get("mine_column") and f.get("filename")]
    if not names:
        return ""
    return ("- Scanned but not attributed to this mine (no mine column / no name match): "
            + ", ".join(names))


def satellite_block(mine_data: dict, geo: dict, weather: dict) -> str:
    """Report remote-sensing inputs that actually exist in the data."""
    rows: list[str] = []
    csv_weather = mine_data.get("csv_weather") or {}
    for key, label, unit in (
        ("vegetation_index", "Vegetation index", ""),
        ("soil_moisture", "Soil moisture", ""),
        ("temperature", "Land surface / air temperature", "deg C"),
    ):
        entry = csv_weather.get(key)
        if entry:
            rows.append(f"- {label} from uploaded dataset: mean {entry.get('mean')}{unit}, "
                        f"max {entry.get('max')}{unit} over {entry.get('records')} records")

    if geo.get("vegetation_index") not in (None, "Not available"):
        rows.append(f"- Vegetation index: {geo.get('vegetation_index')}")
    if geo.get("land_surface_temperature") not in (None, "Not available"):
        rows.append(f"- Land surface temperature: {geo.get('land_surface_temperature')}")

    # Satellite-derived weather variables that are genuinely available
    hist = weather.get("historical") or {}
    if hist.get("available"):
        rows.append(f"- Historical rainfall series (Open-Meteo archive): total "
                    f"{hist.get('rainfall_total_mm')} mm over {hist.get('records')} days")

    if not rows:
        return (NA + "\nNo satellite or remote-sensing product was supplied for this mine. "
                     "Do not describe satellite-derived indicators.")
    return "\n".join(rows)
