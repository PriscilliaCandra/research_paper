"""
Frozen, versioned constants for the research evaluator (E1–E8).

Changing any value here changes the evaluation and must bump CRITERIA_VERSION.
Values marked [SPEC] are copied from the locked specification.
Values marked [IMPL-v2] are the concrete entries that the specification asks to be
frozen but does not enumerate. They are listed for review in the correction report.

Version history
- v1: initial evaluator codebook (E6 and E7 shared one overlap detector; api_error in E5).
- v2: E6 and E7 separated into two codebooks and two logical stages; API failures are
  N/A for E5 rather than E5 failures; separate E6 injury-reference and plan-object lists.
"""

CRITERIA_VERSION = "v2"

# [SPEC] E2: pass iff abs(gemini - baseline) <= 0.15 km per week. The only methodological tolerance.
E2_WEEKLY_MILEAGE_TOLERANCE_KM = 0.15

# Numerical representation protection only: binary floats such as 20.15 - 20.0 =
# 0.1500000000000021 are rounded to this many decimals before the tolerance comparison.
# This is NOT an evaluation tolerance.
FLOAT_COMPARISON_DECIMALS = 6

# [SPEC] E6 sentinel normalization: null, empty, and whitespace-only are handled in
# evaluator.is_no_injury(); the sentinels below are compared after trim and lowercasing.
E6_NO_INJURY_SENTINELS = frozenset({"none", "no", "n/a", "na", "-"})

# [SPEC] Structural rest-day label used by the deterministic baseline.
REST_WORKOUT_TYPE = "Rest & Recovery"

# Generated workout text fields. Searched by E6/E7 for injury-related adaptation.
# NOT E5 requirements: the schema gives them defaults (see E5_REQUIRED_NONBLANK_FIELDS).
WORKOUT_TEXT_FIELDS = ("warmup_cooldown", "workout_execution", "recovery_instruction", "purpose")

# [SPEC] Required structural fields per generated workout and week (AIWorkoutItem / AIWeekPlan).
REQUIRED_WORKOUT_FIELDS = {
    "day": str,
    "workout_type": str,
    "distance_km": (int, float),
    "pace_target": str,
    "intensity_zone": str,
}
REQUIRED_WEEK_FIELDS = {
    "week_number": int,
    "phase": str,
    "weekly_mileage_km": (int, float),
    "workouts": list,
}

# [SPEC] E5 schema-required string fields that must be non-blank (day, phase, workout_type,
# pace_target, intensity_zone have no schema default). Optional/default text fields are not listed.
E5_REQUIRED_NONBLANK_FIELDS = {
    "week": ("phase",),
    "workout": ("day", "workout_type", "pace_target", "intensity_zone"),
}

# [IMPL-v2] Stop words removed before tokenizing text for E6/E7.
STOP_WORDS_V2 = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "i", "in", "is", "it", "its", "of", "on", "or", "that", "the", "this", "to",
    "was", "were", "with", "your", "you", "my", "me", "during", "after", "before",
    "if", "when", "but", "not", "so", "while", "any", "all", "more", "than",
})

# [IMPL-v2] Sentence delimiters for E6/E7 sentence-level evidence.
SENTENCE_DELIMITERS = ".;!?\n"

# ---------- E6 codebook (injury-related adaptation) ----------

# [IMPL-v2] Stage 1a: injury-related reference terms. Generic injury vocabulary only;
# the runner's stored body part or condition is NOT required here (that is E7).
E6_INJURY_REFERENCE_TERMS_V2 = frozenset({
    "injury", "injuries", "injured", "injure",
    "pain", "discomfort", "soreness", "sore", "ache", "aches", "tightness",
    "limitation", "limitations", "symptom", "symptoms", "complaint",
})

# [IMPL-v2] Stage 1b: plan-object terms that place the adaptation in the training plan.
E6_PLAN_OBJECT_TERMS_V2 = frozenset({
    "session", "sessions", "workout", "workouts", "run", "runs", "running",
    "interval", "intervals", "repeat", "repeats", "repetition", "repetitions",
    "tempo", "threshold", "speed", "intensity", "hill", "hills", "long", "easy",
    "volume", "distance", "pace", "mileage", "quality", "race", "training", "week", "weekly",
})

