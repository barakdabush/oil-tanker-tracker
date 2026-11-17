"""Add portwatch_chokepoint_daily and ofac_sanctioned_vessels tables.

Revision ID: add_portwatch_and_ofac
Revises: add_position_source
Create Date: 2026-11-17 06:52:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "add_portwatch_and_ofac"
down_revision: Union[str, None] = "add_position_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portwatch_chokepoint_daily",
        sa.Column("portid", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("portname", sa.String(length=128), nullable=False),
        sa.Column("n_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_tanker", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cargo", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_container", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("capacity_tanker", sa.Float(), nullable=True),
        sa.Column("capacity_total", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=True),
        sa.PrimaryKeyConstraint("portid", "date"),
    )
    op.create_index("idx_portwatch_portname_date", "portwatch_chokepoint_daily", ["portname", "date"])

    op.create_table(
        "ofac_sanctioned_vessels",
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("imo", sa.BigInteger(), nullable=True),
        sa.Column("mmsi", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sdn_type", sa.String(length=32), nullable=True),
        sa.Column("program", sa.String(length=128), nullable=True),
        sa.Column("flag", sa.String(length=64), nullable=True),
        sa.Column("vessel_type", sa.String(length=64), nullable=True),
        sa.Column("remarks", sa.String(length=1000), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=True),
        sa.PrimaryKeyConstraint("uid"),
    )
    op.create_index("ix_ofac_sanctioned_vessels_imo", "ofac_sanctioned_vessels", ["imo"])
    op.create_index("ix_ofac_sanctioned_vessels_mmsi", "ofac_sanctioned_vessels", ["mmsi"])
    op.create_index("ix_ofac_sanctioned_vessels_name", "ofac_sanctioned_vessels", ["name"])


def downgrade() -> None:
    op.drop_index("ix_ofac_sanctioned_vessels_name", table_name="ofac_sanctioned_vessels")
    op.drop_index("ix_ofac_sanctioned_vessels_mmsi", table_name="ofac_sanctioned_vessels")
    op.drop_index("ix_ofac_sanctioned_vessels_imo", table_name="ofac_sanctioned_vessels")
    op.drop_table("ofac_sanctioned_vessels")

    op.drop_index("idx_portwatch_portname_date", table_name="portwatch_chokepoint_daily")
    op.drop_table("portwatch_chokepoint_daily")
