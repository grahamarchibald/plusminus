# MacroCoach — Design Brief (for Claude Design)

Use this if you want to prototype the interface before building. You don't need to design
the whole app — most of it is a chat box. Prototype the **one screen that benefits from
visual iteration: the daily dashboard.** Everything else, let Claude Code scaffold.

## What makes this dashboard different
It has to show a fourth dimension normal trackers don't: **confidence.** Every logged item
carries an uncertainty. The design challenge is making a day's macros glanceable *while*
honestly conveying that some entries are precise and some are ballpark.

## Screen 1 — Daily dashboard (prototype this)
Must show, at a glance:
- **Four macros** (calories, protein, carb, fat) as progress toward the day's target — rings or bars
- **The resolved target for today** — note it changes with activity (rest vs training day), so show which scope is active
- **Remaining budget** per macro — the number the user acts on ("44g fat left")
- **Confidence** — some visual treatment (a fuzzy/hatched segment, a ± label, a muted tone) that marks how much of today's total is estimated vs known. This is the distinctive bit.
- **Running list** of today's items, each with its own confidence marker, tappable to edit

Explore: how do you show "protein is hit, carbs are 80g short, fat is over" *instantly*?
Color-by-status (under / on-track / over) probably beats raw fills.

## Screen 2 — Log interaction (light prototype)
- A chat/compose input at the bottom ("3 eggs and a serving of Flourish…")
- The response: the estimate appears as a card with the component breakdown, the ± range,
  and the assumptions — with a one-tap **confirm / adjust**. The confirm step matters; the
  estimate is a proposal, not a fact.
- Photo attach → same card, with the component breakdown from the image.

## Screen 3 — Weigh-in / trend (optional)
- Weight over time as a **trend line with a rolling average**, single days de-emphasized.
- A one-line contextual read under the chart ("up 1.2 lb — consistent with yesterday's long run + salt; trend still down").

## Visual principles
- **Uncertainty-forward, not precision-forward.** The UI should feel comfortable saying "about." Fake-precise competitors look confident; this looks honest.
- **The remaining number is the hero**, not the consumed number — people act on what's left.
- **Calm, not gamified.** No streaks-shame, no red alarms. The coach tone is analytical and kind.

## Handoff
Once the dashboard direction feels right, take it into Claude Code and build against SPEC.md.
The design's job is to settle the confidence-visualization question before you write the
component.
