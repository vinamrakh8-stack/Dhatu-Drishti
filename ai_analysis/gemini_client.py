"""Gemini REST client: call, parse, validate.

The API key is only ever read from the server environment and sent in a
request header, so it never appears in a URL, a template or browser source.
Model output is treated as untrusted text: it is parsed defensively and
shape-checked before the frontend ever sees it.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

from .config import (
    GEMINI_MODEL_FALLBACKS,
    GEMINI_TIMEOUT_SECONDS,
    gemini_api_key,
    gemini_model,
)
from .prompts import SYSTEM_INSTRUCTION

log = logging.getLogger(__name__)

NA = "DATA NOT AVAILABLE"
UNAVAILABLE = "Not available"
NOT_AVAILABLE = "Data not available"

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH"}
_DIRECTIONS = {"INCREASE", "STABLE", "DECREASE"}

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S | re.I)


class GeminiError(Exception):
    """Raised with a message that is safe to show to the user."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


def _extract_json(text: str) -> dict | None:
    """Pull a JSON object out of a model response without executing anything."""
    if not text:
        return None
    candidate = text.strip()

    fenced = _FENCE_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # Locate the first balanced object starting at a top-level '{'.
    start = candidate.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(candidate)):
            char = candidate[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    chunk = candidate[start:index + 1]
                    try:
                        parsed = json.loads(chunk)
                        if isinstance(parsed, dict):
                            return parsed
                    except (json.JSONDecodeError, TypeError):
                        break
        start = candidate.find("{", start + 1)
    return None


def _as_text(value: Any, default: str = NOT_AVAILABLE) -> str:
    if isinstance(value, str):
        text = value.strip()
        return text if text else default
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        joined = "; ".join(f"{k}: {v}" for k, v in value.items())
        return joined.strip() or default
    if isinstance(value, list):
        return "; ".join(str(v) for v in value) if value else default
    return str(value)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                text = " - ".join(str(v) for v in item.values() if v)
                out.append(text or json.dumps(item))
            else:
                out.append(str(item))
        return [x for x in out if x.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _enum(value: Any, allowed: set[str]) -> str:
    text = str(value or "").strip().upper().replace("-", " ").replace("_", " ")
    for option in allowed:
        if option.lower() in text.lower():
            return option
    if "avail" in text.lower() or "not available" in text.lower():
        return NOT_AVAILABLE
    return NOT_AVAILABLE


def validate_report(raw: dict) -> dict:
    """Coerce an arbitrary model object into the documented report schema."""
    production = raw.get("production_trend") or {}
    prediction = raw.get("short_term_prediction") or {}

    report = {
        "mine": _as_text(raw.get("mine"), UNAVAILABLE),
        "overview": _as_text(raw.get("overview") or raw.get("summary")),
        "operational_condition": _as_text(raw.get("operational_condition")),
        "production_trend": {
            "direction": _enum(production.get("direction") if isinstance(production, dict) else None,
                               _DIRECTIONS),
            "evidence": _as_text(production.get("evidence") if isinstance(production, dict) else None),
        },
        "reserve_status": _as_text(raw.get("reserve_status")),
        "equipment_risk": _as_text(raw.get("equipment_risk")),
        "weather_impact": _as_text(raw.get("weather_impact")),
        "geographic_risk": _as_text(raw.get("geographic_risk")),
        "risk_level": _enum(raw.get("risk_level"), _RISK_LEVELS),
        "risk_factors": _as_list(raw.get("risk_factors")),
        "recommended_actions": _as_list(raw.get("recommended_actions")),
        "short_term_prediction": {
            "direction": _enum(prediction.get("direction") if isinstance(prediction, dict) else None,
                               _DIRECTIONS),
            "risk": _enum(prediction.get("risk") if isinstance(prediction, dict) else None,
                          _RISK_LEVELS),
            "forecast": _as_text(prediction.get("forecast") if isinstance(prediction, dict) else None),
            "confidence": _as_text(prediction.get("confidence") if isinstance(prediction, dict) else None),
        },
        "data_limitations": _as_list(raw.get("data_limitations")),
    }

    # Anything the model echoed back verbatim from a missing dataset is kept,
    # but a blank limitation list would hide gaps from the user.
    if not report["data_limitations"]:
        report["data_limitations"] = ["No explicit limitations reported by the model."]
    return report


def _post(model: str, api_key: str, user_prompt: str) -> requests.Response:
    return requests.post(
        ENDPOINT.format(model=model),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.9,
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
            },
        },
        timeout=GEMINI_TIMEOUT_SECONDS,
    )


def generate_report(user_prompt: str, *, preferred_model: str | None = None) -> tuple[dict, str]:
    """Return ``(validated_report, model_name)`` or raise :class:`GeminiError`."""
    api_key = gemini_api_key()
    if not api_key:
        raise GeminiError(
            "The AI service is not configured. Set GEMINI_API_KEY in the server "
            "environment or .env file."
        )

    candidates: list[str] = []
    for model in (preferred_model or gemini_model(), *GEMINI_MODEL_FALLBACKS):
        if model and model not in candidates:
            candidates.append(model)

    last_message = "The AI service could not be reached."
    for model in candidates:
        for try_index in range(2):
            try:
                response = _post(model, api_key, user_prompt)
            except requests.Timeout:
                last_message = "The AI request timed out. Please try again."
                log.warning("gemini timeout model=%s", model)
                break
            except requests.RequestException as exc:
                last_message = "The AI service could not be reached."
                log.warning("gemini transport error model=%s: %s", model, exc)
                break

            if response.status_code == 429:
                last_message = "The AI service is rate limited. Please retry shortly."
                log.warning("gemini rate limited model=%s", model)
                if try_index == 0:
                    time.sleep(1.0)
                    continue
                break
            if response.status_code in (401, 403):
                last_message = "The AI service rejected the configured credentials."
                log.error("gemini auth failure model=%s status=%s", model, response.status_code)
                break
            if response.status_code in (404, 400):
                # 404: model retired for this key. 400 here is a request/key
                # rejection - neither is fixed by retrying the same model.
                last_message = "The AI service could not process this request."
                log.warning("gemini model unusable model=%s status=%s body=%s",
                            model, response.status_code, response.text[:300])
                break
            if response.status_code >= 500:
                # Overloaded. Retrying the same model immediately rarely helps,
                # so move straight to the next candidate instead of stalling.
                last_message = "The AI service is temporarily unavailable."
                log.warning("gemini server error model=%s status=%s", model,
                            response.status_code)
                break
            if not response.ok:
                last_message = "The AI service returned an unexpected response."
                log.warning("gemini error model=%s status=%s body=%s",
                            model, response.status_code, response.text[:300])
                break

            try:
                payload = response.json()
            except ValueError:
                last_message = "The AI service returned an unreadable response."
                log.warning("gemini non-json model=%s", model)
                break

            text = _response_text(payload)
            parsed = _extract_json(text)
            if parsed is None:
                last_message = "The AI returned an invalid report. Please retry."
                log.warning("gemini unparseable model=%s len=%s", model, len(text or ""))
                break

            report = validate_report(parsed)
            report["model"] = model
            return report, model

    raise GeminiError(last_message)


def _response_text(payload: dict) -> str:
    try:
        candidates = payload.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    except (AttributeError, IndexError, TypeError):
        return ""
