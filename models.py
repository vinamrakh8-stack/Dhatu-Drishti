"""SQLAlchemy models and the first-run database bootstrap.

Everything is expressed with portable SQLAlchemy types and ORM queries so the
store can move from SQLite to PostgreSQL purely by changing ``DATABASE_URL`` —
no model edits and no dialect-specific SQL.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import desc, func, or_, select
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db

log = logging.getLogger(__name__)

# The only statuses the application accepts for a machine.
MACHINE_STATUSES = ("Operational", "Maintenance", "Inactive", "Retired")

# Rows shown per page on the public machinery list.
MACHINERY_PER_PAGE = 12
# Rows shown per page in the admin machine table and report history.
ADMIN_PER_PAGE = 10


class User(db.Model):
    """A single account table; ``is_admin`` gates the whole admin panel."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def set_password(self, raw_password: str) -> None:
        """Hash with PBKDF2/scrypt — the plaintext is never stored."""
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        if not raw_password:
            return False
        try:
            return check_password_hash(self.password_hash, raw_password)
        except (ValueError, TypeError):
            return False

    @property
    def display_name(self) -> str:
        return self.name or self.email


class Machinery(db.Model):
    """A registered piece of mining equipment."""

    __tablename__ = "machinery"

    id = db.Column(db.Integer, primary_key=True)

    machine_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)

    machine_type = db.Column(db.String(100), nullable=True)
    manufacturer = db.Column(db.String(200), nullable=True)
    model = db.Column(db.String(200), nullable=True)
    mine_name = db.Column(db.String(200), nullable=True, index=True)

    status = db.Column(db.String(50), nullable=False, default="Operational",
                       server_default="Operational", index=True)

    year = db.Column(db.Integer, nullable=True)
    capacity = db.Column(db.String(100), nullable=True)
    description = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    @property
    def status_tone(self) -> str:
        """CSS tone used for the status badge across public and admin views."""
        return {
            "Operational": "ok",
            "Maintenance": "warn",
            "Inactive": "danger",
            "Retired": "muted",
        }.get(self.status, "muted")

    @property
    def public_url(self) -> str:
        return f"/machinery/{self.machine_id}"


class AIReport(db.Model):
    """A persisted, validated AI Analysis output.

    Only successfully generated and schema-validated reports are written here;
    failed runs never create a row.
    """

    __tablename__ = "ai_reports"

    id = db.Column(db.Integer, primary_key=True)

    mine_name = db.Column(db.String(200), nullable=False, index=True)
    risk_level = db.Column(db.String(50), nullable=True)

    # Plain-text rendering kept for quick display and full-text searching.
    prediction = db.Column(db.Text, nullable=True)

    # Structured Gemini JSON plus the context needed to re-render the report.
    report_data = db.Column(db.JSON, nullable=True)

    # Human-readable rendering used as a fallback and for downloads.
    report_text = db.Column(db.Text, nullable=True)

    model_name = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    @property
    def risk_tone(self) -> str:
        return {"HIGH": "danger", "MEDIUM": "warn", "LOW": "ok"}.get(
            (self.risk_level or "").upper(), "muted")

    @property
    def structured(self) -> dict:
        data = self.report_data
        return data if isinstance(data, dict) else {}

    @property
    def report(self) -> dict:
        data = self.structured.get("report")
        return data if isinstance(data, dict) else {}

    @property
    def generated_at(self) -> str:
        return (self.structured.get("generated_at")
                or (self.created_at.isoformat() if self.created_at else ""))


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def machinery_query(args):
    """Build the filtered, ordered query for a machinery listing.

    ``args`` is a Werkzeug request-style MultiDict.  Every filter is optional
    and they compose, so ``?search=cat&type=Excavator&mine=Ukwa`` narrows all
    three at once.  Only portable ORM constructs are used.

    Returns ``(statement, applied_filters)``.
    """
    statement = select(Machinery)
    applied: dict[str, str] = {}

    search = (args.get("search") or "").strip()
    if search:
        needle = f"%{search.lower()}%"
        columns = (Machinery.name, Machinery.machine_id, Machinery.machine_type,
                   Machinery.manufacturer, Machinery.model, Machinery.mine_name,
                   Machinery.status, Machinery.description)
        statement = statement.where(
            or_(*[func.lower(column).like(needle) for column in columns])
        )
        applied["search"] = search

    for key, column in (("type", Machinery.machine_type),
                        ("mine", Machinery.mine_name),
                        ("status", Machinery.status),
                        ("manufacturer", Machinery.manufacturer)):
        value = (args.get(key) or "").strip()
        if value:
            statement = statement.where(column == value)
            applied[key] = value

    statement = statement.order_by(Machinery.name.asc())
    return statement, applied


def distinct_machinery_values(column) -> list[str]:
    """Sorted, non-empty distinct values used to populate filter dropdowns."""
    rows = db.session.scalars(
        select(column).where(column.is_not(None))
        .where(column != "")
        .distinct().order_by(column)
    ).all()
    return [str(value) for value in rows]


def machinery_counts() -> dict[str, int]:
    """Status totals for the admin dashboard cards."""
    counts = {status: 0 for status in MACHINE_STATUSES}
    rows = db.session.execute(
        select(Machinery.status, func.count(Machinery.id)).group_by(Machinery.status)
    ).all()
    for status, count in rows:
        counts[status or "Operational"] = count
    counts["total"] = sum(counts.values())
    return counts


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def _seed_admin() -> None:
    """Create the first admin account only when no users exist at all."""
    from seed_data import admin_email, admin_name, admin_password, using_default_password

    # Never touch an account that already exists.
    if db.session.scalar(select(func.count(User.id))) or 0:
        return

    user = User(email=admin_email(), name=admin_name(), is_admin=True)
    user.set_password(admin_password())
    db.session.add(user)
    db.session.flush()

    if using_default_password():
        log.warning(
            "Created the initial admin account %s with the built-in password. "
            "Set ADMIN_EMAIL and ADMIN_PASSWORD in .env before exposing this "
            "server to anyone else.", admin_email(),
        )
    else:
        log.info("Created the initial admin account %s.", admin_email())


def _seed_machinery() -> None:
    from seed_data import SEED_MACHINERY

    if db.session.scalar(select(func.count(Machinery.id))) or 0:
        return
    for row in SEED_MACHINERY:
        db.session.add(Machinery(**row))
    db.session.flush()
    log.info("Seeded %d machinery records.", len(SEED_MACHINERY))


def init_db(app) -> None:
    """Attach the database, create missing tables and seed first-run data.

    ``db.create_all()`` only creates tables that do not exist yet: it never
    drops, truncates or otherwise disturbs existing application data.
    """
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    db.init_app(app)

    with app.app_context():
        db.create_all()
        try:
            _seed_admin()
            _seed_machinery()
            db.session.commit()
        except Exception:  # noqa: BLE001 - seeding must never block startup
            db.session.rollback()
            log.exception("database seeding failed")

    log.info("database ready (%s)", uri or "default")
