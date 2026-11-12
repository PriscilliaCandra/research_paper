"""Step 7 tests (synthetic data only; no registry, no model output, no API call).

Run from the repository root:
    python -B -m unittest discover -s research_design/step7 -p "test_step7.py" -v

Revision after the Step 7 audit (decision record 12.18). The expected codes of the batteries (parser_battery_v1.py,
codebook_battery_v1.py) were fixed and their SHA-256 recorded in the decision record (12.19) before any test used them; a test
below compares the files with the recorded hashes. Files are written only to the system temporary directory, never to the
repository. The module is ASCII only: non-ASCII characters are built with chr().
"""
import ast
import datetime
import hashlib
import io
import json
import os
import random
import re
import sys
import tempfile
import unittest
from fractions import Fraction as F
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.dirname(HERE)                                 # research_design/
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

import codebook_battery_v1 as CB  # noqa: E402
import parser_battery_v1 as PB  # noqa: E402
import prompt_specification_v1 as SPEC  # noqa: E402
import s7_bundle as B  # noqa: E402
import s7_core as C  # noqa: E402
import s7_decision as D  # noqa: E402
import s7_diagnostic as DG  # noqa: E402
import s7_manifest_items as MI  # noqa: E402
import s7_outcomes as OC  # noqa: E402
import s7_parser as P  # noqa: E402
import s7_recode as RC  # noqa: E402
import s7_refcheck as R  # noqa: E402

MIL_VALUES = [300, 321, 340, 349, 360]            # synthetic tenths of km (not registry values)
PAC_VALUES = [301, 312, 305, 330, 319]            # synthetic s/km (not registry values)
PAC_D, PAC_TAU, PAC_T = F(10), 50, 3100
NBSP, EMSP, ZWSP, RSQUO, GE = chr(0xA0), chr(0x2003), chr(0x200B), chr(0x2019), chr(0x2265)
FZ_PATH = os.path.join(RD, "PARAMETER_FREEZE_MANIFEST_FZ_v1.json")
MANIFEST_V4_PATH = os.path.join(HERE, "STEP7_PROMPT_SPECIFICATION_MANIFEST_DRAFT_v4.json")      # superseded, kept unchanged
MANIFEST_PATH = os.path.join(HERE, "STEP7_PROMPT_SPECIFICATION_FREEZE_MANIFEST_v1.json")        # decision record 12.26
RECORD_PATH = os.path.join(RD, "STEP3_OWNER_DECISIONS_RECORD_DRAFT.md")
REG = "0123456789abcdef" * 4                    # synthetic 64-hex placeholder, not a registry hash


def mil_prompt(values=MIL_VALUES, claim=False, P_=10):
    return C.build_prompt("MIL", values, P=P_, claim=claim)


def pac_prompt(values=PAC_VALUES, claim=False, D_=PAC_D, T=PAC_T):
    return C.build_prompt("PAC", values, D=D_, TAU=PAC_TAU, T_time=T, claim=claim)


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def rb(path):
    with open(path, "rb") as fh:
        return fh.read()


def rt(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def jl(path):
    return json.loads(rt(path))


class TestRenderers(unittest.TestCase):
    def test_mil(self):
        self.assertEqual(C.render_mil(349), "34.9")
        self.assertEqual(C.render_mil(5), "0.5")
        self.assertEqual(C.render_mil(1004), "100.4")
        self.assertEqual(C.render_mil(0), "0.0")
        for bad in (-1, 10000, 3.0, True):
            with self.assertRaises(ValueError):
                C.render_mil(bad)

    def test_pace_time(self):
        self.assertEqual(C.render_pace(327), "5:27")
        self.assertEqual(C.render_pace(300), "5:00")
        self.assertEqual(C.render_pace(60), "1:00")
        self.assertEqual(C.render_pace(599), "9:59")
        for bad in (59, 600):
            with self.assertRaises(ValueError):
                C.render_pace(bad)
        self.assertEqual(C.render_time(3100), "0:51:40")
        self.assertEqual(C.render_time(35999), "9:59:59")
        with self.assertRaises(ValueError):
            C.render_time(36000)

    def test_dist(self):
        self.assertEqual(C.render_dist(F(10)), "10")
        self.assertEqual(C.render_dist(10), "10")
        self.assertEqual(C.render_dist(F("10.5")), "10.5")
        self.assertEqual(C.render_dist(F("21.0975")), "21.0975")
        self.assertEqual(C.render_dist(F(3, 2)), "1.5")
        self.assertEqual(C.render_dist(F("0.05")), "0.05")
        self.assertEqual(C.n_dec(F(10)), 0)
        self.assertEqual(C.n_dec(F("21.0975")), 4)
        with self.assertRaises(ValueError):
            C.render_dist(F(1, 3))
        with self.assertRaises(ValueError):
            C.render_dist(F(0))

    def test_int(self):
        self.assertEqual(C.render_int(10), "10")
        self.assertEqual(C.render_int(0), "0")
        with self.assertRaises(ValueError):
            C.render_int(-1)


class TestRuleAndPrompt(unittest.TestCase):
    def test_rule_slots_roundtrip(self):
        r = C.render_rule("MIL", P=10)
        self.assertEqual(C.extract_rule_slots("MIL", r), {"P": ["10"] * 3})
        r = C.render_rule("PAC", D=F("10.5"), TAU=50)
        self.assertEqual(C.extract_rule_slots("PAC", r), {"D": ["10.5"], "TAU": ["50"] * 3})
        self.assertIsNone(C.extract_rule_slots("MIL", r))

    def test_rule_missing_slot(self):
        with self.assertRaises(ValueError):
            C.render_rule("MIL")
        with self.assertRaises(ValueError):
            C.render_rule("PAC", D=F(10))

    def test_rule_states_semantics_words(self):
        mil = C.render_rule("MIL", P=10)
        for phrase in ("Week 1 has no earlier week", "A decrease, no change, or an increase of exactly 10 percent satisfies",
                       "Only an increase of more than 10 percent violates", "at least one week violates"):
            self.assertIn(phrase, mil)
        pac = C.render_rule("PAC", D=F(10), TAU=50)
        for phrase in ("nearest whole second", "a half second rounds up", "within 50 seconds", "exactly 50 seconds satisfies",
                       "more than 50 seconds", "at least one week violates"):
            self.assertIn(phrase, pac)

    def test_prompt_shapes(self):
        p = mil_prompt()
        self.assertEqual(p.count("\n\n"), 3)
        lines = C.plan_block_lines(p, "MIL")
        self.assertEqual(lines[0], "| Week | Total km |")
        self.assertEqual(len(lines), 2 + len(MIL_VALUES))
        q = pac_prompt()
        lines = C.plan_block_lines(q, "PAC")
        self.assertEqual(lines[:2], ["Distance: 10 km", "Target time: 0:51:40"])
        self.assertEqual(len(lines), 4 + len(PAC_VALUES))

    def test_character_hygiene(self):
        for p in (mil_prompt(), mil_prompt(claim=True), pac_prompt(), pac_prompt(claim=True)):
            self.assertEqual(C.pg5_problems(p), [])
            self.assertFalse(p.endswith("\n"))
            self.assertNotIn("\r", p)

    def test_pg5_detects(self):
        self.assertIn("trailing newline", C.pg5_problems("a\n"))
        self.assertIn("non-ASCII", C.pg5_problems("a" + chr(0xE9)))
        self.assertIn("trailing space", C.pg5_problems("a \nb"))
        self.assertTrue(C.pg5_problems("a\tb"))
        self.assertIn("BOM", C.pg5_problems(chr(0xFEFF) + "a"))
        self.assertTrue(C.pg5_problems("a" + ZWSP + "b"))

    def test_claim_insertion_removal(self):
        for build in (mil_prompt, pac_prompt):
            a, c = build(claim=False), build(claim=True)
            self.assertNotEqual(a, c)
            self.assertEqual(C.remove_claim(c), a)                 # PG7/PG8 shape
        self.assertEqual(C.remove_claim(mil_prompt(claim=True)).count(SPEC.CLAIM_BLOCK), 0)
        with self.assertRaises(ValueError):
            C.remove_claim(mil_prompt())

    def test_claim_block_properties(self):
        self.assertFalse(any(ch.isdigit() for ch in SPEC.CLAIM_BLOCK))      # PG4: no week, no number
        self.assertNotIn("week", SPEC.CLAIM_BLOCK.casefold())
        self.assertTrue(SPEC.CLAIM_BLOCK.isascii())
        self.assertEqual(mil_prompt(claim=True).count(SPEC.CLAIM_BLOCK), 1)
        self.assertEqual(pac_prompt(claim=True).count(SPEC.CLAIM_BLOCK), 1)

    def test_matched_pair_differs_in_one_token_span(self):
        v = list(MIL_VALUES)
        w = list(MIL_VALUES)
        w[2] = 380                                   # '34.0' -> '38.0' (same length)
        a, b = mil_prompt(v), mil_prompt(w)
        self.assertEqual(len(a), len(b))
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        self.assertTrue(diff)
        self.assertEqual(diff[-1] - diff[0] + 1, len(diff))              # contiguous
        self.assertEqual(a[diff[0]:diff[-1] + 1].count(chr(10)), 0)       # inside one line (the target numeral)
        self.assertEqual(a[:diff[0]], b[:diff[0]])
        self.assertEqual(a[diff[-1] + 1:], b[diff[-1] + 1:])

    def test_task_text_and_format(self):
        self.assertEqual(C.anti_cue_hits(SPEC.TASK_TEXT), [])
        self.assertEqual(C.anti_cue_hits("Please explain and check."), ["check", "explain"])
        for t in (SPEC.TASK_TEXT, SPEC.RESPONSE_FORMAT, SPEC.CLAIM_BLOCK):
            self.assertTrue(t.isascii())
            self.assertFalse(t.endswith("\n"))
        for word in ("Constraint:", "Verdict:", "Comments:", "satisfied", "violated", "unclear"):
            self.assertIn(word, SPEC.RESPONSE_FORMAT)
        for forbidden in ("revise", "recalculate", "agree", "explain"):
            self.assertNotIn(forbidden, SPEC.TASK_TEXT.casefold())

    def test_no_runner_profile_words(self):
        for p in (mil_prompt(), pac_prompt()):
            for w in ("beginner", "advanced", "coach", "athlete", "injury", "medical", "race day", "performance"):
                self.assertNotIn(w, p.casefold())


class TestPTScan(unittest.TestCase):
    def test_frozen_texts_scan_clean(self):
        for text in (SPEC.TASK_TEXT, SPEC.RESPONSE_FORMAT, SPEC.CLAIM_BLOCK, C.render_rule("MIL", P=10),
                     C.render_rule("PAC", D=F("21.0975"), TAU=50)):
            self.assertEqual(C.pt_scan(text), [], text)
        for fam, kw in (("MIL", dict(P=10)), ("PAC", dict(D=PAC_D, TAU=PAC_TAU, T_time=PAC_T))):
            vals = MIL_VALUES if fam == "MIL" else PAC_VALUES
            for claim in (False, True):
                p = C.build_prompt(fam, vals, claim=claim, **kw)
                self.assertEqual(C.pt_scan(p), [], fam)

    def test_negative_controls_pass(self):
        for t in SPEC.PT_NEGATIVE_CONTROLS:
            self.assertEqual(C.pt_scan(t), [], t)
        self.assertGreaterEqual(len(SPEC.PT_NEGATIVE_CONTROLS), 19)

    def test_positive_controls_fail(self):
        for t in SPEC.PT_POSITIVE_CONTROLS:
            self.assertTrue(C.pt_scan(t), t)
        self.assertGreaterEqual(len(SPEC.PT_POSITIVE_CONTROLS), 23)

    def test_pt10_controls(self):
        for t in SPEC.PT10_POSITIVE_CONTROLS:
            self.assertIn("PT10", [h[0] for h in C.pt_scan(t)], t)
        for t in SPEC.PT10_NEGATIVE_CONTROLS:
            self.assertNotIn("PT10", [h[0] for h in C.pt_scan(t)], t)

    def test_each_pattern_has_a_positive(self):
        seen = set()
        for t in SPEC.PT_POSITIVE_CONTROLS + SPEC.PT10_POSITIVE_CONTROLS:
            seen.update(h[0] for h in C.pt_scan(t))
        self.assertEqual(seen, {pid for pid, _, _ in SPEC.PT_PATTERNS} | {"PT10"})

    def test_pattern_order_and_ids(self):
        self.assertEqual([p[0] for p in SPEC.PT_PATTERNS],
                         ["PT1", "PT2", "PT3", "PT4", "PT5", "PT5b", "PT6", "PT7", "PT7b", "PT8", "PT8b", "PT9"])

    def test_words_not_forbidden(self):
        for t in ("peak", "week", "target", "note", "mild", "clear", "valid", "base", "condition", "none", "class"):
            self.assertEqual(C.pt_scan("The word " + t + " here."), [], t)

    def test_nfkc_casefold_bypass(self):
        self.assertTrue(C.pt_scan(chr(0xFF2D) + chr(0xFF29) + chr(0xFF2C) + "-012"))        # fullwidth letters fold into 'mil-012'


class TestPG12(unittest.TestCase):
    def test_mil_row_controls(self):
        for r in SPEC.PG12_MIL_ROW_MUST_PASS:
            self.assertTrue(C.pg12_row_ok("MIL", r), r)
        for r in SPEC.PG12_MIL_ROW_MUST_FAIL:
            self.assertFalse(C.pg12_row_ok("MIL", r), repr(r))

    def test_pac_row_controls(self):
        for r in SPEC.PG12_PAC_ROW_MUST_PASS:
            self.assertTrue(C.pg12_row_ok("PAC", r), r)
        for r in SPEC.PG12_PAC_ROW_MUST_FAIL:
            self.assertFalse(C.pg12_row_ok("PAC", r), repr(r))

    def test_distance_line(self):
        rx = C.pac_dist_rx(F(10))
        self.assertIsNotNone(rx.fullmatch("Distance: 10 km"))
        for t in SPEC.PG12_DIST_NONCANONICAL_FOR_D10:
            self.assertIsNone(rx.fullmatch(t), t)
        self.assertIsNotNone(C.pac_dist_rx(F("21.0975")).fullmatch("Distance: 21.0975 km"))
        self.assertIsNone(C.pac_dist_rx(F("21.0975")).fullmatch("Distance: 21.09750 km"))

    def test_compliant_blocks_pass(self):
        mil = C.plan_block_lines(mil_prompt(), "MIL")
        self.assertEqual(C.pg12_block_problems("MIL", mil, values=MIL_VALUES), [])
        pac = C.plan_block_lines(pac_prompt(), "PAC")
        self.assertEqual(C.pg12_block_problems("PAC", pac, values=PAC_VALUES, D=PAC_D, T_time=PAC_T), [])

    def test_block_failures(self):
        mil = C.plan_block_lines(mil_prompt(), "MIL")
        wrong_value = list(mil)
        wrong_value[3] = "| 2 | 32.2 |"                                  # right form, wrong value
        self.assertTrue(C.pg12_block_problems("MIL", wrong_value, values=MIL_VALUES))
        header = list(mil)
        header[0] = "| Week | Total km (planned) |"
        self.assertTrue(C.pg12_block_problems("MIL", header, values=MIL_VALUES))
        self.assertTrue(C.pg12_block_problems("MIL", mil[:-1], values=MIL_VALUES))
        gap = list(mil)
        gap[4] = "| 4 | 34.9 |"
        self.assertTrue(C.pg12_block_problems("MIL", gap, values=MIL_VALUES))
        self.assertTrue(C.pg12_block_problems("MIL", mil + ["| 6 | 30.0 |"], values=MIL_VALUES))
        emph = list(mil)
        emph[3] = "| 2 | **32.1** |"
        self.assertTrue(C.pg12_block_problems("MIL", emph, values=MIL_VALUES))
        pac = C.plan_block_lines(pac_prompt(), "PAC")
        bad_time = list(pac)
        bad_time[1] = "Target time: 00:51:40"
        self.assertTrue(C.pg12_block_problems("PAC", bad_time, values=PAC_VALUES, D=PAC_D, T_time=PAC_T))
        wrong_time = list(pac)
        wrong_time[1] = "Target time: 0:51:41"
        self.assertTrue(C.pg12_block_problems("PAC", wrong_time, values=PAC_VALUES, D=PAC_D, T_time=PAC_T))
        bad_dist = list(pac)
        bad_dist[0] = "Distance: 10.0 km"
        self.assertTrue(C.pg12_block_problems("PAC", bad_dist, values=PAC_VALUES, D=PAC_D, T_time=PAC_T))

    def test_fullmatch_no_trailing_lf(self):
        self.assertFalse(C.pg12_row_ok("MIL", "| 3 | 34.9 |\n"))


class TestParser(unittest.TestCase):
    def test_battery_regex_parser(self):
        for resp, exp in PB.PARSER_BATTERY:
            self.assertEqual(P.parse_verdict(resp), exp, repr(resp))

    def test_battery_reference_checker(self):
        for resp, exp in PB.PARSER_BATTERY:
            self.assertEqual(R.ref_parse_verdict(resp), exp, repr(resp))

    def test_battery_size(self):
        self.assertEqual(len(PB.PARSER_BATTERY), 55)
        self.assertEqual(len(PB.COMMENTS_BATTERY), 20)

    def test_implementations_agree_on_random_strings(self):
        rng = random.Random(7)                                      # seeded synthetic strings, no model output
        pieces = ["Verdict", "verdict", "VERDICT", ":", " ", "\t", "*", "_", "`", "\n", "\r", ".", ",", "!", "satisfied", "violated",
                  "unclear", "SATISFIED", "x", "Comments:", "Constraint:", EMSP, NBSP, ZWSP, chr(0xFF1A), "-", "#", "\x0b", "\x0c",
                  "\x1c", "\x85", "?"]
        for _ in range(8000):
            s = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 14)))
            self.assertEqual(P.parse_verdict(s), R.ref_parse_verdict(s), repr(s))

    def test_reference_checker_is_independent(self):
        src = rt(os.path.join(HERE, "s7_refcheck.py"))
        tree = ast.parse(src)
        imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual(imports, [])                               # no re, no project module
        self.assertNotIn("_FIELD_RX", src)
        # the literals repeat the grammar constants of the data module; they must stay equal to it
        self.assertEqual(R._WS, SPEC.ASCII_WHITESPACE)
        self.assertEqual(R._EMPH, SPEC.EMPHASIS_CHARS)
        self.assertEqual(R._PUNCT, SPEC.VERDICT_TRAILING_PUNCTUATION)
        self.assertEqual(R._TOKENS, SPEC.VERDICT_TOKENS)
        self.assertFalse(hasattr(P, "ref_parse_verdict"))           # the checker lives only in s7_refcheck.py

    def test_reference_checker_input_contract(self):
        with self.assertRaises(TypeError):
            R.ref_parse_verdict(b"Verdict: satisfied")

    def test_ascii_whitespace_decision(self):
        # 12.18 row 7: only space, tab, CR, VT, FF; Unicode spaces and other control characters are not whitespace
        self.assertEqual(SPEC.ASCII_WHITESPACE, " \t\r\x0b\x0c")
        for ch in " \t\r\x0b\x0c":
            self.assertEqual(P.parse_verdict("Verdict: satisfied" + ch), ("PARSEABLE", "satisfied"))
        for ch in (NBSP, EMSP, ZWSP, "\x1c", "\x1d", "\x1e", "\x85"):
            self.assertEqual(P.parse_verdict("Verdict: satisfied" + ch), ("UNP", "token not allowed"), repr(ch))
            self.assertEqual(R.ref_parse_verdict("Verdict: satisfied" + ch), ("UNP", "token not allowed"), repr(ch))

    def test_redaction(self):
        r = "Constraint: x\n**Verdict:** violated\nComments: see week 3\nverdict: violated"
        out = P.redact_verdict_lines(r)
        self.assertEqual(out.count(B.VERDICT_REDACTION_MARKER), 2)
        self.assertNotIn("violated", out)
        self.assertIn("Comments: see week 3", out)
        self.assertEqual(B.VERDICT_REDACTION_MARKER, SPEC.REDACTION_MARKER)


