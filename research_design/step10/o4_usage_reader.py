"""O4 read-only pilot usage reader (patch P12; owner decision P12 = APPROVE procedure, decision record 12.50 row 6). Written 2026-10-15.
Method (C31_C32_OWNER_DECISION_BRIEF.md section 17): for each pilot call i, r_i = usageMetadata.promptTokenCount_i / characters_i (characters from the build's prompt table);
B = ceil(max_i r_i * 1016), computed with exact fractions. B is compared with the assumption (default 1016).
READ-ONLY and numbers-only: it opens exactly four files and writes nothing: <run>/calls.jsonl, the status marker <run>/RUN_COMPLETE or RUN_INCOMPLETE, <build>/manifest.json
and <build>/pilot/prompt_table.json. It never opens prompts/, raw/, the registry, a checker file or any credential, makes no network request, and prints only numbers and
status words: the call number, the first 8 hex characters of the prompt hash (opaque), characters, promptTokenCount, the ratio, B, counts, the status. It never prints a
prompt, a response, a call key (the key carries the condition letter), an API key or any raw JSON.
Statuses: SUPPORTED_FOR_PILOT_TEMPLATES (all prerequisites valid and B <= assumption); NOT_SUPPORTED_REQUIRES_OWNER_REVIEW (all metadata valid and B > assumption);
INCONCLUSIVE (missing or invalid required metadata, ambiguous mapping, a prompt over 1016 characters, or any failed structural check). Exit code 0, 1, 2 respectively.
Usage: python -B -I o4_usage_reader.py --run <pilot run folder> --build <step8 build folder> [--assumption 1016]"""
import argparse
import hashlib
import json
import os
import sys
from fractions import Fraction

