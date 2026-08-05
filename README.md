# PlusMinus (±)

A macro tracker that **reasons about food instead of looking it up.**

Conventional trackers (MyFitnessPal, Cronometer) are databases with a logging UI: you
search a food table, pick a portion, and it sums the rows. That works for packaged food
and falls apart for whole-food meals, restaurant plates, and things you cook — exactly
the food most people actually eat.

PlusMinus replaces the food database with an **estimation engine**: a Claude-powered
module that turns a plain-language description or a photo into structured macros, *with a
confidence level and its stated assumptions.* Around that engine sits a lightweight store
and a dashboard, plus a coaching layer that adapts your targets and surfaces patterns over
time.

> Design principle: **surface uncertainty, never fake precision.** "≈620 cal, ±150, the
> swing factor is the sauce" is more honest and more useful than a confident wrong number.
> This honesty is the app's actual edge over the polished apps.

---

## What it does that normal macro apps don't

1. **Conversational logging** — "3 eggs, a serving of Flourish, 50g blueberries" → macros + running tally. No search, no barcodes, no dropdowns.
2. **Photo estimation with visible reasoning + uncertainty** — component breakdown, a total, and a ± range with the swing factors named.
3. **Component reconstruction of unlabeled food** — restaurant plates and homemade recipes reasoned from ingredients to a total.
4. **Remaining-budget-aware restaurant suggestions** — "you have 7g fat and 80g protein left — here's what fits nearby."
5. **Adaptive targets from observed patterns** — notices you consistently undershoot carbs / overshoot fat and proposes a split that matches reality.
6. **Activity-scaled daily targets** — rest days and training days get different numbers; eat to match output.
7. **Cross-day pattern coaching** — "your fat overages track restaurant meals almost perfectly."
8. **Trend-vs-noise weigh-in reads** — contextualizes the scale with activity and sodium instead of treating every bounce as fat.
9. **Built-in n=1 experiments** — e.g. meal-timing vs sleep quality, with conditions, confounders, and objective + subjective measures.
10. **A named food library built by talking** — "the loaf," "breakfast" become reusable entries.

The test for each: *could you build it without an LLM in the loop?* For most of these, no —
which is why they're novel and why this is a good Claude project rather than another CRUD app.

---

## Stack

- **Estimation engine:** Anthropic Messages API (Claude), structured-JSON output
- **Store:** SQLite (versioned targets, confidence fields, saved-meal recipes)
- **Backend:** Python (FastAPI) or Node — your call; schema is language-agnostic
- **Frontend:** chat-style logger + daily dashboard. Web (React) or TUI/CLI to start.
- **Optional integrations:** a Places API for restaurant reco; Fitbit export for the sleep layer

Mirrors the shape of the `journal-brain` project (API + SQLite + local storage), with an
estimation engine swapped in for the ingestion pipeline.

---

## Quickstart

```bash
# 1. install
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. seed a demo dataset (2 weeks of food/training/sleep/habits + a run program)
python scripts/seed_demo.py       # or: python scripts/init_db.py for an empty DB

# 3. run — then open http://127.0.0.1:8099
uvicorn app:api --port 8099
```

> **Runs without an API key.** The estimation engine falls back to a deterministic
> offline estimator so the whole app is explorable out of the box. To use the real
> Claude engine, `export ANTHROPIC_API_KEY=sk-...` before step 3.

**What's here:** a Claude-style **Chat** logger, **Daily** and **Weekly** eating views, a
**Training** section (Coach chat · weekly schedule · macrocycle programs), and a journal-style
**Habits** tracker with a sleep/recovery chart. Tab-key autocompletes chat suggestions.

See **SPEC.md** for the full architecture, data model, and phased roadmap.
See **prompts/estimation_engine.md** for the core system prompt (the heart of the app).
See **DESIGN_BRIEF.md** if you want to prototype the dashboard in Claude Design first.

---

## Roadmap (short form)

- **MVP** — conversational logging + estimation engine + daily dashboard with running tally
- **v2** — saved-meal library, photo logging, weigh-in trends, activity-scaled targets
- **v3** — adaptive targets, cross-day pattern coaching
- **v4** — restaurant reco, experiment framework, Fitbit import

Full detail in SPEC.md.
