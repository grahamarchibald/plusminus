# PlusMinus v2 — Training + Nutrition + Recovery Dashboard

## Overview

PlusMinus v1 is a nutrition tracker with an estimation engine. v2 unifies nutrition with
training and recovery under a **calendar timeline**, where every day shows its activity,
macro targets, weight trend, and sleep — not as separate silos, but as one coherent story.

The key insight: **the calendar is the interface.** Macros, training, weight, and sleep all
hang off the day view. The app answers "what happened this week?" by showing activity →
nutrition → weight response → recovery, in sequence.

---

## Architecture (v2)

```
                    ┌──────────────────────────────────┐
                    │   CALENDAR WEEK VIEW (the hub)    │
                    │  - 7 days, each with target tier  │
                    │  - weight trend line underneath   │
                    │  - activity icons, macro status   │
                    └──────────────────────────────────┘
                              │
                ┌─────────────┼─────────────┐
                ▼             ▼             ▼
        ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
        │ DAILY DETAIL │ │ TRAINING LOG │ │ RECOVERY     │
        │ CARD         │ │              │ │ SUMMARY      │
        │ - macros     │ │ - type       │ │ - sleep      │
        │ - log items  │ │ - intensity  │ │ - macro hit  │
        │ - remaining  │ │ - burn est   │ │ - readiness  │
        └──────────────┘ └──────────────┘ └──────────────┘
                │
                ├─→ ESTIMATION ENGINE (unchanged from v1)
                └─→ STORE (SQLite, expanded)
```

The **calendar is the lens** through which you see everything. Tap a day to drill into
the full macro log, training detail, and recovery metrics for that day. The week view
stays high-level — activity type, macro status color, weight trend.

---

## 1. Expanded data model (SQLite)

### Core tables (from v1)

`foods`, `saved_meals`, `targets`, `weigh_ins`, `experiments`, `experiment_nights`
remain unchanged. (See SPEC.md.)

### New tables (v2)

#### Training

```sql
CREATE TABLE training (
  id          INTEGER PRIMARY KEY,
  day         TEXT NOT NULL UNIQUE,         -- YYYY-MM-DD
  type        TEXT NOT NULL,                -- climb | run | lift | dance | walk | mixed | rest
  duration_min INTEGER,
  intensity   TEXT,                         -- easy | moderate | hard | max
  detail      TEXT,                         -- "15km, 300m gain" / "1.5hr bouldering, max session"
  est_burn    REAL,                         -- estimated kcal
  subjective_difficulty INTEGER,           -- 1-10, user input
  notes       TEXT,
  created_at  TEXT
);
```

#### Sleep (Fitbit import + manual)

```sql
CREATE TABLE sleep (
  id          INTEGER PRIMARY KEY,
  day         TEXT NOT NULL UNIQUE,         -- YYYY-MM-DD
  duration_h  REAL,                         -- total sleep hours
  deep_min    INTEGER,                      -- deep sleep minutes
  rem_min     INTEGER,                      -- REM sleep minutes
  wakeups     INTEGER,                      -- number of awakenings
  fitbit_score INTEGER,                     -- 0-100 if imported
  manual_rating INTEGER,                    -- 1-10 subjective, if no Fitbit
  notes       TEXT,
  source      TEXT DEFAULT 'manual',        -- fitbit | manual | both
  created_at  TEXT
);
```

#### Recovery (computed daily)

```sql
CREATE TABLE recovery (
  id          INTEGER PRIMARY KEY,
  day         TEXT NOT NULL UNIQUE,         -- YYYY-MM-DD
  sleep_quality_score REAL,                 -- 0-100, normalized from sleep metrics
  macro_adequacy_pct REAL,                  -- % of target hit (avg protein/carb/fat)
  training_load REAL,                       -- est_burn from that day
  readiness_score REAL,                     -- 0-100, composite for the week ahead
  notes       TEXT,
  computed_at TEXT
);
```

---

## 2. How training drives macro targets (revised)

Previously, targets were resolved at log-time by checking the day's `activity` row.
Now it's cleaner: the `training` table is the source of truth.

**Resolution logic:**

```python
def resolve_target_for_day(day: str) -> dict:
    training = db.query("SELECT type FROM training WHERE day = ?", day)
    if not training:
        scope = "default"
    elif training.type in ("rest", "walk"):
        scope = "rest"
    elif training.type in ("climb", "run") and training.intensity == "max":
        scope = "high_output"
    else:
        scope = "training"
    
    target = db.query("SELECT * FROM targets WHERE scope = ?", scope)
    return target
```

So:
- **Rest or walk day** → 2350 cal / 180p / 215c / 75f
- **Moderate training** (climb easy, run easy, lift) → 2650 cal / 180p / 285c / 85f
- **Hard/max training** (long run, max climb) → 3000 cal / 180p / 300c / 100f

The dashboard shows the resolved target for each day under the activity icon.

---

## 3. Weight trend interpretation

A new read-on-demand based on the week's context:

