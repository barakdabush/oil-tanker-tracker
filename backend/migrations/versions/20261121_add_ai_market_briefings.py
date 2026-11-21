"""Add ai_market_briefings table for stored AI Market Briefing Agent summaries.

Revision ID: add_ai_market_briefings
Revises: add_news_intelligence
Create Date: 2026-11-21 13:24:00.000000

"""
from typing import Sequence, Union
from alembic import op


revision: str = "add_ai_market_briefings"
down_revision: Union[str, None] = "add_news_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ai_market_briefings (
            id BIGSERIAL PRIMARY KEY,
            window_days INT NOT NULL DEFAULT 14,
            model_used VARCHAR(128),
            market_bias VARCHAR(24) NOT NULL DEFAULT 'neutral',
            confidence FLOAT,
            executive_summary TEXT NOT NULL,
            key_points JSONB NOT NULL DEFAULT '[]'::jsonb,
            watch_items JSONB NOT NULL DEFAULT '[]'::jsonb,
            full_markdown TEXT,
            context_snapshot JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_market_briefings_created "
        "ON ai_market_briefings (created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_market_briefings")
