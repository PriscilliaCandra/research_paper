"""P8 resume, provenance, cost stop and fail-closed behaviour -- MOCK provider only (temporary folders, no network, no API key, no model call).
These tests check the local implementation in s10_resume.py / s10_runner.py / s10_integrity.py. They do not show that resume is ALLOWED: MD-08 is still pending
owner/advisor; the tests pin what the code does and refuses.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_resume.py"""
import io
import json
import os
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_integrity as IG  # noqa: E402
import s10_resume as RS  # noqa: E402
import s10_runner as RN  # noqa: E402
RN.FZ_INDEX_ENFORCED = False  # E-1 (12.55): these tests exercise other machinery with mock data; enforcement is tested in test_fz_enforcement.py
import test_ledger_resilience as LR  # noqa: E402
import test_pilot_flow as PF  # noqa: E402
import test_step10 as T10  # noqa: E402

BUILD = T10.BUILD
SECRET = "SECRET-KEY-VALUE-0123456789"


def ledger_of(run):
    return [json.loads(l) for l in PF.rb(os.path.join(run, "ledger.jsonl")).decode().split("\n") if l]


def calls_of(run):
    return [json.loads(l) for l in PF.rb(os.path.join(run, "calls.jsonl")).decode().split("\n") if l]


class OtherProvider(RN.MockProvider):
    pass


SHIPPED_POLICY = (RS.HTTP_STATUS_ALLOWLIST, RS.RATIFIED_TRANSPORT_ERROR_CLASSES, RS.PROCESS_DEATH_CONTRACT_ENFORCED)     # values in the shipped code, read before any test patches them
TEST_ONLY_RATIFIED = frozenset({"TimeoutError"})      # TEST-ONLY stand-in so that positive paths can run. It is NOT a ratification: the owner has ratified no class.


