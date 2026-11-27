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
    "ovx_close": "^OVX",
    "tanker_etf_close": "FRO",
    "energy_etf_close": "XLE",
    "copper_close": "HG=F",
    "china_etf_close": "FXI",
    "natgas_close": "NG=F",
    "us10y_yield": "^TNX",
    "product_tanker_close": "STNG",
    "uso_close": "USO",
    "usl_close": "USL",
}

NOAA_NHC_STORMS_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"


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


def compute_gulf_hurricane_risk(active_storms: list[dict]) -> float:
    """Compute a 0..100 Gulf of Mexico oil-infrastructure hurricane risk score from NOAA NHC CurrentStorms."""
    max_score = 0.0
    for st in active_storms or []:
        lat = st.get("latitudeNumeric")
        lon = st.get("longitudeNumeric")
        intensity = float(st.get("intensity") or 0.0)  # knots
        if lat is None or lon is None:
            continue
        lat_f, lon_f = float(lat), float(lon)
        # Gulf of Mexico oil patch core: 18N..31N, -98W..-80W (Caribbean approach: 14N..32N, -99W..-65W)
        in_gulf = 18.0 <= lat_f <= 31.5 and -98.5 <= lon_f <= -80.0
        in_approach = 13.0 <= lat_f <= 32.5 and -99.0 <= lon_f <= -65.0
        if not in_approach:
            continue
        base = min(100.0, max(15.0, (intensity / 130.0) * 100.0))
        score = base if in_gulf else base * 0.45
        max_score = max(max_score, score)
    return round(max_score, 1)


class MacroMarketFetcher:
    """Periodic Yahoo Finance + NOAA NHC fetcher for crack spreads, curve, demand proxies, and Gulf hurricane risk."""

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

    async def _fetch_noaa_hurricane_risk(self, client: httpx.AsyncClient) -> float:
        try:
            resp = await client.get(NOAA_NHC_STORMS_URL, timeout=15.0)
            resp.raise_for_status()
            payload = resp.json()
            return compute_gulf_hurricane_risk(payload.get("activeStorms") or [])
        except Exception as e:
            logger.warning(f"NOAA NHC storm fetch failed: {type(e).__name__}: {e}")
            return 0.0

    async def fetch_and_store(self, days: Optional[int] = None) -> dict[str, int]:
        """Fetch macro, refining-margin, demand, curve, and NOAA Gulf hurricane series and upsert into oil_macro_daily."""
        if days is None:
            async with async_session() as session:
                count = (await session.execute(select(func.count()).select_from(OilMacroDaily))).scalar() or 0
            days = 1825 if count < 200 else 30

        range_str = self._range_for_days(days)
        series_by_col: dict[str, dict[date, float]] = {}

        async with httpx.AsyncClient(timeout=25.0) as client:
            tasks = [
                self._fetch_yahoo_daily(client, sym, range_str)
                for sym in MACRO_SYMBOLS.values()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            gulf_risk_today = await self._fetch_noaa_hurricane_risk(client)

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
        today_utc = datetime.now(timezone.utc).date()
        latest_d = max(all_dates)

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
                uso = series_by_col["uso_close"].get(d)
                usl = series_by_col["usl_close"].get(d)
                curve_proxy = (
                    round(uso / usl, 4)
                    if (uso is not None and usl is not None and usl > 0)
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
                    "ovx_close": series_by_col["ovx_close"].get(d),
                    "tanker_etf_close": series_by_col["tanker_etf_close"].get(d),
                    "energy_etf_close": series_by_col["energy_etf_close"].get(d),
                    "copper_close": series_by_col["copper_close"].get(d),
                    "china_etf_close": series_by_col["china_etf_close"].get(d),
                    "natgas_close": series_by_col["natgas_close"].get(d),
                    "us10y_yield": series_by_col["us10y_yield"].get(d),
                    "product_tanker_close": series_by_col["product_tanker_close"].get(d),
                    "uso_close": uso,
                    "usl_close": usl,
                    "curve_backwardation_proxy": curve_proxy,
                    "gulf_hurricane_risk": gulf_risk_today if d in (today_utc, latest_d) else None,
                    "fetched_at": now,
                })

            chunk_size = 500
            for i in range(0, len(records), chunk_size):
                chunk = records[i : i + chunk_size]
                stmt = pg_insert(OilMacroDaily).values(chunk)
                upsert = stmt.on_conflict_do_update(
                    index_elements=["date"],
                    set_={
                        col: func.coalesce(getattr(stmt.excluded, col), getattr(OilMacroDaily, col))
                        for col in chunk[0].keys()
                        if col not in ("date", "fetched_at")
                    }
                    | {"fetched_at": stmt.excluded.fetched_at},
                )
                await session.execute(upsert)
            await session.commit()

        logger.info(f"MacroMarketFetcher: upserted {len(records)} daily macro rows (range={range_str}, gulf_risk={gulf_risk_today}).")
        return {"rows_upserted": len(records), "days": days, "gulf_hurricane_risk": gulf_risk_today}
