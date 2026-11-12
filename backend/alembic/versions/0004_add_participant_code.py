"""Add plan_evaluations.participant_code (anonymous pairing code for the RQ3 instrument).

Purely additive: one NULLABLE column and one index on plan_evaluations. Every existing row keeps
participant_code = NULL; no existing row is rewritten. The code is a random-looking token of the form
P-XXXXXXXX validated by the API (app/research/blinding.py); it is not an account, e-mail or name.

NOT applied to the real running_research.db by this change. Apply it deliberately with `alembic upgrade head`
after backing the database file up.

Revision ID: 0004_participant_code
Revises: 0003_workout_logging
Create Date: 2026-10-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_participant_code"
down_revision = "0003_workout_logging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plan_evaluations", sa.Column("participant_code", sa.String(16), nullable=True))
    op.create_index("ix_plan_evaluations_participant_code", "plan_evaluations", ["participant_code"])


def downgrade() -> None:
    op.drop_index("ix_plan_evaluations_participant_code", table_name="plan_evaluations")
    op.drop_column("plan_evaluations", "participant_code")
