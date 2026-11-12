"""Adversarial regression tests for the P8 implementation (resume, provenance, cost stop, integrity) -- MOCK provider only, no network, no key, no model call.
Written 2026-10-14 as a review of s10_runner.py / s10_resume.py / s10_integrity.py. Each test names one way the first implementation could be misled or could
lose evidence. They decide no MD-08 rule: where a rule is missing the expectation is REFUSAL.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_adversarial_p8.py"""
import hashlib
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from fractions import Fraction as F
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_integrity as IG  # noqa: E402
import s10_resume as RS  # noqa: E402
import s10_runner as RN  # noqa: E402
RN.FZ_INDEX_ENFORCED = False  # E-1 (12.55): these tests exercise other machinery with mock data; enforcement is tested in test_fz_enforcement.py
import test_pilot_flow as PF  # noqa: E402
import test_resume as TR  # noqa: E402
import test_step10 as T10  # noqa: E402

BUILD = T10.BUILD


def transport_fail(key, prompt):
    return {"http_status": None, "text": "", "error": "TimeoutError"}


def status_fail(code):
    return lambda key, prompt: {"http_status": code, "text": "", "error": f"HTTP {code}"}


class Tamper:
    """Helpers that rewrite a run folder so that its hash chain stays CONSISTENT (a forger who understands the format)."""

    @staticmethod
    def rewrite_ledger(run, fn):
        p = os.path.join(run, "ledger.jsonl")
        entries = [json.loads(l) for l in PF.rb(p).decode().split("\n") if l]
        for e in entries:
            e.pop("prev", None)
        entries = fn(entries)
        prev, out = "0" * 64, b""
        for e in entries:
            e = dict(e, prev=prev)
            b = RN.cj(e)
            prev = RN.sha(b)
            out += b
        PF.wb(p, out)
        for m in ("RUN_COMPLETE", "RUN_INCOMPLETE"):
            mp = os.path.join(run, m)
            if os.path.exists(mp):
                s = json.loads(PF.rb(mp))
                s["ledger_final_hash"] = prev
                PF.wb(mp, RN.cj(s))

    @staticmethod
    def tamper_record(run, idx, fn):
        rp = os.path.join(run, "raw", f"{idx:05d}.json")
        rec = fn(json.loads(PF.rb(rp)))
        raw = RN.cj(rec)
        PF.wb(rp, raw)
        cp = os.path.join(run, "calls.jsonl")
        lines = [l for l in PF.rb(cp).split(b"\n") if l]
        lines[idx - 1] = raw.rstrip(b"\n")
        PF.wb(cp, b"\n".join(lines) + b"\n")

        def led(entries):
            for e in entries:
                if e.get("event") == "raw" and e.get("call_index") == idx:
                    e["raw_sha256"] = RN.sha(raw)
            return entries
        Tamper.rewrite_ledger(run, led)


class MainBase(TR.Base):
    def main_run(self, days, script):
        ev, au = T10.TestRunner.files(self, "main", days=days)
        reg = json.loads(PF.rb(os.path.join(BUILD, "main", "registry.json")))["templates"]
        text = RN.D.call_list([t["id"] for t in reg], 2)
        rec = os.path.join(self.tmp, f"rec_{days}.json")
        PF.wb(rec, (json.dumps({"final_decision": "PASS", "seed": 20261030, "call_list_sha256": RN.sha(text.encode("ascii"))}) + "\n").encode())
        out = os.path.join(self.tmp, f"main{days}")
        prov = RN.MockProvider(script)
        with redirect_stdout(io.StringIO()):
            s = RN.run("main", BUILD, out, prov, evidence_path=ev, auth_path=au, pilot_record_path=rec, sleeper=lambda x: None, verify_build=False)
        return out, ev, au, rec, s

    def main_ra(self, out, name="ra", basis="transport_halt", **over):
        return self.write_ra(out, name, scope="main", total=2496, basis=basis, **over)

    def main_resume(self, out, ev, au, rec, ra, prov):
        with redirect_stdout(io.StringIO()):
            return RS.resume(out, BUILD, prov, evidence_path=ev, auth_path=au, resume_auth_path=ra, pilot_record_path=rec, sleeper=lambda x: None, verify_build=False)


