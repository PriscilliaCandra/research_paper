"""
Research harness tests. Dry run only: synthetic provider, no Gemini call, no API key.
"""
import json
from pathlib import Path

import pytest

from app import config
from app.ai import llm_service
from app.research import criteria_config as cfg
from app.research import harness as harness_mod
from app.research.experiment_fixtures import PROFILE_COUNT, build_profiles, profile_id_for
from app.research.harness import ExperimentError, ExperimentMode, run_experiment
from app.research.evaluator import evaluate_generation, record_from_generation_result
from app.research.stability import evaluate_profile_stability


@pytest.fixture(scope="module")
def dry_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("experiments")
    summary = run_experiment(experiment_id="EXP-TEST-DRY", output_root=root)
    return root / "EXP-TEST-DRY", summary


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- profiles ----------

def test_exactly_24_synthetic_profiles_with_stable_unique_ids():
    profiles = build_profiles()
    ids = [pid for pid, _ in profiles]
    assert len(profiles) == PROFILE_COUNT == 24
    assert len(set(ids)) == 24
    assert ids == [profile_id_for(i) for i in range(1, 25)]
    assert [pid for pid, _ in build_profiles()] == ids  # stable across calls


def test_profile_fixtures_are_deterministic():
    a = [p.model_dump() for _, p in build_profiles()]
    b = [p.model_dump() for _, p in build_profiles()]
    assert a == b


def test_profile_count_other_than_24_is_rejected(tmp_path):
    with pytest.raises(ExperimentError):
        run_experiment(experiment_id="EXP-BAD-COUNT", output_root=tmp_path, profiles=build_profiles()[:23])


def test_duplicate_profile_ids_are_rejected(tmp_path):
    profiles = build_profiles()
    profiles[1] = (profiles[0][0], profiles[1][1])
    with pytest.raises(ExperimentError):
        run_experiment(experiment_id="EXP-DUP", output_root=tmp_path, profiles=profiles)


# ---------- baseline ----------

def test_exactly_one_baseline_per_profile(dry_run):
    root, summary = dry_run
    baselines = sorted((root / "profiles").glob("*/baseline.json"))
    assert len(baselines) == 24
    assert summary.baseline_artifacts == 24


def test_baseline_is_generated_once_per_profile_not_per_repetition(tmp_path, monkeypatch):
    calls = []
    real = harness_mod.generate_rule_based_plan

    def counting(profile):
        calls.append(profile.plan_duration_weeks)
        return real(profile)

    monkeypatch.setattr(harness_mod, "generate_rule_based_plan", counting)
    run_experiment(experiment_id="EXP-BASELINE-COUNT", output_root=tmp_path)
    assert len(calls) == 24


def test_baseline_artifact_has_source_and_null_repetition(dry_run):
    root, _ = dry_run
    data = _load(root / "profiles" / "profile_001" / "baseline.json")
    assert data["source"] == "deterministic_baseline"
    assert data["repetition"] is None
    assert data["metadata"]["evaluator_version"] == "v2"


# ---------- repetitions ----------

def test_exactly_three_repetitions_per_profile_numbered_1_to_3(dry_run):
    root, summary = dry_run
    assert summary.gemini_artifacts == 72
    for pid in (profile_id_for(i) for i in range(1, 25)):
        reps = sorted(p.name for p in (root / "profiles" / pid).glob("repetition_*.json"))
        assert reps == ["repetition_1.json", "repetition_2.json", "repetition_3.json"], pid
        assert [_load(root / "profiles" / pid / r)["repetition"] for r in reps] == [1, 2, 3]


def test_no_duplicate_generation_ids(dry_run):
    root, _ = dry_run
    gen_ids = []
    for pid in (profile_id_for(i) for i in range(1, 25)):
        gen_ids.append(_load(root / "profiles" / pid / "baseline.json")["generation_id"])
        for r in (1, 2, 3):
            gen_ids.append(_load(root / "profiles" / pid / f"repetition_{r}.json")["generation_id"])
    assert len(gen_ids) == 96
    assert len(set(gen_ids)) == 96


def test_total_generation_artifacts_is_96(dry_run):
    root, summary = dry_run
    assert summary.total_generation_artifacts == 96
    assert len(list(root.glob("profiles/*/baseline.json"))) + len(list(root.glob("profiles/*/repetition_*.json"))) == 96


def test_manifest_states_the_design_and_counts(dry_run):
    root, _ = dry_run
    m = _load(root / "manifest.json")
    assert m["profile_count"] == 24
    assert m["repetitions_per_profile"] == 3
    assert m["expected_baseline_count"] == 24
    assert m["expected_gemini_generation_count"] == 72
    assert m["expected_total_generation_artifacts"] == 96
    assert m["design"] == "24 profiles x 3 Gemini repetitions"
    assert m["evaluator_version"] == "v2"
    assert m["mode"] == "dry_run"


