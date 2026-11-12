"""Post-run integrity of a pilot or main run folder (specification 10, 13.8, 13.9) -- decision record 12.32. Read-only; prints no response text.
Checks: one status marker; hash-chained ledger valid and equal to the summary; per call intent -> raw -> classified in order; raw file hash equals the
ledger and the calls.jsonl line; every record's prompt hash equals the published prompt hash; call keys unique (no retry, no replacement) and,
for a pilot, equal to pilot_calls.txt in order; a COMPLETE run has every planned call; model ID returned; observed token cost against OD-10.
Ledger format 2 (runs made after 2026-10-14) adds a provenance entry ("start"), and, after a resume, "resume" and "reconcile" entries and a segments/ folder
holding earlier status markers; format 1 runs verify exactly as before. Every kind of damage (truncated or invalid JSON lines, missing raw files, unreadable
files) is reported as a PROBLEM line and the run is never reported as valid; the checker does not raise on a damaged folder.
Usage: python -B -I s10_integrity.py --run <dir> --build <build>   exit 0 = no problem."""
import argparse
import hashlib
import json
import os
import sys
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_runner as RN  # noqa: E402
import s10_scoring as SC  # noqa: E402


def rb(p):
    with open(p, "rb") as f:
        return f.read()


def _count(x):
    return x if isinstance(x, int) and not isinstance(x, bool) and x >= 0 else None


def output_tokens(usage):
    """Output tokens billed for one call from the stored provider usage, WITHOUT counting any token twice, and the basis used.
    OD-10 / FZ-v1 model verification: thinking tokens are billed as output and count against max_output_tokens. If totalTokenCount and
    promptTokenCount are present, output = total - prompt (correct whether or not candidatesTokenCount already includes thinking tokens).
    Otherwise candidates + thoughts (thoughts only if reported); otherwise candidates only; otherwise unavailable (not costed)."""
    u = usage if isinstance(usage, dict) else {}
    p, t, c, th = _count(u.get("promptTokenCount")), _count(u.get("totalTokenCount")), _count(u.get("candidatesTokenCount")), _count(u.get("thoughtsTokenCount"))
    if p is not None and t is not None:
        if t >= p and (c is None or t - p >= c):
            return t - p, "total_minus_prompt"
        if c is not None:
            return c + (th or 0), "inconsistent_total_fallback"
        return None, "unavailable"
    if c is not None:
        return (c + th, "candidates_plus_thoughts") if th else (c, "candidates_only")
    return None, "unavailable"


def _read_jsonl(path, label, P):
    """Parsed JSON-object lines, or None. Every kind of damage becomes an explicit problem line (fail-closed); nothing raises."""
    try:
        b = rb(path)
    except OSError as e:
        P.append(f"{label} cannot be read ({type(e).__name__})")
        return None
    try:
        text = b.decode("utf-8")
    except UnicodeDecodeError:
        P.append(f"{label} is not valid UTF-8")
        return None
    if b and not b.endswith(b"\n"):
        P.append(f"{label} does not end with a newline (last line truncated?)")
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            P.append(f"{label} line {n} is not valid JSON (truncated or damaged)")
            return None
        if not isinstance(obj, dict):
            P.append(f"{label} line {n} is not a JSON object")
            return None
        out.append(obj)
    return out


def _sha_of_file(path):
    try:
        return hashlib.sha256(rb(path)).hexdigest()
    except OSError:
        return None


