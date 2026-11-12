"""LOCAL FEASIBILITY CHECK of a local Ollama model (llama3.2:3b) on SYNTHETIC prompts. Written 2026-10-16 on the owner's approved plan. NOT a pilot, NOT part of the
experiment, NOT evidence about any research hypothesis, and decides nothing by itself: its only output is one of FEASIBILITY_PROCEED / OWNER_DECISION /
STOP_NOT_FEASIBLE / INCONCLUSIVE, using criteria that are fixed in this file BEFORE any run (the SHA-256 of this file is printed and stored).
Prompts: the frozen generator and renderer (step8/s8_construct.py, step8/s8_gates.py) on base values that do not intersect the main or pilot registry (asserted at run
time); the frozen three-field response format is used as rendered (NOT JSON). 16 templates x conditions A-D = 64 calls, plus 16 repeats = 80 counted calls, plus 2 probes
(not counted) that show what the server reports for a truncated and a normal answer.
Ollama: loopback only (127.0.0.1, localhost, ::1), standard library only, no credentials, no environment variables, one sequential call at a time, no retry. Options sent:
num_ctx 16384, num_predict 8192, keep_alive 30m, stream false; temperature, seed, top_k and top_p are NOT sent (Ollama defaults); the probe sends num_predict 8.
Output: a NEW folder (never overwrites): provenance.json, prompts_manifest.json, calls.jsonl (synthetic prompts only), summary.json. The terminal shows numbers and status
words only: never a prompt, a response or a raw record.
Usage: python -B -I local_feasibility.py --out <new folder> [--url http://127.0.0.1:11434] [--model llama3.2:3b] [--dry-run]"""
import argparse
import datetime
import hashlib
import json
import os
import random
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.normpath(os.path.join(HERE, ".."))
for _d in (HERE, os.path.join(RD, "step7"), os.path.join(RD, "step8"), os.path.join(RD, "implementation_a")):
    sys.path.insert(0, _d)
sys.dont_write_bytecode = True
import s10_scoring as SC  # noqa: E402
import s8_construct as K  # noqa: E402
import s8_gates as GT  # noqa: E402

# ---------------------------------------------------------------------------------------------------- pre-registered constants (fixed before any run)
MODEL = "llama3.2:3b"
DEFAULT_URL = "http://127.0.0.1:11434"
NUM_CTX, NUM_PREDICT, PROBE_NUM_PREDICT, KEEP_ALIVE, TIMEOUT_S = 16384, 8192, 8, "30m", 180
S_SYN, G_SYN = (205, 245), (345,)                       # synthetic bases: disjoint from the main and pilot registry lists (asserted)
ORDER_SEED, N_REPEAT_STEP = 20261016, 4                 # fixed shuffle seed; every 4th call is repeated (16 repeats)
MAX_CALLS, MAX_CONSEC_SF1, MAX_WALL_S = 100, 5, 4 * 3600
MAIN_RUN_CALLS = 2496
UNP_PROCEED, UNP_OWNER = 0.10, 0.25                     # UNP share <= 0.10 proceed; <= 0.25 owner decision; above: stop
MAX_STRUCTURAL = 1                                      # structural failures (SF1-SF5) allowed among the 80 counted calls; truncation (SF3) must be 0
MAX_PROJECTED_HOURS = 24.0
STATUS = ("FEASIBILITY_PROCEED", "OWNER_DECISION", "STOP_NOT_FEASIBLE", "INCONCLUSIVE")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

urlopen = urllib.request.urlopen                        # patched in tests: no socket is needed to test this file
timer = time.monotonic
clock = lambda: datetime.datetime.now(datetime.timezone.utc)   # noqa: E731


