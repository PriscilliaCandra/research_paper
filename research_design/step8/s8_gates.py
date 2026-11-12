"""Step 8 rendering and rendering-stage gates (specification v2.7 sections 8.1 to 8.3, 9.6, 21.1 row 8) -- decision record 12.28.

Applies PG1-PG10, PG11a, the PG11b builder test, PG12, A1b and A6b to ALL rendered prompts of a registry. Rendering uses the
Step 7 frozen modules (s7_core, prompt_specification_v1); the gate logic below is new code. Returns problems, never repairs.
Gate semantics where the text is ambiguous are the choices of decision record 12.28 (PG4, PG11a bundle, PG11b input, A6b actor).
"""
import hashlib
import os
import re
import sys
from fractions import Fraction as F

STEP7 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "step7")
sys.path.insert(0, STEP7)
sys.dont_write_bytecode = True
import prompt_specification_v1 as SPEC  # noqa: E402
import s7_bundle as B  # noqa: E402
import s7_core as C  # noqa: E402
import parser_battery_v1 as PB  # noqa: E402

NUM_RX = re.compile(r"[0-9]+(?:[.:][0-9]+)*")
STRICT_NONCONSECUTIVE = []   # PG3 strict-reading count (informational, deterministic order); cleared by run_gates
CONDS = {"A": ("valid", False), "B": ("violating", False), "C": ("valid", True), "D": ("violating", True)}


def sha256_text(t):
    return hashlib.sha256(t.encode("ascii")).hexdigest()


def member_values(t, which):
    m = t[which]
    return m["weekly_tenths_km"] if t["family"] == "MIL" else m["tokens_s_per_km"]


def render_kwargs(t, p):
    if t["family"] == "MIL":
        return {"P": p["P"]}
    m = t["valid"]
    return {"D": F(m["D_dist"]["num"], m["D_dist"]["den"]), "TAU": m["tau_F_s"], "T_time": m["T_time_s"]}


def render_template(t, p):
    kw = render_kwargs(t, p)
    return {c: C.build_prompt(t["family"], member_values(t, which), claim=claim, **kw) for c, (which, claim) in CONDS.items()}


def render_all(templates, p):
    return {t["id"]: render_template(t, p) for t in templates}


def _plan_start_index(prompt, family):
    block = C.plan_block_lines(prompt, family)
    if block is None:
        return None, None
    return prompt.index(block[0]), block


def _targets(t, which):
    v = member_values(t, which)
    k = t["target_week"]
    return v[k - 1]


def _tok(t, which):
    v = _targets(t, which)
    return C.render_mil(v) if t["family"] == "MIL" else C.render_pace(v)


def pair_problems(t, pa, pb, tid, tag):
    """PG1, PG2, PG3 for one matched pair (pa = valid member prompt, pb = violating member prompt)."""
    out = {"PG1": [], "PG2": [], "PG3": []}
    ta, tb = list(NUM_RX.finditer(pa)), list(NUM_RX.finditer(pb))
    if len(ta) != len(tb):
        out["PG1"].append(f"{tid} {tag}: numeral count differs {len(ta)} vs {len(tb)}")
        return out
    diff = [i for i, (x, y) in enumerate(zip(ta, tb)) if x.group(0) != y.group(0)]
    if len(diff) != 1:
        out["PG1"].append(f"{tid} {tag}: {len(diff)} numeric tokens differ, expected 1")
        return out
    i = diff[0]
    x, y = ta[i], tb[i]
    if x.group(0) != _tok(t, "valid") or y.group(0) != _tok(t, "violating"):
        out["PG1"].append(f"{tid} {tag}: the differing token is not the target token of week {t['target_week']}")
    if len(x.group(0)) != len(y.group(0)):
        out["PG2"].append(f"{tid} {tag}: token length differs {x.group(0)!r} vs {y.group(0)!r}")
    if t["family"] == "MIL":
        if len(x.group(0).split(".")[0]) != len(y.group(0).split(".")[0]):
            out["PG2"].append(f"{tid} {tag}: integer digit count differs {x.group(0)!r} vs {y.group(0)!r}")
    elif x.group(0).split(":")[0] != y.group(0).split(":")[0]:
        out["PG2"].append(f"{tid} {tag}: minute differs {x.group(0)!r} vs {y.group(0)!r}")
    if len(pa) != len(pb):
        out["PG3"].append(f"{tid} {tag}: prompt length differs")
    else:
        # Decision 12.28 (PG3 reading): "contiguous" = one changed span; every differing position lies inside the target token's
        # span (section 8.2: "a single changed span"). A shared character inside the token (the decimal point of 21.5 / 22.4)
        # does not break the span. The strict reading (consecutive positions) is counted in the gate report, not enforced.
        pos = [j for j, (c1, c2) in enumerate(zip(pa, pb)) if c1 != c2]
        if not pos or not (x.start() <= pos[0] and pos[-1] < x.end()):
            out["PG3"].append(f"{tid} {tag}: differing positions outside the target token span")
        elif pos[-1] - pos[0] + 1 != len(pos):
            STRICT_NONCONSECUTIVE.append(f"{tid} {tag}")
    return out


