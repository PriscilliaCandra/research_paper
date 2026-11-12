"""INACTIVE proposal P13 (owner decision pending): guard test for the file list of s11_final_freeze_manifest.py.
Copy to research_design/step10/test_step11_file_list.py ONLY together with the diff P13_step11_file_list.diff.INACTIVE.
It builds the manifest from a SYNTHETIC PASS record (written to a temporary folder; it is not a decision record) and requires that every project module
reachable by import from the run lifecycle (runner, resume, integrity, pilot glue, report, coding, evidence evaluator, build verifier) is hashed.
Run: python -B -I -m unittest discover -s research_design/step10 -p test_step11_file_list.py"""
import ast
import json
import os
import sys
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s11_final_freeze_manifest as FM  # noqa: E402

ENTRY = ["step10/s10_runner.py", "step10/s10_resume.py", "step10/s10_integrity.py", "step10/s11_pilot.py", "step10/s10_report.py", "step10/s12_cli.py",
         "step10/s11_final_freeze_manifest.py", "step9/s9_blindcheck.py", "step8/s8_verify.py"]
DIRS = ["step7", "step8", "step9", "step10", "implementation_a"]


def project_modules():
    mods = {}
    for d in DIRS:
        for f in os.listdir(os.path.join(RD, d)):
            if f.endswith(".py") and not f.startswith("test_"):
                mods.setdefault(f[:-3], []).append(f"{d}/{f}")
    return mods


def reachable():
    mods, seen, todo = project_modules(), set(), list(ENTRY)
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        tree = ast.parse(open(os.path.join(RD, p), encoding="utf-8").read())
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                names.add(n.module.split(".")[0])
        for m in names & set(mods):
            todo += mods[m]
    return seen


def repo_tmp():
    """a temporary folder INSIDE the repository (the anchor evidence must be repository-relative); the caller removes it"""
    return tempfile.mkdtemp(prefix="_ffm_test_", dir=FM.REPO)


def synthetic(tmp, decision="PASS"):
    """a synthetic PASS (or other) record with valid synthetic anchor evidence, in a temporary folder (neither is a decision record or evidence of the study)"""
    rec = os.path.join(tmp, "synthetic_record.json")
    with open(rec, "w") as f:
        json.dump({"final_decision": decision, "seed": 1, "call_list_sha256": "0" * 64, "utc_timestamp": "2026-10-16T03:55:17Z"}, f)
    with open(rec, "rb") as f:
        import hashlib
        h = hashlib.sha256(f.read()).hexdigest()
    anc = os.path.join(tmp, "synthetic_anchor.json")
    with open(anc, "w") as f:
        json.dump({"anchor_kind": "external_timestamped", "anchored_at_utc": "2026-10-16T04:18:20Z", "decision_record_sha256": h, "reference": "synthetic reference for tests"}, f)
    return rec, anc


def hashed_files(build=FM.build):
    tmp = repo_tmp()
    try:
        rec, anc = synthetic(tmp)
        return set(build(rec, anc)["files"])
    finally:
        shutil.rmtree(tmp, True)


class TestFileListCoversTheRunLifecycle(unittest.TestCase):
    def test_every_reachable_module_is_hashed(self):
        missing = sorted(reachable() - hashed_files())
        self.assertEqual(missing, [], "modules reachable from the run lifecycle but not in the final freeze manifest")

    def test_proposals_that_are_not_adopted_are_not_hashed(self):
        files = hashed_files()
        self.assertNotIn("step7/s7_outcomes.py", files)                     # PROPOSED 18.2 policy, not adopted
        self.assertFalse([f for f in files if "/test_" in f])               # tests are verification evidence, not part of the freeze set


