"""Oil price fetcher service.

Fetches daily Brent and WTI spot prices from the EIA Open Data API
and upserts them into the oil_prices table.
Backfills the last 30 days on first run, then fetches latest daily.
Runs daily at ~00:30 UTC via the worker.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta, date

import httpx
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import async_session
from app.models.global_oil_features import OilPrice

logger = logging.getLogger(__name__)

# EIA API v2 endpoints for petroleum spot prices
EIA_BASE_URL = "https://api.eia.gov/v2/petroleum/pri/spt/data/"

# Series IDs
# Brent: Europe Brent Spot Price FOB (Dollars per Barrel)
# WTI:   Cushing, OK WTI Spot Price FOB (Dollars per Barrel)
BRENT_PRODUCT = "EPCBRENT"
WTI_PRODUCT = "EPCWTI"

RUN_INTERVAL_SECONDS = 24 * 60 * 60
BACKFILL_DAYS = 30


class OilPriceFetcher:
    """Fetches daily oil prices from EIA API."""

    async def run_periodic(self):
        """Run oil price fetching once daily."""
        # Delay a bit (30 min offset from feature aggregator)
        await asyncio.sleep(30)
        while True:
            try:
                await self.fetch_prices()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Oil price fetch error: {e}")
                await asyncio.sleep(5 * 60)
                continue
            await asyncio.sleep(RUN_INTERVAL_SECONDS)

    async def fetch_prices(self, days: int | None = None):
        """Fetch and upsert oil prices from EIA.

        On first run (empty table), backfills the last 30 days.
        On subsequent runs, fetches only the most recent prices.
        """
        start_time = time.time()

        async with async_session() as session:
            # Check if we need backfill
            row_count = (await session.execute(
                select(func.count()).select_from(OilPrice)
            )).scalar() or 0

        if days is None:
            days = 30  # Always look back 30 days to ensure no gaps during long EIA lags

        start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if not settings.EIA_API_KEY or settings.EIA_API_KEY == "your_eia_api_key_here":
            logger.warning("EIA_API_KEY not configured — skipping oil price fetch")
            return

        logger.info(f"Fetching oil prices from EIA for range: {start_date} to {end_date}")

        # Fetch both Brent and WTI in one request
        params = {
            "api_key": settings.EIA_API_KEY,
            "frequency": "daily",
            "data[0]": "value",
            "facets[product][]": [BRENT_PRODUCT, WTI_PRODUCT],
            "start": start_date,
            "end": end_date,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": 5000,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(EIA_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        records = data.get("response", {}).get("data", [])
        if not records:
            logger.info(f"No oil price records returned from EIA for {start_date} to {end_date}")
            return

        # Group prices by date
        prices_by_date: dict[str, dict[str, float | None]] = {}
        for record in records:
            period = record.get("period")
            product = record.get("product")
            value = record.get("value")

            if not period or value is None:
                continue

            if period not in prices_by_date:
                prices_by_date[period] = {"brent": None, "wti": None}

            try:
                price = float(value)
            except (ValueError, TypeError):
                continue

            if product == BRENT_PRODUCT:
                prices_by_date[period]["brent"] = price
            elif product == WTI_PRODUCT:
                prices_by_date[period]["wti"] = price

        # Upsert into database
        upserted = 0
        async with async_session() as session:
            for date_str, prices in prices_by_date.items():
                try:
                    price_date = date.fromisoformat(date_str)
                except ValueError:
                    continue

                values = {
                    "price_date": price_date,
                    "brent_close_usd": prices["brent"],
                    "wti_close_usd": prices["wti"],
                }
                stmt = pg_insert(OilPrice).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["price_date"],
                    set_={
                        "brent_close_usd": stmt.excluded.brent_close_usd,
                        "wti_close_usd": stmt.excluded.wti_close_usd,
                        "fetched_at": func.now(),
                    },
                )
                await session.execute(stmt)
                upserted += 1

            await session.commit()

        duration = time.time() - start_time
        latest_date = max(prices_by_date.keys()) if prices_by_date else "N/A"
        logger.info(
            f"Oil price fetch complete in {duration:.1f}s — "
            f"{upserted} records upserted. Latest data date: {latest_date}"
        )
