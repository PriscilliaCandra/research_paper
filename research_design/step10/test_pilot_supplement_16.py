"""Acceptance tests for the descriptive pilot supplement (non-official). SYNTHETIC data only, in temporary folders: no real pilot is read; no network.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_pilot_supplement_16.py"""
import builtins
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import pilot_supplement_16 as PS  # noqa: E402

SECRET_TEXT = "SENTINEL_RESPONSE_TEXT_55d2"
SECRET_KEY = "SENTINEL_API_KEY_9e0f"
GOOD = "Constraint: x\nVerdict: satisfied\nComments: " + SECRET_TEXT
NOVERDICT = "I think the plan is fine. " + SECRET_TEXT


def rec(i, text=GOOD, http=200, **kw):
    r = {"call_index": i, "call_key": f"TPL-{i:03d},Z,1", "template_id": f"TPL-{i:03d}", "condition": "Z", "repetition": 1, "utc_start": f"2026-10-15T09:24:{i:02d}Z",
         "http_status": http, "error": None, "response_text": text, "finish_reason": "STOP", "model_returned": "gemini-3.5-flash-lite", "block_reason": None,
         "usage": {"promptTokenCount": 200 + i, "candidatesTokenCount": 100 + i, "totalTokenCount": 300 + 2 * i, "serviceTier": "standard"},
         "latency_ms": 1000 + i, "prompt_sha256": hashlib.sha256(str(i).encode()).hexdigest(), "extra_secret": SECRET_KEY}
    r.update(kw)
    return r


def pilot(n_bad=2, unp=3):
    recs = []
    for i in range(1, 19 - n_bad):
        recs.append(rec(i, NOVERDICT if i <= unp else GOOD))
    for i in range(19 - n_bad, 19):
        recs.append(rec(i, text="", http=429, error="HTTP 429", finish_reason=None, model_returned=None, usage=None, latency_ms=400))
    return recs


class Fixture(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="ps_")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.run = os.path.join(self.d, "run")
        os.makedirs(os.path.join(self.run, "raw"))
        with open(os.path.join(self.run, "raw", "00001.json"), "w") as f:
            f.write(SECRET_TEXT + SECRET_KEY)
        self.marker = "RUN_COMPLETE"
        self.recs = pilot()

    def write(self, summary=None):
        with open(os.path.join(self.run, "calls.jsonl"), "w", encoding="utf-8") as f:
            for r in self.recs:
                f.write(json.dumps(r) + "\n")
        for m in ("RUN_COMPLETE", "RUN_INCOMPLETE"):
            p = os.path.join(self.run, m)
            if os.path.exists(p):
                os.remove(p)
        s = {"scope": "pilot_round_1", "status": self.marker, "planned_calls": len(self.recs), "recorded_calls": len(self.recs)}
        s.update(summary or {})
        with open(os.path.join(self.run, self.marker), "w") as f:
            json.dump(s, f)

    def cli(self, summary=None):
        self.write(summary)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = PS.main(["--run", self.run])
        return code, out.getvalue()


