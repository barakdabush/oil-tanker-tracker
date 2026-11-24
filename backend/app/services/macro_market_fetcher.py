"""Macro & Refining-Margin (Crack Spread) daily market fetcher.

Fetches free 5-year daily closing series from Yahoo Finance:
- ``RB=F``: RBOB Gasoline futures ($/gal)
- ``HO=F``: NY Harbor ULSD Heating Oil / Diesel futures ($/gal)
- ``DX-Y.NYB``: US Dollar Index (DXY)
- ``^VIX``: CBOE Volatility Index
- ``FRO``: Frontline plc (VLCC crude tanker equity proxy)
- ``XLE``: Energy Select Sector SPDR ETF

Combines with ``oil_prices`` (``BZ=F`` Brent, ``CL=F`` WTI) to compute:
- 3-2-1 Crack Spread = ((2 * RB*42 + 1 * HO*42) / 3) - WTI ($/bbl)
- Brent-WTI Spread = BZ=F - CL=F ($/bbl)

Upserts into ``oil_macro_daily`` so the walk-forward Gradient Boosted Tree
predictor has 1,250+ trading days of high-signal oil market features.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models.global_oil_features import OilMacroDaily, OilPrice

logger = logging.getLogger(__name__)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
RUN_INTERVAL_SECONDS = 60 * 60
GALLONS_PER_BARREL = 42.0

MACRO_SYMBOLS: dict[str, str] = {
    "rbob_gasoline_usd": "RB=F",
    "heating_oil_usd": "HO=F",
    "dxy_close": "DX-Y.NYB",
    "vix_close": "^VIX",
    "tanker_etf_close": "FRO",
    "energy_etf_close": "XLE",
}


def compute_crack_spread_321(
    rbob_gal: Optional[float],
    heating_oil_gal: Optional[float],
    wti_bbl: Optional[float],
) -> Optional[float]:
    """Compute the standard 3-2-1 refinery crack spread in USD per barrel."""
    if rbob_gal is None or heating_oil_gal is None or wti_bbl is None:
        return None
    if rbob_gal <= 0 or heating_oil_gal <= 0 or wti_bbl <= 0:
        return None
    product_bbl = (2.0 * rbob_gal * GALLONS_PER_BARREL + heating_oil_gal * GALLONS_PER_BARREL) / 3.0
    return round(product_bbl - wti_bbl, 2)


class MacroMarketFetcher:
    """Periodic Yahoo Finance fetcher for crack spreads, USD, VIX, and tanker/energy equities."""

    async def run_periodic(self):
        await asyncio.sleep(45)
        while True:
            try:
                await self.fetch_and_store()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"MacroMarketFetcher error: {type(e).__name__}: {e}")
                await asyncio.sleep(10 * 60)
                continue
            await asyncio.sleep(RUN_INTERVAL_SECONDS)

    @staticmethod
    def _range_for_days(days: int) -> str:
        if days <= 30:
            return "1mo"
        if days <= 90:
            return "3mo"
        if days <= 365:
            return "1y"
        if days <= 730:
            return "2y"
        if days <= 1825:
            return "5y"
        return "10y"

    async def _fetch_yahoo_daily(
        self,
        client: httpx.AsyncClient,
        symbol: str,
        range_str: str,
    ) -> dict[date, float]:
        url = YAHOO_CHART_URL.format(symbol=symbol)
        resp = await client.get(
            url,
            params={"range": range_str, "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        payload = resp.json()
        result_list = payload.get("chart", {}).get("result") or []
        if not result_list:
            return {}

        chart = result_list[0]
        timestamps = chart.get("timestamp") or []
        quotes = (chart.get("indicators", {}).get("quote") or [{}])[0]
        closes = quotes.get("close") or []

        out: dict[date, float] = {}
        for ts, close in zip(timestamps, closes):
            if ts is None or close is None:
                continue
            d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
            out[d] = round(float(close), 4)

        meta = chart.get("meta") or {}
        mkt_p = meta.get("regularMarketPrice")
        mkt_t = meta.get("regularMarketTime")
        if mkt_p is not None and mkt_t is not None:
            d_now = datetime.fromtimestamp(mkt_t, tz=timezone.utc).date()
            out[d_now] = round(float(mkt_p), 4)
        return out

    async def fetch_and_store(self, days: Optional[int] = None) -> dict[str, int]:
        """Fetch macro & refining-margin series and upsert into oil_macro_daily."""
        if days is None:
            async with async_session() as session:
                count = (await session.execute(select(func.count()).select_from(OilMacroDaily))).scalar() or 0
            # First run backfills full 5 years; incremental hourly runs fetch 30 days
            days = 1825 if count < 200 else 30

        range_str = self._range_for_days(days)
        series_by_col: dict[str, dict[date, float]] = {}

        async with httpx.AsyncClient(timeout=25.0) as client:
            tasks = [
                self._fetch_yahoo_daily(client, sym, range_str)
                for sym in MACRO_SYMBOLS.values()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for (col_name, sym), res in zip(MACRO_SYMBOLS.items(), results):
            if isinstance(res, Exception):
                logger.warning(f"MacroMarketFetcher failed for {sym}: {type(res).__name__}: {res}")
                series_by_col[col_name] = {}
            else:
                series_by_col[col_name] = res

        all_dates: set[date] = set()
        for d_map in series_by_col.values():
            all_dates.update(d_map.keys())
        if not all_dates:
            return {"rows_upserted": 0, "days": days}

        min_d = min(all_dates)
        async with async_session() as session:
            oil_rows = (
                await session.execute(
                    select(OilPrice).where(OilPrice.price_date >= min_d)
                )
            ).scalars().all()
            oil_by_date = {r.price_date: (r.brent_close_usd, r.wti_close_usd) for r in oil_rows}

            now = datetime.now(timezone.utc)
            records: list[dict] = []
            for d in sorted(all_dates):
                rbob = series_by_col["rbob_gasoline_usd"].get(d)
                ho = series_by_col["heating_oil_usd"].get(d)
                brent, wti = oil_by_date.get(d, (None, None))
                crack = compute_crack_spread_321(rbob, ho, wti)
                bw_spread = (
                    round(brent - wti, 2)
                    if (brent is not None and wti is not None)
                    else None
                )
                records.append({
                    "date": d,
                    "rbob_gasoline_usd": rbob,
                    "heating_oil_usd": ho,
                    "crack_spread_321_usd": crack,
                    "brent_wti_spread_usd": bw_spread,
                    "dxy_close": series_by_col["dxy_close"].get(d),
                    "vix_close": series_by_col["vix_close"].get(d),
                    "tanker_etf_close": series_by_col["tanker_etf_close"].get(d),
                    "energy_etf_close": series_by_col["energy_etf_close"].get(d),
                    "fetched_at": now,
                })

            chunk_size = 500
            for i in range(0, len(records), chunk_size):
                chunk = records[i : i + chunk_size]
                stmt = pg_insert(OilMacroDaily).values(chunk)
                upsert = stmt.on_conflict_do_update(
                    index_elements=["date"],
                    set_={
                        col: getattr(stmt.excluded, col)
                        for col in chunk[0].keys()
                        if col != "date"
                    },
                )
                await session.execute(upsert)
            await session.commit()

        logger.info(f"MacroMarketFetcher: upserted {len(records)} daily macro rows (range={range_str}).")
        return {"rows_upserted": len(records), "days": days}
