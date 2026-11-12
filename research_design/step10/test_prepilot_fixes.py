"""Tests of the pre-pilot fixes (decision record 12.34): local preflight, double-count-safe token accounting, latency measurement and the timeout
statement. Mock providers and synthetic usage dictionaries only; os.environ is patched; no network, no secret printed.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_prepilot_fixes.py"""
import datetime
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
KEY = "SECRET-KEY-VALUE-123"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pre_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bh = RN.sha(RN.file_bytes(os.path.join(BUILD, "manifest.json")))
        self.ev, self.au = T10.TestRunner.files(self, "pilot_round_1", days=9, calls=18)


class TestPreflight(Base):
    def run_with(self, provider, out="out"):
        return RN.run("pilot_round_1", BUILD, os.path.join(self.tmp, out), provider, evidence_path=self.ev, auth_path=self.au,
                      sleeper=lambda x: None, verify_build=False)

    def gemini_without_network(self):
        p = RN.GeminiProvider("gemini-3.5-flash-lite")
        return mock.patch.object(RN.GeminiProvider, "generate", side_effect=AssertionError("provider must not be called"))

    def test_missing_or_blank_key_creates_nothing_and_calls_nothing(self):
        for env in ({}, {"GEMINI_API_KEY": ""}, {"GEMINI_API_KEY": "   "}):
            with mock.patch.dict(os.environ, env, clear=True), self.gemini_without_network() as gen:
                with self.assertRaises(RN.PreflightFailed) as cm:
                    self.run_with(RN.GeminiProvider("gemini-3.5-flash-lite"))
                self.assertEqual(gen.call_count, 0)
            self.assertTrue(any("GEMINI_API_KEY" in r for r in cm.exception.reasons))
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "out")))              # no folder, hence no ledger, no call record, no round

    def test_model_mismatch_is_reported_without_printing_the_key(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": KEY}, clear=True), self.gemini_without_network() as gen:
            with self.assertRaises(RN.PreflightFailed) as cm:
                self.run_with(RN.GeminiProvider("some-other-model"))
            self.assertEqual(gen.call_count, 0)
        self.assertNotIn(KEY, str(cm.exception))
        self.assertTrue(any("model ID" in r for r in cm.exception.reasons))

    def test_incomplete_settings_are_reported(self):
        base = dict(RN.EXPECTED_SETTINGS)
        self.assertEqual(RN.preflight_settings(base), [])
        for k in RN.REQUIRED_SETTING_TYPES:
            bad = dict(base)
            del bad[k]
            self.assertTrue(RN.preflight_settings(bad), k)
        self.assertTrue(RN.preflight_settings(dict(base, retries=1)))
        self.assertTrue(RN.preflight_settings(dict(base, concurrency=2)))
        self.assertTrue(RN.preflight_settings(dict(base, timeout_s=0)))
        self.assertTrue(RN.preflight_settings(dict(base, model_id="  ")))
        self.assertTrue(RN.preflight_settings(dict(base, max_output_tokens=True)))
        self.assertTrue(RN.preflight_settings(None))

    def test_a_failing_provider_preflight_blocks_before_any_call(self):
        class Bad:
            calls = 0

            def preflight(self, s):
                return ["configuration incomplete"]

            def generate(self, *a):
                Bad.calls += 1
                return {}
        with self.assertRaises(RN.PreflightFailed):
            self.run_with(Bad())
        self.assertEqual(Bad.calls, 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out")))
        # the same out folder is then still free: the round was not consumed
        s = self.run_with(RN.MockProvider(T10.good))
        self.assertEqual(s["status"], "RUN_COMPLETE")

    def test_cli_live_with_no_key_refuses_and_creates_nothing(self):
        buf = io.StringIO()
        out = os.path.join(self.tmp, "cli_out")
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(buf), mock.patch.object(RN, "check_gates", return_value=dict(RN.EXPECTED_SETTINGS)), \
                self.gemini_without_network() as gen:
            code = RN.main(["--scope", "pilot_round_1", "--build", BUILD, "--out", out, "--evidence", self.ev, "--authorization", self.au, "--live"])
        self.assertEqual(code, 2)
        self.assertEqual(gen.call_count, 0)
        self.assertIn("PREFLIGHT FAILED", buf.getvalue())
        self.assertFalse(os.path.exists(out))

    def test_cli_without_live_reports_preflight_state_and_never_prints_the_key(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": KEY}, clear=True), redirect_stdout(buf), \
                mock.patch.object(RN, "check_gates", return_value=dict(RN.EXPECTED_SETTINGS)), self.gemini_without_network() as gen:
            code = RN.main(["--scope", "pilot_round_1", "--build", BUILD, "--out", os.path.join(self.tmp, "x"), "--evidence", self.ev, "--authorization", self.au])
        self.assertEqual(code, 0)
        self.assertEqual(gen.call_count, 0)
        self.assertIn("preflight (no network): OK", buf.getvalue())
        self.assertNotIn(KEY, buf.getvalue())


class TestTokenAccounting(unittest.TestCase):
    def test_variants(self):
        f = IG.output_tokens
        self.assertEqual(f({"promptTokenCount": 100, "candidatesTokenCount": 50, "totalTokenCount": 150}), (50, "total_minus_prompt"))
        self.assertEqual(f({"promptTokenCount": 100, "candidatesTokenCount": 50, "thoughtsTokenCount": 30, "totalTokenCount": 180}), (80, "total_minus_prompt"))
        self.assertEqual(f({"promptTokenCount": 100, "candidatesTokenCount": 80, "thoughtsTokenCount": 30, "totalTokenCount": 180}), (80, "total_minus_prompt"))   # candidates already include thoughts: not counted twice
        self.assertEqual(f({"candidatesTokenCount": 50, "thoughtsTokenCount": 30}), (80, "candidates_plus_thoughts"))
        self.assertEqual(f({"candidatesTokenCount": 50}), (50, "candidates_only"))
        self.assertEqual(f({"promptTokenCount": 10}), (None, "unavailable"))
        self.assertEqual(f(None), (None, "unavailable"))
        self.assertEqual(f({"promptTokenCount": "x", "candidatesTokenCount": True, "totalTokenCount": -1}), (None, "unavailable"))
        self.assertEqual(f({"promptTokenCount": 100, "candidatesTokenCount": 90, "totalTokenCount": 150})[1], "inconsistent_total_fallback")

    def test_integrity_reports_incomplete_usage_and_a_lower_bound(self):
        tmp = tempfile.mkdtemp(prefix="tok_")
        self.addCleanup(shutil.rmtree, tmp, True)
        bh = RN.sha(RN.file_bytes(os.path.join(BUILD, "manifest.json")))
        helper = Base("setUp")
        helper.tmp, helper.bh = tmp, bh
        ev, au = T10.TestRunner.files(helper, "pilot_round_1", days=9, calls=18)
        n = []

        def fn(k, p):
            n.append(k)
            r = T10.good(k, p)
            r["usage"] = [{"promptTokenCount": 100, "candidatesTokenCount": 50, "thoughtsTokenCount": 30, "totalTokenCount": 180}, None,
                          {"promptTokenCount": 100, "candidatesTokenCount": 50}][len(n) % 3]
            return r
        out = os.path.join(tmp, "run")
        with redirect_stdout(io.StringIO()):
            RN.run("pilot_round_1", BUILD, out, RN.MockProvider(fn), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        P, st = IG.verify_run(out, BUILD)
        self.assertEqual(P, [])
        self.assertEqual(st["calls_with_incomplete_usage"], 6)
        self.assertTrue(st["cost_is_lower_bound_if_incomplete"])
        self.assertEqual(st["output_token_basis"], {"total_minus_prompt": 6, "unavailable": 6, "candidates_only": 6})
        self.assertEqual(st["observed_tokens"]["output_billed"], 6 * 80 + 6 * 50)


class TestLatencyAndTimeout(Base):
    def test_latency_is_measured_when_the_provider_gives_none_and_kept_when_it_does(self):
        t = iter(x * 0.25 for x in range(1000))

        def timer():
            return next(t)

        def fn(k, p):
            r = T10.good(k, p)
            if k.endswith(",B,1"):
                r["latency_ms"] = 77
            else:
                r.pop("latency_ms")
            return r
        out = os.path.join(self.tmp, "run")
        with redirect_stdout(io.StringIO()):
            s = RN.run("pilot_round_1", BUILD, out, RN.MockProvider(fn), evidence_path=self.ev, auth_path=self.au, sleeper=lambda x: None, verify_build=False, timer=timer)
        recs = [json.loads(l) for l in RN.file_bytes(os.path.join(out, "calls.jsonl")).decode().split("\n") if l]
        self.assertTrue(all(isinstance(r["latency_ms"], int) for r in recs))
        self.assertEqual({r["latency_ms"] for r in recs if not r["call_key"].endswith(",B,1")}, {250})
        self.assertEqual([r["latency_ms"] for r in recs if r["call_key"].endswith(",B,1")], [77] * sum(r["call_key"].endswith(",B,1") for r in recs))
        P, st = IG.verify_run(out, BUILD)
        self.assertEqual((P, st["latency_recorded_calls"], st["calls_with_latency_over_nominal_timeout_180s"]), ([], 18, 0))

    def test_timeout_is_not_presented_as_a_total_deadline(self):
        self.assertIn("NO total deadline", RN.TIMEOUT_SEMANTICS)
        out = os.path.join(self.tmp, "run2")
        with redirect_stdout(io.StringIO()):
            s = RN.run("pilot_round_1", BUILD, out, RN.MockProvider(T10.good), evidence_path=self.ev, auth_path=self.au, sleeper=lambda x: None, verify_build=False)
        self.assertEqual(s["timeout_semantics"], RN.TIMEOUT_SEMANTICS)
        self.assertEqual(s["settings"]["timeout_s"], 180)                               # the frozen value is unchanged

    def test_slow_call_is_flagged_after_the_fact(self):
        t = iter([0, 200.0] + [x * 0.001 for x in range(10000)])
        out = os.path.join(self.tmp, "run3")
        with redirect_stdout(io.StringIO()):
            RN.run("pilot_round_1", BUILD, out, RN.MockProvider(lambda k, p: {k2: v for k2, v in T10.good(k, p).items() if k2 != "latency_ms"}), evidence_path=self.ev,
                   auth_path=self.au, sleeper=lambda x: None, verify_build=False, timer=lambda: next(t))
        P, st = IG.verify_run(out, BUILD)
        self.assertEqual(st["calls_with_latency_over_nominal_timeout_180s"], 1)


if __name__ == "__main__":
    unittest.main()
