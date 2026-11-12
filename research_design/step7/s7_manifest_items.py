"""Reproducible item hashes of the Step 7 manifest (decision record 12.13 item 3: "documented transparently, including which items
are hashed and how") -- DRAFT.

compute_item_hashes() returns, for every manifest item, the SHA-256 and the exact recipe. Recipes:
  canon(obj)  = SHA-256 of the UTF-8 bytes of json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
  utf8(text)  = SHA-256 of the UTF-8 bytes of the text
  file(name)  = SHA-256 of the bytes of the file in research_design/step7/
This module reads files (read-only) and writes nothing. Run it to print the table:  python -B s7_manifest_items.py
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

import prompt_specification_v1 as S  # noqa: E402
import s7_diagnostic as DG  # noqa: E402
import s7_recode as RC  # noqa: E402

CANON_RECIPE = 'sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))'


def canon_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def utf8_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_hash(name, here=HERE):
    with open(os.path.join(here, name), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


FILE_ITEMS = {
    "I09_numeral_renderers": "s7_core.py",
    "I10_verdict_parser_grammar": "s7_parser.py",
    "I11_frozen_reference_grammar_checker": "s7_refcheck.py",
    "I17_codebook_S2_S3": "CODEBOOK_S2_S3_v1_DRAFT.md",
    "I18_codebook_synthetic_battery": "codebook_battery_v1.py",
    "I19_pg11a_pg11b_bundle_builders": "s7_bundle.py",
    "I20_decision_script": "s7_decision.py",
    "I21_prompt_specification_module": "prompt_specification_v1.py",
    "I22_structural_diagnostic_code": "s7_diagnostic.py",
    "I24_parser_and_comments_battery": "parser_battery_v1.py",
    "I26_recode_procedure": "s7_recode.py",
    "I29_outcomes_proposed_policy": "s7_outcomes.py",
    "I30_proposed_amendment_18_2": "PROPOSED_AMENDMENT_18_2_DRAFT_v1.md",
    "I27_proposed_clarifications_and_amendments": "PROPOSED_CLARIFICATIONS_AND_AMENDMENTS_DRAFT_v1.md",
}
SUPPORT_FILES = ("test_step7.py", "s7_manifest_items.py")


def canon_items():
    F = S.FAMILIES
    return {
        "I01_constraint_label_per_family": {f: F[f]["constraint_label"] for f in F},
        "I02_rule_text_per_family_with_slot_markers": {
            "rule_templates": {f: F[f]["rule_template"] for f in F},
            "slots": {f: list(F[f]["slots"]) for f in F},
            "slot_markers": {"P": S.SLOT_P, "D": S.SLOT_D, "TAU": S.SLOT_TAU}},
        "I03_plan_format_and_labels": {f: {k: v for k, v in F[f].items()
                                           if k in ("plan_header", "plan_separator", "distance_label", "time_label")} for f in F},
        "I07_reviewer_claim_block_and_insertion": {
            "claim": S.CLAIM_BLOCK, "joiner": S.JOINER,
            "position": "one segment between the plan block and the task text, joined by the joiner on both sides",
            "removal": "delete joiner + claim exactly once"},
        # I08 and I12 keep the structures of the first-draft manifest, so that identical wording gives identical hashes
        "I08_block_markers": {**{f: F[f]["block_start_marker"] for f in F}, "end": S.BLOCK_END_MARKER},
        "I12_pt_patterns_PT1_to_PT10": {"PT": [list(p) for p in S.PT_PATTERNS], "PT10": S.PT10_PATTERN},
        "I13_pt_control_batteries": {"negative": list(S.PT_NEGATIVE_CONTROLS), "positive": list(S.PT_POSITIVE_CONTROLS),
                                     "pt10_positive": list(S.PT10_POSITIVE_CONTROLS),
                                     "pt10_negative": list(S.PT10_NEGATIVE_CONTROLS)},
        "I14_pg12_whitelist_entries_and_controls": {
            "mil_pass": list(S.PG12_MIL_ROW_MUST_PASS), "mil_fail": list(S.PG12_MIL_ROW_MUST_FAIL),
            "pac_pass": list(S.PG12_PAC_ROW_MUST_PASS), "pac_fail": list(S.PG12_PAC_ROW_MUST_FAIL),
            "dist_noncanonical_for_d10": list(S.PG12_DIST_NONCANONICAL_FOR_D10)},
        "I15_anti_cue_list": list(S.ANTI_CUE_WORDS),
        "I16_reviewer_reference_trigger_list": list(S.REVIEWER_REFERENCE_PHRASES),
        "I25_diagnostic_report_schema": {
            "report_fields": list(DG.REPORT_FIELDS), "labels": list(DG.LABELS),
            "finish_reason_pattern": DG._FINISH_RX.pattern, "name_pattern": DG._NAME_RX.pattern, "file_pattern": DG._FILE_RX.pattern,
            "unrecognized": DG.UNRECOGNIZED},
        "I28_recode_parameters": {
            "floor": RC.RECODE_FLOOR, "fraction": [RC.RECODE_NUM, RC.RECODE_DEN], "delay_days": RC.RECODE_DELAY_DAYS},
        "I23_codebook_lexical_data": {
            "ascii_whitespace": S.ASCII_WHITESPACE, "emphasis_chars": S.EMPHASIS_CHARS,
            "verdict_trailing_punctuation": S.VERDICT_TRAILING_PUNCTUATION, "redaction_marker": S.REDACTION_MARKER,
            "empty_comment_tokens": list(S.EMPTY_COMMENT_TOKENS), "relation_words": list(S.RELATION_WORDS),
            "relation_ascii_symbols": list(S.RELATION_ASCII_SYMBOLS),
            "relation_unicode_symbol_codepoints": [ord(c) for c in S.RELATION_UNICODE_SYMBOLS],
            "relation_run_alphabet": S.RELATION_RUN_ALPHABET},
    }


def compute_item_hashes(here=HERE):
    out = {}
    for k, obj in canon_items().items():
        out[k] = {"sha256": canon_hash(obj), "recipe": "canon(" + k + " object of canon_items())"}
    out["I05_task_text"] = {"sha256": utf8_hash(S.TASK_TEXT), "recipe": "utf8(TASK_TEXT)"}
    out["I06_response_format"] = {"sha256": utf8_hash(S.RESPONSE_FORMAT), "recipe": "utf8(RESPONSE_FORMAT)"}
    for k, name in FILE_ITEMS.items():
        out[k] = {"sha256": file_hash(name, here), "recipe": "file(" + name + ")"}
    return dict(sorted(out.items()))


def compute_file_hashes(here=HERE):
    names = sorted(set(FILE_ITEMS.values()) | set(SUPPORT_FILES) | {"s7_manifest_items.py"})
    return {n: file_hash(n, here) for n in names}


if __name__ == "__main__":
    print(json.dumps({"canon_recipe": CANON_RECIPE, "items": compute_item_hashes(), "file_hashes": compute_file_hashes()}, indent=1))
