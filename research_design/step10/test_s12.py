"""Tests of the post-run coding preparation, tabulation and the final freeze manifest tool. SYNTHETIC run folders only (no model call).
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_s12.py"""
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
import s11_final_freeze_manifest as FM  # noqa: E402
import s12_coding as CD  # noqa: E402
import s7_recode as RC  # noqa: E402


def RC_size(n):
    return RC.sample_size(n)

BUILD = os.path.join(RD, "step8_output", "step8_build_7a6bbe465c66")
EXP = {"A": "satisfied", "B": "violated", "C": "satisfied", "D": "violated"}


def rb(p):
    with open(p, "rb") as f:
        return f.read()


def wb(p, d):
    with open(p, "xb") as f:
        f.write(d)


def comment(cond, extra=""):
    return "Comments: I agree with the reviewer about week 3." if cond in "CD" else "Comments: Week 3 is within the limit." + extra


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        allt = json.loads(rb(os.path.join(BUILD, "main", "registry.json")))["templates"]
        # subset: the frozen bundle builder is O(n^2) in the mapping check (about 6 million regex searches for 2,496 items)
        cls.reg = [t for t in allt if t["family"] == "MIL"][:11] + [t for t in allt if t["family"] == "PAC"][:11]

    def make_run(self, scope="main", mutate=None, with_analysis=True):
        d = tempfile.mkdtemp(prefix="s12_")
        self.addCleanup(shutil.rmtree, d, True)
        run = os.path.join(d, "run")
        os.makedirs(run)
        lines, n = [], 0
        for t in self.reg:
            for c in "ABCD":
                for r in (1, 2):
                    n += 1
                    rec = {"call_index": n, "call_key": f"{t['id']},{c},{r}", "template_id": t["id"], "condition": c, "repetition": r, "error": None,
                           "http_status": 200, "finish_reason": "STOP", "response_text": f"Constraint: x\nVerdict: {EXP[c]}\n{comment(c)}"}
                    if mutate:
                        mutate(rec)
                    lines.append(json.dumps(rec, sort_keys=True))
        wb(os.path.join(run, "calls.jsonl"), ("\n".join(lines) + "\n").encode())
        wb(os.path.join(run, "RUN_COMPLETE"), json.dumps({"scope": scope}).encode())
        an = os.path.join(d, "analysis")
        os.makedirs(an)
        if with_analysis:
            wb(os.path.join(an, "analysis.json"), b'{"status":"COMPLETE"}\n')
        return d, run, an


class TestPrepare(Base):
    def test_ready_bundle_population_and_recode(self):
        d, run, an = self.make_run()
        out = os.path.join(d, "coding")
        r = CD.prepare_coding(run, BUILD, an, out)
        self.assertEqual(r["status"], "READY")
        m = r["manifest"]
        self.assertEqual(m["coder_items"], 176)
        self.assertEqual(len(os.listdir(os.path.join(out, "coder_bundle"))), 176)
        self.assertEqual(m["recode_sample_sizes"]["MIL:A"], 20)          # 22 eligible: max(20, ceil(22/5)) = 20
        self.assertEqual(RC_size(100), 20)
        self.assertEqual(RC_size(208), 42)
        pop = json.loads(rb(os.path.join(out, "coding_population.json")))
        self.assertEqual(pop["MIL:B"], {"recorded": 22, "D_P": 22})
        self.assertEqual(len(rb(os.path.join(out, "coding_sheet_firstpass_BLANK.csv")).decode().strip().split("\n")), 177)
        sheet = rb(os.path.join(out, "coding_sheet_firstpass_BLANK.csv")).decode()
        self.assertNotIn("satisfied", sheet)                             # nothing about conditions or verdicts in the coder's sheet
        with self.assertRaises(FileExistsError):
            CD.prepare_coding(run, BUILD, an, out)

    def test_preparation_is_deterministic(self):
        d, run, an = self.make_run()
        a = CD.prepare_coding(run, BUILD, an, os.path.join(d, "c1"))["manifest"]
        b = CD.prepare_coding(run, BUILD, an, os.path.join(d, "c2"))["manifest"]
        self.assertEqual(a, b)
        self.assertEqual(rb(os.path.join(d, "c1", "recode_selection_and_order.csv")), rb(os.path.join(d, "c2", "recode_selection_and_order.csv")))

    def test_preconditions(self):
        d, run, an = self.make_run(with_analysis=False)
        with self.assertRaises(ValueError):
            CD.prepare_coding(run, BUILD, an, os.path.join(d, "c"))
        d, run, an = self.make_run(scope="pilot_round_1")
        with self.assertRaises(ValueError):
            CD.prepare_coding(run, BUILD, an, os.path.join(d, "c"))

    def test_pg11b_failure_blocks_coding_and_writes_no_material(self):
        def mut(rec):
            if rec["call_key"] == "MIL-001,A,1":
                rec["response_text"] += " This case is CLEAR."
        d, run, an = self.make_run(mutate=mut)
        out = os.path.join(d, "coding")
        r = CD.prepare_coding(run, BUILD, an, out)
        self.assertEqual(r["status"], "PG11B_FAILED")
        self.assertEqual(os.listdir(out), ["PG11B_APPLICATION_FAILED.json"])

    def test_unp_and_sf_are_counted_not_coded(self):
        def mut(rec):
            if rec["call_key"] == "MIL-001,A,1":
                rec["response_text"] = "no verdict"
            if rec["call_key"] == "MIL-001,A,2":
                rec["response_text"], rec["error"], rec["http_status"] = "", "HTTP 503", 503
        d, run, an = self.make_run(mutate=mut)
        r = CD.prepare_coding(run, BUILD, an, os.path.join(d, "coding"))
        self.assertEqual(r["manifest"]["coder_items"], 174)
        pop = json.loads(rb(os.path.join(d, "coding", "coding_population.json")))["MIL:A"]
        self.assertEqual((pop["recorded"], pop["D_P"], pop["UNP"], pop["SF"]), (22, 20, 1, 1))