class Base(PF.Base):
    def setUp(self):
        super().setUp()
        self.k = 0
        # MECHANICS ONLY: no exception class is ratified and the process-death contract needs a ratified class. The tests of this base class exercise the reconcile /
        # chain / counter / lock machinery with a TEST-ONLY ratified class and the contract check switched off; they assert nothing about policy and verify no policy.
        # ProductionPolicyBase below restores the shipped constants (contract enforced, nothing ratified) and TEST-ONLY ratified set only where a positive path needs one.
        for name, val in (("RATIFIED_TRANSPORT_ERROR_CLASSES", TEST_ONLY_RATIFIED), ("PROCESS_DEATH_CONTRACT_ENFORCED", False)):
            pa = mock.patch.object(RS, name, val)
            pa.start()
            self.addCleanup(pa.stop)

    # ---- building interrupted runs -------------------------------------------------------------------------------------------------
    def pilot_files(self):
        self.k += 1
        return T10.TestRunner.files(self, "pilot_round_1", days=30 + self.k, calls=18)

    def interrupted_pilot(self, kill_at=None, patch=None, extra_env=None):
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, f"int{self.k}")
        seen = []

        def script(key, prompt):
            seen.append(key)
            if kill_at is not None and patch is None and len(seen) == kill_at:
                raise KeyboardInterrupt()
            return T10.good(key, prompt)
        prov = RN.MockProvider(script)
        ctx = patch if patch is not None else mock.patch.dict(os.environ, extra_env or {})
        with ctx, redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                RN.run("pilot_round_1", BUILD, out, prov, evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        return out, ev, au, prov

    @staticmethod
    def kill_on_event(event, index):
        orig = RN.RunWriter.ledger

        def ledger(self_, entry):
            if entry.get("event") == event and entry.get("call_index") == index:
                raise KeyboardInterrupt()
            return orig(self_, entry)
        return mock.patch.object(RN.RunWriter, "ledger", ledger)

    @staticmethod
    def kill_on_calls_write(n):
        orig_init = RN.RunWriter.__init__

        class Boom:
            def __init__(self, f):
                self.f, self.count = f, 0

            def write(self, b):
                self.count += 1
                if self.count == n:
                    raise KeyboardInterrupt()
                return self.f.write(b)

            def flush(self):
                self.f.flush()

            def fileno(self):
                return self.f.fileno()

        def init(self_, out_dir, led, calls, prev):
            orig_init(self_, out_dir, led, Boom(calls), prev)
        return mock.patch.object(RN.RunWriter, "__init__", init)

    # ---- resume helpers -------------------------------------------------------------------------------------------------------------
    @staticmethod
    def head_of(run):
        lines = [l for l in PF.rb(os.path.join(run, "ledger.jsonl")).split(b"\n") if l]
        return RN.sha(lines[-1] + b"\n")

    def write_ra(self, run, name="ra.json", scope="pilot_round_1", total=18, basis="process_interruption", **over):
        led = ledger_of(run)
        ra = {"authorized_by": "owner", "scope": scope, "run_id": led[0]["run_id"], "ledger_head_sha256": self.head_of(run), "resume_basis": basis,
              "acknowledges_unspecified_basis": basis == "process_interruption", "remaining_calls": total - len(os.listdir(os.path.join(run, "raw"))),
              "cost_ceiling_usd": 100}
        ra.update(over)
        p = os.path.join(self.tmp, f"{name}.{self.k}.json")
        PF.wb(p, (json.dumps(ra, sort_keys=True) + "\n").encode())
        return p

    def resume(self, run, ev, au, ra, prov, **kw):
        with redirect_stdout(io.StringIO()):
            return RS.resume(run, BUILD, prov, evidence_path=ev, auth_path=au, resume_auth_path=ra, sleeper=lambda x: None, verify_build=False, **kw)

    def assert_refused(self, run, ev, au, ra, text, prov=None, **kw):
        prov = prov or RN.MockProvider(T10.good)
        before = LR.tree_hashes(run)
        with self.assertRaises(RS.ResumeRefused) as cm:
            self.resume(run, ev, au, ra, prov, **kw)
        self.assertIn(text, " | ".join(cm.exception.reasons))
        self.assertEqual(prov.calls, 0)                                    # no provider call
        self.assertEqual(LR.tree_hashes(run), before)                      # nothing changed
        return cm.exception


class TestProvenance(Base):
    def test_provenance_is_on_the_ledger_before_the_first_call_finishes_and_holds_no_secret(self):
        out, ev, au, prov = self.interrupted_pilot(kill_at=1, extra_env={"GEMINI_API_KEY": SECRET})
        led = ledger_of(out)
        self.assertEqual([(e["event"], e["call_index"]) for e in led], [("start", 0), ("intent", 1)])        # stopped inside the first call
        s = led[0]
        self.assertEqual((s["scope"], s["planned_calls"], s["provider"], s["ledger_format"]), ("pilot_round_1", 18, "MockProvider", 2))
        self.assertEqual(s["build_manifest_sha256"], RN.file_sha(os.path.join(BUILD, "manifest.json")))
        self.assertEqual(s["authorization"]["sha256"], RN.file_sha(au))
        self.assertEqual(s["evidence_sha256"], RN.file_sha(ev))
        self.assertEqual(s["fz_file_sha256"], RN.file_sha(RN.FZ_PATH))
        self.assertEqual(s["settings"]["model_id"], "gemini-3.5-flash-lite")
        self.assertEqual(len(s["run_id"]), 16)
        for d, _, fs in os.walk(out):
            for f in fs:
                self.assertNotIn(SECRET.encode(), PF.rb(os.path.join(d, f)))                                    # no key anywhere in the run folder

    def test_a_clean_run_has_provenance_and_passes_integrity(self):
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "clean")
        with redirect_stdout(io.StringIO()):
            s = RN.run("pilot_round_1", BUILD, out, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        self.assertEqual((s["ledger_format"], s["resumes"]), (2, []))
        P, st = IG.verify_run(out, BUILD)
        self.assertEqual(P, [])
        self.assertEqual((st["provenance_present"], st["resumes"]), (True, 0))

    def test_integrity_flags_a_format_2_run_without_its_provenance_entry(self):
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "clean2")
        with redirect_stdout(io.StringIO()):
            RN.run("pilot_round_1", BUILD, out, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        p = os.path.join(out, "ledger.jsonl")
        lines = [l for l in PF.rb(p).split(b"\n") if l]
        PF.wb(p, b"\n".join(lines[1:]) + b"\n")
        P, _ = IG.verify_run(out, BUILD)
        self.assertTrue(any("provenance" in x for x in P) and any("chain" in x for x in P))


class TestResumeAfterProcessInterruption(Base):
    def test_intent_without_a_persisted_record_is_never_repeated_and_the_resume_is_refused(self):
        out, ev, au, _ = self.interrupted_pilot(kill_at=4)
        ra = self.write_ra(out)
        e = self.assert_refused(out, ev, au, ra, "attempted, no record")
        self.assertIn("MD-08", " ".join(e.reasons))

    def test_raw_saved_but_ledger_raw_entry_missing_is_reconciled_without_calling_again(self):
        out, ev, au, prov1 = self.interrupted_pilot(patch=self.kill_on_event("raw", 5))
        self.assertTrue(os.path.exists(os.path.join(out, "raw", "00005.json")))
        self.assertNotIn(("raw", 5), [(e["event"], e["call_index"]) for e in ledger_of(out)])
        raw5 = LR.tree_hashes(out)["raw\\00005.json" if os.sep == "\\" else "raw/00005.json"]
        prov2 = RN.MockProvider(T10.good)
        s = self.resume(out, ev, au, self.write_ra(out), prov2)
        self.assertEqual((s["status"], s["recorded_calls"]), ("RUN_COMPLETE", 18))
        self.assertEqual((prov1.calls, prov2.calls), (5, 13))                      # call 5 reached the provider once and is not called again
        self.assertEqual(LR.tree_hashes(out)["raw\\00005.json" if os.sep == "\\" else "raw/00005.json"], raw5)   # the persisted record is untouched
        ev5 = [(e["event"], e["call_index"]) for e in ledger_of(out) if e.get("call_index") in (5,) or e["event"] in ("resume", "reconcile")]
        self.assertEqual(ev5, [("intent", 5), ("resume", 6), ("reconcile", 5), ("raw", 5), ("classified", 5)])
        self.assertEqual(len({c["call_key"] for c in calls_of(out)}), 18)
        P, st = IG.verify_run(out, BUILD)
        self.assertEqual(P, [])
        self.assertEqual((st["resumes"], st["reconciled_calls"]), (1, [5]))

    def test_calls_line_missing_is_reconciled(self):
        out, ev, au, prov1 = self.interrupted_pilot(patch=self.kill_on_calls_write(5))
        self.assertEqual(len(calls_of(out)), 4)
        self.assertIn(("raw", 5), [(e["event"], e["call_index"]) for e in ledger_of(out)])
        prov2 = RN.MockProvider(T10.good)
        s = self.resume(out, ev, au, self.write_ra(out), prov2)
        self.assertEqual((s["status"], prov1.calls + prov2.calls), ("RUN_COMPLETE", 18))
        self.assertEqual(IG.verify_run(out, BUILD)[0], [])

    def test_classified_entry_missing_is_reconciled(self):
        out, ev, au, prov1 = self.interrupted_pilot(patch=self.kill_on_event("classified", 5))
        prov2 = RN.MockProvider(T10.good)
        s = self.resume(out, ev, au, self.write_ra(out), prov2)
        self.assertEqual((s["status"], prov1.calls + prov2.calls), ("RUN_COMPLETE", 18))
        self.assertEqual(IG.verify_run(out, BUILD)[0], [])

    def test_two_interruptions_two_resumes_form_one_chain(self):
        out, ev, au, p1 = self.interrupted_pilot(patch=self.kill_on_event("raw", 5))
        ra = self.write_ra(out, "raA")
        p2 = RN.MockProvider(T10.good)
        with self.kill_on_event("classified", 9), self.assertRaises(KeyboardInterrupt):
            self.resume(out, ev, au, ra, p2)
        p3 = RN.MockProvider(T10.good)
        s = self.resume(out, ev, au, self.write_ra(out, "raB"), p3)
        self.assertEqual(s["status"], "RUN_COMPLETE")
        self.assertEqual(p1.calls + p2.calls + p3.calls, 18)
        P, st = IG.verify_run(out, BUILD)
        self.assertEqual((P, st["resumes"]), ([], 2))
        self.assertEqual([r["resume_number"] for r in s["resumes"]], [1, 2])
        self.assertEqual([(r["persisted_calls"], r["reconciles_call"]) for r in s["resumes"]], [(5, 5), (9, 9)])
        self.assertEqual(len({r["previous_ledger_head"] for r in s["resumes"]}), 2)                        # each resume continues from a different chain head
        self.assertEqual(len({c["call_key"] for c in calls_of(out)}), 18)

    def test_a_finished_run_that_only_lost_its_marker_is_finalised_without_any_call(self):
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "nomarker")
        with redirect_stdout(io.StringIO()):
            RN.run("pilot_round_1", BUILD, out, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        os.remove(os.path.join(out, "RUN_COMPLETE"))                       # as if the process died after the last classified entry
        prov = RN.MockProvider(T10.good)
        s = self.resume(out, ev, au, self.write_ra(out), prov)
        self.assertEqual((s["status"], s["recorded_calls"], prov.calls), ("RUN_COMPLETE", 18, 0))
        self.assertEqual(IG.verify_run(out, BUILD)[0], [])


class TestResumeRefusals(Base):
    def prepared(self):
        out, ev, au, _ = self.interrupted_pilot(patch=self.kill_on_event("raw", 5))
        return out, ev, au

    def test_authorization_conditions(self):
        out, ev, au = self.prepared()
        cases = {
            "not owner": ({"authorized_by": "assistant"}, "not written by the owner"),
            "wrong scope": ({"scope": "main"}, "another scope"),
            "wrong run": ({"run_id": "0" * 16}, "another run"),
            "stale head": ({"ledger_head_sha256": "f" * 64}, "current ledger head"),
            "bad count": ({"remaining_calls": 14}, "remaining_calls"),
            "bool count": ({"remaining_calls": True}, "remaining_calls"),
            "wrong ceiling": ({"cost_ceiling_usd": 1000}, "cost ceiling"),
            "wrong basis": ({"resume_basis": "transport_halt"}, "requires resume_basis"),
            "unknown basis": ({"resume_basis": "whatever"}, "resume_basis must be"),
        }
        for name, (over, text) in cases.items():
            with self.subTest(name):
                self.assert_refused(out, ev, au, self.write_ra(out, "ra_" + name.replace(" ", "_"), **over), text)
        with self.subTest("missing file"):
            self.assert_refused(out, ev, au, os.path.join(self.tmp, "nowhere.json"), "resume authorization file missing")

    def test_mismatch_of_authorization_evidence_fz_settings_or_provider_refuses(self):
        out, ev, au = self.prepared()
        ra = self.write_ra(out)
        au2 = os.path.join(self.tmp, "au_other.json")
        d = json.loads(PF.rb(au))
        d["input_tokens_per_call_assumed"] = 1017
        PF.wb(au2, (json.dumps(d) + "\n").encode())
        self.assert_refused(out, ev, au2, ra, "authorization file differs")
        ev2 = os.path.join(self.tmp, "ev_other.json")
        e = json.loads(PF.rb(ev))
        e["human_blind_check"]["worksheet_sha256"] = "b" * 64
        PF.wb(ev2, (json.dumps(e) + "\n").encode())
        self.assert_refused(out, ev2, au, ra, "evidence file differs")
        fz2 = os.path.join(self.tmp, "fz_copy.json")
        PF.wb(fz2, PF.rb(RN.FZ_PATH) + b"\n")
        with mock.patch.object(RN, "FZ_PATH", fz2):
            self.assert_refused(out, ev, au, ra, "FZ-v1 file differs")
        self.assert_refused(out, ev, au, ra, "provider class differs", prov=OtherProvider(T10.good))

    def test_settings_differing_from_the_run_refuse(self):
        real = RN.check_gates

        def altered(*a, **k):
            return dict(real(*a, **k), timeout_s=181)
        with mock.patch.object(RN, "check_gates", altered):
            out, ev, au, _ = self.interrupted_pilot(patch=self.kill_on_event("raw", 5))
        self.assert_refused(out, ev, au, self.write_ra(out), "settings differ")

    def test_complete_run_and_runs_without_provenance_cannot_be_resumed(self):
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "done")
        with redirect_stdout(io.StringIO()):
            RN.run("pilot_round_1", BUILD, out, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        self.assert_refused(out, ev, au, self.write_ra(out, remaining_calls=0), "COMPLETE")
        legacy = os.path.join(self.tmp, "legacy")
        shutil.copytree(out, legacy)
        p = os.path.join(legacy, "ledger.jsonl")
        PF.wb(p, b"\n".join([l for l in PF.rb(p).split(b"\n") if l][1:]) + b"\n")
        os.remove(os.path.join(legacy, "RUN_COMPLETE"))
        with self.assertRaises(RS.ResumeRefused) as cm:
            self.resume(legacy, ev, au, os.path.join(self.tmp, "x.json"), RN.MockProvider(T10.good))
        self.assertIn("provenance", " ".join(cm.exception.reasons))

    def test_damaged_files_refuse_with_a_diagnosis_and_change_nothing(self):
        def tail(name):
            def m(d):
                p = os.path.join(d, name)
                PF.wb(p, PF.rb(p)[:-40])
            return m
        damage = {
            "ledger last line truncated": (tail("ledger.jsonl"), "truncated"),
            "calls last line truncated": (tail("calls.jsonl"), "truncated"),
            "complete raw file deleted": (lambda d: os.remove(os.path.join(d, "raw", "00002.json")), "raw file of call 2 is missing"),
            "pending raw file garbage": (lambda d: PF.wb(os.path.join(d, "raw", "00005.json"), b"{not json"), "not a valid record"),
            "extra raw file": (lambda d: PF.wb(os.path.join(d, "raw", "00099.json"), b"{}"), "raw folder"),
        }
        for name, (mut, text) in damage.items():
            with self.subTest(name):
                out, ev, au = self.prepared()
                ra = self.write_ra(out, "ra_" + name.replace(" ", "_"))
                mut(out)
                self.assert_refused(out, ev, au, ra, text)


class TestCostStop(Base):
    def test_observed_cost_at_the_limit_halts_and_the_run_cannot_be_resumed(self):
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "cost")
        seen = []

        def script(key, prompt):
            seen.append(key)
            r = T10.good(key, prompt)
            if len(seen) == 3:
                r["usage"] = {"promptTokenCount": 100, "totalTokenCount": 100 + 40_000_001}        # 40,000,001 output tokens ~ US$100.0000025
            return r
        with redirect_stdout(io.StringIO()):
            s = RN.run("pilot_round_1", BUILD, out, RN.MockProvider(script), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        self.assertEqual((s["status"], s["recorded_calls"], len(seen)), ("RUN_INCOMPLETE", 3, 3))
        self.assertEqual(s["halt"], RN.COST_STOP_HALT)
        self.assertIn("observed cost reaches the OD-10 limit", IG.verify_run(out, BUILD)[0])
        self.assert_refused(out, ev, au, self.write_ra(out, basis="transport_halt", remaining_calls=15), "cost stop")

    def test_ordinary_usage_never_triggers_the_cost_stop(self):
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "nocost")
        with redirect_stdout(io.StringIO()):
            s = RN.run("pilot_round_1", BUILD, out, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        self.assertEqual((s["status"], s["halt"]), ("RUN_COMPLETE", None))


class TestMainRunTransportHalt(Base):
    def main_setup(self, days):
        ev, au = T10.TestRunner.files(self, "main", days=days)
        reg = json.loads(PF.rb(os.path.join(BUILD, "main", "registry.json")))["templates"]
        text = RN.D.call_list([t["id"] for t in reg], 2)
        rec = os.path.join(self.tmp, f"rec_{days}.json")
        PF.wb(rec, (json.dumps({"final_decision": "PASS", "seed": 20261030, "call_list_sha256": RN.sha(text.encode("ascii"))}) + "\n").encode())
        return ev, au, rec

    def run_main(self, days, script):
        ev, au, rec = self.main_setup(days)
        out = os.path.join(self.tmp, f"main{days}")
        prov = RN.MockProvider(script)
        with redirect_stdout(io.StringIO()):
            s = RN.run("main", BUILD, out, prov, evidence_path=ev, auth_path=au, pilot_record_path=rec, sleeper=lambda x: None, verify_build=False)
        return out, ev, au, rec, s, prov

    def ra(self, out, total=2496, **kw):
        return self.write_ra(out, scope="main", total=total, basis="transport_halt", **kw)

    def test_transport_only_halt_resumes_to_completion_without_repeating_any_call(self):
        seen = []

        def script(key, prompt):
            seen.append(key)
            if 3 <= len(seen) <= 7:
                return {"http_status": None, "text": "", "error": "TimeoutError"}
            return T10.good(key, prompt)
        out, ev, au, rec, s, p1 = self.run_main(60, script)
        self.assertEqual((s["status"], s["recorded_calls"]), ("RUN_INCOMPLETE", 7))
        self.assertIn("13.8", s["halt"])
        p2 = RN.MockProvider(T10.good)
        with redirect_stdout(io.StringIO()):
            s2 = RS.resume(out, BUILD, p2, evidence_path=ev, auth_path=au, resume_auth_path=self.ra(out), pilot_record_path=rec,
                           sleeper=lambda x: None, verify_build=False)
        self.assertEqual((s2["status"], s2["recorded_calls"], p1.calls + p2.calls), ("RUN_COMPLETE", 2496, 2496))      # each call exactly once in total
        self.assertEqual(len({c["call_key"] for c in calls_of(out)}), 2496)
        self.assertEqual(s2["resumes"][0]["counters_carried"], {"consecutive_sf": 5, "sf_total": 5, "completed": 7})
        self.assertEqual(os.listdir(os.path.join(out, "segments")), ["RUN_INCOMPLETE.segment1"])                      # earlier marker kept, not deleted
        P, st = IG.verify_run(out, BUILD)
        self.assertEqual((P, st["resumes"], st["status"]), ([], 1, "RUN_COMPLETE"))

    def test_counters_are_not_reset_by_a_resume(self):
        def fail(key, prompt):
            return {"http_status": None, "text": "", "error": "TimeoutError"}
        out, ev, au, rec, s, p1 = self.run_main(61, fail)
        self.assertEqual((s["recorded_calls"], s["status"]), (5, "RUN_INCOMPLETE"))
        p2 = RN.MockProvider(fail)
        with redirect_stdout(io.StringIO()):
            s2 = RS.resume(out, BUILD, p2, evidence_path=ev, auth_path=au, resume_auth_path=self.ra(out), pilot_record_path=rec, sleeper=lambda x: None, verify_build=False)
        self.assertEqual((s2["status"], s2["recorded_calls"], p2.calls), ("RUN_INCOMPLETE", 6, 1))        # the carried count of 5 makes the first new failure halt again
        self.assertEqual(IG.verify_run(out, BUILD)[0], [])

    def test_halt_caused_by_an_http_status_error_is_not_resumable(self):
        out, ev, au, rec, s, _ = self.run_main(62, lambda k, p: {"http_status": 503, "text": "", "error": "HTTP 503"})
        self.assertEqual(s["recorded_calls"], 5)
        self.assert_refused(out, ev, au, self.ra(out), "not transport-only", pilot_record_path=rec)

    def test_halt_caused_by_an_unlisted_exception_class_is_not_resumable(self):
        def script(key, prompt):
            raise RuntimeError("provider bug")                  # recorded as SF1 by the unchanged scoring, but not on the transport allow-list
        out, ev, au, rec, s, _ = self.run_main(63, script)
        self.assert_refused(out, ev, au, self.ra(out), "not transport-only", pilot_record_path=rec)


class TestCommandLine(Base):
    def test_cli_refuses_without_gate_evidence_and_never_calls_a_provider(self):
        out, ev, au, _ = self.interrupted_pilot(patch=self.kill_on_event("raw", 5))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = RS.main(["--run", out, "--build", BUILD, "--evidence", os.path.join(self.tmp, "none.json"), "--authorization", au,
                          "--resume-authorization", os.path.join(self.tmp, "none2.json")])
        self.assertEqual(rc, 2)
        self.assertIn("GATE CLOSED", buf.getvalue())

    def test_cli_refuses_a_folder_with_no_provenance(self):
        ev, au = self.pilot_files()
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = RS.main(["--run", empty, "--build", BUILD, "--evidence", ev, "--authorization", au, "--resume-authorization", os.path.join(self.tmp, "none.json")])
        self.assertEqual(rc, 2)
        self.assertIn("RESUME REFUSED", buf.getvalue())


class ProductionPolicyBase(Base):
    """Shipped policy: the contract is enforced and nothing is ratified. A test that needs a positive path calls ratify_for_test() (TEST-ONLY set, not a ratification)."""

    def setUp(self):
        super().setUp()
        for name, val in (("RATIFIED_TRANSPORT_ERROR_CLASSES", frozenset()), ("PROCESS_DEATH_CONTRACT_ENFORCED", True)):
            pa = mock.patch.object(RS, name, val)
            pa.start()
            self.addCleanup(pa.stop)

    def ratify_for_test(self, names=TEST_ONLY_RATIFIED):
        pa = mock.patch.object(RS, "RATIFIED_TRANSPORT_ERROR_CLASSES", frozenset(names))
        pa.start()
        self.addCleanup(pa.stop)


class TestHttpAndClassPolicy(ProductionPolicyBase):
    """Owner instruction 2026-10-15 (record 12.42): the HTTP status allow-list is empty; the exception allow-list is deny-by-default."""

    @staticmethod
    def rec(status=None, error="TimeoutError", text="", **extra):
        r = {"http_status": status, "response_text": text, "error": error}
        r.update(extra)
        return r

    def test_the_shipped_policy_is_empty(self):
        self.assertEqual(SHIPPED_POLICY, (frozenset(), frozenset(), True))
        self.assertEqual(len(RS.TRANSPORT_ERROR_CLASSES), 14)           # the audited candidates; membership gives no eligibility

    def test_nothing_is_transport_only_while_no_class_is_ratified(self):
        for cls in sorted(RS.TRANSPORT_ERROR_CLASSES):
            with self.subTest(cls):
                self.assertFalse(RS.is_transport_only(self.rec(error=cls)))

    def test_every_http_status_is_rejected_even_for_a_ratified_class(self):
        self.ratify_for_test()
        self.assertTrue(RS.is_transport_only(self.rec()))              # control: a ratified class without a status qualifies (mechanics only)
        for st in (0, 100, 200, 204, 301, 400, 401, 403, 404, 408, 409, 413, 422, 429, 500, 501, 502, 503, 504, 599):
            with self.subTest(st):
                self.assertFalse(RS.is_transport_only(self.rec(status=st)))
                self.assertFalse(RS.is_transport_only(self.rec(status=st, error=f"HTTP {st}")))

    def test_mixed_or_contradictory_flags_and_response_text_are_rejected(self):
        self.ratify_for_test()
        for name, r in {"SF1+SF3": self.rec(finish_reason="MAX_TOKENS"), "SF1+SF4": self.rec(block_reason="SAFETY"), "SF1+SF4 finish": self.rec(finish_reason="SAFETY"),
                        "response text present": self.rec(text="Verdict: satisfied"), "no error, no status": self.rec(error=None)}.items():
            with self.subTest(name):
                self.assertFalse(RS.is_transport_only(r))

    def test_a_ratified_name_that_is_not_an_audited_candidate_is_ignored(self):
        self.ratify_for_test({"RuntimeError"})
        self.assertFalse(RS.is_transport_only(self.rec(error="RuntimeError")))

    def test_a_candidate_that_is_not_ratified_is_rejected(self):
        self.ratify_for_test()
        self.assertFalse(RS.is_transport_only(self.rec(error="ConnectionResetError")))

    def test_a_main_run_halt_is_refused_while_no_class_is_ratified(self):
        def fail(key, prompt):
            return {"http_status": None, "text": "", "error": "TimeoutError"}
        out, ev, au, rec, s, _ = self.run_main_of(90, fail)
        self.assertEqual((s["status"], s["recorded_calls"]), ("RUN_INCOMPLETE", 5))
        self.assert_refused(out, ev, au, self.write_ra(out, scope="main", total=2496, basis="transport_halt"), "no exception class is ratified", pilot_record_path=rec)

    def test_http_halts_are_refused_even_with_a_ratified_class(self):
        self.ratify_for_test()
        for days, st in ((91, 429), (92, 503)):
            with self.subTest(st):
                out, ev, au, rec, s, _ = self.run_main_of(days, lambda k, p, st=st: {"http_status": st, "text": "", "error": f"HTTP {st}"})
                self.assertEqual(s["recorded_calls"], 5)
                self.assert_refused(out, ev, au, self.write_ra(out, scope="main", total=2496, basis="transport_halt"), "not transport-only", pilot_record_path=rec)

    main_setup = TestMainRunTransportHalt.main_setup

    def run_main_of(self, days, script):
        return TestMainRunTransportHalt.run_main(self, days, script)


class TestProcessDeathContract(ProductionPolicyBase):
    """Decision record 12.42: a resume after a stop without halt marker needs persistent evidence of a transport failure. Only the machinery is VERIFIED here: the
    positive cases use a TEST-ONLY ratified class, because the owner has ratified none."""

    def death(self, fail_calls=(), error="TimeoutError", status=None, extra=None, death_after=5):
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, f"pd{self.k}")
        seen, n = [], [0]

        def script(key, prompt):
            seen.append(key)
            if len(seen) in fail_calls:
                r = {"http_status": status, "text": "", "error": error}
                r.update(extra or {})
                return r
            return T10.good(key, prompt)

        def sleeper(x):
            n[0] += 1
            if n[0] == death_after:
                raise KeyboardInterrupt()                       # the process dies after the call and its records are persisted, during the delay
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                RN.run("pilot_round_1", BUILD, out, RN.MockProvider(script), evidence_path=ev, auth_path=au, sleeper=sleeper, verify_build=False)
        return out, ev, au

    def ra_pd(self, out, cause="TimeoutError", count=1, **over):
        kw = {"failure_cause": cause, "trailing_transport_failures": count}
        kw.update(over)
        return self.write_ra(out, "ra_pd", **kw)

    def test_default_without_any_ratified_class_the_death_after_a_transport_failure_is_refused(self):
        out, ev, au = self.death(fail_calls=(5,))
        self.assert_refused(out, ev, au, self.ra_pd(out), "not an SF1-only transport failure")

    def test_positive_after_a_persisted_transport_failure_with_a_test_only_class(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(5,))
        s = self.resume(out, ev, au, self.ra_pd(out), RN.MockProvider(T10.good))
        self.assertEqual((s["status"], s["recorded_calls"]), ("RUN_COMPLETE", 18))
        r = [e for e in ledger_of(out) if e.get("event") == "resume"][0]
        self.assertEqual((r["resume_basis"], r["authorized_by"]), ("process_interruption", "owner"))
        self.assertEqual(IG.verify_run(out, BUILD)[0], [])

    def test_positive_with_two_trailing_failures_needs_the_count_two(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(4, 5))
        self.assert_refused(out, ev, au, self.ra_pd(out, count=1), "trailing_transport_failures")
        s = self.resume(out, ev, au, self.ra_pd(out, count=2), RN.MockProvider(T10.good))
        self.assertEqual(s["status"], "RUN_COMPLETE")

    def test_negative_trailing_successful_record(self):
        self.ratify_for_test()
        out, ev, au = self.death()
        self.assert_refused(out, ev, au, self.ra_pd(out), "not an SF1-only transport failure")

    def test_negative_trailing_success_after_an_earlier_failure(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(3,))
        self.assert_refused(out, ev, au, self.ra_pd(out), "not an SF1-only transport failure")

    def test_negative_trailing_sf1_with_an_http_status(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(5,), status=503, error="HTTP 503")
        self.assert_refused(out, ev, au, self.ra_pd(out, cause="HTTP 503"), "not an SF1-only transport failure")

    def test_negative_unratified_class(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(5,), error="ConnectionResetError")
        self.assert_refused(out, ev, au, self.ra_pd(out, cause="ConnectionResetError"), "not an SF1-only transport failure")

    def test_negative_mixed_sf_flags(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(5,), extra={"finish_reason": "MAX_TOKENS"})
        self.assert_refused(out, ev, au, self.ra_pd(out), "not an SF1-only transport failure")

    def test_negative_no_persisted_call(self):
        why, cause, n = RS._process_death_contract({"records": []}, None)
        self.assertEqual((cause, n), (None, None))
        self.assertIn("no persisted call", " ".join(why))

    def test_negative_intent_without_a_raw_record(self):
        self.ratify_for_test()
        out, ev, au, _ = self.interrupted_pilot(kill_at=4)
        self.assert_refused(out, ev, au, self.ra_pd(out), "attempted, no record")

    @staticmethod
    def flip_second_line(b):
        lines = b.split(b"\n")
        lines[1] = lines[1].replace(b"intent", b"intenx", 1)             # an altered entry breaks the hash chain of the entry after it
        return b"\n".join(lines)

    def test_negative_damaged_files(self):
        self.ratify_for_test()
        cases = {"missing raw file": lambda o: os.remove(os.path.join(o, "raw", "00005.json")),
                 "corrupt raw file": lambda o: PF.wb(os.path.join(o, "raw", "00005.json"), b"{not json"),
                 "corrupt ledger chain": lambda o: PF.wb(os.path.join(o, "ledger.jsonl"), self.flip_second_line(PF.rb(os.path.join(o, "ledger.jsonl"))))}
        for name, damage in cases.items():
            with self.subTest(name):
                out, ev, au = self.death(fail_calls=(5,))
                ra = self.ra_pd(out)
                damage(out)
                with self.assertRaises(RS.ResumeRefused) as cm:
                    self.resume(out, ev, au, ra, RN.MockProvider(T10.good))
                text = " ".join(cm.exception.reasons)
                self.assertTrue(text.strip())
                self.assertNotIn("not an SF1-only", text)               # refused by the integrity layer, not by the contract's own evidence rule
                self.assertTrue(any(w in text.lower() for w in ("raw", "ledger", "chain")), text)

    def test_negative_markers(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(5,))
        PF.wb(os.path.join(out, "RUN_COMPLETE"), b"{}")
        self.assert_refused(out, ev, au, self.ra_pd(out), "COMPLETE")
        out2, ev2, au2 = self.death(fail_calls=(5,))
        PF.wb(os.path.join(out2, "RUN_COMPLETE"), b"{}")
        PF.wb(os.path.join(out2, "RUN_INCOMPLETE"), b"{}")
        self.assert_refused(out2, ev2, au2, self.ra_pd(out2), "two status markers")

    def test_negative_stale_head_wrong_run_wrong_cause_wrong_count_advisor_malformed(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(5,))
        for name, (over, text) in {"stale head": ({"ledger_head_sha256": "f" * 64}, "current ledger head"), "wrong run": ({"run_id": "0" * 16}, "another run"),
                                   "wrong cause": ({"failure_cause": "ConnectionResetError"}, "failure_cause"), "wrong count": ({"trailing_transport_failures": 2}, "failure_cause"),
                                   "bool count": ({"trailing_transport_failures": True}, "failure_cause"), "advisor": ({"authorized_by": "advisor"}, "not written by the owner"),
                                   "no cause field": ({"failure_cause": None}, "failure_cause")}.items():
            with self.subTest(name):
                self.assert_refused(out, ev, au, self.ra_pd(out, **over), text)
        bad = os.path.join(self.tmp, "ra_bad.json")
        PF.wb(bad, b"[1, 2]")
        self.assert_refused(out, ev, au, bad, "not a JSON object")

    def test_negative_duplicate_authorization(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(5,))
        ra = self.ra_pd(out)
        self.resume(out, ev, au, ra, RN.MockProvider(T10.good))
        self.assert_refused(out, ev, au, ra, "COMPLETE")


