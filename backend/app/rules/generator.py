import math
from typing import Dict, List, Any
from app.schemas import RunnerProfileCreate, WorkoutItem, parse_time_to_seconds, format_seconds_to_pace
from app.rules.vdot import (
    calculate_vdot_from_race,
    calculate_training_paces,
    calculate_target_pace,
    get_distance_meters,
)

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Long-run cap per race distance (km) and minimum session lengths (km).
LONG_RUN_CAP_KM = {"5K": 12.0, "10K": 16.0, "Half Marathon": 21.0}
MIN_LONG_KM, MIN_QUALITY_KM, MIN_EASY_KM = 4.0, 3.5, 3.0


def allocate_session_distances(weekly_mileage: float, days_count: int, race: str):
    """Distances (km) of the long run, the quality session and each easy run for one training week.

    Shares of the weekly target: long run 30% (capped per race distance), quality 20%, the remainder split over the
    easy days. The minimum session lengths apply only when they fit inside the weekly target; when they do not, they
    are scaled down together so the realized week equals the target. No session may be longer than the long run, so
    when the available days cannot carry the target the realized week falls below it (see `max_weekly_volume`)."""
    t = weekly_mileage
    easy_n = max(days_count - 2, 0)
    cap = LONG_RUN_CAP_KM.get(race, float("inf"))
    floors = MIN_LONG_KM + MIN_QUALITY_KM + MIN_EASY_KM * easy_n
    if t < floors:
        k = t / floors
        long_km, quality_km, easy_km = round(MIN_LONG_KM * k, 1), round(MIN_QUALITY_KM * k, 1), round(MIN_EASY_KM * k, 1)
    else:
        long_km = max(min(round(t * 0.30, 1), cap), MIN_LONG_KM)
        quality_km = max(round(t * 0.20, 1), MIN_QUALITY_KM)
        if easy_n == 0:
            easy_km = 0.0
        else:
            easy_km = (t - long_km - quality_km) / easy_n
            if easy_km < MIN_EASY_KM:
                # the easy days take their minimum; the long run and the quality session share what is left (60/40)
                easy_km = MIN_EASY_KM
                rest = t - easy_km * easy_n
                long_km = max(MIN_LONG_KM, min(cap, round(rest * 0.6, 1)))
                quality_km = round(rest - long_km, 1)
                if quality_km < MIN_QUALITY_KM:
                    quality_km = MIN_QUALITY_KM
                    long_km = round(rest - quality_km, 1)
            if easy_km > long_km:
                # no easy run may be longer than the long run: the long run takes an equal share of what the
                # quality session leaves, up to its cap
                long_km = max(long_km, round(min(cap, (t - quality_km) / (easy_n + 1)), 1))
                easy_km = (t - long_km - quality_km) / easy_n
            easy_km = round(easy_km, 1)
    easy_km = min(easy_km, long_km)
    quality_km = min(quality_km, long_km)
    # the long run absorbs the rounding of the other sessions so that the realized week equals the target
    diff = round(t - (long_km + quality_km + easy_km * easy_n), 1)
    if easy_n and abs(diff) <= 0.05 * easy_n + 0.1 and max(easy_km, quality_km) <= long_km + diff <= cap:
        long_km = round(long_km + diff, 1)
    return long_km, quality_km, easy_km


def max_weekly_volume(days_count: int, race: str):
    """Largest weekly target (km) that `allocate_session_distances` can realize in full for the given number of
    training days; None when the notion does not apply (one or two training days)."""
    if days_count <= 2:
        return None
    best, t = 0.0, 5.0
    while t < 250.0:
        long_km, quality_km, easy_km = allocate_session_distances(t, days_count, race)
        if long_km + quality_km + easy_km * (days_count - 2) >= t - 0.15:
            best = t
        t += 0.5
    return best

