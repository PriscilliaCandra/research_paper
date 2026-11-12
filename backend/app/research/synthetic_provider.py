"""
Deterministic synthetic Gemini provider for DRY RUN only.

It never touches the network and never reads an API key. It is passed explicitly as
provider_call to generate_gemini_result, so the real provider cannot be reached from
dry-run code. Outcomes are fixed by (profile index, repetition) so that every dry run
produces the same artifacts.
"""
import copy
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Awaitable

from app.ai.llm_service import SYNTHETIC_PROVIDER_KIND, GeminiJSONResponse, GeminiMalformedJSON

# Fixed outcome script: (profile_index, repetition) -> outcome kind.
# Everything not listed is a valid output.
SCRIPTED_OUTCOMES: Dict[tuple, str] = {
    (5, 2): "malformed_truncated",
    (9, 1): "schema_invalid_missing_week",
    (13, 3): "api_failure",
    (17, 2): "constraint_violation",
}


@dataclass(frozen=True)
class SyntheticScript:
    profile_index: int
    repetition: int
    kind: str
    rule_plan: Dict[str, Any]
    injury_limitations: Optional[str]


def scripted_kind(profile_index: int, repetition: int) -> str:
    return SCRIPTED_OUTCOMES.get((profile_index, repetition), "valid")


def _anchor_sentence(injury: Optional[str], repetition: int) -> str:
    generic = "Listen to your body and stop if you experience pain."
    if repetition != 2 or not injury:
        return generic
    lowered = injury.lower()
    for term in ("knee", "achilles"):
        if term in lowered:
            return f"Because of your {term} injury, reduce the intensity of this session."
    return generic


def _valid_payload(rule_plan: Dict[str, Any], sentence: str) -> Dict[str, Any]:
    weeks = []
    for baseline_week in rule_plan["weeks"]:
        workouts = []
        for w in baseline_week["workouts"]:
            item = copy.deepcopy(w)
            item["warmup_cooldown"] = "Synthetic warm-up."
            item["workout_execution"] = sentence if not w["workout_type"] == "Rest & Recovery" else "Rest."
            item["recovery_instruction"] = "Synthetic recovery."
            item["purpose"] = "Synthetic purpose."
            workouts.append(item)
        weeks.append({
            "week_number": baseline_week["week_number"],
            "phase": baseline_week["phase"],
            "weekly_mileage_km": baseline_week["weekly_mileage_km"],
            "workouts": workouts,
        })
    return {
        "coach_overview": "Synthetic overview.",
        "weeks": weeks,
        "personalized_insights": {"pacing_strategy": "Synthetic.", "injury_prevention_note": "Synthetic.",
                                  "nutrition_and_recovery_advice": "Synthetic."},
        "ai_personalization_summary": ["Synthetic summary."],
        "why_this_plan_was_generated": {"key_influencing_factors": ["Synthetic factor."],
                                        "ai_personalization_additions": "Synthetic additions."},
    }


def make_synthetic_provider(script: SyntheticScript) -> Callable[[str], Awaitable[Any]]:
    """Returns an async callable with the signature generate_gemini_result expects for provider_call."""

    async def _call(_prompt_text: str) -> Any:
        if script.kind == "malformed_truncated":
            raise GeminiMalformedJSON('{"weeks": [', "MAX_TOKENS")
        if script.kind == "api_failure":
            raise ConnectionError("synthetic provider unavailable")
        sentence = _anchor_sentence(script.injury_limitations, script.repetition)
        payload = _valid_payload(script.rule_plan, sentence)
        if script.kind == "schema_invalid_missing_week":
            payload["weeks"] = payload["weeks"][:-1]
        elif script.kind == "constraint_violation":
            payload["weeks"][1]["weekly_mileage_km"] = round(payload["weeks"][1]["weekly_mileage_km"] + 0.5, 1)
        response = GeminiJSONResponse(payload)
        response.finish_reason = "STOP"
        return response

    _call.research_provider_kind = SYNTHETIC_PROVIDER_KIND
    return _call
