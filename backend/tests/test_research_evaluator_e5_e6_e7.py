"""Evaluator tests: E5 (validity/completeness; N/A for generation failures), E6 (explicit injury adaptation), E7 (runner-specific anchor, conditional on E6)."""
import pytest

from app.research.evaluator import (
    GenerationRecord, evaluate_e5, evaluate_e6, evaluate_e7, evaluate_generation, is_no_injury,
)
from tests.research_fixtures import (
    KNEE_INJURY, api_error_record, baseline_for, first_training_workout, malformed_record,
    success_record, valid_raw, workout,
)


def _e5(record, base):
    result, categories, _ = evaluate_e5(record, base)
    return result.value, categories


# ---------- E5 ----------

def test_e5_valid_complete_output_is_one():
    _, base = baseline_for(8)
    assert _e5(success_record(valid_raw(base)), base) == (1.0, [])


def test_e5_malformed_json_is_zero_because_output_exists():
    _, base = baseline_for(8)
    assert _e5(malformed_record(), base) == (0.0, ["malformed_json"])


def test_e5_truncated_malformed_output_is_truncated_and_malformed():
    _, base = baseline_for(8)
    value, cats = _e5(malformed_record(finish_reason="MAX_TOKENS"), base)
    assert value == 0.0
    assert "truncated_output" in cats and "malformed_json" in cats


def test_e5_api_error_is_not_applicable_not_zero():
    _, base = baseline_for(8)
    result, categories, details = evaluate_e5(api_error_record(), base)
    assert result.value is None
    assert categories == [] and details == []


def test_e5_missing_week_is_zero():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    del raw["weeks"][3]
    value, cats = _e5(success_record(raw), base)
    assert value == 0.0 and "missing_week" in cats


def test_e5_duplicate_week_is_zero_even_though_first_occurrence_is_used_for_e1_e4():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"][4]["week_number"] = 3
    value, cats = _e5(success_record(raw), base)
    assert value == 0.0 and "duplicate_week" in cats


def test_e5_missing_schema_required_workout_field_is_zero():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    del first_training_workout(raw)["pace_target"]  # pace_target has no schema default
    value, cats = _e5(success_record(raw), base)
    assert value == 0.0 and "missing_required_field" in cats


def test_e5_blank_schema_required_string_is_zero():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    first_training_workout(raw)["pace_target"] = ""
    value, cats = _e5(success_record(raw), base)
    assert value == 0.0 and "empty_required_text" in cats


def test_e5_whitespace_only_schema_required_string_is_zero():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    first_training_workout(raw)["intensity_zone"] = "   \t "
    value, cats = _e5(success_record(raw), base)
    assert value == 0.0 and "empty_required_text" in cats


def test_e5_optional_explanatory_text_missing_or_blank_is_still_valid():
    """Explanatory text fields have schema defaults and are not E5 requirements."""
    _, base = baseline_for(8)
    raw = valid_raw(base)
    del first_training_workout(raw)["purpose"]
    first_training_workout(raw)["warmup_cooldown"] = ""
    first_training_workout(raw)["workout_execution"] = "   "
    assert _e5(success_record(raw), base) == (1.0, [])


def test_e5_valid_json_but_incomplete_content_is_zero():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"] = raw["weeks"][:5]
    value, cats = _e5(success_record(raw), base)
    assert value == 0.0 and "missing_week" in cats


def test_e5_schema_invalid_type_is_zero():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    first_training_workout(raw)["distance_km"] = "abc"
    value, cats = _e5(success_record(raw), base)
    assert value == 0.0 and "schema_invalid" in cats


def test_e5_invalid_week_number_is_zero():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"][0]["week_number"] = 0
    value, cats = _e5(success_record(raw), base)
    assert value == 0.0 and "other_structural_failure" in cats


def test_e5_top_level_not_an_object_is_schema_invalid():
    _, base = baseline_for(8)
    value, cats = _e5(success_record([]), base)
    assert value == 0.0 and cats == ["schema_invalid"]


def test_e5_optional_plan_level_text_missing_is_still_valid():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    del raw["coach_overview"]
    assert _e5(success_record(raw), base) == (1.0, [])


def test_e5_empty_optional_summary_list_is_still_valid():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["ai_personalization_summary"] = []
    assert _e5(success_record(raw), base) == (1.0, [])


