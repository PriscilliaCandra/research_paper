"""Unit tests for Implementation A (numeric-stage gates of the frozen specification v2.7).

Run (from the repository root, without writing bytecode):
    python -B -m unittest discover -s research_design/implementation_a -p "test_impl_a.py" -v

Expected values are derived BY HAND from the specification text (section numbers in comments)
or from properties stated in the specification; they are not produced by the implementation.
Registers used for end-to-end tests are SYNTHETIC TEST REGISTERS, not candidates.

The recorded candidate lists (main and pilot) are used ONLY for: input representation, list
properties computed with independent arithmetic, and the construction of individual templates
(whose values were computed by hand). No test runs the gates on a candidate register: that is
the Step 4 dry run, which is not part of this task.
"""
import hashlib
import os
import sys
import unittest
from dataclasses import replace
from fractions import Fraction as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import impl_a as A  # noqa: E402

SPEC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                         "SCENARIO_CONSTRUCTION_SPECIFICATION_v2_7_DRAFT.md")

WIN = A.WindowSet("0.60", "0.90", "1.10", "1.30", "1.50", "2.00")
SIZING = A.Sizing(z="1.96", hw_star="0.2", C_max=2496, R_min=2, R_cap=2, n_boot=10000, L_small=10, level="0.95")


def make_reg(**over):
    kw = dict(P=10, N=5, tau_F=50, rho_NT="0.4", windows=WIN, D_dist=10,
              S_list=(243, 257), G_list=(345, 346),     # SYNTHETIC test lists (not candidates)
              X_min=100, X_max=999, p_min=270, p_max=420, G_min=300, G_max=349,
              Tm_min=2700, Tm_max=4200, sizing=SIZING)
    kw.update(over)
    return A.Register(**kw)


TOY = make_reg()


def mil_tpl(V, W, i=0, cls="MILD", b=0):
    return A.MilTemplate(b, V[0], i, cls, tuple(V), tuple(W))


def pac_tpl(D, T, tau, G, vtok, wtok, i=0, s=0, cls="MILD", b=0, T2=None, tau2=None, D2=None):
    return A.PacTemplate(b, G, i, s, cls,
                         A.PacMember(A.exact(D), T, tau, tuple(vtok)),
                         A.PacMember(A.exact(D if D2 is None else D2), T if T2 is None else T2,
                                     tau if tau2 is None else tau2, tuple(wtok)))


def passes(res, gate):
    return res[gate] == []


# ======================================================================================
class TestSpecBinding(unittest.TestCase):
    def test_spec_hash_is_the_frozen_baseline(self):
        if not os.path.exists(SPEC_PATH):
            self.skipTest("specification file not found")
        with open(SPEC_PATH, "rb") as fh:
            h = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(h, A.SPEC_SHA256)
        self.assertEqual(h, "d5fcffadd1045fc0c86a7c84eb8d51592d807576b6a51b712cef025164082ca8")

    def test_gate_inventory_matches_section_3_7(self):
        # §3.7: 30 numeric-stage gates = M1-M11 (11) + Q1-Q12 (12) + A1a, A2, A3, A4, A5, A6a, A7 (7)
        self.assertEqual(len(A.NUMERIC_GATES), 30)
        self.assertEqual(len(set(A.NUMERIC_GATES)), 30)
        self.assertEqual([g for g in A.NUMERIC_GATES if g.startswith("M")], [f"M{n}" for n in range(1, 12)])
        self.assertEqual([g for g in A.NUMERIC_GATES if g.startswith("Q")], [f"Q{n}" for n in range(1, 13)])
        self.assertEqual([g for g in A.NUMERIC_GATES if g.startswith("A")], ["A1a", "A2", "A3", "A4", "A5", "A6a", "A7"])


# ======================================================================================
class TestPrimitives(unittest.TestCase):
    def test_nearest_ties_toward_lower_integer(self):  # §4 helper
        cases = [("17/2", 8), ("35/4", 9), ("33/4", 8), ("19/2", 9), ("1/2", 0), ("-1/2", -1),
                 ("-3/2", -2), ("2", 2), ("11/4", 3), ("7/4", 2)]
        for y, want in cases:
            self.assertEqual(A.nearest(F(y)), want, y)

    def test_rnd_star_ties_up(self):  # §3.3: floor(y + 1/2)
        cases = [("5/2", 3), ("7/2", 4), ("-5/2", -2), ("249/100", 2), ("3161/2", 1581), ("-1/2", 0)]
        for y, want in cases:
            self.assertEqual(A.rnd_star(F(y)), want, y)

    def test_no_floating_point_input(self):  # §3.1
        for bad in (0.4, True):
            with self.assertRaises(TypeError):
                A.exact(bad)
        with self.assertRaises(TypeError):
            A.WindowSet(0.6, "0.9", "1.1", "1.3", "1.5", "2.0")
        with self.assertRaises(TypeError):
            make_reg(rho_NT=0.4)
        self.assertEqual(A.exact("0.4"), F(2, 5))
        self.assertEqual(A.exact("3/2"), F(3, 2))

    def test_render_pace(self):  # §3.1, §8.3 item 3: f"{p//60}:{p%60:02d}", 60 <= p <= 599
        for p, s in ((267, "4:27"), (300, "5:00"), (359, "5:59"), (60, "1:00"), (599, "9:59"), (341, "5:41")):
            self.assertEqual(A.render_pace(p), s)
        for bad in (59, 600, -1):
            with self.assertRaises(A.SpecError):
                A.render_pace(bad)

    def test_render_mileage(self):  # §8.3: str(X//10) + "." + str(X%10)
        for X, s in ((349, "34.9"), (5, "0.5"), (1004, "100.4"), (0, "0.0"), (9999, "999.9")):
            self.assertEqual(A.render_mileage(X), s)
        with self.assertRaises(A.SpecError):
            A.render_mileage(10000)

    def test_render_time(self):  # §8.3: H:MM:SS, T < 36000
        for T, s in ((3400, "0:56:40"), (3661, "1:01:01"), (35999, "9:59:59"), (0, "0:00:00")):
            self.assertEqual(A.render_time(T), s)
        with self.assertRaises(A.SpecError):
            A.render_time(36000)

    def test_render_distance_canonical(self):  # §8.3 item 4 and its illustrations
        self.assertEqual(A.render_distance(F(10)), "10")
        self.assertEqual(A.render_distance(F("10.5")), "10.5")
        self.assertEqual(A.render_distance(F("21.0975")), "21.0975")
        self.assertEqual(A.render_distance(F("7.5")), "7.5")
        self.assertEqual(A.render_distance(F("10.00")), "10")   # '10.00' is non-canonical text for 10
        with self.assertRaises(A.SpecError):
            A.render_distance(F(1, 3))

    def test_n_dec_and_finite_decimal(self):
        self.assertEqual(A.n_dec(F(10)), 0)
        self.assertEqual(A.n_dec(F("10.5")), 1)
        self.assertEqual(A.n_dec(F("21.0975")), 4)
        self.assertTrue(A.has_finite_decimal(F(3, 8)))
        self.assertFalse(A.has_finite_decimal(F(1, 3)))
        self.assertFalse(A.has_finite_decimal(F(1, 6)))

    def test_digits(self):
        self.assertEqual([A.digits(n) for n in (0, 9, 10, 99, 100)], [1, 1, 2, 2, 3])
        with self.assertRaises(A.SpecError):
            A.digits(-1)


