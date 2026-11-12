"""Acceptance tests for the SF2 detection rule of C-31 (MASTER_PROPOSED_DECISION_RECORD.md MD-01; C31_C32_OWNER_DECISION_BRIEF.md). Mock records and a patched
urlopen only; no provider, no network.

Text (SCENARIO_CONSTRUCTION_SPECIFICATION_v2_7_DRAFT.md): 13.4 line 810 "SF2 | Empty response: no non-whitespace character."; line 816 "Structural failures
are SF1-SF5."; 13.6 line 830 PASS needs "no SF2, no SF3, no SF5; SF1 + SF4 <= f_S"; line 831 FAIL-S is "any SF2, SF3, SF5, or SF1 + SF4 > f_S".

STATUS (2026-10-13): these tests encode the WORKING DECISION of the researcher (option B of C-31, equal to variant A2c on the 384-record grid of test_sf2_variants.py):
SF1/SF4 take precedence over SF2, i.e. an empty body that is explained by a transport/HTTP error or by a block / missing candidate is SF1 or SF4 only. It is NOT
an official owner entry and NOT approved by the advisor; the literal reading (A1, SF2 for every empty body) and the strict-2xx reading (A2a) are analysed, with their
consequences, in the brief and in test_sf2_variants.py and are not implemented. History: the four tests that encoded A1 as expectedFailure (backup
_backup/master_exec/test_sf2_detection.py.before_optionB) were replaced by the tests below when the researcher directed option B. If the owner/advisor choose A1 or
A2a instead, rewrite TestSf2WorkingDecisionB from the ratified rule; do not weaken it.
Definitions D1-D8 below refer to section 6.1 of the brief. No definition of SF1-SF5, threshold or denominator is changed by this file.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_sf2_detection.py"""
import itertools
import os
import sys
import unittest
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_runner as RN  # noqa: E402
import s10_scoring as SC  # noqa: E402
import test_provider_response as TP  # noqa: E402

D = RN.D


def rec(**kw):
    r = {"response_text": "Verdict: satisfied", "error": None, "http_status": 200, "finish_reason": "STOP"}
    r.update(kw)
    return r


def outcome(bad):
    """Outcome of an 18-call pilot round: one record `bad`, 17 clean. f_S = 1, theta_unp = 1/2 (FZ-v1 row 18), as in test_sf_matrix_literal."""
    pc = SC.pilot_counts([bad] + [rec() for _ in range(17)])
    return D.round_outcome(pc["counts"], pc["n_calls"], 1, F(1, 2), planned_calls=18)["outcome"]


# (label, record, expected flags). Every record has NO non-whitespace character in response_text.
EMPTY_BODY_CASES = [
    ("http_503_empty_body", rec(response_text="", error="HTTP 503", http_status=503), ["SF1"]),
    ("http_503_empty_body_no_error_marker", rec(response_text="", http_status=503), ["SF1"]),
    ("timeout_no_body", rec(response_text="", error="timeout", http_status=None, finish_reason=None), ["SF1"]),
    ("transport_error_whitespace_body", rec(response_text=" \t\r\n\x0b\x0c ", error="HTTP 500", http_status=500), ["SF1"]),
    ("blocked_finish_reason_empty", rec(response_text="", finish_reason="SAFETY"), ["SF4"]),
    ("prompt_block_reason_empty", rec(response_text="", finish_reason=None, block_reason="SAFETY"), ["SF4"]),
    ("no_candidate_returned", rec(response_text="", finish_reason=None), ["SF4"]),
    ("error_marker_plus_block_reason_empty", rec(response_text="", error="HTTP 500", http_status=500, block_reason="SAFETY"), ["SF1", "SF4"]),
]


