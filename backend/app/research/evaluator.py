"""
Research evaluator for E1–E7 (per generated plan) and the E5 completeness split.

Offline and deterministic: no network, no randomness, no clock. Inputs are the
deterministic baseline (rule_plan) and one research generation record built from
GeminiGenerationResult. Criteria are recomputed here from the raw parsed output;
the production validator's first error message is never used for scoring.

Score convention: a float in [0, 1] when the criterion is evaluable, and None for
N/A. N/A is never converted to 0 and never to a failure.

Duplicate week numbers: E1–E4 use the first occurrence of a week number as an
implementation safeguard so that the calculation is deterministic. The generation
is still an E5 failure (duplicate_week), so duplicates never make E5 pass.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from app.research import criteria_config as cfg


@dataclass(frozen=True)
class GenerationRecord:
    """One research generation attempt, as returned by generate_gemini_result (no fallback)."""
    profile_id: str
    generation_id: str
    status: str
    raw_output: Any
    raw_text_available: bool
    finish_reason: Optional[str]
    error_category: Optional[str]
    error: Optional[str]


def record_from_generation_result(profile_id: str, generation_id: str, result: Any) -> GenerationRecord:
    """Builds a GenerationRecord from a GeminiGenerationResult without importing the service."""
    return GenerationRecord(
        profile_id=profile_id,
        generation_id=generation_id,
        status=result.status.value,
        raw_output=result.raw_output,
        raw_text_available=result.raw_text is not None,
        finish_reason=result.finish_reason,
        error_category=result.error_category,
        error=result.error,
    )


@dataclass
class CriterionResult:
    criterion: str
    value: Optional[float]
    note: str = ""
    evidence: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def evaluable(self) -> bool:
        return self.value is not None


@dataclass
class GenerationEvaluation:
    profile_id: str
    generation_id: str
    status: str
    output_present: bool
    generation_failure: Optional[str]
    criteria: Dict[str, CriterionResult]
    e5_failure_categories: List[str]
    failure_details: List[Dict[str, Any]]
    criteria_version: str = cfg.CRITERIA_VERSION

    def score(self, criterion: str) -> Optional[float]:
        return self.criteria[criterion].value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "criteria_version": self.criteria_version,
            "profile_id": self.profile_id,
            "generation_id": self.generation_id,
            "status": self.status,
            "output_present": self.output_present,
            "generation_failure": self.generation_failure,
            **{c: self.criteria[c].value for c in ("e1", "e2", "e3", "e4", "e5", "e6", "e7")},
            "e5_failure_categories": list(self.e5_failure_categories),
            "failure_details": list(self.failure_details),
            "criteria": {
                c: {"value": r.value, "note": r.note, "evidence": r.evidence}
                for c, r in self.criteria.items()
            },
        }


# ---------- generic helpers ----------

def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _weeks_of(raw: Any) -> Optional[List[Any]]:
    if not isinstance(raw, dict):
        return None
    weeks = raw.get("weeks")
    return weeks if isinstance(weeks, list) else None


def _week_index(weeks: List[Any]) -> Dict[int, Dict[str, Any]]:
    """Maps week_number to its first dict entry (implementation safeguard; see module docstring)."""
    index: Dict[int, Dict[str, Any]] = {}
    for item in weeks:
        if isinstance(item, dict) and _is_int(item.get("week_number")) and item["week_number"] not in index:
            index[item["week_number"]] = item
    return index


def _day_index(week: Optional[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Maps day name to all workout dicts with that day, so duplicates stay visible."""
    index: Dict[str, List[Dict[str, Any]]] = {}
    if not isinstance(week, dict) or not isinstance(week.get("workouts"), list):
        return index
    for item in week["workouts"]:
        if isinstance(item, dict) and isinstance(item.get("day"), str):
            index.setdefault(item["day"], []).append(item)
    return index


def _norm_structured(value: str) -> str:
    """Structured-field normalization used for pace and intensity zone: trim, then exact."""
    return value.strip()


def _norm_category(value: str) -> str:
    """Categorical normalization (E3 workout type): trim and case-fold; never applied to free text."""
    return value.strip().lower()


