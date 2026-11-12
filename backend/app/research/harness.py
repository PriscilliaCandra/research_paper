"""
Research experiment harness (research infrastructure only; not a production path).

Locked design: 24 profiles; 1 deterministic baseline per profile (generated once, never regenerated);
3 Gemini repetitions per profile through the research path (generate_gemini_result), 72 in total;
E1–E7 per generation via the frozen evaluator; E8 per profile via the frozen stability module.

Modes
- DRY_RUN (default): synthetic deterministic provider; never reaches the real Gemini call.
- REAL: real Gemini provider. Requires allow_real=True. Not invoked by tests or by this task.

Artifacts are JSON files under <output_root>/<experiment_id>/. Every file is created with
exclusive-create semantics: an existing artifact is never overwritten.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.ai.llm_service import generate_gemini_result
from app.rules.generator import generate_rule_based_plan
from app.research import criteria_config as cfg
from app.research.evaluator import evaluate_generation, record_from_generation_result
from app.research.experiment_fixtures import PROFILE_COUNT, build_profiles
from app.research.stability import evaluate_profile_stability
from app.research.synthetic_provider import SyntheticScript, make_synthetic_provider, scripted_kind
from app.schemas import RunnerProfileCreate

HARNESS_VERSION = "1"
REPETITIONS_PER_PROFILE = 3
DRY_RUN_CLOCK_VALUE = "2026-01-01T00:00:00+00:00"
DESIGN_STATEMENT = "24 profiles x 3 Gemini repetitions"


class ExperimentMode(str, Enum):
    DRY_RUN = "dry_run"
    REAL = "real"


class ExperimentError(Exception):
    """Raised for invalid experiment configuration (never for an individual generation failure)."""


@dataclass(frozen=True)
class ExperimentSummary:
    experiment_id: str
    mode: str
    profiles: int
    baseline_artifacts: int
    gemini_artifacts: int
    evaluation_artifacts: int
    status_counts: Dict[str, int]

    @property
    def total_generation_artifacts(self) -> int:
        return self.baseline_artifacts + self.gemini_artifacts


def _write_exclusive(path: Path, payload: Dict[str, Any]) -> None:
    """Creates a new JSON artifact. Fails if the path already exists (never overwrites)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


COMPLETE_MARKER = "COMPLETE"


def _count_persisted_artifacts(root: Path) -> Dict[str, Any]:
    """Counts artifacts on disk and tallies repetition statuses from the persisted files."""
    profiles_dir = root / "profiles"
    status_counts: Dict[str, int] = {}
    for path in profiles_dir.glob("*/repetition_*.json"):
        status = json.loads(path.read_text(encoding="utf-8"))["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "baseline": len(list(profiles_dir.glob("*/baseline.json"))),
        "repetition": len(list(profiles_dir.glob("*/repetition_*.json"))),
        "evaluation": len(list(profiles_dir.glob("*/evaluation.json"))),
        "status_counts": dict(sorted(status_counts.items())),
    }


