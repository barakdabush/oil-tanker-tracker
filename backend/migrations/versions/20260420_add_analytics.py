"""Add analytics tables for oil market snapshots and prices.

Revision ID: add_analytics
Revises: base_initial
Create Date: 2026-04-20 18:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'add_analytics'
down_revision: Union[str, None] = 'base_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL with IF NOT EXISTS because the seed SQL (03_analytics_tables.sql)
    # may have already created these tables before Alembic runs.
    op.execute("""
        CREATE TABLE IF NOT EXISTS oil_market_snapshots (
            snapshot_date DATE PRIMARY KEY,
            total_active_vessels INT,
            vessels_in_transit INT,
            vessels_at_port INT,
            avg_fleet_speed FLOAT,
            vessels_idle_gt_48h INT,
            dark_vessels_count INT,
            new_ais_gaps_24h INT,
            resolved_gaps_24h INT,
            avg_gap_duration_hours FLOAT,
            sts_events_24h INT,
            sts_confirmed_24h INT,
            chokepoint_transits_24h INT,
            strait_of_hormuz_transits INT,
            cargo_events_24h INT,
            estimated_volume_barrels_24h FLOAT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS oil_prices (
            price_date DATE PRIMARY KEY,
            brent_close_usd FLOAT,
            wti_close_usd FLOAT,
            fetched_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.drop_table('oil_prices')
    op.drop_table('oil_market_snapshots')