def checker_projection(prompt, family):
    """PG11a material (decision 12.28): the rendered rule and the rendered plan block, without claim, task text or format
    (specification 11 layer 3)."""
    start, block = _plan_start_index(prompt, family)
    if block is None:
        raise ValueError("plan block not found")
    return prompt[:start] + "\n".join(block)


def run_gates(templates, prompts, p, registry_hash, windows):
    """All rendering-stage gates on all prompts. Returns (problems dict gate -> list, checker_bundle files, mapping)."""
    del STRICT_NONCONSECUTIVE[:]
    G = {g: [] for g in ("PG1", "PG2", "PG3", "PG4", "PG5", "PG6", "PG7", "PG8", "PG9", "PG10", "PG11a", "PG11b", "PG12", "A1b", "A6b")}
    claim_hashes = {"MIL": set(), "PAC": set()}
    for t in templates:
        tid, fam, pr = t["id"], t["family"], prompts[t["id"]]
        kw = render_kwargs(t, p)
        for tag, (a, b) in (("A/B", ("A", "B")), ("C/D", ("C", "D"))):
            for g, probs in pair_problems(t, pr[a], pr[b], tid, tag).items():
                G[g] += probs
        # PG4 (decision 12.28): the prompt is exactly the frozen segments around the registry plan block; the frozen texts carry no numeral
        for c, (which, claim) in CONDS.items():
            segs = [C.render_rule(fam, **{k: v for k, v in kw.items() if k in ("P", "D", "TAU")}),
                    "\n".join(C.render_plan_block(fam, member_values(t, which), D=kw.get("D"), T_time=kw.get("T_time")))]
            if claim:
                segs.append(SPEC.CLAIM_BLOCK)
            segs += [SPEC.TASK_TEXT, SPEC.RESPONSE_FORMAT]
            if pr[c] != SPEC.JOINER.join(segs):
                G["PG4"].append(f"{tid} {c}: prompt is not the frozen segment structure")
        for txt in (SPEC.CLAIM_BLOCK, SPEC.TASK_TEXT, SPEC.RESPONSE_FORMAT):
            if re.search(r"[0-9]", txt):
                G["PG4"].append("a frozen text contains a numeral")
        for c, prompt in pr.items():
            G["PG5"] += [f"{tid} {c}: {x}" for x in C.pg5_problems(prompt)]
            G["PG10"] += [f"{tid} {c}: {h}" for h in C.pt_scan(prompt)]
            start, block = _plan_start_index(prompt, fam)
            which, claim = CONDS[c]
            G["PG12"] += [f"{tid} {c}: {x}" for x in C.pg12_block_problems(fam, block, values=member_values(t, which), D=kw.get("D"), T_time=kw.get("T_time"))]
            # PG6: rule slots by markers, canonical strings, byte-identical rule across members
            rule = prompt[:start - len(SPEC.JOINER)] if start else ""
            slots = C.extract_rule_slots(fam, rule)
            want = {"P": C.render_int(p["P"])} if fam == "MIL" else {"D": C.render_dist(kw["D"]), "TAU": C.render_int(kw["TAU"])}
            if slots is None or any(set(v) != {want[k]} for k, v in slots.items()) or set(slots) != set(want):
                G["PG6"].append(f"{tid} {c}: rule slots not canonical")
            if rule != prompts[tid]["A"][:len(rule)]:
                G["PG6"].append(f"{tid} {c}: rule text differs from Cond-A")
        # PG7 / PG8 / PG9
        for g, (cl, base) in (("PG7", ("C", "A")), ("PG8", ("D", "B"))):
            try:
                if C.remove_claim(pr[cl]) != pr[base]:
                    G[g].append(f"{tid}: {cl} minus claim is not byte-identical to {base}")
            except ValueError as e:
                G[g].append(f"{tid}: {e}")
        for cl in ("C", "D"):
            if pr[cl].count(SPEC.CLAIM_BLOCK) != 1:
                G["PG9"].append(f"{tid} {cl}: claim block not present exactly once")
            claim_hashes[fam].add(sha256_text(SPEC.CLAIM_BLOCK))
    for fam, hs in claim_hashes.items():
        if len(hs) > 1:
            G["PG9"].append(f"{fam}: more than one claim block hash")
    if C.anti_cue_hits(SPEC.TASK_TEXT):
        G["PG10"].append("anti-cue word in the task text")
    for ctl in SPEC.PT_POSITIVE_CONTROLS:                      # controls: positives must hit, negatives must not
        if not C.pt_scan(ctl):
            G["PG10"].append(f"positive control not detected: {ctl!r}")
    for ctl in SPEC.PT_NEGATIVE_CONTROLS:
        if C.pt_scan(ctl):
            G["PG10"].append(f"negative control rejected: {ctl!r}")
    # A1b
    allp = [pr[c] for pr in prompts.values() for c in "ABCD"]
    if len(allp) != 4 * len(templates) or len(set(allp)) != len(allp):
        G["A1b"].append(f"expected {4 * len(templates)} pairwise distinct prompts, found {len(set(allp))} distinct of {len(allp)}")
    # A6b: rho recomputed from the RENDERED numerals with exact fractions
    for t in templates:
        for c in ("A", "B"):
            which = "valid" if c == "A" else "violating"
            win = windows["V"] if c == "A" else windows[t["class"]]
            G["A6b"] += a6b_problems(t, prompts[t["id"]][c], which, win)
    # PG11a: checker bundle from the projections of the Cond-A and Cond-B prompts (624 for the main registry)
    proj = {}
    for t in templates:
        for c in ("A", "B"):
            proj[f"{t['id']}-{c}"] = checker_projection(prompts[t["id"]][c], t["family"])
    files, mapping = {}, {}
    try:
        files, mapping = B.build_checker_bundle(proj)
        if len(files) != 2 * len(templates):
            G["PG11a"].append(f"expected {2 * len(templates)} checker files, found {len(files)}")
    except B.BuildRejected as e:
        G["PG11a"].append(str(e)[:300])
    # PG11b builder test: synthetic battery of 13.5 (no pilot or main response), registry hash of this registry
    G["PG11b"] += pg11b_builder_test(templates, prompts, registry_hash)
    return G, files, mapping


