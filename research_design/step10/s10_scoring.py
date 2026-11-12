"""Step 10-12 scoring: call records -> structural-failure flags, parse outcome, match against the deterministic state.
Specification 13.4 (SF1-SF5, UNP), 13.6/13.7 (pilot counts only), 18.1/18.2 (main). Decision record 12.30.

Precedence (owner clarification 12.18 row 1, applied to every count): a call that meets any SF definition is classified SF only; it
is never UNP and never a verdict class. Several flags on one call: the specification defines no order (proposal Q4, not adopted);
this module records ALL flags. The provisional primary type SF1 > SF4 > SF3 > SF2 > SF5 is used for RECORDING only (primary_counts, ledger).
The disjoint count vector that s7_decision.round_outcome needs comes from decision_type(): a call meeting SF2, SF3 or SF5 counts once as hard,
any other SF call once as soft (f_S per call). That rule is WORKING decision MD-01 (project owner's direction, 2026-10-13); it is NOT ratified by
the official record or the advisor, and the relation of SF2 to SF1/SF4 is open (conflict C-31). The number of multi-flag calls is reported next to it.
Pure functions; reads no file; calls no model.
"""
import os
import sys

STEP7 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "step7")
sys.path.insert(0, STEP7)
sys.dont_write_bytecode = True
import s7_parser  # noqa: E402

BLOCK_REASONS = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "RECITATION", "OTHER", "LANGUAGE"}
TOKEN_LIMIT_REASONS = {"MAX_TOKENS"}
PRIMARY_ORDER = ("SF1", "SF4", "SF3", "SF2", "SF5")      # provisional, NOT adopted (proposal Q4)
EXPECTED = {"A": "satisfied", "B": "violated", "C": "satisfied", "D": "violated"}


KNOWN_FINISH = {"STOP"} | TOKEN_LIMIT_REASONS | BLOCK_REASONS


def finish_reason_class(fr):
    """Metadata only: 'stop', 'token_limit' (13.4 SF3), 'blocked' (13.4 SF4: which provider names count as blocked is an implementation list, see
    ADVISOR/validation report), 'none' or 'unlisted'. An unlisted reason is NOT given a protocol meaning here: it is only counted and reported."""
    if fr is None:
        return "none"
    if fr == "STOP":
        return "stop"
    if fr in TOKEN_LIMIT_REASONS:
        return "token_limit"
    if fr in BLOCK_REASONS:
        return "blocked"
    return "unlisted"


def _status_ok(rec):
    st = rec.get("http_status")
    return st is None or (isinstance(st, int) and not isinstance(st, bool) and 200 <= st < 300)


def sf_flags(rec, parse_exc=None):
    """All SF flags a recorded call meets (13.4). rec: dict with error, http_status, response_text, finish_reason, block_reason.
    Fail-closed (decision record 12.36): a record that carries an error marker, or an HTTP status outside 2xx, is an HTTP/API error (SF1) even
    if a text is present; a prompt-level block reason is a provider block (SF4)."""
    text = rec.get("response_text") or ""
    flags = []
    if rec.get("error") or not _status_ok(rec):
        flags.append("SF1")                                         # transport failure, HTTP/API error or timeout
    fr = rec.get("finish_reason")
    if fr in TOKEN_LIMIT_REASONS:
        flags.append("SF3")
    if fr in BLOCK_REASONS or rec.get("block_reason") or (not rec.get("error") and rec.get("http_status") == 200 and fr is None and not text):
        flags.append("SF4")                                         # blocked/safety finish, prompt block, or no candidate returned
    if not rec.get("error") and not text.strip(chr(32) + chr(9) + chr(10) + chr(11) + chr(12) + chr(13)) and "SF4" not in flags and "SF1" not in flags:
        flags.append("SF2")                                         # no non-whitespace character; not emitted when SF1/SF4 explains the empty body
                                                                    # (option B of C-31: working decision of the researcher, not ratified; see test_sf2_detection.py)
    if parse_exc is not None:
        flags.append("SF5")
    return [f for f in PRIMARY_ORDER if f in flags]


def classify(rec):
    """Returns dict: flags, primary_sf (provisional), outcome in SF/satisfied/violated/unclear/UNP, parse_reason."""
    exc = None
    parsed = ("UNP", "not parsed")
    text = rec.get("response_text") or ""
    if text.strip():
        try:
            parsed = s7_parser.parse_verdict(text)
        except Exception as e:                                      # SF5: the parser raised on a non-empty response
            exc = e
    flags = sf_flags(rec, exc)
    if flags:
        return {"flags": flags, "primary_sf": flags[0], "outcome": "SF", "token": None}
    kind, tok = parsed
    return {"flags": [], "primary_sf": None, "outcome": tok if kind == "PARSEABLE" else "UNP", "token": tok if kind == "PARSEABLE" else None}


