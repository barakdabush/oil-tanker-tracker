"""AIS data ingestion service via aisstream.io and Open Waters AIS WebSockets."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import websockets
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import async_session
from app.models import Vessel, VesselPosition
from app.utils.geo import haversine_km, is_plausible_move, is_valid_coordinate
from app.ws_manager import manager

logger = logging.getLogger(__name__)


from app.types.ais import AISPositionReport, AISShipDimension, AISEta, AISShipStaticData, AISMetaData, AISMessage

# AIS tanker ship type codes
TANKER_TYPES = set(range(80, 90))

# Focused bounding boxes around the 8 key oil tanker chokepoints
# Format: (min_lat, min_lon, max_lat, max_lon)
CHOKEPOINT_BBOXES = [
    (22.0, 48.0, 30.5, 62.0),     # Strait of Hormuz + full Persian Gulf & Gulf of Oman
    (0.5, 98.0, 7.0, 105.5),      # Strait of Malacca (Singapore to Andaman Sea)
    (20.0, 31.5, 32.5, 35.5),     # Suez Canal + northern/central Red Sea
    (10.5, 38.0, 20.0, 46.5),     # Bab el-Mandeb + southern Red Sea & Gulf of Aden
    (40.0, 27.5, 42.0, 30.5),     # Turkish Straits / Bosphorus (Black Sea mouth + Marmara Sea)
    (54.0, 9.5, 57.5, 14.5),      # Danish Straits (Baltic Sea exits)
    (-37.0, 16.5, -32.0, 21.5),   # Cape of Good Hope (southern Africa rounding)
    (7.5, -80.5, 10.5, -77.5),    # Panama Canal (Pacific + Caribbean approaches)
]

# Additional coastal oil-corridor & major hub bounding boxes subscribed on
# aisstream.io (Open Waters stays on the 8 core CHOKEPOINT_BBOXES due to its
# area limit). Bridges key blind spots between chokepoints without global noise.
CORRIDOR_BBOXES = [
    (5.0, 66.0, 23.5, 82.0),      # West India & Sri Lanka (Jamnagar, Mumbai, Colombo — Hormuz <-> Malacca)
    (30.5, 22.0, 38.0, 36.5),     # Eastern Mediterranean & Aegean (Suez <-> Bosporus + Laconian Gulf STS)
    (34.5, -9.0, 38.5, 3.0),      # Strait of Gibraltar & Western Med approach
    (49.0, -5.5, 54.0, 6.5),      # English Channel & Rotterdam / ARA refining hub
    (21.0, 113.0, 33.0, 125.0),   # East / South China Sea (Zhoushan, Ningbo, Taiwan Strait)
    (25.0, -98.0, 30.5, -87.0),   # US Gulf Coast (Houston, Corpus Christi, LOOP crude exports)
]

AISSTREAM_BBOXES = CHOKEPOINT_BBOXES + CORRIDOR_BBOXES

POSITION_MSG_TYPES = (
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "LongRangeAisBroadcastMessage",
)
STATIC_MSG_TYPES = ("ShipStaticData", "StaticDataReport")

MAX_PENDING_POSITIONS = 10000
MAX_KNOWN_NON_TANKERS = 150000
SUSPECT_POSITION_QUORUM = 3

# Regex for Go time.String() format used by aisstream.io, e.g. "2026-04-23 18:42:33.749439468 +0000 UTC"
_GO_TIME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d+))?\s*([+-]\d{2}:?\d{2})?(?:\s+UTC)?$"
)

# In-memory caches to avoid excessive DB writes and UI updates
_last_saved_positions = {}
_last_trusted_positions = {}  # MMSI -> {"lat", "lon", "time"} of latest accepted position
_suspect_positions = {}       # MMSI -> list[{"lat", "lon", "time"}] awaiting quorum
_last_vessel_db_updates = {}
_last_ui_updates = {}
_last_seen_timestamps = {}
_known_drafts = {}
_known_tankers = set()      # MMSIs confirmed to be tankers
_known_non_tankers = set()  # MMSIs confirmed to be non-tankers
_pending_positions = {}     # MMSI -> (msg, pos_key) buffered while awaiting static data

# In-memory batch accumulators
_vessel_batch = {}
_position_batch = []
_batch_lock = asyncio.Lock()

# One-time structure logging
_logged_structures = set()

# Per-source ingestion telemetry
_source_stats = {
    "aisstream": {"messages_received": 0, "tanker_positions": 0, "dropped_glitches": 0, "last_message_at": None},
    "openwaters": {"messages_received": 0, "tanker_positions": 0, "dropped_glitches": 0, "last_message_at": None, "sub_sources": {}},
}


def _parse_ais_timestamp(timestamp_str: Optional[str], fallback: datetime) -> datetime:
    """Parse AIS timestamp strings from both ISO 8601 (Open Waters) and Go time.String() (aisstream.io)."""
    if not timestamp_str or not isinstance(timestamp_str, str):
        return fallback
    raw = timestamp_str.strip()
    if not raw:
        return fallback
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        pass

    m = _GO_TIME_RE.match(raw)
    if m:
        base, frac, tz_part = m.groups()
        iso = base.replace(" ", "T")
        if frac:
            iso += "." + frac[:6].ljust(6, "0")
        if tz_part:
            if ":" not in tz_part and len(tz_part) == 5:
                tz_part = f"{tz_part[:3]}:{tz_part[3:]}"
            iso += tz_part
        else:
            iso += "+00:00"
        try:
            dt = datetime.fromisoformat(iso)
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    return fallback


def get_ingestion_stats() -> dict:
    """Return a snapshot of per-source AIS ingestion statistics."""
    return {
        "aisstream": dict(_source_stats["aisstream"]),
        "openwaters": {
            **_source_stats["openwaters"],
            "sub_sources": dict(_source_stats["openwaters"]["sub_sources"]),
        },
        "known_tankers": len(_known_tankers),
        "pending_positions": len(_pending_positions),
    }


async def start_ais_ingestion():
    """Main AIS ingestion loop — connects to configured AIS streams and processes messages."""

    # ── Seed Whitelist & Last Trusted Positions ──
    logger.info("Seeding known tankers whitelist from database...")
    try:
        async with async_session() as session:
            result = await session.execute(
                select(
                    Vessel.mmsi,
                    Vessel.draft,
                    Vessel.last_lat,
                    Vessel.last_lon,
                    Vessel.last_seen,
                ).where(Vessel.ship_type.in_(TANKER_TYPES))
            )
            seeded_positions = 0
            for row in result.all():
                _known_tankers.add(row.mmsi)
                if row.draft is not None:
                    _known_drafts[row.mmsi] = row.draft
                if row.last_seen is not None and is_valid_coordinate(row.last_lat, row.last_lon):
                    ts = row.last_seen
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    _last_trusted_positions[row.mmsi] = {
                        "lat": row.last_lat,
                        "lon": row.last_lon,
                        "time": ts,
                    }
                    seeded_positions += 1
        logger.info(
            f"Loaded {len(_known_tankers)} known tankers into whitelist "
            f"({seeded_positions} with trusted last positions)."
        )
    except Exception as e:
        logger.error(f"Failed to seed whitelist: {e}")

    tasks = []
    if settings.AIS_API_KEY and settings.AIS_API_KEY != "your_aisstream_api_key_here":
        tasks.append(_run_stream_loop("aisstream.io", _connect_and_consume_aisstream))
    else:
        logger.warning("AIS_API_KEY is not configured; skipping aisstream.io connection.")

    if settings.OPENWATERS_ENABLED:
        tasks.append(_run_stream_loop("openwaters.io", _connect_and_consume_openwaters))

    if not tasks:
        logger.error("No AIS stream sources enabled or configured!")
        return

    await asyncio.gather(*tasks)


async def _run_stream_loop(source_name: str, connect_fn):
    """Reconnect loop with exponential backoff for a single AIS stream source."""
    backoff = 5
    while True:
        try:
            await connect_fn()
            backoff = 5
        except asyncio.CancelledError:
            logger.info(f"AIS ingestion ({source_name}) cancelled")
            break
        except (TimeoutError, ConnectionRefusedError):
            logger.warning(f"AIS ({source_name}) connection timeout/refused. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 60)
        except Exception as e:
            err_msg = str(e).lower()
            if "invalid token" in err_msg or "policy violation" in err_msg or "1008" in err_msg:
                logger.warning(
                    f"AIS ({source_name}) rejected credentials ({e.__class__.__name__}: {e}). "
                    "Stopping retries until configuration is updated."
                )
                break
            logger.error(f"AIS ({source_name}) disrupted ({e.__class__.__name__}: {e}). Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 60)


async def _connect_and_consume_aisstream():
    """Connect to aisstream.io WebSocket and consume messages."""
    # aisstream.io expects [[top-left lat, top-left lon], [bottom-right lat, bottom-right lon]]
    subscribe_msg = {
        "APIKey": settings.AIS_API_KEY,
        "BoundingBoxes": [
            [[max_lat, min_lon], [min_lat, max_lon]]
            for (min_lat, min_lon, max_lat, max_lon) in AISSTREAM_BBOXES
        ],
        "FilterMessageTypes": list(POSITION_MSG_TYPES + STATIC_MSG_TYPES),
    }

    logger.info(f"Connecting to aisstream.io at {settings.AIS_WS_URL}...")
    key_masked = settings.AIS_API_KEY[:4] + "***" + settings.AIS_API_KEY[-4:] if len(settings.AIS_API_KEY) > 8 else "***"
    logger.info(f"Using aisstream.io API Key: {key_masked}")

    async with websockets.connect(
        settings.AIS_WS_URL,
        open_timeout=30,
    ) as ws:
        await ws.send(json.dumps(subscribe_msg))
        logger.info(
            f"✅ Connected to aisstream.io — receiving AIS data across "
            f"{len(AISSTREAM_BBOXES)} zones ({len(CHOKEPOINT_BBOXES)} chokepoints + {len(CORRIDOR_BBOXES)} oil corridors)"
        )

        async for raw_msg in ws:
            try:
                msg = json.loads(raw_msg)
                _source_stats["aisstream"]["messages_received"] += 1
                _source_stats["aisstream"]["last_message_at"] = datetime.now(timezone.utc).isoformat()
                if isinstance(msg.get("MetaData"), dict):
                    msg["MetaData"]["Source"] = "aisstream"
                await _process_message(msg)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from aisstream.io")
            except Exception as e:
                logger.error(f"Error processing aisstream.io message: {e}")


def _normalize_openwaters_event(event: dict) -> Optional[dict]:
    """Normalize an Open Waters /v1/stream event into the AISMessage envelope."""
    frame_type = event.get("type")
    if frame_type and frame_type != "event":
        if frame_type == "error":
            logger.warning(f"Open Waters AIS error frame: {event.get('error', event)}")
        elif frame_type in ("welcome", "ack"):
            logger.info(f"Open Waters AIS {frame_type}: {json.dumps(event, default=str)}")
        return None

    msg_type = event.get("msg_type")
    payload = event.get("message")
    if not msg_type or not isinstance(payload, dict):
        return None

    mmsi = payload.get("UserID") or event.get("mmsi") or 0
    if not mmsi:
        return None

    _source_stats["openwaters"]["messages_received"] += 1
    _source_stats["openwaters"]["last_message_at"] = datetime.now(timezone.utc).isoformat()
    sub_source = event.get("source")
    if sub_source:
        sub_counts = _source_stats["openwaters"]["sub_sources"]
        sub_counts[sub_source] = sub_counts.get(sub_source, 0) + 1
        source_label = f"openwaters:{sub_source}"
    else:
        source_label = "openwaters"

    meta = {
        "MMSI": mmsi,
        "ShipName": (payload.get("Name") or "").strip(),
        "latitude": event.get("lat"),
        "longitude": event.get("lon"),
        "time_utc": event.get("time"),
        "Source": source_label,
    }
    return {
        "MessageType": msg_type,
        "Message": {msg_type: payload},
        "MetaData": meta,
    }


def _fit_openwaters_boxes(area_limit: Optional[float]) -> list[list[float]]:
    """Select chokepoint bounding boxes that fit within Open Waters' area limit (sq degrees)."""
    all_boxes = [[min_lat, min_lon, max_lat, max_lon] for (min_lat, min_lon, max_lat, max_lon) in CHOKEPOINT_BBOXES]
    if area_limit is None or area_limit <= 0:
        return all_boxes

    selected = []
    used_area = 0.0
    for box in all_boxes:
        box_area = abs(box[2] - box[0]) * abs(box[3] - box[1])
        if used_area + box_area <= area_limit:
            selected.append(box)
            used_area += box_area

    if not selected:
        # Fallback to core Strait of Hormuz sized to fit tight anonymous limits
        # Center around (26.2, 56.5), e.g., 2.5 x 3.5 = 8.75 sq deg
        core_hormuz = [24.8, 54.8, 27.3, 58.3]
        if 2.5 * 3.5 <= area_limit:
            selected.append(core_hormuz)
        else:
            side = max(0.5, (area_limit * 0.9) ** 0.5)
            selected.append([26.2 - side / 2, 56.5 - side / 2, 26.2 + side / 2, 56.5 + side / 2])

    return selected


