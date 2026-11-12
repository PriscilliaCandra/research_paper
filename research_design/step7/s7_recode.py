"""Delayed intra-rater re-code: deterministic selection, order and agreement tables (specification 16.3; owner decision OD-3;
decision record 12.21 row 6) -- DRAFT for owner review.

Why this exists: with one human coder (OD-3) section 16.3 prescribes "a delayed intra-rater re-code on an order scrambled by
opaque ID". This module makes the selection and the order reproducible and independent of any code. It reads no response, no
code and no registry; its inputs are opaque IDs, cell labels and code pairs.

Parameters, fixed here BEFORE any response exists (delegated operational choices pending advisor review, without an empirical
basis; 12.21 row 6, confirmed in 12.24 decision 2):
  - sample per (family, condition) cell: all parseable responses of the cell if there are at most RECODE_FLOOR of them,
    otherwise max(RECODE_FLOOR, ceil(RECODE_NUM / RECODE_DEN * D_P));
  - minimum delay RECODE_DELAY_DAYS full days between the last first-pass code and the first re-code.
Canonical inputs (decision record 12.24, decision 2; all ASCII, no BOM, no terminating LF, no other whitespace):
  selection key input = the bytes of  recode-select,<bundle hash>,<opaque id>   (the literal tag, a comma, 64 lowercase hex, a
  comma, 16 lowercase hex); order key input = the same with the tag  recode-order.
  key = SHA-256 of the input bytes, written as 64 lowercase hex characters; keys are compared as those strings (equal to the
  comparison of the big-endian integers). Ordering: ascending key; tie-break (a collision, practically impossible): ascending
  opaque ID. The first sample_size(D_P) opaque IDs of a cell in selection order are the sample. Cells are processed in ascending
  order of their label (a str); the labels play no part in the keys. A response belongs to one cell only. The bundle hash is the SHA-256 of the sorted opaque IDs, LF
separated, with a final LF; it is recorded when the coder bundle has passed PG11b and before any coding starts, so neither
key can depend on a code.

Agreement is reported as raw counts and a full cross-table per variable. There is no kappa, no interval and no reliability claim
anywhere in this module (OD-3).
"""
import datetime
import hashlib
import re

RECODE_FLOOR = 20
RECODE_NUM, RECODE_DEN = 1, 5
RECODE_DELAY_DAYS = 14
_ID_RX = re.compile(r"[0-9a-f]{16}")


def _check_ids(ids):
    ids = list(ids)
    if any(not isinstance(i, str) or _ID_RX.fullmatch(i) is None for i in ids):
        raise ValueError("opaque IDs must be 16 lowercase hex characters")
    if len(set(ids)) != len(ids):
        raise ValueError("opaque IDs must be unique")
    return ids


def bundle_hash(opaque_ids):
    """SHA-256 of the sorted opaque IDs, LF separated, final LF."""
    ids = sorted(_check_ids(opaque_ids))
    if not ids:
        raise ValueError("empty bundle")
    return hashlib.sha256(("\n".join(ids) + "\n").encode("ascii")).hexdigest()


def sample_size(d_p):
    """Number of responses re-coded in a cell with d_p parseable responses."""
    if not isinstance(d_p, int) or isinstance(d_p, bool) or d_p < 0:
        raise ValueError("d_p must be a non-negative int")
    if d_p <= RECODE_FLOOR:
        return d_p
    return max(RECODE_FLOOR, -(-d_p * RECODE_NUM // RECODE_DEN))


def key_input(tag, bundle_hex, oid):
    """The exact bytes hashed for one key (tag is 'recode-select' or 'recode-order')."""
    if tag not in ("recode-select", "recode-order"):
        raise ValueError("unknown tag")
    return f"{tag},{bundle_hex},{oid}".encode("ascii")


def _key(tag, bundle_hex, oid):
    return hashlib.sha256(key_input(tag, bundle_hex, oid)).hexdigest()


def select_sample(cells, bundle_hex):
    """cells: {cell label: [opaque IDs of the parseable responses of the cell]}. Returns {cell: [selected IDs]} in selection-key
    order. A response can belong to one cell only."""
    if re.fullmatch(r"[0-9a-f]{64}", bundle_hex or "") is None:
        raise ValueError("bundle hash must be 64 lowercase hex characters")
    seen = set()
    out = {}
    for cell in sorted(cells):
        ids = _check_ids(cells[cell])
        if seen & set(ids):
            raise ValueError("an opaque ID belongs to two cells")
        seen |= set(ids)
        ranked = sorted(ids, key=lambda i: (_key("recode-select", bundle_hex, i), i))
        out[cell] = ranked[:sample_size(len(ids))]
    return out


def recode_order(selected_ids, bundle_hex):
    """The scrambled order of the re-code (differs from the first-pass order, which is by opaque ID)."""
    if re.fullmatch(r"[0-9a-f]{64}", bundle_hex or "") is None:
        raise ValueError("bundle hash must be 64 lowercase hex characters")
    return sorted(_check_ids(selected_ids), key=lambda i: (_key("recode-order", bundle_hex, i), i))


def delay_ok(last_first_pass_code, first_recode, min_days=RECODE_DELAY_DAYS):
    """True iff at least min_days full days lie between the two dates (datetime.date or datetime.datetime in UTC)."""
    a = last_first_pass_code.date() if isinstance(last_first_pass_code, datetime.datetime) else last_first_pass_code
    b = first_recode.date() if isinstance(first_recode, datetime.datetime) else first_recode
    return (b - a).days >= min_days


def agreement_table(pairs):
    """pairs: list of (first-pass code, re-code) of one variable. Returns raw counts, the full cross-table and the disagreement
    counts. The percentage is raw agreement 100*agree/n with one decimal, computed with integers. No chance correction."""
    pairs = list(pairs)
    if any(not (isinstance(p, tuple) and len(p) == 2) for p in pairs):
        raise ValueError("pairs must be 2-tuples")
    n = len(pairs)
    cross = {}
    for a, b in pairs:
        cross[(a, b)] = cross.get((a, b), 0) + 1
    agree = sum(c for (a, b), c in cross.items() if a == b)
    pct = None if n == 0 else f"{(1000 * agree * 2 + n) // (2 * n) // 10}.{((1000 * agree * 2 + n) // (2 * n)) % 10}"
    return {"n": n, "agree": agree, "disagree": n - agree, "raw_agreement_percent": pct,
            "cross_table": {f"{a}|{b}": c for (a, b), c in sorted(cross.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))},
            "disagreements_by_pair": {f"{a}|{b}": c for (a, b), c in sorted(cross.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))) if a != b}}
