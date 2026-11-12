"""ANALYSIS ONLY (not part of the harness, never imported by the runner): compares the CURRENT SF flag detection with the candidate rules for conflict C-31
(see results_package_2026-10-13/C31_C32_OWNER_DECISION_BRIEF.md). Pure functions on mock records; no file read, no network, no provider.

Variants for the SF2 flag only (SF1, SF3, SF4, SF5 detection is identical in every variant):
  B   : SF2 is not emitted when the call also meets SF1 or SF4 (this IS the current code in s10_scoring.sf_flags).
  A1  : SF2 whenever the text has no non-whitespace character (spec 13.4 line 810 read literally).
  A2a : SF2 only when a response was received (integer HTTP status 2xx, no error marker) and the text has no non-whitespace character.
  A2b : A2a, except that SF2 is not met when SF4 is met (the empty text is explained by a block or by no candidate).
  A2c : SF2 when the text is empty and the call is neither SF1 nor SF4 (a status-less record without error is allowed). Proven equal to B by grid().
Outcome of a round: one failed call among 17 clean calls, f_S = 1, theta_unp = 1/2, the decision vector of s10_scoring (hard flag first, f_S per call).
Run: python -B -I research_design/step10/sf2_variants_analysis.py  (prints the table as markdown)"""
import os
import sys
from fractions import Fraction as F
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_runner as RN  # noqa: E402
import s10_scoring as SC  # noqa: E402

D = RN.D
WS = chr(32) + chr(9) + chr(10) + chr(11) + chr(12) + chr(13)
VARIANTS = ("B", "A1", "A2a", "A2b", "A2c")


def _clean():
    return {"response_text": "Verdict: satisfied", "error": None, "http_status": 200, "finish_reason": "STOP"}


def rec(**kw):
    r = _clean()
    r.update(kw)
    return r


# (id, short description, record, parser_raises, combination label)
CASES = [
    ("K01", "HTTP 2xx, candidate with finish STOP, body empty", rec(response_text=""), False, "SF2"),
    ("K02", "HTTP 2xx, body whitespace only", rec(response_text=" \t\r\n\x0b\x0c "), False, "SF2"),
    ("K03", "HTTP 2xx, body empty, finish MAX_TOKENS", rec(response_text="", finish_reason="MAX_TOKENS"), False, "SF3+SF2"),
    ("K04", "HTTP 503, body empty", rec(response_text="", error="HTTP 503", http_status=503), False, "SF1+SF2"),
    ("K05", "timeout (no response; status None)", rec(response_text="", error="TimeoutError", http_status=None, finish_reason=None), False, "SF1+SF2"),
    ("K06", "transport exception (URLError; status None)", rec(response_text="", error="URLError", http_status=None, finish_reason=None), False, "SF1+SF2"),
    ("K07", "HTTP 200 but body not JSON (adapter: exception class, status None)", rec(response_text="", error="JSONDecodeError", http_status=None, finish_reason=None), False, "SF1+SF2"),
    ("K08", "valid text with HTTP 500 and error marker", rec(error="HTTP 500", http_status=500), False, "SF1"),
    ("K09", "valid text, HTTP 200, but error marker (inconsistent metadata)", rec(error="TimeoutError"), False, "SF1"),
    ("K10", "valid text, no HTTP status, no error marker", rec(http_status=None), False, "none"),
    ("K11", "finish SAFETY, body empty", rec(response_text="", finish_reason="SAFETY"), False, "SF4+SF2"),
    ("K12", "finish SAFETY, partial text", rec(response_text="Verdict: sat", finish_reason="SAFETY"), False, "SF4"),
    ("K13", "no candidate (HTTP 200, finish None, body empty); identical record for a candidate without text and without finishReason", rec(response_text="", finish_reason=None), False, "SF4+SF2"),
    ("K14", "prompt block reason, no candidate, body empty", rec(response_text="", finish_reason=None, block_reason="SAFETY"), False, "SF4+SF2"),
    ("K15", "error marker + block reason, body empty (HTTP 500)", rec(response_text="", error="HTTP 500", http_status=500, block_reason="SAFETY"), False, "SF1+SF4+SF2"),
    ("K16", "SF1+SF3: HTTP 500 marker, text, MAX_TOKENS", rec(error="HTTP 500", http_status=500, finish_reason="MAX_TOKENS"), False, "SF1+SF3"),
    ("K17", "SF4+SF5: prompt block reason, text, parser raises", rec(block_reason="SAFETY"), True, "SF4+SF5"),
    ("K18", "clean response (control)", rec(), False, "none"),
    ("K19", "no HTTP status, no error marker, body empty (incoherent record)", rec(response_text="", http_status=None), False, "SF2"),
    ("K20", "HTTP 2xx, body only a no-break space U+00A0 (not in the code's whitespace set)", rec(response_text=chr(0xA0)), False, "none (goes to parsing)"),
]


