"""The training coach — reasons about a block, or answers a question.

Mirrors engine.py's contract: try Claude, fall back to a deterministic regex
parser on any failure, and always record *why* via the same `engine` block, so
a degraded coach is as visible as a degraded estimator (§1, §3).

The model's job is deliberately narrow: free-form advice plus *parameter
extraction* for program creation. The actual grid is always built by the
deterministic builders in db.py (running_program / climbing_program), so the
model never emits a whole template and a malformed reply can't produce a broken
program.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

import anthropic

import db
import engine

log = logging.getLogger("plusminus.coach")

MODEL = os.environ.get("MACRO_COACH_MODEL", engine.MODEL)
PROMPT_PATH = Path(__file__).parent / "prompts" / "training_coach.md"

# Coach-flavored wording for the shared offline reasons — a degraded coach is
# pattern-matched, not "a lookup-table estimate" (that phrasing is the engine's).
COACH_REASONS = {
    "forced": "Offline mode is on (MACRO_OFFLINE=1) — this is the pattern-matched coach.",
    "no_key": "No API key set — this is the pattern-matched coach, not the reasoning one. "
              "Set ANTHROPIC_API_KEY for real advice.",
    "auth_failed": "Your API key was rejected — this is the pattern-matched coach, not the "
                   "reasoning one. Check ANTHROPIC_API_KEY.",
    "rate_limited": "Rate limited by the API — falling back to the pattern-matched coach. "
                    "Try again shortly.",
    "unreachable": "Couldn't reach the API — falling back to the pattern-matched coach. "
                   "Check your connection.",
    "bad_output": "The coach returned something unparseable — falling back to the "
                  "pattern-matched coach.",
    "error": "The coach hit an unexpected error — falling back to the pattern-matched coach.",
}


# --- offline (regex) coach — the deterministic fallback ---------------------

def parse_program_command(text: str) -> dict | None:
    """Offline NL parse: recognize sport + weeks + base km + % + deload."""
    import re

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
        spec = db.running_program(weeks=weeks, base=base, pct=pct)
    else:
        spec = db.climbing_program(weeks=weeks)
    if "deload" not in t:
        spec["progression"]["deload_every"] = 0  # user didn't ask for a deload
    else:
        dm = re.search(r"(?:every\s*)?(\d+)(?:th|rd|nd|st)?\s*week\s*deload|week\s*(\d+)\s*deload|deload\s*(?:every\s*)?(\d+)", t)
        if dm:
            n = next((g for g in dm.groups() if g), None)
            if n:
                spec["progression"]["deload_every"] = int(n)
    return spec


def coach_reply_offline(text: str) -> dict:
    """Regex coach: create/modify programs or summarize the current week."""
    t = (text or "").strip()
    tl = t.lower()

    # "what's this week" style query
    if any(k in tl for k in ("this week", "what's on", "whats on", "today", "next session")):
        progs = db.list_programs()
        if not progs:
            return {"reply": "No program yet. Try: “Create a 10-week running program starting at 30km, +10% a week, week-4 deload.”"}
        grid = db.program_grid(progs[0]["id"])
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
        p = db.create_program(spec)
        grid = db.program_grid(p["id"])
        vols = [w["km"] for w in grid["weeks"] if w["km"] is not None]
        ramp = (" Weekly volume ramps " + " → ".join(f"{v}km" for v in vols[:5]) + ("…" if len(vols) > 5 else "") + ".") if vols else ""
        deloads = [str(w["week"]) for w in grid["weeks"] if w["deload"]]
        dl = f" Deload weeks: {', '.join(deloads)}." if deloads else ""
        return {
            "reply": f"Built **{p['name']}** ({p['num_weeks']} weeks).{ramp}{dl} Open the Macrocycle view to see the full grid and log sessions.",
            "program_id": p["id"], "action": "created",
        }

    return {"reply": "I can build a training macrocycle. Try: “10-week running program, 30km, +10% a week, week-4 deload” or “8-week climbing block”."}


# --- LLM coach — reasoning path ---------------------------------------------

@lru_cache(maxsize=1)
def _prompt_template() -> str:
    return engine._extract_fenced(PROMPT_PATH.read_text(encoding="utf-8"))


def _context_block() -> str:
    """A compact snapshot of the user's training state for the model to reason over."""
    lines: list[str] = []
    progs = db.list_programs()
    if progs:
        lines.append("Programs:")
        for p in progs:
            lines.append(f"- id={p['id']} \"{p['name']}\" ({p['sport']}, {p['num_weeks']} weeks)")
        grid = db.program_grid(progs[0]["id"])
        cur = next((w for w in grid["weeks"] if w["is_current"]), None)
        if cur:
            planned = [f"{d['wd_label']} {d['session'].get('role', d['session']['type'])}"
                       for d in cur["days"] if d["session"]]
            lines.append(f"Current week: {cur['week']}"
                         + (" (deload)" if cur["deload"] else "")
                         + (f", volume {cur['km']}km" if cur["km"] else "")
                         + (". Sessions: " + ", ".join(planned) if planned else ""))
    else:
        lines.append("No training programs exist yet.")

    # Recent training load: the last 7 days of logged sessions.
    today = db._date(db._today())
    recent = []
    for i in range(6, -1, -1):
        day = (today - db.timedelta(days=i)).strftime("%Y-%m-%d")
        for s in db.list_training(day):
            if s.get("type") != "rest":
                recent.append(f"{day} {s['type']}/{s.get('intensity', '?')}"
                              + (f" {s['duration_min']}min" if s.get("duration_min") else ""))
    lines.append("Recent sessions (7d): " + ("; ".join(recent) if recent else "none logged"))
    return "\n".join(lines)