class TestFzV2Coverage(unittest.TestCase):
    """owner decision 12.72: the manifest names FZ-v2, the Step 7 manifest v2 and the FZ-v2 build, and states how the FZ index and the waiver are covered"""
    @classmethod
    def setUpClass(cls):
        cls.tmp = repo_tmp()
        cls.rec, cls.anc = synthetic(cls.tmp)
        cls.m = FM.build(cls.rec, cls.anc)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_fz_v2_replaces_fz_v1_everywhere(self):
        files = set(self.m["files"])
        self.assertIn("PARAMETER_FREEZE_MANIFEST_FZ_v2.json", files)
        self.assertIn("step7/STEP7_PROMPT_SPECIFICATION_FREEZE_MANIFEST_v2.json", files)
        self.assertTrue([f for f in files if f.startswith("step8_output_fz_v2/")])
        self.assertFalse([f for f in files if "FZ_v1" in f or "MANIFEST_v1" in f or f.startswith("step8_output/")])

    def test_fz_index_list_and_waiver_are_covered_outside_research_design(self):
        out = self.m["files_outside_research_design"]
        self.assertEqual(sorted(out), sorted(FM.OUTSIDE + [self.m["decision_anchor"]["evidence_file"]]))
        self.assertEqual(self.m["fz_hash"], out["results_package_2026-10-13/fz_freeze/FZ_INDEX_L-B_v2.json"])
        self.assertEqual(self.m["fz_hash"], "3f558fa73130e71f4ae625a7640ac072c29feff2ff68a3a77c60b1fdfe721251")      # the official FZ-v2 hash of 12.66

    def test_a_record_that_is_not_pass_gets_no_manifest(self):
        tmp = repo_tmp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p, anc = synthetic(tmp, "STOP")
        with self.assertRaises(ValueError):
            FM.build(p, anc)

    def test_the_writer_refuses_to_overwrite_and_a_non_pass_record(self):
        with tempfile.TemporaryDirectory() as tmp:                         # the output may be anywhere; the anchor evidence must be inside the repository
            out = os.path.join(tmp, "m.json")
            self.assertEqual(FM.main(["--decision-record", self.rec, "--decision-anchor", self.anc, "--out", out]), 0)
            with self.assertRaises(FileExistsError):
                FM.main(["--decision-record", self.rec, "--decision-anchor", self.anc, "--out", out])
            self.assertEqual(json.load(open(out))["fz_hash"], self.m["fz_hash"])


class TestAnchorIdInTheFinalFreezeManifest(unittest.TestCase):
    """owner decision 12.74 D / specification section 10: the anchor ID is recorded in the final freeze manifest"""
    def setUp(self):
        self.tmp = repo_tmp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.rec, self.anc = synthetic(self.tmp)

    def test_the_anchor_identity_and_the_kept_receipt_are_in_the_manifest(self):
        m = FM.build(self.rec, self.anc)
        a = json.load(open(self.anc))
        self.assertEqual({k: m["decision_anchor"][k] for k in a}, a)
        import hashlib
        self.assertEqual(m["decision_anchor"]["sha256"], hashlib.sha256(open(self.anc, "rb").read()).hexdigest())
        self.assertIn("results_package_2026-10-13/decision_records/pilot_round_2_decision_record.json.ots", m["files_outside_research_design"])
        self.assertIn(m["decision_anchor"]["evidence_file"], m["files_outside_research_design"])

    def test_no_manifest_without_valid_anchor_evidence(self):
        with self.assertRaises(ValueError):
            FM.build(self.rec)                                                   # no evidence at all
        with self.assertRaises(ValueError):
            FM.build(self.rec, os.path.join(self.tmp, "missing.json"))
        bad = json.load(open(self.anc))
        for name, change in (("another hash", {"decision_record_sha256": "1" * 64}), ("early", {"anchored_at_utc": "2026-10-16T03:00:00Z"}), ("kind", {"anchor_kind": "hash_only"}),
                             ("short reference", {"reference": "x"})):
            with self.subTest(name):
                p = os.path.join(self.tmp, name.replace(" ", "_") + ".json")
                json.dump(dict(bad, **change), open(p, "w"))
                with self.assertRaises(ValueError):
                    FM.build(self.rec, p)

    def test_anchor_evidence_outside_the_repository_is_refused(self):
        with tempfile.TemporaryDirectory() as outside:
            rec, anc = synthetic(outside)
            with self.assertRaises(ValueError):
                FM.build(rec, anc)

    def test_the_command_line_requires_the_anchor(self):
        with self.assertRaises(SystemExit):
            FM.main(["--decision-record", self.rec, "--out", os.path.join(self.tmp, "m.json")])


if __name__ == "__main__":
    unittest.main()