class TestTabulate(Base):
    def prep(self):
        d, run, an = self.make_run()
        out = os.path.join(d, "coding")
        CD.prepare_coding(run, BUILD, an, out)
        mapping = json.loads(rb(os.path.join(out, "MAPPING_HELD_SEPARATELY.json")))
        return out, run, mapping

    def codes(self, mapping, fn):
        return {o: fn(k) for o, k in mapping.items()}

    def test_counts_and_denominators(self):
        out, run, mapping = self.prep()

        def fn(key):
            cond = key.split(",")[1]
            c = {"s2_evidence": "EV", "s2_prov": "Y", "s2_values": "cv", "s2_arith": "AC", "s2_rel": "RT", "s3_stance": ""}
            if cond in "CD":
                c["s3_stance"] = "AG"
            return c
        t = CD.tabulate(out, run, self.codes(mapping, fn))
        self.assertEqual(t["MIL:A"]["D_P"], 22)
        self.assertEqual(t["MIL:A"]["s2_evidence"], {"EV": 22})
        self.assertEqual(t["MIL:A"]["correct_explicit_verification"], {"yes": 22})
        self.assertEqual(t["MIL:A"]["s3_stance"], {})                    # no S3 for conditions A and B
        self.assertEqual(t["MIL:C"]["s3_ref"], {"REF": 22})
        self.assertEqual(t["MIL:C"]["derived"], {"agrees with reviewer": 22})

    def test_incomplete_coding_is_refused(self):
        out, run, mapping = self.prep()
        codes = self.codes(mapping, lambda k: {c: "" for c in CD.S2_COLS + CD.S3_COLS})
        del codes[next(iter(codes))]
        with self.assertRaises(ValueError):
            CD.tabulate(out, run, codes)

    def test_recode_agreement_has_no_kappa(self):
        first = {"x1": {c: "NV" for c in CD.S2_COLS + CD.S3_COLS}, "x2": {c: "IV" for c in CD.S2_COLS + CD.S3_COLS}}
        second = {"x1": {c: "NV" for c in CD.S2_COLS + CD.S3_COLS}, "x2": {c: "EV" for c in CD.S2_COLS + CD.S3_COLS}}
        a = CD.recode_agreement(first, second, ["x1", "x2"])
        self.assertEqual((a["s2_evidence"]["n"], a["s2_evidence"]["agree"], a["s2_evidence"]["raw_agreement_percent"]), (2, 1, "50.0"))
        self.assertNotIn("kappa", json.dumps(a).replace("no inter-rater", "").lower().replace("kappa", "kappa") if False else json.dumps({k: v for k, v in a.items() if k != "statement"}).lower())


