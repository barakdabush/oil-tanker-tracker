"""Database connection, session management, and initialization."""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_size=20, max_overflow=10)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """Dependency that yields an async database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """Initialize database, create tables, enable extensions, create hypertables."""
    for attempt in range(10):
        try:
            async with engine.begin() as conn:
                # Enable extensions
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"))
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis CASCADE;"))

                # Create all tables
                await conn.run_sync(Base.metadata.create_all)

                # Create hypertables (idempotent)
                try:
                    await conn.execute(text("""
                        SELECT create_hypertable('vessel_positions', 'time',
                            chunk_time_interval => INTERVAL '7 days',
                            if_not_exists => TRUE,
                            migrate_data => TRUE
                        );
                    """))
                    logger.info("Hypertable 'vessel_positions' ready")
                except Exception as e:
                    logger.warning(f"Hypertable vessel_positions: {e}")

                try:
                    await conn.execute(text("""
                        SELECT create_hypertable('chokepoint_transits', 'time',
                            chunk_time_interval => INTERVAL '7 days',
                            if_not_exists => TRUE,
                            migrate_data => TRUE
                        );
                    """))
                    logger.info("Hypertable 'chokepoint_transits' ready")
                except Exception as e:
                    logger.warning(f"Hypertable chokepoint_transits: {e}")

                # Enable compression on vessel_positions after 30 days
                try:
                    await conn.execute(text("""
                        ALTER TABLE vessel_positions SET (
                            timescaledb.compress,
                            timescaledb.compress_segmentby = 'mmsi',
                            timescaledb.compress_orderby = 'time DESC'
                        );
                    """))
                    await conn.execute(text("""
                        SELECT add_compression_policy('vessel_positions', INTERVAL '30 days', if_not_exists => TRUE);
                    """))
                    logger.info("Compression policy set for vessel_positions")
                except Exception as e:
                    logger.warning(f"Compression policy: {e}")

            logger.info("Database initialized successfully")
            break
        except Exception as e:
            if attempt < 9:
                logger.warning(f"Database connection refused, retrying in 2s... ({e})")
                await asyncio.sleep(2)
            else:
                logger.error("Failed to connect to the database after 10 attempts.")
                raise
