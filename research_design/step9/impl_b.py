"""Implementation B (specification v2.7 section 11 layer 2, section 21.1 step 9) -- decision record 12.29.

Reads the FINAL RENDERED PROMPT STRINGS of a published Step 8 build, extracts every quantity by a strict parse, recomputes member
states and rho with decimal.Decimal (explicit half-up quantization for rnd*, strict '>'), and re-verifies PG1-PG3, PG5-PG10, PG11a,
PG12, A1b, A6b, A7 and the guard (Propositions GM, GP) on the parsed values. Standard library only: it imports NO project module
(not Implementation A, not Step 7, not Step 8), as section 11 requires ("written from section 3 without importing or reading A").
Strict parse: every field must be found exactly once with one value; a missing field, a duplicate, or two values for one quantity
is an ambiguity/contradiction FAILURE. Registry metadata (family, base index, position, direction, class, target week) is read
only to know WHICH template a prompt belongs to; no number is taken from the registry except the windows of register_values.
The author of A, of Step 8 and of this module is the same person: this is code-path independence only (decision record 12.29).

Usage: python -B -I impl_b.py <build folder> [report.json]      exit 0 = AGREE on everything, 1 = failures.
"""
import hashlib
import json
import os
import re
import sys
import unicodedata
from decimal import Decimal, ROUND_HALF_UP, getcontext
from fractions import Fraction as F

getcontext().prec = 60
NUM = re.compile(r"[0-9]+(?:[.:][0-9]+)*")
BND_L, BND_R = r"(?<![a-z0-9_])", r"(?![a-z0-9_])"
PT = [  # section 8.1 table, fixed order; True = case-sensitive (runs on the original string)
    ("PT1", BND_L + r"(?:mil|pac)-[0-9]{3,}(?:-(?:v|mild|clear))?" + BND_R, False),
    ("PT2", BND_L + r"[0-9a-f]{16,}" + BND_R, False),
    ("PT3", BND_L + r"(?:cond|condition|arm|stratum)[_=-][abcd]" + BND_R, False),
    ("PT4", r"(?<![A-Za-z0-9_])(?:MILD|CLEAR|MIL|PAC)(?![A-Za-z0-9_])", True),
    ("PT5", BND_L + r"[a-z]+_(?:id|class|cond|index|label|member|position|direction|cluster|base|arm)" + BND_R, False),
    ("PT5b", BND_L + r"(?:template|member|cluster|base)_[a-z0-9]+" + BND_R, False),
    ("PT6", BND_L + r"(?:cond|class|member|template|cluster|pos|dir|sigma)\s*=\s*\S", False),
    ("PT7", BND_L + r"target[_-](?:week|position|pos|token|transition)" + BND_R, False),
    ("PT7b", r"\((?:target|valid|violating|mild|clear)\)", False),
    ("PT8", BND_L + r"(?:valid|violating)[_-](?:plan|member|version|variant|arm|case|template)" + BND_R, False),
    ("PT8b", BND_L + r"(?:plan|member|case)[_-](?:valid|violating)" + BND_R, False),
    ("PT9", r"\{\{|\}\}|\[\[|\]\]|<<|>>|%\([a-z_]+\)s|\{[a-z_]+\}", False),
]
PT10 = r"(?<=[0-9])[*_~`]|[*_~`](?=[0-9])|\[\^[^\]]*\]"
BUNDLE_SCAN = {"PT1", "PT3", "PT4", "PT5", "PT5b", "PT6", "PT7", "PT7b", "PT8", "PT8b"}   # section 8.2: PT1, PT3-PT8b
FORBIDDEN = {chr(c) for c in (9, 11, 12, 13, 28, 29, 30, 0x85, 0xA0, 0x200B, 0x200C, 0x200D, 0x2028, 0x2029, 0x2060, 0xFEFF)}


class Ambiguity(Exception):
    pass


def sha(b):
    return hashlib.sha256(b).hexdigest()


def once(rx, text, what):
    m = re.findall(rx, text, flags=re.M)
    if len(set(m)) != 1:
        raise Ambiguity(f"{what}: found {len(m)} occurrence(s) with {len(set(m))} distinct value(s)")
    return m[0]


