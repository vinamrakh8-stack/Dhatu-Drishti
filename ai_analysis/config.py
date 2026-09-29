"""Central configuration for the AI Analysis module.

Secrets are read from the process environment, optionally seeded from a local
``.env`` file.  The Gemini key must never be rendered into a template or
returned by an API endpoint.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
ENV_FILE = BASE_DIR / ".env"

# Upload constraints (no arbitrary paths are ever accepted from the client).
ALLOWED_UPLOAD_SUFFIXES = {".csv"}
MAX_UPLOAD_BYTES = 16 * 1024 * 1024  # 16 MB
MAX_UPLOADED_FILES = 50

# Limits used when compressing datasets before they reach Gemini.
MAX_PROMPT_CHARS = 48_000
MAX_TREND_ROWS = 12
MAX_RAW_PREVIEW_ROWS = 5

GEMINI_TIMEOUT_SECONDS = 60
GEMINI_MODEL_DEFAULT = "gemini-3.8-flash"
# Tried in order when the preferred model is unavailable or rate limited.
GEMINI_MODEL_FALLBACKS = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
)

ANALYSIS_CACHE_TTL_SECONDS = 600
WEATHER_CACHE_TTL_SECONDS = 1800


def _load_dotenv() -> None:
    """Seed ``os.environ`` from ``.env`` without overriding real env vars."""
    if not ENV_FILE.is_file():
        return
    try:  # pragma: no cover - depends on optional package
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE, override=False)
        return
    except Exception:  # noqa: BLE001 - fall back to a tiny stdlib parser
        pass

    try:
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        # Missing/unreadable .env must never break the app; the API key may
        # still be provided directly through the process environment.
        return


_load_dotenv()


def gemini_api_key() -> str | None:
    """Return the configured Gemini key, or ``None`` when unset."""
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    return key or None


def gemini_model() -> str:
    return (os.environ.get("GEMINI_MODEL") or GEMINI_MODEL_DEFAULT).strip()


def weather_timeout() -> float:
    try:
        return float(os.environ.get("WEATHER_TIMEOUT", "12"))
    except (TypeError, ValueError):
        return 12.0
