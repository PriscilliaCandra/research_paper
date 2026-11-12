"""Characterization tests (mock provider, temporary folders, no network) of what the CURRENT runner and integrity checker do with an interrupted run and
with a damaged ledger. They pin existing behaviour; they decide no rule and do not implement resume (patch P8 / MD-08 is not adopted).
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_ledger_resilience.py"""
import hashlib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_integrity as IG  # noqa: E402
import s10_runner as RN  # noqa: E402
RN.FZ_INDEX_ENFORCED = False  # E-1 (12.55): these tests exercise other machinery with mock data; enforcement is tested in test_fz_enforcement.py
import test_pilot_flow as PF  # noqa: E402
import test_step10 as T10  # noqa: E402

BUILD = T10.BUILD


def ledger_of(run):
    return [json.loads(l) for l in PF.rb(os.path.join(run, "ledger.jsonl")).decode().split("\n") if l]


def tree_hashes(run):
    out = {}
    for d, _, fs in os.walk(run):
        for f in fs:
            p = os.path.join(d, f)
            out[os.path.relpath(p, run)] = hashlib.sha256(PF.rb(p)).hexdigest()
    return out


class TestInterruptedRun(PF.Base):
    def interrupted(self, at=3):
        seen = []

        def script(key, prompt):
            seen.append(key)
            if len(seen) == at:
                raise KeyboardInterrupt()
            return T10.good(key, prompt)
        prov = RN.MockProvider(script)
        ev, au = T10.TestRunner.files(self, "pilot_round_1", days=9, calls=18)
        out = os.path.join(self.tmp, "interrupted")
        with self.assertRaises(KeyboardInterrupt):
            RN.run("pilot_round_1", BUILD, out, prov, evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        return out, ev, au, seen, prov

    def test_interrupt_leaves_an_intent_without_raw_and_no_status_marker(self):
        out, _, _, seen, prov = self.interrupted(3)
        led = ledger_of(out)
        events = [(e["event"], e["call_index"]) for e in led]
        self.assertEqual(events[-1], ("intent", 3))                             # last ledger entry: intent 3 with no raw entry
        self.assertNotIn(("raw", 3), events)
        self.assertEqual(sorted(os.listdir(os.path.join(out, "raw"))), ["00001.json", "00002.json"])
        self.assertFalse(any(os.path.exists(os.path.join(out, m)) for m in ("RUN_COMPLETE", "RUN_INCOMPLETE")))
        self.assertEqual(prov.calls, 3)                                          # the interrupted call was attempted exactly once
        self.assertEqual(len(seen), len(set(seen)))                              # no key repeated

    def test_integrity_check_reports_the_unfinished_run(self):
        out, *_ = self.interrupted(3)
        P, _ = IG.verify_run(out, BUILD)
        self.assertEqual(P, ["not exactly one status marker"])

    def test_rerun_into_the_same_folder_is_refused_and_changes_nothing(self):
        out, ev, au, seen, _ = self.interrupted(3)
        before = tree_hashes(out)
        prov2 = RN.MockProvider(T10.good)
        with self.assertRaises(FileExistsError):
            RN.run("pilot_round_1", BUILD, out, prov2, evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        self.assertEqual(prov2.calls, 0)                                         # refused before any provider call
        self.assertEqual(tree_hashes(out), before)                               # existing evidence untouched

    def test_resume_exists_only_as_a_fail_closed_module_and_never_repeats_an_unresolved_intent(self):
        # Changed 2026-10-14 (owner instruction): s10_resume.py exists but MD-08 is still NOT adopted. The runner itself still has no resume path;
        # the separate module refuses a call with an intent and no persisted record (full coverage in test_resume.py).
        self.assertFalse(hasattr(RN, "resume"))
        self.assertTrue(os.path.exists(os.path.join(HERE, "s10_resume.py")))
        import s10_resume as RS
        out, ev, au, seen, prov = self.interrupted(3)
        self.assertEqual(prov.calls, 3)
        with self.assertRaises(RS.ResumeRefused):
            RS.inspect_run(out, BUILD, "pilot_round_1", RN.plan_calls("pilot_round_1", BUILD))
        self.assertEqual(prov.calls, 3)                                          # nothing was called by the refusal


class TestDamagedLedger(PF.Base):
    def test_ledger_cut_before_the_last_classified_entry(self):
        run = self.run_pilot(T10.good)
        p = os.path.join(run, "ledger.jsonl")
        lines = PF.rb(p).split(b"\n")
        PF.wb(p, b"\n".join(lines[:-2]) + b"\n")                                 # drop the last `classified` entry
        P, _ = IG.verify_run(run, BUILD)
        self.assertTrue(any("ledger final hash differs" in x for x in P), P)
        self.assertTrue(any("intent/raw/classified" in x for x in P), P)

    def test_chain_break_in_the_middle(self):
        run = self.run_pilot(T10.good)
        p = os.path.join(run, "ledger.jsonl")
        led = ledger_of(run)
        led[4]["prev"] = "f" * 64
        PF.wb(p, b"".join(RN.cj(e) for e in led))
        P, _ = IG.verify_run(run, BUILD)
        self.assertIn("ledger chain broken", P)

    def test_reordered_events_are_detected(self):
        run = self.run_pilot(T10.good)
        p = os.path.join(run, "ledger.jsonl")
        lines = PF.rb(p).split(b"\n")
        lines[0], lines[1] = lines[1], lines[0]
        PF.wb(p, b"\n".join(lines))
        P, _ = IG.verify_run(run, BUILD)
        self.assertIn("ledger chain broken", P)

    def test_duplicate_call_key_is_named(self):
        run = self.run_pilot(T10.good)
        cp = os.path.join(run, "calls.jsonl")
        recs = [json.loads(l) for l in PF.rb(cp).decode().split("\n") if l]
        recs[1]["call_key"] = recs[0]["call_key"]
        PF.wb(cp, ("".join(RN.cj(r).decode() for r in recs)).encode())
        P, _ = IG.verify_run(run, BUILD)
        self.assertIn("duplicate call keys (retry or replacement)", P)

    def test_prompt_hash_not_matching_the_build_is_named(self):
        run = self.run_pilot(T10.good)
        for name in ("calls.jsonl",):
            cp = os.path.join(run, name)
            recs = [json.loads(l) for l in PF.rb(cp).decode().split("\n") if l]
            recs[2]["prompt_sha256"] = "1" * 64
            PF.wb(cp, ("".join(RN.cj(r).decode() for r in recs)).encode())
        P, _ = IG.verify_run(run, BUILD)
        self.assertTrue(any(x == "prompt hash differs from the build at call 3" for x in P), P)

    def test_two_status_markers_are_refused(self):
        run = self.run_pilot(T10.good)
        PF.wb(os.path.join(run, "RUN_INCOMPLETE"), PF.rb(os.path.join(run, "RUN_COMPLETE")))
        P, _ = IG.verify_run(run, BUILD)
        self.assertEqual(P, ["not exactly one status marker"])


class TestFailureRecordsKeepOneCallPerKey(PF.Base):
    def test_provider_exception_is_recorded_as_error_and_never_repeated(self):
        seen = []

        def script(key, prompt):
            seen.append(key)
            if len(seen) == 2:
                raise RuntimeError("boom")
            return T10.good(key, prompt)
        run = self.run_pilot(script)
        recs = [json.loads(l) for l in PF.rb(os.path.join(run, "calls.jsonl")).decode().split("\n") if l]
        self.assertEqual(len(seen), 18)
        self.assertEqual(len(set(seen)), 18)                                     # retries = 0: each key once
        self.assertEqual(recs[1]["error"], "RuntimeError")                       # class name only, no message
        self.assertNotIn("boom", json.dumps(recs[1]))
        self.assertEqual(IG.verify_run(run, BUILD)[0], [])


if __name__ == "__main__":
    unittest.main()
