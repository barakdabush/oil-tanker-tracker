"""Seed chokepoint polygon data for maritime chokepoint monitoring."""

import logging
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.database import async_session
from app.models import Chokepoint

logger = logging.getLogger(__name__)

# Chokepoint definitions with approximate polygon boundaries
# Each polygon is defined as a list of (lon, lat) tuples forming a closed ring
CHOKEPOINTS = [
    {
        "name": "Strait of Hormuz",
        "region": "Persian Gulf",
        "avg_daily_oil_flow_mbd": 21.0,
        "congestion_threshold": 30,
        "polygon_wkt": "POLYGON((56.0 26.0, 56.5 26.0, 57.0 26.5, 56.8 27.0, 56.3 27.0, 55.8 26.5, 56.0 26.0))",
    },
    {
        "name": "Strait of Malacca",
        "region": "Southeast Asia",
        "avg_daily_oil_flow_mbd": 16.0,
        "congestion_threshold": 40,
        "polygon_wkt": "POLYGON((99.5 1.0, 100.0 2.0, 101.0 2.5, 103.5 1.3, 104.0 1.0, 103.5 0.8, 100.5 1.5, 99.5 1.0))",
    },
    {
        "name": "Suez Canal",
        "region": "Egypt",
        "avg_daily_oil_flow_mbd": 9.0,
        "congestion_threshold": 15,
        "polygon_wkt": "POLYGON((32.2 29.8, 32.6 29.8, 32.6 31.3, 32.2 31.3, 32.2 29.8))",
    },
    {
        "name": "Bab el-Mandeb",
        "region": "Red Sea",
        "avg_daily_oil_flow_mbd": 9.0,
        "congestion_threshold": 20,
        "polygon_wkt": "POLYGON((42.5 12.2, 43.5 12.2, 43.8 12.8, 43.5 13.2, 42.5 13.2, 42.2 12.8, 42.5 12.2))",
    },
    {
        "name": "Turkish Straits (Bosporus)",
        "region": "Turkey",
        "avg_daily_oil_flow_mbd": 3.5,
        "congestion_threshold": 10,
        "polygon_wkt": "POLYGON((28.9 40.9, 29.2 40.9, 29.2 41.3, 28.9 41.3, 28.9 40.9))",
    },
    {
        "name": "Danish Straits",
        "region": "Scandinavia",
        "avg_daily_oil_flow_mbd": 3.0,
        "congestion_threshold": 15,
        "polygon_wkt": "POLYGON((10.5 54.5, 13.0 54.5, 13.0 56.5, 10.5 56.5, 10.5 54.5))",
    },
    {
        "name": "Cape of Good Hope",
        "region": "South Africa",
        "avg_daily_oil_flow_mbd": 9.0,
        "congestion_threshold": 20,
        "polygon_wkt": "POLYGON((17.5 -35.5, 19.5 -35.5, 19.5 -33.5, 17.5 -33.5, 17.5 -35.5))",
    },
    {
        "name": "Panama Canal",
        "region": "Central America",
        "avg_daily_oil_flow_mbd": 1.0,
        "congestion_threshold": 8,
        "polygon_wkt": "POLYGON((-80.0 8.8, -79.4 8.8, -79.4 9.5, -80.0 9.5, -80.0 8.8))",
    },
]


async def seed_chokepoints():
    """Insert chokepoint data into the database (idempotent)."""
    async with async_session() as session:
        for cp_data in CHOKEPOINTS:
            polygon_wkt = cp_data.pop("polygon_wkt")
            stmt = pg_insert(Chokepoint).values(
                **cp_data,
                boundary_polygon=text(f"ST_GeomFromText('{polygon_wkt}', 4326)"),
            ).on_conflict_do_nothing()
            await session.execute(stmt)

        await session.commit()
        logger.info(f"Seeded {len(CHOKEPOINTS)} chokepoints")
