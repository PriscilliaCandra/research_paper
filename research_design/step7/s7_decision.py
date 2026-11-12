"""Step 7 decision script (specification sections 10, 13.6, 14) -- DRAFT for owner review.

It computes, from counts only, the pilot round outcome (13.6) and the final pilot decision, builds the canonical call list (10),
derives the seed from the script-stamped UTC date (10), shuffles the canonical order once with random.Random(seed).shuffle (10),
and writes the decision record (10) and prints its SHA-256. It never reads response content: its inputs are integers.

Nothing runs at import. The clock is injectable (a Python argument, never a command-line option) so that tests are deterministic;
in real use the clock is the system UTC clock (the seed is script-stamped, never typed). write_decision_record() refuses to
overwrite and every invocation of main() appends one line to a run log (10: a further run is a logged event; the first run's
timestamp and seed stand).

Owner decisions applied (decision record 12.18):
  GAP-1  the UNP share is UNP / all calls of the round; a call counted under SF1..SF5 is not counted as UNP (disjoint counts),
         so SF1+...+SF5+UNP may not exceed the number of calls of the round, and the number of calls must equal the planned
         number of calls of the round.
  GAP-2  a round that meets both FAIL-S and FAIL-U has the outcome FAIL-U (action STOP-U); the FAIL-S condition is still
         reported in the result. There is no combined label.
  The record is written only for a FINAL pilot decision: PASS, STOP-U, or FAIL-S in round 2 (STOP). FAIL-S in round 1 raises
  RoundNotFinal: the round-1 action (one whitelisted change, or STOP when none fits) and the case of several failure types are
  not decided by the specification (13.6) and are not automated here.

Serialization (specification 10: sorted keys, UTF-8, LF, final LF): compact separators (",", ":") and ensure_ascii=False, the
same canonical form as the register hash of the FZ-v1 manifest. The specification fixes no separators; this is a Step 7 choice.
"""
import argparse
import datetime
import hashlib
import json
import os
import platform
import random
import re
import sys
from fractions import Fraction

CONDITION_TOKENS = ("A", "B", "C", "D")
STRUCTURAL = ("SF1", "SF2", "SF3", "SF4", "SF5")
MIN_PILOT_CALLS = 18          # 13.3: 6 MIL + 12 PAC calls; extra pilot calls are a register parameter (row 18)
TEMPLATE_ID_RX = re.compile(r"(?:MIL|PAC)-[0-9]{3,}")
HEX64_RX = re.compile(r"[0-9a-f]{64}")


class RoundNotFinal(Exception):
    """The round outcome is not a final pilot decision (FAIL-S in round 1)."""