HARD_FLAGS = ("SF2", "SF3", "SF5")
SOFT_FLAGS = ("SF1", "SF4")


def decision_type(flags):
    """MD-01 (working technical decision, not institutionally approved): the single count a failed call enters in the pilot decision vector.
    A call that meets a hard definition (SF2, SF3, SF5; spec 13.6) enters a HARD count even if it also meets SF1 or SF4; otherwise it enters
    one SOFT count (SF1 or SF4), once, however many soft flags it has (f_S counts calls). Order inside each group follows PRIMARY_ORDER.
    This is a disjoint assignment only; every flag stays recorded (flag_totals) and the recording primary (classify) is unchanged."""
    for f in PRIMARY_ORDER:
        if f in flags and f in HARD_FLAGS:
            return f
    for f in PRIMARY_ORDER:
        if f in flags and f in SOFT_FLAGS:
            return f
    return None


def decision_counts(flag_sets, unp=0):
    """Decision vector (SF1..SF5, UNP) from a list of flag lists, one per SF call."""
    c = {k: 0 for k in ("SF1", "SF2", "SF3", "SF4", "SF5", "UNP")}
    for fl in flag_sets:
        t = decision_type(fl)
        if t is not None:
            c[t] += 1
    c["UNP"] = unp
    return c


def pilot_counts(records):
    """Counts only (13.7). `counts` = the DECISION vector (MD-01): disjoint, one entry per failed call, hard definitions first (see decision_type);
    `primary_counts` = the recording classification by PRIMARY_ORDER (provisional, never used for the decision); `flag_totals` = every flag met
    (non-exclusive). Also UNP, n_calls, multi-flag calls, latency, tokens. Agreement with the ground truth and the verdict distribution are NOT
    computed for the pilot."""
    c = {k: 0 for k in ("SF1", "SF2", "SF3", "SF4", "SF5", "UNP")}
    primary = {k: 0 for k in ("SF1", "SF2", "SF3", "SF4", "SF5")}
    flag_totals = {k: 0 for k in ("SF1", "SF2", "SF3", "SF4", "SF5")}
    multi, lat, tok = 0, [], 0
    no_usage, unlisted, models, no_model = 0, {}, {}, 0
    for r in records:
        cl = classify(r)
        if cl["outcome"] == "SF":
            c[decision_type(cl["flags"])] += 1
            primary[cl["primary_sf"]] += 1
            for f in cl["flags"]:
                flag_totals[f] += 1
            multi += len(cl["flags"]) > 1
        elif cl["outcome"] == "UNP":
            c["UNP"] += 1
        if r.get("latency_ms") is not None:
            lat.append(r["latency_ms"])
        u = (r.get("usage") or {}).get("totalTokenCount")
        if isinstance(u, int) and not isinstance(u, bool) and u >= 0:
            tok += u
        else:
            no_usage += 1                                           # never silently counted as zero tokens
        if finish_reason_class(r.get("finish_reason")) == "unlisted":
            unlisted[r["finish_reason"]] = unlisted.get(r["finish_reason"], 0) + 1
        if r.get("model_returned"):
            models[r["model_returned"]] = models.get(r["model_returned"], 0) + 1
        elif r.get("response_text"):
            no_model += 1
    return {"counts": c, "primary_counts": primary, "flag_totals": flag_totals, "n_calls": len(records), "multi_flag_calls": multi,
            "latency_ms_max": max(lat) if lat else None, "total_tokens_known": tok, "calls_without_usable_total_tokens": no_usage,
            "unlisted_finish_reasons": unlisted, "models_returned": models, "responses_without_returned_model": no_model}


def call_table(records, template_meta):
    """Main-run per-call table. template_meta: {template_id: {family, base_index, class, ...}}. One row per recorded call."""
    rows = []
    for r in records:
        cl = classify(r)
        tid, cond = r["template_id"], r["condition"]
        m = template_meta[tid]
        rows.append({"call_key": r["call_key"], "template_id": tid, "condition": cond, "repetition": r["repetition"], "family": m["family"],
                     "base_index": m["base_index"], "class": m["class"], "outcome": cl["outcome"], "flags": cl["flags"],
                     "match": cl["outcome"] == EXPECTED[cond]})
    return rows