class TestMarkerAndHaltState(MainBase):
    def test_crash_between_the_resume_entry_and_the_marker_move_does_not_strand_the_run(self):
        out, ev, au, rec, s = self.main_run(70, transport_fail)
        self.assertEqual(s["recorded_calls"], 5)
        with mock.patch.object(RS.os, "replace", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.main_resume(out, ev, au, rec, self.main_ra(out, "raA"), RN.MockProvider(transport_fail))
        self.assertTrue(os.path.exists(os.path.join(out, "RUN_INCOMPLETE")))          # marker not moved yet, ledger already has the resume entry
        s2 = self.main_resume(out, ev, au, rec, self.main_ra(out, "raB"), RN.MockProvider(transport_fail))
        self.assertEqual(s2["status"], "RUN_INCOMPLETE")
        self.assertEqual(IG.verify_run(out, BUILD)[0], [])

    def test_a_halt_whose_marker_was_lost_is_still_governed_by_the_transport_only_rule(self):
        out, ev, au, rec, s = self.main_run(71, status_fail(503))
        os.remove(os.path.join(out, "RUN_INCOMPLETE"))                                  # as if the process died before writing the marker
        self.assert_refused(out, ev, au, self.main_ra(out, "ra1", basis="process_interruption", acknowledges_unspecified_basis=True),
                            "not transport-only", pilot_record_path=rec)

    def test_a_transport_halt_with_a_lost_marker_needs_the_transport_halt_basis(self):
        out, ev, au, rec, s = self.main_run(72, transport_fail)
        os.remove(os.path.join(out, "RUN_INCOMPLETE"))
        self.assert_refused(out, ev, au, self.main_ra(out, "ra1", basis="process_interruption", acknowledges_unspecified_basis=True),
                            "requires resume_basis 'transport_halt'", pilot_record_path=rec)
        s2 = self.main_resume(out, ev, au, rec, self.main_ra(out, "ra2"), RN.MockProvider(transport_fail))
        self.assertEqual((s2["status"], s2["recorded_calls"]), ("RUN_INCOMPLETE", 6))        # carried count of 5 + one new failure

    def test_http_429_500_503_halts_are_not_resumable(self):
        for code in (429, 500, 503):
            with self.subTest(code):
                out, ev, au, rec, s = self.main_run(73 + code, status_fail(code))
                self.assert_refused(out, ev, au, self.main_ra(out, f"ra{code}"), "not transport-only", pilot_record_path=rec)

    def test_marker_lost_after_a_cost_stop_still_refuses(self):
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "cost")
        seen = []

        def script(key, prompt):
            seen.append(key)
            r = T10.good(key, prompt)
            if len(seen) == 3:
                r["usage"] = {"promptTokenCount": 100, "totalTokenCount": 100 + 40_000_001}
            return r
        with redirect_stdout(io.StringIO()):
            RN.run("pilot_round_1", BUILD, out, RN.MockProvider(script), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        os.remove(os.path.join(out, "RUN_INCOMPLETE"))
        self.assert_refused(out, ev, au, self.write_ra(out), "OD-10 limit")


class TestSegments(MainBase):
    def test_an_existing_file_in_segments_is_never_overwritten(self):
        out, ev, au, rec, s = self.main_run(80, transport_fail)
        marker_sha = RN.file_sha(os.path.join(out, "RUN_INCOMPLETE"))
        os.makedirs(os.path.join(out, "segments"))
        PF.wb(os.path.join(out, "segments", "RUN_INCOMPLETE.segment2"), b"EVIDENCE-THAT-MUST-SURVIVE")
        self.main_resume(out, ev, au, rec, self.main_ra(out), RN.MockProvider(transport_fail))
        seg = os.path.join(out, "segments")
        self.assertEqual(PF.rb(os.path.join(seg, "RUN_INCOMPLETE.segment2")), b"EVIDENCE-THAT-MUST-SURVIVE")
        self.assertIn(marker_sha, {RN.file_sha(os.path.join(seg, f)) for f in os.listdir(seg)})

    def test_a_missing_or_altered_segment_blocks_any_further_resume(self):
        out, ev, au, rec, s = self.main_run(81, transport_fail)
        self.main_resume(out, ev, au, rec, self.main_ra(out, "raA"), RN.MockProvider(transport_fail))
        seg = os.path.join(out, "segments")
        name = os.listdir(seg)[0]
        saved = PF.rb(os.path.join(seg, name))
        os.remove(os.path.join(seg, name))
        self.assert_refused(out, ev, au, self.main_ra(out, "raB"), "segments", pilot_record_path=rec)
        PF.wb(os.path.join(seg, name), saved + b" ")                                      # altered marker copy
        self.assert_refused(out, ev, au, self.main_ra(out, "raC"), "segments", pilot_record_path=rec)
        PF.wb(os.path.join(seg, name), saved)
        s2 = self.main_resume(out, ev, au, rec, self.main_ra(out, "raD"), RN.MockProvider(transport_fail))
        self.assertEqual(s2["recorded_calls"], 7)


class TestExclusionAndReuse(TR.Base):
    def test_a_resume_started_while_the_run_is_alive_is_refused(self):
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "alive")
        box = {}

        def sleeper(x):
            if "done" in box:
                return
            box["done"] = True
            ra = self.write_ra(out, "raLive")
            try:
                RS.resume(out, BUILD, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, resume_auth_path=ra, sleeper=lambda y: None, verify_build=False)
                box["exc"] = None
            except RS.ResumeRefused as e:
                box["exc"] = e
        with redirect_stdout(io.StringIO()):
            s = RN.run("pilot_round_1", BUILD, out, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, sleeper=sleeper, verify_build=False)
        self.assertIsNotNone(box.get("exc"), "the nested resume must have been refused")
        self.assertIn("in progress", str(box["exc"]))
        self.assertEqual((s["status"], s["recorded_calls"]), ("RUN_COMPLETE", 18))
        self.assertEqual(IG.verify_run(out, BUILD)[0], [])

    def test_a_resume_authorization_cannot_be_reused_after_the_ledger_head_moved(self):
        out, ev, au, p1 = self.interrupted_pilot(patch=self.kill_on_event("raw", 5))
        ra1 = self.write_ra(out, "ra1")
        with self.kill_on_event("classified", 9), self.assertRaises(KeyboardInterrupt):
            self.resume(out, ev, au, ra1, RN.MockProvider(T10.good))
        self.assert_refused(out, ev, au, ra1, "current ledger head")

    def test_the_second_resume_of_a_finished_run_is_refused(self):
        out, ev, au, p1 = self.interrupted_pilot(patch=self.kill_on_event("raw", 5))
        self.resume(out, ev, au, self.write_ra(out, "ra1"), RN.MockProvider(T10.good))
        self.assert_refused(out, ev, au, self.write_ra(out, "ra2", remaining_calls=0), "COMPLETE")


