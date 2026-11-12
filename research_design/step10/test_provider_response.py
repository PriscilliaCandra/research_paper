"""Synthetic tests of provider-response handling (decision record 12.36). urllib.request.urlopen is PATCHED: no network, no key value leaves memory.
The payload shapes below are the assumed public REST shapes, NOT observations of the live API.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_provider_response.py"""
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_integrity as IG  # noqa: E402
import s10_runner as RN  # noqa: E402
RN.FZ_INDEX_ENFORCED = False  # E-1 (12.55): these tests exercise other machinery with mock data; enforcement is tested in test_fz_enforcement.py
import s10_scoring as SC  # noqa: E402
import test_step10 as T10  # noqa: E402

BUILD = T10.BUILD
KEY = "SYNTH-KEY-0000-NOT-REAL"
MODEL = "gemini-3.5-flash-lite"
SETTINGS = dict(RN.EXPECTED_SETTINGS)


class FakeResp:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def read(self):
        return self.payload if isinstance(self.payload, bytes) else json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def payload(text="Constraint: x\nVerdict: satisfied\nComments: ok", finish="STOP", model=MODEL, usage=True, candidates=True, feedback=None):
    d = {}
    if candidates:
        d["candidates"] = [{"content": {"parts": [{"text": text}] if text is not None else []}, **({"finishReason": finish} if finish else {})}]
    if model:
        d["modelVersion"] = model
    if usage:
        d["usageMetadata"] = {"promptTokenCount": 100, "candidatesTokenCount": 50, "totalTokenCount": 150} if usage is True else usage
    if feedback:
        d["promptFeedback"] = feedback
    return d


def gen(fake):
    """GeminiProvider.generate with urlopen patched; returns (result, number of urlopen calls, request)."""
    with mock.patch.dict(os.environ, {"GEMINI_API_KEY": KEY}, clear=True), mock.patch("urllib.request.urlopen", side_effect=fake) as m:
        res = RN.GeminiProvider(MODEL).generate("prompt", SETTINGS, "MIL-001,A,1")
    return res, m.call_count, (m.call_args[0][0] if m.call_args else None)


def rec_from(res):
    return {"response_text": res.get("text") or "", "error": res.get("error"), "http_status": res.get("http_status"),
            "finish_reason": res.get("finish_reason"), "model_returned": res.get("model_returned"), "usage": res.get("usage"),
            "block_reason": res.get("block_reason")}


