"""Unified Bottom-Line Oil Price Predictor REST API."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.forecast import BottomLineForecastResponse
from app.services.oil_price_predictor import build_bottom_line_forecast

router = APIRouter()


@router.get("/bottom-line", response_model=BottomLineForecastResponse)
async def get_bottom_line_forecast(
    symbol: str = Query("BZ=F", pattern="^(BZ=F|CL=F)$"),
    db: AsyncSession = Depends(get_db),
):
    """Return the unified Bottom-Line oil price forecast (24h, 3d, 7d) for Brent (BZ=F) or WTI (CL=F)."""
    return await build_bottom_line_forecast(db, symbol=symbol)


@router.post("/backfill-macro")
async def backfill_macro_features(
    days: int = Query(1825, ge=30, le=3650, description="Days of Yahoo Finance crack spreads, DXY, VIX, and tanker equities to backfill."),
):
    """Backfill 5-year daily oil_prices + oil_macro_daily (3-2-1 crack spread, Brent-WTI, DXY, VIX, FRO, XLE)."""
    from app.services.macro_market_fetcher import MacroMarketFetcher
    from app.services.oil_price_fetcher import OilPriceFetcher

    await OilPriceFetcher().fetch_prices(days=days + 30)
    stats = await MacroMarketFetcher().fetch_and_store(days=days)
    return stats