def parse_prompt(text, fam, N):
    """Strict parse. Returns dict of Decimal/int quantities."""
    out = {}
    if fam == "MIL":
        out["P"] = int(once(r"([0-9]+) percent", text, "cap P"))
        if len(re.findall(r"[0-9]+ percent", text)) < 1:
            raise Ambiguity("cap P missing")
        rows = re.findall(r"^\| ([0-9]+) \| ([0-9]+\.[0-9]) \|$", text, flags=re.M)
        out["rows"] = [(int(w), Decimal(v)) for w, v in rows]
    else:
        d_rule = once(r"distance of ([0-9.]+) km", text, "D in rule")
        d_line = once(r"^Distance: ([0-9.]+) km$", text, "D line")
        if d_rule != d_line:
            raise Ambiguity("two values for D_dist")
        out["D"] = Decimal(d_line)
        taus = set(re.findall(r"within ([0-9]+) seconds|exactly ([0-9]+) seconds|more than ([0-9]+) seconds", text))
        vals = {x for tup in taus for x in tup if x}
        if len(vals) != 1:
            raise Ambiguity("tau_F: none or several values")
        out["tau"] = int(vals.pop())
        h, m, s = (int(z) for z in once(r"^Target time: ([0-9]:[0-5][0-9]:[0-5][0-9])$", text, "T_time").split(":"))
        out["T"] = h * 3600 + m * 60 + s
        rows = re.findall(r"^\| ([0-9]+) \| ([1-9]):([0-5][0-9]) \|$", text, flags=re.M)
        out["rows"] = [(int(w), int(mi) * 60 + int(se)) for w, mi, se in rows]
    if [w for w, _ in out["rows"]] != list(range(1, N + 1)):
        raise Ambiguity("table weeks are not 1..N exactly once")
    return out


def rho_and_state(fam, q, k):
    """Exact target rho, violation flag, and whether every week respects the one-unit guard slack (Propositions GM/GP)."""
    rows = [v for _, v in q["rows"]]
    if fam == "MIL":
        X = [int(v * 10) for v in rows]                 # tenths of km
        P = q["P"]
        viol, guard = False, True
        for j in range(1, len(X)):
            inc = X[j] - X[j - 1]
            u = Decimal(P * X[j - 1]) / 100
            if Decimal(inc) * 100 > Decimal(P) * X[j - 1]:
                viol = True
            if j + 1 == k:
                pass
            slack = (inc - u) if j + 1 == k and Decimal(inc) > u else (u - inc)
            if j + 1 != k and not (u - inc >= 1):
                guard = False
            if j + 1 == k and abs(inc - u) < 1:
                guard = False
        rho = Decimal(X[k - 1] - X[k - 2]) / (Decimal(P * X[k - 2]) / 100)
        return rho, viol, guard
    D, T, tau = q["D"], q["T"], q["tau"]
    x = [D * p - T for p in rows]
    implied = [(D * p).quantize(Decimal(1), rounding=ROUND_HALF_UP) for p in rows]
    viol = any(abs(i - T) > tau for i in implied)
    guard = all(abs(v) <= tau - 1 for w, v in enumerate(x, 1) if w != k) and abs(x[k - 1]) != tau
    return abs(x[k - 1]) / tau, viol, (guard, abs(x[k - 1]))


