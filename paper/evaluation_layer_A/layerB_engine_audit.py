"""Layer B (part B1): does the rule engine's own plan satisfy the rules it documents? Independent check, no network, no LLM.
Rules (rules/generator.py, Table II): week-to-week increase <= 10% (build weeks); cutback week = 0.85 x previous reference;
taper weeks = 0.85 and 0.55 x starting volume. Realized weekly volume = sum of the daily workouts (not the planned target)."""
import csv, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
from app.schemas import RunnerProfileCreate  # noqa: E402
from app.rules.generator import generate_rule_based_plan  # noqa: E402
P = json.load(open(os.path.join(HERE, "profiles_30.json"), encoding="utf-8"))["profiles"]
rows = []; viol = {"build_gt10": 0, "cutback_not_085": 0, "taper_not_085_055": 0, "week1_gt_108x": 0, "session_gt_50pct_week": 0}
for p in P:
    base = p["current_weekly_mileage"]
    plan = generate_rule_based_plan(RunnerProfileCreate(**{k: v for k, v in p.items() if k not in ("id", "synthetic")}))
    wk = plan["weeks"]; tot = [w["weekly_mileage_km"] for w in wk]; n = len(wk)
    v = {k: 0 for k in viol}; maxinc = 0.0; maxshare = 0.0
    for i, w in enumerate(wk):
        ph = w["phase"]
        mx = max(x["distance_km"] for x in w["workouts"]); maxshare = max(maxshare, mx / tot[i]) if tot[i] else maxshare
        if mx > 0.5 * tot[i]: v["session_gt_50pct_week"] += 1
        if "Progressive" in ph and i > 0:
            inc = tot[i] / tot[i - 1] - 1; maxinc = max(maxinc, inc)
            if inc > 0.10 + 0.003: v["build_gt10"] += 1
        if "Cutback" in ph:
            ref = tot[i - 1]
            if abs(tot[i] / ref - 0.85) > 0.03: v["cutback_not_085"] += 1
        if i == 0 and tot[0] > 1.08 * base + 0.15: v["week1_gt_108x"] += 1
    if abs(tot[-2] / base - 0.85) > 0.05 or abs(tot[-1] / base - 0.55) > 0.05: v["taper_not_085_055"] += 1
    for k in viol: viol[k] += v[k]
    rows.append({"id": p["id"], "weeks": n, "start_km": base, "week1_km": tot[0], "week1_ratio": round(tot[0] / base, 2), "max_build_increase_pct": round(maxinc * 100, 1),
                 "max_single_session_share_of_week": round(maxshare, 2), **{k: v[k] for k in viol}})
path = os.path.join(HERE, "layerB_engine_audit.csv")
with open(path, "x", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print("violation counts over all weeks/profiles:", viol)
print("profiles with any violation:", sum(1 for r in rows if any(r[k] for k in viol)), "of", len(rows))
print("max build increase %:", max(r["max_build_increase_pct"] for r in rows), " max week1 ratio:", max(r["week1_ratio"] for r in rows))
print("max single-session share of week:", max(r["max_single_session_share_of_week"] for r in rows))
for r in rows:
    if any(r[k] for k in viol): print({k: r[k] for k in ("id", "week1_ratio", "max_build_increase_pct", "max_single_session_share_of_week", *viol)})
