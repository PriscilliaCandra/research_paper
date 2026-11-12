"""Step 11 'final freeze manifest' writer (specification 21.1 row 11) -- decision record 12.31. NOT RUN.
Lists the SHA-256 of the actual files that define the experiment at the time of the final pilot decision (specification, FZ-v2 for the Ollama path, Step 7 freeze manifest v2
and its files, Step 8 manifest, Implementation A and B, Step 10 code, the decision record of the script). It does NOT define an
'FZ hash' (proposal P-2 is not adopted): the decision script's supplied fz_hash stays 'researcher-supplied, unverified'. Refuses to
overwrite; refuses unless the decision record says final_decision PASS (a STOP / STOP-U record gets no freeze manifest).
Coverage of the FZ index and the waiver (owner decision 12.72, condition 1): the files under research_design/ are listed in "files" (paths relative to research_design/).
The files OUTSIDE research_design/ that the gates read are listed in "files_outside_research_design" (paths relative to the repository root): the FZ list L-B v2, the FZ index
(its SHA-256 is the official FZ hash, also given in "fz_hash") and the H1 waiver of the FZ-v2 build. The pilot decision record is hashed in "decision_record_sha256". The H5 authorization of the
main run is NOT hashed here: it is created later, on the owner's final confirmation, and is recorded in the run provenance. "fz_hash" is the SHA-256 of the FZ index file (E-1; official FZ-v2 hash of decision record 12.66); "fz_hash_contract" is the older P-2 label and is left unchanged. Nothing outside these lists is covered by this manifest.
The anchor ID of the decision record (MD-09, specification section 10, owner decision 12.74 D) is recorded in the key "decision_anchor" (the file hash of the anchor evidence plus its four fields), the anchor evidence
file and the kept OpenTimestamps receipt are hashed in "files_outside_research_design", and the manifest is refused unless the anchor evidence passes the gate check of s10_runner.anchor_reasons for this record.
Usage: python -B -I s11_final_freeze_manifest.py --decision-record <json> --decision-anchor <anchor evidence json> --out <new file>"""
import argparse, hashlib, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); RD = os.path.normpath(os.path.join(HERE, ".."))
REPO = os.path.dirname(RD)
OUTSIDE = ["results_package_2026-10-13/fz_freeze/FZ_FILE_LIST_L-B_v2.txt", "results_package_2026-10-13/fz_freeze/FZ_INDEX_L-B_v2.json",
           "results_package_2026-10-13/human_evidence/step9_waiver_fz_v2.json", "results_package_2026-10-13/decision_records/pilot_round_2_decision_record.json.ots"]
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import s10_runner as RN  # the gate check of the anchor evidence is reused, not re-implemented

def fsha(p):
    with open(p, "rb") as f: return hashlib.sha256(f.read()).hexdigest()

def build(record_path, anchor_path=None):
    with open(record_path, "rb") as f: rec = json.loads(f.read().decode("utf-8"))
    if rec.get("final_decision") != "PASS":
        raise ValueError("final pilot decision is not PASS: no final freeze manifest")
    why = RN.anchor_reasons(anchor_path, record_path)
    if why:
        raise ValueError("decision record anchor evidence is not valid: " + "; ".join(why))
    ap = os.path.abspath(anchor_path)
    if os.path.splitdrive(ap)[0].lower() != os.path.splitdrive(REPO)[0].lower() or os.path.relpath(ap, REPO).startswith(".."):
        raise ValueError("the anchor evidence file must be inside the repository (it is hashed with a repository-relative path)")
    anchor_rel = os.path.relpath(ap, REPO).replace("\\", "/")
    rel = ["SCENARIO_CONSTRUCTION_SPECIFICATION_v2_7_DRAFT.md", "PARAMETER_FREEZE_MANIFEST_FZ_v2.json", "PARAMETER_JUSTIFICATION_RECORD_DRAFT.md",
           "METHODOLOGY_FREEZE_MANIFEST_v2_7.json", "step7/STEP7_PROMPT_SPECIFICATION_FREEZE_MANIFEST_v2.json", "implementation_a/impl_a.py", "step9/impl_b.py",
           "step10/s10_scoring.py", "step10/s10_analysis.py", "step10/s10_runner.py", "step10/s10_report.py", "step10/s12_coding.py",
           # proposed P13 additions (owner decision pending): code that decides whether a run may start, how it is verified, resumed, decided and coded
           "step8/s8_verify.py", "step9/s9_blindcheck.py", "step10/s10_integrity.py", "step10/s10_resume.py", "step10/s11_pilot.py",
           "step10/s12_cli.py", "step10/s11_final_freeze_manifest.py", "step10/fz_index.py", "step8/s8_build.py"]
    with open(os.path.join(RD, "step7", "STEP7_PROMPT_SPECIFICATION_FREEZE_MANIFEST_v2.json"), "rb") as f:
        fm = json.loads(f.read())
    rel += ["step7/" + n for n in sorted(fm["freeze_set_files"])]
    rel += ["step8_output_fz_v2/" + d + "/manifest.json" for d in sorted(os.listdir(os.path.join(RD, "step8_output_fz_v2"))) if d.startswith("step8_build_")]
    return {"manifest_kind": "final freeze manifest (21.1 step 11)", "carries_no_self_hash": True, "fz_hash_contract": "none adopted (P-2 open)",
            "decision_record_sha256": fsha(record_path), "decision_record_fields": {k: rec[k] for k in ("seed", "call_list_sha256", "utc_timestamp", "final_decision") if k in rec},
            "fz_hash": fsha(os.path.join(REPO, *OUTSIDE[1].split("/"))),
            "files": {r: fsha(os.path.join(RD, *r.split("/"))) for r in sorted(set(rel))},
            "decision_anchor": dict(RN.anchor_summary(anchor_path), evidence_file=anchor_rel),
            "files_outside_research_design": {r: fsha(os.path.join(REPO, *r.split("/"))) for r in sorted(set(OUTSIDE + [anchor_rel]))}}

def main(argv=None):
    a = argparse.ArgumentParser(); a.add_argument("--decision-record", required=True); a.add_argument("--decision-anchor", required=True); a.add_argument("--out", required=True); a = a.parse_args(argv)
    try: m = build(a.decision_record, a.decision_anchor)
    except ValueError as e: print("refused:", e); return 2
    with open(a.out, "xb") as f: f.write((json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8"))
    print("written", a.out); return 0

if __name__ == "__main__": sys.exit(main())
