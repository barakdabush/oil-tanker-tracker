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
from app.services.global_oil_feature_builder import GlobalOilFeatureBuilder
from app.services.oil_price_fetcher import OilPriceFetcher
from app.services.macro_market_fetcher import MacroMarketFetcher
from app.services.portwatch_fetcher import PortWatchFetcher
from app.services.ofac_fetcher import OfacSanctionsFetcher
from app.services.vessel_state_classifier import VesselStateClassifier
from app.services.strait_risk_fetcher import StraitRiskFetcher
from app.services.news_fetcher import NewsFetcher
from app.services.news_llm_enricher import NewsLLMEnricher
from app.services.news_impact_analyzer import NewsImpactAnalyzer
from app.services.sentinel1_sar_detector import Sentinel1SARDetector
from app.services.gfw_sar_fetcher import GfwSarFetcher
from app.services.db_log_handler import setup_db_logging
from app.database import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("worker")

async def run_workers():
    logger.info("Starting background anomaly detection workers...")
    log_flush_task = setup_db_logging("scheduler")
    
    cargo_detector = CargoDetector()
    dark_fleet_detector = DarkFleetDetector()
    sts_detector = STSDetector()
    chokepoint_monitor = ChokepointMonitor()
    global_oil_feature_builder = GlobalOilFeatureBuilder()
    oil_price_fetcher = OilPriceFetcher()
    macro_market_fetcher = MacroMarketFetcher()
    portwatch_fetcher = PortWatchFetcher()
    ofac_fetcher = OfacSanctionsFetcher()
    vessel_state_classifier = VesselStateClassifier()
    strait_risk_fetcher = StraitRiskFetcher()
    news_fetcher = NewsFetcher()
    news_llm_enricher = NewsLLMEnricher()
    news_impact_analyzer = NewsImpactAnalyzer()
    sentinel1_sar_detector = Sentinel1SARDetector()
    gfw_sar_fetcher = GfwSarFetcher()

    # Create background tasks for the heavy jobs
    detector_task = asyncio.create_task(cargo_detector.run_periodic())
    dark_fleet_task = asyncio.create_task(dark_fleet_detector.run_periodic())
    sts_task = asyncio.create_task(sts_detector.run_periodic())
    chokepoint_task = asyncio.create_task(chokepoint_monitor.run_periodic())
    feature_task = asyncio.create_task(global_oil_feature_builder.run_periodic())
    oil_price_task = asyncio.create_task(oil_price_fetcher.run_periodic())
    macro_market_task = asyncio.create_task(macro_market_fetcher.run_periodic())
    portwatch_task = asyncio.create_task(portwatch_fetcher.run_periodic())
    ofac_task = asyncio.create_task(ofac_fetcher.run_periodic())
    state_task = asyncio.create_task(vessel_state_classifier.run_periodic())
    strait_risk_task = asyncio.create_task(strait_risk_fetcher.run_periodic())
    news_fetch_task = asyncio.create_task(news_fetcher.run_periodic())
    news_llm_task = asyncio.create_task(news_llm_enricher.run_periodic())
    news_impact_task = asyncio.create_task(news_impact_analyzer.run_periodic())
    sentinel1_task = asyncio.create_task(sentinel1_sar_detector.run_periodic())
    gfw_sar_task = asyncio.create_task(gfw_sar_fetcher.run_periodic())

    logger.info("🟢 All detection services started. Awaiting events...")

    try:
        # Wait for all tasks to complete (they loop forever)
        await asyncio.gather(
            detector_task, dark_fleet_task, sts_task, chokepoint_task,
            feature_task, oil_price_task, macro_market_task, portwatch_task, ofac_task, log_flush_task,
            state_task, strait_risk_task, news_fetch_task, news_llm_task, news_impact_task,
            sentinel1_task, gfw_sar_task,
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
