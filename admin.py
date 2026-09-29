"""Admin panel: login, machinery CRUD and AI report history.

Every route in this module is server-side authorised with ``@admin_required``
and every state-changing request goes through CSRF validation.  Nothing here
is protected merely by hiding markup.
"""

from __future__ import annotations

import logging
import re

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from sqlalchemy import delete as sql_delete
from sqlalchemy import desc, func, select

from extensions import db
from models import (ADMIN_PER_PAGE, MACHINE_STATUSES, AIReport, Machinery,
                    User, distinct_machinery_values, machinery_counts,
                    machinery_query)
from security import (admin_required, csrf_protect, current_user,
                      login_user, logout_user, safe_next_url)

log = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__)

_MACHINE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
_MAX = {
    "machine_id": 100, "name": 200, "machine_type": 100,
    "manufacturer": 200, "model": 200, "mine_name": 200,
    "capacity": 100, "description": 4000,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _page(param: str) -> int:
    try:
        value = int(request.args.get(param) or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, value)


def _page_links(param: str, page: int, total_pages: int, endpoint: str) -> list[dict]:
    """Prev / numbered / next links that preserve every other filter."""
    if total_pages <= 1:
        return []

    def href(target: int) -> str:
        args = {key: value for key, value in request.args.items() if key != param}
        if target > 1:
            args[param] = str(target)
        return url_for(endpoint, **args)

    window = list(range(max(1, page - 2), min(total_pages, page + 2) + 1))
    links = [{"label": "‹", "url": href(page - 1), "active": False,
              "disabled": page <= 1}]
    if window[0] > 1:
        links.append({"label": "1", "url": href(1), "active": page == 1, "disabled": False})
        if window[0] > 2:
            links.append({"label": "…", "url": "#", "active": False, "disabled": True})
    for target in window:
        links.append({"label": str(target), "url": href(target),
                      "active": target == page, "disabled": False})
    if window[-1] < total_pages:
        if window[-1] < total_pages - 1:
            links.append({"label": "…", "url": "#", "active": False, "disabled": True})
        links.append({"label": str(total_pages), "url": href(total_pages),
                      "active": total_pages == page, "disabled": False})
    links.append({"label": "›", "url": href(page + 1), "active": False,
                  "disabled": page >= total_pages})
    return links


def _paginate(total: int, per_page: int, page: int, param: str, endpoint: str) -> dict:
    total_pages = max(1, -(-total // per_page)) if per_page else 1
    page = min(page, total_pages)
    return {
        "total": total,
        "page": page,
        "pages": total_pages,
        "per_page": per_page,
        "offset": (page - 1) * per_page,
        "links": _page_links(param, page, total_pages, endpoint),
        "param": param,
    }


def _parse_year(raw: str, errors: list[str]) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        year = int(raw)
    except (TypeError, ValueError):
        errors.append("Year must be a whole number.")
        return None
    if not 1900 <= year <= 2100:
        errors.append("Year must be between 1900 and 2100.")
        return None
    return year


def _read_machine_form(form) -> tuple[dict, list[str]]:
    """Validate an add/edit submission.  Returns ``(values, errors)``."""
    errors: list[str] = []
    values: dict = {}

    for field in ("machine_id", "name", "machine_type", "manufacturer",
                  "model", "mine_name", "capacity", "description"):
        text = (form.get(field) or "").strip()
        values[field] = text[:_MAX[field]]

    status = (form.get("status") or "").strip()
    values["status"] = status
    values["year"] = _parse_year(form.get("year") or "", errors)

    if not values["machine_id"]:
        errors.append("Machine ID is required.")
    elif not _MACHINE_ID_RE.match(values["machine_id"]):
        errors.append("Machine ID may use letters, numbers, dot, dash, slash and underscore.")
    if not values["name"]:
        errors.append("Machine name is required.")
    if status not in MACHINE_STATUSES:
        errors.append("Please choose a valid status.")
    return values, errors


def _machine_id_taken(machine_id: str, exclude_id: int | None = None) -> bool:
    statement = select(Machinery.id).where(
        func.lower(Machinery.machine_id) == machine_id.strip().lower())
    if exclude_id is not None:
        statement = statement.where(Machinery.id != exclude_id)
    return db.session.scalar(statement) is not None


def _apply(machine: Machinery, values: dict) -> None:
    """Copy validated form values onto a model instance.

    Optional text fields become ``None`` (not ``""``) so that "not recorded"
    and "empty" stay distinguishable and the filter dropdowns stay clean.
    """
    for key, value in values.items():
        if key not in ("machine_id", "name", "status"):
            value = value or None
        setattr(machine, key, value)


def _filter_options() -> dict[str, list[str]]:
    return {
        "types": distinct_machinery_values(Machinery.machine_type),
        "mines": distinct_machinery_values(Machinery.mine_name),
        "statuses": distinct_machinery_values(Machinery.status),
        "manufacturers": distinct_machinery_values(Machinery.manufacturer),
    }


def _catalog_mines() -> list[str]:
    """Mine names from the shared catalogue, so new machines line up with
    the AI Analysis and Weather pages."""
    try:
        from ai_analysis.registry import CATALOG
    except Exception:  # noqa: BLE001 - catalogue is optional
        return []
    names: list[str] = []
    for districts in CATALOG.values():
        for mine_list in districts.values():
            names.extend(mine_list)
    return names


def _form_options() -> dict[str, list[str]]:
    return {
        "types": sorted({*distinct_machinery_values(Machinery.machine_type),
                         "Excavator", "Dump Truck", "Wheel Loader", "Drill Rig",
                         "Dozer", "Crusher", "Conveyor", "Grader", "Shovel"}),
        "mines": sorted({*distinct_machinery_values(Machinery.mine_name),
                         *_catalog_mines()}),
    }


def _machine_or_404(machine_pk: int) -> Machinery:
    machine = db.session.get(Machinery, machine_pk)
    if machine is None:
        abort(404)
    return machine


def _report_or_404(report_id: int) -> AIReport:
    report = db.session.get(AIReport, report_id)
    if report is None:
        abort(404)
    return report


# ---------------------------------------------------------------------------
# Errors (rendered in the application's own visual language)
# ---------------------------------------------------------------------------

@admin_bp.errorhandler(400)
@admin_bp.errorhandler(403)
@admin_bp.errorhandler(404)
def _admin_error(error):
    status = getattr(error, "code", 400)
    return render_template("admin/error.html", code=status,
                           message=getattr(error, "description", None)
                           or "Request could not be completed."), status


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/login", methods=["GET", "POST"])
def login():
    if current_user() is not None and current_user().is_admin:
        return redirect(url_for("admin.dashboard"))

    error = None
    status = 200
    email = ""

    if request.method == "POST":
        csrf_protect()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        if not email or not password:
            error = "Email and password are required."
            status = 400
        else:
            user = db.session.scalar(
                select(User).where(func.lower(User.email) == email))
            if user is None or not user.check_password(password):
                # Never reveal which of the two fields was wrong.
                error = "Invalid email or password."
                status = 401
            elif not user.is_admin:
                error = "This account does not have administrator access."
                status = 403
            else:
                login_user(user)
                flash("Signed in successfully.", "success")
                return redirect(safe_next_url())

    return render_template("admin/login.html", error=error, email=email), status


@admin_bp.route("/admin/logout", methods=["POST"])
def logout():
    csrf_protect()
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("admin.login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@admin_bp.route("/admin")
@admin_required
def dashboard():
    machine_statement, applied = machinery_query(request.args)
    machine_total = db.session.scalar(
        select(func.count()).select_from(machine_statement.subquery())) or 0
    machine_page = _paginate(machine_total, ADMIN_PER_PAGE, _page("page"),
                              "page", "admin.dashboard")
    machines = db.session.scalars(
        machine_statement.limit(machine_page["per_page"])
                         .offset(machine_page["offset"])).all()

    report_total = db.session.scalar(select(func.count(AIReport.id))) or 0
    report_page = _paginate(report_total, ADMIN_PER_PAGE, _page("rpage"),
                             "rpage", "admin.dashboard")
    reports = db.session.scalars(
        select(AIReport).order_by(desc(AIReport.created_at), desc(AIReport.id))
        .limit(report_page["per_page"]).offset(report_page["offset"])).all()

    return render_template(
        "admin/dashboard.html",
        counts=machinery_counts(),
        statuses=MACHINE_STATUSES,
        options=_filter_options(),
        applied=applied,
        machines=machines,
        machine_pager=machine_page,
        reports=reports,
        report_pager=report_page,
        user=current_user(),
    )


# ---------------------------------------------------------------------------
# Machinery CRUD
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/machinery/new", methods=["GET", "POST"])
@admin_required
def machine_new():
    values = {field: "" for field in _MAX}
    values["status"] = "Operational"
    errors: list[str] = []

    if request.method == "POST":
        csrf_protect()
        values, errors = _read_machine_form(request.form)
        if not errors and _machine_id_taken(values["machine_id"]):
            errors.append(f"Machine ID “{values['machine_id']}” is already in use.")

        if errors:
            flash("Please correct the highlighted fields.", "error")
        else:
            machine = Machinery()
            _apply(machine, values)
            db.session.add(machine)
            db.session.commit()
            flash("Machine added successfully.", "success")
            return redirect(url_for("admin.dashboard", _anchor="machinery"))

    return render_template("admin/machine_form.html", mode="new", machine=None,
                           values=values, errors=errors, statuses=MACHINE_STATUSES,
                           options=_form_options(), user=current_user())


@admin_bp.route("/admin/machinery/<int:machine_pk>/edit", methods=["GET", "POST"])
@admin_required
def machine_edit(machine_pk: int):
    machine = _machine_or_404(machine_pk)
    errors: list[str] = []

    if request.method == "POST":
        csrf_protect()
        values, errors = _read_machine_form(request.form)
        if not errors and _machine_id_taken(values["machine_id"], exclude_id=machine.id):
            errors.append(f"Machine ID “{values['machine_id']}” is already in use.")

        if errors:
            flash("Please correct the highlighted fields.", "error")
        else:
            _apply(machine, values)
            db.session.commit()
            flash("Machine updated successfully.", "success")
            return redirect(url_for("admin.dashboard", _anchor="machinery"))
        form_values = values
    else:
        form_values = {
            "machine_id": machine.machine_id,
            "name": machine.name,
            "machine_type": machine.machine_type or "",
            "manufacturer": machine.manufacturer or "",
            "model": machine.model or "",
            "mine_name": machine.mine_name or "",
            "status": machine.status,
            "year": machine.year if machine.year is not None else "",
            "capacity": machine.capacity or "",
            "description": machine.description or "",
        }

    return render_template("admin/machine_form.html", mode="edit", machine=machine,
                           values=form_values, errors=errors,
                           statuses=MACHINE_STATUSES, options=_form_options(),
                           user=current_user())


@admin_bp.route("/admin/machinery/<int:machine_pk>/delete", methods=["POST"])
@admin_required
def machine_delete(machine_pk: int):
    csrf_protect()
    machine = db.session.get(Machinery, machine_pk)
    if machine is None:
        flash("That machine no longer exists.", "error")
        return redirect(url_for("admin.dashboard", _anchor="machinery"))
    name = machine.name
    db.session.delete(machine)
    db.session.commit()
    flash(f"Machine “{name}” deleted successfully.", "success")
    return redirect(url_for("admin.dashboard", _anchor="machinery"))


@admin_bp.route("/admin/machinery/<int:machine_pk>")
@admin_required
def machine_detail(machine_pk: int):
    return render_template("admin/machine_detail.html",
                           machine=_machine_or_404(machine_pk),
                           user=current_user())


# ---------------------------------------------------------------------------
# AI report history
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/reports/<int:report_id>")
@admin_required
def report_detail(report_id: int):
    return render_template("admin/report_detail.html",
                           record=_report_or_404(report_id),
                           user=current_user())


@admin_bp.route("/admin/reports/<int:report_id>/download/<kind>")
@admin_required
def report_download(report_id: int, kind: str):
    """Regenerate the document from the stored JSON — no file is persisted."""
    from io import BytesIO

    from flask import send_file

    from ai_analysis.report_docs import build_docx, build_pdf, safe_filename

    record = _report_or_404(report_id)
    report = record.report
    if not report:
        abort(400, description="This record has no structured report to download.")

    # Pick the renderer *before* the try block, so an unsupported kind still
    # produces a 404 instead of being swallowed as an internal error.
    if kind == "docx":
        render = build_docx
        mimetype = ("application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document")
    elif kind == "pdf":
        render = build_pdf
        mimetype = "application/pdf"
    else:
        abort(404)

    mine = record.structured.get("mine") or record.mine_name
    generated = record.generated_at
    try:
        blob = render(report, mine, generated)
    except Exception:  # noqa: BLE001 - never surface a stack trace
        log.exception("historical report render failed id=%s kind=%s", report_id, kind)
        abort(500, description="The document could not be generated. Please retry.")

    return send_file(BytesIO(blob), mimetype=mimetype, as_attachment=True,
                     download_name=safe_filename(mine, kind))


@admin_bp.route("/admin/reports/<int:report_id>/delete", methods=["POST"])
@admin_required
def report_delete(report_id: int):
    csrf_protect()
    record = db.session.get(AIReport, report_id)
    if record is None:
        flash("That report no longer exists.", "error")
        return redirect(url_for("admin.dashboard", _anchor="reports"))
    db.session.delete(record)
    db.session.commit()
    flash("Report deleted successfully.", "success")
    return redirect(url_for("admin.dashboard", _anchor="reports"))


@admin_bp.route("/admin/reports/clear", methods=["POST"])
@admin_required
def report_clear():
    csrf_protect()
    deleted = db.session.execute(sql_delete(AIReport)).rowcount or 0
    db.session.commit()
    flash(f"Report history cleared — {deleted} report(s) permanently deleted.",
          "success")
    return redirect(url_for("admin.dashboard", _anchor="reports"))
