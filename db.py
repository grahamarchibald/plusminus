"""SQLite store for PlusMinus.

The estimation engine is stateless; all state lives here. The MVP writes to
`foods` and `targets`; the rest of the schema (SPEC.md §3) is created up front so
later phases have it. Thin, parameterized helpers only — no ORM.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

DB_PATH = os.environ.get("MACRO_DB", "macro.db")

SCHEMA = """
-- Individual logged food entries
CREATE TABLE IF NOT EXISTS foods (
  id            INTEGER PRIMARY KEY,
  logged_at     TEXT NOT NULL,           -- ISO timestamp
  day           TEXT NOT NULL,           -- YYYY-MM-DD (local), for daily rollups
  name          TEXT NOT NULL,
  calories      REAL, protein REAL, carb REAL, fat REAL,
  source        TEXT,                    -- described | photo | saved | restaurant | manual
  confidence    TEXT,                    -- high | medium | low
  uncertainty_cal REAL,
  assumptions   TEXT,                    -- JSON array
  saved_meal_id INTEGER REFERENCES saved_meals(id)
);
CREATE INDEX IF NOT EXISTS idx_foods_day ON foods(day);

-- Named, reusable meals ("the loaf", "breakfast")
CREATE TABLE IF NOT EXISTS saved_meals (
  id          INTEGER PRIMARY KEY,
  name        TEXT UNIQUE NOT NULL,
  components  TEXT NOT NULL,             -- JSON: [{name, qty, unit}]
  calories    REAL, protein REAL, carb REAL, fat REAL,
  notes       TEXT,
  created_at  TEXT
);

-- Versioned targets — history matters for adaptive targeting
CREATE TABLE IF NOT EXISTS targets (
  id          INTEGER PRIMARY KEY,
  effective_from TEXT NOT NULL,          -- ISO date
  calories    REAL, protein REAL, carb REAL, fat REAL,
  rationale   TEXT,                      -- why this target was set
  scope       TEXT DEFAULT 'default'     -- default | rest | training | high_output
);

-- Body weight, for trend-vs-noise reads
CREATE TABLE IF NOT EXISTS weigh_ins (
  id        INTEGER PRIMARY KEY,
  day       TEXT NOT NULL,
  weight    REAL NOT NULL,
  unit      TEXT DEFAULT 'lb'
);

-- Activity, to drive activity-scaled targets
CREATE TABLE IF NOT EXISTS activity (
  id          INTEGER PRIMARY KEY,
  day         TEXT NOT NULL,
  type        TEXT,                      -- rest | lift | climb | run | walk | dance | mixed
  detail      TEXT,                      -- "15km, 300m gain" / "45k steps"
  est_burn    REAL                       -- kcal, rough
);

-- Generic experiment framework (sleep protocol lives here)
CREATE TABLE IF NOT EXISTS experiments (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  hypothesis  TEXT,
  conditions  TEXT,                      -- JSON: e.g. ["Early","Normal"]
  started_at  TEXT
);
CREATE TABLE IF NOT EXISTS experiment_nights (
  id            INTEGER PRIMARY KEY,
  experiment_id INTEGER REFERENCES experiments(id),
  day           TEXT NOT NULL,
  condition     TEXT,
  metrics       TEXT,                    -- JSON: {onset_min, total_h, deep_min, rem_min, wakeups, rating}
  confounders   TEXT                     -- JSON: {caffeine, thc, alcohol, training_load}
);

