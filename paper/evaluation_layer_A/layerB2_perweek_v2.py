"""Layer B2, per-week variant v2 (exploratory, NOT part of the frozen experiments, NOT the production pipeline).
Same production prompt (app.ai.prompts.build_user_prompt) and same validator (validate_ai_plan), but each call carries ONE week of the
deterministic baseline and the output is validated against that week only. Models: qwen2.5:3b, llama3.2:3b. Profiles: the same five
8-week profiles as the full-plan replication (P02, P07, P09, P12, P13). num_ctx 8192 (the frozen context), num_predict 4096, default sampling.
Writes layerB2_perweek_v2.csv.partial after every call; resumes from it; final layerB2_perweek_v2.csv is never overwritten.
v2 change vs layerB2_perweek.py: the example 'week_number: 1' in the prompt template is replaced by the actual week number of the call (the first run was dominated by models copying 1); raw outputs are saved to layerB2_perweek_v2_raw.jsonl.
Stop rule: a call > 600 s or a server error stops the run."""
import csv, json, os, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
from app.schemas import RunnerProfileCreate  # noqa: E402
from app.rules.generator import generate_rule_based_plan  # noqa: E402
from app.ai.prompts import SYSTEM_PROMPT, build_user_prompt  # noqa: E402
from app.ai.llm_service import clean_json_response  # noqa: E402
from app.ai.validator import validate_ai_plan, AIPlanValidationError  # noqa: E402

MODELS = ["qwen2.5:3b", "llama3.2:3b"]
IDS = ["P02", "P07", "P09", "P12", "P13"]
P = [p for p in json.load(open(os.path.join(HERE, "profiles_30.json"), encoding="utf-8"))["profiles"] if p["id"] in IDS]
final = os.path.join(HERE, "layerB2_perweek_v2.csv"); part = final + ".partial"
if os.path.exists(final): sys.exit("final output exists; not overwriting")
rows = list(csv.DictReader(open(part, encoding="utf-8-sig"))) if os.path.exists(part) else []
done = {(r["model"], r["profile"], int(r["week"])) for r in rows}
stop = False
for model in MODELS:
    for p in P:
        prof = RunnerProfileCreate(**{k: v for k, v in p.items() if k not in ("id", "synthetic")})
        plan = generate_rule_based_plan(prof)
        for wk in plan["weeks"]:
            wn = wk["week_number"]
            if (model, p["id"], wn) in done: continue
            one = {**plan, "weeks": [wk]}  # same plan fields (vdot, paces, ...) as production; only the weeks list is cut to one week
            prompt = build_user_prompt(prof, one)
            _tpl = '"week_number": 1,' + chr(10) + '      "phase": "<exact phase from baseline week>"'
            assert _tpl in prompt, "template marker not found"
            prompt = prompt.replace(_tpl, '"week_number": %d,' % wn + chr(10) + '      "phase": "<exact phase from baseline week>"')
            body = json.dumps({"model": model, "system": SYSTEM_PROMPT, "prompt": prompt, "stream": False, "keep_alive": "10m",
                               "options": {"num_ctx": 8192, "num_predict": 4096}}).encode()
            req = urllib.request.Request("http://127.0.0.1:11434/api/generate", body, {"Content-Type": "application/json"})
            t = time.time()
            try:
                r = json.load(urllib.request.urlopen(req, timeout=600))
            except Exception as e:  # noqa: BLE001
                print("STOP", model, p["id"], wn, type(e).__name__, flush=True); stop = True; break
            wall = time.time() - t; txt = r.get("response", "")
            with open(os.path.join(HERE, "layerB2_perweek_v2_raw.jsonl"), "a", encoding="utf-8") as rf:
                rf.write(json.dumps({"model": model, "profile": p["id"], "week": wn, "response": txt}, ensure_ascii=False) + chr(10))
            row = {"model": model, "profile": p["id"], "week": wn, "wall_s": round(wall, 1), "done_reason": r.get("done_reason"),
                   "prompt_tokens": r.get("prompt_eval_count"), "output_tokens": r.get("eval_count")}
            try:
                parsed = clean_json_response(txt); row["json_ok"] = True
                try:
                    validate_ai_plan(parsed, one, prof); row["validator"] = "PASS"
                except AIPlanValidationError as e:
                    row["validator"] = "FAIL: " + str(e)[:160]
                except Exception as e:  # noqa: BLE001
                    row["validator"] = "ERROR: " + type(e).__name__
            except Exception as e:  # noqa: BLE001
                row["json_ok"] = False; row["validator"] = "NO_JSON: " + type(e).__name__
            rows.append(row); print(row, flush=True)
            with open(part, "w", newline="", encoding="utf-8-sig") as pf:
                pw = csv.DictWriter(pf, fieldnames=list(rows[0])); pw.writeheader(); pw.writerows(rows)
        if stop: break
    if stop: break
if not stop:
    with open(final, "x", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print("written", final)
