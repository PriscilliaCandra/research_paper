"""
Multi-week Gemini contract coverage.

No real network calls: generate_gemini_plan is replaced via monkeypatch.
"""
import asyncio
import copy
import json

from app import config
from app.ai import llm_service
from app.ai.llm_service import GenerationStatus, generate_gemini_result, generate_offline_ai_plan
from app.ai.prompts import build_user_prompt
from app.ai.schemas import AIPersonalizedPlan
from app.ai.validator import validate_ai_plan
from app.rules.generator import generate_rule_based_plan
from tests.factories import make_profile, build_valid_ai_payload


def test_prompt_carries_every_week_of_the_deterministic_baseline():
    profile = make_profile(plan_duration_weeks=12)
    rule_plan = generate_rule_based_plan(profile)
    prompt = build_user_prompt(profile, rule_plan)
    payload, _ = json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])

    sent_weeks = payload["rule_based_baseline_plan"]
    assert len(sent_weeks) == 12
    assert [w["week_number"] for w in sent_weeks] == list(range(1, 13))
    assert [w["phase"] for w in sent_weeks] == [w["phase"] for w in rule_plan["weeks"]]
    assert "rule_based_baseline_week" not in payload


def test_prompt_keeps_injury_limitation_for_gemini():
    profile = make_profile(injury_limitations="Mild left knee pain on downhills")
    rule_plan = generate_rule_based_plan(profile)
    assert "Mild left knee pain on downhills" in build_user_prompt(profile, rule_plan)


def test_gemini_response_parses_into_every_week():
    profile = make_profile(plan_duration_weeks=8)
    rule_plan = generate_rule_based_plan(profile)
    raw = build_valid_ai_payload(rule_plan)

    parsed = AIPersonalizedPlan.model_validate(json.loads(json.dumps(raw)))
    assert len(parsed.weeks) == 8
    assert all(len(w.workouts) == 7 for w in parsed.weeks)


def test_offline_fallback_covers_every_week_and_passes_validation():
    profile = make_profile(plan_duration_weeks=16)
    rule_plan = generate_rule_based_plan(profile)
    offline = generate_offline_ai_plan(profile, rule_plan)

    assert len(offline["weeks"]) == 16
    validated = validate_ai_plan(offline, rule_plan, profile)
    assert len(validated.weeks) == 16


def test_valid_gemini_output_yields_gemini_success(monkeypatch):
    profile = make_profile(plan_duration_weeks=8)
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")

    async def _fake(_prompt):
        return build_valid_ai_payload(rule_plan)

    monkeypatch.setattr(llm_service, "generate_gemini_plan", _fake)
    result = asyncio.run(generate_gemini_result(profile, rule_plan))

    assert result.status == GenerationStatus.GEMINI_SUCCESS
    assert result.validated_plan is not None
    assert len(result.validated_plan["weeks"]) == 8


def test_validation_failure_is_never_reported_as_gemini_success(monkeypatch):
    profile = make_profile(plan_duration_weeks=8)
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")

    bad = copy.deepcopy(build_valid_ai_payload(rule_plan))
    bad["weeks"][5]["weekly_mileage_km"] += 40.0  # tamper with week 6

    async def _fake(_prompt):
        return bad

    monkeypatch.setattr(llm_service, "generate_gemini_plan", _fake)
    result = asyncio.run(generate_gemini_result(profile, rule_plan))

    assert result.status == GenerationStatus.GEMINI_VALIDATION_FAILED
    assert result.validated_plan is None
    assert result.raw_output == bad, "raw Gemini output must be preserved for research"
    assert "week 6" in result.error


def test_malformed_json_is_validation_failed_not_success(monkeypatch):
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")

    async def _fake(_prompt):
        llm_service.clean_json_response("{not valid json")

    monkeypatch.setattr(llm_service, "generate_gemini_plan", _fake)
    result = asyncio.run(generate_gemini_result(profile, rule_plan))

    assert result.status == GenerationStatus.GEMINI_VALIDATION_FAILED
    assert result.validated_plan is None


def test_api_error_is_reported_without_leaking_key(monkeypatch):
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    secret = "FAKE-TEST-KEY-DO-NOT-LEAK-999"
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", secret)

    async def _fake(_prompt):
        raise RuntimeError(f"request to https://example.test?key={secret} failed")

    monkeypatch.setattr(llm_service, "generate_gemini_plan", _fake)
    result = asyncio.run(generate_gemini_result(profile, rule_plan))

    assert result.status == GenerationStatus.GEMINI_API_ERROR
    assert result.error == "RuntimeError"
    assert secret not in json.dumps(result.__dict__, default=str)


