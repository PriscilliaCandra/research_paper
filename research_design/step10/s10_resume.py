"""P8 resume of an interrupted or halted run -- FAIL-CLOSED. Written 2026-10-14 on the owner's instruction; NOT RUN against a provider; NOT adopted by the decision record
(MD-08 is still PENDING OWNER / ADVISOR). Tests use MockProvider only.

What the controlling text says (spec 13.8): "A run interrupted by transport failure resumes only under the identical FZ version (plus any pure implementation fix),
continuing at the next call_index of the same frozen order (no re-draw)"; "no completed call is replaced or discarded; no call is silently substituted"; "A transport-only
halt (SF1) may resume only under the identical FZ version". That is all the specification says.

What this module therefore does (explicit rule -> check):
  * same frozen order, next call_index, no re-draw: the plan is recomputed from the build (and, for main, the pilot decision record); the run's provenance entry must name the
    same call-list hash; the resumed run starts at the first call without a persisted result;
  * identical FZ version, configuration, build, authorization, evidence, settings, provider class: every one is compared with the provenance entry written before the first
    call; any mismatch refuses the resume (a pure implementation fix is allowed: the code hashes are recorded, not compared);
  * no completed call is replaced: a call with a persisted, validated raw record is never called again; a late-written ledger/calls entry for it is appended (a "reconcile"
    entry) and the record itself is not touched;
  * the earlier segment is kept: the ledger is append-only (a "resume" entry records the previous chain head, the counters carried over, the resume authorization hash and
    the previous status marker, which is MOVED into segments/, never deleted); one hash chain therefore covers the whole series.

What it does NOT decide (MD-08 open points; the code refuses instead of guessing):
  * a call with an intent entry and NO persisted raw record ("attempted, no record"): the provider may or may not have processed it. It is never repeated and the resume is
    REFUSED until the owner/advisor decides its disposition (MD-08 point 2). No disposition is implemented.
  * "transport-only": implemented as a strict allow-list (TRANSPORT_ERROR_CLASSES): the record is SF1 only, has no response text, no HTTP status and an error class on the list.
    An HTTP status error (429, 500, ...) or any other exception class is NOT treated as transport-only here, so a halt caused by it cannot be resumed. This is a provisional
    reading of an open point (C-17), not a ratified definition.
  * who may authorize: a resume needs a separate resume-authorization file written by the owner (authorized_by = owner) that names the run, its exact ledger head, the basis,
    and the number of remaining calls. The module never creates it. Whether the advisor must also approve is unconfirmed.
  * UPDATE 2026-10-15 (decision records 12.40 and 12.42): the HTTP status allow-list is EMPTY; no exception class is ratified yet (RATIFIED_TRANSPORT_ERROR_CLASSES is
    empty), so no resume after a halt is possible; a resume after a stop WITHOUT a status marker needs the persistent-evidence contract (_process_death_contract: the last
    persisted call is an SF1-only transport failure on a ratified class, and the owner's authorization names the cause and the count); a change of the driving code
    (runner, resume, integrity) needs an owner-signed H1-H4 evidence file (_check_h1_h4; H1-H3 are manual attestations, not checked by the tool). The next bullet is superseded.
  * (superseded) a run that stopped WITHOUT a status marker (process killed, power loss, sleep) was accepted only with
    resume_basis "process_interruption" and acknowledges_unspecified_basis = true written by the owner, and the entry says so.
  * counters: the consecutive-failure counter, the failure total and the call count are REBUILT from the persisted records; nothing is reset. A halt condition that still
    holds therefore fires again after the next call.
  * a run halted by the cost stop, or by anything other than rule 13.8, or a COMPLETE run, is never resumable.
  * a halt whose status marker was lost (the process died before writing it) is still a halt: when the halt rule holds on the persisted records, the resume needs
    resume_basis "transport_halt" and the same transport-only evidence; it cannot be turned into a "process_interruption" resume to get around the rule;
  * the scoring code (s10_scoring.py) must be the one named in the provenance entry: a resume never mixes two classification versions in one run;
  * a run folder is held by an operating-system lock (run.lock) while a run or a resume is alive; a resume of a live folder is refused;
  * earlier status markers kept in segments/ must still be there and unaltered, and segments/ is never overwritten.

Resume never creates evidence, authorization or decision files and never prints response text or a secret.
"""
import datetime
import json
import os
import sys
import time
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s10_integrity as IG  # noqa: E402
import s10_runner as RN  # noqa: E402
import s10_scoring as SC  # noqa: E402