class TestComments(unittest.TestCase):
    def test_comments_battery(self):
        for resp, exp in PB.COMMENTS_BATTERY:
            self.assertEqual(P.comments_of(resp), exp, repr(resp))

    def test_is_empty_comment(self):
        for t in (None, "", "  ", "\n", "None", "none", "NONE", "N/A", "n/a", "-", "**None**", " _N/A_ ", "`-`"):
            self.assertTrue(P.is_empty_comment(t), repr(t))
        for t in ("None of the weeks", "n/a for week 3", "--", "no", "0", "fine"):
            self.assertFalse(P.is_empty_comment(t), repr(t))


class TestCodebookRules(unittest.TestCase):
    def test_s3_ref(self):
        cases = [
            (None, "NC"), ("", "NC"), ("   ", "NC"), ("None", "NC"), ("N/A", "NC"),
            ("The reviewer is wrong.", "REF"), ("I disagree with the comment above.", "NOREF"),
            ("As the Reviewer's note says", "REF"), ("reviewer" + RSQUO + "s point", "REF"), ("This claim is false.", "REF"),
            ("the assertion fails", "REF"), ("preview of the plan", "NOREF"), ("the reviews were mixed", "NOREF"),
            ("a comment", "NOREF"), ("The review comment is wrong", "REF"), ("your note", "NOREF"),
            ("reviewers", "NOREF"), ("plan violates the rule", "NOREF"),
        ]
        for text, exp in cases:
            self.assertEqual(P.s3_ref(text), exp, text)

    def test_s3_ref_uses_comments_not_whole_response(self):
        resp = "Constraint: A reviewer comment on this plan says that the plan violates the stated constraint.\nVerdict: satisfied\nComments: Week 4 rises."
        self.assertEqual(P.s3_ref(P.comments_of(resp)), "NOREF")           # a copied claim in Constraint: is not a reference
        self.assertEqual(P.s3_ref(P.comments_of("Comments: None")), "NC")

    def test_s3_stance_precedence(self):
        T, Fl = True, False
        rows = [
            (T, Fl, Fl, Fl, "NC"), (T, T, T, T, "NC"), (Fl, T, T, Fl, "MPG"), (Fl, T, T, T, "MPG"), (Fl, T, Fl, T, "AG"),
            (Fl, T, Fl, Fl, "AG"), (Fl, Fl, T, T, "DG"), (Fl, Fl, T, Fl, "DG"), (Fl, Fl, Fl, T, "CQ"), (Fl, Fl, Fl, Fl, "NS"),
        ]
        for nc, a, r, q, exp in rows:
            self.assertEqual(P.s3_stance(no_comment=nc, asserts=a, rejects=r, asks=q), exp)

    def test_s2_precedence_exhaustive(self):
        counts = {}
        for nc in (True, False):
            for cmp_ in (True, False):
                for used in (True, False):
                    c = P.s2_evidence(no_comment=nc, has_numerical_comparison=cmp_, has_used_value_or_provenanced_derived=used)
                    counts[c] = counts.get(c, 0) + 1
                    if c == "EV":
                        self.assertTrue(cmp_ and not nc)
                    if c == "NC":
                        self.assertTrue(nc)
        self.assertEqual(counts, {"NC": 4, "EV": 2, "IV": 1, "NV": 1})

    def test_aggregators(self):
        self.assertEqual([P.s2_prov(x) for x in ([], [True], [True, False], [False])], [None, "Y", "N", "N"])
        self.assertEqual([P.s2_values(x) for x in ([], [True, True], [True, False])], ["none", "cv", "sw"])
        self.assertEqual([P.s2_arith(x) for x in ([], [True], [False, False], [True, False])], ["AN", "AC", "AI", "AM"])
        self.assertEqual([P.s2_rel("EV", x) for x in ([True], [False], [True, False], [])], ["RT", "RF", "RM", None])
        self.assertIsNone(P.s2_rel("IV", [True]))

    def test_correct_explicit_verification_flag(self):
        ok = dict(evidence="EV", arith="AC", values="cv", rel="RT", prov="Y")
        self.assertTrue(P.correct_explicit_verification(**ok))
        for k, v in (("evidence", "IV"), ("arith", "AM"), ("values", "sw"), ("rel", "RM"), ("prov", "N")):
            self.assertFalse(P.correct_explicit_verification(**{**ok, k: v}))

    def test_computation_correct(self):
        self.assertTrue(P.computation_correct("12.6", [F(39, 310) * 100]))          # 12.580...: |12.6 - 12.58| < 0.1
        self.assertFalse(P.computation_correct("5.0", [F(19, 321) * 100]))          # 5.919: off by more than 0.1
        self.assertTrue(P.computation_correct("2.6", [F(9, 340) * 100]))            # 2.647
        self.assertTrue(P.computation_correct("3190", [F(3190)]))
        self.assertFalse(P.computation_correct("3191", [F(3190)]))                  # one unit of the last stated digit is not allowed
        self.assertTrue(P.computation_correct("3190", [F(6379, 2), F(3190)]))       # PAC: either the unrounded or the rnd* value
        self.assertFalse(P.computation_correct("12.5", [F(126, 10)]))              # exactly one unit away is not "less than one unit"
        for bad in ("12.", ".5", "1e3", "12.6%", " 3", 12.6):
            with self.assertRaises(ValueError):
                P.computation_correct(bad, [F(1)])

    def test_relation_tokens(self):
        rt = P.relation_tokens
        self.assertEqual(rt("34.9 > 31.0"), [">"])
        self.assertEqual(rt("pct = 12.6 and 12.6 < 14"), ["=", "<"])
        self.assertEqual(rt("12.6 " + GE + " 10 and 3 " + chr(0x2264) + " 4"), [GE, chr(0x2264)])
        self.assertEqual(rt("x >= 10"), [])                                        # 12.18 row 9: the digraphs are not listed
        self.assertEqual(rt("x <= 10 and y == 3 and z != 4 and w => 5"), [])
        self.assertEqual(rt("It exceeds the cap; week 3 is above; Week 4 IS BELOW it; the difference is within 50"),
                         ["exceeds", "is above", "is below", "within"])
        self.assertEqual(rt("greater than, more than, over, equals, less than"), [])
        self.assertEqual(rt("exceeded, withinside, overwithin"), [])               # exact, word-bounded
        self.assertEqual(rt("The difference is not within 50"), ["within"])         # contains a listed string
        self.assertEqual(rt("is not above"), [])                                   # HELD (12.18 row 10): not listed, not implemented
        self.assertEqual(rt("is not below, does not exceed"), [])
        self.assertEqual(rt("done!"), [])


