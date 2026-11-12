"""Step 7 core: numeral renderers, rule/plan/prompt composition, claim insertion/removal,
forbidden-pattern scan (PT1..PT10), anti-cue check, and the PG12 plan-block whitelist.

DRAFT for owner review (specification v2.7 sections 3.1, 8.1, 8.3, 14). Exact arithmetic only (Fraction).
This module renders and checks strings. It reads no registry, calls no model and writes no file.
"""
import re
import unicodedata
from fractions import Fraction

import prompt_specification_v1 as SPEC

# ----------------------------------------------------------------------------- canonical renderers (3.1, 8.3)


def render_int(n):
    """P and tau_F: decimal integer, no sign, no leading zero, no padding (PG6)."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise ValueError("render_int needs a non-negative int")
    return str(n)


def render_mil(X):
    """Weekly total in tenths of km -> '%.1f' by integer division (0 <= X <= 9999)."""
    if not isinstance(X, int) or isinstance(X, bool) or not 0 <= X <= 9999:
        raise ValueError("MIL numeral out of range")
    return f"{X // 10}.{X % 10}"


def render_pace(p):
    """Pace in s/km -> m:ss (60 <= p <= 599)."""
    if not isinstance(p, int) or isinstance(p, bool) or not 60 <= p <= 599:
        raise ValueError("pace out of range")
    return f"{p // 60}:{p % 60:02d}"


def render_time(T):
    """Target finish time in seconds -> H:MM:SS (T < 36000)."""
    if not isinstance(T, int) or isinstance(T, bool) or not 0 <= T < 36000:
        raise ValueError("time out of range")
    return f"{T // 3600}:{(T % 3600) // 60:02d}:{T % 60:02d}"


def n_dec(D):
    """Smallest n >= 0 with D * 10**n an integer; D must be a positive Fraction with a finite decimal expansion."""
    if isinstance(D, bool) or not isinstance(D, (int, Fraction)):
        raise ValueError("D_dist must be an int or a Fraction (no floating point, section 3.1)")
    D = Fraction(D)
    if D <= 0:
        raise ValueError("D_dist must be positive")
    for n in range(0, 40):
        if (D * 10 ** n).denominator == 1:
            return n
    raise ValueError("D_dist has no finite decimal expansion within 40 digits")


def render_dist(D):
    """Canonical D_dist (8.3 item 4): integer part, and when n_dec > 0 a point plus exactly n_dec digits."""
    nd = n_dec(D)                       # validates the type and the sign
    D = Fraction(D)
    if nd == 0:
        return str(int(D))
    scaled = int(D * 10 ** nd)
    ip, fp = divmod(scaled, 10 ** nd)
    return f"{ip}.{fp:0{nd}d}"


# ----------------------------------------------------------------------------- rule text and slots (PG6)

_SLOT_TOKENS = {"P": SPEC.SLOT_P, "D": SPEC.SLOT_D, "TAU": SPEC.SLOT_TAU}


def render_rule(family, *, P=None, D=None, TAU=None):
    tpl = SPEC.FAMILIES[family]["rule_template"]
    vals = {"P": None if P is None else render_int(P), "D": None if D is None else render_dist(D),
            "TAU": None if TAU is None else render_int(TAU)}
    for slot in SPEC.FAMILIES[family]["slots"]:
        if vals[slot] is None:
            raise ValueError(f"missing slot {slot}")
        tpl = tpl.replace(_SLOT_TOKENS[slot], vals[slot])
    for tok in _SLOT_TOKENS.values():
        if tok in tpl:
            raise ValueError("unfilled slot marker")
    return tpl


def extract_rule_slots(family, rule_text):
    """Slot extraction by the frozen slot markers (what Implementation B does at PG6): returns {slot: [strings]}.
    Every occurrence of a slot must carry the same string; the literal text between the slots must match exactly."""
    tpl = SPEC.FAMILIES[family]["rule_template"]
    pieces, order, pos = [], [], 0
    pat = re.compile("|".join(re.escape(t) for t in _SLOT_TOKENS.values()))
    for m in pat.finditer(tpl):
        pieces.append(tpl[pos:m.start()])
        order.append(next(k for k, v in _SLOT_TOKENS.items() if v == m.group(0)))
        pos = m.end()
    pieces.append(tpl[pos:])
    rx = "".join(re.escape(pc) + (r"([^ ]+)" if i < len(order) else "") for i, pc in enumerate(pieces))
    m = re.fullmatch(rx, rule_text)
    if m is None:
        return None
    out = {}
    for slot, val in zip(order, m.groups()):
        out.setdefault(slot, []).append(val)
    return out


# ----------------------------------------------------------------------------- plan block and prompt


def render_plan_block(family, values, *, D=None, T_time=None):
    """Lines of the plan block (no trailing LF). MIL: header, separator, N rows. PAC: distance line, target-time
    line, header, separator, N rows."""
    fam = SPEC.FAMILIES[family]
    rows = []
    for w, v in enumerate(values, start=1):
        rows.append(f"| {w} | {render_mil(v) if family == 'MIL' else render_pace(v)} |")
    if family == "MIL":
        return [fam["plan_header"], fam["plan_separator"]] + rows
    if D is None or T_time is None:
        raise ValueError("PAC needs D and T_time")
    return [f"{fam['distance_label']}: {render_dist(D)} km", f"{fam['time_label']}: {render_time(T_time)}",
            fam["plan_header"], fam["plan_separator"]] + rows


def build_prompt(family, values, *, P=None, D=None, TAU=None, T_time=None, claim=False):
    """The full model prompt: rule, plan block, optional claim block, task text, response format."""
    segs = [render_rule(family, P=P, D=D, TAU=TAU), "\n".join(render_plan_block(family, values, D=D, T_time=T_time))]
    if claim:
        segs.append(SPEC.CLAIM_BLOCK)
    segs += [SPEC.TASK_TEXT, SPEC.RESPONSE_FORMAT]
    return SPEC.JOINER.join(segs)


def remove_claim(prompt):
    """Frozen claim-removal procedure (PG7/PG8): delete JOINER + CLAIM_BLOCK exactly once."""
    marker = SPEC.JOINER + SPEC.CLAIM_BLOCK
    if prompt.count(marker) != 1:
        raise ValueError("claim block not present exactly once")
    return prompt.replace(marker, "")


def plan_block_lines(prompt, family):
    """Locate the plan block by the frozen block markers: it starts at the first line with the family's start marker
    and ends before the first empty line. Returns the list of lines, or None if no start marker is found."""
    fam = SPEC.FAMILIES[family]
    lines = prompt.split("\n")                      # LF only (8.3 item 2)
    start = None
    for i, ln in enumerate(lines):
        if (family == "MIL" and ln == fam["block_start_marker"]) or (family == "PAC" and ln.startswith(fam["block_start_marker"])):
            start = i
            break
    if start is None:
        return None
    end = start
    while end < len(lines) and lines[end] != SPEC.BLOCK_END_MARKER:
        end += 1
    return lines[start:end]


# ----------------------------------------------------------------------------- PG5 character checks

_FORBIDDEN_CHARS = {chr(c) for c in (9, 11, 12, 13, 28, 29, 30, 0x85, 0xA0, 0x200B, 0x200C, 0x200D, 0x2028, 0x2029, 0x2060, 0xFEFF)}


def pg5_problems(prompt):
    out = []
    if prompt.startswith(chr(0xFEFF)):
        out.append("BOM")
    if not prompt.isascii():
        out.append("non-ASCII")
    if prompt.endswith("\n"):
        out.append("trailing newline")
    bad = sorted({hex(ord(c)) for c in prompt if c in _FORBIDDEN_CHARS})
    if bad:
        out.append("forbidden characters " + ",".join(bad))
    if any(ln.endswith(" ") for ln in prompt.split("\n")):
        out.append("trailing space")
    return out


# ----------------------------------------------------------------------------- PT scan (8.1)

_PT_COMPILED = [(i, re.compile(rx), cs) for i, rx, cs in SPEC.PT_PATTERNS]
_PT10 = re.compile(SPEC.PT10_PATTERN)


def pt_scan(prompt):
    """Run PT1..PT10 in fixed order; return a list of (pattern id, offset). Offsets refer to the text the pattern ran on
    (the NFKC-casefolded text for case-insensitive patterns, the original text for PT4 and PT10)."""
    norm = unicodedata.normalize("NFKC", prompt).casefold()
    hits = []
    for pid, rx, cs in _PT_COMPILED:
        for m in rx.finditer(prompt if cs else norm):
            hits.append((pid, m.start()))
    for m in _PT10.finditer(prompt):
        hits.append(("PT10", m.start()))
    return hits


def anti_cue_hits(text):
    words = re.findall(r"[a-z]+", text.casefold())
    return sorted({w for w in words if w in set(SPEC.ANTI_CUE_WORDS)})


# ----------------------------------------------------------------------------- PG12 whitelist (8.3)

NUM_M = r"(?:0|[1-9][0-9]{0,2})\.[0-9]"
MIL_ROW_RX = re.compile(r"\| ([1-9][0-9]?) \| (" + NUM_M + r") \|")
PAC_ROW_RX = re.compile(r"\| ([1-9][0-9]?) \| ([1-9]:[0-5][0-9]) \|")
PAC_TIME_RX = re.compile(re.escape(SPEC.FAMILIES["PAC"]["time_label"]) + r": [0-9]:[0-5][0-9]:[0-5][0-9]")


def pac_dist_rx(D):
    return re.compile(re.escape(SPEC.FAMILIES["PAC"]["distance_label"] + ": " + render_dist(D) + " km"))


def pg12_row_ok(family, line):
    """Pattern-level test of one table row with fullmatch and no flags."""
    rx = MIL_ROW_RX if family == "MIL" else PAC_ROW_RX
    return rx.fullmatch(line) is not None


def pg12_block_problems(family, block_lines, *, values, D=None, T_time=None):
    """Full PG12 test of a plan block against the registry values: patterns, canonical-value equality, structure.
    No normalization or repair is applied. Returns a list of 'FAIL(...)' strings (empty = pass)."""
    fam = SPEC.FAMILIES[family]
    N = len(values)
    exp = render_plan_block(family, values, D=D, T_time=T_time)
    if block_lines is None:                          # no start marker found by plan_block_lines (8.3 item 8: FAIL, no repair)
        return ["FAIL(PG12 structure: plan block not found)"]
    probs = []
    if len(block_lines) != len(exp):
        probs.append(f"FAIL(PG12 structure: {len(block_lines)} lines, expected {len(exp)})")
    head = 2 if family == "MIL" else 4
    for i, ln in enumerate(block_lines):
        if i >= len(exp):
            probs.append(f"FAIL(PG12 line {i + 1}: extra line)")
            continue
        if i < head:
            if family == "PAC" and i == 0:
                ok = pac_dist_rx(D).fullmatch(ln) is not None
            elif family == "PAC" and i == 1:
                ok = PAC_TIME_RX.fullmatch(ln) is not None
            else:
                ok = ln == exp[i]                    # header and separator must equal the frozen strings
        else:
            ok = pg12_row_ok(family, ln)
        if not ok:
            probs.append(f"FAIL(PG12 line {i + 1}: pattern; expected entry {exp[i]!r})")
        elif ln != exp[i]:
            probs.append(f"FAIL(PG12 line {i + 1}: value; expected {exp[i]!r})")
    return probs
