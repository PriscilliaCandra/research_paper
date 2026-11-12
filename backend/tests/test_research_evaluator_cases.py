"""Synthetic fixture cases A–N and E8 stability tests (offline, deterministic)."""
import json

import pytest

from app.research.evaluator import evaluate_generation
from app.research.stability import evaluate_profile_stability
from tests.research_fixtures import (
    KNEE_INJURY, api_error_record, baseline_for, first_training_workout, malformed_record,
    success_record, valid_raw, workout,
)


def _ev(raw, base, injury=KNEE_INJURY, gid="generation_1"):
    return evaluate_generation(success_record(raw, gid), base, injury)


def _scores(ev):
    return {c: ev.score(c) for c in ("e1", "e2", "e3", "e4", "e5", "e6", "e7")}


# Case A: perfect output. E6 is 0 (generic text) for the knee profile; E7 is N/A.
def test_case_a_perfect_output():
    _, base = baseline_for(8)
    s = _scores(_ev(valid_raw(base), base))
    assert s["e1"] == s["e2"] == s["e3"] == s["e4"] == s["e5"] == 1.0
    assert s["e6"] == 0.0 and s["e7"] is None


# Case B: pace mismatch.
def test_case_b_pace_mismatch():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    first_training_workout(raw)["pace_target"] = "06:30 /km"
    assert _ev(raw, base).score("e1") < 1.0


# Case C: weekly volume exceeds ±0.15 km.
def test_case_c_volume_out_of_tolerance():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"][2]["weekly_mileage_km"] = round(raw["weeks"][2]["weekly_mileage_km"] + 0.2, 1)
    assert _ev(raw, base).score("e2") == pytest.approx(7 / 8)


# Case D: workout moved to another day.
def test_case_d_workout_moved_to_another_day():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    workout(raw, 0, "Tuesday")["day"] = "Friday"
    assert _ev(raw, base).score("e3") < 1.0


# Case E: phase mismatch.
def test_case_e_phase_mismatch():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"][3]["phase"] = "Progressive Build Phase"
    assert _ev(raw, base).score("e4") == pytest.approx(7 / 8)


# Case F: missing week.
def test_case_f_missing_week():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    del raw["weeks"][6]
    ev = _ev(raw, base)
    assert ev.score("e2") == pytest.approx(7 / 8)
    assert ev.score("e5") == 0.0 and "missing_week" in ev.e5_failure_categories


# Case G: malformed JSON.
def test_case_g_malformed_json():
    _, base = baseline_for(8)
    from app.research.evaluator import evaluate_generation as eg
    ev = eg(malformed_record(), base, KNEE_INJURY)
    assert ev.score("e5") == 0.0 and ev.e5_failure_categories == ["malformed_json"]
    assert all(ev.score(c) is None for c in ("e1", "e2", "e3", "e4", "e6", "e7"))


# Case H: missing required workout field.
def test_case_h_missing_required_workout_field():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    del first_training_workout(raw)["distance_km"]
    ev = _ev(raw, base)
    assert ev.score("e5") == 0.0 and "missing_required_field" in ev.e5_failure_categories
    assert ev.score("e1") == 1.0


# Case I: generic injury advice only.
def test_case_i_generic_injury_advice_only():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    for w in raw["weeks"]:
        for wk in w["workouts"]:
            wk["recovery_instruction"] = "Listen to your body and stop if you experience pain."
    raw["personalized_insights"]["injury_prevention_note"] = "Take care and rest when needed."
    s = _scores(_ev(raw, base))
    assert s["e6"] == 0.0 and s["e7"] is None


# Case J: specific injury personalization.
def test_case_j_specific_injury_personalization():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    workout(raw, 0, "Tuesday")["workout_execution"] = (
        "Given your history of left knee discomfort aggravated by downhill running and speed work, "
        "today's quality session is a flat tempo effort rather than hill repeats."
    )
    s = _scores(_ev(raw, base))
    assert s["e6"] == 1.0 and s["e7"] == 1.0


