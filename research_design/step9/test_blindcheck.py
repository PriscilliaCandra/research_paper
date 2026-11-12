"""Tests of the blind-check evaluator with SYNTHETIC worksheets built by an independent helper (Fractions) from pilot checker-bundle files.
No human verdict exists or is simulated as evidence: these rows only exercise the classifier. Run:
python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step9 -p test_blindcheck.py"""
import csv
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import unittest
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s9_blindcheck as BC  # noqa: E402

BUILD = os.path.join(RD, "step8_output", "step8_build_7a6bbe465c66")
BUNDLE = os.path.join(BUILD, "pilot", "checker_bundle")
COLS = ["opaque_id", "date_first_seen_registry_content_or_expected_states_UTC", "date_of_this_check_UTC", "family_read_from_text", "P_or_D_dist",
        "T_time_displayed", "T_time_seconds", "tau_F"]
for w in range(1, 6):
    COLS += [f"w{w}_transcribed", f"w{w}_value", f"w{w}_increase_km_or_implied_finish_s_2dp", f"w{w}_limit_km_2dp_or_rounded_finish_s", f"w{w}_percent_2dp_or_diff_s", f"w{w}_status"]
COLS += ["verdict_satisfied_violated_unclear", "notes"]


def rt(p):
    with open(p, "rb") as f:
        return f.read().decode("ascii")


def fmt(x, d):
    q = F(x)
    sgn = -1 if q < 0 else 1
    n = math.floor(abs(q) * 10 ** d + F(1, 2))
    return f"{'-' if sgn < 0 and n else ''}{n // 10 ** d}.{n % 10 ** d:0{d}d}" if d else str(sgn * n)


def perfect(oid):
    text = rt(os.path.join(BUNDLE, oid))
    row = {c: "" for c in COLS}
    row["opaque_id"] = oid
    if text.startswith("Constraint (weekly"):
        P = int(re.search(r"([0-9]+) percent", text).group(1))
        tot = [F(v) for v in re.findall(r"^\| [0-9] \| ([0-9.]+) \|$", text, flags=re.M)]
        row["P_or_D_dist"] = f"{P}%"
        viol = False
        for w in range(1, 6):
            row[f"w{w}_transcribed"] = f"{tot[w - 1]:.1f}" if False else re.findall(r"^\| [0-9] \| ([0-9.]+) \|$", text, flags=re.M)[w - 1]
            row[f"w{w}_value"] = row[f"w{w}_transcribed"]
            if w == 1:
                row["w1_status"] = "uncompared"
                continue
            inc, lim, pct = tot[w - 1] - tot[w - 2], F(P, 100) * tot[w - 2], (tot[w - 1] - tot[w - 2]) / tot[w - 2] * 100
            row[f"w{w}_increase_km_or_implied_finish_s_2dp"], row[f"w{w}_limit_km_2dp_or_rounded_finish_s"], row[f"w{w}_percent_2dp_or_diff_s"] = fmt(inc, 2), fmt(lim, 2), fmt(pct, 2)
            over = pct > P
            row[f"w{w}_status"] = "over cap" if over else "not over cap"
            viol |= over
    else:
        D = F(re.search(r"^Distance: ([0-9.]+) km$", text, flags=re.M).group(1))
        tm = re.search(r"^Target time: ([0-9]):([0-9]{2}):([0-9]{2})$", text, flags=re.M)
        T = int(tm.group(1)) * 3600 + int(tm.group(2)) * 60 + int(tm.group(3))
        tau = int(re.search(r"within ([0-9]+) seconds", text).group(1))
        paces = re.findall(r"^\| [0-9] \| ([0-9]:[0-9]{2}) \|$", text, flags=re.M)
        row.update(P_or_D_dist=f"{D} km", T_time_displayed=tm.group(0).split(": ")[1], T_time_seconds=str(T), tau_F=f"{tau} sec")
        viol = False
        for w in range(1, 6):
            m, s = paces[w - 1].split(":")
            p = int(m) * 60 + int(s)
            imp = D * p
            rnd = math.floor(imp + F(1, 2))
            row[f"w{w}_transcribed"], row[f"w{w}_value"] = paces[w - 1] + " min/km", str(p)
            row[f"w{w}_increase_km_or_implied_finish_s_2dp"], row[f"w{w}_limit_km_2dp_or_rounded_finish_s"], row[f"w{w}_percent_2dp_or_diff_s"] = fmt(imp, 2), str(rnd), str(rnd - T)
            over = abs(rnd - T) > tau
            row[f"w{w}_status"] = "|e|>tau" if over else "not"
            viol |= over
    row["verdict_satisfied_violated_unclear"] = "violated" if viol else "satisfied"
    return row


class TestBlindCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ids = sorted(os.listdir(BUNDLE))

    def classify(self, row):
        return BC.check_row(row, rt(os.path.join(BUNDLE, row["opaque_id"])))

    def first(self, fam_prefix):
        return next(i for i in self.ids if rt(os.path.join(BUNDLE, i)).startswith(fam_prefix))

    def test_perfect_rows_agree_for_both_families(self):
        for pre in ("Constraint (weekly", "Constraint (race"):
            self.assertEqual(self.classify(perfect(self.first(pre)))[0], "AGREE", pre)
        self.assertEqual({self.classify(perfect(i))[0] for i in self.ids}, {"AGREE"})

    def wrong_verdict(self, row, v):
        row["verdict_satisfied_violated_unclear"] = v
        return row

    def test_adj2_order(self):
        for pre in ("Constraint (weekly", "Constraint (race"):
            oid = self.first(pre)
            ref = perfect(oid)["verdict_satisfied_violated_unclear"]
            other = "satisfied" if ref == "violated" else "violated"
            self.assertEqual(self.classify(self.wrong_verdict(perfect(oid), other))[0], "REJECT")            # correct worksheet, wrong verdict: residual
            self.assertEqual(self.classify(self.wrong_verdict(perfect(oid), "unclear"))[0], "REJECT")
            r = self.wrong_verdict(perfect(oid), other); r["w3_transcribed"] = "99.9" if "weekly" in pre else "9:99"
            self.assertEqual(self.classify(r)[0], "CHECKER ERROR (transcription)")
            r = self.wrong_verdict(perfect(oid), other); r["w2_increase_km_or_implied_finish_s_2dp"] = "1" if "weekly" in pre else "300"
            self.assertEqual(self.classify(r)[0], "CHECKER ERROR (precision)")
            r = self.wrong_verdict(perfect(oid), other); r["w2_increase_km_or_implied_finish_s_2dp"] = "77.77"
            self.assertEqual(self.classify(r)[0], "CHECKER ERROR (arithmetic)")
            r = self.wrong_verdict(perfect(oid), other); r["w4_status"] = ""
            self.assertEqual(self.classify(r)[0], "REJECT")                                                  # completeness
            r = self.wrong_verdict(perfect(oid), "");
            self.assertEqual(self.classify(r)[0], "REJECT")                                                  # no verdict

    def test_unit_spelling_is_neutralized_but_foreign_units_are_not(self):
        n = BC.normalize
        self.assertEqual((n("4:27 min/km", "pace"), n("4:27 /km", "pace"), n("71.9km", "total"), n("71.9 km.", "total"), n("30.0 KM", "total")),
                         ("4:27", "4:27", "71.9", "71.9", "30.0"))
        self.assertEqual((n("10 %", "P"), n("12 sec", "tau"), n("12 s", "tau")), ("10", "12", "12"))
        for bad, f in (("4:27 min", "pace"), ("4:27 s/km", "pace"), ("4:27 %", "pace"), ("10 sec", "P"), ("71.9 kms", "total"), ("71.9 km..", "total"), ("71,9 km", "total")):
            self.assertNotEqual(n(bad, f), {"pace": "4:27", "P": "10", "total": "71.9"}[f] if f != "total" else "71.9", bad)

    def test_ambiguous_rendered_text_rejects_the_build(self):
        oid = self.first("Constraint (weekly")
        text = rt(os.path.join(BUNDLE, oid)).replace("10 percent", "11 percent", 1)
        self.assertEqual(BC.check_row(perfect(oid), text)[0], "REJECT")

    def test_evaluate_and_evidence_file(self):
        tmp = tempfile.mkdtemp(prefix="bc_")
        self.addCleanup(shutil.rmtree, tmp, True)
        sheet = os.path.join(tmp, "sheet.csv")
        s = io.StringIO()
        w = csv.DictWriter(s, COLS, lineterminator="\n")
        w.writeheader()
        for i in self.ids:
            w.writerow(perfect(i))
        with open(sheet, "xb") as f:
            f.write(s.getvalue().encode())
        ev = BC.evaluate(sheet, BUNDLE)
        self.assertEqual((ev["coverage_percent"], ev["build_verdict"], ev["counts"]), (100, "NO REJECT", {"AGREE": len(self.ids)}))
        out = os.path.join(tmp, "evidence.json")
        BC.write_evidence(out, sheet, ev, BUILD, "2026-10-28T09:00:00Z", "2026-10-20T09:00:00Z")
        with open(out, "rb") as fh:
            e = json.loads(fh.read())
        self.assertEqual(e["human_blind_check"]["adjudication"], "AGREE")
        with self.assertRaises(FileExistsError):
            BC.write_evidence(out, sheet, ev, BUILD, "2026-10-28T09:00:00Z", "2026-10-20T09:00:00Z")
        with self.assertRaises(ValueError):
            BC.write_evidence(os.path.join(tmp, "e2.json"), sheet, ev, BUILD, "28 Oct", "2026-10-20T09:00:00Z")      # no invented or malformed dates

    def test_reject_or_missing_rows_give_no_evidence(self):
        tmp = tempfile.mkdtemp(prefix="bc2_")
        self.addCleanup(shutil.rmtree, tmp, True)
        sheet = os.path.join(tmp, "sheet.csv")
        s = io.StringIO()
        w = csv.DictWriter(s, COLS, lineterminator="\n")
        w.writeheader()
        for i in self.ids[1:]:
            w.writerow(perfect(i))                              # first row missing
        with open(sheet, "xb") as f:
            f.write(s.getvalue().encode())
        ev = BC.evaluate(sheet, BUNDLE)
        self.assertEqual(ev["build_verdict"], "REJECT")
        with self.assertRaises(ValueError):
            BC.write_evidence(os.path.join(tmp, "e.json"), sheet, ev, BUILD, "2026-10-28T09:00:00Z", "2026-10-20T09:00:00Z")
        self.assertFalse(os.path.exists(os.path.join(tmp, "e.json")))


if __name__ == "__main__":
    unittest.main()