_REST_NORM = _norm_category(cfg.REST_WORKOUT_TYPE)


def _is_rest_type(workout_type: Any) -> bool:
    return isinstance(workout_type, str) and _norm_category(workout_type) == _REST_NORM


def _rounded_difference(gemini: float, baseline: float) -> float:
    """Numerical representation only (see criteria_config.FLOAT_COMPARISON_DECIMALS)."""
    return round(gemini - baseline, cfg.FLOAT_COMPARISON_DECIMALS)


# ---------- E1: pace / intensity (structured fields, trimmed exact match) ----------

def evaluate_e1(baseline: Dict[str, Any], raw: Any) -> CriterionResult:
    """
    Pace/intensity fidelity over applicable structured fields only. Rest days are excluded:
    their pace_target/intensity_zone are placeholder values, not a running constraint. They are
    neither pass nor fail and are not in the denominator. Values are trimmed, then compared exactly.
    """
    weeks = _weeks_of(raw)
    if weeks is None:
        return CriterionResult("e1", None, "not applicable: no parsed weeks[] to evaluate")
    week_map = _week_index(weeks)
    matched = 0
    total = 0
    rest_excluded = 0
    evidence: List[Dict[str, Any]] = []
    for bw in baseline["weeks"]:
        days = _day_index(week_map.get(bw["week_number"]))
        for bwk in bw["workouts"]:
            if _is_rest_type(bwk["workout_type"]):
                rest_excluded += 1
                continue
            candidates = days.get(bwk["day"], [])
            gwk = candidates[0] if len(candidates) == 1 else None
            for name in ("pace_target", "intensity_zone"):
                total += 1
                gval = gwk.get(name) if gwk is not None else None
                ok = isinstance(gval, str) and _norm_structured(gval) == _norm_structured(bwk[name])
                matched += int(ok)
                evidence.append({
                    "week_number": bw["week_number"],
                    "day": bwk["day"],
                    "field": name,
                    "baseline": bwk[name],
                    "gemini": gval,
                    "match": ok,
                    "reason": "workout missing or duplicated on this day" if gwk is None else None,
                })
    if total == 0:
        return CriterionResult("e1", None,
                               "not applicable: no applicable (non-rest) structured pace/intensity fields",
                               [{"rest_days_excluded": rest_excluded}])
    return CriterionResult("e1", matched / total,
                           f"matching applicable structured fields / applicable structured fields "
                           f"(rest days excluded: {rest_excluded})", evidence)


# ---------- E2: weekly volume within ±0.15 km, every baseline week ----------

def evaluate_e2(baseline: Dict[str, Any], raw: Any) -> CriterionResult:
    weeks = _weeks_of(raw)
    if weeks is None:
        return CriterionResult("e2", None, "not applicable: no parsed weeks[] to evaluate")
    week_map = _week_index(weeks)
    passed = 0
    total = 0
    evidence: List[Dict[str, Any]] = []
    for bw in baseline["weeks"]:
        total += 1
        gw = week_map.get(bw["week_number"])
        baseline_km = bw["weekly_mileage_km"]
        if gw is None:
            ok, gemini_km, diff, reason = False, None, None, "missing week"
        elif not _is_number(gw.get("weekly_mileage_km")):
            ok, gemini_km, diff, reason = False, gw.get("weekly_mileage_km"), None, "missing or invalid weekly_mileage_km"
        else:
            gemini_km = gw["weekly_mileage_km"]
            diff = _rounded_difference(gemini_km, baseline_km)
            ok = abs(diff) <= cfg.E2_WEEKLY_MILEAGE_TOLERANCE_KM
            reason = None
        passed += int(ok)
        evidence.append({
            "week_number": bw["week_number"],
            "baseline_km": baseline_km,
            "gemini_km": gemini_km,
            "difference_km": diff,
            "pass": ok,
            "reason": reason,
        })
    return CriterionResult("e2", passed / total, "weeks within ±0.15 km / total baseline weeks", evidence)


# ---------- E3: structure by day (presence, rest status, workout type, session count) ----------