-- v2: training sessions (multiple per day allowed — no UNIQUE(day))
CREATE TABLE IF NOT EXISTS training (
  id          INTEGER PRIMARY KEY,
  day         TEXT NOT NULL,             -- YYYY-MM-DD
  type        TEXT NOT NULL,             -- climb | run | lift | dance | walk | mixed | rest
  duration_min INTEGER,
  intensity   TEXT,                      -- easy | moderate | hard | max
  detail      TEXT,
  est_burn    REAL,                      -- estimated kcal (MET formula or manual override)
  subjective_difficulty INTEGER,        -- 1-10
  notes       TEXT,
  created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_training_day ON training(day);

-- v2: sleep (manual rating for MVP; Fitbit import later)
CREATE TABLE IF NOT EXISTS sleep (
  id          INTEGER PRIMARY KEY,
  day         TEXT NOT NULL UNIQUE,      -- YYYY-MM-DD
  duration_h  REAL,
  deep_min    INTEGER,
  rem_min     INTEGER,
  wakeups     INTEGER,
  fitbit_score INTEGER,
  manual_rating INTEGER,                 -- 1-10 subjective
  notes       TEXT,
  source      TEXT DEFAULT 'manual',     -- fitbit | manual | both
  created_at  TEXT
);

-- v2: recovery (schema for completeness; MVP computes on read)
CREATE TABLE IF NOT EXISTS recovery (
  id          INTEGER PRIMARY KEY,
  day         TEXT NOT NULL UNIQUE,      -- YYYY-MM-DD
  sleep_quality_score REAL,
  macro_adequacy_pct REAL,
  training_load REAL,
  readiness_score REAL,
  notes       TEXT,
  computed_at TEXT
);

-- v2: customizable habit tracker (columns) with per-day checkboxes (rows)
CREATE TABLE IF NOT EXISTS habits (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  sort_order  INTEGER DEFAULT 0,
  active      INTEGER DEFAULT 1,
  created_at  TEXT
);
CREATE TABLE IF NOT EXISTS habit_entries (
  id          INTEGER PRIMARY KEY,
  habit_id    INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
  day         TEXT NOT NULL,
  done        INTEGER DEFAULT 0,
  UNIQUE(habit_id, day)
);
CREATE INDEX IF NOT EXISTS idx_habit_entries_day ON habit_entries(day);

-- v2: training programs / macrocycles (grid generated from spec)
CREATE TABLE IF NOT EXISTS programs (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  sport        TEXT NOT NULL,             -- run | climb | other
  num_weeks    INTEGER NOT NULL,
  start_monday TEXT NOT NULL,             -- YYYY-MM-DD (Monday of week 1)
  spec         TEXT NOT NULL,             -- JSON: {template, progression, overrides}
  active       INTEGER DEFAULT 1,
  created_at   TEXT
);
"""

DEFAULT_HABITS = ["20,000 steps", "Read", "Movement", "Time with a friend"]

# weekday index: 0=Mon ... 6=Sun
_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Activity-scaled target scopes (protein held constant; calories/carbs scale with output).
# resolve_target_for_day() maps a day's training to one of these scopes.
SCOPE_TARGETS = {
    "default": {"calories": 2350, "protein": 180, "carb": 215, "fat": 75},
    "rest": {"calories": 2350, "protein": 180, "carb": 215, "fat": 75},
    "training": {"calories": 2650, "protein": 180, "carb": 285, "fat": 85},
    "high_output": {"calories": 3000, "protein": 180, "carb": 300, "fat": 100},
}

MACROS = ("calories", "protein", "carb", "fat")

# MET values by training type × intensity (rough, for the burn estimate).
_MET = {
    "climb": {"easy": 5.0, "moderate": 7.0, "hard": 9.0, "max": 11.0},
    "run": {"easy": 7.0, "moderate": 9.0, "hard": 12.0, "max": 14.0},
    "lift": {"easy": 3.0, "moderate": 4.5, "hard": 6.0, "max": 6.0},
    "dance": {"easy": 4.0, "moderate": 6.0, "hard": 7.5, "max": 8.0},
    "walk": {"easy": 2.8, "moderate": 3.5, "hard": 4.5, "max": 5.0},
    "mixed": {"easy": 4.0, "moderate": 6.0, "hard": 8.0, "max": 9.0},
    "rest": {"easy": 0.0, "moderate": 0.0, "hard": 0.0, "max": 0.0},
}
_DEFAULT_BODYWEIGHT_KG = 75.0  # fallback when no weigh-in exists


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create the schema and seed any missing activity-scaled target scope."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        existing = {
            r["scope"]
            for r in conn.execute("SELECT DISTINCT scope FROM targets").fetchall()
        }
        for scope, t in SCOPE_TARGETS.items():
            if scope in existing:
                continue
            conn.execute(
                """INSERT INTO targets (effective_from, calories, protein, carb, fat, rationale, scope)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (_today(), t["calories"], t["protein"], t["carb"], t["fat"],
                 f"Seeded {scope} target", scope),
            )
        # Seed default habits if none exist yet.
        if conn.execute("SELECT COUNT(*) AS n FROM habits").fetchone()["n"] == 0:
            for i, name in enumerate(DEFAULT_HABITS):
                conn.execute(
                    "INSERT INTO habits (name, sort_order, active, created_at) VALUES (?, ?, 1, ?)",
                    (name, i, _now_iso()),
                )


def _today() -> str:
    """Server-local YYYY-MM-DD."""
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# --- foods --------------------------------------------------------------

def insert_food(entry: dict[str, Any]) -> int:
    """Write one confirmed item to `foods`. Returns the new row id.

    Expected keys: name, calories, protein, carb, fat, source, confidence,
    uncertainty_cal, assumptions (list). day/logged_at default to now.
    """
    day = entry.get("day") or _today()
    logged_at = entry.get("logged_at") or _now_iso()
    assumptions = entry.get("assumptions") or []
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO foods
               (logged_at, day, name, calories, protein, carb, fat,
                source, confidence, uncertainty_cal, assumptions)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                logged_at,
                day,
                entry.get("name", "food"),
                _num(entry.get("calories")),
                _num(entry.get("protein")),
                _num(entry.get("carb")),
                _num(entry.get("fat")),
                entry.get("source", "described"),
                entry.get("confidence"),
                _num(entry.get("uncertainty_cal")),
                json.dumps(assumptions),
            ),
        )
        return int(cur.lastrowid)


