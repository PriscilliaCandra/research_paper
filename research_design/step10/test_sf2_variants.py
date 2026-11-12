"""Tests of the ANALYSIS in sf2_variants_analysis.py (conflict C-31 / C-32 decision brief). They check that the decision table is reproducible, that its "current code"
column is the REAL scoring code, and the structural facts the brief relies on. They do not test or change the scoring rule and decide nothing.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_sf2_variants.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import sf2_variants_analysis as A  # noqa: E402

ROWS = {r["id"]: r for r in A.table()}
EMPTY_CASES = ("K01", "K02", "K03", "K04", "K05", "K06", "K07", "K11", "K13", "K14", "K15", "K19")


class TestCurrentCodeColumn(unittest.TestCase):
    def test_current_code_equals_variant_B_for_every_case(self):
        for cid, r in ROWS.items():
            self.assertEqual(r["current_flags"], r["B"]["flags"], cid)
            self.assertEqual(r["current_outcome"], r["B"]["outcome"], cid)
            self.assertEqual(r["current_primary"], r["B"]["primary"], cid)

    def test_variant_B_equals_the_real_flag_function_on_the_whole_grid(self):
        import itertools
        import s10_scoring as SC
        for st, er, fr, br, tx, ex in itertools.product((None, 200, 503), (None, "E"), (None, "STOP", "MAX_TOKENS", "SAFETY"), (None, "SAFETY"),
                                                        ("", " \n", "a", chr(0xA0)), (None, ValueError("x"))):
            r = {"http_status": st, "error": er, "finish_reason": fr, "block_reason": br, "response_text": tx}
            self.assertEqual(A.flags_variant(r, "B", ex), SC.sf_flags(r, ex), r)


class TestTableFacts(unittest.TestCase):
    def test_pinned_rows_of_the_brief(self):
        o = lambda c, v: ROWS[c][v]["outcome"]          # noqa: E731
        for c in ("K01", "K02", "K03"):                 # a received empty or whitespace response: SF2 in every variant
            self.assertEqual([o(c, v) for v in A.VARIANTS], ["FAIL-S"] * 5, c)
        for c in ("K04", "K05", "K06", "K07", "K15"):   # transport / HTTP error with an empty body
            self.assertEqual([o(c, v) for v in A.VARIANTS], ["PASS", "FAIL-S", "PASS", "PASS", "PASS"], c)
        for c in ("K11", "K13", "K14"):                 # block / no candidate with an empty body
            self.assertEqual([o(c, v) for v in A.VARIANTS], ["PASS", "FAIL-S", "FAIL-S", "PASS", "PASS"], c)
        self.assertEqual([o("K19", v) for v in A.VARIANTS], ["FAIL-S", "FAIL-S", "PASS", "PASS", "FAIL-S"])   # status-less empty record: the A2a/A2b hole
        for c in ("K08", "K09", "K10", "K12", "K18", "K20"):
            self.assertEqual({o(c, v) for v in A.VARIANTS}, {"PASS"}, c)
        for c in ("K16", "K17"):                        # hard flag with SF1 / SF4: FAIL-S in every variant
            self.assertEqual({o(c, v) for v in A.VARIANTS}, {"FAIL-S"}, c)

    def test_flag_lists_keep_every_flag_in_every_variant_where_the_definition_is_met(self):
        self.assertEqual(ROWS["K16"]["B"]["flags"], ["SF1", "SF3"])
        self.assertEqual(ROWS["K17"]["B"]["flags"], ["SF4", "SF5"])
        self.assertEqual(ROWS["K04"]["A1"]["flags"], ["SF1", "SF2"])
        self.assertEqual(ROWS["K11"]["A1"]["flags"], ["SF4", "SF2"])
        self.assertEqual(ROWS["K15"]["A1"]["flags"], ["SF1", "SF4", "SF2"])

    def test_record_cannot_tell_no_candidate_from_a_candidate_without_text_and_finish_reason(self):
        # K13 is one record for both situations: the adapter stores neither a candidate count nor a flag for "candidate present".
        self.assertEqual(ROWS["K13"]["current_flags"], ["SF4"])


class TestGridFacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n, cls.diff = A.grid()

    def test_grid_size(self):
        self.assertEqual(self.n, 3 * 2 * 4 * 2 * 4 * 2)

    def test_A2c_is_identical_to_B_on_every_record_of_the_grid(self):
        self.assertEqual(self.diff["A2c"], [])

    def test_A2b_differs_from_B_only_for_status_less_records_without_error_and_without_block(self):
        self.assertTrue(self.diff["A2b"])
        for r, _ in self.diff["A2b"]:
            self.assertIsNone(r["http_status"])
            self.assertFalse(r["error"])
            self.assertFalse(r["block_reason"])
            self.assertFalse(r["response_text"].strip(" \t\n\v\f\r"))
            self.assertNotEqual(r["finish_reason"], "SAFETY")

    def test_no_variant_differs_from_B_when_the_text_is_not_empty(self):
        for v, lst in self.diff.items():
            for r, _ in lst:
                self.assertFalse(A.WS and r["response_text"].strip(A.WS) != "", (v, r))

    def test_hard_flags_decide_identically_in_every_variant(self):
        import itertools
        for st, er, fr, br, tx, ex in itertools.product((None, 200, 503), (None, "E"), (None, "STOP", "MAX_TOKENS", "SAFETY"), (None, "SAFETY"),
                                                        ("", " \n", "a", chr(0xA0)), (None, ValueError("x"))):
            r = {"http_status": st, "error": er, "finish_reason": fr, "block_reason": br, "response_text": tx}
            for v in A.VARIANTS:
                fl = A.flags_variant(r, v, ex)
                if "SF3" in fl or "SF5" in fl or "SF2" in fl:
                    self.assertEqual(A.outcome_of_flags(fl), "FAIL-S", (v, r))


class TestRelationToTheMarkedTests(unittest.TestCase):
    def test_A1_is_the_rule_encoded_by_the_expected_failure_group_and_B_the_pinned_group(self):
        for c in ("K04", "K05", "K06", "K11", "K13", "K14"):
            self.assertEqual(ROWS[c]["A1"]["outcome"], "FAIL-S", c)       # what test_sf2_detection.TestSf2LiteralTextExpectedToFail expects
            self.assertEqual(ROWS[c]["B"]["outcome"], "PASS", c)          # what TestSf2CurrentBehaviourPinned pins


if __name__ == "__main__":
    unittest.main()
