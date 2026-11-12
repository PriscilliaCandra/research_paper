"""Evaluator tests: E1 (structured pace/zone), E2 (±0.15 km weekly volume), E3 (day-based structure), E4 (phase only)."""
import copy

import pytest

from app.research import evaluator as ev
from app.research.evaluator import evaluate_e1, evaluate_e2, evaluate_e3, evaluate_e4
from tests.research_fixtures import baseline_for, valid_raw, first_training_workout, workout, KNEE_INJURY


# ---------- E1 ----------

def test_e1_exact_match_is_one():
    _, base = baseline_for(8)
    assert evaluate_e1(base, valid_raw(base)).value == 1.0


def test_e1_one_pace_mismatch():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    first_training_workout(raw)["pace_target"] = "05:00 - 05:10 /km"
    result = evaluate_e1(base, raw)
    assert result.value == pytest.approx(63 / 64)  # 32 applicable workouts x 2 fields (rest days excluded)
    assert any(e["field"] == "pace_target" and e["match"] is False for e in result.evidence)


def test_e1_missing_structured_field_is_violation():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    del first_training_workout(raw)["intensity_zone"]
    assert evaluate_e1(base, raw).value == pytest.approx(63 / 64)


def test_e1_multiple_mismatches():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    first_training_workout(raw, 0)["pace_target"] = "x"
    first_training_workout(raw, 1)["pace_target"] = "y"
    first_training_workout(raw, 2)["intensity_zone"] = "z"
    assert evaluate_e1(base, raw).value == pytest.approx(61 / 64)


def test_e1_na_when_no_parsed_output():
    _, base = baseline_for(8)
    assert evaluate_e1(base, None).value is None


def test_e1_na_when_baseline_has_no_structured_fields():
    _, base = baseline_for(8)
    empty = copy.deepcopy(base)
    for w in empty["weeks"]:
        w["workouts"] = []
    assert evaluate_e1(empty, valid_raw(base)).value is None


def test_e1_free_text_is_not_a_structured_match():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["coach_overview"] = "run comfortably at a moderate effort"
    first_training_workout(raw)["workout_execution"] = "challenging but controlled"
    assert evaluate_e1(base, raw).value == 1.0


def test_e1_has_no_numerical_tolerance():
    import re
    _, base = baseline_for(8)
    raw = valid_raw(base)
    w = first_training_workout(raw)
    match = re.search(r"(\d{1,2}):(\d{2})", w["pace_target"])
    seconds = int(match.group(1)) * 60 + int(match.group(2)) + 1  # one second slower
    shifted = f"{seconds // 60:02d}:{seconds % 60:02d}"
    w["pace_target"] = w["pace_target"][: match.start()] + shifted + w["pace_target"][match.end():]
    assert evaluate_e1(base, raw).value == pytest.approx(63 / 64)


def test_e1_matches_by_day_not_array_position():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"][0]["workouts"] = list(reversed(raw["weeks"][0]["workouts"]))
    assert evaluate_e1(base, raw).value == 1.0


# ---------- E2 ----------

def _bump_week(raw, index, delta):
    raw["weeks"][index]["weekly_mileage_km"] = round(raw["weeks"][index]["weekly_mileage_km"] + delta, 6)


def test_e2_exact_match_is_one():
    _, base = baseline_for(8)
    assert evaluate_e2(base, valid_raw(base)).value == 1.0


@pytest.mark.parametrize("delta", [0.15, -0.15])
def test_e2_boundary_within_tolerance_passes(delta):
    _, base = baseline_for(8)
    raw = valid_raw(base)
    _bump_week(raw, 0, delta)
    assert evaluate_e2(base, raw).value == 1.0


@pytest.mark.parametrize("delta", [0.1501, -0.1501])
def test_e2_just_outside_tolerance_fails(delta):
    _, base = baseline_for(8)
    raw = valid_raw(base)
    _bump_week(raw, 0, delta)
    assert evaluate_e2(base, raw).value == pytest.approx(7 / 8)


def test_e2_missing_week_fails():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    del raw["weeks"][2]
    result = evaluate_e2(base, raw)
    assert result.value == pytest.approx(7 / 8)
    assert any(e["reason"] == "missing week" for e in result.evidence)


def test_e2_multiple_weeks_out_of_tolerance():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    _bump_week(raw, 1, 0.5)
    _bump_week(raw, 4, -0.2)
    assert evaluate_e2(base, raw).value == pytest.approx(6 / 8)


