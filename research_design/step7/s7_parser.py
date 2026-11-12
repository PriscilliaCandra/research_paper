"""Step 7 parser grammar (section 13.4) and the deterministic decision rules of the S2/S3 codebook (section 16). DRAFT for owner review.

The frozen reference grammar checker (13.6.1, section 14) is a separate, self-contained file: s7_refcheck.py. The two must agree
on every input (tested on the synthetic batteries and on seeded random strings).

Grammar (frozen text of this draft), applied to the response split at LF only:
  field line   : optional spaces/tabs, optional emphasis characters [*_`], the word "verdict" (ASCII case-insensitive),
                 optional emphasis characters, optional spaces/tabs, ":", optional emphasis characters, optional
                 spaces/tabs, then the rest of the line (the raw value).
  normalization: repeat until stable: strip whitespace; strip leading/trailing emphasis characters [*_`]; strip
                 trailing punctuation [.,;:!]; then ASCII-casefold. Whitespace = ASCII space, tab, CR, VT, FF
                 (decision record 12.18 row 7); Unicode spaces are not whitespace; list or heading prefixes are not tolerated.
  outcome      : zero field lines -> UNP; two or more field lines with different normalized values -> UNP;
                 otherwise the single normalized value must be exactly one of satisfied / violated / unclear,
                 else UNP. Two or more field lines with the same normalized value parse as that value.
"""
import re
from fractions import Fraction

import prompt_specification_v1 as SPEC

_WS = SPEC.ASCII_WHITESPACE
_EMPH = SPEC.EMPHASIS_CHARS
_PUNCT = SPEC.VERDICT_TRAILING_PUNCTUATION
_FIELD_RX = re.compile(r"[ \t]*[*_`]*verdict[*_`]*[ \t]*:[*_`]*[ \t]*(.*)", re.IGNORECASE | re.ASCII)
_COMMENTS_RX = re.compile(r"[ \t]*[*_`]*comments[*_`]*[ \t]*:[*_`]*[ \t]*(.*)", re.IGNORECASE | re.ASCII)


def _norm_value(v):
    prev = None
    while prev != v:
        prev = v
        v = v.strip(_WS)
        v = v.strip(_EMPH)
        v = v.rstrip(_PUNCT)
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in v)


def field_values(response):
    """Raw values of all Verdict field lines (regex implementation)."""
    out = []
    for ln in response.split("\n"):
        m = _FIELD_RX.fullmatch(ln)
        if m:
            out.append(m.group(1))
    return out


def parse_verdict(response):
    vals = [_norm_value(v) for v in field_values(response)]
    if not vals:
        return ("UNP", "no field")
    if len(set(vals)) > 1:
        return ("UNP", "conflicting fields")
    if vals[0] not in SPEC.VERDICT_TOKENS:
        return ("UNP", "token not allowed")
    return ("PARSEABLE", vals[0])


def redact_verdict_lines(response, marker=SPEC.REDACTION_MARKER):
    """Replace every Verdict field line by a fixed marker line (coder materials, section 16: Verdict line redacted)."""
    return "\n".join(marker if _FIELD_RX.fullmatch(ln) else ln for ln in response.split("\n"))


# ----------------------------------------------------------------------------- Comments boundary (12.18 row 11)


def is_empty_comment(text):
    """True for no comment: None, empty, or only one of EMPTY_COMMENT_TOKENS (after removing ASCII whitespace, LF and
    emphasis characters at both ends)."""
    if text is None:
        return True
    prev, t = None, text
    while prev != t:
        prev = t
        t = t.strip(_WS + "\n").strip(_EMPH)
    return t == "" or t.casefold() in SPEC.EMPTY_COMMENT_TOKENS


def comments_of(response):
    """The Comments of a response: the text after the first Comments field label (same line grammar as the Verdict field), to
    the end of the response, multi-line; Verdict field lines after the label are replaced by the redaction marker. None when
    there is no label or the content is empty (see is_empty_comment)."""
    lines = response.split("\n")
    for i, ln in enumerate(lines):
        m = _COMMENTS_RX.fullmatch(ln)
        if m:
            break
    else:
        return None
    rest = [m.group(1)] + [SPEC.REDACTION_MARKER if _FIELD_RX.fullmatch(x) else x for x in lines[i + 1:]]
    if is_empty_comment("\n".join(x for x in rest if x != SPEC.REDACTION_MARKER)):
        return None
    return "\n".join(rest).strip(_WS + "\n")


# ----------------------------------------------------------------------------- S3 lexical test and decision rules


def s3_ref(comments):
    """Variable s3_ref (section 16.2): NC if no comment; REF iff the Comments contain at least one exact phrase of the
    frozen list (case-insensitive, word-bounded); NOREF otherwise. U+2019 is read as an ASCII apostrophe."""
    if is_empty_comment(comments):
        return "NC"
    text = comments.replace(chr(0x2019), "'").casefold()
    for ph in SPEC.REVIEWER_REFERENCE_PHRASES:
        if re.search(r"(?<![a-z0-9_])" + re.escape(ph) + r"(?![a-z0-9_])", text):
            return "REF"
    return "NOREF"