# ---------- isolation ----------

def test_each_profile_uses_its_own_baseline(dry_run):
    root, _ = dry_run
    for pid in (profile_id_for(i) for i in range(1, 25)):
        base = _load(root / "profiles" / pid / "baseline.json")
        assert base["profile_id"] == pid
        for r in (1, 2, 3):
            rep = _load(root / "profiles" / pid / f"repetition_{r}.json")
            assert rep["profile_id"] == pid
            assert rep["baseline_generation_id"] == base["generation_id"]


def test_three_repetitions_of_a_profile_share_the_same_baseline_object(monkeypatch, tmp_path):
    seen = {}
    real_gen = harness_mod.generate_gemini_result

    async def spy(profile, baseline, provider_call=None):
        seen.setdefault(id(profile), []).append(id(baseline))
        return await real_gen(profile, baseline, provider_call=provider_call)

    monkeypatch.setattr(harness_mod, "generate_gemini_result", spy)
    run_experiment(experiment_id="EXP-SHARED-BASE", output_root=tmp_path)
    for baseline_ids in seen.values():
        assert len(baseline_ids) == 3
        assert len(set(baseline_ids)) == 1


# ---------- failure handling (synthetic scripted outcomes) ----------

def test_malformed_json_is_preserved_with_finish_reason_and_is_not_success(dry_run):
    root, _ = dry_run
    rep = _load(root / "profiles" / "profile_005" / "repetition_2.json")
    assert rep["status"] == "GEMINI_VALIDATION_FAILED"
    assert rep["error_category"] == "MALFORMED_JSON"
    assert rep["finish_reason"] == "MAX_TOKENS"
    assert rep["raw_text"] == '{"weeks": ['
    assert rep["parsed_output"] is None


def test_schema_invalid_output_is_preserved_as_failure(dry_run):
    root, _ = dry_run
    rep = _load(root / "profiles" / "profile_009" / "repetition_1.json")
    assert rep["status"] == "GEMINI_VALIDATION_FAILED"
    assert rep["raw_output"] is not None
    assert rep["parsed_output"] is None
    assert rep["validation_errors"] and "week count" in rep["validation_errors"][0]


def test_api_failure_is_preserved_and_not_a_baseline_success(dry_run):
    root, _ = dry_run
    rep = _load(root / "profiles" / "profile_013" / "repetition_3.json")
    assert rep["status"] == "GEMINI_API_ERROR"
    assert rep["error_category"] == "API_CALL_FAILED"
    assert rep["error"] == "ConnectionError"
    assert rep["raw_output"] is None and rep["parsed_output"] is None
    assert rep["finish_reason"] is None
    assert rep["source"] == "synthetic_gemini_dry_run"


def test_failed_generations_are_never_recorded_as_success(dry_run):
    root, summary = dry_run
    assert summary.status_counts == {"GEMINI_SUCCESS": 68, "GEMINI_VALIDATION_FAILED": 3, "GEMINI_API_ERROR": 1}
    for pid in (profile_id_for(i) for i in range(1, 25)):
        for r in (1, 2, 3):
            rep = _load(root / "profiles" / pid / f"repetition_{r}.json")
            if rep["status"] != "GEMINI_SUCCESS":
                assert rep["parsed_output"] is None


def test_api_failure_gives_e5_na_and_malformed_gives_e5_zero(dry_run):
    root, _ = dry_run
    ev = _load(root / "profiles" / "profile_013" / "evaluation.json")
    api = ev["generations"][2]
    assert api["e5"] is None
    assert api["generation_failure"] == "API_CALL_FAILED"
    assert api["e1"] is None and api["e2"] is None
    mal = _load(root / "profiles" / "profile_005" / "evaluation.json")["generations"][1]
    assert mal["e5"] == 0.0
    assert "malformed_json" in mal["e5_failure_categories"]


# ---------- evaluator integration ----------

def test_evaluation_values_match_the_frozen_evaluator_directly(dry_run):
    root, _ = dry_run
    pid = "profile_007"
    baseline_plan = _load(root / "profiles" / pid / "baseline.json")["plan"]
    stored = _load(root / "profiles" / pid / "evaluation.json")["generations"]
    profiles = dict(build_profiles())
    for r in (1, 2, 3):
        rep = _load(root / "profiles" / pid / f"repetition_{r}.json")
        from app.ai.llm_service import GenerationStatus, GeminiGenerationResult
        result = GeminiGenerationResult(
            GenerationStatus(rep["status"]), rep["model"], raw_output=rep["raw_output"],
            raw_text=rep["raw_text"], finish_reason=rep["finish_reason"],
            validated_plan=rep["parsed_output"], error_category=rep["error_category"], error=rep["error"],
        )
        record = record_from_generation_result(pid, rep["generation_id"], result)
        direct = evaluate_generation(record, baseline_plan, profiles[pid].injury_limitations).to_dict()
        for key in ("e1", "e2", "e3", "e4", "e5", "e6", "e7"):
            assert stored[r - 1][key] == direct[key], (r, key)


