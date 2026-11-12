from typing import Any, Dict

from pydantic import ValidationError

from app.ai.schemas import AIPersonalizedPlan
from app.schemas import RunnerProfileCreate

# Tolerances account for floating point rounding introduced when the LLM
# echoes back numbers as JSON, not for genuine deviation from the baseline.
DISTANCE_TOLERANCE_KM = 0.05
MILEAGE_TOLERANCE_KM = 0.15


class AIPlanValidationError(Exception):
    """
    Raised when raw LLM output either fails schema validation or silently
    changes a numerical value that must be controlled exclusively by the
    deterministic rule-based engine (distances, paces, weekly mileage,
    workout/rest day counts) in any week. The production dispatcher treats
    this as "never trust the AI plan" and falls back to the offline
    rule-constrained synthesizer; the research path reports it as
    GEMINI_VALIDATION_FAILED without fallback.
    """


def validate_ai_plan(
    raw_plan: Dict[str, Any],
    rule_plan: Dict[str, Any],
    profile: RunnerProfileCreate,
) -> AIPersonalizedPlan:
    """
    Validates a raw (already JSON-parsed) LLM plan against the deterministic
    rule-based baseline for the same runner profile.

    The AI is allowed to personalize purely textual/explanatory fields
    (coach_overview, warmup_cooldown, workout_execution, recovery_instruction,
    purpose, personalized_insights, ai_personalization_summary,
    why_this_plan_was_generated). It must NOT change any physiologically
    derived numerical value: workout count, workout type, distance, pace,
    intensity zone, weekly mileage, or the number of training/rest days.

    Raises AIPlanValidationError on any schema or constraint violation.
    """
    try:
        parsed = AIPersonalizedPlan.model_validate(raw_plan)
    except ValidationError as e:
        raise AIPlanValidationError(f"schema validation failed ({e.error_count()} error(s))") from e

    baseline_weeks = rule_plan["weeks"]
    if len(parsed.weeks) != len(baseline_weeks):
        raise AIPlanValidationError(
            f"week count mismatch: expected {len(baseline_weeks)}, got {len(parsed.weeks)}"
        )

    for ai_week, baseline_week in zip(parsed.weeks, baseline_weeks):
        ordered = _validate_week(ai_week, baseline_week, profile)
        ai_week.workouts = ordered

    week_1 = parsed.weeks[0]
    parsed.weekly_mileage_km = week_1.weekly_mileage_km
    parsed.workouts = week_1.workouts

    return parsed


def _validate_week(ai_week, baseline_week, profile: RunnerProfileCreate):
    prefix = f"week {baseline_week['week_number']}"
    if ai_week.week_number != baseline_week["week_number"]:
        raise AIPlanValidationError(
            f"{prefix}: week_number mismatch, got {ai_week.week_number}"
        )
    if ai_week.phase.strip() != baseline_week["phase"].strip():
        raise AIPlanValidationError(
            f"{prefix}: phase changed: expected '{baseline_week['phase']}', got '{ai_week.phase}'"
        )

    baseline_workouts = baseline_week["workouts"]
    baseline_by_day = {w["day"]: w for w in baseline_workouts}

    if len(ai_week.workouts) != len(baseline_workouts):
        raise AIPlanValidationError(
            f"{prefix}: workout count mismatch: expected {len(baseline_workouts)}, got {len(ai_week.workouts)}"
        )

    seen_days = set()
    for w in ai_week.workouts:
        baseline_w = baseline_by_day.get(w.day)
        if baseline_w is None:
            raise AIPlanValidationError(f"{prefix}: unrecognized day '{w.day}' not present in the rule-based baseline")
        if w.day in seen_days:
            raise AIPlanValidationError(f"{prefix}: duplicate day '{w.day}' in AI plan")
        seen_days.add(w.day)

        if w.workout_type.strip().lower() != baseline_w["workout_type"].strip().lower():
            raise AIPlanValidationError(
                f"{prefix}: workout_type changed on {w.day}: expected '{baseline_w['workout_type']}', got '{w.workout_type}'"
            )
        if abs(w.distance_km - baseline_w["distance_km"]) > DISTANCE_TOLERANCE_KM:
            raise AIPlanValidationError(
                f"{prefix}: distance_km changed on {w.day}: expected {baseline_w['distance_km']}, got {w.distance_km}"
            )
        if w.pace_target.strip() != baseline_w["pace_target"].strip():
            raise AIPlanValidationError(
                f"{prefix}: pace_target changed on {w.day}: expected '{baseline_w['pace_target']}', got '{w.pace_target}'"
            )
        if w.intensity_zone.strip() != baseline_w["intensity_zone"].strip():
            raise AIPlanValidationError(
                f"{prefix}: intensity_zone changed on {w.day}: expected '{baseline_w['intensity_zone']}', got '{w.intensity_zone}'"
            )

    missing_days = set(baseline_by_day.keys()) - seen_days
    if missing_days:
        raise AIPlanValidationError(f"{prefix}: missing day(s) in AI plan: {sorted(missing_days)}")

    expected_mileage = baseline_week["weekly_mileage_km"]
    if abs(ai_week.weekly_mileage_km - expected_mileage) > MILEAGE_TOLERANCE_KM:
        raise AIPlanValidationError(
            f"{prefix}: weekly_mileage_km changed: expected {expected_mileage}, got {ai_week.weekly_mileage_km}"
        )

    expected_rest_days = 7 - profile.training_days_per_week
    actual_rest_days = sum(1 for w in ai_week.workouts if w.workout_type.strip().lower() == "rest & recovery")
    if actual_rest_days != expected_rest_days:
        raise AIPlanValidationError(
            f"{prefix}: rest day count mismatch: expected {expected_rest_days}, got {actual_rest_days}"
        )

    actual_training_days = len(ai_week.workouts) - actual_rest_days
    if actual_training_days != profile.training_days_per_week:
        raise AIPlanValidationError(
            f"{prefix}: training day count mismatch: expected {profile.training_days_per_week}, got {actual_training_days}"
        )

    # Re-order to match the baseline's Monday->Sunday sequence so the UI grid
    # stays consistent regardless of the order the LLM emitted workouts in.
    by_day = {w.day: w for w in ai_week.workouts}
    return [by_day[day] for day in baseline_by_day.keys()]
