"""
RQ3 instrument: blinded presentation, anonymous participant code, neutral defaults.

These tests check (a) that the participant-facing API payloads and the blinded page source do not name the
condition, (b) that the server maps A/B to the right internal condition, (c) that participant codes cannot
carry free text, and (d) that the rating page has no pre-selected answer.

What they do NOT show: that a human participant cannot tell the plans apart. The personalized plan has more
text than the baseline, so content can still reveal the source. Blinding is only established for labels and
field names, and nothing here claims more.
"""
import json
import re
import uuid
from pathlib import Path

import pytest

from app.research.blinding import blind_assignment, neutral_plan_view
from tests.factories import make_profile_payload, register_and_login

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

FORBIDDEN_IN_PAYLOAD = [
    "rule_based", "rule-based", "rule baseline", "ai_personalized", "ai-personalized", "ai_model", "ai_plan",
    "gemini", "openai", "offline", "fallback", "evaluated_plan_type", "llm", "synthesizer", "ai component",
]
IDENTITY_WORD = re.compile(r"\b(ai|llm|gemini|openai|rule[- ]based|offline|fallback)\b", re.IGNORECASE)


def _generate_plan(client, **overrides):
    res = client.post("/api/plans/generate", json=make_profile_payload(**overrides))
    assert res.status_code == 201, res.text
    return res.json()["plan_id"]


def _code():
    return "P-" + uuid.uuid4().hex[:8].upper()


def _body(plan_id, label="A", **overrides):
    body = {
        "training_plan_id": plan_id, "plan_label": label, "participant_code": _code(),
        "personalization_score": 3, "usefulness_score": 4, "clarity_score": 2, "confidence_score": 5,
        "comments": None,
    }
    body.update(overrides)
    return body


def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


class TestBlindAssignment:
    def test_assignment_is_a_permutation_and_deterministic(self):
        for _ in range(50):
            pid = str(uuid.uuid4())
            a = blind_assignment(pid)
            assert sorted(a.values()) == ["ai_personalized", "rule_based"]
            assert a == blind_assignment(pid)

    def test_assignment_is_roughly_balanced_across_plans(self):
        a_is_baseline = sum(blind_assignment(str(uuid.uuid4()))["A"] == "rule_based" for _ in range(2000))
        assert 0.45 <= a_is_baseline / 2000 <= 0.55


class TestParticipantPayloadDoesNotNameTheCondition:
    def test_blinded_plans_have_no_identifying_keys_or_words(self, client):
        plan_id = _generate_plan(client)
        res = client.get(f"/api/evaluations/blind/{plan_id}")
        assert res.status_code == 200
        payload = res.json()
        assert [p["label"] for p in payload["plans"]] == ["A", "B"]
        text = json.dumps(payload).lower()
        leaks = [w for w in FORBIDDEN_IN_PAYLOAD if w in text]
        assert not leaks, f"participant payload names the condition: {leaks}"
        assert not any("rule" in k.lower() or k.lower().startswith("ai") for k in _all_keys(payload)), \
            "a key name in the participant payload names the source"
        assert not IDENTITY_WORD.search(text), "free text in the participant payload names how the plan was produced"

    def test_both_plans_share_the_same_week_structure_keys(self, client):
        plan_id = _generate_plan(client)
        plans = client.get(f"/api/evaluations/blind/{plan_id}").json()["plans"]
        week_keys = [set(p["weeks"][0].keys()) for p in plans]
        assert week_keys[0] == week_keys[1]
        assert len(plans[0]["weeks"]) == len(plans[1]["weeks"])

    def test_the_mapping_is_not_in_the_payload_but_matches_the_server_side_rule(self, client):
        plan_id = _generate_plan(client)
        payload = client.get(f"/api/evaluations/blind/{plan_id}").json()
        assert "mapping" not in json.dumps(payload).lower()
        # The first workout row of label A equals the stored plan chosen by the server-side rule.
        from app.database import SessionLocal
        from app.models import TrainingPlan
        db = SessionLocal()
        try:
            row = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
            sources = {"rule_based": json.loads(row.rule_based_plan_json), "ai_personalized": json.loads(row.ai_plan_json)}
        finally:
            db.close()
        expected = neutral_plan_view(sources[blind_assignment(plan_id)["A"]])
        got = {k: v for k, v in payload["plans"][0].items() if k != "label"}
        assert got == expected