```python
def contextualize_weight(current_weight: float, 
                         trend_7day_avg: float,
                         prior_week_avg: float,
                         week_data: dict) -> str:
    """
    Computes a contextual one-liner about the weigh-in.
    week_data = {calories, sodium, training_load, macros}
    """
    # Example logic
    change = current_weight - trend_7day_avg
    trend = trend_7day_avg - prior_week_avg
    
    if abs(change) > 2 and week_data['sodium_high']:
        context = f"Up {change:.1f}lb — likely sodium/water; trend still {trend:.1f}lb"
    elif change > 1 and week_data['carbs_high']:
        context = f"Up {change:.1f}lb — glycogen + refuel day; actual fat loss ~{loss_estimate:.1f}lb"
    elif trend < -0.5:
        context = f"Down {abs(trend):.1f}lb/wk. Deficit working; keep it up."
    else:
        context = f"Trend flat. Check deficit is real against recent meals."
    
    return context
```

This appears under the weight trend line on the week view.

---

## 4. Daily summary card

Appears when you tap a day on the calendar. Shows:

```
┌─────────────────────────────────────────┐
│ Tuesday, Aug 6                          │
├─────────────────────────────────────────┤
│ 🏔️  Max Climb (est. burn 1200 kcal)     │
│ Intensity: Hard (subjective 8/10)       │
│ Duration: 1.5 hours                     │
├─────────────────────────────────────────┤
│ Target: 3000 cal / 180p / 300c / 100f  │
│ Logged: 2890 cal / 175p / 295c / 105f  │
│ Status: 🟢 protein, 🟡 carbs, 🔴 fat   │
├─────────────────────────────────────────┤
│ Weight: 188.2 lb (trend -0.4 lb/wk)    │
│ Sleep: 7.2 hours, deep 95 min           │
│ Recovery: 78/100 (Good)                │
├─────────────────────────────────────────┤
│ [Tap to view full macro log] [Edit day] │
└─────────────────────────────────────────┘
```

The color coding:
- 🟢 (green) — within 5% of target
- 🟡 (yellow) — 5–15% off
- 🔴 (red) — >15% off

---

## 5. Week view calendar (the main screen)

```
┌─────────────────────────────────────────────────────────┐
│ Week of Aug 4, 2026                                    │
├─────────────────────────────────────────────────────────┤
│ MON   TUE   WED   THU   FRI   SAT   SUN                │
│ Rest  Max   Easy  Train Long  Rest  Light             │
│ 🟢    🟢    🟡    🟢     🟢    🟢    🔴              │
│ 188.8 —   188.4 188.2  188.0 187.8 187.9             │
│ 2350  3000 2650  2650   3000  2350  2650             │
├─────────────────────────────────────────────────────────┤
│ Weight trend: -0.45 lb/wk (on pace for 180-183 lb)    │
│ Avg daily deficit: 520 cal (good pacing for cut)       │
├─────────────────────────────────────────────────────────┤
│ [Tap a day for detail] [Log today] [Add training]      │
└─────────────────────────────────────────────────────────┘
```

