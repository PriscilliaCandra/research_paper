"""Tests of the FZ-version selection of s8_build.py (decision record 12.64, D2). Reads the published files; publishes nothing.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step8 -p test_step8_fz_v2.py"""
import glob
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s8_build as B  # noqa: E402

V1_BUILD = os.path.join(RD, "step8_output", "step8_build_7a6bbe465c66")


def jl(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestVersionSelection(unittest.TestCase):
    def test_default_is_fz_v1_and_unchanged(self):
        self.assertEqual(B.ACTIVE["label"], "FZ-v1")
        fz, fm, h = B.load_inputs()
        self.assertEqual(fz["register_canonical_sha256"], B.FZ_REG_SHA)
        self.assertEqual(fz["register"]["row17_model_and_settings"]["provider"], "Gemini")

    def test_fz_v2_inputs_load_and_differ_only_in_item_17(self):
        with mock.patch.object(B, "ACTIVE", B.FZ_VERSIONS["v2"]):
            fz2, fm2, h2 = B.load_inputs()
        fz1, _, _ = B.load_inputs()
        self.assertEqual(fz2["register"]["row17_model_and_settings"]["provider"], "Ollama")
        self.assertEqual([k for k in fz1["register"] if fz1["register"][k] != fz2["register"][k]], ["row17_model_and_settings"])
        self.assertEqual(fm2["fz_register"]["register_canonical_sha256"], fz2["register_canonical_sha256"])
        self.assertEqual(fm2["freeze_set_files"], jl(os.path.join(RD, "step7", B.FREEZE_MANIFEST))["freeze_set_files"])

    def test_wrong_hash_wrong_settings_or_wrong_step7_manifest_reject_the_inputs(self):
        v2 = B.FZ_VERSIONS["v2"]
        for name, bad in {"register hash": dict(v2, register_sha="0" * 64), "settings": dict(v2, settings=("llama3.2:3b", "not applicable", 8192, 0, 180, 2, 1))}.items():
            with self.subTest(name), mock.patch.object(B, "ACTIVE", bad), self.assertRaises(B.Rejected):
                B.load_inputs()
        with mock.patch.object(B, "ACTIVE", dict(v2, freeze_manifest=B.FREEZE_MANIFEST)), self.assertRaises((B.Rejected, KeyError)):
            B.load_inputs()                                                  # the v1 Step 7 manifest does not cite the FZ-v2 register

    def test_the_cli_accepts_only_known_versions(self):
        with self.assertRaises(SystemExit):
            B.main(["--out", os.path.join(RD, "step8_output_unused"), "--fz-version", "v3"])


class TestPublishedFZv2Build(unittest.TestCase):
    """Evidence check on the already published FZ-v2 build against the published FZ-v1 build (R-7 sequence, D2: comparison of the prompt hashes)."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        dirs = glob.glob(os.path.join(RD, "step8_output_fz_v2", "step8_build_*"))
        if len(dirs) != 1 or not os.path.isdir(V1_BUILD):
            raise unittest.SkipTest("the FZ-v2 build or the FZ-v1 build is not present")
        cls.m1 = jl(os.path.join(V1_BUILD, "manifest.json"))
        cls.m2 = jl(os.path.join(dirs[0], "manifest.json"))
        cls.i1 = {e["path"]: e["sha256"] for e in cls.m1["inventory"]}
        cls.i2 = {e["path"]: e["sha256"] for e in cls.m2["inventory"]}

    def test_same_files_and_only_four_differ(self):
        self.assertEqual(set(self.i1), set(self.i2))
        self.assertEqual(len(self.i1), 2026)
        self.assertEqual(sorted(p for p in self.i1 if self.i1[p] != self.i2[p]),
                         ["main/gate_report.json", "main/registry.json", "pilot/gate_report.json", "pilot/registry.json"])

    def test_all_prompts_checker_files_tables_and_call_list_are_identical(self):
        for part in ("/prompts/", "checker_bundle", "prompt_table.json", "pilot_calls.txt", "checker_mapping"):
            a = {p: h for p, h in self.i1.items() if part in p}
            self.assertTrue(a, part)
            self.assertEqual(a, {p: self.i2[p] for p in a}, part)
        self.assertEqual(sum("/prompts/" in p for p in self.i1), 1344)

    def test_the_manifest_cites_fz_v2_and_the_new_step7_manifest(self):
        self.assertEqual(self.m2["fz_v1_register_sha256"], B.FZ_VERSIONS["v2"]["register_sha"])
        self.assertNotEqual(self.m2["fz_v1_register_sha256"], self.m1["fz_v1_register_sha256"])
        self.assertEqual(self.m2["specification_sha256"], self.m1["specification_sha256"])


if __name__ == "__main__":
    unittest.main()
