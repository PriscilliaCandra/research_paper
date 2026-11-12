"""Pilot round evaluation and final pilot decision (specification 13.6, 13.7, 10, 21.1 step 11) -- decision record 12.32. Glue only: counts come from the
run folder (s10_scoring, counts only, no ground-truth agreement, 13.7); the outcome and the record come from s7_decision (the frozen decision script).
  report : writes counts, round outcome (PASS / FAIL-S / FAIL-U) as JSON; refuses to overwrite.
  decide : calls the decision script; FAIL-S in round 1 is 'not final' (exit 3, nothing written); the record is written once.
counts.counts is the decision vector of s10_scoring.decision_type (working decision MD-01, not ratified; conflict C-31 open); the provisional primary order only decides the recorded primary type (multi_flag_calls is reported).
--fz-manifest names the FZ manifest in use (for example PARAMETER_FREEZE_MANIFEST_FZ_v2.json) and is REQUIRED when FZ index enforcement is on: the code never falls back silently to FZ-v1
(decision record 12.68 request; owner instruction after 12.67). With a named manifest, decide also requires it to be listed in the FZ index and the run to carry the same FZ hash.
Usage: python -B -I s11_pilot.py report --run <dir> --round N --fz-manifest <FZ file> --out <new json>
       python -B -I s11_pilot.py decide --run <dir> --round N --fz-manifest <FZ file> --fz-index <index> --fz-hash <64 hex> --record <new json>"""
import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_runner as RN  # noqa: E402
import s10_scoring as SC  # noqa: E402

D = RN.D


def load(run, rnd):
    marks = [m for m in ("RUN_COMPLETE", "RUN_INCOMPLETE") if os.path.exists(os.path.join(run, m))]
    if len(marks) != 1 or marks[0] != "RUN_COMPLETE":
        raise ValueError("a pilot decision needs a COMPLETE pilot run")
    s = json.loads(RN.file_bytes(os.path.join(run, marks[0])))
    if s["scope"] != f"pilot_round_{rnd}":
        raise ValueError("run scope does not match the round")
    recs = [json.loads(l) for l in RN.file_bytes(os.path.join(run, "calls.jsonl")).decode().split("\n") if l]
    return s, recs


def report(run, rnd):
    s, recs = load(run, rnd)
    pc = SC.pilot_counts(recs)
    pil = RN.jl(RN.FZ_PATH)["register"]["row18_pilot"]
    rr = D.round_outcome(pc["counts"], pc["n_calls"], pil["f_S"], pil["theta_unp"], planned_calls=18 + pil["pilot_extras"])
    return {"round": rnd, "counts": pc, "round_result": rr,
            "statement": "counts only (13.7); no agreement with ground truth and no verdict distribution is computed; counts.counts is the decision vector (MD-01, working technical decision, formal approval pending)"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--run", required=True)
    r.add_argument("--round", type=int, required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--fz-manifest", help="the FZ manifest in use; REQUIRED when FZ index enforcement is on")
    d = sub.add_parser("decide")
    d.add_argument("--run", required=True)
    d.add_argument("--round", type=int, required=True)
    d.add_argument("--fz-hash", required=True)
    d.add_argument("--fz-index", help="the FZ index file (E-1): its SHA-256 must equal --fz-hash and its entries must match the files on disk")
    d.add_argument("--record", required=True)
    d.add_argument("--fz-manifest", help="the FZ manifest in use; REQUIRED when FZ index enforcement is on; it must be listed in the FZ index")
    a = ap.parse_args(argv)
    if a.fz_manifest:
        if not os.path.isfile(a.fz_manifest):
            print("refused: the FZ manifest file does not exist")
            return 2
        try:
            fzm = RN.jl(os.path.abspath(a.fz_manifest))
            if D.register_hash(fzm) != fzm.get("register_canonical_sha256"):
                raise ValueError("register hash does not match its recorded value")
        except (ValueError, KeyError, TypeError, OSError) as e:
            print("refused: the FZ manifest is not valid:", e)
            return 2
        RN.FZ_PATH = os.path.abspath(a.fz_manifest)
    elif RN.FZ_INDEX_ENFORCED:
        print("refused (E-1): the FZ manifest in use is not named (--fz-manifest is required)")
        return 2
    try:
        rep = report(a.run, a.round)
    except (ValueError, OSError) as e:
        print("refused:", e)
        return 2
    if a.cmd == "report":
        with open(a.out, "xb") as f:
            f.write(RN.cj(rep))
        print("written", a.out, rep["round_result"]["outcome"])
        return 0
    if RN.FZ_INDEX_ENFORCED:
        why = RN.fz_index_reasons({"index_path": a.fz_index, "hash": a.fz_hash, "must_list": [os.path.relpath(RN.FZ_PATH, RN.REPO_ROOT).replace("\\", "/")]})
        try:
            if load(a.run, a.round)[0].get("fz_hash") != a.fz_hash:
                why.append("the run carries another FZ hash than --fz-hash")
        except (ValueError, OSError) as e:
            why.append(f"the run cannot be read: {e}")
        if why:
            print("refused (E-1):", *why, sep="\n  - ")
            return 2
    fz = RN.jl(RN.FZ_PATH)
    with tempfile.TemporaryDirectory() as td:
        ids = os.path.join(td, "ids.txt")
        with open(ids, "wb") as f:
            f.write(("\n".join(D.expected_template_ids(fz["register"])) + "\n").encode("ascii"))
        return D.main(["--fz-manifest", RN.FZ_PATH, "--fz-hash", a.fz_hash, "--round", str(a.round), "--round-calls", str(rep["counts"]["n_calls"]),
                       "--counts-json", json.dumps(rep["counts"]["counts"]), "--template-ids", ids, "--record", a.record])


if __name__ == "__main__":
    sys.exit(main())
