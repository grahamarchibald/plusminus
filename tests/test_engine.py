"""§1 — the estimation engine's contract and its honest-fallback behavior.

No test here touches the network: we either force offline mode or monkeypatch
`estimate_describe` to raise the exception we want to exercise.
"""

from __future__ import annotations

import anthropic

import engine

CONTRACT_KEYS = {"items", "total", "confidence", "uncertainty_cal",
                 "assumptions", "swing_factors"}


# --- offline estimator shape -------------------------------------------------

def test_offline_estimate_matches_contract():
    data = engine.offline_estimate("3 eggs and rice")
    assert CONTRACT_KEYS <= data.keys()
    assert data["total"]["calories"] > 0
    assert data["confidence"] in {"high", "medium", "low"}
    assert isinstance(data["items"], list) and data["items"]


def test_offline_confidence_scales_with_recognition():
    # Fully recognized items -> high; unknown gibberish -> low.
    known = engine.offline_estimate("3 eggs")
    unknown = engine.offline_estimate("zxqwerty foodstuff")
    assert known["confidence"] == "high"
    assert unknown["confidence"] == "low"
    # Lower confidence carries a wider uncertainty band.
    assert unknown["uncertainty_cal"] / max(1, unknown["total"]["calories"]) > \
        known["uncertainty_cal"] / max(1, known["total"]["calories"])


# --- the engine block: every path is labelled -------------------------------

def test_forced_offline(monkeypatch):
    monkeypatch.setenv("MACRO_OFFLINE", "1")
    data = engine.estimate("3 eggs")
    assert data["engine"]["mode"] == "offline"
    assert data["engine"]["reason"] == "forced"
    assert data["engine"]["message"]  # a user-facing sentence is always present


def _raise(exc):
    def _inner(_text):
        raise exc
    return _inner


def test_no_key_when_env_unset(monkeypatch):
    monkeypatch.delenv("MACRO_OFFLINE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    err = anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)
    Exception.__init__(err, "no creds")
    monkeypatch.setattr(engine, "estimate_describe", _raise(err))
    data = engine.estimate("3 eggs")
    assert data["engine"]["reason"] == "no_key"
    assert data["engine"]["mode"] == "offline"


def test_auth_failed_when_key_present(monkeypatch):
    monkeypatch.delenv("MACRO_OFFLINE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-bad")
    err = anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)
    Exception.__init__(err, "401 invalid key")
    monkeypatch.setattr(engine, "estimate_describe", _raise(err))
    data = engine.estimate("3 eggs")
    assert data["engine"]["reason"] == "auth_failed"


def test_rate_limited(monkeypatch):
    monkeypatch.delenv("MACRO_OFFLINE", raising=False)
    err = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    Exception.__init__(err, "429")
    monkeypatch.setattr(engine, "estimate_describe", _raise(err))
    assert engine.estimate("3 eggs")["engine"]["reason"] == "rate_limited"


def test_unreachable(monkeypatch):
    monkeypatch.delenv("MACRO_OFFLINE", raising=False)
    err = anthropic.APIConnectionError.__new__(anthropic.APIConnectionError)
    Exception.__init__(err, "connection refused")
    monkeypatch.setattr(engine, "estimate_describe", _raise(err))
    assert engine.estimate("3 eggs")["engine"]["reason"] == "unreachable"


def test_bad_output_preserves_raw_and_logs(monkeypatch, caplog):
    monkeypatch.delenv("MACRO_OFFLINE", raising=False)
    err = engine.EstimationError("not JSON", raw="<<the model's actual text>>")
    monkeypatch.setattr(engine, "estimate_describe", _raise(err))
    with caplog.at_level("WARNING"):
        data = engine.estimate("3 eggs")
    assert data["engine"]["reason"] == "bad_output"
    # The raw model text is logged server-side — the only way to debug this path.
    assert "the model's actual text" in caplog.text


def test_unexpected_error_degrades(monkeypatch):
    monkeypatch.delenv("MACRO_OFFLINE", raising=False)
    monkeypatch.setattr(engine, "estimate_describe", _raise(RuntimeError("boom")))
    data = engine.estimate("3 eggs")
    assert data["engine"]["reason"] == "error"
    assert data["engine"]["mode"] == "offline"


def test_every_reason_has_a_message():
    # No reason slug may render a blank banner.
    for reason, msg in engine.OFFLINE_REASONS.items():
        assert msg and isinstance(msg, str), reason


# --- prompt assembly ---------------------------------------------------------

def test_extract_fenced_pulls_first_block():
    md = "prose\n```\nSYSTEM PROMPT BODY\n```\nmore prose"
    assert engine._extract_fenced(md) == "SYSTEM PROMPT BODY"


def test_system_prompt_injects_user_facts(monkeypatch, tmp_path):
    engine._system_prompt.cache_clear()
    facts = tmp_path / "facts.txt"
    facts.write_text("# comment ignored\n- Eggs: 70 cal each\n", encoding="utf-8")
    monkeypatch.setattr(engine, "USER_FACTS_PATH", facts)
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "intro\n```\nBody with {{USER_FACTS}} slot\n```\n", encoding="utf-8"
    )
    monkeypatch.setattr(engine, "PROMPT_PATH", prompt)
    out = engine._system_prompt()
    assert "Eggs: 70 cal each" in out
    assert "{{USER_FACTS}}" not in out
    assert "comment ignored" not in out  # commented lines are stripped
    engine._system_prompt.cache_clear()