async def _connect_and_consume_openwaters():
    """Connect to Open Waters AIS /v1/stream WebSocket and consume messages."""
    ws_url = settings.OPENWATERS_WS_URL
    if settings.OPENWATERS_API_KEY:
        sep = "&" if "?" in ws_url else "?"
        ws_url = f"{ws_url}{sep}key={settings.OPENWATERS_API_KEY}"

    logger.info(f"Connecting to Open Waters AIS at {settings.OPENWATERS_WS_URL}...")
    async with websockets.connect(
        ws_url,
        open_timeout=30,
    ) as ws:
        area_limit = None
        try:
            first_raw = await asyncio.wait_for(ws.recv(), timeout=10)
            first_event = json.loads(first_raw)
            _normalize_openwaters_event(first_event)
            if first_event.get("type") == "welcome":
                area_limit = (first_event.get("limits") or {}).get("area")
        except Exception as e:
            logger.debug(f"Open Waters initial welcome check skipped: {e}")

        boxes = _fit_openwaters_boxes(area_limit)
        if len(boxes) < len(CHOKEPOINT_BBOXES):
            logger.warning(
                f"Open Waters area limit is {area_limit} sq°; subscribing to {len(boxes)}/{len(CHOKEPOINT_BBOXES)} "
                f"boxes (Hormuz prioritized). Set OPENWATERS_API_KEY for full 8-chokepoint coverage."
            )

        subscribe_msg = {
            "type": "subscribe",
            "bbox": boxes,
            "snapshot": True,
        }
        await ws.send(json.dumps(subscribe_msg))
        logger.info(f"✅ Connected to Open Waters AIS — subscribed to {len(boxes)} bounding box(es)")

        async for raw_msg in ws:
            try:
                event = json.loads(raw_msg)
                msg = _normalize_openwaters_event(event)
                if msg is not None:
                    await _process_message(msg)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from Open Waters AIS")
            except Exception as e:
                logger.error(f"Error processing Open Waters AIS message: {e}")


