"""Disruption scenarios with the MOCK provider only (temporary folders, no network, no API key, no model call).
They pin what the CURRENT runner and integrity checker do with transport failures, anomalous responses, a very slow call (for example a computer that
slept), a killed run followed by a restart, and damaged run files. They decide no rule and implement no resume (patch P8 / MD-08 is not adopted).
The damaged-file tests were changed on 2026-10-14 together with s10_integrity (the owner authorised the fix): they now require a PROBLEM line instead of an exception.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_disruption_scenarios.py"""
import hashlib
import io
import json
import os
import shutil
import sys
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_integrity as IG  # noqa: E402
import s10_runner as RN  # noqa: E402
RN.FZ_INDEX_ENFORCED = False  # E-1 (12.55): these tests exercise other machinery with mock data; enforcement is tested in test_fz_enforcement.py
import s10_scoring as SC  # noqa: E402
import test_pilot_flow as PF  # noqa: E402
import test_step10 as T10  # noqa: E402

BUILD = T10.BUILD


def ledger_of(run):
    return [json.loads(l) for l in PF.rb(os.path.join(run, "ledger.jsonl")).decode().split("\n") if l]


def calls_of(run):
    return [json.loads(l) for l in PF.rb(os.path.join(run, "calls.jsonl")).decode().split("\n") if l]


class Base(PF.Base):
    def run_with(self, script, **kw):
        """Runs the 18-call pilot with a scripted mock provider; returns (run folder, provider, summary)."""
        self.n += 1
        ev, au = T10.TestRunner.files(self, "pilot_round_1", days=20 + self.n, calls=18)
        out = os.path.join(self.tmp, f"run{self.n}")
        prov = RN.MockProvider(script)
        with redirect_stdout(io.StringIO()):
            s = RN.run("pilot_round_1", BUILD, out, prov, evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False, **kw)
        return out, prov, s


class TestTransportFailuresAndAnomalousResponses(Base):
    def test_burst_of_transport_failures_is_recorded_once_each_and_never_repeated(self):
        seen = []

        def script(key, prompt):
            seen.append(key)
            n = len(seen)
            if n in (4, 5, 6):
                return {"http_status": None, "text": "", "error": "TimeoutError"}
            if n == 7:
                raise ConnectionError("simulated reset")          # escapes the provider: the runner records the class name
            return T10.good(key, prompt)

        out, prov, s = self.run_with(script)
        self.assertEqual((s["status"], s["recorded_calls"], prov.calls), ("RUN_COMPLETE", 18, 18))     # one provider invocation per planned call
        recs = calls_of(out)
        self.assertEqual(len({r["call_key"] for r in recs}), 18)
        self.assertEqual([r["error"] for r in recs[3:7]], ["TimeoutError"] * 3 + ["ConnectionError"])
        self.assertEqual(s["pilot_counts"]["counts"]["SF1"], 4)
        self.assertEqual(IG.verify_run(out, BUILD)[0], [])

    def test_anomalous_responses_are_recorded_classified_and_not_retried(self):
        cases = {
            "empty_200": {"http_status": 200, "text": "", "error": None, "model_returned": "gemini-3.5-flash-lite", "finish_reason": None},
            "max_tokens": {"http_status": 200, "text": "Constraint: x\nVerdict: satisfied", "error": None, "model_returned": "gemini-3.5-flash-lite", "finish_reason": "MAX_TOKENS"},
            "safety_block": {"http_status": 200, "text": "", "error": None, "model_returned": "gemini-3.5-flash-lite", "finish_reason": "SAFETY"},
            "http_500": {"http_status": 500, "text": "", "error": "HTTP 500"},
            "http_429": {"http_status": 429, "text": "", "error": "HTTP 429"},
            "no_verdict_line": {"http_status": 200, "text": "I think the plan is fine.", "error": None, "model_returned": "gemini-3.5-flash-lite", "finish_reason": "STOP"},
            "conflicting_verdicts": {"http_status": 200, "text": "Verdict: satisfied\nVerdict: violated", "error": None, "model_returned": "gemini-3.5-flash-lite", "finish_reason": "STOP"},
            "whitespace_only": {"http_status": 200, "text": " \n\t ", "error": None, "model_returned": "gemini-3.5-flash-lite", "finish_reason": "STOP"},
        }
        for name, bad in cases.items():
            with self.subTest(name):
                def script(key, prompt, bad=bad):
                    return dict(bad) if key.endswith(",1") and script.hit() else T10.good(key, prompt)
                state = {"n": 0}

                def hit():
                    state["n"] += 1
                    return state["n"] == 1                       # only the first repetition-1 call is anomalous
                script.hit = hit
                out, prov, s = self.run_with(script)
                self.assertEqual((s["status"], s["recorded_calls"], prov.calls), ("RUN_COMPLETE", 18, 18))
                first_bad = next(r for r in calls_of(out) if r["call_key"].endswith(",1"))
                self.assertIn(SC.classify(first_bad)["outcome"], ("SF", "UNP"), name)    # never counted as a verdict class
                self.assertEqual(IG.verify_run(out, BUILD)[0], [], name)


class TestSlowCall(Base):
    def test_a_400_second_call_is_recorded_and_flagged_but_does_not_stop_or_invalidate_the_run(self):
        clock = {"t": 0.0}
        seen = []

        def script(key, prompt):
            seen.append(key)
            clock["t"] += 400.0 if len(seen) == 3 else 1.0           # a stall, for example a computer that slept
            r = T10.good(key, prompt)
            del r["latency_ms"]                                       # force the runner's own wall-clock measurement
            return r

        out, prov, s = self.run_with(script, timer=lambda: clock["t"])
        self.assertEqual((s["status"], s["recorded_calls"]), ("RUN_COMPLETE", 18))
        recs = calls_of(out)
        self.assertEqual(recs[2]["latency_ms"], 400000)
        self.assertEqual(recs[0]["latency_ms"], 1000)
        P, st = IG.verify_run(out, BUILD)
        self.assertEqual(P, [])                                       # reported as a counter, not as a problem
        self.assertEqual(st["calls_with_latency_over_nominal_timeout_180s"], 1)