class TestSubmission:
    def test_label_is_mapped_to_the_internal_condition_and_not_revealed(self, client):
        plan_id = _generate_plan(client)
        for label in ("A", "B"):
            res = client.post("/api/evaluations/blind", json=_body(plan_id, label))
            assert res.status_code == 201, res.text
            assert set(res.json().keys()) == {"id", "plan_label", "created_at"}
            assert res.json()["plan_label"] == label
        from app.database import SessionLocal
        from app.models import PlanEvaluation
        db = SessionLocal()
        try:
            rows = db.query(PlanEvaluation).filter(PlanEvaluation.training_plan_id == plan_id).all()
            stored = {}
            assert len(rows) == 2
            mapping = blind_assignment(plan_id)
            assert sorted(r.evaluated_plan_type for r in rows) == sorted(mapping.values())
            assert all(r.participant_code and r.participant_code.startswith("P-") for r in rows)
        finally:
            db.close()

    @pytest.mark.parametrize("bad_code", [
        "Budi Santoso", "budi@example.com", "081234567890", "p-1a2b3c4d", "P-1A2B3C4", "P-1A2B3C4D5", "", "P-ZZZZZZZZ",
    ])
    def test_participant_code_cannot_carry_free_text(self, client, bad_code):
        plan_id = _generate_plan(client)
        res = client.post("/api/evaluations/blind", json=_body(plan_id, participant_code=bad_code))
        assert res.status_code == 422

    @pytest.mark.parametrize("override", [
        {"plan_label": "C"}, {"personalization_score": 0}, {"usefulness_score": 6}, {"clarity_score": None},
    ])
    def test_invalid_label_or_missing_score_is_rejected(self, client, override):
        plan_id = _generate_plan(client)
        res = client.post("/api/evaluations/blind", json=_body(plan_id, **override))
        assert res.status_code == 422

    def test_missing_participant_code_is_rejected(self, client):
        plan_id = _generate_plan(client)
        body = _body(plan_id)
        del body["participant_code"]
        assert client.post("/api/evaluations/blind", json=body).status_code == 422

    def test_unknown_plan_is_404(self, client):
        assert client.get("/api/evaluations/blind/not-a-plan").status_code == 404
        assert client.post("/api/evaluations/blind", json=_body("not-a-plan")).status_code == 404

    def test_a_consumer_owned_plan_is_not_served_or_rated_here(self, client):
        register_and_login(client)
        plan_id = _generate_plan(client)  # generated while logged in => user-owned plan
        assert client.get(f"/api/evaluations/blind/{plan_id}").status_code == 404
        assert client.post("/api/evaluations/blind", json=_body(plan_id)).status_code == 404

    def test_original_endpoint_is_unchanged_and_stores_no_participant_code(self, client):
        plan_id = _generate_plan(client)
        res = client.post("/api/evaluations", json={
            "training_plan_id": plan_id, "evaluated_plan_type": "rule_based", "personalization_score": 4,
            "usefulness_score": 4, "clarity_score": 4, "confidence_score": 4,
        })
        assert res.status_code == 201
        from app.database import SessionLocal
        from app.models import PlanEvaluation
        db = SessionLocal()
        try:
            assert db.query(PlanEvaluation).one().participant_code is None
        finally:
            db.close()


class TestFrontendSourceStatic:
    """Static checks of the shipped source. They cannot prove the rendered page; see the module docstring."""

    def test_blind_page_source_does_not_name_the_condition(self):
        src = (FRONTEND / "pages" / "BlindEvaluationPage.jsx").read_text(encoding="utf-8").lower()
        code_only = re.sub(r"//[^\n]*", "", src)  # comments may explain the design; shipped strings may not
        code_only = re.sub(r"/\*.*?\*/", "", code_only, flags=re.S)
        for word in ["rule_based", "rule-based", "ai_personalized", "ai-personalized", "baseline", "gemini", "openai",
                     "evaluated_plan_type", "ai_model", "llm", "disclaimerbanner"]:
            assert word not in code_only, word

    def test_rating_defaults_are_unset_on_both_pages(self):
        for name in ("EvaluationPage.jsx", "BlindEvaluationPage.jsx"):
            src = (FRONTEND / "pages" / name).read_text(encoding="utf-8")
            block = re.search(r"EMPTY_SCORES = \{(.*?)\};", src, re.S).group(1)
            assert block.count(": null") == 4, name
            assert not re.search(r"_score:\s*[1-5]\b", src), f"{name} pre-selects a numeric score"

    def test_blind_page_sets_a_neutral_tab_title(self):
        src = (FRONTEND / "pages" / "BlindEvaluationPage.jsx").read_text(encoding="utf-8")
        assert "document.title = 'Plan rating'" in src

    def test_submit_is_blocked_until_all_four_answers_exist(self):
        for name in ("EvaluationPage.jsx", "BlindEvaluationPage.jsx"):
            src = (FRONTEND / "pages" / name).read_text(encoding="utf-8")
            assert "=== null" in src or "!== null" in src
            assert "Not rated yet" in src