def sha(b):
    return hashlib.sha256(b).hexdigest()


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def local_base(url):
    """Returns the normalised base URL, or raises ValueError if it is not plain http on a loopback host without credentials."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "http" or u.username or u.password or (u.hostname or "") not in LOCAL_HOSTS or u.path not in ("", "/") or u.query or u.fragment:
        raise ValueError("only plain http on 127.0.0.1, localhost or ::1 without credentials, path or query is accepted")
    host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    return f"http://{host}:{u.port or 11434}"


# ---------------------------------------------------------------------------------------------------- synthetic prompts
def build_prompts():
    """Returns (ordered main calls, repeats, manifest). Each call: {template_id, condition, prompt}. Frozen generator and renderer; nothing from the registry."""
    with open(os.path.join(RD, "PARAMETER_FREEZE_MANIFEST_FZ_v1.json"), "rb") as f:
        fz = json.loads(f.read().decode("utf-8"))["register"]
    p = K.params_from_fz(fz)
    if set(S_SYN) & (set(p["S_main"]) | set(p["S_pilot"])) or set(G_SYN) & (set(p["G_main"]) | set(p["G_pilot"])):
        raise ValueError("synthetic bases intersect the registry bases")
    templates = K.construct(p, S_SYN, G_SYN)
    prompts = GT.render_all(templates, p)
    calls = [{"template_id": t["id"], "condition": c, "prompt": prompts[t["id"]][c]} for t in templates for c in "ABCD"]
    random.Random(ORDER_SEED).shuffle(calls)
    repeats = calls[::N_REPEAT_STEP]
    if len(calls) != 64 or len(repeats) != 16:
        raise ValueError("unexpected number of synthetic calls")
    manifest = [{"template_id": c["template_id"], "condition": c["condition"], "prompt_sha256": sha(c["prompt"].encode("utf-8")), "chars": len(c["prompt"])} for c in calls]
    return calls, repeats, manifest


# ---------------------------------------------------------------------------------------------------- Ollama client
def _get_json(base, path):
    try:
        with urlopen(urllib.request.Request(base + path, method="GET"), timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def map_finish(raw):
    """done_reason -> project vocabulary. 'stop' -> STOP, 'length' -> MAX_TOKENS (the value 'length' is NOT documented in the Ollama pages read; the probe shows what the
    server reports). Any other value is prefixed so that it can never collide with the project's block names (for example OTHER) and is counted as an anomaly."""
    if raw is None:
        return None
    if raw == "stop":
        return "STOP"
    if raw == "length":
        return "MAX_TOKENS"
    s = "".join(ch for ch in str(raw).upper() if ch.isalnum() or ch == "_")[:24]
    return "OLLAMA_" + (s or "EMPTY")


def generate(base, model, prompt, num_predict):
    body = {"model": model, "prompt": prompt, "stream": False, "keep_alive": KEEP_ALIVE, "options": {"num_ctx": NUM_CTX, "num_predict": num_predict}}
    t0 = timer()
    rec = {"http_status": None, "error": None, "response_text": "", "finish_reason": None, "model_returned": None, "block_reason": None, "usage": None}
    extra = {}
    try:
        req = urllib.request.Request(base + "/api/generate", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=TIMEOUT_S) as r:
            status = r.status
            raw = r.read()
        rec["http_status"] = status
        try:
            d = json.loads(raw.decode("utf-8"))
            if not isinstance(d, dict):
                raise ValueError
        except ValueError:
            rec["error"] = "InvalidJSON"
            d = {}
        if rec["error"] is None:
            rec["response_text"] = d.get("response") if isinstance(d.get("response"), str) else ""
            rec["finish_reason"] = map_finish(d.get("done_reason")) if d.get("done") is True else map_finish(d.get("done_reason") or "not_done")
            rec["model_returned"] = d.get("model") if isinstance(d.get("model"), str) else None
            pe, ev = d.get("prompt_eval_count"), d.get("eval_count")
            if is_int(pe) and is_int(ev):
                rec["usage"] = {"promptTokenCount": pe, "candidatesTokenCount": ev, "totalTokenCount": pe + ev}
            elif is_int(pe) or is_int(ev):
                rec["usage"] = {k: v for k, v in (("promptTokenCount", pe), ("candidatesTokenCount", ev)) if is_int(v)}
            extra = {"done_reason_raw": d.get("done_reason") if isinstance(d.get("done_reason"), str) else None, "done": d.get("done") is True,
                     "prompt_eval_cached_count": d.get("prompt_eval_cached_count") if is_int(d.get("prompt_eval_cached_count")) else None}
            for k in ("eval_duration", "total_duration", "load_duration", "prompt_eval_duration"):
                extra[k + "_ns"] = d.get(k) if is_int(d.get(k)) else None
    except urllib.error.HTTPError as e:
        rec["http_status"], rec["error"] = e.code, f"HTTP {e.code}"
    except Exception as e:                                                # connection refused, timeout, ...: the class name only
        rec["error"] = type(e).__name__
    rec["latency_ms"] = int(round((timer() - t0) * 1000))
    rec["extra"] = extra
    return rec


