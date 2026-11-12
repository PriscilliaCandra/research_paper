"""Small, dependency-free helpers shared across the backend test suite."""
import itertools
from datetime import date
from typing import Any, Dict

from app.schemas import RunnerProfileCreate

_email_counter = itertools.count(1)


def unique_email() -> str:
    return f"test-user-{next(_email_counter)}@example.com"


def make_register_payload(**overrides: Any) -> Dict[str, Any]:
    payload = {
        "email": unique_email(),
        "password": "CorrectHorse123",
        "display_name": "Test Runner",
    }
    payload.update(overrides)
    return payload


def register_and_login(client, **overrides: Any) -> Dict[str, Any]:
    """Registers a new user (which also logs them in / sets the session cookie
    on the given TestClient) and returns the registration response JSON."""
    payload = make_register_payload(**overrides)
    res = client.post("/api/auth/register", json=payload)
    assert res.status_code == 201, f"registration failed: {res.status_code} {res.text}"
    return res.json()

DEFAULT_PROFILE_KWARGS: Dict[str, Any] = dict(
    age=21,
    gender="Male",
    experience_level="Intermediate",
    pb_5k="24:56",
    pb_10k="52:10",
    target_race_distance="5K",
    target_race_time="23:00",
    current_weekly_mileage=25.0,
    training_days_per_week=4,
    preferred_training_days=["Tuesday", "Thursday", "Friday", "Sunday"],
    plan_duration_weeks=8,
    injury_limitations="Mild patellar knee discomfort on steep downhill",
    easy_run_pace="05:45",
)


def make_profile(**overrides: Any) -> RunnerProfileCreate:
    """Builds a validated RunnerProfileCreate from the canonical S2 example, with overrides."""
    kwargs = {**DEFAULT_PROFILE_KWARGS, **overrides}
    return RunnerProfileCreate(**kwargs)


def make_profile_payload(**overrides: Any) -> Dict[str, Any]:
    """Same as make_profile(), but as a plain JSON-serializable dict for API requests."""
    return {**DEFAULT_PROFILE_KWARGS, **overrides}


def make_workout_payload(**overrides: Any) -> Dict[str, Any]:
    payload = {
        "workout_date": date.today().isoformat(),
        "distance_meters": 5000,
        "duration_seconds": 1800,
        "workout_type": "EASY",
    }
    payload.update(overrides)
    return payload


def build_valid_ai_payload(rule_plan: Dict[str, Any]) -> Dict[str, Any]:
    """Builds a syntactically and numerically valid multi-week AI plan payload from a baseline rule plan."""
    weeks = []
    for baseline_week in rule_plan["weeks"]:
        workouts = [
            {
                **w,
                "warmup_cooldown": "Personalized warm-up drills.",
                "workout_execution": "Personalized step-by-step execution cues.",
            }
            for w in baseline_week["workouts"]
        ]
        weeks.append({
            "week_number": baseline_week["week_number"],
            "phase": baseline_week["phase"],
            "weekly_mileage_km": baseline_week["weekly_mileage_km"],
            "workouts": workouts,
        })
    return {
        "coach_overview": "Welcome to your personalized plan!",
        "weeks": weeks,
        "personalized_insights": {
            "pacing_strategy": "Stay controlled on easy days.",
            "injury_prevention_note": "Watch the knee on downhills.",
            "nutrition_and_recovery_advice": "Hydrate well after quality sessions.",
        },
        "ai_personalization_summary": ["Adapted to 4 available training days per week."],
        "why_this_plan_was_generated": {
            "key_influencing_factors": ["5K PB established the baseline VDOT."],
            "ai_personalization_additions": "Added tailored warm-ups and pacing cues.",
        },
    }
