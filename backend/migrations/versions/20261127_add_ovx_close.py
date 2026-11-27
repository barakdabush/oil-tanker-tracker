"""Add ovx_close (CBOE Crude Oil Volatility Index ^OVX) to oil_macro_daily.

Revision ID: add_ovx_close
Revises: add_oil_fundamentals_weekly
Create Date: 2026-11-27
"""

from alembic import op
import sqlalchemy as sa

revision = "add_ovx_close"
down_revision = "add_oil_fundamentals_weekly"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE oil_macro_daily ADD COLUMN IF NOT EXISTS ovx_close DOUBLE PRECISION")


def downgrade() -> None:
    op.drop_column("oil_macro_daily", "ovx_close")
