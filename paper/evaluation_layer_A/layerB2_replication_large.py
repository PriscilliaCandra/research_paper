"""Layer B2 replication, larger models (7-8B) (exploratory, NOT part of the frozen experiments): the first 5 profiles with an 8-week plan (profile order), 1 call per
model per profile, production prompt (app.ai.prompts), num_ctx 32768, num_predict 24000, default sampling, then validate_ai_plan.
Selection rule fixed before running. Never overwrites outputs."""
import csv, json, os, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
from app.schemas import RunnerProfileCreate  # noqa: E402
from app.rules.generator import generate_rule_based_plan  # noqa: E402
from app.ai.prompts import SYSTEM_PROMPT, build_user_prompt  # noqa: E402
from app.ai.llm_service import clean_json_response  # noqa: E402
from app.ai.validator import validate_ai_plan, AIPlanValidationError  # noqa: E402

MODELS = ["qwen2.5:7b", "llama3.1:8b"]
P = [p for p in json.load(open(os.path.join(HERE, "profiles_30.json"), encoding="utf-8"))["profiles"] if p["plan_duration_weeks"] == 8][:5]
out = os.path.join(HERE, "layerB2_replication_large.csv")
if os.path.exists(out): sys.exit("output exists; not overwriting")
rows = []
_prev = os.path.join(HERE, "layerB2_replication_large_partial_saved.csv")
if os.path.exists(_prev):
    rows = list(csv.DictReader(open(_prev, encoding="utf-8-sig")))
_done = {(r["model"], r["profile"]) for r in rows}
for model in MODELS:
    for p in P:
        if (model, p["id"]) in _done:
            continue
        prof = RunnerProfileCreate(**{k: v for k, v in p.items() if k not in ("id", "synthetic")})
        plan = generate_rule_based_plan(prof)
        body = json.dumps({"model": model, "system": SYSTEM_PROMPT, "prompt": build_user_prompt(prof, plan), "stream": False, "keep_alive": "10m",
                           "options": {"num_ctx": 32768, "num_predict": 24000}}).encode()
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", body, {"Content-Type": "application/json"})
        t = time.time()
        try:
            r = json.load(urllib.request.urlopen(req, timeout=900))
        except Exception as e:  # stop rule: > 15 min or server error -> stop and report
            print("STOP", model, p["id"], type(e).__name__, flush=True); break
        wall = time.time() - t
        txt = r.get("response", "")
        row = {"model": model, "profile": p["id"], "wall_s": round(wall, 1), "done_reason": r.get("done_reason"), "prompt_tokens": r.get("prompt_eval_count"),
               "output_tokens": r.get("eval_count"), "tok_per_s": round(r.get("eval_count", 0) / max(r.get("eval_duration", 1) / 1e9, 1e-9), 1)}
        try:
            parsed = clean_json_response(txt); row["json_ok"] = True; row["weeks_in_output"] = len(parsed.get("weeks", [])) if isinstance(parsed, dict) else None
            try:
                validate_ai_plan(parsed, plan, prof); row["validator"] = "PASS"
            except AIPlanValidationError as e:
                row["validator"] = "FAIL: " + str(e)[:160]
            except Exception as e:  # noqa: BLE001
                row["validator"] = "ERROR: " + type(e).__name__
        except Exception as e:  # noqa: BLE001
            row["json_ok"] = False; row["weeks_in_output"] = None; row["validator"] = "NO_JSON: " + type(e).__name__
        rows.append(row); print(row, flush=True)
        with open(out + ".partial", "w", newline="", encoding="utf-8-sig") as pf:
            pw = csv.DictWriter(pf, fieldnames=list(rows[0])); pw.writeheader(); pw.writerows(rows)
with open(out, "x", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print("written", out)
