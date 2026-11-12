"""Pilot / main-run harness (specification 10, 13.2, 13.3, 13.6, 13.8, 21.1 steps 10-12) -- decision record 12.30. NOT RUN.

Gates (all must hold, otherwise GateClosed and nothing is created): the published build verifies (s8_verify) and Implementation B
agrees; a Step 9 evidence file proves the human blind check (completion date, last viewing date, delay >= FZ row 19 = 7 days,
worksheet hash, adjudication) and is not self-contradictory; an owner authorization file names the scope (pilot_round_N or main),
the exact number of calls, the cost ceiling and the source of the input-size figure, and the recomputed hard-cap cost stays below the
OD-10 limit; for the main run a final pilot decision record with final_decision PASS exists. The runner never creates evidence or
authorization files, never reads the registry's expected states for any decision, and prints no response text.

Per call: intent entry (write-ahead, hash-chained) -> provider call (retries 0, timeout 180 s, concurrency 1, delay 1 s) -> raw
record stored BEFORE any parsing -> classification (s10_scoring). Main run: halt per 13.8 (s7_decision.main_run_halt); a halted or
incomplete run is marked INCOMPLETE and nothing is replaced. The Gemini provider below follows the public REST shape and is
UNVERIFIED against the live API (never exercised); tests use MockProvider only.

Ledger format 2 (2026-10-14, local change, nothing run): the first ledger entry is a provenance entry (event "start", call_index 0) written BEFORE the
first provider call; it holds no secret. Resume after an interruption is in s10_resume.py (fail-closed; see its docstring and the open points of MD-08).
An observed-cost stop at the OD-10 limit halts the run (RUN_INCOMPLETE); with the hard-cap bound checked at the gate it can only fire if the provider
returns more billed tokens than the bound assumes.
"""
import datetime
import hashlib
import json
import os
import sys
import time
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.normpath(os.path.join(HERE, ".."))
for d in (HERE, os.path.join(RD, "step7"), os.path.join(RD, "step8"), os.path.join(RD, "step9")):
    sys.path.insert(0, d)
sys.dont_write_bytecode = True
import impl_b  # noqa: E402
import s10_scoring as SC  # noqa: E402
import s7_decision as D  # noqa: E402
import s8_verify  # noqa: E402

FZ_PATH = os.path.join(RD, "PARAMETER_FREEZE_MANIFEST_FZ_v1.json")
EXPECTED_SETTINGS = {"model_id": "gemini-3.5-flash-lite", "thinking_level": "minimal", "max_output_tokens": 8192, "retries": 0,
                     "timeout_s": 180, "delay_between_calls_s": 1, "concurrency": 1}          # the settings of FZ-v1 (Gemini)
# FZ-v2 (decision record 12.64): local Ollama on the loopback interface. The digest and the template hash are those of the instance verified on 2026-10-16.
OLLAMA_EXPECTED = {"provider": "Ollama", "model_id": "llama3.2:3b", "max_output_tokens": 8192, "retries": 0, "timeout_s": 180, "delay_between_calls_s": 1,
                   "concurrency": 1, "base_url": "http://127.0.0.1:11434", "num_ctx": 16384, "keep_alive": "30m", "stream": False, "ollama_version": "0.6.7",
                   "model_digest": "a80c4f17acd55265feec403c7aef86be0c25983ab279d83f3bcd3abbcb5b8b72",
                   "model_template_sha256": "966de95ca8a62200913e3f8bfbf84c8494536f1b94b49166851e76644e966396"}
# FZ-v3 (owner decisions D13-D18, 2026-10-26): second small-LLM arm. Same settings as FZ-v2 except model ID, digest, template hash, Ollama version and timeout_s (600, D16).
OLLAMA_QWEN_EXPECTED = dict(OLLAMA_EXPECTED, model_id="qwen2.5:3b", timeout_s=600, ollama_version="0.40.11",
                            model_digest="357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b",
                            model_template_sha256="eb4402837c7829a690fa845de4d7f3fd842c2adee476d5341da8a46ea9255175")
EXPECTED_BY_PROVIDER = {"Gemini": EXPECTED_SETTINGS, "Ollama": OLLAMA_EXPECTED}


def expected_settings_for(m):
    """The frozen expected settings of the register's provider (and, for the Qwen arm, of its model); None if the provider is not supported.
    Any other Ollama model is compared with the first arm's settings, so it shows up as a settings mismatch."""
    if m.get("provider") == "Ollama" and m.get("model_id") == OLLAMA_QWEN_EXPECTED["model_id"]:
        return OLLAMA_QWEN_EXPECTED
    return EXPECTED_BY_PROVIDER.get(m.get("provider"))
PROVIDER_PRICES = {"Gemini": (F(30, 100), F(250, 100)), "Ollama": (F(0), F(0))}              # USD per 1M tokens; Ollama: API cost only (hardware and electricity are not counted)


FZ_INDEX_ENFORCED = True                    # E-1 (decision record 12.55): pilot and main refuse without a valid FZ index. Production default ON; only tests of other machinery switch it off.
REPO_ROOT = os.path.dirname(RD)


