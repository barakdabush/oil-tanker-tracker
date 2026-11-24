"""Oil price fetcher service.

Fetches daily Brent (BZ=F) and WTI (CL=F) front-month futures prices from
Yahoo Finance (matching Google Finance) with EIA spot prices as a fallback,
and upserts them into the oil_prices table.
"""

import asyncio
import logging
import math
import time
from datetime import datetime, timezone, timedelta, date

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import async_session
from app.models.global_oil_features import OilPrice
from app.models.news import OilPriceIntraday

logger = logging.getLogger(__name__)

# Yahoo Finance chart API (front-month futures, matches Google Finance)
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_BRENT_SYMBOL = "BZ=F"
YAHOO_WTI_SYMBOL = "CL=F"

# EIA API v2 fallback endpoint for petroleum spot prices
EIA_BASE_URL = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
BRENT_PRODUCT = "EPCBRENT"
WTI_PRODUCT = "EPCWTI"

RUN_INTERVAL_SECONDS = 60 * 60
BACKFILL_DAYS = 400
INTRADAY_INITIAL_RANGE = "730d"
INTRADAY_INCREMENTAL_RANGE = "30d"


def compute_rolling_daily_vol(closes: list[float], window_bars: int = 20 * 23) -> list[float | None]:
    """Compute trailing daily realized volatility (log-return std scaled to 1 day) from hourly closes.

    Uses ~23 trading hours per day for Brent/WTI futures. Returns None until at least 24 returns exist.
    """
    n = len(closes)
    if n == 0:
        return []
    log_rets: list[float | None] = [None] * n
    for i in range(1, n):
        if closes[i] and closes[i - 1] and closes[i] > 0 and closes[i - 1] > 0:
            log_rets[i] = math.log(closes[i] / closes[i - 1])

    vols: list[float | None] = [None] * n
    scale = math.sqrt(23.0)
    for i in range(n):
        start = max(1, i - window_bars + 1)
        window = [r for r in log_rets[start : i + 1] if r is not None]
        if len(window) < 24:
            continue
        mean = sum(window) / len(window)
        var = sum((r - mean) ** 2 for r in window) / max(1, len(window) - 1)
        vol = math.sqrt(var) * scale
        vols[i] = round(vol, 6) if vol > 0 else None
    return vols


class OilPriceFetcher:
    """Fetches daily and 1h intraday oil prices from Yahoo Finance with EIA daily fallback."""

    async def run_periodic(self):
        """Run oil price fetching periodically every hour (daily + 1h intraday bars)."""
        await asyncio.sleep(30)
        while True:
            try:
                await self.fetch_prices()
                await self.fetch_intraday_prices()
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
        if days <= 30:
            range_str = "1mo"
        elif days <= 90:
            range_str = "3mo"
        elif days <= 365:
            range_str = "1y"
        elif days <= 730:
            range_str = "2y"
        elif days <= 1825:
            range_str = "5y"
        else:
            range_str = "10y"
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

    async def _fetch_yahoo_intraday_symbol(
        self, client: httpx.AsyncClient, symbol: str, range_str: str
    ) -> list[dict]:
        """Fetch 1h OHLCV bars for a Yahoo Finance symbol and compute 20d realized volatility."""
        url = YAHOO_CHART_URL.format(symbol=symbol)
        resp = await client.get(
            url,
            params={"range": range_str, "interval": "1h"},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()

        result_list = data.get("chart", {}).get("result") or []
        if not result_list:
            return []

        chart = result_list[0]
        timestamps = chart.get("timestamp") or []
        quotes = (chart.get("indicators", {}).get("quote") or [{}])[0]
        opens = quotes.get("open") or []
        highs = quotes.get("high") or []
        lows = quotes.get("low") or []
        closes = quotes.get("close") or []
        volumes = quotes.get("volume") or []

        raw_bars: list[dict] = []
        for idx, ts in enumerate(timestamps):
            close = closes[idx] if idx < len(closes) else None
            if ts is None or close is None:
                continue
            bar_time = datetime.fromtimestamp(ts, tz=timezone.utc)
            raw_bars.append({
                "symbol": symbol,
                "bar_time": bar_time,
                "interval": "1h",
                "open": round(float(opens[idx]), 4) if idx < len(opens) and opens[idx] is not None else None,
                "high": round(float(highs[idx]), 4) if idx < len(highs) and highs[idx] is not None else None,
                "low": round(float(lows[idx]), 4) if idx < len(lows) and lows[idx] is not None else None,
                "close": round(float(close), 4),
                "volume": float(volumes[idx]) if idx < len(volumes) and volumes[idx] is not None else None,
            })

        # Deduplicate by bar_time preserving chronological order
        by_time: dict[datetime, dict] = {b["bar_time"]: b for b in raw_bars}
        ordered = [by_time[t] for t in sorted(by_time.keys())]
        vols = compute_rolling_daily_vol([b["close"] for b in ordered])
        now = datetime.now(timezone.utc)
        for bar, vol in zip(ordered, vols):
            bar["realized_vol_20d"] = vol
            bar["fetched_at"] = now
        return ordered

    async def fetch_intraday_prices(self, range_str: str | None = None) -> int:
        """Fetch and upsert 1h intraday Brent & WTI bars into oil_prices_intraday."""
        start_time = time.time()
        if range_str is None:
            async with async_session() as session:
                existing_cnt = (
                    await session.execute(select(func.count()).select_from(OilPriceIntraday))
                ).scalar() or 0
            range_str = INTRADAY_INITIAL_RANGE if existing_cnt < 500 else INTRADAY_INCREMENTAL_RANGE

        all_bars: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                brent_bars, wti_bars = await asyncio.gather(
                    self._fetch_yahoo_intraday_symbol(client, YAHOO_BRENT_SYMBOL, range_str),
                    self._fetch_yahoo_intraday_symbol(client, YAHOO_WTI_SYMBOL, range_str),
                )
            all_bars = brent_bars + wti_bars
        except Exception as e:
            logger.warning(f"Yahoo Finance 1h intraday oil fetch failed: {type(e).__name__}: {e}")
            return 0

        if not all_bars:
            return 0

        # Batch upsert in chunks of 500 to keep parameter counts well below asyncpg limits
        chunk_size = 500
        async with async_session() as session:
            for i in range(0, len(all_bars), chunk_size):
                chunk = all_bars[i : i + chunk_size]
                stmt = pg_insert(OilPriceIntraday).values(chunk)
                upsert = stmt.on_conflict_do_update(
                    index_elements=["symbol", "bar_time"],
                    set_={
                        "open": stmt.excluded.open,
                        "high": stmt.excluded.high,
                        "low": stmt.excluded.low,
                        "close": stmt.excluded.close,
                        "volume": stmt.excluded.volume,
                        "realized_vol_20d": func.coalesce(
                            stmt.excluded.realized_vol_20d,
                            OilPriceIntraday.realized_vol_20d,
                        ),
                        "fetched_at": stmt.excluded.fetched_at,
                    },
                )
                await session.execute(upsert)
            await session.commit()

        duration = time.time() - start_time
        logger.info(
            f"Intraday 1h oil price fetch ({range_str}) complete in {duration:.1f}s — "
            f"{len(all_bars)} bars upserted."
        )
        return len(all_bars)

