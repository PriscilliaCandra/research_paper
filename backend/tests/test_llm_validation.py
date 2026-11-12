"""
LLM output validation and fallback-dispatch coverage.

No real network calls are made: Gemini/OpenAI network functions are patched
via monkeypatch so these tests run offline and deterministically.
"""
import asyncio
import copy
import json
import logging

from app import config
from app.ai import llm_service
from app.ai.llm_service import get_personalized_ai_plan, clean_json_response, generate_offline_ai_plan
from app.ai.validator import validate_ai_plan, AIPlanValidationError
from app.rules.generator import generate_rule_based_plan
from tests.factories import make_profile, build_valid_ai_payload


def test_valid_llm_output_passes_validation_unchanged():
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    payload = build_valid_ai_payload(rule_plan)

    validated = validate_ai_plan(payload, rule_plan, profile)
    assert len(validated.workouts) == 7
    assert validated.weekly_mileage_km == rule_plan["week_1_plan"]["weekly_mileage_km"]


def test_offline_synthesizer_output_also_passes_validation():
    """The deterministic fallback must itself satisfy the same constraints it enforces on the LLM."""
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    offline_plan = generate_offline_ai_plan(profile, rule_plan)

    validated = validate_ai_plan(offline_plan, rule_plan, profile)
    assert len(validated.workouts) == 7


def test_malformed_json_raises_json_decode_error():
    try:
        clean_json_response("{not valid json")
        assert False, "Expected json.JSONDecodeError"
    except json.JSONDecodeError:
        pass


def test_missing_required_field_is_rejected():
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    payload = build_valid_ai_payload(rule_plan)
    del payload["weeks"]

    try:
        validate_ai_plan(payload, rule_plan, profile)
        assert False, "Expected AIPlanValidationError"
    except AIPlanValidationError as e:
        assert "schema validation failed" in str(e)


def test_hallucinated_distance_is_rejected():
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    payload = build_valid_ai_payload(rule_plan)
    payload["weeks"][0]["workouts"][0]["distance_km"] += 50.0

    try:
        validate_ai_plan(payload, rule_plan, profile)
        assert False, "Expected AIPlanValidationError"
    except AIPlanValidationError as e:
        assert "distance_km" in str(e)


def test_hallucinated_pace_is_rejected():
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    payload = build_valid_ai_payload(rule_plan)
    changed = False
    for w in payload["weeks"][0]["workouts"]:
        if w["pace_target"] != "N/A":
            w["pace_target"] = "02:00 /km"
            changed = True
            break
    assert changed, "test setup error: no non-rest workout to tamper with"

    try:
        validate_ai_plan(payload, rule_plan, profile)
        assert False, "Expected AIPlanValidationError"
    except AIPlanValidationError as e:
        assert "pace_target" in str(e)


def test_changed_weekly_mileage_is_rejected():
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    payload = build_valid_ai_payload(rule_plan)
    payload["weeks"][0]["weekly_mileage_km"] += 30.0

    try:
        validate_ai_plan(payload, rule_plan, profile)
        assert False, "Expected AIPlanValidationError"
    except AIPlanValidationError as e:
        assert "weekly_mileage_km" in str(e)


def test_invalid_workout_count_is_rejected():
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    payload = build_valid_ai_payload(rule_plan)
    payload["weeks"][0]["workouts"] = payload["weeks"][0]["workouts"][:5]

    try:
        validate_ai_plan(payload, rule_plan, profile)
        assert False, "Expected AIPlanValidationError"
    except AIPlanValidationError as e:
        assert "workout count" in str(e)


def test_valid_multi_week_output_is_accepted_for_every_week():
    profile = make_profile(plan_duration_weeks=12)
    rule_plan = generate_rule_based_plan(profile)
    validated = validate_ai_plan(build_valid_ai_payload(rule_plan), rule_plan, profile)

    assert len(validated.weeks) == 12
    assert [w.week_number for w in validated.weeks] == list(range(1, 13))
    assert [w.phase for w in validated.weeks] == [w["phase"] for w in rule_plan["weeks"]]


def test_tampering_with_a_later_week_is_rejected_and_names_that_week():
    profile = make_profile(plan_duration_weeks=8)
    rule_plan = generate_rule_based_plan(profile)
    payload = build_valid_ai_payload(rule_plan)
    payload["weeks"][4]["weekly_mileage_km"] += 10.0  # week 5, a post-cutback build week

    try:
        validate_ai_plan(payload, rule_plan, profile)
        assert False, "Expected AIPlanValidationError"
    except AIPlanValidationError as e:
        assert "week 5" in str(e)
        assert "weekly_mileage_km" in str(e)


def test_phase_change_is_rejected():
    profile = make_profile(plan_duration_weeks=8)
    rule_plan = generate_rule_based_plan(profile)
    payload = build_valid_ai_payload(rule_plan)
    payload["weeks"][3]["phase"] = "Progressive Build Phase"  # week 4 is the cutback

    try:
        validate_ai_plan(payload, rule_plan, profile)
        assert False, "Expected AIPlanValidationError"
    except AIPlanValidationError as e:
        assert "phase changed" in str(e)


def test_missing_week_is_rejected():
    profile = make_profile(plan_duration_weeks=8)
    rule_plan = generate_rule_based_plan(profile)
    payload = build_valid_ai_payload(rule_plan)
    payload["weeks"] = payload["weeks"][:7]

    try:
        validate_ai_plan(payload, rule_plan, profile)
        assert False, "Expected AIPlanValidationError"
    except AIPlanValidationError as e:
        assert "week count mismatch" in str(e)


def test_dispatcher_falls_back_when_no_api_keys_configured(monkeypatch):
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config.settings, "OPENAI_API_KEY", "")

    plan, model_used = asyncio.run(get_personalized_ai_plan(profile, rule_plan))
    assert "Fallback" in model_used or "Synthesis" in model_used
    assert len(plan["workouts"]) == 7


def test_dispatcher_discards_invalid_plan_and_never_logs_the_api_key(monkeypatch, caplog):
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    secret_key = "FAKE-TEST-KEY-DO-NOT-LOG-1234567890"
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", secret_key)
    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "gemini")

    bad_payload = copy.deepcopy(build_valid_ai_payload(rule_plan))
    bad_payload["weeks"][0]["weekly_mileage_km"] += 999.0  # hallucinated volume spike

    async def _fake_gemini_call(_prompt):
        return bad_payload

    monkeypatch.setattr(llm_service, "generate_gemini_plan", _fake_gemini_call)

    with caplog.at_level(logging.WARNING, logger="app.ai.llm_service"):
        plan, model_used = asyncio.run(get_personalized_ai_plan(profile, rule_plan))

    assert "Fallback" in model_used or "Synthesis" in model_used
    assert plan["weekly_mileage_km"] == rule_plan["week_1_plan"]["weekly_mileage_km"]

    combined_log = " ".join(r.getMessage() for r in caplog.records)
    assert secret_key not in combined_log, "API key leaked into application logs!"
    assert "falling back" in combined_log.lower()


def test_dispatcher_falls_back_on_malformed_json(monkeypatch):
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")
    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "gemini")

    async def _fake_gemini_call(_prompt):
        clean_json_response("{not valid json")  # raises json.JSONDecodeError

    monkeypatch.setattr(llm_service, "generate_gemini_plan", _fake_gemini_call)

    plan, model_used = asyncio.run(get_personalized_ai_plan(profile, rule_plan))
    assert "Fallback" in model_used or "Synthesis" in model_used
    assert len(plan["workouts"]) == 7
