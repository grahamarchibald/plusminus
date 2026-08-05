"""PlusMinus API + static host.

Flow: POST /api/log runs the engine and returns an estimate *without saving*
(the estimate is a proposal, not a fact — DESIGN_BRIEF.md). POST /api/confirm
persists the confirmed/edited item and returns the fresh day view.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
from engine import estimate

STATIC_DIR = Path(__file__).parent / "static"

api = FastAPI(title="PlusMinus", version="0.1.0")

db.init_db()  # idempotent; safe on every boot


# --- request models -----------------------------------------------------

class LogRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ConfirmItem(BaseModel):
    name: str
    calories: float = 0
    protein: float = 0
    carb: float = 0
    fat: float = 0
    confidence: str | None = None
    uncertainty_cal: float = 0
    assumptions: list[str] = Field(default_factory=list)
    source: str = "described"
    day: str | None = None  # log to a specific calendar day (defaults to today)


class TargetRequest(BaseModel):
    calories: float
    protein: float
    carb: float
    fat: float
    rationale: str = "Manual update"
    scope: str = "default"


class TrainingRequest(BaseModel):
    day: str | None = None
    type: str
    duration_min: int | None = None
    intensity: str = "moderate"
    detail: str | None = None
    est_burn: float | None = None
    subjective_difficulty: int | None = None
    notes: str | None = None


class SleepRequest(BaseModel):
    day: str | None = None
    duration_h: float | None = None
    deep_min: int | None = None
    rem_min: int | None = None
    wakeups: int | None = None
    manual_rating: int | None = None
    notes: str | None = None


class WeighInRequest(BaseModel):
    day: str | None = None
    weight: float
    unit: str = "lb"


class HabitCreate(BaseModel):
    name: str


class HabitRename(BaseModel):
    name: str


class HabitToggle(BaseModel):
    day: str
    done: bool


class CoachRequest(BaseModel):
    text: str


class ProgramCreate(BaseModel):
    preset: str | None = None      # "running" | "climbing"
    spec: dict | None = None
    start_monday: str | None = None


class OverrideReq(BaseModel):
    week: int
    weekday: int
    fields: dict


class LogDayReq(BaseModel):
    week: int
    weekday: int


# --- helpers ------------------------------------------------------------

def _day_view(day: str | None = None) -> dict[str, Any]:
    """Everything the daily summary card needs: macros, training, sleep, recovery.

    Fetches each shared piece once (day's sessions, rollup, sleep) and threads
    them through resolve_target_for_day / day_training_summary / compute_recovery,
    which otherwise each re-derive them — collapsing list_training from 5 queries
    per view to 1 (see §2).
    """
    day = day or db._today()
    sessions = db.list_training(day)
    roll = db.day_rollup(day)
    sleep = db.get_sleep(day)
    target = db.resolve_target_for_day(day, sessions=sessions)  # activity-scaled
    summary = db.day_training_summary(day, sessions=sessions)
    total = roll["total"]
    remaining = {m: round((target.get(m) or 0) - total.get(m, 0), 1) for m in db.MACROS}
    status = {m: db.macro_status(total.get(m, 0), target.get(m) or 0) for m in db.MACROS}
    return {
        "day": day,
        "items": db.list_items(day),
        "total": total,
        "uncertainty_cal": roll["uncertainty_cal"],
        "confidence_calories": roll["confidence_calories"],
        "target": target,
        "remaining": remaining,
        "status": status,
        "training": sessions,
        "training_summary": summary,
        "sleep": sleep,
        "weight": db.weight_for_day(day),
        "recovery": db.compute_recovery(
            day, roll=roll, target=target, summary=summary, sleep=sleep
        ),
    }


# --- routes -------------------------------------------------------------

@api.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@api.post("/api/log")
def log(req: LogRequest) -> dict[str, Any]:
    """Estimate macros for a description. Does NOT save — returns a proposal.

    Uses the LLM engine when a key is available, else a deterministic offline
    estimate so the whole app is explorable without the LLM.
    """
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail={"error": "Empty description."})
    return estimate(req.text)


@api.post("/api/confirm")
def confirm(item: ConfirmItem) -> dict[str, Any]:
    """Persist a confirmed (possibly edited) item, then return the fresh day view."""
    payload = item.model_dump()
    day = payload.pop("day", None)
    if day:
        payload["day"] = day
    db.insert_food(payload)
    return _day_view(day)


@api.get("/api/day")
def day(day: str | None = None) -> dict[str, Any]:
    return _day_view(day)


@api.get("/api/week")
def week(start: str | None = None) -> dict[str, Any]:
    return db.week_view(start)


@api.post("/api/training")
def post_training(req: TrainingRequest) -> dict[str, Any]:
    db.insert_training(req.model_dump())
    return _day_view(req.day)


@api.post("/api/sleep")
def post_sleep(req: SleepRequest) -> dict[str, Any]:
    db.insert_sleep(req.model_dump())
    return _day_view(req.day)


@api.post("/api/weigh_in")
def post_weigh_in(req: WeighInRequest) -> dict[str, Any]:
    db.insert_weigh_in(req.day or db._today(), req.weight, req.unit)
    return _day_view(req.day)


@api.get("/api/habits")
def get_habits() -> list[dict[str, Any]]:
    return db.list_habits()


@api.post("/api/habits")
def create_habit(req: HabitCreate) -> dict[str, Any]:
    return db.add_habit(req.name)


@api.post("/api/habits/{habit_id}/rename")
def rename_habit(habit_id: int, req: HabitRename) -> dict[str, str]:
    db.rename_habit(habit_id, req.name)
    return {"status": "ok"}


@api.delete("/api/habits/{habit_id}")
def delete_habit(habit_id: int) -> dict[str, str]:
    db.delete_habit(habit_id)
    return {"status": "ok"}


@api.post("/api/habits/{habit_id}/toggle")
def toggle_habit(habit_id: int, req: HabitToggle) -> dict[str, str]:
    db.set_habit(habit_id, req.day, req.done)
    return {"status": "ok"}


@api.get("/api/habit_grid")
def habit_grid(days: int = 14) -> dict[str, Any]:
    return db.habit_grid(days)


@api.get("/api/series")
def series(days: int = 14) -> dict[str, Any]:
    return db.sleep_recovery_series(days)


# --- training programs / coach ------------------------------------------

@api.get("/api/programs")
def programs() -> list[dict[str, Any]]:
    return db.list_programs()


@api.post("/api/programs")
def create_program(req: ProgramCreate) -> dict[str, Any]:
    if req.preset:
        maker = db.PRESETS.get(req.preset)
        if not maker:
            raise HTTPException(status_code=400, detail={"error": f"Unknown preset '{req.preset}'"})
        spec = maker()
    elif req.spec:
        spec = req.spec
    else:
        raise HTTPException(status_code=400, detail={"error": "Provide a preset or spec"})
    return db.create_program(spec, req.start_monday)


@api.get("/api/programs/{pid}")
def get_program(pid: int) -> dict[str, Any]:
    grid = db.program_grid(pid)
    if not grid:
        raise HTTPException(status_code=404, detail={"error": "Program not found"})
    return grid


@api.delete("/api/programs/{pid}")
def delete_program(pid: int) -> dict[str, str]:
    db.delete_program(pid)
    return {"status": "ok"}


@api.post("/api/programs/{pid}/override")
def override_program(pid: int, req: OverrideReq) -> dict[str, str]:
    db.override_session(pid, req.week, req.weekday, req.fields)
    return {"status": "ok"}


@api.post("/api/programs/{pid}/log_day")
def log_program_day(pid: int, req: LogDayReq) -> dict[str, Any]:
    res = db.log_program_day(pid, req.week, req.weekday)
    if not res:
        raise HTTPException(status_code=400, detail={"error": "No planned session on that day"})
    return res


@api.post("/api/coach")
def coach(req: CoachRequest) -> dict[str, Any]:
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail={"error": "Empty message."})
    return db.coach_reply(req.text)


@api.get("/api/target")
def get_target(scope: str = "default") -> dict[str, Any]:
    return db.get_active_target(scope) or {}


@api.post("/api/target")
def post_target(req: TargetRequest) -> dict[str, Any]:
    return db.set_target(req.calories, req.protein, req.carb, req.fat, req.rationale, req.scope)


# Mount static assets last so it doesn't shadow /api routes.
api.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
