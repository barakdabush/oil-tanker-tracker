"""Add incidents table for the auto-incident (logs -> GitHub issues) pipeline.

Revision ID: add_incidents
Revises: add_system_logs
Create Date: 2026-11-17 13:35:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "add_incidents"
down_revision: Union[str, None] = "add_system_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("service", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("sample_message", sa.Text(), nullable=False),
        sa.Column("logger_name", sa.String(length=128), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("occurrences_at_notify", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("github_issue_number", sa.Integer(), nullable=True),
        sa.Column("issue_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("fingerprint"),
    )
    op.create_index("idx_incidents_status", "incidents", ["status"])


def downgrade() -> None:
    op.drop_index("idx_incidents_status", table_name="incidents")
    op.drop_table("incidents")