def evaluate_e3(baseline: Dict[str, Any], raw: Any) -> CriterionResult:
    weeks = _weeks_of(raw)
    if weeks is None:
        return CriterionResult("e3", None, "not applicable: no parsed weeks[] to evaluate")
    week_map = _week_index(weeks)
    matched = 0
    total = 0
    evidence: List[Dict[str, Any]] = []

    def record(kind: str, week_number: int, day: Optional[str], ok: bool, expected: Any, actual: Any) -> None:
        nonlocal matched, total
        total += 1
        matched += int(ok)
        if not ok:
            evidence.append({"week_number": week_number, "day": day, "element": kind,
                             "expected": expected, "actual": actual, "match": False})

    for bw in baseline["weeks"]:
        n = bw["week_number"]
        gw = week_map.get(n)
        days = _day_index(gw)
        for bwk in bw["workouts"]:
            day = bwk["day"]
            candidates = days.get(day, [])
            present = len(candidates) == 1
            gwk = candidates[0] if present else None
            record("presence", n, day, present, "exactly one workout", len(candidates))
            g_type = gwk.get("workout_type") if gwk is not None else None
            b_rest = _is_rest_type(bwk["workout_type"])
            g_rest = _is_rest_type(g_type)
            record("rest_status", n, day, present and g_rest == b_rest,
                   "rest" if b_rest else "training",
                   None if g_type is None else ("rest" if g_rest else "training"))
            record("workout_type", n, day,
                   present and isinstance(g_type, str)
                   and _norm_category(g_type) == _norm_category(bwk["workout_type"]),
                   bwk["workout_type"], g_type)
        baseline_sessions = sum(1 for w in bw["workouts"] if not _is_rest_type(w["workout_type"]))
        gemini_sessions = None
        if gw is not None and isinstance(gw.get("workouts"), list):
            gemini_sessions = sum(
                1 for w in gw["workouts"]
                if isinstance(w, dict) and isinstance(w.get("workout_type"), str) and not _is_rest_type(w["workout_type"])
            )
        record("session_count", n, None, gemini_sessions == baseline_sessions, baseline_sessions, gemini_sessions)
    return CriterionResult("e3", matched / total, "matching structural elements / required structural elements", evidence)


# ---------- E4: phase trajectory only (no mileage) ----------

def evaluate_e4(baseline: Dict[str, Any], raw: Any) -> CriterionResult:
    weeks = _weeks_of(raw)
    if weeks is None:
        return CriterionResult("e4", None, "not applicable: no parsed weeks[] to evaluate")
    week_map = _week_index(weeks)
    matched = 0
    total = 0
    evidence: List[Dict[str, Any]] = []
    for bw in baseline["weeks"]:
        total += 1
        gw = week_map.get(bw["week_number"])
        gphase = gw.get("phase") if gw is not None else None
        ok = isinstance(gphase, str) and _norm_structured(gphase) == _norm_structured(bw["phase"])
        matched += int(ok)
        evidence.append({
            "week_number": bw["week_number"],
            "baseline_phase": bw["phase"],
            "gemini_phase": gphase,
            "match": ok,
        })
    return CriterionResult("e4", matched / total, "matching phase positions / total baseline weeks", evidence)


# ---------- E5: output validity and completeness (only when a provider output exists) ----------

def _output_present(record: GenerationRecord) -> bool:
    if record.status == "GEMINI_API_ERROR":
        return False
    return record.raw_output is not None or record.raw_text_available or record.status == "GEMINI_VALIDATION_FAILED"


