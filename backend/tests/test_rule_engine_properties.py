"""
Property tests for the deterministic rule engine over the full valid input domain
(plan_duration_weeks 4..24 x training_days_per_week 1..7 x several base mileages).

Purpose (PAPER_GAPS section 1 item 4, "Deterministic-engine checks not yet done"): the earlier tests covered
durations 4/8/12/16/24 and 4/8/24 only. These tests do not change the engine.

Two groups:
  * TestVerifiedInvariants: properties that hold on the whole domain today and must keep holding.
  * TestDocumentedClaimsOutsideEnvelope: claims the engine's own explainability text or the paper makes
    ("long run <= 30% of weekly volume", "weekly distance follows the progression target") that DO NOT hold on
    parts of the domain because of minimum floors / caps. They are marked xfail(strict=False) so that the suite
    stays green, the limitation is visible in every run, and a future engine change that fixes it shows as XPASS.
    They are findings, not decisions: whether to change the engine is an owner decision (the baseline is part of
    the confirmed design).
"""
import itertools

import pytest

from app.rules.generator import generate_rule_based_plan, generate_weekly_progression_summary
from tests.factories import make_profile

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
RACE_TIME = {"5K": "23:00", "10K": "48:00", "Half Marathon": "01:45:00"}
DURATIONS = list(range(4, 25))
DAY_COUNTS = list(range(1, 8))
BASES = (5.0, 19.9, 20.0, 40.0, 100.0)


def _profile(duration, days, base, distance="10K", experience="Intermediate"):
    return make_profile(
        plan_duration_weeks=duration,
        training_days_per_week=days,
        preferred_training_days=DAYS[:days],
        current_weekly_mileage=base,
        target_race_distance=distance,
        target_race_time=RACE_TIME[distance],
        experience_level=experience,
    )


class TestVerifiedInvariants:
    @pytest.mark.parametrize("duration,days", list(itertools.product(DURATIONS, DAY_COUNTS)))
    def test_plan_structure_for_every_duration_and_day_count(self, duration, days):
        for base in BASES:
            plan = generate_rule_based_plan(_profile(duration, days, base))
            weeks = plan["weeks"]
            assert [w["week_number"] for w in weeks] == list(range(1, duration + 1))
            assert plan["week_1_plan"]["workouts"] == weeks[0]["workouts"]
            for w in weeks:
                workouts = w["workouts"]
                assert [x["day"] for x in workouts] == DAYS
                assert all(x["distance_km"] >= 0 for x in workouts)
                active = [x for x in workouts if x["distance_km"] > 0]
                assert len(active) == days
                assert abs(sum(x["distance_km"] for x in workouts) - w["weekly_mileage_km"]) <= 0.051

    @pytest.mark.parametrize("duration", DURATIONS)
    @pytest.mark.parametrize("base", BASES)
    def test_phase_schedule_and_progression_bounds(self, duration, base):
        prog = generate_weekly_progression_summary(base, duration, "10K")
        assert len(prog) == duration
        assert prog[-1]["phase"] == "Race Week & Taper"
        assert prog[-1]["target_mileage_km"] == round(base * 0.55, 1)
        assert prog[-2]["phase"] == "Taper Phase"
        assert prog[-2]["target_mileage_km"] == round(base * 0.85, 1)
        factor = 1.06 if base >= 20 else 1.08
        for i in range(1, duration - 2):  # weeks 2..N-2 (index i is week i+1)
            week, prev = prog[i], prog[i - 1]
            if (i + 1) % 4 == 0:
                assert week["phase"] == "Recovery Cutback Week"
                assert week["target_mileage_km"] == round(prev["target_mileage_km"] * 0.85, 1)
            else:
                assert week["phase"] == "Progressive Build Phase"
                assert week["target_mileage_km"] == round(prev["target_mileage_km"] * factor, 1)
                # the paper's "at most 10% weekly increase" wording for build weeks
                assert week["target_mileage_km"] <= prev["target_mileage_km"] * 1.10 + 0.05

    @pytest.mark.parametrize("days", DAY_COUNTS)
    def test_engine_is_deterministic(self, days):
        profile = _profile(12, days, 30.0)
        assert generate_rule_based_plan(profile) == generate_rule_based_plan(profile)

    @pytest.mark.parametrize("distance", list(RACE_TIME))
    @pytest.mark.parametrize("experience", ["Beginner", "Intermediate", "Advanced"])
    def test_no_exception_across_distances_and_experience_levels(self, distance, experience):
        for days in DAY_COUNTS:
            plan = generate_rule_based_plan(_profile(8, days, 25.0, distance, experience))
            assert len(plan["weeks"]) == 8


@pytest.mark.xfail(strict=False, reason="Finding 2026-10-14: explainability text and paper claim a <=30% long-run share; "
                                         "minimum floors and caps break it for 2-3 training days and for some 4-7 day, low-mileage cases. "
                                         "Owner decision needed before any engine change.")
@pytest.mark.parametrize("days", [2, 3, 4, 5, 6, 7])
def test_long_run_share_is_at_most_30_percent_of_realized_weekly_volume(days):
    violations = []
    for duration, base in itertools.product(DURATIONS, BASES):
        plan = generate_rule_based_plan(_profile(duration, days, base))
        for w in plan["weeks"]:
            total = w["weekly_mileage_km"]
            for x in w["workouts"]:
                if x["workout_type"] == "Long Run" and x["distance_km"] > 0.3 * total + 0.051:
                    violations.append((duration, base, w["week_number"], x["distance_km"], total))
    assert not violations, f"{len(violations)} week(s) exceed 30% (first: {violations[0]})"


@pytest.mark.xfail(strict=False, reason="Finding 2026-10-14: realized weekly distance departs from the progression target by more than 10% "
                                         "(2 training days: well below; low base mileage with many days: far above, because of per-run floors). "
                                         "Already listed in PAPER_GAPS section 5 ('realized weekly distance can exceed the target'); "
                                         "owner decision needed before any engine change.")
@pytest.mark.parametrize("days", DAY_COUNTS)
def test_realized_weekly_distance_stays_within_10_percent_of_progression_target(days):
    violations = []
    for duration, base in itertools.product(DURATIONS, BASES):
        plan = generate_rule_based_plan(_profile(duration, days, base))
        for w, p in zip(plan["weeks"], plan["progression_schedule"]):
            target = p["target_mileage_km"]
            if abs(w["weekly_mileage_km"] - target) > 0.10 * target + 0.05:
                violations.append((duration, base, w["week_number"], target, w["weekly_mileage_km"]))
    assert not violations, f"{len(violations)} week(s) outside +-10% (first: {violations[0]})"
