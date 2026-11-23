"""Shared process-wide rate limiter and 429 cooldown for GDELT DOC 2.0 API.

Both NewsFetcher and StraitRiskFetcher run in the same worker process and share
a single egress IP. GDELT enforces strict per-IP rate limits (~1 req / 5s and
burst caps), so coordinating all GDELT calls through one lock + IP cooldown
prevents cross-service collisions and 429 storms.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
MIN_REQUEST_SPACING_SECONDS = 15.0
GDELT_429_COOLDOWN_SECONDS = 60 * 60  # 1 hour IP-level cooldown after persistent 429

_gdelt_lock = asyncio.Lock()
_last_request_monotonic: Optional[float] = None
_cooldown_until: Optional[datetime] = None


def reset_gdelt_state() -> None:
    """Reset shared rate-limit state (intended for unit tests)."""
    global _last_request_monotonic, _cooldown_until
    _last_request_monotonic = None
    _cooldown_until = None


def is_gdelt_in_cooldown(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return _cooldown_until is not None and now < _cooldown_until


def set_gdelt_cooldown(seconds: float = GDELT_429_COOLDOWN_SECONDS, now: Optional[datetime] = None) -> None:
    global _cooldown_until
    now = now or datetime.now(timezone.utc)
    clamped = min(max(float(seconds), 60.0), 4 * 3600.0)
    _cooldown_until = now + timedelta(seconds=clamped)


def _parse_retry_after(resp: httpx.Response) -> Optional[float]:
    raw = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
    if not raw:
        return None
    try:
        return max(1.0, float(str(raw).strip()))
    except ValueError:
        return None


async def gdelt_get_json(
    client: httpx.AsyncClient,
    params: dict,
    *,
    label: str = "GDELT",
    backoff_delays: tuple[float, ...] = (15.0, 30.0),
) -> Optional[dict]:
    """Fetch JSON from GDELT DOC 2.0 with shared spacing and 429 IP cooldown.

    Returns parsed JSON dict, or None if skipped/backed off due to HTTP 429.
    Non-429 HTTP errors still raise via ``raise_for_status()``.
    """
    global _last_request_monotonic

    now = datetime.now(timezone.utc)
    if is_gdelt_in_cooldown(now):
        return None

    async with _gdelt_lock:
        now = datetime.now(timezone.utc)
        if is_gdelt_in_cooldown(now):
            return None

        if _last_request_monotonic is not None:
            elapsed = time.monotonic() - _last_request_monotonic
            if elapsed < MIN_REQUEST_SPACING_SECONDS:
                await asyncio.sleep(MIN_REQUEST_SPACING_SECONDS - elapsed)

        resp: Optional[httpx.Response] = None
        for attempt in range(len(backoff_delays) + 1):
            _last_request_monotonic = time.monotonic()
            resp = await client.get(GDELT_DOC_URL, params=params)
            if resp.status_code != 429:
                break
            if attempt == len(backoff_delays):
                retry_after = _parse_retry_after(resp)
                cooldown_s = max(float(GDELT_429_COOLDOWN_SECONDS), retry_after or 0.0)
                set_gdelt_cooldown(cooldown_s)
                logger.info(
                    f"{label} rate-limited by GDELT (429); pausing all GDELT queries for "
                    f"{int(cooldown_s // 60)}m."
                )
                return None
            retry_after = _parse_retry_after(resp)
            wait_s = min(max(retry_after, 1.0), 60.0) if retry_after is not None else backoff_delays[attempt]
            await asyncio.sleep(wait_s)

        assert resp is not None
        resp.raise_for_status()
        return resp.json()