async def _process_message(msg: AISMessage):
    """Process an incoming AIS message."""
    msg_type = msg.get("MessageType")

    # Log full structure once per message type so we can see all available fields
    if msg_type and msg_type not in _logged_structures:
        _logged_structures.add(msg_type)
        logger.info(f"\n{'='*60}\nFIRST {msg_type} structure:\n{json.dumps(msg, indent=2, default=str)}\n{'='*60}")

    if msg_type in POSITION_MSG_TYPES:
        await _handle_position_report(msg, msg_type)
    elif msg_type in STATIC_MSG_TYPES:
        await _handle_static_data(msg, msg_type)


async def _handle_position_report(msg: AISMessage, pos_key: str = "PositionReport"):
    """Handle an AIS position report — insert position and update vessel."""
    meta: Optional[AISMetaData] = msg.get("MetaData")
    position: Optional[AISPositionReport] = msg.get("Message", {}).get(pos_key)
    if not position or not meta:
        return

    mmsi = int(meta.get("MMSI") or position.get("UserID") or 0)
    if mmsi == 0:
        return

    # Extract ship type from metadata or ExtendedClassBPositionReport if available
    ship_type = meta.get("ShipType", None)
    if ship_type is None:
        ship_type = position.get("Type", None)

    # ── Tanker Whitelist & Pending Buffer ──
    if mmsi in _known_non_tankers:
        if ship_type is not None and ship_type in TANKER_TYPES:
            _known_non_tankers.discard(mmsi)
            _known_tankers.add(mmsi)
        else:
            return

    if mmsi not in _known_tankers:
        if ship_type is not None and ship_type in TANKER_TYPES:
            _known_tankers.add(mmsi)
            logger.info(f"Added new tanker to whitelist from position data: MMSI {mmsi}")
        elif ship_type is not None:
            if len(_known_non_tankers) < MAX_KNOWN_NON_TANKERS:
                _known_non_tankers.add(mmsi)
            _pending_positions.pop(mmsi, None)
            return
        else:
            # ShipType not yet known — buffer latest position until ShipStaticData arrives
            if mmsi not in _pending_positions and len(_pending_positions) >= MAX_PENDING_POSITIONS:
                oldest_mmsi = next(iter(_pending_positions))
                _pending_positions.pop(oldest_mmsi, None)
            _pending_positions[mmsi] = (msg, pos_key)
            return
    elif ship_type is not None and ship_type not in TANKER_TYPES:
        # Edge case: It was in our whitelist but is now reporting as a non-tanker
        _known_tankers.discard(mmsi)
        if len(_known_non_tankers) < MAX_KNOWN_NON_TANKERS:
            _known_non_tankers.add(mmsi)
        logger.info(f"Removed MMSI {mmsi} from whitelist (type changed to {ship_type})")
        return

    source = meta.get("Source")
    provider = ("openwaters" if source.startswith("openwaters") else "aisstream") if source else None

    if position.get("Valid") is False:
        if provider:
            _source_stats[provider]["dropped_glitches"] += 1
        return

    lat = position.get("Latitude")
    lon = position.get("Longitude")
    speed = position.get("Sog", None)  # Speed over ground
    course = position.get("Cog", None)  # Course over ground
    heading = position.get("TrueHeading", None)
    nav_status = position.get("NavigationalStatus", None)

    # Validate coordinates (rejects None, NaN/Inf, out-of-range, and Null Island (0, 0))
    if not is_valid_coordinate(lat, lon):
        if provider:
            _source_stats[provider]["dropped_glitches"] += 1
        return

    now = datetime.now(timezone.utc)
    timestamp = _parse_ais_timestamp(meta.get("time_utc", None), now)

    # Drop stale/out-of-order positions (e.g. delayed duplicate from a slower feed)
    last_seen_ts = _last_seen_timestamps.get(mmsi)
    if last_seen_ts and timestamp < last_seen_ts:
        return

    # ── Teleport / GPS-Spoofing Guard (before throttle so glitches never bypass 30-min save) ──
    trusted = _last_trusted_positions.get(mmsi)
    if trusted is not None:
        if is_plausible_move(trusted["lat"], trusted["lon"], trusted["time"], lat, lon, timestamp):
            _suspect_positions.pop(mmsi, None)
            _last_trusted_positions[mmsi] = {"lat": lat, "lon": lon, "time": timestamp}
        else:
            suspects = _suspect_positions.get(mmsi, [])
            if suspects and is_plausible_move(
                suspects[-1]["lat"], suspects[-1]["lon"], suspects[-1]["time"], lat, lon, timestamp
            ):
                suspects.append({"lat": lat, "lon": lon, "time": timestamp})
            else:
                suspects = [{"lat": lat, "lon": lon, "time": timestamp}]

            if len(suspects) >= SUSPECT_POSITION_QUORUM:
                # 3 consecutive consistent reports confirm real relocation / recovery from bad DB anchor
                logger.info(
                    f"Position quorum reached for MMSI {mmsi}: relocating from "
                    f"({trusted['lat']:.4f}, {trusted['lon']:.4f}) to ({lat:.4f}, {lon:.4f})"
                )
                _last_trusted_positions[mmsi] = {"lat": lat, "lon": lon, "time": timestamp}
                _suspect_positions.pop(mmsi, None)
            else:
                _suspect_positions[mmsi] = suspects
                if provider:
                    _source_stats[provider]["dropped_glitches"] += 1
                dist_km = haversine_km(trusted["lat"], trusted["lon"], lat, lon)
                dt_sec = (timestamp - trusted["time"]).total_seconds()
                logger.debug(
                    f"Dropped implausible jump for MMSI {mmsi} ({source}): "
                    f"({trusted['lat']:.4f}, {trusted['lon']:.4f}) -> ({lat:.4f}, {lon:.4f}) "
                    f"({dist_km:.1f} km in {dt_sec:.0f}s; suspect {len(suspects)}/{SUSPECT_POSITION_QUORUM})"
                )
                return
    else:
        _last_trusted_positions[mmsi] = {"lat": lat, "lon": lon, "time": timestamp}

    _last_seen_timestamps[mmsi] = timestamp

    # ── Smart Position Save Logic ──
    last_saved = _last_saved_positions.get(mmsi)
    save_position = False

    if not last_saved:
        save_position = True
    else:
        time_diff = (timestamp - last_saved["time"]).total_seconds() / 60.0
        dist_km = haversine_km(last_saved["lat"], last_saved["lon"], lat, lon)

        # Save if moved more than 10km or more than 30 minutes have passed
        if dist_km > 10.0 or time_diff > 30.0 or time_diff < 0: # time_diff < 0 handles clock skew/resets
            save_position = True

    # Throttle DB updates to vessels table (update when position is saved or at least every 60s)
    last_v_update = _last_vessel_db_updates.get(mmsi)
    should_update_vessel_db = (
        save_position
        or not last_v_update
        or (timestamp - last_v_update).total_seconds() >= 60.0
        or (timestamp - last_v_update).total_seconds() < 0
    )

    if provider:
        _source_stats[provider]["tanker_positions"] += 1

    if should_update_vessel_db:
        async with _batch_lock:
            v_data = _vessel_batch.setdefault(mmsi, {"mmsi": mmsi})
            v_data["last_seen"] = timestamp
            v_data["last_lat"] = lat
            v_data["last_lon"] = lon
            v_data["last_speed"] = speed
            v_data["last_course"] = course
            if source:
                v_data["last_source"] = source[:64]
            name_val = meta.get("ShipName", "").strip()
            if name_val:
                v_data["name"] = name_val
            if ship_type is not None:
                v_data["ship_type"] = ship_type
        _last_vessel_db_updates[mmsi] = timestamp

    if save_position:
        async with _batch_lock:
            _position_batch.append({
                "time": timestamp,
                "mmsi": mmsi,
                "latitude": lat,
                "longitude": lon,
                "speed": speed,
                "course": course,
                "heading": heading,
                "nav_status": nav_status,
                "draft": _known_drafts.get(mmsi),
                "source": source[:64] if source else None,
            })
        _last_saved_positions[mmsi] = {"lat": lat, "lon": lon, "time": timestamp}

    # logger.debug(f"Position: MMSI={mmsi} lat={lat:.4f} lon={lon:.4f} speed={speed}")

    # ── UI Trail Update Logic (Every 1 minute) ──
    last_ui_time = _last_ui_updates.get(mmsi)
    update_ui_trail = False
    
    if not last_ui_time or (timestamp - last_ui_time).total_seconds() >= 60:
        update_ui_trail = True
        _last_ui_updates[mmsi] = timestamp

    # Broadcast position update to websocket clients
    await manager.broadcast({
        "type": "POSITION_UPDATE",
        "mmsi": mmsi,
        "name": meta.get("ShipName", "").strip() or None,
        "lat": lat,
        "lon": lon,
        "speed": speed,
        "course": course,
        "heading": heading,
        "nav_status": nav_status,
        "source": source,
        "timestamp": timestamp.isoformat(),
        "update_ui_trail": update_ui_trail
    })