def fz_index_reasons(fz):
    """Independent verifier of the FZ index (writer: fz_index.py). `fz` = {"index_path", "hash", "root"} (root defaults to the repository root).
    The index must be the canonical JSON {relative path -> SHA-256 of the file bytes}, its own SHA-256 must equal the recorded FZ hash, and every listed file
    must exist and hash to the stored value. Returns the list of reasons (empty when valid). Fail closed on anything missing or malformed."""
    if not isinstance(fz, dict) or not fz.get("index_path") or not fz.get("hash"):
        return ["FZ index and recorded FZ hash missing (E-1): the gate refuses without a valid FZ index"]
    h, ip, root = fz["hash"], fz["index_path"], fz.get("root") or REPO_ROOT
    hexd = set("0123456789abcdef")
    if not isinstance(h, str) or len(h) != 64 or not set(h) <= hexd:
        return ["recorded FZ hash is not 64 lowercase hex characters"]
    if not isinstance(ip, str) or not os.path.isfile(ip):
        return ["FZ index file missing"]
    data = file_bytes(ip)
    if sha(data) != h:
        return ["FZ index SHA-256 differs from the recorded FZ hash"]
    try:
        idx = json.loads(data.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return ["FZ index is not valid ASCII JSON"]
    if not isinstance(idx, dict) or not idx:
        return ["FZ index is not a non-empty object"]
    if (json.dumps(idx, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii") != data:
        return ["FZ index is not in the canonical form"]
    why = []
    for need in (fz.get("must_list") or []):
        if need not in idx:
            why.append(f"the FZ manifest in use ({need}) is not listed in the FZ index")
    for rel, want in sorted(idx.items()):
        parts = rel.replace("\\", "/").split("/")
        if os.path.isabs(rel) or rel.startswith(("/", "\\")) or ":" in rel or any(x in ("", ".", "..") for x in parts) or rel != "/".join(parts):
            why.append(f"FZ index lists an unsafe path: {rel!r}")
            continue
        if not isinstance(want, str) or len(want) != 64 or not set(want) <= hexd:
            why.append(f"FZ index entry for {rel} is not a SHA-256")
            continue
        full = os.path.join(root, *parts)
        if not os.path.isfile(full):
            why.append(f"FZ file missing: {rel}")
        elif sha(file_bytes(full)) != want:
            why.append(f"FZ file changed since the FZ hash: {rel}")
    return ["FZ index check (E-1): " + w for w in why[:10]] + (["FZ index check (E-1): ... and more"] if len(why) > 10 else [])


WAIVER_DECISION_ENTRY = "12.56"             # H1 waiver (decision record 12.56, gate implementation authorized in 12.57)
WAIVER_KEYS = {"waived_by", "decision_entry", "human_blind_check", "build_manifest_sha256"}
DECISION_RECORD_PATH = os.path.join(RD, "STEP3_OWNER_DECISIONS_RECORD_DRAFT.md")


def waiver_reasons(ev, build):
    """Validates the owner's H1 waiver in place of the Step 9 evidence file (G4-G8). It is evidence of a DECISION, never of a performed check: any other key (for
    example a human_blind_check object, dates, coverage, worksheet hash, adjudication) is refused. Returns the list of reasons (empty when valid)."""
    if not isinstance(ev, dict):
        return ["H1 waiver is not a JSON object"]
    why = []
    if set(ev) != WAIVER_KEYS:
        why.append("H1 waiver has missing or unexpected keys (a file that claims a performed blind check is refused)")
    if ev.get("waived_by") != "owner":
        why.append("H1 waiver is not waived_by = owner")
    if ev.get("decision_entry") != WAIVER_DECISION_ENTRY:
        why.append(f"H1 waiver does not cite decision entry {WAIVER_DECISION_ENTRY}")
    if ev.get("human_blind_check") != "NOT PERFORMED":
        why.append("H1 waiver must state human_blind_check = NOT PERFORMED")
    if ev.get("build_manifest_sha256") != file_sha(os.path.join(build, "manifest.json")):
        why.append("H1 waiver refers to another build")
    try:
        text = file_bytes(DECISION_RECORD_PATH).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    if not any(l.startswith(f"### {WAIVER_DECISION_ENTRY} ") for l in text.splitlines()):
        why.append(f"the decision record does not contain entry {WAIVER_DECISION_ENTRY}")
    return why


class GateClosed(Exception):
    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def sha(b):
    return hashlib.sha256(b).hexdigest()


def cj(o):
    return (json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def file_sha(p):
    with open(p, "rb") as f:
        return sha(f.read())


def file_bytes(p):
    with open(p, "rb") as f:
        return f.read()


def jl(p):
    with open(p, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def _utc(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def hard_cap_cost_usd(slots, i_max_tokens, out_tokens=8192, p_in=F(30, 100), p_out=F(250, 100)):
    return (F(slots) * (F(i_max_tokens) * p_in + F(out_tokens) * p_out)) / 10 ** 6


INPUT_LIMIT_NOTE = "operational character limit stated by the owner in the authorization; NOT a token measurement; independent of the cost ceiling"


def _valid_limit(x):
    return isinstance(x, int) and not isinstance(x, bool) and x > 0


def read_prompt(build, scope, template_id, condition):
    kind = "main" if scope == "main" else "pilot"
    with open(os.path.join(build, kind, "prompts", f"{template_id}-{condition}.txt"), "rb") as f:
        return f.read().decode("ascii")


def planned_table_lengths(scope, build):
    """Character lengths of the prompts that the scope would send, from the published prompt table (gate level, before any plan exists)."""
    kind = "main" if scope == "main" else "pilot"
    table = {(e["template_id"], e["condition"]): e["chars"] for e in jl(os.path.join(build, kind, "prompt_table.json"))}
    if scope == "main":
        return list(table.values())
    lines = file_bytes(os.path.join(build, "pilot", "pilot_calls.txt")).decode("ascii").splitlines()
    return [table[(k.split(",")[0], k.split(",")[1])] for k in lines]


def check_input_sizes(prompts_by_key, limit):
    """Reasons (empty = ok) for prompts that are empty or longer than the owner-stated character limit. Never truncates, never prints text."""
    why = []
    for key, text in prompts_by_key:
        if len(text) == 0:
            why.append(f"prompt of {key} is empty")
        elif len(text) > limit:
            why.append(f"prompt of {key} has {len(text)} characters, above the limit {limit}")
    return why


ANCHOR_KEYS = {"anchor_kind", "anchored_at_utc", "decision_record_sha256", "reference"}
ANCHOR_KINDS = ("git_commit", "external_timestamped")


def anchor_reasons(anchor_path, record_path):
    """MD-09 / specification section 10: the main run needs anchor evidence for the decision record before the order is generated.
    The evidence is a small JSON file written by the owner AFTER the record was anchored (own commit, or an external write-once or timestamped store):
    {anchor_kind, anchored_at_utc, decision_record_sha256, reference}. The code checks that the evidence names the SHA-256 of the exact bytes of the record
    it is given, is later than the record's own timestamp, and carries a non-empty reference. It cannot check the external store itself."""
    import re
    if not anchor_path or not os.path.isfile(anchor_path):
        return ["decision record anchor (MD-09): evidence missing, the order is generated only after the record is anchored (section 10)"]
    try:
        a = jl(anchor_path)
    except (ValueError, OSError):
        return ["decision record anchor (MD-09): evidence is not valid JSON"]
    if not isinstance(a, dict) or set(a) != ANCHOR_KEYS:
        return [f"decision record anchor (MD-09): evidence must be an object with exactly the keys {sorted(ANCHOR_KEYS)}"]
    why = []
    if a["anchor_kind"] not in ANCHOR_KINDS:
        why.append(f"anchor_kind must be one of {list(ANCHOR_KINDS)}")
    if not isinstance(a["reference"], str) or len(a["reference"].strip()) < 8:
        why.append("anchor reference (commit ID or timestamp-store reference) missing")
    if not isinstance(a["anchored_at_utc"], str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", a["anchored_at_utc"]) is None:
        why.append("anchored_at_utc must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")
    if a["decision_record_sha256"] != sha(file_bytes(record_path)):
        why.append("anchor evidence names another SHA-256 than the decision record file")
    else:
        try:
            if isinstance(a["anchored_at_utc"], str) and a["anchored_at_utc"] < jl(record_path)["utc_timestamp"]:
                why.append("anchor is earlier than the decision record")
        except (ValueError, OSError, KeyError):
            why.append("decision record has no readable utc_timestamp")
    return ["decision record anchor (MD-09): " + w for w in why]


def check_gates(scope, build, evidence_path, auth_path, pilot_record_path=None, verify_build=True, fz=None):
    """Return the frozen register settings if every gate holds, else raise GateClosed with all reasons."""
    why = []
    fz_index = fz                                  # the parameter (E-1); `fz` below is the FZ-v1 register
    fz = jl(FZ_PATH)["register"]
    m = fz["row17_model_and_settings"]
    expected = expected_settings_for(m)
    if expected is None:
        why.append("model provider of the register is not supported")
    elif {k: m.get(k) for k in expected} != expected:
        why.append("model settings of the register differ from the expected frozen values")
    price_in, price_out = PROVIDER_PRICES.get(m.get("provider"), PROVIDER_PRICES["Gemini"])
    if verify_build:
        if s8_verify.verify(build):
            why.append("Step 8 verifier reports problems")
        if impl_b.verify(build)["failures"]:
            why.append("Implementation B does not agree")
    ev = None
    if not os.path.exists(evidence_path):
        why.append("Step 9 evidence file missing (human blind check not documented)")
    else:
        try:
            ev = jl(evidence_path)
        except ValueError:
            ev = None
            why.append("Step 9 evidence / H1 waiver file is not valid JSON")
        if ev is not None and isinstance(ev, dict) and ("waived_by" in ev or isinstance(ev.get("human_blind_check"), str)):
            why += waiver_reasons(ev, build)                  # H1 waiver (12.56): replaces G4-G8; G9 (same build) is inside waiver_reasons
            ev = None
        elif ev is not None and not isinstance(ev, dict):
            why.append("Step 9 evidence file is not a JSON object")
            ev = None
    if ev is not None and os.path.exists(evidence_path):
        hc = ev.get("human_blind_check") or {}
        try:
            delay = (_utc(hc["completed_utc"]) - _utc(hc["last_registry_view_utc"])).days
            if delay < fz["row19_blind_check"]["minimum_delay_days"]:
                why.append(f"blind-check delay {delay} days < {fz['row19_blind_check']['minimum_delay_days']}")
        except (KeyError, ValueError):
            why.append("blind-check dates missing or malformed")
        if hc.get("coverage_percent") != 100:
            why.append("blind-check coverage is not 100 percent")
        if len(hc.get("worksheet_sha256", "")) != 64:
            why.append("blind-check worksheet hash missing")
        if hc.get("adjudication") not in ("AGREE", "ADJUDICATED_NO_REJECT"):
            why.append("blind-check adjudication is not AGREE / ADJUDICATED_NO_REJECT")
        if ev.get("build_manifest_sha256") != file_sha(os.path.join(build, "manifest.json")):
            why.append("evidence refers to another build")
    if not os.path.exists(auth_path):
        why.append("owner authorization file missing")
    else:
        au = jl(auth_path)
        planned = {"pilot_round_1": 18, "pilot_round_2": 18, "main": fz["row13_C_max_planned_main_call_slots"]}.get(scope)
        if planned is None or au.get("scope") != scope or au.get("max_calls") != planned or au.get("authorized_by") != "owner":
            why.append("authorization does not match the scope and the planned number of calls")
        try:
            cost = hard_cap_cost_usd(planned, au["input_tokens_per_call_assumed"], p_in=price_in, p_out=price_out)
            if cost >= fz["operational_cost_limit_usd"] or au.get("cost_ceiling_usd") != fz["operational_cost_limit_usd"]:
                why.append("hard-cap cost not below the OD-10 limit or ceiling not stated")
        except (KeyError, TypeError):
            why.append("input-size assumption missing in the authorization")
        lim = au.get("max_input_characters")
        if not _valid_limit(lim):
            why.append("authorization lacks a positive integer max_input_characters (owner-stated operational input limit)")
        elif planned is not None and os.path.exists(os.path.join(build, "main" if scope == "main" else "pilot", "prompt_table.json")):
            longest = max(planned_table_lengths(scope, build))
            if longest > lim:
                why.append(f"longest planned prompt has {longest} characters, above max_input_characters {lim}")
    if scope == "main":
        if not pilot_record_path or not os.path.exists(pilot_record_path):
            why.append("final pilot decision record missing")
        else:
            rec = jl(pilot_record_path)
            if rec.get("final_decision") != "PASS":
                why.append("final pilot decision is not PASS")
            if FZ_INDEX_ENFORCED and isinstance(fz_index, dict) and fz_index.get("hash") and rec.get("fz_hash") != fz_index["hash"]:
                why.append("final pilot decision record names another FZ hash than the recorded one (E-1)")
    if FZ_INDEX_ENFORCED:
        why += fz_index_reasons(fz_index)
        if scope == "main" and pilot_record_path and os.path.exists(pilot_record_path):
            why += anchor_reasons((fz_index or {}).get("anchor_path"), pilot_record_path)
    if why:
        raise GateClosed(why)
    return m


def plan_calls(scope, build, pilot_record_path=None):
    """List of call dicts in execution order. Pilot: canonical order, one repetition. Main: the canonical call list shuffled once
    by random.Random(seed).shuffle with the seed of the decision record (10); the list hash must equal the recorded one."""
    if scope.startswith("pilot"):
        with open(os.path.join(build, "pilot", "pilot_calls.txt"), "rb") as f:
            lines = f.read().decode("ascii").split("\n")[:-1]
    else:
        reg = jl(os.path.join(build, "main", "registry.json"))
        fz = jl(FZ_PATH)["register"]
        text = D.call_list([t["id"] for t in reg["templates"]], fz["row12_R"])
        rec = jl(pilot_record_path)
        if sha(text.encode("ascii")) != rec["call_list_sha256"]:
            raise GateClosed(["call list hash differs from the decision record"])
        lines = D.shuffled_order(text, rec["seed"])
    out = []
    for n, ln in enumerate(lines, 1):
        tid, cond, rep = ln.split(",")
        out.append({"call_index": n, "call_key": ln, "template_id": tid, "condition": cond, "repetition": int(rep)})
    return out


class MockProvider:
    """Deterministic scripted provider for tests: script is a callable (call_key, prompt) -> dict."""
    def __init__(self, script):
        self.script, self.calls = script, 0

    def generate(self, prompt, settings, call_key):
        self.calls += 1
        return self.script(call_key, prompt)


REQUIRED_SETTING_TYPES = {"model_id": str, "thinking_level": str, "max_output_tokens": int, "retries": int, "timeout_s": int,
                          "delay_between_calls_s": int, "concurrency": int}
TIMEOUT_SEMANTICS = "per socket operation (urllib); there is NO total deadline per request"


class PreflightFailed(Exception):
    """Local configuration is not usable. Raised BEFORE any output folder, ledger entry, call record or provider call exists, so no pilot round is consumed."""
    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def preflight_settings(settings):
    """Local, network-free validation of the register settings handed to the provider. Returns a list of reasons (empty = usable)."""
    why = []
    for k, t in REQUIRED_SETTING_TYPES.items():
        v = settings.get(k) if isinstance(settings, dict) else None
        if v is None or isinstance(v, bool) or not isinstance(v, t) or (t is str and not v.strip()) or (t is int and k != "retries" and v <= 0):
            why.append(f"setting {k} missing or invalid")
    if isinstance(settings, dict) and settings.get("retries") != 0:
        why.append("retries must be 0")
    if isinstance(settings, dict) and settings.get("concurrency") != 1:
        why.append("concurrency must be 1")
    return why


class GeminiProvider:
    """REST provider. UNVERIFIED against the live API; the key comes from the environment only and is never stored or printed.
    Timeout: urllib's timeout applies to each blocking socket operation, not to the whole request: a response that trickles in
    can exceed timeout_s in total. The frozen text says '180 s' without defining which; this is recorded in every run summary."""
    ENDPOINT_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, model_id, env="GEMINI_API_KEY"):
        self.model_id, self.env = model_id, env

    def preflight(self, settings):
        """Local checks only (no network, no secret printed). Reasons name the environment variable, never its value."""
        why = []
        if not (os.environ.get(self.env) or "").strip():
            why.append(f"API key missing or empty in environment variable {self.env}")
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            why.append("provider model ID empty")
        elif isinstance(settings, dict) and settings.get("model_id") != self.model_id:
            why.append("provider model ID differs from the register model ID")
        if not self.ENDPOINT_BASE.startswith("https://generativelanguage.googleapis.com/"):
            why.append("endpoint is not the expected https host")
        return why

    def generate(self, prompt, settings, call_key):
        import urllib.error
        import urllib.request
        key = os.environ.get(self.env)
        if not key:
            return {"http_status": None, "text": "", "error": "no API key in the environment"}
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": settings["max_output_tokens"], "thinkingConfig": {"thinkingLevel": settings["thinking_level"]}}}
        req = urllib.request.Request(f"{self.ENDPOINT_BASE}/{self.model_id}:generateContent",
                                     data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=settings["timeout_s"]) as r:
                data = json.loads(r.read().decode("utf-8"))
                status = r.status
        except urllib.error.HTTPError as e:
            return {"http_status": e.code, "text": "", "error": f"HTTP {e.code}"}
        except Exception as e:                                       # timeout or transport failure (SF1); message not stored beyond the class
            return {"http_status": None, "text": "", "error": type(e).__name__, "error_category": error_category(e)}
        cands = data.get("candidates") or []
        text = "".join(p.get("text", "") for p in (cands[0].get("content", {}).get("parts", []) if cands else []))
        return {"http_status": status, "text": text, "error": None, "finish_reason": cands[0].get("finishReason") if cands else None,
                "model_returned": data.get("modelVersion"), "usage": data.get("usageMetadata"),
                "block_reason": (data.get("promptFeedback") or {}).get("blockReason")}


class OllamaProvider:
    """Local Ollama server, loopback only (decision record 12.64). No credential, no environment variable. Mapping checked on Ollama 0.6.7 with the evaluation 12.62:
    `response` -> text; `done_reason` stop -> STOP, length -> MAX_TOKENS (observed; not listed in the documentation read), any other value -> an unlisted OLLAMA_* name
    (never a block name); `model` -> model_returned; `prompt_eval_count` and `eval_count` -> usage promptTokenCount, candidatesTokenCount, totalTokenCount.
    preflight() makes only loopback GET/POST requests that start no generation."""
    LOOPBACK = ("127.0.0.1", "localhost", "::1")

    def __init__(self, model_id, base_url="http://127.0.0.1:11434"):
        self.model_id, self.base_url = model_id, base_url

    def _base(self):
        import urllib.parse
        u = urllib.parse.urlsplit(self.base_url or "")
        if u.scheme != "http" or u.username or u.password or (u.hostname or "") not in self.LOOPBACK or u.path not in ("", "/") or u.query or u.fragment:
            raise ValueError("the Ollama URL must be plain http on 127.0.0.1, localhost or ::1 without credentials, path or query")
        host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
        return f"http://{host}:{u.port or 11434}"

    def _call(self, method, path, body=None, timeout=30):
        import urllib.request
        req = urllib.request.Request(self._base() + path, data=json.dumps(body).encode("utf-8") if body is not None else None,
                                     headers={"Content-Type": "application/json"} if body is not None else {}, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def preflight(self, settings):
        """Local checks plus loopback requests that start no generation. The reasons never contain a response or a prompt."""
        try:
            self._base()
        except ValueError as e:
            return [str(e)]
        why = []
        if not isinstance(settings, dict):
            return ["settings missing"]
        if settings.get("provider") != "Ollama":
            why.append("the register provider is not Ollama")
        if settings.get("model_id") != self.model_id:
            why.append("provider model ID differs from the register model ID")
        if settings.get("base_url") != self.base_url:
            why.append("provider URL differs from the register URL")
        try:
            ver = self._call("GET", "/api/version").get("version")
            if ver != settings.get("ollama_version"):
                why.append("installed Ollama version differs from the frozen version")
            tags = self._call("GET", "/api/tags").get("models") or []
            dig = [m.get("digest") for m in tags if isinstance(m, dict) and self.model_id in (m.get("name"), m.get("model"))]
            if dig != [settings.get("model_digest")]:
                why.append("installed model digest differs from the frozen digest (or the model is not installed)")
            show = self._call("POST", "/api/show", {"model": self.model_id})
            tpl = show.get("template") if isinstance(show, dict) else None
            if not isinstance(tpl, str) or sha(tpl.encode("utf-8")) != settings.get("model_template_sha256"):
                why.append("model template differs from the frozen template hash")
        except Exception as e:                                         # server not running, refused, timeout, invalid answer: the class name only
            why.append(f"the local Ollama server could not be verified ({type(e).__name__})")
        return why

    @staticmethod
    def finish(done_reason, done):
        raw = done_reason if done is True else (done_reason or "not_done")
        if raw is None:
            return None
        if raw == "stop":
            return "STOP"
        if raw == "length":
            return "MAX_TOKENS"
        name = "".join(ch for ch in str(raw).upper() if ch.isalnum() or ch == "_")[:24]
        return "OLLAMA_" + (name or "EMPTY")

    def generate(self, prompt, settings, call_key):
        import urllib.error
        body = {"model": self.model_id, "prompt": prompt, "stream": False, "keep_alive": settings["keep_alive"],
                "options": {"num_ctx": settings["num_ctx"], "num_predict": settings["max_output_tokens"]}}
        try:
            self._base()
            import urllib.request
            req = urllib.request.Request(self._base() + "/api/generate", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=settings["timeout_s"]) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            return {"http_status": e.code, "text": "", "error": f"HTTP {e.code}"}
        except Exception as e:                                         # connection refused, timeout: the class name only
            return {"http_status": None, "text": "", "error": type(e).__name__, "error_category": error_category(e)}
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            return {"http_status": status, "text": "", "error": "InvalidJSON"}
        if not isinstance(data, dict):
            return {"http_status": status, "text": "", "error": "InvalidJSON"}
        pe, ev = data.get("prompt_eval_count"), data.get("eval_count")
        isint = lambda v: isinstance(v, int) and not isinstance(v, bool)    # noqa: E731
        usage = None
        if isint(pe) and isint(ev):
            usage = {"promptTokenCount": pe, "candidatesTokenCount": ev, "totalTokenCount": pe + ev}
        elif isint(pe) or isint(ev):
            usage = {k: v for k, v in (("promptTokenCount", pe), ("candidatesTokenCount", ev)) if isint(v)}
        return {"http_status": status, "text": data.get("response") if isinstance(data.get("response"), str) else "", "error": None,
                "finish_reason": self.finish(data.get("done_reason") if isinstance(data.get("done_reason"), str) else None, data.get("done")),
                "model_returned": data.get("model") if isinstance(data.get("model"), str) else None, "usage": usage, "block_reason": None}


def make_provider(settings):
    """The provider named by the register settings (row 17)."""
    name = (settings or {}).get("provider") or "Gemini"            # settings without a provider key are the legacy FZ-v1 settings; the gate refuses a register without a supported provider
    if name == "Gemini":
        return GeminiProvider(settings["model_id"])
    if name == "Ollama":
        return OllamaProvider(settings["model_id"], settings.get("base_url"))
    raise ValueError("unsupported provider")


def prices_for(settings):
    return PROVIDER_PRICES.get((settings or {}).get("provider"), PROVIDER_PRICES["Gemini"])


LEDGER_FORMAT = 2

# Owner decision 12.43 (X-3, option O3): a COARSE diagnostic category from a closed enum, derived from the TYPE of the exception (or of URLError.reason), never from its text.
# It is evidence, not proof: on the Windows development host a refused connection presented as reason TimeoutError, so TIMEOUT can hide a refusal. It is recorded in the raw
# record only for a failure that arrives as an exception, and it gives NO eligibility for resume (the exception class allow-list is separate and empty).
ERROR_CATEGORIES = ("DNS", "REFUSED", "TIMEOUT", "TLS_VERIFY", "TLS_OTHER", "LOCAL_FILE", "OTHER_OS", "OTHER")


def error_category(e):
    """Never reads str(e), e.args, a filename or any other text: only isinstance checks. Unknown or non-OS exceptions (JSON errors, http.client errors that are not OSError
    such as IncompleteRead, a URLError whose reason is not an exception) fall back to OTHER."""
    try:
        import socket
        import ssl
        import urllib.error
        if isinstance(e, urllib.error.HTTPError):
            return "OTHER"                                              # an HTTP response is not a transport category (HTTP errors keep http_status / error "HTTP <code>")
        r = e
        if isinstance(e, urllib.error.URLError):
            r = e.reason
        if isinstance(r, ssl.SSLCertVerificationError):
            return "TLS_VERIFY"
        if isinstance(r, ssl.SSLError):
            return "TLS_OTHER"
        if isinstance(r, socket.gaierror):
            return "DNS"
        if isinstance(r, ConnectionRefusedError):
            return "REFUSED"
        if isinstance(r, TimeoutError):
            return "TIMEOUT"
        if isinstance(r, (FileNotFoundError, PermissionError, NotADirectoryError, IsADirectoryError)):
            return "LOCAL_FILE"
        if isinstance(r, OSError):
            return "OTHER_OS"
    except Exception:
        pass
    return "OTHER"
PRICE_IN_USD_PER_M, PRICE_OUT_USD_PER_M = F(30, 100), F(250, 100)   # as in hard_cap_cost_usd; constants in the code, to be re-read from the provider on the run date
COST_STOP_HALT = "cost stop: observed cost reached the OD-10 limit"
COST_STOP_PRE = "cost stop: the worst case of the next call could cross the OD-10 limit"


class RunLockBusy(Exception):
    pass


class RunLock:
    """Exclusive, operating-system lock on <run folder>/run.lock for the whole life of a run or a resume. It is released by the system when the process dies,
    so a killed run leaves no stale lock; a second run or resume on a live folder is refused instead of appending to the same ledger."""
    def __init__(self, folder):
        self.path, self.fh = os.path.join(folder, "run.lock"), None

    def __enter__(self):
        self.fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            self.fh = None
            raise RunLockBusy("another run or resume holds this run folder (run.lock): in progress")
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                self.fh.close()
        return False


def code_hashes():
    """SHA-256 of the Step 10 code that drives a run (recorded in the provenance entry; a later resume records its own, it does not have to equal)."""
    names = ("s10_runner.py", "s10_scoring.py", "s10_integrity.py", "s10_resume.py")
    return {n: file_sha(os.path.join(HERE, n)) for n in names if os.path.exists(os.path.join(HERE, n))}


def anchor_summary(anchor_path):
    """The identity of the anchor evidence for the run manifest: file hash plus the four evidence fields; None when there is no anchor (pilot scopes). The gate has already validated the file."""
    if not anchor_path:
        return None
    a = jl(anchor_path)
    return {"sha256": file_sha(anchor_path), **{k: a[k] for k in sorted(ANCHOR_KEYS)}}


def build_provenance(scope, build, plan, settings, provider, evidence_path, auth_path, pilot_record_path, input_check, clock, anchor_path=None):
    """The first ledger entry of a run (before any provider call). Holds no secret: no environment variable, key or response text.
    It names what the run was made from, so that a stopped run can still be identified and a later resume can be refused on any mismatch."""
    au = jl(auth_path)
    utc = clock().strftime("%Y-%m-%dT%H:%M:%SZ")
    prov = {
        "event": "start", "call_index": 0, "ledger_format": LEDGER_FORMAT, "scope": scope, "utc": utc,
        "build_manifest_sha256": file_sha(os.path.join(build, "manifest.json")),
        "fz_file_sha256": file_sha(FZ_PATH),
        "evidence_sha256": file_sha(evidence_path),
        "authorization": {"sha256": file_sha(auth_path), "authorized_by": au.get("authorized_by"), "scope": au.get("scope"),
                          "max_calls": au.get("max_calls"), "cost_ceiling_usd": au.get("cost_ceiling_usd"),
                          "input_tokens_per_call_assumed": au.get("input_tokens_per_call_assumed"),
                          "max_input_characters": au.get("max_input_characters")},
        "pilot_record_sha256": file_sha(pilot_record_path) if pilot_record_path else None,
        "decision_anchor": anchor_summary(anchor_path),            # MD-09 / section 10: the anchor ID is part of every main-run manifest (None outside the main scope)
        "planned_calls": len(plan),
        "call_list_sha256": sha("\n".join(c["call_key"] for c in plan).encode("ascii")),
        "settings": settings, "timeout_semantics": TIMEOUT_SEMANTICS, "input_size_check": input_check,
        "provider": type(provider).__name__, "code_sha256": code_hashes(), "python": sys.version.split()[0],
    }
    prov["run_id"] = sha(cj({k: prov[k] for k in ("scope", "utc", "build_manifest_sha256", "call_list_sha256")}) + prov["authorization"]["sha256"].encode("ascii"))[:16]
    return prov


class RunWriter:
    """Append-only writer of one run folder: the hash-chained ledger, calls.jsonl and raw/. Shared by run() and by s10_resume.resume()."""
    def __init__(self, out_dir, led, calls, prev):
        self.out_dir, self.led, self.calls, self.prev = out_dir, led, calls, prev

    def ledger(self, entry):
        entry = dict(entry, prev=self.prev)
        b = cj(entry)
        self.prev = sha(b)
        self.led.write(b)
        self.led.flush()
        os.fsync(self.led.fileno())                                   # forced to disk before the next step (durability)

    def append_call(self, raw):
        self.calls.write(raw)
        self.calls.flush()
        os.fsync(self.calls.fileno())


class RunState:
    """Counters for the halt rule and the cost stop. Rebuilt from the persisted records on a resume: nothing is reset."""
    def __init__(self, worst_call_usd=None, prices=None):
        self.prices = prices or (PRICE_IN_USD_PER_M, PRICE_OUT_USD_PER_M)
        self.consec = self.sf_total = self.done = 0
        self.records = []
        self.tokens_in = self.tokens_out = 0
        self.unknown_calls = 0                                        # calls whose usage is missing or incomplete: charged at worst case
        self.worst_call_usd = worst_call_usd

    def add(self, rec, cl):
        self.records.append(rec)
        self.done += 1
        if cl["outcome"] == "SF":
            self.consec += 1
            self.sf_total += 1
        else:
            self.consec = 0
        import s10_integrity as IG                                    # lazy: s10_integrity imports this module at load time
        u = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}
        p = IG._count(u.get("promptTokenCount"))
        o, _ = IG.output_tokens(rec.get("usage"))
        if p is None or o is None:
            self.unknown_calls += 1
        self.tokens_in += p or 0
        self.tokens_out += o or 0                                     # the known part only; unknown calls are added at worst case by cost_conservative_usd

    def observed_cost_usd(self):
        """Lower bound: only the tokens that the provider reported."""
        return (F(self.tokens_in) * self.prices[0] + F(self.tokens_out) * self.prices[1]) / 10 ** 6

    def cost_conservative_usd(self, worst_call_usd=None):
        """Reported tokens plus the worst case (assumed input tokens, full output allowance) for every call without usable usage."""
        w = self.worst_call_usd if worst_call_usd is None else worst_call_usd
        return self.observed_cost_usd() + (F(self.unknown_calls) * F(w) if w is not None else 0)


def execute_call(w, c, prompt, provider, settings, clock, timer):
    """One call: intent (write-ahead) -> provider -> raw record stored BEFORE any parsing -> classification. Never retries."""
    w.ledger({"event": "intent", "call_index": c["call_index"], "call_key": c["call_key"]})
    t0 = clock()
    m0 = timer()
    try:
        res = provider.generate(prompt, settings, c["call_key"])
    except Exception as e:
        res = {"http_status": None, "text": "", "error": type(e).__name__, "error_category": error_category(e)}
    measured_ms = int(round((timer() - m0) * 1000))               # wall-clock around the whole provider call; the provider's own figure wins if given
    rec = dict(c, utc_start=t0.strftime("%Y-%m-%dT%H:%M:%SZ"), http_status=res.get("http_status"), error=res.get("error"),
               response_text=res.get("text") or "", finish_reason=res.get("finish_reason"), model_returned=res.get("model_returned"), block_reason=res.get("block_reason"),
               usage=res.get("usage"), latency_ms=res.get("latency_ms") if res.get("latency_ms") is not None else measured_ms, prompt_sha256=sha(prompt.encode("ascii")))
    if res.get("error_category") in ERROR_CATEGORIES:                 # only a failure that arrived as an exception carries it; every other record keeps its bytes
        rec["error_category"] = res["error_category"]
    raw = cj(rec)
    with open(os.path.join(w.out_dir, "raw", f"{c['call_index']:05d}.json"), "xb") as f:
        f.write(raw)                                              # raw record stored before any parsing
        f.flush()
        os.fsync(f.fileno())
    w.ledger({"event": "raw", "call_index": c["call_index"], "raw_sha256": sha(raw)})
    w.append_call(raw)
    cl = SC.classify(rec)
    w.ledger({"event": "classified", "call_index": c["call_index"], "outcome": cl["outcome"], "flags": cl["flags"]})
    return rec, cl


def provider_anomaly(rec, requested_model):
    """MD-06 (12.50 row 1) safe-stop triggers 1, 2 and 4, evaluated on the stored record. Returns a reason text or None.
    Error markers and non-2xx statuses are SF1 (no anomaly); block NAMES are not a trigger here; classification is unchanged."""
    if rec.get("error"):
        return None
    st = rec.get("http_status")
    if st is None:
        return "record without HTTP status and without error marker"
    if not (isinstance(st, int) and 200 <= st < 300):
        return None
    if rec.get("model_returned") != requested_model or not rec.get("model_returned"):
        return "returned model ID missing or not exactly the requested ID"
    if SC.finish_reason_class(rec.get("finish_reason")) == "unlisted":
        return "unlisted finish reason"
    return None


def drive(w, scope, build, plan, start_pos, provider, settings, st, pil, limit_usd, sleeper, clock, timer, worst_call_usd=None):
    """Executes plan[start_pos:] and returns the halt text (None if none). Halt rules: 13.8 (main only) and the OD-10 cost stop (any scope).
    The cost stop is checked BEFORE each call (reported cost + worst cases of unknown calls + the worst case of this call must stay within the limit) and after it."""
    halted = None
    worst = st.worst_call_usd if worst_call_usd is None else worst_call_usd
    for c in plan[start_pos:]:
        if limit_usd is not None and worst is not None and st.cost_conservative_usd(worst) + F(worst) > F(limit_usd):
            halted = COST_STOP_PRE
            break
        prompt = read_prompt(build, scope, c["template_id"], c["condition"])
        rec, cl = execute_call(w, c, prompt, provider, settings, clock, timer)
        st.add(rec, cl)
        why = provider_anomaly(rec, settings.get("model_id"))
        if why:                                                   # MD-06: stored first, then stop; no further call, no resume
            halted = f"provider anomaly: {why}"
            break
        if scope == "main" and D.main_run_halt(st.consec, st.done, st.sf_total, pil["c_stop"], pil["calls_min_main"], pil["theta_main"]):
            halted = f"halt rule 13.8 after call {st.done}"
            break
        if limit_usd is not None and st.cost_conservative_usd(worst) >= F(limit_usd):
            halted = COST_STOP_HALT
            break
        if st.done < len(plan):
            sleeper(settings["delay_between_calls_s"])
    return halted


def finish_run(w, scope, build, plan, st, halted, settings, input_check, run_id, resumes, fz_hash=None):
    status = "RUN_COMPLETE" if st.done == len(plan) and halted is None else "RUN_INCOMPLETE"
    summary = {"scope": scope, "status": status, "halt": halted, "planned_calls": len(plan), "recorded_calls": st.done,
               "ledger_final_hash": w.prev, "build_manifest_sha256": file_sha(os.path.join(build, "manifest.json")),
               "settings": settings, "timeout_semantics": TIMEOUT_SEMANTICS, "input_size_check": input_check,
               "ledger_format": LEDGER_FORMAT, "run_id": run_id, "resumes": resumes,
               "observed_cost_usd_lower_bound": float(st.observed_cost_usd()), "observed_cost_usd_conservative": float(st.cost_conservative_usd())}
    if fz_hash is not None:
        summary["fz_hash"] = fz_hash                                # E-1: the recorded FZ hash is part of every run manifest
    if scope.startswith("pilot"):
        summary["pilot_counts"] = SC.pilot_counts(st.records)       # counts only (13.7)
    with open(os.path.join(w.out_dir, status), "xb") as f:
        f.write(cj(summary))
    return summary


def run(scope, build, out_dir, provider, *, evidence_path, auth_path, pilot_record_path=None, sleeper=time.sleep, clock=None, verify_build=True,
        timer=time.monotonic, fz=None):
    settings = check_gates(scope, build, evidence_path, auth_path, pilot_record_path, verify_build, fz)
    fz_hash = (fz or {}).get("hash")                          # `fz` is re-bound to the FZ-v1 register below
    anchor_path = (fz or {}).get("anchor_path") if scope == "main" else None
    plan = plan_calls(scope, build, pilot_record_path)
    why = preflight_settings(settings) + (list(provider.preflight(settings)) if hasattr(provider, "preflight") else [])
    limit = jl(auth_path).get("max_input_characters")
    input_check = None
    if not _valid_limit(limit):
        why.append("authorization lacks max_input_characters")
    else:
        lens = [(c["call_key"], read_prompt(build, scope, c["template_id"], c["condition"])) for c in plan]
        why += check_input_sizes(lens, limit)
        input_check = {"limit_characters": limit, "checked_prompts": len(lens), "max_observed_characters": max((len(t) for _, t in lens), default=0),
                       "basis": INPUT_LIMIT_NOTE}
    if why:
        raise PreflightFailed(why)                                    # nothing exists yet: no folder, no ledger entry, no provider call
    fz = jl(FZ_PATH)["register"]
    pil = fz["row18_pilot"]
    os.makedirs(os.path.join(out_dir, "raw"))                         # FileExistsError if the output exists: nothing is overwritten
    clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))
    pin, pout = prices_for(settings)
    st = RunState(hard_cap_cost_usd(1, jl(auth_path)["input_tokens_per_call_assumed"], p_in=pin, p_out=pout), (pin, pout))
    with RunLock(out_dir):
        with open(os.path.join(out_dir, "ledger.jsonl"), "xb") as led, open(os.path.join(out_dir, "calls.jsonl"), "xb") as calls:
            w = RunWriter(out_dir, led, calls, "0" * 64)
            start = build_provenance(scope, build, plan, settings, provider, evidence_path, auth_path, pilot_record_path, input_check, clock, anchor_path)
            w.ledger(start)                                           # provenance BEFORE the first provider call
            halted = drive(w, scope, build, plan, 0, provider, settings, st, pil, fz.get("operational_cost_limit_usd"), sleeper, clock, timer)
        return finish_run(w, scope, build, plan, st, halted, settings, input_check, start["run_id"], [], fz_hash)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="s10_runner.py")
    ap.add_argument("--scope", required=True, choices=["pilot_round_1", "pilot_round_2", "main"])
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--authorization", required=True)
    ap.add_argument("--pilot-record")
    ap.add_argument("--fz-index", help="the FZ index file (E-1)")
    ap.add_argument("--fz-hash", help="the recorded FZ hash: SHA-256 of the FZ index file (E-1)")
    ap.add_argument("--fz-manifest", help="the FZ manifest in use (for example PARAMETER_FREEZE_MANIFEST_FZ_v2.json); REQUIRED when FZ index enforcement is on; it must be listed in the FZ index")
    ap.add_argument("--decision-anchor", help="anchor evidence of the decision record (MD-09), required for the main scope when enforcement is on")
    ap.add_argument("--live", action="store_true", help="use the real provider; without it the gates are checked and the run is refused")
    a = ap.parse_args(argv)
    global FZ_PATH
    if a.fz_manifest:
        FZ_PATH = os.path.abspath(a.fz_manifest)
    elif FZ_INDEX_ENFORCED:
        print("GATE CLOSED:\n  - the FZ manifest in use is not named (--fz-manifest is required, E-1)")
        return 2
    fz = {"index_path": a.fz_index, "hash": a.fz_hash, "must_list": [os.path.relpath(FZ_PATH, REPO_ROOT).replace("\\", "/")], "anchor_path": a.decision_anchor}
    try:
        settings = check_gates(a.scope, a.build, a.evidence, a.authorization, a.pilot_record, True, fz)
    except GateClosed as e:
        print("GATE CLOSED:", *e.reasons, sep="\n  - ")
        return 2
    prov = make_provider(settings)
    why = preflight_settings(settings) + prov.preflight(settings)
    if not a.live:
        print("gates open; --live not given: nothing was run")
        print("local preflight (no network):", "OK" if not why else "NOT READY")
        for r in why:
            print("  - " + r)
        return 0
    if why:
        print("PREFLIGHT FAILED (nothing was created, no round consumed):")
        for r in why:
            print("  - " + r)
        return 2
    summary = run(a.scope, a.build, a.out, prov, evidence_path=a.evidence,
                  auth_path=a.authorization, pilot_record_path=a.pilot_record, fz=fz)
    print(summary["status"], summary["recorded_calls"], "of", summary["planned_calls"])
    return 0 if summary["status"] == "RUN_COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
