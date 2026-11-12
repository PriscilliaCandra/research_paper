"""Step 4 numeric-stage dry run (specification v2.7, section 21.1 step 4) -- runner.

Runs Implementation A (impl_a.py) on the register values recorded in
research_design/STEP3_OWNER_DECISIONS_RECORD_DRAFT.md: the MAIN register and the PILOT register
(pilot lists, L_pilot = 2, pilot extras = 0). It runs numeric-stage gates, screening aids and the
reading-invariance check ONLY. No prompt text, no rendering-stage gate, no pilot, no model or API
call, no experiment. No value is changed in response to a gate result (reject-not-repair, 3.6).

The runner reads the specification only to verify its hash. It writes nothing; the output is
printed to stdout (the caller redirects it).

Usage (from the repository root):
    python -B research_design/implementation_a/run_step4.py
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import impl_a as A  # noqa: E402

EXPECTED_SHA256 = {
    "spec": "d5fcffadd1045fc0c86a7c84eb8d51592d807576b6a51b712cef025164082ca8",
    "impl_a.py": "daa2cd49b86ed1a546e95478bc3f40c1fc131ea52d4622b981e4e3b117f018ee",   # corrected: D-1, F-1, F-2 (2026-10-12)
    "test_impl_a.py": "92d36252f54c9e216d09754a172aa112e44b92df5497384c9c50277731daa723",
}
PATHS = {
    "spec": os.path.join(HERE, "..", "SCENARIO_CONSTRUCTION_SPECIFICATION_v2_7_DRAFT.md"),
    "impl_a.py": os.path.join(HERE, "impl_a.py"),
    "test_impl_a.py": os.path.join(HERE, "test_impl_a.py"),
}

# ---------------------------------------------------------------------------------------------
# Register values and their sources: research_design/STEP3_OWNER_DECISIONS_RECORD_DRAFT.md
# (section numbers refer to that record). Status of every value there: NOT FROZEN, requires Step 4.
# ---------------------------------------------------------------------------------------------
WINDOWS = A.WindowSet("0.60", "0.90", "1.10", "1.30", "1.50", "2.00")      # record s4: shared windows
SIZING = A.Sizing(z="1.96", hw_star="0.20", C_max=2496, R_min=2, R_cap=2,  # record s1
                  n_boot=10000, L_small=10, level="0.95")
COMMON = dict(P=10, N=5, tau_F=50, rho_NT="0.40", windows=WINDOWS, D_dist=10,   # record s1, s4
              X_min=100, X_max=999, p_min=270, p_max=420, G_min=300, G_max=349,  # record s4
              Tm_min=2700, Tm_max=4200, sizing=SIZING)
S_MAIN = tuple(200 + 10 * j for j in range(26))     # record s4: S_0 = 200, step 10, L_M = 26
G_MAIN = tuple(310 + j for j in range(26))          # record s4: G_0 = 310, step 1, L_P = 26 (310..335)
S_PILOT = (455, 465)                                # record s3: step 10
G_PILOT = (340, 341)                                # record s3: step 1
L_PILOT, PILOT_EXTRAS = 2, 0                        # record s3

SOURCES = [
    ("P", 10, "record s4"), ("N", 5, "record s1 (K = 4)"), ("tau_F", 50, "record s4"),
    ("rho_NT", "0.40", "record s4"), ("D_dist", 10, "record s4"),
    ("windows (shared)", "W_V=[0.60,0.90] W_MILD=[1.10,1.30] W_CLEAR=[1.50,2.00]", "record s4 (OD-5 not final)"),
    ("S_list main", "200..450 step 10, L_M=26", "record s4"), ("G_list main", "310..335 step 1, L_P=26", "record s4"),
    ("S_pilot", "(455, 465) step 10", "record s3"), ("G_pilot", "(340, 341) step 1", "record s3"),
    ("L_pilot / pilot extras", "2 / 0", "record s3"),
    ("X_min, X_max", "100, 999", "record s4"), ("G_min, G_max", "300, 349", "record s4 (G 300-349)"),
    ("p_min, p_max", "270, 420", "record s4"), ("Tm_min, Tm_max", "2700, 4200", "record s4"),
    ("z, hw*, level", "1.96, 0.20, 0.95", "record s1"), ("C_max", 2496, "record s1"),
    ("R_min, R_cap", "2, 2", "record s1"), ("n_boot, L_small", "10000, 10", "record s1"),
]


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def status(o):
    if not o.evaluated:
        return "NOT EVALUATED"
    return "PASS" if o.passed else "FAIL"


def show_report(label, reg):
    print(f"\n{'=' * 100}\nREGISTER: {label}")
    print(f"  S_list = {reg.S_list[0]}..{reg.S_list[-1]} (len {len(reg.S_list)}); "
          f"G_list = {reg.G_list[0]}..{reg.G_list[-1]} (len {len(reg.G_list)}); K = {reg.K}")
    rep = A.run_numeric_gates(reg)
    print(f"  built = {rep.built}; templates: MIL {rep.n_mil}, PAC {rep.n_pac}")
    print(f"\n  {'gate':5} {'status':14} detail")
    for g in A.NUMERIC_GATES:
        o = rep.outcomes[g]
        detail = "" if o.passed else f"{len(o.failures)} message(s)"
        print(f"  {g:5} {status(o):14} {detail}".rstrip())
        if not o.passed:
            for m in o.failures[:6]:
                print(f"        - {m}")
            if len(o.failures) > 6:
                print(f"        ... {len(o.failures) - 6} more")
    n_pass = sum(1 for o in rep.outcomes.values() if o.evaluated and o.passed)
    n_fail = sum(1 for o in rep.outcomes.values() if o.evaluated and not o.passed)
    n_ne = sum(1 for o in rep.outcomes.values() if not o.evaluated)
    print(f"\n  SUMMARY {label}: PASS {n_pass}, FAIL {n_fail}, NOT EVALUATED {n_ne} of {len(A.NUMERIC_GATES)}")
    print(f"  numerically admissible (all 30 numeric-stage gates PASS): {rep.passed}")

    # Interpretation I-2: A3 on the registry templates (gate) versus the stricter all-classes reading
    print("\n  A3 reading used by the gate (templates of the registry, one class per slot): "
          f"{status(rep.outcomes['A3'])}")
    if rep.built:
        extra = A.a3_all_classes(reg)
        print(f"  A3 all-classes reading (informative, NOT the gate): {'PASS' if not extra else 'FAIL'} "
              f"({len(extra)} failure message(s))")
        for m in extra[:6]:
            print(f"        - {m}")
        print("  readings differ: " + str((rep.outcomes['A3'].passed) != (not extra)))
    else:
        print("  A3 all-classes reading: NOT EVALUATED (registry not built)")

    # Screening aids (never reject, 3.7)
    if rep.built:
        sm, sp = A.screen_mil(reg), A.screen_pac(reg)
        print("\n  Screening aids (informational; they never reject):")
        print("   MIL:", {k: (str(v) if not isinstance(v, bool) else v) for k, v in sm.items()})
        ti = sp["tau_interval"]
        print("   PAC:", {k: v for k, v in sp.items() if k not in ("tau_interval", "G_adm")})
        if ti.get("undefined"):
            print("   tau interval: undefined (screening warning only)")
        else:
            ints = ti["integers"]
            print(f"   tau interval: tau_lo = {ti['tau_lo']}, tau_hi = {ti['tau_hi']} ({float(ti['tau_hi']):.2f}), "
                  f"integers {ints[0]}..{ints[-1]}, tau_F inside: {ti['tau_F_inside']}")
        gadm = set(sp["G_adm"])
        print(f"   G_adm size {len(gadm)}; every G_list element screened-eligible: "
              f"{all(g in gadm for g in reg.G_list)}")

        # Reading-invariance (guard) check, informational (interpretation I-6)
        mil, pac = A.build_registry(reg)
        bad = [m for t in mil for m in A.reading_invariance_mil(reg, t)]
        bad += [m for t in pac for m in A.reading_invariance_pac(t)]
        print(f"\n  Reading-invariance check over {len(mil)} MIL and {len(pac)} PAC templates "
              f"(informational, not a gate): {'all readings agree' if not bad else str(len(bad)) + ' disagreement(s)'}")
        for m in bad[:5]:
            print(f"        - {m}")
    return rep


def main():
    print("STEP 4 NUMERIC-STAGE DRY RUN -- specification v2.7 (frozen), section 21.1 step 4")
    print("Implementation A: corrected version (fixes D-1, F-1, F-2); this is the corrected re-run, not the original run.")
    print("Scope: numeric-stage gates, screening aids, reading-invariance check. No prompt, no rendering gate,")
    print("no pilot, no model/API call, no experiment, no parameter change.\n")
    print("PREREQUISITE HASHES")
    ok = True
    for k, p in PATHS.items():
        h = sha(p)
        same = h == EXPECTED_SHA256[k]
        ok &= same
        print(f"  {k:15} {h}  {'MATCH' if same else 'MISMATCH'}")
    if not ok:
        print("STOP: a hash does not match the audited value.")
        return 2
    print("\nPARAMETERS AND SOURCES (record = research_design/STEP3_OWNER_DECISIONS_RECORD_DRAFT.md)")
    for name, val, src in SOURCES:
        print(f"  {name:24} {str(val):60} {src}")
    print(f"\n  inputs consistency: len(S_MAIN)=2*13 -> {len(S_MAIN) == 26}; len(G_MAIN)=2*13 -> {len(G_MAIN) == 26};"
          f" planned slots 8*K*R*(n_M+2n_P) = {8 * 4 * 2 * (13 + 26)} (C_max {SIZING.C_max});"
          f" pilot minimum 18 calls/round (not executed)")
    main_reg = A.Register(S_list=S_MAIN, G_list=G_MAIN, **COMMON)
    pilot_reg = A.Register(S_list=S_PILOT, G_list=G_PILOT, **COMMON)
    rm = show_report("MAIN", main_reg)
    rp = show_report("PILOT (L_pilot = 2, extras = 0; pilot-registry stage per PC-2 is ambiguous)", pilot_reg)
    print("\nVERDICT INPUTS")
    print(f"  MAIN  numerically admissible: {rm.passed}")
    print(f"  PILOT numerically admissible: {rp.passed}")
    print("  Numeric admissibility only: rendering-stage gates (PG*, A1b, A6b) were not run (3.7).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
