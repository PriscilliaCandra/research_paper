"""Cell outcome proportions under the PROPOSED amendment of section 18.2 (decision record 12.24, decision 1) -- DRAFT.

THIS MODULE IMPLEMENTS A PROPOSAL THAT IS NOT ADOPTED. Specification v2.7, section 18.2, defines the four proportions over all
`n_calls` calls of the cell. The proposed policy (PROPOSED_AMENDMENT_18_2_DRAFT_v1.md) is:
  1. the four primary proportions (satisfied, violated, unclear, unparseable) use the number of EVALUABLE responses as the denominator;
  2. evaluable responses = the actual calls of the cell minus the calls classified as structural failures (SF1..SF5);
  3. the four proportions sum to 1;
  4. the structural-failure rate is reported separately over the protocol-defined total number of calls of the cell;
  5. (pilot, not here) UNP share = UNP calls / all actual calls of the round, as in s7_decision.py;
  6. SF and UNP are separate classifications; UNP is never inferred from SF;
  7. if the observed number of calls differs from the frozen registry, or a denominator is invalid, nothing is returned.

What this module does NOT do (each is an unresolved point recorded in 12.24; no rule is invented here):
  - it is a cell-level (pooled count) calculation only. Section 18.2 and section 12.3 define the reported figures as cluster-level
    means of p_t(c) = matches / R. With SF calls removed, a template may have fewer than R evaluable calls or none, so p_t(c) can
    be undefined and "equal to the pooled template mean under exact balance" no longer holds. The cluster-level treatment needs
    a decision and is not implemented;
  - it does not decide the precedence of overlapping definitions of the frozen text (section 13.4 read literally also makes an
    empty or truncated response with zero Verdict matches "unparseable"; a call with SF3 may also carry a parseable verdict).
    classify_call applies the precedence recorded in 12.18 row 1 (SF before every parse outcome) only for a call with at most one
    SF code, and refuses a call with several SF codes, for which no order is defined;
  - it does not define a display rounding: the specification has no reporting-rounding convention for these proportions, so exact
    fractions are returned (section 3.1: rational arithmetic) and their sum is exactly 1;
  - the parseable-only sensitivity table of section 18.2 is not computed (its denominator under the proposal is not decided).
"""
from fractions import Fraction

SF_CODES = ("SF1", "SF2", "SF3", "SF4", "SF5")
OUTCOMES = ("satisfied", "violated", "unclear", "unparseable")


def _count(x):
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def classify_call(sf_codes, parse_outcome):
    """One class per call. sf_codes: the set of SF codes the call meets (possibly empty); parse_outcome: 'satisfied', 'violated',
    'unclear', 'UNP', or None when there is no response to parse. Returns 'SF1'..'SF5' or one of OUTCOMES.
    SF takes precedence over every parse outcome (12.18 row 1); UNP is never inferred from an SF code; a call with no SF code and
    no response is undefined (ValueError); a call with several SF codes has no defined precedence (ValueError)."""
    sf = set(sf_codes)
    if not sf <= set(SF_CODES):
        raise ValueError("unknown SF code")
    if len(sf) > 1:
        raise ValueError("several SF codes on one call: no precedence is defined by the specification")
    if sf:
        return next(iter(sf))
    if parse_outcome in ("satisfied", "violated", "unclear"):
        return parse_outcome
    if parse_outcome == "UNP":
        return "unparseable"
    raise ValueError("a call without an SF code needs a parse outcome")


def cell_outcomes(*, n_calls, planned_calls, sf_counts, satisfied, violated, unclear, unparseable):
    """Pooled cell proportions under the proposed policy. All counts are ints; n_calls is the number of calls actually recorded
    in the cell and must equal planned_calls (the frozen number of calls of the cell). Raises ValueError otherwise, so no figure
    is produced for an incomplete cell, an invalid denominator, or classes that do not partition the calls."""
    if not isinstance(sf_counts, dict) or set(sf_counts) != set(SF_CODES):
        raise ValueError("sf_counts must have exactly the keys SF1..SF5")
    counts = dict(sf_counts, satisfied=satisfied, violated=violated, unclear=unclear, unparseable=unparseable)
    if not all(_count(v) for v in counts.values()):
        raise ValueError("all counts must be non-negative ints")
    if not _count(n_calls) or not _count(planned_calls) or planned_calls == 0:
        raise ValueError("n_calls and planned_calls must be ints and planned_calls positive")
    if n_calls != planned_calls:
        raise ValueError("the observed number of calls differs from the frozen number of calls of the cell")
    sf_total = sum(sf_counts.values())
    evaluable = n_calls - sf_total
    if evaluable <= 0:
        raise ValueError("no evaluable response: the denominator is invalid")
    if satisfied + violated + unclear + unparseable != evaluable:
        raise ValueError("the four outcome classes do not partition the evaluable calls (SF and outcome classes must be disjoint)")
    props = {k: Fraction(counts[k], evaluable) for k in OUTCOMES}
    assert sum(props.values()) == 1
    return {"n_calls": n_calls, "evaluable": evaluable, "sf_total": sf_total, "sf_by_type": dict(sf_counts),
            "proportions": {k: str(v) for k, v in props.items()}, "proportions_sum": str(sum(props.values())),
            "structural_failure_rate": str(Fraction(sf_total, planned_calls)),
            "structural_failure_rate_denominator": planned_calls,
            "status": "PROPOSED POLICY, NOT ADOPTED: specification 18.2 defines these over n_calls"}
