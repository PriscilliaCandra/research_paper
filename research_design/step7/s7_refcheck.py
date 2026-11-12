"""Frozen reference grammar checker (specification 13.6, 13.6.1, H3; section 14) -- DRAFT for owner review.

An independent minimal implementation of the Verdict grammar of section 13.4, used only for the H3 check. It is deliberately
self-contained: it imports no project module and no `re`, and it repeats the grammar constants as literals, so that a change
in the harness parser or in the data module cannot silently change it. The test suite asserts that the literals equal the
data module. Written as a separate file (decision record 12.18, row 3) so that a change to the harness parser does not change
the hash of this frozen item.

Grammar (applied to the response split at LF only):
  field line   : optional spaces/tabs, optional emphasis characters [*_`], the word "verdict" (ASCII case-insensitive),
                 optional emphasis characters, optional spaces/tabs, ":", optional emphasis characters, optional
                 spaces/tabs, then the rest of the line (the raw value).
  normalization: remove from the left whitespace and emphasis characters; from the right whitespace, emphasis characters
                 and the punctuation . , ; : ! ; then ASCII-casefold. Whitespace = ASCII space, tab, CR, VT, FF.
  outcome      : zero field lines -> UNP; two or more field lines with different normalized values -> UNP; otherwise the
                 single normalized value must be exactly one of satisfied / violated / unclear, else UNP.

Input contract: a str (the stored record decoded by the caller). Any other input raises TypeError; the caller (the diagnostic
code) never passes anything else.
"""

_EMPH = "*_`"
_WS = " \t\r\x0b\x0c"
_PUNCT = ".,;:!"
_TOKENS = ("satisfied", "violated", "unclear")


def _lower_ascii(s):
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in s)


def _field_value(line):
    i, n = 0, len(line)
    while i < n and line[i] in " \t":
        i += 1
    while i < n and line[i] in _EMPH:
        i += 1
    if _lower_ascii(line[i:i + 7]) != "verdict":
        return None
    i += 7
    while i < n and line[i] in _EMPH:
        i += 1
    while i < n and line[i] in " \t":
        i += 1
    if i >= n or line[i] != ":":
        return None
    i += 1
    while i < n and line[i] in _EMPH:
        i += 1
    while i < n and line[i] in " \t":
        i += 1
    return line[i:]


def _norm(v):
    a, b = 0, len(v)
    while a < b and (v[a] in _WS or v[a] in _EMPH):
        a += 1
    while b > a and (v[b - 1] in _WS or v[b - 1] in _EMPH or v[b - 1] in _PUNCT):
        b -= 1
    return _lower_ascii(v[a:b])


def ref_parse_verdict(response):
    if not isinstance(response, str):
        raise TypeError("the reference checker takes a str")
    vals = []
    start = 0
    for idx in range(len(response) + 1):
        if idx == len(response) or response[idx] == "\n":
            val = _field_value(response[start:idx])
            if val is not None:
                vals.append(_norm(val))
            start = idx + 1
    if not vals:
        return ("UNP", "no field")
    first = vals[0]
    for v in vals[1:]:
        if v != first:
            return ("UNP", "conflicting fields")
    if first not in _TOKENS:
        return ("UNP", "token not allowed")
    return ("PARSEABLE", first)
