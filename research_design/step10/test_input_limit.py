"""Acceptance tests for patch P3 (MD-02, working technical decision): the owner-stated `max_input_characters` of the authorization is checked at the gate
and again before any provider call; an empty or over-limit prompt rejects the run before any folder, ledger entry or provider call exists; nothing is
truncated; the limit is an operational control, independent of the cost ceiling. Mock provider and local files only.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_input_limit.py"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_integrity as IG  # noqa: E402
import s10_runner as RN  # noqa: E402
RN.FZ_INDEX_ENFORCED = False  # E-1 (12.55): these tests exercise other machinery with mock data; enforcement is tested in test_fz_enforcement.py
import test_step10 as T10  # noqa: E402

BUILD = T10.BUILD
PLAN = RN.plan_calls("pilot_round_1", BUILD)
LONGEST = max(len(RN.read_prompt(BUILD, "pilot_round_1", c["template_id"], c["condition"])) for c in PLAN)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lim_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bh = RN.sha(RN.file_bytes(os.path.join(BUILD, "manifest.json")))
        self.n = 0

    def auth(self, **kw):
        self.n += 1
        return T10.TestRunner.files(self, "pilot_round_1", days=8 + self.n, calls=18, **kw)

    def run_it(self, ev, au, provider, out="out"):
        with redirect_stdout(io.StringIO()):
            return RN.run("pilot_round_1", BUILD, os.path.join(self.tmp, out), provider, evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)


class TestLimitAtGate(Base):
    def test_known_longest_prompt_is_1016_characters(self):
        self.assertEqual(LONGEST, 1016)                                           # matches 12.18 row 6 and the prompt table

    def test_missing_or_invalid_limit_closes_the_gate(self):
        for bad in (None, 0, -5, True, "1016", 10.5):
            ev, au = self.auth(max_chars=bad)
            with self.assertRaises(RN.GateClosed) as cm:
                RN.check_gates("pilot_round_1", BUILD, ev, au, verify_build=False)
            self.assertTrue(any("max_input_characters" in r for r in cm.exception.reasons), bad)

    def test_exactly_at_the_limit_is_accepted_one_below_is_not(self):
        ev, au = self.auth(max_chars=LONGEST)
        RN.check_gates("pilot_round_1", BUILD, ev, au, verify_build=False)
        ev, au = self.auth(max_chars=LONGEST - 1)
        with self.assertRaises(RN.GateClosed) as cm:
            RN.check_gates("pilot_round_1", BUILD, ev, au, verify_build=False)
        self.assertTrue(any("above max_input_characters" in r for r in cm.exception.reasons))

    def test_limit_is_independent_of_the_cost_assumption(self):
        ev, au = self.auth(max_chars=LONGEST, cost_assumption=10 ** 8)            # cost ceiling still decides
        with self.assertRaises(RN.GateClosed) as cm:
            RN.check_gates("pilot_round_1", BUILD, ev, au, verify_build=False)
        self.assertTrue(any("cost" in r for r in cm.exception.reasons))
        ev, au = self.auth(max_chars=10 ** 6, cost_assumption=1016)               # a huge character limit does not lift the cost check or prove a cost
        RN.check_gates("pilot_round_1", BUILD, ev, au, verify_build=False)

    def test_the_template_is_rejected(self):
        tpl = os.path.join(os.path.dirname(HERE), "..", "results_package_2026-10-13", "templates", "owner_authorization_TEMPLATE.json")
        ev, _ = self.auth()
        with self.assertRaises(RN.GateClosed):
            RN.check_gates("pilot_round_1", BUILD, ev, tpl, verify_build=False)


class TestLimitBeforeAnyCall(Base):
    def test_over_limit_input_is_refused_before_the_provider_and_creates_nothing(self):
        ev, au = self.auth(max_chars=LONGEST)
        long_text = "x" * (LONGEST + 1)
        calls = []
        prov = RN.MockProvider(lambda k, p: calls.append(k) or T10.good(k, p))
        real = RN.read_prompt
        first = PLAN[0]

        def patched(build, scope, tid, cond):
            return long_text if (tid, cond) == (first["template_id"], first["condition"]) else real(build, scope, tid, cond)
        with mock.patch.object(RN, "read_prompt", patched):
            with self.assertRaises(RN.PreflightFailed) as cm:
                self.run_it(ev, au, prov)
        self.assertEqual((calls, prov.calls), ([], 0))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out")))
        self.assertTrue(any(f"{LONGEST + 1} characters, above the limit {LONGEST}" in r for r in cm.exception.reasons))
        self.assertNotIn("xxxx", str(cm.exception))                               # no prompt text in the message

    def test_exact_limit_and_empty_prompt(self):
        ev, au = self.auth(max_chars=LONGEST)
        real = RN.read_prompt
        first = PLAN[0]
        with mock.patch.object(RN, "read_prompt", lambda b, s, t, c: "" if (t, c) == (first["template_id"], first["condition"]) else real(b, s, t, c)):
            with self.assertRaises(RN.PreflightFailed) as cm:
                self.run_it(ev, au, RN.MockProvider(T10.good))
        self.assertTrue(any("is empty" in r for r in cm.exception.reasons))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out")))
        ev, au = self.auth(max_chars=LONGEST)
        s = self.run_it(ev, au, RN.MockProvider(T10.good), out="ok")             # at the limit: runs
        self.assertEqual(s["status"], "RUN_COMPLETE")
        self.assertEqual(s["input_size_check"]["limit_characters"], LONGEST)
        self.assertEqual(s["input_size_check"]["max_observed_characters"], LONGEST)
        self.assertEqual(s["input_size_check"]["checked_prompts"], 18)
        self.assertIn("NOT a token measurement", s["input_size_check"]["basis"])

    def test_prompts_are_sent_unchanged_never_truncated(self):
        ev, au = self.auth(max_chars=LONGEST)
        seen = {}

        def fn(k, p):
            seen[k] = p
            return T10.good(k, p)
        self.run_it(ev, au, RN.MockProvider(fn))
        for c in PLAN:
            self.assertEqual(seen[c["call_key"]], RN.read_prompt(BUILD, "pilot_round_1", c["template_id"], c["condition"]))

    def test_rejected_run_leaves_the_round_unconsumed(self):
        ev, au = self.auth(max_chars=LONGEST - 1)
        with self.assertRaises(RN.GateClosed):
            self.run_it(ev, au, RN.MockProvider(T10.good))
        ev2, au2 = self.auth(max_chars=LONGEST)
        s = self.run_it(ev2, au2, RN.MockProvider(T10.good))                      # same output name is still free
        self.assertEqual(s["recorded_calls"], 18)
        self.assertEqual(IG.verify_run(os.path.join(self.tmp, "out"), BUILD)[0], [])

    def test_cli_without_live_reports_the_limit_reason(self):
        ev, au = self.auth(max_chars=LONGEST - 1)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = RN.main(["--scope", "pilot_round_1", "--build", BUILD, "--out", os.path.join(self.tmp, "x"), "--evidence", ev, "--authorization", au])
        self.assertEqual(code, 2)
        self.assertIn("above max_input_characters", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
