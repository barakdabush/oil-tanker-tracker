"""Add per-strait traffic, diversion, and transit delay features to oil_market_snapshots.

Revision ID: add_strait_traffic_and_delay
Revises: add_incidents
Create Date: 2026-11-18 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op


revision: str = "add_strait_traffic_and_delay"
down_revision: Union[str, None] = "add_incidents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_COLUMNS = [
    ("cape_of_good_hope_transits", "INT"),
    ("bab_el_mandeb_transits", "INT"),
    ("suez_canal_transits", "INT"),
    ("strait_of_malacca_transits", "INT"),
    ("panama_canal_transits", "INT"),
    ("turkish_straits_transits", "INT"),
    ("danish_straits_transits", "INT"),
    ("other_straits_transits", "INT"),
    ("other_straits_traffic_share_pct", "FLOAT"),
    ("avg_chokepoint_transit_min", "FLOAT"),
    ("cape_of_good_hope_avg_transit_min", "FLOAT"),
    ("other_straits_avg_transit_min", "FLOAT"),
    ("hormuz_bab_avg_transit_min", "FLOAT"),
    ("portwatch_cape_tankers", "INT"),
    ("portwatch_other_straits_tankers", "INT"),
]


def upgrade() -> None:
    for col_name, col_type in NEW_COLUMNS:
        op.execute(
            f"ALTER TABLE oil_market_snapshots ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
        )


def downgrade() -> None:
    for col_name, _ in reversed(NEW_COLUMNS):
        op.execute(
            f"ALTER TABLE oil_market_snapshots DROP COLUMN IF EXISTS {col_name}"
        )
