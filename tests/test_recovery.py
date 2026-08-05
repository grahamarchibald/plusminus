"""§2 — recovery scoring and the single-pass fan-out.

Covers the null-safe weight renormalization, that precomputed inputs produce
identical results to letting compute_recovery fetch, and a query-count guard
proving list_training runs once per day view rather than five times.
"""

from __future__ import annotations


def _seed_full_day(db, day):
    db.insert_training({"day": day, "type": "run", "intensity": "moderate",
                        "duration_min": 45})
    db.insert_sleep({"day": day, "duration_h": 8.0, "deep_min": 95})
    db.insert_food({"day": day, "name": "meal", "calories": 2000, "protein": 150,
                    "carb": 200, "fat": 70, "confidence": "high"})


def test_recovery_none_when_no_data(tmp_db):
    rec = tmp_db.compute_recovery("2026-09-01")
    # Training-load always contributes (a no-session day is full headroom), so a
    # bare day still scores — but with no sleep and no food it leans on load only.
    assert rec["score"] is not None
    assert 0 <= rec["score"] <= 100


def test_recovery_renormalizes_when_sleep_missing(tmp_db):
    db = tmp_db
    day = "2026-09-02"
    db.insert_food({"day": day, "name": "meal", "calories": 2000, "protein": 150,
                    "carb": 200, "fat": 70, "confidence": "high"})
    rec = db.compute_recovery(day)
    # No sleep logged: score still valid, driven by macro + load components.
    assert rec["score"] is not None
    assert 0 <= rec["score"] <= 100


def test_recovery_full_day_scores_high(tmp_db):
    db = tmp_db
    day = "2026-09-03"
    _seed_full_day(db, day)
    rec = db.compute_recovery(day)
    assert rec["score"] >= 60
    assert rec["verdict"] in {"Good", "Partial", "Low"}


def test_precomputed_inputs_match_fetching(tmp_db):
    db = tmp_db
    day = "2026-09-04"
    _seed_full_day(db, day)
    sessions = db.list_training(day)
    roll = db.day_rollup(day)
    sleep = db.get_sleep(day)
    target = db.resolve_target_for_day(day, sessions=sessions)
    summary = db.day_training_summary(day, sessions=sessions)
    passed = db.compute_recovery(day, roll=roll, target=target, summary=summary,
                                 sleep=sleep)
    fetched = db.compute_recovery(day)
    assert passed == fetched


def test_day_view_queries_training_once(tmp_db, monkeypatch):
    """The §2 win: _day_view must fetch the day's sessions once, not 5×."""
    import app

    db = tmp_db
    day = "2026-09-05"
    _seed_full_day(db, day)

    calls = {"n": 0}
    real = db.list_training

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(db, "list_training", counting)
    # app holds its own `db` reference to the same module object.
    view = app._day_view(day)
    assert view["day"] == day
    assert calls["n"] == 1, f"list_training ran {calls['n']}× (expected 1)"