def list_items(day: str | None = None) -> list[dict[str, Any]]:
    day = day or _today()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, logged_at, name, calories, protein, carb, fat,
                      source, confidence, uncertainty_cal, assumptions
               FROM foods WHERE day = ? ORDER BY id ASC""",
            (day,),
        ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        try:
            d["assumptions"] = json.loads(d["assumptions"]) if d["assumptions"] else []
        except (TypeError, json.JSONDecodeError):
            d["assumptions"] = []
        items.append(d)
    return items


def day_rollup(day: str | None = None) -> dict[str, Any]:
    """Sum macros for a day and aggregate uncertainty + confidence breakdown."""
    items = list_items(day)
    total = {m: 0.0 for m in MACROS}
    # calorie share by confidence band
    by_conf = {"high": 0.0, "medium": 0.0, "low": 0.0}
    # aggregate ± via root-sum-square of per-item uncertainty (uncertainties add in quadrature)
    var = 0.0
    for it in items:
        for m in MACROS:
            total[m] += it.get(m) or 0.0
        band = (it.get("confidence") or "medium").lower()
        if band not in by_conf:
            band = "medium"
        by_conf[band] += it.get("calories") or 0.0
        u = it.get("uncertainty_cal") or 0.0
        var += u * u
    uncertainty_cal = round(var ** 0.5)
    return {
        "day": day or _today(),
        "total": {m: round(total[m], 1) for m in MACROS},
        "uncertainty_cal": uncertainty_cal,
        "confidence_calories": {k: round(v, 1) for k, v in by_conf.items()},
        "item_count": len(items),
    }


# --- targets ------------------------------------------------------------

def get_active_target(scope: str = "default") -> dict[str, Any] | None:
    """Newest target row for the given scope (versioned by effective_from)."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT id, effective_from, calories, protein, carb, fat, rationale, scope
               FROM targets WHERE scope = ?
               ORDER BY effective_from DESC, id DESC LIMIT 1""",
            (scope,),
        ).fetchone()
    return dict(row) if row else None


def set_target(
    calories: float,
    protein: float,
    carb: float,
    fat: float,
    rationale: str = "Manual update",
    scope: str = "default",
) -> dict[str, Any]:
    """Write a new versioned target row (never edits history)."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO targets (effective_from, calories, protein, carb, fat, rationale, scope)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_today(), _num(calories), _num(protein), _num(carb), _num(fat), rationale, scope),
        )
    return get_active_target(scope)  # type: ignore[return-value]


def _num(v: Any) -> float:
    """Coerce a value to float, defaulting to 0.0 for None/blank/garbage."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _iday(v: Any) -> int:
    return int(round(_num(v)))


# --- weigh-ins ----------------------------------------------------------

def insert_weigh_in(day: str, weight: float, unit: str = "lb") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO weigh_ins (day, weight, unit) VALUES (?, ?, ?)",
            (day or _today(), _num(weight), unit or "lb"),
        )
        return int(cur.lastrowid)


def _weight_lb(row: sqlite3.Row) -> float:
    """Normalize a weigh-in to pounds."""
    w = row["weight"]
    return w * 2.2046226 if (row["unit"] or "lb") == "kg" else w


def latest_weight_lb() -> float | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT weight, unit FROM weigh_ins ORDER BY day DESC, id DESC LIMIT 1"
        ).fetchone()
    return _weight_lb(row) if row else None


def latest_bodyweight_kg() -> float:
    """Bodyweight for the MET burn formula; falls back to a default if none logged."""
    lb = latest_weight_lb()
    return (lb / 2.2046226) if lb else _DEFAULT_BODYWEIGHT_KG


def weight_for_day(day: str) -> float | None:
    """The most recent weigh-in on or before `day`, in pounds."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT weight, unit FROM weigh_ins WHERE day <= ? ORDER BY day DESC, id DESC LIMIT 1",
            (day,),
        ).fetchone()
    return round(_weight_lb(row), 1) if row else None


def _weight_on(day: str) -> float | None:
    """Weigh-in recorded *on* this exact day (for the calendar cell), in pounds."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT weight, unit FROM weigh_ins WHERE day = ? ORDER BY id DESC LIMIT 1",
            (day,),
        ).fetchone()
    return round(_weight_lb(row), 1) if row else None


def weight_trend(day: str | None = None) -> dict[str, Any]:
    """Rolling 7-day avg vs. the prior 7-day avg, with a series for the sparkline."""
    day = day or _today()
    end = _date(day)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT day, weight, unit FROM weigh_ins WHERE day <= ? ORDER BY day ASC",
            (day,),
        ).fetchall()
    by_day = {r["day"]: _weight_lb(r) for r in rows}

    def avg(days: list[str]) -> float | None:
        vals = [by_day[d] for d in days if d in by_day]
        return round(sum(vals) / len(vals), 2) if vals else None

    last7 = [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    prev7 = [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7, 14)]
    trend7 = avg(last7)
    prior = avg(prev7)
    series = [round(by_day[d], 1) for d in sorted(by_day)][-14:]
    return {
        "current": round(by_day.get(day), 1) if day in by_day else latest_weight_lb(),
        "trend_7day_avg": trend7,
        "prior_week_avg": prior,
        "weekly_change": round(trend7 - prior, 2) if (trend7 is not None and prior is not None) else None,
        "series": series,
    }


# --- training -----------------------------------------------------------

def met_burn(type_: str, intensity: str, duration_min: float, weight_kg: float | None = None) -> float:
    """MET × bodyweight(kg) × hours. Used only when the user doesn't supply est_burn."""
    weight_kg = weight_kg or latest_bodyweight_kg()
    met = _MET.get((type_ or "mixed").lower(), _MET["mixed"]).get(
        (intensity or "moderate").lower(), 6.0
    )
    hours = _num(duration_min) / 60.0
    return round(met * weight_kg * hours, 0)


