"""Acceptance tests for the pilot decision derived from the TEXT of spec 13.6 (lines 828-834): "PASS: no SF2, no SF3, no SF5; SF1 + SF4 <= f_S; UNP share <= theta_unp"
and "FAIL-S: any SF2, SF3, SF5, or SF1 + SF4 > f_S". They do not depend on the unadopted primary order or on the per-call/per-flag unit of f_S
(MASTER_PREPILOT_RESOLUTION.md section 2): they only say that a call which meets SF2, SF3 or SF5 must make the round FAIL-S.
History: before patch P1 six rows were expectedFailure (documented defect C-04). Patch P1 applies the WORKING technical decision MD-01 (directed by the
project owner on 2026-10-13; NOT an institutional approval by the advisor or a formal owner entry): all flags are kept, the primary classification is
for recording only, a call meeting SF2/SF3/SF5 counts as a hard failure even with SF1/SF4, and f_S counts calls (SF1+SF4 on one call is one soft call).
The markers were therefore removed and all rows are ordinary tests. Mock records only. Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_sf_matrix_literal.py"""
import os
import sys
import unittest
from fractions import Fraction as F
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_runner as RN  # noqa: E402
import s10_scoring as SC  # noqa: E402

D = RN.D


def rec(**kw):
    r = {"response_text": "Verdict: satisfied", "error": None, "http_status": 200, "finish_reason": "STOP"}
    r.update(kw)
    return r


def pilot_outcome(bad, raise_in_parser=False):
    """Outcome of an 18-call pilot round: one record `bad`, 17 clean ones. f_S = 1, theta_unp = 0.50 (FZ-v1 row 18)."""
    recs = [rec() for _ in range(17)]
    if raise_in_parser:
        with mock.patch.object(SC.s7_parser, "parse_verdict", side_effect=lambda t: (_ for _ in ()).throw(ValueError("x")) if t == "BOOM" else ("PARSEABLE", "satisfied")):
            recs.append(dict(bad, response_text="BOOM"))
            pc = SC.pilot_counts(recs)
    else:
        recs.append(bad)
        pc = SC.pilot_counts(recs)
    return D.round_outcome(pc["counts"], pc["n_calls"], 1, F(1, 2), planned_calls=18)["outcome"]


class TestSingleFlagRows(unittest.TestCase):
    def test_single_flags_follow_the_text(self):
        self.assertEqual(pilot_outcome(rec(response_text="", error="HTTP 503", http_status=503)), "PASS")      # SF1 alone: soft 1 <= f_S
        self.assertEqual(pilot_outcome(rec(response_text="")), "FAIL-S")                                        # SF2
        self.assertEqual(pilot_outcome(rec(finish_reason="MAX_TOKENS")), "FAIL-S")                              # SF3
        self.assertEqual(pilot_outcome(rec(finish_reason="SAFETY")), "PASS")                                    # SF4 alone: soft
        self.assertEqual(pilot_outcome(rec(), raise_in_parser=True), "FAIL-S")                                  # SF5
        self.assertEqual(pilot_outcome(rec(finish_reason="MAX_TOKENS", response_text="")), "FAIL-S")            # SF3 + SF2 (both hard)

    def test_two_soft_failures_exceed_f_S(self):
        recs = [rec(response_text="", error="HTTP 503", http_status=503), rec(finish_reason="SAFETY")] + [rec() for _ in range(16)]
        pc = SC.pilot_counts(recs)
        self.assertEqual(D.round_outcome(pc["counts"], 18, 1, F(1, 2), planned_calls=18)["outcome"], "FAIL-S")


class TestHardFlagHiddenBySoftFlag(unittest.TestCase):
    """A call that meets a hard definition must make the round FAIL-S whatever other flag it also meets (spec 13.6 text)."""

    def test_sf1_plus_sf3(self):
        self.assertEqual(pilot_outcome(rec(error="HTTP 500", http_status=500, finish_reason="MAX_TOKENS")), "FAIL-S")

    def test_sf4_plus_sf3(self):
        self.assertEqual(pilot_outcome(rec(block_reason="SAFETY", finish_reason="MAX_TOKENS")), "FAIL-S")

    def test_sf1_plus_sf5(self):
        self.assertEqual(pilot_outcome(rec(error="HTTP 500", http_status=500), raise_in_parser=True), "FAIL-S")

    def test_sf4_plus_sf5(self):
        self.assertEqual(pilot_outcome(rec(block_reason="SAFETY"), raise_in_parser=True), "FAIL-S")

    def test_sf1_plus_sf3_plus_sf5(self):
        self.assertEqual(pilot_outcome(rec(error="HTTP 500", http_status=500, finish_reason="MAX_TOKENS"), raise_in_parser=True), "FAIL-S")

    def test_sf4_plus_sf3_plus_sf5(self):
        self.assertEqual(pilot_outcome(rec(block_reason="SAFETY", finish_reason="MAX_TOKENS"), raise_in_parser=True), "FAIL-S")


