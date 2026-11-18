"""Add vessel operational states, vessel_state_history, port_daily_stats, and market snapshot columns.

Revision ID: add_vessel_states
Revises: add_incidents
Create Date: 2026-11-18 11:32:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_vessel_states"
down_revision: Union[str, None] = "add_incidents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Current state columns on vessels
    op.add_column("vessels", sa.Column("operational_state", sa.String(length=32), nullable=True))
    op.add_column("vessels", sa.Column("state_since", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vessels", sa.Column("current_port_id", sa.Integer(), nullable=True))
    op.add_column("vessels", sa.Column("cargo_status", sa.String(length=16), nullable=True))
    op.add_column("vessels", sa.Column("min_draft", sa.Float(), nullable=True))
    op.add_column("vessels", sa.Column("max_draft", sa.Float(), nullable=True))
    op.create_index("idx_vessels_operational_state", "vessels", ["operational_state"])
    op.create_index("idx_vessels_current_port_id", "vessels", ["current_port_id"])
    op.create_foreign_key(
        "fk_vessels_current_port_id",
        "vessels",
        "ports",
        ["current_port_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 2. Interval-based state transition history
    op.create_table(
        "vessel_state_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mmsi", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("cargo_status", sa.String(length=16), nullable=True),
        sa.Column("port_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_hours", sa.Float(), nullable=True),
        sa.Column("start_lat", sa.Float(), nullable=True),
        sa.Column("start_lon", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["mmsi"], ["vessels.mmsi"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["port_id"], ["ports.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_vsh_mmsi_started", "vessel_state_history", ["mmsi", "started_at"])
    op.create_index("idx_vsh_state_started", "vessel_state_history", ["state", "started_at"])
    op.create_index("idx_vsh_port_started", "vessel_state_history", ["port_id", "started_at"])

    # 3. Daily port congestion & utilization rollup
    op.create_table(
        "port_daily_stats",
        sa.Column("port_id", sa.Integer(), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("berthed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("anchored_waiting_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_wait_hours", sa.Float(), nullable=True),
        sa.Column("utilization_pct", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=True),
        sa.ForeignKeyConstraint(["port_id"], ["ports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("port_id", "stat_date"),
    )

    # 4. New state & port columns on oil_market_snapshots
    op.add_column("oil_market_snapshots", sa.Column("anchored_waiting_count", sa.Integer(), nullable=True))
    op.add_column("oil_market_snapshots", sa.Column("avg_port_wait_hours", sa.Float(), nullable=True))
    op.add_column("oil_market_snapshots", sa.Column("port_congestion_pct", sa.Float(), nullable=True))
    op.add_column("oil_market_snapshots", sa.Column("floating_storage_count", sa.Integer(), nullable=True))
    op.add_column("oil_market_snapshots", sa.Column("in_transit_laden_count", sa.Integer(), nullable=True))
    op.add_column("oil_market_snapshots", sa.Column("in_transit_ballast_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("oil_market_snapshots", "in_transit_ballast_count")
    op.drop_column("oil_market_snapshots", "in_transit_laden_count")
    op.drop_column("oil_market_snapshots", "floating_storage_count")
    op.drop_column("oil_market_snapshots", "port_congestion_pct")
    op.drop_column("oil_market_snapshots", "avg_port_wait_hours")
    op.drop_column("oil_market_snapshots", "anchored_waiting_count")

    op.drop_table("port_daily_stats")

    op.drop_index("idx_vsh_port_started", table_name="vessel_state_history")
    op.drop_index("idx_vsh_state_started", table_name="vessel_state_history")
    op.drop_index("idx_vsh_mmsi_started", table_name="vessel_state_history")
    op.drop_table("vessel_state_history")

    op.drop_constraint("fk_vessels_current_port_id", "vessels", type_="foreignkey")
    op.drop_index("idx_vessels_current_port_id", table_name="vessels")
    op.drop_index("idx_vessels_operational_state", table_name="vessels")
    op.drop_column("vessels", "max_draft")
    op.drop_column("vessels", "min_draft")
    op.drop_column("vessels", "cargo_status")
    op.drop_column("vessels", "current_port_id")
    op.drop_column("vessels", "state_since")
    op.drop_column("vessels", "operational_state")
