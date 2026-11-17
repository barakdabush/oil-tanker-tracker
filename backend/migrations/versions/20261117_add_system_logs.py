"""Add system_logs table for container and worker observability.

Revision ID: add_system_logs
Revises: add_portwatch_and_ofac
Create Date: 2026-11-17 12:56:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "add_system_logs"
down_revision: Union[str, None] = "add_portwatch_and_ofac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("service", sa.String(length=32), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("logger_name", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_system_logs_created_at", "system_logs", ["created_at"])
    op.create_index("idx_system_logs_service_level", "system_logs", ["service", "level"])


def downgrade() -> None:
    op.drop_index("idx_system_logs_service_level", table_name="system_logs")
    op.drop_index("idx_system_logs_created_at", table_name="system_logs")
    op.drop_table("system_logs")