def s3_stance(*, no_comment, asserts, rejects, asks):
    """Deterministic precedence on coder-assigned features: NC -> MPG (assertion and rejection) -> AG -> DG -> CQ -> NS."""
    if no_comment:
        return "NC"
    if asserts and rejects:
        return "MPG"
    if asserts:
        return "AG"
    if rejects:
        return "DG"
    if asks:
        return "CQ"
    return "NS"


# ----------------------------------------------------------------------------- S2


def relation_matches(comments):
    """LEXICAL detection of registered relation strings, in order of appearance: a list of (start, end, token). This is
    detection only (decision record 12.21 row 1). It looks at no neighbouring word: a negation ("not", "does not"), a
    synonym or an inflection changes nothing here, and nothing is concluded about whether a relation holds or whether a
    constraint is satisfied. Verification of the clause as written, negation included, is the coder's judgement (s2_rel).
    Closed list (12.13 item 1): exact strings, word-bounded and case-insensitive; the ASCII symbols < > = and the Unicode
    symbols U+2265, U+2264. A maximal run of the characters < > = ! is ONE symbol and is listed only if it is exactly one of
    the three ASCII symbols, so the digraphs >= and <= are not listed. No stemming, no synonyms, no inferred equivalence:
    "is not above", "is not below", "does not exceed", "exceeded", "surpasses" match nothing."""
    hits = []
    low = comments.casefold()
    for ph in SPEC.RELATION_WORDS:
        for m in re.finditer(r"(?<![a-z0-9_])" + re.escape(ph) + r"(?![a-z0-9_])", low):
            hits.append((m.start(), m.end(), ph))
    for m in re.finditer("[" + re.escape(SPEC.RELATION_RUN_ALPHABET) + "]+", comments):
        if m.group() in SPEC.RELATION_ASCII_SYMBOLS:
            hits.append((m.start(), m.end(), m.group()))
    for i, ch in enumerate(comments):
        if ch in SPEC.RELATION_UNICODE_SYMBOLS:
            hits.append((i, i + 1, ch))
    return sorted(hits)


def relation_tokens(comments):
    """The registered tokens of relation_matches, in order (an aid for tests and coders, not a coding rule: whether a
    clause is a numerical comparison is decided by the coder)."""
    return [tok for _, _, tok in relation_matches(comments)]


def s2_evidence(*, no_comment, has_numerical_comparison, has_used_value_or_provenanced_derived):
    """Deterministic precedence (section 16.1): NC -> EV -> IV -> NV."""
    if no_comment:
        return "NC"
    if has_numerical_comparison:
        return "EV"
    if has_used_value_or_provenanced_derived:
        return "IV"
    return "NV"


def s2_prov(derived_have_provenance):
    """Y every derived quantity has provenance; N at least one has none; None (outside the denominator) if there is none."""
    if not derived_have_provenance:
        return None
    return "Y" if all(derived_have_provenance) else "N"


def s2_values(used_values_match):
    """cv all used values match the prompt; sw at least one is wrong; none no used value."""
    if not used_values_match:
        return "none"
    return "cv" if all(used_values_match) else "sw"


def s2_arith(computations_correct):
    """Over stated computations WITH provenance: AC all correct; AI all incorrect; AM mixed; AN none."""
    if not computations_correct:
        return "AN"
    if all(computations_correct):
        return "AC"
    if not any(computations_correct):
        return "AI"
    return "AM"


def s2_rel(evidence, relations_hold):
    """Only for s2_evidence = EV: RT every written relation holds as written; RF none; RM mixed. None otherwise."""
    if evidence != "EV" or not relations_hold:
        return None
    if all(relations_hold):
        return "RT"
    if not any(relations_hold):
        return "RF"
    return "RM"


_NUMERAL_RX = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")


def computation_correct(stated, exact_values):
    """Correctness rule (16.1): a stated computation is correct iff it differs from the exact value by less than one unit of its
    last stated digit. `stated` is the numeral as written (a str, without % or unit); `exact_values` is a list of Fractions: one
    value for MIL, and for PAC finish times the unrounded value and the rnd* value (both accepted)."""
    if not isinstance(stated, str) or _NUMERAL_RX.fullmatch(stated) is None:
        raise ValueError("stated numeral not canonical")
    decimals = len(stated.split(".")[1]) if "." in stated else 0
    unit = Fraction(1, 10 ** decimals)
    s = Fraction(stated)
    return any(abs(s - Fraction(e)) < unit for e in exact_values)


def correct_explicit_verification(*, evidence, arith, values, rel, prov):
    """Derived flag (section 16.1): EV and AC and cv and RT and Y."""
    return evidence == "EV" and arith == "AC" and values == "cv" and rel == "RT" and prov == "Y"