def test_e5_optional_field_present_with_wrong_type_is_schema_invalid():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["ai_personalization_summary"] = "not a list"
    value, cats = _e5(success_record(raw), base)
    assert value == 0.0 and "schema_invalid" in cats


def test_e5_otherwise_valid_plan_with_schema_defaults_is_valid():
    """
    Regression: an otherwise valid plan whose optional/default explanatory fields are left at their
    schema defaults (empty strings and empty lists) still receives E5 = 1.
    """
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["coach_overview"] = ""
    raw["ai_personalization_summary"] = []
    raw["personalized_insights"] = {"pacing_strategy": "", "injury_prevention_note": "",
                                    "nutrition_and_recovery_advice": ""}
    raw["why_this_plan_was_generated"] = {"key_influencing_factors": [], "ai_personalization_additions": ""}
    for w in raw["weeks"]:
        for wk in w["workouts"]:
            wk["warmup_cooldown"] = ""
            wk["workout_execution"] = ""
            wk["recovery_instruction"] = ""
            wk["purpose"] = ""
    assert _e5(success_record(raw), base) == (1.0, [])


def test_e5_otherwise_valid_plan_with_optional_fields_omitted_is_valid():
    _, base = baseline_for(8)
    raw = {"weeks": valid_raw(base)["weeks"]}
    for w in raw["weeks"]:
        for wk in w["workouts"]:
            for key in ("warmup_cooldown", "workout_execution", "recovery_instruction", "purpose"):
                wk.pop(key, None)
    assert _e5(success_record(raw), base) == (1.0, [])


def test_e5_rest_day_text_is_not_required():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    workout(raw, 0, "Monday")["purpose"] = ""  # Monday is a rest day in the baseline
    assert _e5(success_record(raw), base) == (1.0, [])


def test_e5_truncation_with_parsed_incomplete_output_is_recorded():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["weeks"] = raw["weeks"][:5]
    record = GenerationRecord("p", "g", "GEMINI_VALIDATION_FAILED", raw, False, "MAX_TOKENS",
                              "SCHEMA_OR_CONSTRAINT", "week count")
    value, cats = _e5(record, base)
    assert value == 0.0 and "truncated_output" in cats and "missing_week" in cats


def test_e5_finish_reason_max_tokens_with_complete_parsed_output_is_still_valid():
    """Truncation-related finish reason alone is not a failure; completeness decides E5."""
    _, base = baseline_for(8)
    record = GenerationRecord("p", "g", "GEMINI_SUCCESS", valid_raw(base), False, "MAX_TOKENS", None, None)
    result, cats, _ = evaluate_e5(record, base)
    assert result.value == 1.0 and cats == []
    assert any("finishReason=MAX_TOKENS observed" in e["detail"] for e in result.evidence)


# ---------- E6: explicit injury-related plan adaptation (no anchor required) ----------

def _with_text(base, text, week=0, day_name=None):
    raw = valid_raw(base)
    target = workout(raw, week, day_name) if day_name else first_training_workout(raw, week)
    target["workout_execution"] = text
    return raw


def test_e6_test1_generic_advice_is_miss_and_e7_na():
    """Test 1: 'Listen to your body and stop if you experience pain.' -> E6 = 0, E7 = N/A."""
    _, base = baseline_for(8)
    raw = _with_text(base, "Listen to your body and stop if you experience pain.")
    ev = evaluate_generation(success_record(raw), base, KNEE_INJURY)
    assert ev.score("e6") == 0.0
    assert ev.score("e7") is None


def test_e6_test2_injury_related_adaptation_without_runner_anchor_is_e6_1_e7_0():
    """Test 2: 'Avoid high-intensity sessions due to your injury.' -> E6 = 1, E7 = 0."""
    _, base = baseline_for(8)
    raw = _with_text(base, "Avoid high-intensity sessions due to your injury.")
    ev = evaluate_generation(success_record(raw), base, KNEE_INJURY)
    assert ev.score("e6") == 1.0
    assert ev.score("e7") == 0.0


def test_e6_test3_specific_adaptation_is_e6_1_e7_1():
    """Test 3: 'Avoid downhill running because of your left knee discomfort.' -> E6 = 1, E7 = 1."""
    _, base = baseline_for(8)
    raw = _with_text(base, "Avoid downhill running because of your left knee discomfort.")
    ev = evaluate_generation(success_record(raw), base, KNEE_INJURY)
    assert ev.score("e6") == 1.0
    assert ev.score("e7") == 1.0