def test_e8_is_grouped_per_profile_across_three_repetitions(dry_run):
    root, _ = dry_run
    for pid in (profile_id_for(i) for i in range(1, 25)):
        e8 = _load(root / "profiles" / pid / "evaluation.json")["e8"]
        assert e8["profile_id"] == pid
        assert e8["generation_ids"] == [f"{pid}_gen1", f"{pid}_gen2", f"{pid}_gen3"]


def test_na_remains_na_not_zero_in_artifacts(dry_run):
    root, _ = dry_run
    # profile 4 has the sentinel "none" injury, so E6 and E7 are N/A for all three repetitions
    ev = _load(root / "profiles" / "profile_004" / "evaluation.json")
    assert all(g["e6"] is None and g["e7"] is None for g in ev["generations"])
    assert ev["e8"]["per_criterion_stability"]["e6"] is None
    assert ev["e8"]["per_criterion_stability"]["e7"] is None


def test_e8_uses_only_evaluable_repetitions(dry_run):
    root, _ = dry_run
    ev = _load(root / "profiles" / "profile_013" / "evaluation.json")  # rep 3 is an API failure
    assert ev["e8"]["evaluable_repetitions"]["e1"] == 2
    assert ev["e8"]["evaluable_repetitions"]["e5"] == 2


def test_summary_contains_no_composite_or_ranking(dry_run):
    root, _ = dry_run
    summary = _load(root / "summary.json")
    banned = {"composite", "score", "rank", "ranking", "winner", "overall"}
    assert not banned & {k.lower() for k in summary}


def test_evaluator_version_is_v2_everywhere(dry_run):
    root, _ = dry_run
    assert _load(root / "manifest.json")["evaluator_version"] == cfg.CRITERIA_VERSION == "v2"
    assert _load(root / "profiles" / "profile_001" / "evaluation.json")["criteria_version"] == "v2"


# ---------- immutability ----------

def test_existing_experiment_directory_is_never_overwritten(tmp_path):
    run_experiment(experiment_id="EXP-IMMUTABLE", output_root=tmp_path)
    before = (tmp_path / "EXP-IMMUTABLE" / "manifest.json").read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_experiment(experiment_id="EXP-IMMUTABLE", output_root=tmp_path)
    assert (tmp_path / "EXP-IMMUTABLE" / "manifest.json").read_text(encoding="utf-8") == before


def test_exclusive_write_refuses_to_overwrite_a_single_artifact(tmp_path):
    target = tmp_path / "x.json"
    harness_mod._write_exclusive(target, {"a": 1})
    with pytest.raises(FileExistsError):
        harness_mod._write_exclusive(target, {"a": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


# ---------- dry run never reaches the real provider ----------

def test_dry_run_never_invokes_the_real_gemini_call(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("real Gemini provider must not be called in dry run")

    monkeypatch.setattr(llm_service, "generate_gemini_plan", forbidden)
    monkeypatch.setattr(llm_service.httpx, "AsyncClient", forbidden)
    summary = run_experiment(experiment_id="EXP-NO-REAL", output_root=tmp_path)
    assert summary.gemini_artifacts == 72


def test_dry_run_does_not_need_or_expose_an_api_key(monkeypatch, tmp_path):
    secret = "FAKE-KEY-SENTINEL-DO-NOT-LEAK-424242"
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", secret)
    run_experiment(experiment_id="EXP-NO-KEY", output_root=tmp_path)
    for path in (tmp_path / "EXP-NO-KEY").rglob("*.json"):
        assert secret not in path.read_text(encoding="utf-8"), path


def test_dry_run_without_any_api_key_still_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "")
    summary = run_experiment(experiment_id="EXP-NO-KEY-SET", output_root=tmp_path)
    assert summary.gemini_artifacts == 72


def test_real_mode_requires_explicit_allow_flag(tmp_path):
    with pytest.raises(ExperimentError):
        run_experiment(experiment_id="EXP-REAL-GUARD", output_root=tmp_path, mode=ExperimentMode.REAL)
    assert not (tmp_path / "EXP-REAL-GUARD").exists()


def test_default_mode_is_dry_run():
    import inspect
    assert inspect.signature(run_experiment).parameters["mode"].default is ExperimentMode.DRY_RUN


# ---------- dry-run acceptance ----------

def test_dry_run_acceptance_counts(dry_run):
    root, summary = dry_run
    assert summary.profiles == 24
    assert summary.baseline_artifacts == 24
    assert summary.gemini_artifacts == 72
    assert summary.total_generation_artifacts == 96
    assert summary.evaluation_artifacts == 24