class TestProcessDeathContractGaps(ProductionPolicyBase):
    """Added in the audit round after record 12.42. Production configuration: contract enforced, nothing ratified by default; the TEST-ONLY set is used only where a
    positive state is needed so that the refusal under test can be attributed to the condition under test (and not to the empty ratified set)."""

    death = TestProcessDeathContract.death
    ra_pd = TestProcessDeathContract.ra_pd

    def test_calls_jsonl_that_disagrees_with_the_raw_record_is_refused(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(5,))
        ra = self.ra_pd(out)
        lines = PF.rb(os.path.join(out, "calls.jsonl")).split(b"\n")
        lines[4] = lines[4].replace(b"TimeoutError", b"TimeoutErrox")
        PF.wb(os.path.join(out, "calls.jsonl"), b"\n".join(lines))
        with self.assertRaises(RS.ResumeRefused) as cm:
            self.resume(out, ev, au, ra, RN.MockProvider(T10.good))
        self.assertIn("calls.jsonl", " ".join(cm.exception.reasons))

    def test_a_crash_during_a_call_gives_no_credit_from_an_earlier_transport_failure(self):
        self.ratify_for_test()
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "midcall")
        seen = []

        def script(key, prompt):
            seen.append(key)
            if len(seen) == 4:
                return {"http_status": None, "text": "", "error": "TimeoutError"}      # a persisted, ratified-in-this-test transport failure ...
            if len(seen) == 5:
                raise KeyboardInterrupt()                                               # ... then the process dies while call 5 is running
            return T10.good(key, prompt)
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                RN.run("pilot_round_1", BUILD, out, RN.MockProvider(script), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        self.assert_refused(out, ev, au, self.ra_pd(out), "attempted, no record")

    def test_trailing_count_must_equal_the_stored_evidence_exactly(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(4, 5))
        for bad in (0, 1, 3, -2, 2.0, "2", None):
            with self.subTest(bad):
                self.assert_refused(out, ev, au, self.ra_pd(out, count=bad), "trailing_transport_failures")

    def test_a_marker_that_contradicts_the_ledger_is_refused(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(5,))
        PF.wb(os.path.join(out, "RUN_INCOMPLETE"), b'{"halt": "halt rule 13.8 after call 5", "ledger_final_hash": "' + b"0" * 64 + b'", "recorded_calls": 5}')
        self.assert_refused(out, ev, au, self.ra_pd(out), "does not match the ledger")

    def test_a_cost_stopped_run_is_refused_under_the_production_configuration(self):
        self.ratify_for_test()
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "coststop")
        seen = []

        def script(key, prompt):
            seen.append(key)
            r = T10.good(key, prompt)
            if len(seen) == 3:
                r["usage"] = {"promptTokenCount": 100, "totalTokenCount": 100 + 40_000_001}
            return r
        with redirect_stdout(io.StringIO()):
            s = RN.run("pilot_round_1", BUILD, out, RN.MockProvider(script), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        self.assertEqual(s["halt"], RN.COST_STOP_HALT)
        self.assert_refused(out, ev, au, self.write_ra(out, basis="transport_halt", remaining_calls=15), "cost stop")
        self.assert_refused(out, ev, au, self.write_ra(out, "ra2", failure_cause="TimeoutError", trailing_transport_failures=1, remaining_calls=15), "cost stop")
        self.assertNotIn("SF1", [f for r in calls_of(out) for f in RS.SC.classify(r)["flags"]])      # a cost stop is not recorded as a transport failure

    def test_an_active_run_lock_blocks_the_resume_under_the_production_configuration(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(5,))
        ra = self.ra_pd(out)
        with RN.RunLock(out):
            with self.assertRaises(RS.ResumeRefused) as cm:
                self.resume(out, ev, au, ra, RN.MockProvider(T10.good))
        self.assertIn("in progress", " ".join(cm.exception.reasons))
        self.assertEqual(self.resume(out, ev, au, ra, RN.MockProvider(T10.good))["status"], "RUN_COMPLETE")      # control: the same authorization works once the lock is free


class TestProcessDeathContractAdversarial(ProductionPolicyBase):
    """Post-audit round. Production configuration (contract enforced); the TEST-ONLY ratified set only where a state with a transport failure is needed."""

    death = TestProcessDeathContract.death
    ra_pd = TestProcessDeathContract.ra_pd
    main_setup = TestMainRunTransportHalt.main_setup

    def run_main_of(self, days, script):
        return TestMainRunTransportHalt.run_main(self, days, script)

    def test_an_authorization_cannot_be_reused_on_a_run_that_is_still_incomplete(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(5,))
        ra1 = self.ra_pd(out)
        seen = []

        def script(key, prompt):
            seen.append(key)
            return {"http_status": None, "text": "", "error": "TimeoutError"}           # the resumed call fails again ...
        n = [0]

        def sleeper(x):
            n[0] += 1
            if n[0] == 2:
                raise KeyboardInterrupt()                                              # ... and the process dies again after it is persisted
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                RS.resume(out, BUILD, RN.MockProvider(script), evidence_path=ev, auth_path=au, resume_auth_path=ra1, sleeper=sleeper, verify_build=False)
        self.assertEqual(len(seen), 1)
        self.assert_refused(out, ev, au, ra1, "current ledger head")                    # the first authorization is spent: the ledger moved on
        self.assert_refused(out, ev, au, self.ra_pd(out, count=1), "trailing_transport_failures")      # a fresh head with the old count is refused too
        s = self.resume(out, ev, au, self.ra_pd(out, count=2), RN.MockProvider(T10.good))
        self.assertEqual(s["status"], "RUN_COMPLETE")                                  # control: the correct new authorization works

    def test_the_basis_must_match_the_state(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(5,))
        self.assert_refused(out, ev, au, self.ra_pd(out, resume_basis="transport_halt"), "requires resume_basis")
        def fail(key, prompt):
            return {"http_status": None, "text": "", "error": "TimeoutError"}
        mout, mev, mau, rec, s, _ = self.run_main_of(95, fail)
        self.assertEqual(s["recorded_calls"], 5)
        self.assert_refused(mout, mev, mau, self.write_ra(mout, scope="main", total=2496, basis="process_interruption", failure_cause="TimeoutError",
                                                           trailing_transport_failures=5), "requires resume_basis", pilot_record_path=rec)

    def a1_run(self, plan):
        """plan: {call_index: provider result} for the failing calls; the process dies (during the delay) after call 5. Returns (out, ev, au)."""
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, f"a1_{self.k}_{abs(hash(str(sorted(plan))))}")
        seen, n = [], [0]

        def script(key, prompt):
            seen.append(key)
            if len(seen) in plan:
                return dict(plan[len(seen)])
            return T10.good(key, prompt)

        def sleeper(x):
            n[0] += 1
            if n[0] == 5:
                raise KeyboardInterrupt()
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                RN.run("pilot_round_1", BUILD, out, RN.MockProvider(script), evidence_path=ev, auth_path=au, sleeper=sleeper, verify_build=False)
        return out, ev, au

    TIMEOUT = {"http_status": None, "text": "", "error": "TimeoutError"}

    def test_A1_an_earlier_transport_only_failure_is_allowed(self):
        self.ratify_for_test()
        out, ev, au = self.a1_run({2: self.TIMEOUT, 5: self.TIMEOUT})
        why, cause, count = RS._process_death_contract({"records": calls_of(out)}, None)
        self.assertEqual((why, cause, count), ([], "TimeoutError", 1))
        self.assertEqual(self.resume(out, ev, au, self.ra_pd(out), RN.MockProvider(T10.good))["status"], "RUN_COMPLETE")

    def test_A1_an_earlier_failure_that_is_not_transport_only_refuses_the_resume(self):
        """Option A1 (decision record 12.43): a MECHANISM test with a TEST-ONLY ratified class; it says nothing about which classes are eligible in production."""
        self.ratify_for_test()
        cases = {
            "HTTP status (first call)": ({1: {"http_status": 500, "text": "", "error": "HTTP 500"}, 5: self.TIMEOUT}, "calls [1]"),
            "HTTP status (adjacent to the trailing block)": ({4: {"http_status": 429, "text": "", "error": "HTTP 429"}, 5: self.TIMEOUT}, "calls [4]"),
            "unratified class": ({2: {"http_status": None, "text": "", "error": "ConnectionResetError"}, 5: self.TIMEOUT}, "calls [2]"),
            "SF1 with a block reason (mixed SF1+SF4)": ({3: dict(self.TIMEOUT, block_reason="SAFETY"), 5: self.TIMEOUT}, "calls [3]"),
            "SF3 truncation": ({2: {"http_status": 200, "text": "Verdict: satisfied", "error": None, "model_returned": "gemini-3.5-flash-lite", "finish_reason": "MAX_TOKENS"}, 5: self.TIMEOUT}, "calls [2]"),
            "SF2 empty response": ({3: {"http_status": 200, "text": "", "error": None, "model_returned": "gemini-3.5-flash-lite", "finish_reason": "STOP"}, 5: self.TIMEOUT}, "calls [3]"),
            "SF4 provider block": ({2: {"http_status": 200, "text": "", "error": None, "model_returned": "gemini-3.5-flash-lite", "finish_reason": "SAFETY"}, 5: self.TIMEOUT}, "calls [2]"),
            "two earlier bad calls": ({1: {"http_status": 500, "text": "", "error": "HTTP 500"}, 3: {"http_status": 503, "text": "", "error": "HTTP 503"}, 5: self.TIMEOUT}, "calls [1, 3]"),
        }
        for name, (plan, text) in cases.items():
            with self.subTest(name):
                out, ev, au = self.a1_run(plan)
                self.assert_refused(out, ev, au, self.ra_pd(out), text)
                self.assertIn("option A1", " ".join(RS._process_death_contract({"records": calls_of(out)}, None)[0]))

    def test_A1_a_run_with_only_clean_records_before_the_trailing_failure_is_unchanged(self):
        self.ratify_for_test()
        out, ev, au = self.a1_run({5: self.TIMEOUT})
        why, cause, count = RS._process_death_contract({"records": calls_of(out)}, None)
        self.assertEqual((why, cause, count), ([], "TimeoutError", 1))

    def test_A1_the_trailing_rule_is_still_checked_first(self):
        self.ratify_for_test()
        out, ev, au = self.a1_run({2: {"http_status": 500, "text": "", "error": "HTTP 500"}})              # earlier bad call AND a trailing success
        self.assert_refused(out, ev, au, self.ra_pd(out), "not an SF1-only transport failure")

    def test_a_non_owner_authorization_is_refused_even_with_valid_evidence(self):
        self.ratify_for_test()
        out, ev, au = self.death(fail_calls=(5,))
        for who in ("advisor", "assistant", "", None, "Owner"):
            with self.subTest(who):
                self.assert_refused(out, ev, au, self.ra_pd(out, authorized_by=who), "not written by the owner")


