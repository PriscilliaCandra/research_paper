"""MD-09 / specification section 10: the main-scope gate needs anchor evidence for the decision record before the order can be generated.
Mock provider only; synthetic files in temporary folders; no network.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_md09_anchor.py"""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_runner as RN  # noqa: E402
import test_fz_enforcement as FE  # noqa: E402
import test_step10 as T10  # noqa: E402

BUILD = T10.BUILD


class TestAnchor(FE.Fixture):
    def setUp(self):
        super().setUp()
        self.tmp = self.d
        self.bh = RN.sha(RN.file_bytes(os.path.join(BUILD, "manifest.json")))
        self.ev, self.au = T10.TestRunner.files(self, "main", days=9)
        self.rec = os.path.join(self.d, "rec.json")
        with open(self.rec, "w") as f:
            json.dump({"final_decision": "PASS", "fz_hash": self.h, "call_list_sha256": "0" * 64, "utc_timestamp": "2026-10-16T05:00:00Z"}, f)

    def good(self):
        return {"anchor_kind": "external_timestamped", "anchored_at_utc": "2026-10-16T06:00:00Z", "decision_record_sha256": RN.sha(RN.file_bytes(self.rec)),
                "reference": "ots:proof-0001-abcdef"}

    def write(self, obj, name="anchor.json"):
        p = os.path.join(self.d, name)
        with open(p, "w") as f:
            json.dump(obj, f)
        return p

    def gate(self, anchor_path):
        return RN.check_gates("main", BUILD, self.ev, self.au, self.rec, verify_build=False, fz=self.fz(anchor_path=anchor_path))

    def reasons(self, anchor_path):
        with self.assertRaises(RN.GateClosed) as cm:
            self.gate(anchor_path)
        return [r for r in cm.exception.reasons if "MD-09" in r]

    def test_valid_anchor_opens_the_gate(self):
        self.gate(self.write(self.good()))
        self.gate(self.write(dict(self.good(), anchor_kind="git_commit", reference="0123456789abcdef0123456789abcdef01234567"), "a2.json"))

    def test_missing_anchor_is_refused(self):
        self.assertTrue(any("missing" in r for r in self.reasons(None)))
        self.assertTrue(any("missing" in r for r in self.reasons(os.path.join(self.d, "nope.json"))))

    def test_wrong_hash_is_refused_and_a_record_changed_after_anchoring_too(self):
        self.assertTrue(any("another SHA-256" in r for r in self.reasons(self.write(dict(self.good(), decision_record_sha256="1" * 64)))))
        p = self.write(self.good())
        with open(self.rec, "a") as f:
            f.write(" ")
        self.assertTrue(any("another SHA-256" in r for r in self.reasons(p)))

    def test_malformed_evidence_is_refused(self):
        bad = {"extra key": dict(self.good(), note="x"), "missing key": {k: v for k, v in self.good().items() if k != "reference"}, "kind": dict(self.good(), anchor_kind="hash_only"),
               "short ref": dict(self.good(), reference="abc"), "empty ref": dict(self.good(), reference="   "), "no timestamp": dict(self.good(), anchored_at_utc="2026-10-16"),
               "number ref": dict(self.good(), reference=12345678), "list": []}
        for name, obj in bad.items():
            with self.subTest(name):
                self.assertTrue(self.reasons(self.write(obj, name.replace(" ", "_") + ".json")))
        notjson = os.path.join(self.d, "garbage.json")
        with open(notjson, "wb") as f:
            f.write(b"not json")
        self.assertTrue(self.reasons(notjson))

    def test_anchor_earlier_than_the_record_is_refused(self):
        self.assertTrue(any("earlier" in r for r in self.reasons(self.write(dict(self.good(), anchored_at_utc="2026-10-16T04:59:59Z")))))

    def test_pilot_scope_does_not_need_an_anchor(self):
        ev, au = T10.TestRunner.files(self, "pilot_round_2", days=11, calls=18)
        RN.check_gates("pilot_round_2", BUILD, ev, au, verify_build=False, fz=self.fz())

    def test_enforcement_off_does_not_check_the_anchor(self):
        with mock.patch.object(RN, "FZ_INDEX_ENFORCED", False):
            RN.check_gates("main", BUILD, self.ev, self.au, self.rec, verify_build=False)

    def test_the_order_is_not_generated_when_the_anchor_is_missing(self):
        out = os.path.join(self.d, "run_main")
        with mock.patch.object(RN, "plan_calls", side_effect=AssertionError("order generated")) as pc, self.assertRaises(RN.GateClosed):
            RN.run("main", BUILD, out, RN.MockProvider(T10.good), evidence_path=self.ev, auth_path=self.au, pilot_record_path=self.rec, sleeper=lambda x: None,
                   verify_build=False, fz=self.fz())
        pc.assert_not_called()
        self.assertFalse(os.path.exists(out))



