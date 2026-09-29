"""HTTP endpoints for the AI Analysis module.

Every handler returns JSON with ``success``/``error`` on failure and never
leaks a stack trace, an internal path or the Gemini API key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from werkzeug.utils import secure_filename

from . import data_loader, prompts, registry
from .config import (
    ALLOWED_UPLOAD_SUFFIXES,
    ANALYSIS_CACHE_TTL_SECONDS,
    MAX_PROMPT_CHARS,
    MAX_UPLOAD_BYTES,
    MAX_UPLOADED_FILES,
    UPLOAD_DIR,
)
from .gemini_client import GeminiError, generate_report
from .geodata import collect_geography, compact_geo_block
from .report_docs import build_docx, build_pdf, build_text, safe_filename
from .risk import build_risk_model, compact_risk_block
from .weather import collect_weather, compact_weather_block

log = logging.getLogger(__name__)

bp = Blueprint("ai_analysis", __name__)

_analysis_cache: dict[str, dict] = {}

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")
_MINE_NAME_MAX = 120

DISCLAIMER = (
    "Predictions are model-generated estimates, not guaranteed future "
    "observations. Risk overlays are derived from weather, elevation and "
    "operational inputs - the satellite basemap does not itself indicate risk."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error(message: str, status: int = 400) -> tuple:
    return jsonify({"success": False, "error": message}), status


def _payload() -> dict:
    """Accept both JSON bodies and classic Flask form submissions."""
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return {key: value for key, value in request.form.items()}


def _read_mine_input(data: dict) -> tuple[str, str, str, str]:
    mine = registry.clean_mine_name(
        data.get("mine_name") or data.get("mining_field") or data.get("mine") or ""
    )
    state = registry.clean_mine_name(data.get("state") or "", limit=60)
    district = registry.clean_mine_name(data.get("district") or "", limit=60)
    question = registry.clean_mine_name(data.get("question") or data.get("instruction") or "", limit=600)
    return mine, state, district, question


def _locate_in_catalog(mine: str) -> tuple[str, str]:
    """Find the configured state/district for a mine, if any."""
    target = registry.normalise_name(mine)
    for state, districts in registry.CATALOG.items():
        for district, names in districts.items():
            for name in names:
                if registry.mine_matches(name, target) or registry.mine_matches(mine,
                                                                                registry.normalise_name(name)):
                    return state, district
    return "", ""


def _cache_key(mine: str, question: str, fingerprint: str) -> str:
    raw = "|".join([mine, question, fingerprint])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _fingerprint(payload: dict) -> str:
    """Cheap, stable signature of the underlying data (not the model output)."""
    raw = json.dumps(
        {
            "quality": payload.get("data_summary"),
            "weather": payload.get("weather_available"),
            "geo": payload.get("geo_summary"),
            "risk": payload.get("risk_summary"),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _build_payload(mine: str, state: str, district: str, question: str) -> dict:
    """Collect every factual input.  No network or file error aborts the run."""
    if not state or not district:
        found_state, found_district = _locate_in_catalog(mine)
        state = state or found_state
        district = district or found_district

    coordinates = registry.resolve_coordinates(mine, state=state, district=district)
    mine_data = data_loader.collect_for_mine(mine)

    csv_geo = None
    if mine_data.get("available") and mine_data.get("csv_coordinates"):
        csv_geo = {**mine_data["csv_coordinates"], **{
            "soil_moisture": (mine_data.get("csv_weather") or {}).get("soil_moisture", {}).get("mean"),
            "vegetation_index": (mine_data.get("csv_weather") or {}).get("vegetation_index", {}).get("mean"),
            "lst": (mine_data.get("csv_weather") or {}).get("temperature", {}).get("mean"),
        }}

    weather = collect_weather(coordinates.get("latitude"), coordinates.get("longitude"))
    geography = collect_geography(coordinates, mine, csv_geo=csv_geo)
    risk = build_risk_model(coordinates, weather, mine_data)

    return {
        "mine": mine,
        "state": state,
        "district": district,
        "question": question,
        "coordinates": coordinates,
        "mine_data": mine_data,
        "weather": weather,
        "geography": geography,
        "risk": risk,
        "data_summary": (mine_data.get("quality") if mine_data.get("available") else None),
        "weather_available": weather.get("available"),
        "geo_summary": {
            "elevation": geography.get("elevation_m"),
            "slope": geography.get("slope_deg"),
            "precision": geography.get("coordinate_precision"),
        },
        "risk_summary": {
            "level": risk.get("overall_risk_level"),
            "score": risk.get("overall_score"),
        },
    }


def _has_any_data(payload: dict) -> bool:
    return bool(
        payload["mine_data"].get("available")
        or payload["coordinates"].get("latitude") is not None
        or payload["weather"].get("available")
    )


def _assemble_response(payload: dict, report: dict, generated_at: str, cached: bool) -> dict:
    mine = payload["mine_data"]
    weather = payload["weather"]
    geography = payload["geography"]
    risk = payload["risk"]

    prediction = report.get("short_term_prediction") or {}
    equipment_summary = (mine.get("equipment") or {}).get("summary") if mine.get("available") else None

    kpis = {
        "risk_level": report.get("risk_level", "Data not available"),
        "production_outlook": prediction.get("direction", "Data not available"),
        "weather_impact": _summarise_weather(weather),
        "machinery_health": equipment_summary or "Data not available",
    }

    # Backwards-compatible aliases used by the previous page contract.
    legacy_findings = [
        {"title": "Operational condition", "description": report.get("operational_condition", "")},
        {"title": "Weather impact", "description": report.get("weather_impact", "")},
        {"title": "Geographic / environmental risk", "description": report.get("geographic_risk", "")},
        {"title": "Reserve status", "description": report.get("reserve_status", "")},
    ]

    return {
        "success": True,
        "mine": payload["mine"],
        "state": payload["state"],
        "district": payload["district"],
        "generated_at": generated_at,
        "model": report.get("model"),
        "cached": cached,
        "title": f"Mining Intelligence Report — {payload['mine']}",
        # Primary schema (documented response format)
        "report": report,
        # Legacy aliases
        "summary": report.get("overview", ""),
        "prediction": prediction.get("forecast", ""),
        "kpis": kpis,
        "findings": legacy_findings,
        "recommended_actions": report.get("recommended_actions", []),
        "risk_factors": report.get("risk_factors", []),
        "risks": report.get("risk_factors", []),
        # Supporting factual data for the dashboard widgets
        "coordinates": payload["coordinates"],
        "dataset": {
            "production": mine.get("production") if mine.get("available") else {"available": False},
            "quality": mine.get("quality"),
            "reserve": mine.get("reserve"),
            "equipment": mine.get("equipment"),
            "grade": mine.get("grade"),
        },
        "weather": weather,
        "geography": geography,
        "risk": risk,
        "data_summary": payload["data_summary"],
        "files_scanned": mine.get("files_scanned", []),
        "disclaimer": DISCLAIMER,
    }


def _persist_report(payload: dict, report: dict, model_name: str,
                    generated_at: str) -> None:
    """Store a successfully generated, schema-validated report.

    Called only after ``generate_report`` has returned validated JSON, so a
    missing dataset, a Gemini failure or an invalid response never writes a
    history row.  A database problem must not break the analysis response, so
    every error here is logged and swallowed.
    """
    if not report:
        return

    db = None
    try:
        from extensions import db as _db
        from models import AIReport

        db = _db
        risk = payload.get("risk") or {}
        prediction = report.get("short_term_prediction") or {}

        risk_level = report.get("risk_level")
        if risk_level not in ("LOW", "MEDIUM", "HIGH"):
            risk_level = None

        # Everything needed to re-render the report later.  Deliberately excludes
        # the Gemini key and any raw CSV rows.
        context = {
            "report": report,
            "generated_at": generated_at,
            "mine": payload.get("mine"),
            "state": payload.get("state"),
            "district": payload.get("district"),
            "coordinates": payload.get("coordinates"),
            "risk": {
                "level": risk.get("overall_risk_level"),
                "score": risk.get("overall_score"),
                "zones": len(risk.get("zones") or []),
            },
            "data_summary": payload.get("data_summary"),
            "weather_available": payload.get("weather_available"),
            "disclaimer": DISCLAIMER,
        }
        # Normalise to plain JSON types so the column never trips a serializer.
        context = json.loads(json.dumps(context, default=str))

        row = AIReport(
            mine_name=str(payload.get("mine") or "Unknown mine")[:200],
            risk_level=risk_level,
            prediction=(str(prediction.get("forecast") or "").strip()[:8000] or None),
            report_data=context,
            report_text=build_text(report, str(payload.get("mine") or ""),
                                   generated_at),
            model_name=str(model_name or report.get("model") or "").strip()[:100] or None,
        )
        db.session.add(row)
        db.session.commit()
        log.info("stored AI report id=%s mine=%r model=%s",
                 row.id, row.mine_name, row.model_name)
    except Exception:  # noqa: BLE001 - persistence must never fail the request
        log.exception("could not persist AI report for mine=%r",
                      payload.get("mine"))
        if db is not None:
            try:
                db.session.rollback()
            except Exception:  # noqa: BLE001
                pass


def _summarise_weather(weather: dict) -> str:
    if not weather.get("available"):
        return "Data not available"
    forecast = weather.get("forecast") or {}
    historical = weather.get("historical") or {}
    bits = []
    if forecast.get("available"):
        bits.append(f"forecast {forecast.get('rainfall_total_mm') or 0} mm")
    if historical.get("available"):
        bits.append(f"observed {historical.get('rainfall_total_mm') or 0} mm")
    return " / ".join(bits) if bits else "Data not available"


def _weather_text(payload: dict) -> str:
    """Open-Meteo archive/forecast plus what the uploaded rows recorded.

    The uploaded datasets are often the only mine-specific weather source, so
    they must reach the model alongside the coordinate-based services.
    """
    blocks = [compact_weather_block(payload["weather"])]
    observed = prompts.observed_weather_block(payload.get("mine_data") or {})
    if observed:
        blocks.append(observed)
    return "\n\n".join(blocks)


def run_analysis(mine: str, state: str, district: str, question: str) -> tuple:
    """Full pipeline; returns ``(response_dict, http_status)``."""
    payload = _build_payload(mine, state, district, question)

    if not _has_any_data(payload):
        return _error("No relevant data was found for the selected mine.", 422)

    generated_at = _now()
    fingerprint = _fingerprint(payload)
    key = _cache_key(mine, question, fingerprint)
    cached_entry = _analysis_cache.get(key)
    now = time.time()
    if cached_entry and cached_entry["expires"] > now:
        response = dict(cached_entry["value"])
        response["generated_at"] = generated_at
        response["cached"] = True
        return jsonify(response), 200

    weather_text = _weather_text(payload)
    geo_text = compact_geo_block(payload["geography"])
    production_text = prompts.production_block(payload["mine_data"])
    reserve_text = prompts.reserve_block(payload["mine_data"])
    equipment_text = prompts.equipment_block(payload["mine_data"])
    satellite_text = prompts.satellite_block(payload["mine_data"], payload["geography"],
                                             payload["weather"])
    risk_text = compact_risk_block(payload["risk"])

    location_lines = [
        f"- Configured state: {payload['state'] or 'Data not available'}",
        f"- Configured district: {payload['district'] or 'Data not available'}",
        f"- Coordinates: {payload['geography'].get('latitude')}, "
        f"{payload['geography'].get('longitude')}",
        f"- Coordinate precision: {payload['geography'].get('coordinate_precision')}",
    ]
    if payload["geography"].get("coordinate_note"):
        location_lines.append(f"- Note: {payload['geography']['coordinate_note']}")

    other_lines = []
    if payload["mine_data"].get("available"):
        other_lines.append(f"- Uploaded files scanned: {len(payload['mine_data'].get('files_scanned', []))}")
        other_lines.append(f"- Mine column detection: {payload['mine_data'].get('mine_column_source')}")
    else:
        other_lines.append("- Uploaded datasets: none contain this mine")
    unattributed = prompts.unattributed_files_block(payload["mine_data"])
    if unattributed:
        other_lines.append(unattributed)
    other_lines.append("- Data collection ran at: " + generated_at)

    user_prompt = prompts.build_user_prompt(
        mine_name=mine,
        instruction=question,
        location_data="\n".join(location_lines),
        production_data=production_text,
        reserve_data=reserve_text,
        equipment_data=equipment_text,
        weather_data=weather_text,
        geographical_data=geo_text,
        satellite_data=satellite_text,
        risk_data=risk_text,
        other_data="\n".join(other_lines),
        max_chars=MAX_PROMPT_CHARS,
    )

    try:
        report, model_name = generate_report(user_prompt)
    except GeminiError as exc:
        log.warning("analysis failed for mine=%r: %s", mine, exc.message)
        return _error(exc.message, 502)

    response = _assemble_response(payload, report, generated_at, cached=False)
    _persist_report(payload, report, model_name, generated_at)
    _analysis_cache[key] = {"value": response, "expires": now + ANALYSIS_CACHE_TTL_SECONDS}
    if len(_analysis_cache) > 64:  # keep the cache bounded
        oldest = sorted(_analysis_cache.items(), key=lambda kv: kv[1]["expires"])[:8]
        for stale_key, _ in oldest:
            _analysis_cache.pop(stale_key, None)

    return jsonify(response), 200


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

def handle_analysis_request() -> tuple:
    """Shared entry point for ``/ai-analysis`` and ``/api/ai-analysis`` POSTs."""
    data = _payload()
    mine, state, district, question = _read_mine_input(data)
    if not mine:
        return _error("Please select a mine.", 400)
    return run_analysis(mine, state, district, question)


@bp.route("/api/ai-analysis", methods=["POST"])
def api_ai_analysis():
    """Endpoint used by the existing dashboard contract."""
    return handle_analysis_request()


@bp.route("/api/mines", methods=["GET"])
def api_mines():
    """Catalogue (configured mines plus mines found in uploaded datasets)."""
    for name in data_loader.available_mine_names():
        state, district = _locate_in_catalog(name)
        registry.add_catalog_mine(
            state or "Uploaded data",
            district or "Unclassified",
            name,
        )
    return jsonify({"success": True, "catalog": registry.CATALOG,
                    "uploads": data_loader.list_upload_info()})


@bp.route("/api/ai-analysis/uploads", methods=["GET"])
def list_uploads():
    return jsonify({"success": True, "uploads": data_loader.list_upload_info(),
                    "upload_dir_name": "uploads"})


@bp.route("/api/ai-analysis/uploads", methods=["POST"])
def upload_csv():
    files = request.files.getlist("file") or []
    files = [f for f in files if f and f.filename]
    if not files:
        return _error("No file was uploaded.", 400)

    existing = data_loader.upload_files()
    if len(existing) >= MAX_UPLOADED_FILES:
        return _error("Upload limit reached. Remove a file before adding more.", 400)

    saved = []
    for storage in files:
        filename = secure_filename(storage.filename or "")
        if not filename or Path(filename).suffix.lower() not in ALLOWED_UPLOAD_SUFFIXES:
            return _error("Only .csv files can be uploaded.", 400)

        storage.stream.seek(0, 2)
        size = storage.stream.tell()
        storage.stream.seek(0)
        if size > MAX_UPLOAD_BYTES:
            return _error("File is larger than the 16 MB limit.", 400)
        if size == 0:
            return _error("The uploaded file is empty.", 400)

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        target_name = f"{uuid.uuid4().hex[:8]}-{_UNSAFE_NAME.sub('_', filename)}"
        target = (UPLOAD_DIR / target_name).resolve()
        if target.parent != UPLOAD_DIR.resolve() or not target.name.endswith(".csv"):
            return _error("Invalid file name.", 400)
        storage.save(str(target))
        saved.append({"name": target.name, "original": filename, "size_bytes": size})

    return jsonify({"success": True, "uploaded": saved,
                    "uploads": data_loader.list_upload_info()})


@bp.route("/api/ai-analysis/uploads/<path:filename>", methods=["DELETE"])
def delete_upload(filename: str):
    safe_name = Path(secure_filename(filename)).name
    if not safe_name or safe_name != filename or not safe_name.endswith(".csv"):
        return _error("Invalid file name.", 400)
    target = (UPLOAD_DIR / safe_name).resolve()
    if target.parent != UPLOAD_DIR.resolve():
        return _error("Invalid file name.", 400)
    if not target.is_file():
        return _error("File not found.", 404)
    try:
        target.unlink()
    except OSError:
        return _error("The file could not be removed.", 500)
    return jsonify({"success": True, "uploads": data_loader.list_upload_info()})


@bp.route("/api/ai-analysis/report/<kind>", methods=["POST"])
def download_report(kind: str):
    data = _payload()
    mine, state, district, question = _read_mine_input(data)
    if not mine:
        return _error("Please select a mine.", 400)

    # Re-run without cache so the downloaded document matches the schema.
    payload = _build_payload(mine, state, district, question)
    if not _has_any_data(payload):
        return _error("No relevant data was found for the selected mine.", 422)

    fingerprint = _fingerprint(payload)
    key = _cache_key(mine, question, fingerprint)
    cached_entry = _analysis_cache.get(key)
    if cached_entry and cached_entry["expires"] > time.time():
        response = cached_entry["value"]
    else:
        user_prompt = prompts.build_user_prompt(
            mine_name=mine,
            instruction=question,
            location_data=compact_geo_block(payload["geography"]),
            production_data=prompts.production_block(payload["mine_data"]),
            reserve_data=prompts.reserve_block(payload["mine_data"]),
            equipment_data=prompts.equipment_block(payload["mine_data"]),
            weather_data=_weather_text(payload),
            geographical_data=compact_geo_block(payload["geography"]),
            satellite_data=prompts.satellite_block(payload["mine_data"], payload["geography"],
                                                   payload["weather"]),
            risk_data=compact_risk_block(payload["risk"]),
            other_data="Report requested for download.",
            max_chars=MAX_PROMPT_CHARS,
        )
        try:
            report, _model = generate_report(user_prompt)
        except GeminiError as exc:
            return _error(exc.message, 502)
        response = _assemble_response(payload, report, _now(), cached=False)
        _analysis_cache[key] = {"value": response,
                                "expires": time.time() + ANALYSIS_CACHE_TTL_SECONDS}

    report = response.get("report") or {}
    generated_at = response.get("generated_at", _now())
    try:
        if kind == "docx":
            blob = build_docx(report, mine, generated_at)
            mimetype = ("application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document")
        elif kind == "pdf":
            blob = build_pdf(report, mine, generated_at)
            mimetype = "application/pdf"
        else:
            return _error("Unsupported report format.", 400)
    except Exception:  # noqa: BLE001 - never surface a stack trace
        log.exception("report rendering failed kind=%s", kind)
        return _error("The report could not be generated. Please retry.", 500)

    return send_file(
        BytesIO(blob),
        mimetype=mimetype,
        as_attachment=True,
        download_name=safe_filename(mine, kind),
    )


def _history_entry(row) -> dict:
    """Shape one stored ``AIReport`` row for the public ``/reports`` table.

    Every field is copied verbatim from what was persisted at generation time;
    nothing is invented when a value is missing.
    """
    context = row.structured
    report = row.report
    prediction = report.get("short_term_prediction")
    prediction = prediction if isinstance(prediction, dict) else {}

    created = row.created_at
    stamp = created.strftime("%Y-%m-%d %H:%M") if created else ""

    def _text(value) -> str:
        return value if isinstance(value, str) else ""

    return {
        # A string id: the page compares it with strict equality against the
        # value handed to ``viewAI()``/``downloadReport()``.
        "id": str(row.id),
        "date": stamp,
        "state": _text(context.get("state")),
        "district": _text(context.get("district")),
        "mine": _text(row.mine_name),
        "risk": row.risk_level or "Unknown",
        "summary": _text(report.get("overview")),
        "prediction": _text(prediction.get("forecast")) or _text(row.prediction),
        "machinery": _text(report.get("equipment_risk")),
        "model": _text(row.model_name),
        "disclaimer": _text(context.get("disclaimer")) or DISCLAIMER,
    }


@bp.route("/api/reports/history")
def api_reports_history():
    """Saved AI analyses backing the public ``/reports`` page.

    There is no weather-prediction table, so that list is returned empty rather
    than filled with sample rows.
    """
    try:
        from models import AIReport

        rows = AIReport.query.order_by(AIReport.created_at.desc(),
                                        AIReport.id.desc()).limit(50).all()
        history = [_history_entry(row) for row in rows]
    except Exception:  # noqa: BLE001 - never surface a stack trace
        log.exception("could not read saved AI report history")
        return _error("Report history is unavailable right now.", 500)

    return jsonify({"ai_analysis_history": history,
                    "weather_prediction_history": []})


@bp.route("/api/reports/ai/<int:report_id>/download/<kind>")
def api_reports_download(report_id: int, kind: str):
    """Regenerate a PDF/DOCX for a stored report from its saved JSON."""
    if kind not in ("pdf", "docx"):
        return _error("Unsupported report format.", 404)

    try:
        from extensions import db
        from models import AIReport

        row = db.session.get(AIReport, report_id)
    except Exception:  # noqa: BLE001
        log.exception("could not load AI report id=%s", report_id)
        return _error("The report could not be loaded. Please retry.", 500)

    if row is None:
        return _error("Report not found.", 404)

    report = row.report
    if not report:
        return _error("This record has no structured report to download.", 400)

    if kind == "docx":
        render = build_docx
        mimetype = ("application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document")
    else:
        render = build_pdf
        mimetype = "application/pdf"

    mine = row.structured.get("mine") or row.mine_name or "report"
    try:
        blob = render(report, mine, row.generated_at)
    except Exception:  # noqa: BLE001 - never surface a stack trace
        log.exception("historical report render failed id=%s kind=%s",
                      report_id, kind)
        return _error("The report could not be generated. Please retry.", 500)

    return send_file(
        BytesIO(blob),
        mimetype=mimetype,
        as_attachment=True,
        download_name=safe_filename(mine, kind),
    )
