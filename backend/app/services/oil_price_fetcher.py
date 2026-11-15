"""Oil price fetcher service.

Fetches daily Brent (BZ=F) and WTI (CL=F) front-month futures prices from
Yahoo Finance (matching Google Finance) with EIA spot prices as a fallback,
and upserts them into the oil_prices table.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta, date

import httpx
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import async_session
from app.models.global_oil_features import OilPrice

logger = logging.getLogger(__name__)

# Yahoo Finance chart API (front-month futures, matches Google Finance)
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_BRENT_SYMBOL = "BZ=F"
YAHOO_WTI_SYMBOL = "CL=F"

# EIA API v2 fallback endpoint for petroleum spot prices
EIA_BASE_URL = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
BRENT_PRODUCT = "EPCBRENT"
WTI_PRODUCT = "EPCWTI"

RUN_INTERVAL_SECONDS = 4 * 60 * 60
BACKFILL_DAYS = 30


class OilPriceFetcher:
    """Fetches daily oil prices from Yahoo Finance with EIA fallback."""

    async def run_periodic(self):
        """Run oil price fetching periodically every 4 hours."""
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

    async def _fetch_yahoo_symbol(
        self, client: httpx.AsyncClient, symbol: str, range_str: str
    ) -> dict[str, float]:
        """Fetch daily close prices for a Yahoo Finance symbol keyed by YYYY-MM-DD."""
        url = YAHOO_CHART_URL.format(symbol=symbol)
        resp = await client.get(
            url,
            params={"range": range_str, "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()

        result_list = data.get("chart", {}).get("result") or []
        if not result_list:
            return {}

        chart = result_list[0]
        timestamps = chart.get("timestamp") or []
        quotes = (chart.get("indicators", {}).get("quote") or [{}])[0]
        closes = quotes.get("close") or []

        prices: dict[str, float] = {}
        for ts, close in zip(timestamps, closes):
            if ts is None or close is None:
                continue
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            prices[date_str] = round(float(close), 2)

        # Include latest regularMarketPrice for today if daily bar isn't finalized yet
        meta = chart.get("meta") or {}
        market_price = meta.get("regularMarketPrice")
        market_time = meta.get("regularMarketTime")
        if market_price is not None and market_time is not None:
            today_str = datetime.fromtimestamp(market_time, tz=timezone.utc).strftime("%Y-%m-%d")
            prices[today_str] = round(float(market_price), 2)

        return prices

    async def _fetch_from_yahoo(self, days: int) -> dict[str, dict[str, float | None]]:
        """Fetch Brent and WTI futures from Yahoo Finance."""
        range_str = "1mo" if days <= 30 else ("3mo" if days <= 90 else "1y")
        async with httpx.AsyncClient(timeout=20.0) as client:
            brent_prices, wti_prices = await asyncio.gather(
                self._fetch_yahoo_symbol(client, YAHOO_BRENT_SYMBOL, range_str),
                self._fetch_yahoo_symbol(client, YAHOO_WTI_SYMBOL, range_str),
            )

        all_dates = set(brent_prices.keys()) | set(wti_prices.keys())
        return {
            d: {"brent": brent_prices.get(d), "wti": wti_prices.get(d)}
            for d in sorted(all_dates)
        }

    async def _fetch_from_eia(self, days: int) -> dict[str, dict[str, float | None]]:
        """Fallback: fetch daily spot prices from EIA API."""
        if not settings.EIA_API_KEY or settings.EIA_API_KEY == "your_eia_api_key_here":
            logger.warning("EIA_API_KEY not configured — skipping EIA fallback")
            return {}

        start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logger.info(f"Fetching oil prices from EIA fallback for range: {start_date} to {end_date}")

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
        prices_by_date: dict[str, dict[str, float | None]] = {}
        for record in records:
            period = record.get("period")
            product = record.get("product")
            value = record.get("value")
            if not period or value is None:
                continue
            prices_by_date.setdefault(period, {"brent": None, "wti": None})
            try:
                price = float(value)
            except (ValueError, TypeError):
                continue
            if product == BRENT_PRODUCT:
                prices_by_date[period]["brent"] = price
            elif product == WTI_PRODUCT:
                prices_by_date[period]["wti"] = price

        return prices_by_date

    async def fetch_prices(self, days: int | None = None):
        """Fetch and upsert oil prices (Yahoo Finance primary, EIA fallback)."""
        start_time = time.time()
        if days is None:
            days = BACKFILL_DAYS

        prices_by_date: dict[str, dict[str, float | None]] = {}
        source = "Yahoo Finance"
        try:
            prices_by_date = await self._fetch_from_yahoo(days)
        except Exception as e:
            logger.warning(f"Yahoo Finance oil price fetch failed ({e}), falling back to EIA")

        if not prices_by_date:
            source = "EIA"
            prices_by_date = await self._fetch_from_eia(days)

        if not prices_by_date:
            logger.info("No oil price records returned from Yahoo Finance or EIA")
            return

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
            f"Oil price fetch ({source}) complete in {duration:.1f}s — "
            f"{upserted} records upserted. Latest data date: {latest_date}"
        )