def canonical_json(obj):
    """Canonical JSON text (sorted keys, compact separators, no ASCII escaping) plus one final LF."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _count(x):
    return isinstance(x, int) and not isinstance(x, bool)


def round_outcome(counts, n_calls, f_S, theta_unp, *, planned_calls):
    """Round outcome by counts only (13.6). counts: dict with exactly SF1..SF5 and UNP (non-negative ints, disjoint classes);
    n_calls: calls of the round, equal to planned_calls; f_S: int >= 0; theta_unp: Fraction/str/int with 0 < theta < 1."""
    if not isinstance(counts, dict) or set(counts) != set(STRUCTURAL) | {"UNP"}:
        raise ValueError("counts must have exactly the keys SF1..SF5 and UNP")
    for k, v in counts.items():
        if not _count(v) or v < 0:
            raise ValueError(f"count {k} invalid")
    if not _count(n_calls) or n_calls <= 0 or not _count(planned_calls) or planned_calls <= 0:
        raise ValueError("n_calls and planned_calls must be positive ints")
    if n_calls != planned_calls:
        raise ValueError("the number of calls of the round differs from the planned number (round not complete)")
    if not _count(f_S) or f_S < 0:
        raise ValueError("f_S must be an int >= 0")
    theta = Fraction(theta_unp)
    if not 0 < theta < 1:
        raise ValueError("theta_unp must satisfy 0 < theta_unp < 1")
    if sum(counts.values()) > n_calls:
        raise ValueError("SF1..SF5 and UNP are disjoint classes: their sum exceeds the calls of the round")
    hard = counts["SF2"] + counts["SF3"] + counts["SF5"]
    soft = counts["SF1"] + counts["SF4"]
    unp_share = Fraction(counts["UNP"], n_calls)
    fail_s = hard > 0 or soft > f_S
    fail_u = unp_share > theta
    outcome = "FAIL-U" if fail_u else ("FAIL-S" if fail_s else "PASS")      # GAP-2: FAIL-U takes precedence
    return {"outcome": outcome, "fail_s": fail_s, "fail_u": fail_u, "both_conditions": fail_s and fail_u,
            "hard_SF2_SF3_SF5": hard, "soft_SF1_SF4": soft, "unp_share": str(unp_share), "unp_share_denominator": n_calls,
            "unp_definition": "UNP / all calls of the round; SF-classified calls are not UNP (12.18 row 1)",
            "f_S": f_S, "theta_unp": str(theta)}


def final_pilot_decision(round_result, round_number):
    """PASS -> PASS; FAIL-U -> STOP-U; FAIL-S in round 2 -> STOP; FAIL-S in round 1 -> RoundNotFinal."""
    if round_number not in (1, 2):
        raise ValueError("round_number must be 1 or 2 (cap of two pilot rounds, 13.6)")
    out = round_result["outcome"]
    if out == "PASS":
        return "PASS"
    if out == "FAIL-U":
        return "STOP-U"
    if out == "FAIL-S":
        if round_number == 2:
            return "STOP"
        raise RoundNotFinal("FAIL-S in round 1 is not a final decision (13.6); nothing is written")
    raise ValueError("unknown round outcome")


def main_run_halt(consecutive_structural_failures, completed_calls, structural_failures, c_stop, calls_min_main, theta_main):
    """Main-run monitor rule (13.8): halt if the consecutive structural failures reach c_stop, or if, after at least
    calls_min_main completed calls, the cumulative share of SF1-SF5 exceeds theta_main."""
    if consecutive_structural_failures >= c_stop:
        return True
    if completed_calls >= calls_min_main and completed_calls > 0:
        return Fraction(structural_failures, completed_calls) > Fraction(theta_main)
    return False


def call_list(template_ids, R):
    """Canonical call list (10): (template, condition, repetition) triples, templates in the given canonical order,
    condition tokens A<B<C<D, repetition 1..R; one line '<template_id>,<token>,<repetition>' per triple, LF-joined, final LF."""
    if not isinstance(R, int) or isinstance(R, bool) or R < 1:
        raise ValueError("R must be a positive int")
    lines = [f"{t},{c},{r}" for t in template_ids for c in CONDITION_TOKENS for r in range(1, R + 1)]
    return "\n".join(lines) + "\n"


def seed_from_clock(now_utc):
    """YYYYMMDD of the script-stamped UTC date as an integer (10)."""
    if now_utc.tzinfo is None or now_utc.utcoffset() != datetime.timedelta(0):
        raise ValueError("the clock value must be timezone-aware UTC")
    return int(now_utc.strftime("%Y%m%d"))


def shuffled_order(call_list_text, seed):
    """The one shuffle (10): random.Random(seed).shuffle over the canonical lines."""
    lines = call_list_text.rstrip("\n").split("\n")
    random.Random(seed).shuffle(lines)
    return lines


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def register_hash(manifest):
    """SHA-256 of the UTF-8 JSON of the 'register' object, sorted keys, separators (',', ':'), ensure_ascii=False (the method
    recorded in PARAMETER_FREEZE_MANIFEST_FZ_v1.json)."""
    return sha256_hex(json.dumps(manifest["register"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def expected_template_ids(register):
    """The canonical template IDs implied by the frozen register (10; 9.2): MIL-001.. then PAC-001.., counters 1-based,
    zero-padded to width max(3, digits of N_tpl,FAM). N_tpl,MIL = K*L_M, N_tpl,PAC = 2*K*L_P with K = N - 1."""
    K = register["fixed_rules"]["N"] - 1
    n_mil, n_pac = K * register["row11_L_M"], 2 * K * register["row11_L_P"]
    out = []
    for fam, n in (("MIL", n_mil), ("PAC", n_pac)):
        w = max(3, len(str(n)))
        out += [f"{fam}-{i:0{w}d}" for i in range(1, n + 1)]
    return out


def build_decision_record(*, pilot_counts, round_result, final_decision, round_number, fz_hash, script_path, call_list_text,
                          register_sha256=None, clock=None):
    """Decision record (10): pilot structural counts and UNP share, FZ hash, decision-script hash, script-stamped UTC
    timestamp, computed seed, interpreter version, SHA-256 and length of the canonical call list. Added by this draft:
    the round number, the final decision, and the recomputed register hash."""
    if not isinstance(fz_hash, str) or HEX64_RX.fullmatch(fz_hash) is None:
        raise ValueError("fz_hash must be 64 lowercase hex characters")
    now = clock() if clock else datetime.datetime.now(datetime.timezone.utc)
    seed = seed_from_clock(now)
    with open(script_path, "rb") as fh:
        script_hash = sha256_hex(fh.read())
    data = call_list_text.encode("ascii")
    rec = {
        "pilot_structural_counts": dict(pilot_counts),
        "round_number": round_number,
        "round_result": round_result,
        "final_decision": final_decision,
        "fz_hash": fz_hash,
        "fz_hash_provenance": "researcher-supplied, unverified: no FZ hash contract is adopted (proposal P-2)",
        "decision_script_sha256": script_hash,
        "utc_timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": seed,
        "interpreter_version": platform.python_version(),
        "call_list_sha256": sha256_hex(data),
        "call_list_length_bytes": len(data),
        "call_list_lines": call_list_text.count("\n"),
    }
    if register_sha256 is not None:
        rec["register_canonical_sha256"] = register_sha256
    return rec


def write_decision_record(record, path):
    """Write the canonical record (UTF-8, LF); refuses to overwrite. Returns the SHA-256 of the written bytes."""
    data = canonical_json(record).encode("utf-8")
    with open(path, "xb") as fh:                 # FileExistsError if the record exists: the first run stands
        fh.write(data)
    return sha256_hex(data)


def _log(path, entry):
    with open(path, "ab") as fh:
        fh.write(canonical_json(entry).encode("utf-8"))


def main(argv=None, *, clock=None, out=None):
    """Command line: compute the final pilot decision from counts, write the decision record, print its SHA-256.
    Exit codes: 0 record written; 2 invalid input; 3 the round outcome is not final; 4 the record already exists."""
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="s7_decision.py")
    ap.add_argument("--fz-manifest", required=True, help="PARAMETER_FREEZE_MANIFEST_FZ_v1.json (thresholds, R, pilot extras)")
    ap.add_argument("--fz-hash", required=True, help="the FZ hash to record (64 lowercase hex)")
    ap.add_argument("--round", required=True, type=int, dest="round_number")
    ap.add_argument("--round-calls", required=True, type=int, help="the number of calls actually recorded in the round")
    ap.add_argument("--counts-json", required=True, help='{"SF1":0,"SF2":0,"SF3":0,"SF4":0,"SF5":0,"UNP":0}')
    ap.add_argument("--template-ids", required=True, help="file with the canonical template IDs, one per line, canonical order")
    ap.add_argument("--record", required=True, help="path of the decision record to create (never overwritten)")
    args = ap.parse_args(argv)
    now = clock() if clock else datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    logpath = args.record + ".runlog"

    def finish(code, status, **extra):
        _log(logpath, dict(status=status, utc_timestamp=stamp, **extra))
        print(status, file=out)
        return code

    try:
        with open(args.fz_manifest, "rb") as fh:
            manifest = json.loads(fh.read().decode("utf-8"))
        reg_hash = register_hash(manifest)
        if reg_hash != manifest.get("register_canonical_sha256"):
            return finish(2, "invalid input: the register hash of the FZ manifest does not match its recorded value")
        reg = manifest["register"]
        pilot = reg["row18_pilot"]
        planned = MIN_PILOT_CALLS + pilot["pilot_extras"]
        counts = json.loads(args.counts_json)
        with open(args.template_ids, "rb") as fh:
            ids = fh.read().decode("ascii").split("\n")
        if ids and ids[-1] == "":
            ids.pop()
        if not ids or any(TEMPLATE_ID_RX.fullmatch(i) is None for i in ids) or len(set(ids)) != len(ids):
            return finish(2, "invalid input: template IDs must be unique canonical IDs, one per line")
        if ids != expected_template_ids(reg) or 4 * reg["row12_R"] * len(ids) != reg["row13_C_max_planned_main_call_slots"]:
            return finish(2, "invalid input: the template list is not the canonical list implied by the frozen register")
        rr = round_outcome(counts, args.round_calls, pilot["f_S"], pilot["theta_unp"], planned_calls=planned)
        final = final_pilot_decision(rr, args.round_number)
        text = call_list(ids, reg["row12_R"])
        rec = build_decision_record(pilot_counts=counts, round_result=rr, final_decision=final, round_number=args.round_number,
                                    fz_hash=args.fz_hash, script_path=os.path.abspath(__file__), call_list_text=text,
                                    register_sha256=reg_hash, clock=lambda: now)
    except RoundNotFinal as e:
        return finish(3, f"not final: {e}")
    except (ValueError, KeyError, TypeError, OSError, UnicodeError) as e:
        detail = str(e) if isinstance(e, ValueError) and not isinstance(e, UnicodeError) else type(e).__name__
        return finish(2, f"invalid input: {detail}")
    try:
        digest = write_decision_record(rec, args.record)
    except FileExistsError:
        return finish(4, "refused: the decision record exists; the first run stands")
    print(f"final_decision {final}", file=out)
    print(f"seed {rec['seed']}", file=out)
    print(f"call_list_sha256 {rec['call_list_sha256']}", file=out)
    print(f"decision_record_sha256 {digest}", file=out)
    _log(logpath, dict(status="written", utc_timestamp=stamp, decision_record_sha256=digest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
