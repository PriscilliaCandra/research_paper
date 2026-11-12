"""Tests of run integrity and the pilot report / decision glue with SYNTHETIC mock runs. No model call.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_pilot_flow.py"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_integrity as IG  # noqa: E402
import s10_runner as RN  # noqa: E402
RN.FZ_INDEX_ENFORCED = False  # E-1 (12.55): these tests exercise other machinery with mock data; enforcement is tested in test_fz_enforcement.py
import s11_pilot as PL  # noqa: E402
import test_step10 as T10  # noqa: E402

BUILD = T10.BUILD
HASH = "a" * 64


def rb(p):
    with open(p, "rb") as f:
        return f.read()


def wb(p, d):
    with open(p, "wb") as f:
        f.write(d)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pf_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bh = RN.sha(rb(os.path.join(BUILD, "manifest.json")))
        self.n = 0

    def run_pilot(self, provider_fn, scope="pilot_round_1"):
        self.n += 1
        ev, au = T10.TestRunner.files(self, scope, days=7 + self.n, calls=18)
        out = os.path.join(self.tmp, f"run{self.n}")
        with redirect_stdout(io.StringIO()):
            RN.run(scope, BUILD, out, RN.MockProvider(provider_fn), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        return out


class TestIntegrity(Base):
    def test_clean_run_is_ok(self):
        run = self.run_pilot(T10.good)
        P, st = IG.verify_run(run, BUILD)
        self.assertEqual(P, [])
        self.assertEqual((st["recorded"], st["status"]), (18, "RUN_COMPLETE"))

    def test_tampering_is_detected(self):
        run = self.run_pilot(T10.good)
        p = os.path.join(run, "raw", "00003.json")
        orig = rb(p)
        wb(p, orig.replace(b"satisfied", b"violated", 1) if b"satisfied" in orig else orig.replace(b"violated", b"satisfied", 1))
        self.assertTrue(any("raw hash differs" in x for x in IG.verify_run(run, BUILD)[0]))
        wb(p, orig)
        self.assertEqual(IG.verify_run(run, BUILD)[0], [])
        cp = os.path.join(run, "calls.jsonl")
        lines = rb(cp).split(b"\n")
        wb(cp, b"\n".join(lines[:-2]) + b"\n")                       # a missing line
        self.assertTrue(IG.verify_run(run, BUILD)[0])

    def test_duplicate_call_and_wrong_prompt_hash(self):
        run = self.run_pilot(T10.good)
        cp = os.path.join(run, "calls.jsonl")
        recs = [json.loads(l) for l in rb(cp).decode().split("\n") if l]
        recs[1]["prompt_sha256"] = "0" * 64
        wb(cp, ("\n".join(json.dumps(r, sort_keys=True) for r in recs) + "\n").encode())
        self.assertTrue(any("prompt hash" in x or "differs" in x for x in IG.verify_run(run, BUILD)[0]))

    def test_incomplete_main_run_is_consistent(self):
        ev, au = T10.TestRunner.files(self, "main", days=30)
        reg = json.loads(rb(os.path.join(BUILD, "main", "registry.json")))["templates"]
        text = RN.D.call_list([t["id"] for t in reg], 2)
        rec = os.path.join(self.tmp, "rec.json")
        T10.wj(rec, {"final_decision": "PASS", "seed": 5, "call_list_sha256": RN.sha(text.encode("ascii"))})
        out = os.path.join(self.tmp, "mainrun")
        with redirect_stdout(io.StringIO()):
            RN.run("main", BUILD, out, RN.MockProvider(lambda k, p: {"http_status": 503, "text": "", "error": "HTTP 503"}), evidence_path=ev, auth_path=au,
                   pilot_record_path=rec, sleeper=lambda x: None, verify_build=False)
        P, st = IG.verify_run(out, BUILD)
        self.assertEqual(P, [])
        self.assertEqual((st["status"], st["recorded"]), ("RUN_INCOMPLETE", 5))


class TestPilotGlue(Base):
    def test_all_good_is_pass_and_record_is_written_once(self):
        run = self.run_pilot(T10.good)
        outj = os.path.join(self.tmp, "rep.json")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(PL.main(["report", "--run", run, "--round", "1", "--out", outj]), 0)
        rep = json.loads(rb(outj))
        self.assertEqual(rep["round_result"]["outcome"], "PASS")
        self.assertNotIn("match", json.dumps(rep["counts"]))
        rec = os.path.join(self.tmp, "rec.json")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(PL.main(["decide", "--run", run, "--round", "1", "--fz-hash", HASH, "--record", rec]), 0)
            self.assertEqual(PL.main(["decide", "--run", run, "--round", "1", "--fz-hash", HASH, "--record", rec]), 4)      # first run stands
        r = json.loads(rb(rec))
        self.assertEqual((r["final_decision"], r["call_list_lines"]), ("PASS", 2496))

    def test_round_one_fail_s_is_not_final(self):
        calls = []

        def fn(k, p):
            calls.append(k)
            return {"http_status": 200, "text": "", "error": None, "finish_reason": "STOP", "model_returned": "gemini-3.5-flash-lite"} if len(calls) == 1 else T10.good(k, p)
        run = self.run_pilot(fn)
        rec = os.path.join(self.tmp, "rec.json")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(PL.main(["decide", "--run", run, "--round", "1", "--fz-hash", HASH, "--record", rec]), 3)
        self.assertFalse(os.path.exists(rec))

    def test_round_two_fail_s_stops_and_fail_u_is_stop_u(self):
        calls = []

        def empty_first(k, p):
            calls.append(k)
            return {"http_status": 200, "text": "", "error": None, "finish_reason": "STOP", "model_returned": "gemini-3.5-flash-lite"} if len(calls) == 1 else T10.good(k, p)
        run = self.run_pilot(empty_first, scope="pilot_round_2")
        rec = os.path.join(self.tmp, "r2.json")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(PL.main(["decide", "--run", run, "--round", "2", "--fz-hash", HASH, "--record", rec]), 0)
        self.assertEqual(json.loads(rb(rec))["final_decision"], "STOP")
        junk = self.run_pilot(lambda k, p: {"http_status": 200, "text": "no verdict", "error": None, "finish_reason": "STOP", "model_returned": "gemini-3.5-flash-lite"})
        rec2 = os.path.join(self.tmp, "r3.json")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(PL.main(["decide", "--run", junk, "--round", "1", "--fz-hash", HASH, "--record", rec2]), 0)
        self.assertEqual(json.loads(rb(rec2))["final_decision"], "STOP-U")

    def test_incomplete_or_mismatched_run_is_refused(self):
        run = self.run_pilot(T10.good)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(PL.main(["report", "--run", run, "--round", "2", "--out", os.path.join(self.tmp, "x.json")]), 2)


if __name__ == "__main__":
    unittest.main()
