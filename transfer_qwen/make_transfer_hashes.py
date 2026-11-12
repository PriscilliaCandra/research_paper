"""Writes TRANSFER_HASHES.json: SHA-256 of every file that must be identical on the second laptop. Run on the laptop that holds the reference copy."""
import hashlib, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RP = "results_package_2026-10-13"
files = {}


def add(rel):
    p = os.path.join(ROOT, *rel.split("/"))
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    files[rel] = h


idx = json.load(open(os.path.join(ROOT, RP, "fz_freeze", "FZ_INDEX_L-B_v3.json"), encoding="utf-8"))
for rel in idx:
    add(rel)
for d in ("step7", "step8", "step9", "step10"):
    for f in sorted(os.listdir(os.path.join(ROOT, "research_design", d))):
        if f.endswith(".py"):
            add(f"research_design/{d}/{f}")
for root, dirs, fs in os.walk(os.path.join(ROOT, "research_design", "step8_output_fz_v3")):
    for f in fs:
        add(os.path.relpath(os.path.join(root, f), ROOT).replace("\\", "/"))
for rel in (f"{RP}/human_evidence/step9_waiver_fz_v3.json", f"{RP}/human_evidence/h5_authorization_main_qwen.json",
            f"{RP}/human_evidence/decision_record_anchor_qwen.json", f"{RP}/decision_records/pilot_round_2_qwen_decision_record.json",
            f"{RP}/fz_freeze/FZ_INDEX_L-B_v3.json"):
    add(rel)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "TRANSFER_HASHES.json")
json.dump({"note": "files that must be byte-identical on the second laptop", "files": files}, open(out, "w", encoding="utf-8"), indent=0, sort_keys=True)
print("files hashed:", len(files))