def test_e6_test4_no_injury_limitation_is_na_for_both():
    """Test 4: no injury limitation -> E6 = N/A, E7 = N/A."""
    _, base = baseline_for(8, injury=None)
    raw = _with_text(base, "Avoid high-intensity sessions due to your injury.")
    ev = evaluate_generation(success_record(raw), base, None)
    assert ev.score("e6") is None
    assert ev.score("e7") is None


def test_e6_spec_example_long_adaptation_is_hit_and_specific():
    _, base = baseline_for(8)
    text = ("Given your history of left knee discomfort aggravated by downhill running and speed work, "
            "today's quality session is a flat tempo effort rather than hill repeats.")
    raw = _with_text(base, text)
    ev = evaluate_generation(success_record(raw), base, KNEE_INJURY)
    assert ev.score("e6") == 1.0 and ev.score("e7") == 1.0


def test_e6_stage1_requires_injury_reference_even_when_action_and_plan_object_present():
    _, base = baseline_for(8)
    raw = _with_text(base, "Avoid downhill repeats and reduce the volume this week.")
    assert evaluate_e6(raw, KNEE_INJURY).value == 0.0


def test_e6_stage1_requires_plan_object_placing_adaptation_in_training():
    _, base = baseline_for(8)
    raw = _with_text(base, "Avoid this because of your injury.")  # action and injury reference, no plan object
    assert evaluate_e6(raw, KNEE_INJURY).value == 0.0


def test_e6_all_generic_output_is_miss():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    for w in raw["weeks"]:
        for wk in w["workouts"]:
            wk["workout_execution"] = "Listen to your body and stop if you experience pain."
    assert evaluate_e6(raw, KNEE_INJURY).value == 0.0


def test_e6_hit_anywhere_in_multi_week_output_counts():
    _, base = baseline_for(8)
    raw = _with_text(base, "Avoid downhill running because of your left knee discomfort.", week=5)
    result = evaluate_e6(raw, KNEE_INJURY)
    assert result.value == 1.0
    assert any("week 6" in e["field"] for e in result.evidence)


def test_e6_does_not_search_baseline_derived_structured_fields():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    first_training_workout(raw)["pace_target"] = "avoid due to your injury session"
    first_training_workout(raw)["intensity_zone"] = "avoid injury session"
    assert evaluate_e6(raw, KNEE_INJURY).value == 0.0


def test_e6_counts_text_in_summary_fields():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    raw["ai_personalization_summary"] = ["Avoid downhill workouts because of your knee injury."]
    assert evaluate_e6(raw, KNEE_INJURY).value == 1.0


@pytest.mark.parametrize("value", [None, "", "   ", "none", "None", " NO ", "n/a", "N/A", "na", "NA", "-"])
def test_e6_sentinels_and_empty_are_not_applicable(value):
    assert is_no_injury(value)
    _, base = baseline_for(8, injury=value)
    raw = valid_raw(base)
    e6 = evaluate_e6(raw, value)
    assert e6.value is None
    assert evaluate_e7(raw, value, e6).value is None


def test_e6_na_is_not_zero_when_no_injury():
    _, base = baseline_for(8, injury=None)
    raw = valid_raw(base)
    e6 = evaluate_e6(raw, None)
    assert e6.value is None and e6.value != 0.0


def test_non_sentinel_text_is_applicable():
    assert not is_no_injury("none of the above, but a sore heel")


def test_e6_na_without_parsed_output():
    assert evaluate_e6(None, KNEE_INJURY).value is None


# ---------- E7: runner-specific anchor, conditional on E6 ----------

def test_e7_na_when_e6_is_zero():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    e6 = evaluate_e6(raw, KNEE_INJURY)
    assert e6.value == 0.0
    assert evaluate_e7(raw, KNEE_INJURY, e6).value is None


def test_e7_na_when_e6_is_na():
    _, base = baseline_for(8)
    raw = valid_raw(base)
    e6 = evaluate_e6(raw, None)
    assert evaluate_e7(raw, None, e6).value is None