class TestSf2WorkingDecisionB(unittest.TestCase):
    """Option B (working decision): SF1/SF4 explain an empty body; SF2 is not emitted for them. All flags that ARE met stay recorded."""

    def test_d1_flags_of_the_empty_body_cases(self):
        for label, r, expected in EMPTY_BODY_CASES:
            self.assertEqual(SC.classify(r)["flags"], expected, label)

    def test_d1_these_calls_are_soft_and_pass_within_f_S(self):
        for label, r, _ in EMPTY_BODY_CASES:
            self.assertEqual(outcome(r), "PASS", label)                       # one soft call, f_S = 1 (D7: per call)

    def test_d1_two_such_calls_exceed_f_S(self):
        for label, r, _ in EMPTY_BODY_CASES[:3]:
            pc = SC.pilot_counts([r, dict(r)] + [rec() for _ in range(16)])
            self.assertEqual(D.round_outcome(pc["counts"], 18, 1, F(1, 2), planned_calls=18)["outcome"], "FAIL-S", label)

    def test_d7_sf1_plus_sf4_on_one_call_is_one_soft_call_and_keeps_both_flags(self):
        r = EMPTY_BODY_CASES[7][1]
        pc = SC.pilot_counts([r] + [rec() for _ in range(17)])
        self.assertEqual((pc["flag_totals"]["SF1"], pc["flag_totals"]["SF4"]), (1, 1))
        self.assertEqual(pc["counts"]["SF1"] + pc["counts"]["SF4"], 1)

    def test_d2_whitespace_set_is_space_tab_lf_vt_ff_cr_only(self):
        for ch in (" ", "\t", "\n", "\x0b", "\x0c", "\r", " \t\r\n\x0b\x0c"):
            self.assertEqual(SC.classify(rec(response_text=ch))["flags"], ["SF2"], repr(ch))
        for ch in (chr(0xA0), chr(0x2003), chr(0x2028)):                       # other Unicode spaces are content: parsed, UNP, no structural flag
            c = SC.classify(rec(response_text=ch))
            self.assertEqual((c["flags"], c["outcome"]), ([], "UNP"), repr(ch))

    def test_d3_status_less_record_without_error_and_empty_body_is_sf2(self):
        r = rec(response_text="", http_status=None)
        self.assertEqual(SC.classify(r)["flags"], ["SF2"])
        self.assertEqual(outcome(r), "FAIL-S")

    def test_d4_non_json_body_through_the_real_adapter_is_sf1_not_sf2(self):
        res, calls, _ = TP.gen(lambda *a, **k: TP.FakeResp(b"<html>oops</html>"))
        self.assertEqual(calls, 1)
        self.assertEqual(SC.classify(TP.rec_from(res))["flags"], ["SF1"])
        self.assertEqual(outcome(TP.rec_from(res)), "PASS")

    def test_d4_http_error_timeout_and_transport_exception_through_the_real_adapter_are_sf1_only(self):
        import socket
        import urllib.error
        errs = [urllib.error.HTTPError("u", 503, "x", {}, None), socket.timeout("t"), urllib.error.URLError("net")]
        for e in errs:
            def boom(*a, _e=e, **k):
                raise _e
            res, calls, _ = TP.gen(boom)
            self.assertEqual(calls, 1, repr(e))
            self.assertEqual(SC.classify(TP.rec_from(res))["flags"], ["SF1"], repr(e))

    def test_d6_no_candidate_through_the_real_adapter_is_sf4_only(self):
        res, _, _ = TP.gen(lambda *a, **k: TP.FakeResp(TP.payload(candidates=False)))
        self.assertEqual(SC.classify(TP.rec_from(res))["flags"], ["SF4"])
        res, _, _ = TP.gen(lambda *a, **k: TP.FakeResp(TP.payload(candidates=False, feedback={"blockReason": "SAFETY"})))
        self.assertEqual(SC.classify(TP.rec_from(res))["flags"], ["SF4"])

    def test_d6_candidate_with_finish_stop_and_no_text_is_sf2_but_without_finish_reason_it_is_sf4(self):
        res, _, _ = TP.gen(lambda *a, **k: TP.FakeResp(TP.payload(text=None, finish="STOP")))
        self.assertEqual(SC.classify(TP.rec_from(res))["flags"], ["SF2"])
        res, _, _ = TP.gen(lambda *a, **k: TP.FakeResp(TP.payload(text=None, finish=None)))     # indistinguishable from no candidate (open question Q5)
        self.assertEqual(SC.classify(TP.rec_from(res))["flags"], ["SF4"])

    def test_d5_error_or_non_2xx_status_with_text_is_sf1_only(self):
        for r in (rec(error="HTTP 500", http_status=500), rec(http_status=503), rec(error="TimeoutError")):
            self.assertEqual(SC.classify(r)["flags"], ["SF1"])
            self.assertEqual(outcome(r), "PASS")

    def test_overlap_rule_sf2_never_accompanies_sf1_or_sf4_but_may_accompany_sf3_and_sf5(self):
        n = 0
        for st, er, fr, br, tx, ex in itertools.product((None, 200, 503), (None, "E"), (None, "STOP", "MAX_TOKENS", "SAFETY"), (None, "SAFETY"),
                                                        ("", " \n", "a", chr(0xA0)), (None, ValueError("x"))):
            fl = SC.sf_flags({"http_status": st, "error": er, "finish_reason": fr, "block_reason": br, "response_text": tx}, ex)
            if "SF2" in fl:
                self.assertFalse({"SF1", "SF4"} & set(fl), (st, er, fr, br, tx))
            n += 1
        self.assertEqual(n, 384)
        self.assertEqual(sorted(SC.classify(rec(response_text="", finish_reason="MAX_TOKENS"))["flags"]), ["SF2", "SF3"])

    def test_hard_flags_are_never_hidden_by_the_precedence(self):
        for r in (rec(error="HTTP 500", http_status=500, finish_reason="MAX_TOKENS"), rec(block_reason="SAFETY", finish_reason="MAX_TOKENS")):
            self.assertEqual(outcome(r), "FAIL-S")


class TestSf2NoRegressionWhereCodeAgreesWithText(unittest.TestCase):
    def test_empty_successful_response_is_sf2_and_fail_s(self):
        r = rec(response_text="")
        self.assertEqual(SC.classify(r)["flags"], ["SF2"])
        self.assertEqual(outcome(r), "FAIL-S")

    def test_whitespace_only_successful_response_is_sf2_and_fail_s(self):
        for ws in (" ", "\n", " \t\r\n\x0b\x0c", "   \n\n"):
            r = rec(response_text=ws)
            self.assertEqual(SC.classify(r)["flags"], ["SF2"], repr(ws))
            self.assertEqual(outcome(r), "FAIL-S", repr(ws))

    def test_none_text_is_treated_as_empty(self):
        r = rec(response_text=None)
        self.assertIn("SF2", SC.classify(r)["flags"])
        self.assertEqual(outcome(r), "FAIL-S")

    def test_sf3_with_empty_body_carries_both_sf3_and_sf2(self):
        r = rec(response_text="", finish_reason="MAX_TOKENS")
        self.assertEqual(sorted(SC.classify(r)["flags"]), ["SF2", "SF3"])
        self.assertEqual(outcome(r), "FAIL-S")

    def test_sf1_and_sf4_remain_independent_of_each_other(self):
        r = rec(error="HTTP 500", http_status=500, block_reason="SAFETY")
        self.assertEqual(SC.classify(r)["flags"], ["SF1", "SF4"])

    def test_clean_response_has_no_flag(self):
        self.assertEqual(SC.classify(rec())["flags"], [])
        self.assertEqual(outcome(rec()), "PASS")


if __name__ == "__main__":
    unittest.main()