def test_missing_key_is_gemini_api_error(monkeypatch):
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "")
    result = asyncio.run(generate_gemini_result(profile, rule_plan))
    assert result.status == GenerationStatus.GEMINI_API_ERROR
    assert result.validated_plan is None


class _FakeHttpResponse:
    def __init__(self, text, finish_reason=None):
        self._text = text
        self._finish_reason = finish_reason

    def raise_for_status(self):
        pass

    def json(self):
        candidate = {"content": {"parts": [{"text": self._text}]}}
        if self._finish_reason is not None:
            candidate["finishReason"] = self._finish_reason
        return {"candidates": [candidate]}


def _patch_gemini_http(monkeypatch, text, finish_reason=None):
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            return _FakeHttpResponse(text, finish_reason)

    monkeypatch.setattr(llm_service.httpx, "AsyncClient", _FakeClient)


def _forbid_offline_fallback(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("research path must not invoke deterministic fallback")

    monkeypatch.setattr(llm_service, "generate_offline_ai_plan", _boom)


def test_malformed_json_keeps_raw_text_and_redacts_key(monkeypatch):
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    secret = "FAKE-TEST-KEY-REDACT-ME-555"
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", secret)
    _patch_gemini_http(monkeypatch, f"here is not json, key echoed {secret}")
    _forbid_offline_fallback(monkeypatch)

    result = asyncio.run(generate_gemini_result(profile, rule_plan))

    assert result.status == GenerationStatus.GEMINI_VALIDATION_FAILED
    assert result.error_category == "MALFORMED_JSON"
    assert result.validated_plan is None
    assert result.raw_text is not None
    assert "here is not json" in result.raw_text
    assert secret not in result.raw_text
    assert secret not in json.dumps(result.__dict__, default=str)


def test_malformed_json_on_research_path_is_not_success_and_not_fallback(monkeypatch):
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")
    _patch_gemini_http(monkeypatch, "{truncated")
    _forbid_offline_fallback(monkeypatch)

    result = asyncio.run(generate_gemini_result(profile, rule_plan))
    assert result.status != GenerationStatus.GEMINI_SUCCESS
    assert result.status != GenerationStatus.GEMINI_FALLBACK
    assert result.raw_text == "{truncated"


def test_valid_response_exposes_parsed_and_raw_output(monkeypatch):
    profile = make_profile(plan_duration_weeks=8)
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")
    raw = build_valid_ai_payload(rule_plan)
    _patch_gemini_http(monkeypatch, json.dumps(raw))
    _forbid_offline_fallback(monkeypatch)

    result = asyncio.run(generate_gemini_result(profile, rule_plan))
    assert result.status == GenerationStatus.GEMINI_SUCCESS
    assert result.raw_output == raw
    assert len(result.validated_plan["weeks"]) == 8
    assert result.error_category is None


def test_validation_failure_preserves_raw_output_and_category(monkeypatch):
    profile = make_profile(plan_duration_weeks=8)
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")
    bad = copy.deepcopy(build_valid_ai_payload(rule_plan))
    bad["weeks"][2]["workouts"][0]["pace_target"] = "02:00 /km"
    _patch_gemini_http(monkeypatch, json.dumps(bad))
    _forbid_offline_fallback(monkeypatch)

    result = asyncio.run(generate_gemini_result(profile, rule_plan))
    assert result.status == GenerationStatus.GEMINI_VALIDATION_FAILED
    assert result.error_category == "SCHEMA_OR_CONSTRAINT"
    assert result.raw_output == bad
    assert result.raw_text is None
    assert "week 3" in result.error and "pace_target" in result.error


def test_api_failure_is_gemini_api_error_without_secret_or_fallback(monkeypatch):
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    secret = "FAKE-TEST-KEY-API-FAIL-777"
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", secret)
    _forbid_offline_fallback(monkeypatch)

    class _ExplodingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise ConnectionError(f"could not reach https://host/?key={secret}")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(llm_service.httpx, "AsyncClient", _ExplodingClient)
    result = asyncio.run(generate_gemini_result(profile, rule_plan))

    assert result.status == GenerationStatus.GEMINI_API_ERROR
    assert result.error_category == "API_CALL_FAILED"
    assert result.error == "ConnectionError"
    assert secret not in json.dumps(result.__dict__, default=str)


def test_production_dispatcher_reports_fallback_status_on_malformed_json(monkeypatch):
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")
    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "gemini")
    _patch_gemini_http(monkeypatch, "not json at all")

    outcome = asyncio.run(llm_service.get_personalized_ai_plan_with_status(profile, rule_plan))
    assert outcome.status == GenerationStatus.GEMINI_FALLBACK
    assert outcome.model_used.startswith("AI Synthesis Engine")
    assert len(outcome.plan["weeks"]) == len(rule_plan["weeks"])


