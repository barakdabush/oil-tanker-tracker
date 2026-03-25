"""Seed world oil ports and terminals data."""

import logging
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.database import async_session
from app.models import Port

logger = logging.getLogger(__name__)

# Major oil ports and terminals worldwide
OIL_PORTS = [
    # Middle East
    {"name": "Ras Tanura", "country": "Saudi Arabia", "region": "Persian Gulf", "port_type": "oil_terminal", "latitude": 26.644, "longitude": 50.161},
    {"name": "Ju'aymah", "country": "Saudi Arabia", "region": "Persian Gulf", "port_type": "oil_terminal", "latitude": 26.901, "longitude": 49.880},
    {"name": "Yanbu", "country": "Saudi Arabia", "region": "Red Sea", "port_type": "oil_terminal", "latitude": 24.089, "longitude": 38.064},
    {"name": "Kharg Island", "country": "Iran", "region": "Persian Gulf", "port_type": "oil_terminal", "latitude": 29.233, "longitude": 50.325},
    {"name": "Bandar Abbas", "country": "Iran", "region": "Persian Gulf", "port_type": "oil_terminal", "latitude": 27.185, "longitude": 56.268},
    {"name": "Basra Oil Terminal", "country": "Iraq", "region": "Persian Gulf", "port_type": "oil_terminal", "latitude": 29.683, "longitude": 48.800},
    {"name": "Mina Al Ahmadi", "country": "Kuwait", "region": "Persian Gulf", "port_type": "oil_terminal", "latitude": 29.077, "longitude": 48.148},
    {"name": "Jebel Ali", "country": "UAE", "region": "Persian Gulf", "port_type": "oil_terminal", "latitude": 25.002, "longitude": 55.058},
    {"name": "Fujairah", "country": "UAE", "region": "Gulf of Oman", "port_type": "oil_terminal", "latitude": 25.128, "longitude": 56.336},
    {"name": "Muscat", "country": "Oman", "region": "Arabian Sea", "port_type": "oil_terminal", "latitude": 23.625, "longitude": 58.555},
    {"name": "Sohar", "country": "Oman", "region": "Gulf of Oman", "port_type": "oil_terminal", "latitude": 24.367, "longitude": 56.748},

    # Africa
    {"name": "Bonny", "country": "Nigeria", "region": "West Africa", "port_type": "oil_terminal", "latitude": 4.433, "longitude": 7.167},
    {"name": "Forcados", "country": "Nigeria", "region": "West Africa", "port_type": "oil_terminal", "latitude": 5.350, "longitude": 5.350},
    {"name": "Escravos", "country": "Nigeria", "region": "West Africa", "port_type": "oil_terminal", "latitude": 5.617, "longitude": 5.167},
    {"name": "Luanda", "country": "Angola", "region": "West Africa", "port_type": "oil_terminal", "latitude": -8.800, "longitude": 13.233},
    {"name": "Sidi Kerir", "country": "Egypt", "region": "Mediterranean", "port_type": "oil_terminal", "latitude": 31.133, "longitude": 29.683},
    {"name": "Ain Sukhna", "country": "Egypt", "region": "Red Sea", "port_type": "oil_terminal", "latitude": 29.600, "longitude": 32.350},
    {"name": "Skikda", "country": "Algeria", "region": "Mediterranean", "port_type": "oil_terminal", "latitude": 36.883, "longitude": 6.917},

    # Russia & FSU
    {"name": "Primorsk", "country": "Russia", "region": "Baltic Sea", "port_type": "oil_terminal", "latitude": 60.354, "longitude": 28.683},
    {"name": "Ust-Luga", "country": "Russia", "region": "Baltic Sea", "port_type": "oil_terminal", "latitude": 59.678, "longitude": 28.421},
    {"name": "Novorossiysk", "country": "Russia", "region": "Black Sea", "port_type": "oil_terminal", "latitude": 44.724, "longitude": 37.768},
    {"name": "Kozmino", "country": "Russia", "region": "Pacific", "port_type": "oil_terminal", "latitude": 42.733, "longitude": 133.117},
    {"name": "Murmansk", "country": "Russia", "region": "Arctic", "port_type": "oil_terminal", "latitude": 68.967, "longitude": 33.083},
    {"name": "Aktau", "country": "Kazakhstan", "region": "Caspian Sea", "port_type": "oil_terminal", "latitude": 43.650, "longitude": 51.150},

    # Europe
    {"name": "Rotterdam", "country": "Netherlands", "region": "North Sea", "port_type": "oil_terminal", "latitude": 51.900, "longitude": 4.500},
    {"name": "Antwerp", "country": "Belgium", "region": "North Sea", "port_type": "oil_terminal", "latitude": 51.317, "longitude": 4.283},
    {"name": "Trieste", "country": "Italy", "region": "Mediterranean", "port_type": "oil_terminal", "latitude": 45.650, "longitude": 13.750},
    {"name": "Augusta", "country": "Italy", "region": "Mediterranean", "port_type": "oil_terminal", "latitude": 37.233, "longitude": 15.200},
    {"name": "Wilhelmshaven", "country": "Germany", "region": "North Sea", "port_type": "oil_terminal", "latitude": 53.517, "longitude": 8.150},
    {"name": "Mongstad", "country": "Norway", "region": "North Sea", "port_type": "oil_terminal", "latitude": 60.817, "longitude": 5.033},
    {"name": "Sullom Voe", "country": "UK", "region": "North Sea", "port_type": "oil_terminal", "latitude": 60.467, "longitude": -1.283},
    {"name": "Milford Haven", "country": "UK", "region": "Atlantic", "port_type": "oil_terminal", "latitude": 51.700, "longitude": -5.033},
    {"name": "Algeciras", "country": "Spain", "region": "Mediterranean", "port_type": "oil_terminal", "latitude": 36.133, "longitude": -5.433},
    {"name": "Sines", "country": "Portugal", "region": "Atlantic", "port_type": "oil_terminal", "latitude": 37.950, "longitude": -8.867},

    # Americas
    {"name": "Houston", "country": "USA", "region": "Gulf of Mexico", "port_type": "oil_terminal", "latitude": 29.733, "longitude": -95.267},
    {"name": "Louisiana Offshore Oil Port", "country": "USA", "region": "Gulf of Mexico", "port_type": "oil_terminal", "latitude": 28.883, "longitude": -90.017},
    {"name": "Corpus Christi", "country": "USA", "region": "Gulf of Mexico", "port_type": "oil_terminal", "latitude": 27.817, "longitude": -97.400},
    {"name": "Port Arthur", "country": "USA", "region": "Gulf of Mexico", "port_type": "oil_terminal", "latitude": 29.867, "longitude": -93.933},
    {"name": "Long Beach", "country": "USA", "region": "Pacific", "port_type": "oil_terminal", "latitude": 33.750, "longitude": -118.200},
    {"name": "Valdez", "country": "USA", "region": "Pacific", "port_type": "oil_terminal", "latitude": 61.117, "longitude": -146.350},
    {"name": "Jose", "country": "Venezuela", "region": "Caribbean", "port_type": "oil_terminal", "latitude": 10.183, "longitude": -64.917},
    {"name": "Maracaibo", "country": "Venezuela", "region": "Caribbean", "port_type": "oil_terminal", "latitude": 10.617, "longitude": -71.617},
    {"name": "Angra dos Reis", "country": "Brazil", "region": "South Atlantic", "port_type": "oil_terminal", "latitude": -23.000, "longitude": -44.317},
    {"name": "São Sebastião", "country": "Brazil", "region": "South Atlantic", "port_type": "oil_terminal", "latitude": -23.800, "longitude": -45.400},
    {"name": "Cayo Arcas", "country": "Mexico", "region": "Gulf of Mexico", "port_type": "oil_terminal", "latitude": 20.217, "longitude": -91.983},

    # Asia Pacific
    {"name": "Singapore", "country": "Singapore", "region": "Southeast Asia", "port_type": "oil_terminal", "latitude": 1.267, "longitude": 103.833},
    {"name": "Ningbo-Zhoushan", "country": "China", "region": "East China Sea", "port_type": "oil_terminal", "latitude": 29.933, "longitude": 121.850},
    {"name": "Qingdao", "country": "China", "region": "Yellow Sea", "port_type": "oil_terminal", "latitude": 36.067, "longitude": 120.317},
    {"name": "Dalian", "country": "China", "region": "Yellow Sea", "port_type": "oil_terminal", "latitude": 38.917, "longitude": 121.650},
    {"name": "Ulsan", "country": "South Korea", "region": "East Sea", "port_type": "oil_terminal", "latitude": 35.500, "longitude": 129.383},
    {"name": "Yeosu", "country": "South Korea", "region": "East China Sea", "port_type": "oil_terminal", "latitude": 34.733, "longitude": 127.750},
    {"name": "Chiba", "country": "Japan", "region": "Pacific", "port_type": "oil_terminal", "latitude": 35.567, "longitude": 140.067},
    {"name": "Yokohama", "country": "Japan", "region": "Pacific", "port_type": "oil_terminal", "latitude": 35.450, "longitude": 139.650},
    {"name": "Mumbai (JNPT)", "country": "India", "region": "Arabian Sea", "port_type": "oil_terminal", "latitude": 18.950, "longitude": 72.950},
    {"name": "Vadinar", "country": "India", "region": "Arabian Sea", "port_type": "oil_terminal", "latitude": 22.433, "longitude": 69.700},
    {"name": "Paradip", "country": "India", "region": "Bay of Bengal", "port_type": "oil_terminal", "latitude": 20.267, "longitude": 86.667},
]


async def seed_ports():
    """Insert oil port data into the database (idempotent)."""
    async with async_session() as session:
        for port_data in OIL_PORTS:
            lat = port_data["latitude"]
            lon = port_data["longitude"]
            stmt = pg_insert(Port).values(
                **port_data,
                position=text(f"ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography"),
            ).on_conflict_do_nothing()
            await session.execute(stmt)

        await session.commit()
        logger.info(f"Seeded {len(OIL_PORTS)} oil ports")
