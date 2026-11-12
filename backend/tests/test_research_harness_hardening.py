"""Hardening tests: provider guard (Fix 1) and completion/reconciliation (Fix 2). No Gemini calls."""
import asyncio
import json
from pathlib import Path

import pytest

from app.ai import llm_service
from app.ai.llm_service import GenerationStatus, generate_gemini_result
from app.research import harness
from app.research.experiment_fixtures import build_profile
from app.research.harness import COMPLETE_MARKER, ExperimentError, ExperimentMode, run_experiment
from app.research.synthetic_provider import SyntheticScript, make_synthetic_provider
from app.rules.generator import generate_rule_based_plan


def _no_network(monkeypatch):
    async def forbidden(*_a, **_k):
        raise AssertionError("real Gemini call attempted")
    monkeypatch.setattr(llm_service, "generate_gemini_plan", forbidden)


# ---------- Fix 1: provider guard ----------

def test_dry_run_synthetic_provider_works_without_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_service.settings, "GEMINI_API_KEY", "")
    _no_network(monkeypatch)
    summary = run_experiment(experiment_id="g1", output_root=tmp_path)
    assert summary.gemini_artifacts == 72


def test_default_production_path_still_requires_api_key(monkeypatch):
    monkeypatch.setattr(llm_service.settings, "GEMINI_API_KEY", "")
    _no_network(monkeypatch)
    profile = build_profile(1)
    baseline = generate_rule_based_plan(profile)
    result = asyncio.run(generate_gemini_result(profile, baseline))
    assert result.status is GenerationStatus.GEMINI_API_ERROR
    assert result.error_category == "CONFIG_MISSING_KEY"


def test_arbitrary_injected_provider_cannot_bypass_real_mode_protection(monkeypatch):
    monkeypatch.setattr(llm_service.settings, "GEMINI_API_KEY", "")
    _no_network(monkeypatch)
    calls = []

    async def fake_provider(_prompt):
        calls.append(1)
        return {}

    profile = build_profile(1)
    baseline = generate_rule_based_plan(profile)
    with pytest.raises(ValueError):
        asyncio.run(generate_gemini_result(profile, baseline, provider_call=fake_provider))
    assert calls == []


def test_arbitrary_provider_rejected_even_when_api_key_is_present(monkeypatch):
    monkeypatch.setattr(llm_service.settings, "GEMINI_API_KEY", "not-a-real-key")
    _no_network(monkeypatch)

    async def fake_provider(_prompt):
        raise AssertionError("injected provider must not run")

    profile = build_profile(1)
    baseline = generate_rule_based_plan(profile)
    with pytest.raises(ValueError):
        asyncio.run(generate_gemini_result(profile, baseline, provider_call=fake_provider))


def test_synthetic_provider_is_the_only_accepted_injection(monkeypatch):
    monkeypatch.setattr(llm_service.settings, "GEMINI_API_KEY", "")
    _no_network(monkeypatch)
    profile = build_profile(1)
    baseline = generate_rule_based_plan(profile)
    provider = make_synthetic_provider(SyntheticScript(1, 1, "valid", baseline, profile.injury_limitations))
    result = asyncio.run(generate_gemini_result(profile, baseline, provider_call=provider))
    assert result.status is GenerationStatus.GEMINI_SUCCESS


def test_production_caller_still_omits_provider_call():
    source = Path(llm_service.__file__).read_text(encoding="utf-8")
    assert "generate_gemini_result(profile, rule_plan)" in source


# ---------- Fix 2: completion and reconciliation ----------

def _write_order(monkeypatch):
    order = []
    real_write = harness._write_exclusive

    def recording(path, payload):
        order.append(path.name)
        real_write(path, payload)

    monkeypatch.setattr(harness, "_write_exclusive", recording)
    return order


def test_complete_run_creates_completion_marker_last(tmp_path, monkeypatch):
    order = _write_order(monkeypatch)
    run_experiment(experiment_id="c1", output_root=tmp_path)
    root = tmp_path / "c1"
    assert (root / COMPLETE_MARKER).exists()
    assert order[-1] == COMPLETE_MARKER
    assert order[-2] == "summary.json"


def test_summary_reflects_persisted_artifacts(tmp_path):
    summary = run_experiment(experiment_id="s1", output_root=tmp_path)
    on_disk = json.loads((tmp_path / "s1" / "summary.json").read_text(encoding="utf-8"))
    assert on_disk["baseline_artifacts"] == len(list((tmp_path / "s1" / "profiles").glob("*/baseline.json"))) == 24
    assert on_disk["gemini_artifacts"] == len(list((tmp_path / "s1" / "profiles").glob("*/repetition_*.json"))) == 72
    assert on_disk["evaluation_artifacts"] == 24
    assert on_disk["total_generation_artifacts"] == 96
    assert sum(on_disk["status_counts"].values()) == 72
    assert summary.total_generation_artifacts == 96


def test_partial_run_has_no_completion_marker(tmp_path, monkeypatch):
    async def interrupted(*_a, **_k):
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(harness, "_run_all", interrupted)
    with pytest.raises(RuntimeError):
        run_experiment(experiment_id="p1", output_root=tmp_path)
    root = tmp_path / "p1"
    assert (root / "manifest.json").exists()
    assert not (root / COMPLETE_MARKER).exists()
    assert not (root / "summary.json").exists()


def test_missing_artifact_prevents_completion(tmp_path, monkeypatch):
    real_run_all = harness._run_all

    async def drop_one(*args, **kwargs):
        result = await real_run_all(*args, **kwargs)
        (tmp_path / "m1" / "profiles" / "profile_001" / "repetition_2.json").unlink()
        return result

    monkeypatch.setattr(harness, "_run_all", drop_one)
    with pytest.raises(ExperimentError, match="count mismatch"):
        run_experiment(experiment_id="m1", output_root=tmp_path)
    root = tmp_path / "m1"
    assert not (root / COMPLETE_MARKER).exists()
    assert not (root / "summary.json").exists()


def test_existing_experiment_directory_is_not_overwritten(tmp_path):
    run_experiment(experiment_id="o1", output_root=tmp_path)
    marker_before = (tmp_path / "o1" / COMPLETE_MARKER).read_bytes()
    with pytest.raises(FileExistsError):
        run_experiment(experiment_id="o1", output_root=tmp_path)
    assert (tmp_path / "o1" / COMPLETE_MARKER).read_bytes() == marker_before


def test_rerunning_same_id_fails_safely_without_touching_existing_artifacts(tmp_path):
    run_experiment(experiment_id="r1", output_root=tmp_path)
    manifest = (tmp_path / "r1" / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        run_experiment(experiment_id="r1", output_root=tmp_path)
    assert (tmp_path / "r1" / "manifest.json").read_bytes() == manifest
