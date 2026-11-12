"""Frozen structural diagnostic code (specification 13.6.1 activity A, 13.6 H3; decision record 12.18 rows 3 and 4) -- DRAFT.

It builds the structural report of a stored raw record. The report has exactly the fields of REPORT_FIELDS and no others
(13.6.1: "produced from a fixed schema, a whitelist of fields, not by filtering free text"):
  byte_length, finish_reason, utf8_valid, label_counts, label_offsets, exception_class, frames, checker_outcome_class,
  checker_agrees.
It never contains the exception message, exception arguments, local variables, traceback source lines, the repr of any object,
or any substring of the response. The extracted verdict token of the harness and of the reference checker are compared by code
and are never placed in the report; only the outcome class of the reference checker and the agreement flag are.

Input contract (12.18 row 4): the stored record as bytes. It is decoded as UTF-8 with replacement characters; whether the
bytes were valid UTF-8 is reported separately (utf8_valid). The label offsets are character offsets in the decoded text, so
they shift only if invalid bytes precede a label. The reference checker is run on the decoded text; if it raised, its outcome
class is None and the agreement flag is False (H3: the checker must not raise, so a raise means the fix is not accepted).

Invalid arguments (record not bytes, harness not callable, exc not an exception, harness_dir not a str) raise TypeError
before anything is read (decision record 12.21 row 4). It is a different artifact from the H3 reference checker
(s7_refcheck.py), which it only calls.

Values that are not code-controlled (the provider finish reason, class, function and file names) are passed through a
pattern whitelist and replaced by "unrecognized" if they do not match.
"""
import os
import re
import traceback

import s7_refcheck

LABELS = ("Constraint:", "Verdict:", "Comments:")
REPORT_FIELDS = ("byte_length", "finish_reason", "utf8_valid", "label_counts", "label_offsets", "exception_class", "frames",
                 "checker_outcome_class", "checker_agrees")
_FINISH_RX = re.compile(r"[A-Za-z0-9_]{1,40}")
_NAME_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,80}")
_FILE_RX = re.compile(r"[A-Za-z0-9_.\-]{1,80}")
UNRECOGNIZED = "unrecognized"


def _safe(value, rx):
    return value if isinstance(value, str) and rx.fullmatch(value) is not None else UNRECOGNIZED


def _label_offsets(text, label):
    out, start = [], 0
    while True:
        i = text.find(label, start)
        if i < 0:
            return out
        out.append(i)
        start = i + 1


def _outcome(result):
    """(class, token) of a parse result ('PARSEABLE', token) or ('UNP', reason); None if malformed."""
    if not (isinstance(result, tuple) and len(result) == 2):
        return None
    if result[0] == "PARSEABLE":
        return ("parseable", result[1])
    if result[0] == "UNP":
        return ("UNP", None)
    return None


def _frames(exc, harness_dir):
    base = os.path.normcase(os.path.abspath(harness_dir)) + os.sep
    out = []
    for fs in traceback.extract_tb(exc.__traceback__):          # FrameSummary.line (source text) is never read
        path = os.path.normcase(os.path.abspath(fs.filename))
        if path.startswith(base):
            out.append({"file": _safe(os.path.basename(fs.filename), _FILE_RX), "function": _safe(fs.name, _NAME_RX),
                        "line": fs.lineno if isinstance(fs.lineno, int) else -1})
    return out


def structural_report(raw, finish_reason, harness_parse, *, exc=None, harness_dir=None):
    """Structural report of one stored record.
    raw: bytes; finish_reason: the provider's finish reason string (or None); harness_parse: the (fixed) harness parser,
    a callable str -> ('PARSEABLE', token) | ('UNP', reason), run here on the decoded record; exc: the exception that triggered
    the diagnosis, if any (only its class name and the harness frames of its traceback are used); harness_dir: the directory
    of harness code whose frames are reported (default: the directory of this file)."""
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("the stored record must be bytes")
    if not callable(harness_parse):
        raise TypeError("harness_parse must be callable")
    if exc is not None and not isinstance(exc, BaseException):
        raise TypeError("exc must be an exception or None")
    if harness_dir is not None and not isinstance(harness_dir, str):
        raise TypeError("harness_dir must be a str or None")
    raw = bytes(raw)
    try:
        raw.decode("utf-8")
        utf8_valid = True
    except UnicodeDecodeError:
        utf8_valid = False
    text = raw.decode("utf-8", errors="replace")
    offsets = {lab: _label_offsets(text, lab) for lab in LABELS}

    try:
        checker = _outcome(s7_refcheck.ref_parse_verdict(text))
    except Exception:
        checker = None
    harness_exc = None
    try:
        harness = _outcome(harness_parse(text))
    except Exception as e:                                       # a raise by the fixed harness is itself a finding
        harness, harness_exc = None, e
    agrees = checker is not None and harness is not None and checker[0] == harness[0] and (
        checker[0] != "parseable" or checker[1] == harness[1])

    reported = exc if exc is not None else harness_exc
    report = {
        "byte_length": len(raw),
        "finish_reason": None if finish_reason is None else _safe(finish_reason, _FINISH_RX),
        "utf8_valid": utf8_valid,
        "label_counts": {lab: len(offsets[lab]) for lab in LABELS},
        "label_offsets": offsets,
        "exception_class": None if reported is None else _safe(type(reported).__name__, _NAME_RX),
        "frames": [] if reported is None else _frames(reported, harness_dir or os.path.dirname(os.path.abspath(__file__))),
        "checker_outcome_class": None if checker is None else checker[0],
        "checker_agrees": agrees,
    }
    assert tuple(report) == REPORT_FIELDS
    return report
