"""Populate a rich demo dataset so the whole UI is explorable without the LLM.

Usage:  python scripts/seed_demo.py
Wipes macro.db and seeds ~2 weeks of training, weigh-ins, sleep, food, and habit ticks.
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


def d(offset: int) -> str:
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


def main() -> None:
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()

    # 14 days of weigh-ins trending gently down (with noise)
    weights = [189.4, 189.1, 189.3, 188.8, 188.9, 188.5, 188.6,
               188.8, 188.6, 188.4, 188.2, 188.0, 187.8, 187.9]
    for i, w in enumerate(weights):
        db.insert_weigh_in(d(-13 + i), w)

    # training across the two weeks
    plan = {
        -13: ("rest", "easy", 0, None),
        -12: ("climb", "max", 90, "bouldering, max session"),
        -11: ("lift", "moderate", 60, "push day"),
        -10: ("run", "easy", 35, "5k shakeout"),
        -9: ("run", "max", 95, "20k long run"),
        -8: ("rest", "easy", 0, None),
        -7: ("walk", "easy", 50, "hike, 300m gain"),
        -6: ("climb", "hard", 80, "lead climbing"),
        -5: ("lift", "moderate", 55, "pull day"),
        -4: ("dance", "moderate", 60, "class"),
        -3: ("run", "moderate", 45, "tempo"),
        -2: ("climb", "max", 100, "comp prep, max session"),
        -1: ("rest", "easy", 0, None),
        0: ("lift", "moderate", 60, "full body"),
    }
    for off, (typ, inten, dur, detail) in plan.items():
        e = {"day": d(off), "type": typ, "intensity": inten, "duration_min": dur}
        if detail:
            e["detail"] = detail
        if typ != "rest":
            e["subjective_difficulty"] = {"easy": 4, "moderate": 6, "hard": 8, "max": 9}[inten]
        db.insert_training(e)

    # sleep, most nights
    sleeps = {-13: (7.4, 100, 8), -12: (7.2, 95, 8), -11: (6.4, 60, 6), -10: (7.6, 110, 9),
              -9: (6.1, 55, 5), -8: (8.0, 120, 9), -7: (7.3, 90, 8), -6: (6.8, 75, 7),
              -5: (7.1, 88, 7), -4: (7.5, 100, 8), -3: (6.5, 62, 6), -2: (7.0, 85, 7), -1: (7.8, 115, 9)}
    for off, (h, deep, rating) in sleeps.items():
        db.insert_sleep({"day": d(off), "duration_h": h, "deep_min": deep, "manual_rating": rating})

    # food on the last several days (drives status colors + rings)
    meals = {
        -6: [("Breakfast: 3 eggs, oats, banana", 620, 32, 78, 20, "high"),
             ("Lunch: chicken, rice, salad", 720, 55, 80, 18, "high"),
             ("Dinner: salmon, potato, greens", 640, 44, 55, 26, "medium")],
        -5: [("Big breakfast", 700, 40, 70, 26, "high"),
             ("Deli sandwich", 780, 34, 60, 42, "low"),
             ("Dinner: pasta + beef", 820, 46, 92, 24, "medium")],
        -2: [("Pre-climb oats + shake", 520, 32, 70, 10, "high"),
             ("Post-climb burrito", 620, 26, 70, 26, "medium"),
             ("Dinner: chicken, rice, avocado", 900, 58, 88, 34, "medium")],
        -1: [("Breakfast", 560, 30, 62, 18, "high"),
             ("Lunch: greek yogurt + fruit + nuts", 470, 24, 46, 20, "medium"),
             ("Dinner: steak + potato", 760, 52, 48, 34, "medium")],
        0: [("3 eggs, a serving of Flourish, 50g blueberries", 514, 44, 45, 17, "high")],
    }
    for off, items in meals.items():
        for name, cal, p, c, f, conf in items:
            db.insert_food({"name": name, "calories": cal, "protein": p, "carb": c, "fat": f,
                            "confidence": conf, "uncertainty_cal": {"high": 40, "medium": 90, "low": 150}[conf],
                            "day": d(off)})

    # habit ticks — realistic completion across the two weeks
    habits = db.list_habits()  # 20,000 steps, Read, Movement, Time with a friend
    import itertools
    patterns = {
        habits[0]["name"]: [1, 1, 0, 1, 1, 1, 0, 1, 1, 0, 1, 1, 1, 1],  # steps
        habits[1]["name"]: [1, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1, 0, 1, 1],  # read
        habits[2]["name"]: [1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1, 1],  # movement
        habits[3]["name"]: [0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 1, 0, 1],  # friend
    }
    for h in habits:
        pat = patterns.get(h["name"], [])
        for i, done in enumerate(pat):
            db.set_habit(h["id"], d(-13 + i), bool(done))

    # a sample 10-week running macrocycle starting this Monday
    prog = db.create_program(db.running_program(weeks=10, base=30, pct=0.10), start_monday=db.week_start())

    print("Seeded demo data:")
    print("  weigh-ins:", len(weights), "| training days:", len(plan), "| sleep nights:", len(sleeps))
    print("  habits:", [h["name"] for h in habits])
    print("  program:", prog["name"], "id", prog["id"])
    print("  today =", d(0))


if __name__ == "__main__":
    main()
