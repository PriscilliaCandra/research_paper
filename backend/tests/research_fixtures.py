"""Synthetic evaluator fixtures. Built from the real deterministic baseline and a valid Gemini payload."""
import copy
from typing import Any, Dict, Optional

from app.research.evaluator import GenerationRecord
from app.rules.generator import generate_rule_based_plan
from tests.factories import make_profile, build_valid_ai_payload

KNEE_INJURY = "mild left knee pain, aggravated by downhill running and speed work"


def baseline_for(duration: int = 8, injury: Optional[str] = KNEE_INJURY, **overrides: Any):
    profile = make_profile(plan_duration_weeks=duration, injury_limitations=injury, **overrides)
    return profile, generate_rule_based_plan(profile)


def valid_raw(baseline: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(build_valid_ai_payload(baseline))


def success_record(raw: Dict[str, Any], generation_id: str = "generation_1", profile_id: str = "profile_1") -> GenerationRecord:
    return GenerationRecord(profile_id, generation_id, "GEMINI_SUCCESS", raw, False, None, None, None)


def malformed_record(finish_reason: Optional[str] = None, generation_id: str = "generation_1") -> GenerationRecord:
    return GenerationRecord("profile_1", generation_id, "GEMINI_VALIDATION_FAILED", None, True,
                            finish_reason, "MALFORMED_JSON", "malformed JSON")


def api_error_record(generation_id: str = "generation_1") -> GenerationRecord:
    return GenerationRecord("profile_1", generation_id, "GEMINI_API_ERROR", None, False, None,
                            "API_CALL_FAILED", "RuntimeError")


def workout(raw: Dict[str, Any], week_index: int, day: str) -> Dict[str, Any]:
    for w in raw["weeks"][week_index]["workouts"]:
        if w["day"] == day:
            return w
    raise KeyError(day)


def first_training_workout(raw: Dict[str, Any], week_index: int = 0) -> Dict[str, Any]:
    for w in raw["weeks"][week_index]["workouts"]:
        if w["workout_type"] != "Rest & Recovery":
            return w
    raise AssertionError("no training workout")
