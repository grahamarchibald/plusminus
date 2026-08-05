"""§2 — training math and scope resolution (pure logic over the store)."""

from __future__ import annotations


def _add(db, day, **kw):
    entry = {"day": day, "type": kw.get("type", "run"),
             "intensity": kw.get("intensity", "moderate"),
             "duration_min": kw.get("duration_min", 60)}
    if "est_burn" in kw:
        entry["est_burn"] = kw["est_burn"]
    db.insert_training(entry)


def test_met_burn_scales_with_duration_and_intensity(tmp_db):
    db = tmp_db
    easy = db.met_burn("run", "easy", 60, weight_kg=70)
    hard = db.met_burn("run", "hard", 60, weight_kg=70)
    half = db.met_burn("run", "easy", 30, weight_kg=70)
    assert hard > easy > 0
    assert round(half, 0) == round(easy / 2, 0)  # linear in duration


def test_met_burn_unknown_type_uses_mixed(tmp_db):
    db = tmp_db
    assert db.met_burn("underwater-basketweaving", "moderate", 60, weight_kg=70) == \
        db.met_burn("mixed", "moderate", 60, weight_kg=70)


def test_day_summary_dominant_type_by_burn(tmp_db):
    db = tmp_db
    day = "2026-08-01"
    _add(db, day, type="walk", intensity="easy", est_burn=50)
    _add(db, day, type="run", intensity="hard", est_burn=600)
    summary = db.day_training_summary(day)
    assert summary["type"] == "run"  # highest-burn stimulus wins
    assert summary["intensity"] == "hard"  # hardest intensity across sessions
    assert summary["sessions"] == 2


def test_day_summary_multiple_stimulus_types_is_mixed(tmp_db):
    db = tmp_db
    day = "2026-08-02"
    _add(db, day, type="run", est_burn=300)
    _add(db, day, type="climb", est_burn=300)
    assert db.day_training_summary(day)["type"] == "mixed"


def test_day_summary_no_sessions_is_rest(tmp_db):
    assert tmp_db.day_training_summary("2026-08-03")["type"] == "rest"


def test_resolve_target_scope_mapping(tmp_db):
    db = tmp_db
    rest_day, train_day, max_day = "2026-08-04", "2026-08-05", "2026-08-06"
    assert db.resolve_target_for_day(rest_day)["scope"] == "default"  # no sessions
    _add(db, train_day, type="lift", intensity="moderate")
    assert db.resolve_target_for_day(train_day)["scope"] == "training"
    _add(db, max_day, type="run", intensity="max")
    assert db.resolve_target_for_day(max_day)["scope"] == "high_output"


def test_resolve_target_walk_only_is_rest_scope(tmp_db):
    db = tmp_db
    day = "2026-08-07"
    _add(db, day, type="walk", intensity="easy")
    assert db.resolve_target_for_day(day)["scope"] == "rest"


def test_passing_sessions_matches_fetching(tmp_db):
    db = tmp_db
    day = "2026-08-08"
    _add(db, day, type="climb", intensity="max", est_burn=400)
    sessions = db.list_training(day)
    assert db.day_training_summary(day, sessions=sessions) == \
        db.day_training_summary(day)
    assert db.resolve_target_for_day(day, sessions=sessions) == \
        db.resolve_target_for_day(day)