# [IMPL-v2, retained from v1] Stage 1c: explicit modification/action terms (single words or phrases).
E6_ADAPTATION_ACTION_TERMS_V2 = (
    "substitute", "substituted", "substitutes", "substituting",
    "replace", "replaced", "replaces", "replacing",
    "modify", "modified", "modifies", "modifying",
    "swap", "swapped", "swaps", "swapping",
    "reduce", "reduced", "reduces", "reducing",
    "avoid", "avoids", "avoided", "avoiding",
    "skip", "skipped", "skips", "skipping",
    "shorten", "shortened", "shortens",
    "instead", "rather than", "instead of", "lower-impact",
)

# ---------- E7 codebook (runner-specific anchors traceable to the stored injury text) ----------

# [IMPL-v2] E7 anchor classes. A token is a runner-specific anchor only if it appears in the
# stored injury text AND is listed here. Generic vocabulary below is never an anchor.
E7_ANCHOR_CODEBOOK_V2 = {
    "body_part": frozenset({
        "knee", "knees", "shin", "shins", "achilles", "ankle", "ankles", "calf", "calves",
        "hamstring", "hamstrings", "quad", "quads", "quadriceps", "hip", "hips", "groin",
        "glute", "glutes", "foot", "feet", "heel", "heels", "plantar", "toe", "toes",
        "tibia", "patella", "patellar", "iliotibial", "shoulder", "back",
    }),
    "condition": frozenset({
        "tendinitis", "tendonitis", "tendinopathy", "tendon", "ligament", "meniscus",
        "fracture", "sprain", "strain", "shinsplints", "syndrome", "bursitis", "arthritis",
    }),
    "trigger_activity": frozenset({
        "downhill", "downhills", "uphill", "uphills", "hill", "hills", "speed", "speedwork",
        "sprint", "sprints", "stairs", "stair", "trail", "pavement", "track",
    }),
    # [IMPL-v2] No runner-specific limitation entries are frozen in v2. Adding any entry requires a
    # documented methodological decision; it must not be added after seeing Gemini outputs.
    "runner_limitation": frozenset(),
}

# [IMPL-v2 cleanup] Contextual gating for two anchor terms whose plain-token use is ambiguous in
# ordinary English. The term counts as an anchor only when the adjacent raw token is in the set.
#   back  : counts only in the anatomical phrase "lower back" (previous token is "lower").
#   track : counts only as a training/location term (next token is a training or location noun).
E7_CONTEXTUAL_RULES_V2 = {
    "back": ("previous", frozenset({"lower"})),
    "track": ("next", frozenset({
        "workout", "workouts", "session", "sessions", "interval", "intervals",
        "repeat", "repeats", "repetition", "repetitions", "training", "running", "run", "runs",
    })),
}

# [IMPL-v2, retained from v1] Generic vocabulary that is never a runner-specific anchor (explicit exclusion).
E7_GENERIC_VOCABULARY_V2 = frozenset({
    "pain", "injury", "injuries", "caution", "recovery", "body", "fatigue", "discomfort",
    "run", "runs", "running", "pace", "week", "session", "workout", "training",
    "effort", "distance", "mileage", "volume", "listen", "stop", "intensity",
})

# [E5] Failure categories for generated outputs that exist. API failures are NOT in this list:
# they are generation failures, recorded separately, and E5 is N/A for them.
E5_FAILURE_CATEGORIES = (
    "malformed_json",
    "schema_invalid",
    "missing_week",
    "duplicate_week",
    "missing_required_field",
    "empty_required_text",
    "truncated_output",
    "other_structural_failure",
)

# Finish reason that indicates truncation by the provider's output-token limit.
TRUNCATION_FINISH_REASON = "MAX_TOKENS"

# Maximum characters of generated text stored in evidence (audit without bulk copying).
EVIDENCE_EXCERPT_CHARS = 200
