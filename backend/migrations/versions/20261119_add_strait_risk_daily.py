"""Add strait_risk_daily table for Straits.live and GDELT Hormuz/Bab el-Mandeb risk features.

Revision ID: add_strait_risk_daily
Revises: add_strait_traffic_and_delay
Create Date: 2026-11-19 00:20:00.000000

"""
from typing import Sequence, Union
from alembic import op


revision: str = "add_strait_risk_daily"
down_revision: Union[str, None] = "add_strait_traffic_and_delay"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS strait_risk_daily (
            date DATE PRIMARY KEY,
            hormuz_crisis_pressure FLOAT,
            hormuz_escalation_prob FLOAT,
            hormuz_stranded_vessels FLOAT,
            hormuz_incidents_count INT,
            gdelt_strait_risk_vol FLOAT,
            gdelt_strait_tone FLOAT,
            fetched_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS strait_risk_daily")
