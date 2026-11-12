"""E-1 acceptance tests (decision record 12.55): the gates refuse without a valid FZ index. Mock provider only; synthetic files in temporary folders; no network.
Other test modules switch enforcement off at import (it tests other machinery); here every test sets it ON explicitly.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_fz_enforcement.py"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import fz_index as FZ  # noqa: E402
import s10_resume as RS  # noqa: E402
import s10_runner as RN  # noqa: E402
import s11_pilot as PL  # noqa: E402
import test_pilot_flow as PF  # noqa: E402
import test_step10 as T10  # noqa: E402

BUILD = T10.BUILD


class Fixture(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(RN, "FZ_INDEX_ENFORCED", True)
        p.start()
        self.addCleanup(p.stop)
        self.d = tempfile.mkdtemp(prefix="e1_")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.root = os.path.join(self.d, "root")
        os.makedirs(os.path.join(self.root, "a"))
        self.files = {"a/x.txt": b"alpha\n", "b.json": b"{}\n"}
        for rel, b in self.files.items():
            with open(os.path.join(self.root, *rel.split("/")), "wb") as f:
                f.write(b)
        self.lst = os.path.join(self.d, "list.txt")
        with open(self.lst, "wb") as f:
            f.write(b"a/x.txt\nb.json\n")
        self.idx = os.path.join(self.d, "fz_index.json")
        self.h = FZ.write_index(self.root, self.lst, self.idx)

    def anchor_for(self, record_path):
        """valid MD-09 anchor evidence for a record file (test helper)"""
        p = record_path + ".anchor.json"
        with open(p, "w") as f:
            json.dump({"anchor_kind": "git_commit", "anchored_at_utc": "2999-01-01T00:00:00Z", "decision_record_sha256": RN.sha(RN.file_bytes(record_path)),
                       "reference": "0123456789abcdef0123456789abcdef01234567"}, f)
        return p

    def fz(self, **kw):
        d = {"index_path": self.idx, "hash": self.h, "root": self.root}
        d.update(kw)
        return d


class TestVerifier(Fixture):
    def test_production_default_is_enforced(self):
        out = subprocess.run([sys.executable, "-B", "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import s10_runner as R; print(R.FZ_INDEX_ENFORCED)", HERE],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "True")

    def test_valid_index_passes(self):
        self.assertEqual(RN.fz_index_reasons(self.fz()), [])

    def test_the_writer_and_the_independent_verifier_agree(self):
        self.assertEqual(RN.sha(RN.file_bytes(self.idx)), self.h)

    def test_missing_pieces_are_refused(self):
        for fz in (None, {}, {"index_path": self.idx}, {"hash": self.h}, self.fz(index_path=os.path.join(self.d, "nope.json")), self.fz(hash=None)):
            with self.subTest(fz=fz):
                self.assertTrue(RN.fz_index_reasons(fz))

    def test_malformed_hash_is_refused(self):
        for h in ("", "A" * 64, self.h[:-1], self.h + "0", 12345, "g" * 64):
            with self.subTest(h=h):
                self.assertTrue(RN.fz_index_reasons(self.fz(hash=h)))

    def test_wrong_hash_is_refused(self):
        why = RN.fz_index_reasons(self.fz(hash="0" * 64))
        self.assertTrue(any("differs from the recorded FZ hash" in w for w in why))

    def test_one_changed_byte_in_a_listed_file_is_refused_and_named(self):
        with open(os.path.join(self.root, "a", "x.txt"), "wb") as f:
            f.write(b"alphb\n")
        why = RN.fz_index_reasons(self.fz())
        self.assertTrue(any("a/x.txt" in w and "changed" in w for w in why), why)

    def test_missing_listed_file_is_refused(self):
        os.remove(os.path.join(self.root, "b.json"))
        self.assertTrue(any("missing: b.json" in w for w in RN.fz_index_reasons(self.fz())))

    def test_non_canonical_index_is_refused_even_with_its_own_hash(self):
        idx = json.loads(RN.file_bytes(self.idx))
        pretty = (json.dumps(idx, indent=2) + "\n").encode("ascii")
        p = os.path.join(self.d, "pretty.json")
        with open(p, "wb") as f:
            f.write(pretty)
        why = RN.fz_index_reasons(self.fz(index_path=p, hash=RN.sha(pretty)))
        self.assertTrue(any("canonical" in w for w in why), why)

    def test_unsafe_or_malformed_entries_are_refused(self):
        sha0 = "0" * 64
        cases = {"traversal": {"../x": sha0}, "absolute": {"/etc/x": sha0}, "drive": {"C:/x": sha0}, "empty part": {"a//x": sha0}, "dot": {"./b.json": sha0},
                 "backslash": {"a\\x.txt": sha0}, "not a sha": {"b.json": "xyz"}, "empty object": {}, "list": [], "number value": {"b.json": 5}}
        for name, obj in cases.items():
            with self.subTest(name):
                data = (json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
                p = os.path.join(self.d, "bad.json")
                with open(p, "wb") as f:
                    f.write(data)
                self.assertTrue(RN.fz_index_reasons(self.fz(index_path=p, hash=RN.sha(data))))

    def test_non_ascii_and_garbage_are_refused(self):
        non_ascii = ('{"é":"' + "0" * 64 + '"}' + chr(10)).encode("utf-8")
        for data in (non_ascii, b"not json", bytes([255, 254])):
            with self.subTest(data=data[:6]):
                p = os.path.join(self.d, "g.json")
                with open(p, "wb") as f:
                    f.write(data)
                self.assertTrue(RN.fz_index_reasons(self.fz(index_path=p, hash=RN.sha(data))))

    def test_enforcement_off_does_not_check_the_gate(self):
        with mock.patch.object(RN, "FZ_INDEX_ENFORCED", False):
            with self.assertRaises(RN.GateClosed) as cm:
                RN.check_gates("pilot_round_1", BUILD, os.path.join(self.d, "e.json"), os.path.join(self.d, "a.json"), verify_build=False)
        self.assertFalse(any("E-1" in r for r in cm.exception.reasons))


class TestGateAndRun(Fixture):
    def setUp(self):
        super().setUp()
        self.tmp = self.d
        self.bh = RN.sha(RN.file_bytes(os.path.join(BUILD, "manifest.json")))
        self.ev, self.au = T10.TestRunner.files(self, "pilot_round_1", days=9, calls=18)

    def test_gate_refuses_without_an_index_even_if_everything_else_holds(self):
        with self.assertRaises(RN.GateClosed) as cm:
            RN.check_gates("pilot_round_1", BUILD, self.ev, self.au, verify_build=False)
        self.assertEqual([r for r in cm.exception.reasons if "E-1" in r], ["FZ index and recorded FZ hash missing (E-1): the gate refuses without a valid FZ index"])

    def test_gate_refuses_on_a_changed_file_and_opens_with_a_valid_index(self):
        RN.check_gates("pilot_round_1", BUILD, self.ev, self.au, verify_build=False, fz=self.fz())
        with open(os.path.join(self.root, "b.json"), "wb") as f:
            f.write(b"[]\n")
        with self.assertRaises(RN.GateClosed) as cm:
            RN.check_gates("pilot_round_1", BUILD, self.ev, self.au, verify_build=False, fz=self.fz())
        self.assertTrue(any("b.json" in r for r in cm.exception.reasons))

    def test_run_refuses_without_an_index_and_creates_nothing(self):
        out = os.path.join(self.d, "run_none")
        called = []
        with self.assertRaises(RN.GateClosed):
            RN.run("pilot_round_1", BUILD, out, RN.MockProvider(lambda k, p: called.append(k) or T10.good(k, p)), evidence_path=self.ev, auth_path=self.au,
                   sleeper=lambda x: None, verify_build=False)
        self.assertEqual((called, os.path.exists(out)), ([], False))

    def test_run_with_a_valid_index_records_the_fz_hash_in_the_summary(self):
        out = os.path.join(self.d, "run_ok")
        with redirect_stdout(io.StringIO()):
            s = RN.run("pilot_round_1", BUILD, out, RN.MockProvider(T10.good), evidence_path=self.ev, auth_path=self.au, sleeper=lambda x: None,
                       verify_build=False, fz=self.fz())
        self.assertEqual((s["status"], s["recorded_calls"], s["fz_hash"]), ("RUN_COMPLETE", 18, self.h))

    def test_cli_gate_check_with_a_valid_and_with_a_missing_index(self):
        args = ["--scope", "pilot_round_1", "--build", BUILD, "--out", os.path.join(self.d, "o"), "--evidence", self.ev, "--authorization", self.au, "--fz-manifest", RN.FZ_PATH]
        with mock.patch.object(RN, "check_gates", wraps=RN.check_gates) as cg, redirect_stdout(io.StringIO()):
            self.assertEqual(RN.main(args), 2)
            self.assertIsNone(cg.call_args[0][6]["index_path"])
        self.assertFalse(os.path.exists(os.path.join(self.d, "o")))

    def test_main_scope_pilot_record_must_name_the_recorded_fz_hash(self):
        ev, au = T10.TestRunner.files(self, "main", days=9)
        rec = os.path.join(self.d, "rec.json")
        for fh, ok in ((self.h, True), ("b" * 64, False)):
            with open(rec, "w") as f:
                json.dump({"final_decision": "PASS", "fz_hash": fh, "call_list_sha256": "0" * 64, "utc_timestamp": "2026-10-16T05:00:00Z"}, f)
            if ok:
                RN.check_gates("main", BUILD, ev, au, rec, verify_build=False, fz=self.fz(anchor_path=self.anchor_for(rec)))
                continue
            with self.assertRaises(RN.GateClosed) as cm:
                RN.check_gates("main", BUILD, ev, au, rec, verify_build=False, fz=self.fz())
            self.assertTrue(any("another FZ hash" in r for r in cm.exception.reasons), cm.exception.reasons)


class TestPilotDecideAndResume(PF.Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(RN, "FZ_INDEX_ENFORCED", True)
        p.start()
        self.addCleanup(p.stop)
        self.root = os.path.join(self.tmp, "froot")
        os.makedirs(self.root)
        with open(os.path.join(self.root, "f.txt"), "wb") as f:
            f.write(b"x\n")
        lst = os.path.join(self.tmp, "l.txt")
        with open(lst, "wb") as f:
            f.write(b"f.txt\n")
        self.idx = os.path.join(self.tmp, "idx.json")
        self.h = FZ.write_index(self.root, lst, self.idx)

    def test_decide_refuses_without_and_with_a_wrong_index_and_writes_nothing(self):
        with mock.patch.object(RN, "FZ_INDEX_ENFORCED", False):
            run = self.run_pilot(T10.good)
        rec = os.path.join(self.tmp, "rec.json")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(PL.main(["decide", "--run", run, "--round", "1", "--fz-manifest", RN.FZ_PATH, "--fz-hash", self.h, "--record", rec]), 2)
            self.assertEqual(PL.main(["decide", "--run", run, "--round", "1", "--fz-manifest", RN.FZ_PATH, "--fz-hash", "c" * 64, "--fz-index", self.idx, "--record", rec]), 2)
        self.assertFalse(os.path.exists(rec))

    def test_resume_checks_the_index_too(self):
        with mock.patch.object(RN, "FZ_INDEX_ENFORCED", False):
            run = self.run_pilot(T10.good)
        ev, au = T10.TestRunner.files(self, "pilot_round_1", days=30, calls=18)
        ra = os.path.join(self.tmp, "ra.json")
        with open(ra, "w") as f:
            json.dump({}, f)
        with self.assertRaises((RN.GateClosed, RS.ResumeRefused)) as cm:
            RS.prepare_resume(run, BUILD, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, resume_auth_path=ra, verify_build=False)
        reasons = getattr(cm.exception, "reasons", [])
        self.assertTrue(any("E-1" in r for r in reasons), reasons)


if __name__ == "__main__":
    unittest.main()