# AUDITED CANDIDATES ONLY (MD08_EXCEPTION_AUDIT_2026-10-15.md). Membership here is NOT ratification and gives no eligibility; see RATIFIED_TRANSPORT_ERROR_CLASSES.
TRANSPORT_ERROR_CLASSES = frozenset({
    "TimeoutError", "timeout", "URLError", "ConnectionError", "ConnectionResetError", "ConnectionAbortedError", "ConnectionRefusedError",
    "BrokenPipeError", "RemoteDisconnected", "IncompleteRead", "gaierror", "SSLError", "SSLEOFError", "SSLZeroReturnError",
})
BASES = ("transport_halt", "process_interruption")
# Policy (decision record entry 12.42, owner instructions of 2026-10-15). Module constants on purpose: nothing in a resume authorization file can change them.
HTTP_STATUS_ALLOWLIST = frozenset()              # owner instruction, Option A: EMPTY. No HTTP status is eligible for resume.
RATIFIED_TRANSPORT_ERROR_CLASSES = frozenset()   # classes the OWNER has individually ratified for resume. None so far: TRANSPORT_ERROR_CLASSES is only the audited candidate list.
PROCESS_DEATH_CONTRACT_ENFORCED = True           # persistent-evidence contract for a resume after a stop without halt marker (12.42). Tests of the machinery behind it may switch it off.
H_REASON_CLASSES = ("crash", "hang", "logging", "I/O")      # spec 13.6 H4
UNRESOLVED_INTENT_MESSAGE = ("call {k} has an intent entry and no persisted raw record (attempted, no record): it is never repeated automatically and the resume is refused "
                             "until the owner/advisor decide its disposition (MD-08 point 2, undecided)")


class ResumeRefused(Exception):
    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def is_transport_only(rec):
    """SF1 and nothing else, no response text, no HTTP status (the HTTP allow-list is empty, so no status ever qualifies) and an error class that the owner has
    ratified (a ratified name that is not an audited candidate is ignored). With nothing ratified this is always False."""
    cl = SC.classify(rec)
    st = rec.get("http_status")
    return (cl["outcome"] == "SF" and cl["flags"] == ["SF1"] and not rec.get("response_text") and st is None and st not in HTTP_STATUS_ALLOWLIST
            and rec.get("error") in (RATIFIED_TRANSPORT_ERROR_CLASSES & TRANSPORT_ERROR_CLASSES))


