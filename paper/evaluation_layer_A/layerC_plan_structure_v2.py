"""Layer C (plan structure): structure of the rule engine's plans for the 30 synthetic profiles, grouped by (race distance, level).
Deterministic, no network. Writes layerC_plan_structure_ours_v2_revised_engine.csv (new file, never overwritten) and prints group summaries."""
import csv, json, os, statistics as st, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
from app.schemas import RunnerProfileCreate  # noqa: E402
from app.rules.generator import generate_rule_based_plan  # noqa: E402

P = json.load(open(os.path.join(HERE, "profiles_30.json"), encoding="utf-8"))["profiles"]
rows = []
for p in P:
    plan = generate_rule_based_plan(RunnerProfileCreate(**{k: v for k, v in p.items() if k not in ("id", "synthetic")}))
    wk = plan["weeks"]
    tot = [w["weekly_mileage_km"] for w in wk]
    runs = [sum(1 for x in w["workouts"] if x["distance_km"] > 0) for w in wk]
    long_ = [max((x["distance_km"] for x in w["workouts"]), default=0) for w in wk]
    cut = [w["week_number"] for w in wk if "Cutback" in w["phase"]]
    taper = [w["week_number"] for w in wk if "Taper" in w["phase"]]
    inc = [(tot[i] / tot[i - 1] - 1) * 100 for i in range(1, len(tot)) if tot[i] > tot[i - 1] and tot[i - 1] > 0 and wk[i]["phase"] != "Taper Phase"]
    rows.append({"id": p["id"], "distance": p["target_race_distance"], "level": p["experience_level"], "weeks": len(wk),
                 "start_km": p["current_weekly_mileage"], "run_days_mode": st.mode(runs), "peak_weekly_km": max(tot),
                 "peak_week": tot.index(max(tot)) + 1, "peak_long_run_km": max(long_), "cutback_weeks": ";".join(map(str, cut)),
                 "taper_weeks": len(taper), "max_weekly_increase_pct": round(max(inc), 1) if inc else 0, "weekly_km": ";".join(map(str, tot))})
path = os.path.join(HERE, "layerC_plan_structure_ours_v2_revised_engine.csv")
with open(path, "x", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
groups = {}
for r in rows: groups.setdefault((r["distance"], r["level"]), []).append(r)
for k in sorted(groups):
    g = groups[k]
    print(k, "n", len(g), "weeks", sorted({r["weeks"] for r in g}), "run days", sorted({r["run_days_mode"] for r in g}),
          "peak km", min(r["peak_weekly_km"] for r in g), "-", max(r["peak_weekly_km"] for r in g),
          "long run", min(r["peak_long_run_km"] for r in g), "-", max(r["peak_long_run_km"] for r in g),
          "taper wks", sorted({r["taper_weeks"] for r in g}), "cutbacks", sorted({r["cutback_weeks"] for r in g}),
          "max inc %", max(r["max_weekly_increase_pct"] for r in g))
print("overall max weekly increase %", max(r["max_weekly_increase_pct"] for r in rows))