# ======================================================================================
class TestClassRule(unittest.TestCase):
    """§9.2 rule checked against the printed tables of §9.4 (independent expected values)."""
    MIL_TABLE = {0: "MMCC", 1: "CCMM", 2: "CMMC", 3: "MCCM"}
    PAC_TABLE = {0: ("MMCC", "CMMC"), 1: ("CCMM", "MCCM"), 2: ("CMMC", "CCMM"), 3: ("MCCM", "MMCC")}

    def test_mil_table_k4_l4(self):
        for b, row in self.MIL_TABLE.items():
            got = "".join(A.class_of(b, i, 0, 4)[0] for i in range(4))
            self.assertEqual(got, row, f"base {b}")

    def test_pac_table_k4_l4(self):
        for b, (r0, r1) in self.PAC_TABLE.items():
            for s, row in enumerate((r0, r1)):
                got = "".join(A.class_of(b, i, s, 4)[0] for i in range(4))
                self.assertEqual(got, row, f"base {b} dir {s}")

    def test_odd_k_not_defined(self):
        with self.assertRaises(A.SpecError):
            A.class_of(0, 0, 0, 5)

    def test_exact_balance_properties_9_3(self):
        # §9.3 items 1-3 (proved for even K >= 4, even L); counted directly here
        for K in (4, 6, 8, 10):
            for L in (2, 4, 6, 8):
                for s in (0, 1):
                    for b in range(L):
                        self.assertEqual(sum(A.class_of(b, i, s, K) == "MILD" for i in range(K)), K // 2)
                    for i in range(K):
                        self.assertEqual(sum(A.class_of(b, i, s, K) == "MILD" for b in range(L)), L // 2)

    def test_dir_shift_never_makes_directions_equal_or_complementary(self):  # §9.3 item 5
        for K in (4, 6, 8, 10, 12):
            for b in range(4):
                same = sum(A.class_of(b, i, 0, K) == A.class_of(b, i, 1, K) for i in range(K))
                self.assertEqual(same, K - 2 * (K // 4))


# ======================================================================================
class TestMilConstruction(unittest.TestCase):
    """Hand-computed templates: P=10, rho_NT=0.4 (gamma=1/25), windows 0.60-0.90 / 1.10-1.30 / 1.50-2.00."""

    def test_s200_k2_mild_and_clear(self):
        reg = make_reg(S_list=(200, 210))
        t = A.build_mil_template(reg, 0, 0, "MILD")
        # u=20; dV=nearest(15)=15; dMILD=nearest(24)=24; tail 215-8=207, 207-8=199, 199-7=192
        self.assertEqual(t.valid, (200, 215, 207, 199, 192))
        self.assertEqual(t.viol, (200, 224, 207, 199, 192))
        c = A.build_mil_template(reg, 0, 0, "CLEAR")   # dCLEAR = nearest(35) = 35
        self.assertEqual(c.viol, (200, 235, 207, 199, 192))

    def test_s200_k3(self):
        reg = make_reg(S_list=(200, 210))
        t = A.build_mil_template(reg, 0, 1, "MILD")
        # X2=200+8=208; u=20.8; dV=nearest(15.6)=16; dMILD=nearest(24.96)=25; tail 224-8=216, 216-8=208
        self.assertEqual(t.valid, (200, 208, 224, 216, 208))
        self.assertEqual(t.viol, (200, 208, 233, 216, 208))
        c = A.build_mil_template(reg, 0, 1, "CLEAR")   # nearest(36.4) = 36
        self.assertEqual(c.viol, (200, 208, 244, 216, 208))

    def test_s200_k4(self):
        reg = make_reg(S_list=(200, 210))
        t = A.build_mil_template(reg, 0, 2, "MILD")
        # X2=208, X3=216; u=21.6; dV=nearest(16.2)=16; dMILD=nearest(25.92)=26; X5=232-9=223
        self.assertEqual(t.valid, (200, 208, 216, 232, 223))
        self.assertEqual(t.viol, (200, 208, 216, 242, 223))
        c = A.build_mil_template(reg, 0, 2, "CLEAR")   # nearest(37.8) = 38
        self.assertEqual(c.viol, (200, 208, 216, 254, 223))

    def test_synthetic_bases(self):
        # S=243, k=2: u=24.3; dV=nearest(18.225)=18; dMILD=nearest(29.16)=29; tail 261-10, 251-10, 241-9
        t = A.build_mil_template(TOY, 0, 0, "MILD")
        self.assertEqual(t.valid, (243, 261, 251, 241, 232))
        self.assertEqual(t.viol, (243, 272, 251, 241, 232))
        # S=257, k=2, CLEAR: u=25.7; dV=nearest(19.275)=19; dCLEAR=nearest(44.975)=45; tail 276-11, 265-10, 255-10
        c = A.build_mil_template(TOY, 1, 0, "CLEAR")
        self.assertEqual(c.valid, (257, 276, 265, 255, 245))
        self.assertEqual(c.viol, (257, 302, 265, 255, 245))

    def test_recorded_pilot_base_construction_only(self):
        # S=455 (recorded pilot candidate) k=2: u=45.5; dV=nearest(34.125)=34; dMILD=nearest(54.6)=55
        # tail 489-19, 470-18, 452-18.  CONSTRUCTION ONLY: no gate is evaluated here.
        reg = make_reg(S_list=(455, 465))
        t = A.build_mil_template(reg, 0, 0, "MILD")
        self.assertEqual(t.valid, (455, 489, 470, 452, 434))
        self.assertEqual(t.viol, (455, 510, 470, 452, 434))

    def test_ramp_and_dnt(self):  # §4 helpers; gamma = 1/25
        g = F(1, 25)
        self.assertEqual(A.ramp(200, g), 208)
        self.assertEqual(A.ramp(24, g), 25)     # floor(0.96)=0 -> max(1,0)=1
        self.assertEqual(A.ramp(25, g), 26)
        self.assertEqual(A.ramp(50, g), 52)


class TestMilGatesOnHandExamples(unittest.TestCase):
    def test_hand_examples_pass_m1_to_m10(self):
        reg = make_reg(S_list=(200, 210))
        for i in range(4):
            for cls in ("MILD", "CLEAR"):
                t = A.build_mil_template(reg, 0, i, cls)
                res = A.mil_element_gates(reg, t)
                self.assertEqual({g: m for g, m in res.items() if m}, {}, f"i={i} {cls}")

    def test_hand_computed_target_rho(self):
        reg = make_reg(S_list=(200, 210))
        t = A.build_mil_template(reg, 0, 2, "MILD")
        self.assertEqual(A.mil_target_rho_violating(reg, t), F(26) / F("21.6"))   # 26/21.6 = 65/54


class TestMilGateBoundaries(unittest.TestCase):
    reg = make_reg()

    def base(self, x2_valid, x2_viol, prev=210, tail=(200, 190, 180)):
        # N=5, target week 2 (i=0): X1=prev, X2 differs, weeks 3-5 identical decreasing tail
        return mil_tpl((prev, x2_valid) + tail, (prev, x2_viol) + tail)

    def test_M3_valid_side_exact_threshold(self):
        # X_prev=210 -> u=21. valid needs u - delta >= 1  => delta <= 20
        for delta, ok in ((20, True), (21, False), (19, True)):
            res = A.mil_element_gates(self.reg, self.base(210 + delta, 210 + 23))
            self.assertEqual(res["M3"] == [] or all("valid" not in m for m in res["M3"]), ok, delta)

    def test_M3_violating_side_exact_threshold(self):
        # violating needs delta - u >= 1 => delta >= 22
        for delta, ok in ((22, True), (21, False), (23, True)):
            res = A.mil_element_gates(self.reg, self.base(210 + 15, 210 + delta))
            self.assertEqual(all("violating" not in m for m in res["M3"]), ok, delta)

    def test_M3_non_integer_cap(self):
        # X_prev=205 -> u=20.5: valid 19 ok (1.5), 20 not (0.5); violating 22 ok (1.5), 21 not (0.5)
        r = lambda dv, dw: A.mil_element_gates(self.reg, self.base(205 + dv, 205 + dw, prev=205))
        self.assertEqual([m for m in r(19, 22)["M3"]], [])
        self.assertTrue(any("valid" in m for m in r(20, 22)["M3"]))
        self.assertTrue(any("violating" in m for m in r(19, 21)["M3"]))

    def test_M2_window_edges_are_inclusive(self):
        # X_prev=200 -> u=20, rho = delta/20
        def m2(dv, dw, cls="MILD"):
            t = mil_tpl((200, 200 + dv, 190, 180, 170), (200, 200 + dw, 190, 180, 170), cls=cls)
            return A.mil_element_gates(self.reg, t)["M2"]
        self.assertEqual(m2(12, 22), [])                      # 0.60 and 1.10 exactly
        self.assertTrue(m2(11, 22))                           # 0.55 < 0.60
        self.assertEqual(m2(18, 26), [])                      # 0.90 and 1.30 exactly
        self.assertTrue(m2(19, 26))                           # 0.95 > 0.90
        self.assertTrue(m2(18, 21))                           # 1.05 < 1.10
        self.assertTrue(m2(18, 27))                           # 1.35 > 1.30
        self.assertEqual(m2(15, 30, "CLEAR"), [])             # 1.50 exactly
        self.assertEqual(m2(15, 40, "CLEAR"), [])             # 2.00 exactly
        self.assertTrue(m2(15, 29, "CLEAR"))
        self.assertTrue(m2(15, 41, "CLEAR"))

    def test_M1_integrality_and_positive_increases(self):
        t = mil_tpl((200, 200, 190, 180, 170), (200, 224, 190, 180, 170))
        self.assertTrue(any("Delta_V=0" in m for m in A.mil_element_gates(self.reg, t)["M1"]))
        t = mil_tpl((200, F(431, 2), 190, 180, 170), (200, 224, 190, 180, 170))
        self.assertTrue(A.mil_element_gates(self.reg, t)["M1"])
        t = mil_tpl((200, 215, 190, 180, 170), (200, 224, 190, 180, 170))
        self.assertEqual(A.mil_element_gates(self.reg, t)["M1"], [])

    def test_M4_non_target_guard_boundary(self):
        # target week 3 (i=1); transition 1 is non-target: X1=200 (u=20)
        def m4(x2):
            V = (200, x2, x2 + 20, 215, 205)
            W = (200, x2, x2 + 28, 215, 205)
            return A.mil_element_gates(self.reg, mil_tpl(V, W, i=1))["M4"]
        self.assertEqual(m4(219), [])         # increase 19, u-19 = 1 (exact boundary)
        self.assertTrue(m4(220))              # increase 20, u-20 = 0
        self.assertEqual(m4(190), [])         # a decrease is always fine

    def test_M5_strict_unique_argmax(self):
        # target k=3: target rho 16/21.9 vs earlier rho
        ok = mil_tpl((200, 210, 226, 220, 210), (200, 210, 235, 220, 210), i=1)
        # earlier transition 200->210 has rho 0.5; target 16/21 = 0.762 -> strict max
        self.assertEqual(A.mil_element_gates(self.reg, ok)["M5"], [])
        bigger_other = mil_tpl((200, 220, 236, 230, 220), (200, 220, 246, 230, 220), i=1)  # other rho = 1.0 > target
        self.assertTrue(A.mil_element_gates(self.reg, bigger_other)["M5"])
        tie = mil_tpl((200, 220, 242, 230, 220), (200, 220, 250, 230, 220), i=1)           # 20/20 vs 22/22: both 1.0
        self.assertTrue(any("valid" in m for m in A.mil_element_gates(self.reg, tie)["M5"]))

    def test_M6_exactly_one_token_differs_at_k(self):
        same = mil_tpl((200, 215, 207, 199, 192), (200, 215, 207, 199, 192))
        self.assertTrue(A.mil_element_gates(self.reg, same)["M6"])
        two = mil_tpl((200, 215, 207, 199, 192), (200, 224, 207, 198, 192))
        self.assertTrue(A.mil_element_gates(self.reg, two)["M6"])
        wrong_pos = mil_tpl((200, 215, 207, 199, 192), (200, 215, 208, 199, 192))
        self.assertTrue(A.mil_element_gates(self.reg, wrong_pos)["M6"])

    def test_M7_digit_width_pairs(self):
        def m7(a, b):
            return A.mil_element_gates(self.reg, mil_tpl((a - 5, a, 1, 2, 3), (a - 5, b, 1, 2, 3)))["M7"]
        self.assertTrue(m7(98, 100))       # 9.8 km -> 10.0 km : floor(98/10)=9 vs 10
        self.assertTrue(m7(99, 100))       # 9.9 -> 10.0
        self.assertEqual(m7(100, 101), [])
        self.assertEqual(m7(989, 999), [])  # floor 98 vs 99
        self.assertTrue(m7(999, 1000))     # 99.9 -> 100.0 : floor 99 vs 100
        self.assertEqual(m7(95, 98), [])   # both one digit (floor 9)

    def test_M8_bounds_inclusive(self):
        def m8(x):
            return A.mil_element_gates(self.reg, mil_tpl((x, x + 15, x + 7, x - 1, x - 8), (x, x + 24, x + 7, x - 1, x - 8)))["M8"]
        self.assertTrue(m8(100))       # includes x-8=92 < X_min
        low = mil_tpl((100, 115, 107, 101, 100), (100, 124, 107, 101, 100))
        self.assertEqual(A.mil_element_gates(self.reg, low)["M8"], [])       # 100 == X_min is allowed
        under = mil_tpl((100, 115, 107, 101, 99), (100, 124, 107, 101, 99))
        self.assertTrue(A.mil_element_gates(self.reg, under)["M8"])         # 99 < X_min
        high = mil_tpl((990, 999, 990, 980, 970), (990, 999, 990, 980, 970))
        self.assertEqual(A.mil_element_gates(self.reg, high)["M8"], [])      # 999 == X_max
        over = mil_tpl((990, 1000, 990, 980, 970), (990, 1000, 990, 980, 970))
        self.assertTrue(A.mil_element_gates(self.reg, over)["M8"])

    def test_M9_no_flat_step(self):
        flat = mil_tpl((200, 215, 215, 199, 192), (200, 224, 215, 199, 192))
        self.assertTrue(any("flat" in m for m in A.mil_element_gates(self.reg, flat)["M9"]))

    def test_M10_downstream_identical(self):
        bad = mil_tpl((200, 215, 207, 199, 192), (200, 224, 207, 199, 190))
        self.assertTrue(A.mil_element_gates(self.reg, bad)["M10"])
        first_after_target = mil_tpl((200, 215, 207, 199, 192), (200, 224, 206, 199, 192))   # only week k+1 differs
        self.assertTrue(A.mil_element_gates(self.reg, first_after_target)["M10"])
        self.assertEqual(A.mil_element_gates(self.reg, mil_tpl((200, 215, 207, 199, 192), (200, 224, 207, 199, 192)))["M10"], [])

    def test_malformed_template_fails_every_gate(self):
        t = mil_tpl((200, 215, 207), (200, 224, 207))
        res = A.mil_element_gates(self.reg, t)
        self.assertTrue(all(res[g] for g in A.MIL_ELEMENT_GATES))


class TestM11(unittest.TestCase):
    def test_class_separation_rule(self):
        self.assertEqual(A.class_separation({"MILD": [F("1.1"), F("1.3")], "CLEAR": [F("1.5"), F(2)]}), [])
        self.assertTrue(A.class_separation({"MILD": [F("1.3")], "CLEAR": [F("1.3")]}))     # equality fails (strict)
        self.assertTrue(A.class_separation({"MILD": [F("1.4")], "CLEAR": [F("1.3")]}))
        self.assertTrue(A.class_separation({"MILD": [F("1.2")], "CLEAR": []}))             # interpretation I-4

    def test_on_built_synthetic_templates(self):
        mil, _ = A.build_registry(TOY)
        self.assertEqual(A.gate_M11(TOY, mil), [])


# ======================================================================================
class TestPacConstruction(unittest.TestCase):
    """Hand-computed templates, D_dist=10, tau_F=50, rho_NT=0.4 (m_NT=1)."""

    def test_m_NT_boundary(self):  # §5.2 step 2: floor((0.4*tau - 1/2)/10)
        self.assertEqual(A.m_NT_value(make_reg(tau_F=26)), 0)    # (10.4-0.5)/10 = 0.99
        self.assertEqual(A.m_NT_value(make_reg(tau_F=27)), 1)    # (10.8-0.5)/10 = 1.03
        self.assertEqual(A.m_NT_value(make_reg(tau_F=50)), 1)    # 1.95
        self.assertEqual(A.m_NT_value(make_reg(tau_F=100)), 3)   # 3.95

    def test_g340_k2_slower(self):
        reg = make_reg(G_list=(340, 341))
        t = A.build_pac_template(reg, 0, 0, 0, "MILD")
        self.assertEqual(t.valid.T_time, 3400)
        # non-target weeks 1,3,4,5 -> d = +1,-1,+1,-1 ; target: dV=+4, dMILD=+6
        self.assertEqual(t.valid.tokens, (341, 344, 339, 341, 339))
        self.assertEqual(t.viol.tokens, (341, 346, 339, 341, 339))
        c = A.build_pac_template(reg, 0, 0, 0, "CLEAR")         # nearest(8.75) = 9
        self.assertEqual(c.viol.tokens, (341, 349, 339, 341, 339))

    def test_g341_k3_faster(self):
        reg = make_reg(G_list=(340, 341))
        t = A.build_pac_template(reg, 1, 1, 1, "MILD")
        self.assertEqual(t.valid.T_time, 3410)
        # non-target weeks 1,2,4,5 -> +1,-1,+1,-1 ; sigma=-1: dV=-4, dMILD=-6, dCLEAR=-9
        self.assertEqual(t.valid.tokens, (342, 340, 337, 342, 340))
        self.assertEqual(t.viol.tokens, (342, 340, 335, 342, 340))
        c = A.build_pac_template(reg, 1, 1, 1, "CLEAR")
        self.assertEqual(c.viol.tokens, (342, 340, 332, 342, 340))

    def test_synthetic_g345(self):
        t = A.build_pac_template(TOY, 0, 0, 0, "MILD")
        self.assertEqual(t.valid.T_time, 3450)
        self.assertEqual(t.valid.tokens, (346, 349, 344, 346, 344))
        self.assertEqual(t.viol.tokens, (346, 351, 344, 346, 344))

    def test_tie_toward_lower_and_cyclic_non_target_steps(self):
        # tau=100, D=10: m_NT=3; targets nearest(7.5)=7 (tie->lower), nearest(12)=12, nearest(17.5)=17
        reg = make_reg(tau_F=100, G_list=(330, 331))
        t = A.build_pac_template(reg, 0, 0, 0, "MILD")
        # non-target weeks 1,3,4,5: d_j = (-1)^(j+1) * (1 + ((j-1) mod 3)) = +1, -2, +3, -1
        self.assertEqual(t.valid.tokens, (331, 337, 328, 333, 329))
        self.assertEqual(t.viol.tokens, (331, 342, 328, 333, 329))
        self.assertEqual(A.build_pac_template(reg, 0, 0, 0, "CLEAR").viol.tokens, (331, 347, 328, 333, 329))

    def test_non_integer_distance_rounding_stage(self):
        # D=21/2, G=301: D*G=3160.5 -> T=rnd*=3161, r=T-D*G=1/2.  sigma=+1, k=2.
        reg = make_reg(D_dist="21/2", G_list=(301, 302))
        t = A.build_pac_template(reg, 0, 0, 0, "MILD")
        self.assertEqual(t.valid.T_time, 3161)
        # dV=nearest((37.5+0.5)/10.5=3.619)=4, dMILD=nearest((60+0.5)/10.5=5.76)=6, dCLEAR=nearest(8.38)=8
        self.assertEqual(t.valid.tokens, (302, 305, 300, 302, 300))
        self.assertEqual(t.viol.tokens, (302, 307, 300, 302, 300))
        q = A.pace_quantities(t.valid, 301)
        self.assertEqual(q["x"], [F(10), F("41.5"), F(-11), F(10), F(-11)])   # x = D*d - r
        self.assertEqual(q["e"], [10, 42, -11, 10, -11])                      # F=3202.5 -> 3203 (ties up)
        qm = A.pace_quantities(t.viol, 301)
        self.assertEqual(qm["x"][1], F("62.5"))
        self.assertEqual(qm["e"][1], 63)
        self.assertEqual(abs(qm["x"][1]) / 50, F("1.25"))

    def test_construction_error_when_m_NT_below_one(self):
        with self.assertRaises(A.ConstructionError):
            A.build_pac_template(make_reg(tau_F=26), 0, 0, 0, "MILD")

    def test_identity_e_equals_rnd_star_of_x(self):  # §3.3 proved identity (T integer)
        for D in (F(1), F(10), F(21, 2), F(21, 10), F(7, 2), F(1, 2), F("21.0975")):
            for G in (300, 301, 317, 349):
                T = A.rnd_star(D * G)
                m = A.PacMember(D, T, 50, tuple(range(G - 30, G + 31)))
                q = A.pace_quantities(m, G)
                for x, e in zip(q["x"], q["e"]):
                    self.assertEqual(e, A.rnd_star(x))
                # x_w = D*d_w - r
                r = T - D * G
                for d, x in zip(q["d"], q["x"]):
                    self.assertEqual(x, D * d - r)


class TestPacGates(unittest.TestCase):
    # D_dist = 1, tau = 20, G = 330 so that x_w = d_w exactly; rho_NT=0.4 -> m_NT = floor((8-0.5)/1) = 7
    # T_time = D*G = 330 here, so the plausibility range of T_time is relaxed for these isolated gate tests
    reg = make_reg(D_dist=1, tau_F=20, G_list=(330, 331), S_list=(243, 257), Tm_min=100)

    def tpl(self, dv, dm, cls="MILD", nt=(1, -1, 1, -1), G=330, i=0, s=0, **kw):
        sig = 1 if s == 0 else -1
        k = i + 2
        nontarget = [w for w in range(1, 6) if w != k]
        base = {w: G + d for w, d in zip(nontarget, nt)}
        v = tuple(G + sig * dv if w == k else base[w] for w in range(1, 6))
        m = tuple(G + sig * dm if w == k else base[w] for w in range(1, 6))
        return pac_tpl(1, G, 20, G, v, m, i=i, s=s, cls=cls, **kw)

    def g(self, tpl, gate):
        return A.pac_element_gates(self.reg, tpl)[gate]

    def test_reference_template_passes_every_element_gate(self):
        # d_V=12 (rho 0.6), d_MILD=22 (rho 1.1); m_V=12 > m_NT=7; tokens 329..352 stay in minute 5
        res = A.pac_element_gates(self.reg, self.tpl(12, 22))
        self.assertEqual({k: v for k, v in res.items() if v}, {})

    def test_Q3_window_edges(self):
        self.assertEqual(self.g(self.tpl(12, 22), "Q3"), [])          # 0.60 and 1.10 exactly
        self.assertTrue(self.g(self.tpl(11, 22), "Q3"))
        self.assertEqual(self.g(self.tpl(18, 26), "Q3"), [])          # 0.90 and 1.30
        self.assertTrue(self.g(self.tpl(19, 26), "Q3"))
        self.assertTrue(self.g(self.tpl(18, 21), "Q3"))
        self.assertTrue(self.g(self.tpl(18, 27), "Q3"))
        self.assertEqual(self.g(self.tpl(15, 30, "CLEAR"), "Q3"), [])
        self.assertEqual(self.g(self.tpl(15, 40, "CLEAR"), "Q3"), [])
        self.assertTrue(self.g(self.tpl(15, 29, "CLEAR"), "Q3"))
        self.assertTrue(self.g(self.tpl(15, 41, "CLEAR"), "Q3"))

    def test_Q4_guard_exact_boundaries(self):
        self.assertEqual(self.g(self.tpl(19, 21), "Q4"), [])          # valid |x|=tau-1 ok; violating tau+1 ok
        self.assertTrue(self.g(self.tpl(20, 21), "Q4"))                # valid |x|=tau fails (equality)
        self.assertTrue(self.g(self.tpl(15, 20), "Q4"))                # violating |x|=tau fails
        self.assertEqual(self.g(self.tpl(15, 22), "Q4"), [])
        # a non-target week at tau-1 passes, at tau fails (both members)
        self.assertEqual(self.g(self.tpl(15, 25, nt=(19, -1, 1, -1)), "Q4"), [])
        self.assertTrue(self.g(self.tpl(15, 25, nt=(20, -1, 1, -1)), "Q4"))

    def test_Q5_strict_unique_maximum_and_mV_greater_than_mNT(self):
        self.assertTrue(self.g(self.tpl(7, 22), "Q5"))                 # m_V = 7 = m_NT
        self.assertEqual(self.g(self.tpl(8, 22), "Q5"), [])
        self.assertTrue(self.g(self.tpl(8, 22, nt=(8, -1, 1, -1)), "Q5"))   # tie with a non-target week

    def test_Q6_nonzero_deviations(self):
        zero = self.tpl(12, 22, nt=(0, -1, 1, -1))
        self.assertTrue(any("d_1 = 0" in m for m in self.g(zero, "Q6")))
        # |x| < 1 with a non-zero d: D = 1/2, G = 330 -> T = 165, token 331 -> x = 1/2
        reg = make_reg(D_dist="1/2", tau_F=20, Tm_min=100, Tm_max=4200)
        t = pac_tpl(F(1, 2), 165, 20, 330, (331, 342, 329, 331, 329), (331, 352, 329, 331, 329))
        self.assertTrue(any("|x_1|" in m for m in A.pac_element_gates(reg, t)["Q6"]))

    def test_Q7_same_minute_boundaries(self):
        def q7(G, v, w):
            t = pac_tpl(1, G, 20, G, v, w)
            return A.pac_element_gates(self.reg, t)["Q7"]
        self.assertEqual(q7(300, (300, 310, 301, 302, 303), (300, 310, 301, 302, 303)), [])
        self.assertTrue(q7(300, (299, 310, 301, 302, 303), (299, 310, 301, 302, 303)))      # 4:59
        self.assertEqual(q7(359, (359, 350, 349, 350, 351), (359, 350, 349, 350, 351)), [])
        self.assertTrue(q7(359, (360, 350, 349, 350, 351), (360, 350, 349, 350, 351)))      # 6:00
        self.assertTrue(q7(59, (59, 59, 59, 59, 59), (59, 59, 59, 59, 59)))                 # minute digit 0
        self.assertTrue(q7(600, (600, 600, 600, 600, 600), (600, 600, 600, 600, 600)))      # minute 10

    def test_Q7_token_form_equals_exact_form(self):
        # two independent formulas of §5.4 Q7 agree for every G and every token near it
        for G in range(60, 600):
            for p in range(max(0, G - 70), G + 71):
                self.assertEqual(p // 60 == G // 60, A.same_minute_exact_form(G, [p]), (G, p))

    def test_Q8_single_token_same_length_same_minute(self):
        base_v, base_w = (331, 342, 329, 331, 329), (331, 352, 329, 331, 329)
        self.assertEqual(self.g(pac_tpl(1, 330, 20, 330, base_v, base_w), "Q8"), [])
        two = pac_tpl(1, 330, 20, 330, base_v, (331, 352, 329, 332, 329))
        self.assertTrue(self.g(two, "Q8"))
        none = pac_tpl(1, 330, 20, 330, base_v, base_v)
        self.assertTrue(self.g(none, "Q8"))
        crossing = pac_tpl(1, 330, 20, 330, (331, 359, 329, 331, 329), (331, 360, 329, 331, 329))   # 5:59 vs 6:00
        self.assertTrue(self.g(crossing, "Q8"))
        unrenderable = pac_tpl(1, 330, 20, 330, (331, 342, 329, 331, 329), (331, 600, 329, 331, 329))
        self.assertTrue(self.g(unrenderable, "Q8"))

    def test_Q9_byte_identical_fields(self):
        self.assertEqual(self.g(self.tpl(12, 22), "Q9"), [])
        self.assertTrue(self.g(self.tpl(12, 22, T2=331), "Q9"))
        self.assertTrue(self.g(self.tpl(12, 22, tau2=21), "Q9"))
        self.assertTrue(self.g(self.tpl(12, 22, D2=2), "Q9"))
        t = self.tpl(12, 22)
        changed = replace(t, viol=replace(t.viol, tokens=(332,) + t.viol.tokens[1:]))
        self.assertTrue(self.g(changed, "Q9"))

    def test_Q10_rounded_verdict_matches_exact_class(self):
        # D=2.1, G=300 -> T=630, token 324: x=50.4 (exact violated for tau=50) but e=rnd*(680.4)-630=50 (not violated)
        reg = make_reg(D_dist="21/10", tau_F=50, Tm_min=100, Tm_max=4200)
        v = (301, 304, 299, 301, 299)
        w = (301, 324, 299, 301, 299)
        t = pac_tpl(F(21, 10), 630, 50, 300, v, w, cls="CLEAR")
        self.assertTrue(A.pac_element_gates(reg, t)["Q10"])
        # D=10, x=50 exactly on the violating member: rounded and exact verdicts both 'satisfied' -> fails the class
        reg2 = make_reg()
        t2 = pac_tpl(10, 3400, 50, 340, (341, 344, 339, 341, 339), (341, 345, 339, 341, 339))
        self.assertTrue(A.pac_element_gates(reg2, t2)["Q10"])      # |x|=50 is not > 50
        good = A.build_pac_template(make_reg(G_list=(340, 341)), 0, 0, 0, "MILD")
        self.assertEqual(A.pac_element_gates(make_reg(G_list=(340, 341)), good)["Q10"], [])

    def test_Q1_and_Q11_bounds(self):
        reg = make_reg(D_dist=1, tau_F=20)
        def q(T, gate):
            t = pac_tpl(1, T, 20, 330, (331, 342, 329, 331, 329), (331, 352, 329, 331, 329))
            t2 = replace(t, valid=replace(t.valid, T_time=T), viol=replace(t.viol, T_time=T))
            return A.pac_element_gates(reg, t2)[gate]
        self.assertEqual(q(2700, "Q1"), [])
        self.assertTrue(q(2699, "Q1"))
        self.assertEqual(q(4200, "Q1"), [])
        self.assertTrue(q(4201, "Q1"))
        # G bounds
        def g(G):
            t = pac_tpl(1, G, 20, G, (G + 1, G + 12, G - 1, G + 1, G - 1), (G + 1, G + 22, G - 1, G + 1, G - 1))
            return A.pac_element_gates(reg, t)["Q11"]
        self.assertTrue(any("G=" in m for m in g(299)))
        self.assertFalse(any("G=" in m for m in g(300)))
        self.assertFalse(any("G=" in m for m in g(349)))
        self.assertTrue(any("G=" in m for m in g(350)))
        # token bounds
        t = pac_tpl(1, 349, 20, 349, (350, 361, 348, 350, 348), (350, 421, 348, 350, 348))
        self.assertTrue(any("421" in m for m in A.pac_element_gates(reg, t)["Q11"]))
        t = pac_tpl(1, 349, 20, 349, (350, 361, 348, 350, 348), (350, 420, 348, 350, 348))
        self.assertFalse(any("token" in m for m in A.pac_element_gates(reg, t)["Q11"]))

    def test_Q2_reports_m_NT(self):
        reg = make_reg(tau_F=26)
        t = pac_tpl(10, 3400, 26, 340, (341, 344, 339, 341, 339), (341, 346, 339, 341, 339))
        self.assertTrue(A.pac_element_gates(reg, t)["Q2"])
        self.assertEqual(A.pac_element_gates(make_reg(tau_F=27), t)["Q2"], [])

    def test_Q12_class_separation(self):
        t1 = A.build_pac_template(make_reg(G_list=(340, 341)), 0, 0, 0, "MILD")
        t2 = A.build_pac_template(make_reg(G_list=(340, 341)), 0, 1, 0, "CLEAR")
        self.assertEqual(A.gate_Q12([t1, t2]), [])
        self.assertTrue(A.gate_Q12([t1, t1]))     # no CLEAR template (I-4)


# ======================================================================================
class TestReadingInvariance(unittest.TestCase):
    def test_hand_example_all_readings_agree(self):
        reg = make_reg(S_list=(200, 210))
        t = A.build_mil_template(reg, 0, 0, "MILD")
        self.assertEqual(A.reading_invariance_mil(reg, t), [])
        p = A.build_pac_template(make_reg(G_list=(340, 341)), 0, 0, 0, "CLEAR")
        self.assertEqual(A.reading_invariance_pac(p), [])

    def test_equality_with_the_cap_splits_the_readings(self):
        # valid week with increase exactly equal to the cap u=20: over-cap says satisfied, at-or-over says violated
        v = A.mil_reading_verdicts(10, (200, 220, 210, 200, 190))
        self.assertEqual(v[("floor", "over_cap")], "satisfied")
        self.assertEqual(v[("floor", "at_or_over_cap")], "violated")
        # PAC: |e| == tau splits inclusive / exclusive
        m = A.PacMember(F(10), 3400, 50, (345, 340, 340, 340, 340))     # week 1: F=3450, e=50=tau
        pv = A.pac_reading_verdicts(m)
        self.assertEqual(pv[("floor", "inclusive")], "satisfied")
        self.assertEqual(pv[("floor", "exclusive")], "violated")

    def test_proposition_GM_enumeration(self):
        # |delta - t| >= 1 => every one of the six readings equals the exact verdict (delta > t)
        for X in range(100, 400):
            t = F(10 * X, 100)
            for delta in range(-30, 80):
                if abs(delta - t) < 1:
                    continue
                exact = "violated" if delta > t else "satisfied"
                v = A.mil_reading_verdicts(10, (X, X + delta))
                self.assertEqual(set(v.values()), {exact}, (X, delta))

    def test_proposition_GP_enumeration(self):
        # |x| <= tau-1 => all 12 readings satisfied ; |x| >= tau+1 => all violated
        for D in (F(1), F(10), F(21, 2), F(21, 10), F(7, 2)):
            for G in (300, 331):
                T = A.rnd_star(D * G)
                for p in range(G - 60, G + 61):
                    x = D * p - T
                    for tau in (5, 20, 50):
                        if abs(x) <= tau - 1:
                            want = "satisfied"
                        elif abs(x) >= tau + 1:
                            want = "violated"
                        else:
                            continue
                        v = A.pac_reading_verdicts(A.PacMember(D, T, tau, (p,)))
                        self.assertEqual(set(v.values()), {want}, (D, G, p, tau))


# ======================================================================================
class TestRegistryGatesAndOrder(unittest.TestCase):
    def test_synthetic_register_counts_and_spot_templates(self):
        # K=4, L=2 -> MIL K*L = 8 templates, PAC 2*K*L = 16 (§9.2)
        mil, pac = A.build_registry(TOY)
        self.assertEqual((len(mil), len(pac)), (8, 16))
        self.assertEqual(sorted(t.cls for t in mil if t.b == 0), ["CLEAR", "CLEAR", "MILD", "MILD"])
        self.assertEqual("".join(t.cls[0] for t in sorted((t for t in mil if t.b == 0), key=lambda t: t.i)), "MMCC")
        self.assertEqual("".join(t.cls[0] for t in sorted((t for t in pac if t.b == 1 and t.s == 1), key=lambda t: t.i)), "MCCM")

    def test_synthetic_register_screening_conditions_hold(self):
        # sufficient conditions of §4 / §5.5 verified by hand-derived numbers
        sm = A.screen_mil(TOY)
        self.assertEqual(sm["U_M"], F(2430, 100))
        self.assertTrue(all(sm[k] for k in ("RC-M2", "RC-M3", "RC-M4", "RC-M5", "RC-M6", "RC-M7")))
        self.assertEqual(sm["Xprev_max"], 288)
        sp = A.screen_pac(TOY)
        self.assertTrue(all(sp[k] for k in ("RC-P2", "RC-P3", "RC-P4", "RC-P5", "RC-P6", "RC-P7", "RC-P8")))
        self.assertEqual(sp["m_star"], 10)

    def test_full_run_on_synthetic_register(self):
        # By the sufficiency statements of §4 (RC-M1..M7) and §5.5/5.6 (RC-P*, G_adm) this register passes.
        rep = A.run_numeric_gates(TOY)
        self.assertEqual(set(rep.outcomes), set(A.NUMERIC_GATES))
        self.assertTrue(rep.built)
        self.assertEqual(rep.failing(), [], {g: o.failures[:2] for g, o in rep.outcomes.items() if o.failures})
        self.assertTrue(rep.passed)
        self.assertEqual((rep.n_mil, rep.n_pac), (8, 16))
        self.assertEqual(A.a3_all_classes(TOY), [])    # stricter reading (I-2) also clean for this register

    def test_A5_failure_stops_before_construction(self):
        rep = A.run_numeric_gates(make_reg(P=0))
        self.assertFalse(rep.built)
        self.assertFalse(rep.passed)
        self.assertEqual(len(rep.outcomes), 30)
        self.assertFalse(rep.outcomes["A5"].passed)
        self.assertTrue(all(not rep.outcomes[g].evaluated for g in A.NUMERIC_GATES if g not in ("A5",)))

    def test_A2_failures(self):
        for N in (3, 4, 6):       # K = 2, 3, 5 : K < 4 or odd
            rep = A.run_numeric_gates(make_reg(N=N))
            self.assertFalse(rep.outcomes["A2"].passed, N)
            self.assertFalse(rep.built)
        self.assertEqual(A.gate_A2(make_reg(N=5), 2, 2), [])
        self.assertEqual(A.gate_A2(make_reg(N=7), 4, 6), [])
        self.assertTrue(A.gate_A2(make_reg(N=5), 3, 2))      # L_M odd
        self.assertTrue(A.gate_A2(make_reg(N=5), 2, 5))      # L_P odd

    def test_M8_failure_is_localised(self):
        rep = A.run_numeric_gates(make_reg(X_max=300))
        self.assertIn("M8", rep.failing())
        self.assertIn("A3", rep.failing())
        self.assertNotIn("M1", rep.failing())
        self.assertNotIn("Q4", rep.failing())

    def test_Q2_A4_failure_when_tau_too_small(self):
        rep = A.run_numeric_gates(make_reg(tau_F=26))
        self.assertFalse(rep.built)
        self.assertFalse(rep.outcomes["Q2"].passed)
        self.assertFalse(rep.outcomes["A4"].passed)
        self.assertFalse(rep.outcomes["Q3"].evaluated)

    def test_plausibility_failure_reaches_A3(self):
        rep = A.run_numeric_gates(make_reg(G_list=(349, 350)))      # 350 > G_max
        self.assertFalse(rep.outcomes["Q11"].passed)
        self.assertFalse(rep.outcomes["A3"].passed)
        self.assertTrue(any("350" in m for m in rep.outcomes["A3"].failures))

    def test_A1a_duplicate_plans(self):
        mil, pac = A.build_registry(TOY)
        self.assertEqual(A.gate_A1a(mil, pac), [])
        self.assertTrue(A.gate_A1a(mil + [mil[0]], pac))
        self.assertTrue(A.gate_A1a(mil, pac + [pac[3]]))

    def test_A6a_class_matrix(self):
        mil, pac = A.build_registry(TOY)
        self.assertEqual(A.gate_A6a(TOY, mil, pac), [])
        flipped = [replace(mil[0], cls="CLEAR")] + mil[1:]
        self.assertTrue(A.gate_A6a(TOY, flipped, pac))
        swapped = list(pac)
        swapped[0] = replace(pac[0], cls="CLEAR")
        self.assertTrue(A.gate_A6a(TOY, mil, swapped))

    def test_A7_cluster_integrity(self):
        mil, pac = A.build_registry(TOY)
        self.assertEqual(A.gate_A7(TOY, mil, pac), [])
        self.assertTrue(A.gate_A7(TOY, mil[1:], pac))           # one slot missing
        self.assertTrue(A.gate_A7(TOY, mil + [mil[0]], pac))    # a slot twice
        self.assertTrue(A.gate_A7(TOY, mil, pac[:-1]))
        # slot imbalance with per-cluster counts intact: b1's class at slot 0 -> MILD, at slot 2 -> CLEAR
        by_key = {(t.b, t.i): t for t in mil}
        moved = [replace(t, cls="MILD") if (t.b, t.i) == (1, 0) else replace(t, cls="CLEAR") if (t.b, t.i) == (1, 2) else t
                 for t in mil]
        self.assertEqual(sum(t.cls == "MILD" for t in moved if t.b == 1), 2)       # cluster counts unchanged
        self.assertTrue(any("slot (0,0)" in m for m in A.gate_A7(TOY, moved, pac)))
        self.assertEqual(by_key[(1, 0)].cls, "CLEAR")


class TestA5(unittest.TestCase):
    def a5(self, **over):
        return A.gate_A5(make_reg(**over))

    def test_synthetic_register_passes(self):
        r = self.a5()
        self.assertEqual((r["a"], r["b"], r["c"]), ([], [], []))

    def test_a_integers_and_positivity(self):
        for over in ({"P": 0}, {"N": -5}, {"tau_F": 0}, {"P": 10.0}, {"N": True}, {"rho_NT": 0}):
            self.assertTrue(self.a5(**over)["a"], over)

    def test_a_bounds_and_representation_limits(self):
        for over in ({"X_min": 1000}, {"p_min": 59}, {"p_max": 600}, {"Tm_min": 0}, {"Tm_max": 36000},
                     {"G_min": 350}, {"p_min": 421}, {"Tm_min": 4201}):
            self.assertTrue(self.a5(**over)["a"], over)
        for over in ({"p_min": 60}, {"p_max": 599}, {"Tm_min": 1}, {"Tm_max": 35999}):
            self.assertEqual(self.a5(**over)["a"], [], over)

    def test_a_distance_must_have_finite_decimal(self):
        self.assertTrue(self.a5(D_dist=F(1, 3))["a"])
        self.assertEqual(self.a5(D_dist="21.0975")["a"], [])
        self.assertTrue(self.a5(D_dist=0)["a"])

    def test_a_lists_are_even_length_positive_progressions(self):
        self.assertTrue(self.a5(S_list=(243, 257, 271))["a"])             # odd length
        self.assertTrue(self.a5(S_list=(243,))["a"])
        self.assertTrue(self.a5(S_list=(243, 257, 272, 286))["a"])         # not a progression
        self.assertTrue(self.a5(S_list=(257, 243))["a"])                   # negative step
        self.assertTrue(self.a5(S_list=(243, 243))["a"])                   # zero step
        self.assertTrue(self.a5(G_list=(0, 1))["a"])                       # non-positive element
        self.assertEqual(self.a5(S_list=(243, 257, 271, 285))["a"], [])

    def test_a_window_well_formedness(self):
        bad = A.WindowSet("0.9", "0.6", "1.1", "1.3", "1.5", "2.0")       # lo > hi
        self.assertTrue(self.a5(windows=bad)["a"])
        zero = A.WindowSet("0", "0.9", "1.1", "1.3", "1.5", "2.0")
        self.assertTrue(self.a5(windows=zero)["a"])

    def test_b_sizing_domains(self):
        base = dict(z="1.96", hw_star="0.2", C_max=2496, R_min=2, R_cap=2, n_boot=10000, L_small=10, level="0.95")
        self.assertEqual(self.a5(sizing=A.Sizing(**base))["b"], [])
        for k, v in (("z", 0), ("hw_star", 0), ("C_max", 0), ("C_max", -1), ("C_max", 2496.5), ("R_min", 1),
                     ("R_cap", 1), ("n_boot", 0), ("L_small", 0), ("level", 1), ("level", 0)):
            s = dict(base)
            s[k] = v
            self.assertTrue(self.a5(sizing=A.Sizing(**s))["b"], (k, v))
        self.assertTrue(self.a5(sizing=None)["b"])
        self.assertTrue(self.a5(sizing=A.Sizing(**{**base, "C_max": None}))["b"])

    def test_c_window_sides_and_strict_ordering(self):
        self.assertTrue(self.a5(windows=A.WindowSet("0.6", "1.0", "1.1", "1.3", "1.5", "2.0"))["c"])   # v_hi = 1
        self.assertTrue(self.a5(windows=A.WindowSet("0.6", "0.9", "1.0", "1.3", "1.5", "2.0"))["c"])   # mi_lo = 1
        self.assertTrue(self.a5(windows=A.WindowSet("0.6", "0.9", "1.1", "1.5", "1.5", "2.0"))["c"])   # mi_hi = c_lo
        self.assertEqual(self.a5(windows=A.WindowSet("0.6", "0.9", "1.1", "1.49", "1.5", "2.0"))["c"], [])

    def test_c_not_evaluated_when_a_window_bound_is_missing(self):
        r = self.a5(windows=A.WindowSet("0.6", "0.9", None, "1.3", "1.5", "2.0"))
        self.assertTrue(r["a"])
        self.assertTrue(any("not evaluated" in m for m in r["c"]))

    def test_c_is_applied_to_every_declared_set(self):
        bad = A.WindowSet("0.6", "1.0", "1.1", "1.3", "1.5", "2.0")
        r = self.a5(windows=None, windows_mil=WIN, windows_pac=bad)
        self.assertEqual(r["a"], [])
        self.assertTrue(any("PAC" in m for m in r["c"]))
        self.assertFalse(any("MIL" in m for m in r["c"]))
        self.assertEqual(self.a5(windows=None, windows_mil=WIN, windows_pac=WIN)["c"], [])

    def test_ordering_clauses_not_enforced_as_gates(self):
        # PRD-1: rho_NT < v_lo, p_min <= G_min, G_max <= p_max are NOT hard gates
        r = self.a5(rho_NT="0.7")                       # rho_NT > v_lo = 0.6
        self.assertEqual(r["a"] + r["c"], [])
        r = self.a5(p_min=320, G_min=300)               # p_min > G_min
        self.assertEqual(r["a"], [])
        r = self.a5(p_max=340, G_max=349)               # G_max > p_max
        self.assertEqual(r["a"], [])


class TestScreeningAids(unittest.TestCase):
    def test_tau_interval_by_hand(self):
        ti = A.tau_interval(TOY)
        # tau_lo = max(10/0.2, 1/0.1, 1/0.1, 10.5/0.4, 1/0.6) = max(50, 10, 10, 26.25, 1.667) = 50
        self.assertEqual(ti["tau_lo"], 50)
        self.assertEqual(ti["tau_hi"], F("149.75"))
        self.assertEqual(ti["integers"], list(range(50, 150)))
        self.assertTrue(ti["tau_F_inside"])

    def test_tau_interval_undefined_term(self):
        ti = A.tau_interval(make_reg(windows=A.WindowSet("0.6", "1.0", "1.1", "1.3", "1.5", "2.0")))
        self.assertTrue(ti["undefined"])

    def test_g_adm_by_hand(self):
        # m* = 10 : residues 10..49 inside minute 5 of [300, 349] -> 310..349
        self.assertEqual(A.eligible_paces(TOY), list(range(310, 350)))

    def test_screening_never_rejects(self):
        # a register violating RC-P2 still produces a report (screening is informational, §3.7)
        reg = make_reg(windows=A.WindowSet("0.6", "0.7", "1.1", "1.2", "1.5", "2.0"))
        sp = A.screen_pac(reg)
        self.assertFalse(sp["RC-P2"])


# ======================================================================================
class TestRecordedCandidatesRepresentationOnly(unittest.TestCase):
    """The recorded candidate lists: input representation and independent arithmetic ONLY.
    No gate is evaluated on a candidate register in this suite (that is the Step 4 dry run)."""
    S_MAIN = tuple(range(200, 451, 10))
    G_MAIN = tuple(range(310, 336))
    S_PILOT = (455, 465)
    G_PILOT = (340, 341)

    def test_list_properties(self):
        self.assertEqual((len(self.S_MAIN), len(self.G_MAIN)), (26, 26))
        self.assertEqual(len(self.S_MAIN), 2 * 13)
        self.assertTrue(A.is_arithmetic_progression(self.S_MAIN) and self.S_MAIN[1] - self.S_MAIN[0] == 10)
        self.assertTrue(A.is_arithmetic_progression(self.G_MAIN) and self.G_MAIN[1] - self.G_MAIN[0] == 1)
        self.assertTrue(set(self.S_PILOT).isdisjoint(self.S_MAIN))
        self.assertTrue(set(self.G_PILOT).isdisjoint(self.G_MAIN))
        self.assertTrue(A.is_arithmetic_progression(self.S_PILOT) and A.is_arithmetic_progression(self.G_PILOT))
        self.assertEqual(self.S_PILOT[1] - self.S_PILOT[0], 10)
        self.assertEqual(self.G_PILOT[1] - self.G_PILOT[0], 1)

    def test_counts_from_section_9_and_13_3(self):
        K, n_M, n_P, R = 4, 13, 13, 2
        self.assertEqual(K * 2 * n_M, 104)                       # N_tpl,MIL = 2K*n_M
        self.assertEqual(K * 4 * n_P, 208)                       # N_tpl,PAC = 4K*n_P
        self.assertEqual(8 * K * R * (n_M + 2 * n_P), 2496)      # §9.7
        self.assertEqual(4 * (K * 2) + 4 * (2 * K * 2), 96)      # pilot registry, L_pilot=2, one repetition, 4 conditions
        self.assertEqual((2 + 4) + (4 + 8), 18)                  # §13.3 minimum coverage

    def test_registers_can_be_represented_without_floats(self):
        main = make_reg(S_list=self.S_MAIN, G_list=self.G_MAIN)
        pilot = make_reg(S_list=self.S_PILOT, G_list=self.G_PILOT)
        self.assertEqual((len(main.S_list), len(main.G_list), len(pilot.S_list), len(pilot.G_list)), (26, 26, 2, 2))
        with self.assertRaises(TypeError):
            make_reg(S_list=self.S_MAIN, G_list=self.G_MAIN, D_dist=10.0)

    def test_pilot_class_cells_follow_the_rule(self):
        # K=4, L_pilot=2: one couple. §9.4 rows for b0,b1
        self.assertEqual("".join(A.class_of(0, i, 0, 4)[0] for i in range(4)), "MMCC")
        self.assertEqual("".join(A.class_of(1, i, 0, 4)[0] for i in range(4)), "CCMM")


# ======================================================================================
# Regression tests for the corrected defects D-1, F-1, F-2 and for the test gaps found by mutation testing.
# Fixtures are synthetic (not candidate registers). Expected values are derived by hand from the specification.
# ======================================================================================
class TestD1ExactDivision(unittest.TestCase):
    """D-1: a PacMember built with an integer D_dist must not make |x_k|/tau a floating-point division.
    Reproduction (before the fix): D_dist = 1, tau = 20, G = 330, valid target token 342 -> rho = 12/20 = 3/5,
    which belongs to the closed window [0.60, 0.90]; with an int D_dist Q3 reported 'outside'."""
    reg = make_reg(D_dist=1, tau_F=20, Tm_min=100)

    def tpl(self, D):
        return A.PacTemplate(0, 330, 0, 0, "MILD", A.PacMember(D, 330, 20, (331, 342, 329, 331, 329)),
                             A.PacMember(D, 330, 20, (331, 352, 329, 331, 329)))

    def test_member_normalises_D_dist_to_fraction(self):
        for D in (1, F(1), "1"):
            m = A.PacMember(D, 330, 20, (331,))
            self.assertIs(type(m.D_dist), F)
            self.assertEqual(m.D_dist, 1)
        self.assertEqual(A.PacMember("21/2", 3475, 35, (331,)).D_dist, F(21, 2))

    def test_float_D_dist_is_rejected(self):
        with self.assertRaises(TypeError):
            A.PacMember(1.0, 330, 20, (331,))

    def test_boundary_rho_three_fifths_passes_Q3_for_every_D_type(self):
        for D in (1, F(1), "1"):
            self.assertEqual(A.pac_element_gates(self.reg, self.tpl(D))["Q3"], [], D)

    def test_rho_helper_returns_an_exact_fraction(self):
        r = A.pac_target_rho_violating(self.tpl(1))      # violating target d = 22 -> 22/20
        self.assertIs(type(r), F)
        self.assertEqual(r, F(11, 10))

    def test_hazard_being_guarded(self):
        # documentation of the hazard (test-only float): 12/20 as a float is below the exact 3/5
        self.assertTrue(F(3, 5) > 12 / 20)

    def test_int_and_fraction_members_give_identical_gate_results_on_a_grid(self):
        # D in {1, 2, 5, 10}; every target offset d; T_time = D*G is an integer, so x_w = D*d_w exactly
        for D in (1, 2, 5, 10):
            for d in range(1, 41):
                v = (331, 330 + d, 329, 331, 329)
                w = (331, 330 + d + 12, 329, 331, 329)
                a = A.PacTemplate(0, 330, 0, 0, "MILD", A.PacMember(D, D * 330, 20, v), A.PacMember(D, D * 330, 20, w))
                b = A.PacTemplate(0, 330, 0, 0, "MILD", A.PacMember(F(D), D * 330, 20, v), A.PacMember(F(D), D * 330, 20, w))
                reg = make_reg(D_dist=D, tau_F=20, Tm_min=1, Tm_max=100000)
                self.assertEqual(A.pac_element_gates(reg, a), A.pac_element_gates(reg, b), (D, d))
                # independent expectation for Q3 on the valid side: rho = D*d/20 inside [3/5, 9/10]
                want = F(3, 5) <= F(D * d, 20) <= F(9, 10)
                self.assertEqual(not any(m.startswith("valid |x_k|") for m in A.pac_element_gates(reg, a)["Q3"]), want, (D, d))

    def test_non_integer_tau_in_a_member_is_a_clean_failure(self):
        t = self.tpl(1)
        bad = A.PacTemplate(0, 330, 0, 0, "MILD", replace(t.valid, tau_F=20.0), t.viol)
        res = A.pac_element_gates(self.reg, bad)
        self.assertTrue(all(res[g] for g in A.PAC_ELEMENT_GATES))


class TestA7Membership(unittest.TestCase):
    """F-1: A7 (§9.6) must reject missing, repeated and extra templates, including a class-balanced pair of
    extra bases (before the fix gate_A7 returned [] for that case)."""
    mil, pac = A.build_registry(TOY)                         # 2 bases, K = 4
    big = make_reg(S_list=(243, 257, 271, 285), G_list=(345, 346, 347, 348))

    def extra_mil(self, b, i=0):
        return A.build_mil_template(self.big, b, i, A.class_of(b, i, 0, 4))

    def test_complete_registry_passes(self):
        self.assertEqual(A.gate_A7(TOY, self.mil, self.pac), [])
        reg4 = self.big
        m4, p4 = A.build_registry(reg4)
        self.assertEqual(A.gate_A7(reg4, m4, p4), [])

    def test_missing_base_and_missing_slot(self):
        f = A.gate_A7(TOY, [t for t in self.mil if t.b != 1], self.pac)
        self.assertTrue(any("base 1 appears 0 times" in m for m in f))
        f = A.gate_A7(TOY, self.mil, self.pac[1:])
        self.assertTrue(any("PAC" in m and "appears 0 times" in m for m in f))

    def test_repeated_template(self):
        f = A.gate_A7(TOY, self.mil + [self.mil[0]], self.pac)
        self.assertTrue(any("appears 2 times" in m for m in f))

    def test_single_extra_base(self):
        f = A.gate_A7(TOY, self.mil + [self.extra_mil(2)], self.pac)
        self.assertTrue(any("unexpected template at base 2" in m for m in f))
        self.assertTrue(any("cluster 2 has 1 templates, expected 4" in m for m in f))

    def test_class_balanced_pair_of_extra_bases_is_rejected(self):
        e2, e3 = self.extra_mil(2), self.extra_mil(3)
        self.assertEqual({e2.cls, e3.cls}, {"MILD", "CLEAR"})        # the pair is balanced at slot (0,0)
        f = A.gate_A7(TOY, self.mil + [e2, e3], self.pac)
        self.assertTrue(f)
        self.assertFalse(any("slot (0,0)" in m for m in f))           # rejected on membership, not by accident
        self.assertTrue(any("unexpected template at base 2" in m for m in f))
        self.assertTrue(any("unexpected template at base 3" in m for m in f))

    def test_cluster_totals_are_checked_for_every_observed_cluster(self):
        f = A.gate_A7(TOY, self.mil + [self.extra_mil(2), self.extra_mil(3)], self.pac)
        self.assertTrue(any("cluster 2 has 1 templates, expected 4" in m for m in f))
        self.assertTrue(any("cluster 3 has 1 templates, expected 4" in m for m in f))
        self.assertTrue(any("cluster 2 has 0 MILD templates, expected 2" in m or "cluster 2 has 1 MILD templates, expected 2" in m for m in f))

    def test_complete_extra_clusters_are_still_extra(self):
        extras = [self.extra_mil(b, i) for b in (2, 3) for i in range(4)]
        f = A.gate_A7(TOY, self.mil + extras, self.pac)
        self.assertEqual(sum("unexpected template" in m for m in f), 8)

    def test_out_of_range_position_and_direction(self):
        moved = replace(self.pac[0], i=4)                              # position K does not exist
        f = A.gate_A7(TOY, self.mil, [moved] + self.pac[1:])
        self.assertTrue(any("unexpected template at base 0, position 4" in m for m in f))
        self.assertTrue(any("appears 0 times in slot (0,0)" in m for m in f))
        extra_dir = replace(self.pac[0], s=2)
        f = A.gate_A7(TOY, self.mil, self.pac + [extra_dir])
        self.assertTrue(any("direction 2" in m and "unexpected" in m for m in f))

    def test_valid_registry_result_through_the_full_path_is_unchanged(self):
        rep = A.run_numeric_gates(TOY)
        self.assertTrue(rep.outcomes["A7"].passed)
        self.assertTrue(rep.passed)


class TestInvalidClassLabels(unittest.TestCase):
    """F-2: an unknown class label must be a clean gate failure / SpecError, never a KeyError and never mapped
    to a valid class."""
    labels = ("mild", "Mild", "clear", "", None, 0, "CLEAR ", "SEVERE")
    mil_ok = A.build_mil_template(TOY, 0, 0, "MILD")
    pac_ok = A.build_pac_template(TOY, 0, 0, 0, "MILD")

    def test_class_label_error_helper(self):
        for ok in ("MILD", "CLEAR"):
            self.assertIsNone(A.class_label_error(ok))
        for bad in self.labels:
            self.assertIn("invalid class label", A.class_label_error(bad))

    def test_mil_element_gates_fail_every_gate(self):
        for lab in self.labels:
            res = A.mil_element_gates(TOY, replace(self.mil_ok, cls=lab))
            self.assertTrue(all(res[g] for g in A.MIL_ELEMENT_GATES), lab)
            self.assertTrue(all("invalid class label" in res[g][0] for g in A.MIL_ELEMENT_GATES), lab)

    def test_pac_element_gates_fail_every_gate(self):
        for lab in self.labels:
            res = A.pac_element_gates(TOY, replace(self.pac_ok, cls=lab))
            self.assertTrue(all(res[g] for g in A.PAC_ELEMENT_GATES), lab)
            self.assertTrue(all("invalid class label" in res[g][0] for g in A.PAC_ELEMENT_GATES), lab)

    def test_valid_labels_keep_their_behaviour(self):
        self.assertEqual({g: m for g, m in A.mil_element_gates(TOY, self.mil_ok).items() if m}, {})
        self.assertEqual({g: m for g, m in A.pac_element_gates(TOY, self.pac_ok).items() if m}, {})
        self.assertEqual(self.mil_ok.cls, "MILD")

    def test_builders_reject_unknown_labels_without_mapping(self):
        for lab in self.labels:
            with self.assertRaises(A.SpecError):
                A.build_mil_template(TOY, 0, 0, lab)
            with self.assertRaises(A.SpecError):
                A.build_pac_template(TOY, 0, 0, 0, lab)

    def test_registry_level_gates_do_not_raise(self):
        mil, pac = A.build_registry(TOY)
        bad_m = replace(mil[0], cls="mild")
        bad_p = replace(pac[0], cls="mild")
        self.assertTrue(any("invalid class label" in m for m in A.gate_M11(TOY, [bad_m] + mil[1:])))
        self.assertTrue(any("invalid class label" in m for m in A.gate_Q12([bad_p] + pac[1:])))
        self.assertTrue(any("invalid class label" in m for m in A.gate_A7(TOY, [bad_m] + mil[1:], pac)))
        self.assertTrue(A.gate_A6a(TOY, [bad_m] + mil[1:], pac))


class TestMutationGapTests(unittest.TestCase):
    """Behaviours that the mutation survivors showed to be untested."""

    # -- Q4 bound for the NON-target weeks of the violating member (the valid member must not mask it) ----------
    def test_Q4_violating_member_non_target_bound(self):
        reg = make_reg(D_dist=1, tau_F=20, Tm_min=100)
        valid = (331, 342, 329, 331, 329)                      # D = 1, G = 330: x_w = d_w

        def q4(week1_d):
            viol = (330 + week1_d, 352, 329, 331, 329)
            t = pac_tpl(1, 330, 20, 330, valid, viol)
            return A.pac_element_gates(reg, t)["Q4"]
        self.assertEqual(q4(19), [])                            # |x| = tau - 1 passes
        f = q4(20)                                              # |x| = tau fails, and only on the violating side
        self.assertTrue(f)
        self.assertTrue(all(m.startswith("violating: week 1") for m in f))

    # -- A6a margins on their own (slot balance), independent of the class-matrix comparison ----------------------
    def test_A6a_margins_fail_for_an_odd_number_of_bases(self):
        reg3 = make_reg(S_list=(243, 257, 271), G_list=(345, 346, 347))
        mil3, pac3 = A.build_registry(reg3)                    # classes follow the rule exactly (K = 4, L = 3)
        f = A.gate_A6a(reg3, mil3, pac3)
        self.assertFalse(any("rule gives" in m for m in f))
        # hand-derived: base 2 (couple 1) is MILD at positions {1,2} (s=0) and {2,3} (s=1); the couple (b0,b1)
        # contributes exactly one MILD per slot, so slots with base 2 MILD have 2 MILD bases instead of L//2 = 1
        want = {"MIL: slot (1,0) has 2 MILD bases, expected 1", "MIL: slot (2,0) has 2 MILD bases, expected 1",
                "PAC: slot (1,0) has 2 MILD bases, expected 1", "PAC: slot (2,0) has 2 MILD bases, expected 1",
                "PAC: slot (2,1) has 2 MILD bases, expected 1", "PAC: slot (3,1) has 2 MILD bases, expected 1"}
        self.assertEqual(set(f), want)

    # -- the sign of r in m_q for a non-integer D_dist ----------------------------------------------------------
    def test_mq_uses_plus_sigma_r(self):
        # D = 21/2, G = 331: D*G = 3475.5 -> T = 3476, r = T - D*G = +1/2 ; tau = 35, rho_NT = 0.4 -> m_NT = 1
        reg = make_reg(D_dist="21/2", tau_F=35, G_list=(331, 333), Tm_min=100)
        self.assertEqual(A.m_NT_value(reg), 1)
        slow = A.build_pac_template(reg, 0, 0, 0, "MILD")      # sigma = +1
        self.assertEqual(slow.valid.T_time, 3476)
        # m_V = nearest((26.25 + 0.5)/10.5 = 2.548) = 3 (the flipped sign would give 2); m_MILD = nearest(4.048) = 4
        self.assertEqual(slow.valid.tokens, (332, 334, 330, 332, 330))
        self.assertEqual(slow.viol.tokens, (332, 335, 330, 332, 330))
        self.assertEqual(abs(A.pace_quantities(slow.valid, 331)["x"][1]), F(31))            # D*3 - r
        self.assertEqual(A.pac_element_gates(reg, slow)["Q3"], [])
        fast = A.build_pac_template(reg, 0, 0, 1, "MILD")      # sigma = -1
        # m_V = nearest((26.25 - 0.5)/10.5 = 2.452) = 2 (the flipped sign would give 3 and rho = 0.914 > 0.9)
        self.assertEqual(fast.valid.tokens, (332, 329, 330, 332, 330))
        self.assertEqual(abs(A.pace_quantities(fast.valid, 331)["x"][1]), F(43, 2))         # D*2 + r
        self.assertEqual(A.pac_element_gates(reg, fast)["Q3"], [])

    def test_mq_sign_for_the_violating_member(self):
        # same D = 21/2, G = 331 (r = +1/2) but tau = 33: m_NT = floor((13.2 - 0.5)/10.5) = 1 ; CLEAR window mid 1.75
        # sigma = +1: m_CLEAR = nearest((57.75 + 0.5)/10.5 = 5.548) = 6  (the flipped sign gives 5.452 -> 5)
        # sigma = -1: m_CLEAR = nearest((57.75 - 0.5)/10.5 = 5.452) = 5  (the flipped sign gives 6)
        reg = make_reg(D_dist="21/2", tau_F=33, G_list=(331, 333), Tm_min=100)
        self.assertEqual(A.m_NT_value(reg), 1)
        slow = A.build_pac_template(reg, 0, 0, 0, "CLEAR")
        self.assertEqual(slow.valid.tokens, (332, 333, 330, 332, 330))      # m_V = nearest(2.405) = 2
        self.assertEqual(slow.viol.tokens, (332, 337, 330, 332, 330))
        self.assertEqual(abs(A.pace_quantities(slow.viol, 331)["x"][1]), F(125, 2))   # 10.5*6 - 0.5 = 62.5
        fast = A.build_pac_template(reg, 0, 0, 1, "CLEAR")
        self.assertEqual(fast.viol.tokens, (332, 326, 330, 332, 330))
        self.assertEqual(abs(A.pace_quantities(fast.viol, 331)["x"][1]), F(53))       # 10.5*5 + 0.5 = 53

    # -- half-even and the other ties of the reading helper ----------------------------------------------------
    def test_round_mode_ties(self):
        table = {  # y: (floor, ceil, nearest, half_up, half_down, half_even), derived by hand
            F(5, 2): (2, 3, 2, 3, 2, 2), F(7, 2): (3, 4, 3, 4, 3, 4), F(1, 2): (0, 1, 0, 1, 0, 0),
            F(3, 2): (1, 2, 1, 2, 1, 2), F(-1, 2): (-1, 0, -1, 0, -1, 0), F(-3, 2): (-2, -1, -2, -1, -2, -2),
        }
        modes = ("floor", "ceil", "nearest", "half_up", "half_down", "half_even")
        for y, want in table.items():
            self.assertEqual(tuple(A._round_mode(m, y) for m in modes), want, y)

    def test_half_even_changes_a_pac_reading_verdict(self):
        # D = 1/2, T = 100, token 301: F = 150.5, F - T = 50.5 = tau + 1/2 with tau = 50
        v = A.pac_reading_verdicts(A.PacMember(F(1, 2), 100, 50, (301,)))
        self.assertEqual(v[("unrounded", "inclusive")], "violated")
        self.assertEqual(v[("half_up", "inclusive")], "violated")       # 151 - 100 = 51
        self.assertEqual(v[("half_even", "inclusive")], "satisfied")    # 150 is even -> 50
        self.assertEqual(v[("half_down", "inclusive")], "satisfied")
        self.assertEqual(v[("half_even", "exclusive")], "violated")     # |50| >= 50

    # -- A4 aggregates Q6 --------------------------------------------------------------------------------------
    def test_A4_fails_when_only_Q6_fails(self):
        # D = 1/2, tau = 10, rho_NT = 0.4: m_NT = floor((4 - 0.5)/0.5) = 7 ; targets m = 15, 24, 35 (rho 0.75, 1.2, 1.75)
        # G = 330 -> T = 165, r = 0 ; non-target token step d = +-1 gives |x| = 0.5 < 1 -> Q6 fails; Q2-Q5 hold
        reg = make_reg(D_dist="1/2", tau_F=10, G_list=(330, 331), p_min=60, p_max=599, G_min=60, G_max=599,
                       Tm_min=1, Tm_max=35999)
        rep = A.run_numeric_gates(reg)
        for g in ("Q2", "Q3", "Q4", "Q5"):
            self.assertTrue(rep.outcomes[g].evaluated and rep.outcomes[g].passed, g)
        self.assertTrue(rep.outcomes["Q6"].evaluated and not rep.outcomes["Q6"].passed)
        self.assertTrue(any("|x_" in m for m in rep.outcomes["Q6"].failures))
        a4 = rep.outcomes["A4"]
        self.assertTrue(a4.evaluated and not a4.passed)
        self.assertTrue(a4.failures and all(("|x_" in m or "d_" in m) for m in a4.failures))


if __name__ == "__main__":
    unittest.main()