def _parse_json(b):
    try:
        v = json.loads(b.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return v if isinstance(v, dict) else None


def peek_scope(run_dir):
    """Scope named by the FIRST ledger line (the provenance entry), or None. Used only to pick the gate; damage elsewhere in the file is diagnosed by inspect_run."""
    try:
        with open(os.path.join(run_dir, "ledger.jsonl"), "rb") as f:
            first = f.readline()
    except OSError:
        return None
    e = _parse_json(first)
    return e.get("scope") if e and e.get("event") == "start" else None


def inspect_run(run_dir, build, scope, plan):
    """Strict reading of a run folder. Returns the facts a resume needs, or raises ResumeRefused with every reason found."""
    why = []
    led = IG._read_jsonl(os.path.join(run_dir, "ledger.jsonl"), "ledger.jsonl", why)
    calls = IG._read_jsonl(os.path.join(run_dir, "calls.jsonl"), "calls.jsonl", why)
    if led is None or calls is None or why:
        raise ResumeRefused(why or ["ledger or calls file unreadable"])
    if not led or led[0].get("event") != "start" or led[0].get("ledger_format") != RN.LEDGER_FORMAT:
        raise ResumeRefused(["the ledger has no provenance entry of format 2 (legacy or foreign run): the identity of the run cannot be verified"])
    prev = "0" * 64
    chain = []
    for i, e in enumerate(led):
        if e.get("prev") != prev:
            raise ResumeRefused([f"ledger chain broken at entry {i + 1}"])
        prev = RN.sha(RN.cj(e))
        chain.append(prev)
    head = prev
    start = led[0]
    table = {(r["template_id"], r["condition"]): r["sha256"]
             for r in RN.jl(os.path.join(build, "main" if scope == "main" else "pilot", "prompt_table.json"))}
    status, cur = {}, 0
    for pos, e in enumerate(led[1:], 2):
        ev, idx = e.get("event"), e.get("call_index")
        if ev in ("resume", "reconcile"):
            continue
        if ev == "intent":
            if idx != cur + 1 or (cur > 0 and not status[cur]["classified"]) or idx > len(plan):
                why.append(f"ledger entry {pos}: intent for call {idx} out of order")
                break
            if e.get("call_key") != plan[idx - 1]["call_key"]:
                why.append(f"ledger entry {pos}: call {idx} is not the call of the frozen order")
                break
            cur = idx
            status[idx] = {"raw": None, "classified": False}
        elif ev == "raw":
            if idx != cur or cur == 0 or status[cur]["raw"] is not None:
                why.append(f"ledger entry {pos}: raw entry out of order")
                break
            status[cur]["raw"] = e.get("raw_sha256")
        elif ev == "classified":
            if idx != cur or cur == 0 or status[cur]["raw"] is None or status[cur]["classified"]:
                why.append(f"ledger entry {pos}: classified entry out of order")
                break
            status[cur]["classified"] = True
        else:
            why.append(f"ledger entry {pos}: unknown event {ev!r}")
            break
    if why:
        raise ResumeRefused(why)
    k = cur
    last_open = k > 0 and not status[k]["classified"]
    complete_n = k - 1 if last_open else k
    records = []
    for i in range(1, complete_n + 1):
        try:
            raw = RN.file_bytes(os.path.join(run_dir, "raw", f"{i:05d}.json"))
        except OSError:
            raise ResumeRefused([f"raw file of call {i} is missing although the ledger completed it"])
        rec = _parse_json(raw)
        if rec is None or RN.sha(raw) != status[i]["raw"] or rec.get("call_index") != i or rec.get("call_key") != plan[i - 1]["call_key"]:
            raise ResumeRefused([f"raw record of call {i} does not match the ledger or the frozen order"])
        if table.get((rec.get("template_id"), rec.get("condition"))) != rec.get("prompt_sha256"):
            raise ResumeRefused([f"the raw record of call {i} does not carry the published prompt hash"])
        if i > len(calls) or calls[i - 1] != rec:
            raise ResumeRefused([f"calls.jsonl does not hold the raw record of call {i}"])
        records.append(rec)
    pending = None
    if last_open:
        path = os.path.join(run_dir, "raw", f"{k:05d}.json")
        if not os.path.exists(path):
            if status[k]["raw"] is not None:
                raise ResumeRefused([f"the ledger has a raw entry for call {k} but the raw file is missing"])
            raise ResumeRefused([UNRESOLVED_INTENT_MESSAGE.format(k=k)])
        raw = RN.file_bytes(path)
        rec = _parse_json(raw)
        if rec is None:
            raise ResumeRefused([f"the raw file of call {k} is not a valid record (truncated?)"])
        if rec.get("call_index") != k or rec.get("call_key") != plan[k - 1]["call_key"]:
            raise ResumeRefused([f"the raw file of call {k} is not the record of that call"])
        if table.get((rec.get("template_id"), rec.get("condition"))) != rec.get("prompt_sha256"):
            raise ResumeRefused([f"the raw record of call {k} does not carry the published prompt hash"])
        if status[k]["raw"] is not None and RN.sha(raw) != status[k]["raw"]:
            raise ResumeRefused([f"the raw file of call {k} differs from the hash in the ledger"])
        if len(calls) == k and calls[k - 1] != rec:
            raise ResumeRefused([f"calls.jsonl holds a different record for call {k}"])
        if len(calls) not in (k - 1, k):
            raise ResumeRefused(["calls.jsonl length does not fit the ledger"])
        pending = {"index": k, "raw_bytes": raw, "rec": rec, "need_raw_entry": status[k]["raw"] is None,
                   "need_calls_line": len(calls) == k - 1, "need_classified": True}
        records.append(rec)
    elif len(calls) != complete_n:
        raise ResumeRefused(["calls.jsonl length does not fit the ledger"])
    expected_files = {f"{i:05d}.json" for i in range(1, len(records) + 1)}
    try:
        present = set(os.listdir(os.path.join(run_dir, "raw")))
    except OSError:
        raise ResumeRefused(["the raw folder cannot be read"])
    if present != expected_files:
        raise ResumeRefused(["the raw folder holds files that the ledger does not explain, or lacks files it should hold"])
    marks = [m for m in ("RUN_COMPLETE", "RUN_INCOMPLETE") if os.path.exists(os.path.join(run_dir, m))]
    if len(marks) > 1:
        raise ResumeRefused(["two status markers"])
    summary = None
    if marks:
        summary = _parse_json(RN.file_bytes(os.path.join(run_dir, marks[0])))
        if summary is None:
            raise ResumeRefused(["the status marker is not readable"])
    resumes = [{k2: v for k2, v in e.items() if k2 != "prev"} for e in led if e.get("event") == "resume"]
    seg_dir = os.path.join(run_dir, "segments")
    seg_hashes = {RN.file_sha(os.path.join(seg_dir, f)) for f in os.listdir(seg_dir)} if os.path.isdir(seg_dir) else set()
    root_marker_sha = RN.file_sha(os.path.join(run_dir, marks[0])) if marks else None
    for n, r in enumerate(resumes, 1):
        h = r.get("prior_marker_sha256")
        if h is not None and h not in seg_hashes and h != root_marker_sha:
            raise ResumeRefused([f"segments/ does not hold, unaltered, the status marker named by resume entry {n}: the history of the run is incomplete"])
    return {"start": start, "head": head, "chain": chain, "ledger": led, "records": records, "pending": pending, "marker": marks[0] if marks else None,
            "summary": summary, "resumes": resumes}


def _process_death_contract(st0, marker):
    """Persistent-evidence contract for a resume after a stop WITHOUT a halt marker (decision record 12.42). A dead process, a missing marker or an owner statement is
    not evidence. Conditions checked here; the rest of the contract (chain, calls.jsonl and raw files verified; intent without a raw record refused; two or
    contradictory markers refused; the run lock; the ledger head named by the authorization; owner authorship) is enforced by inspect_run, resume() and prepare_resume.
    Option A1 (12.43): every structural failure in the whole run must also be transport-only. Returns (reasons, cause, trailing count). It covers only a process that died AFTER a persisted transport failure; a crash during a call leaves an intent without a
    raw record and stays refused."""
    recs = st0["records"]
    if marker is not None:
        return ["a status marker exists that is not a halt by rule 13.8: no process-death evidence"], None, None
    if not recs:
        return ["no persisted call: there is no evidence of a transport failure"], None, None
    n = 0
    for r in reversed(recs):
        if not is_transport_only(r):
            break
        n += 1
    if n == 0:
        return ["the last persisted call is not an SF1-only transport failure with no response text, no HTTP status and a RATIFIED error class: a stop without halt "
                "marker is not evidence of a transport failure (decision record 12.42)"], None, None
    bad = [r["call_index"] for r in recs if SC.classify(r)["outcome"] == "SF" and not is_transport_only(r)]
    if bad:
        # Option A1 (decision record 12.43): the WHOLE run must hold no structural failure that is not transport-only, as on the halt path. HTTP statuses, SF2-SF5,
        # mixed flags and unratified classes anywhere in the run therefore refuse the resume.
        return [f"calls {bad[:5]} are structural failures that are not transport-only (HTTP status, SF2-SF5, mixed flags or an unratified class): the process-death "
                "contract requires the whole run to be clean (option A1, decision record 12.43)"], None, None
    return [], recs[-1].get("error"), n


def _check_h1_h4(st0, path):
    """Spec 13.6 H1-H4 for a resume after a change of the driving code. Returns (reasons, evidence sha256 or None). The tool compares the hashes (before = the last
    segment's code hashes, after = the files now); H1, H2, H3 and the regression result are the OWNER's attestation of work done outside this tool (not verified here)."""
    base = (st0["resumes"][-1] if st0["resumes"] else st0["start"]).get("code_sha256") or {}
    now = RN.code_hashes()
    changed = sorted(n for n in set(base) | set(now) if base.get(n) != now.get(n) and n != "s10_scoring.py")      # s10_scoring.py is refused outright elsewhere
    if not changed:
        return [], None
    head = "driving code changed since the last segment (" + ", ".join(changed) + "): resume needs a pure implementation fix (spec 13.6 H1-H4) with an owner-signed evidence file; "
    if not path or not os.path.exists(path):
        return [head + "no H1-H4 evidence file was given"], None
    ev = _parse_json(RN.file_bytes(path))
    if ev is None:
        return [head + "the H1-H4 evidence file is not a JSON object"], None
    start = st0["start"]
    before = {n: base.get(n) for n in changed}
    after = {n: now.get(n) for n in changed}
    idx = ev.get("h3_triggering_call_index")
    probs = [m for ok, m in [
        (ev.get("authorized_by") == "owner" and ev.get("owner_signoff") is True, "not signed off by the owner"),
        (ev.get("run_id") == start.get("run_id"), "names another run"),
        (ev.get("code_hash_before") == before, "code_hash_before does not equal the hashes of the last segment (H4)"),
        (ev.get("code_hash_after") == after, "code_hash_after does not equal the files now (H4)"),
        (ev.get("reason_class") in H_REASON_CLASSES, f"reason_class must be one of {H_REASON_CLASSES} (H4)"),
        (isinstance(ev.get("reason"), str) and ev["reason"].strip() != "", "no written reason (H4)"),
        (ev.get("h1_executable_code_only") is True, "H1 (executable code only, no frozen layer) not attested"),
        (ev.get("h2_regression_byte_identical") is True and isinstance(ev.get("h2_regression_result_ref"), str) and ev["h2_regression_result_ref"].strip() != "",
         "H2 (byte-identical regression) not attested with a result reference"),
        (ev.get("h3_reference_checker_agreement") is True and isinstance(idx, int) and not isinstance(idx, bool),
         "H3 (agreement with the frozen reference grammar checker on the triggering stored record) not attested"),
        (ev.get("h4_logged") is True, "H4 logging not attested"),
    ] if not ok]
    if probs:
        return [head + "H1-H4 evidence invalid: " + "; ".join(probs)], None
    return [], RN.file_sha(path)


def prepare_resume(run_dir, build, provider, *, evidence_path, auth_path, resume_auth_path, pilot_record_path=None, verify_build=True, h1_h4_path=None, fz=None):
    """Every check that precedes the first provider call. Raises GateClosed, ResumeRefused or PreflightFailed; creates nothing."""
    scope = peek_scope(run_dir)
    if scope not in ("pilot_round_1", "pilot_round_2", "main"):
        raise ResumeRefused(["the run folder has no readable provenance entry: nothing can be resumed"])
    settings = RN.check_gates(scope, build, evidence_path, auth_path, pilot_record_path, verify_build, fz)
    plan = RN.plan_calls(scope, build, pilot_record_path)
    fz_hash_arg = (fz or {}).get("hash")                         # `fz` is re-bound to the FZ register below
    anchor_path = (fz or {}).get("anchor_path") if scope == "main" else None
    fz = RN.jl(RN.FZ_PATH)["register"]
    pil = fz["row18_pilot"]
    limit_usd = fz.get("operational_cost_limit_usd")
    st0 = inspect_run(run_dir, build, scope, plan)
    start = st0["start"]
    assumed = (start.get("authorization") or {}).get("input_tokens_per_call_assumed")
    pin, pout = RN.prices_for(settings)
    worst = RN.hard_cap_cost_usd(1, assumed, p_in=pin, p_out=pout) if isinstance(assumed, int) and not isinstance(assumed, bool) and assumed > 0 else None
    st = RN.RunState(worst, (pin, pout))
    for r in st0["records"]:
        st.add(r, SC.classify(r))
    why = []
    same = [
        (start.get("scope") == scope, "scope differs from the provenance entry"),
        (start.get("build_manifest_sha256") == RN.file_sha(os.path.join(build, "manifest.json")), "build manifest differs from the provenance entry"),
        (start.get("fz_file_sha256") == RN.file_sha(RN.FZ_PATH), "FZ-v1 file differs from the provenance entry"),
        (start.get("evidence_sha256") == RN.file_sha(evidence_path), "evidence file differs from the provenance entry"),
        ((start.get("authorization") or {}).get("sha256") == RN.file_sha(auth_path), "authorization file differs from the provenance entry"),
        (start.get("pilot_record_sha256") == (RN.file_sha(pilot_record_path) if pilot_record_path else None), "pilot decision record differs from the provenance entry"),
        ((start.get("decision_anchor") or {}).get("sha256") == (RN.file_sha(anchor_path) if anchor_path else None), "decision record anchor evidence differs from the provenance entry"),
        (start.get("settings") == settings, "settings differ from the provenance entry"),
        (start.get("planned_calls") == len(plan), "number of planned calls differs from the provenance entry"),
        (start.get("call_list_sha256") == RN.sha("\n".join(c["call_key"] for c in plan).encode("ascii")), "call list differs from the provenance entry"),
        (start.get("provider") == type(provider).__name__, "provider class differs from the provenance entry"),
        ((start.get("code_sha256") or {}).get("s10_scoring.py") == RN.code_hashes().get("s10_scoring.py"),
         "the scoring code (s10_scoring.py) differs from the one named in the provenance entry: a resume never mixes two classification versions"),
        (worst is not None, "the provenance entry has no usable input-token assumption"),
    ]
    why += [m for ok, m in same if not ok]
    why += RN.preflight_settings(settings) + (list(provider.preflight(settings)) if hasattr(provider, "preflight") else [])
    records = st0["records"]
    marker, summary = st0["marker"], st0["summary"]
    # A halt is a state of the persisted records, not only a file: if the halt rule holds although the marker was never written, the run is still halted.
    halt_holds = scope == "main" and RN.D.main_run_halt(st.consec, st.done, st.sf_total, pil["c_stop"], pil["calls_min_main"], pil["theta_main"])
    halted_state = marker == "RUN_INCOMPLETE" or halt_holds
    pd_cause = pd_trailing = None
    if halted_state and not (RATIFIED_TRANSPORT_ERROR_CLASSES & TRANSPORT_ERROR_CLASSES):
        why.append("resume after a halt is closed: the HTTP status allow-list is empty (no status qualifies) and no exception class is ratified for resume "
                   "(decision records 12.40 row 2 and 12.42)")
    if not halted_state and marker != "RUN_COMPLETE" and PROCESS_DEATH_CONTRACT_ENFORCED:
        pd_why, pd_cause, pd_trailing = _process_death_contract(st0, marker)
        why += pd_why
    h_why, h_sha = _check_h1_h4(st0, h1_h4_path)
    why += h_why
    if marker == "RUN_COMPLETE":
        why.append("the run is COMPLETE: there is nothing to resume")
    elif marker == "RUN_INCOMPLETE":
        halt = (summary or {}).get("halt") or ""
        if not halt.startswith("halt rule 13.8"):
            why.append(f"the run was stopped by {halt or 'an unknown cause'!r}: only a halt by rule 13.8 that is transport-only can be resumed")
        chain, led = st0["chain"], st0["ledger"]
        mh = summary.get("ledger_final_hash")
        # the marker may predate trailing resume entries when a previous resume died between its ledger entry and moving the marker
        marker_fits = mh == st0["head"] or (mh in chain and all(e.get("event") == "resume" for e in led[chain.index(mh) + 1:]))
        if not marker_fits or summary.get("recorded_calls") != len(records):
            why.append("the status marker does not match the ledger")
    if halted_state:
        bad = [r["call_index"] for r in records if SC.classify(r)["outcome"] == "SF" and not is_transport_only(r)]
        if bad:
            why.append(f"calls {bad[:5]} are structural failures that are not transport-only (allow-list TRANSPORT_ERROR_CLASSES): a halt after them is not resumable here")
    if why:
        raise ResumeRefused(why)
    ra = _parse_json(RN.file_bytes(resume_auth_path)) if os.path.exists(resume_auth_path) else None
    if ra is None:
        raise ResumeRefused(["resume authorization file missing or not a JSON object"])
    remaining = len(plan) - len(records)
    basis = ra.get("resume_basis")
    want_basis = "transport_halt" if halted_state else "process_interruption"
    for ok, m in [
        (ra.get("authorized_by") == "owner", "resume authorization is not written by the owner"),
        (ra.get("scope") == scope, "resume authorization names another scope"),
        (ra.get("run_id") == start.get("run_id"), "resume authorization names another run"),
        (ra.get("ledger_head_sha256") == st0["head"], "resume authorization does not name the current ledger head (the run changed after it was written)"),
        (basis in BASES, "resume_basis must be 'transport_halt' or 'process_interruption'"),
        (basis == want_basis, f"the run state requires resume_basis {want_basis!r}"),
        (basis != "process_interruption" or not PROCESS_DEATH_CONTRACT_ENFORCED
         or (ra.get("failure_cause") == pd_cause and isinstance(ra.get("trailing_transport_failures"), int) and not isinstance(ra.get("trailing_transport_failures"), bool)
             and ra.get("trailing_transport_failures") == pd_trailing),
         "the authorization must name the failure cause (failure_cause) and the count of trailing transport failures (trailing_transport_failures) found in the persisted records"),
        (ra.get("remaining_calls") == remaining and isinstance(ra.get("remaining_calls"), int) and not isinstance(ra.get("remaining_calls"), bool),
         f"resume authorization must state remaining_calls = {remaining}"),
        (ra.get("cost_ceiling_usd") == limit_usd, "resume authorization must state the same cost ceiling as the register"),
    ]:
        if not ok:
            why.append(m)
    limit = (start.get("authorization") or {}).get("max_input_characters")
    if RN._valid_limit(limit):
        why += RN.check_input_sizes([(c["call_key"], RN.read_prompt(build, scope, c["template_id"], c["condition"])) for c in plan[len(records):]], limit)
    else:
        why.append("the provenance entry has no valid input-size limit")
    if limit_usd is not None and st.cost_conservative_usd() >= F(limit_usd):
        why.append("the observed cost of the persisted calls (unknown usage at worst case) already reaches the OD-10 limit")
    if why:
        raise ResumeRefused(why)
    return {"scope": scope, "settings": settings, "plan": plan, "pil": pil, "limit_usd": limit_usd, "inspected": st0, "state": st, "resume_auth": ra,
            "resume_auth_sha256": RN.file_sha(resume_auth_path), "remaining": remaining, "worst": worst, "h1_h4_sha256": h_sha, "fz_hash": fz_hash_arg}


def resume(run_dir, build, provider, *, evidence_path, auth_path, resume_auth_path, pilot_record_path=None, sleeper=time.sleep, clock=None, verify_build=True,
           timer=time.monotonic, h1_h4_path=None, fz=None):
    if not os.path.isdir(run_dir):
        raise ResumeRefused(["the run folder does not exist"])
    try:
        lock = RN.RunLock(run_dir)
        lock.__enter__()
    except RN.RunLockBusy as e:
        raise ResumeRefused([str(e)])
    try:
        return _resume_locked(run_dir, build, provider, evidence_path, auth_path, resume_auth_path, pilot_record_path, sleeper, clock, verify_build, timer, h1_h4_path, fz)
    finally:
        lock.__exit__(None, None, None)


def _resume_locked(run_dir, build, provider, evidence_path, auth_path, resume_auth_path, pilot_record_path, sleeper, clock, verify_build, timer, h1_h4_path=None, fz=None):
    ctx = prepare_resume(run_dir, build, provider, evidence_path=evidence_path, auth_path=auth_path, resume_auth_path=resume_auth_path,
                         pilot_record_path=pilot_record_path, verify_build=verify_build, h1_h4_path=h1_h4_path, fz=fz)
    clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))
    scope, plan, st, ins = ctx["scope"], ctx["plan"], ctx["state"], ctx["inspected"]
    start, pending = ins["start"], ins["pending"]
    marker_path = os.path.join(run_dir, ins["marker"]) if ins["marker"] else None
    prior_marker_sha = RN.file_sha(marker_path) if marker_path else None
    with open(os.path.join(run_dir, "ledger.jsonl"), "ab") as led, open(os.path.join(run_dir, "calls.jsonl"), "ab") as cf:
        w = RN.RunWriter(run_dir, led, cf, ins["head"])
        entry = {"event": "resume", "call_index": len(ins["records"]) + 1, "utc": clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "resume_number": len(ins["resumes"]) + 1, "resume_basis": ctx["resume_auth"]["resume_basis"],
                 "resume_authorization_sha256": ctx["resume_auth_sha256"], "authorized_by": "owner", "h1_h4_evidence_sha256": ctx["h1_h4_sha256"],
                 "previous_ledger_head": ins["head"],
                 "prior_marker": ins["marker"], "prior_marker_sha256": prior_marker_sha,
                 "persisted_calls": len(ins["records"]), "remaining_calls": ctx["remaining"],
                 "counters_carried": {"consecutive_sf": st.consec, "sf_total": st.sf_total, "completed": st.done},
                 "reconciles_call": pending["index"] if pending else None,
                 "code_sha256": RN.code_hashes(), "provider": type(provider).__name__}
        w.ledger(entry)                                                  # the resume is on the chain BEFORE anything else changes
        if marker_path:
            seg = os.path.join(run_dir, "segments")
            os.makedirs(seg, exist_ok=True)
            n = 1
            while os.path.exists(os.path.join(seg, f"{ins['marker']}.segment{n}")):
                n += 1                                                                                          # never overwrite an existing file
            os.replace(marker_path, os.path.join(seg, f"{ins['marker']}.segment{n}"))                          # moved, never deleted
        if pending:
            missing = [n for n, f in (("raw_entry", pending["need_raw_entry"]), ("calls_line", pending["need_calls_line"]), ("classified_entry", pending["need_classified"])) if f]
            w.ledger({"event": "reconcile", "call_index": pending["index"], "missing": missing,
                      "reason": "raw record persisted; ledger or calls entries were not written before the interruption"})
            if pending["need_raw_entry"]:
                w.ledger({"event": "raw", "call_index": pending["index"], "raw_sha256": RN.sha(pending["raw_bytes"])})
            if pending["need_calls_line"]:
                w.append_call(pending["raw_bytes"])
            cl = SC.classify(pending["rec"])
            w.ledger({"event": "classified", "call_index": pending["index"], "outcome": cl["outcome"], "flags": cl["flags"]})
        if ctx["remaining"] > 0:
            sleeper(ctx["settings"]["delay_between_calls_s"])
        halted = _drive(w, scope, build, plan, len(ins["records"]), provider, ctx, st, sleeper, clock, timer)
    resumes = [{k: v for k, v in e.items()} for e in ins["resumes"]] + [entry]
    return RN.finish_run(w, scope, build, plan, st, halted, ctx["settings"], start.get("input_size_check"), start.get("run_id"), resumes, ctx.get("fz_hash"))


