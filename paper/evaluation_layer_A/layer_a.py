"""Layer A of the accuracy evaluation: accuracy of the pace computation (VDOT and training paces).

Exploratory; NOT part of the frozen v2.7 / FZ-v2 / FZ-v3 experiments. Synthetic profiles only. Ground truth = the Daniels-Gilbert equations
re-implemented here independently (bisection instead of the quadratic used by the engine), plus a few published anchor values.
Modes:
  engine   compare backend rule engine paces with the independent reference for the 30 profiles (no network)
  llm      ask a local Ollama model for the same paces (LLM-only arm), R repetitions per profile; compare with the reference
Usage: python layer_a.py engine
       python layer_a.py llm --model qwen2.5:3b --reps 3
Outputs go to this folder as new files (never overwritten).
"""
import argparse, csv, json, math, os, re, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
from app.rules.vdot import calculate_vdot_from_race, calculate_training_paces  # noqa: E402
from app.schemas import parse_time_to_seconds  # noqa: E402

ZONES = ("easy_fast", "easy_slow", "marathon", "threshold", "interval", "repetition")
FRAC = {"easy_slow": 0.65, "easy_fast": 0.74, "marathon": 0.80, "threshold": 0.88, "interval": 0.98, "repetition": 1.05}


def ref_vdot(dist_m, t_s):
    tm = t_s / 60.0
    v = dist_m / tm
    vo2 = -4.60 + 0.182258 * v + 0.000104 * v * v
    f = 0.8 + 0.1894393 * math.exp(-0.012778 * tm) + 0.2989558 * math.exp(-0.1932605 * tm)
    return vo2 / f


def ref_pace(vo2):
    lo, hi = 50.0, 700.0  # m/min, bisection on vo2(v) = target
    for _ in range(200):
        mid = (lo + hi) / 2
        if -4.60 + 0.182258 * mid + 0.000104 * mid * mid < vo2:
            lo = mid
        else:
            hi = mid
    return 60000.0 / ((lo + hi) / 2)  # sec per km


def ref_paces(pb_s):
    vd = ref_vdot(5000.0, pb_s)
    return vd, {z: ref_pace(vd * FRAC[z]) for z in ZONES}


def parse_pace(txt):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", txt)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def engine_paces(pb_s):
    vd = calculate_vdot_from_race(5000.0, pb_s)
    tp = calculate_training_paces(vd)
    e = re.findall(r"(\d{2}:\d{2})", tp["easy"]["pace_range"])
    out = {"easy_fast": parse_pace(e[0]), "easy_slow": parse_pace(e[1])}
    for z, key in (("marathon", "marathon"), ("threshold", "threshold"), ("interval", "interval"), ("repetition", "repetition")):
        out[z] = parse_pace(re.findall(r"(\d{2}:\d{2})", tp[key]["pace_range"])[0])
    return vd, out


def load_profiles():
    return json.load(open(os.path.join(HERE, "profiles_30.json"), encoding="utf-8"))["profiles"]


def new_path(name):
    p = os.path.join(HERE, name)
    if os.path.exists(p):
        sys.exit(f"{name} exists; not overwriting")
    return p


def mode_engine():
    rows = []
    for p in load_profiles():
        pb = parse_time_to_seconds(p["pb_5k"])
        vd_e, eng = engine_paces(pb)
        vd_r, ref = ref_paces(pb)
        row = {"id": p["id"], "pb_5k": p["pb_5k"], "engine_vdot": vd_e, "ref_vdot": round(vd_r, 3), "vdot_abs_err": round(abs(vd_e - vd_r), 3)}
        for z in ZONES:
            row[f"engine_{z}"] = eng[z]
            row[f"ref_{z}"] = round(ref[z], 2)
            row[f"err_{z}_s"] = round(eng[z] - ref[z], 2)
        rows.append(row)
    path = new_path("layerA_engine_vs_reference.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print("written", path)
    for z in ZONES:
        errs = [abs(r[f"err_{z}_s"]) for r in rows]
        print(f"{z:11s} max |err| {max(errs):.2f} s/km, mean {sum(errs)/len(errs):.2f}")
    print("max vdot abs err", max(r["vdot_abs_err"] for r in rows))


PROMPT = ("A runner's recent 5K race time is {pb}. Using Jack Daniels' VDOT training system, give the runner's training paces in minutes:seconds "
          "per kilometre. Reply in exactly this format, one line each, with no other text:\n"
          "Easy: m:ss-m:ss\nMarathon: m:ss\nThreshold: m:ss\nInterval: m:ss\nRepetition: m:ss")


def ask(model, prompt, url="http://127.0.0.1:11434"):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "keep_alive": "30m",
                       "options": {"num_ctx": 16384, "num_predict": 512}}).encode()
    req = urllib.request.Request(url + "/api/generate", body, {"Content-Type": "application/json"})
    t = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=600))
    return r.get("response", ""), time.time() - t


def parse_llm(txt):
    out = {}
    m = re.search(r"Easy:\s*(\d{1,2}:\d{2})\s*[-\u2013to ]+\s*(\d{1,2}:\d{2})", txt, re.I)
    if m:
        a, b = parse_pace(m.group(1)), parse_pace(m.group(2))
        out["easy_fast"], out["easy_slow"] = min(a, b), max(a, b)
    for z, name in (("marathon", "Marathon"), ("threshold", "Threshold"), ("interval", "Interval"), ("repetition", "Repetition")):
        m = re.search(name + r":\s*(\d{1,2}:\d{2})", txt, re.I)
        if m:
            out[z] = parse_pace(m.group(1))
    return out


def mode_llm(model, reps):
    rows = []
    for p in load_profiles():
        pb = parse_time_to_seconds(p["pb_5k"])
        _, ref = ref_paces(pb)
        for r in range(1, reps + 1):
            txt, dt = ask(model, PROMPT.format(pb=p["pb_5k"]))
            got = parse_llm(txt)
            row = {"id": p["id"], "rep": r, "pb_5k": p["pb_5k"], "latency_s": round(dt, 1), "parsed_zones": len(got), "raw": txt.replace("\n", " | ")[:300]}
            for z in ZONES:
                row[f"llm_{z}"] = got.get(z)
                row[f"err_{z}_s"] = round(got[z] - ref[z], 2) if z in got else None
            rows.append(row)
            print(p["id"], r, row["parsed_zones"], f"{dt:.1f}s", flush=True)
    path = new_path(f"layerA_llm_{model.replace(':', '_')}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print("written", path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["engine", "llm"])
    ap.add_argument("--model", default="qwen2.5:3b")
    ap.add_argument("--reps", type=int, default=3)
    a = ap.parse_args()
    mode_engine() if a.mode == "engine" else mode_llm(a.model, a.reps)