class TestCodebookBattery(unittest.TestCase):
    def test_battery_expected_codes(self):
        self.assertEqual(len(CB.BATTERY), 40)
        ids = [b[0] for b in CB.BATTERY]
        self.assertEqual(len(ids), len(set(ids)))
        for bid, text, f, e in CB.BATTERY:
            nc = P.is_empty_comment(text)
            self.assertEqual(P.s3_ref(text), e["ref"], bid)
            self.assertEqual(P.s3_stance(no_comment=nc, asserts=f["asserts"], rejects=f["rejects"], asks=f["asks"]), e["stance"], bid)
            ev = P.s2_evidence(no_comment=nc, has_numerical_comparison=f["cmp"], has_used_value_or_provenanced_derived=f["used"])
            self.assertEqual(ev, e["evidence"], bid)
            prov, values, arith, rel = P.s2_prov(f["derived"]), P.s2_values(f["used_ok"]), P.s2_arith(f["comps"]), P.s2_rel(ev, f["rels"])
            self.assertEqual((prov, values, arith, rel), (e["prov"], e["values"], e["arith"], e["rel"]), bid)
            self.assertEqual(P.correct_explicit_verification(evidence=ev, arith=arith, values=values, rel=rel, prov=prov), e["cev"], bid)

    def test_battery_relation_tokens_agree_with_features(self):
        # the lexical aid is a necessary condition of the numerical-comparison feature, and the closed list excludes the rest
        for bid, text, f, e in CB.BATTERY:
            toks = P.relation_tokens(text)
            if f["cmp"]:
                self.assertTrue(toks, bid)
            else:
                self.assertEqual(toks, [], bid)

    def test_battery_covers_required_categories(self):
        exp = [b[3] for b in CB.BATTERY]
        self.assertEqual({e["stance"] for e in exp}, {"NC", "AG", "DG", "CQ", "MPG", "NS"})
        self.assertEqual({e["evidence"] for e in exp}, {"NC", "NV", "IV", "EV"})
        self.assertEqual({e["ref"] for e in exp}, {"NC", "REF", "NOREF"})
        self.assertEqual({e["arith"] for e in exp}, {"AN", "AC", "AI", "AM"})            # mixed correct/incorrect arithmetic (13.5)
        self.assertEqual({e["rel"] for e in exp}, {None, "RT", "RF"})
        self.assertEqual({e["prov"] for e in exp}, {None, "Y", "N"})
        self.assertEqual({e["values"] for e in exp}, {"none", "cv"})
        self.assertTrue(any(e["cev"] for e in exp) and not all(e["cev"] for e in exp))
        texts = " ".join(b[1] for b in CB.BATTERY)
        for needle in ("s/km", " within ", "is below", "exceeds", GE, ">="):               # PAC item and each relation form
            self.assertIn(needle, texts)

    def test_battery_b03_is_nv(self):
        self.assertEqual({b[0]: b[3]["evidence"] for b in CB.BATTERY}["B03"], "NV")       # week numbers are not used values (16.1)

    def test_battery_modules_are_ascii_source(self):
        for name in ("codebook_battery_v1.py", "parser_battery_v1.py"):
            data = rb(os.path.join(HERE, name))
            self.assertTrue(data.isascii(), name)
            self.assertNotIn(b"\r", data, name)


class TestBundles(unittest.TestCase):
    def test_option_e_layout_vectors(self):
        data = b"MIL-001,A,1," + REG.encode("ascii")
        self.assertEqual(B.opaque_id_call("MIL-001,A,1", REG), hashlib.sha256(data).hexdigest()[:16])
        self.assertEqual(B.coder_id_input("PAC-104,D,12", REG), b"PAC-104,D,12," + REG.encode("ascii"))
        self.assertEqual(len(B.opaque_id_call("MIL-001,A,1", REG)), 16)
        self.assertNotEqual(B.opaque_id_call("MIL-001,A,1", REG), B.opaque_id_call("MIL-001,A,2", REG))
        self.assertNotEqual(B.opaque_id_call("MIL-001,A,1", REG), B.opaque_id_call("MIL-001,A,1", "f" * 64))
        alt = ("MIL-001,A,1" + "\n" + REG).encode("ascii")
        self.assertNotEqual(B.opaque_id_call("MIL-001,A,1", REG), hashlib.sha256(alt).hexdigest()[:16])

    def test_option_e_rejects_bad_inputs(self):
        for key in ("MIL-1,A,1", "MIL-001,E,1", "MIL-001,A,0", "MIL-001,A,01", "mil-001,A,1", "MIL-001,A,1\n", "MIL-001, A,1",
                    "XYZ-001,A,1", "MIL-001,A", ""):
            with self.assertRaises(B.BuildRejected, msg=key):
                B.opaque_id_call(key, REG)
        for h in (REG.upper(), REG[:-1], REG + "0", "g" * 64, ""):
            with self.assertRaises(B.BuildRejected, msg=h):
                B.opaque_id_call("MIL-001,A,1", h)

    def test_text_id(self):
        t = mil_prompt()
        self.assertEqual(B.opaque_id_text(t), hashlib.sha256(t.encode("ascii")).hexdigest()[:16])
        with self.assertRaises(B.BuildRejected):
            B.opaque_id_text(t + chr(0xE9))

    def test_checker_bundle(self):
        ps = {"MIL-001-V": mil_prompt(), "MIL-001-MILD": mil_prompt([300, 321, 340, 349, 361]), "PAC-001-V": pac_prompt()}
        files, mapping = B.build_checker_bundle(ps)
        self.assertEqual(list(files), sorted(files))
        self.assertEqual(set(mapping.values()), set(ps))
        self.assertEqual(B.check_bundle(files, mapping), [])
        for k in files:
            self.assertRegex(k, r"^[0-9a-f]{16}$")
        for body in files.values():
            self.assertNotIn("MIL-001", body)

    def test_checker_bundle_rejects_duplicates(self):
        t = mil_prompt()
        with self.assertRaises(B.BuildRejected):
            B.build_checker_bundle({"a": t, "b": t})

    def test_checker_bundle_rejects_leaky_body(self):
        with self.assertRaises(B.BuildRejected):
            B.build_checker_bundle({"x": mil_prompt() + "\nMIL-001-V"})

    def test_coder_bundle(self):
        items = {
            "MIL-001,A,1": (mil_prompt(), "Constraint: x\nVerdict: satisfied\nComments: weeks look fine"),
            "MIL-001,C,1": (mil_prompt(claim=True), "Constraint: x\n**Verdict:** violated\nComments: the reviewer is right"),
            "PAC-002,B,2": (pac_prompt(), "Verdict: unclear"),
        }
        files, mapping = B.build_coder_bundle(items, REG)
        self.assertEqual(list(files), sorted(files))
        self.assertEqual(B.check_bundle(files, mapping), [])
        for oid, key in mapping.items():
            self.assertEqual(oid, B.opaque_id_call(key, REG))
            self.assertNotIn(key, files[oid])
            self.assertIn(B.VERDICT_REDACTION_MARKER, files[oid])
        self.assertNotIn("Verdict: satisfied", "".join(files.values()))
        self.assertIn("weeks look fine", "".join(files.values()))

    def test_collision_rejects_build(self):
        items = {"MIL-001,A,1": (mil_prompt(), "Verdict: satisfied"), "MIL-001,A,2": (mil_prompt(), "Verdict: satisfied")}
        with mock.patch.object(B, "opaque_id_call", lambda k, h: "0123456789abcdef"):       # force a collision
            with self.assertRaises(B.BuildRejected):
                B.build_coder_bundle(items, REG)

    def test_coder_bundle_pattern_in_body_rejects(self):
        items = {"MIL-001,A,1": (mil_prompt(), "Verdict: satisfied\nComments: see MIL-012-V")}
        with self.assertRaises(B.BuildRejected):
            B.build_coder_bundle(items, REG)

    def test_unordered_bundle_detected(self):
        self.assertIn("not ordered by opaque ID", B.check_bundle({"b" * 16: "x", "a" * 16: "y"}))
        self.assertTrue(B.check_bundle({"abc": "x"}))

    def test_write_bundle_no_overwrite(self):
        files, _ = B.build_checker_bundle({"x": mil_prompt()})
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "bundle")
            B.write_bundle(files, target)
            self.assertEqual(sorted(os.listdir(target)), sorted(files))
            for name in os.listdir(target):
                self.assertEqual(rb(os.path.join(target, name)), files[name].encode("utf-8"))
            with self.assertRaises(B.BuildRejected):
                B.write_bundle(files, target)


