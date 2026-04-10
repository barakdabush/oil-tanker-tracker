import asyncio
import pytest
from datetime import date, datetime, timezone, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session, engine
from app.models import Vessel, AISGap, STSEvent, CargoEvent
from app.models.global_oil_features import OilMarketSnapshot
from app.services.global_oil_feature_builder import GlobalOilFeatureBuilder

async def setup_mock_data(session):
    """Inserts mock data into the year 2099 to keep it isolated from real data."""
    target_date = date(2099, 1, 2)
    day_start = datetime(2099, 1, 2, tzinfo=timezone.utc)
    
    # 1. Clean existing mock data
    await session.execute(delete(AISGap).where(AISGap.gap_start >= datetime(2099, 1, 1, tzinfo=timezone.utc)))
    await session.execute(delete(STSEvent).where(STSEvent.start_time >= datetime(2099, 1, 1, tzinfo=timezone.utc)))
    await session.execute(delete(CargoEvent).where(CargoEvent.arrival_time >= datetime(2099, 1, 1, tzinfo=timezone.utc)))
    await session.execute(delete(Vessel).where(Vessel.mmsi.in_([9991, 9992, 9993, 9994])))
    await session.commit()

    # 2. Insert Mock Vessels
    v1 = Vessel(mmsi=9991, name="Vessel Transit", last_seen=day_start + timedelta(hours=1), last_speed=12.0)
    v2 = Vessel(mmsi=9992, name="Vessel Port", last_seen=day_start + timedelta(hours=2), last_speed=0.5)
    v3 = Vessel(mmsi=9993, name="Vessel Idle", last_seen=day_start - timedelta(hours=50), last_speed=0.0)
    v4 = Vessel(mmsi=9994, name="Vessel Dark", last_seen=day_start + timedelta(hours=3), last_speed=10.0)
    session.add_all([v1, v2, v3, v4])
    await session.flush()

    # 3. Insert mock dark fleet gaps
    gap1 = AISGap(mmsi=9994, gap_start=day_start - timedelta(hours=10), status="dark", gap_end=None)
    gap2 = AISGap(mmsi=9991, gap_start=day_start - timedelta(hours=5), gap_end=day_start + timedelta(hours=3), status="resolved")
    session.add_all([gap1, gap2])

    # 4. Insert mock STS events
    sts1 = STSEvent(vessel_a_mmsi=9991, vessel_b_mmsi=9994, start_time=day_start + timedelta(hours=1), created_at=day_start + timedelta(hours=1), status="detected")
    sts2 = STSEvent(vessel_a_mmsi=9991, vessel_b_mmsi=9992, start_time=day_start + timedelta(hours=2), created_at=day_start + timedelta(hours=2), status="confirmed")
    session.add_all([sts1, sts2])

    # 5. Insert mock Cargo
    cargo = CargoEvent(mmsi=9992, event_type="loading", arrival_time=day_start, created_at=day_start + timedelta(hours=4), estimated_volume_barrels=500000.0)
    session.add(cargo)

    await session.commit()
    return target_date


async def async_test_global_oil_feature_builder_logic():
    """Async implementation of the actual test logic."""
    async with async_session() as session:
        target_date = await setup_mock_data(session)
        
        # Run logic exactly against the 2099 target date
        builder = GlobalOilFeatureBuilder()
        await builder.aggregate(target_date)
        
        # Pull snapshot from DB
        result = await session.execute(select(OilMarketSnapshot).where(OilMarketSnapshot.snapshot_date == target_date))
        snapshot = result.scalar_one_or_none()
        
        assert snapshot is not None
        
        try:
            # Mathematics verification
            assert snapshot.total_active_vessels == 3
            assert snapshot.vessels_in_transit == 2
            assert snapshot.vessels_at_port == 1
            assert snapshot.avg_fleet_speed == 7.5
            assert snapshot.vessels_idle_gt_48h == 1
            assert snapshot.dark_vessels_count == 1
            assert snapshot.resolved_gaps_24h == 1
            assert snapshot.avg_gap_duration_hours == 8.0
            assert snapshot.sts_events_24h == 2
            assert snapshot.sts_confirmed_24h == 1
            assert snapshot.cargo_events_24h == 1
            assert snapshot.estimated_volume_barrels_24h == 500000.0
            
        finally:
            # Clean up the 2099 mock data so we don't pollute the DB
            await session.execute(delete(OilMarketSnapshot).where(OilMarketSnapshot.snapshot_date == target_date))
            await session.execute(delete(AISGap).where(AISGap.gap_start >= datetime(2099, 1, 1, tzinfo=timezone.utc)))
            await session.execute(delete(STSEvent).where(STSEvent.start_time >= datetime(2099, 1, 1, tzinfo=timezone.utc)))
            await session.execute(delete(CargoEvent).where(CargoEvent.arrival_time >= datetime(2099, 1, 1, tzinfo=timezone.utc)))
            await session.execute(delete(Vessel).where(Vessel.mmsi.in_([9991, 9992, 9993, 9994])))
            await session.commit()


def test_global_oil_feature_builder_logic():
    """Pytest entrypoint. Proves the logic works mathematically using isolated mock records."""
    asyncio.run(async_test_global_oil_feature_builder_logic())
