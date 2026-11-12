"""Acceptance tests for the H1 waiver in the gate (decision record 12.56, implementation authorized in 12.57). Mock provider only; synthetic files in temporary folders.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_h1_waiver.py"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_runner as RN  # noqa: E402
import test_step10 as T10  # noqa: E402

RN.FZ_INDEX_ENFORCED = False  # E-1 is tested in test_fz_enforcement.py
BUILD = T10.BUILD


class TestWaiver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="w_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bh = RN.sha(RN.file_bytes(os.path.join(BUILD, "manifest.json")))
        self.ev, self.au = T10.TestRunner.files(self, "pilot_round_1", days=9, calls=18)      # a valid evidence file and authorization
        self.rec = os.path.join(self.tmp, "record.md")
        with open(self.rec, "w", encoding="utf-8") as f:
            f.write("### 12.55 earlier\n\ntext\n\n### 12.56 Owner decision: waiver\n\ntext\n")
        p = mock.patch.object(RN, "DECISION_RECORD_PATH", self.rec)
        p.start()
        self.addCleanup(p.stop)

    def waiver(self, **kw):
        w = {"waived_by": "owner", "decision_entry": "12.56", "human_blind_check": "NOT PERFORMED", "build_manifest_sha256": self.bh}
        w.update(kw)
        for k in [k for k, v in w.items() if v is None]:
            del w[k]
        p = os.path.join(self.tmp, "step9_waiver.json")
        with open(p, "w") as f:
            json.dump(w, f)
        return p

    def reasons(self, path):
        try:
            RN.check_gates("pilot_round_1", BUILD, path, self.au, verify_build=False)
        except RN.GateClosed as e:
            return e.reasons
        return []

    def test_valid_waiver_opens_the_evidence_predicates(self):
        self.assertEqual(self.reasons(self.waiver()), [])

    def test_missing_file_still_closes(self):
        self.assertTrue(any("evidence file missing" in r for r in self.reasons(os.path.join(self.tmp, "none.json"))))

    def test_waiver_does_not_replace_the_authorization(self):
        with self.assertRaises(RN.GateClosed) as cm:
            RN.check_gates("pilot_round_1", BUILD, self.waiver(), os.path.join(self.tmp, "no_auth.json"), verify_build=False)
        self.assertTrue(any("authorization file missing" in r for r in cm.exception.reasons))

    def test_wrong_fields_are_refused(self):
        for name, kw in {"owner": dict(waived_by="advisor"), "entry": dict(decision_entry="12.55"), "text": dict(human_blind_check="DONE"),
                         "hash": dict(build_manifest_sha256="0" * 64), "no hash": dict(build_manifest_sha256=None), "no owner": dict(waived_by=None)}.items():
            with self.subTest(name):
                self.assertTrue(self.reasons(self.waiver(**kw)), name)

    def test_a_file_claiming_a_performed_check_is_refused(self):
        for extra in ({"completed_utc": "2026-10-30T09:00:00Z"}, {"coverage_percent": 100}, {"worksheet_sha256": "a" * 64}, {"adjudication": "AGREE"}, {"note": "x"}):
            with self.subTest(extra=extra):
                self.assertTrue(any("unexpected keys" in r for r in self.reasons(self.waiver(**extra))))
        self.assertTrue(self.reasons(self.waiver(human_blind_check={"completed_utc": "2026-10-30T09:00:00Z", "coverage_percent": 100})))

    def test_record_without_the_entry_refuses(self):
        with open(self.rec, "w", encoding="utf-8") as f:
            f.write("### 12.55 earlier\n\nmention of ### 12.56 inside a line is not a heading\n")
        self.assertTrue(any("does not contain entry 12.56" in r for r in self.reasons(self.waiver())))
        os.remove(self.rec)
        self.assertTrue(any("does not contain entry 12.56" in r for r in self.reasons(self.waiver())))

    def test_malformed_files_close_the_gate_instead_of_crashing(self):
        for body in ("not json", "[]", "5", ""):
            with self.subTest(body=body):
                p = os.path.join(self.tmp, "bad.json")
                with open(p, "w") as f:
                    f.write(body)
                self.assertTrue(self.reasons(p))

    def test_the_normal_evidence_file_is_still_accepted_and_still_checked(self):
        self.assertEqual(self.reasons(self.ev), [])
        ev6, _ = T10.TestRunner.files(self, "pilot_round_1", days=6, calls=18)
        self.assertTrue(any("delay" in r for r in self.reasons(ev6)))

    def test_waiver_for_another_build_is_refused_and_main_scope_is_handled_alike(self):
        self.assertTrue(any("another build" in r for r in self.reasons(self.waiver(build_manifest_sha256="f" * 64))))
        au_main = T10.TestRunner.files(self, "main", days=9)[1]
        with self.assertRaises(RN.GateClosed) as cm:
            RN.check_gates("main", BUILD, self.waiver(), au_main, verify_build=False)
        self.assertFalse(any("H1 waiver" in r or "evidence" in r for r in cm.exception.reasons))     # only the pilot-record reason remains
        self.assertTrue(any("pilot decision record missing" in r for r in cm.exception.reasons))

    def test_the_real_record_contains_the_entry(self):
        with mock.patch.object(RN, "DECISION_RECORD_PATH", os.path.join(RN.RD, "STEP3_OWNER_DECISIONS_RECORD_DRAFT.md")):
            self.assertEqual(self.reasons(self.waiver()), [])


if __name__ == "__main__":
    unittest.main()
