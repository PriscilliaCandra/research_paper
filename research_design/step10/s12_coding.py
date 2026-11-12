"""Post-run S2/S3 coding preparation and tabulation (specification 8.2 PG11b application, 13.6.1 activity C, 16, 16.3) -- decision record 12.31.
NOT RUN on real data. Pure code plus file writers that refuse to overwrite.

prepare_coding(): preconditions (a main run folder, the primary analysis exists and its hash is recorded BEFORE any coding material is
written); builds the coder bundle from the stored responses with the frozen bundle builder (s7_bundle.build_coder_bundle, Option E
IDs); the PG11b application check is the builder's own check (names, bodies, patterns PT1, PT3-PT8b; literal text, owner decision
2026-10-13); a failure writes ONLY a failure file and no coding material (section 8.2: coding waits). Re-code sample and order come from
s7_recode (delegated values, pending advisor review) with the bundle hash; the lists are written and hashed here, before any code exists.
ELIGIBILITY (provisional, NOT adopted, package D-10): recorded calls with no SF flag and a parseable verdict (satisfied, violated,
unclear). UNP and SF calls are counted and reported beside the tables (TH16), never coded.
tabulate(): counts of the coder-assigned codes by family and condition with the denominators of 16.1; no code is invented, inferred
or changed; s3_ref comes from the frozen lexical rule applied to the Comments by code.
"""
import csv
import hashlib
import io
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.normpath(os.path.join(HERE, ".."))
for d in (HERE, os.path.join(RD, "step7")):
    sys.path.insert(0, d)
sys.dont_write_bytecode = True
import s10_scoring as SC  # noqa: E402
import s7_bundle as B  # noqa: E402
import s7_parser as P  # noqa: E402
import s7_recode as RC  # noqa: E402

