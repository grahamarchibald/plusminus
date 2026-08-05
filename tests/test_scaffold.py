"""Smoke test: the tmp_db fixture isolates the store and seeds it."""

from __future__ import annotations


def test_tmp_db_is_isolated_and_seeded(tmp_db, tmp_path):
    db = tmp_db
    # The path helper resolves to the tmp file, not the real macro.db.
    assert db._db_path() == str(tmp_path / "test.db")
    # init_db seeded the default target scope.
    assert db.get_active_target("default") is not None
    # A fresh insert round-trips.
    db.insert_food(
        {"name": "test egg", "calories": 72, "protein": 6, "carb": 0, "fat": 5}
    )
    items = db.list_items()
    assert any(it["name"] == "test egg" for it in items)