def test_production_dispatcher_reports_gemini_success_for_valid_output(monkeypatch):
    profile = make_profile(plan_duration_weeks=8)
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")
    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "gemini")
    _patch_gemini_http(monkeypatch, json.dumps(build_valid_ai_payload(rule_plan)))

    outcome = asyncio.run(llm_service.get_personalized_ai_plan_with_status(profile, rule_plan))
    assert outcome.status == GenerationStatus.GEMINI_SUCCESS
    assert outcome.model_used.startswith("Gemini (")
    assert outcome.plan["workouts"] == outcome.plan["weeks"][0]["workouts"]


# ---------- finishReason preservation for every provider response (correction pass) ----------

def test_finish_reason_preserved_for_successful_parsed_response(monkeypatch):
    profile = make_profile(plan_duration_weeks=8)
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")
    raw = build_valid_ai_payload(rule_plan)
    _patch_gemini_http(monkeypatch, json.dumps(raw), finish_reason="STOP")

    result = asyncio.run(generate_gemini_result(profile, rule_plan))
    assert result.status == GenerationStatus.GEMINI_SUCCESS
    assert result.finish_reason == "STOP"
    assert result.raw_output == raw


def test_finish_reason_max_tokens_preserved_even_when_json_parses(monkeypatch):
    profile = make_profile(plan_duration_weeks=8)
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")
    _patch_gemini_http(monkeypatch, json.dumps(build_valid_ai_payload(rule_plan)), finish_reason="MAX_TOKENS")

    result = asyncio.run(generate_gemini_result(profile, rule_plan))
    assert result.finish_reason == "MAX_TOKENS"
    assert result.raw_output is not None


def test_finish_reason_preserved_for_validation_failure(monkeypatch):
    profile = make_profile(plan_duration_weeks=8)
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")
    bad = copy.deepcopy(build_valid_ai_payload(rule_plan))
    bad["weeks"][1]["weekly_mileage_km"] += 50.0
    _patch_gemini_http(monkeypatch, json.dumps(bad), finish_reason="MAX_TOKENS")

    result = asyncio.run(generate_gemini_result(profile, rule_plan))
    assert result.status == GenerationStatus.GEMINI_VALIDATION_FAILED
    assert result.finish_reason == "MAX_TOKENS"
    assert result.raw_output == bad


def test_finish_reason_preserved_for_malformed_json(monkeypatch):
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")
    _patch_gemini_http(monkeypatch, "{\"weeks\": [", finish_reason="MAX_TOKENS")

    result = asyncio.run(generate_gemini_result(profile, rule_plan))
    assert result.status == GenerationStatus.GEMINI_VALIDATION_FAILED
    assert result.error_category == "MALFORMED_JSON"
    assert result.finish_reason == "MAX_TOKENS"
    assert result.raw_text == "{\"weeks\": ["


def test_api_error_has_no_provider_finish_reason(monkeypatch):
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")

    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise ConnectionError("no response")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(llm_service.httpx, "AsyncClient", _Boom)
    result = asyncio.run(generate_gemini_result(profile, rule_plan))
    assert result.status == GenerationStatus.GEMINI_API_ERROR
    assert result.finish_reason is None
    assert result.raw_output is None and result.raw_text is None


def test_top_level_json_array_is_preserved_as_output_with_finish_reason(monkeypatch):
    profile = make_profile()
    rule_plan = generate_rule_based_plan(profile)
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "FAKE-TEST-KEY")
    _patch_gemini_http(monkeypatch, "[1, 2, 3]", finish_reason="STOP")

    result = asyncio.run(generate_gemini_result(profile, rule_plan))
    assert result.status == GenerationStatus.GEMINI_VALIDATION_FAILED
    assert result.raw_output == [1, 2, 3]
    assert result.finish_reason == "STOP"