class TestAdapterMapping(unittest.TestCase):
    def test_normal_response_and_request_shape(self):
        res, n, req = gen(lambda *a, **k: FakeResp(payload()))
        self.assertEqual((res["http_status"], res["finish_reason"], res["model_returned"], n), (200, "STOP", MODEL, 1))
        self.assertEqual(SC.classify(rec_from(res))["outcome"], "satisfied")
        self.assertEqual(req.get_header("X-goog-api-key"), KEY)                       # the key goes into a header only
        self.assertNotIn(KEY.encode(), req.data)
        body = json.loads(req.data)
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 8192)
        self.assertNotIn("temperature", json.dumps(body))                             # temperature is not sent (FZ-v1 row 17)
        self.assertNotIn("systemInstruction", body)

    def test_no_candidates_with_and_without_prompt_block(self):
        res, _, _ = gen(lambda *a, **k: FakeResp(payload(candidates=False, feedback={"blockReason": "SAFETY"})))
        self.assertEqual(res["block_reason"], "SAFETY")
        self.assertEqual(SC.classify(rec_from(res))["flags"], ["SF4"])
        res, _, _ = gen(lambda *a, **k: FakeResp(payload(candidates=False)))
        c = SC.classify(rec_from(res))
        self.assertEqual((c["outcome"], c["flags"]), ("SF", ["SF4"]))                 # no candidate returned = SF4 (13.4)

    def test_candidate_without_text(self):
        res, _, _ = gen(lambda *a, **k: FakeResp(payload(text=None)))
        self.assertEqual(SC.classify(rec_from(res))["flags"], ["SF2"])
        res, _, _ = gen(lambda *a, **k: FakeResp(payload(text="   \n ")))
        self.assertEqual(SC.classify(rec_from(res))["flags"], ["SF2"])

    def test_transport_failures_are_sf1_and_called_once(self):
        for exc, label in ((urllib.error.HTTPError("u", 429, "x", {}, None), "HTTP 429"), (urllib.error.HTTPError("u", 400, "x", {}, None), "HTTP 400"),
                           (socket.timeout("t"), "TimeoutError"), (urllib.error.URLError("x"), "URLError")):
            def boom(*a, **k):
                raise exc
            res, n, _ = gen(boom)
            self.assertEqual(n, 1, label)                                              # retries = 0
            self.assertEqual(res["error"], label)
            self.assertNotIn(KEY, json.dumps(res))
            c = SC.classify(rec_from(res))
            self.assertEqual((c["outcome"], c["flags"]), ("SF", ["SF1"]), label)

    def test_non_json_body_is_sf1_not_success(self):
        res, _, _ = gen(lambda *a, **k: FakeResp(b"<html>oops</html>"))
        self.assertIn(SC.classify(rec_from(res))["flags"][0], ("SF1",))
        self.assertTrue(res["error"])

    def test_missing_key_never_reaches_urlopen(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("urllib.request.urlopen") as m:
            res = RN.GeminiProvider(MODEL).generate("p", SETTINGS, "k")
        self.assertEqual(m.call_count, 0)
        self.assertEqual(SC.classify(rec_from(res))["flags"], ["SF1"])


class TestClassificationRules(unittest.TestCase):
    def c(self, **kw):
        base = {"response_text": "Verdict: satisfied", "error": None, "http_status": 200, "finish_reason": "STOP", "model_returned": MODEL, "usage": {"totalTokenCount": 150}}
        base.update(kw)
        return SC.classify(base)

    def test_blocked_finish_reasons_are_sf4_even_with_partial_text(self):
        for fr in sorted(SC.BLOCK_REASONS):
            c = self.c(finish_reason=fr)
            self.assertEqual((c["outcome"], c["flags"]), ("SF", ["SF4"]), fr)

    def test_token_limit_is_sf3_even_with_a_parseable_verdict(self):
        self.assertEqual(self.c(finish_reason="MAX_TOKENS")["flags"], ["SF3"])

    def test_unknown_finish_reason_is_not_given_a_meaning_but_is_reported(self):
        c = self.c(finish_reason="SOMETHING_NEW")
        self.assertEqual(c["outcome"], "satisfied")                                    # protocol silent: parse decides (decision question Q-P2)
        self.assertEqual(SC.finish_reason_class("SOMETHING_NEW"), "unlisted")
        pc = SC.pilot_counts([{"response_text": "Verdict: satisfied", "error": None, "http_status": 200, "finish_reason": "SOMETHING_NEW", "usage": None}])
        self.assertEqual(pc["unlisted_finish_reasons"], {"SOMETHING_NEW": 1})

    def test_contradictory_records_fail_closed(self):
        self.assertEqual(self.c(error="HTTP 500")["flags"], ["SF1"])                    # error marker plus text is not a success
        self.assertEqual(self.c(http_status=500)["flags"], ["SF1"])
        self.assertEqual(self.c(http_status=302)["flags"], ["SF1"])
        self.assertEqual(self.c(block_reason="SAFETY")["flags"], ["SF4"])
        self.assertEqual(self.c(http_status=200)["outcome"], "satisfied")

    def test_http_status_absent_without_error_is_not_decided_but_reported(self):
        self.assertEqual(self.c(http_status=None)["outcome"], "satisfied")              # protocol silent (decision question Q-P4)
        recs = [{"call_key": "k", "response_text": "x", "http_status": None, "error": None}]
        self.assertEqual(sum(1 for r in recs if r.get("http_status") is None and not r.get("error")), 1)

    def test_missing_usage_is_counted_not_zero_costed(self):
        base = {"response_text": "Verdict: satisfied", "error": None, "http_status": 200, "finish_reason": "STOP"}
        pc = SC.pilot_counts([dict(base, usage=None), dict(base, usage={"promptTokenCount": 5}), dict(base, usage={"totalTokenCount": 150}),
                              dict(base, usage={"totalTokenCount": True})])
        self.assertEqual((pc["total_tokens_known"], pc["calls_without_usable_total_tokens"]), (150, 3))


class TestRunLevel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prv_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bh = RN.sha(RN.file_bytes(os.path.join(BUILD, "manifest.json")))
        self.ev, self.au = T10.TestRunner.files(self, "pilot_round_1", days=9, calls=18)

    def run_pilot(self, provider, out="run"):
        with redirect_stdout(io.StringIO()):
            RN.run("pilot_round_1", BUILD, os.path.join(self.tmp, out), provider, evidence_path=self.ev, auth_path=self.au, sleeper=lambda x: None, verify_build=False)
        return os.path.join(self.tmp, out)

    def mixed(self, k, p):
        r = dict(T10.good(k, p), model_returned=MODEL)
        i = self.calls.index(k)
        if i == 0:
            return {"http_status": 200, "text": "", "error": None, "finish_reason": None, "model_returned": MODEL}        # no candidate
        if i == 1:
            return dict(r, finish_reason="SAFETY", text="partial")
        if i == 2:
            return dict(r, finish_reason="MAX_TOKENS", text="Verdict: satisfied")                                          # token limit (SF3)
        if i == 3:
            return dict(r, finish_reason="BLOCKLIST", text="")
        if i == 4:
            return dict(r, usage=None)
        if i == 5:
            return {"http_status": 200, "text": "Verdict: satisfied", "error": "HTTP 500"}
        return r

    def test_mixed_responses_run_keeps_ledger_hashes_retries_and_marks_failures(self):
        self.calls = [c["call_key"] for c in RN.plan_calls("pilot_round_1", BUILD)]
        seen = []

        def fn(k, p):
            seen.append(k)
            return self.mixed(k, p)
        run = self.run_pilot(RN.MockProvider(fn))
        self.assertEqual(seen, self.calls)                                                 # every key exactly once: retries = 0, no replacement
        P, st = IG.verify_run(run, BUILD)
        self.assertEqual(P, [])                                                            # ledger chain, order, raw hashes intact
        pc = json.loads(RN.file_bytes(os.path.join(run, "RUN_COMPLETE")))["pilot_counts"]
        self.assertEqual(pc["counts"], {"SF1": 1, "SF2": 0, "SF3": 1, "SF4": 3, "SF5": 0, "UNP": 0})     # five failures counted, none as success
        self.assertEqual(pc["unlisted_finish_reasons"], {})                          # an unlisted reason now stops the run (MD-06), see test_safe_stop
        self.assertEqual(pc["calls_without_usable_total_tokens"], 3)               # no-candidate, usage None, error-with-text records
        pcs = st["provider_checks"]
        self.assertEqual(pcs["responses_with_returned_model_not_exactly_requested"], 0)
        self.assertEqual(pcs["unlisted_finish_reasons"], {})
        self.assertEqual(st["calls_with_incomplete_usage"], 18)                 # the mock usage has no promptTokenCount: every call is incomplete for costing
        recs = [json.loads(l) for l in RN.file_bytes(os.path.join(run, "calls.jsonl")).decode().split("\n") if l]
        outcomes = [SC.classify(r)["outcome"] for r in recs]
        self.assertEqual(outcomes[:6], ["SF", "SF", "SF", "SF", "violated", "SF"])
        self.assertEqual(sum(o == "SF" for o in outcomes), 5)

    def test_returned_model_difference_stops_the_run_after_recording_it(self):
        """Rewritten to the adopted policy (12.50 row 1, MD-06 trigger 1): a differing returned model ID is stored, then the run stops as RUN_INCOMPLETE; no scoring."""
        run = self.run_pilot(RN.MockProvider(lambda k, p: dict(T10.good(k, p), model_returned="gemini-3.5-flash-lite-001")))
        s = json.loads(RN.file_bytes(os.path.join(run, "RUN_INCOMPLETE")))
        self.assertEqual((s["status"], s["recorded_calls"]), ("RUN_INCOMPLETE", 1))
        self.assertIn("provider anomaly: returned model ID", s["halt"])
        P, st = IG.verify_run(run, BUILD)
        self.assertEqual(P, [])
        self.assertEqual(st["provider_checks"]["responses_with_returned_model_not_exactly_requested"], 1)

    def test_adapter_end_to_end_with_patched_urlopen_never_writes_the_key(self):
        calls = []

        def fake(req, timeout=None):
            calls.append(timeout)
            return FakeResp(payload(text="Verdict: satisfied"))
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": KEY}, clear=True), mock.patch("urllib.request.urlopen", side_effect=fake):
            run = self.run_pilot(RN.GeminiProvider(MODEL), out="gem")
        self.assertEqual((len(calls), set(calls)), (18, {180}))
        for dp, _, fs in os.walk(run):
            for f in fs:
                self.assertNotIn(KEY.encode(), RN.file_bytes(os.path.join(dp, f)), f)
        self.assertEqual(IG.verify_run(run, BUILD)[0], [])

    def test_gate_behaviour_is_unchanged(self):
        with self.assertRaises(RN.GateClosed):
            RN.check_gates("pilot_round_1", BUILD, os.path.join(self.tmp, "none.json"), os.path.join(self.tmp, "none2.json"), verify_build=False)


if __name__ == "__main__":
    unittest.main()