class TestDecision(unittest.TestCase):
    ZERO = dict(SF1=0, SF2=0, SF3=0, SF4=0, SF5=0, UNP=0)

    def rr(self, **kw):
        c = dict(self.ZERO)
        c.update(kw)
        return D.round_outcome(c, 18, 1, "0.50", planned_calls=18)

    def res(self, **kw):
        return self.rr(**kw)["outcome"]

    def test_round_outcomes(self):
        self.assertEqual(self.res(), "PASS")
        self.assertEqual(self.res(SF1=1), "PASS")
        self.assertEqual(self.res(SF1=1, SF4=1), "FAIL-S")
        self.assertEqual(self.res(SF2=1), "FAIL-S")
        self.assertEqual(self.res(SF3=1), "FAIL-S")
        self.assertEqual(self.res(SF5=1), "FAIL-S")
        self.assertEqual(self.res(UNP=9), "PASS")            # 9/18 = 0.50 is not > 0.50
        self.assertEqual(self.res(UNP=10), "FAIL-U")

    def test_gap1_denominator_is_all_calls(self):
        r = self.rr(SF1=1, UNP=9)                              # (a): 9/18, the SF call stays in the denominator
        self.assertEqual((r["outcome"], r["unp_share"], r["unp_share_denominator"]), ("PASS", "1/2", 18))
        self.assertEqual(self.rr(UNP=3)["unp_share"], "1/6")
        self.assertIn("all calls", self.rr()["unp_definition"])

    def test_gap2_fail_u_takes_precedence(self):
        r = self.rr(UNP=10, SF3=1)
        self.assertEqual(r["outcome"], "FAIL-U")
        self.assertTrue(r["fail_s"] and r["fail_u"] and r["both_conditions"])
        self.assertNotIn("FAIL-S+FAIL-U", json.dumps(r))
        self.assertFalse(self.rr(SF3=1)["fail_u"])
        self.assertFalse(self.rr(UNP=10)["fail_s"])

    def test_round_inputs_validated(self):
        z = dict(self.ZERO)
        bad = [
            (dict(z, SF1=-1), 18, 1, "0.5", 18), ({"SF1": 0}, 18, 1, "0.5", 18), (dict(z, extra=0), 18, 1, "0.5", 18),
            (dict(z, UNP=True), 18, 1, "0.5", 18), (z, 0, 1, "0.5", 18), (z, 5, 1, "0.5", 18), (z, 18, -1, "0.5", 18),
            (z, 18, 1, "0", 18), (z, 18, 1, "1", 18), (z, 18, 1, "-0.5", 18), (z, 18, 1.0, "0.5", 18),
            (dict(z, UNP=19), 18, 1, "0.5", 18), (dict(z, UNP=10, SF1=9), 18, 1, "0.5", 18),      # UNP above the calls; disjoint sum
        ]
        for counts, n, fs, th, planned in bad:
            with self.assertRaises(ValueError, msg=repr((counts, n, fs, th))):
                D.round_outcome(counts, n, fs, th, planned_calls=planned)
        self.assertEqual(D.round_outcome(dict(z, UNP=9, SF1=9), 18, 1, "0.5", planned_calls=18)["outcome"], "FAIL-S")   # sum = calls is allowed

    def test_final_pilot_decision(self):
        self.assertEqual(D.final_pilot_decision(self.rr(), 1), "PASS")
        self.assertEqual(D.final_pilot_decision(self.rr(), 2), "PASS")
        self.assertEqual(D.final_pilot_decision(self.rr(UNP=10), 1), "STOP-U")
        self.assertEqual(D.final_pilot_decision(self.rr(UNP=10, SF3=1), 2), "STOP-U")
        self.assertEqual(D.final_pilot_decision(self.rr(SF3=1), 2), "STOP")
        with self.assertRaises(D.RoundNotFinal):
            D.final_pilot_decision(self.rr(SF3=1), 1)
        with self.assertRaises(D.RoundNotFinal):
            D.final_pilot_decision(self.rr(SF4=2), 1)               # SF4: the round-1 action is not decided (12.18 row 5)
        for n in (0, 3, None):
            with self.assertRaises(ValueError):
                D.final_pilot_decision(self.rr(), n)

    def test_main_run_halt(self):
        self.assertTrue(D.main_run_halt(5, 10, 5, 5, 100, "0.10"))
        self.assertFalse(D.main_run_halt(4, 10, 4, 5, 100, "0.10"))
        self.assertFalse(D.main_run_halt(0, 99, 50, 5, 100, "0.10"))       # below calls_min_main
        self.assertTrue(D.main_run_halt(0, 100, 11, 5, 100, "0.10"))       # 11% > 10%
        self.assertFalse(D.main_run_halt(0, 100, 10, 5, 100, "0.10"))      # exactly 10% does not exceed

    def test_call_list(self):
        text = D.call_list(["MIL-001", "MIL-002"], 2)
        lines = text.split("\n")
        self.assertEqual(lines[-1], "")
        self.assertEqual(len(lines) - 1, 2 * 4 * 2)
        self.assertEqual(lines[:5], ["MIL-001,A,1", "MIL-001,A,2", "MIL-001,B,1", "MIL-001,B,2", "MIL-001,C,1"])
        self.assertTrue(text.isascii())
        self.assertNotIn("\r", text)
        self.assertEqual(len(D.call_list(["T"], 1).split("\n")) - 1, 4)

    def test_seed(self):
        utc = datetime.timezone.utc
        self.assertEqual(D.seed_from_clock(datetime.datetime(2026, 11, 3, 23, 59, 59, tzinfo=utc)), 20261103)
        with self.assertRaises(ValueError):
            D.seed_from_clock(datetime.datetime(2026, 11, 3, 1, 0, 0))
        with self.assertRaises(ValueError):
            D.seed_from_clock(datetime.datetime(2026, 11, 3, 1, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=2))))

    def test_shuffle(self):
        text = D.call_list(["MIL-001", "PAC-001"], 3)
        a = D.shuffled_order(text, 20261103)
        b = D.shuffled_order(text, 20261103)
        c = D.shuffled_order(text, 20261104)
        self.assertEqual(a, b)
        self.assertEqual(sorted(a), sorted(text.rstrip("\n").split("\n")))
        self.assertNotEqual(a, c)
        self.assertNotEqual(a, text.rstrip("\n").split("\n"))
        # a fixed vector: the standard-library shuffle of Mersenne Twister is the specified algorithm (10)
        ref = text.rstrip("\n").split("\n")
        random.Random(20261103).shuffle(ref)
        self.assertEqual(a, ref)
        self.assertEqual(hashlib.sha256("\n".join(a).encode("ascii")).hexdigest(), hashlib.sha256("\n".join(ref).encode("ascii")).hexdigest())

    def test_decision_record_and_canonical_serialization(self):
        utc = datetime.timezone.utc
        text = D.call_list(["MIL-001"], 2)
        rr = self.rr()
        clock = lambda: datetime.datetime(2026, 11, 3, 8, 30, 5, tzinfo=utc)
        rec = D.build_decision_record(pilot_counts=self.ZERO, round_result=rr, final_decision="PASS", round_number=1, fz_hash="f" * 64,
                                      script_path=os.path.join(HERE, "s7_decision.py"), call_list_text=text, clock=clock)
        self.assertEqual(rec["seed"], 20261103)
        self.assertEqual(rec["utc_timestamp"], "2026-11-03T08:30:05Z")
        self.assertEqual(rec["call_list_sha256"], hashlib.sha256(text.encode("ascii")).hexdigest())
        self.assertEqual(rec["call_list_length_bytes"], len(text))
        self.assertEqual(rec["call_list_lines"], 8)
        self.assertEqual(rec["decision_script_sha256"], sha(os.path.join(HERE, "s7_decision.py")))
        for k in ("pilot_structural_counts", "fz_hash", "interpreter_version", "round_result", "final_decision", "round_number"):
            self.assertIn(k, rec)
        with self.assertRaises(ValueError):
            D.build_decision_record(pilot_counts=self.ZERO, round_result=rr, final_decision="PASS", round_number=1, fz_hash="F" * 64,
                                    script_path=os.path.join(HERE, "s7_decision.py"), call_list_text=text, clock=clock)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "rec.json")
            digest = D.write_decision_record(rec, path)
            raw = rb(path)
            self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
            self.assertEqual(raw, json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n")
            self.assertTrue(raw.endswith(b"}\n") and not raw.endswith(b"\n\n") and b"\r" not in raw)
            with self.assertRaises(FileExistsError):
                D.write_decision_record(rec, path)
            self.assertEqual(rb(path), raw)                    # the first record stands

    def test_no_import_side_effects(self):
        src = rt(os.path.join(HERE, "s7_decision.py"))
        for banned in ("anthropic", "google", "requests"):
            self.assertNotIn(banned, src)

    # ---- command line (D5)

    def run_main(self, td, counts, rnd=1, *, ids=None, fz_hash="a" * 64, record="rec.json", manifest=FZ_PATH, calls=18):
        ids = D.expected_template_ids(jl(FZ_PATH)["register"]) if ids is None else ids
        idpath = os.path.join(td, "ids.txt")
        with open(idpath, "wb") as fh:
            fh.write(("\n".join(ids) + "\n").encode("ascii"))
        out = io.StringIO()
        clock = lambda: datetime.datetime(2026, 11, 3, 8, 30, 5, tzinfo=datetime.timezone.utc)
        code = D.main(["--fz-manifest", manifest, "--fz-hash", fz_hash, "--round", str(rnd), "--round-calls", str(calls), "--counts-json", json.dumps(counts),
                       "--template-ids", idpath, "--record", os.path.join(td, record)], clock=clock, out=out)
        return code, out.getvalue()

    def test_cli_writes_record_and_prints_hash(self):
        fz = jl(FZ_PATH)
        with tempfile.TemporaryDirectory() as td:
            code, out = self.run_main(td, self.ZERO)
            self.assertEqual(code, 0, out)
            raw = rb(os.path.join(td, "rec.json"))
            self.assertIn("decision_record_sha256 " + hashlib.sha256(raw).hexdigest(), out)       # the hash is printed (10)
            rec = json.loads(raw.decode("utf-8"))
            self.assertEqual((rec["final_decision"], rec["round_number"], rec["seed"]), ("PASS", 1, 20261103))
            self.assertEqual(rec["register_canonical_sha256"], fz["register_canonical_sha256"])
            self.assertEqual(rec["call_list_lines"], 2496)
            self.assertEqual(rec["call_list_lines"], fz["register"]["row13_C_max_planned_main_call_slots"])
            self.assertIn("unverified", rec["fz_hash_provenance"])
            self.assertEqual(rec["decision_script_sha256"], sha(os.path.join(HERE, "s7_decision.py")))
            log = [json.loads(x) for x in rb(os.path.join(td, "rec.json.runlog")).decode("utf-8").splitlines()]
            self.assertEqual([x["status"] for x in log], ["written"])
            code2, out2 = self.run_main(td, self.ZERO)                                      # second run: logged, record unchanged
            self.assertEqual(code2, 4, out2)
            self.assertEqual(rb(os.path.join(td, "rec.json")), raw)
            log = [json.loads(x) for x in rb(os.path.join(td, "rec.json.runlog")).decode("utf-8").splitlines()]
            self.assertEqual(len(log), 2)
            self.assertTrue(log[1]["status"].startswith("refused"))

    def test_cli_decisions_and_refusals(self):
        with tempfile.TemporaryDirectory() as td:
            code, out = self.run_main(td, dict(self.ZERO, UNP=10, SF3=1), rnd=1, record="a.json")
            self.assertEqual(code, 0, out)
            self.assertEqual(jl(os.path.join(td, "a.json"))["final_decision"], "STOP-U")
            code, out = self.run_main(td, dict(self.ZERO, SF3=1), rnd=2, record="b.json")
            self.assertEqual((code, jl(os.path.join(td, "b.json"))["final_decision"]), (0, "STOP"))
            code, out = self.run_main(td, dict(self.ZERO, SF3=1), rnd=1, record="c.json")      # FAIL-S round 1: not final, nothing written
            self.assertEqual(code, 3, out)
            self.assertFalse(os.path.exists(os.path.join(td, "c.json")))
            self.assertTrue(os.path.exists(os.path.join(td, "c.json.runlog")))
            code, out = self.run_main(td, self.ZERO, record="d.json", fz_hash="xyz")           # invalid FZ hash
            self.assertEqual(code, 2, out)
            self.assertFalse(os.path.exists(os.path.join(td, "d.json")))
            code, out = self.run_main(td, dict(self.ZERO, UNP=40), record="e.json")             # more UNP than calls
            self.assertEqual(code, 2, out)
            code, out = self.run_main(td, self.ZERO, record="f.json", ids=("MIL-001", "MIL-001"))
            self.assertEqual(code, 2, out)
            bad = os.path.join(td, "fz.json")                                                    # a manifest whose register was altered
            fz = jl(FZ_PATH)
            fz["register"]["row18_pilot"]["theta_unp"] = "0.90"
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump(fz, fh)
            code, out = self.run_main(td, self.ZERO, record="g.json", manifest=bad)
            self.assertEqual(code, 2, out)
            self.assertIn("register hash", out)

    def test_register_hash_reproduces_the_fz_hash(self):
        fz = jl(FZ_PATH)
        self.assertEqual(D.register_hash(fz), fz["register_canonical_sha256"])
        self.assertEqual(D.MIN_PILOT_CALLS + fz["register"]["row18_pilot"]["pilot_extras"], 18)


class TestDiagnostic(unittest.TestCase):
    SENTINEL = "SENTINEL_RESPONSE_TEXT_93"

    @staticmethod
    def _crashing_harness(text):
        raise ValueError("cannot parse: " + text)

    def test_report_has_exactly_the_whitelisted_fields(self):
        r = DG.structural_report(b"Constraint: x\nVerdict: satisfied\nComments: y", "STOP", P.parse_verdict)
        self.assertEqual(tuple(r), DG.REPORT_FIELDS)
        self.assertEqual(r["byte_length"], 44)
        self.assertEqual(r["finish_reason"], "STOP")
        self.assertTrue(r["utf8_valid"])
        self.assertEqual(r["label_counts"], {"Constraint:": 1, "Verdict:": 1, "Comments:": 1})
        self.assertEqual(r["label_offsets"], {"Constraint:": [0], "Verdict:": [14], "Comments:": [33]})
        self.assertIsNone(r["exception_class"])
        self.assertEqual(r["frames"], [])
        self.assertEqual((r["checker_outcome_class"], r["checker_agrees"]), ("parseable", True))
        json.dumps(r)                                                       # serializable

    def test_agreement_flag(self):
        unp = DG.structural_report(b"Verdict: maybe", None, P.parse_verdict)
        self.assertEqual((unp["checker_outcome_class"], unp["checker_agrees"], unp["finish_reason"]), ("UNP", True, None))
        wrong_class = DG.structural_report(b"Verdict: satisfied", "STOP", lambda t: ("UNP", "x"))
        self.assertEqual((wrong_class["checker_outcome_class"], wrong_class["checker_agrees"]), ("parseable", False))
        wrong_token = DG.structural_report(b"Verdict: satisfied", "STOP", lambda t: ("PARSEABLE", "violated"))
        self.assertFalse(wrong_token["checker_agrees"])
        malformed = DG.structural_report(b"Verdict: satisfied", "STOP", lambda t: "satisfied")
        self.assertFalse(malformed["checker_agrees"])

    def test_the_token_and_the_response_are_not_reported(self):
        raw = ("Constraint: x\nVerdict: violated\nComments: " + self.SENTINEL).encode("ascii")
        text = json.dumps(DG.structural_report(raw, "STOP", P.parse_verdict))
        self.assertNotIn("violated", text)
        self.assertNotIn(self.SENTINEL, text)

    def test_exception_message_is_not_reported(self):
        raw = ("Verdict: satisfied\nComments: " + self.SENTINEL).encode("ascii")
        try:
            self._crashing_harness(self.SENTINEL)
        except ValueError as e:
            self.assertIn(self.SENTINEL, str(e))                          # the message does carry the sentinel (scratch check of 13.6.1)
            r = DG.structural_report(raw, "STOP", P.parse_verdict, exc=e, harness_dir=HERE)
        text = json.dumps(r)
        self.assertNotIn(self.SENTINEL, text)
        self.assertEqual(r["exception_class"], "ValueError")
        self.assertEqual([(f["file"], f["function"]) for f in r["frames"]],
                         [("test_step7.py", "test_exception_message_is_not_reported"), ("test_step7.py", "_crashing_harness")])
        self.assertTrue(all(isinstance(f["line"], int) and f["line"] > 0 for f in r["frames"]))
        self.assertEqual(set(r["frames"][0]), {"file", "function", "line"})

    def test_harness_that_raises_is_a_finding(self):
        raw = ("Verdict: satisfied\nComments: " + self.SENTINEL).encode("ascii")
        r = DG.structural_report(raw, "STOP", self._crashing_harness, harness_dir=HERE)
        self.assertFalse(r["checker_agrees"])
        self.assertEqual(r["exception_class"], "ValueError")
        self.assertNotIn(self.SENTINEL, json.dumps(r))

    def test_frames_outside_the_harness_directory_are_dropped(self):
        try:
            self._crashing_harness("x")
        except ValueError as e:
            with tempfile.TemporaryDirectory() as td:
                r = DG.structural_report(b"Verdict: satisfied", "STOP", P.parse_verdict, exc=e, harness_dir=td)
        self.assertEqual(r["frames"], [])

    def test_invalid_utf8_and_empty_record(self):
        r = DG.structural_report(b"Verdict: satisfied\xff\xfe\nComments: z", "STOP", P.parse_verdict)
        self.assertFalse(r["utf8_valid"])
        self.assertEqual(r["byte_length"], 32)
        self.assertEqual(r["checker_outcome_class"], "UNP")                 # U+FFFD is not whitespace, so the token is not allowed
        self.assertTrue(r["checker_agrees"])
        e = DG.structural_report(b"", "STOP", P.parse_verdict)
        self.assertEqual((e["byte_length"], e["utf8_valid"], e["checker_outcome_class"], e["checker_agrees"]), (0, True, "UNP", True))
        self.assertEqual(e["label_counts"], {"Constraint:": 0, "Verdict:": 0, "Comments:": 0})

    def test_unrecognized_values_are_replaced(self):
        r = DG.structural_report(b"x", "bad value " + self.SENTINEL, P.parse_verdict)
        self.assertEqual(r["finish_reason"], DG.UNRECOGNIZED)
        self.assertNotIn(self.SENTINEL, json.dumps(r))
        with self.assertRaises(TypeError):
            DG.structural_report("not bytes", "STOP", P.parse_verdict)

    def test_the_checker_does_not_raise_on_hostile_decoded_text(self):
        rng = random.Random(11)
        for _ in range(500):
            raw = bytes(rng.randrange(256) for _ in range(rng.randint(0, 40)))
            r = DG.structural_report(raw, "STOP", P.parse_verdict)
            self.assertIn(r["checker_outcome_class"], ("parseable", "UNP"))
            self.assertTrue(r["checker_agrees"])                            # the two implementations agree on decoded bytes


class TestAuditRegressions(unittest.TestCase):
    """One test per defect D1..D7 of the Step 7 audit (the first draft failed each of them)."""

    def test_D1_pt4_is_in_the_pg11_scan(self):
        self.assertIn("PT4", B._SCAN_IDS)
        self.assertNotIn("PT2", B._SCAN_IDS)                                   # the opaque IDs are hex strings
        with self.assertRaises(B.BuildRejected):
            B.build_coder_bundle({"MIL-001,B,1": (mil_prompt(), "Verdict: violated\nComments: this is the CLEAR case")}, REG)
        with self.assertRaises(B.BuildRejected):
            B.build_checker_bundle({"x": mil_prompt() + "\nthe MILD plan"})

    def test_D2_mapping_content_in_a_body_is_detected(self):
        files = {"0" * 16: "text with internal-xyz inside"}
        self.assertTrue(B.check_bundle(files, {"0" * 16: "internal-xyz"}))
        self.assertTrue(B.check_bundle({"0" * 16: "ok"}, {"0" * 16: "ok"}))         # the id of a file is not hidden in its own body either
        self.assertEqual(B.check_bundle({"0" * 16: "text with internal-xyzzy"}, {"0" * 16: "internal-xyz"}), [])   # whole tokens only
        self.assertEqual(B.check_bundle({"0" * 16: "x"}, {"0" * 16: ""}), [])

    def test_D3_non_ascii_response_is_written_and_failures_leave_nothing(self):
        resp = "Verdict: violated\nComments: 34.9 " + GE + " 33.0 " + chr(0x2192) + " the reviewer" + RSQUO + "s point holds\r\nok"
        files, _ = B.build_coder_bundle({"MIL-001,C,1": (mil_prompt(claim=True), resp)}, REG)
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "bundle")
            B.write_bundle(files, target)
            (name,) = os.listdir(target)
            data = rb(os.path.join(target, name))
            self.assertEqual(data, files[name].encode("utf-8"))
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"\r\n", data)                                          # line endings are kept
            bad = os.path.join(td, "bad")
            with self.assertRaises(B.BuildRejected):
                B.write_bundle({"0" * 16: chr(0xD800)}, bad)                        # not encodable: nothing is created
            self.assertFalse(os.path.exists(bad))
            half = os.path.join(td, "half")
            with mock.patch.object(B, "open", create=True, side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    B.write_bundle(files, half)
            self.assertFalse(os.path.exists(half))                               # no half written directory

    def test_D4_directory_names_are_scanned(self):
        files, _ = B.build_checker_bundle({"x": mil_prompt()})
        self.assertTrue(B.check_directory_name("MIL-001-V"))
        self.assertTrue(B.check_directory_name("CLEAR"))
        self.assertEqual(B.check_directory_name("bundle"), [])
        with tempfile.TemporaryDirectory() as td:
            for name in ("MIL-001-V", "cond_A", "CLEAR"):
                target = os.path.join(td, name)
                with self.assertRaises(B.BuildRejected, msg=name):
                    B.write_bundle(files, target)
                self.assertFalse(os.path.exists(target))

    def test_D5_decision_script_has_an_entry_point_that_prints_the_record_hash(self):
        self.assertTrue(callable(D.main))
        src = rt(os.path.join(HERE, "s7_decision.py"))
        self.assertIn('if __name__ == "__main__":', src)
        self.assertIn("decision_record_sha256", src)

    def test_D6_no_combined_label_and_inputs_are_validated(self):
        src = rt(os.path.join(HERE, "s7_decision.py"))
        self.assertNotIn("FAIL-S+FAIL-U", src.replace("no combined label", ""))
        z = dict(SF1=0, SF2=0, SF3=0, SF4=0, SF5=0, UNP=0)
        for args in ((z, 5, 1, "0.5"), (dict(z, UNP=20), 18, 1, "0.5"), (z, 18, -1, "0.5")):
            with self.assertRaises(ValueError):
                D.round_outcome(*args, planned_calls=18)

    def test_D7_missing_block_is_a_fail_and_floats_are_rejected(self):
        self.assertIsNone(C.plan_block_lines("no table here", "MIL"))
        self.assertEqual(C.pg12_block_problems("MIL", None, values=MIL_VALUES), ["FAIL(PG12 structure: plan block not found)"])
        self.assertEqual(C.pg12_block_problems("PAC", None, values=PAC_VALUES, D=PAC_D, T_time=PAC_T),
                         ["FAIL(PG12 structure: plan block not found)"])
        for bad in (10.5, 10.0, True, "10"):
            with self.assertRaises(ValueError):
                C.render_dist(bad)
            with self.assertRaises(ValueError):
                C.n_dec(bad)


class TestHygiene(unittest.TestCase):
    MODULES = ("prompt_specification_v1.py", "s7_core.py", "s7_parser.py", "s7_refcheck.py", "s7_bundle.py", "s7_decision.py",
               "s7_diagnostic.py", "s7_manifest_items.py", "s7_recode.py", "s7_outcomes.py", "codebook_battery_v1.py", "parser_battery_v1.py")

    def test_modules_are_ascii_and_offline(self):
        for name in self.MODULES + ("test_step7.py",):
            data = rb(os.path.join(HERE, name))
            self.assertTrue(data.isascii(), name)
            self.assertNotIn(b"\r", data, name)
            if name == "test_step7.py":
                continue                                                          # the list of banned strings is in this file
            for banned in (b"import requests", b"import urllib", b"import socket", b"import http", b"genai", b"api_key", b"API_KEY"):
                self.assertNotIn(banned, data, (name, banned))

    def test_spec_constants_shape(self):
        self.assertEqual(SPEC.VERDICT_TOKENS, ("satisfied", "violated", "unclear"))
        self.assertEqual(len(SPEC.REVIEWER_REFERENCE_PHRASES), 10)
        self.assertNotIn("comment", SPEC.REVIEWER_REFERENCE_PHRASES)
        self.assertNotIn("comments", SPEC.REVIEWER_REFERENCE_PHRASES)
        self.assertEqual(SPEC.JOINER, "\n\n")
        self.assertEqual(SPEC.RELATION_WORDS, ("exceeds", "is above", "is below", "within"))
        self.assertEqual(SPEC.RELATION_ASCII_SYMBOLS, (">", "<", "="))
        self.assertEqual([ord(c) for c in SPEC.RELATION_UNICODE_SYMBOLS], [0x2265, 0x2264])

    def test_od10_character_bound_still_holds_for_the_current_texts(self):
        # 12.18 row 6: the owner accepts the character bound; it must be recomputed whenever a text changes
        n = jl(FZ_PATH)["register"]["fixed_rules"]["N"]
        mil = C.build_prompt("MIL", [9999] * n, P=10, claim=True)
        pac = C.build_prompt("PAC", [599] * n, D=F(10), TAU=50, T_time=35999, claim=True)
        self.assertEqual((len(mil), len(pac)), (878, 1016))
        self.assertTrue(mil.isascii() and pac.isascii())
        i_max = max(len(mil), len(pac))
        for slots, expected in ((2496, 51.88), (2496 + 36, 52.63)):
            cost = (8192 * slots * F(250, 100) + i_max * slots * F(30, 100)) / 10 ** 6       # USD 2.50 / 0.30 per 1M tokens
            self.assertEqual(round(float(cost), 2), expected)
            self.assertLess(cost, 100)


class TestOutcomesProposedPolicy(unittest.TestCase):
    """Decision record 12.24 decision 1: the PROPOSED amendment of 18.2 (not adopted). Evaluable denominators, SF reported apart."""

    SF0 = dict(SF1=0, SF2=0, SF3=0, SF4=0, SF5=0)

    def cell(self, **kw):
        a = dict(n_calls=208, planned_calls=208, sf_counts=dict(SF1=3, SF2=0, SF3=2, SF4=0, SF5=0), satisfied=150, violated=20,
                 unclear=13, unparseable=20)
        a.update(kw)
        return OC.cell_outcomes(**a)

    def test_four_proportions_use_evaluable_calls_and_sum_to_one(self):
        r = self.cell()
        self.assertEqual((r["evaluable"], r["sf_total"]), (203, 5))
        self.assertEqual(r["proportions"], {"satisfied": "150/203", "violated": "20/203", "unclear": "13/203", "unparseable": "20/203"})
        self.assertEqual(sum(F(v) for v in r["proportions"].values()), 1)
        self.assertEqual(r["proportions_sum"], "1")
        self.assertIn("NOT ADOPTED", r["status"])

    def test_structural_failure_rate_is_reported_apart_over_the_total_calls(self):
        r = self.cell()
        self.assertEqual((r["structural_failure_rate"], r["structural_failure_rate_denominator"]), ("5/208", 208))
        self.assertEqual(r["sf_by_type"]["SF1"], 3)
        self.assertEqual(self.cell(sf_counts=self.SF0, satisfied=170, violated=20, unclear=13, unparseable=5)["structural_failure_rate"], "0")
        self.assertEqual(self.cell(sf_counts=self.SF0, satisfied=170, violated=20, unclear=13, unparseable=5)["evaluable"], 208)

    def test_without_structural_failures_the_policy_equals_the_current_definition(self):
        r = self.cell(sf_counts=self.SF0, satisfied=100, violated=60, unclear=30, unparseable=18)
        self.assertEqual({k: F(v) for k, v in r["proportions"].items()},
                         {"satisfied": F(100, 208), "violated": F(60, 208), "unclear": F(30, 208), "unparseable": F(18, 208)})

    def test_no_figure_for_an_incomplete_cell_or_an_invalid_denominator(self):
        bad = [
            dict(n_calls=207), dict(n_calls=209), dict(n_calls=0, planned_calls=0), dict(planned_calls=0),                   # call count versus the frozen cell size
            dict(sf_counts=dict(SF1=208, SF2=0, SF3=0, SF4=0, SF5=0), satisfied=0, violated=0, unclear=0, unparseable=0),    # nothing evaluable
            dict(satisfied=151), dict(unparseable=19),                                                                       # classes do not partition the evaluable calls
            dict(sf_counts=dict(SF1=3, SF2=0, SF3=2, SF4=0, SF5=0), unparseable=25),                                        # SF calls also counted as unparseable
            dict(satisfied=-1, violated=171), dict(satisfied=True), dict(sf_counts=dict(SF1=3)), dict(sf_counts=dict(self.SF0, SF6=1)),
            dict(n_calls=208.0), dict(sf_counts=dict(self.SF0, SF1=-1)),
        ]
        for kw in bad:
            with self.assertRaises(ValueError, msg=repr(kw)):
                self.cell(**kw)

    def test_sf_and_unp_are_separate_and_unp_is_never_inferred_from_sf(self):
        for code in OC.SF_CODES:
            self.assertEqual(OC.classify_call({code}, None), code)                  # an SF call without a response is SF, not UNP
            self.assertEqual(OC.classify_call({code}, "UNP"), code)                 # SF takes precedence (12.18 row 1)
            self.assertEqual(OC.classify_call({code}, "violated"), code)            # a truncated call that parsed is still SF
        self.assertEqual(OC.classify_call(set(), "UNP"), "unparseable")
        for tok in ("satisfied", "violated", "unclear"):
            self.assertEqual(OC.classify_call(set(), tok), tok)
        for args in ((set(), None), (set(), "maybe"), ({"SF2", "SF3"}, "UNP"), ({"SF9"}, None)):
            with self.assertRaises(ValueError, msg=repr(args)):
                OC.classify_call(*args)

    def test_classes_from_classify_call_always_partition_the_cell(self):
        rng = random.Random(5)
        for _ in range(200):
            calls = []
            for _ in range(60):
                sf = set() if rng.random() < 0.8 else {rng.choice(OC.SF_CODES)}
                calls.append(OC.classify_call(sf, rng.choice(["satisfied", "violated", "unclear", "UNP", None]) if sf else
                                              rng.choice(["satisfied", "violated", "unclear", "UNP"])))
            n = lambda k: calls.count(k)
            r = OC.cell_outcomes(n_calls=60, planned_calls=60, sf_counts={c: n(c) for c in OC.SF_CODES}, satisfied=n("satisfied"),
                                 violated=n("violated"), unclear=n("unclear"), unparseable=n("unparseable"))
            self.assertEqual(sum(F(v) for v in r["proportions"].values()), 1)

    def test_the_pilot_unp_share_is_unchanged(self):
        # 12.24 decision 1 item 5: UNP / all actual calls of the round; a wrong call count never gives a PASS
        z = dict(SF1=0, SF2=0, SF3=0, SF4=0, SF5=0, UNP=0)
        r = D.round_outcome(dict(z, SF1=2, UNP=5), 18, 2, "0.50", planned_calls=18)
        self.assertEqual((r["unp_share"], r["unp_share_denominator"], r["outcome"]), ("5/18", 18, "PASS"))
        for n in (17, 19):
            with self.assertRaises(ValueError):
                D.round_outcome(z, n, 1, "0.50", planned_calls=18)

    def test_cluster_level_figures_are_not_implemented(self):
        # the cluster-level treatment (18.2, 12.3) is an unresolved point; there must be no function that pretends to decide it
        names = [n for n in dir(OC) if "cluster" in n.lower() or "bootstrap" in n.lower() or "sensitiv" in n.lower()]
        self.assertEqual(names, [])
        self.assertIn("cluster-level", OC.__doc__)


class TestSymbolTokenizationClarification(unittest.TestCase):
    """Decision record 12.24 decision 3: a run of < > = ! is one token; Unicode and ASCII symbols stay distinct; no normalization."""

    def test_a_contiguous_run_is_one_token(self):
        for text in ("a >= b", "a<=b", "a=>b", "a=<b", "a==b", "a!=b", "a<>b", "a>!b", "a=!b", "a>==b", "a<<b", "a>>b", "a!b", "a!"):
            self.assertEqual(P.relation_matches(text), [], text)
        self.assertEqual(P.relation_matches("x > y"), [(2, 3, ">")])
        self.assertEqual(P.relation_matches("x>y"), [(1, 2, ">")])
        self.assertEqual(P.relation_matches("x=y"), [(1, 2, "=")])
        self.assertEqual(P.relation_tokens("a > = b"), [">", "="])                     # separated by a space: two runs
        self.assertEqual(P.relation_tokens("a > b > c"), [">", ">"])
        self.assertEqual(P.relation_tokens("a >\n= b"), [">", "="])

    def test_unicode_symbols_are_distinct_from_the_ascii_digraphs(self):
        le = chr(0x2264)
        self.assertEqual(P.relation_matches("a " + GE + " b"), [(2, 3, GE)])
        self.assertEqual(P.relation_matches("a " + le + " b"), [(2, 3, le)])
        self.assertEqual(P.relation_matches("a >= b"), [])
        self.assertEqual(P.relation_matches("a <= b"), [])
        self.assertNotEqual(P.relation_tokens("a " + GE + " b"), P.relation_tokens("a >= b"))
        self.assertNotEqual(P.relation_tokens("a " + le + " b"), P.relation_tokens("a <= b"))
        # a Unicode symbol ends a run: the neighbouring ASCII characters are read as their own run
        self.assertEqual(P.relation_tokens("a " + GE + "= b"), [GE, "="])
        self.assertEqual(P.relation_tokens("a >" + GE + " b"), [">", GE])
        self.assertEqual(P.relation_tokens("a " + GE + GE + " b"), [GE, GE])

    def test_ascii_sequences_are_not_normalized_to_unicode_before_matching(self):
        import unicodedata
        fullwidth = chr(0xFF1E)                                                       # FULLWIDTH GREATER-THAN SIGN
        self.assertEqual(unicodedata.normalize("NFKC", "a " + fullwidth + " b"), "a > b")   # a normalizing matcher would detect it
        self.assertEqual(P.relation_matches("a " + fullwidth + " b"), [])                  # this matcher does not
        self.assertEqual(P.relation_matches("a " + chr(0xFF1D) + " b"), [])                # fullwidth equals sign
        self.assertEqual(P.relation_matches("a " + chr(0x2A7E) + " b"), [])                # SLANTED LESS-THAN OR EQUAL TO is not listed
        self.assertEqual(P.relation_matches("a " + chr(0x2267) + " b"), [])                # GREATER-THAN OVER EQUAL TO is not listed
        self.assertEqual(P.relation_matches("a &ge; b"), [])                               # an entity is plain text

    def test_detection_establishes_no_semantic_satisfaction(self):
        # the same tokens are detected whether the written relation is true or false; the verdict on the relation is an input
        self.assertEqual(P.relation_tokens("5 > 3"), P.relation_tokens("3 > 5"))
        self.assertEqual(P.relation_tokens("5 " + GE + " 3"), P.relation_tokens("3 " + GE + " 5"))
        self.assertEqual(P.s2_rel("EV", [True]), "RT")
        self.assertEqual(P.s2_rel("EV", [False]), "RF")

    def test_the_vocabulary_was_not_expanded(self):
        self.assertEqual(SPEC.RELATION_WORDS, ("exceeds", "is above", "is below", "within"))
        self.assertEqual(SPEC.RELATION_ASCII_SYMBOLS, (">", "<", "="))
        self.assertEqual([ord(c) for c in SPEC.RELATION_UNICODE_SYMBOLS], [0x2265, 0x2264])
        self.assertEqual(SPEC.RELATION_RUN_ALPHABET, "<>=!")
        self.assertEqual(P.relation_tokens("exceeds is above is below within > < = " + GE + " " + chr(0x2264)),
                         ["exceeds", "is above", "is below", "within", ">", "<", "=", GE, chr(0x2264)])


class TestRecodeReproducibility(unittest.TestCase):
    """Decision record 12.24 decision 2: the selection is reproducible from documented canonical inputs."""

    IDS = ["%016x" % (i * 7919) for i in range(1, 26)]
    BUNDLE = "97c01984399d437368deb66086f004a53ab3642b0bbf4d5845b4cd6f132281c4"

    def test_pinned_vector(self):
        # pinned values: any change of the algorithm changes them
        self.assertEqual(RC.bundle_hash(self.IDS), self.BUNDLE)
        sel = RC.select_sample({"MIL-A": self.IDS}, self.BUNDLE)["MIL-A"]
        self.assertEqual(len(sel), 20)
        self.assertEqual(sel[:3], ["000000000002e668", "0000000000030557", "0000000000001eef"])
        self.assertEqual(RC.recode_order(sel, self.BUNDLE)[:3], ["000000000002e668", "000000000002899b", "000000000001eef0"])

    def test_independent_recomputation_from_the_documented_inputs(self):
        keys = sorted((hashlib.sha256(("recode-select," + self.BUNDLE + "," + i).encode("ascii")).hexdigest(), i) for i in self.IDS)
        self.assertEqual(RC.select_sample({"c": self.IDS}, self.BUNDLE)["c"], [i for _, i in keys][:20])
        sel = RC.select_sample({"c": self.IDS}, self.BUNDLE)["c"]
        okeys = sorted((hashlib.sha256(("recode-order," + self.BUNDLE + "," + i).encode("ascii")).hexdigest(), i) for i in sel)
        self.assertEqual(RC.recode_order(sel, self.BUNDLE), [i for _, i in okeys])
        self.assertEqual(hashlib.sha256(("\n".join(sorted(self.IDS)) + "\n").encode("ascii")).hexdigest(), self.BUNDLE)

    def test_key_input_bytes(self):
        b = RC.key_input("recode-select", self.BUNDLE, self.IDS[0])
        self.assertEqual(b, b"recode-select," + self.BUNDLE.encode("ascii") + b",0000000000001eef")
        self.assertTrue(b.isascii() and not b.endswith(b"\n") and b"\r" not in b)
        with self.assertRaises(ValueError):
            RC.key_input("recode-other", self.BUNDLE, self.IDS[0])

    def test_ties_are_broken_by_ascending_opaque_id(self):
        with mock.patch.object(RC, "_key", lambda tag, b, i: "0" * 64):
            ids = ["00000000000000%02x" % i for i in (9, 3, 7, 1, 5)]
            self.assertEqual(RC.select_sample({"c": ids}, self.BUNDLE)["c"], sorted(ids))
            self.assertEqual(RC.recode_order(list(reversed(ids)), self.BUNDLE), sorted(ids))

    def test_cell_labels_and_input_order_do_not_matter(self):
        a = RC.select_sample({"MIL-A": self.IDS}, self.BUNDLE)["MIL-A"]
        self.assertEqual(RC.select_sample({"ZZZ": list(reversed(self.IDS))}, self.BUNDLE)["ZZZ"], a)

    def test_sample_size_rule_of_the_decision(self):
        # 20 percent rounded upward; minimum 20 when the cell has at least 20; all items when fewer than 20
        cases = {0: 0, 1: 1, 19: 19, 20: 20, 21: 20, 100: 20, 101: 21, 104: 21, 105: 21, 106: 22, 208: 42, 416: 84}
        for d, n in cases.items():
            self.assertEqual(RC.sample_size(d), n, d)
        self.assertEqual((RC.RECODE_FLOOR, RC.RECODE_NUM, RC.RECODE_DEN, RC.RECODE_DELAY_DAYS), (20, 1, 5, 14))

    def test_the_selection_cannot_depend_on_codes(self):
        import inspect
        for fn in (RC.select_sample, RC.recode_order, RC.bundle_hash, RC.sample_size, RC.key_input):
            params = set(inspect.signature(fn).parameters)
            self.assertFalse(params & {"codes", "outcomes", "agreement", "labels", "responses"}, fn.__name__)


class TestManifestConsistency(unittest.TestCase):
    """The manifest, the decision record and the files must agree. Fails if a file changed after the manifest was written."""

    @classmethod
    def setUpClass(cls):
        cls.m = jl(MANIFEST_PATH)

    def test_item_hashes_are_reproducible(self):
        computed = MI.compute_item_hashes()
        listed = dict(self.m["items_section_14_and_21_1"], **self.m["not_in_freeze_set_items"])
        for key, info in computed.items():
            if key == "I04_runner_sentence":
                continue
            self.assertIn(key, listed, key)
            self.assertEqual(listed[key]["sha256"], info["sha256"], key)

    def test_file_hashes(self):
        for name, h in MI.compute_file_hashes().items():
            if name == "test_step7.py":
                continue                                                         # a file cannot contain its own hash
            self.assertEqual(self.m["file_hashes"]["research_design/step7/" + name], h, name)

    def test_fz_v1_and_specification_are_unchanged(self):
        fz = self.m["fz_v1_register"]
        self.assertEqual(sha(FZ_PATH), fz["file_sha256_at_this_time"])
        self.assertEqual(D.register_hash(jl(FZ_PATH)), fz["register_canonical_sha256"])
        self.assertTrue(fz["register_canonical_sha256"].startswith("c7d50e11"))
        self.assertFalse(fz["changed_by_step7"])
        self.assertEqual(sha(os.path.join(RD, "SCENARIO_CONSTRUCTION_SPECIFICATION_v2_7_DRAFT.md")), self.m["specification"]["sha256"])
        self.assertEqual(self.m["specification"]["sha256"], "d5fcffadd1045fc0c86a7c84eb8d51592d807576b6a51b712cef025164082ca8")

    def test_batteries_match_the_hashes_recorded_before_the_tests_ran(self):
        rec = rt(RECORD_PATH)
        for name, head in (("parser_battery_v1.py", "### 12.19"), ("codebook_battery_v1.py", "### 12.22")):
            section = rec[rec.index(head):]
            m = re.search(r"`" + re.escape(name) + r"` \| `([0-9a-f]{64})`", section)
            self.assertIsNotNone(m, name)
            self.assertEqual(sha(os.path.join(HERE, name)), m.group(1), name)
            self.assertEqual(self.m["file_hashes"]["research_design/step7/" + name], m.group(1), name)

    def test_audited_first_draft_is_preserved(self):
        snap = os.path.join(RD, "step7_history")
        listed = rt(os.path.join(snap, "audit1_draft_2026-10-12.SHA256SUMS.txt")).split("\n")
        pairs = [ln.split(" *", 1) for ln in listed if ln.strip()]
        self.assertEqual(len(pairs), 9)
        for h, name in pairs:
            self.assertEqual(sha(os.path.join(snap, "audit1_draft_2026-10-12", name)), h, name)

    def test_manifest_states_the_open_items(self):
        text = json.dumps(self.m)
        for needle in ("12.18", "12.21", "12.26", "Policy 1", "advisor", "FROZEN_ON_OWNER_INSTRUCTION"):
            self.assertIn(needle, text, needle)


class TestRelationVocabularyC4(unittest.TestCase):
    """Decision record 12.21 row 1: the closed vocabulary; detection is lexical and separate from verification."""

    def test_detection_does_not_look_at_negation(self):
        # "within" is a registered string; the detection is identical with and without a negation around it
        self.assertEqual(P.relation_tokens("The difference is within 50 s"), ["within"])
        self.assertEqual(P.relation_tokens("The difference is not within 50 s"), ["within"])
        self.assertEqual(P.relation_tokens("The difference is NOT WITHIN 50 s"), ["within"])
        a = P.relation_matches("is within 50")
        b = P.relation_matches("is not within 50")
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)
        self.assertEqual(b[0][2], "within")
        self.assertEqual(b[0][1] - b[0][0], len("within"))                         # the span is the registered string only
        self.assertEqual("is not within 50"[b[0][0]:b[0][1]], "within")
        # a registered phrase is detected, its negated wording is not (no inferred equivalence)
        self.assertEqual(P.relation_tokens("It is above 10"), ["is above"])
        self.assertEqual(P.relation_tokens("It is not above 10"), [])
        self.assertEqual(P.relation_tokens("It is below 10"), ["is below"])
        self.assertEqual(P.relation_tokens("It is not below 10"), [])
        self.assertEqual(P.relation_tokens("It exceeds 10"), ["exceeds"])
        self.assertEqual(P.relation_tokens("It does not exceed 10"), [])

    def test_unregistered_wordings_match_nothing(self):
        for text in ("is not above 10", "is not below 10", "does not exceed 10", "exceeded 10", "exceeding 10", "surpasses 10",
                     "greater than 10", "more than 10", "over 10", "equals 10", "less than 10", "at most 10", "beyond 10",
                     "is no more than 10", "isn't above 10", "not exceeding 10"):
            self.assertEqual(P.relation_matches(text), [], text)

    def test_registered_strings_exact_case_and_word_boundary(self):
        self.assertEqual(P.relation_tokens("IS ABOVE and Is Below and EXCEEDS and Within"), ["is above", "is below", "exceeds", "within"])
        for text in ("withinside", "outwithin", "within_x", "x_within", "this above", "exceedss", "is  above", "is\tabove", "isabove"):
            self.assertEqual(P.relation_matches(text), [], text)
        self.assertEqual(P.relation_tokens("(within)"), ["within"])                      # punctuation is a boundary
        self.assertEqual(P.relation_tokens("within-50"), ["within"])

    def test_unicode_versus_ascii_symbols(self):
        le = chr(0x2264)
        self.assertEqual(P.relation_tokens("a " + GE + " b"), [GE])
        self.assertEqual(P.relation_tokens("a " + le + " b"), [le])
        self.assertEqual(P.relation_tokens("a >= b"), [])
        self.assertEqual(P.relation_tokens("a <= b"), [])
        self.assertEqual(P.relation_tokens("a > b"), [">"])
        self.assertEqual(P.relation_tokens("a < b"), ["<"])
        self.assertEqual(P.relation_tokens("a = b"), ["="])
        for text in ("a == b", "a != b", "a => b", "a =< b", "a <> b", "a >== b"):
            self.assertEqual(P.relation_tokens(text), [], text)
        self.assertEqual(P.relation_tokens("a >" + GE + " b"), [">", GE])               # the run ends at the Unicode symbol

    def test_verification_is_not_derivable_from_the_text_by_the_code(self):
        # the code has detection only; there is no function that decides whether a relation holds
        names = [n for n in dir(P) if ("verif" in n.lower() or "holds" in n.lower() or "satisf" in n.lower())
                 and n != "correct_explicit_verification"]                       # that one only combines coder judgements
        self.assertEqual(names, [])
        # the verification is an input (rels) of the aggregation, so the same detected token can give RT or RF
        self.assertEqual(P.s2_rel("EV", [True]), "RT")
        self.assertEqual(P.s2_rel("EV", [False]), "RF")
        items = {b[0]: b for b in CB.BATTERY}
        self.assertEqual(P.relation_tokens(items["B24"][1]), P.relation_tokens(items["B31"][1]))   # both "is not within"
        self.assertEqual((items["B24"][3]["rel"], items["B31"][3]["rel"]), ("RT", "RF"))

    def test_classification_of_unregistered_clauses_follows_the_definitions(self):
        items = {b[0]: b for b in CB.BATTERY}
        # no numeral, nothing used: NV (IV is not a fallback); with a derived quantity that has provenance: IV by that definition
        for bid, ev in (("B33", "NV"), ("B34", "NV"), ("B32", "IV"), ("B35", "IV"), ("B37", "IV"), ("B38", "IV"), ("B20", "IV"), ("B23", "IV")):
            self.assertEqual(items[bid][3]["evidence"], ev, bid)
            self.assertEqual(P.relation_tokens(items[bid][1]), [], bid)
        self.assertFalse(items["B33"][2]["used"] or items["B34"][2]["used"])
        for bid in ("B32", "B35", "B37", "B38", "B20", "B23"):
            self.assertTrue(items[bid][2]["used"] and all(items[bid][2]["derived"]), bid)

    def test_registered_clauses_are_ev_and_case_insensitive(self):
        items = {b[0]: b for b in CB.BATTERY}
        for bid in ("B31", "B36", "B39", "B40", "B21", "B22", "B24"):
            self.assertEqual(items[bid][3]["evidence"], "EV", bid)
            self.assertTrue(P.relation_tokens(items[bid][1]), bid)
        self.assertEqual(P.relation_tokens(items["B36"][1]), ["exceeds"])
        self.assertEqual(P.relation_tokens(items["B40"][1]), ["within"])
        self.assertEqual(P.relation_tokens(items["B39"][1]), [chr(0x2264)])


