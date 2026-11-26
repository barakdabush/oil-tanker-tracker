"""Centralized write helpers for the ``vessels`` table.

All concurrent writers to ``vessels`` (AIS ingestion, state classification,
position cleanup) must acquire row locks in ascending ``mmsi`` order to avoid
PostgreSQL circular deadlocks (SQLSTATE 40P01). This module centralizes
deterministic ordering and bounded deadlock retries.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from typing import TypeVar

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Vessel, VesselPosition

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEADLOCK_SQLSTATE = "40P01"
DEFAULT_DEADLOCK_MAX_ATTEMPTS = 3
DEFAULT_DEADLOCK_BASE_DELAY_S = 0.05
DEFAULT_DEADLOCK_MAX_DELAY_S = 0.25


def is_deadlock_error(exc: BaseException) -> bool:
    """Return True if *exc* (or a wrapped DBAPI/asyncpg cause) is a PG deadlock."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "sqlstate", None) == DEADLOCK_SQLSTATE:
            return True
        if getattr(current, "pgcode", None) == DEADLOCK_SQLSTATE:
            return True
        if type(current).__name__ == "DeadlockDetectedError":
            return True
        next_exc = getattr(current, "orig", None) or current.__cause__ or current.__context__
        current = next_exc if isinstance(next_exc, BaseException) else None
    return False


async def run_with_deadlock_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = DEFAULT_DEADLOCK_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_DEADLOCK_BASE_DELAY_S,
    max_delay: float = DEFAULT_DEADLOCK_MAX_DELAY_S,
    label: str = "db_write",
) -> T:
    """Execute *operation* with bounded jittered retries on PostgreSQL deadlocks.

    Callers should create a fresh DB session inside *operation* because a
    deadlocked PostgreSQL transaction is aborted and cannot be reused.
    """
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            if not is_deadlock_error(exc) or attempt >= attempts:
                raise
            delay = random.uniform(base_delay, max_delay) * attempt
            logger.warning(
                "PostgreSQL deadlock during %s (attempt %d/%d); retrying in %.3fs",
                label,
                attempt,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _normalize_vessel_rows(
    vessels: Iterable[dict],
    *,
    now: datetime,
) -> tuple[list[dict], set[str]]:
    """Return vessel dicts sorted by ``mmsi ASC`` with uniform keys."""
    sorted_input = sorted(vessels, key=lambda v: v["mmsi"])
    all_keys: set[str] = set()
    for row in sorted_input:
        all_keys.update(row.keys())
    all_keys.add("updated_at")

    normalized: list[dict] = []
    for row in sorted_input:
        item = {key: row.get(key, None) for key in all_keys}
        item["updated_at"] = now
        normalized.append(item)
    return normalized, all_keys


def _sort_position_rows(positions: Iterable[dict]) -> list[dict]:
    """Return position dicts sorted by ``(mmsi, time)`` ascending."""
    return sorted(
        positions,
        key=lambda p: (
            p["mmsi"],
            p["time"].timestamp() if isinstance(p.get("time"), datetime) else 0.0,
        ),
    )


async def upsert_vessels_and_positions(
    session: AsyncSession,
    vessels: Iterable[dict],
    positions: Iterable[dict] = (),
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Bulk-upsert vessels and insert positions in deterministic ``mmsi ASC`` order."""
    ts = now or datetime.now(timezone.utc)
    vessel_list, all_keys = _normalize_vessel_rows(vessels, now=ts)
    position_list = _sort_position_rows(positions)

    if vessel_list:
        v_stmt = pg_insert(Vessel).values(vessel_list)

        set_clause = {}
        for col_name in sorted(all_keys):
            if col_name in ("mmsi", "created_at"):
                continue
            if col_name == "updated_at":
                set_clause[col_name] = v_stmt.excluded[col_name]
            else:
                # Only update if the incoming value is not NULL
                set_clause[col_name] = func.coalesce(
                    v_stmt.excluded[col_name], getattr(Vessel, col_name)
                )

        upsert_stmt = v_stmt.on_conflict_do_update(
            index_elements=["mmsi"],
            set_=set_clause,
            # Only update if the incoming data is newer or existing has no last_seen
            where=(
                (v_stmt.excluded.last_seen == None)  # noqa: E711
                | (Vessel.last_seen == None)  # noqa: E711
                | (v_stmt.excluded.last_seen >= Vessel.last_seen)
            ),
        )
        await session.execute(upsert_stmt)

    if position_list:
        p_stmt = pg_insert(VesselPosition).values(position_list)
        p_upsert_stmt = p_stmt.on_conflict_do_nothing()
        await session.execute(p_upsert_stmt)

    return len(vessel_list), len(position_list)


async def flush_vessels_and_positions(
    session_factory: Callable[[], object],
    vessels: Iterable[dict],
    positions: Iterable[dict] = (),
    *,
    now: datetime | None = None,
    max_attempts: int = DEFAULT_DEADLOCK_MAX_ATTEMPTS,
) -> tuple[int, int]:
    """Commit a batch of vessel upserts and positions with deadlock retry."""
    vessel_rows = list(vessels)
    position_rows = list(positions)
    ts = now or datetime.now(timezone.utc)

    async def _attempt() -> tuple[int, int]:
        async with session_factory() as session:
            counts = await upsert_vessels_and_positions(
                session,
                vessel_rows,
                position_rows,
                now=ts,
            )
            await session.commit()
            return counts

    return await run_with_deadlock_retry(
        _attempt,
        max_attempts=max_attempts,
        label="flush_vessels_and_positions",
    )
