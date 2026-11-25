"""Unified Bottom-Line Oil Price Predictor REST API."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.forecast import BottomLineForecastResponse
from app.services.oil_price_predictor import (
    build_bottom_line_forecast,
    build_feature_importance_report,
)

router = APIRouter()


@router.get("/bottom-line", response_model=BottomLineForecastResponse)
async def get_bottom_line_forecast(
    symbol: str = Query("BZ=F", pattern="^(BZ=F|CL=F)$"),
    db: AsyncSession = Depends(get_db),
):
    """Return the unified Bottom-Line oil price forecast (3d, 7d, 14d) for Brent (BZ=F) or WTI (CL=F)."""
    return await build_bottom_line_forecast(db, symbol=symbol)


@router.get("/feature-importance")
async def get_feature_importance(
    symbol: str = Query("BZ=F", pattern="^(BZ=F|CL=F)$"),
    horizon: str = Query("14d", pattern="^(3d|7d|14d)$"),
    include_all_candidates: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """Return strictly out-of-sample permutation feature importance ranked by OOS accuracy lift."""
    return await build_feature_importance_report(
        db,
        symbol=symbol,
        horizon=horizon,
        include_all_candidates=include_all_candidates,
    )


@router.post("/backfill-macro")
async def backfill_macro_features(
    days: int = Query(1825, ge=30, le=3650, description="Days of macro, CFTC/EIA fundamentals, and IMF PortWatch data to backfill."),
    include_portwatch: bool = Query(False, description="Also backfill 5-year IMF PortWatch satellite-AIS chokepoint tanker counts."),
):
    """Backfill 5-year daily oil_prices, oil_macro_daily (crack/curve/demand/hurricane), weekly CFTC COT + EIA storage, and optional PortWatch."""
    from app.services.macro_market_fetcher import MacroMarketFetcher
    from app.services.oil_fundamentals_fetcher import OilFundamentalsFetcher
    from app.services.oil_price_fetcher import OilPriceFetcher
    from app.services.portwatch_fetcher import PortWatchFetcher

    await OilPriceFetcher().fetch_prices(days=days + 30)
    macro_stats = await MacroMarketFetcher().fetch_and_store(days=days)
    fundamentals_stats = await OilFundamentalsFetcher().fetch_and_store(days=days)
    portwatch_rows = 0
    if include_portwatch:
        portwatch_rows = await PortWatchFetcher().fetch_and_store(lookback_days=days)
    return {
        "macro": macro_stats,
        "fundamentals": fundamentals_stats,
        "portwatch_rows_upserted": portwatch_rows,
    }