# ---------------------------------------------------------------------------------------------------- analysis
def evaluate(s):
    """Pre-registered criteria applied to a summary dict. Returns (status, components)."""
    comp = {}
    if s.get("aborted") or s.get("anomalies", {}).get("model_mismatch") or s["counted_calls"] < 80:
        return "INCONCLUSIVE", {"reason": "the run was aborted, incomplete or showed a model-identity anomaly"}
    u = s["unp_share"]
    comp["unp"] = "PROCEED" if u <= UNP_PROCEED else ("OWNER_DECISION" if u <= UNP_OWNER else "STOP")
    sf = s["counts"]
    comp["structural"] = "PROCEED" if (sum(sf[k] for k in ("SF1", "SF2", "SF3", "SF4", "SF5")) <= MAX_STRUCTURAL and sf["SF3"] == 0) else "STOP"
    ph = s["projected_main_run_hours"]
    comp["time"] = "PROCEED" if (ph is not None and ph <= MAX_PROJECTED_HOURS) else "STOP"
    if "STOP" in comp.values():
        return "STOP_NOT_FEASIBLE", comp
    if "OWNER_DECISION" in comp.values():
        return "OWNER_DECISION", comp
    return "FEASIBILITY_PROCEED", comp


def summarise(records, requested_model, aborted, probes):
    counted = [r for r in records if r["kind"] in ("main", "repeat")]
    counts = {k: 0 for k in ("SF1", "SF2", "SF3", "SF4", "SF5", "UNP")}
    verdicts = {}
    parseable = 0
    for r in counted:
        cl = SC.classify(r)
        r["outcome"], r["flags"] = cl["outcome"], cl["flags"]
        if cl["outcome"] == "SF":
            counts[SC.decision_type(cl["flags"])] += 1
        elif cl["outcome"] == "UNP":
            counts["UNP"] += 1
        else:
            parseable += 1
        verdicts[r["call_index"]] = cl["outcome"]
    n = len(counted)
    anomalies = {"non_200": sum(1 for r in counted if r["http_status"] != 200),
                 "unlisted_finish_reason": sum(1 for r in counted if r["finish_reason"] not in (None, "STOP", "MAX_TOKENS")),
                 "model_mismatch": sum(1 for r in counted if r["http_status"] == 200 and r["model_returned"] != requested_model),
                 "usage_incomplete": sum(1 for r in counted if r["http_status"] == 200 and not (r["usage"] and is_int(r["usage"].get("promptTokenCount")) and is_int(r["usage"].get("totalTokenCount")))),
                 "not_done": sum(1 for r in counted if r["extra"] and r["extra"].get("done") is False)}
    lat = [r["latency_ms"] for r in counted]
    warm = lat[1:] if len(lat) > 1 else lat
    mean_s = statistics.fmean(warm) / 1000 if warm else None
    tps = [r["usage"]["candidatesTokenCount"] / (r["extra"]["eval_duration_ns"] / 1e9) for r in counted
           if r["usage"] and is_int(r["usage"].get("candidatesTokenCount")) and r["extra"] and is_int(r["extra"].get("eval_duration_ns")) and r["extra"]["eval_duration_ns"] > 0]
    by_key = {}
    for r in counted:
        by_key.setdefault((r["template_id"], r["condition"]), []).append(r)
    pairs = [v for v in by_key.values() if len(v) == 2]
    stability = {"pairs": len(pairs), "identical_text": sum(1 for a, b in pairs if a["response_text"] == b["response_text"]),
                 "identical_outcome": sum(1 for a, b in pairs if a["outcome"] == b["outcome"])}
    informational = {"parseable": parseable, "verdict_equals_synthetic_expectation": sum(1 for r in counted if r["outcome"] in ("satisfied", "violated") and r["outcome"] == SC.EXPECTED[r["condition"]]),
                     "note": "informational only, never a criterion; synthetic prompts"}
    s = {"counted_calls": n, "counts": counts, "unp_share": (counts["UNP"] / n) if n else None, "anomalies": anomalies, "aborted": aborted,
         "latency_ms": {"first_call": lat[0] if lat else None, "min": min(warm) if warm else None, "median": statistics.median(warm) if warm else None,
                        "mean": round(statistics.fmean(warm), 1) if warm else None, "max": max(warm) if warm else None},
         "projected_main_run_hours": (MAIN_RUN_CALLS * mean_s / 3600) if mean_s is not None else None,
         "output_tokens_per_second_median": round(statistics.median(tps), 2) if tps else None, "stability": stability, "informational": informational,
         "probes": probes}
    s["status"], s["components"] = evaluate(s)
    s["criteria"] = {"unp_proceed_le": UNP_PROCEED, "unp_owner_decision_le": UNP_OWNER, "max_structural": MAX_STRUCTURAL, "truncation_allowed": 0,
                     "max_projected_hours": MAX_PROJECTED_HOURS, "main_run_calls": MAIN_RUN_CALLS}
    return s


