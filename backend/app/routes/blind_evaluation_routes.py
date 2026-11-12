"""
Blinded evaluation endpoints for the RQ3 instrument (additive; the existing /evaluations endpoints are unchanged).

* GET  /evaluations/blind/{plan_id}  -> the two plans as "A" and "B", with no source-identifying keys.
* POST /evaluations/blind            -> stores one rating for label A or B; the label is mapped to the internal
                                        condition on the server and the response does not reveal the mapping.

Only anonymous research plans (training_plans.user_id IS NULL) are served; a plan owned by a consumer account
returns the same generic 404 as a missing plan. No data is collected by this change: these endpoints are the
transport for an instrument that is not in use until a study is authorised.
"""
import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import PlanEvaluation, TrainingPlan
from app.research.blinding import blinded_plans, is_valid_participant_code, label_to_condition

router = APIRouter(prefix="/evaluations/blind", tags=["Blinded research evaluation"])


class BlindEvaluationCreate(BaseModel):
    training_plan_id: str
    plan_label: Literal["A", "B"]
    participant_code: str = Field(..., description="Anonymous code of the form P-XXXXXXXX (8 hex characters)")
    personalization_score: int = Field(..., ge=1, le=5)
    usefulness_score: int = Field(..., ge=1, le=5)
    clarity_score: int = Field(..., ge=1, le=5)
    confidence_score: int = Field(..., ge=1, le=5)
    comments: Optional[str] = Field(None, max_length=2000)

    @field_validator("participant_code")
    @classmethod
    def _code_is_anonymous_token(cls, v: str) -> str:
        if not is_valid_participant_code(v):
            raise ValueError("participant_code must look like P-1A2B3C4D (no names or contact details)")
        return v


def _anonymous_plan_or_404(db: Session, plan_id: str) -> TrainingPlan:
    plan = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
    if plan is None or plan.user_id is not None:
        raise HTTPException(status_code=404, detail="Training plan not found")
    return plan


@router.get("/{plan_id}")
def get_blinded_plans(plan_id: str, db: Session = Depends(get_db)):
    plan = _anonymous_plan_or_404(db, plan_id)
    return {
        "plan_id": plan.id,
        "plans": blinded_plans(plan.id, json.loads(plan.rule_based_plan_json), json.loads(plan.ai_plan_json)),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def submit_blinded_evaluation(body: BlindEvaluationCreate, db: Session = Depends(get_db)):
    plan = _anonymous_plan_or_404(db, body.training_plan_id)
    row = PlanEvaluation(
        training_plan_id=plan.id,
        evaluated_plan_type=label_to_condition(plan.id, body.plan_label),
        participant_code=body.participant_code,
        personalization_score=body.personalization_score,
        usefulness_score=body.usefulness_score,
        clarity_score=body.clarity_score,
        confidence_score=body.confidence_score,
        comments=body.comments,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    # Deliberately no condition, model or mapping in the response.
    return {"id": row.id, "plan_label": body.plan_label, "created_at": row.created_at}
