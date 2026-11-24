"""Deduplicate adjacent 25-second Sentinel-1 overpass slices and their duplicate detections.

Revision ID: dedup_satellite_overpasses
Revises: add_satellite_detections
Create Date: 2026-11-24 07:30:00.000000
"""

from typing import Sequence, Union
from alembic import op


revision: str = "dedup_satellite_overpasses"
down_revision: Union[str, None] = "add_satellite_detections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Identify redundant processed scenes that have a newer processed scene for the
    # same chokepoint within 15 minutes (adjacent 25s slices of the same orbit pass).
    op.execute(
        """
        WITH redundant_scenes AS (
            SELECT s1.scene_id
            FROM satellite_scenes s1
            WHERE s1.status = 'processed'
              AND EXISTS (
                  SELECT 1
                  FROM satellite_scenes s2
                  WHERE s2.status = 'processed'
                    AND s2.chokepoint_name = s1.chokepoint_name
                    AND s2.scene_id <> s1.scene_id
                    AND s2.acquired_at > s1.acquired_at
                    AND s2.acquired_at - s1.acquired_at <= INTERVAL '15 minutes'
              )
        )
        DELETE FROM satellite_detections
        WHERE scene_id IN (SELECT scene_id FROM redundant_scenes);
        """
    )

    op.execute(
        """
        WITH redundant_scenes AS (
            SELECT s1.scene_id
            FROM satellite_scenes s1
            WHERE s1.status = 'processed'
              AND EXISTS (
                  SELECT 1
                  FROM satellite_scenes s2
                  WHERE s2.status = 'processed'
                    AND s2.chokepoint_name = s1.chokepoint_name
                    AND s2.scene_id <> s1.scene_id
                    AND s2.acquired_at > s1.acquired_at
                    AND s2.acquired_at - s1.acquired_at <= INTERVAL '15 minutes'
              )
        )
        UPDATE satellite_scenes
        SET status = 'merged_into_pass',
            detections_count = 0,
            dark_detections_count = 0
        WHERE scene_id IN (SELECT scene_id FROM redundant_scenes);
        """
    )


def downgrade() -> None:
    # Irreversible data cleanup; no schema changes to revert.
    pass