sys.dont_write_bytecode = True
CHAR_LIMIT = 1016
SUPPORTED, NOT_SUPPORTED, INCONCLUSIVE = "SUPPORTED_FOR_PILOT_TEMPLATES", "NOT_SUPPORTED_REQUIRES_OWNER_REVIEW", "INCONCLUSIVE"
NOTE = ("note: promptTokenCount is the provider-reported count and is used as reported (provider overhead included); the result covers the pilot's prompts only; "
        "input_tokens_per_call_assumed remains an assumption until the owner records the result")


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def read_json(path):
    with open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def analyse(run, build, assumption=CHAR_LIMIT):
    """Returns (rows, problems, B). rows = list of dicts of numbers only; problems = list of short codes/messages without any content."""
    problems, rows = [], []
    marker = None
    for name in ("RUN_COMPLETE", "RUN_INCOMPLETE"):
        if os.path.isfile(os.path.join(run, name)):
            marker = name if marker is None else "BOTH"
    if marker is None or marker == "BOTH":
        return rows, ["PREREQUISITE: the run folder has no single status marker"], None
    try:
        summ = read_json(os.path.join(run, marker))
    except (OSError, ValueError):
        return rows, ["PREREQUISITE: the status marker is unreadable"], None
    if marker != "RUN_COMPLETE":
        problems.append("PREREQUISITE: the run is RUN_INCOMPLETE (a stopped run is not measured)")
    if not isinstance(summ, dict) or not str(summ.get("scope", "")).startswith("pilot_round_"):
        problems.append("PREREQUISITE: the run is not a pilot round")
    try:
        with open(os.path.join(build, "manifest.json"), "rb") as f:
            mhash = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return rows, problems + ["PREREQUISITE: the build manifest is unreadable"], None
    if isinstance(summ, dict) and summ.get("build_manifest_sha256") != mhash:
        problems.append("MAPPING: the run was made on another build (manifest hash differs)")
    try:
        table = read_json(os.path.join(build, "pilot", "prompt_table.json"))
    except (OSError, ValueError):
        return rows, problems + ["MAPPING: the pilot prompt table is unreadable"], None
    if not isinstance(table, list):
        return rows, problems + ["MAPPING: the pilot prompt table is not a list"], None
    index = {}
    for e in table:
        if isinstance(e, dict):
            index.setdefault((e.get("template_id"), e.get("condition")), []).append(e)
    recs = []
    try:
        with open(os.path.join(run, "calls.jsonl"), "rb") as f:
            for n, line in enumerate(f.read().decode("utf-8").split("\n"), 1):
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    problems.append(f"STRUCTURE: calls.jsonl line {n} is not valid JSON")
                    continue
                recs.append(r if isinstance(r, dict) else {})
    except (OSError, UnicodeDecodeError):
        return rows, problems + ["PREREQUISITE: calls.jsonl is unreadable"], None
    if isinstance(summ, dict):
        if not is_int(summ.get("planned_calls")) or summ.get("planned_calls") != summ.get("recorded_calls") or len(recs) != summ.get("recorded_calls"):
            problems.append("STRUCTURE: the number of records differs from the planned/recorded calls of the status marker")
    seen = set()
    invalid = 0
    for r in recs:
        ci = r.get("call_index")
        row = {"call": ci if is_int(ci) else None, "hash8": str(r.get("prompt_sha256") or "")[:8], "chars": None, "tokens": None, "ratio": None, "ok": False}
        rows.append(row)
        why = []
        if not is_int(ci) or ci in seen:
            why.append("call_index missing or duplicate")
        seen.add(ci)
        hits = index.get((r.get("template_id"), r.get("condition")), [])
        if len(hits) != 1:
            why.append("mapping ambiguous or missing")
        else:
            ent = hits[0]
            ch = ent.get("chars")
            if not is_int(ch) or ch <= 0:
                why.append("characters missing or invalid")
            else:
                row["chars"] = ch
                if ch > CHAR_LIMIT:
                    why.append("PROMPT_OVER_LIMIT")
            if ent.get("sha256") != r.get("prompt_sha256") or not isinstance(r.get("prompt_sha256"), str):
                why.append("prompt hash of the record differs from the prompt table")
        u = r.get("usage")
        tok = u.get("promptTokenCount") if isinstance(u, dict) else None
        if not is_int(tok) or tok <= 0:
            why.append("promptTokenCount missing or invalid")
        else:
            row["tokens"] = tok
        if not why and row["chars"] and row["tokens"]:
            row["ratio"] = Fraction(row["tokens"], row["chars"])
            row["ok"] = True
        else:
            invalid += 1
            for w in why:
                problems.append(f"{'LIMIT' if w == 'PROMPT_OVER_LIMIT' else 'RECORD'}: call {row['call']}: {w}")
    if not rows:
        problems.append("PREREQUISITE: no call records")
    B = None
    if rows and invalid == 0 and not problems:
        mx = max(r["ratio"] for r in rows)
        x = mx * CHAR_LIMIT
        B = -((-x.numerator) // x.denominator)                    # exact ceiling
    return rows, problems, B


def status_of(problems, B, assumption):
    if problems or B is None:
        return INCONCLUSIVE
    return SUPPORTED if B <= assumption else NOT_SUPPORTED


def render(rows, problems, B, assumption):
    out = ["O4 read-only pilot usage reader (numbers only)", "call  prompt8    chars  promptTokenCount  ratio"]
    for r in rows:
        ratio = f"{float(r['ratio']):.6f}" if r["ratio"] is not None else "n/a"
        out.append(f"{str(r['call']):>4}  {r['hash8']:<8}  {str(r['chars']) if r['chars'] is not None else 'n/a':>5}  "
                   f"{str(r['tokens']) if r['tokens'] is not None else 'n/a':>16}  {ratio}")
    valid = sum(1 for r in rows if r["ok"])
    out.append(f"records: {len(rows)}; valid: {valid}; incomplete or invalid: {len(rows) - valid}")
    out.append(f"B = ceil(max ratio x {CHAR_LIMIT}): {B if B is not None else 'n/a (not computed: required checks failed)'}; assumption: {assumption}")
    for p in problems[:60]:
        out.append("problem: " + p)
    if len(problems) > 60:
        out.append(f"problem: ... and {len(problems) - 60} more")
    st = status_of(problems, B, assumption)
    out.append("STATUS: " + st)
    out.append(NOTE)
    return "\n".join(out), st


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--assumption", type=int, default=CHAR_LIMIT)
    a = ap.parse_args(argv)
    rows, problems, B = analyse(a.run, a.build, a.assumption)
    text, st = render(rows, problems, B, a.assumption)
    print(text)
    return {SUPPORTED: 0, NOT_SUPPORTED: 1, INCONCLUSIVE: 2}[st]


if __name__ == "__main__":
    sys.exit(main())