# Case K: three repetitions, the third with a small E2 deviation (one week out of tolerance).
def test_case_k_three_repetitions_e8_formula():
    _, base = baseline_for(8)
    perfect_1 = _ev(valid_raw(base), base, gid="generation_1")
    perfect_2 = _ev(valid_raw(base), base, gid="generation_2")
    raw3 = valid_raw(base)
    raw3["weeks"][1]["weekly_mileage_km"] = round(raw3["weeks"][1]["weekly_mileage_km"] + 0.2, 1)
    run3 = _ev(raw3, base, gid="generation_3")

    stab = evaluate_profile_stability("profile_1", [perfect_1, perfect_2, run3])
    assert stab.per_criterion_stability["e2"] == pytest.approx(0.875)  # 1 - (1 - 0.875)
    assert stab.per_criterion_stability["e1"] == 1.0
    assert stab.per_criterion_stability["e7"] is None  # E6 = 0 everywhere, E7 N/A
    assert set(stab.e8_applicable_criteria) == {"e1", "e2", "e3", "e4", "e5", "e6"}
    assert stab.e8_score == pytest.approx((1.0 + 0.875 + 1.0 + 1.0 + 1.0 + 1.0) / 6)


# Case L: one failed repetition. It must not be treated as a stable identical output.
def test_case_l_failed_repetition_not_counted_as_stable_value():
    _, base = baseline_for(8)
    ok_1 = _ev(valid_raw(base), base, gid="generation_1")
    ok_2 = _ev(valid_raw(base), base, gid="generation_2")
    failed = evaluate_generation(api_error_record("generation_3"), base, KNEE_INJURY)

    stab = evaluate_profile_stability("profile_1", [ok_1, ok_2, failed])
    assert stab.evaluable_repetitions["e1"] == 2  # the failed run is not an E1 value
    assert stab.evaluable_repetitions["e5"] == 2  # nor an E5 value: E5 is N/A for API failures
    assert stab.per_criterion_stability["e1"] == 1.0
    assert failed.score("e5") is None
    assert failed.generation_failure == "API_CALL_FAILED"  # recorded separately


def test_case_l_insufficient_repetitions_yield_na_not_stability():
    _, base = baseline_for(8)
    ok = _ev(valid_raw(base), base, gid="generation_1")
    failed_a = evaluate_generation(api_error_record("generation_2"), base, KNEE_INJURY)
    failed_b = evaluate_generation(api_error_record("generation_3"), base, KNEE_INJURY)
    stab = evaluate_profile_stability("profile_1", [ok, failed_a, failed_b])
    assert stab.e8_score is None
    assert "e1" in stab.insufficient_repetitions
    assert stab.per_criterion_stability["e1"] is None


def test_case_l_all_failed_is_not_applicable_and_not_stable():
    _, base = baseline_for(8)
    fails = [evaluate_generation(api_error_record(f"generation_{i}"), base, KNEE_INJURY) for i in (1, 2, 3)]
    stab = evaluate_profile_stability("profile_1", fails)
    assert stab.e8_score is None
    assert stab.e8_applicable_criteria == []


# Case M: no injury limitation. E6 and E7 are N/A, not zero.
def test_case_m_no_injury_limitation():
    _, base = baseline_for(8, injury=None)
    s = _scores(_ev(valid_raw(base), base, injury=None))
    assert s["e6"] is None and s["e7"] is None
    assert s["e1"] == 1.0


# Case N: workout array reordered but days unchanged. E3 stays correct.
def test_case_n_reordered_array_days_unchanged():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    for w in raw["weeks"]:
        w["workouts"] = list(reversed(w["workouts"]))
    s = _scores(_ev(raw, base))
    assert s["e3"] == 1.0 and s["e1"] == 1.0


# Output contract and secrets.
def test_result_dict_contains_no_raw_payload_or_secret():
    _, base = baseline_for(8)
    ev = _ev(valid_raw(base), base)
    dumped = json.dumps(ev.to_dict())
    assert '"raw_output"' not in dumped and '"raw_text"' not in dumped
    assert "GEMINI_API_KEY" not in dumped
    assert ev.to_dict()["e1"] == 1.0


def test_n_a_is_serialized_as_null_not_zero():
    _, base = baseline_for(8, injury=None)
    d = _ev(valid_raw(base), base, injury=None).to_dict()
    assert d["e6"] is None and d["e7"] is None
