"""Acceptance tests for local_feasibility.py. A FAKE Ollama is used: urllib's urlopen is patched, so NO socket is opened and nothing is sent anywhere. Synthetic prompts only.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_local_feasibility.py"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import local_feasibility as LF  # noqa: E402
import s10_scoring as SC  # noqa: E402

SECRET = "SENTINEL_RESPONSE_TEXT_c41d"


class FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._b = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Fake:
    """Scripted Ollama. handler(n, body, cond) -> dict (200 response body), ('http', code), ('raise', exc) or ('latency', seconds, dict)."""

    def __init__(self, handler=None, show_status=200):
        self.t = 0.0
        self.requests = []
        self.gen = 0
        self.handler = handler or self.good
        self.show_status = show_status

    @staticmethod
    def good(n, body, cond):
        if body["options"]["num_predict"] == LF.PROBE_NUM_PREDICT:
            return {"model": "llama3.2:3b", "response": "Constr", "done": True, "done_reason": "length", "prompt_eval_count": 250, "eval_count": 8, "eval_duration": 400000000}
        return {"model": "llama3.2:3b", "response": f"Constraint: x\nVerdict: {SC.EXPECTED[cond]}\nComments: {SECRET}", "done": True, "done_reason": "stop",
                "prompt_eval_count": 250, "prompt_eval_cached_count": 0, "eval_count": 60, "eval_duration": 3000000000, "total_duration": 3500000000, "load_duration": 100}

    def __call__(self, req, timeout=None):
        url = req.full_url
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        self.requests.append((req.get_method(), url, body, timeout))
        if url.endswith("/api/version"):
            return FakeResp(200, {"version": "0.0.0-test"})
        if url.endswith("/api/tags"):
            return FakeResp(200, {"models": [{"name": "llama3.2:3b", "model": "llama3.2:3b", "digest": "d" * 64}]})
        if url.endswith("/api/show"):
            if self.show_status != 200:
                raise urllib.error.HTTPError(url, self.show_status, "x", None, io.BytesIO(b"{}"))
            return FakeResp(200, {"template": "{{ if .System }}<|system|>{{ end }}", "parameters": "stop \"<|eot_id|>\"", "details": {"parameter_size": "3.2B"}})
        assert url.endswith("/api/generate"), url
        self.gen += 1
        cond = None
        for c in self.cond_by_prompt:
            if c[0] == body["prompt"]:
                cond = c[1]
        out = self.handler(self.gen, body, cond)
        lat = 2.0
        if isinstance(out, tuple) and out[0] == "latency":
            lat, out = out[1], out[2]
        self.t += lat
        if isinstance(out, tuple) and out[0] == "http":
            raise urllib.error.HTTPError(url, out[1], "err", None, io.BytesIO(b'{"error":"x"}'))
        if isinstance(out, tuple) and out[0] == "raise":
            raise out[1]
        return FakeResp(200, out)


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="lf_")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.out = os.path.join(self.d, "run")
        calls, repeats, _ = LF.build_prompts()
        self.calls, self.repeats = calls, repeats

    def go(self, fake, out=None, **kw):
        fake.cond_by_prompt = [(c["prompt"], c["condition"]) for c in self.calls]
        lines = []
        with mock.patch.object(LF, "urlopen", fake), mock.patch.object(LF, "timer", lambda: fake.t):
            s = LF.run(out or self.out, say=lines.append, **kw)
        return s, lines


class TestPrompts(Base):
    def test_64_main_and_16_repeats_with_the_frozen_format_and_disjoint_bases(self):
        self.assertEqual((len(self.calls), len(self.repeats)), (64, 16))
        self.assertEqual({c["condition"] for c in self.calls}, set("ABCD"))
        self.assertEqual(len({(c["template_id"], c["condition"]) for c in self.calls}), 64)
        self.assertEqual({c["condition"] for c in self.repeats}, set("ABCD"))
        for c in self.calls:
            self.assertIn("Verdict:", c["prompt"])
            self.assertLessEqual(len(c["prompt"]), 1016)
        a, b, _ = LF.build_prompts()                                     # deterministic order
        self.assertEqual([c["prompt"] for c in a], [c["prompt"] for c in self.calls])
        self.assertTrue(set(LF.S_SYN).isdisjoint({200 + 10 * j for j in range(26)} | {455, 465}) and set(LF.G_SYN).isdisjoint(set(range(310, 336)) | {340, 341}))

    def test_dry_run_makes_no_request_and_writes_nothing(self):
        with mock.patch.object(LF, "urlopen", side_effect=AssertionError("request")):
            lines = []
            self.assertIsNone(LF.run(self.out, dry_run=True, say=lines.append))
        self.assertFalse(os.path.exists(self.out))
        self.assertTrue(any("dry run" in l for l in lines))


class TestSafetyOfTheTarget(Base):
    def test_only_loopback_http_is_accepted(self):
        for bad in ("http://example.com:11434", "http://192.168.1.5:11434", "https://127.0.0.1:11434", "http://127.0.0.1@evil.com", "http://localhost.evil.com:11434",
                    "http://127.0.0.1:11434/api", "http://u:p@127.0.0.1:11434", "ftp://127.0.0.1", "http://0.0.0.0:11434", ""):
            with self.subTest(bad), self.assertRaises(ValueError):
                LF.local_base(bad)
        for ok, want in (("http://127.0.0.1:11434", "http://127.0.0.1:11434"), ("http://localhost", "http://localhost:11434"), ("http://[::1]:8080/", "http://[::1]:8080")):
            self.assertEqual(LF.local_base(ok), want)

    def test_a_non_local_url_stops_the_run_before_any_request_or_file(self):
        with mock.patch.object(LF, "urlopen", side_effect=AssertionError("request")), self.assertRaises(ValueError):
            LF.run(self.out, url="http://example.com:11434")
        self.assertFalse(os.path.exists(self.out))

    def test_cli_refusals_and_no_credentials_in_the_source(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(LF.main(["--url", "http://example.com"]), 2)
            self.assertEqual(LF.main(["--out", self.out, "--url", "http://example.com"]), 2)
        with open(os.path.join(HERE, "local_feasibility.py"), encoding="utf-8") as f:
            src = f.read()
        for word in ("os.environ", "getenv", "api_key", "x-goog", "import socket", "googleapis"):
            self.assertNotIn(word, src, word)


class TestRun(Base):
    def test_happy_path_requests_outputs_and_status(self):
        fake = Fake()
        s, lines = self.go(fake)
        self.assertEqual((s["status"], s["counted_calls"], fake.gen), ("FEASIBILITY_PROCEED", 80, 82))
        self.assertEqual(s["counts"], {"SF1": 0, "SF2": 0, "SF3": 0, "SF4": 0, "SF5": 0, "UNP": 0})
        gens = [r for r in fake.requests if r[1].endswith("/api/generate")]
        for m, url, body, timeout in gens:
            self.assertEqual((m, url, timeout), ("POST", "http://127.0.0.1:11434/api/generate", LF.TIMEOUT_S))
            self.assertEqual((body["model"], body["stream"], body["keep_alive"]), ("llama3.2:3b", False, "30m"))
            self.assertEqual(body["options"]["num_ctx"], 16384)
            self.assertNotIn("temperature", body["options"] | body)
            self.assertNotIn("seed", body["options"])
            self.assertNotIn("system", body)
        self.assertEqual([g[2]["options"]["num_predict"] for g in gens[:2]], [8, 8192])
        self.assertTrue(all(g[2]["options"]["num_predict"] == 8192 for g in gens[2:]))
        self.assertEqual(sorted(os.listdir(self.out)), ["calls.jsonl", "prompts_manifest.json", "provenance.json", "summary.json"])
        with open(os.path.join(self.out, "calls.jsonl"), encoding="utf-8") as f:
            self.assertEqual(sum(1 for _ in f), 82)
        with open(os.path.join(self.out, "provenance.json"), encoding="utf-8") as h:
            prov = json.load(h)
        self.assertEqual((prov["ollama_version"], prov["model_digest"], prov["template_has_system_block"]), ("0.0.0-test", "d" * 64, True))
        self.assertEqual(len(prov["harness_sha256"]), 64)
        self.assertEqual(s["stability"], {"pairs": 16, "identical_text": 16, "identical_outcome": 16})
        self.assertEqual(s["informational"]["verdict_equals_synthetic_expectation"], 80)
        self.assertAlmostEqual(s["projected_main_run_hours"], 2496 * 2.0 / 3600, places=6)

    def test_probes_are_not_counted_and_record_the_truncation_value(self):
        s, _ = self.go(Fake())
        p = s["probes"]
        self.assertEqual((p[0]["done_reason_raw"], p[0]["finish_reason"], p[0]["num_predict"]), ("length", "MAX_TOKENS", 8))
        self.assertEqual((p[1]["finish_reason"], p[1]["num_predict"]), ("STOP", 8192))
        self.assertEqual(s["counts"]["SF3"], 0)

    def test_unp_thresholds_at_the_boundaries(self):
        def with_unp(k):
            def h(n, body, cond):
                if body["options"]["num_predict"] == 8:
                    return Fake.good(n, body, cond)
                return dict(Fake.good(n, body, cond), response="I think it is fine.") if n - 2 <= k else Fake.good(n, body, cond)
            return h
        for k, want in ((0, "FEASIBILITY_PROCEED"), (8, "FEASIBILITY_PROCEED"), (9, "OWNER_DECISION"), (20, "OWNER_DECISION"), (21, "STOP_NOT_FEASIBLE")):
            with self.subTest(unp=k):
                s, _ = self.go(Fake(with_unp(k)), out=os.path.join(self.d, f"u{k}"))
                self.assertEqual((s["counts"]["UNP"], s["status"]), (k, want))

    def test_structural_failures_and_truncation(self):
        def sf(ks, kind):
            def h(n, body, cond):
                if body["options"]["num_predict"] == 8 or (n - 2) not in ks:
                    return Fake.good(n, body, cond)
                return ("http", 503) if kind == "http" else dict(Fake.good(n, body, cond), done_reason="length")
            return h
        s, _ = self.go(Fake(sf({5}, "http")), out=os.path.join(self.d, "a"))
        self.assertEqual((s["counts"]["SF1"], s["status"]), (1, "FEASIBILITY_PROCEED"))
        s, _ = self.go(Fake(sf({5, 9}, "http")), out=os.path.join(self.d, "b"))
        self.assertEqual((s["counts"]["SF1"], s["status"]), (2, "STOP_NOT_FEASIBLE"))
        s, _ = self.go(Fake(sf({7}, "len")), out=os.path.join(self.d, "c"))
        self.assertEqual((s["counts"]["SF3"], s["status"]), (1, "STOP_NOT_FEASIBLE"))

    def test_unlisted_finish_reason_cannot_collide_with_block_names(self):
        self.assertEqual(LF.map_finish("other"), "OLLAMA_OTHER")
        self.assertEqual(LF.map_finish("load"), "OLLAMA_LOAD")
        self.assertEqual(LF.map_finish("a b!"), "OLLAMA_AB")
        self.assertEqual((LF.map_finish("stop"), LF.map_finish("length"), LF.map_finish(None)), ("STOP", "MAX_TOKENS", None))
        self.assertNotIn(LF.map_finish("other"), SC.BLOCK_REASONS)

        def h(n, body, cond):
            return dict(Fake.good(n, body, cond), done_reason="load") if n == 10 else Fake.good(n, body, cond)
        s, _ = self.go(Fake(h))
        self.assertEqual(s["anomalies"]["unlisted_finish_reason"], 1)
        self.assertEqual(s["counts"]["SF4"], 0)

    def test_model_mismatch_makes_the_run_inconclusive(self):
        def h(n, body, cond):
            return dict(Fake.good(n, body, cond), model="llama3.2:1b") if n == 12 else Fake.good(n, body, cond)
        s, _ = self.go(Fake(h))
        self.assertEqual((s["anomalies"]["model_mismatch"], s["status"]), (1, "INCONCLUSIVE"))

    def test_five_consecutive_failures_abort_the_run(self):
        def h(n, body, cond):
            return ("raise", ConnectionRefusedError()) if n >= 6 else Fake.good(n, body, cond)
        fake = Fake(h)
        s, _ = self.go(fake)
        self.assertEqual(s["status"], "INCONCLUSIVE")
        self.assertIn("consecutive", s["aborted"])
        self.assertEqual(fake.gen, 2 + 3 + 5)                            # 2 probes, 3 good calls, 5 failures, then no further request

    def test_projected_time_boundary(self):
        per_call_ok = 24 * 3600 / 2496
        s, _ = self.go(Fake(lambda n, b, c: ("latency", per_call_ok, Fake.good(n, b, c))), out=os.path.join(self.d, "t1"))
        self.assertEqual(s["status"], "FEASIBILITY_PROCEED")
        s, _ = self.go(Fake(lambda n, b, c: ("latency", per_call_ok + 0.5, Fake.good(n, b, c))), out=os.path.join(self.d, "t2"))
        self.assertEqual((s["components"]["time"], s["status"]), ("STOP", "STOP_NOT_FEASIBLE"))

    def test_the_first_call_is_excluded_from_the_projection(self):
        def h(n, b, c):
            return ("latency", 600.0 if n == 3 else 2.0, Fake.good(n, b, c))
        s, _ = self.go(Fake(h))
        self.assertEqual(s["latency_ms"]["first_call"], 600000)
        self.assertEqual(s["status"], "FEASIBILITY_PROCEED")

    def test_missing_show_endpoint_is_recorded_not_fatal(self):
        s, _ = self.go(Fake(show_status=500))
        self.assertEqual(s["status"], "FEASIBILITY_PROCEED")
        with open(os.path.join(self.out, "provenance.json"), encoding="utf-8") as h:
            self.assertEqual(json.load(h)["template_sha256"], "unavailable")

    def test_usage_incomplete_is_an_anomaly_not_an_invented_count(self):
        def h(n, body, cond):
            r = dict(Fake.good(n, body, cond))
            if n == 20:
                r.pop("prompt_eval_count")
            return r
        s, _ = self.go(Fake(h))
        self.assertEqual(s["anomalies"]["usage_incomplete"], 1)
        with open(os.path.join(self.out, "calls.jsonl"), encoding="utf-8") as f:
            recs = [json.loads(l) for l in f]
        self.assertEqual(recs[19]["usage"], {"candidatesTokenCount": 60})

    def test_stability_counts_differences_between_repeats(self):
        def h(n, body, cond):
            r = Fake.good(n, body, cond)
            return dict(r, response=r["response"] + " extra") if n > 66 and n % 2 else r      # a few repeats differ in text
        s, _ = self.go(Fake(h))
        self.assertLess(s["stability"]["identical_text"], 16)
        self.assertEqual(s["stability"]["identical_outcome"], 16)

    def test_existing_output_folder_is_refused(self):
        os.makedirs(self.out)
        with mock.patch.object(LF, "urlopen", side_effect=AssertionError("request")), self.assertRaises(FileExistsError):
            LF.run(self.out)


class TestNoLeak(Base):
    def test_terminal_output_has_no_prompt_response_or_raw_record(self):
        s, lines = self.go(Fake())
        text = "\n".join(lines)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("Verdict", text)
        for c in self.calls[:5]:
            self.assertNotIn(c["prompt"][:60], text)
        for bad in ("response_text", "{", "}", "prompt_sha256"):
            self.assertNotIn(bad, text, bad)
        self.assertIn("STATUS: FEASIBILITY_PROCEED", text)
        self.assertIn("not a pilot outcome", text)
        for forbidden in ("PASS", "FAIL-S"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
