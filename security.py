"""Session authentication, CSRF protection and the reusable admin guard.

Authorisation is enforced here on the server for every admin route — hiding
buttons in the template is never treated as access control.
"""

from __future__ import annotations

import functools
import hmac
import secrets
from typing import Callable

from flask import abort, redirect, request, session, url_for

from extensions import db
from models import User

_CSRF_KEY = "_csrf_token"


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------

def current_user() -> User | None:
    """Return the signed-in user, or ``None`` for anonymous requests."""
    user_id = session.get("uid")
    if not isinstance(user_id, int):
        return None
    return db.session.get(User, user_id)


def login_user(user: User) -> None:
    """Establish a fresh session for ``user`` (regenerates the session id)."""
    session.clear()
    session["uid"] = user.id
    session.permanent = True
    generate_csrf()


def logout_user() -> None:
    session.clear()


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

def generate_csrf() -> str:
    """Return the per-session token, creating it on first use."""
    token = session.get(_CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_CSRF_KEY] = token
    return token


def csrf_token() -> str:
    """Template-facing alias for :func:`generate_csrf`."""
    return generate_csrf()


def validate_csrf() -> bool:
    expected = session.get(_CSRF_KEY) or ""
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    if not expected or not supplied:
        return False
    return hmac.compare_digest(expected, supplied)


def csrf_protect() -> None:
    """Abort the request when a state-changing POST has a bad/missing token."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not validate_csrf():
        abort(400, description="Invalid or missing security token. Please retry.")


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------

def safe_next_url(default: str = "admin.dashboard") -> str:
    """Only ever redirect to a local path — never an external origin."""
    target = request.values.get("next") or ""
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for(default)


def admin_required(view: Callable) -> Callable:
    """Guard an admin view.

    * anonymous      -> redirect to ``/admin/login``
    * signed in, not an admin -> ``403 Forbidden``
    """
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            next_url = request.full_path.rstrip("?") if request.query_string else request.path
            return redirect(url_for("admin.login", next=next_url))
        if not user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped
