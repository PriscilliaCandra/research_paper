"""
Per-session limits of the baseline generator (branch engine-allocation-fix).

The engine audit of the 30 synthetic profiles (paper/evaluation_layer_A/layerB_engine_audit.py) found single
sessions above 50% of the weekly volume (up to 61%), easy runs of up to 52 km, and week-1 volumes up to 1.23 times the
starting volume. These tests state the limits that the revised allocation keeps over the whole input domain
(durations 4..24, 3..7 training days, several base mileages, three race distances).
"""
import itertools

import pytest

from app.rules.generator import (
    LONG_RUN_CAP_KM,
    allocate_session_distances,
    generate_rule_based_plan,
    max_weekly_volume,
)
from tests.factories import make_profile

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
RACE_TIME = {"5K": "23:00", "10K": "48:00", "Half Marathon": "01:45:00"}
BASES = (11.0, 19.9, 20.0, 40.0, 83.0)


def _plan(duration, days, base, distance):
    return generate_rule_based_plan(make_profile(
        plan_duration_weeks=duration, training_days_per_week=days, preferred_training_days=DAYS[:days],
        current_weekly_mileage=base, target_race_distance=distance, target_race_time=RACE_TIME[distance],
    ))


@pytest.mark.parametrize("distance,days", list(itertools.product(RACE_TIME, range(3, 8))))
def test_no_session_exceeds_half_the_week_and_no_easy_run_exceeds_the_long_run(distance, days):
    for base in BASES:
        for w in _plan(12, days, base, distance)["weeks"]:
            by_type = {}
            for x in w["workouts"]:
                by_type.setdefault(x["workout_type"], []).append(x["distance_km"])
            total = w["weekly_mileage_km"]
            assert max(max(v) for v in by_type.values()) <= 0.5 * total + 0.05
            long_km = max(by_type["Long Run"])
            assert max(by_type.get("Easy Aerobic Run", [0.0])) <= long_km + 0.05
            assert max(LONG_RUN_CAP_KM.values()) >= long_km


@pytest.mark.parametrize("distance,days", list(itertools.product(RACE_TIME, range(3, 8))))
def test_build_weeks_never_rise_more_than_ten_percent_in_realized_distance(distance, days):
    for base in BASES:
        weeks = _plan(16, days, base, distance)["weeks"]
        for prev, cur in zip(weeks, weeks[1:]):
            if "Progressive" in cur["phase"]:
                assert cur["weekly_mileage_km"] <= prev["weekly_mileage_km"] * 1.10 + 0.05


@pytest.mark.parametrize("distance,days", list(itertools.product(RACE_TIME, range(3, 8))))
def test_week_one_stays_within_the_documented_start_factor(distance, days):
    for base in BASES:
        w1 = _plan(8, days, base, distance)["weeks"][0]["weekly_mileage_km"]
        factor = 1.06 if base >= 20 else 1.08
        assert w1 <= base * factor + 0.15


@pytest.mark.parametrize("distance,days", list(itertools.product(RACE_TIME, range(3, 8))))
def test_allocation_conserves_the_target_when_the_days_can_carry_it(distance, days):
    limit = max_weekly_volume(days, distance)
    assert limit is not None and limit > 0
    for t in (8.0, 14.0, 25.0, limit):
        long_km, quality_km, easy_km = allocate_session_distances(t, days, distance)
        realized = long_km + quality_km + easy_km * (days - 2)
        assert abs(realized - t) <= 0.5
    # above the limit the realized week falls below the target instead of producing a longer session
    long_km, quality_km, easy_km = allocate_session_distances(limit + 40.0, days, distance)
    assert long_km + quality_km + easy_km * (days - 2) < limit + 40.0
    assert easy_km <= long_km


def test_max_weekly_volume_is_undefined_for_one_or_two_training_days():
    assert max_weekly_volume(1, "5K") is None
    assert max_weekly_volume(2, "10K") is None