class TestErrorCategory(ProductionPolicyBase):
    """Owner decision 12.43 (X-3, option O3). Offline: exceptions are CONSTRUCTED, nothing is connected. The category is diagnostic evidence and gives no eligibility."""

    def cat(self, e):
        return RN.error_category(e)

    def test_mapping_from_constructed_exceptions(self):
        import socket
        import ssl
        import urllib.error as ue
        import http.client as hc
        cases = [
            (ue.URLError(socket.gaierror(11001, "getaddrinfo failed")), "DNS"),
            (ue.URLError(ConnectionRefusedError(10061, "refused")), "REFUSED"),
            (ue.URLError(TimeoutError("timed out")), "TIMEOUT"),
            (ue.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")), "TLS_VERIFY"),
            (ue.URLError(ssl.SSLError(1, "handshake")), "TLS_OTHER"),
            (ue.URLError(FileNotFoundError(2, "no file")), "LOCAL_FILE"),
            (ue.URLError(PermissionError(13, "denied")), "LOCAL_FILE"),
            (ue.URLError(OSError(5, "io")), "OTHER_OS"),
            (TimeoutError(), "TIMEOUT"), (socket.timeout(), "TIMEOUT"), (ConnectionResetError(), "OTHER_OS"), (hc.RemoteDisconnected("x"), "OTHER_OS"),
            (ssl.SSLEOFError(), "TLS_OTHER"), (socket.gaierror(), "DNS"),
        ]
        for e, want in cases:
            with self.subTest(type(e).__name__ + "/" + want):
                self.assertEqual(self.cat(e), want)

    def test_fallback_is_OTHER_for_everything_that_is_not_an_os_error_or_unknown(self):
        import urllib.error as ue
        import http.client as hc
        import json as js
        for e in (ValueError("x"), js.JSONDecodeError("x", "y", 0), hc.IncompleteRead(b"ab"), RuntimeError("boom"), ue.URLError("a plain string reason"),
                  ue.HTTPError("http://x", 500, "m", {}, None), KeyError("k")):
            with self.subTest(type(e).__name__):
                self.assertEqual(self.cat(e), "OTHER")
        self.assertIn("OTHER", RN.ERROR_CATEGORIES)

    def test_a_hostile_exception_cannot_leak_text_or_break_the_mapping(self):
        class Evil(OSError):
            def __str__(self):
                raise AssertionError("str() must never be called")
            __repr__ = __str__
        self.assertEqual(self.cat(Evil("secret host internal.example /home/user/x")), "OTHER_OS")

    def test_every_category_is_in_the_closed_enum(self):
        import urllib.error as ue
        for e in (TimeoutError(), ue.URLError(OSError(1, "x")), ValueError()):
            self.assertIn(self.cat(e), RN.ERROR_CATEGORIES)

    def test_a_record_carries_the_category_and_no_text(self):
        import socket
        import urllib.error as ue
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "cat1")

        def script(key, prompt):
            raise ue.URLError(socket.gaierror(11001, "getaddrinfo failed for internal.example at C:\\Users\\someone\\ca.pem"))
        with redirect_stdout(io.StringIO()):
            RN.run("pilot_round_1", BUILD, out, RN.MockProvider(script), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        raw = PF.rb(os.path.join(out, "raw", "00001.json"))
        rec = json.loads(raw)
        self.assertEqual((rec["error"], rec["error_category"]), ("URLError", "DNS"))
        for leak in (b"internal.example", b"someone", b"ca.pem", b"getaddrinfo", b"11001"):
            self.assertNotIn(leak, raw)

    def test_records_without_an_exception_keep_their_bytes_and_their_classification(self):
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "cat2")
        with redirect_stdout(io.StringIO()):
            RN.run("pilot_round_1", BUILD, out, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        for c in calls_of(out):
            self.assertNotIn("error_category", c)
        ev2, au2 = self.pilot_files()
        out2 = os.path.join(self.tmp, "cat3")
        with redirect_stdout(io.StringIO()):
            RN.run("pilot_round_1", BUILD, out2, RN.MockProvider(lambda k, p: {"http_status": None, "text": "", "error": "TimeoutError"}), evidence_path=ev2, auth_path=au2,
                   sleeper=lambda x: None, verify_build=False)
        for c in calls_of(out2):
            self.assertNotIn("error_category", c)                                  # a provider result without a category (the mock) gives no key

    def test_the_category_does_not_change_scoring_or_eligibility(self):
        import urllib.error as ue
        base = {"http_status": None, "response_text": "", "error": "URLError"}
        with_cat = dict(base, error_category="TIMEOUT")
        self.assertEqual(RS.SC.classify(base), RS.SC.classify(with_cat))
        self.assertFalse(RS.is_transport_only(with_cat))                            # nothing ratified
        self.ratify_for_test()                                                       # TEST-ONLY {TimeoutError}
        self.assertFalse(RS.is_transport_only(with_cat))                            # URLError stays ineligible whatever its category
        self.assertEqual(RS.RATIFIED_TRANSPORT_ERROR_CLASSES, frozenset({"TimeoutError"}))

    def test_windows_ambiguity_is_documented_in_the_code(self):
        src = PF.rb(os.path.join(HERE, "s10_runner.py")).decode("utf-8")
        self.assertIn("a refused connection presented as reason TimeoutError", src)


class TestH1H4AtResume(Base):
    """Spec 13.6: the only code change allowed at resume is a pure implementation fix (H1-H4). The tool checks hashes and the attested fields; H1-H3 are the owner's attestation."""

    def setup_run(self):
        out, ev, au, _ = self.interrupted_pilot(patch=self.kill_on_event("raw", 5))
        real = RN.code_hashes()
        changed = dict(real, **{"s10_runner.py": "a" * 64})
        return out, ev, au, real, changed

    def evidence(self, out, real, changed, name="h.json", **over):
        run_id = ledger_of(out)[0]["run_id"]
        e = {"authorized_by": "owner", "owner_signoff": True, "run_id": run_id, "code_hash_before": {"s10_runner.py": real["s10_runner.py"]},
             "code_hash_after": {"s10_runner.py": changed["s10_runner.py"]}, "reason_class": "crash", "reason": "synthetic test fix",
             "h1_executable_code_only": True, "h2_regression_byte_identical": True, "h2_regression_result_ref": "synthetic-regression-run-1",
             "h3_reference_checker_agreement": True, "h3_triggering_call_index": 5, "h4_logged": True}
        e.update(over)
        p = os.path.join(self.tmp, f"{name}.{self.k}.json")
        PF.wb(p, (json.dumps(e, sort_keys=True) + "\n").encode())
        return p

    def test_a_changed_runner_without_evidence_is_refused(self):
        out, ev, au, real, changed = self.setup_run()
        with mock.patch.object(RN, "code_hashes", return_value=changed):
            self.assert_refused(out, ev, au, self.write_ra(out), "no H1-H4 evidence file")

    def test_each_missing_or_false_criterion_is_refused(self):
        out, ev, au, real, changed = self.setup_run()
        cases = {
            "no owner signoff": ({"owner_signoff": False}, "not signed off"),
            "advisor": ({"authorized_by": "advisor"}, "not signed off"),
            "other run": ({"run_id": "0" * 16}, "another run"),
            "before hash wrong": ({"code_hash_before": {"s10_runner.py": "b" * 64}}, "code_hash_before"),
            "after hash wrong": ({"code_hash_after": {"s10_runner.py": "c" * 64}}, "code_hash_after"),
            "reason class": ({"reason_class": "speed"}, "reason_class"),
            "no reason": ({"reason": " "}, "no written reason"),
            "H1": ({"h1_executable_code_only": False}, "H1"),
            "H2 false": ({"h2_regression_byte_identical": False}, "H2"),
            "H2 no ref": ({"h2_regression_result_ref": ""}, "H2"),
            "H3": ({"h3_reference_checker_agreement": False}, "H3"),
            "H3 no index": ({"h3_triggering_call_index": True}, "H3"),
            "H4": ({"h4_logged": False}, "H4 logging"),
        }
        with mock.patch.object(RN, "code_hashes", return_value=changed):
            for name, (over, text) in cases.items():
                with self.subTest(name):
                    self.assert_refused(out, ev, au, self.write_ra(out, "ra_h"), text, h1_h4_path=self.evidence(out, real, changed, "h_" + name.replace(" ", "_"), **over))

    def test_valid_evidence_allows_the_resume_and_is_recorded_on_the_chain(self):
        out, ev, au, real, changed = self.setup_run()
        hp = self.evidence(out, real, changed)
        with mock.patch.object(RN, "code_hashes", return_value=changed):
            s = self.resume(out, ev, au, self.write_ra(out), RN.MockProvider(T10.good), h1_h4_path=hp)
        self.assertEqual(s["status"], "RUN_COMPLETE")
        r = [e for e in ledger_of(out) if e.get("event") == "resume"][0]
        self.assertEqual((r["h1_h4_evidence_sha256"], r["authorized_by"]), (RN.file_sha(hp), "owner"))
        self.assertEqual(r["code_sha256"]["s10_runner.py"], "a" * 64)

    def test_unchanged_code_needs_no_evidence(self):
        out, ev, au, _ = self.interrupted_pilot(patch=self.kill_on_event("raw", 5))
        s = self.resume(out, ev, au, self.write_ra(out), RN.MockProvider(T10.good))
        self.assertIsNone([e for e in ledger_of(out) if e.get("event") == "resume"][0]["h1_h4_evidence_sha256"])
        self.assertEqual(s["status"], "RUN_COMPLETE")


if __name__ == "__main__":
    unittest.main()
