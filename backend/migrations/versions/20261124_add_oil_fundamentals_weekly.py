"""Add oil_fundamentals_weekly table (CFTC COT + EIA weekly storage) and extend oil_macro_daily with demand/curve/hurricane columns.

Revision ID: add_oil_fundamentals_weekly
Revises: add_oil_macro_daily
Create Date: 2026-11-24 23:05:00.000000
"""

from typing import Sequence, Union
from alembic import op


revision: str = "add_oil_fundamentals_weekly"
down_revision: Union[str, None] = "add_oil_macro_daily"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE oil_macro_daily
            ADD COLUMN IF NOT EXISTS copper_close DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS china_etf_close DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS natgas_close DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS us10y_yield DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS product_tanker_close DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS uso_close DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS usl_close DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS curve_backwardation_proxy DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS gulf_hurricane_risk DOUBLE PRECISION;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS oil_fundamentals_weekly (
            report_date DATE PRIMARY KEY,
            cftc_mm_long INTEGER,
            cftc_mm_short INTEGER,
            cftc_mm_net INTEGER,
            cftc_open_interest INTEGER,
            cftc_mm_net_pct_oi DOUBLE PRECISION,
            eia_crude_stocks_kbbl DOUBLE PRECISION,
            eia_crude_stocks_chg_kbbl DOUBLE PRECISION,
            eia_cushing_stocks_kbbl DOUBLE PRECISION,
            eia_spr_stocks_kbbl DOUBLE PRECISION,
            eia_refinery_util_pct DOUBLE PRECISION,
            fetched_at TIMESTAMPTZ DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS oil_fundamentals_weekly;")
    op.execute(
        """
        ALTER TABLE oil_macro_daily
            DROP COLUMN IF EXISTS copper_close,
            DROP COLUMN IF EXISTS china_etf_close,
            DROP COLUMN IF EXISTS natgas_close,
            DROP COLUMN IF EXISTS us10y_yield,
            DROP COLUMN IF EXISTS product_tanker_close,
            DROP COLUMN IF EXISTS uso_close,
            DROP COLUMN IF EXISTS usl_close,
            DROP COLUMN IF EXISTS curve_backwardation_proxy,
            DROP COLUMN IF EXISTS gulf_hurricane_risk;
        """
    )