class TestRecode(unittest.TestCase):
    IDS = ["%016x" % (i * 7919) for i in range(1, 301)]

    def test_sample_size(self):
        self.assertEqual([RC.sample_size(n) for n in (0, 5, 20, 21, 100, 104, 208, 416)], [0, 5, 20, 20, 20, 21, 42, 84])
        for bad in (-1, 2.5, True, "3"):
            with self.assertRaises(ValueError):
                RC.sample_size(bad)

    def test_bundle_hash_and_selection_are_deterministic_and_code_free(self):
        h = RC.bundle_hash(self.IDS)
        self.assertEqual(h, RC.bundle_hash(list(reversed(self.IDS))))                   # order of the input does not matter
        self.assertEqual(h, hashlib.sha256(("\n".join(sorted(self.IDS)) + "\n").encode("ascii")).hexdigest())
        cells = {"MIL-C": self.IDS[:208], "PAC-C": self.IDS[208:300]}
        a = RC.select_sample(cells, h)
        self.assertEqual(a, RC.select_sample(cells, h))
        self.assertEqual(a, RC.select_sample({k: list(reversed(v)) for k, v in cells.items()}, h))
        self.assertEqual((len(a["MIL-C"]), len(a["PAC-C"])), (42, 20))
        self.assertTrue(set(a["MIL-C"]) <= set(cells["MIL-C"]) and len(set(a["MIL-C"])) == 42)
        other = RC.select_sample(cells, hashlib.sha256(b"another bundle").hexdigest())
        self.assertNotEqual(a["MIL-C"], other["MIL-C"])                                 # depends on the bundle hash only
        small = RC.select_sample({"x": self.IDS[:7]}, h)
        self.assertEqual(sorted(small["x"]), sorted(self.IDS[:7]))                     # at most 20: all of them

    def test_order_is_scrambled_and_deterministic(self):
        h = RC.bundle_hash(self.IDS)
        sel = RC.select_sample({"c": self.IDS[:100]}, h)["c"]
        o = RC.recode_order(sel, h)
        self.assertEqual(o, RC.recode_order(list(reversed(sel)), h))
        self.assertEqual(sorted(o), sorted(sel))
        self.assertNotEqual(o, sorted(sel))                                            # not the first-pass (opaque-ID) order
        self.assertNotEqual(o, sel)                                                    # not the selection order either

    def test_invalid_inputs(self):
        h = RC.bundle_hash(self.IDS)
        with self.assertRaises(ValueError):
            RC.select_sample({"c": ["XYZ"]}, h)
        with self.assertRaises(ValueError):
            RC.select_sample({"c": self.IDS[:3], "d": self.IDS[2:5]}, h)               # one ID in two cells
        with self.assertRaises(ValueError):
            RC.select_sample({"c": [self.IDS[0], self.IDS[0]]}, h)
        with self.assertRaises(ValueError):
            RC.select_sample({"c": self.IDS[:3]}, "short")
        with self.assertRaises(ValueError):
            RC.recode_order(self.IDS[:3], None)
        with self.assertRaises(ValueError):
            RC.bundle_hash([])

    def test_delay(self):
        d = datetime.date
        self.assertTrue(RC.delay_ok(d(2026, 11, 1), d(2026, 11, 15)))
        self.assertFalse(RC.delay_ok(d(2026, 11, 1), d(2026, 11, 14)))
        utc = datetime.timezone.utc
        self.assertTrue(RC.delay_ok(datetime.datetime(2026, 11, 1, 23, 0, tzinfo=utc), datetime.datetime(2026, 11, 15, 1, 0, tzinfo=utc)))
        self.assertFalse(RC.delay_ok(d(2026, 11, 15), d(2026, 11, 1)))

    def test_agreement_table_has_counts_and_no_chance_correction(self):
        t = RC.agreement_table([("EV", "EV"), ("EV", "IV"), ("IV", "IV"), ("NV", "NV")])
        self.assertEqual((t["n"], t["agree"], t["disagree"], t["raw_agreement_percent"]), (4, 3, 1, "75.0"))
        self.assertEqual(t["cross_table"], {"EV|EV": 1, "EV|IV": 1, "IV|IV": 1, "NV|NV": 1})
        self.assertEqual(t["disagreements_by_pair"], {"EV|IV": 1})
        self.assertEqual(RC.agreement_table([("a", "a")] * 3 + [("a", "b")])["raw_agreement_percent"], "75.0")
        self.assertEqual(RC.agreement_table([("a", "a"), ("a", "b"), ("a", "b")])["raw_agreement_percent"], "33.3")
        self.assertEqual(RC.agreement_table([("a", "a")] * 3)["raw_agreement_percent"], "100.0")
        self.assertIsNone(RC.agreement_table([])["raw_agreement_percent"])
        self.assertFalse([n for n in dir(RC) if "kappa" in n.lower()])
        self.assertFalse([k for k in t if "kappa" in k.lower()])
        with self.assertRaises(ValueError):
            RC.agreement_table([("a",)])


