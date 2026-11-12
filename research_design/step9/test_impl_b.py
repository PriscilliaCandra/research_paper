"""Mutation tests of Implementation B: each injected defect in a COPY of the published build must be reported. Synthetic mutations
only; the copy lives in a temporary folder outside the repository. Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step9 -p test_impl_b.py"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import impl_b as B  # noqa: E402

BUILD = os.path.join(RD, "step8_output", "step8_build_7a6bbe465c66")


def rt(p):
    with open(p, "rb") as f:
        return f.read().decode("utf-8")


def wt(p, s):
    with open(p, "wb") as f:
        f.write(s.encode("utf-8"))


class TestImplementationB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="implb_")
        cls.root = os.path.join(cls.tmp, "b")
        shutil.copytree(BUILD, cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def mutated(self, rel, fn):
        p = os.path.join(self.root, *rel.split("/"))
        orig = rt(p)
        wt(p, fn(orig))
        try:
            return B.verify(self.root)["failures"]
        finally:
            wt(p, orig)

    def test_clean_build_agrees(self):
        r = B.verify(self.root)
        self.assertEqual(r["failures"], [])
        self.assertEqual(r["agree"]["main"], {"templates": 312, "checked_templates": 312})
        self.assertEqual(r["agree"]["pilot"], {"templates": 24, "checked_templates": 24})

    def test_flipped_state_is_adj1(self):
        f = self.mutated("pilot/prompts/MIL-001-B.txt", lambda s: rt(os.path.join(self.root, "pilot", "prompts", "MIL-001-A.txt")))
        self.assertTrue(any("ADJ-1" in x or "PG1" in x for x in f))

    def test_two_values_for_the_cap_is_a_strict_parse_failure(self):
        f = self.mutated("pilot/prompts/MIL-001-A.txt", lambda s: s.replace("10 percent", "11 percent", 1))
        self.assertTrue(any("strict parse" in x for x in f))

    def test_two_values_for_the_distance(self):
        f = self.mutated("pilot/prompts/PAC-001-A.txt", lambda s: s.replace("Distance: 10 km", "Distance: 11 km", 1))
        self.assertTrue(any("two values for D_dist" in x or "strict parse" in x for x in f))

    def test_trailing_space_and_forbidden_word(self):
        f = self.mutated("pilot/prompts/MIL-002-A.txt", lambda s: s + " ")
        self.assertTrue(any("PG5" in x for x in f))
        f = self.mutated("pilot/prompts/MIL-002-A.txt", lambda s: s.replace("Does the plan", "MILD Does the plan", 1))
        self.assertTrue(any("PG10" in x for x in f))

    def test_missing_week_row(self):
        f = self.mutated("pilot/prompts/MIL-003-A.txt", lambda s: s.replace("| 3 | ", "| 4 | ", 1))
        self.assertTrue(any("strict parse" in x or "PG12" in x for x in f))

    def test_wrong_class_in_registry(self):
        f = self.mutated("pilot/registry.json", lambda s: s.replace('"class":"MILD"', '"class":"CLEAR"', 1))
        self.assertTrue(any("A6b" in x or "A7" in x for x in f))

    def test_renamed_checker_file_breaks_the_opaque_id_rule(self):
        d = os.path.join(self.root, "pilot", "checker_bundle")
        name = sorted(os.listdir(d))[0]
        new = "0" * 16 if name != "0" * 16 else "1" * 16
        os.rename(os.path.join(d, name), os.path.join(d, new))
        try:
            f = B.verify(self.root)["failures"]
        finally:
            os.rename(os.path.join(d, new), os.path.join(d, name))
        self.assertTrue(any("PG11a opaque ID" in x for x in f))

    def test_claim_text_in_a_checker_file_is_caught(self):
        d = os.path.join(self.root, "pilot", "checker_bundle")
        name = sorted(os.listdir(d))[0]
        p = os.path.join(d, name)
        orig = rt(p)
        wt(p, orig + "\n\nA reviewer comment")
        try:
            f = B.verify(self.root)["failures"]
        finally:
            wt(p, orig)
        self.assertTrue(any("PG11a" in x for x in f))

    def test_module_imports_no_project_code(self):
        src = rt(os.path.join(HERE, "impl_b.py"))
        body = src.split('"""', 2)[2]
        for bad in ("import impl_a", "import s7", "import s8", "prompt_specification", "from s7", "from s8", "import numpy"):
            self.assertNotIn(bad, body, bad)


if __name__ == "__main__":
    unittest.main()
