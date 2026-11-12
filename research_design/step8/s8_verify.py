"""Step 8 independent verifier (decision record 12.28). Standard library only: it imports NO project module (not the generator, not
the gates, not Step 7, not Implementation A). It reads a published build folder and re-derives what it can from the stored bytes:
inventory and hashes, canonical re-serialization, ID and class rules, prompt hashes and character rules, distinctness (A1b),
single-token pairing (PG1-PG3), claim removal (PG7-PG9), the pilot selection (13.3), the call list, and the ground-truth state and
rho class window of every member recomputed with decimal arithmetic from the rendered text (A6b). It is NOT Implementation B
(Step 9): it does not re-verify PG4-PG6, PG10-PG12, and it does not replace the human blind check.

Usage: python -B -I s8_verify.py <build folder>      Exit code 0 = no problem found; 1 = problems printed.
"""
import hashlib
import json
import os
import re
import sys
from decimal import Decimal, ROUND_HALF_UP, getcontext
from fractions import Fraction as F

getcontext().prec = 50
NUM = re.compile(r"[0-9]+(?:[.:][0-9]+)*")
RD = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def sha(b):
    return hashlib.sha256(b).hexdigest()


def cj(o):
    return (json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def read(root, rel):
    with open(os.path.join(root, *rel.split("/")), "rb") as f:
        return f.read()


def cls_rule(b, i, s, K):
    a = (b // 2 + s * (K // 4) + (b % 2) * (K // 2)) % K
    return "MILD" if (i - a) % K < K // 2 else "CLEAR"


def rows_of(prompt):
    return [ln for ln in prompt.split("\n") if re.fullmatch(r"\| [0-9]+ \| [0-9.:]+ \|", ln)]


def verify(root):
    P = []
    man = json.loads(read(root, "manifest.json"))
    inv = man["inventory"]
    paths = [e["path"] for e in inv]
    if paths != sorted(paths) or len(set(paths)) != len(paths):
        P.append("inventory not sorted or has duplicates")
    on_disk = set()
    for dp, _, fs in os.walk(root):
        for f in fs:
            on_disk.add(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/"))
    expected = set(paths) | {"manifest.json", "build_log.json"}
    if on_disk != expected:
        P.append(f"inventory mismatch: missing {sorted(expected - on_disk)[:3]}, extra {sorted(on_disk - expected)[:3]}")
    for e in inv:
        try:
            d = read(root, e["path"])
        except OSError:
            continue
        if sha(d) != e["sha256"] or len(d) != e["bytes"]:
            P.append(f"hash or size mismatch: {e['path']}")
    if man["manifest_kind"] != "Step 8 registry generation manifest" or not man.get("carries_no_self_hash"):
        P.append("manifest header")
    with open(os.path.join(RD, "PARAMETER_FREEZE_MANIFEST_FZ_v1.json"), "rb") as f:
        fz = json.loads(f.read())["register"]
    R = fz["row12_R"]
    claims = {}
    for kind in ("main", "pilot"):
        reg_b = read(root, f"{kind}/registry.json")
        reg = json.loads(reg_b)
        if cj(reg) != reg_b:
            P.append(f"{kind}: registry.json is not canonical JSON")
        if sha(reg_b) != man["summary"][kind]["registry_sha256"]:
            P.append(f"{kind}: registry hash differs from the manifest")
        rv, T = reg["register_values"], reg["templates"]
        K = rv["K"]
        nm, npc = K * len(rv["S_list"]), 2 * K * len(rv["G_list"])
        if (len(T), reg["counts"]["templates"]) != (nm + npc, nm + npc):
            P.append(f"{kind}: template count")
        ids_exp = []
        for fam, n in (("MIL", nm), ("PAC", npc)):
            w = max(3, len(str(n)))
            ids_exp += [f"{fam}-{x:0{w}d}" for x in range(1, n + 1)]
        if [t["id"] for t in T] != ids_exp:
            P.append(f"{kind}: IDs are not the canonical sequence")
        order = [(t["family"] != "MIL", t["position_index"], t["direction"] or 0, t["base_index"]) for t in T]
        if order != sorted(order):
            P.append(f"{kind}: templates not in canonical order")
        win = {k: (F(v[0]), F(v[1])) for k, v in rv["windows"].items()}
        table = json.loads(read(root, f"{kind}/prompt_table.json"))
        if len(table) != 4 * len(T):
            P.append(f"{kind}: prompt table size")
        seen, prompts = set(), {}
        for e in table:
            try:
                txt_b = read(root, f"{kind}/prompts/{e['template_id']}-{e['condition']}.txt")
            except OSError:
                P.append(f"{kind}: prompt file missing {e['template_id']}-{e['condition']}")
                prompts[(e["template_id"], e["condition"])] = None
                continue
            if sha(txt_b) != e["sha256"] or len(txt_b) != e["chars"]:
                P.append(f"{kind}: prompt hash {e['template_id']}-{e['condition']}")
            try:
                txt = txt_b.decode("ascii")
            except UnicodeDecodeError:
                P.append("non-ASCII prompt")
                continue
            if "\r" in txt or "\t" in txt or txt.endswith("\n") or txt.startswith("﻿") or any(l.endswith(" ") for l in txt.split("\n")):
                P.append(f"{kind}: character rule {e['template_id']}-{e['condition']}")
            seen.add(txt)
            prompts[(e["template_id"], e["condition"])] = txt
        if len(seen) != 4 * len(T):
            P.append(f"{kind}: A1b distinct prompts {len(seen)} != {4 * len(T)}")
        for t in T:
            fam, k, tid = t["family"], t["target_week"], t["id"]
            if t["class"] != cls_rule(t["base_index"], t["position_index"], t["direction"] or 0, K):
                P.append(f"{kind} {tid}: class rule")
            pr = {c: prompts.get((tid, c)) for c in "ABCD"}
            if any(v is None for v in pr.values()):
                continue                                         # already reported as missing or non-ASCII
            # PG1-PG3 own implementation
            for a, b in (("A", "B"), ("C", "D")):
                ta, tb = list(NUM.finditer(pr[a])), list(NUM.finditer(pr[b]))
                d = [i for i, (x, y) in enumerate(zip(ta, tb)) if x.group() != y.group()]
                if len(ta) != len(tb) or len(d) != 1 or len(pr[a]) != len(pr[b]) or len(ta[d[0]].group()) != len(tb[d[0]].group()):
                    P.append(f"{kind} {tid} {a}/{b}: pair rule")
                    continue
                pos = [j for j, (x, y) in enumerate(zip(pr[a], pr[b])) if x != y]
                if not (ta[d[0]].start() <= pos[0] and pos[-1] < ta[d[0]].end()):
                    P.append(f"{kind} {tid} {a}/{b}: change outside the token")
            # PG7-PG9: the claim is a single string inserted into the prompt; A (B) is C (D) without it
            for c, base in (("C", "A"), ("D", "B")):
                x, y = pr[c], pr[base]
                i = 0
                while i < len(y) and x[i] == y[i]:
                    i += 1
                ins = x[i:i + len(x) - len(y)]
                if x[:i] + x[i + len(ins):] != y:
                    P.append(f"{kind} {tid} {c}/{base}: not a single insertion")
                claims.setdefault(fam, set()).add(ins)
            # ground truth and class window from the rendered text, decimal arithmetic
            for c in "ABCD":
                rws = rows_of(pr[c])
                valid = c in "AC"
                if fam == "MIL":
                    X = [int(r.split("|")[2].strip().replace(".", "")) for r in rws]
                    Pp = rv["P"]
                    viol = any((X[j] - X[j - 1]) * 100 > Pp * X[j - 1] for j in range(1, len(X)))
                    rho = F(X[k - 1] - X[k - 2], 1) / F(Pp * X[k - 2], 100)
                else:
                    pace = [int(r.split("|")[2].strip().split(":")[0]) * 60 + int(r.split("|")[2].strip().split(":")[1]) for r in rws]
                    dl = [l for l in pr[c].split("\n") if l.endswith(" km")]
                    Dd = Decimal(dl[0].rsplit(": ", 1)[1][:-3])
                    tl = [l for l in pr[c].split("\n") if re.fullmatch(r".*: [0-9]:[0-5][0-9]:[0-5][0-9]", l)]
                    h, m, s = (int(z) for z in tl[0].rsplit(": ", 1)[1].split(":"))
                    Tt = h * 3600 + m * 60 + s
                    tau = rv["tau_F_s"]
                    viol = any(abs((Dd * p).quantize(Decimal(1), rounding=ROUND_HALF_UP) - Tt) > tau for p in pace)
                    rho = F(abs(F(str(Dd)) * pace[k - 1] - Tt)) / tau
                if viol == valid:
                    P.append(f"{kind} {tid} {c}: recomputed state {'violated' if viol else 'satisfied'} contradicts the condition")
                w = win["V"] if valid else win[t["class"]]
                if not (w[0] <= rho <= w[1]):
                    P.append(f"{kind} {tid} {c}: rho {rho} outside its window")
    for fam, s in claims.items():
        if len(s) != 1:
            P.append(f"{fam}: more than one claim string")
    # pilot selection and call list, recomputed from the stored registries
    pil = json.loads(read(root, "pilot/registry.json"))["templates"]
    def first(fam, direction=None, cls=None):
        return next(t["id"] for t in pil if t["family"] == fam and (direction is None or t["direction"] == direction) and (cls is None or t["class"] == cls))
    sel = []
    for c in "AC":
        sel += [(first("MIL"), c)] + [(first("PAC", s), c) for s in (0, 1)]
    for c in "BD":
        for cl in ("MILD", "CLEAR"):
            sel += [(first("MIL", None, cl), c)] + [(first("PAC", s, cl), c) for s in (0, 1)]
    idx = {t["id"]: n for n, t in enumerate(pil)}
    sel.sort(key=lambda x: (idx[x[0]], x[1]))
    if "".join(f"{a},{b},1\n" for a, b in sel).encode() != read(root, "pilot/pilot_calls.txt") or len(sel) != 18:
        P.append("pilot_calls.txt differs from the section 13.3 rule")
    mids = [t["id"] for t in json.loads(read(root, "main/registry.json"))["templates"]]
    cl = "".join(f"{t},{c},{r}\n" for t in mids for c in "ABCD" for r in range(1, R + 1))
    if sha(cl.encode()) != man["summary"]["main"]["call_list_sha256"] or cl.count("\n") != fz["row13_C_max_planned_main_call_slots"]:
        P.append("call list hash or length")
    return P


if __name__ == "__main__":
    probs = verify(sys.argv[1])
    for p in probs[:50]:
        print("PROBLEM:", p)
    print("VERIFIED: no problem found" if not probs else f"{len(probs)} problem(s)")
    sys.exit(1 if probs else 0)