def evaluate_e5(record: GenerationRecord, baseline: Dict[str, Any]) -> Tuple[CriterionResult, List[str], List[Dict[str, Any]]]:
    """
    E5 = 1 valid and complete; 0 invalid or incomplete; N/A when no provider output exists
    (generation failure). Generation failures are recorded separately and are never E5 = 0.
    """
    if not _output_present(record):
        result = CriterionResult(
            "e5", None,
            f"not applicable: generation failed before an output was produced ({record.error_category or 'unknown'})",
        )
        return result, [], []

    categories: List[str] = []
    details: List[Dict[str, Any]] = []

    def fail(category: str, where: str, detail: str) -> None:
        if category not in categories:
            categories.append(category)
        details.append({"category": category, "where": where, "detail": detail})

    raw = record.raw_output
    if not isinstance(raw, dict):
        if record.raw_text_available or record.error_category == "MALFORMED_JSON":
            fail("malformed_json", "generation", "response is not valid JSON")
        else:
            fail("schema_invalid", "top_level", "output is not a JSON object")
    else:
        _check_structure(raw, baseline, fail)

    if record.finish_reason == cfg.TRUNCATION_FINISH_REASON and categories:
        fail("truncated_output", "generation", f"finishReason={cfg.TRUNCATION_FINISH_REASON} with incomplete output")

    evidence: List[Dict[str, Any]] = list(details)
    if record.finish_reason == cfg.TRUNCATION_FINISH_REASON and not categories:
        evidence.append({"category": None, "where": "generation",
                         "detail": f"finishReason={cfg.TRUNCATION_FINISH_REASON} observed; output complete"})
    value = 0.0 if categories else 1.0
    return CriterionResult("e5", value, "1 = valid and complete; 0 = invalid or incomplete", evidence), categories, details


def _check_text(container: Dict[str, Any], key: str, where: str, fail) -> None:
    """Required string field: must be present and non-blank."""
    if key not in container:
        fail("missing_required_field", where, key)
        return
    value = container[key]
    if not isinstance(value, str):
        fail("schema_invalid", where, f"{key} is not a string")
    elif value.strip() == "":
        fail("empty_required_text", where, key)


def _check_optional_str(container: Dict[str, Any], key: str, where: str, fail) -> None:
    """Optional (schema-defaulted) string: may be absent or blank; if present it must be a string."""
    if key in container and not isinstance(container[key], str):
        fail("schema_invalid", where, f"{key} is not a string")


def _check_optional_str_list(container: Dict[str, Any], key: str, where: str, fail) -> None:
    """Optional (schema-defaulted) list of strings: may be absent or empty; if present it must be a list of strings."""
    if key not in container:
        return
    value = container[key]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        fail("schema_invalid", where, f"{key} is not a list of strings")


def _check_structure(raw: Dict[str, Any], baseline: Dict[str, Any], fail) -> None:
    """
    E5 checks the actual output contract (app/ai/schemas.py). Required: weeks; per week
    week_number, phase, weekly_mileage_km, workouts; per workout day, workout_type,
    distance_km, pace_target, intensity_zone. Explanatory text fields have schema defaults
    and are NOT required; they are type-checked only when present.
    """
    if "weeks" not in raw:
        fail("missing_required_field", "top_level", "weeks")
    _check_optional_str(raw, "coach_overview", "top_level", fail)
    _check_optional_str_list(raw, "ai_personalization_summary", "top_level", fail)
    _check_optional_insights(raw, fail)

    weeks = raw.get("weeks")
    if not isinstance(weeks, list):
        if "weeks" in raw:
            fail("schema_invalid", "weeks", "weeks is not a list")
        return

    valid_numbers: List[int] = []
    for idx, item in enumerate(weeks):
        where = f"weeks[{idx}]"
        if not isinstance(item, dict):
            fail("schema_invalid", where, "week is not an object")
            continue
        if "week_number" not in item:
            fail("missing_required_field", where, "week_number")
        elif not _is_int(item["week_number"]) or item["week_number"] < 1:
            fail("other_structural_failure", where, "invalid week_number")
        else:
            valid_numbers.append(item["week_number"])
        for key, expected in cfg.REQUIRED_WEEK_FIELDS.items():
            if key == "week_number":
                continue
            if key not in item:
                fail("missing_required_field", where, key)
            elif isinstance(item[key], bool) or not isinstance(item[key], expected):
                fail("schema_invalid", where, f"{key} has wrong type")
            elif key == "phase" and item[key].strip() == "":
                fail("empty_required_text", where, "phase")
        if isinstance(item.get("workouts"), list):
            _check_week_workouts(item, where, fail)

    seen: Set[int] = set()
    for number in valid_numbers:
        if number in seen:
            fail("duplicate_week", "weeks", f"week_number {number} appears more than once")
        seen.add(number)

    baseline_numbers = {bw["week_number"] for bw in baseline["weeks"]}
    for number in sorted(baseline_numbers - seen):
        fail("missing_week", "weeks", f"baseline week {number} is absent")
    for number in sorted(seen - baseline_numbers):
        fail("other_structural_failure", "weeks", f"unexpected week_number {number}")
    if len(weeks) != len(baseline_numbers):
        fail("other_structural_failure", "weeks", f"week count {len(weeks)} != baseline {len(baseline_numbers)}")

    week_map = _week_index(weeks)
    for bw in baseline["weeks"]:
        gw = week_map.get(bw["week_number"])
        if gw is None or not isinstance(gw.get("workouts"), list):
            continue
        days = _day_index(gw)
        for bwk in bw["workouts"]:
            candidates = days.get(bwk["day"], [])
            if not candidates:
                fail("other_structural_failure", f"week {bw['week_number']}", f"missing workout on {bwk['day']}")
            elif len(candidates) > 1:
                fail("other_structural_failure", f"week {bw['week_number']}", f"duplicate workout on {bwk['day']}")


