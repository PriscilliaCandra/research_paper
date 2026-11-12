"""Summaries for layer A: LLM-only accuracy and the published anchor check. Reads the CSV files in this folder; prints numbers only."""
import csv, glob, os, statistics as st, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import layer_a as L  # noqa: E402

TOL = {z: 10 for z in ("easy_fast", "easy_slow", "marathon", "threshold", "interval", "repetition")}  # owner decision 2026-10-27: +-10 s/km, all zones


def f(x):
    return None if x in ("", None) else float(x)


for path in sorted(glob.glob(os.path.join(HERE, "layerA_llm_*.csv"))):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    n = len(rows)
    print("\n==", os.path.basename(path), "calls", n, "profiles", len({r['id'] for r in rows}))
    print("fully parsed (6 zones):", sum(1 for r in rows if r["parsed_zones"] == "6"), "of", n)
    print("latency mean s", round(st.mean(float(r["latency_s"]) for r in rows), 1))
    for z in L.ZONES:
        errs = [f(r[f"err_{z}_s"]) for r in rows if f(r[f"err_{z}_s"]) is not None]
        if not errs:
            print(f"{z:11s} no parsed values"); continue
        within = sum(1 for e in errs if abs(e) <= TOL[z])
        print(f"{z:11s} n={len(errs):3d} mean signed {st.mean(errs):7.1f}  MAE {st.mean(abs(e) for e in errs):6.1f}  median |e| {st.median(abs(e) for e in errs):6.1f}  within +-{TOL[z]}s: {within}/{len(errs)} ({100*within/len(errs):.0f}%)")
    # stability across the repetitions of the same profile (identical parsed paces)
    by = {}
    for r in rows:
        by.setdefault(r["id"], []).append(tuple(r[f"llm_{z}"] for z in L.ZONES))
    same = sum(1 for v in by.values() if len(set(v)) == 1)
    print("profiles whose repetitions gave identical paces:", same, "of", len(by))

print("\n== anchor check (published values, secondary web source; verify against the book)")
pb = 19 * 60 + 57
vd, eng = L.engine_paces(pb)
pub = {"easy_fast": 5 * 60 + 7, "easy_slow": 5 * 60 + 39, "marathon": 4 * 60 + 31, "threshold": 4 * 60 + 16, "interval": 3 * 60 + 55, "repetition": 3 * 60 + 40}
print("engine VDOT for 5K 19:57:", vd, "(published: 50)")
for z in L.ZONES:
    print(f"{z:11s} engine {eng[z]//60}:{eng[z]%60:02d}  published {pub[z]//60}:{pub[z]%60:02d}  diff {eng[z]-pub[z]:+d} s/km")