async def _handle_static_data(msg: AISMessage, static_key: str = "ShipStaticData"):
    """Handle AIS static/voyage data — update vessel registry."""
    meta: Optional[AISMetaData] = msg.get("MetaData")
    static: Optional[dict] = msg.get("Message", {}).get(static_key)
    if not static or not meta:
        return

    mmsi = int(meta.get("MMSI") or static.get("UserID") or 0)
    if mmsi == 0:
        return

    # Support both ShipStaticData (Msg 5) and StaticDataReport (Msg 24)
    report_a = static.get("ReportA") or {}
    report_b = static.get("ReportB") or {}
    ship_type = static.get("Type", None)
    if ship_type is None and isinstance(report_b, dict):
        ship_type = report_b.get("ShipType", None)

    newly_confirmed_tanker = False

    # Strictly process only tankers (types 80-89)
    if ship_type is not None:
        if ship_type not in TANKER_TYPES:
            if mmsi in _known_tankers:
                _known_tankers.discard(mmsi)
                logger.info(f"Removed MMSI {mmsi} from whitelist (static data confirmed type {ship_type})")
            if len(_known_non_tankers) < MAX_KNOWN_NON_TANKERS:
                _known_non_tankers.add(mmsi)
            _pending_positions.pop(mmsi, None)
            return
        _known_non_tankers.discard(mmsi)
        if mmsi not in _known_tankers:
            _known_tankers.add(mmsi)
            newly_confirmed_tanker = True
            logger.info(f"Added new tanker to whitelist from static data: MMSI {mmsi}")
    elif mmsi not in _known_tankers:
        # Static data has no ship_type (e.g. Msg 24 Part A only) and vessel is not a known tanker -> ignore
        return

    dimension = static.get("Dimension") or (report_b.get("Dimension") if isinstance(report_b, dict) else None) or {}
    length = None
    beam = None
    if dimension:
        a = dimension.get("A", 0)
        b = dimension.get("B", 0)
        c = dimension.get("C", 0)
        d = dimension.get("D", 0)
        length = (a + b) if (a + b) > 0 else None
        beam = (c + d) if (c + d) > 0 else None

    draft = static.get("MaximumStaticDraught", None)
    if draft and draft > 0:
        draft = draft / 10.0  # AIS draft is in 1/10 meters
    else:
        draft = None

    imo = static.get("ImoNumber", None)
    raw_callsign = static.get("CallSign") or (report_b.get("CallSign") if isinstance(report_b, dict) else "") or ""
    callsign = raw_callsign.strip() or None
    destination = (static.get("Destination") or "").strip() or None
    raw_name = static.get("Name") or (report_a.get("Name") if isinstance(report_a, dict) else "") or meta.get("ShipName") or ""
    name = raw_name.strip() or None

    eta_data = static.get("Eta", {})
    eta = None
    if eta_data:
        try:
            month = eta_data.get("Month", 0)
            day = eta_data.get("Day", 0)
            hour = eta_data.get("Hour", 0)
            minute = eta_data.get("Minute", 0)
            if month > 0 and day > 0:
                now = datetime.now(timezone.utc)
                year = now.year if month >= now.month else now.year + 1
                eta = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass

    now = datetime.now(timezone.utc)

    async with _batch_lock:
        v_data = _vessel_batch.setdefault(mmsi, {"mmsi": mmsi})
        if imo: v_data["imo"] = imo
        if name: v_data["name"] = name
        if ship_type is not None: v_data["ship_type"] = ship_type
        if callsign: v_data["callsign"] = callsign
        flag = meta.get("country", None)
        if flag: v_data["flag"] = flag
        if length is not None: v_data["length"] = length
        if beam is not None: v_data["beam"] = beam
        if draft is not None:
            v_data["draft"] = draft
            _known_drafts[mmsi] = draft
        if destination: v_data["destination"] = destination
        if eta is not None: v_data["eta"] = eta

    if newly_confirmed_tanker:
        pending = _pending_positions.pop(mmsi, None)
        if pending is not None:
            pending_msg, pending_key = pending
            await _handle_position_report(pending_msg, pending_key)

    # logger.debug(f"Static: MMSI={mmsi} name={name} type={ship_type}")

