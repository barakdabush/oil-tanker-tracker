"""Initial base migration for existing tables.

Revision ID: base_initial
Revises: 
Create Date: 2026-04-06 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import geoalchemy2

# revision identifiers, used by Alembic.
revision: str = 'base_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # This reflects the tables already in 01_seed_init.sql and 03_analytics_tables.sql
    # Since they ALREADY exist, we use Alembic stamp to mark it as current.
    pass

def downgrade() -> None:
    pass