def _system_prompt() -> str:
    return _prompt_template().replace("{{CONTEXT}}", _context_block())


def _spec_from_params(sport=None, weeks=None, base_km=None, increase_pct=None,
                      deload_every=None, **_ignored) -> dict:
    """Turn the model's extracted parameters into a valid program spec via the
    deterministic builders. Unknown keys are ignored so a loose reply is safe."""
    weeks = max(1, min(52, int(weeks or 10)))
    if str(sport).lower() in ("run", "running"):
        spec = db.running_program(
            weeks=weeks,
            base=float(base_km) if base_km else 30.0,
            pct=float(increase_pct) if increase_pct else 0.10,
        )
    else:
        spec = db.climbing_program(weeks=weeks)
    spec["progression"]["deload_every"] = int(deload_every) if deload_every else 0
    return spec


def _llm_coach(text: str) -> dict:
    """One Messages call. Returns the parsed reply dict; raises on bad output."""
    resp = engine._client().messages.create(
        model=MODEL,
        max_tokens=1500,
        system=_system_prompt(),
        messages=[{"role": "user", "content": text.strip()}],
    )
    raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    if not raw.strip():
        raise engine.EstimationError("Coach returned no text.", raw)
    try:
        data = json.loads(engine._strip_fences(raw))
    except json.JSONDecodeError as e:
        raise engine.EstimationError(f"Coach output was not valid JSON: {e}", raw) from e
    if "reply" not in data:
        raise engine.EstimationError("Coach output missing 'reply'.", raw)
    return data


def _offline_reply(text: str, reason: str, detail: str | None = None) -> dict:
    result = coach_reply_offline(text)
    result["engine"] = {
        "mode": "offline",
        "reason": reason,
        "message": COACH_REASONS.get(reason, COACH_REASONS["error"]),
        "detail": detail,
    }
    return result


def coach_reply(text: str) -> dict:
    """Run the reasoning coach; on any failure fall back to the regex coach,
    always recording why (same honest contract as the estimation engine)."""
    t = (text or "").strip()
    if not t:
        return _offline_reply(t, "forced")
    if os.environ.get("MACRO_OFFLINE") == "1":
        return _offline_reply(t, "forced")
    try:
        data = _llm_coach(t)
        result = {"reply": (data.get("reply") or "").strip() or "(no reply)"}
        create = data.get("create")
        if create:
            p = db.create_program(_spec_from_params(**create))
            result["program_id"] = p["id"]
            result["action"] = "created"
        elif data.get("program_id"):
            result["program_id"] = data["program_id"]
        result["engine"] = {"mode": "llm", "reason": "ok", "message": None, "detail": None}
        return result
    except anthropic.AuthenticationError as e:
        reason = "auth_failed" if os.environ.get("ANTHROPIC_API_KEY") else "no_key"
        return _offline_reply(t, reason, engine._short(e))
    except anthropic.RateLimitError as e:
        return _offline_reply(t, "rate_limited", engine._short(e))
    except anthropic.APIConnectionError as e:
        return _offline_reply(t, "unreachable", engine._short(e))
    except engine.EstimationError as e:
        log.warning("Coach returned unparseable output: %s | raw=%r", e, e.raw)
        return _offline_reply(t, "bad_output", str(e))
    except Exception as e:  # noqa: BLE001 — last resort: degrade, never 500
        log.warning("Coach hit an unexpected error: %s", e)
        return _offline_reply(t, "error", engine._short(e))