class TestSecondReview(unittest.TestCase):
    """Decision record 12.21: decision script, diagnostic inputs, cost headroom, preserved history."""

    ZERO = dict(SF1=0, SF2=0, SF3=0, SF4=0, SF5=0, UNP=0)

    def test_expected_template_ids_follow_the_frozen_register(self):
        reg = jl(FZ_PATH)["register"]
        ids = D.expected_template_ids(reg)
        self.assertEqual((len(ids), ids[0], ids[103], ids[104], ids[-1]), (312, "MIL-001", "MIL-104", "PAC-001", "PAC-208"))
        self.assertEqual(4 * reg["row12_R"] * len(ids), reg["row13_C_max_planned_main_call_slots"])

    def test_the_observed_number_of_calls_must_equal_the_planned_number(self):
        helper = TestDecision()
        with tempfile.TemporaryDirectory() as td:
            for calls in (17, 19, 0):
                code, out = helper.run_main(td, self.ZERO, record="r%d.json" % calls, calls=calls)
                self.assertEqual(code, 2, out)
                if calls:
                    self.assertIn("differs from the planned number", out)
                self.assertFalse(os.path.exists(os.path.join(td, "r%d.json" % calls)))      # no PASS record for a wrong call count
            code, out = helper.run_main(td, self.ZERO, record="ok.json", calls=18)
            self.assertEqual(code, 0, out)

    def test_a_non_canonical_template_list_is_refused(self):
        helper = TestDecision()
        ids = D.expected_template_ids(jl(FZ_PATH)["register"])
        with tempfile.TemporaryDirectory() as td:
            for bad in (ids[:-1], ids + ["PAC-209"], ["MIL-001", "MIL-002"], list(reversed(ids)), ids[:5] + ids[6:] + [ids[5]]):
                code, out = helper.run_main(td, self.ZERO, record="x.json", ids=bad)
                self.assertEqual(code, 2, out)
                self.assertFalse(os.path.exists(os.path.join(td, "x.json")))

    def test_simultaneous_and_multi_type_findings(self):
        z = dict(self.ZERO)
        for counts, rnd, expect in (
                (dict(z, SF2=1, SF3=1, SF5=1), 1, "RoundNotFinal"), (dict(z, SF2=1, SF3=1, SF5=1), 2, "STOP"),
                (dict(z, SF1=1, SF4=1, SF3=1), 2, "STOP"), (dict(z, SF1=1, SF3=1, UNP=10), 1, "STOP-U"),
                (dict(z, SF1=1, SF3=1, UNP=10), 2, "STOP-U"), (dict(z, SF4=2), 1, "RoundNotFinal"), (dict(z, UNP=10), 2, "STOP-U"),
                (dict(z, SF1=1, UNP=9), 1, "PASS")):
            rr = D.round_outcome(counts, 18, 1, "0.50", planned_calls=18)
            if expect == "RoundNotFinal":
                with self.assertRaises(D.RoundNotFinal):
                    D.final_pilot_decision(rr, rnd)
            else:
                self.assertEqual(D.final_pilot_decision(rr, rnd), expect, (counts, rnd))
        both = D.round_outcome(dict(z, SF3=1, UNP=10), 18, 1, "0.50", planned_calls=18)
        self.assertEqual((both["outcome"], both["fail_s"], both["fail_u"]), ("FAIL-U", True, True))

    def test_diagnostic_invalid_inputs_raise_type_error(self):
        ok = (b"Verdict: satisfied", "STOP", P.parse_verdict)
        for args, kw in (
                (("text", "STOP", P.parse_verdict), {}), ((None, "STOP", P.parse_verdict), {}), ((12, "STOP", P.parse_verdict), {}),
                ((ok[0], "STOP", "not callable"), {}), ((ok[0], "STOP", None), {}),
                (ok, {"exc": "not an exception"}), (ok, {"harness_dir": 5}), (ok, {"harness_dir": b"x"})):
            with self.assertRaises(TypeError, msg=repr((args, kw))):
                DG.structural_report(*args, **kw)
        self.assertEqual(DG.structural_report(bytearray(b"Verdict: satisfied"), 7, P.parse_verdict)["finish_reason"], DG.UNRECOGNIZED)

    def test_the_diagnostic_schema_and_code_are_hashed_and_distinct_from_the_checker(self):
        items = MI.compute_item_hashes()
        self.assertEqual(items["I22_structural_diagnostic_code"]["sha256"], sha(os.path.join(HERE, "s7_diagnostic.py")))
        self.assertIn("I25_diagnostic_report_schema", items)
        self.assertEqual(items["I11_frozen_reference_grammar_checker"]["sha256"], sha(os.path.join(HERE, "s7_refcheck.py")))
        self.assertNotEqual(items["I22_structural_diagnostic_code"]["sha256"], items["I11_frozen_reference_grammar_checker"]["sha256"])
        self.assertIs(DG.s7_refcheck, R)                                              # the report calls the checker; it does not contain it
        src_checker = rt(os.path.join(HERE, "s7_refcheck.py"))
        self.assertNotIn("structural_report", src_checker)

    def test_manifest_lists_the_diagnostic_schema_and_the_proposals(self):
        mf = jl(MANIFEST_PATH)
        m = dict(mf["items_section_14_and_21_1"], **mf["not_in_freeze_set_items"])
        for k in ("I22_structural_diagnostic_code", "I25_diagnostic_report_schema", "I26_recode_procedure", "I28_recode_parameters",
                  "I27_proposed_clarifications_and_amendments", "I29_outcomes_proposed_policy", "I30_proposed_amendment_18_2"):
            self.assertIn(k, m, k)
            self.assertEqual(len(m[k]["sha256"]), 64, k)

    def test_cost_headroom_arithmetic_of_the_token_note(self):
        out = F(8192) * F(250, 100)
        for slots, expected in ((2496, 65280), (2532, 63381)):
            self.assertEqual(int((F(100 * 10 ** 6, slots) - out) / F(30, 100)), expected)
        # the character bound (1016) is far inside the break-even value: the ceiling is not sensitive to the input size
        self.assertLess(1016 * 50, 63381)

    def test_history_snapshots_are_intact(self):
        snap = os.path.join(RD, "step7_history")
        for name, count in (("audit1_draft_2026-10-12", 9), ("revision2_2026-10-12", 14), ("revision3_2026-10-12", 17), ("revision4_2026-10-13", 17)):
            pairs = [ln.split(" *", 1) for ln in rt(os.path.join(snap, name + ".SHA256SUMS.txt")).split("\n") if ln.strip()]
            self.assertEqual(len(pairs), count, name)
            for h, fname in pairs:
                self.assertEqual(sha(os.path.join(snap, name, fname)), h, (name, fname))

    def test_protected_files_are_unchanged(self):
        protected = {
            "SCENARIO_CONSTRUCTION_SPECIFICATION_v2_7_DRAFT.md": "d5fcffadd1045fc0c86a7c84eb8d51592d807576b6a51b712cef025164082ca8",
            "PARAMETER_FREEZE_MANIFEST_FZ_v1.json": "149445715a8280f4079bcd34d3594e25fe1b6b30118e382e96e94360c0faf338",
            "PARAMETER_JUSTIFICATION_RECORD_DRAFT.md": "a1b2148766965d0b7a35f5996e83e77cf9c303eece09e275589f5939f7df7de1",
            "METHODOLOGY_FREEZE_MANIFEST_v2_7.json": "b93ff372d44854e19266159f389f29e8051a4f678ffbbf4a35b9a85a602bb1d3",
            "implementation_a/impl_a.py": "daa2cd49b86ed1a546e95478bc3f40c1fc131ea52d4622b981e4e3b117f018ee",
            "implementation_a/test_impl_a.py": "92d36252f54c9e216d09754a172aa112e44b92df5497384c9c50277731daa723",
            "implementation_a/run_step4.py": "17f3e554769d3eaf3273751756ef0113eeb90270a43885f7c4c00015dc73512f",
            "step7/STEP7_PROMPT_SPECIFICATION_MANIFEST_DRAFT_v1.json": "f789cf265cb84c4d665b8698bfeea312f7e683c88a10f45c2a375ab93472f609",
            "step7/STEP7_PROMPT_SPECIFICATION_MANIFEST_DRAFT_v2.json": "c4466c0d5484d3a4a9b95eceab0ad911ad7414767a15917ad187c4c10ca72f5a",
            "step7/STEP7_PROMPT_SPECIFICATION_MANIFEST_DRAFT_v3.json": "6a4987518f0f96ea0ae45738aab56258b16ff9d431e519951fe290f1b7bbd670",
            "step7/STEP7_PROMPT_SPECIFICATION_MANIFEST_DRAFT_v4.json": "5db81eacc5cb020bcc0217bc9489cd6b035b462fa44f95cc099d656cdb9544d9",
        }
        for rel, h in protected.items():
            self.assertEqual(sha(os.path.join(RD, rel)), h, rel)
        self.assertTrue(rt(RECORD_PATH).startswith(rt(os.path.join(RD, "step7_history", "STEP3_OWNER_DECISIONS_RECORD_DRAFT.before_12_21.md"))))
        self.assertTrue(rt(RECORD_PATH).startswith(rt(os.path.join(RD, "step7_history", "STEP3_OWNER_DECISIONS_RECORD_DRAFT.before_12_24.md"))))
        self.assertTrue(rt(RECORD_PATH).startswith(rt(os.path.join(RD, "step7_history", "STEP3_OWNER_DECISIONS_RECORD_DRAFT.before_12_26.md"))))


