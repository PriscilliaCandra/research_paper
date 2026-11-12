"""
E8 — cross-repetition stability for one runner profile (three Gemini generations).

Per applicable criterion: stability = 1 - (max - min) over evaluable repetitions.
Profile E8 = mean of per-criterion stabilities over applicable criteria (equal weight).
Only evaluable (non-N/A) scores are used. A failed or N/A repetition is never treated
as an identical, stable output. Textual identity is never compared.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.research import criteria_config as cfg
from app.research.evaluator import GenerationEvaluation

E8_CRITERIA = ("e1", "e2", "e3", "e4", "e5", "e6", "e7")
# E1–E7 as written in the specification. A criterion contributes a value only when it is
# evaluable (not N/A). E5 is N/A for generation failures, so a failed attempt never
# appears as an evaluable E5 repetition; a malformed or invalid output is an evaluable E5 = 0.
MIN_EVALUABLE_REPETITIONS = 2


@dataclass
class ProfileStability:
    profile_id: str
    generation_ids: List[str]
    criteria_version: str = cfg.CRITERIA_VERSION
    per_criterion_stability: Dict[str, Optional[float]] = field(default_factory=dict)
    evaluable_repetitions: Dict[str, int] = field(default_factory=dict)
    e8_applicable_criteria: List[str] = field(default_factory=list)
    insufficient_repetitions: List[str] = field(default_factory=list)
    not_applicable_criteria: List[str] = field(default_factory=list)
    e8_score: Optional[float] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "criteria_version": self.criteria_version,
            "profile_id": self.profile_id,
            "generation_ids": list(self.generation_ids),
            "per_criterion_stability": dict(self.per_criterion_stability),
            "evaluable_repetitions": dict(self.evaluable_repetitions),
            "e8_applicable_criteria": list(self.e8_applicable_criteria),
            "insufficient_repetitions": list(self.insufficient_repetitions),
            "not_applicable_criteria": list(self.not_applicable_criteria),
            "e8_score": self.e8_score,
        }


def evaluate_profile_stability(profile_id: str, evaluations: List[GenerationEvaluation]) -> ProfileStability:
    result = ProfileStability(profile_id=profile_id, generation_ids=[e.generation_id for e in evaluations])
    stabilities: List[float] = []
    for criterion in E8_CRITERIA:
        values = [e.score(criterion) for e in evaluations if e.score(criterion) is not None]
        result.evaluable_repetitions[criterion] = len(values)
        if not values:
            result.per_criterion_stability[criterion] = None
            result.not_applicable_criteria.append(criterion)
        elif len(values) < MIN_EVALUABLE_REPETITIONS:
            result.per_criterion_stability[criterion] = None
            result.insufficient_repetitions.append(criterion)
        else:
            stability = 1 - (max(values) - min(values))
            result.per_criterion_stability[criterion] = stability
            result.e8_applicable_criteria.append(criterion)
            stabilities.append(stability)
    if stabilities:
        result.e8_score = sum(stabilities) / len(stabilities)
    return result
