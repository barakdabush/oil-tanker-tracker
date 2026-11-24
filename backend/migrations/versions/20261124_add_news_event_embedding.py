"""Add optional embedding JSONB column to news_events for semantic analog matching.

Revision ID: add_news_event_embedding
Revises: dedup_satellite_overpasses
Create Date: 2026-11-24 18:00:00.000000
"""

from typing import Sequence, Union
from alembic import op


revision: str = "add_news_event_embedding"
down_revision: Union[str, None] = "dedup_satellite_overpasses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE news_events
        ADD COLUMN IF NOT EXISTS embedding JSONB;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE news_events
        DROP COLUMN IF EXISTS embedding;
        """
    )