def provenance(base, model):
    prov = {"utc": clock().strftime("%Y-%m-%dT%H:%M:%SZ"), "requested_model": model, "base_url": base, "options_sent": {"num_ctx": NUM_CTX, "num_predict": NUM_PREDICT,
            "probe_num_predict": PROBE_NUM_PREDICT, "keep_alive": KEEP_ALIVE, "stream": False, "not_sent": ["temperature", "seed", "top_k", "top_p", "system"]},
            "harness_sha256": None}
    try:
        with open(os.path.abspath(__file__), "rb") as f:
            prov["harness_sha256"] = sha(f.read())
    except OSError:
        pass
    v = _get_json(base, "/api/version")
    prov["ollama_version"] = v.get("version") if isinstance(v, dict) and isinstance(v.get("version"), str) else "unavailable"
    tags = _get_json(base, "/api/tags")
    dig = None
    if isinstance(tags, dict):
        for m in tags.get("models") or []:
            if isinstance(m, dict) and model in (m.get("name"), m.get("model")):
                dig = m.get("digest")
    prov["model_digest"] = dig if isinstance(dig, str) else "unavailable"
    show = None
    try:
        req = urllib.request.Request(base + "/api/show", data=json.dumps({"model": model}).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=30) as r:
            show = json.loads(r.read().decode("utf-8"))
    except Exception:
        show = None
    if isinstance(show, dict):
        tpl = show.get("template") if isinstance(show.get("template"), str) else None
        prov["template_sha256"] = sha(tpl.encode("utf-8")) if tpl is not None else "unavailable"
        prov["template_has_system_block"] = ("System" in tpl) if tpl is not None else "unavailable"
        prov["parameters"] = show.get("parameters") if isinstance(show.get("parameters"), str) else "unavailable"
        prov["details"] = show.get("details") if isinstance(show.get("details"), dict) else "unavailable"
    else:
        prov["template_sha256"] = prov["parameters"] = prov["details"] = "unavailable"
    return prov