class TestFlagsAreAlwaysRecorded(unittest.TestCase):
    def test_all_flags_are_detected_even_when_the_count_hides_them(self):
        self.assertEqual(SC.classify(rec(error="HTTP 500", http_status=500, finish_reason="MAX_TOKENS"))["flags"], ["SF1", "SF3"])
        self.assertEqual(SC.classify(rec(block_reason="SAFETY", finish_reason="MAX_TOKENS"))["flags"], ["SF4", "SF3"])
        pc = SC.pilot_counts([rec(error="HTTP 500", http_status=500, finish_reason="MAX_TOKENS")])
        self.assertEqual(pc["multi_flag_calls"], 1)                                   # the multi-flag call is at least reported


class TestSoftUnitIsTheCall(unittest.TestCase):
    """MD-01 item 4-5: f_S counts calls; SF1+SF4 on one call is one soft failure."""

    def test_sf1_plus_sf4_on_one_call_is_one_soft_failure_and_both_flags_are_kept(self):
        bad = rec(error="HTTP 500", http_status=500, block_reason="SAFETY")
        self.assertEqual(SC.classify(bad)["flags"], ["SF1", "SF4"])
        self.assertEqual(pilot_outcome(bad), "PASS")
        pc = SC.pilot_counts([bad] + [rec() for _ in range(17)])
        self.assertEqual(pc["flag_totals"]["SF1"], 1)
        self.assertEqual(pc["flag_totals"]["SF4"], 1)
        self.assertEqual(sum(pc["counts"][k] for k in ("SF1", "SF4")), 1)           # one soft call in the decision vector

    def test_two_such_calls_exceed_f_S(self):
        bad = rec(error="HTTP 500", http_status=500, block_reason="SAFETY")
        pc = SC.pilot_counts([bad, dict(bad)] + [rec() for _ in range(16)])
        self.assertEqual(D.round_outcome(pc["counts"], 18, 1, F(1, 2), planned_calls=18)["outcome"], "FAIL-S")


class TestEveryFlagCombination(unittest.TestCase):
    """All 31 non-empty subsets of {SF1..SF5} as one failed call among 17 clean ones. Expected value from the text of 13.6 and MD-01 (hand table):
    a subset containing SF2, SF3 or SF5 -> FAIL-S; a subset of {SF1, SF4} -> one soft call -> PASS (f_S = 1)."""

    def test_all_subsets(self):
        import itertools
        flags = ["SF1", "SF2", "SF3", "SF4", "SF5"]
        n = 0
        for r in range(1, 6):
            for sub in itertools.combinations(flags, r):
                counts = SC.decision_counts([list(sub)])
                out = D.round_outcome(counts, 18, 1, F(1, 2), planned_calls=18)["outcome"]
                expected = "FAIL-S" if set(sub) & {"SF2", "SF3", "SF5"} else "PASS"
                self.assertEqual(out, expected, sub)
                self.assertEqual(sum(counts[k] for k in flags), 1, sub)                  # disjoint: exactly one entry for the call
                n += 1
        self.assertEqual(n, 31)

    def test_hard_flag_wins_inside_the_decision_type(self):
        for sub in (["SF1", "SF3"], ["SF4", "SF5"], ["SF1", "SF4", "SF3"], ["SF1", "SF3", "SF5"], ["SF4", "SF3", "SF5"]):
            self.assertIn(SC.decision_type(sub), ("SF2", "SF3", "SF5"), sub)
        self.assertIn(SC.decision_type(["SF1", "SF4"]), ("SF1", "SF4"))
        self.assertIsNone(SC.decision_type([]))

    def test_recording_primary_and_flag_totals_are_unchanged_by_the_decision_rule(self):
        bad = rec(error="HTTP 500", http_status=500, finish_reason="MAX_TOKENS")
        c = SC.classify(bad)
        self.assertEqual((c["flags"], c["primary_sf"]), (["SF1", "SF3"], "SF1"))        # recording rule unchanged (provisional order)
        pc = SC.pilot_counts([bad])
        self.assertEqual(pc["primary_counts"]["SF1"], 1)
        self.assertEqual(pc["counts"]["SF3"], 1)                                        # decision vector: hard
        self.assertEqual((pc["flag_totals"]["SF1"], pc["flag_totals"]["SF3"], pc["multi_flag_calls"]), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
