"""Add last_source column to vessels table.

Revision ID: add_vessel_last_source
Revises: compact_storage
Create Date: 2026-11-16 11:22:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "add_vessel_last_source"
down_revision: Union[str, None] = "compact_storage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vessels", sa.Column("last_source", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("vessels", "last_source")