S2_COLS = ("s2_evidence", "s2_prov", "s2_values", "s2_arith", "s2_rel")
S3_COLS = ("s3_stance",)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def cj(o):
    return (json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def rbytes(p):
    with open(p, "rb") as f:
        return f.read()


def write_new(p, data):
    with open(p, "xb") as f:
        f.write(data)


def _csv(header, rows):
    s = io.StringIO()
    w = csv.writer(s, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return s.getvalue().encode("utf-8")


def cell_of(call_key):
    tid, cond, _ = call_key.split(",")
    return f"{tid.split('-')[0]}:{cond}"


def prepare_coding(run_dir, build, analysis_dir, out_dir):
    status = [f for f in ("RUN_COMPLETE", "RUN_INCOMPLETE") if os.path.exists(os.path.join(run_dir, f))]
    if len(status) != 1:
        raise ValueError("not a finished run folder")
    if json.loads(rbytes(os.path.join(run_dir, status[0])))["scope"] != "main":
        raise ValueError("coding applies to the main run only (pilot content is never opened, 13.6.1)")
    ap = os.path.join(analysis_dir, "analysis.json")
    if not os.path.exists(ap):
        raise ValueError("primary scoring (analysis.json) must exist and be hashed before coding (13.6.1 activity C)")
    analysis_hash = sha(rbytes(ap))
    reg_hash = sha(rbytes(os.path.join(build, "main", "registry.json")))
    recs = [json.loads(l) for l in rbytes(os.path.join(run_dir, "calls.jsonl")).decode("utf-8").split("\n") if l]
    items, pop = {}, {}
    for r in recs:
        cl = SC.classify(r)
        c = pop.setdefault(cell_of(r["call_key"]), Counter())
        c["recorded"] += 1
        if cl["outcome"] in ("satisfied", "violated", "unclear"):
            c["D_P"] += 1
            with open(os.path.join(build, "main", "prompts", f"{r['template_id']}-{r['condition']}.txt"), "rb") as f:
                items[r["call_key"]] = (f.read().decode("ascii"), r["response_text"])
        elif cl["outcome"] == "UNP":
            c["UNP"] += 1
        else:
            c["SF"] += 1
    os.makedirs(out_dir)                                              # FileExistsError: nothing is overwritten
    try:
        files, mapping = B.build_coder_bundle(items, reg_hash)       # PG11b application check = the builder's checks
    except B.BuildRejected as e:
        write_new(os.path.join(out_dir, "PG11B_APPLICATION_FAILED.json"), cj({"coding": "blocked", "reason": str(e)[:2000],
                                                                              "analysis_sha256": analysis_hash}))
        return {"status": "PG11B_FAILED", "reason": str(e)[:300]}
    ids = list(files)
    bh = RC.bundle_hash(ids)
    cells = {}
    for oid, key in mapping.items():
        cells.setdefault(cell_of(key), []).append(oid)
    sample = RC.select_sample(cells, bh)
    order = {c: RC.recode_order(v, bh) for c, v in sample.items()}
    B.write_bundle(files, os.path.join(out_dir, "coder_bundle"))
    write_new(os.path.join(out_dir, "MAPPING_HELD_SEPARATELY.json"), cj(mapping))
    write_new(os.path.join(out_dir, "coding_sheet_firstpass_BLANK.csv"),
              _csv(["opaque_id"] + [f"coder1_{c}" for c in S2_COLS + S3_COLS] + ["coder1_notes"], [[i] + [""] * 6 for i in ids]))
    rec_rows = [[i] for c in sorted(order) for i in order[c]]
    write_new(os.path.join(out_dir, "recode_selection_and_order.csv"), _csv(["opaque_id"], rec_rows))
    write_new(os.path.join(out_dir, "coding_population.json"), cj({k: dict(v) for k, v in sorted(pop.items())}))
    man = {"run_status": status[0], "registry_sha256": reg_hash, "analysis_sha256": analysis_hash, "bundle_hash": bh, "coder_items": len(ids),
           "recode_sample_sizes": {c: len(v) for c, v in sorted(sample.items())},
           "recode_values": {"floor": RC.RECODE_FLOOR, "fraction": f"{RC.RECODE_NUM}/{RC.RECODE_DEN}", "delay_days": RC.RECODE_DELAY_DAYS,
                             "status": "delegated, advisor review pending"},
           "eligibility": "PROVISIONAL (not adopted): non-SF calls with a parseable verdict",
           "files": {n: sha(rbytes(os.path.join(out_dir, n))) for n in ("MAPPING_HELD_SEPARATELY.json", "coding_sheet_firstpass_BLANK.csv",
                                                                        "recode_selection_and_order.csv", "coding_population.json")}}
    write_new(os.path.join(out_dir, "coding_manifest.json"), cj(man))
    return {"status": "READY", "manifest": man}


def read_sheet(path, prefix):
    rows = list(csv.DictReader(io.StringIO(rbytes(path).decode("utf-8"))))
    return {r["opaque_id"]: {c: r[f"{prefix}_{c}"].strip() for c in S2_COLS + S3_COLS} for r in rows}


def tabulate(prep_dir, run_dir, codes):
    """codes: {opaque_id: {s2_evidence,...,s3_stance}} (first-pass codes as entered by the coder). Returns counts by (family, condition).
    Denominators (16.1): s2_evidence over D_P(c) (NC included); s2_prov, s2_values, s2_arith (except AN), s2_rel over responses where the
    coder entered a code (the applicable population); AN against D_P. S3 only for conditions C and D."""
    mapping = json.loads(rbytes(os.path.join(prep_dir, "MAPPING_HELD_SEPARATELY.json")))
    pop = json.loads(rbytes(os.path.join(prep_dir, "coding_population.json")))
    resp = {}
    for l in rbytes(os.path.join(run_dir, "calls.jsonl")).decode("utf-8").split("\n"):
        if l:
            r = json.loads(l)
            resp[r["call_key"]] = r["response_text"]
    missing = [o for o in mapping if o not in codes]
    if missing:
        raise ValueError(f"{len(missing)} coder item(s) have no code: coding incomplete")
    out = {}
    for oid, key in mapping.items():
        cell = cell_of(key)
        cond = cell.split(":")[1]
        c = codes[oid]
        t = out.setdefault(cell, {"D_P": 0, "population": pop[cell], **{k: Counter() for k in S2_COLS + ("s3_stance", "s3_ref", "derived", "correct_explicit_verification")}})
        t["D_P"] += 1
        for k in S2_COLS:
            if c[k]:
                t[k][c[k]] += 1
        if cond in "CD":
            com = P.comments_of(resp[key])
            ref = P.s3_ref(com or "")
            t["s3_ref"][ref] += 1
            if c["s3_stance"]:
                t["s3_stance"][c["s3_stance"]] += 1
                st = c["s3_stance"]
                if st == "AG" and ref == "REF":
                    t["derived"]["agrees with reviewer"] += 1
                elif st == "DG" and ref == "REF":
                    t["derived"]["disagrees with reviewer"] += 1
                elif st == "CQ":
                    t["derived"]["asks clarification"] += 1
                elif st == "MPG":
                    t["derived"]["mixed"] += 1
                elif st == "NS":
                    t["derived"]["no stance"] += 1
                elif st == "NC":
                    t["derived"]["no comment"] += 1
                if st in ("AG", "DG", "MPG") and ref == "NOREF":
                    t["derived"]["discusses compliance without reference to the reviewer"] += 1
        if P.correct_explicit_verification(evidence=c["s2_evidence"], arith=c["s2_arith"], values=c["s2_values"], rel=c["s2_rel"], prov=c["s2_prov"]):
            t["correct_explicit_verification"]["yes"] += 1
    return {cell: {k: (dict(v) if isinstance(v, Counter) else v) for k, v in t.items()} for cell, t in sorted(out.items())}


def recode_agreement(first, second, sample_ids):
    """Raw agreement and cross-tables per variable (no kappa, no reliability claim; OD-3). second: codes of the re-code (recode1_*)."""
    out = {}
    for var in S2_COLS + S3_COLS:
        pairs = [(first[i][var], second[i][var]) for i in sample_ids if i in second]
        out[var] = RC.agreement_table(pairs)
    out["statement"] = "single coder, intra-rater only; delegated values pending advisor review; no inter-rater reliability claim"
    return out
