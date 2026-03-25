"""
Background worker to run periodic detection jobs separately from the main FastAPI app.
Run using: python worker.py

Running these CPU-heavy jobs in a separate process prevents them from blocking
the async event loop of the FastAPI server (which causes WebSocket disconnects).
"""
import asyncio
import logging
import sys
import os

# Ensure the backend directory is in the python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.services.cargo_detector import CargoDetector
from app.services.dark_fleet_detector import DarkFleetDetector
from app.services.sts_detector import STSDetector
from app.services.chokepoint_monitor import ChokepointMonitor
from app.database import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("worker")

async def run_workers():
    logger.info("Starting background anomaly detection workers...")
    
    cargo_detector = CargoDetector()
    dark_fleet_detector = DarkFleetDetector()
    sts_detector = STSDetector()
    chokepoint_monitor = ChokepointMonitor()

    # Create background tasks for the heavy jobs
    detector_task = asyncio.create_task(cargo_detector.run_periodic())
    dark_fleet_task = asyncio.create_task(dark_fleet_detector.run_periodic())
    sts_task = asyncio.create_task(sts_detector.run_periodic())
    chokepoint_task = asyncio.create_task(chokepoint_monitor.run_periodic())

    logger.info("🟢 All detection services started. Awaiting events...")

    try:
        # Wait for all tasks to complete (they loop forever)
        await asyncio.gather(
            detector_task, dark_fleet_task, sts_task, chokepoint_task
        )
    except asyncio.CancelledError:
        logger.info("Worker tasks cancelled.")
    finally:
        await engine.dispose()
        logger.info("Database engine disposed.")

if __name__ == "__main__":
    try:
        asyncio.run(run_workers())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")
