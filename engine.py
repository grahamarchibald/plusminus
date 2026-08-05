"""The estimation engine — the heart of MacroCoach.

A single Claude Messages API call (describe mode): a food description in,
structured macros with honest uncertainty out. Stateless per call; the store
passes any needed context in. The system prompt lives in
`prompts/estimation_engine.md` and is the product — keep it authoritative.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

import anthropic

MODEL = os.environ.get("MACRO_MODEL", "claude-opus-5")
PROMPT_PATH = Path(__file__).parent / "prompts" / "estimation_engine.md"
USER_FACTS_PATH = Path(__file__).parent / "user_facts.txt"

# The contract fields the dashboard depends on (SPEC.md §2).
_REQUIRED = ("items", "total", "confidence", "uncertainty_cal", "assumptions", "swing_factors")


class EstimationError(RuntimeError):
    """Raised when the engine's output can't be parsed into the contract shape.

    Carries the raw model text so the API layer can surface it for debugging.
    """

    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    # Reads ANTHROPIC_API_KEY, or an `ant auth login` profile.
    return anthropic.Anthropic()


def _extract_fenced(md: str) -> str:
    """Return the first fenced code block's contents from the prompt markdown.

    The prompt file wraps the actual system prompt in a ``` fence with prose
    around it; we want just the fenced text.
    """
    m = re.search(r"```[^\n]*\n(.*?)\n```", md, re.DOTALL)
    return m.group(1) if m else md


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    md = PROMPT_PATH.read_text(encoding="utf-8")
    prompt = _extract_fenced(md)
    facts = ""
    if USER_FACTS_PATH.exists():
        lines = [
            ln for ln in USER_FACTS_PATH.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        facts = "\n".join(lines).strip()
    if not facts:
        facts = "(No saved user facts yet — reason from standard nutrition data.)"
    return prompt.replace("{{USER_FACTS}}", facts)


def _strip_fences(text: str) -> str:
    """Defensively strip a ```json ... ``` wrapper if the model added one."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\n", "", t)
        t = re.sub(r"\n```$", "", t)
    return t.strip()


def estimate_describe(text: str) -> dict:
    """Text in, contract JSON out. Raises EstimationError on unparseable output."""
    if not text or not text.strip():
        raise EstimationError("Empty food description.")

    resp = _client().messages.create(
        model=MODEL,
        max_tokens=2048,  # room for adaptive thinking + the small JSON body
        system=_system_prompt(),
        messages=[{"role": "user", "content": text.strip()}],
    )

    raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    if not raw.strip():
        raise EstimationError("Engine returned no text output.", raw)

    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as e:
        raise EstimationError(f"Engine output was not valid JSON: {e}", raw) from e

    missing = [k for k in _REQUIRED if k not in data]
    if missing:
        raise EstimationError(f"Engine output missing fields: {missing}", raw)

    data.setdefault("clarify", None)
    return data


# --- offline estimator --------------------------------------------------
# A deterministic, key-free fallback so the whole UI is explorable without the
# LLM. Not accurate — plausible. Real estimates come from estimate_describe().

