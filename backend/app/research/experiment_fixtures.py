"""
Synthetic, fixed dry-run profile set (24 profiles).

These are NOT the locked study profiles. The locked stratification is an open advisor decision
(RESEARCH_METHODOLOGY_LOCK §4.4). The harness uses this fixed set only to exercise the
pipeline. Nothing here is generated randomly; the same index always gives the same profile.
"""
from typing import List, Tuple

from app.schemas import RunnerProfileCreate

PROFILE_COUNT = 24

_PREFERRED_DAYS = {
    3: ["Tuesday", "Thursday", "Sunday"],
    4: ["Tuesday", "Thursday", "Saturday", "Sunday"],
    5: ["Monday", "Tuesday", "Thursday", "Saturday", "Sunday"],
}
_DISTANCES = ["5K", "10K", "Half Marathon"]
_EXPERIENCE = ["Beginner", "Intermediate", "Advanced"]
_DURATIONS = [8, 12, 16]
_INJURIES = [
    None,
    "mild left knee pain, aggravated by downhill running and speed work",
    "mild achilles tightness after speed work",
    "none",
]


def profile_id_for(index: int) -> str:
    return f"profile_{index:03d}"


def build_profile(index: int) -> RunnerProfileCreate:
    """Deterministic profile for index in 1..24."""
    if not 1 <= index <= PROFILE_COUNT:
        raise ValueError(f"profile index must be in 1..{PROFILE_COUNT}")
    distance = _DISTANCES[index % 3]
    if distance == "5K":
        target = f"{21 + index % 4:02d}:{(index * 5) % 60:02d}"
    elif distance == "10K":
        target = f"{46 + index % 6:02d}:{(index * 11) % 60:02d}"
    else:
        target = f"01:{50 + index % 12:02d}:{(index * 13) % 60:02d}"
    days = 3 + index % 3
    return RunnerProfileCreate(
        age=20 + index,
        gender="Male" if index % 2 else "Female",
        experience_level=_EXPERIENCE[index % 3],
        pb_5k=f"{22 + index % 6:02d}:{(index * 7) % 60:02d}",
        pb_10k=None,
        target_race_distance=distance,
        target_race_time=target,
        current_weekly_mileage=float(20 + (index % 5) * 5),
        training_days_per_week=days,
        preferred_training_days=list(_PREFERRED_DAYS[days]),
        plan_duration_weeks=_DURATIONS[index % 3],
        injury_limitations=_INJURIES[index % 4],
        easy_run_pace="05:45",
    )


def build_profiles() -> List[Tuple[str, RunnerProfileCreate]]:
    return [(profile_id_for(i), build_profile(i)) for i in range(1, PROFILE_COUNT + 1)]
