"""Extend inland gate lines for Hormuz, Suez, and Bab el-Mandeb.

Extends the inland endpoints of the gate transects across solid land barriers
(UAE/Oman desert, Egypt/Sinai desert, Ethiopia/Yemen interior) so straight-line
chords between longer dark-AIS positions (e.g. Qatar/Abu Dhabi <-> Muscat/Sohar,
Alexandria <-> Red Sea, Jeddah <-> Aden) reliably intersect the gate without
introducing false positives from open-sea traffic.

Revision ID: extend_chokepoint_gate_lines
Revises: add_chokepoint_gate_lines
Create Date: 2026-11-22 06:50:00.000000
"""

from typing import Sequence, Union
from alembic import op


revision: str = "extend_chokepoint_gate_lines"
down_revision: Union[str, None] = "add_chokepoint_gate_lines"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPDATED_GATE_LINES_WKT = {
    "Strait of Hormuz": "LINESTRING(56.50 28.50, 56.35 26.40, 55.50 21.50)",
    "Suez Canal": "LINESTRING(29.00 30.55, 36.00 30.55)",
    "Bab el-Mandeb": "LINESTRING(40.00 10.50, 43.30 12.65, 46.50 15.00)",
}

PREVIOUS_GATE_LINES_WKT = {
    "Strait of Hormuz": "LINESTRING(56.45 27.35, 56.35 26.40, 55.90 24.50)",
    "Suez Canal": "LINESTRING(31.50 30.55, 33.30 30.55)",
    "Bab el-Mandeb": "LINESTRING(42.40 12.20, 44.10 13.30)",
}


def upgrade() -> None:
    for name, wkt in UPDATED_GATE_LINES_WKT.items():
        safe_name = name.replace("'", "''")
        op.execute(
            f"UPDATE chokepoints "
            f"SET gate_line = ST_GeomFromText('{wkt}', 4326) "
            f"WHERE name = '{safe_name}'"
        )


def downgrade() -> None:
    for name, wkt in PREVIOUS_GATE_LINES_WKT.items():
        safe_name = name.replace("'", "''")
        op.execute(
            f"UPDATE chokepoints "
            f"SET gate_line = ST_GeomFromText('{wkt}', 4326) "
            f"WHERE name = '{safe_name}'"
        )
