"""§4 — food deletion (the mislog-fix backend)."""

from __future__ import annotations


def test_delete_food_removes_item_and_returns_true(tmp_db):
    db = tmp_db
    fid = db.insert_food({"name": "oops", "calories": 500, "protein": 10,
                          "carb": 40, "fat": 20})
    assert any(it["id"] == fid for it in db.list_items())
    assert db.delete_food(fid) is True
    assert not any(it["id"] == fid for it in db.list_items())


def test_delete_missing_food_returns_false(tmp_db):
    assert tmp_db.delete_food(999999) is False


def test_delete_food_route_refreshes_day_view(tmp_db):
    import app

    db = tmp_db
    day = "2026-10-01"
    fid = db.insert_food({"day": day, "name": "oops", "calories": 500,
                          "protein": 10, "carb": 40, "fat": 20})
    view = app.delete_food(fid, day=day)
    assert view["day"] == day
    assert not any(it["id"] == fid for it in view["items"])