class TestSupplement(Fixture):
    def test_inclusion_is_decided_from_the_records_and_changes_are_disclosed(self):
        code, text = self.cli()
        self.assertEqual(code, 0)
        self.assertIn("recorded calls: 18; included: 16; excluded: 2", text)
        self.assertIn("excluded: call 17: http_status 429; error marker present; no complete usage", text)
        self.assertIn("excluded: call 18: http_status 429", text)
        self.assertIn("NOT THE OFFICIAL PILOT RESULT", text)
        self.assertIn("CHANGES the population", text)

    def test_counts_tokens_and_latency_on_the_included_calls(self):
        _, text = self.cli()
        self.assertIn("SF1 0  SF2 0  SF3 0  SF4 0  SF5 0  UNP 3   (n = 16; multi-flag calls 0)", text)
        self.assertIn("UNP share on the included calls: 3/16 = 0.1875", text)
        self.assertIn("finish reasons: STOP 16", text)
        self.assertIn("models returned: gemini-3.5-flash-lite 16", text)
        self.assertIn("promptTokenCount:   min 201  median 208.5  mean 208.5  max 216  sum 3336", text)
        self.assertIn("latency_ms:         min 1001  median 1008.5  mean 1008.5  max 1016  sum 16136", text)
        self.assertIn("requests started per UTC clock minute (all recorded calls): 09:24 18", text)

    def test_a_200_without_complete_usage_is_excluded_not_inferred(self):
        self.recs[5]["usage"] = {"candidatesTokenCount": 10, "totalTokenCount": 20}
        self.recs[6]["usage"] = {"promptTokenCount": True, "candidatesTokenCount": 1, "totalTokenCount": 2}
        _, text = self.cli()
        self.assertIn("included: 14; excluded: 4", text)
        self.assertIn("excluded: call 6: http_status 200; no complete usage", text)
        self.assertIn("excluded: call 7: http_status 200; no complete usage", text)

    def test_non_200_without_429_and_a_200_with_an_error_marker_are_excluded(self):
        self.recs[0]["http_status"] = 500
        self.recs[1]["error"] = "x"
        _, text = self.cli()
        self.assertIn("included: 14; excluded: 4", text)

    def test_no_included_call(self):
        self.recs = [rec(i, http=429, error="HTTP 429", usage=None) for i in range(1, 19)]
        code, text = self.cli()
        self.assertEqual(code, 0)
        self.assertIn("no call is included", text)
        self.assertNotIn("UNP share", text)

    def test_all_calls_included(self):
        self.recs = [rec(i) for i in range(1, 19)]
        _, text = self.cli()
        self.assertIn("included: 18; excluded: 0", text)

    def test_prerequisites(self):
        self.marker = "RUN_INCOMPLETE"
        self.assertEqual(self.cli()[0], 0)                                  # a stopped run can be described, the marker is named in the output
        self.assertIn("run status marker: RUN_INCOMPLETE", self.cli()[1])
        self.marker = "RUN_COMPLETE"
        self.assertEqual(self.cli({"scope": "main"})[0], 2)
        self.assertEqual(self.cli({"planned_calls": 17})[0], 2)
        self.recs[3]["call_index"] = self.recs[2]["call_index"]
        self.assertEqual(self.cli()[0], 2)
        shutil.rmtree(self.run)
        os.makedirs(self.run)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(PS.main(["--run", self.run]), 2)


class TestSafety(Fixture):
    def test_output_has_no_content_labels_keys_status_words_or_json(self):
        code, text = self.cli()
        for s in (SECRET_TEXT, SECRET_KEY, "TPL-0", "call_key", "template", "condition", "response_text", "usageMetadata", "{", "}", "STATUS", "SUPPORTED", "B = ", "PASS", "FAIL",
                  "accuracy", "satisfied", "violated", "UNP rate"):
            self.assertNotIn(s, text, s)

    def test_hostile_enum_like_fields_are_masked(self):
        self.recs[8]["finish_reason"] = "free text with " + SECRET_TEXT
        self.recs[9]["model_returned"] = "Model With Spaces " + SECRET_KEY
        _, text = self.cli()
        self.assertNotIn(SECRET_TEXT, text)
        self.assertNotIn(SECRET_KEY, text)
        self.assertIn("other 1", text)

    def test_only_two_files_are_opened_for_reading_and_nothing_is_written(self):
        self.write()
        opened = []
        real = builtins.open

        def spy(file, mode="r", *a, **k):
            if isinstance(file, str) and os.path.commonpath([os.path.abspath(file), self.d]) == self.d:
                opened.append((os.path.relpath(file, self.d).replace("\\", "/"), mode))
            return real(file, mode, *a, **k)
        def snapshot():
            out = {}
            for dp, _, fs in os.walk(self.d):
                for p in fs:
                    with real(os.path.join(dp, p), "rb") as h:
                        out[p] = hashlib.sha256(h.read()).hexdigest()
            return out
        before = snapshot()
        with mock.patch("builtins.open", spy), contextlib.redirect_stdout(io.StringIO()):
            PS.main(["--run", self.run])
        self.assertEqual(sorted(set(n for n, _ in opened)), ["run/RUN_COMPLETE", "run/calls.jsonl"])
        self.assertTrue(all(set(m) <= set("rb") for _, m in opened), opened)
        after = snapshot()
        self.assertEqual(before, after)

    def test_no_network_no_credentials_and_no_threshold_or_bound_in_the_source(self):
        import socket
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network")), mock.patch.dict(os.environ, {"GEMINI_API_KEY": SECRET_KEY}):
            code, text = self.cli()
        self.assertEqual(code, 0)
        self.assertNotIn(SECRET_KEY, text)
        with open(os.path.join(HERE, "pilot_supplement_16.py"), encoding="utf-8") as f:
            src = f.read()
        for word in ("urllib", "socket", "import requests", "environ", "getenv", "f_S", "theta", "ceil(", "round_outcome", "final_decision"):
            self.assertNotIn(word, src, word)


if __name__ == "__main__":
    unittest.main()
