"""Synthetic batteries for the verdict parser (13.4) and the Comments boundary (owner decision 12.18 row 11) -- DRAFT.

Written for the revision after the Step 7 audit (decision record 12.18). Every string is synthetic. None comes from a
pilot or main response (none exists) or from Stage 1/2 records. The expected outcomes below were fixed BEFORE the revised
code was written or run; the SHA-256 of this file is recorded in the decision record (12.19) before any test uses it.
The module is ASCII only: non-ASCII characters are written as escapes.

Whitespace in the grammar = ASCII space, tab, CR, VT, FF (12.18 row 7). List or heading prefixes are not tolerated.
"""

P_ = "PARSEABLE"
U_ = "UNP"
MARKER = "[Verdict line removed]"

# (response, expected outcome of parse_verdict and of ref_parse_verdict)
PARSER_BATTERY = (
    ("Constraint: x\nVerdict: satisfied\nComments: fine", (P_, "satisfied")),
    ("Constraint: x\nVerdict: violated\nComments: no", (P_, "violated")),
    ("Verdict: unclear", (P_, "unclear")),
    ("verdict: SATISFIED", (P_, "satisfied")),
    ("VERDICT: Violated.", (P_, "violated")),
    ("**Verdict:** satisfied", (P_, "satisfied")),
    ("**Verdict: violated**", (P_, "violated")),
    ("Verdict: *unclear*", (P_, "unclear")),
    ("`Verdict:` `satisfied`", (P_, "satisfied")),
    ("  Verdict :  violated  ", (P_, "violated")),
    ("Verdict: satisfied!", (P_, "satisfied")),
    ("Verdict: satisfied\nVerdict: satisfied", (P_, "satisfied")),
    ("Verdict: satisfied\nVerdict: violated", (U_, "conflicting fields")),
    ("Verdict: satisfied\nVerdict: banana", (U_, "conflicting fields")),
    ("Constraint: x\nComments: nothing here", (U_, "no field")),
    ("", (U_, "no field")),
    ("Verdict:", (U_, "token not allowed")),
    ("Verdict: satisfied because week 3 is fine", (U_, "token not allowed")),
    ("Verdict: maybe", (U_, "token not allowed")),
    ("The verdict is satisfied", (U_, "no field")),
    ("Comments: Verdict: satisfied", (U_, "no field")),
    ("Verdict: satisfied\r", (P_, "satisfied")),
    ("Constraint: x\nVerdict: viol", (U_, "token not allowed")),                       # truncated response
    ("Constraint: copy of the rule that was cut off mid-", (U_, "no field")),          # truncated before the field
    ("Verdict: not satisfied", (U_, "token not allowed")),
    ("Verdict: satisfied, violated", (U_, "token not allowed")),
    ("verdict:\tunclear", (P_, "unclear")),
    ("\u2003Verdict: satisfied", (U_, "no field")),                                    # Unicode space before the label
    ("Verdict\uff1a satisfied", (U_, "no field")),                                     # fullwidth colon
    ("V\u0435rdict: satisfied", (U_, "no field")),                                     # Cyrillic e
    # --- added after the audit (12.18 row 7: ASCII whitespace only; no list or heading prefix) ---
    ("Verdict: satisfied\u00a0", (U_, "token not allowed")),                           # no-break space is not whitespace
    ("Verdict: \u2003satisfied", (U_, "token not allowed")),                           # em space is not whitespace
    ("- Verdict: satisfied", (U_, "no field")),
    ("## Verdict: satisfied", (U_, "no field")),
    ("1. Verdict: violated", (U_, "no field")),
    ("> Verdict: violated", (U_, "no field")),
    ("Verdict: unclear?", (U_, "token not allowed")),
    ("Verdict: 'satisfied'", (U_, "token not allowed")),
    ("Verdict: [satisfied]", (U_, "token not allowed")),
    ("Verdict:satisfied", (P_, "satisfied")),
    ("**Verdict**: **Satisfied**", (P_, "satisfied")),
    ("Verdict: satisfied.\nVerdict: Satisfied!", (P_, "satisfied")),
    ("Final Verdict: violated", (U_, "no field")),
    ("Verdict (final): violated", (U_, "no field")),
    ("Verdict: satisfied\x0b", (P_, "satisfied")),                                     # VT is ASCII whitespace
    ("Verdict: satisfied\x0c", (P_, "satisfied")),                                     # FF is ASCII whitespace
    ("Verdict: satisfied\x1c", (U_, "token not allowed")),                             # FS: str.isspace() is true, not ASCII whitespace here
    ("Verdict: satisfied\x85", (U_, "token not allowed")),
    ("Verdict: satisfied\u200b", (U_, "token not allowed")),
    ("Verdict: * satisfied *", (P_, "satisfied")),
    ("Verdict: satisfied\nVerdict: violated\nVerdict: satisfied", (U_, "conflicting fields")),
    ("Verdict: ", (U_, "token not allowed")),
    ("VERDICT:UNCLEAR", (P_, "unclear")),
    ("Verdict: Satisfied \t", (P_, "satisfied")),
    ("Constraint: x\r\nVerdict: satisfied\r\nComments: ok\r\n", (P_, "satisfied")),
)

# (response, expected comments_of(response)); None = no comment (NC). Verdict lines after the label are replaced by MARKER.
COMMENTS_BATTERY = (
    ("Constraint: x\nVerdict: satisfied\nComments: fine", "fine"),
    ("Constraint: x\nVerdict: satisfied", None),
    ("Constraint: x\nVerdict: satisfied\nComments:", None),
    ("Constraint: x\nVerdict: satisfied\nComments: None", None),
    ("Constraint: x\nVerdict: satisfied\nComments: N/A", None),
    ("Constraint: x\nVerdict: satisfied\nComments: -", None),
    ("Constraint: x\nVerdict: satisfied\nComments: **None**", None),
    ("Constraint: x\nVerdict: satisfied\nComments: none", None),
    ("Verdict: violated\nComments: week 4 rises.\nIt is above the cap.", "week 4 rises.\nIt is above the cap."),
    ("Comments: The reviewer is wrong.\nVerdict: satisfied", "The reviewer is wrong.\n" + MARKER),
    ("**Comments:** ok", "ok"),
    ("comments: ok", "ok"),
    ("Comments:\nVerdict: satisfied", None),
    ("Constraint: see Comments: here\nVerdict: satisfied", None),
    ("Comments: a\nComments: b", "a\nComments: b"),
    ("Comments: Verdict: satisfied", "Verdict: satisfied"),
    ("Comments:\n\nThe plan is fine.", "The plan is fine."),
    ("Verdict: satisfied\r\nComments: ok\r\n", "ok"),
    ("Comments: None of the weeks exceed the cap.", "None of the weeks exceed the cap."),
    ("", None),
)