def insert_training(entry: dict[str, Any]) -> int:
    """Insert one training session. Computes est_burn via MET if not provided."""
    day = entry.get("day") or _today()
    burn = entry.get("est_burn")
    if burn in (None, "", 0):
        burn = met_burn(entry.get("type", "mixed"), entry.get("intensity", "moderate"),
                        entry.get("duration_min", 0))
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO training
               (day, type, duration_min, intensity, detail, est_burn,
                subjective_difficulty, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                day, entry.get("type", "mixed"), _iday(entry.get("duration_min")),
                entry.get("intensity", "moderate"), entry.get("detail"), _num(burn),
                _iday(entry.get("subjective_difficulty")) or None,
                entry.get("notes"), _now_iso(),
            ),
        )
        return int(cur.lastrowid)


def list_training(day: str | None = None) -> list[dict[str, Any]]:
    day = day or _today()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, day, type, duration_min, intensity, detail, est_burn,
                      subjective_difficulty, notes
               FROM training WHERE day = ? ORDER BY id ASC""",
            (day,),
        ).fetchall()
    return [dict(r) for r in rows]


# Types that count as a "training stimulus" (vs. rest/walk recovery movement).
_TRAINING_TYPES = {"climb", "run", "lift", "dance", "mixed"}


def day_training_summary(day: str) -> dict[str, Any]:
    """Aggregate the day's sessions: dominant type, total burn/load, hardest intensity."""
    sessions = list_training(day)
    if not sessions:
        return {"type": "rest", "label": "Rest", "sessions": 0,
                "total_burn": 0.0, "intensity": None}
    total_burn = round(sum(s.get("est_burn") or 0.0 for s in sessions), 0)
    # dominant type = the session with the most burn; "mixed" if several stimulus types
    stim_types = {s["type"] for s in sessions if s["type"] in _TRAINING_TYPES}
    if len(stim_types) > 1:
        dtype = "mixed"
    else:
        dtype = max(sessions, key=lambda s: s.get("est_burn") or 0.0)["type"]
    order = {"easy": 1, "moderate": 2, "hard": 3, "max": 4}
    hardest = max((s.get("intensity") or "moderate" for s in sessions),
                  key=lambda i: order.get(i, 2))
    return {
        "type": dtype,
        "label": dtype.capitalize(),
        "sessions": len(sessions),
        "total_burn": total_burn,
        "intensity": hardest,
    }


def resolve_target_for_day(day: str) -> dict[str, Any]:
    """Map a day's training to a target scope, then return that scope's active target.

    any max climb/run -> high_output; any stimulus training -> training;
    only rest/walk -> rest; no sessions -> default.
    """
    sessions = list_training(day)
    if not sessions:
        scope = "default"
    elif any((s["type"] in ("climb", "run") and (s.get("intensity") == "max"))
             for s in sessions):
        scope = "high_output"
    elif any(s["type"] in _TRAINING_TYPES for s in sessions):
        scope = "training"
    else:
        scope = "rest"
    target = get_active_target(scope) or get_active_target("default") or {}
    target = dict(target)
    target["scope"] = scope
    return target


# --- sleep --------------------------------------------------------------

def insert_sleep(entry: dict[str, Any]) -> None:
    """Upsert one sleep record for a day (UNIQUE(day))."""
    day = entry.get("day") or _today()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sleep (day, duration_h, deep_min, rem_min, wakeups,
                                  fitbit_score, manual_rating, notes, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(day) DO UPDATE SET
                 duration_h=excluded.duration_h, deep_min=excluded.deep_min,
                 rem_min=excluded.rem_min, wakeups=excluded.wakeups,
                 fitbit_score=excluded.fitbit_score, manual_rating=excluded.manual_rating,
                 notes=excluded.notes, source=excluded.source""",
            (
                day, _numornone(entry.get("duration_h")), _iornone(entry.get("deep_min")),
                _iornone(entry.get("rem_min")), _iornone(entry.get("wakeups")),
                _iornone(entry.get("fitbit_score")), _iornone(entry.get("manual_rating")),
                entry.get("notes"), entry.get("source", "manual"), _now_iso(),
            ),
        )


def get_sleep(day: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT day, duration_h, deep_min, rem_min, wakeups, fitbit_score,
                      manual_rating, notes, source FROM sleep WHERE day = ?""",
            (day,),
        ).fetchone()
    return dict(row) if row else None


# --- recovery (computed on read) ---------------------------------------

