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
    # Create oil_market_snapshots table
    op.create_table(
        'oil_market_snapshots',
        sa.Column('snapshot_date', sa.Date(), nullable=False),
        sa.Column('total_active_vessels', sa.Integer(), nullable=True),
        sa.Column('vessels_in_transit', sa.Integer(), nullable=True),
        sa.Column('vessels_at_port', sa.Integer(), nullable=True),
        sa.Column('avg_fleet_speed', sa.Float(), nullable=True),
        sa.Column('vessels_idle_gt_48h', sa.Integer(), nullable=True),
        sa.Column('dark_vessels_count', sa.Integer(), nullable=True),
        sa.Column('new_ais_gaps_24h', sa.Integer(), nullable=True),
        sa.Column('resolved_gaps_24h', sa.Integer(), nullable=True),
        sa.Column('avg_gap_duration_hours', sa.Float(), nullable=True),
        sa.Column('sts_events_24h', sa.Integer(), nullable=True),
        sa.Column('sts_confirmed_24h', sa.Integer(), nullable=True),
        sa.Column('chokepoint_transits_24h', sa.Integer(), nullable=True),
        sa.Column('strait_of_hormuz_transits', sa.Integer(), nullable=True),
        sa.Column('cargo_events_24h', sa.Integer(), nullable=True),
        sa.Column('estimated_volume_barrels_24h', sa.Float(), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('NOW()'), nullable=True),
        sa.PrimaryKeyConstraint('snapshot_date')
    )

    # Create oil_prices table
    op.create_table(
        'oil_prices',
        sa.Column('price_date', sa.Date(), nullable=False),
        sa.Column('brent_close_usd', sa.Float(), nullable=True),
        sa.Column('wti_close_usd', sa.Float(), nullable=True),
        sa.Column('fetched_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('NOW()'), nullable=True),
        sa.PrimaryKeyConstraint('price_date')
    )


def downgrade() -> None:
    op.drop_table('oil_prices')
    op.drop_table('oil_market_snapshots')
