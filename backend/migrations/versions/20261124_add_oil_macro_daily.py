"""Add oil_macro_daily table for 5-year crack spreads, DXY, VIX, and tanker equity features.

Revision ID: add_oil_macro_daily
Revises: add_news_event_embedding
Create Date: 2026-11-24 21:20:00.000000
"""

from typing import Sequence, Union
from alembic import op


revision: str = "add_oil_macro_daily"
down_revision: Union[str, None] = "add_news_event_embedding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS oil_macro_daily (
            date DATE PRIMARY KEY,
            rbob_gasoline_usd DOUBLE PRECISION,
            heating_oil_usd DOUBLE PRECISION,
            crack_spread_321_usd DOUBLE PRECISION,
            brent_wti_spread_usd DOUBLE PRECISION,
            dxy_close DOUBLE PRECISION,
            vix_close DOUBLE PRECISION,
            tanker_etf_close DOUBLE PRECISION,
            energy_etf_close DOUBLE PRECISION,
            fetched_at TIMESTAMPTZ DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS oil_macro_daily;")
