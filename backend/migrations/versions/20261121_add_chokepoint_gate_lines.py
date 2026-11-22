"""Add gate_line geometry to chokepoints and detection_method to chokepoint_transits.

Enables hybrid chokepoint transit detection: vessels that go dark (turn off AIS)
or have sparse pings while transiting a chokepoint are detected when consecutive
position segments cross the chokepoint's gate line.

Revision ID: add_chokepoint_gate_lines
Revises: add_ai_market_briefings
Create Date: 2026-11-21 14:20:00.000000
"""

from typing import Sequence, Union
from alembic import op


revision: str = "add_chokepoint_gate_lines"
down_revision: Union[str, None] = "add_ai_market_briefings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Gate lines (lon, lat) spanning each strait and extending inland across adjacent
# land barriers so that straight-line chords between pre-gap and post-gap AIS
# positions reliably intersect the gate even around bends (e.g. Musandam Peninsula).
GATE_LINES_WKT = {
    "Strait of Hormuz": "LINESTRING(56.45 27.35, 56.35 26.40, 55.90 24.50)",
    "Strait of Malacca": "LINESTRING(101.00 0.50, 102.80 3.00)",
    "Suez Canal": "LINESTRING(31.50 30.55, 33.30 30.55)",
    "Bab el-Mandeb": "LINESTRING(42.40 12.20, 44.10 13.30)",
    "Turkish Straits (Bosporus)": "LINESTRING(28.40 41.12, 29.80 41.12)",
    "Danish Straits": "LINESTRING(9.80 55.40, 13.50 55.80)",
    "Cape of Good Hope": "LINESTRING(18.50 -33.80, 18.50 -37.50)",
    "Panama Canal": "LINESTRING(-80.30 8.90, -79.20 9.35)",
}


def upgrade() -> None:
    op.execute(
        "ALTER TABLE chokepoints "
        "ADD COLUMN IF NOT EXISTS gate_line geometry(LineString, 4326)"
    )
    op.execute(
        "ALTER TABLE chokepoint_transits "
        "ADD COLUMN IF NOT EXISTS detection_method VARCHAR(32) DEFAULT 'polygon'"
    )

    for name, wkt in GATE_LINES_WKT.items():
        safe_name = name.replace("'", "''")
        op.execute(
            f"UPDATE chokepoints "
            f"SET gate_line = ST_GeomFromText('{wkt}', 4326) "
            f"WHERE name = '{safe_name}' AND gate_line IS NULL"
        )


def downgrade() -> None:
    op.execute("ALTER TABLE chokepoint_transits DROP COLUMN IF EXISTS detection_method")
    op.execute("ALTER TABLE chokepoints DROP COLUMN IF EXISTS gate_line")
