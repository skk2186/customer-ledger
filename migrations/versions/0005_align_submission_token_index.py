"""Align the idempotency token index with the model's unique index.

Revision ID: 0005_align_submission_token_index
Revises: 0004_bookkeeping_workflow
"""

from alembic import op

revision = "0005_align_submission_token_index"
down_revision = "0004_bookkeeping_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_submission_record_token", table_name="submission_record")
    op.create_index("ix_submission_record_token", "submission_record", ["token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_submission_record_token", table_name="submission_record")
    op.create_index("ix_submission_record_token", "submission_record", ["token"], unique=False)
