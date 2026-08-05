"""§3 — the training coach: regex parsing, volume ramp, and honest fallback."""

from __future__ import annotations

import json

import anthropic

import coach

# --- offline parser ----------------------------------------------------------

def test_parse_running_full_spec():
    spec = coach.parse_program_command(
        "10-week running program, 30km, +10% a week, week-4 deload")
    assert spec["sport"] == "run"
    assert spec["num_weeks"] == 10
    assert spec["progression"]["base_km"] == 30.0
    assert abs(spec["progression"]["increase_pct"] - 0.10) < 1e-9
    assert spec["progression"]["deload_every"] == 4


def test_parse_no_deload_mentioned_zeroes_it():
    # The branch at the old db.py:1050 — no "deload" word means no deload.
    spec = coach.parse_program_command("8-week running program at 25km")
    assert spec["progression"]["deload_every"] == 0


def test_parse_climbing():
    spec = coach.parse_program_command("8-week climbing block")
    assert spec["sport"] == "climb"
    assert spec["num_weeks"] == 8


def test_parse_weeks_clamped():
    assert coach.parse_program_command("999-week running block")["num_weeks"] == 52


def test_parse_non_program_returns_none():
    assert coach.parse_program_command("how are you feeling about my week?") is None


# --- weekly volume ramp + deload placement (db builder) ----------------------

def test_weekly_volumes_ramp_and_deload(tmp_db):
    db = tmp_db
    prog = {"base_km": 30.0, "increase_pct": 0.10, "deload_every": 4,
            "deload_factor": 0.6}
    weeks = db._weekly_volumes(8, prog)
    assert weeks[0]["km"] == 30.0
    assert weeks[1]["km"] == 33.0          # +10% off the last build week
    assert weeks[3]["deload"] is True      # week 4
    assert weeks[7]["deload"] is True      # week 8
    # A deload eases off the prior *build* week, not the running total.
    assert weeks[3]["km"] < weeks[2]["km"]


def test_weekly_volumes_no_deload_when_every_zero(tmp_db):
    db = tmp_db
    weeks = db._weekly_volumes(5, {"base_km": 20.0, "increase_pct": 0.1,
                                   "deload_every": 0})
    assert not any(w["deload"] for w in weeks)


# --- LLM dispatch + fallback honesty -----------------------------------------

class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


def _fake_client(reply_json):
    class _Msgs:
        def create(self, **_):
            return _Resp(reply_json)

    class _Client:
        messages = _Msgs()

    return _Client()


def test_llm_advice_reply_no_program(tmp_db, monkeypatch):
    monkeypatch.delenv("MACRO_OFFLINE", raising=False)
    payload = json.dumps({"reply": "Back off ~25% this week.", "create": None,
                          "program_id": None})
    monkeypatch.setattr(coach.engine, "_client", lambda: _fake_client(payload))
    monkeypatch.setattr(coach, "_system_prompt", lambda: "sys")
    out = coach.coach_reply("I felt wrecked after Tuesday, back off next week")
    assert out["engine"]["mode"] == "llm"
    assert "Back off" in out["reply"]
    assert "program_id" not in out  # advice only — no program created


def test_llm_create_builds_program(tmp_db, monkeypatch):
    monkeypatch.delenv("MACRO_OFFLINE", raising=False)
    payload = json.dumps({
        "reply": "Built your block.",
        "create": {"sport": "run", "weeks": 12, "base_km": 40,
                   "increase_pct": 0.08, "deload_every": 4},
        "program_id": None,
    })
    monkeypatch.setattr(coach.engine, "_client", lambda: _fake_client(payload))
    monkeypatch.setattr(coach, "_system_prompt", lambda: "sys")
    before = len(tmp_db.list_programs())
    out = coach.coach_reply("build me a 12-week run block")
    assert out["engine"]["mode"] == "llm"
    assert out.get("action") == "created"
    assert "program_id" in out
    assert len(tmp_db.list_programs()) == before + 1
    grid = tmp_db.program_grid(out["program_id"])
    assert grid["program"]["num_weeks"] == 12


def test_bad_json_falls_back_to_regex(tmp_db, monkeypatch, caplog):
    monkeypatch.delenv("MACRO_OFFLINE", raising=False)
    monkeypatch.setattr(coach.engine, "_client",
                        lambda: _fake_client("not json at all"))
    monkeypatch.setattr(coach, "_system_prompt", lambda: "sys")
    with caplog.at_level("WARNING"):
        out = coach.coach_reply("10-week running program at 30km, week-4 deload")
    # Degraded, but the regex still built the program the user asked for.
    assert out["engine"]["mode"] == "offline"
    assert out["engine"]["reason"] == "bad_output"
    assert "program_id" in out


def test_auth_error_falls_back(tmp_db, monkeypatch):
    monkeypatch.delenv("MACRO_OFFLINE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-bad")

    def boom():
        class _C:
            class messages:
                @staticmethod
                def create(**_):
                    err = anthropic.AuthenticationError.__new__(
                        anthropic.AuthenticationError)
                    Exception.__init__(err, "401")
                    raise err
        return _C()

    monkeypatch.setattr(coach.engine, "_client", boom)
    monkeypatch.setattr(coach, "_system_prompt", lambda: "sys")
    out = coach.coach_reply("how's my week look?")
    assert out["engine"]["reason"] == "auth_failed"


def test_forced_offline_uses_regex(tmp_db, monkeypatch):
    monkeypatch.setenv("MACRO_OFFLINE", "1")
    out = coach.coach_reply("8-week climbing block")
    assert out["engine"]["mode"] == "offline"
    assert out["engine"]["reason"] == "forced"
    assert "program_id" in out  # regex created the climbing block