def _status_received(r):
    st = r.get("http_status")
    return isinstance(st, int) and not isinstance(st, bool) and 200 <= st < 300 and not r.get("error")


def flags_variant(r, variant, parse_exc=None):
    """All flags per variant. SF1/SF3/SF4/SF5 exactly as s10_scoring.sf_flags; only the SF2 condition differs."""
    text = r.get("response_text") or ""
    f = []
    if r.get("error") or not SC._status_ok(r):
        f.append("SF1")
    fr = r.get("finish_reason")
    if fr in SC.TOKEN_LIMIT_REASONS:
        f.append("SF3")
    if fr in SC.BLOCK_REASONS or r.get("block_reason") or (not r.get("error") and r.get("http_status") == 200 and fr is None and not text):
        f.append("SF4")
    empty = not text.strip(WS)
    if variant == "B":
        sf2 = empty and not r.get("error") and "SF4" not in f and "SF1" not in f
    elif variant == "A1":
        sf2 = empty
    elif variant == "A2a":
        sf2 = empty and _status_received(r)
    elif variant == "A2b":
        sf2 = empty and _status_received(r) and "SF4" not in f
    elif variant == "A2c":
        sf2 = empty and "SF1" not in f and "SF4" not in f
    else:
        raise ValueError(variant)
    if sf2:
        f.append("SF2")
    if parse_exc is not None:
        f.append("SF5")
    return [x for x in SC.PRIMARY_ORDER if x in f]


def outcome_of_flags(flags):
    counts = SC.decision_counts([flags]) if flags else SC.decision_counts([])
    return D.round_outcome(counts, 18, 1, F(1, 2), planned_calls=18)["outcome"]


def current_code(r, raises):
    """Flags, recording primary and round outcome from the REAL scoring code (not a simulation)."""
    recs = [r] + [_clean() for _ in range(17)]
    if raises:
        with mock.patch.object(SC.s7_parser, "parse_verdict", side_effect=lambda t: (_ for _ in ()).throw(ValueError("x")) if t == r["response_text"] else ("PARSEABLE", "satisfied")):
            cl = SC.classify(r)
            pc = SC.pilot_counts(recs)
    else:
        cl = SC.classify(r)
        pc = SC.pilot_counts(recs)
    out = D.round_outcome(pc["counts"], pc["n_calls"], 1, F(1, 2), planned_calls=18)["outcome"]
    return cl["flags"], cl["primary_sf"], out


def table():
    rows = []
    for cid, desc, r, raises, combo in CASES:
        exc = ValueError("x") if raises else None
        cur_flags, cur_primary, cur_out = current_code(r, raises)
        row = {"id": cid, "desc": desc, "combo": combo, "current_flags": cur_flags, "current_primary": cur_primary, "current_outcome": cur_out}
        for v in VARIANTS:
            fl = flags_variant(r, v, exc)
            row[v] = {"flags": fl, "primary": fl[0] if fl else None, "outcome": outcome_of_flags(fl)}
        rows.append(row)
    return rows


def grid():
    """Exhaustive record grid (status x error x finish reason x block reason x text x parser exception). Returns {variant: [records whose flags differ from B]}."""
    import itertools
    out = {v: [] for v in VARIANTS if v != "B"}
    n = 0
    for st, er, fr, br, tx, ex in itertools.product((None, 200, 503), (None, "E"), (None, "STOP", "MAX_TOKENS", "SAFETY"), (None, "SAFETY"), ("", " " + chr(10), "a", chr(0xA0)), (None, ValueError("x"))):
        r = {"http_status": st, "error": er, "finish_reason": fr, "block_reason": br, "response_text": tx}
        n += 1
        base = flags_variant(r, "B", ex)
        for v in out:
            if flags_variant(r, v, ex) != base:
                out[v].append((r, ex is not None))
    return n, out


def markdown(rows):
    h = "| Case | Description | Current code: flags / primary / outcome | B | A1 | A2a | A2b |\n|---|---|---|---|---|---|---|\n"
    lines = []
    for x in rows:
        cur = f"{'+'.join(x['current_flags']) or '-'} / {x['current_primary'] or '-'} / {x['current_outcome']}"
        cells = " | ".join(f"{'+'.join(x[v]['flags']) or '-'} -> {x[v]['outcome']}" for v in VARIANTS)
        lines.append(f"| {x['id']} | {x['desc']} | {cur} | {cells} |")
    return h + "\n".join(lines)


if __name__ == "__main__":
    print(markdown(table()))