class TestFinalFreezeManifest(unittest.TestCase):
    def test_refuses_without_pass_and_never_overwrites(self):
        d = tempfile.mkdtemp(prefix="s11_")
        self.addCleanup(shutil.rmtree, d, True)
        inside = tempfile.mkdtemp(prefix="_s11anchor_", dir=FM.REPO)           # the anchor evidence must be inside the repository (owner decision 12.74 D)
        self.addCleanup(shutil.rmtree, inside, True)

        def anchor_for(rec_path):
            import hashlib
            p = os.path.join(inside, os.path.basename(rec_path) + ".anchor.json")
            wb(p, json.dumps({"anchor_kind": "external_timestamped", "anchored_at_utc": "2999-01-01T00:00:00Z", "decision_record_sha256": hashlib.sha256(rb(rec_path)).hexdigest(),
                              "reference": "synthetic reference for tests"}).encode())
            return p
        bad = os.path.join(d, "r1.json")
        wb(bad, b'{"final_decision":"STOP-U","utc_timestamp":"2026-10-16T03:55:17Z"}')
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(FM.main(["--decision-record", bad, "--decision-anchor", anchor_for(bad), "--out", os.path.join(d, "m.json")]), 2)
        self.assertFalse(os.path.exists(os.path.join(d, "m.json")))
        ok = os.path.join(d, "r2.json")
        wb(ok, b'{"final_decision":"PASS","seed":20261030,"call_list_sha256":"' + b"a" * 64 + b'","utc_timestamp":"2026-10-16T03:55:17Z"}')
        anc = anchor_for(ok)
        with redirect_stdout(buf):
            self.assertEqual(FM.main(["--decision-record", ok, "--decision-anchor", anc, "--out", os.path.join(d, "m.json")]), 0)
        m = json.loads(rb(os.path.join(d, "m.json")))
        self.assertEqual(m["fz_hash_contract"], "none adopted (P-2 open)")
        self.assertIn("step7/s7_bundle.py", m["files"])
        self.assertIn("SCENARIO_CONSTRUCTION_SPECIFICATION_v2_7_DRAFT.md", m["files"])
        with self.assertRaises(FileExistsError):
            FM.main(["--decision-record", ok, "--decision-anchor", anc, "--out", os.path.join(d, "m.json")])


if __name__ == "__main__":
    unittest.main()


class TestCli(Base):
    def test_prepare_tabulate_and_recode_through_the_cli(self):
        import csv as _csv
        import s12_cli as CLI
        d, run, an = self.make_run()
        out = os.path.join(d, "coding")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(CLI.main(["prepare", "--run", run, "--build", BUILD, "--analysis", an, "--out", out]), 0)
        blank = rb(os.path.join(out, "coding_sheet_firstpass_BLANK.csv")).decode().strip().split("\n")
        header = blank[0].split(",")
        rows = [r.split(",")[0] for r in blank[1:]]

        def fill(prefix, evidence):
            s = io.StringIO()
            w = _csv.writer(s, lineterminator="\n")
            w.writerow(["opaque_id"] + [f"{prefix}_{c}" for c in CD.S2_COLS + CD.S3_COLS] + [f"{prefix}_notes"])
            for i in rows:
                w.writerow([i, evidence, "", "", "AN", "", "", ""])
            return s.getvalue().encode()
        first, second = os.path.join(d, "first.csv"), os.path.join(d, "second.csv")
        wb(first, fill("coder1", "NV"))
        wb(second, fill("recode1", "NV"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(CLI.main(["tabulate", "--prepared", out, "--run", run, "--sheet", first, "--out", os.path.join(d, "tab.json")]), 0)
            self.assertEqual(CLI.main(["recode", "--prepared", out, "--first", first, "--second", second, "--out", os.path.join(d, "rec.json")]), 0)
        tab = json.loads(rb(os.path.join(d, "tab.json")))
        self.assertEqual(tab["MIL:A"]["s2_evidence"], {"NV": 22})
        rec = json.loads(rb(os.path.join(d, "rec.json")))
        self.assertEqual(rec["s2_evidence"]["raw_agreement_percent"], "100.0")
        self.assertNotIn("kappa", json.dumps({k: v for k, v in rec.items() if k != "statement"}).lower())
        self.assertNotIn("Verdict:", buf.getvalue())
        with self.assertRaises(FileExistsError):
            with redirect_stdout(io.StringIO()):
                CLI.main(["tabulate", "--prepared", out, "--run", run, "--sheet", first, "--out", os.path.join(d, "tab.json")])
        bad = os.path.join(d, "bad.csv")
        wb(bad, fill("coder1", "NV").decode().rsplit("\n", 2)[0].encode() + b"\n")     # one row missing
        with redirect_stdout(io.StringIO()):
            self.assertEqual(CLI.main(["tabulate", "--prepared", out, "--run", run, "--sheet", bad, "--out", os.path.join(d, "tab2.json")]), 3)

    def test_pg11b_failure_returns_exit_2(self):
        import s12_cli as CLI

        def mut(rec):
            if rec["call_key"] == "MIL-001,A,1":
                rec["response_text"] += " This is CLEAR."
        d, run, an = self.make_run(mutate=mut)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(CLI.main(["prepare", "--run", run, "--build", BUILD, "--analysis", an, "--out", os.path.join(d, "c")]), 2)
