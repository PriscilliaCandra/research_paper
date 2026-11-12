"""
T7 regression coverage: deterministic multi-week baseline progression.

Background (see paper/CODE_AUDIT_EASY_RUN_PACE_AND_T7.md and
RESEARCH_METHODOLOGY_LOCK.md's T7 row for the full audit trail):

Before the fix, `generate_weekly_progression_summary`'s `current` accumulator
was left untouched by the cutback branch, so the build week immediately after
a cutback resumed compounding from the pre-cutback peak instead of the actual
post-cutback mileage -- producing an unintended ~+25% rebound the week after
every 4th-week cutback.

Taper/race-week volume was (and remains, correctly) anchored to
`base_mileage` -- the plan's starting weekly mileage -- per the documented
methodology (PAPER_DRAFT.md Table II: "Taper | Second-to-last week is 0.85
and last week is 0.55 times the base (starting) mileage"). The pre-fix inline
code comment incorrectly said "~75% of peak", which these tests also guard
against regressing back to.

Formulas under test (base_mileage=20.0, so increase_factor=1.06 since
base_mileage >= 20; target_race_distance does not affect the numbers):
  build week:    current = round(current * 1.06, 1); vol = current
  cutback week (every 4th week, not in the final two): vol = round(current * 0.85, 1); current = vol  [T7 fix]
  taper week (total_weeks - 1):                        vol = round(base_mileage * 0.85, 1)
  race week  (total_weeks):                            vol = round(base_mileage * 0.55, 1)

Expected 8-week trajectory (base_mileage=20.0), computed by hand-applying the
formulas above and cross-checked by running the fixed function directly:
  Week 1: 20.0 * 1.06          = 21.2   (Progressive Build Phase)
  Week 2: 21.2 * 1.06 = 22.472 = 22.5   (Progressive Build Phase)
  Week 3: 22.5 * 1.06 = 23.85  = 23.9   (Progressive Build Phase)
  Week 4: 23.9 * 0.85 = 20.315 = 20.3   (Recovery Cutback Week)  <- cutback
  Week 5: 20.3 * 1.06 = 21.518 = 21.5   (Progressive Build Phase) <- post-cutback build, NOT a rebound past week 3's 23.9
  Week 6: 21.5 * 1.06 = 22.79  = 22.8   (Progressive Build Phase)
  Week 7: 20.0 * 0.85           = 17.0  (Taper Phase)
  Week 8: 20.0 * 0.55           = 11.0  (Race Week & Taper)
"""
import pytest

from app.rules.generator import generate_weekly_progression_summary


EXPECTED_8WEEK_BASE_20 = [
    (1, 21.2, "Progressive Build Phase"),
    (2, 22.5, "Progressive Build Phase"),
    (3, 23.9, "Progressive Build Phase"),
    (4, 20.3, "Recovery Cutback Week"),
    (5, 21.5, "Progressive Build Phase"),
    (6, 22.8, "Progressive Build Phase"),
    (7, 17.0, "Taper Phase"),
    (8, 11.0, "Race Week & Taper"),
]


# --- Test A: Cutback applies -15% and does not leave a stale peak reference ---

def test_cutback_is_exactly_fifteen_percent_off_the_pre_cutback_reference():
    progression = generate_weekly_progression_summary(20.0, 8, "10K")
    week3 = progression[2]
    week4 = progression[3]

    assert week4["phase"] == "Recovery Cutback Week"
    assert week4["target_mileage_km"] == round(week3["target_mileage_km"] * 0.85, 1)


def test_post_cutback_build_week_does_not_rebound_from_pre_cutback_peak():
    """
    This is the core T7 regression guard. Pre-fix, week 5 recomputed from the
    stale pre-cutback `current` (23.9), producing 23.9 * 1.06 = 25.3 -- a
    rebound of (25.3 - 20.3) / 20.3 = +24.6%, already higher than week 3's
    pre-cutback peak of 23.9. Post-fix, week 5 must build from week 4's own
    (reduced) volume instead.
    """
    progression = generate_weekly_progression_summary(20.0, 8, "10K")
    week3, week4, week5 = progression[2], progression[3], progression[4]

    assert week4["phase"] == "Recovery Cutback Week"
    assert week5["phase"] == "Progressive Build Phase"

    # Week 5 must be a normal ~6% build step off week 4's reduced volume...
    assert week5["target_mileage_km"] == round(week4["target_mileage_km"] * 1.06, 1)

    # ...and must NOT exceed week 3's pre-cutback peak, which is exactly the
    # unintended rebound T7 recorded (pre-fix: week 5 = 25.3 > week 3 = 23.9).
    assert week5["target_mileage_km"] <= week3["target_mileage_km"]

    rebound_pct = (week5["target_mileage_km"] - week4["target_mileage_km"]) / week4["target_mileage_km"]
    assert rebound_pct == pytest.approx(0.06, abs=0.005), (
        f"post-cutback step of {rebound_pct:.1%} should match the normal ~6% build "
        "increment, not the previously-observed ~+24.6% stale-accumulator rebound"
    )