def verify_run(run, build):
    P, st = [], {}
    marks = [m for m in ("RUN_COMPLETE", "RUN_INCOMPLETE") if os.path.exists(os.path.join(run, m))]
    if len(marks) != 1:
        return ["not exactly one status marker"], st
    try:
        summ = json.loads(rb(os.path.join(run, marks[0])))
        scope = summ["scope"]
    except (OSError, ValueError, KeyError, TypeError):
        return ["the status marker cannot be read as a run summary"], st
    kind = "main" if scope == "main" else "pilot"
    led = _read_jsonl(os.path.join(run, "ledger.jsonl"), "ledger.jsonl", P)
    recs = _read_jsonl(os.path.join(run, "calls.jsonl"), "calls.jsonl", P)
    if led is None or recs is None:
        return P, st                                              # nothing further can be checked on an unreadable file
    prev = "0" * 64
    chain = []
    for e in led:
        if e.get("prev") != prev:
            P.append("ledger chain broken")
            break
        prev = hashlib.sha256(RN.cj(e)).hexdigest()
        chain.append(prev)
    if prev != summ.get("ledger_final_hash"):
        P.append("ledger final hash differs from the summary")
    if len(recs) != summ.get("recorded_calls"):
        P.append("calls.jsonl length differs from the summary")
    # Provenance, resume and reconcile entries (ledger format 2). They carry call_index 0 or the call they concern and are not part of the
    # per-call grammar, which is unchanged: intent, raw, classified for call 1, 2, 3, ... in order.
    core = [e for e in led if e.get("event") not in ("start", "resume", "reconcile")]
    ev = [(e.get("event"), e.get("call_index")) for e in core]
    if ev != [(k, i) for i in range(1, len(recs) + 1) for k in ("intent", "raw", "classified")]:
        P.append("ledger events are not intent/raw/classified per call in order")
    starts = [i for i, e in enumerate(led) if e.get("event") == "start"]
    if summ.get("ledger_format") == 2 and starts != [0]:
        P.append("ledger lacks exactly one provenance entry (start) as its first entry")
    elif summ.get("ledger_format") != 2 and starts:
        P.append("provenance entry present in a run whose summary is not ledger format 2")
    if starts and led[0].get("event") == "start":
        s0 = led[0]
        if s0.get("scope") != scope:
            P.append("provenance scope differs from the status marker")
        if s0.get("build_manifest_sha256") != RN.file_sha(os.path.join(build, "manifest.json")):
            P.append("provenance names another build than the one given")
        if summ.get("run_id") != s0.get("run_id"):
            P.append("run_id differs between the provenance entry and the status marker")
    resumes = [e for e in led if e.get("event") == "resume"]
    if len(resumes) != len(summ.get("resumes") or []):
        P.append("number of resume entries differs from the summary")
    for r in resumes:
        h = r.get("prior_marker_sha256")
        if h is not None and h not in {_sha_of_file(os.path.join(run, "segments", f)) for f in (os.listdir(os.path.join(run, "segments")) if os.path.isdir(os.path.join(run, "segments")) else [])}:
            P.append("a resume entry names a previous status marker that is not preserved in segments/")
    # Each resume entry must be consistent with the chain and with the outcomes that precede it: a forger who recomputes the hash chain still has to get the
    # previous head and the carried counters right (they are re-derived here from the classified entries, not read from the resume entry).
    outcome_of = {e.get("call_index"): e.get("outcome") for e in led if e.get("event") == "classified"}
    for j, e in enumerate(led):
        if e.get("event") != "resume":
            continue
        if j == 0 or j > len(chain) or e.get("previous_ledger_head") != chain[j - 1]:
            P.append(f"resume entry {e.get('resume_number')} names a previous ledger head that is not the chain value before it")
        n = e.get("persisted_calls")
        if not isinstance(n, int) or isinstance(n, bool) or n < 0 or any(i not in outcome_of for i in range(1, n + 1)):
            P.append(f"resume entry {e.get('resume_number')} states a number of persisted calls that the ledger does not support")
            continue
        sf_total = sum(1 for i in range(1, n + 1) if outcome_of[i] == "SF")
        consec = 0
        for i in range(n, 0, -1):
            if outcome_of[i] != "SF":
                break
            consec += 1
        if e.get("counters_carried") != {"consecutive_sf": consec, "sf_total": sf_total, "completed": n}:
            P.append(f"resume entry {e.get('resume_number')}: the counters carried over differ from the counters re-derived from the ledger")
    reconciled = []
    for j, e in enumerate(led):
        if e.get("event") == "reconcile":
            reconciled.append(e.get("call_index"))
            nxt = led[j + 1] if j + 1 < len(led) else {}
            if nxt.get("event") not in ("raw", "classified") or nxt.get("call_index") != e.get("call_index"):
                P.append(f"reconcile entry for call {e.get('call_index')} is not followed by the missing entries")
    table = {(r["template_id"], r["condition"]): r["sha256"] for r in json.loads(rb(os.path.join(build, kind, "prompt_table.json")))}
    keys = [r.get("call_key") for r in recs]
    if len(set(keys)) != len(keys):
        P.append("duplicate call keys (retry or replacement)")
    raws = {e.get("call_index"): e.get("raw_sha256") for e in led if e.get("event") == "raw"}
    for r in recs:
        idx = r.get("call_index")
        try:
            raw = rb(os.path.join(run, "raw", f"{idx:05d}.json"))
        except (OSError, TypeError, ValueError):
            P.append(f"raw file missing or unreadable for call {idx}")
            continue
        if hashlib.sha256(raw).hexdigest() != raws.get(idx):
            P.append(f"raw hash differs from the ledger at call {idx}")
        try:
            same = json.loads(raw) == r
        except ValueError:
            P.append(f"raw file of call {idx} is not valid JSON")
            continue
        if not same:
            P.append(f"raw record differs from calls.jsonl at call {idx}")
        if table.get((r.get("template_id"), r.get("condition"))) != r.get("prompt_sha256"):
            P.append(f"prompt hash differs from the build at call {idx}")
    try:
        n_raw = len(os.listdir(os.path.join(run, "raw")))
    except OSError:
        n_raw = -1
        P.append("raw folder cannot be read")
    if n_raw >= 0 and n_raw != len(recs):
        P.append("raw folder size")
    if kind == "pilot":
        planned = rb(os.path.join(build, "pilot", "pilot_calls.txt")).decode().split("\n")[:-1]
        if keys != planned[:len(keys)]:
            P.append("pilot calls differ from pilot_calls.txt (order or content)")
    if marks[0] == "RUN_COMPLETE" and len(recs) != summ.get("planned_calls"):
        P.append("COMPLETE run with missing calls")
    toks_in = sum((_count((r.get("usage") or {}).get("promptTokenCount")) or 0) for r in recs)
    outs = [output_tokens(r.get("usage")) for r in recs]
    toks_out = sum(v for v, _ in outs if v is not None)
    bases = {}
    for _, b in outs:
        bases[b] = bases.get(b, 0) + 1
    incomplete = sum(1 for (v, _), r in zip(outs, recs) if v is None or _count((r.get("usage") or {}).get("promptTokenCount")) is None)
    over = sum(1 for r in recs if (r.get("latency_ms") or 0) > 180000)
    requested = (summ.get("settings") or {}).get("model_id")
    returned = [r.get("model_returned") for r in recs if r.get("response_text")]
    unlisted = {}
    for r in recs:
        if SC.finish_reason_class(r.get("finish_reason")) == "unlisted":
            unlisted[r["finish_reason"]] = unlisted.get(r["finish_reason"], 0) + 1
    provider_checks = {"requested_model_id": requested,
                       "responses_with_returned_model_not_exactly_requested": sum(1 for m in returned if m and m != requested),
                       "responses_without_returned_model": sum(1 for m in returned if not m),
                       "records_without_http_status": sum(1 for r in recs if r.get("http_status") is None and not r.get("error")),
                       "unlisted_finish_reasons": unlisted,
                       "records_with_block_reason": sum(1 for r in recs if r.get("block_reason"))}
    st = {"scope": scope, "status": marks[0], "recorded": len(recs), "models_returned": sorted({r.get("model_returned") for r in recs if r.get("model_returned")}),
          "observed_tokens": {"prompt": toks_in, "output_billed": toks_out}, "output_token_basis": bases,
          "provider_checks": provider_checks, "calls_with_incomplete_usage": incomplete, "cost_is_lower_bound_if_incomplete": incomplete > 0,
          "calls_with_latency_over_nominal_timeout_180s": over, "latency_recorded_calls": sum(1 for r in recs if r.get("latency_ms") is not None),
          "observed_cost_usd_at_recorded_prices": float((F(toks_in) * RN.prices_for(summ.get("settings"))[0] + F(toks_out) * RN.prices_for(summ.get("settings"))[1]) / 10 ** 6),
          "ledger_format": summ.get("ledger_format", 1), "provenance_present": bool(starts), "run_id": summ.get("run_id"),
          "resumes": len(resumes), "reconciled_calls": reconciled, "halt": summ.get("halt")}
    halt = summ.get("halt") or ""
    if halt.startswith("provider anomaly:"):                    # MD-06: the stated reason must be visible in the LAST stored record, and no call follows

        last = recs[-1] if recs else {}
        if RN.provider_anomaly(last, requested) != halt[len("provider anomaly: "):]:
            P.append("halt reason (provider anomaly) is not supported by the last stored record")
        if summ.get("recorded_calls") != len(recs):
            P.append("provider-anomaly stop: recorded_calls differs from the stored records")
    if st["observed_cost_usd_at_recorded_prices"] >= 100:
        P.append("observed cost reaches the OD-10 limit")
    return P, st


def main(argv=None):
    a = argparse.ArgumentParser()
    a.add_argument("--run", required=True)
    a.add_argument("--build", required=True)
    a = a.parse_args(argv)
    P, st = verify_run(a.run, a.build)
    for p in P[:30]:
        print("PROBLEM:", p)
    print(json.dumps(st, sort_keys=True))
    print("RUN INTEGRITY OK" if not P else f"{len(P)} problem(s)")
    return 1 if P else 0


if __name__ == "__main__":
    sys.exit(main())
