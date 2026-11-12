"""Evaluation of the HUMAN blind check and construction of the Step 9 evidence file (specification 11 layer 3, 11.1 ADJ-1, 11.2 ADJ-2, FZ-v1 row 19)
-- decision record 12.33. It performs no human task: it only compares a worksheet that a person filled with a reference computed from the DISPLAYED text of
the checker-bundle file (rule + plan block), by the ordered procedure of 11.2:
  1 strict parse of the rendered text (Implementation B) -> REJECT;  2 completeness -> REJECT;  3 transcription (normalized) -> CHECKER ERROR;
  4 precision floor -> CHECKER ERROR;  5 arithmetic (half a unit of the last stated decimal) -> CHECKER ERROR;  6 residual verdict difference -> REJECT.
AGREE if the verdict equals the reference verdict. A CHECKER ERROR keeps the member (logged); any REJECT rejects the build (21.2).
The reference verdict is recomputed here from the displayed values (not read from the mapping). The mapping is optional and only names the template of a REJECT.
Worksheet columns: those of results_package.../step9_blind_check_kit/make_blank_worksheet.py. Column meaning per family:
 MIL: P_or_D_dist = P; wN_transcribed = displayed weekly total; wN_value = the total in km; increase (km, >=1 decimal), limit (km, >=2), percent (>=2), status 'over cap'/'not over cap' (week 1: 'uncompared').
 PAC: P_or_D_dist = D_dist; T_time_displayed; T_time_seconds; tau_F; wN_transcribed = pace string; wN_value = s/km; implied finish before rounding (>=2 decimals),
      after rounding, difference from T_time, status '|e|>tau' or 'not'.
PRD-5 (the unit-token sets of the normalization) is still a pending researcher decision; the sets below are those printed in 11.2 step 3.
The evidence writer never invents a date: the completion date and the last-viewing date are arguments given by the person; it refuses to write for any REJECT.
Standard library plus Implementation B's parser. Usage: see main()."""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
from decimal import Decimal, ROUND_HALF_UP, getcontext

getcontext().prec = 50
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import impl_b  # noqa: E402

UNITS = {"P": ["%"], "total": ["km"], "D": ["km"], "T": [], "tau": ["sec", "s"], "pace": ["min/km", "/km"]}
N = 5


def normalize(s, field):
    """Section 11.2 step 1-3 normalization (closed list; no other transformation)."""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s).strip(" ")
    if s and s[-1] in ".,;":
        s = s[:-1].rstrip(" ")
    for tok in sorted(UNITS[field], key=len, reverse=True):
        if s.lower().endswith(tok) and len(s) > len(tok):
            before = s[:-len(tok)]
            if before[-1].isdigit():
                return before
            if before[-1] == " " and (len(before) < 2 or before[-2] != " ") and len(before) > 1:
                return before[:-1]
            return s                                       # first matching token is the only candidate (11.2 step 3)
    return s


def decimals(s):
    return len(s.split(".")[1]) if "." in s else 0


def num(s):
    return Decimal(s.strip().replace(" ", ""))


def reference(text):
    """Family, parsed values and the reference worksheet from the displayed text."""
    fam = "MIL" if text.startswith("Constraint (weekly mileage") else "PAC"
    q = impl_b.parse_prompt(text, fam, N)                    # raises impl_b.Ambiguity on any missing/duplicate/conflicting field
    return fam, q


