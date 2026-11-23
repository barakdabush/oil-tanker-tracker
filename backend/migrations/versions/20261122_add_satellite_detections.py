"""Add satellite_scenes and satellite_detections tables for open-source SAR vessel tracking.

Revision ID: add_satellite_detections
Revises: extend_chokepoint_gate_lines
Create Date: 2026-11-22 12:30:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_satellite_detections"
down_revision: Union[str, None] = "extend_chokepoint_gate_lines"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "satellite_scenes",
        sa.Column("scene_id", sa.String(length=128), primary_key=True),
        sa.Column("satellite", sa.String(length=16), nullable=False),
        sa.Column("chokepoint_name", sa.String(length=100), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("footprint_geojson", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="cataloged"),
        sa.Column("detections_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dark_detections_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_satellite_scenes_chokepoint_name", "satellite_scenes", ["chokepoint_name"])
    op.create_index("ix_satellite_scenes_acquired_at", "satellite_scenes", ["acquired_at"])

    op.create_table(
        "satellite_detections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("scene_id", sa.String(length=128), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("chokepoint_name", sa.String(length=100), nullable=True),
        sa.Column("estimated_length_m", sa.Float(), nullable=True),
        sa.Column("radar_strength_db", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("is_dark", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "matched_mmsi",
            sa.BigInteger(),
            sa.ForeignKey("vessels.mmsi", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("matched_vessel_name", sa.String(length=255), nullable=True),
        sa.Column("match_distance_km", sa.Float(), nullable=True),
        sa.Column("detection_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("dedup_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_satellite_detections_source", "satellite_detections", ["source"])
    op.create_index("ix_satellite_detections_scene_id", "satellite_detections", ["scene_id"])
    op.create_index("ix_satellite_detections_detected_at", "satellite_detections", ["detected_at"])
    op.create_index("ix_satellite_detections_chokepoint_name", "satellite_detections", ["chokepoint_name"])
    op.create_index("ix_satellite_detections_is_dark", "satellite_detections", ["is_dark"])
    op.create_index("ix_satellite_detections_matched_mmsi", "satellite_detections", ["matched_mmsi"])
    op.create_index("idx_sat_det_time_dark", "satellite_detections", ["detected_at", "is_dark"])
    op.create_index("idx_sat_det_lat_lon", "satellite_detections", ["latitude", "longitude"])


def downgrade() -> None:
    op.drop_table("satellite_detections")
    op.drop_table("satellite_scenes")
