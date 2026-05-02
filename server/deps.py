"""Shared dependencies to avoid circular imports."""

from __future__ import annotations

from server.database import Database

_db: Database | None = None


def set_db(db: Database | None) -> None:
    global _db
    _db = db


def get_db() -> Database:
    assert _db is not None, "Database not initialized"
    return _db