# name-keywords -> (unit, cal, protein, carb, fat). unit: "each" | "per100g" | "serving"
_FOOD_TABLE = [
    (("egg", "eggs"), ("each", 72, 6, 0, 5)),
    (("chicken breast", "chicken"), ("per100g", 165, 31, 0, 4)),
    (("rice",), ("per100g", 130, 3, 28, 0)),
    (("blueberr",), ("per100g", 57, 1, 14, 0)),
    (("banana",), ("each", 105, 1, 27, 0)),
    (("oat", "oats", "oatmeal", "porridge"), ("serving", 150, 5, 27, 3)),
    (("flourish", "pancake"), ("serving", 270, 23, 38, 2)),
    (("pb2", "powdered peanut"), ("serving", 60, 6, 5, 2)),
    (("peanut butter", "pb"), ("serving", 190, 7, 7, 16)),
    (("bread", "toast", "slice"), ("each", 80, 3, 15, 1)),
    (("avocado",), ("each", 240, 3, 12, 22)),
    (("greek yogurt", "yogurt"), ("serving", 130, 17, 9, 0)),
    (("protein shake", "protein powder", "whey", "shake"), ("serving", 150, 25, 5, 3)),
    (("salmon",), ("per100g", 208, 20, 0, 13)),
    (("steak", "beef"), ("per100g", 250, 26, 0, 15)),
    (("pasta", "spaghetti", "noodle"), ("per100g", 158, 6, 31, 1)),
    (("potato",), ("per100g", 87, 2, 20, 0)),
    (("salad",), ("serving", 120, 3, 12, 7)),
    (("burrito", "wrap"), ("each", 620, 26, 70, 26)),
    (("shawarma", "kebab", "gyro"), ("serving", 700, 45, 55, 38)),
    (("pizza slice", "pizza"), ("each", 285, 12, 36, 10)),
    (("coffee", "espresso", "americano"), ("each", 5, 0, 1, 0)),
    (("latte", "cappuccino"), ("each", 120, 8, 12, 5)),
    (("apple", "orange", "pear"), ("each", 95, 0, 25, 0)),
    (("almond", "nuts", "cashew"), ("serving", 170, 6, 6, 15)),
]

_QTY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(g|grams|gram|oz|ounces?|cups?|servings?|slices?|scoops?)?")


def _lookup(chunk: str):
    for keys, macros in _FOOD_TABLE:
        if any(k in chunk for k in keys):
            return macros
    return None


def _title(chunk: str) -> str:
    c = chunk.strip(" .,")
    return (c[:1].upper() + c[1:]) if c else "item"


def offline_estimate(text: str) -> dict:
    """Key-free heuristic estimate in the same contract shape as estimate_describe."""
    chunks = re.split(r",|\band\b|\bwith\b|\+|\bplus\b|;", text.lower())
    items = []
    matched = 0
    for raw_chunk in chunks:
        chunk = raw_chunk.strip()
        if not chunk:
            continue
        m = _QTY_RE.search(chunk)
        qty = float(m.group(1)) if m and m.group(1) else 1.0
        unit_word = (m.group(2) or "").rstrip("s") if m else ""
        found = _lookup(chunk)
        if found:
            matched += 1
            unit, cal, p, c, f = found
            if unit == "per100g":
                grams = qty if unit_word in ("g", "gram", "ounce", "oz", "") and qty > 10 else qty * 100
                if unit_word in ("ounce", "oz"):
                    grams = qty * 28.35
                factor = grams / 100.0
            elif unit == "each":
                factor = qty
            else:  # serving
                factor = qty if qty <= 6 else 1.0
            item = {
                "name": _title(chunk),
                "calories": round(cal * factor),
                "protein": round(p * factor, 1),
                "carb": round(c * factor, 1),
                "fat": round(f * factor, 1),
            }
        else:
            # generic fallback for an unrecognized item
            item = {"name": _title(chunk), "calories": 180, "protein": 8, "carb": 18, "fat": 8}
        items.append(item)

    if not items:
        items = [{"name": _title(text) or "meal", "calories": 400, "protein": 20, "carb": 45, "fat": 15}]

    total = {k: round(sum(it[k] for it in items), 1) for k in ("calories", "protein", "carb", "fat")}
    frac = matched / max(1, len(items))
    if frac >= 0.99:
        confidence, unc = "high", round(total["calories"] * 0.08)
    elif frac >= 0.5:
        confidence, unc = "medium", round(total["calories"] * 0.15)
    else:
        confidence, unc = "low", round(total["calories"] * 0.25)

    return {
        "items": items,
        "total": total,
        "confidence": confidence,
        "uncertainty_cal": unc,
        "assumptions": ["Offline estimate — standard portions assumed (add ANTHROPIC_API_KEY for the reasoning engine)"],
        "swing_factors": ["portion size", "cooking fat / hidden oil"],
        "clarify": None,
        "offline": True,
    }


def estimate(text: str) -> dict:
    """Try the real engine; fall back to the offline estimator without a key."""
    if os.environ.get("MACRO_OFFLINE") == "1":
        return offline_estimate(text)
    try:
        return estimate_describe(text)
    except Exception:
        return offline_estimate(text)
