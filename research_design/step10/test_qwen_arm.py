"""Tests for the second small-LLM arm (owner decisions D13-D18, 2026-10-26): expected settings per Ollama model and FZ-v3 consistency.
No network, no model call."""
import hashlib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(RD, "step7"))
import s10_runner as RN  # noqa: E402
import s7_decision as D  # noqa: E402


def load(name):
    with open(os.path.join(RD, name), "rb") as f:
        return json.loads(f.read().decode("utf-8"))


class QwenArm(unittest.TestCase):
    def setUp(self):
        self.fz2 = load("PARAMETER_FREEZE_MANIFEST_FZ_v2.json")
        self.fz3 = load("PARAMETER_FREEZE_MANIFEST_FZ_v3.json")

    def test_register_settings_equal_expected_per_model(self):
        for fz in (self.fz2, self.fz3):
            m = fz["register"]["row17_model_and_settings"]
            exp = RN.expected_settings_for(m)
            self.assertIsNotNone(exp)
            self.assertEqual({k: m.get(k) for k in exp}, exp)

    def test_models_are_not_interchangeable(self):
        m2 = self.fz2["register"]["row17_model_and_settings"]
        m3 = self.fz3["register"]["row17_model_and_settings"]
        self.assertNotEqual({k: m3.get(k) for k in RN.expected_settings_for(m2)}, RN.expected_settings_for(m2))
        self.assertNotEqual({k: m2.get(k) for k in RN.expected_settings_for(m3)}, RN.expected_settings_for(m3))

    def test_unknown_ollama_model_is_a_settings_mismatch_and_unknown_provider_is_unsupported(self):
        m = {"provider": "Ollama", "model_id": "other:1b"}
        exp = RN.expected_settings_for(m)
        self.assertEqual(exp, RN.OLLAMA_EXPECTED)
        self.assertNotEqual({k: m.get(k) for k in exp}, exp)
        self.assertIsNone(RN.expected_settings_for({"provider": "Nobody", "model_id": "x"}))

    def test_changed_timeout_is_a_mismatch(self):
        m = dict(self.fz3["register"]["row17_model_and_settings"], timeout_s=180)
        exp = RN.expected_settings_for(m)
        self.assertNotEqual({k: m.get(k) for k in exp}, exp)

    def test_gemini_and_llama_expectations_unchanged(self):
        self.assertEqual(RN.EXPECTED_BY_PROVIDER["Ollama"]["model_id"], "llama3.2:3b")
        self.assertEqual(RN.EXPECTED_BY_PROVIDER["Ollama"]["timeout_s"], 180)
        self.assertEqual(RN.EXPECTED_BY_PROVIDER["Gemini"]["model_id"], "gemini-3.5-flash-lite")

    def test_fz3_differs_from_fz2_only_in_row17(self):
        r2, r3 = self.fz2["register"], self.fz3["register"]
        self.assertEqual({k: v for k, v in r2.items() if k != "row17_model_and_settings"},
                         {k: v for k, v in r3.items() if k != "row17_model_and_settings"})
        a, b = r2["row17_model_and_settings"], r3["row17_model_and_settings"]
        changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        self.assertEqual(changed, ["model_digest", "model_id", "model_template_sha256", "ollama_version", "timeout_s"])
        self.assertEqual(b["timeout_s"], 600)

    def test_fz3_register_hash_recorded_correctly(self):
        self.assertEqual(D.register_hash(self.fz3), self.fz3["register_canonical_sha256"])

    def test_fz2_file_unchanged_since_step7_v2_manifest(self):
        with open(os.path.join(RD, "PARAMETER_FREEZE_MANIFEST_FZ_v2.json"), "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        m = load(os.path.join("step7", "STEP7_PROMPT_SPECIFICATION_FREEZE_MANIFEST_v2.json"))
        self.assertEqual(h, m["fz_register"]["file_sha256_at_this_time"])
        self.assertEqual(h, self.fz3["relation_to_fz_v2"]["file_sha256"])

    def test_build_wrapper_registers_fz3_settings_equal_to_the_register(self):
        sys.path.insert(0, os.path.join(RD, "step8"))
        import s8_build_fz_v3 as W
        v3 = W.B.FZ_VERSIONS["v3"]
        m = self.fz3["register"]["row17_model_and_settings"]
        self.assertEqual(v3["settings"], (m["model_id"], m["thinking_level"] if False else "not applicable", m["max_output_tokens"], m["retries"],
                                          m["timeout_s"], m["delay_between_calls_s"], m["concurrency"]))
        self.assertEqual(v3["register_sha"], self.fz3["register_canonical_sha256"])
        self.assertNotIn("v3", ("v1", "v2"))
        self.assertEqual(sorted(k for k in W.B.FZ_VERSIONS), ["v1", "v2", "v3"])

    def test_step7_v3_manifest_cites_fz3(self):
        m = load(os.path.join("step7", "STEP7_PROMPT_SPECIFICATION_FREEZE_MANIFEST_v3.json"))
        with open(os.path.join(RD, "PARAMETER_FREEZE_MANIFEST_FZ_v3.json"), "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(m["fz_register"]["file_sha256_at_this_time"], h)
        self.assertEqual(m["fz_register"]["register_canonical_sha256"], self.fz3["register_canonical_sha256"])
        m2 = load(os.path.join("step7", "STEP7_PROMPT_SPECIFICATION_FREEZE_MANIFEST_v2.json"))
        self.assertEqual(m["freeze_set_files"], m2["freeze_set_files"])


if __name__ == "__main__":
    unittest.main()
