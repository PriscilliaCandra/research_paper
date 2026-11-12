"""INACTIVE DRAFT acceptance tests for P2 (MD-06 safe-stop clauses 1, 2, 4), to be activated together with P2_safe_stop.diff.INACTIVE as step10/test_safe_stop.py.
Mock provider only; temporary folders; no network. Written from the MD-06 text, not from the draft code.
Run (after activation): python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_safe_stop.py"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_integrity as IG  # noqa: E402
import s10_runner as RN  # noqa: E402
import s11_pilot as PL  # noqa: E402
import test_pilot_flow as PF  # noqa: E402
import test_step10 as T10  # noqa: E402

BUILD = T10.BUILD
REQ = "gemini-3.5-flash-lite"


def scripted(at, **override):
    """Provider whose `at`-th call returns the good response with `override` fields (None removes a field)."""
    seen = []

    def fn(key, prompt):
        seen.append(key)
        res = dict(T10.good(key, prompt))
        if len(seen) == at:
            for k, v in override.items():
                if v is None:
                    res.pop(k, None)
                else:
                    res[k] = v
        return res
    fn.seen = seen
    return fn


class TestSafeStop(PF.Base):
    def stopped_run(self, at, **override):
        fn = scripted(at, **override)
        run = self.run_pilot(fn)
        s = json.loads(PF.rb(os.path.join(run, "RUN_INCOMPLETE")))
        return run, s, fn

    def check_stopped(self, at, reason_part, **override):
        run, s, fn = self.stopped_run(at, **override)
        self.assertEqual((s["status"], s["recorded_calls"]), ("RUN_INCOMPLETE", at))
        self.assertIn(reason_part, s["halt"])
        self.assertEqual(len(fn.seen), at)                                       # no call after the anomaly
        self.assertEqual(len(set(fn.seen)), at)                                  # retries 0
        self.assertTrue(os.path.exists(os.path.join(run, "raw", f"{at:05d}.json")))   # raw stored before the stop
        self.assertEqual(IG.verify_run(run, BUILD)[0], [])                       # hash chain and files consistent
        with self.assertRaises(ValueError):                                      # never scored as a valid pilot
            PL.load(run, 1)
        return run

    def test_clause1_model_id_different_stops_after_recording(self):
        run = self.check_stopped(3, "returned model ID", model_returned="other-model")
        recs = [json.loads(l) for l in PF.rb(os.path.join(run, "calls.jsonl")).decode().split("\n") if l]
        self.assertEqual(recs[2]["model_returned"], "other-model")              # kept as stored, not replaced

    def test_clause1_model_id_missing_stops(self):
        self.check_stopped(2, "returned model ID", model_returned=None)

    def test_clause2_unlisted_finish_reason_stops(self):
        self.check_stopped(4, "unlisted finish reason", finish_reason="SOMETHING_NEW")

    def test_clause4_record_without_http_status_stops(self):
        self.check_stopped(5, "without HTTP status", http_status=None)

    def test_transport_failure_does_not_stop(self):
        run = self.run_pilot(scripted(3, http_status=503, text="", error="HTTP 503", model_returned=None, finish_reason=None))
        s = json.loads(PF.rb(os.path.join(run, "RUN_COMPLETE")))
        self.assertEqual(s["recorded_calls"], 18)

    def test_known_finish_reasons_and_matching_model_do_not_stop(self):
        for fr in ("STOP", "MAX_TOKENS", "SAFETY"):
            run = self.run_pilot(scripted(2, finish_reason=fr))
            self.assertEqual(json.loads(PF.rb(os.path.join(run, "RUN_COMPLETE")))["recorded_calls"], 18, fr)

    def test_anomaly_function_directly(self):
        a = RN.provider_anomaly
        ok = {"http_status": 200, "error": None, "model_returned": REQ, "finish_reason": "STOP"}
        self.assertIsNone(a(ok, REQ))
        self.assertIsNone(a(dict(ok, error="HTTP 503", http_status=503, model_returned=None), REQ))
        self.assertIsNone(a(dict(ok, http_status=500, error=None), REQ))         # non-2xx without marker is SF1 by 12.36, not an anomaly here
        self.assertTrue(a(dict(ok, http_status=None), REQ))
        self.assertTrue(a(dict(ok, model_returned=REQ + "-001"), REQ))
        self.assertTrue(a(dict(ok, finish_reason="WEIRD"), REQ))
        self.assertIsNone(a(dict(ok, finish_reason=None), REQ))                  # no candidate: SF4 by the scoring, not unlisted


if __name__ == "__main__":
    unittest.main()
