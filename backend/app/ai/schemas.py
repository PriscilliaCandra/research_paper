from typing import List
from pydantic import BaseModel, Field


class AIWorkoutItem(BaseModel):
    day: str
    workout_type: str
    distance_km: float
    pace_target: str
    intensity_zone: str
    warmup_cooldown: str = ""
    workout_execution: str = ""
    recovery_instruction: str = ""
    purpose: str = ""


class AIPersonalizedInsights(BaseModel):
    pacing_strategy: str = ""
    injury_prevention_note: str = ""
    nutrition_and_recovery_advice: str = ""


class AIWhyGenerated(BaseModel):
    key_influencing_factors: List[str] = Field(default_factory=list)
    ai_personalization_additions: str = ""


class AIWeekPlan(BaseModel):
    week_number: int
    phase: str
    weekly_mileage_km: float
    workouts: List[AIWorkoutItem]


class AIPersonalizedPlan(BaseModel):
    """
    Schema for the AI-personalized multi-week plan, whether produced by a live LLM
    or the offline fallback synthesizer. `weeks` mirrors the deterministic baseline's
    `weeks` array one-to-one; only textual/explanatory fields are expected to vary,
    and the numeric/structural fields are cross-checked week by week against the
    baseline in app.ai.validator before this model is ever trusted.

    `weekly_mileage_km` and `workouts` are a backward-compatible mirror of week 1
    (populated from `weeks[0]` after validation) for existing single-week consumers.
    """
    coach_overview: str = ""
    weeks: List[AIWeekPlan]
    weekly_mileage_km: float = 0.0
    workouts: List[AIWorkoutItem] = Field(default_factory=list)
    personalized_insights: AIPersonalizedInsights = Field(default_factory=AIPersonalizedInsights)
    ai_personalization_summary: List[str] = Field(default_factory=list)
    why_this_plan_was_generated: AIWhyGenerated = Field(default_factory=AIWhyGenerated)
