"""Create the 30 synthetic runner profiles for the accuracy evaluation (seeded, stratified). All profiles are SYNTHETIC; none is a real person.
Usage: python make_profiles.py   (writes profiles_30.json next to this file; refuses to overwrite)"""
import json, os, random, sys
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "profiles_30.json")
SEED = 20261026
if os.path.exists(OUT):
    sys.exit("profiles_30.json exists; not overwriting")
rng = random.Random(SEED)
mmss = lambda s: f"{int(s)//60:02d}:{int(s)%60:02d}"
hhmmss = lambda s: f"{int(s)//3600:d}:{(int(s)%3600)//60:02d}:{int(s)%60:02d}"
LEVELS = {  # level: (5K PB seconds range, weekly km range)
    "Beginner": ((1680, 2220), (10, 25)),
    "Intermediate": ((1320, 1700), (25, 50)),
    "Advanced": ((1020, 1300), (50, 90)),
}
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
RACES = ["5K", "10K", "Half Marathon"]
profiles = []
i = 0
for level, ((lo, hi), (mlo, mhi)) in LEVELS.items():
    for k in range(10):
        i += 1
        pb = rng.randrange(lo, hi + 1, 5)
        race = RACES[(i - 1) % 3]
        imp = rng.uniform(0.02, 0.06)
        base = {"5K": pb, "10K": pb * 2.08, "Half Marathon": pb * 4.5}[race]
        target = round(base * (1 - imp))
        days = rng.choice([3, 4, 4, 5, 6]) if level != "Beginner" else rng.choice([3, 3, 4])
        profiles.append({
            "id": f"P{i:02d}", "synthetic": True,
            "age": rng.randrange(19, 55), "gender": rng.choice(["Male", "Female"]),
            "experience_level": level, "pb_5k": mmss(pb), "target_race_distance": race,
            "target_race_time": mmss(target) if race != "Half Marathon" else hhmmss(target),
            "current_weekly_mileage": round(rng.uniform(mlo, mhi), 1),
            "training_days_per_week": days, "preferred_training_days": sorted(rng.sample(DAYS, days), key=DAYS.index),
            "plan_duration_weeks": rng.choice([8, 12, 16]),
            "injury_limitations": "mild knee soreness after long runs" if k % 4 == 3 else None,
            "easy_run_pace": mmss(round(pb / 5 * 1.28)),
        })
json.dump({"seed": SEED, "note": "synthetic profiles; stratified 10 per experience level; races cycle 5K/10K/Half Marathon; fields follow backend RunnerProfileCreate",
           "profiles": profiles}, open(OUT, "w", encoding="utf-8", newline="\n"), indent=1, ensure_ascii=False)
print("written", OUT, len(profiles))
