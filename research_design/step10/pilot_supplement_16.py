"""DESCRIPTIVE, NON-OFFICIAL structural supplement for a pilot round: the calls with a recorded HTTP 200 and complete usage metadata (written 2026-10-15 on the owner's approval).
It is NOT the pilot result, NOT a replacement of the official report, and decides nothing. It applies no threshold, computes no round outcome, no bound B, no agreement with
the ground truth and no verdict distribution (spec 13.7). Population: the recorded calls with http_status 200, no error marker and a complete usage object; every other call is
listed with its HTTP status only, so that the change of the analysis population is explicit.
READ-ONLY and numbers-only: it opens exactly two files for reading, <run>/calls.jsonl and the status marker <run>/RUN_COMPLETE or RUN_INCOMPLETE, and writes nothing. It makes no
network request and never reads a credential. It prints only numbers, upper-case enum names (finish reasons) and model identifiers; never a prompt, a response, a call key, a template or
condition label, an API key or raw JSON. Counts use the official `s10_scoring.pilot_counts` on the included calls.
Usage: python -B -I pilot_supplement_16.py --run <pilot run folder>"""
import argparse
import datetime
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_scoring as SC  # noqa: E402

ENUM = re.compile(r"^[A-Z_]{1,24}$")
MODEL = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,39}$")
BANNER = ["=" * 78,
          "NOT THE OFFICIAL PILOT RESULT. DESCRIPTIVE STRUCTURAL SUPPLEMENT ONLY.",
          "The official report of the round (all planned calls) is authoritative and is not changed or replaced.",
          "The analysis population excludes the calls that did not return HTTP 200 with complete usage; excluding them CHANGES the population.",
          "No threshold, round outcome, bound B, ground-truth agreement or verdict distribution is computed or implied (spec 13.7).",
          "=" * 78]


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def usage_ok(u):
    return isinstance(u, dict) and all(is_int(u.get(k)) and u.get(k) >= 0 for k in ("promptTokenCount", "candidatesTokenCount", "totalTokenCount"))


def load(run):
    marker = [n for n in ("RUN_COMPLETE", "RUN_INCOMPLETE") if os.path.isfile(os.path.join(run, n))]
    if len(marker) != 1:
        raise ValueError("PREREQUISITE: the run folder has no single status marker")
    with open(os.path.join(run, marker[0]), "rb") as f:
        summ = json.loads(f.read().decode("utf-8"))
    if not isinstance(summ, dict) or not str(summ.get("scope", "")).startswith("pilot_round_"):
        raise ValueError("PREREQUISITE: the run is not a pilot round")
    recs = []
    with open(os.path.join(run, "calls.jsonl"), "rb") as f:
        for n, line in enumerate(f.read().decode("utf-8").split("\n"), 1):
            if line.strip():
                try:
                    r = json.loads(line)
                except ValueError:
                    raise ValueError(f"STRUCTURE: calls.jsonl line {n} is not valid JSON")
                recs.append(r if isinstance(r, dict) else {})
    if not (is_int(summ.get("planned_calls")) and summ.get("planned_calls") == summ.get("recorded_calls") == len(recs)):
        raise ValueError("STRUCTURE: the number of records differs from the planned/recorded calls of the status marker")
    idx = [r.get("call_index") for r in recs]
    if not all(is_int(i) for i in idx) or len(set(idx)) != len(idx):
        raise ValueError("STRUCTURE: call_index missing or duplicate")
    return marker[0], summ, recs


def split(recs):
    inc, exc = [], []
    for r in recs:
        if r.get("http_status") == 200 and is_int(r.get("http_status")) and r.get("error") is None and usage_ok(r.get("usage")):
            inc.append(r)
        else:
            exc.append(r)
    return inc, exc


def dist(vals):
    if not vals:
        return "n/a"
    return f"min {min(vals)}  median {statistics.median(vals):g}  mean {statistics.fmean(vals):.1f}  max {max(vals)}  sum {sum(vals)}"


def enum_or(v, rx):
    return v if isinstance(v, str) and rx.match(v) else ("none" if v is None else "other")