class TestAnchorIdInTheRunManifest(FE.Fixture):
    """owner decision 12.74 D / specification section 10: the anchor ID is part of the run manifest (the first ledger entry) of a main run"""
    def setUp(self):
        super().setUp()
        self.tmp = self.d
        self.bh = RN.sha(RN.file_bytes(os.path.join(BUILD, "manifest.json")))

    def first_entry(self, out):
        with open(os.path.join(out, "ledger.jsonl"), "rb") as f:
            return json.loads(f.readline())

    def test_main_run_provenance_names_the_anchor_evidence(self):
        ev, au = T10.TestRunner.files(self, "main", days=9)
        with open(os.path.join(BUILD, "main", "registry.json"), "rb") as f:
            reg = json.loads(f.read())["templates"]
        text = RN.D.call_list([t["id"] for t in reg], 2)
        rec = os.path.join(self.d, "rec_main.json")
        with open(rec, "w") as f:
            json.dump({"final_decision": "PASS", "seed": 20261016, "call_list_sha256": RN.sha(text.encode("ascii")), "fz_hash": self.h,
                       "utc_timestamp": "2026-10-16T03:55:17Z"}, f)
        anchor = self.anchor_for(rec)
        out = os.path.join(self.d, "run_main_anchor")
        with mock.patch.object(RN, "FZ_INDEX_ENFORCED", True), redirect_stdout(io.StringIO()):
            s = RN.run("main", BUILD, out, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, pilot_record_path=rec, sleeper=lambda x: None, verify_build=False,
                       fz=self.fz(anchor_path=anchor))
        self.assertEqual((s["status"], s["recorded_calls"]), ("RUN_COMPLETE", 2496))
        with open(anchor) as f:
            a = json.load(f)
        self.assertEqual(self.first_entry(out)["decision_anchor"], dict(a, sha256=RN.sha(RN.file_bytes(anchor))))

    def test_pilot_run_provenance_has_no_anchor(self):
        ev, au = T10.TestRunner.files(self, "pilot_round_2", days=11, calls=18)
        out = os.path.join(self.d, "run_pilot_noanchor")
        with mock.patch.object(RN, "FZ_INDEX_ENFORCED", True), redirect_stdout(io.StringIO()):
            RN.run("pilot_round_2", BUILD, out, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False, fz=self.fz())
        self.assertIsNone(self.first_entry(out)["decision_anchor"])

    def test_anchor_summary_is_none_without_a_path_and_carries_the_four_fields(self):
        self.assertIsNone(RN.anchor_summary(None))
        rec = os.path.join(self.d, "r.json")
        with open(rec, "w") as f:
            json.dump({"utc_timestamp": "2026-10-16T03:55:17Z"}, f)
        a = RN.anchor_summary(self.anchor_for(rec))
        self.assertEqual(sorted(a), sorted(RN.ANCHOR_KEYS | {"sha256"}))


if __name__ == "__main__":
    unittest.main()