def test_cutback_recurs_every_fourth_week_and_each_rebound_stays_bounded():
    """A longer (12-week) plan has two cutbacks; neither should produce a
    post-cutback jump past the pre-cutback peak."""
    progression = generate_weekly_progression_summary(20.0, 12, "Half Marathon")
    cutback_weeks = [p["week_number"] for p in progression if p["phase"] == "Recovery Cutback Week"]
    assert cutback_weeks == [4, 8]

    for cutback_week_num in cutback_weeks:
        pre_cutback_peak = progression[cutback_week_num - 2]["target_mileage_km"]  # week before cutback
        cutback_vol = progression[cutback_week_num - 1]["target_mileage_km"]
        next_week = progression[cutback_week_num]
        if next_week["phase"] == "Progressive Build Phase":
            assert next_week["target_mileage_km"] == round(cutback_vol * 1.06, 1)
            assert next_week["target_mileage_km"] <= pre_cutback_peak


# --- Test B: Taper uses the documented reference (base/starting mileage) ---

def test_taper_and_race_week_are_anchored_to_base_mileage_not_peak():
    """
    Per PAPER_DRAFT.md Table II, taper/race-week volume is intentionally a
    fixed percentage of base_mileage (the starting weekly mileage), not of
    the peak volume reached during the build phase. This guards against
    "fixing" the anchor to peak, which would contradict the documented,
    citation-audited methodology even though an earlier version of the
    in-code comment incorrectly said "~75% of peak".
    """
    base_mileage = 20.0
    progression = generate_weekly_progression_summary(base_mileage, 8, "10K")

    peak = max(p["target_mileage_km"] for p in progression if p["phase"] == "Progressive Build Phase")
    taper_week = progression[6]   # week 7 of 8 -> total_weeks - 1
    race_week = progression[7]    # week 8 of 8 -> total_weeks

    assert taper_week["phase"] == "Taper Phase"
    assert race_week["phase"] == "Race Week & Taper"

    assert taper_week["target_mileage_km"] == round(base_mileage * 0.85, 1)
    assert race_week["target_mileage_km"] == round(base_mileage * 0.55, 1)

    # Explicitly confirm the anchor is the starting mileage, not the peak --
    # the whole point of the documented design (and the opposite of what the
    # stale comment claimed).
    assert taper_week["target_mileage_km"] != round(peak * 0.85, 1)
    assert peak > base_mileage  # sanity check that there WAS a peak to be anchored to, had it been wrong


def test_taper_anchoring_holds_across_plan_lengths():
    for duration in [4, 8, 12, 16, 24]:
        base_mileage = 22.0
        progression = generate_weekly_progression_summary(base_mileage, duration, "Half Marathon")
        taper_week = progression[duration - 2]
        race_week = progression[duration - 1]
        assert taper_week["target_mileage_km"] == round(base_mileage * 0.85, 1)
        assert race_week["target_mileage_km"] == round(base_mileage * 0.55, 1)


# --- Test C: Full multi-week trajectory, asserted week-by-week ---

def test_full_eight_week_trajectory_matches_documented_formula():
    """
    Week | Expected | Actual | Pass/Fail
    -----|----------|--------|----------
      1  |   21.2   |   see assertion loop below
      2  |   22.5   |
      3  |   23.9   |
      4  |   20.3   |
      5  |   21.5   |
      6  |   22.8   |
      7  |   17.0   |
      8  |   11.0   |

    Expected values are computed from the formulas documented in this
    module's docstring, not copied from any pre-fix program output.
    """
    progression = generate_weekly_progression_summary(20.0, 8, "10K")

    results = []
    for (week_num, expected_vol, expected_phase), actual in zip(EXPECTED_8WEEK_BASE_20, progression):
        actual_vol = actual["target_mileage_km"]
        actual_phase = actual["phase"]
        passed = (actual_vol == expected_vol) and (actual_phase == expected_phase)
        results.append((week_num, expected_vol, expected_phase, actual_vol, actual_phase, passed))

    table_lines = ["Week | Expected (vol, phase) | Actual (vol, phase) | Pass/Fail"]
    for week_num, exp_vol, exp_phase, act_vol, act_phase, passed in results:
        table_lines.append(
            f"{week_num:4} | ({exp_vol}, {exp_phase}) | ({act_vol}, {act_phase}) | "
            f"{'PASS' if passed else 'FAIL'}"
        )
    table_str = "\n".join(table_lines)

    assert all(r[-1] for r in results), f"T7 trajectory mismatch:\n{table_str}"


def test_full_eight_week_trajectory_with_lower_base_mileage_uses_eight_percent_factor():
    """Same shape, but base_mileage < 20 => increase_factor = 1.08."""
    progression = generate_weekly_progression_summary(15.0, 8, "5K")
    expected = [
        (1, 16.2, "Progressive Build Phase"),
        (2, 17.5, "Progressive Build Phase"),
        (3, 18.9, "Progressive Build Phase"),
        (4, 16.1, "Recovery Cutback Week"),
        (5, 17.4, "Progressive Build Phase"),
        (6, 18.8, "Progressive Build Phase"),
        (7, 12.8, "Taper Phase"),
        (8, 8.2, "Race Week & Taper"),
    ]
    for (week_num, expected_vol, expected_phase), actual in zip(expected, progression):
        assert actual["target_mileage_km"] == expected_vol, (
            f"week {week_num}: expected {expected_vol}, got {actual['target_mileage_km']}"
        )
        assert actual["phase"] == expected_phase
