"""Tests of scoring, analysis and the runner gates/loop with SYNTHETIC fixtures and a mock provider. No model call, no network.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_step10.py"""
import datetime
import hashlib
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
import s10_analysis as AN  # noqa: E402
import s10_runner as RN  # noqa: E402
RN.FZ_INDEX_ENFORCED = False  # E-1 (12.55): these tests exercise other machinery with mock data; enforcement is tested in test_fz_enforcement.py
import s10_scoring as SC  # noqa: E402

BUILD = os.path.join(RD, "step8_output", "step8_build_7a6bbe465c66")
SECRET = "RESPONSE-CONTENT-MARKER-XYZ"


def rb(p):
    with open(p, "rb") as f:
        return f.read()


def wj(p, o):
    with open(p, "xb") as f:
        f.write(RN.cj(o))


def good(call_key, prompt):
    cond = call_key.split(",")[1]
    return {"http_status": 200, "text": f"Constraint: x\nVerdict: {SC.EXPECTED[cond]}\nComments: {SECRET}", "error": None,
            "finish_reason": "STOP", "model_returned": "gemini-3.5-flash-lite", "usage": {"totalTokenCount": 900}, "latency_ms": 1000}


class TestScoring(unittest.TestCase):
    def rec(self, text="", **kw):
        d = {"response_text": text, "error": None, "http_status": 200, "finish_reason": "STOP"}
        d.update(kw)
        return d

    def test_classes(self):
        c = SC.classify
        self.assertEqual(c(self.rec("Verdict: satisfied"))["outcome"], "satisfied")
        self.assertEqual(c(self.rec("Verdict: Violated."))["outcome"], "violated")
        self.assertEqual(c(self.rec("Verdict: unclear"))["outcome"], "unclear")
        self.assertEqual(c(self.rec("no verdict here"))["outcome"], "UNP")
        self.assertEqual(c(self.rec("Verdict: satisfied\nVerdict: violated"))["outcome"], "UNP")
        self.assertEqual(c(self.rec("Verdict: maybe"))["outcome"], "UNP")

    def test_sf_precedence_and_flags(self):
        c = SC.classify
        self.assertEqual(c(self.rec("", error="HTTP 503", http_status=503))["flags"], ["SF1"])
        self.assertEqual(c(self.rec("   \n"))["flags"], ["SF2"])
        self.assertEqual(c(self.rec("Verdict: satisfied", finish_reason="MAX_TOKENS"))["outcome"], "SF")      # SF only, never the parse
        self.assertEqual(c(self.rec("", finish_reason="SAFETY"))["flags"], ["SF4"])
        self.assertEqual(c(self.rec("", finish_reason=None))["flags"], ["SF4"])                                # no candidate
        self.assertEqual(c(self.rec("", finish_reason="MAX_TOKENS"))["flags"], ["SF3", "SF2"])                 # several flags, provisional order
        self.assertIsNone(c(self.rec("Verdict: satisfied"))["primary_sf"])

    def test_pilot_counts_are_disjoint_and_content_free(self):
        recs = [self.rec("Verdict: satisfied"), self.rec("", error="x", http_status=None), self.rec("junk"), self.rec("", finish_reason="MAX_TOKENS")]
        r = SC.pilot_counts(recs)
        self.assertEqual(r["counts"], {"SF1": 1, "SF2": 0, "SF3": 1, "SF4": 0, "SF5": 0, "UNP": 1})
        self.assertEqual(r["multi_flag_calls"], 1)
        self.assertNotIn("match", json.dumps(r))                                                                # no agreement with ground truth (13.7)


class TestAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = json.loads(rb(os.path.join(BUILD, "main", "registry.json")))["templates"]

    def rows(self, fn):
        out = []
        for t in self.reg:
            for c in "ABCD":
                for r in (1, 2):
                    outc = fn(t, c, r)
                    out.append({"call_key": f"{t['id']},{c},{r}", "template_id": t["id"], "condition": c, "repetition": r, "family": t["family"],
                                "base_index": t["base_index"], "class": t["class"], "outcome": outc, "flags": ["SF2"] if outc == "SF" else [],
                                "match": outc == SC.EXPECTED[c]})
        return out

    def test_perfect_model(self):
        r = AN.analyze(self.rows(lambda t, c, r: SC.EXPECTED[c]), self.reg, 2, 20261014, 200, "0.95", 10)
        self.assertEqual(r["status"], "COMPLETE")
        self.assertEqual(len(r["cells"]), 12)
        for name, cell in r["cells"].items():
            self.assertEqual(cell["estimate_cluster_mean"]["match"], "1", name)
        self.assertEqual(r["contrasts"]["MIL"]["MIL:contrast C-A"]["point"], "0")
        self.assertEqual(r["cells"]["MIL:A"]["planned_calls"], 104 // 2 * 2 * 1 * 1 * 1 if False else r["cells"]["MIL:A"]["recorded_calls"])

    def test_sf_stays_in_the_denominator_and_is_reported_apart(self):
        def fn(t, c, r):
            return "SF" if (t["id"] == "MIL-001" and c == "A" and r == 1) else SC.EXPECTED[c]
        r = AN.analyze(self.rows(fn), self.reg, 2, 1, 50, "0.95", 10)
        cell = r["cells"]["MIL:A"]
        self.assertEqual(cell["counts"]["SF"], 1)
        self.assertEqual(cell["recorded_calls"], 208)
        self.assertEqual(cell["proportions_over_recorded_calls"]["SF"], str(__import__("fractions").Fraction(1, 208)))
        self.assertNotEqual(cell["estimate_cluster_mean"]["match"], "1")

    def test_incomplete_cell_gives_counts_only(self):
        rows = [x for x in self.rows(lambda t, c, r: SC.EXPECTED[c]) if not (x["template_id"] == "PAC-001" and x["condition"] == "B" and x["repetition"] == 2)]
        r = AN.analyze(rows, self.reg, 2, 1, 50, "0.95", 10)
        self.assertEqual(r["status"], "INCOMPLETE")
        self.assertIsNone(r["cells"]["PAC:B:" + next(t["class"] for t in self.reg if t["id"] == "PAC-001")]["estimate"])
        self.assertIn("INCOMPLETE", json.dumps(r["contrasts"]["PAC"]))
        self.assertEqual(r["cells"]["MIL:A"]["complete"], True)

    def test_bootstrap_is_deterministic_and_seed_dependent(self):
        f = lambda t, c, r: SC.EXPECTED[c] if (t["base_index"] + r) % 3 else "violated" if SC.EXPECTED[c] == "satisfied" else "satisfied"
        a = AN.analyze(self.rows(f), self.reg, 2, 7, 100, "0.95", 10)
        b = AN.analyze(self.rows(f), self.reg, 2, 7, 100, "0.95", 10)
        c = AN.analyze(self.rows(f), self.reg, 2, 8, 100, "0.95", 10)
        self.assertEqual(a, b)
        self.assertNotEqual(a["contrasts"], c["contrasts"])

    def test_small_family_reports_no_interval(self):
        pil = json.loads(rb(os.path.join(BUILD, "pilot", "registry.json")))["templates"]
        rows = []
        for t in pil:
            for c in "ABCD":
                for r in (1, 2):
                    rows.append({"call_key": "k", "template_id": t["id"], "condition": c, "repetition": r, "family": t["family"], "base_index": t["base_index"],
                                 "class": t["class"], "outcome": SC.EXPECTED[c], "flags": [], "match": True})
        res = AN.analyze(rows, pil, 2, 1, 50, "0.95", 10)
        self.assertIsNone(res["contrasts"]["MIL"]["MIL:A"]["interval"])


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="s10_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bh = RN.sha(rb(os.path.join(BUILD, "manifest.json")))

    def files(self, scope, days=7, adjud="AGREE", calls=None, cost_assumption=1016, max_chars=1016):
        ev, au = os.path.join(self.tmp, f"ev_{scope}_{days}.json"), os.path.join(self.tmp, f"au_{scope}_{days}.json")
        wj(ev, {"human_blind_check": {"completed_utc": (datetime.datetime(2026, 10, 20, 9) + datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"), "last_registry_view_utc": "2026-10-20T09:00:00Z",
                                       "coverage_percent": 100, "worksheet_sha256": "a" * 64, "adjudication": adjud}, "build_manifest_sha256": self.bh})
        n = calls if calls is not None else {"pilot_round_1": 18, "main": 2496}[scope]
        wj(au, {"authorized_by": "owner", "scope": scope, "max_calls": n, "cost_ceiling_usd": 100, "input_tokens_per_call_assumed": cost_assumption, "max_input_characters": max_chars})
        return ev, au

    def test_gates_are_closed_in_the_real_repository_state(self):
        with self.assertRaises(RN.GateClosed) as cm:
            RN.check_gates("pilot_round_1", BUILD, os.path.join(self.tmp, "none.json"), os.path.join(self.tmp, "none2.json"), verify_build=False)
        self.assertTrue(any("Step 9 evidence" in r for r in cm.exception.reasons))
        self.assertTrue(any("authorization" in r for r in cm.exception.reasons))
        self.assertEqual(RN.main(["--scope", "pilot_round_1", "--build", BUILD, "--out", os.path.join(self.tmp, "o"), "--evidence", os.path.join(self.tmp, "x"),
                                  "--authorization", os.path.join(self.tmp, "y"), "--live"]), 2)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "o")))

    def test_six_day_delay_closes_the_gate_seven_opens_it(self):
        ev, au = self.files("pilot_round_1", days=6)
        with self.assertRaises(RN.GateClosed) as cm:
            RN.check_gates("pilot_round_1", BUILD, ev, au, verify_build=False)
        self.assertTrue(any("delay" in r for r in cm.exception.reasons))
        ev, au = self.files("pilot_round_1", days=7)
        self.assertEqual(RN.check_gates("pilot_round_1", BUILD, ev, au, verify_build=False)["model_id"], "gemini-3.5-flash-lite")

    def test_bad_adjudication_wrong_build_wrong_scope_and_cost(self):
        ev, au = self.files("pilot_round_1", days=8, adjud="REJECT")
        with self.assertRaises(RN.GateClosed):
            RN.check_gates("pilot_round_1", BUILD, ev, au, verify_build=False)
        ev, au = self.files("pilot_round_1", days=9, calls=17)
        with self.assertRaises(RN.GateClosed):
            RN.check_gates("pilot_round_1", BUILD, ev, au, verify_build=False)
        ev, au = self.files("pilot_round_1", days=10, cost_assumption=10 ** 8)
        with self.assertRaises(RN.GateClosed) as cm:
            RN.check_gates("pilot_round_1", BUILD, ev, au, verify_build=False)
        self.assertTrue(any("cost" in r for r in cm.exception.reasons))

    def test_full_build_verification_is_part_of_the_gate(self):
        ev, au = self.files("pilot_round_1", days=11)
        RN.check_gates("pilot_round_1", BUILD, ev, au, verify_build=True)            # real verifier + Implementation B on the real build

    def test_pilot_run_with_mock_provider(self):
        ev, au = self.files("pilot_round_1", days=12)
        out = os.path.join(self.tmp, "pilot_out")
        buf = io.StringIO()
        waits = []
        with redirect_stdout(buf):
            s = RN.run("pilot_round_1", BUILD, out, RN.MockProvider(good), evidence_path=ev, auth_path=au, sleeper=waits.append, verify_build=False)
        self.assertEqual((s["status"], s["recorded_calls"], s["planned_calls"]), ("RUN_COMPLETE", 18, 18))
        self.assertEqual(len(waits), 17)
        self.assertEqual(set(waits), {1})
        self.assertNotIn(SECRET, buf.getvalue())
        self.assertEqual(s["pilot_counts"]["counts"], {"SF1": 0, "SF2": 0, "SF3": 0, "SF4": 0, "SF5": 0, "UNP": 0})
        self.assertNotIn("match", json.dumps(s["pilot_counts"]))
        led = [json.loads(l) for l in rb(os.path.join(out, "ledger.jsonl")).decode().split("\n") if l]
        self.assertEqual([e["event"] for e in led[:4]], ["start", "intent", "raw", "classified"])      # ledger format 2: provenance first (before any call)
        self.assertEqual((led[0]["call_index"], led[0]["provider"], led[0]["scope"]), (0, "MockProvider", "pilot_round_1"))
        self.assertNotIn("KEY", json.dumps(led[0]).upper().replace("MAX_OUTPUT_TOKENS", ""))   # no secret or key material in the provenance entry
        prev = "0" * 64
        for e in led:                                                                # hash chain
            self.assertEqual(e["prev"], prev)
            prev = RN.sha(RN.cj(e))
        self.assertEqual(prev, s["ledger_final_hash"])
        self.assertEqual(len(os.listdir(os.path.join(out, "raw"))), 18)
        with self.assertRaises(FileExistsError):
            RN.run("pilot_round_1", BUILD, out, RN.MockProvider(good), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)

    def test_pilot_records_failures_without_retry(self):
        ev, au = self.files("pilot_round_1", days=13)
        seq = []

        def bad(k, p):
            seq.append(k)
            if len(seq) == 1:
                return {"http_status": 503, "text": "", "error": "HTTP 503"}
            if len(seq) == 2:
                return {"http_status": 200, "text": "", "error": None, "model_returned": "gemini-3.5-flash-lite", "finish_reason": "STOP"}
            if len(seq) == 3:
                raise RuntimeError("boom")
            return good(k, p)
        s = RN.run("pilot_round_1", BUILD, os.path.join(self.tmp, "p2"), RN.MockProvider(bad), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        self.assertEqual(len(seq), 18)                                               # no retry: each call exactly once
        self.assertEqual(s["pilot_counts"]["counts"]["SF1"], 2)
        self.assertEqual(s["pilot_counts"]["counts"]["SF2"], 1)

    def main_files(self, days):
        ev, au = self.files("main", days=days)
        reg = json.loads(rb(os.path.join(BUILD, "main", "registry.json")))["templates"]
        text = RN.D.call_list([t["id"] for t in reg], 2)
        rec = os.path.join(self.tmp, f"rec_{days}.json")
        wj(rec, {"final_decision": "PASS", "seed": 20261030, "call_list_sha256": RN.sha(text.encode("ascii"))})
        return ev, au, rec

    def test_main_run_halts_after_c_stop_consecutive_failures(self):
        ev, au, rec = self.main_files(14)
        s = RN.run("main", BUILD, os.path.join(self.tmp, "m1"), RN.MockProvider(lambda k, p: {"http_status": 503, "text": "", "error": "HTTP 503"}),
                   evidence_path=ev, auth_path=au, pilot_record_path=rec, sleeper=lambda x: None, verify_build=False)
        self.assertEqual((s["status"], s["recorded_calls"]), ("RUN_INCOMPLETE", 5))
        self.assertIn("13.8", s["halt"])

    def test_main_run_requires_a_pass_record_and_matching_call_list(self):
        ev, au, rec = self.main_files(15)
        bad = os.path.join(self.tmp, "badrec.json")
        wj(bad, {"final_decision": "STOP-U", "seed": 1, "call_list_sha256": "0" * 64})
        with self.assertRaises(RN.GateClosed):
            RN.check_gates("main", BUILD, ev, au, bad, verify_build=False)
        wj(os.path.join(self.tmp, "badhash.json"), {"final_decision": "PASS", "seed": 1, "call_list_sha256": "0" * 64})
        with self.assertRaises(RN.GateClosed):
            RN.plan_calls("main", BUILD, os.path.join(self.tmp, "badhash.json"))

    def test_complete_mock_main_run_scores_analyses_and_is_reproducible(self):
        ev, au, rec = self.main_files(16)
        out = os.path.join(self.tmp, "m2")
        s = RN.run("main", BUILD, out, RN.MockProvider(good), evidence_path=ev, auth_path=au, pilot_record_path=rec, sleeper=lambda x: None, verify_build=False)
        self.assertEqual((s["status"], s["recorded_calls"]), ("RUN_COMPLETE", 2496))
        plan = RN.plan_calls("main", BUILD, rec)
        self.assertEqual(len(plan), 2496)
        self.assertEqual(len({p["call_key"] for p in plan}), 2496)
        self.assertNotEqual([p["call_key"] for p in plan[:20]], sorted(p["call_key"] for p in plan)[:20])     # shuffled
        self.assertEqual(plan, RN.plan_calls("main", BUILD, rec))                                           # deterministic
        reg = json.loads(rb(os.path.join(BUILD, "main", "registry.json")))["templates"]
        meta = {t["id"]: t for t in reg}
        recs = [json.loads(l) for l in rb(os.path.join(out, "calls.jsonl")).decode().split("\n") if l]
        rows = SC.call_table(recs, meta)
        res = AN.analyze(rows, reg, 2, 20261030, 100, "0.95", 10)
        self.assertEqual(res["status"], "COMPLETE")
        self.assertTrue(all(c["estimate_cluster_mean"]["match"] == "1" for c in res["cells"].values()))
        import s10_report as REP
        outr = os.path.join(self.tmp, "rep")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = REP.main(["--run", out, "--build", BUILD, "--decision-record", rec, "--out", outr])
        self.assertEqual(code, 0)
        self.assertNotIn(SECRET, buf.getvalue())
        self.assertNotIn(SECRET, rb(os.path.join(outr, "RESULTS.md")).decode())
        self.assertEqual(len(os.listdir(outr)), 3)
        with self.assertRaises(FileExistsError):
            REP.main(["--run", out, "--build", BUILD, "--decision-record", rec, "--out", outr])


class TestSafety(unittest.TestCase):
    def test_provider_and_modules_do_not_leak_content_or_keys(self):
        for n in ("s10_scoring.py", "s10_analysis.py"):
            src = rb(os.path.join(HERE, n)).decode()
            for bad in ("import urllib", "import socket", "import http", "GEMINI_API_KEY"):
                self.assertNotIn(bad, src, (n, bad))
        run_src = rb(os.path.join(HERE, "s10_runner.py")).decode()
        top = [l for l in run_src.splitlines() if l.startswith(("import urllib", "from urllib", "import socket", "import http"))]
        self.assertEqual(top, [])                                                    # no module-level network import: urllib is imported only inside the provider methods
        inside = [l for l in run_src.splitlines() if "import urllib" in l and l.startswith(" ")]
        self.assertTrue(inside and all(l.startswith("        ") for l in inside), inside)
        self.assertNotIn("print(rec", run_src)
        self.assertNotIn("response_text\"]", run_src.split("def main")[1])


if __name__ == "__main__":
    unittest.main()
