"""FastAPI application entrypoint with lifespan management."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine
from app.api import vessels, ports, alerts, analytics, chokepoints

# Ensure all models are registered with Base.metadata
import app.models  # noqa: F401

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: start background services."""
    logger.info("🚢 Oil Tanker Monitor starting up...")

    # Start background services
    # WebSocket and AIS ingestion moved to websocket_app.py
    logger.info("🟢 API server background services started")

    yield

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
