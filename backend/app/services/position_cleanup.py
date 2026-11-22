"""Backward cleanup for corrupted / spoofed AIS vessel positions in the database.

Usage (CLI):
    python -m app.services.position_cleanup --dry-run
    python -m app.services.position_cleanup --mmsi 352002032
    python -m app.services.position_cleanup
"""

import argparse
import asyncio
import logging
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import Vessel, VesselPosition
from app.utils.geo import (
    MAX_PLAUSIBLE_TANKER_SPEED_KNOTS,
    POSITION_JITTER_GRACE_KM,
    despike_positions,
    haversine_km,
    is_plausible_move,
    is_valid_coordinate,
)

logger = logging.getLogger(__name__)

DELETE_BATCH_SIZE = 500


def _vessel_last_position_needs_repair(
    vessel: Vessel,
    latest_clean: VesselPosition,
    *,
    max_speed_knots: float = MAX_PLAUSIBLE_TANKER_SPEED_KNOTS,
    jitter_km: float = POSITION_JITTER_GRACE_KM,
) -> bool:
    """Return True if vessels.last_lat/last_lon is invalid or disagrees implausibly with latest_clean."""
    if not is_valid_coordinate(vessel.last_lat, vessel.last_lon):
        return True
    if vessel.last_seen is None:
        return True

    dist_km = haversine_km(
        latest_clean.latitude,
        latest_clean.longitude,
        vessel.last_lat,
        vessel.last_lon,
    )
    if dist_km <= jitter_km:
        return False

    if vessel.last_seen <= latest_clean.time:
        return True

    return not is_plausible_move(
        latest_clean.latitude,
        latest_clean.longitude,
        latest_clean.time,
        vessel.last_lat,
        vessel.last_lon,
        vessel.last_seen,
        max_speed_knots=max_speed_knots,
        jitter_km=jitter_km,
    )


async def cleanup_corrupted_positions(
    db: AsyncSession,
    *,
    mmsi: Optional[int] = None,
    dry_run: bool = False,
    max_speed_knots: float = MAX_PLAUSIBLE_TANKER_SPEED_KNOTS,
) -> dict:
    """Scan vessel_positions for invalid/teleport spikes, delete them, and repair vessels.last_*.

    When ``dry_run=True``, computes exact counts without modifying the database.
    """
    vessel_query = select(Vessel)
    if mmsi is not None:
        vessel_query = vessel_query.where(Vessel.mmsi == mmsi)

    vessels = list((await db.execute(vessel_query)).scalars().all())

    vessels_scanned = 0
    vessels_affected = 0
    positions_deleted = 0
    vessels_repaired = 0
    affected_mmsis: list[int] = []

    for vessel in vessels:
        vessels_scanned += 1
        pos_result = await db.execute(
            select(VesselPosition)
            .where(VesselPosition.mmsi == vessel.mmsi)
            .order_by(VesselPosition.time.asc())
        )
        rows = list(pos_result.scalars().all())

        clean, rejected = despike_positions(rows, max_speed_knots=max_speed_knots)
        vessel_changed = False

        if rejected:
            vessel_changed = True
            positions_deleted += len(rejected)
            if not dry_run:
                rejected_times = [r.time for r in rejected]
                for i in range(0, len(rejected_times), DELETE_BATCH_SIZE):
                    batch_times = rejected_times[i : i + DELETE_BATCH_SIZE]
                    await db.execute(
                        delete(VesselPosition).where(
                            VesselPosition.mmsi == vessel.mmsi,
                            VesselPosition.time.in_(batch_times),
                        )
                    )

        if clean:
            latest_clean = clean[-1]
            if _vessel_last_position_needs_repair(
                vessel, latest_clean, max_speed_knots=max_speed_knots
            ):
                vessel_changed = True
                vessels_repaired += 1
                if not dry_run:
                    vessel.last_lat = latest_clean.latitude
                    vessel.last_lon = latest_clean.longitude
                    vessel.last_seen = latest_clean.time
                    vessel.last_speed = latest_clean.speed
                    vessel.last_course = latest_clean.course
                    if latest_clean.source:
                        vessel.last_source = latest_clean.source
        elif not is_valid_coordinate(vessel.last_lat, vessel.last_lon) and (
            vessel.last_lat is not None or vessel.last_lon is not None
        ):
            vessel_changed = True
            vessels_repaired += 1
            if not dry_run:
                vessel.last_lat = None
                vessel.last_lon = None

        if vessel_changed:
            vessels_affected += 1
            if len(affected_mmsis) < 100:
                affected_mmsis.append(vessel.mmsi)

    if not dry_run and (positions_deleted > 0 or vessels_repaired > 0):
        await db.commit()

    summary = {
        "dry_run": dry_run,
        "mmsi": mmsi,
        "vessels_scanned": vessels_scanned,
        "vessels_affected": vessels_affected,
        "positions_deleted": positions_deleted,
        "vessels_repaired": vessels_repaired,
        "affected_mmsis_sample": affected_mmsis,
    }
    logger.info(f"Position cleanup complete: {summary}")
    return summary


async def _run_cli(mmsi: Optional[int], dry_run: bool, max_speed_knots: float) -> None:
    async with async_session() as session:
        result = await cleanup_corrupted_positions(
            session,
            mmsi=mmsi,
            dry_run=dry_run,
            max_speed_knots=max_speed_knots,
        )
    print(result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean corrupted/spoofed AIS positions and repair vessels.last_lat/last_lon."
    )
    parser.add_argument("--mmsi", type=int, default=None, help="Limit cleanup to a single MMSI")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be cleaned without modifying DB")
    parser.add_argument(
        "--max-speed-knots",
        type=float,
        default=MAX_PLAUSIBLE_TANKER_SPEED_KNOTS,
        help=f"Maximum plausible implied speed in knots (default: {MAX_PLAUSIBLE_TANKER_SPEED_KNOTS})",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run_cli(args.mmsi, args.dry_run, args.max_speed_knots))


if __name__ == "__main__":
    main()