def _check_week_workouts(week: Dict[str, Any], where: str, fail) -> None:
    days_seen: Dict[str, int] = {}
    for j, wk in enumerate(week["workouts"]):
        wwhere = f"{where}.workouts[{j}]"
        if not isinstance(wk, dict):
            fail("schema_invalid", wwhere, "workout is not an object")
            continue
        for key, expected in cfg.REQUIRED_WORKOUT_FIELDS.items():
            if key not in wk:
                fail("missing_required_field", wwhere, key)
            elif isinstance(wk[key], bool) or not isinstance(wk[key], expected):
                fail("schema_invalid", wwhere, f"{key} has wrong type")
            elif key in cfg.E5_REQUIRED_NONBLANK_FIELDS["workout"] and isinstance(wk[key], str) and wk[key].strip() == "":
                fail("empty_required_text", wwhere, key)
        for key in cfg.WORKOUT_TEXT_FIELDS:
            _check_optional_str(wk, key, wwhere, fail)
        day = wk.get("day")
        if isinstance(day, str):
            days_seen[day] = days_seen.get(day, 0) + 1
    for day, count in days_seen.items():
        if count > 1:
            fail("other_structural_failure", where, f"duplicate day {day}")


def _check_optional_insights(raw: Dict[str, Any], fail) -> None:
    if "personalized_insights" in raw:
        insights = raw["personalized_insights"]
        if not isinstance(insights, dict):
            fail("schema_invalid", "personalized_insights", "not an object")
        else:
            for key in ("pacing_strategy", "injury_prevention_note", "nutrition_and_recovery_advice"):
                _check_optional_str(insights, key, "personalized_insights", fail)
    if "why_this_plan_was_generated" in raw:
        why = raw["why_this_plan_was_generated"]
        if not isinstance(why, dict):
            fail("schema_invalid", "why_this_plan_was_generated", "not an object")
        else:
            _check_optional_str_list(why, "key_influencing_factors", "why_this_plan_was_generated", fail)
            _check_optional_str(why, "ai_personalization_additions", "why_this_plan_was_generated", fail)


# ---------- E6 / E7 (injury-only; two separate logical stages) ----------

_TOKEN_RE = re.compile(r"[a-z]+")


def is_no_injury(injury_limitations: Optional[str]) -> bool:
    """[SPEC] null, empty, whitespace-only, and the sentinels (trimmed, case-insensitive) mean not applicable."""
    if injury_limitations is None:
        return True
    normalized = injury_limitations.strip().lower()
    return normalized == "" or normalized in cfg.E6_NO_INJURY_SENTINELS


def _content_tokens(text: str) -> Set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in cfg.STOP_WORDS_V2}


def _sentences(text: str) -> List[str]:
    pieces = re.split("[" + re.escape(cfg.SENTENCE_DELIMITERS) + "]", text)
    return [p.strip() for p in pieces if p.strip()]


