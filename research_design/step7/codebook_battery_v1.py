"""Synthetic battery for the S2/S3 codebook (specification 13.5) -- DRAFT, revision after the Step 7 audit (12.18).

Every comment below is a synthetic sentence written for this battery. None comes from a pilot or main response (none exists)
and none comes from Stage 1/2 records. Numbers are placeholders. The module is ASCII only (non-ASCII written as escapes).

Each item fixes, BEFORE any coder reads it and BEFORE the revised code was written or run:
  features : what a coder would mark --
             asserts / rejects / asks : stance features;
             cmp   : a numerical comparison with a LISTED relation (closed list, Unicode symbols only, 12.18 rows 9 and 10);
             used  : at least one used value or one derived quantity with provenance;
             derived : per derived quantity, does it have provenance;
             used_ok : per used value, does it match the prompt;
             comps   : per stated computation WITH provenance, is it correct;
             rels    : per numerical comparison, does the written relation hold between the numerals as written.
  expected : the codes that follow by the deterministic rules of section 16 (s3_ref, s3_stance, s2_evidence, s2_prov,
             s2_values, s2_arith, s2_rel and the derived flag cev = correct explicit verification). None = not applicable.
The nc feature is derived from the comment text by the empty-comment rule (12.18 row 11) and checked, not assumed.
The deterministic functions are tested against these expected codes. Whether a human coder, reading the free text, assigns
the features as written here has NOT been tested: no coder has read this battery. The feature assignments are the author's.
"""


def _f(asserts=False, rejects=False, asks=False, cmp=False, used=False, derived=(), used_ok=(), comps=(), rels=()):
    return dict(asserts=asserts, rejects=rejects, asks=asks, cmp=cmp, used=used, derived=list(derived),
                used_ok=list(used_ok), comps=list(comps), rels=list(rels))


def _e(ref, stance, ev, prov=None, values="none", arith="AN", rel=None, cev=False):
    return dict(ref=ref, stance=stance, evidence=ev, prov=prov, values=values, arith=arith, rel=rel, cev=cev)


T, F = True, False

