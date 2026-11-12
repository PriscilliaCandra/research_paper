"""Tests of the Step 8 generator, gates, publication and verifier (decision record 12.28). Synthetic mutations only; builds go to
temporary folders outside the repository. Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step8 -p test_step8.py
Passing tests are not independent validation: the author of the code and of the tests is the same."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from fractions import Fraction as F
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s8_build as BLD  # noqa: E402
import s8_construct as K  # noqa: E402
import s8_gates as GT  # noqa: E402
import s8_verify as VER  # noqa: E402


def sha_file(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()



def rb(p):
    with open(p, "rb") as f:
        return f.read()


def wb(p, data):
    with open(p, "wb") as f:
        f.write(data)


def jl(p):
    return json.loads(rb(p).decode("utf-8"))


FZ = jl(os.path.join(RD, "PARAMETER_FREEZE_MANIFEST_FZ_v1.json"))
P = K.params_from_fz(FZ["register"])


class TestConstruction(unittest.TestCase):
    def test_rounding_helpers(self):
        self.assertEqual((K.nearest(F(5, 2)), K.nearest(F(7, 2)), K.nearest(F(-5, 2)), K.nearest(F(26, 10))), (2, 3, -3, 3))
        self.assertEqual((K.rnd_star(F(5, 2)), K.rnd_star(F(249, 100)), K.rnd_star(F(3, 1))), (3, 2, 3))

    def test_class_rule_balance(self):
        K4 = 4
        for b in range(26):
            self.assertEqual(sum(K.class_of(b, i, 0, K4) == "MILD" for i in range(K4)), 2)
        self.assertEqual([K.class_of(0, i, 0, 4) for i in range(4)], ["MILD", "MILD", "CLEAR", "CLEAR"])
        self.assertEqual([K.class_of(1, i, 0, 4) for i in range(4)], ["CLEAR", "CLEAR", "MILD", "MILD"])

    def test_counts_ids_and_order(self):
        for S, G, nm, npc in ((P["S_main"], P["G_main"], 104, 208), (P["S_pilot"], P["G_pilot"], 8, 16)):
            T = K.construct(P, S, G)
            self.assertEqual((sum(t["family"] == "MIL" for t in T), sum(t["family"] == "PAC" for t in T)), (nm, npc))
            self.assertEqual(T[0]["id"], "MIL-001")
            self.assertEqual(T[nm]["id"], "PAC-001")
            order = [(t["position_index"], t["direction"] or 0, t["base_index"]) for t in T if t["family"] == "PAC"]
            self.assertEqual(order, sorted(order))

    def test_hand_computed_mil_template(self):
        # S=200 (20.0 km), k=2, MILD: u = 10*200/100 = 20; mid(W_V)=0.75 -> 15; mid(W_MILD)=1.2 -> 24
        v, w = K.build_mil(P, 0, 200, 0, "MILD")
        self.assertEqual((v[:2], w[:2]), ([200, 215], [200, 224]))
        self.assertEqual(v[2:], w[2:])

    def test_hand_computed_pac_template(self):
        tv, tw, T = K.build_pac(P, 0, 310, 0, 0, "MILD")      # G=310, D=10: T=3100, r=0, m_NT=floor((20-0.5)/10)=1
        self.assertEqual(T, 3100)
        self.assertEqual(sum(a != b for a, b in zip(tv, tw)), 1)
        self.assertEqual([abs(x - 310) for x in tv[:1] + tv[2:]], [1, 1, 1, 1][:4])


class BuiltRegistry(unittest.TestCase):
    """One real build into a temporary folder, shared by the tests below."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="s8test_")
        cls.root, cls.summary = BLD.publish(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestBuildAndVerify(BuiltRegistry):
    def test_counts(self):
        self.assertEqual(self.summary["main"]["templates"], 312)
        self.assertEqual(self.summary["main"]["prompts"], 1248)
        self.assertEqual(self.summary["pilot"]["templates"], 24)
        self.assertEqual(self.summary["pilot"]["prompts"], 96)
        self.assertEqual(self.summary["main"]["call_list_lines"], 2496)
        self.assertEqual(len(os.listdir(os.path.join(self.root, "main", "prompts"))), 1248)
        self.assertEqual(len(os.listdir(os.path.join(self.root, "main", "checker_bundle"))), 624)
        with open(os.path.join(self.root, "pilot", "pilot_calls.txt")) as f:
            self.assertEqual(len(f.read().split("\n")) - 1, 18)

    def test_gate_report_has_no_failure(self):
        for kind in ("main", "pilot"):
            rep = jl(os.path.join(self.root, kind, "gate_report.json"))
            self.assertEqual(len(rep["numeric_gates_on_generator"]), 30)
            for sect in ("numeric_gates_on_generator", "numeric_gates_on_implementation_a_builders", "equivalence", "rendering_gates"):
                self.assertEqual(set(rep[sect].values()), {"PASS"}, (kind, sect))
            self.assertEqual(len(rep["rendering_gates"]), 15)          # PG1-PG10, PG11a, PG11b, PG12, A1b, A6b

    def test_verifier_passes(self):
        self.assertEqual(VER.verify(self.root), [])

    def test_manifest_inventory_is_complete_and_has_no_self_hash(self):
        man = jl(os.path.join(self.root, "manifest.json"))
        self.assertTrue(man["carries_no_self_hash"])
        self.assertNotIn("manifest.json", [e["path"] for e in man["inventory"]])
        self.assertEqual(len(man["inventory"]), len(set(e["path"] for e in man["inventory"])))

    def test_generator_code_hashes_in_the_registry_are_current(self):
        reg = jl(os.path.join(self.root, "main", "registry.json"))
        h = reg["provenance"]["generator_code_sha256"]
        for name in ("s8_construct.py", "s8_gates.py", "s8_build.py"):
            self.assertEqual(h["step8/" + name], sha_file(os.path.join(HERE, name)))
        self.assertEqual(h["implementation_a/impl_a.py"], BLD.IMPL_A_SHA)

    def test_published_target_is_not_overwritten(self):
        with self.assertRaises(FileExistsError):
            BLD.publish(self.tmp)

    def test_repeat_build_is_byte_identical(self):
        other = tempfile.mkdtemp(prefix="s8test2_")
        try:
            root2, _ = BLD.publish(other)
            a = jl(os.path.join(self.root, "manifest.json"))["inventory"]
            b = jl(os.path.join(root2, "manifest.json"))["inventory"]
            self.assertEqual(a, b)
        finally:
            shutil.rmtree(other, ignore_errors=True)


class TestVerifierDetectsTampering(BuiltRegistry):
    def copy(self):
        d = tempfile.mkdtemp(prefix="s8tamper_")
        root = os.path.join(d, "b")
        shutil.copytree(self.root, root)
        self.addCleanup(shutil.rmtree, d, True)
        return root

    def test_changed_prompt_byte(self):
        root = self.copy()
        p = os.path.join(root, "main", "prompts", "MIL-001-A.txt")
        wb(p, rb(p).replace(b"satisfied", b"satisfiet", 1))
        self.assertTrue(any("hash" in x for x in VER.verify(root)))

    def test_missing_and_extra_file(self):
        root = self.copy()
        os.remove(os.path.join(root, "main", "prompts", "PAC-001-B.txt"))
        wb(os.path.join(root, "extra.txt"), b"x")
        probs = VER.verify(root)
        self.assertTrue(any("inventory mismatch" in x for x in probs))

    def test_consistently_rehashed_prompt_that_breaks_the_pairing_is_still_caught(self):
        root = self.copy()
        pa = os.path.join(root, "main", "prompts", "MIL-001-A.txt")
        pb = os.path.join(root, "main", "prompts", "MIL-001-B.txt")
        wb(pa, rb(pb))               # Cond-A becomes identical to Cond-B
        h = hashlib.sha256(rb(pa)).hexdigest()
        tp = os.path.join(root, "main", "prompt_table.json")
        table = jl(tp)
        for e in table:
            if e["template_id"] == "MIL-001" and e["condition"] == "A":
                e["sha256"] = h
        wb(tp, VER.cj(table))
        mp = os.path.join(root, "manifest.json")
        man = jl(mp)
        for e in man["inventory"]:
            if e["path"] in ("main/prompts/MIL-001-A.txt", "main/prompt_table.json"):
                d = rb(os.path.join(root, *e["path"].split("/")))
                e["sha256"], e["bytes"] = hashlib.sha256(d).hexdigest(), len(d)
        wb(mp, VER.cj(man))
        probs = VER.verify(root)
        self.assertTrue(any("A1b" in x or "pair rule" in x or "contradicts" in x for x in probs), probs[:3])

    def test_changed_registry_value_is_caught(self):
        root = self.copy()
        rp = os.path.join(root, "main", "registry.json")
        wb(rp, rb(rp).replace(b'"class":"MILD"', b'"class":"CLEAR"', 1))
        self.assertTrue(VER.verify(root))


class TestGatesDetectDefects(unittest.TestCase):
    """Run the rendering gates on the pilot registry with one injected defect at a time."""
    @classmethod
    def setUpClass(cls):
        cls.T = K.construct(P, P["S_pilot"], P["G_pilot"])
        cls.pr = GT.render_all(cls.T, P)

    def run_with(self, mutate):
        pr = {k: dict(v) for k, v in self.pr.items()}
        mutate(pr)
        G, _, _ = GT.run_gates(self.T, pr, P, "0" * 64, P["windows"])
        return {g for g, v in G.items() if v}

    def test_clean_registry_passes_everything(self):
        self.assertEqual(self.run_with(lambda pr: None), set())

    def test_trailing_space_is_pg5(self):
        self.assertIn("PG5", self.run_with(lambda pr: pr["MIL-001"].__setitem__("A", pr["MIL-001"]["A"] + " ")))

    def test_forbidden_word_in_prompt_is_pg10(self):
        self.assertIn("PG10", self.run_with(lambda pr: pr["MIL-001"].__setitem__("B", pr["MIL-001"]["B"].replace("Does", "MILD Does", 1))))

    def test_second_numeric_change_is_pg1(self):
        def m(pr):
            pr["MIL-001"]["B"] = pr["MIL-001"]["B"].replace("| 1 | ", "| 1 | 9", 1)
        self.assertIn("PG1", self.run_with(m))

    def test_change_outside_the_target_row_is_caught(self):
        def m(pr):
            lines = pr["MIL-001"]["B"].split("\n")
            i = next(n for n, l in enumerate(lines) if l.startswith("| 1 |"))
            lines[i] = lines[i][:-3] + "9 |" if False else lines[i].replace(lines[i].split("|")[2].strip(), "99.9", 1)
            pr["MIL-001"]["B"] = "\n".join(lines)
        out = self.run_with(m)
        self.assertTrue(out & {"PG1", "PG3", "PG12", "PG4"})

    def test_modified_claim_is_pg7_pg9(self):
        out = self.run_with(lambda pr: pr["MIL-001"].__setitem__("C", pr["MIL-001"]["C"].replace("reviewer", "Reviewer", 1)))
        self.assertTrue(out & {"PG7", "PG9", "PG4", "PG5"})

    def test_duplicate_prompts_are_a1b(self):
        out = self.run_with(lambda pr: pr["MIL-001"].__setitem__("B", pr["MIL-001"]["A"]))
        self.assertIn("A1b", out)

    def test_wrong_class_window_is_a6b(self):
        T2 = [dict(t) for t in self.T]
        T2[0]["class"] = "CLEAR" if T2[0]["class"] == "MILD" else "MILD"
        G, _, _ = GT.run_gates(T2, self.pr, P, "0" * 64, P["windows"])
        self.assertTrue(G["A6b"])

    def test_non_canonical_numeral_is_pg12(self):
        def m(pr):
            pr["MIL-001"]["A"] = pr["MIL-001"]["A"].replace("| 1 | ", "| 1 | 0", 1)
        self.assertIn("PG12", self.run_with(m))


class TestAtomicPublicationAndSafety(unittest.TestCase):
    def test_a_failing_gate_publishes_nothing_and_writes_no_partial_tree(self):
        tmp = tempfile.mkdtemp(prefix="s8fail_")
        try:
            real = GT.run_gates

            def bad(*a, **k):
                G, f, m = real(*a, **k)
                G["PG1"] = ["injected failure"]
                return G, f, m
            with mock.patch.object(GT, "run_gates", bad):
                with self.assertRaises(BLD.Rejected):
                    BLD.publish(tmp)
            self.assertEqual(os.listdir(tmp), [])                      # no target, no temporary folder
            with mock.patch.object(GT, "run_gates", bad):
                self.assertEqual(BLD.main(["--out", tmp]), 2)
            self.assertEqual(os.listdir(tmp), ["step8_failures"])      # the log is in a separate folder
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_changed_frozen_input_is_refused(self):
        with mock.patch.object(BLD, "SPEC_SHA", "0" * 64):
            with self.assertRaises(BLD.Rejected):
                BLD.load_inputs()

    def test_no_network_or_model_module_is_imported(self):
        for name in ("s8_construct.py", "s8_gates.py", "s8_build.py", "s8_verify.py"):
            src = rb(os.path.join(HERE, name)).decode("utf-8")
            for bad in ("import socket", "import http", "import urllib", "import requests", "import httpx", "google.genai", "import asyncio"):
                self.assertNotIn(bad, src, (name, bad))

    def test_verifier_imports_no_project_module(self):
        src = rb(os.path.join(HERE, "s8_verify.py")).decode("utf-8")
        for bad in ("import s7", "import s8", "import impl_a", "prompt_specification"):
            self.assertNotIn(bad, src.split('"""', 2)[2], bad)

    def test_protected_files_are_unchanged(self):
        for rel, h in {"SCENARIO_CONSTRUCTION_SPECIFICATION_v2_7_DRAFT.md": BLD.SPEC_SHA,
                       "implementation_a/impl_a.py": BLD.IMPL_A_SHA,
                       "PARAMETER_FREEZE_MANIFEST_FZ_v1.json": "149445715a8280f4079bcd34d3594e25fe1b6b30118e382e96e94360c0faf338"}.items():
            self.assertEqual(sha_file(os.path.join(RD, rel)), h, rel)


if __name__ == "__main__":
    unittest.main()