def compute_recovery(day: str) -> dict[str, Any]:
    """Composite readiness: sleep 40% + macro-hit 40% + training-load 20%.

    Null-safe: any missing component drops out and the weights renormalize;
    with nothing to go on, returns score=None.
    """
    sleep = get_sleep(day)
    roll = day_rollup(day)
    target = resolve_target_for_day(day)
    summary = day_training_summary(day)

    components: list[tuple[float, float]] = []  # (score, weight)

    # Sleep component
    if sleep:
        if sleep.get("duration_h"):
            s = min(100.0, (sleep["duration_h"] / 8.0) * 100.0)
            if (sleep.get("deep_min") or 0) > 90:
                s = min(100.0, s + 10.0)
        elif sleep.get("manual_rating"):
            s = min(100.0, sleep["manual_rating"] * 10.0)
        else:
            s = None  # type: ignore[assignment]
        if s is not None:
            components.append((s, 0.4))

    # Macro-adequacy component (avg of protein/carb/fat % of target, clamped)
    if roll["item_count"] > 0:
        hits = []
        for m in ("protein", "carb", "fat"):
            tgt = target.get(m) or 0
            if tgt:
                hits.append((roll["total"][m] / tgt) * 100.0)
        if hits:
            macro = max(0.0, min(100.0, sum(hits) / len(hits)))
            components.append((macro, 0.4))

    # Training-load component (fixed: high burn -> lower recovery headroom)
    # 1500 kcal burn -> 0, 750 -> 50, 0 -> 100.
    load = max(0.0, 100.0 - min(100.0, (summary["total_burn"] or 0.0) / 15.0))
    components.append((load, 0.2))

    if not components:
        return {"score": None, "verdict": "No data", "note": "Log sleep or food to see recovery."}

    wsum = sum(w for _, w in components)
    score = round(sum(s * w for s, w in components) / wsum, 0)
    verdict, note = _recovery_verdict(score)
    return {"score": score, "verdict": verdict, "note": note,
            "load": summary["total_burn"]}


def _recovery_verdict(score: float) -> tuple[str, str]:
    if score >= 80:
        return "Good", "Fully recovered. Ready for intensity."
    if score >= 60:
        return "Good", "Good recovery. Normal training OK."
    if score >= 40:
        return "Partial", "Partial recovery. Consider active recovery."
    return "Low", "Low recovery. Rest day recommended."


# --- status colors + weight context ------------------------------------

def macro_status(logged: float, target: float) -> str:
    """green within 5% of target, yellow 5-15% off, red >15% off."""
    if not target:
        return "none"
    off = abs(logged - target) / target
    if off <= 0.05:
        return "green"
    if off <= 0.15:
        return "yellow"
    return "red"


def contextualize_weight(day: str | None = None) -> str:
    """A deterministic one-line read of the weigh-in (context over numbers).

    We don't track sodium, so the water read is approximated from a high-carb /
    high-burn day and phrased honestly.
    """
    day = day or _today()
    t = weight_trend(day)
    cur, trend7, prior = t["current"], t["trend_7day_avg"], t["prior_week_avg"]
    if cur is None:
        return "No weigh-ins yet — log a weight to see the trend."

    roll = day_rollup(day)
    tgt = resolve_target_for_day(day)
    carbs_high = bool(tgt.get("carb")) and roll["total"]["carb"] > tgt["carb"] * 1.1
    burn = day_training_summary(day)["total_burn"] or 0.0
    weekly = t["weekly_change"]

    # Week-over-week trend: prefer the prior-week comparison; if there's no prior
    # week yet, fall back to the slope across this week's series (first vs last).
    series = t["series"]
    if weekly is None and len(series) >= 4:
        weekly = round(series[-1] - series[0], 2)

    change = (cur - trend7) if trend7 is not None else 0.0
    parts = []
    if abs(change) >= 1.0 and (carbs_high or burn >= 800):
        cause = "glycogen + refuel" if carbs_high else "training water retention"
        parts.append(f"{'Up' if change > 0 else 'Down'} {abs(change):.1f} lb vs your 7-day avg — likely {cause}")
    elif abs(change) >= 0.6:
        parts.append(f"{'Up' if change > 0 else 'Down'} {abs(change):.1f} lb vs your 7-day avg")

    if weekly is not None:
        if weekly < -0.3:
            parts.append(f"trend {weekly:.1f} lb/wk — deficit working")
        elif weekly > 0.3:
            parts.append(f"trend +{weekly:.1f} lb/wk — check the deficit is real")
        else:
            parts.append("trend flat")
    if not parts:
        parts.append(f"{cur:.1f} lb")
    return "; ".join(parts) + "."


# --- week view ----------------------------------------------------------

