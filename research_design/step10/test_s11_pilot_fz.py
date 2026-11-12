"""Tests of the explicit FZ manifest in s11_pilot.py (report and decide): the code never falls back silently to FZ-v1 when enforcement is on.
Mock provider only; synthetic index in a temporary root; no network.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_s11_pilot_fz.py"""
import io
import json
import os
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import fz_index as FZ  # noqa: E402
import s10_runner as RN  # noqa: E402
import s11_pilot as PL  # noqa: E402
import test_pilot_flow as PF  # noqa: E402
import test_step10 as T10  # noqa: E402

FZ_V1 = os.path.join(RN.RD, "PARAMETER_FREEZE_MANIFEST_FZ_v1.json")
FZ_V2 = os.path.join(RN.RD, "PARAMETER_FREEZE_MANIFEST_FZ_v2.json")


def jf(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


class Fixture(PF.Base):
    def setUp(self):
        super().setUp()
        for target in ("FZ_PATH", "REPO_ROOT"):
            p = mock.patch.object(RN, target, getattr(RN, target))
            p.start()
            self.addCleanup(p.stop)
        self.root = os.path.join(self.tmp, "root")
        os.makedirs(self.root)
        self.m2 = os.path.join(self.root, "fz_v2.json")
        shutil.copyfile(FZ_V2, self.m2)
        lst = os.path.join(self.tmp, "l.txt")
        with open(lst, "wb") as f:
            f.write(b"fz_v2.json\n")
        self.idx = os.path.join(self.tmp, "idx.json")
        self.h = FZ.write_index(self.root, lst, self.idx)
        self.fz = {"index_path": self.idx, "hash": self.h, "root": self.root}

    def pilot_with_hash(self, scope="pilot_round_2"):
        """a mock pilot run that records the FZ hash of the synthetic index (enforcement on only for the run itself)"""
        self.n += 1
        ev, au = T10.TestRunner.files(self, scope, days=40 + self.n, calls=18)
        out = os.path.join(self.tmp, f"hrun{self.n}")
        with mock.patch.object(RN, "FZ_INDEX_ENFORCED", True), redirect_stdout(io.StringIO()):
            RN.run(scope, T10.BUILD, out, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False, fz=self.fz)
        return out

    def decide(self, run, *extra, rnd="2"):
        rec = os.path.join(self.tmp, "rec%d.json" % len(os.listdir(self.tmp)))
        with mock.patch.object(RN, "FZ_INDEX_ENFORCED", True), mock.patch.object(RN, "REPO_ROOT", self.root), redirect_stdout(io.StringIO()):
            code = PL.main(["decide", "--run", run, "--round", rnd, "--record", rec, *extra])
        return code, rec


class TestExplicitManifest(Fixture):
    def test_fz_v2_differs_from_v1_only_in_item_17(self):
        a, b = jf(FZ_V1)["register"], jf(FZ_V2)["register"]
        self.assertEqual([k for k in a if a[k] != b[k]], ["row17_model_and_settings"])
        self.assertNotEqual(jf(FZ_V1)["register_canonical_sha256"], jf(FZ_V2)["register_canonical_sha256"])

    def test_enforced_report_and_decide_refuse_without_a_named_manifest(self):
        run = self.pilot_with_hash()
        out = os.path.join(self.tmp, "r.json")
        with mock.patch.object(RN, "FZ_INDEX_ENFORCED", True), redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(PL.main(["report", "--run", run, "--round", "2", "--out", out]), 2)
            self.assertEqual(PL.main(["decide", "--run", run, "--round", "2", "--fz-hash", self.h, "--fz-index", self.idx, "--record", out + ".d"]), 2)
        self.assertIn("--fz-manifest is required", buf.getvalue())
        self.assertFalse(os.path.exists(out) or os.path.exists(out + ".d"))

    def test_positive_decide_with_fz_v2_records_the_v2_register_hash(self):
        run = self.pilot_with_hash()
        code, rec = self.decide(run, "--fz-manifest", self.m2, "--fz-hash", self.h, "--fz-index", self.idx)
        self.assertEqual(code, 0)
        r = jf(rec)
        self.assertEqual(r["register_canonical_sha256"], jf(FZ_V2)["register_canonical_sha256"])
        self.assertNotEqual(r["register_canonical_sha256"], jf(FZ_V1)["register_canonical_sha256"])
        self.assertEqual((r["fz_hash"], r["final_decision"], r["round_number"]), (self.h, "PASS", 2))

    def test_positive_report_with_a_named_manifest(self):
        run = self.pilot_with_hash()
        out = os.path.join(self.tmp, "rep.json")
        with mock.patch.object(RN, "FZ_INDEX_ENFORCED", True), redirect_stdout(io.StringIO()):
            self.assertEqual(PL.main(["report", "--run", run, "--round", "2", "--fz-manifest", self.m2, "--out", out]), 0)
        self.assertEqual(jf(out)["round_result"]["outcome"], "PASS")
        self.assertEqual(RN.FZ_PATH, os.path.abspath(self.m2))

    def test_manifest_not_listed_in_the_index_is_refused(self):
        run = self.pilot_with_hash()
        other = os.path.join(self.root, "other.json")
        shutil.copyfile(FZ_V2, other)
        code, rec = self.decide(run, "--fz-manifest", other, "--fz-hash", self.h, "--fz-index", self.idx)
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(rec))

    def test_fz_v1_manifest_is_refused_when_the_index_lists_only_v2(self):
        run = self.pilot_with_hash()
        v1 = os.path.join(self.root, "fz_v1.json")
        shutil.copyfile(FZ_V1, v1)
        code, rec = self.decide(run, "--fz-manifest", v1, "--fz-hash", self.h, "--fz-index", self.idx)
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(rec))

    def test_run_with_another_fz_hash_is_refused(self):
        run = self.pilot_with_hash()
        with open(os.path.join(self.root, "x.txt"), "wb") as f:
            f.write(b"x\n")
        lst = os.path.join(self.tmp, "l2.txt")
        with open(lst, "wb") as f:
            f.write(b"fz_v2.json\nx.txt\n")
        idx2 = os.path.join(self.tmp, "idx2.json")
        h2 = FZ.write_index(self.root, lst, idx2)
        code, rec = self.decide(run, "--fz-manifest", self.m2, "--fz-hash", h2, "--fz-index", idx2)
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(rec))

    def test_tampered_manifest_and_missing_manifest_are_refused(self):
        run = self.pilot_with_hash()
        bad = os.path.join(self.root, "bad.json")
        d = jf(FZ_V2)
        d["register"]["row12_R"] += 1
        with open(bad, "w", encoding="utf-8") as f:
            json.dump(d, f)
        for m in (bad, os.path.join(self.root, "missing.json")):
            with self.subTest(m=os.path.basename(m)):
                code, rec = self.decide(run, "--fz-manifest", m, "--fz-hash", self.h, "--fz-index", self.idx)
                self.assertEqual(code, 2)
                self.assertFalse(os.path.exists(rec))

    def test_changed_listed_file_is_refused(self):
        run = self.pilot_with_hash()
        with open(self.m2, "ab") as f:
            f.write(b" ")
        code, rec = self.decide(run, "--fz-manifest", self.m2, "--fz-hash", self.h, "--fz-index", self.idx)
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(rec))


if __name__ == "__main__":
    unittest.main()