def check_row(row, text):
    """Returns (classification, step) for one worksheet row (11.1 ADJ-1, 11.2 ADJ-2)."""
    try:
        fam, q = reference(text)
    except impl_b.Ambiguity as e:
        return "REJECT", f"1 strict parse: {e}"
    rows = q["rows"]
    if fam == "MIL":
        P = q["P"]
        X = [v for _, v in rows]
        pct = [None] + [(X[j] - X[j - 1]) / X[j - 1] * 100 for j in range(1, N)]
        inc = [None] + [X[j] - X[j - 1] for j in range(1, N)]
        lim = [None] + [Decimal(P) / 100 * X[j - 1] for j in range(1, N)]
        ref_verdict = "violated" if any(pct[j] > P for j in range(1, N)) else "satisfied"
        trans = [("P", row.get("P_or_D_dist", ""), str(P))] + [("total", row.get(f"w{w}_transcribed", ""), str(X[w - 1])) for w in range(1, N + 1)]
    else:
        D, T, tau = q["D"], q["T"], q["tau"]
        paces = [v for _, v in rows]
        imp = [D * p for p in paces]
        rnd = [i.quantize(Decimal(1), rounding=ROUND_HALF_UP) for i in imp]
        diff = [r - T for r in rnd]
        ref_verdict = "violated" if any(abs(d) > tau for d in diff) else "satisfied"
        tline = f"{T // 3600}:{(T % 3600) // 60:02d}:{T % 60:02d}"
        trans = [("D", row.get("P_or_D_dist", ""), format(D.normalize(), "f")), ("T", row.get("T_time_displayed", ""), tline), ("tau", row.get("tau_F", ""), str(tau))]
        trans += [("pace", row.get(f"w{w}_transcribed", ""), f"{paces[w - 1] // 60}:{paces[w - 1] % 60:02d}") for w in range(1, N + 1)]
    verdict = (row.get("verdict_satisfied_violated_unclear") or "").strip().lower()
    if verdict == ref_verdict:
        return "AGREE", ""
    # the verdict differs (or is missing): the ordered procedure of 11.2
    need = ["verdict_satisfied_violated_unclear", "P_or_D_dist"] + (["T_time_displayed", "T_time_seconds", "tau_F"] if fam == "PAC" else [])
    for w in range(1, N + 1):
        need += [f"w{w}_transcribed", f"w{w}_value", f"w{w}_status"]
        if w >= 2 or fam == "PAC":
            need += [f"w{w}_increase_km_or_implied_finish_s_2dp", f"w{w}_limit_km_2dp_or_rounded_finish_s", f"w{w}_percent_2dp_or_diff_s"]
    if any(not (row.get(c) or "").strip() for c in need):
        return "REJECT", "2 completeness"
    for field, human, shown in trans:
        if normalize(human, field) != shown:
            return "CHECKER ERROR (transcription)", f"3 {field}"
    if fam == "PAC" and num(row["T_time_seconds"]) != T:
        return "CHECKER ERROR (transcription)", "3 T_time seconds"
    for w in range(1, N + 1):
        if fam == "MIL" and w == 1:
            continue
        a = row[f"w{w}_increase_km_or_implied_finish_s_2dp"].strip()
        b = row[f"w{w}_limit_km_2dp_or_rounded_finish_s"].strip()
        c = row[f"w{w}_percent_2dp_or_diff_s"].strip()
        try:
            if fam == "MIL":
                for s_, f_, r_ in ((a, 1, inc[w - 1]), (b, 2, lim[w - 1]), (c, 2, pct[w - 1])):
                    if decimals(s_) < f_:
                        return "CHECKER ERROR (precision)", f"4 week {w}"
                    if abs(num(s_) - r_) > Decimal(5) / (10 ** (decimals(s_) + 1)):
                        return "CHECKER ERROR (arithmetic)", f"5 week {w}"
            else:
                if decimals(a) < 2:
                    return "CHECKER ERROR (precision)", f"4 week {w}"
                if abs(num(a) - imp[w - 1]) > Decimal(5) / (10 ** (decimals(a) + 1)) or num(b) != rnd[w - 1] or num(c) != diff[w - 1]:
                    return "CHECKER ERROR (arithmetic)", f"5 week {w}"
        except Exception:
            return "REJECT", f"7 unparseable entry at week {w}"
    return "REJECT", "6 residual: verdict differs with a correct worksheet"