def generate_weekly_progression_summary(
    base_mileage: float,
    total_weeks: int,
    target_race_distance: str,
    max_weekly_km: float | None = None
) -> List[Dict[str, Any]]:
    """
    Generates a safe periodization progression:
    - Base Building (Weeks 1 to N-3 with periodic cutback recovery every 4 weeks)
    - Peak volume (Week N-2 or N-3)
    - Tapering (Final 1-2 weeks before race)

    `current` is the build-progression reference: the volume the NEXT build
    week compounds from. A cutback week now updates `current` to its own
    (reduced) volume before returning, so the following build week resumes
    from the actual post-cutback mileage rather than the stale pre-cutback
    peak (T7 fix — previously produced an unintended ~+25% rebound the week
    after every cutback, since `current` was left untouched by the cutback
    branch). Taper/race weeks remain anchored to `base_mileage` (the starting
    weekly mileage), not to `current`/peak, per the documented methodology
    (PAPER_DRAFT.md Table II: "Taper | Second-to-last week is 0.85 and last
    week is 0.55 times the base (starting) mileage") — this anchoring was
    already correct; only the misleading inline comment below has been fixed.
    """
    progression = []
    current = base_mileage

    for w in range(1, total_weeks + 1):
        if w == total_weeks:
            # Race Week: Tapered volume (~55% of the base/starting mileage)
            vol = round(base_mileage * 0.55, 1)
            phase = "Race Week & Taper"
            note = f"Taper week: Reduced volume to keep legs fresh for race day ({target_race_distance})."
        elif w == total_weeks - 1:
            # Pre-race taper (0.85x the base/starting mileage, NOT peak volume
            # -- matches the documented methodology, see function docstring)
            vol = round(base_mileage * 0.85, 1)
            phase = "Taper Phase"
            note = "Begin race taper; maintain intensity in short bursts but drop overall volume."
        elif w % 4 == 0:
            # Recovery / Cutback week (-15% volume off the current reference)
            vol = round(current * 0.85, 1)
            phase = "Recovery Cutback Week"
            note = "Scheduled deload week to allow muscular and connective tissue adaptation."
            # T7 fix: the cutback's reduced volume becomes the new reference,
            # so the next build week progresses from the real post-cutback
            # mileage instead of silently reverting to the pre-cutback peak.
            current = vol
        else:
            # Safe 5-8% increase capped at 10%
            increase_factor = 1.06 if base_mileage >= 20 else 1.08
            current = round(current * increase_factor, 1)
            if max_weekly_km is not None:
                # The available training days cannot carry more than this volume under the session limits.
                current = min(current, max(max_weekly_km, base_mileage))
            vol = current
            phase = "Progressive Build Phase"
            note = "Steady volume increment respecting the ≤10% weekly safe mileage principle."

        progression.append({
            "week_number": w,
            "target_mileage_km": vol,
            "phase": phase,
            "focus_note": note
        })

    return progression