def _matched_terms(lowered: str, terms) -> List[str]:
    return [t for t in terms if re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", lowered)]


def _excerpt(text: str) -> str:
    return text if len(text) <= cfg.EVIDENCE_EXCERPT_CHARS else text[: cfg.EVIDENCE_EXCERPT_CHARS] + "..."


def _generated_text_fields(raw: Dict[str, Any]) -> Iterator[Tuple[str, str]]:
    """Gemini text fields only. Numeric, pace, zone, phase, and mileage fields are never searched."""
    if isinstance(raw.get("coach_overview"), str):
        yield "coach_overview", raw["coach_overview"]
    insights = raw.get("personalized_insights")
    if isinstance(insights, dict):
        for key in ("pacing_strategy", "injury_prevention_note", "nutrition_and_recovery_advice"):
            if isinstance(insights.get(key), str):
                yield f"personalized_insights.{key}", insights[key]
    summary = raw.get("ai_personalization_summary")
    if isinstance(summary, list):
        for i, item in enumerate(summary):
            if isinstance(item, str):
                yield f"ai_personalization_summary[{i}]", item
    why = raw.get("why_this_plan_was_generated")
    if isinstance(why, dict):
        factors = why.get("key_influencing_factors")
        if isinstance(factors, list):
            for i, item in enumerate(factors):
                if isinstance(item, str):
                    yield f"why_this_plan_was_generated.key_influencing_factors[{i}]", item
        if isinstance(why.get("ai_personalization_additions"), str):
            yield "why_this_plan_was_generated.ai_personalization_additions", why["ai_personalization_additions"]
    weeks = raw.get("weeks")
    if isinstance(weeks, list):
        for w in weeks:
            if not isinstance(w, dict) or not isinstance(w.get("workouts"), list):
                continue
            for wk in w["workouts"]:
                if not isinstance(wk, dict):
                    continue
                for text_field in cfg.WORKOUT_TEXT_FIELDS:
                    value = wk.get(text_field)
                    if isinstance(value, str):
                        yield f"weeks[week {w.get('week_number')}, {wk.get('day')}].{text_field}", value


def _e6_stage1_hits(raw: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
    """
    E6 stage 1: an explicit injury-related training-plan adaptation.
    A sentence is a hit when it contains all three:
      - an injury-related reference term (E6_INJURY_REFERENCE_TERMS_V2),
      - an explicit adaptation/action term (E6_ADAPTATION_ACTION_TERMS_V2),
      - a plan-object term (E6_PLAN_OBJECT_TERMS_V2) placing it in the training plan.
    Returns (hits with the full sentence, count of injury-reference sentences that were not hits).
    """
    hits: List[Dict[str, Any]] = []
    injury_only = 0
    for path, text in _generated_text_fields(raw):
        for sentence in _sentences(text):
            lowered = sentence.lower()
            injury_terms = _matched_terms(lowered, cfg.E6_INJURY_REFERENCE_TERMS_V2)
            action_terms = _matched_terms(lowered, cfg.E6_ADAPTATION_ACTION_TERMS_V2)
            object_terms = _matched_terms(lowered, cfg.E6_PLAN_OBJECT_TERMS_V2)
            if injury_terms and action_terms and object_terms:
                hits.append({"field": path, "sentence": sentence, "injury_terms": injury_terms,
                             "action_terms": action_terms, "object_terms": object_terms})
            elif injury_terms:
                injury_only += 1
    return hits, injury_only


def evaluate_e6(raw: Any, injury_limitations: Optional[str]) -> CriterionResult:
    """E6 = 1 if an explicit injury-related plan adaptation is detected, 0 otherwise; N/A when not applicable."""
    if is_no_injury(injury_limitations):
        return CriterionResult("e6", None, "not applicable: no stored injury limitation")
    if not isinstance(raw, dict):
        return CriterionResult("e6", None, "not applicable: no parsed output to search")
    hits, injury_only = _e6_stage1_hits(raw)
    if hits:
        return CriterionResult(
            "e6", 1.0, "explicit injury-related plan adaptation detected",
            [{"field": h["field"], "sentence": _excerpt(h["sentence"]), "injury_terms": h["injury_terms"],
              "action_terms": h["action_terms"], "object_terms": h["object_terms"]} for h in hits],
        )
    return CriterionResult(
        "e6", 0.0, "no explicit injury-related plan adaptation (generic advice does not count)",
        [{"injury_reference_sentences_without_adaptation": injury_only}],
    )


def _e7_anchor_tokens(text: str) -> Dict[str, str]:
    """
    Maps each runner-specific anchor token in text to its E7 anchor class. A codebook token counts
    only if it is not generic and, for contextual terms (E7_CONTEXTUAL_RULES_V2), its adjacent raw
    token satisfies the rule. Deterministic and offline.
    """
    raw_tokens = _TOKEN_RE.findall(text.lower())
    classes = {term: klass for klass, terms in cfg.E7_ANCHOR_CODEBOOK_V2.items() for term in terms}
    found: Dict[str, str] = {}
    for i, token in enumerate(raw_tokens):
        if token not in classes or token in cfg.E7_GENERIC_VOCABULARY_V2:
            continue
        if token in cfg.E7_CONTEXTUAL_RULES_V2:
            side, allowed = cfg.E7_CONTEXTUAL_RULES_V2[token]
            neighbour = None
            if side == "previous" and i > 0:
                neighbour = raw_tokens[i - 1]
            elif side == "next" and i + 1 < len(raw_tokens):
                neighbour = raw_tokens[i + 1]
            if neighbour not in allowed:
                continue
        found[token] = classes[token]
    return found


def evaluate_e7(raw: Any, injury_limitations: Optional[str], e6: CriterionResult) -> CriterionResult:
    """
    E7 is conditional on E6 (E6 = 0 or N/A -> N/A). Stage 2: does an E6 adaptation sentence contain a
    runner-specific anchor traceable to the stored injury text? An anchor is a token that appears in the
    stored injury text and is classified in E7_ANCHOR_CODEBOOK_V2 (body part, condition, trigger/activity,
    or runner limitation). Generic vocabulary is never an anchor.
    """
    if e6.value is None:
        return CriterionResult("e7", None, "not applicable: E6 is N/A")
    if e6.value == 0:
        return CriterionResult("e7", None, "not applicable: E6 = 0")
    anchor_class = _e7_anchor_tokens(injury_limitations or "")
    hits = _e6_stage1_hits(raw)[0] if isinstance(raw, dict) else []
    evidence: List[Dict[str, Any]] = []
    specific = False
    for hit in hits:
        anchors = sorted(set(_e7_anchor_tokens(hit["sentence"])) & set(anchor_class))
        if anchors:
            specific = True
        evidence.append({
            "field": hit["field"],
            "sentence": _excerpt(hit["sentence"]),
            "runner_anchors": anchors,
            "anchor_classes": {a: anchor_class[a] for a in anchors},
        })
    if specific:
        return CriterionResult("e7", 1.0, "E6 adaptation contains a runner-specific anchor", evidence)
    return CriterionResult("e7", 0.0, "E6 adaptation contains no runner-specific anchor (generic adaptation)", evidence)


# ---------- per-generation entry point ----------

def evaluate_generation(record: GenerationRecord, baseline: Dict[str, Any],
                        injury_limitations: Optional[str]) -> GenerationEvaluation:
    output_present = _output_present(record)
    raw = record.raw_output if output_present else None
    e5, categories, details = evaluate_e5(record, baseline)
    e6 = evaluate_e6(raw, injury_limitations)
    criteria = {
        "e1": evaluate_e1(baseline, raw),
        "e2": evaluate_e2(baseline, raw),
        "e3": evaluate_e3(baseline, raw),
        "e4": evaluate_e4(baseline, raw),
        "e5": e5,
        "e6": e6,
        "e7": evaluate_e7(raw, injury_limitations, e6),
    }
    generation_failure = record.error_category if record.status == "GEMINI_API_ERROR" else None
    return GenerationEvaluation(
        profile_id=record.profile_id,
        generation_id=record.generation_id,
        status=record.status,
        output_present=output_present,
        generation_failure=generation_failure,
        criteria=criteria,
        e5_failure_categories=categories,
        failure_details=details,
    )
