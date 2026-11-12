"""Step 7 bundle builders for PG11a (checker materials) and PG11b (coder materials) -- DRAFT for owner review.

Section 8.2 of the specification. Opaque-ID layout of PG11b = Option E (owner approval of 2026-10-12, decision record 12.16):
  input     = <call_key>,<registry_hash_hex>   (ASCII, no BOM, no terminating LF)
  call_key  = <template_id>,<token>,<repetition>     (the call-list line of section 10 without its LF)
  registry_hash_hex = 64 lowercase hex characters (SHA-256 of the canonical registry JSON)
  opaque ID = first 16 lowercase hex characters of SHA-256(input)
An ID collision rejects the build. No salt, no automatic change of layout.
PG11a opaque ID = first 16 hex characters of SHA-256 over the rendered text (specification text; rendered prompts are ASCII).

PG11a / PG11b scan (8.2): "no pattern PT1, PT3-PT8b occurs in any file name, file body or directory name". The range PT3-PT8b
includes PT4 (all-caps identifiers). PT2 (hex strings) is outside the range because the opaque IDs are hex strings.

The functions are pure: they build bundles in memory. write_bundle() is the only function that writes, it refuses to overwrite,
it writes the bodies as UTF-8 (a coder body contains model text, which is not ASCII in general) and it never leaves a half
written directory behind. Nothing here reads a registry, a prompt file or a response; tests use synthetic strings only.
"""
import hashlib
import os
import re
import shutil

import prompt_specification_v1 as SPEC
import s7_core
import s7_parser

CALL_KEY_RX = re.compile(r"(?:MIL|PAC)-[0-9]{3,},[ABCD],[1-9][0-9]*")
HASH_RX = re.compile(r"[0-9a-f]{64}")
ID_RX = re.compile(r"[0-9a-f]{16}")
_SCAN_IDS = {"PT1", "PT3", "PT4", "PT5", "PT5b", "PT6", "PT7", "PT7b", "PT8", "PT8b"}
VERDICT_REDACTION_MARKER = SPEC.REDACTION_MARKER


class BuildRejected(Exception):
    """A bundle build is rejected as a whole (reject-not-repair, section 3.6)."""


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def opaque_id_text(rendered_text):
    """PG11a opaque ID: first 16 hex characters of SHA-256 over the rendered text (ASCII)."""
    try:
        data = rendered_text.encode("ascii")
    except UnicodeEncodeError:
        raise BuildRejected("rendered text is not ASCII")
    return sha256_hex(data)[:16]


def coder_id_input(call_key, registry_hash_hex):
    if CALL_KEY_RX.fullmatch(call_key) is None:
        raise BuildRejected(f"call key not canonical: {call_key!r}")
    if HASH_RX.fullmatch(registry_hash_hex) is None:
        raise BuildRejected("registry hash must be 64 lowercase hex characters")
    return (call_key + "," + registry_hash_hex).encode("ascii")


def opaque_id_call(call_key, registry_hash_hex):
    """PG11b opaque ID, Option E."""
    return sha256_hex(coder_id_input(call_key, registry_hash_hex))[:16]


def _scan_problems(text):
    return [h for h in s7_core.pt_scan(text) if h[0] in _SCAN_IDS]


def _leaks(internal_id, text):
    """True if the internal identifier occurs in the text as a whole token (not inside a longer alphanumeric run)."""
    if not isinstance(internal_id, str) or internal_id == "":
        return False
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(internal_id) + r"(?![A-Za-z0-9])", text) is not None


def check_directory_name(name):
    """PG11a/PG11b: no pattern PT1, PT3-PT8b in a directory name; the name of a bundle directory is the last path component."""
    return [f"pattern {h[0]} in directory name {name!r}" for h in _scan_problems(name)]


def check_bundle(files, mapping=None):
    """PG11a/PG11b format checks on a bundle (dict opaque_id -> body). Returns a list of problems (empty = pass)."""
    probs = []
    ids = list(files)
    for i in ids:
        if ID_RX.fullmatch(i) is None:
            probs.append(f"name not 16 lowercase hex: {i!r}")
        for h in _scan_problems(i):
            probs.append(f"pattern {h[0]} in name {i!r}")
        for h in _scan_problems(files[i]):
            probs.append(f"pattern {h[0]} in body {i!r} at {h[1]}")
    if ids != sorted(ids):
        probs.append("not ordered by opaque ID")
    if mapping is not None:
        for internal in mapping.values():
            for i in ids:
                if _leaks(internal, files[i]) or _leaks(internal, i):
                    probs.append(f"mapping content leaked into {i!r}")
    return probs


def build_checker_bundle(rendered_by_internal_id):
    """PG11a. Input: {internal_id: rendered prompt text}. Returns (files, mapping): files = {opaque_id: text} ordered by
    opaque ID; mapping = {opaque_id: internal_id}, to be held separately and not shown to the checker."""
    files, mapping = {}, {}
    for internal, text in rendered_by_internal_id.items():
        oid = opaque_id_text(text)
        if oid in files:
            raise BuildRejected(f"opaque ID collision or duplicate prompt: {oid}")
        files[oid], mapping[oid] = text, internal
    files = {k: files[k] for k in sorted(files)}
    probs = check_bundle(files, mapping)
    if probs:
        raise BuildRejected("; ".join(probs))
    return files, {k: mapping[k] for k in sorted(mapping)}


def build_coder_bundle(items, registry_hash_hex):
    """PG11b. Input: {call_key: (prompt text, response text)}. The body shows the prompt and the response with every
    Verdict field line redacted. Returns (files, mapping)."""
    files, mapping = {}, {}
    for call_key, (prompt, response) in items.items():
        oid = opaque_id_call(call_key, registry_hash_hex)
        if oid in files:
            raise BuildRejected(f"opaque ID collision: {oid}")
        body = "Prompt:\n" + prompt + "\n\nResponse:\n" + s7_parser.redact_verdict_lines(response, VERDICT_REDACTION_MARKER)
        files[oid], mapping[oid] = body, call_key
    files = {k: files[k] for k in sorted(files)}
    probs = check_bundle(files, mapping)
    if probs:
        raise BuildRejected("; ".join(probs))
    return files, {k: mapping[k] for k in sorted(mapping)}


def write_bundle(files, directory):
    """Write the bundle files (names = opaque IDs, no extension; bodies as UTF-8 without BOM, line endings kept). Refuses to
    write into an existing directory; rejects a directory name that matches PT1, PT3-PT8b; encodes every body before anything
    is created; removes the directory again if a write fails."""
    probs = check_directory_name(os.path.basename(os.path.normpath(directory)))
    if probs:
        raise BuildRejected("; ".join(probs))
    if os.path.exists(directory):
        raise BuildRejected("target directory exists; nothing is overwritten")
    encoded = {}
    for k, body in files.items():
        try:
            encoded[k] = body.encode("utf-8")
        except UnicodeEncodeError:
            raise BuildRejected(f"body {k!r} cannot be encoded as UTF-8")
    os.makedirs(directory)
    try:
        for k, data in encoded.items():
            with open(os.path.join(directory, k), "wb") as fh:
                fh.write(data)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