def test_e7_evidence_names_runner_anchors():
    _, base = baseline_for(8)
    raw = _with_text(base, "Avoid downhill running because of your left knee discomfort.")
    e6 = evaluate_e6(raw, KNEE_INJURY)
    e7 = evaluate_e7(raw, KNEE_INJURY, e6)
    assert e7.value == 1.0
    anchors = e7.evidence[0]["runner_anchors"]
    assert "knee" in anchors and "downhill" in anchors
    assert "running" not in anchors and "discomfort" not in anchors


def test_e7_generic_only_stored_injury_has_no_anchor_pool():
    _, base = baseline_for(8, injury="pain and fatigue")
    raw = _with_text(base, "Avoid high-intensity sessions due to your injury.")
    e6 = evaluate_e6(raw, "pain and fatigue")
    assert e6.value == 1.0
    assert evaluate_e7(raw, "pain and fatigue", e6).value == 0.0


def test_e6_e7_three_meaningful_states_are_all_reachable():
    _, base = baseline_for(8)
    cases = {
        "e6_0": ("Listen to your body and stop if you experience pain.", 0.0, None),
        "e6_1_e7_0": ("Avoid high-intensity sessions due to your injury.", 1.0, 0.0),
        "e6_1_e7_1": ("Avoid downhill running because of your left knee discomfort.", 1.0, 1.0),
    }
    for name, (text, expected_e6, expected_e7) in cases.items():
        raw = _with_text(base, text)
        ev = evaluate_generation(success_record(raw), base, KNEE_INJURY)
        assert (ev.score("e6"), ev.score("e7")) == (expected_e6, expected_e7), name


# ---------- E6/E7 final freeze: examples and four explicit states ----------

def test_e6_final_example_injury_reference_action_and_plan_object_in_one_sentence_is_hit():
    _, base = baseline_for(8)
    raw = _with_text(base, "Because of your knee injury, reduce the intensity of tomorrow's interval session.")
    ev = evaluate_generation(success_record(raw), base, KNEE_INJURY)
    assert ev.score("e6") == 1.0
    assert ev.score("e7") == 1.0
    anchor_classes = ev.criteria["e7"].evidence[0]["anchor_classes"]
    assert anchor_classes.get("knee") == "body_part"


def test_e6_terms_split_across_sentences_do_not_count():
    _, base = baseline_for(8)
    raw = _with_text(base, "Your injury needs attention. Reduce the intensity of tomorrow's interval session.")
    assert evaluate_e6(raw, KNEE_INJURY).value == 0.0


def test_e7_runner_limitation_class_is_empty_in_v2():
    from app.research import criteria_config as cfg
    assert cfg.E7_ANCHOR_CODEBOOK_V2["runner_limitation"] == frozenset()


def test_e7_generic_running_vocabulary_is_never_an_anchor():
    from app.research import criteria_config as cfg
    anchors = set().union(*cfg.E7_ANCHOR_CODEBOOK_V2.values())
    for generic in ("running", "run", "workout", "session", "pace", "training", "intensity",
                    "recovery", "pain", "injury", "discomfort"):
        assert generic not in anchors


def test_e7_codebook_classes_are_disjoint_from_generic_vocabulary():
    from app.research import criteria_config as cfg
    anchors = set().union(*cfg.E7_ANCHOR_CODEBOOK_V2.values())
    assert anchors.isdisjoint(cfg.E7_GENERIC_VOCABULARY_V2)


@pytest.mark.parametrize("text,stored,expected_e6,expected_e7", [
    # State 1: generic advice -> E6 = 0, E7 = N/A
    ("Listen to your body and stop if you experience pain.", KNEE_INJURY, 0.0, None),
    # State 2: generic injury adaptation -> E6 = 1, E7 = 0
    ("Avoid high-intensity sessions due to your injury.", KNEE_INJURY, 1.0, 0.0),
    # State 3: runner-specific injury adaptation -> E6 = 1, E7 = 1
    ("Avoid downhill running because of your left knee discomfort.", KNEE_INJURY, 1.0, 1.0),
    # State 4: no injury limitation -> E6 = N/A, E7 = N/A
    ("Avoid high-intensity sessions due to your injury.", None, None, None),
])
def test_four_e6_e7_states_are_each_explicitly_reachable(text, stored, expected_e6, expected_e7):
    _, base = baseline_for(8, injury=stored)
    raw = _with_text(base, text)
    ev = evaluate_generation(success_record(raw), base, stored)
    assert ev.score("e6") == expected_e6
    assert ev.score("e7") == expected_e7


