"""Standalone incident detector worker.

Runs IncidentDetector in its own container, isolated from the main FastAPI
server and the heavy scheduler jobs, and depends only on PostgreSQL.
Run using: python incident_worker.py
"""
import asyncio
import logging
import os
import sys

# Ensure the backend directory is in the python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import engine
from app.services.incident_detector import IncidentDetector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("incident_worker")


async def run_incident_monitor():
    logger.info("Starting isolated incident monitor...")
    detector = IncidentDetector()
    try:
        await detector.run_periodic()
    except asyncio.CancelledError:
        logger.info("Incident monitor cancelled.")
    finally:
        await engine.dispose()
        logger.info("Database engine disposed.")


if __name__ == "__main__":
    try:
        asyncio.run(run_incident_monitor())
    except KeyboardInterrupt:
        logger.info("Incident monitor stopped by user.")