def test_e2_matches_weeks_by_week_number_not_position():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"] = list(reversed(raw["weeks"]))
    assert evaluate_e2(base, raw).value == 1.0


def test_e2_has_no_relative_percentage_tolerance():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    _bump_week(raw, 0, 0.2)  # under 1% of ~26 km, but more than 0.15 km absolute
    assert evaluate_e2(base, raw).value == pytest.approx(7 / 8)


def test_e2_na_without_parsed_output():
    _, base = baseline_for(8)
    assert evaluate_e2(base, None).value is None


# ---------- E3 ----------

def test_e3_exact_structure_is_one():
    _, base = baseline_for(8)
    assert evaluate_e3(base, valid_raw(base)).value == 1.0


def test_e3_reordered_array_with_same_days_still_matches():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"][3]["workouts"] = list(reversed(raw["weeks"][3]["workouts"]))
    assert evaluate_e3(base, raw).value == 1.0


def test_e3_workout_moved_to_another_day_fails():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    tuesday = workout(raw, 0, "Tuesday")
    wednesday = workout(raw, 0, "Wednesday")
    tuesday["day"], wednesday["day"] = "Wednesday", "Tuesday"
    result = evaluate_e3(base, raw)
    assert result.value < 1.0
    failing_days = {e["day"] for e in result.evidence}
    assert {"Tuesday", "Wednesday"} <= failing_days


def test_e3_missing_workout_fails():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"][0]["workouts"] = [w for w in raw["weeks"][0]["workouts"] if w["day"] != "Tuesday"]
    result = evaluate_e3(base, raw)
    assert result.value == pytest.approx(172 / 176)
    assert any(e["element"] == "presence" and e["day"] == "Tuesday" for e in result.evidence)


def test_e3_rest_day_mismatch_fails():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    workout(raw, 0, "Monday")["workout_type"] = "Easy Aerobic Run"  # baseline Monday is rest
    result = evaluate_e3(base, raw)
    assert any(e["element"] == "rest_status" and e["day"] == "Monday" for e in result.evidence)
    assert result.value < 1.0


def test_e3_workout_type_mismatch_fails():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    tue = workout(raw, 0, "Tuesday")
    tue["workout_type"] = "Threshold Run"
    result = evaluate_e3(base, raw)
    assert result.value == pytest.approx(175 / 176)
    assert [e["element"] for e in result.evidence] == ["workout_type"]


def test_e3_spec_example_swapped_interval_and_rest_is_detected():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    tue = workout(raw, 0, "Tuesday")
    wed = workout(raw, 0, "Wednesday")
    tue["workout_type"], wed["workout_type"] = wed["workout_type"], tue["workout_type"]
    assert evaluate_e3(base, raw).value < 1.0


def test_e3_does_not_depend_on_array_index():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"][0]["workouts"].insert(0, raw["weeks"][0]["workouts"].pop())
    assert evaluate_e3(base, raw).value == 1.0


# ---------- E4 ----------

def _phase_baseline(phases):
    return {"weeks": [{"week_number": i + 1, "phase": p, "weekly_mileage_km": 20.0, "workouts": []}
                      for i, p in enumerate(phases)]}


def _phase_raw(phases, mileages=None):
    mileages = mileages or [20.0] * len(phases)
    return {"weeks": [{"week_number": i + 1, "phase": p, "weekly_mileage_km": m, "workouts": []}
                      for i, (p, m) in enumerate(zip(phases, mileages))]}


def test_e4_exact_trajectory_is_one():
    phases = ["Progressive Build Phase"] * 3 + ["Recovery Cutback Week", "Progressive Build Phase",
                                               "Progressive Build Phase", "Taper Phase", "Race Week & Taper"]
    _, base = baseline_for(8)
    assert evaluate_e4(base, valid_raw(base)).value == 1.0
    assert evaluate_e4(_phase_baseline(phases), _phase_raw(phases)).value == 1.0


def test_e4_one_phase_mismatch():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"][3]["phase"] = "Progressive Build Phase"  # week 4 should be cutback
    assert evaluate_e4(base, raw).value == pytest.approx(7 / 8)


def test_e4_multiple_phase_mismatches():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"][3]["phase"] = "Progressive Build Phase"
    raw["weeks"][6]["phase"] = "Race Week & Taper"
    assert evaluate_e4(base, raw).value == pytest.approx(6 / 8)