def a6b_problems(t, prompt, which, window):
    out = []
    fam = t["family"]
    start, block = _plan_start_index(prompt, fam)
    if block is None:
        return [f"{t['id']}: no plan block"]
    rows = [ln for ln in block if re.fullmatch(r"\| [0-9]+ \| [0-9.:]+ \|", ln)]
    k = t["target_week"]
    if fam == "MIL":
        X = [int(r.split("|")[2].strip().replace(".", "")) for r in rows]
        rule = prompt[:start - 2]
        P = int(C.extract_rule_slots("MIL", rule)["P"][0])
        u = F(P * X[k - 2], 100)
        rho = F(X[k - 1] - X[k - 2]) / u
    else:
        pace = [int(r.split("|")[2].strip().split(":")[0]) * 60 + int(r.split("|")[2].strip().split(":")[1]) for r in rows]
        dline = next(x for x in block if x.startswith(SPEC.FAMILIES["PAC"]["distance_label"]))
        D = F(dline.split(": ", 1)[1].rsplit(" km", 1)[0])
        tline = next(x for x in block if x.startswith(SPEC.FAMILIES["PAC"]["time_label"])).split(": ", 1)[1]
        h, m, s = (int(z) for z in tline.split(":"))
        T = h * 3600 + m * 60 + s
        tau = int(C.extract_rule_slots("PAC", prompt[:start - 2])["TAU"][0])
        rho = abs(D * pace[k - 1] - T) / tau
    if not (window[0] <= rho <= window[1]):
        out.append(f"{t['id']} {which}: rho {rho} outside {window}")
    return out


def pg11b_builder_test(templates, prompts, registry_hash):
    """Build a coder bundle from the synthetic batteries of 13.5 and the registry call keys; it must pass the PG11a checks."""
    texts = [x[0] for x in PB.PARSER_BATTERY] + [x[0] for x in PB.COMMENTS_BATTERY]
    items, n = {}, 0
    for t in templates:
        for c in "ABCD":
            if n >= len(texts):
                break
            items[f"{t['id']},{c},1"] = (prompts[t["id"]][c], texts[n])
            n += 1
    try:
        files, mapping = B.build_coder_bundle(items, registry_hash)
    except B.BuildRejected as e:
        return [str(e)[:300]]
    probs = B.check_bundle(files, mapping)
    if len(files) != len(items):
        probs.append("coder bundle size differs from the number of items")
    return probs
