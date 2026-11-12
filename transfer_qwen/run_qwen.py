"""Portable launcher for the Qwen second-model main run (FZ-v3). Standard library only.
Usage (from the repository folder, in Command Prompt):
    python transfer_qwen\\run_qwen.py cek                  hash check + Ollama check + gate dry run (nothing is run)
    python transfer_qwen\\run_qwen.py jalan NAMA_FOLDER    live run of the 2,496 calls into results_package_2026-10-13\\runs\\NAMA_FOLDER
The paths are taken from the location of this file, so the folder may be anywhere as long as its layout is unchanged.
The runner itself (research_design/step10/s10_runner.py) is not modified; this file only passes the arguments and asks Windows not to
idle-sleep while the run is going (SetThreadExecutionState; no setting is changed)."""
import hashlib, json, os, runpy, subprocess, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RP = os.path.join(ROOT, "results_package_2026-10-13")
STEP10 = os.path.join(ROOT, "research_design", "step10")
FZ_HASH = "9751cc5d40ad8901cfd2376fd0676674f471b5d1e4750b446beabf5f5b559344"
EXPECTED_OLLAMA = "0.40.11"
EXPECTED_DIGEST_PREFIX = "357c53fb659c"


def runner_args(out_dir, live):
    a = ["--scope", "main",
         "--build", os.path.join(ROOT, "research_design", "step8_output_fz_v3", "step8_build_84456106c215"),
         "--out", out_dir,
         "--evidence", os.path.join(RP, "human_evidence", "step9_waiver_fz_v3.json"),
         "--authorization", os.path.join(RP, "human_evidence", "h5_authorization_main_qwen.json"),
         "--pilot-record", os.path.join(RP, "decision_records", "pilot_round_2_qwen_decision_record.json"),
         "--fz-index", os.path.join(RP, "fz_freeze", "FZ_INDEX_L-B_v3.json"),
         "--fz-hash", FZ_HASH,
         "--fz-manifest", os.path.join(ROOT, "research_design", "PARAMETER_FREEZE_MANIFEST_FZ_v3.json"),
         "--decision-anchor", os.path.join(RP, "human_evidence", "decision_record_anchor_qwen.json")]
    return a + (["--live"] if live else [])


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_hashes():
    man = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "TRANSFER_HASHES.json"), encoding="utf-8"))
    bad = []
    for rel, want in man["files"].items():
        p = os.path.join(ROOT, *rel.split("/"))
        if not os.path.isfile(p):
            bad.append((rel, "MISSING"))
        elif sha(p) != want:
            bad.append((rel, "DIFFERENT"))
    print(f"hash check: {len(man['files'])} files, problems: {len(bad)}")
    for rel, why in bad[:25]:
        print("  ", why, rel)
    return not bad


def check_ollama():
    try:
        v = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=5)).get("version")
        tags = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5)).get("models", [])
    except Exception as e:
        print("Ollama server NOT reachable:", type(e).__name__, "- start it in another window with:  ollama serve")
        return False
    q = [m for m in tags if m.get("name") == "qwen2.5:3b"]
    ok = v == EXPECTED_OLLAMA and len(q) == 1 and q[0].get("digest", "").startswith(EXPECTED_DIGEST_PREFIX)
    print(f"Ollama version {v} (need {EXPECTED_OLLAMA}); qwen2.5:3b digest {q[0]['digest'][:12] if q else 'NOT INSTALLED'} (need {EXPECTED_DIGEST_PREFIX}) -> {'OK' if ok else 'PROBLEM'}")
    return ok


def out_dir_for(name):
    return os.path.join(RP, "runs", name)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("cek", "jalan"):
        sys.exit(__doc__)
    print("python", sys.version.split()[0], "| repository folder:", ROOT)
    if sys.argv[1] == "cek":
        a = check_hashes()
        b = check_ollama()
        print("gate dry run (nothing is run):")
        r = subprocess.run([sys.executable, "-B", "-I", os.path.join(STEP10, "s10_runner.py")] + runner_args(out_dir_for("_dry_run_only_not_created"), live=False), cwd=STEP10)
        print("RESULT:", "READY" if (a and b and r.returncode == 0) else "NOT READY, fix the lines above first")
        return
    if len(sys.argv) < 3:
        sys.exit("give a new folder name, e.g.:  python transfer_qwen\\run_qwen.py jalan main_live_qwen_run3_laptop2")
    out = out_dir_for(sys.argv[2])
    if os.path.exists(out):
        sys.exit("that output folder already exists; give a new name (a run is never overwritten or resumed here)")
    if not (check_hashes() and check_ollama()):
        sys.exit("checks failed; nothing was started")
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    sys.argv = ["s10_runner.py"] + runner_args(out, live=True)
    sys.dont_write_bytecode = True
    os.chdir(STEP10)
    sys.path.insert(0, STEP10)
    try:
        runpy.run_path(os.path.join(STEP10, "s10_runner.py"), run_name="__main__")
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


if __name__ == "__main__":
    main()