def verify(root, report=None):
    R = {"failures": [], "agree": {}}
    fail = R["failures"].append
    with open(os.path.join(root, "manifest.json"), "rb") as f:
        man = json.loads(f.read())
    for kind in ("main", "pilot"):
        with open(os.path.join(root, kind, "registry.json"), "rb") as f:
            reg = json.loads(f.read())
        rv = reg["register_values"]
        N = rv["N"]
        dec = lambda z: Decimal(F(z).numerator) / Decimal(F(z).denominator)
        win = {k: (dec(v[0]), dec(v[1])) for k, v in rv["windows"].items()}
        texts, per = {}, {}
        for t in reg["templates"]:
            for c in "ABCD":
                with open(os.path.join(root, kind, "prompts", f"{t['id']}-{c}.txt"), "rb") as f:
                    b = f.read()
                try:
                    texts[(t["id"], c)] = b.decode("ascii")
                except UnicodeDecodeError:
                    fail(f"{kind} {t['id']}-{c}: PG5 non-ASCII")
        n_ok = 0
        for t in reg["templates"]:
            fam, k, tid = t["family"], t["target_week"], t["id"]
            pr = {c: texts.get((tid, c)) for c in "ABCD"}
            if None in pr.values():
                continue
            st = {}
            for c, p in pr.items():
                # PG5
                if p.startswith("﻿") or p.endswith("\n") or any(ch in FORBIDDEN for ch in p) or any(l.endswith(" ") for l in p.split("\n")):
                    fail(f"{kind} {tid}-{c}: PG5")
                # PG10 (PT1-PT10)
                norm = unicodedata.normalize("NFKC", p).casefold()
                for pid, rx, cs in PT:
                    if re.search(rx, p if cs else norm):
                        fail(f"{kind} {tid}-{c}: PG10 {pid}")
                if re.search(PT10, p):
                    fail(f"{kind} {tid}-{c}: PG10 PT10")
                # PG12 whitelist on the plan block, fullmatch with no flags
                for ln in p.split("\n"):
                    row_rx = (r"\| [1-9][0-9]? \| (?:0|[1-9][0-9]{0,2})\.[0-9] \|" if fam == "MIL" else r"\| [1-9][0-9]? \| [1-9]:[0-5][0-9] \|")
                    if ln.startswith("| ") and not re.fullmatch(row_rx, ln)                             and ln not in ("| Week | Total km |", "| --- | --- |", "| Week | Target race pace (min/km) |"):
                        fail(f"{kind} {tid}-{c}: PG12 line {ln!r}")
                try:
                    q = parse_prompt(p, fam, N)
                except Ambiguity as e:
                    fail(f"{kind} {tid}-{c}: strict parse {e}")
                    continue
                rho, viol, guard = rho_and_state(fam, q, k)
                valid = c in "AC"
                if viol == valid:
                    fail(f"{kind} {tid}-{c}: ADJ-1 state recomputed {'violated' if viol else 'satisfied'}, expected {'satisfied' if valid else 'violated'}")
                w = win["V"] if valid else win[t["class"]]
                if not (w[0] <= rho <= w[1]):
                    fail(f"{kind} {tid}-{c}: A6b rho {rho} outside its window")
                g_ok = guard if fam == "MIL" else guard[0]
                if not g_ok:
                    fail(f"{kind} {tid}-{c}: guard (GM/GP) slack below one unit")
                if fam == "PAC":
                    xk = guard[1]
                    if (valid and xk > q["tau"] - 1) or (not valid and xk < q["tau"] + 1):
                        fail(f"{kind} {tid}-{c}: target finish-time guard")
                if q["rows"] is not None:
                    st[c] = q
            # PG1-PG3 on the pair
            for a, b in (("A", "B"), ("C", "D")):
                ta, tb = list(NUM.finditer(pr[a])), list(NUM.finditer(pr[b]))
                d = [i for i, (x, y) in enumerate(zip(ta, tb)) if x.group() != y.group()]
                if len(ta) != len(tb) or len(d) != 1:
                    fail(f"{kind} {tid} {a}/{b}: PG1"); continue
                x, y = ta[d[0]], tb[d[0]]
                if len(x.group()) != len(y.group()) or (fam == "MIL" and len(x.group().split(".")[0]) != len(y.group().split(".")[0])) or (fam == "PAC" and x.group().split(":")[0] != y.group().split(":")[0]):
                    fail(f"{kind} {tid} {a}/{b}: PG2")
                diff = [j for j, (u, v) in enumerate(zip(pr[a], pr[b])) if u != v]
                if len(pr[a]) != len(pr[b]) or not (x.start() <= diff[0] and diff[-1] < x.end()):
                    fail(f"{kind} {tid} {a}/{b}: PG3")
            # PG6: rule text identical across all four prompts and starts the prompt
            rule = [p.split("\n\n")[0] for p in pr.values()]
            if len(set(rule)) != 1:
                fail(f"{kind} {tid}: PG6 rule text differs between members")
            # PG7 / PG8 / PG9 (claim = the unique inserted string, identical everywhere)
            for c, base in (("C", "A"), ("D", "B")):
                x, y = pr[c], pr[base]
                i = 0
                while i < len(y) and x[i] == y[i]:
                    i += 1
                ins = x[i:i + len(x) - len(y)]
                if x[:i] + x[i + len(ins):] != y or not ins:
                    fail(f"{kind} {tid}: PG7/PG8 {c} minus claim is not {base}")
                per.setdefault(fam, set()).add(ins)
            n_ok += 1
        for fam, s in per.items():
            if len(s) != 1:
                fail(f"{kind} {fam}: PG9 several claim strings")
        # A1b
        vals = list(texts.values())
        if len(vals) != 4 * len(reg["templates"]) or len(set(vals)) != len(vals):
            fail(f"{kind}: A1b")
        # A7 cluster integrity from template metadata
        K = N - 1
        cells = {}
        for t in reg["templates"]:
            cells.setdefault((t["family"], t["base_index"], t["position_index"], t["direction"]), []).append(t["class"])
        for fam, L, dirs in (("MIL", len(rv["S_list"]), (None,)), ("PAC", len(rv["G_list"]), (0, 1))):
            for b in range(L):
                for i in range(K):
                    for s in dirs:
                        if len(cells.get((fam, b, i, s), [])) != 1:
                            fail(f"{kind}: A7 slot {(fam, b, i, s)} not exactly once")
            for s in dirs:
                for b in range(L):
                    n_mild = sum(cells.get((fam, b, i, s), [""])[0] == "MILD" for i in range(K))
                    if n_mild != K // 2:
                        fail(f"{kind}: A7 base {b} {fam} dir {s} MILD in {n_mild} of {K} positions")
                for i in range(K):
                    n_mild = sum(cells.get((fam, b, i, s), [""])[0] == "MILD" for b in range(L))
                    if n_mild != L // 2:
                        fail(f"{kind}: A7 slot position {i} {fam} dir {s} has {n_mild} MILD of {L}")
        # PG11a: checker bundle
        bdir = os.path.join(root, kind, "checker_bundle")
        names = sorted(os.listdir(bdir))
        if len(names) != 2 * len(reg["templates"]):
            fail(f"{kind}: PG11a file count {len(names)}")
        for nme in names:
            if not re.fullmatch(r"[0-9a-f]{16}", nme):
                fail(f"{kind}: PG11a name {nme}")
            with open(os.path.join(bdir, nme), "rb") as f:
                body = f.read()
            if sha(body)[:16] != nme:
                fail(f"{kind}: PG11a opaque ID is not the first 16 hex of SHA-256 of the text ({nme})")
            s = body.decode("ascii")
            nrm = unicodedata.normalize("NFKC", s).casefold()
            for pid, rx, cs in PT:
                if pid in BUNDLE_SCAN and re.search(rx, s if cs else nrm):
                    fail(f"{kind}: PG11a pattern {pid} in {nme}")
            if "reviewer" in s.lower():
                fail(f"{kind}: PG11a claim text present in {nme}")
        R["agree"][kind] = {"templates": len(reg["templates"]), "checked_templates": n_ok}
    R["summary"] = {"failures": len(R["failures"]), "main_registry_sha256": man["summary"]["main"]["registry_sha256"]}
    return R


if __name__ == "__main__":
    rep = verify(sys.argv[1])
    for x in rep["failures"][:40]:
        print("FAIL:", x)
    print(json.dumps({"agree": rep["agree"], "summary": rep["summary"]}, sort_keys=True))
    if len(sys.argv) > 2:
        with open(sys.argv[2], "x", encoding="utf-8") as f:
            f.write(json.dumps(rep, indent=1, sort_keys=True) + "\n")
    print("IMPLEMENTATION B: AGREE" if not rep["failures"] else f"IMPLEMENTATION B: {len(rep['failures'])} FAILURE(S)")
    sys.exit(1 if rep["failures"] else 0)
