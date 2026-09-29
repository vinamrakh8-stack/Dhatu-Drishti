"""Dhatu Drishti — Flask application entry point.

The existing public routes are preserved; this module adds the database
bootstrap, the admin blueprint and the database-backed /machinery listing.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlencode

from flask import Flask, abort, jsonify, render_template, request
from sqlalchemy import func, select

# Importing the AI config first seeds os.environ from .env, so DATABASE_URL,
# SECRET_KEY and the admin credentials below are already available.
from ai_analysis import config as _ai_config  # noqa: F401
from ai_analysis import bp as ai_analysis_bp
from ai_analysis.routes import handle_analysis_request

from admin import admin_bp
from extensions import db
from models import (MACHINERY_PER_PAGE, Machinery, distinct_machinery_values,
                    init_db, machinery_query)

log = logging.getLogger(__name__)

_SQLITE_PREFIX = "sqlite:///"


def _database_uri() -> str:
    """Resolve DATABASE_URL into a SQLAlchemy URI.

    ``DATABASE_URL=sqlite:///instance/app.db`` must land at
    ``<project>/instance/app.db``.  Flask-SQLAlchemy joins a *relative* SQLite
    path onto ``app.instance_path`` (which is already ``<project>/instance``),
    so the redundant leading ``instance/`` is stripped to avoid creating a
    nested ``instance/instance/app.db``.  Absolute paths and non-SQLite URLs
    (e.g. PostgreSQL) are passed through untouched.
    """
    uri = (os.environ.get("DATABASE_URL") or "").strip() or "sqlite:///instance/app.db"
    if not uri.startswith(_SQLITE_PREFIX):
        return uri

    path = uri[len(_SQLITE_PREFIX):]
    if not path or path.startswith("/") or path.startswith("file:") or path == ":memory:":
        return uri
    if path.startswith("instance/") or path.startswith("instance\\"):
        path = path[len("instance/"):]
    return _SQLITE_PREFIX + path


app = Flask(__name__)

# Environment-driven configuration. Point DATABASE_URL at a PostgreSQL URL and
# nothing else in this project needs to change.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or os.urandom(32).hex()
app.config["SQLALCHEMY_DATABASE_URI"] = _database_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

init_db(app)                      # db.init_app + create_all + first-run seed
app.register_blueprint(admin_bp)
app.register_blueprint(ai_analysis_bp)

# Every admin form renders {{ csrf_token() }} directly, so expose it globally
# rather than passing it through each render_template call.
from security import csrf_token  # noqa: E402  (needs a configured app)

app.jinja_env.globals["csrf_token"] = csrf_token

# --------------------------------------------------------------------------
# Existing public routes
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("cover.html")


@app.route("/weather")
def weather():
    return render_template("weather.html")


def _page_number() -> int:
    try:
        value = int(request.args.get("page") or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, value)


def _machinery_pager(page: int, pages: int, applied: dict) -> list[dict]:
    """Prev / numbered / next links that keep the active filters."""
    if pages <= 1:
        return []

    def href(target: int) -> str:
        args = {key: value for key, value in applied.items() if value}
        if target > 1:
            args["page"] = str(target)
        query = urlencode(args)
        return "/machinery" + (f"?{query}" if query else "")

    links = [{"label": "‹", "url": href(page - 1), "active": False,
              "disabled": page <= 1}]
    for target in range(1, pages + 1):
        links.append({"label": str(target), "url": href(target),
                      "active": target == page, "disabled": False})
    links.append({"label": "›", "url": href(page + 1), "active": False,
                  "disabled": page >= pages})
    return links


@app.route("/machinery")
def machinery():
    """Public machinery list — server-side search, filters and pagination."""
    statement, applied = machinery_query(request.args)

    total = db.session.scalar(
        select(func.count()).select_from(statement.subquery())) or 0
    pages = max(1, -(-total // MACHINERY_PER_PAGE))
    page = min(_page_number(), pages)
    offset = (page - 1) * MACHINERY_PER_PAGE

    machines = db.session.scalars(
        statement.limit(MACHINERY_PER_PAGE).offset(offset)).all()

    return render_template(
        "machinery.html",
        machines=machines,
        total=total,
        page=page,
        pages=pages,
        pager=_machinery_pager(page, pages, applied),
        applied=applied,
        types=distinct_machinery_values(Machinery.machine_type),
        mines=distinct_machinery_values(Machinery.mine_name),
        statuses=distinct_machinery_values(Machinery.status),
        manufacturers=distinct_machinery_values(Machinery.manufacturer),
    )


@app.route("/machinery/<machine_id>")
def machinery_detail(machine_id: str):
    """Public machine detail — business fields only, no internal columns."""
    machine = db.session.scalar(
        select(Machinery).where(Machinery.machine_id == machine_id))
    if machine is None:
        abort(404)
    return render_template("machinery_detail.html", machine=machine)


@app.route("/ai-analysis", methods=["GET", "POST"])
def ai_analysis():
    if request.method == "POST":
        return handle_analysis_request()
    return render_template("ai_analysis.html")


@app.route("/reports")
def reports():
    return render_template("reports.html")


@app.route("/api/status")
def status():
    return jsonify({"message": "Flask is running", "ok": True})


# --------------------------------------------------------------------------
# Error pages (styled to match the rest of the application)
# --------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "error": "Not found."}), 404
    return render_template("error.html", code=404,
                           message="That page could not be found."), 404


@app.errorhandler(403)
def forbidden(_error):
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "error": "Forbidden."}), 403
    return render_template("error.html", code=403,
                           message="You do not have permission to view this page."), 403


if __name__ == "__main__":
    app.run(debug=True, port=5001, host='0.0.0.0')