**Each column shows:**
- Day name
- Activity type (icon + label, or "Rest")
- Macro status color (based on % of target hit)
- Weight (this day's weigh-in, or — if no data)
- Resolved target calories (small text, 2350/2650/3000)

**Interaction:**
- Tap a day → drill into the daily summary card
- Swipe left/right → move to prev/next week
- Long-press a day → quick-add training or edit macros

---

## 6. Recovery readiness score

Computed daily (after-the-fact, not predictive):

```python
def compute_recovery_score(day: str) -> float:
    sleep = db.query("SELECT * FROM sleep WHERE day = ?", day)
    macros = db.query("SELECT * FROM daily_log WHERE day = ?", day)  # aggregated
    training = db.query("SELECT * FROM training WHERE day = ?", day)
    targets = resolve_target_for_day(day)
    
    # Sleep component (0–100)
    sleep_score = min(100, (sleep.duration_h / 8) * 100)  # 8h = 100
    if sleep.deep_min > 90:
        sleep_score += 10  # bonus for deep sleep
    
    # Macro component (0–100)
    p_hit = (macros.protein / targets.protein) * 100
    c_hit = (macros.carb / targets.carb) * 100
    f_hit = (macros.fat / targets.fat) * 100
    macro_score = (p_hit + c_hit + f_hit) / 3
    macro_score = min(100, max(0, macro_score))  # clamp 0–100
    
    # Training load (0–100, lower = better recovery)
    load_score = max(0, 100 - training.est_burn)  # high burn = lower score
    
    # Composite (sleep 40%, macros 40%, training load 20%)
    readiness = (sleep_score * 0.4) + (macro_score * 0.4) + (load_score * 0.2)
    
    return readiness
```

Shows on the daily card as "Recovery: 78/100 (Good)" with a one-liner:
- 80+: "Fully recovered. Ready for intensity."
- 60–80: "Good recovery. Normal training OK."
- 40–60: "Partial recovery. Consider active recovery."
- <40: "Low recovery. Rest day recommended."

---

## 7. Weekly coaching summary (optional v2.1 feature)

A Claude call at week-end over the week's aggregated data:

```
Input to Claude:
{
  week: "Jul 29 — Aug 4",
  activity: [
    {day: "Mon", type: "rest"},
    {day: "Tue", type: "climb", intensity: "max", burn: 1200},
    ...
  ],
  macros: [
    {day: "Mon", cal: 2340, p: 178, c: 210, f: 75, status: "on target"},
    ...
  ],
  weight: [188.6, 188.2, 187.8, 187.9, 188.1, 187.6, 187.8],
  sleep_avg: 7.1,
  recovery_avg: 75
}

Output (Claude):
"You climbed 3x, hit macros 5 of 7 days, weight trend -0.4 lb/wk. Recovery strong (75/100).
The Saul's deli day spiked sodium but deficit is real. Ready to hold pace or push intensity
this week."
```

Not critical for v2 MVP, but the data structure supports it.

---

## 8. Fitbit integration (optional)

If available, import sleep data from Fitbit via their API (or user manual upload):

```python
def import_fitbit_sleep(user_fitbit_token: str, days: int = 7):
    fitbit_data = fitbit_api.get_sleep_summary(user_fitbit_token, days)
    for day_data in fitbit_data:
        day = day_data['dateOfSleep']
        db.insert('sleep', {
            'day': day,
            'duration_h': day_data['duration'] / 3600,
            'deep_min': day_data['deep'],
            'rem_min': day_data['rem'],
            'wakeups': day_data['awake'],
            'fitbit_score': day_data['efficiency'],
            'source': 'fitbit'
        })
```

For MVP, make it optional — users can manually log sleep (1–10 rating) if they don't have Fitbit.

---

## 9. Updated data flow

```
User logs food ("3 eggs, Flourish...")
       ↓
Estimation engine → JSON macros
       ↓
Save to foods table
       ↓
Aggregate daily_log (sum macros for the day)
       ↓
Resolve target for day (training type → scope → tier)
       ↓
Compute daily summary (status colors, remaining budget)
       ↓
Render in calendar week view + daily detail card
       ↓
Each morning: weigh-in → compute recovery score
       ↓
Weekly view updates with trend line + recovery avg
```

---

## 10. Revised roadmap

**MVP (v2.0):**
- Estimation engine (describe + photo mode)
- Daily macro dashboard (from v1)
- Training log (day, type, intensity, duration, burn)
- Week-view calendar showing activity + macro status + weight trend
- Daily summary card (activity, macros, weight, sleep, recovery score)
- Manual sleep logging (1–10 rating, or Fitbit if available)

**v2.1:**
- Adaptive-target proposals (from v1)
- Weekly pattern-detection coach (from v1)
- Fitbit full integration (automated sleep import)

**v2.2:**
- Restaurant reco (budget-aware)
- n=1 experiment framework (sleep × meal timing from the original spec)

**v3:**
- Mobile app (currently web/desktop)
- Sharing + coaching (share recovery trends with a coach/therapist)

---

## 11. Key design principles

1. **The calendar is the hub.** Everything navigates from the week view. Don't hide activity or recovery in separate tabs — they live on the day.
2. **Context over numbers.** "Up 2.5lb, but sodium-heavy day + 20km run; trend still -0.4lb/wk" beats "Weight: 191lb."
3. **Confidence + honesty.** Estimate ranges, recovery readiness scores, not fake precision.
4. **Activity drives targets.** A max climb day looks different from a rest day in one glance (different calorie tier, different recovery expectations).
5. **One narrative per week.** The summary card reads like a coach's note: "You trained hard, fueled well, slept OK, recovering on pace."

---

## 12. Open questions / build decisions

- **Fitbit auth flow:** simplify with OAuth or ask for token paste? (OAuth cleaner, requires backend.)
- **Weigh-in cadence:** daily, or prompt for 3–4x/week? (Daily is noisier but more granular; suggest rolling 7-day average always.)
- **Training burn estimation:** use a formula (METs × weight × time) or user input? (Formula + adjustment slider best.)
- **Recovery score thresholds:** the 40/60/80 cutoffs — tune once you have a week of data.
- **Timezone:** assume local time for all date fields. Store as YYYY-MM-DD without tz.
- **Offline first:** keep the store local + optional sync later (not MVP).

---

## 13. Claude Code handoff

**Prompt:**

```
Build PlusMinus v2: a unified training + nutrition + recovery dashboard.

Start with v1 MVP (estimation engine + daily macro dashboard), then add:
- Training log: type, intensity, duration, burn estimate
- Week-view calendar showing 7 days with activity, macro status, weight, target tier
- Daily summary card: drill-down view with macros, training, weight, sleep, recovery
- Recovery score: 0–100 computed from sleep + macro hit + training load
- Manual sleep logging + optional Fitbit import
- Weight trend line with context-aware one-liner

Data model: expand from SPEC.md with training, sleep, recovery tables.
UI: calendar week view as the main screen. Tap day → daily summary card.

Phasing:
1. Estimation engine + daily dashboard (working from SPEC.md)
2. Training log + calendar week view
3. Daily summary card + recovery scoring
4. Sleep import + coach summary (after MVP works)

See SPEC_v2.md for full schema, design, and decisions.
```

This gives Code the full picture without overwhelming it — the roadmap is clear, the schema is specified, the UI mockups are there.