def distribute_workouts_across_week(
    profile: RunnerProfileCreate,
    paces: Dict[str, Dict[str, str]],
    target_pace_str: str,
    weekly_mileage: float
) -> List[WorkoutItem]:
    """
    Distributes workouts scientifically across days of the week:
    - Quality session (Tempo or Interval)
    - Long Run (planned at 30% of the weekly target volume, capped per race distance and floored at 4 km; the realized share can exceed 30% when few training days are available or volume is low)
    - Easy Aerobic Runs
    - Rest / Active Recovery days
    """
    days_count = profile.training_days_per_week
    preferred_days = [d.strip().capitalize() for d in profile.preferred_training_days]
    
    # If preferred days count doesn't match days_count, align them
    active_days_set = set()
    for d in DAYS_OF_WEEK:
        if d in preferred_days and len(active_days_set) < days_count:
            active_days_set.add(d)

    # Fill remaining days if needed
    if len(active_days_set) < days_count:
        for d in ["Tuesday", "Thursday", "Saturday", "Sunday", "Wednesday", "Friday", "Monday"]:
            if d not in active_days_set:
                active_days_set.add(d)
                if len(active_days_set) == days_count:
                    break

    # With only one training day available, there is no second active day to host a
    # separate quality (tempo/interval) session, which would otherwise crash the
    # day-selection logic below. Schedule a single safe aerobic session instead.
    if days_count <= 1:
        only_day = next(iter(active_days_set)) if active_days_set else DAYS_OF_WEEK[0]
        single_run_km = round(max(weekly_mileage, 3.0), 1)
        has_injury_note = bool(profile.injury_limitations and profile.injury_limitations.strip().lower() not in ["none", "no", "n/a", "-"])
        recovery = "Cool down with 10 minutes of walking, refuel with carbohydrates and protein within 45 minutes, hydrate thoroughly."
        if has_injury_note:
            recovery += f" Note: Monitor {profile.injury_limitations}; substitute with low-impact strides or reduce pace if irritation arises."

        single_day_workouts: List[WorkoutItem] = []
        for day in DAYS_OF_WEEK:
            if day == only_day:
                single_day_workouts.append(WorkoutItem(
                    day=day,
                    workout_type="Long Run",
                    distance_km=single_run_km,
                    pace_target=paces["easy"]["pace_range"],
                    intensity_zone=paces["easy"]["intensity"],
                    recovery_instruction=recovery,
                    purpose="With only one weekly training day available, this single aerobic session covers the full weekly volume at a safe, conversational effort rather than splitting into a separate high-intensity session."
                ))
            else:
                single_day_workouts.append(WorkoutItem(
                    day=day,
                    workout_type="Rest & Recovery",
                    distance_km=0.0,
                    pace_target="N/A",
                    intensity_zone="Rest (Zone 1 or Complete Rest)",
                    recovery_instruction="Rest completely or perform light mobility stretching, foam rolling, and adequate hydration.",
                    purpose="Allows muscle glycogen replenishment, micro-tear repair, and physiological adaptation."
                ))
        return single_day_workouts

    # Calculate Volume Allocations (80/20 Rule) with per-session limits
    long_run_km, quality_km, easy_run_each_km = allocate_session_distances(
        weekly_mileage, days_count, profile.target_race_distance
    )
    easy_days_count = max(days_count - 2, 0)

    # Pick long run day (Sunday if active, else Saturday, else last active day)
    long_run_day = "Sunday" if "Sunday" in active_days_set else ("Saturday" if "Saturday" in active_days_set else sorted(list(active_days_set))[-1])
    
    # Pick quality workout day (Tuesday or Wednesday, not adjacent to long run if possible)
    potential_quality_days = [d for d in ["Tuesday", "Wednesday", "Thursday"] if d in active_days_set and d != long_run_day]
    quality_day = potential_quality_days[0] if potential_quality_days else [d for d in active_days_set if d != long_run_day][0]

    workouts: List[WorkoutItem] = []

    has_injury_note = bool(profile.injury_limitations and profile.injury_limitations.strip().lower() not in ["none", "no", "n/a", "-"])

    for day in DAYS_OF_WEEK:
        if day not in active_days_set:
            workouts.append(WorkoutItem(
                day=day,
                workout_type="Rest & Recovery",
                distance_km=0.0,
                pace_target="N/A",
                intensity_zone="Rest (Zone 1 or Complete Rest)",
                recovery_instruction="Rest completely or perform light mobility stretching, foam rolling, and adequate hydration.",
                purpose="Allows muscle glycogen replenishment, micro-tear repair, and physiological adaptation."
            ))
        elif day == long_run_day:
            workouts.append(WorkoutItem(
                day=day,
                workout_type="Long Run",
                distance_km=long_run_km,
                pace_target=paces["easy"]["pace_range"],
                intensity_zone=paces["easy"]["intensity"],
                recovery_instruction="Cool down with 10 minutes of walking, refuel with carbohydrates and protein within 45 minutes, hydrate thoroughly.",
                purpose="Builds capillary density, mitochondrial density, and mental stamina for race distance."
            ))
        elif day == quality_day:
            # Alternates or chooses Tempo / Interval based on experience
            if profile.experience_level.lower() == "beginner":
                workout_type = "Tempo Run"
                pace_target = paces["threshold"]["pace_range"]
                intensity = paces["threshold"]["intensity"]
                purpose = "Improves lactate threshold clearance and running economy at steady challenging effort."
                recovery = "10 min easy warm-up jog, 10 min cool-down jog, dynamic stretches before starting."
            else:
                workout_type = "Interval Training"
                pace_target = f"{paces['interval']['pace_range']} (Reps) / Easy Jog (Rest)"
                intensity = paces["interval"]["intensity"]
                purpose = f"Increases VO2max and cardiac stroke volume. Target race pace awareness ({target_pace_str}/km)."
                recovery = "Warm up 1.5 km easy, 4x800m or 6x400m hard with 2 min recovery jogs, 1.5 km cool down."
            
            if has_injury_note:
                recovery += f" Note: Monitor {profile.injury_limitations}; substitute with low-impact strides or reduce pace if irritation arises."

            workouts.append(WorkoutItem(
                day=day,
                workout_type=workout_type,
                distance_km=quality_km,
                pace_target=pace_target,
                intensity_zone=intensity,
                recovery_instruction=recovery,
                purpose=purpose
            ))
        else:
            # Easy Aerobic Run
            workouts.append(WorkoutItem(
                day=day,
                workout_type="Easy Aerobic Run",
                distance_km=easy_run_each_km,
                pace_target=paces["easy"]["pace_range"],
                intensity_zone=paces["easy"]["intensity"],
                recovery_instruction="Keep pace strictly conversational. If breathing becomes labored, slow down.",
                purpose="Develops aerobic base conditioning and promotes active muscular blood flow without fatigue."
            ))

    return workouts


def generate_rule_based_plan(profile: RunnerProfileCreate) -> Dict[str, Any]:
    """
    Executes the pure deterministic sports-science rule engine:
    1. VDOT calculation
    2. Pace zones determination
    3. Target race pace calculation
    4. Safe weekly mileage distribution (80/20 Seiler rule & 10% progression rule)
    5. Explainability matrix
    """
    # 1. Calculate VDOT from 5K PB
    dist_5k_meters = 5000.0
    pb_5k_sec = parse_time_to_seconds(profile.pb_5k)
    vdot = calculate_vdot_from_race(dist_5k_meters, pb_5k_sec)

    # 2. Calculate Paces
    paces = calculate_training_paces(vdot)
    target_pace_str, target_pace_sec = calculate_target_pace(profile.target_race_distance, profile.target_race_time)

    # Calculate quantitative pace delta for defensible feasibility check
    pb_5k_pace_sec = int(pb_5k_sec / 5.0)
    pace_delta_pct = round(((pb_5k_pace_sec - target_pace_sec) / pb_5k_pace_sec) * 100.0, 1)

    if pace_delta_pct <= 0:
        feasibility_note = f"Target pace ({target_pace_str}/km) is conservative relative to 5K PB pace ({format_seconds_to_pace(pb_5k_pace_sec)}/km). High feasibility."
        feasibility_status = "Conservative / Highly Feasible"
    elif pace_delta_pct <= 3.5:
        feasibility_note = f"Target pace requires a {pace_delta_pct}% pace improvement. Physiologically realistic for a {profile.plan_duration_weeks}-week progressive build."
        feasibility_status = "Realistic / Well-Calibrated"
    elif pace_delta_pct <= 6.5:
        feasibility_note = f"Target pace requires a {pace_delta_pct}% pace improvement. Ambitious goal requiring strict adherence to aerobic volume and recovery."
        feasibility_status = "Ambitious / High Effort"
    else:
        feasibility_note = f"Target pace requires a {pace_delta_pct}% pace improvement. Aggressive overreach for an {profile.plan_duration_weeks}-week training window; elevated injury risk."
        feasibility_status = "Aggressive Overreach / High Fatigue Risk"

    # 3. Weekly progression summary across total plan weeks
    progression = generate_weekly_progression_summary(
        base_mileage=profile.current_weekly_mileage,
        total_weeks=profile.plan_duration_weeks,
        target_race_distance=profile.target_race_distance,
        max_weekly_km=max_weekly_volume(profile.training_days_per_week, profile.target_race_distance)
    )

    # 4. Generate daily workouts for every week in the progression schedule
    weeks = []
    for week_info in progression:
        w_num = week_info["week_number"]
        target_vol = week_info["target_mileage_km"]
        w_workouts = distribute_workouts_across_week(
            profile=profile,
            paces=paces,
            target_pace_str=target_pace_str,
            weekly_mileage=target_vol
        )
        weeks.append({
            "week_number": w_num,
            "phase": week_info["phase"],
            "focus_note": week_info["focus_note"],
            "weekly_mileage_km": round(sum(w.distance_km for w in w_workouts), 1),
            "workouts": [w.model_dump() for w in w_workouts],
        })

    week_1_workouts = weeks[0]["workouts"]
    actual_week_mileage = weeks[0]["weekly_mileage_km"]

    # 5. Scientific 4-Part Traceability & Explainability Matrix
    explainability = {
        # Part 1: Runner Inputs Utilized
        "runner_inputs_utilized": {
            "age": profile.age,
            "gender": profile.gender,
            "experience_level": profile.experience_level,
            "pb_5k": profile.pb_5k,
            "optional_10k_pb": profile.pb_10k or "Not provided",
            "target_race": f"{profile.target_race_distance} in {profile.target_race_time}",
            "current_weekly_mileage": f"{profile.current_weekly_mileage} km/week",
            "training_frequency": f"{profile.training_days_per_week} days/week",
            "preferred_days": profile.preferred_training_days,
            "injury_limitations": profile.injury_limitations or "None reported",
            "easy_run_pace": profile.easy_run_pace
        },
        # Part 2: Physiological Engine Values
        "physiological_engine_values": {
            "calculated_vdot": vdot,
            "vdot_interpretation": f"Daniels-Gilbert formula derived VDOT of {vdot:.1f} from 5K PB ({profile.pb_5k}).",
            "target_race_pace": f"{target_pace_str} /km",
            "pb_5k_pace": f"{format_seconds_to_pace(pb_5k_pace_sec)} /km",
            "pace_delta_percentage": f"{pace_delta_pct}%",
            "feasibility_assessment": feasibility_note,
            "feasibility_status": feasibility_status,
            "training_pace_zones": {
                "easy_zone": paces["easy"]["pace_range"],
                "marathon_zone": paces["marathon"]["pace_range"],
                "threshold_zone": paces["threshold"]["pace_range"],
                "interval_zone": paces["interval"]["pace_range"],
                "repetition_zone": paces["repetition"]["pace_range"]
            }
        },
        # Part 3: Applied Prototype Constraints (Design principles, not universal laws)
        "applied_prototype_constraints": {
            "volume_split_heuristic": (
                f"Polarized training design heuristic (~80% low-intensity aerobic: {actual_week_mileage * 0.8:.1f} km, "
                f"~20% high-intensity quality: {actual_week_mileage * 0.2:.1f} km) across {actual_week_mileage:.1f} km total."
            ),
            "baseline_mileage_constraint": f"Week 1 target volume is the current weekly mileage ({profile.current_weekly_mileage} km) times 1.06 (1.08 below 20 km), a design heuristic to limit sudden increases; realized weekly distance can differ from the target because of per-session minimum distances and the available training days.",
            "long_run_boundary": "Long run is planned at 30% of the weekly target volume, capped per race distance (12/16/21 km) and floored at 4 km; the realized long-run share can exceed 30% with few training days or low volume.",
            "weekly_progression_heuristic": "Target volume rises by 6-8% per build week (at most 10%) with scheduled cutback deload weeks; realized weekly distance can differ from the target.",
            "rest_day_spacing": f"{7 - profile.training_days_per_week} rest days scheduled strategically around quality and long run sessions."
        },
        # Part 4: AI Personalization Summary
        "ai_personalization_summary": [
            f"Training frequency adapted to {profile.training_days_per_week} available days per week.",
            f"Workout sessions aligned with preferred days: {', '.join(profile.preferred_training_days)}.",
            f"Warm-up protocols, strides, and cool-down routines personalized for {profile.experience_level.lower()} experience level.",
            (
                f"Injury precautions tailored for reported issue: '{profile.injury_limitations}'."
                if profile.injury_limitations else "General running durability and cadence stability guidance integrated."
            ),
            f"Cognitive pacing cues provided to guide adherence to target race pace ({target_pace_str}/km)."
        ]
    }

    return {
        "vdot": vdot,
        "target_pace_per_km": target_pace_str,
        "paces": paces,
        "week_1_plan": {
            "week_number": 1,
            "weekly_mileage_km": round(actual_week_mileage, 1),
            "focus": "Aerobic Base Calibration & Routine Adaptation",
            "workouts": week_1_workouts
        },
        "progression_schedule": progression,
        "weeks": weeks,
        "explainability": explainability
    }
