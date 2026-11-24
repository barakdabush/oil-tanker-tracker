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