def evaluate(sheet_path, bundle_dir, mapping_path=None):
    with open(sheet_path, "rb") as f:
        rows = list(csv.DictReader(io.StringIO(f.read().decode("utf-8"))))
    mapping = None
    if mapping_path:
        with open(mapping_path, "rb") as f:
            mapping = json.loads(f.read())
    res, counts = [], {}
    names = sorted(os.listdir(bundle_dir))
    byid = {r["opaque_id"]: r for r in rows}
    for oid in names:
        with open(os.path.join(bundle_dir, oid), "rb") as f:
            text = f.read().decode("ascii")
        row = byid.get(oid)
        cls, step = ("REJECT", "2 completeness: row missing") if row is None else check_row(row, text)
        counts[cls] = counts.get(cls, 0) + 1
        res.append({"opaque_id": oid, "classification": cls, "step": step, **({"member": mapping[oid]} if mapping and cls == "REJECT" else {})})
    extra = sorted(set(byid) - set(names))
    answered = sum(1 for oid in names if oid in byid and (byid[oid].get("verdict_satisfied_violated_unclear") or "").strip())
    return {"rows_expected": len(names), "rows_with_verdict": answered, "coverage_percent": 100 * answered // max(1, len(names)), "extra_rows": extra,
            "counts": counts, "build_verdict": "REJECT" if counts.get("REJECT") or extra else "NO REJECT", "results": res}


def write_evidence(out, sheet_path, evaluation, build, completed_utc, last_view_utc):
    """Evidence file for the runner gate. Refuses for any REJECT, missing coverage or malformed dates."""
    if evaluation["build_verdict"] != "NO REJECT" or evaluation["coverage_percent"] != 100:
        raise ValueError("the blind check does not support an evidence file (REJECT or coverage below 100 percent)")
    for d in (completed_utc, last_view_utc):
        if not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", d):
            raise ValueError("dates must be UTC like 2026-10-27T09:00:00Z, given by the person")
    with open(sheet_path, "rb") as f:
        wh = hashlib.sha256(f.read()).hexdigest()
    with open(os.path.join(build, "manifest.json"), "rb") as f:
        bh = hashlib.sha256(f.read()).hexdigest()
    adj = "AGREE" if set(evaluation["counts"]) == {"AGREE"} else "ADJUDICATED_NO_REJECT"
    ev = {"human_blind_check": {"completed_utc": completed_utc, "last_registry_view_utc": last_view_utc, "coverage_percent": 100, "worksheet_sha256": wh,
                                "adjudication": adj, "classification_counts": evaluation["counts"]}, "build_manifest_sha256": bh}
    with open(out, "xb") as f:
        f.write((json.dumps(ev, sort_keys=True, separators=(",", ":")) + "\n").encode())
    return ev


def main(argv=None):
    ap = argparse.ArgumentParser(prog="s9_blindcheck.py")
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--bundle", required=True, help="<build>/main/checker_bundle")
    ap.add_argument("--mapping", help="optional, <build>/main/checker_mapping_HELD_SEPARATELY.json (open it only after the check)")
    ap.add_argument("--evaluation-out", required=True)
    ap.add_argument("--evidence-out")
    ap.add_argument("--completed-utc")
    ap.add_argument("--last-registry-view-utc")
    a = ap.parse_args(argv)
    ev = evaluate(a.sheet, a.bundle, a.mapping)
    with open(a.evaluation_out, "xb") as f:
        f.write((json.dumps(ev, sort_keys=True, indent=1) + "\n").encode())
    print(json.dumps({k: ev[k] for k in ("rows_expected", "rows_with_verdict", "coverage_percent", "counts", "build_verdict")}, sort_keys=True))
    if a.evidence_out:
        if not (a.completed_utc and a.last_registry_view_utc):
            print("evidence file needs --completed-utc and --last-registry-view-utc given by the person")
            return 2
        build = os.path.dirname(os.path.dirname(os.path.abspath(a.bundle)))
        try:
            write_evidence(a.evidence_out, a.sheet, ev, build, a.completed_utc, a.last_registry_view_utc)
        except ValueError as e:
            print("no evidence file:", e)
            return 3
        print("evidence file written", a.evidence_out)
    return 0 if ev["build_verdict"] == "NO REJECT" else 1


if __name__ == "__main__":
    sys.exit(main())