async def flush_batch():
    """Flush accumulated vessels and positions to DB."""
    global _vessel_batch, _position_batch
    
    async with _batch_lock:
        if not _vessel_batch and not _position_batch:
            return
            
        current_vessels = _vessel_batch
        current_positions = _position_batch
        
        # Reset current accumulators
        _vessel_batch = {}
        _position_batch = []
        
    now = datetime.now(timezone.utc)
    
    try:
        async with async_session() as session:
            # 1. Bulk Upsert Vessels
            if current_vessels:
                vessel_list = list(current_vessels.values())
                
                # Gather all keys across all vessels to normalize the dictionaries
                # SQLAlchemy multi-row inserts require consistent keys across all dicts
                all_keys = set()
                for v in vessel_list:
                    all_keys.update(v.keys())
                
                for v in vessel_list:
                    v["updated_at"] = now
                    # Add missing keys as None
                    for key in all_keys:
                        if key not in v:
                            v[key] = None
                
                v_stmt = pg_insert(Vessel).values(vessel_list)
                
                # Build DO UPDATE set dict cleanly
                set_clause = {}
                for col_name in all_keys:
                    if col_name in ["mmsi", "created_at"]:
                        continue
                    if col_name == "updated_at":
                        set_clause[col_name] = v_stmt.excluded[col_name]
                    else:
                        # Only update if the new value is not NULL
                        set_clause[col_name] = func.coalesce(v_stmt.excluded[col_name], getattr(Vessel, col_name))
                        
                upsert_stmt = v_stmt.on_conflict_do_update(
                    index_elements=["mmsi"],
                    set_=set_clause,
                    # Only update if the incoming data is newer or existing has no last_seen
                    where=(
                        (v_stmt.excluded.last_seen == None) | 
                        (Vessel.last_seen == None) | 
                        (v_stmt.excluded.last_seen >= Vessel.last_seen)
                    )
                )
                await session.execute(upsert_stmt)
                
            # 2. Bulk Insert Positions
            if current_positions:
                p_stmt = pg_insert(VesselPosition).values(current_positions)
                p_upsert_stmt = p_stmt.on_conflict_do_nothing()
                await session.execute(p_upsert_stmt)
                
            await session.commit()
            logger.info(f"💾 Flushed {len(current_vessels)} vessels and {len(current_positions)} positions to DB")
    except Exception as e:
        logger.error(f"Error flushing DB batch: {e}")

async def start_flush_task():
    """Periodic task to flush DB batches."""
    logger.info("Starting batch flush task, interval=2s")
    tick = 0
    while True:
        try:
            await asyncio.sleep(2)
            await flush_batch()
            tick += 1
            if tick % 30 == 0:
                ais_s = _source_stats["aisstream"]
                ow_s = _source_stats["openwaters"]
                logger.info(
                    f"📊 AIS sources: aisstream(msgs={ais_s['messages_received']}, tankers={ais_s['tanker_positions']}, glitches={ais_s['dropped_glitches']}) | "
                    f"openwaters(msgs={ow_s['messages_received']}, tankers={ow_s['tanker_positions']}, glitches={ow_s['dropped_glitches']}, sub={ow_s['sub_sources']})"
                )
        except asyncio.CancelledError:
            logger.info("Batch flush task cancelled")
            # Final flush before exit
            await flush_batch()
            break
        except Exception as e:
            logger.error(f"Flush task error: {e}")
