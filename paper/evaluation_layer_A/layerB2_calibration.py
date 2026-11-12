"""Layer B2 calibration (exploratory, NOT part of the frozen experiments): ONE call to a local Ollama model with the production
personalization prompt (app.ai.prompts) for ONE profile, then validate_ai_plan on the output. Records speed, token counts, whether the
context was exceeded, and the validator verdict. Never overwrites outputs.
Usage: python layerB2_calibration.py --model qwen2.5:3b --num-ctx 32768 --num-predict 24000"""
import argparse, json, os, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
from app.schemas import RunnerProfileCreate  # noqa: E402
from app.rules.generator import generate_rule_based_plan  # noqa: E402
from app.ai.prompts import SYSTEM_PROMPT, build_user_prompt  # noqa: E402
from app.ai.llm_service import clean_json_response  # noqa: E402
from app.ai.validator import validate_ai_plan, AIPlanValidationError  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True); ap.add_argument("--num-ctx", type=int, default=32768); ap.add_argument("--num-predict", type=int, default=24000)
a = ap.parse_args()
P = json.load(open(os.path.join(HERE, "profiles_30.json"), encoding="utf-8"))["profiles"]
best = None
for p in P:
    if p["plan_duration_weeks"] != 8: continue
    prof = RunnerProfileCreate(**{k: v for k, v in p.items() if k not in ("id", "synthetic")})
    plan = generate_rule_based_plan(prof); n = len(json.dumps(plan["weeks"]))
    if best is None or n < best[0]: best = (n, p["id"], prof, plan)
n, pid, prof, plan = best
user = build_user_prompt(prof, plan)
out = os.path.join(HERE, f"layerB2_calibration_{a.model.replace(':', '_')}_{pid}.json")
if os.path.exists(out): sys.exit("output exists; not overwriting")
body = json.dumps({"model": a.model, "system": SYSTEM_PROMPT, "prompt": user, "stream": False, "keep_alive": "10m",
                   "options": {"num_ctx": a.num_ctx, "num_predict": a.num_predict}}).encode()
req = urllib.request.Request("http://127.0.0.1:11434/api/generate", body, {"Content-Type": "application/json"})
print("profile", pid, "baseline chars", n, "prompt chars", len(user) + len(SYSTEM_PROMPT), flush=True)
t = time.time()
r = json.load(urllib.request.urlopen(req, timeout=3600)); wall = time.time() - t
txt = r.get("response", "")
res = {"model": a.model, "profile": pid, "num_ctx": a.num_ctx, "num_predict": a.num_predict, "wall_s": round(wall, 1), "done_reason": r.get("done_reason"),
       "prompt_tokens": r.get("prompt_eval_count"), "output_tokens": r.get("eval_count"),
       "prompt_eval_s": round(r.get("prompt_eval_duration", 0) / 1e9, 1), "eval_s": round(r.get("eval_duration", 0) / 1e9, 1),
       "tok_per_s": round(r.get("eval_count", 0) / (r.get("eval_duration", 1) / 1e9), 1), "load_s": round(r.get("load_duration", 0) / 1e9, 1), "raw_chars": len(txt)}
try:
    parsed = clean_json_response(txt); res["json_ok"] = True
    try:
        validate_ai_plan(parsed, plan, prof); res["validator"] = "PASS"
    except AIPlanValidationError as e:
        res["validator"] = "FAIL: " + str(e)[:300]
    except Exception as e:  # noqa: BLE001
        res["validator"] = "ERROR: " + type(e).__name__ + " " + str(e)[:200]
except Exception as e:  # noqa: BLE001
    res["json_ok"] = False; res["json_error"] = type(e).__name__ + " " + str(e)[:200]
res["raw_head"] = txt[:400]; res["raw_tail"] = txt[-400:]
with open(out, "x", encoding="utf-8") as f: json.dump(res, f, indent=1, ensure_ascii=False)
print(json.dumps({k: v for k, v in res.items() if k not in ("raw_head", "raw_tail")}, indent=1))