def week_start(day: str | None = None) -> str:
    """Monday of the week containing `day`."""
    d = _date(day or _today())
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def week_view(start: str | None = None) -> dict[str, Any]:
    """7 days (Mon-Sun): activity, macro status, weight, resolved target — plus trend."""
    start = start or week_start()
    d0 = _date(start)
    days = []
    deficits = []
    for i in range(7):
        d = (d0 + timedelta(days=i)).strftime("%Y-%m-%d")
        roll = day_rollup(d)
        target = resolve_target_for_day(d)
        summary = day_training_summary(d)
        # overall status = worst per-macro status for the day (only if food logged)
        status = "none"
        if roll["item_count"] > 0:
            worsts = [macro_status(roll["total"][m], target.get(m) or 0) for m in MACROS]
            status = ("red" if "red" in worsts else
                      "yellow" if "yellow" in worsts else "green")
        # daily deficit = how far under the day's (activity-scaled) intake goal you ate.
        # The target tier already accounts for the day's burn, so don't add it again.
        if roll["item_count"] > 0 and target.get("calories"):
            deficits.append(target["calories"] - roll["total"]["calories"])
        days.append({
            "day": d,
            "weekday": (d0 + timedelta(days=i)).strftime("%a"),
            "activity": summary,
            "status": status,
            "weight": _weight_on(d),
            "target_calories": target.get("calories"),
            "scope": target.get("scope"),
            "logged_calories": round(roll["total"]["calories"], 0),
            "item_count": roll["item_count"],
        })
    last_day = (d0 + timedelta(days=6)).strftime("%Y-%m-%d")
    trend = weight_trend(last_day)
    avg_deficit = round(sum(deficits) / len(deficits), 0) if deficits else None
    return {
        "start": start,
        "prev": (d0 - timedelta(days=7)).strftime("%Y-%m-%d"),
        "next": (d0 + timedelta(days=7)).strftime("%Y-%m-%d"),
        "days": days,
        "trend": trend,
        "weight_context": contextualize_weight(last_day),
        "avg_daily_deficit": avg_deficit,
    }


# --- small helpers ------------------------------------------------------

def _date(day: str):
    return datetime.strptime(day, "%Y-%m-%d")


