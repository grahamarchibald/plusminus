"""Shared pytest fixtures.

Every test that touches the store runs against a fresh tmp database. This works
because `db.get_conn()` resolves `MACRO_DB` at call time via `db._db_path()`, so
pointing the env var here — after import — redirects all subsequent connections.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """A seeded, isolated database for the duration of one test."""
    import db

    dbfile = tmp_path / "test.db"
    monkeypatch.setenv("MACRO_DB", str(dbfile))
    # Force the offline engine so no test ever reaches the network.
    monkeypatch.setenv("MACRO_OFFLINE", "1")
    importlib.reload(db)  # pick up a clean module state
    db.init_db()
    return db
