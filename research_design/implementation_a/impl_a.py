"""Implementation A: numeric-stage gates of the frozen scenario-construction specification v2.7.

Source of authority: research_design/SCENARIO_CONSTRUCTION_SPECIFICATION_v2_7_DRAFT.md
(sha256 d5fcffadd1045fc0c86a7c84eb8d51592d807576b6a51b712cef025164082ca8). Section numbers
below refer to that document.

SCOPE (§11 layer 1, §21.1 step 4, "numeric stage" of §3.7):
  * the 30 numeric-stage hard gates  M1-M11, Q1-Q12, A1a, A2, A3, A4, A5, A6a, A7;
  * the canonical numeral renderers of §3.1 / §8.3 that the numeric stage needs (Q8);
  * the deterministic construction of §4, §5.2 and §9.2 that produces the templates the
    gates are evaluated on (a gate never reads construction intermediates, it recomputes
    from the member data);
  * the screening aids of §4 and §5.5-5.6 (RC-M*, RC-P*, tau interval, G_adm): they are
    reported and NEVER reject (§3.7);
  * the reading-invariance (guard) checks of §6.1.

NOT IN SCOPE: rendering-stage gates (PG*, A1b, A6b), Implementation B, reference
worksheets (§11.2), the decision procedure DP1-DP9, model/API calls, the pilot, the
generator of step 8, any experiment logic.

ARITHMETIC: every ground-truth or gate computation uses fractions.Fraction and integers.
Floats are rejected on input (TypeError); no float is ever used to decide a gate (§3.1).

This module does not decide any parameter and does not claim that any parameter set passes.
Interpretations that the specification does not settle are listed in INTERPRETATIONS.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SPEC_SHA256 = "d5fcffadd1045fc0c86a7c84eb8d51592d807576b6a51b712cef025164082ca8"

NUMERIC_GATES: Tuple[str, ...] = (
    "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10", "M11",
    "Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "Q10", "Q11", "Q12",
    "A1a", "A2", "A3", "A4", "A5", "A6a", "A7",
)
MIL_ELEMENT_GATES = tuple(f"M{n}" for n in range(1, 11))
PAC_ELEMENT_GATES = tuple(f"Q{n}" for n in range(1, 12))

# Points where the specification text does not fix the implementation (reported, not resolved).
INTERPRETATIONS: Dict[str, str] = {
    "I-1 PAC non-target index": (
        "§5.2 step 3 indexes the N-1 non-target weeks 'j = 1 ... N-1 in ascending week order'. "
        "Implemented as the ordinal j among the non-target weeks (the week numbers skip the "
        "target week); the count N-1 matches the ordinal reading."
    ),
    "I-2 A3 scope": (
        "A3 says 'in every template that uses it'; §5.6 adds '(all positions, both directions, "
        "both classes)'. The gate A3 is evaluated on the templates of the registry (one class "
        "per slot). The stricter all-classes reading is exposed separately as "
        "a3_all_classes() and is not part of the gate result."
    ),
    "I-3 A1a plan identity": (
        "'Numeral sequences': MIL = the N weekly totals; PAC = (T_time, the N weekly tokens). "
        "D_dist is constant within a register. Whether T_time belongs to the PAC plan identity "
        "is not stated."
    ),
    "I-4 empty class": (
        "M11/Q12 compare max over MILD with min over CLEAR. If a class has no template the "
        "comparison is undefined; implemented as a failure (§6.2: 'No class is empty for any "
        "admissible parameter set')."
    ),
    "I-5 window bounds": (
        "Windows are closed intervals [lo, hi] (notation of §6.2); membership is lo <= rho <= hi."
    ),
    "I-6 six readings": (
        "'Six-reading invariance check' (§11 layer 1) is not defined in the text. MIL: 3 threshold "
        "roundings (floor, ceiling, nearest with ties toward the lower integer) x 2 comparisons "
        "(violation iff increase > cap, iff increase >= cap) = 6. PAC: finish time unrounded or "
        "rounded by floor, ceiling, half-up, half-down, half-even, each with inclusive and "
        "exclusive comparison = 12 (reading set of §6.1). Tie handling of 'nearest' for the MIL "
        "threshold is not specified."
    ),
    "I-7 pilot registry lists": (
        "A5(a) names lists of lengths 2*n_M and 2*n_P; whether it applies literally to the pilot "
        "lists is not stated (§13.3 says 'the same gates'). Implemented identically for any "
        "register with even-length lists >= 2."
    ),
    "I-8 not evaluated gates": (
        "If A5 or A2 fails the build is not constructed (A5 is 'input checks only, no "
        "construction'; the class rule needs an even K). The remaining gates are reported as "
        "not evaluated and count as not passed."
    ),
    "I-10 A7 membership": (
        "A7 is evaluated over the expected (base, position, direction) set AND over every observed "
        "template. A template outside the expected set (an extra base, position or direction) is "
        "reported as a failure: an extra base is an extra cluster whose template counts differ from "
        "the other clusters (§9.6 A7, §9.5). The specification does not spell out this case."
    ),
    "I-9 PAC members": (
        "Q9 compares D_dist, T_time and tau_F between the members, so each PAC member carries "
        "its own copy of these three values."
    ),
}


VALID_CLASSES: Tuple[str, ...] = ("MILD", "CLEAR")


def class_label_error(cls: Any) -> Optional[str]:
    """None if `cls` is one of the two class labels of §9.2, else a message. An unknown label is never
    mapped to a valid class (F-2)."""
    if isinstance(cls, str) and cls in VALID_CLASSES:
        return None
    return f"invalid class label {cls!r} (expected 'MILD' or 'CLEAR')"


class ConstructionError(Exception):
    """A template cannot be constructed from the register values (e.g. m_NT < 1)."""


class SpecError(ValueError):
    """An input outside the domain of a helper (not a gate result)."""


# --------------------------------------------------------------------------------------
# Exact helpers (§3.1, §4 helpers, §3.3)
# --------------------------------------------------------------------------------------
def is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def exact(x: Any, name: str = "value") -> Fraction:
    """Convert to Fraction. Accepts int, Fraction or str ('0.4', '3/2'). Rejects float and bool."""
    if isinstance(x, bool) or isinstance(x, float):
        raise TypeError(f"{name}: floating-point/bool input is not allowed (§3.1)")
    if isinstance(x, Fraction):
        return x
    if isinstance(x, int):
        return Fraction(x)
    if isinstance(x, str):
        return Fraction(x)
    raise TypeError(f"{name}: unsupported type {type(x).__name__}")


def nearest(y: Any) -> int:
    """§4 helper: nearest integer, ties toward the LOWER integer."""
    return math.ceil(exact(y, "nearest") - Fraction(1, 2))


def rnd_star(y: Any) -> int:
    """§3.3 frozen reference rounding rnd*(y) = floor(y + 1/2) (ties up)."""
    return math.floor(exact(y, "rnd*") + Fraction(1, 2))


def mid(window: Tuple[Fraction, Fraction]) -> Fraction:
    return (window[0] + window[1]) / 2


def in_window(rho: Fraction, window: Tuple[Fraction, Fraction]) -> bool:
    return window[0] <= rho <= window[1]


def has_finite_decimal(q: Fraction) -> bool:
    d = q.denominator
    for p in (2, 5):
        while d % p == 0:
            d //= p
    return d == 1


def n_dec(q: Fraction) -> int:
    """§8.3 item 4: smallest n >= 0 with q*10^n an integer."""
    if not has_finite_decimal(q):
        raise SpecError("no finite decimal expansion")
    n = 0
    while (q * 10 ** n).denominator != 1:
        n += 1
    return n


# --- canonical numeral renderers (§3.1, §8.3 items 3-4) ---------------------------------
def render_mileage(X: int) -> str:
    if not is_int(X) or not 0 <= X <= 9999:
        raise SpecError("mileage must be an integer 0 <= X <= 9999 tenths")
    return f"{X // 10}.{X % 10}"


def render_pace(p: int) -> str:
    if not is_int(p) or not 60 <= p <= 599:
        raise SpecError("pace must be an integer 60 <= p <= 599 s/km")
    return f"{p // 60}:{p % 60:02d}"


def render_time(T: int) -> str:
    if not is_int(T) or not 0 <= T < 36000:
        raise SpecError("T_time must be an integer 0 <= T < 36000 s")
    return f"{T // 3600}:{(T % 3600) // 60:02d}:{T % 60:02d}"


def render_distance(D: Fraction) -> str:
    D = exact(D, "D_dist")
    if D <= 0:
        raise SpecError("D_dist must be positive")
    n = n_dec(D)
    if n == 0:
        return str(int(D))
    whole = math.floor(D)
    frac = D - whole
    digits = str(int(frac * 10 ** n)).zfill(n)
    return f"{whole}.{digits}"


def digits(n: int) -> int:
    """Number of decimal digits of a non-negative integer (0 has one digit)."""
    if not is_int(n) or n < 0:
        raise SpecError("digits() needs a non-negative integer")
    return len(str(n))


# --------------------------------------------------------------------------------------
# Register (§17) and templates
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class WindowSet:
    """Margin windows of §6.2 as closed intervals; any bound may be None (missing)."""
    v_lo: Any = None
    v_hi: Any = None
    mi_lo: Any = None
    mi_hi: Any = None
    c_lo: Any = None
    c_hi: Any = None

    def __post_init__(self) -> None:
        for name in ("v_lo", "v_hi", "mi_lo", "mi_hi", "c_lo", "c_hi"):
            v = getattr(self, name)
            if v is not None:
                object.__setattr__(self, name, exact(v, name))

    def as_dict(self) -> Dict[str, Tuple[Fraction, Fraction]]:
        return {"V": (self.v_lo, self.v_hi), "MILD": (self.mi_lo, self.mi_hi),
                "CLEAR": (self.c_lo, self.c_hi)}

    def bounds(self) -> List[Any]:
        return [self.v_lo, self.v_hi, self.mi_lo, self.mi_hi, self.c_lo, self.c_hi]


@dataclass(frozen=True)
class Sizing:
    """Sizing inputs checked by A5(b) (§12.2, §9.6). None = missing."""
    z: Any = None
    hw_star: Any = None
    C_max: Any = None
    R_min: Any = None
    R_cap: Any = None
    n_boot: Any = None
    L_small: Any = None
    level: Any = None

    def __post_init__(self) -> None:
        for name in ("z", "hw_star", "level"):
            v = getattr(self, name)
            if v is not None:
                object.__setattr__(self, name, exact(v, name))


@dataclass(frozen=True)
class Register:
    P: Any
    N: Any
    tau_F: Any
    rho_NT: Any
    windows: Optional[WindowSet]
    D_dist: Any
    S_list: Tuple[Any, ...]
    G_list: Tuple[Any, ...]
    X_min: Any
    X_max: Any
    p_min: Any
    p_max: Any
    G_min: Any
    G_max: Any
    Tm_min: Any
    Tm_max: Any
    windows_mil: Optional[WindowSet] = None   # per-family sets (OD-5 undecided); None = use `windows`
    windows_pac: Optional[WindowSet] = None
    sizing: Optional[Sizing] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rho_NT", exact(self.rho_NT, "rho_NT"))
        object.__setattr__(self, "D_dist", exact(self.D_dist, "D_dist"))
        object.__setattr__(self, "S_list", tuple(self.S_list))
        object.__setattr__(self, "G_list", tuple(self.G_list))

    # derived (valid only after A5)
    @property
    def K(self) -> int:
        return self.N - 1

    @property
    def gamma(self) -> Fraction:
        return self.rho_NT * self.P / 100

    def windows_for(self, family: str) -> WindowSet:
        ws = {"MIL": self.windows_mil, "PAC": self.windows_pac}[family]
        return ws if ws is not None else self.windows

    def declared_window_sets(self) -> List[Tuple[str, Optional[WindowSet]]]:
        if self.windows_mil is None and self.windows_pac is None:
            return [("shared", self.windows)]
        return [("MIL", self.windows_for("MIL")), ("PAC", self.windows_for("PAC"))]


@dataclass(frozen=True)
class MilTemplate:
    b: int
    S_b: int
    i: int          # 0-based position index; target week k = i + 2
    cls: str        # "MILD" | "CLEAR"
    valid: Tuple[int, ...]   # X_1 ... X_N (tenths of km)
    viol: Tuple[int, ...]

    @property
    def k(self) -> int:
        return self.i + 2

    @property
    def tid(self) -> str:
        return f"MIL(b={self.b},i={self.i})"


@dataclass(frozen=True)
class PacMember:
    D_dist: Fraction
    T_time: int
    tau_F: int
    tokens: Tuple[int, ...]   # p_1 ... p_N (s/km)

    def __post_init__(self) -> None:
        # D-1: exact arithmetic at the data boundary. An int or str D_dist becomes a Fraction so that no
        # division in the gates can be a floating-point division; a float is rejected (TypeError, §3.1).
        object.__setattr__(self, "D_dist", exact(self.D_dist, "D_dist"))


@dataclass(frozen=True)
class PacTemplate:
    b: int
    G_b: int
    i: int
    s: int          # 0 slower (sigma +1), 1 faster (sigma -1)
    cls: str
    valid: PacMember
    viol: PacMember

    @property
    def k(self) -> int:
        return self.i + 2

    @property
    def sigma(self) -> int:
        return 1 if self.s == 0 else -1

    @property
    def tid(self) -> str:
        return f"PAC(b={self.b},i={self.i},s={self.s})"


# --------------------------------------------------------------------------------------
# Class assignment (§9.2)
# --------------------------------------------------------------------------------------
def window_start(b: int, s: int, K: int) -> int:
    if K % 2 != 0:
        raise SpecError("K must be even for the class rule (A2)")
    return (b // 2 + s * (K // 4) + (b % 2) * (K // 2)) % K


def class_of(b: int, i: int, s: int, K: int) -> str:
    return "MILD" if ((i - window_start(b, s, K)) % K) < K // 2 else "CLEAR"


# --------------------------------------------------------------------------------------
# Construction (§4 MIL, §5.2 PAC): produce the data on which the gates are evaluated
# --------------------------------------------------------------------------------------
def _dnt(X: int, gamma: Fraction) -> int:
    return max(1, math.floor(gamma * X))


def ramp(X: int, gamma: Fraction) -> int:
    return X + _dnt(X, gamma)


def build_mil_template(reg: Register, b: int, i: int, cls: str) -> MilTemplate:
    err = class_label_error(cls)
    if err:
        raise SpecError(err)
    N, P, gamma = reg.N, reg.P, reg.gamma
    k = i + 2
    ws = reg.windows_for("MIL").as_dict()
    S = reg.S_list[b]
    X = [S]                                   # step 1: X_1 = S_b
    for _ in range(1, k - 1):                 # step 2: j = 1 .. k-2
        X.append(ramp(X[-1], gamma))
    X_prev = X[k - 2]                         # step 3: X_{k-1}
    u = Fraction(P * X_prev, 100)
    dV = nearest(mid(ws["V"]) * u)            # step 4
    dq = nearest(mid(ws[cls]) * u)
    valid = list(X) + [X_prev + dV]           # step 5 (weeks 1..k)
    viol = list(X) + [X_prev + dq]
    for j in range(k, N):                     # step 6: j = k .. N-1, computed in the valid member
        nxt = valid[j - 1] - _dnt(valid[j - 1], gamma)
        valid.append(nxt)
        viol.append(nxt)                      # copied verbatim
    return MilTemplate(b, S, i, cls, tuple(valid), tuple(viol))


def m_NT_value(reg: Register) -> int:
    """§5.2 step 2: floor((rho_NT*tau_F - 1/2) / D_dist)."""
    return math.floor((reg.rho_NT * reg.tau_F - Fraction(1, 2)) / reg.D_dist)


def build_pac_template(reg: Register, b: int, i: int, s: int, cls: str) -> PacTemplate:
    err = class_label_error(cls)
    if err:
        raise SpecError(err)
    N, tau, D = reg.N, reg.tau_F, reg.D_dist
    k = i + 2
    sigma = 1 if s == 0 else -1
    ws = reg.windows_for("PAC").as_dict()
    G = reg.G_list[b]
    T = rnd_star(D * G)                       # step 1
    r = T - D * G
    mNT = m_NT_value(reg)                     # step 2
    if mNT < 1:
        raise ConstructionError("m_NT < 1 (gate Q2)")
    d: Dict[int, int] = {}
    nontarget = [w for w in range(1, N + 1) if w != k]
    for j, w in enumerate(nontarget, start=1):  # step 3 (interpretation I-1)
        d[w] = (-1) ** (j + 1) * (1 + ((j - 1) % mNT))
    mV = nearest((mid(ws["V"]) * tau + sigma * r) / D)      # step 4
    mq = nearest((mid(ws[cls]) * tau + sigma * r) / D)
    dv, dm = dict(d), dict(d)
    dv[k] = sigma * mV
    dm[k] = sigma * mq
    tv = tuple(G + dv[w] for w in range(1, N + 1))          # step 5
    tm = tuple(G + dm[w] for w in range(1, N + 1))
    return PacTemplate(b, G, i, s, cls,
                       PacMember(D, T, tau, tv), PacMember(D, T, tau, tm))


def build_registry(reg: Register) -> Tuple[List[MilTemplate], List[PacTemplate]]:
    """All templates of §9.2: MIL (b, i); PAC (b, i, s). Requires a register that passed A5 and A2."""
    K = reg.K
    mil: List[MilTemplate] = []
    pac: List[PacTemplate] = []
    for b in range(len(reg.S_list)):
        for i in range(K):
            mil.append(build_mil_template(reg, b, i, class_of(b, i, 0, K)))
    for b in range(len(reg.G_list)):
        for i in range(K):
            for s in (0, 1):
                pac.append(build_pac_template(reg, b, i, s, class_of(b, i, s, K)))
    return mil, pac


# --------------------------------------------------------------------------------------
# Element-level gates M1-M10 (§4)
# --------------------------------------------------------------------------------------
def _shape_failure(seq: Sequence[Any], N: int) -> Optional[str]:
    if len(seq) != N:
        return f"expected {N} weekly values, found {len(seq)}"
    return None


def mil_element_gates(reg: Register, t: MilTemplate) -> Dict[str, List[str]]:
    """Evaluate M1-M10 for one MIL template from the member data. Empty list = gate passed."""
    N, P = reg.N, reg.P
    ws = reg.windows_for("MIL").as_dict()
    out: Dict[str, List[str]] = {g: [] for g in MIL_ELEMENT_GATES}
    bad = _shape_failure(t.valid, N) or _shape_failure(t.viol, N)
    if bad:
        for g in out:
            out[g].append(f"malformed template: {bad}")
        return out
    k = t.k
    if not 2 <= k <= N:
        for g in out:
            out[g].append(f"target week k={k} outside 2..{N}")
        return out
    err = class_label_error(t.cls)
    if err:
        for g in out:
            out[g].append(err)
        return out
    V, W = list(t.valid), list(t.viol)

    # M1: integrality, Delta_V >= 1 and Delta_q >= 1
    if not all(is_int(x) for x in V + W):
        out["M1"].append("non-integer week value")
    int_ok = all(is_int(x) for x in V + W)
    positive = int_ok and all(x > 0 for x in V + W)
    if int_ok:
        dV = V[k - 1] - V[k - 2]
        dq = W[k - 1] - W[k - 2]
        if dV < 1:
            out["M1"].append(f"Delta_V={dV} < 1")
        if dq < 1:
            out["M1"].append(f"Delta_q={dq} < 1")

    if not positive:
        # rho and u need positive week totals; every dependent gate fails explicitly
        for g in ("M2", "M3", "M4", "M5"):
            out[g].append("week totals must be positive integers to define u and rho")
    else:
        u = Fraction(P * V[k - 2], 100)               # same X_{k-1} in both members (M6)
        uW = Fraction(P * W[k - 2], 100)
        dV = V[k - 1] - V[k - 2]
        dq = W[k - 1] - W[k - 2]
        # M2: target rho in the windows (exact rationals)
        if not in_window(Fraction(dV) / u, ws["V"]):
            out["M2"].append(f"valid target rho={Fraction(dV) / u} outside W_V={ws['V']}")
        if not in_window(Fraction(dq) / uW, ws[t.cls]):
            out["M2"].append(f"violating target rho={Fraction(dq) / uW} outside W_{t.cls}={ws[t.cls]}")
        # M3: target guard
        if not u - dV >= 1:
            out["M3"].append(f"valid: u - Delta_V = {u - dV} < 1")
        if not dq - uW >= 1:
            out["M3"].append(f"violating: Delta_q - u = {dq - uW} < 1")
        # M4 and M5 over the transitions j = 1 .. N-1
        for name, X in (("valid", V), ("violating", W)):
            rhos = []
            for j in range(1, N):
                uj = Fraction(P * X[j - 1], 100)
                inc = X[j] - X[j - 1]
                rhos.append(Fraction(inc) / uj)
                if j != k - 1 and not uj - inc >= 1:
                    out["M4"].append(f"{name}: transition {j}->{j + 1}: u - increase = {uj - inc} < 1")
            tgt = rhos[k - 2]
            others = [r for jj, r in enumerate(rhos, start=1) if jj != k - 1]
            if not all(tgt > r for r in others):
                out["M5"].append(f"{name}: target rho={tgt} is not the strict unique maximum")

    # M6: exactly one displayed token differs, and it is week k
    diff = [w for w in range(1, N + 1) if V[w - 1] != W[w - 1]]
    if diff != [k]:
        out["M6"].append(f"differing weeks {diff}, expected exactly [{k}]")

    # M7: digit width of floor(X_k/10) equal in both members
    try:
        dv_, dw_ = digits(V[k - 1] // 10), digits(W[k - 1] // 10)
        if dv_ != dw_:
            out["M7"].append(f"digit width differs: floor(X_k/10) has {dv_} vs {dw_} digits")
    except SpecError:
        out["M7"].append("non-integer or negative target value")

    # M8: plausibility and tail floor for every X in both members
    for name, X in (("valid", V), ("violating", W)):
        for w, x in enumerate(X, start=1):
            try:
                ok = reg.X_min <= x <= reg.X_max
            except TypeError:
                ok = False
            if not ok:
                out["M8"].append(f"{name}: week {w} value {x} outside [{reg.X_min}, {reg.X_max}]")

    # M9: no flat step in either member
    for name, X in (("valid", V), ("violating", W)):
        for j in range(1, N):
            if X[j] == X[j - 1]:
                out["M9"].append(f"{name}: flat step {j}->{j + 1} at {X[j]}")

    # M10: downstream tokens (weeks > k) identical
    for w in range(k + 1, N + 1):
        if V[w - 1] != W[w - 1]:
            out["M10"].append(f"downstream week {w} differs: {V[w - 1]} vs {W[w - 1]}")
    return out


def mil_target_rho_violating(reg: Register, t: MilTemplate) -> Fraction:
    k = t.k
    u = Fraction(reg.P * t.viol[k - 2], 100)
    return Fraction(t.viol[k - 1] - t.viol[k - 2]) / u


# --------------------------------------------------------------------------------------
# Element-level gates Q1-Q11 (§5.4, §5.6)
# --------------------------------------------------------------------------------------
def pace_quantities(m: PacMember, G: int) -> Dict[str, List[Any]]:
    """Per-week exact quantities (§3.3): d_w, F_w, x_w (exact), e_w (rounded)."""
    D, T = m.D_dist, m.T_time
    F = [D * p for p in m.tokens]
    return {
        "d": [p - G for p in m.tokens],
        "F": F,
        "x": [f - T for f in F],
        "e": [rnd_star(f) - T for f in F],
    }


def same_minute_exact_form(G: int, tokens: Iterable[int]) -> bool:
    """§5.4 Q7 'exact form': sec_G >= largest negative deviation and 59 - sec_G >= largest positive."""
    tokens = list(tokens)
    secG = G % 60
    neg = max([0] + [G - p for p in tokens])
    pos = max([0] + [p - G for p in tokens])
    return secG >= neg and 59 - secG >= pos


def pac_element_gates(reg: Register, t: PacTemplate) -> Dict[str, List[str]]:
    """Evaluate Q1-Q11 for one PAC template from the member data. Empty list = gate passed."""
    N = reg.N
    ws = reg.windows_for("PAC").as_dict()
    out: Dict[str, List[str]] = {g: [] for g in PAC_ELEMENT_GATES}
    bad = _shape_failure(t.valid.tokens, N) or _shape_failure(t.viol.tokens, N)
    if bad:
        for g in out:
            out[g].append(f"malformed template: {bad}")
        return out
    k = t.k
    if not 2 <= k <= N:
        for g in out:
            out[g].append(f"target week k={k} outside 2..{N}")
        return out
    err = class_label_error(t.cls)
    if err:
        for g in out:
            out[g].append(err)
        return out
    G = t.G_b
    mv, mw = t.valid, t.viol
    ints_ok = (is_int(G) and is_int(mv.T_time) and is_int(mw.T_time)
               and is_int(mv.tau_F) and is_int(mw.tau_F) and mv.tau_F > 0 and mw.tau_F > 0
               and all(is_int(p) for p in mv.tokens + mw.tokens))
    if not ints_ok:
        for g in out:
            out[g].append("non-integer G, T_time or token, or tau_F not a positive integer")
        return out
    tau = mv.tau_F
    qv, qw = pace_quantities(mv, G), pace_quantities(mw, G)
    xv = [abs(x) for x in qv["x"]]
    xw = [abs(x) for x in qw["x"]]

    # Q1: T_time integer, plausibility range, representational limit
    for name, m in (("valid", mv), ("violating", mw)):
        if not (reg.Tm_min <= m.T_time <= reg.Tm_max):
            out["Q1"].append(f"{name}: T_time={m.T_time} outside [{reg.Tm_min}, {reg.Tm_max}]")
        if not m.T_time < 36000:
            out["Q1"].append(f"{name}: T_time={m.T_time} >= 36000")

    # Q2: m_NT >= 1 (register-level quantity, reported per template)
    mNT = m_NT_value(reg)
    if mNT < 1:
        out["Q2"].append(f"m_NT={mNT} < 1")

    # Q3: window membership of the exact target margin
    rho_v = Fraction(xv[k - 1]) / Fraction(tau)
    rho_w = Fraction(xw[k - 1]) / Fraction(mw.tau_F)
    if not in_window(rho_v, ws["V"]):
        out["Q3"].append(f"valid |x_k|/tau={rho_v} outside W_V={ws['V']}")
    if not in_window(rho_w, ws[t.cls]):
        out["Q3"].append(f"violating |x_k|/tau={rho_w} outside W_{t.cls}={ws[t.cls]}")

    # Q4: finish-time guard
    for w in range(1, N + 1):
        if not xv[w - 1] <= tau - 1:
            out["Q4"].append(f"valid: week {w} |x|={xv[w - 1]} > tau-1={tau - 1}")
        if w == k:
            if not xw[w - 1] >= mw.tau_F + 1:
                out["Q4"].append(f"violating: target |x_k|={xw[w - 1]} < tau+1={mw.tau_F + 1}")
        elif not xw[w - 1] <= mw.tau_F - 1:
            out["Q4"].append(f"violating: week {w} |x|={xw[w - 1]} > tau-1={mw.tau_F - 1}")

    # Q5: target is the strict unique maximum of |x| in both members; m_V > m_NT
    for name, xs in (("valid", xv), ("violating", xw)):
        if not all(xs[k - 1] > x for w, x in enumerate(xs, start=1) if w != k):
            out["Q5"].append(f"{name}: target |x_k|={xs[k - 1]} is not the strict unique maximum")
    mV = abs(qv["d"][k - 1])
    if not mV > mNT:
        out["Q5"].append(f"m_V={mV} <= m_NT={mNT}")

    # Q6: non-zero deviations
    for name, q in (("valid", qv), ("violating", qw)):
        for w in range(1, N + 1):
            if q["d"][w - 1] == 0:
                out["Q6"].append(f"{name}: d_{w} = 0 (token equals G)")
            if abs(q["x"][w - 1]) < 1:
                out["Q6"].append(f"{name}: |x_{w}|={abs(q['x'][w - 1])} < 1")

    # Q7: same-minute rule, minute digit in 1..9
    minute = G // 60
    if not 1 <= minute <= 9:
        out["Q7"].append(f"minute digit of G={G} is {minute}, not in 1..9")
    for name, m in (("valid", mv), ("violating", mw)):
        for w, p in enumerate(m.tokens, start=1):
            if p // 60 != minute:
                out["Q7"].append(f"{name}: token {w}={p} has minute {p // 60} != {minute}")

    # Q8: exactly one displayed token differs; identical length and first two characters
    try:
        sv = [render_pace(p) for p in mv.tokens]
        sw = [render_pace(p) for p in mw.tokens]
    except SpecError as e:
        out["Q8"].append(f"cannot render canonical numeral: {e}")
        sv = sw = None
    if sv is not None:
        diff = [w for w in range(1, N + 1) if sv[w - 1] != sw[w - 1]]
        if len(diff) != 1:
            out["Q8"].append(f"{len(diff)} displayed tokens differ, expected exactly 1")
        for w in diff:
            a, b2 = sv[w - 1], sw[w - 1]
            if len(a) != len(b2):
                out["Q8"].append(f"week {w}: different length {a!r} vs {b2!r}")
            elif a[:2] != b2[:2]:
                out["Q8"].append(f"week {w}: first two characters differ {a!r} vs {b2!r}")
            elif a[2:] == b2[2:]:
                out["Q8"].append(f"week {w}: seconds field identical")

    # Q9: D_dist, T_time, tau_F and every non-target token identical between members
    if mv.D_dist != mw.D_dist:
        out["Q9"].append("D_dist differs between members")
    if mv.T_time != mw.T_time:
        out["Q9"].append("T_time differs between members")
    if mv.tau_F != mw.tau_F:
        out["Q9"].append("tau_F differs between members")
    for w in range(1, N + 1):
        if w != k and mv.tokens[w - 1] != mw.tokens[w - 1]:
            out["Q9"].append(f"non-target token {w} differs: {mv.tokens[w - 1]} vs {mw.tokens[w - 1]}")

    # Q10: ground truth (rounded e_w, rnd*) equals the class by the exact x_w
    def verdict_rounded(q: Dict[str, List[Any]], tau_: int) -> str:
        return "violated" if any(abs(e) > tau_ for e in q["e"]) else "satisfied"

    def verdict_exact(q: Dict[str, List[Any]], tau_: int) -> str:
        return "violated" if any(abs(x) > tau_ for x in q["x"]) else "satisfied"

    for name, q, tau_, want in (("valid", qv, mv.tau_F, "satisfied"), ("violating", qw, mw.tau_F, "violated")):
        gr, ge = verdict_rounded(q, tau_), verdict_exact(q, tau_)
        if gr != ge or gr != want:
            out["Q10"].append(f"{name}: rounded verdict {gr}, exact verdict {ge}, expected {want}")

    # Q11: plausibility
    if not reg.G_min <= G <= reg.G_max:
        out["Q11"].append(f"G={G} outside [{reg.G_min}, {reg.G_max}]")
    for name, m in (("valid", mv), ("violating", mw)):
        for w, p in enumerate(m.tokens, start=1):
            if not reg.p_min <= p <= reg.p_max:
                out["Q11"].append(f"{name}: token {w}={p} outside [{reg.p_min}, {reg.p_max}]")
        if not reg.Tm_min <= m.T_time <= reg.Tm_max:
            out["Q11"].append(f"{name}: T_time={m.T_time} outside [{reg.Tm_min}, {reg.Tm_max}]")
    return out


def pac_target_rho_violating(t: PacTemplate) -> Fraction:
    q = pace_quantities(t.viol, t.G_b)
    return Fraction(abs(q["x"][t.k - 1])) / Fraction(t.viol.tau_F)


# --------------------------------------------------------------------------------------
# Registry-level gates M11, Q12, A1a, A2, A3, A4, A5, A6a, A7
# --------------------------------------------------------------------------------------
def class_separation(rhos_by_class: Dict[str, List[Fraction]]) -> List[str]:
    """M11 / Q12 (interpretation I-4 for an empty class)."""
    mild, clear = rhos_by_class.get("MILD", []), rhos_by_class.get("CLEAR", [])
    if not mild or not clear:
        return ["a class has no template: separation undefined (I-4)"]
    if not max(mild) < min(clear):
        return [f"max MILD rho={max(mild)} is not < min CLEAR rho={min(clear)}"]
    return []


def gate_M11(reg: Register, mil: Sequence[MilTemplate]) -> List[str]:
    by: Dict[str, List[Fraction]] = {"MILD": [], "CLEAR": []}
    bad: List[str] = []
    for t in mil:
        err = class_label_error(t.cls)
        if err:
            bad.append(f"{t.tid}: {err}")
        else:
            by[t.cls].append(mil_target_rho_violating(reg, t))
    return bad + class_separation(by)


def gate_Q12(pac: Sequence[PacTemplate]) -> List[str]:
    by: Dict[str, List[Fraction]] = {"MILD": [], "CLEAR": []}
    bad: List[str] = []
    for t in pac:
        err = class_label_error(t.cls)
        if err:
            bad.append(f"{t.tid}: {err}")
        else:
            by[t.cls].append(pac_target_rho_violating(t))
    return bad + class_separation(by)


def gate_A1a(mil: Sequence[MilTemplate], pac: Sequence[PacTemplate]) -> List[str]:
    fails: List[str] = []

    def dup(label: str, keys: List[Tuple[Any, ...]], ids: List[str]) -> None:
        seen: Dict[Tuple[Any, ...], str] = {}
        for key, tid in zip(keys, ids):
            if key in seen:
                fails.append(f"{label}: {tid} repeats the plan of {seen[key]}")
            else:
                seen[key] = tid

    ids = [t.tid for t in mil]
    dup("MIL valid", [t.valid for t in mil], ids)
    dup("MIL violating", [t.viol for t in mil], ids)
    ids = [t.tid for t in pac]
    dup("PAC valid", [(t.valid.T_time,) + t.valid.tokens for t in pac], ids)
    dup("PAC violating", [(t.viol.T_time,) + t.viol.tokens for t in pac], ids)
    return fails


def gate_A2(reg: Register, L_M: int, L_P: int) -> List[str]:
    fails: List[str] = []
    K = reg.N - 1
    if K % 2 != 0 or K < 4:
        fails.append(f"K={K} must be even and >= 4 (N odd, N >= 5)")
    if L_M % 2 != 0:
        fails.append(f"L_M={L_M} must be even")
    if L_P % 2 != 0:
        fails.append(f"L_P={L_P} must be even")
    return fails


def is_arithmetic_progression(seq: Sequence[Any]) -> bool:
    if len(seq) < 2:
        return False
    d = seq[1] - seq[0]
    return all(seq[j + 1] - seq[j] == d for j in range(len(seq) - 1))


def gate_A5(reg: Register) -> Dict[str, List[str]]:
    """A5(a), A5(b), A5(c) (§9.6). (c) is not evaluated if a window bound fails (a)."""
    res: Dict[str, List[str]] = {"a": [], "b": [], "c": []}
    a = res["a"]
    for name in ("P", "N", "tau_F"):
        v = getattr(reg, name)
        if not (is_int(v) and v > 0):
            a.append(f"{name}={v!r} must be a positive integer")
    if not reg.rho_NT > 0:
        a.append("rho_NT must be > 0")
    window_ok = True
    for label, ws in reg.declared_window_sets():
        if ws is None:
            a.append(f"window set {label}: missing")
            window_ok = False
            continue
        for bname, bv in zip(("v_lo", "v_hi", "mi_lo", "mi_hi", "c_lo", "c_hi"), ws.bounds()):
            if bv is None:
                a.append(f"window set {label}: bound {bname} missing")
                window_ok = False
            elif not bv > 0:
                a.append(f"window set {label}: bound {bname}={bv} must be positive")
                window_ok = False
        if window_ok:
            for wn, (lo, hi) in ws.as_dict().items():
                if not 0 < lo <= hi:
                    a.append(f"window set {label}: W_{wn} must satisfy 0 < lo <= hi")
                    window_ok = False
    def _check(ok_fn: Any, msg: str) -> None:
        try:
            ok = bool(ok_fn())
        except TypeError:
            ok = False
        if not ok:
            a.append(msg)

    for lo_n, hi_n in (("X_min", "X_max"), ("p_min", "p_max"), ("G_min", "G_max"), ("Tm_min", "Tm_max")):
        _check(lambda lo_n=lo_n, hi_n=hi_n: getattr(reg, lo_n) <= getattr(reg, hi_n),
               f"{lo_n} <= {hi_n} violated or not numeric")
    _check(lambda: 60 <= reg.p_min, "p_min must be >= 60")
    _check(lambda: reg.p_max <= 599, "p_max must be <= 599")
    _check(lambda: reg.Tm_min > 0, "Tm_min must be > 0")
    _check(lambda: reg.Tm_max < 36000, "Tm_max must be < 36000")
    if not (reg.D_dist > 0 and has_finite_decimal(reg.D_dist)):
        a.append("D_dist must be a positive rational with a finite decimal expansion")
    for name, lst in (("S_list", reg.S_list), ("G_list", reg.G_list)):
        if not all(is_int(v) and v > 0 for v in lst):
            a.append(f"{name}: all elements must be positive integers")
        elif len(lst) < 2 or len(lst) % 2 != 0:
            a.append(f"{name}: length {len(lst)} must be 2n with n >= 1")
        elif not is_arithmetic_progression(lst) or lst[1] - lst[0] <= 0:
            a.append(f"{name}: must be an arithmetic progression with a positive step")
    # (b) sizing-input domains
    b = res["b"]
    sz = reg.sizing
    if sz is None:
        b.append("sizing inputs missing")
    else:
        if sz.z is None or not sz.z > 0:
            b.append("z must be > 0")
        if sz.hw_star is None or not sz.hw_star > 0:
            b.append("hw* must be > 0")
        if not (is_int(sz.C_max) and sz.C_max > 0):
            b.append("C_max must be a positive integer")
        if not (is_int(sz.R_min) and sz.R_min >= 2):
            b.append("R_min must be an integer >= 2")
        if not (is_int(sz.R_cap) and is_int(sz.R_min) and sz.R_cap >= sz.R_min):
            b.append("R_cap must be an integer >= R_min")
        if not (is_int(sz.n_boot) and sz.n_boot > 0):
            b.append("n_boot must be a positive integer")
        if not (is_int(sz.L_small) and sz.L_small > 0):
            b.append("L_small must be a positive integer")
        if sz.level is None or not 0 < sz.level < 1:
            b.append("interval level must be in (0,1)")
    # (c) window sides and ordering, per declared set; only if (a) left the windows well-formed
    c = res["c"]
    if not window_ok:
        c.append("not evaluated: a window bound failed A5(a)")
    else:
        for label, ws in reg.declared_window_sets():
            if not ws.v_hi < 1 < ws.mi_lo:
                c.append(f"window set {label}: need v_hi < 1 < mi_lo")
            if not ws.mi_hi < ws.c_lo:
                c.append(f"window set {label}: need mi_hi < c_lo (strict)")
    return res


def expected_class_matrix(K: int, L: int, directions: int) -> Dict[Tuple[int, int, int], str]:
    return {(b, i, s): class_of(b, i, s, K) for b in range(L) for i in range(K) for s in range(directions)}


def _margin_failures(classes: Dict[Tuple[int, int, int], str], K: int, L: int, directions: int, label: str) -> List[str]:
    """§9.3 items 1-3: base x direction x class, slot x class, direction x class (all exact)."""
    fails: List[str] = []
    for b in range(L):
        for s in range(directions):
            n = sum(1 for i in range(K) if classes[(b, i, s)] == "MILD")
            if n != K // 2:
                fails.append(f"{label}: base {b} direction {s} has {n} MILD positions, expected {K // 2}")
    for i in range(K):
        for s in range(directions):
            n = sum(1 for b in range(L) if classes[(b, i, s)] == "MILD")
            if n != L // 2:
                fails.append(f"{label}: slot ({i},{s}) has {n} MILD bases, expected {L // 2}")
    for s in range(directions):
        n = sum(1 for b in range(L) for i in range(K) if classes[(b, i, s)] == "MILD")
        if n != L * K // 2:
            fails.append(f"{label}: direction {s} has {n} MILD templates, expected {L * K // 2}")
    return fails


def gate_A6a(reg: Register, mil: Sequence[MilTemplate], pac: Sequence[PacTemplate]) -> List[str]:
    K = reg.K
    fails: List[str] = []
    for label, tpls, L, dirs in (("MIL", mil, len(reg.S_list), 1), ("PAC", pac, len(reg.G_list), 2)):
        got: Dict[Tuple[int, int, int], str] = {}
        for t in tpls:
            key = (t.b, t.i, getattr(t, "s", 0))
            if key in got:
                fails.append(f"{label}: duplicate slot {key}")
            got[key] = t.cls
        want = expected_class_matrix(K, L, dirs)
        if set(got) != set(want):
            fails.append(f"{label}: slot set differs from the rule of §9.2")
            continue
        for key, cls in want.items():
            if got[key] != cls:
                fails.append(f"{label}: slot {key} has class {got[key]}, rule gives {cls}")
        fails += _margin_failures(got, K, L, dirs, label)
    return fails


def gate_A7(reg: Register, mil: Sequence[MilTemplate], pac: Sequence[PacTemplate]) -> List[str]:
    """A7 (§9.6): every base appears in every (position, [direction]) slot exactly once and in both classes
    equally; every cluster has equal template counts per reporting cell (§9.5). Evaluated over the expected
    (base, position, direction) set AND over every observed template (interpretation I-10): a missing, repeated
    or extra template is a failure, and so is an extra base (an extra cluster with unequal counts)."""
    K = reg.K
    fails: List[str] = []
    for label, tpls, L, dirs in (("MIL", mil, len(reg.S_list), 1), ("PAC", pac, len(reg.G_list), 2)):
        expected = {(b, i, s) for b in range(L) for i in range(K) for s in range(dirs)}
        counts: Dict[Tuple[int, int, int], int] = {}
        for t in tpls:
            key = (t.b, t.i, getattr(t, "s", 0))
            counts[key] = counts.get(key, 0) + 1
            err = class_label_error(t.cls)
            if err:
                fails.append(f"{label}: template {t.tid}: {err}")
        for key in sorted(expected):
            n = counts.get(key, 0)
            if n != 1:
                fails.append(f"{label}: base {key[0]} appears {n} times in slot ({key[1]},{key[2]}), expected once")
        for key in sorted(set(counts) - expected):
            fails.append(f"{label}: unexpected template at base {key[0]}, position {key[1]}, direction {key[2]} "
                         f"(expected {L} bases x {K} positions x {dirs} direction(s))")
        slots = sorted({(k[1], k[2]) for k in counts} | {(i, s) for i in range(K) for s in range(dirs)})
        for i, s in slots:
            nm = sum(1 for t in tpls if t.i == i and getattr(t, "s", 0) == s and t.cls == "MILD")
            nc = sum(1 for t in tpls if t.i == i and getattr(t, "s", 0) == s and t.cls == "CLEAR")
            if nm != nc:
                fails.append(f"{label}: slot ({i},{s}) has {nm} MILD vs {nc} CLEAR")
        # equal template counts per reporting cell and cluster (§9.5), over every observed cluster
        for b in sorted({t.b for t in tpls} | set(range(L))):
            mine = [t for t in tpls if t.b == b]
            want_total = K * dirs
            if len(mine) != want_total:
                fails.append(f"{label}: cluster {b} has {len(mine)} templates, expected {want_total}")
            for cls in VALID_CLASSES:
                n = sum(1 for t in mine if t.cls == cls)
                if n != K * dirs // 2:
                    fails.append(f"{label}: cluster {b} has {n} {cls} templates, expected {K * dirs // 2}")
            if dirs == 2:
                for s in (0, 1):
                    for cls in VALID_CLASSES:
                        n = sum(1 for t in mine if t.s == s and t.cls == cls)
                        if n != K // 2:
                            fails.append(f"{label}: cluster {b} direction {s} has {n} {cls}, expected {K // 2}")
    return fails


# --------------------------------------------------------------------------------------
# Orchestration (§21.1 step 4 order: A5 -> A2 -> build -> element gates -> registry gates)
# --------------------------------------------------------------------------------------
@dataclass
class GateOutcome:
    gate: str
    evaluated: bool
    passed: bool
    failures: List[str] = field(default_factory=list)


@dataclass
class Report:
    outcomes: Dict[str, GateOutcome]
    built: bool
    n_mil: int = 0
    n_pac: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(o.evaluated and o.passed for o in self.outcomes.values()) and len(self.outcomes) == len(NUMERIC_GATES)

    def failing(self) -> List[str]:
        return [g for g, o in self.outcomes.items() if not (o.evaluated and o.passed)]


def _outcome(gate: str, failures: List[str], evaluated: bool = True) -> GateOutcome:
    return GateOutcome(gate, evaluated, evaluated and not failures, list(failures))


def _not_evaluated(gate: str, why: str) -> GateOutcome:
    return GateOutcome(gate, False, False, [why])


def run_numeric_gates(reg: Register) -> Report:
    """Evaluate all 30 numeric-stage gates on a register (§3.7, §21.1 step 4).

    This is the computation of Implementation A. Running it on a parameter set is a dry-run
    step; this module never runs it by itself.
    """
    outcomes: Dict[str, GateOutcome] = {}
    notes: List[str] = []
    a5 = gate_A5(reg)
    a5_fail = a5["a"] + a5["b"] + a5["c"]
    outcomes["A5"] = _outcome("A5", [f"A5({k}): {m}" for k in "abc" for m in a5[k]])
    L_M, L_P = len(reg.S_list), len(reg.G_list)
    a2_fail: List[str] = []
    if a5["a"]:
        outcomes["A2"] = _not_evaluated("A2", "A5(a) failed (interpretation I-8)")
    else:
        a2_fail = gate_A2(reg, L_M, L_P)
        outcomes["A2"] = _outcome("A2", a2_fail)
    if a5_fail or a2_fail or a5["a"]:
        for g in NUMERIC_GATES:
            if g not in outcomes:
                outcomes[g] = _not_evaluated(g, "build not constructed because A5/A2 failed (interpretation I-8)")
        notes.append("build not constructed")
        return Report(outcomes, False, notes=notes)

    mil, pac = [], []
    pac_error: Optional[str] = None
    K = reg.K
    for b in range(L_M):
        for i in range(K):
            mil.append(build_mil_template(reg, b, i, class_of(b, i, 0, K)))
    try:
        for b in range(L_P):
            for i in range(K):
                for s in (0, 1):
                    pac.append(build_pac_template(reg, b, i, s, class_of(b, i, s, K)))
    except ConstructionError as e:
        pac_error = str(e)
        pac = []

    # element-level MIL gates
    mil_fail: Dict[str, List[str]] = {g: [] for g in MIL_ELEMENT_GATES}
    bad_S: Dict[int, List[str]] = {}
    for t in mil:
        res = mil_element_gates(reg, t)
        for g, msgs in res.items():
            for m in msgs:
                mil_fail[g].append(f"{t.tid}: {m}")
                bad_S.setdefault(t.b, []).append(f"{g}")
    for g in MIL_ELEMENT_GATES:
        outcomes[g] = _outcome(g, mil_fail[g])
    outcomes["M11"] = _outcome("M11", gate_M11(reg, mil))

    # element-level PAC gates
    if pac_error is not None:
        for g in PAC_ELEMENT_GATES + ("Q12",):
            if g == "Q2":
                outcomes[g] = _outcome(g, [f"construction impossible: {pac_error}"])
            else:
                outcomes[g] = _not_evaluated(g, f"PAC templates not constructed: {pac_error}")
    else:
        pac_fail: Dict[str, List[str]] = {g: [] for g in PAC_ELEMENT_GATES}
        bad_G: Dict[int, List[str]] = {}
        for t in pac:
            res = pac_element_gates(reg, t)
            for g, msgs in res.items():
                for m in msgs:
                    pac_fail[g].append(f"{t.tid}: {m}")
                    bad_G.setdefault(t.b, []).append(g)
        for g in PAC_ELEMENT_GATES:
            outcomes[g] = _outcome(g, pac_fail[g])
        outcomes["Q12"] = _outcome("Q12", gate_Q12(pac))

    # registry-level
    if pac_error is None:
        outcomes["A1a"] = _outcome("A1a", gate_A1a(mil, pac))
        outcomes["A6a"] = _outcome("A6a", gate_A6a(reg, mil, pac))
        outcomes["A7"] = _outcome("A7", gate_A7(reg, mil, pac))
        a3 = [f"S_list element {reg.S_list[b]} (base {b}) fails {sorted(set(gs))}" for b, gs in sorted(bad_S.items())]
        a3 += [f"G_list element {reg.G_list[b]} (base {b}) fails {sorted(set(gs))}" for b, gs in sorted(bad_G.items())]
        outcomes["A3"] = _outcome("A3", a3)
        a4 = [m for g in ("Q2", "Q3", "Q4", "Q5", "Q6") for m in outcomes[g].failures]
        outcomes["A4"] = _outcome("A4", a4)
    else:
        for g in ("A1a", "A6a", "A7", "A3"):
            outcomes[g] = _not_evaluated(g, f"PAC templates not constructed: {pac_error}")
        outcomes["A4"] = _outcome("A4", [f"construction impossible: {pac_error}"])
    return Report(outcomes, pac_error is None, len(mil), len(pac), notes)


def a3_all_classes(reg: Register) -> List[str]:
    """Stricter reading of A3 (interpretation I-2), informative only: every element at every
    slot with BOTH classes. Requires a register that passed A5 and A2."""
    fails: List[str] = []
    K = reg.K
    for b in range(len(reg.S_list)):
        for i in range(K):
            for cls in ("MILD", "CLEAR"):
                t = build_mil_template(reg, b, i, cls)
                for g, msgs in mil_element_gates(reg, t).items():
                    fails += [f"MIL(b={b},i={i},{cls}) {g}: {m}" for m in msgs]
    for b in range(len(reg.G_list)):
        for i in range(K):
            for s in (0, 1):
                for cls in ("MILD", "CLEAR"):
                    t = build_pac_template(reg, b, i, s, cls)
                    for g, msgs in pac_element_gates(reg, t).items():
                        fails += [f"PAC(b={b},i={i},s={s},{cls}) {g}: {m}" for m in msgs]
    return fails


# --------------------------------------------------------------------------------------
# Reading-invariance (guard) checks (§6.1, Propositions GM and GP; interpretation I-6)
# --------------------------------------------------------------------------------------
def _round_mode(mode: str, y: Fraction) -> int:
    if mode == "floor":
        return math.floor(y)
    if mode == "ceil":
        return math.ceil(y)
    if mode == "nearest":
        return nearest(y)
    if mode == "half_up":
        return rnd_star(y)
    if mode == "half_down":
        return math.ceil(y - Fraction(1, 2))
    if mode == "half_even":
        return round(y)
    raise SpecError(mode)


def mil_reading_verdicts(P: int, X: Sequence[int]) -> Dict[Tuple[str, str], str]:
    """Plan verdict under the 3 threshold roundings x 2 comparisons; keys (rounding, comparison)."""
    out: Dict[Tuple[str, str], str] = {}
    for mode in ("floor", "ceil", "nearest"):
        for cmp_ in ("over_cap", "at_or_over_cap"):
            violated = False
            for w in range(1, len(X)):
                cap = _round_mode(mode, Fraction(P * X[w - 1], 100))
                inc = X[w] - X[w - 1]
                if (inc > cap) if cmp_ == "over_cap" else (inc >= cap):
                    violated = True
            out[(mode, cmp_)] = "violated" if violated else "satisfied"
    return out


def pac_reading_verdicts(m: PacMember) -> Dict[Tuple[str, str], str]:
    """Plan verdict under F unrounded or rounded (5 modes), each inclusive / exclusive."""
    out: Dict[Tuple[str, str], str] = {}
    for mode in ("unrounded", "floor", "ceil", "half_up", "half_down", "half_even"):
        for cmp_ in ("inclusive", "exclusive"):
            violated = False
            for p in m.tokens:
                F = m.D_dist * p
                e = F - m.T_time if mode == "unrounded" else _round_mode(mode, F) - m.T_time
                if (abs(e) > m.tau_F) if cmp_ == "inclusive" else (abs(e) >= m.tau_F):
                    violated = True
            out[(mode, cmp_)] = "violated" if violated else "satisfied"
    return out


def reading_invariance_mil(reg: Register, t: MilTemplate) -> List[str]:
    fails = []
    for name, X, want in (("valid", t.valid, "satisfied"), ("violating", t.viol, "violated")):
        for key, v in mil_reading_verdicts(reg.P, X).items():
            if v != want:
                fails.append(f"{t.tid} {name}: reading {key} gives {v}, expected {want}")
    return fails


def reading_invariance_pac(t: PacTemplate) -> List[str]:
    fails = []
    for name, m, want in (("valid", t.valid, "satisfied"), ("violating", t.viol, "violated")):
        for key, v in pac_reading_verdicts(m).items():
            if v != want:
                fails.append(f"{t.tid} {name}: reading {key} gives {v}, expected {want}")
    return fails


# --------------------------------------------------------------------------------------
# Screening aids (§4 RC-M*, §5.5 RC-P*, §5.6 G_adm): reported, never reject (§3.7)
# --------------------------------------------------------------------------------------
def xprev_max(reg: Register) -> int:
    x = max(reg.S_list)
    for _ in range(reg.N - 2):
        x = ramp(x, reg.gamma)
    return x


def screen_mil(reg: Register) -> Dict[str, Any]:
    ws = reg.windows_for("MIL")
    d = ws.as_dict()
    U = Fraction(reg.P * min(reg.S_list), 100)
    gam = reg.gamma
    res: Dict[str, Any] = {"U_M": U}
    res["RC-M1"] = (0 < reg.rho_NT < ws.v_lo <= ws.v_hi < 1 < ws.mi_lo <= ws.mi_hi < ws.c_lo <= ws.c_hi)
    res["RC-M2"] = all((hi - lo) * U >= 1 for lo, hi in d.values())
    res["RC-M3"] = ws.v_hi <= 1 - 1 / U and ws.mi_lo >= 1 + 1 / U
    res["RC-M4"] = reg.rho_NT * U >= 1
    res["RC-M5"] = digits(reg.X_min // 10) == digits(reg.X_max // 10)
    res["RC-M6"] = reg.X_min >= 1 / gam and min(reg.S_list) * (1 - gam) ** (reg.N - 2) >= reg.X_min
    xp = xprev_max(reg)
    res["Xprev_max"] = xp
    res["RC-M7"] = xp * (1 + ws.c_hi * reg.P / 100) <= reg.X_max and min(reg.S_list) >= reg.X_min
    return res


def m_star(reg: Register) -> int:
    ws = reg.windows_for("PAC")
    return max(m_NT_value(reg), math.floor((ws.c_hi * reg.tau_F + Fraction(1, 2)) / reg.D_dist))


def tau_interval(reg: Register) -> Dict[str, Any]:
    ws = reg.windows_for("PAC")
    d = ws.as_dict()
    omega_min = min(hi - lo for lo, hi in d.values())
    terms = []
    undefined = False
    for num, den in ((reg.D_dist, omega_min), (1, 1 - ws.v_hi), (1, ws.mi_lo - 1),
                     (reg.D_dist + Fraction(1, 2), reg.rho_NT), (1, ws.v_lo)):
        if den > 0:
            terms.append(Fraction(num) / den)
        else:
            undefined = True
    tau_hi = (30 * reg.D_dist - Fraction(1, 2)) / ws.c_hi
    if undefined:
        return {"undefined": True, "tau_hi": tau_hi}
    tau_lo = max(terms)
    ints = [t for t in range(math.ceil(tau_lo), math.ceil(tau_hi)) if tau_lo <= t < tau_hi]
    return {"undefined": False, "tau_lo": tau_lo, "tau_hi": tau_hi, "integers": ints,
            "tau_F_inside": reg.tau_F in ints}


def eligible_paces(reg: Register) -> List[int]:
    """§5.6 G_adm restricted to integers in [G_min, G_max]."""
    ms = m_star(reg)
    out = []
    for G in range(reg.G_min, reg.G_max + 1):
        if (reg.p_min + ms <= G <= reg.p_max - ms
                and reg.Tm_min <= rnd_star(reg.D_dist * G) <= reg.Tm_max
                and 1 <= G // 60 <= 9
                and ms <= G % 60 <= 59 - ms):
            out.append(G)
    return out


def screen_pac(reg: Register) -> Dict[str, Any]:
    ws = reg.windows_for("PAC")
    d = ws.as_dict()
    res: Dict[str, Any] = {}
    res["RC-P1"] = (0 < reg.rho_NT < ws.v_lo <= ws.v_hi < 1 < ws.mi_lo <= ws.mi_hi < ws.c_lo <= ws.c_hi)
    res["RC-P2"] = all((hi - lo) * reg.tau_F >= reg.D_dist for lo, hi in d.values())
    res["RC-P3"] = ws.v_hi <= 1 - Fraction(1, reg.tau_F) and ws.mi_lo >= 1 + Fraction(1, reg.tau_F)
    res["RC-P4"] = reg.rho_NT * reg.tau_F >= reg.D_dist + Fraction(1, 2)
    res["RC-P5"] = reg.D_dist >= Fraction(3, 2) and ws.v_lo * reg.tau_F >= 1
    res["RC-P6"] = (ws.c_hi * reg.tau_F + Fraction(1, 2)) / reg.D_dist < 30
    res["RC-P7"] = reg.p_min <= reg.G_min <= reg.G_max <= reg.p_max
    res["m_star"] = m_star(reg)
    res["tau_interval"] = tau_interval(reg)
    elig = eligible_paces(reg)
    res["G_adm"] = elig
    res["RC-P8"] = len(elig) > 0
    return res