def _numornone(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iornone(v: Any) -> int | None:
    n = _numornone(v)
    return int(round(n)) if n is not None else None


# --- habits -------------------------------------------------------------

def list_habits(include_inactive: bool = False) -> list[dict[str, Any]]:
    q = "SELECT id, name, sort_order, active FROM habits"
    if not include_inactive:
        q += " WHERE active = 1"
    q += " ORDER BY sort_order ASC, id ASC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def add_habit(name: str) -> dict[str, Any]:
    name = (name or "").strip() or "Habit"
    with get_conn() as conn:
        nxt = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM habits").fetchone()["n"]
        cur = conn.execute(
            "INSERT INTO habits (name, sort_order, active, created_at) VALUES (?, ?, 1, ?)",
            (name, nxt, _now_iso()),
        )
        hid = int(cur.lastrowid)
        row = conn.execute("SELECT id, name, sort_order, active FROM habits WHERE id = ?", (hid,)).fetchone()
    return dict(row)


def rename_habit(habit_id: int, name: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE habits SET name = ? WHERE id = ?", ((name or "").strip() or "Habit", habit_id))


def delete_habit(habit_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM habits WHERE id = ?", (habit_id,))


def set_habit(habit_id: int, day: str, done: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO habit_entries (habit_id, day, done) VALUES (?, ?, ?)
               ON CONFLICT(habit_id, day) DO UPDATE SET done = excluded.done""",
            (habit_id, day, 1 if done else 0),
        )


def _recent_days(days: int, end: str | None = None) -> list[str]:
    """List of ISO dates, newest first, ending at `end` (default today)."""
    e = _date(end or _today())
    return [(e - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]


def habit_grid(days: int = 14, end: str | None = None) -> dict[str, Any]:
    """Grid for the tracker: columns = habits, rows = dates (newest first)."""
    dates = _recent_days(days, end)
    habits = list_habits()
    ids = [h["id"] for h in habits]
    cells: dict[str, dict[str, bool]] = {d: {} for d in dates}
    if ids and dates:
        qmarks = ",".join("?" * len(ids))
        with get_conn() as conn:
            rows = conn.execute(
                f"""SELECT habit_id, day, done FROM habit_entries
                    WHERE day >= ? AND day <= ? AND habit_id IN ({qmarks})""",
                (dates[-1], dates[0], *ids),
            ).fetchall()
        for r in rows:
            if r["day"] in cells:
                cells[r["day"]][str(r["habit_id"])] = bool(r["done"])
    return {"dates": dates, "habits": habits, "cells": cells}


# --- sleep / recovery series (for the line graphs) ----------------------

def sleep_recovery_series(days: int = 14, end: str | None = None) -> dict[str, Any]:
    dates = list(reversed(_recent_days(days, end)))  # oldest -> newest for a left-to-right graph
    sleep_vals = []
    rec_vals = []
    for d in dates:
        s = get_sleep(d)
        sleep_vals.append(s["duration_h"] if (s and s.get("duration_h")) else None)
        rec = compute_recovery(d)
        rec_vals.append(rec["score"])
    return {"dates": dates, "sleep": sleep_vals, "recovery": rec_vals}


# --- training programs / macrocycles ------------------------------------

def running_program(weeks: int = 10, base: float = 30.0, pct: float = 0.10) -> dict[str, Any]:
    """Placeholder run block: Tue speed / Wed easy / Fri easy / Sat long."""
    return {
        "name": f"{weeks}-week running block",
        "sport": "run",
        "num_weeks": weeks,
        "template": {
            "1": {"type": "run", "intensity": "hard", "role": "speed", "share": 0.20},
            "2": {"type": "run", "intensity": "easy", "role": "easy", "share": 0.20},
            "4": {"type": "run", "intensity": "easy", "role": "easy", "share": 0.20},
            "5": {"type": "run", "intensity": "moderate", "role": "long run", "share": 0.40},
        },
        "progression": {"base_km": base, "increase_pct": pct, "deload_every": 4, "deload_factor": 0.6},
        "overrides": {},
    }


def climbing_program(weeks: int = 10) -> dict[str, Any]:
    """Placeholder climbing block: Mon limit / Wed ARC / Fri+Sat moderate volume."""
    return {
        "name": f"{weeks}-week climbing block",
        "sport": "climb",
        "num_weeks": weeks,
        "template": {
            "0": {"type": "climb", "intensity": "max", "role": "limit bouldering", "duration_min": 90},
            "2": {"type": "climb", "intensity": "easy", "role": "ARCing", "duration_min": 45},
            "4": {"type": "climb", "intensity": "moderate", "role": "volume", "duration_min": 90},
            "5": {"type": "climb", "intensity": "moderate", "role": "volume", "duration_min": 120},
        },
        "progression": {"deload_every": 4, "deload_factor": 0.6},
        "overrides": {},
    }


PRESETS = {"running": running_program, "climbing": climbing_program}


def _weekly_volumes(num_weeks: int, prog: dict) -> list[dict[str, Any]]:
    """Per-week volume + deload flag using the ramp described in the plan."""
    base = prog.get("base_km")
    pct = prog.get("increase_pct", 0.10)
    every = prog.get("deload_every", 4)
    factor = prog.get("deload_factor", 0.6)
    weeks = []
    last_build = None
    for w in range(1, num_weeks + 1):
        deload = every and (w % every == 0)
        if base is None:  # km-less (climbing): no volume number
            weeks.append({"week": w, "km": None, "deload": bool(deload)})
            continue
        if deload and last_build is not None:
            km = round(last_build * factor, 1)
        elif last_build is None:
            km = float(base)
            last_build = km
        else:
            km = round(last_build * (1 + pct), 1)
            last_build = km
        weeks.append({"week": w, "km": km, "deload": bool(deload)})
    return weeks


def create_program(spec: dict[str, Any], start_monday: str | None = None) -> dict[str, Any]:
    start_monday = start_monday or week_start()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO programs (name, sport, num_weeks, start_monday, spec, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (spec.get("name", "Program"), spec.get("sport", "other"), int(spec.get("num_weeks", 10)),
             start_monday, json.dumps(spec), _now_iso()),
        )
        pid = int(cur.lastrowid)
    return get_program(pid)  # separate connection after commit


def list_programs() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, sport, num_weeks, start_monday FROM programs WHERE active = 1 ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_program(pid: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM programs WHERE id = ?", (pid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["spec"] = json.loads(d["spec"])
    return d


def delete_program(pid: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM programs WHERE id = ?", (pid,))


def program_grid(pid: int) -> dict[str, Any] | None:
    """Weeks (rows) x Mon-Sun (cols) of planned sessions, with weekly volume + deload."""
    p = get_program(pid)
    if not p:
        return None
    spec = p["spec"]
    template = spec.get("template", {})
    prog = spec.get("progression", {})
    overrides = spec.get("overrides", {})
    num_weeks = p["num_weeks"]
    start = _date(p["start_monday"])
    today = _today()
    vols = _weekly_volumes(num_weeks, prog)
    rows = []
    for wi in range(1, num_weeks + 1):
        vol = vols[wi - 1]
        days = []
        # count session shares this week (for km distribution)
        for wd in range(7):
            base_sess = template.get(str(wd))
            planned = None
            if base_sess:
                planned = dict(base_sess)
                # distribute km for running
                if vol["km"] is not None and "share" in base_sess:
                    factor = vol["deload"] and 0.85 or 1.0  # slight taper handled by weekly km already
                    planned["planned_km"] = round(vol["km"] * base_sess["share"] * factor, 1)
                if vol["deload"]:
                    # deloads ease intensity
                    if planned.get("intensity") == "max":
                        planned["intensity"] = "hard"
                ov = overrides.get(f"w{wi}-{wd}")
                if ov:
                    planned.update(ov)
            cal_date = (start + timedelta(days=(wi - 1) * 7 + wd)).strftime("%Y-%m-%d")
            days.append({
                "weekday": wd, "wd_label": _WD[wd], "date": cal_date,
                "session": planned, "is_today": cal_date == today,
            })
        week_dates = [d["date"] for d in days]
        rows.append({
            "week": wi, "km": vol["km"], "deload": vol["deload"],
            "is_current": week_dates[0] <= today <= week_dates[6],
            "days": days,
        })
    return {"program": {k: p[k] for k in ("id", "name", "sport", "num_weeks", "start_monday")},
            "weeks": rows}


def override_session(pid: int, week: int, weekday: int, fields: dict[str, Any]) -> None:
    p = get_program(pid)
    if not p:
        return
    spec = p["spec"]
    spec.setdefault("overrides", {})[f"w{week}-{weekday}"] = fields
    with get_conn() as conn:
        conn.execute("UPDATE programs SET spec = ? WHERE id = ?", (json.dumps(spec), pid))


def log_program_day(pid: int, week: int, weekday: int) -> dict[str, Any] | None:
    """Turn a planned session into a real training entry on its calendar date."""
    grid = program_grid(pid)
    if not grid:
        return None
    for wk in grid["weeks"]:
        if wk["week"] != week:
            continue
        for day in wk["days"]:
            if day["weekday"] == weekday and day["session"]:
                s = day["session"]
                entry = {
                    "day": day["date"], "type": s.get("type", "run"),
                    "intensity": s.get("intensity", "moderate"),
                    "duration_min": s.get("duration_min", 0),
                    "detail": (s.get("role", "") + (f" · {s['planned_km']}km" if s.get("planned_km") else "")).strip(" ·"),
                }
                insert_training(entry)
                return {"logged": day["date"], "detail": entry["detail"]}
    return None


def parse_program_command(text: str) -> dict[str, Any] | None:
    """Offline NL parse: recognize sport + weeks + base km + % + deload from a sentence."""
    t = (text or "").lower()
    sport = None
    if any(k in t for k in ("run", "5k", "10k", "marathon", "speed", "long run")):
        sport = "running"
    elif any(k in t for k in ("climb", "boulder", "arc", "limit")):
        sport = "climbing"
    if not sport:
        return None
    weeks_m = re.search(r"(\d+)\s*[- ]?\s*week", t)
    weeks = int(weeks_m.group(1)) if weeks_m else 10
    weeks = max(1, min(52, weeks))
    if sport == "running":
        base_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:km|k\b)", t)
        base = float(base_m.group(1)) if base_m else 30.0
        pct_m = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
        pct = (float(pct_m.group(1)) / 100.0) if pct_m else 0.10
        spec = running_program(weeks=weeks, base=base, pct=pct)
    else:
        spec = climbing_program(weeks=weeks)
    if "deload" not in t:
        spec["progression"]["deload_every"] = 0  # user didn't ask for a deload
    else:
        dm = re.search(r"(?:every\s*)?(\d+)(?:th|rd|nd|st)?\s*week\s*deload|week\s*(\d+)\s*deload|deload\s*(?:every\s*)?(\d+)", t)
        if dm:
            n = next((g for g in dm.groups() if g), None)
            if n:
                spec["progression"]["deload_every"] = int(n)
    return spec


def coach_reply(text: str) -> dict[str, Any]:
    """Offline training coach: create/modify programs or summarize the current week."""
    t = (text or "").strip()
    tl = t.lower()

    # "what's this week" style query
    if any(k in tl for k in ("this week", "what's on", "whats on", "today", "next session")):
        progs = list_programs()
        if not progs:
            return {"reply": "No program yet. Try: “Create a 10-week running program starting at 30km, +10% a week, week-4 deload.”"}
        grid = program_grid(progs[0]["id"])
        cur = next((w for w in grid["weeks"] if w["is_current"]), grid["weeks"][0])
        lines = [f"**{grid['program']['name']}** — week {cur['week']}{' (deload)' if cur['deload'] else ''}"
                 + (f", target {cur['km']} km" if cur["km"] else "")]
        for d in cur["days"]:
            if d["session"]:
                s = d["session"]
                lines.append(f"· {d['wd_label']}: {s.get('role', s.get('type'))}"
                             + (f" — {s['planned_km']} km" if s.get("planned_km") else "")
                             + (f" ({s['intensity']})" if s.get("intensity") else ""))
        return {"reply": "\n".join(lines), "program_id": progs[0]["id"]}

    # program creation
    spec = parse_program_command(t)
    if spec:
        p = create_program(spec)
        grid = program_grid(p["id"])
        vols = [w["km"] for w in grid["weeks"] if w["km"] is not None]
        ramp = (" Weekly volume ramps " + " → ".join(f"{v}km" for v in vols[:5]) + ("…" if len(vols) > 5 else "") + ".") if vols else ""
        deloads = [str(w["week"]) for w in grid["weeks"] if w["deload"]]
        dl = f" Deload weeks: {', '.join(deloads)}." if deloads else ""
        return {
            "reply": f"Built **{p['name']}** ({p['num_weeks']} weeks).{ramp}{dl} Open the Macrocycle view to see the full grid and log sessions.",
            "program_id": p["id"], "action": "created",
        }

    return {"reply": "I can build a training macrocycle. Try: “10-week running program, 30km, +10% a week, week-4 deload” or “8-week climbing block”."}