class TestKilledRunAndRestart(Base):
    def kill_at(self, at):
        seen = []

        def script(key, prompt):
            seen.append(key)
            if len(seen) == at:
                raise KeyboardInterrupt()
            return T10.good(key, prompt)
        self.n += 1
        ev, au = T10.TestRunner.files(self, "pilot_round_1", days=40 + self.n, calls=18)
        out = os.path.join(self.tmp, f"killed{self.n}")
        prov = RN.MockProvider(script)
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                RN.run("pilot_round_1", BUILD, out, prov, evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        return out, prov, ev, au

    def test_killed_run_keeps_a_verifiable_chain_but_cannot_be_reported_or_continued(self):
        out, prov, ev, au = self.kill_at(10)
        led = ledger_of(out)
        prev = "0" * 64
        for e in led:                                                  # the partial ledger is a valid hash chain up to its last entry
            self.assertEqual(e["prev"], prev)
            prev = hashlib.sha256(RN.cj(e)).hexdigest()
        done = {e["call_index"] for e in led if e["event"] == "classified"}
        self.assertEqual(done, set(range(1, 10)))                      # 9 calls fully recorded
        self.assertEqual(led[-1]["event"], "intent")                   # call 10 was attempted: its fate is unknown
        self.assertEqual(prov.calls, 10)
        self.assertEqual(IG.verify_run(out, BUILD)[0], ["not exactly one status marker"])
        with self.assertRaises(FileExistsError):                       # the same folder cannot be re-entered
            with redirect_stdout(io.StringIO()):
                RN.run("pilot_round_1", BUILD, out, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)

    def test_restart_in_a_new_folder_repeats_every_call_already_made(self):
        out, prov, ev, au = self.kill_at(10)
        again = RN.MockProvider(T10.good)
        new = os.path.join(self.tmp, "restart")
        with redirect_stdout(io.StringIO()):
            RN.run("pilot_round_1", BUILD, new, again, evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        first = [r["call_key"] for r in calls_of(out)]
        second = [r["call_key"] for r in calls_of(new)]
        self.assertEqual(first, second[:9])                            # same stable keys, same order, issued a second time
        self.assertEqual(prov.calls + again.calls, 10 + 18)            # 28 provider invocations for 18 planned calls
        self.assertEqual(IG.verify_run(new, BUILD)[0], [])             # the new folder is clean; nothing links it to the killed one

    def test_the_call_plan_is_deterministic_so_a_future_resume_could_rely_on_stable_keys(self):
        a = RN.plan_calls("pilot_round_1", BUILD)
        b = RN.plan_calls("pilot_round_1", BUILD)
        self.assertEqual(a, b)
        self.assertEqual(len({c["call_key"] for c in a}), 18)


class TestDamagedRunFilesAreReportedAsProblems(Base):
    """Fail-closed with a diagnosis (changed 2026-10-14; before, a truncated line or a missing raw file made verify_run raise an exception)."""

    def damaged(self, mutate):
        out, _, _ = self.run_with(T10.good)
        mutate(out)
        return out

    def problems(self, mutate):
        P, _ = IG.verify_run(self.damaged(mutate), BUILD)                # must return, never raise
        self.assertTrue(P)
        return P

    def test_truncated_last_line_of_calls_jsonl_is_reported(self):
        def m(d):
            p = os.path.join(d, "calls.jsonl")
            PF.wb(p, PF.rb(p)[:-40])
        self.assertTrue(any("calls.jsonl" in x and "truncated" in x for x in self.problems(m)))

    def test_truncated_last_line_of_the_ledger_is_reported(self):
        def m(d):
            p = os.path.join(d, "ledger.jsonl")
            PF.wb(p, PF.rb(p)[:-40])
        self.assertTrue(any("ledger.jsonl" in x and "truncated" in x for x in self.problems(m)))

    def test_deleted_raw_file_is_reported(self):
        P = self.problems(lambda d: os.remove(os.path.join(d, "raw", "00005.json")))
        self.assertTrue(any("raw file missing" in x and "5" in x for x in P))

    def test_missing_ledger_calls_file_or_raw_folder_are_reported(self):
        self.assertTrue(any("ledger.jsonl cannot be read" in x for x in self.problems(lambda d: os.remove(os.path.join(d, "ledger.jsonl")))))
        self.assertTrue(any("calls.jsonl cannot be read" in x for x in self.problems(lambda d: os.remove(os.path.join(d, "calls.jsonl")))))
        self.assertTrue(any("raw" in x for x in self.problems(lambda d: shutil.rmtree(os.path.join(d, "raw")))))

    def test_garbage_in_the_status_marker_is_reported(self):
        self.assertEqual(self.problems(lambda d: PF.wb(os.path.join(d, "RUN_COMPLETE"), b"not json")), ["the status marker cannot be read as a run summary"])

    def test_extra_raw_file_and_missing_marker_are_reported_as_problems(self):
        self.assertIn("raw folder size", IG.verify_run(self.damaged(lambda d: PF.wb(os.path.join(d, "raw", "00099.json"), b"{}")), BUILD)[0])
        self.assertEqual(IG.verify_run(self.damaged(lambda d: os.remove(os.path.join(d, "RUN_COMPLETE"))), BUILD)[0], ["not exactly one status marker"])


if __name__ == "__main__":
    unittest.main()
