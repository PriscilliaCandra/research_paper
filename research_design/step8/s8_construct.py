"""Step 8 registry construction -- independent implementation (decision record 12.28, F-4 Architecture 1).

Written from specification v2.7 sections 4, 5.2, 9.2 and 10. It does NOT import Implementation A (impl_a.py): the two
constructions are compared by s8_build.py (equivalence criteria E-ID, E-PAR, E-GATE, E-PROMPT). The same author wrote both,
so the independence is limited to the code path (decision record 12.26/12.28). Exact arithmetic only (Fraction); no float.

Input: a plain dict `params` (see params_from_fz) and the two base lists. Output: the list of template dicts in canonical
order (position ascending, direction slower first, base ascending; section 10) with canonical IDs.
"""
import math
from fractions import Fraction as F

CONDITIONS = ("A", "B", "C", "D")


def nearest(y):
    """Nearest integer, ties toward the lower integer (section 4)."""
    y = F(y)
    n = math.floor(y)
    return n + 1 if y - n > F(1, 2) else n


def rnd_star(y):
    """Nearest whole second, ties up (section 3.3)."""
    return math.floor(F(y) + F(1, 2))


def class_of(b, i, s, K):
    """Section 9.2: MILD iff ((i - a(b,s)) mod K) < K/2, a = (cpl + s*dir_shift + b_par*K/2) mod K."""
    a = (b // 2 + s * (K // 4) + (b % 2) * (K // 2)) % K
    return "MILD" if (i - a) % K < K // 2 else "CLEAR"


def params_from_fz(register):
    """Plain parameter dict from the 'register' object of PARAMETER_FREEZE_MANIFEST_FZ_v1.json."""
    w = register["row4_windows_shared_OD5"]
    win = {k: (F(w["W_" + k][0]), F(w["W_" + k][1])) for k in ("V", "MILD", "CLEAR")}
    s, g = register["row7_S_list"], register["row8_G_list"]
    return {
        "P": register["row1_P_percent"], "N": register["row2_N"], "rho_NT": F(register["row3_rho_NT"]),
        "windows": win, "D": F(register["row5_D_dist_km"]), "tau_F": register["row6_tau_F_s"],
        "S_main": tuple(s["S_0"] + j * s["step"] for j in range(s["length"])),
        "G_main": tuple(g["G_0"] + j * g["step"] for j in range(g["length"])),
        "S_pilot": tuple(register["row18_pilot"]["S_pilot"]), "G_pilot": tuple(register["row18_pilot"]["G_pilot"]),
    }


def _mid(w):
    return (w[0] + w[1]) / 2


def _dnt(X, gamma):
    return max(1, math.floor(gamma * X))


def build_mil(p, b, S, i, cls):
    """Section 4 algorithm for one template. Returns (valid, violating) weekly totals in tenths of km."""
    N, P = p["N"], p["P"]
    gamma = p["rho_NT"] * P / 100
    k = i + 2
    X = [S]
    for _ in range(1, k - 1):
        X.append(X[-1] + _dnt(X[-1], gamma))
    xprev = X[k - 2]
    u = F(P * xprev, 100)
    dV = nearest(_mid(p["windows"]["V"]) * u)
    dq = nearest(_mid(p["windows"][cls]) * u)
    valid, viol = X + [xprev + dV], X + [xprev + dq]
    for j in range(k, N):
        nxt = valid[j - 1] - _dnt(valid[j - 1], gamma)
        valid.append(nxt)
        viol.append(nxt)
    return valid, viol


def build_pac(p, b, G, i, s, cls):
    """Section 5.2 algorithm for one template. Returns (valid tokens, violating tokens, T_time)."""
    N, tau, D = p["N"], p["tau_F"], p["D"]
    k = i + 2
    sigma = 1 if s == 0 else -1
    T = rnd_star(D * G)
    r = T - D * G
    m_nt = math.floor((p["rho_NT"] * tau - F(1, 2)) / D)
    if m_nt < 1:
        raise ValueError("m_NT < 1 (gate Q2)")
    d = {}
    ordinal = 0
    for w in range(1, N + 1):
        if w == k:
            continue
        ordinal += 1
        d[w] = (-1) ** (ordinal + 1) * (1 + ((ordinal - 1) % m_nt))
    mV = nearest((_mid(p["windows"]["V"]) * tau + sigma * r) / D)
    mq = nearest((_mid(p["windows"][cls]) * tau + sigma * r) / D)
    tv = [G + (sigma * mV if w == k else d[w]) for w in range(1, N + 1)]
    tw = [G + (sigma * mq if w == k else d[w]) for w in range(1, N + 1)]
    return tv, tw, T


def construct(p, S_list, G_list):
    """All templates of section 9.2 in canonical order (section 10), IDs assigned. Returns a list of dicts."""
    K = p["N"] - 1
    mil, pac = [], []
    for i in range(K):                                   # canonical: position, (direction), base
        for b, S in enumerate(S_list):
            cls = class_of(b, i, 0, K)
            v, w = build_mil(p, b, S, i, cls)
            mil.append({"family": "MIL", "base_index": b, "base_value": S, "position_index": i, "target_week": i + 2,
                        "direction": None, "class": cls, "valid": {"weekly_tenths_km": v}, "violating": {"weekly_tenths_km": w}})
    for i in range(K):
        for s in (0, 1):
            for b, G in enumerate(G_list):
                cls = class_of(b, i, s, K)
                tv, tw, T = build_pac(p, b, G, i, s, cls)
                mem = lambda toks: {"tokens_s_per_km": toks, "D_dist": {"num": p["D"].numerator, "den": p["D"].denominator},
                                    "T_time_s": T, "tau_F_s": p["tau_F"]}
                pac.append({"family": "PAC", "base_index": b, "base_value": G, "position_index": i, "target_week": i + 2,
                            "direction": s, "class": cls, "valid": mem(tv), "violating": mem(tw)})
    out = []
    for fam, lst in (("MIL", mil), ("PAC", pac)):
        width = max(3, len(str(len(lst))))
        for n, t in enumerate(lst, start=1):
            t = dict(t)
            t["id"] = f"{fam}-{n:0{width}d}"
            t["members"] = {"valid": f"{t['id']}-V", "violating": f"{t['id']}-{t['class']}"}
            out.append(t)
    return out
