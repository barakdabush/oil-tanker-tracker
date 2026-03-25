"""FastAPI application entrypoint with lifespan management."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, init_db
from app.api import vessels, ports, alerts, analytics, chokepoints
from app.seed.ports import seed_ports
from app.seed.chokepoints import seed_chokepoints

# Ensure all models are registered with Base.metadata before init_db runs
import app.models  # noqa: F401

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_seed_data():
    """Seed ports and chokepoints data."""
    try:
        await seed_ports()
        await seed_chokepoints()
        logger.info("Seed data loaded successfully")
    except Exception as e:
        logger.warning(f"Seed data: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: init DB, seed data, start background services."""
    logger.info("🚢 Oil Tanker Monitor starting up...")

    # Initialize database
    await init_db()
    await run_seed_data()

    # Start background services
    # WebSocket and AIS ingestion moved to websocket_app.py
    logger.info("🟢 API server background services started")

    yield

    try:
        pass
    except asyncio.CancelledError:
        pass

    await engine.dispose()
    logger.info("👋 Shutdown complete")


app = FastAPI(
    title="Oil Tanker Monitor API",
    description="Worldwide oil tanker tracking, cargo detection, and supply chain analytics",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(vessels.router, prefix="/api/vessels", tags=["Vessels"])
app.include_router(ports.router, prefix="/api/ports", tags=["Ports"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["Alerts"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(chokepoints.router, prefix="/api/chokepoints", tags=["Chokepoints"])


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "oil-tanker-monitor"}