def _drive(w, scope, build, plan, start_pos, provider, ctx, st, sleeper, clock, timer):
    return RN.drive(w, scope, build, plan, start_pos, provider, ctx["settings"], st, ctx["pil"], ctx["limit_usd"], sleeper, clock, timer, ctx["worst"])


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="s10_resume.py")
    ap.add_argument("--run", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--authorization", required=True)
    ap.add_argument("--resume-authorization", required=True)
    ap.add_argument("--pilot-record")
    ap.add_argument("--fz-index", help="the FZ index file (E-1)")
    ap.add_argument("--fz-hash", help="the recorded FZ hash (E-1)")
    ap.add_argument("--fz-manifest", help="the FZ manifest in use; REQUIRED when FZ index enforcement is on; it must be listed in the FZ index")
    ap.add_argument("--h1-h4-evidence", help="owner-signed H1-H4 evidence file; needed only if the driving code changed since the last segment")
    ap.add_argument("--decision-anchor", help="anchor evidence of the decision record (MD-09), required for the main scope when enforcement is on")
    ap.add_argument("--live", action="store_true", help="use the real provider; without it only the resume checks run and nothing is called")
    a = ap.parse_args(argv)
    if a.fz_manifest:
        RN.FZ_PATH = os.path.abspath(a.fz_manifest)
    elif RN.FZ_INDEX_ENFORCED:
        print("GATE CLOSED:", "the FZ manifest in use is not named (--fz-manifest is required, E-1)", sep="\n  - ")
        return 2
    prov = RN.make_provider(RN.jl(RN.FZ_PATH)["register"]["row17_model_and_settings"])
    kw = dict(evidence_path=a.evidence, auth_path=a.authorization, resume_auth_path=a.resume_authorization, pilot_record_path=a.pilot_record, h1_h4_path=a.h1_h4_evidence,
              fz={"index_path": a.fz_index, "hash": a.fz_hash, "must_list": [os.path.relpath(RN.FZ_PATH, RN.REPO_ROOT).replace("\\", "/")], "anchor_path": a.decision_anchor})
    try:
        ctx = prepare_resume(a.run, a.build, prov, **kw)
    except RN.GateClosed as e:
        print("GATE CLOSED:", *e.reasons, sep="\n  - ")
        return 2
    except ResumeRefused as e:
        print("RESUME REFUSED (nothing was changed):", *e.reasons, sep="\n  - ")
        return 2
    if not a.live:
        print(f"resume checks passed: {len(ctx['inspected']['records'])} calls persisted, {ctx['remaining']} remaining; --live not given: nothing was run")
        return 0
    summary = resume(a.run, a.build, prov, **kw)
    print(summary["status"], summary["recorded_calls"], "of", summary["planned_calls"])
    return 0 if summary["status"] == "RUN_COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