class TestScoringAndHistory(TR.Base):
    def test_scoring_code_changed_between_segments_refuses_the_resume(self):
        out, ev, au, _ = self.interrupted_pilot(patch=self.kill_on_event("raw", 5))
        real = RN.code_hashes()
        changed = dict(real, **{"s10_scoring.py": "e" * 64})
        with mock.patch.object(RN, "code_hashes", return_value=changed):
            self.assert_refused(out, ev, au, self.write_ra(out), "scoring code")

    def test_prompt_hash_of_every_completed_record_is_checked_on_resume(self):
        out, ev, au, _ = self.interrupted_pilot(patch=self.kill_on_event("raw", 5))
        Tamper.tamper_record(out, 2, lambda r: dict(r, prompt_sha256="0" * 64))
        self.assert_refused(out, ev, au, self.write_ra(out), "prompt hash")


class TestVerifyRunCannotBeForged(TR.Base):
    def resumed(self):
        out, ev, au, _ = self.interrupted_pilot(patch=self.kill_on_event("raw", 5))
        self.resume(out, ev, au, self.write_ra(out), RN.MockProvider(T10.good))
        self.assertEqual(IG.verify_run(out, BUILD)[0], [])
        return out

    def test_false_counters_in_a_consistent_chain_are_reported(self):
        out = self.resumed()

        def fn(entries):
            for e in entries:
                if e.get("event") == "resume":
                    e["counters_carried"] = {"consecutive_sf": 3, "sf_total": 3, "completed": 5}
            return entries
        Tamper.rewrite_ledger(out, fn)
        self.assertTrue(any("counters" in x for x in IG.verify_run(out, BUILD)[0]))

    def test_false_previous_head_in_a_consistent_chain_is_reported(self):
        out = self.resumed()

        def fn(entries):
            for e in entries:
                if e.get("event") == "resume":
                    e["previous_ledger_head"] = "0" * 64
            return entries
        Tamper.rewrite_ledger(out, fn)
        self.assertTrue(any("previous ledger head" in x for x in IG.verify_run(out, BUILD)[0]))

    def test_a_removed_resume_entry_is_reported(self):
        out = self.resumed()
        Tamper.rewrite_ledger(out, lambda entries: [e for e in entries if e.get("event") != "resume"])
        P = IG.verify_run(out, BUILD)[0]
        self.assertTrue(any("resume" in x for x in P))

    def test_a_duplicate_result_line_and_a_swapped_pair_of_ledger_entries_are_reported(self):
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "dup")
        with redirect_stdout(io.StringIO()):
            RN.run("pilot_round_1", BUILD, out, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        cp = os.path.join(out, "calls.jsonl")
        lines = [l for l in PF.rb(cp).split(b"\n") if l]
        PF.wb(cp, b"\n".join(lines + [lines[-1]]) + b"\n")
        self.assertTrue(IG.verify_run(out, BUILD)[0])
        PF.wb(cp, b"\n".join(lines) + b"\n")
        self.assertEqual(IG.verify_run(out, BUILD)[0], [])

        def swap(entries):
            entries[3], entries[4] = entries[4], entries[3]
            return entries
        Tamper.rewrite_ledger(out, swap)
        self.assertTrue(any("not intent/raw/classified" in x for x in IG.verify_run(out, BUILD)[0]))


class TestCostAccountingAndDurability(TR.Base):
    def test_the_next_call_is_not_made_when_its_worst_case_could_cross_the_limit(self):
        ev, au = self.pilot_files()
        out = os.path.join(self.tmp, "guard")
        seen = []

        def script(key, prompt):
            seen.append(key)
            r = T10.good(key, prompt)
            if len(seen) == 3:
                r["usage"] = {"promptTokenCount": 100, "totalTokenCount": 100 + 39_996_000}      # about US$99.99, below the limit
            return r
        with redirect_stdout(io.StringIO()):
            s = RN.run("pilot_round_1", BUILD, out, RN.MockProvider(script), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        self.assertEqual((s["status"], s["recorded_calls"], len(seen)), ("RUN_INCOMPLETE", 3, 3))
        self.assertTrue(s["halt"].startswith("cost stop"))
        self.assertEqual(IG.verify_run(out, BUILD)[0], [])                                       # observed cost stayed below the limit

    def test_calls_without_usable_usage_are_charged_at_worst_case(self):
        st = RN.RunState()
        rec = {"call_index": 1, "usage": None, "response_text": "", "error": "TimeoutError", "http_status": None}
        st.add(rec, {"outcome": "SF"})
        st.add(dict(rec, usage={"promptTokenCount": 10}), {"outcome": "SF"})                       # incomplete usage
        worst = F(1, 50)
        self.assertEqual(st.unknown_calls, 2)
        self.assertEqual(st.cost_conservative_usd(worst), 2 * worst + F(10) * RN.PRICE_IN_USD_PER_M / 10 ** 6)       # the 10 reported prompt tokens are charged as reported

    def test_every_record_and_ledger_write_is_forced_to_disk(self):
        ev, au = self.pilot_files()
        with mock.patch.object(RN.os, "fsync", wraps=os.fsync) as fs, redirect_stdout(io.StringIO()):
            RN.run("pilot_round_1", BUILD, os.path.join(self.tmp, "dur"), RN.MockProvider(T10.good), evidence_path=ev, auth_path=au,
                   sleeper=lambda x: None, verify_build=False)
        self.assertGreaterEqual(fs.call_count, 18 * 5)                      # raw file, calls line and three ledger entries per call, plus the start entry


if __name__ == "__main__":
    unittest.main()