BATTERY = (
    ("B01", "", _f(), _e("NC", "NC", "NC")),
    ("B02", "The reviewer is right that the plan violates the constraint, but the plan satisfies the constraint.",
     _f(asserts=T, rejects=T), _e("REF", "MPG", "NV")),
    ("B03", "The reviewer is right about week 4 but not about week 3.",                   # week numbers are not used values: NV
     _f(asserts=T, rejects=T), _e("REF", "MPG", "NV")),
    ("B04", "It might violate the constraint, but I cannot be sure either way.",          # hedge: NS (12.18 row 12)
     _f(), _e("NOREF", "NS", "NV")),
    ("B05", "The plan violates the constraint.", _f(asserts=T), _e("NOREF", "AG", "NV")),
    ("B06", "Week 3 is 31.0 km and week 4 is 34.1 km.", _f(), _e("NOREF", "NS", "NV")),
    ("B07", "The reviewer is wrong; the plan satisfies the constraint.", _f(rejects=T), _e("REF", "DG", "NV")),
    ("B08", "I cannot tell without knowing the exact limit for week 5.", _f(asks=T), _e("NOREF", "CQ", "NV")),
    ("B09", "From week 3 to week 4 the increase is 10.0 percent, which is less than 10 percent; from week 4 to week 5 it is 25 percent.",
     _f(used=T, derived=[T, T], used_ok=[T], comps=[T, T]),                               # "less than" is not a listed relation
     _e("NOREF", "NS", "IV", prov="Y", values="cv", arith="AC")),
    ("B10", "From week 3 to week 4: (34.9 - 31.0) / 31.0 = 12.6% > 10%.",
     _f(cmp=T, used=T, derived=[T], used_ok=[T, T, T], comps=[T], rels=[T]),
     _e("NOREF", "NS", "EV", prov="Y", values="cv", arith="AC", rel="RT", cev=True)),
    ("B11", "The reviewer says the plan violates the constraint, but the", _f(), _e("REF", "NS", "NV")),            # truncated
    ("B12", "As the review comment says, from week 3 to week 4 the increase of 12.5% exceeds the cap of 10%.",
     _f(asserts=T, cmp=T, used=T, derived=[T], used_ok=[T], comps=[T], rels=[T]),
     _e("REF", "AG", "EV", prov="Y", values="cv", arith="AC", rel="RT", cev=True)),
    ("B13", "Does the reviewer mean the weekly total, and does it say that the plan fails?", _f(asks=T), _e("REF", "CQ", "NV")),
    ("B14", "The plan violates the constraint. Actually, I was wrong: the plan satisfies the constraint.",
     _f(rejects=T), _e("NOREF", "DG", "NV")),                                             # explicit retraction: final position only
    ("B15", "Week 3: 10 x 319 s/km = 3190 s, target 3100 s, difference 90 s > 50 s.",
     _f(cmp=T, used=T, derived=[T, T], used_ok=[T, T, T], comps=[T, T], rels=[T]),
     _e("NOREF", "NS", "EV", prov="Y", values="cv", arith="AC", rel="RT", cev=True)),
    ("B16", "From week 2 to week 3: (34.0 - 32.1) / 32.1 = 5.0%; from week 3 to week 4: (34.9 - 34.0) / 34.0 = 2.6%; 5.0% < 10%.",
     _f(cmp=T, used=T, derived=[T, T], used_ok=[T, T, T, T, T], comps=[F, T], rels=[T]),   # 5.0% is wrong (exact 5.92%): mixed arithmetic
     _e("NOREF", "NS", "EV", prov="Y", values="cv", arith="AM", rel="RT")),
    ("B17", "From week 3 to week 4 the increase is 20.0 percent.",
     _f(used=T, derived=[T], comps=[F]), _e("NOREF", "NS", "IV", prov="Y", arith="AI")),
    ("B18", "The increase is 12.6%.",                                                       # no provenance: not evidence
     _f(derived=[F]), _e("NOREF", "NS", "NV", prov="N")),
    ("B19", "The implied finish time 3190 s is within 50 s of the target 3100 s.",           # listed relation, false as written, no provenance
     _f(cmp=T, used=T, derived=[F], used_ok=[T, T], rels=[F]),
     _e("NOREF", "NS", "EV", prov="N", values="cv", rel="RF")),
    ("B20", "From week 3 to week 4 the increase of 12.6% >= 10%.",                          # ASCII digraph is not listed (12.18 row 9)
     _f(used=T, derived=[T], used_ok=[T], comps=[T]), _e("NOREF", "NS", "IV", prov="Y", values="cv", arith="AC")),
    ("B21", "From week 3 to week 4 the increase of 12.6% \u2265 10%.",
     _f(cmp=T, used=T, derived=[T], used_ok=[T], comps=[T], rels=[T]),
     _e("NOREF", "NS", "EV", prov="Y", values="cv", arith="AC", rel="RT", cev=True)),
    ("B22", "From week 3 to week 4 the increase of 8.0% is below 10%.",
     _f(cmp=T, used=T, derived=[T], used_ok=[T], comps=[T], rels=[T]),
     _e("NOREF", "NS", "EV", prov="Y", values="cv", arith="AC", rel="RT", cev=True)),
    ("B23", "From week 3 to week 4 the increase of 12.6% is greater than 10%.",             # unlisted relation: IV
     _f(used=T, derived=[T], used_ok=[T], comps=[T]), _e("NOREF", "NS", "IV", prov="Y", values="cv", arith="AC")),
    ("B24", "The difference of 90 s for week 3 is not within 50 s.",                        # contains the listed string "within"
     _f(cmp=T, used=T, derived=[T], used_ok=[T], comps=[T], rels=[T]),
     _e("NOREF", "NS", "EV", prov="Y", values="cv", arith="AC", rel="RT", cev=True)),
    ("B25", "None", _f(), _e("NC", "NC", "NC")),                                           # placeholder only: no comment
    ("B26", "The reviewer is right: the plan violates the constraint.", _f(asserts=T), _e("REF", "AG", "NV")),
    ("B27", "I cannot determine compliance from the information given.", _f(asks=T), _e("NOREF", "CQ", "NV")),
    ("B28", "If week 4 is the cap, it might violate.", _f(), _e("NOREF", "NS", "NV")),
    ("B29", "Is the claim about the total or about the weekly distance?", _f(asks=T), _e("REF", "CQ", "NV")),
    ("B30", "The plan violates the constraint. Is that what you meant?", _f(asserts=T, asks=T), _e("NOREF", "AG", "NV")),
    # --- added in the second review (decision record 12.21 row 1): detection is lexical, verification is separate, the vocabulary stays closed ---
    ("B31", "The difference of 30 s for week 3 is not within 50 s.",                        # detected by "within"; the written (negated) relation is FALSE as written
     _f(cmp=T, used=T, derived=[T], used_ok=[T], comps=[T], rels=[F]),
     _e("NOREF", "NS", "EV", prov="Y", values="cv", arith="AC", rel="RF")),
    ("B32", "From week 3 to week 4 the increase of 12.6% is not above 10%.",                 # "is not above" is not "is above": unregistered; IV only through the derived quantity
     _f(used=T, derived=[T], used_ok=[T], comps=[T]), _e("NOREF", "NS", "IV", prov="Y", values="cv", arith="AC")),
    ("B33", "This is not below the limit.", _f(), _e("NOREF", "NS", "NV")),                    # unregistered, no numeral: NV, not IV
    ("B34", "The increase does not exceed the cap.", _f(), _e("NOREF", "NS", "NV")),           # unregistered, no numeral: NV
    ("B35", "From week 3 to week 4 the increase of 12.6% surpasses 10%.",                      # unregistered synonym
     _f(used=T, derived=[T], used_ok=[T], comps=[T]), _e("NOREF", "NS", "IV", prov="Y", values="cv", arith="AC")),
    ("B36", "From week 3 to week 4 the increase of 12.6% EXCEEDS the cap of 10%.",             # case-insensitive
     _f(cmp=T, used=T, derived=[T], used_ok=[T], comps=[T], rels=[T]),
     _e("NOREF", "NS", "EV", prov="Y", values="cv", arith="AC", rel="RT", cev=True)),
    ("B37", "The increase of 12.6% exceeded the cap of 10% in week 3.",                        # "exceeded" is not "exceeds": no stemming
     _f(used=T, derived=[T], used_ok=[T], comps=[T]), _e("NOREF", "NS", "IV", prov="Y", values="cv", arith="AC")),
    ("B38", "From week 3 to week 4 the increase of 8.0% <= 10%.",                              # ASCII digraph is not the listed symbol
     _f(used=T, derived=[T], used_ok=[T], comps=[T]), _e("NOREF", "NS", "IV", prov="Y", values="cv", arith="AC")),
    ("B39", "From week 3 to week 4 the increase of 8.0% \u2264 10%.",                          # U+2264 is listed
     _f(cmp=T, used=T, derived=[T], used_ok=[T], comps=[T], rels=[T]),
     _e("NOREF", "NS", "EV", prov="Y", values="cv", arith="AC", rel="RT", cev=True)),
    ("B40", "The difference of 90 s for week 3 is WITHIN 50 s.",                               # case-insensitive; false as written
     _f(cmp=T, used=T, derived=[T], used_ok=[T], comps=[T], rels=[F]),
     _e("NOREF", "NS", "EV", prov="Y", values="cv", arith="AC", rel="RF")),
)
