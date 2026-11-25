"""Weekly Physical Oil Storage (EIA) & Hedge-Fund Positioning (CFTC COT) Fetcher.

Sources:
1. **CFTC Commitment of Traders (100% free, zero API keys):**
   Disaggregated Futures-Only report via CFTC Public Reporting Socrata API for
   NYMEX WTI Crude Oil (``cftc_contract_market_code='067651'``), 5+ years weekly.
2. **EIA Weekly Petroleum Status Report (uses ``EIA_API_KEY`` if configured):**
   - US Commercial Crude Stocks excl. SPR (``WCESTUS1``)
   - Cushing, OK Hub Crude Stocks (``W_EPC0_SAX_YCUOK_MBBL``)
   - US Strategic Petroleum Reserve (``WCSSTUS1``)
   - US Refinery Utilization % (``WPULEUS3``)

Upserts into ``oil_fundamentals_weekly`` keyed by ``report_date``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import async_session
from app.models.global_oil_features import OilFundamentalsWeekly

logger = logging.getLogger(__name__)

CFTC_COT_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
CFTC_WTI_CODE = "067651"

EIA_WEEKLY_URL = "https://api.eia.gov/v2/petroleum/sum/sndw/data/"
EIA_PUBLIC_HTML_URL = "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx"
EIA_SERIES_MAP: dict[str, str] = {
    "eia_crude_stocks_kbbl": "WCESTUS1",
    "eia_cushing_stocks_kbbl": "W_EPC0_SAX_YCUOK_MBBL",
    "eia_spr_stocks_kbbl": "WCSSTUS1",
    "eia_refinery_util_pct": "WPULEUS3",
}

RUN_INTERVAL_SECONDS = 6 * 60 * 60


def _to_int(val) -> Optional[int]:
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _to_float(val) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_date(val) -> Optional[date]:
    if not val:
        return None
    try:
        return date.fromisoformat(str(val)[:10])
    except ValueError:
        return None


def parse_eia_weekly_html(html: str, start_date: date) -> dict[date, float]:
    """Parse official keyless eia.gov LeafHandler weekly petroleum HTML tables."""
    out: dict[date, float] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        ym = re.search(r"class=['\"]B6['\"][^>]*>.*?(\d{4})-[A-Za-z]{3}", row, flags=re.S | re.I)
        if not ym:
            continue
        year = int(ym.group(1))
        pairs = re.findall(
            r"class=['\"]B5['\"][^>]*>\s*(\d{2})/(\d{2}).*?class=['\"]B3['\"][^>]*>\s*([0-9,.\-]+)",
            row,
            flags=re.S | re.I,
        )
        for mm_s, dd_s, val_s in pairs:
            try:
                d = date(year, int(mm_s), int(dd_s))
                val = float(val_s.replace(",", ""))
            except ValueError:
                continue
            if d >= start_date:
                out[d] = round(val, 2)
    return out


class OilFundamentalsFetcher:
    """Periodic fetcher for weekly CFTC Managed Money positioning and EIA physical storage."""

    async def run_periodic(self):
        await asyncio.sleep(55)
        while True:
            try:
                await self.fetch_and_store()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"OilFundamentalsFetcher error: {type(e).__name__}: {e}")
                await asyncio.sleep(15 * 60)
                continue
            await asyncio.sleep(RUN_INTERVAL_SECONDS)

    async def _fetch_cftc_cot(self, client: httpx.AsyncClient, days: int) -> dict[date, dict]:
        """Fetch weekly CFTC Disaggregated COT Managed Money positions for WTI Crude."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
        limit = min(1000, max(60, days // 5 + 20))
        params = {
            "cftc_contract_market_code": CFTC_WTI_CODE,
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": str(limit),
        }
        resp = await client.get(CFTC_COT_URL, params=params)
        resp.raise_for_status()
        rows = resp.json()

        out: dict[date, dict] = {}
        for r in rows:
            d = _parse_date(r.get("report_date_as_yyyy_mm_dd"))
            if d is None or d < cutoff:
                continue
            mm_long = _to_int(r.get("m_money_positions_long_all"))
            mm_short = _to_int(r.get("m_money_positions_short_all"))
            oi = _to_int(r.get("open_interest_all"))
            mm_net = (mm_long - mm_short) if (mm_long is not None and mm_short is not None) else None
            net_pct_oi = (
                round((mm_net / oi) * 100.0, 2)
                if (mm_net is not None and oi and oi > 0)
                else None
            )
            out[d] = {
                "cftc_mm_long": mm_long,
                "cftc_mm_short": mm_short,
                "cftc_mm_net": mm_net,
                "cftc_open_interest": oi,
                "cftc_mm_net_pct_oi": net_pct_oi,
            }
        return out

    async def _fetch_eia_series(
        self,
        client: httpx.AsyncClient,
        series_id: str,
        start_date: date,
    ) -> dict[date, float]:
        """Fetch a single weekly EIA petroleum series (v2 API if key set, else official keyless eia.gov HTML)."""
        api_key = (settings.EIA_API_KEY or "").strip()
        if api_key and api_key != "your_eia_api_key_here":
            try:
                params = {
                    "api_key": api_key,
                    "frequency": "weekly",
                    "data[0]": "value",
                    "facets[series][]": series_id,
                    "start": start_date.isoformat(),
                    "sort[0][column]": "period",
                    "sort[0][direction]": "desc",
                    "length": "1000",
                }
                resp = await client.get(EIA_WEEKLY_URL, params=params)
                resp.raise_for_status()
                payload = resp.json()
                data_rows = payload.get("response", {}).get("data") or []
                out: dict[date, float] = {}
                for item in data_rows:
                    d = _parse_date(item.get("period"))
                    val = _to_float(item.get("value"))
                    if d is not None and val is not None:
                        out[d] = round(val, 2)
                if len(out) >= 20:
                    return out
            except Exception as e:
                logger.warning(f"EIA v2 API for {series_id} failed ({type(e).__name__}), falling back to public HTML")

        resp = await client.get(
            EIA_PUBLIC_HTML_URL,
            params={"n": "PET", "s": series_id, "f": "W"},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        return parse_eia_weekly_html(resp.text, start_date=start_date)

    async def fetch_and_store(self, days: Optional[int] = None) -> dict[str, int]:
        """Fetch weekly CFTC COT + EIA storage series and upsert into oil_fundamentals_weekly."""
        if days is None:
            async with async_session() as session:
                eia_cnt = (
                    await session.execute(
                        select(func.count())
                        .select_from(OilFundamentalsWeekly)
                        .where(OilFundamentalsWeekly.eia_crude_stocks_kbbl.isnot(None))
                    )
                ).scalar() or 0
            days = 1825 if eia_cnt < 80 else 90

        start_date = (datetime.now(timezone.utc) - timedelta(days=days)).date()
        cftc_by_date: dict[date, dict] = {}
        eia_by_col: dict[str, dict[date, float]] = {k: {} for k in EIA_SERIES_MAP}

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                cftc_by_date = await self._fetch_cftc_cot(client, days=days)
            except Exception as e:
                logger.warning(f"CFTC COT fetch failed: {type(e).__name__}: {e}")

            eia_tasks = [
                self._fetch_eia_series(client, sid, start_date)
                for sid in EIA_SERIES_MAP.values()
            ]
            eia_results = await asyncio.gather(*eia_tasks, return_exceptions=True)
            for col, res in zip(EIA_SERIES_MAP.keys(), eia_results):
                if isinstance(res, Exception):
                    logger.warning(f"EIA weekly series {col} failed: {type(res).__name__}: {res}")
                else:
                    eia_by_col[col] = res

        # Compute week-over-week crude stock changes
        crude_dates = sorted(eia_by_col["eia_crude_stocks_kbbl"].keys())
        crude_chg_by_date: dict[date, float] = {}
        for i in range(1, len(crude_dates)):
            d_cur, d_prev = crude_dates[i], crude_dates[i - 1]
            crude_chg_by_date[d_cur] = round(
                eia_by_col["eia_crude_stocks_kbbl"][d_cur] - eia_by_col["eia_crude_stocks_kbbl"][d_prev],
                2,
            )

        all_dates: set[date] = set(cftc_by_date.keys())
        for d_map in eia_by_col.values():
            all_dates.update(d_map.keys())
        if not all_dates:
            return {"rows_upserted": 0, "cftc_weeks": 0, "eia_weeks": 0}

        now = datetime.now(timezone.utc)
        records: list[dict] = []
        for d in sorted(all_dates):
            c = cftc_by_date.get(d, {})
            records.append({
                "report_date": d,
                "cftc_mm_long": c.get("cftc_mm_long"),
                "cftc_mm_short": c.get("cftc_mm_short"),
                "cftc_mm_net": c.get("cftc_mm_net"),
                "cftc_open_interest": c.get("cftc_open_interest"),
                "cftc_mm_net_pct_oi": c.get("cftc_mm_net_pct_oi"),
                "eia_crude_stocks_kbbl": eia_by_col["eia_crude_stocks_kbbl"].get(d),
                "eia_crude_stocks_chg_kbbl": crude_chg_by_date.get(d),
                "eia_cushing_stocks_kbbl": eia_by_col["eia_cushing_stocks_kbbl"].get(d),
                "eia_spr_stocks_kbbl": eia_by_col["eia_spr_stocks_kbbl"].get(d),
                "eia_refinery_util_pct": eia_by_col["eia_refinery_util_pct"].get(d),
                "fetched_at": now,
            })

        async with async_session() as session:
            stmt = pg_insert(OilFundamentalsWeekly).values(records)
            # Only overwrite non-null incoming fields so CFTC (Tue) and EIA (Fri) rows merge cleanly
            upsert = stmt.on_conflict_do_update(
                index_elements=["report_date"],
                set_={
                    col: func.coalesce(getattr(stmt.excluded, col), getattr(OilFundamentalsWeekly, col))
                    for col in records[0].keys()
                    if col not in ("report_date", "fetched_at")
                }
                | {"fetched_at": stmt.excluded.fetched_at},
            )
            await session.execute(upsert)
            await session.commit()

        stats = {
            "rows_upserted": len(records),
            "cftc_weeks": len(cftc_by_date),
            "eia_weeks": len(crude_dates),
        }
        logger.info(f"OilFundamentalsFetcher complete: {stats}")
        return stats