def test_e4_missing_week_counts_as_mismatch():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    del raw["weeks"][5]
    assert evaluate_e4(base, raw).value == pytest.approx(7 / 8)


def test_e4_extra_week_does_not_add_positions():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"].append({"week_number": 9, "phase": "Extra", "weekly_mileage_km": 1.0, "workouts": []})
    assert evaluate_e4(base, raw).value == 1.0


def test_e4_spec_example_four_of_five():
    base = _phase_baseline(["Build", "Build", "Cutback", "Build", "Taper"])
    raw = _phase_raw(["Build", "Build", "Build", "Build", "Taper"])
    assert evaluate_e4(base, raw).value == pytest.approx(0.80)


def test_e4_phase_sequence_preserved_while_volume_differs_and_e4_ignores_volume():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    for w in raw["weeks"]:
        w["weekly_mileage_km"] = round(w["weekly_mileage_km"] + 1.0, 1)
    assert evaluate_e4(base, raw).value == 1.0
    assert evaluate_e2(base, raw).value == 0.0


def test_e4_na_without_parsed_output():
    _, base = baseline_for(8)
    assert evaluate_e4(base, None).value is None


# ---------- normalization (correction pass) ----------

def test_e1_trims_leading_and_trailing_whitespace_before_exact_match():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    w = first_training_workout(raw)
    w["pace_target"] = "  " + w["pace_target"] + "  "
    assert evaluate_e1(base, raw).value == 1.0


def test_e1_internal_whitespace_difference_is_still_a_mismatch():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    w = first_training_workout(raw)
    assert " " in w["pace_target"]
    w["pace_target"] = w["pace_target"].replace(" ", "  ", 1)
    assert evaluate_e1(base, raw).value == pytest.approx(63 / 64)


def test_e3_workout_type_case_difference_is_not_a_structural_failure():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    tue = workout(raw, 0, "Tuesday")
    tue["workout_type"] = tue["workout_type"].upper()
    assert evaluate_e3(base, raw).value == 1.0


def test_e3_rest_status_recognized_regardless_of_case():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    workout(raw, 0, "Monday")["workout_type"] = "rest & recovery"
    assert evaluate_e3(base, raw).value == 1.0


def test_e3_case_normalization_does_not_hide_a_real_type_change():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    workout(raw, 0, "Tuesday")["workout_type"] = "Threshold Run"
    assert evaluate_e3(base, raw).value == pytest.approx(175 / 176)


def test_e1_pace_is_not_case_folded():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    w = first_training_workout(raw)
    w["intensity_zone"] = w["intensity_zone"].swapcase()
    assert evaluate_e1(base, raw).value == pytest.approx(63 / 64)


# ---------- E1 rest-day exclusion (final freeze) ----------

def test_e1_rest_day_placeholders_are_excluded_from_denominator():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    result = evaluate_e1(base, raw)
    scored_days = {(e["week_number"], e["day"]) for e in result.evidence}
    assert len(result.evidence) == 64
    rest_days = {(w["week_number"], wk["day"]) for w in base["weeks"] for wk in w["workouts"]
                 if wk["workout_type"] == "Rest & Recovery"}
    assert scored_days.isdisjoint(rest_days)


def test_e1_changing_a_rest_day_placeholder_does_not_affect_e1():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    workout(raw, 0, "Monday")["pace_target"] = "05:00 /km"  # Monday is rest in the baseline
    workout(raw, 0, "Monday")["intensity_zone"] = "Zone 4"
    assert evaluate_e1(base, raw).value == 1.0


def test_e1_rest_placeholder_na_equals_na_is_not_a_success_signal():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    result = evaluate_e1(base, raw)
    assert all(e["baseline"] != "N/A" for e in result.evidence)


def test_e1_only_rest_days_is_not_applicable():
    _, base = baseline_for(8)
    only_rest = copy.deepcopy(base)
    for w in only_rest["weeks"]:
        for wk in w["workouts"]:
            wk["workout_type"] = "Rest & Recovery"
            wk["pace_target"] = "N/A"
            wk["intensity_zone"] = "Rest (Zone 1 or Complete Rest)"
    assert evaluate_e1(only_rest, valid_raw(base)).value is None


def test_e1_workout_with_applicable_pace_still_uses_exact_normalized_match():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    first_training_workout(raw)["pace_target"] = first_training_workout(raw)["pace_target"] + "x"
    assert evaluate_e1(base, raw).value == pytest.approx(63 / 64)