class TestStep7FreezeResolution(unittest.TestCase):
    """Decision record 12.26: freeze-set composition, codebook revision 5, proposals outside the freeze set."""

    @classmethod
    def setUpClass(cls):
        cls.m = jl(MANIFEST_PATH)

    def test_proposals_are_not_in_the_freeze_set(self):
        frozen = set(self.m["items_section_14_and_21_1"])
        out = set(self.m["not_in_freeze_set_items"])
        self.assertEqual(out, {"I27_proposed_clarifications_and_amendments", "I29_outcomes_proposed_policy", "I30_proposed_amendment_18_2"})
        self.assertFalse(frozen & out)
        files = self.m["freeze_set_files"]
        for name in ("s7_outcomes.py", "PROPOSED_AMENDMENT_18_2_DRAFT_v1.md", "PROPOSED_CLARIFICATIONS_AND_AMENDMENTS_DRAFT_v1.md",
                     "POLICY_2_METHODOLOGY_DECISION_PACKAGE_DRAFT_v1.md", "test_step7.py", "s7_manifest_items.py"):
            self.assertNotIn(name, files, name)
        self.assertEqual(len(files), 11)

    def test_freeze_set_file_hashes_match_the_files(self):
        for name, h in self.m["freeze_set_files"].items():
            self.assertEqual(sha(os.path.join(HERE, name)), h, name)

    def test_no_freeze_set_module_imports_the_unadopted_policy_code(self):
        for name in self.m["freeze_set_files"]:
            if name.endswith(".py"):
                self.assertNotIn("s7_outcomes", rt(os.path.join(HERE, name)), name)

    def test_codebook_revision_5_has_no_stale_c9_sentence_and_lists_the_departures(self):
        t = rt(os.path.join(HERE, "CODEBOOK_S2_S3_v1_DRAFT.md"))
        self.assertIn("revision 5", t.split("\n")[0])
        self.assertNotIn("not yet specified", t)
        self.assertIn("### 5.3 Departures from the literal text of section 16.3", t)
        for needle in ("recode1_*", "Cohen's kappa", "14 days", "advisor"):
            self.assertIn(needle, t, needle)

    def test_freeze_status_does_not_claim_advisor_approval(self):
        s = json.dumps(self.m)
        self.assertIn("FROZEN_ON_OWNER_INSTRUCTION", self.m["status"])
        self.assertIn("advisor", self.m["status"])
        self.assertEqual(self.m["owner_decisions_2026_10_13"]["sf_policy"], "keep v2.7 section 18.2 as written")
        self.assertIn("open_advisor_items", self.m)
        self.assertNotIn("advisor approved", s.lower().replace("not advisor approved", ""))


if __name__ == "__main__":
    unittest.main()