def test_e7_generic_vocabulary_matches_the_specified_list():
    from app.research import criteria_config as cfg
    specified = {"running", "run", "workout", "session", "pace", "training", "intensity",
                 "recovery", "pain", "injury", "discomfort"}
    assert specified <= cfg.E7_GENERIC_VOCABULARY_V2


# ---------- E7 final cleanup: contextual gating for "back" and "track" ----------

LOWER_BACK_INJURY = "mild lower back pain, aggravated by downhill running"
TRACK_INJURY = "mild injury on track workouts"


def test_back_in_lower_back_phrase_is_a_valid_anchor():
    _, base = baseline_for(8, injury=LOWER_BACK_INJURY)
    raw = _with_text(base, "Avoid running because of your lower back injury.")
    e6 = evaluate_e6(raw, LOWER_BACK_INJURY)
    e7 = evaluate_e7(raw, LOWER_BACK_INJURY, e6)
    assert e6.value == 1.0
    assert e7.value == 1.0
    assert e7.evidence[0]["anchor_classes"].get("back") == "body_part"


def test_standalone_back_is_not_an_anchor():
    from app.research.evaluator import _e7_anchor_tokens
    assert "back" not in _e7_anchor_tokens("Take a break and get back to running gradually.")
    _, base = baseline_for(8, injury=LOWER_BACK_INJURY)
    raw = _with_text(base, "Reduce running and get back to it gradually because of your injury.")
    e6 = evaluate_e6(raw, LOWER_BACK_INJURY)
    e7 = evaluate_e7(raw, LOWER_BACK_INJURY, e6)
    assert e6.value == 1.0  # reduce + running + injury in one sentence
    assert e7.value == 0.0  # no valid anchor: "back" here is not the lower back


def test_back_without_lower_is_not_an_anchor_even_in_stored_text():
    from app.research.evaluator import _e7_anchor_tokens
    assert "back" not in _e7_anchor_tokens("mild back pain aggravated by running")
    _, base = baseline_for(8, injury="mild back pain aggravated by running")
    raw = _with_text(base, "Avoid running because of your back injury.")
    e6 = evaluate_e6(raw, "mild back pain aggravated by running")
    assert e6.value == 1.0
    assert evaluate_e7(raw, "mild back pain aggravated by running", e6).value == 0.0


def test_track_in_training_context_with_knee_anchor_is_e7_1_and_track_is_not_the_anchor():
    _, base = baseline_for(8)
    raw = _with_text(base, "Avoid track workouts because of your knee injury.")
    e6 = evaluate_e6(raw, KNEE_INJURY)
    e7 = evaluate_e7(raw, KNEE_INJURY, e6)
    assert e6.value == 1.0
    assert e7.value == 1.0
    assert e7.evidence[0]["runner_anchors"] == ["knee"]  # "track" is not in the stored text here


def test_track_as_stored_training_anchor_counts_only_in_training_context():
    from app.research.evaluator import _e7_anchor_tokens
    assert _e7_anchor_tokens(TRACK_INJURY) == {"track": "trigger_activity"}
    assert "track" not in _e7_anchor_tokens("Track your recovery over the next week.")


def test_track_in_non_training_language_is_not_an_anchor():
    _, base = baseline_for(8, injury=TRACK_INJURY)
    raw = _with_text(base, "Track your recovery over the next week and reduce volume because of your injury.")
    e6 = evaluate_e6(raw, TRACK_INJURY)
    e7 = evaluate_e7(raw, TRACK_INJURY, e6)
    assert e6.value == 1.0  # reduce + volume + injury in one sentence
    assert e7.value == 0.0  # "track" here is not a training/location anchor


@pytest.mark.parametrize("anchor", [
    "knee", "ankle", "hamstring", "achilles", "downhill", "uphill", "pavement", "trail", "sprint", "stairs",
])
def test_existing_legitimate_anchors_still_work(anchor):
    injury = f"mild {anchor} injury aggravated by running"
    _, base = baseline_for(8, injury=injury)
    raw = _with_text(base, f"Avoid running because of your {anchor} injury.")
    e6 = evaluate_e6(raw, injury)
    assert e6.value == 1.0
    assert evaluate_e7(raw, injury, e6).value == 1.0