def run(out, url=DEFAULT_URL, model=MODEL, dry_run=False, say=print):
    base = local_base(url)
    calls, repeats, manifest = build_prompts()
    total = len(calls) + len(repeats)
    assert total + 2 <= MAX_CALLS
    say(f"synthetic prompts: {len(calls)} main + {len(repeats)} repeats = {total} counted calls (+2 probes); bases disjoint from the registry")
    if dry_run:
        say("dry run: no request was made, nothing was written")
        return None
    os.makedirs(out)                                                   # FileExistsError if it exists: nothing is overwritten
    with open(os.path.join(out, "prompts_manifest.json"), "xb") as f:
        f.write(json.dumps(manifest, indent=1).encode("utf-8"))
    prov = provenance(base, model)
    with open(os.path.join(out, "provenance.json"), "xb") as f:
        f.write(json.dumps(prov, indent=1).encode("utf-8"))
    say(f"ollama version: {prov['ollama_version']}; model digest known: {prov['model_digest'] != 'unavailable'}")
    records, probes, aborted, consec = [], [], None, 0
    t_start = timer()
    with open(os.path.join(out, "calls.jsonl"), "xb") as cf:
        def write(rec):
            cf.write((json.dumps(rec, sort_keys=True) + "\n").encode("utf-8"))
            cf.flush()
            os.fsync(cf.fileno())
        p1 = generate(base, model, calls[0]["prompt"], PROBE_NUM_PREDICT)
        p2 = generate(base, model, calls[0]["prompt"], NUM_PREDICT)
        for k, pr in ((1, p1), (2, p2)):
            probes.append({"probe": k, "num_predict": PROBE_NUM_PREDICT if k == 1 else NUM_PREDICT, "http_status": pr["http_status"], "error": pr["error"],
                           "done_reason_raw": pr["extra"].get("done_reason_raw") if pr["extra"] else None, "finish_reason": pr["finish_reason"],
                           "eval_count": (pr["usage"] or {}).get("candidatesTokenCount"), "latency_ms": pr["latency_ms"]})
            write(dict(pr, kind="probe", call_index=-k, template_id=calls[0]["template_id"], condition=calls[0]["condition"]))
        seq = [("main", c) for c in calls] + [("repeat", c) for c in repeats]
        try:
            for i, (kind, c) in enumerate(seq, 1):
                if timer() - t_start > MAX_WALL_S:
                    aborted = "wall-clock limit"
                    break
                r = generate(base, model, c["prompt"], NUM_PREDICT)
                r.update(kind=kind, call_index=i, template_id=c["template_id"], condition=c["condition"], prompt_sha256=sha(c["prompt"].encode("utf-8")),
                         utc_start=clock().strftime("%Y-%m-%dT%H:%M:%SZ"))
                write(r)
                records.append(r)
                consec = consec + 1 if r["error"] or (r["http_status"] is not None and r["http_status"] != 200) else 0
                if consec >= MAX_CONSEC_SF1:
                    aborted = f"{MAX_CONSEC_SF1} consecutive failures"
                    break
        except KeyboardInterrupt:
            aborted = "interrupted"
    s = summarise(records, model, aborted, probes)
    s["harness_sha256"] = prov["harness_sha256"]
    with open(os.path.join(out, "summary.json"), "xb") as f:
        f.write(json.dumps(s, indent=1, default=str).encode("utf-8"))
    report(s, say)
    return s


def report(s, say=print):
    say("=" * 70)
    say("LOCAL FEASIBILITY CHECK (synthetic prompts; NOT a pilot, NOT experiment data)")
    say(f"counted calls: {s['counted_calls']}; aborted: {s['aborted'] or 'no'}")
    c = s["counts"]
    say("SF1 {SF1}  SF2 {SF2}  SF3 {SF3}  SF4 {SF4}  SF5 {SF5}  UNP {UNP}".format(**c))
    say(f"UNP share: {s['unp_share']:.4f}" if s["unp_share"] is not None else "UNP share: n/a")
    say("anomalies: " + ", ".join(f"{k} {v}" for k, v in s["anomalies"].items()))
    L = s["latency_ms"]
    say(f"latency ms: first {L['first_call']} min {L['min']} median {L['median']} mean {L['mean']} max {L['max']}")
    say(f"output tokens per second (median): {s['output_tokens_per_second_median']}")
    ph = s["projected_main_run_hours"]
    say(f"projected hours for {MAIN_RUN_CALLS} calls: {ph:.2f}" if ph is not None else "projected hours: n/a")
    st = s["stability"]
    say(f"repeat pairs: {st['pairs']}; identical text {st['identical_text']}; identical outcome {st['identical_outcome']}")
    for p in s["probes"]:
        say(f"probe {p['probe']}: num_predict {p['num_predict']}; http {p['http_status']}; done_reason_raw {p['done_reason_raw']}; mapped {p['finish_reason']}; eval_count {p['eval_count']}")
    say("components: " + ", ".join(f"{k} {v}" for k, v in s["components"].items()))
    say("STATUS: " + s["status"])
    say("The status is a feasibility indication under criteria fixed before the run; it is not a pilot outcome and not evidence about equivalence with any other model.")
    say("=" * 70)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if not a.dry_run and not a.out:
        print("refused: --out is required unless --dry-run")
        return 2
    try:
        s = run(a.out, a.url, a.model, a.dry_run)
    except (ValueError, FileExistsError) as e:
        print("refused:", e if isinstance(e, ValueError) else "the output folder already exists")
        return 2
    if s is None:
        return 0
    return {"FEASIBILITY_PROCEED": 0, "OWNER_DECISION": 1, "STOP_NOT_FEASIBLE": 3, "INCONCLUSIVE": 2}[s["status"]]


if __name__ == "__main__":
    sys.exit(main())