def render(marker, recs):
    inc, exc = split(recs)
    out = list(BANNER)
    out.append(f"run status marker: {marker}; recorded calls: {len(recs)}; included: {len(inc)}; excluded: {len(exc)}")
    out.append("inclusion rule: http_status = 200, no error marker, complete usage (promptTokenCount, candidatesTokenCount, totalTokenCount).")
    for r in sorted(exc, key=lambda x: x["call_index"]):
        st = r.get("http_status")
        out.append(f"excluded: call {r['call_index']}: http_status {st if is_int(st) else 'none'}" + ("" if r.get("error") is None else "; error marker present") +
                   ("" if usage_ok(r.get("usage")) else "; no complete usage"))
    if not inc:
        out.append("no call is included: no descriptive figures.")
        return "\n".join(out + BANNER[-1:])
    pc = SC.pilot_counts(inc)
    c = pc["counts"]
    n = len(inc)
    out.append("--- structural counts on the included calls (official counting code, counts only) ---")
    out.append("SF1 {SF1}  SF2 {SF2}  SF3 {SF3}  SF4 {SF4}  SF5 {SF5}  UNP {UNP}".format(**c) + f"   (n = {n}; multi-flag calls {pc['multi_flag_calls']})")
    out.append(f"UNP share on the included calls: {c['UNP']}/{n} = {c['UNP'] / n:.4f}   (the official share is defined over all calls of the round and is in the official report)")
    fr = {}
    for r in inc:
        k = enum_or(r.get("finish_reason"), ENUM)
        fr[k] = fr.get(k, 0) + 1
    out.append("finish reasons: " + ", ".join(f"{k} {v}" for k, v in sorted(fr.items())))
    md = {}
    for r in inc:
        k = enum_or(r.get("model_returned"), MODEL)
        md[k] = md.get(k, 0) + 1
    out.append("models returned: " + ", ".join(f"{k} {v}" for k, v in sorted(md.items())))
    out.append("--- latency (ms) and token usage on the included calls ---")
    out.append("latency_ms:         " + dist([r["latency_ms"] for r in inc if is_int(r.get("latency_ms"))]))
    for key, label in (("promptTokenCount", "promptTokenCount:  "), ("candidatesTokenCount", "candidatesTokenCount:"), ("totalTokenCount", "totalTokenCount:   ")):
        out.append(f"{label} " + dist([r["usage"][key] for r in inc]))
    out.append("--- per call (numbers only) ---")
    out.append("call  http  latency_ms  promptTokens  candidateTokens  totalTokens  start_offset_s")
    ts = {}
    for r in recs:
        try:
            ts[r["call_index"]] = datetime.datetime.strptime(r["utc_start"], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError, TypeError):
            pass
    t0 = min(ts.values()) if ts else None
    for r in sorted(recs, key=lambda x: x["call_index"]):
        u = r.get("usage") if isinstance(r.get("usage"), dict) else {}
        g = lambda v: str(v) if is_int(v) else "n/a"    # noqa: E731
        off = str(int((ts[r["call_index"]] - t0).total_seconds())) if r["call_index"] in ts else "n/a"
        out.append(f"{r['call_index']:>4}  {g(r.get('http_status')):>4}  {g(r.get('latency_ms')):>10}  {g(u.get('promptTokenCount')):>12}  "
                   f"{g(u.get('candidatesTokenCount')):>15}  {g(u.get('totalTokenCount')):>11}  {off:>14}")
    perminute = {}
    for t in ts.values():
        k = t.strftime("%H:%M")
        perminute[k] = perminute.get(k, 0) + 1
    out.append("requests started per UTC clock minute (all recorded calls): " + ", ".join(f"{k} {v}" for k, v in sorted(perminute.items())))
    out.append("note: promptTokenCount is the provider-reported count as stored; no missing count is inferred; the bound B of O4 is NOT computed here.")
    return "\n".join(out + BANNER[-1:])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    a = ap.parse_args(argv)
    try:
        marker, summ, recs = load(a.run)
    except (ValueError, OSError, UnicodeDecodeError) as e:
        print("\n".join(BANNER[:2]))
        print(str(e) if isinstance(e, ValueError) else "PREREQUISITE: a required file is unreadable")
        return 2
    print(render(marker, recs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
