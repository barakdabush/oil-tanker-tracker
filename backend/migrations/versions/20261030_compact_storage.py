"""Purge non-tanker data, drop unused spatial column/indexes, and enable fast lossless TimescaleDB compression.

Revision ID: compact_storage
Revises: add_analytics
Create Date: 2026-10-30 12:15:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'compact_storage'
down_revision: Union[str, None] = 'add_analytics'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0. Ensure timescaledb.restoring is off in case PGOPTIONS enabled it
    op.execute("""
        DO $$
        BEGIN
            EXECUTE 'SET LOCAL timescaledb.restoring = off';
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END $$;
    """)

    # 1. Decompress any existing compressed chunks on vessel_positions so we can
    #    delete non-tanker rows and drop the unused position column cleanly.
    op.execute("""
        DO $$
        DECLARE
            chunk_rec RECORD;
        BEGIN
            FOR chunk_rec IN
                SELECT format('%I.%I', chunk_schema, chunk_name) AS chunk_full_name
                FROM timescaledb_information.chunks
                WHERE hypertable_name = 'vessel_positions' AND is_compressed = true
            LOOP
                PERFORM decompress_chunk(chunk_rec.chunk_full_name::regclass, true);
            END LOOP;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Skipping chunk decompression: %', SQLERRM;
        END $$;
    """)

    # 2. Delete all non-tanker records (ship_type IS NULL or not 80-89).
    #    100% of oil tanker records (ship_type BETWEEN 80 AND 89) are preserved.
    op.execute("""
        DELETE FROM ais_gaps
        WHERE mmsi IN (
            SELECT mmsi FROM vessels
            WHERE ship_type IS NULL OR ship_type NOT BETWEEN 80 AND 89
        )
    """)

    op.execute("""
        DELETE FROM sts_events
        WHERE vessel_a_mmsi IN (
            SELECT mmsi FROM vessels
            WHERE ship_type IS NULL OR ship_type NOT BETWEEN 80 AND 89
        )
        OR vessel_b_mmsi IN (
            SELECT mmsi FROM vessels
            WHERE ship_type IS NULL OR ship_type NOT BETWEEN 80 AND 89
        )
    """)

    op.execute("""
        DELETE FROM chokepoint_transits
        WHERE mmsi IN (
            SELECT mmsi FROM vessels
            WHERE ship_type IS NULL OR ship_type NOT BETWEEN 80 AND 89
        )
    """)

    op.execute("""
        DELETE FROM cargo_events
        WHERE mmsi IN (
            SELECT mmsi FROM vessels
            WHERE ship_type IS NULL OR ship_type NOT BETWEEN 80 AND 89
        )
    """)

    op.execute("""
        DELETE FROM voyage_legs
        WHERE mmsi IN (
            SELECT mmsi FROM vessels
            WHERE ship_type IS NULL OR ship_type NOT BETWEEN 80 AND 89
        )
    """)

    op.execute("""
        DELETE FROM vessel_positions
        WHERE mmsi IN (
            SELECT mmsi FROM vessels
            WHERE ship_type IS NULL OR ship_type NOT BETWEEN 80 AND 89
        )
    """)

    op.execute("""
        DELETE FROM vessels
        WHERE ship_type IS NULL OR ship_type NOT BETWEEN 80 AND 89
    """)

    # 3. Drop unused GiST spatial index and redundant time index on vessel_positions
    op.execute("DROP INDEX IF EXISTS idx_vessel_positions_position")
    op.execute("DROP INDEX IF EXISTS vessel_positions_time_idx")

    # 4. Drop unused position Geography column on vessel_positions
    #    (Fallback to setting NULL if TimescaleDB compression catalog blocks DROP COLUMN)
    op.execute("""
        DO $$
        BEGIN
            ALTER TABLE public.vessel_positions DROP COLUMN IF EXISTS "position";
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Could not drop position column directly (%), clearing values to NULL', SQLERRM;
            UPDATE public.vessel_positions SET "position" = NULL WHERE "position" IS NOT NULL;
        END $$;
    """)

    # 5. Configure lossless TimescaleDB compression after 3 days (no retention policy -> zero data loss)
    op.execute("""
        DO $$
        DECLARE
            chunk_rec RECORD;
        BEGIN
            PERFORM set_chunk_time_interval('vessel_positions', INTERVAL '1 day');
            PERFORM remove_compression_policy('vessel_positions', if_exists => true);
            PERFORM add_compression_policy('vessel_positions', INTERVAL '3 days', if_not_exists => true);

            FOR chunk_rec IN
                SELECT show_chunks('vessel_positions', older_than => INTERVAL '3 days') AS chunk_reg
            LOOP
                PERFORM compress_chunk(chunk_rec.chunk_reg, if_not_compressed => true);
            END LOOP;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Skipping TimescaleDB compression policy update: %', SQLERRM;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE public.vessel_positions
        ADD COLUMN IF NOT EXISTS "position" public.geography(Point, 4326)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_vessel_positions_position
        ON public.vessel_positions USING gist ("position")
    """)

