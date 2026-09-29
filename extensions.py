"""Shared Flask extension objects.

Extensions are created in their own module (rather than inside ``main``) so
that ``models`` can import ``db`` without importing ``main`` — which would
otherwise be a circular import.
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
