"""Add source column to vessel_positions hypertable.

Revision ID: add_position_source
Revises: add_vessel_last_source
Create Date: 2026-11-16 11:28:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "add_position_source"
down_revision: Union[str, None] = "add_vessel_last_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Decompress any compressed TimescaleDB chunks on vessel_positions before altering schema
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
    op.add_column("vessel_positions", sa.Column("source", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("vessel_positions", "source")