def _default_real_clock() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _run_profile(
    experiment_id: str,
    mode: ExperimentMode,
    root: Path,
    profile_id: str,
    profile_index: int,
    profile: RunnerProfileCreate,
    clock: Callable[[], str],
    model_identifier: str,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    baseline = generate_rule_based_plan(profile)  # deterministic; generated once per profile
    baseline_generation_id = f"{profile_id}_baseline"
    profile_dir = root / "profiles" / profile_id
    meta = {"evaluator_version": cfg.CRITERIA_VERSION, "harness_version": HARNESS_VERSION,
            "mode": mode.value}

    _write_exclusive(profile_dir / "baseline.json", {
        "experiment_id": experiment_id,
        "profile_id": profile_id,
        "source": "deterministic_baseline",
        "repetition": None,
        "generation_id": baseline_generation_id,
        "plan": baseline,
        "metadata": {**meta, "generated_at": clock()},
    })

    statuses: List[str] = []
    evaluations = []
    for repetition in range(1, REPETITIONS_PER_PROFILE + 1):
        generation_id = f"{profile_id}_gen{repetition}"
        if mode is ExperimentMode.DRY_RUN:
            script = SyntheticScript(profile_index, repetition, scripted_kind(profile_index, repetition),
                                     baseline, profile.injury_limitations)
            provider_call = make_synthetic_provider(script)
            source = "synthetic_gemini_dry_run"
        else:
            provider_call = None  # real research path: the real Gemini call with its key check
            source = "gemini"
        generated_at = clock()
        result = await generate_gemini_result(profile, baseline, provider_call=provider_call)
        record = record_from_generation_result(profile_id, generation_id, result)
        statuses.append(result.status.value)
        _write_exclusive(profile_dir / f"repetition_{repetition}.json", {
            "experiment_id": experiment_id,
            "profile_id": profile_id,
            "repetition": repetition,
            "generation_id": generation_id,
            "baseline_generation_id": baseline_generation_id,
            "source": source,
            "status": result.status.value,
            "finish_reason": result.finish_reason,
            "model": model_identifier if mode is ExperimentMode.DRY_RUN else result.model,
            "error_category": result.error_category,
            "error": result.error,
            "raw_output": result.raw_output,
            "raw_text": result.raw_text,
            "parsed_output": result.validated_plan,
            "validation_errors": [result.error] if result.status.value == "GEMINI_VALIDATION_FAILED" and result.error else [],
            "metadata": {**meta, "generated_at": generated_at},
        })
        evaluations.append(evaluate_generation(record, baseline, profile.injury_limitations))

    stability = evaluate_profile_stability(profile_id, evaluations)
    _write_exclusive(profile_dir / "evaluation.json", {
        "experiment_id": experiment_id,
        "profile_id": profile_id,
        "criteria_version": cfg.CRITERIA_VERSION,
        "generations": [e.to_dict() for e in evaluations],
        "e8": stability.to_dict(),
        "metadata": meta,
    })
    return statuses, [e.to_dict() for e in evaluations]


async def _run_all(
    experiment_id: str,
    mode: ExperimentMode,
    root: Path,
    profiles: Sequence[Tuple[str, RunnerProfileCreate]],
    clock: Callable[[], str],
    model_identifier: str,
) -> List[str]:
    all_statuses: List[str] = []
    for index, (profile_id, profile) in enumerate(profiles, start=1):
        statuses, _ = await _run_profile(experiment_id, mode, root, profile_id, index, profile,
                                         clock, model_identifier)
        all_statuses.extend(statuses)
    return all_statuses


def run_experiment(
    *,
    experiment_id: str,
    output_root: Path,
    mode: ExperimentMode = ExperimentMode.DRY_RUN,
    allow_real: bool = False,
    clock: Optional[Callable[[], str]] = None,
    profiles: Optional[Sequence[Tuple[str, RunnerProfileCreate]]] = None,
) -> ExperimentSummary:
    if mode is ExperimentMode.REAL and not allow_real:
        raise ExperimentError("REAL mode requires allow_real=True; dry run is the default.")
    profiles = list(profiles) if profiles is not None else build_profiles()
    if len(profiles) != PROFILE_COUNT:
        raise ExperimentError(f"expected {PROFILE_COUNT} profiles, got {len(profiles)}")
    ids = [pid for pid, _ in profiles]
    if len(set(ids)) != len(ids):
        raise ExperimentError("duplicate profile_id")

    root = Path(output_root) / experiment_id
    if root.exists():
        raise FileExistsError(f"experiment directory already exists: {root}")
    root.mkdir(parents=True, exist_ok=False)

    if mode is ExperimentMode.DRY_RUN:
        clock_fn = clock or (lambda: DRY_RUN_CLOCK_VALUE)
        model_identifier = "synthetic"
    else:
        from app.config import settings
        clock_fn = clock or _default_real_clock
        model_identifier = f"Gemini ({settings.GEMINI_MODEL})"

    _write_exclusive(root / "manifest.json", {
        "experiment_id": experiment_id,
        "description": "Gemini personalization vs deterministic baseline (controlled reference, not ground truth).",
        "mode": mode.value,
        "design": DESIGN_STATEMENT,
        "profile_count": PROFILE_COUNT,
        "repetitions_per_profile": REPETITIONS_PER_PROFILE,
        "expected_baseline_count": PROFILE_COUNT,
        "expected_gemini_generation_count": PROFILE_COUNT * REPETITIONS_PER_PROFILE,
        "expected_total_generation_artifacts": PROFILE_COUNT + PROFILE_COUNT * REPETITIONS_PER_PROFILE,
        "evaluator_version": cfg.CRITERIA_VERSION,
        "harness_version": HARNESS_VERSION,
        "model_identifier": model_identifier,
        "created_at": clock_fn(),
    })

    asyncio.run(_run_all(experiment_id, mode, root, profiles, clock_fn, model_identifier))

    # Counts come from the files actually persisted on disk, not from in-memory results.
    # Any mismatch raises before summary.json or COMPLETE is written, leaving the run incomplete.
    counted = _count_persisted_artifacts(root)
    expected = {
        "baseline": len(profiles),
        "repetition": len(profiles) * REPETITIONS_PER_PROFILE,
        "evaluation": len(profiles),
    }
    if counted["baseline"] != expected["baseline"] or counted["repetition"] != expected["repetition"] \
            or counted["evaluation"] != expected["evaluation"]:
        raise ExperimentError(f"artifact count mismatch: expected {expected}, found {counted}; run left incomplete")
    status_counts = counted["status_counts"]
    summary = ExperimentSummary(
        experiment_id=experiment_id,
        mode=mode.value,
        profiles=len(profiles),
        baseline_artifacts=counted["baseline"],
        gemini_artifacts=counted["repetition"],
        evaluation_artifacts=counted["evaluation"],
        status_counts=status_counts,
    )
    _write_exclusive(root / "summary.json", {
        "experiment_id": experiment_id,
        "mode": mode.value,
        "profiles": summary.profiles,
        "baseline_artifacts": summary.baseline_artifacts,
        "gemini_artifacts": summary.gemini_artifacts,
        "total_generation_artifacts": summary.total_generation_artifacts,
        "evaluation_artifacts": summary.evaluation_artifacts,
        "status_counts": dict(sorted(status_counts.items())),
    })
    _write_exclusive(root / COMPLETE_MARKER, {"experiment_id": experiment_id, "status": "complete",
                                              "total_generation_artifacts": summary.total_generation_artifacts})
    return summary
