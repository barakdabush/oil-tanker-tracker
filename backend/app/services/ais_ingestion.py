"""AIS data ingestion service via aisstream.io WebSocket."""

import asyncio
import json
import logging
from datetime import datetime, timezone

import websockets
from sqlalchemy import text, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import async_session
from app.models import Vessel, VesselPosition
from app.utils.geo import haversine_km
from app.ws_manager import manager

logger = logging.getLogger(__name__)

# AIS tanker ship type codes
TANKER_TYPES = set(range(80, 90))

# In-memory caches to avoid excessive DB writes and UI updates
_last_saved_positions = {}
_last_ui_updates = {}
_known_tankers = set()  # Strict whitelist: MMSIs confirmed to be tankers

# In-memory batch accumulators
_vessel_batch = {}
_position_batch = []
_batch_lock = asyncio.Lock()


async def start_ais_ingestion():
    """Main AIS ingestion loop — connects to aisstream.io and processes messages."""
    
    # ── Seed Whitelist ──
    logger.info("Seeding known tankers whitelist from database...")
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Vessel.mmsi).where(Vessel.ship_type.in_(TANKER_TYPES))
            )
            for row in result.all():
                _known_tankers.add(row.mmsi)
        logger.info(f"Loaded {len(_known_tankers)} known tankers into whitelist.")
    except Exception as e:
        logger.error(f"Failed to seed whitelist: {e}")

    backoff = 5
    while True:
        try:
            await _connect_and_consume()
            # If we lived and disconnected peacefully (or aisstream restarted us):
            backoff = 5 
        except asyncio.CancelledError:
            logger.info("AIS ingestion cancelled")
            break
        except (TimeoutError, ConnectionRefusedError) as e:
            logger.warning(f"AIS connection timeout/refused. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 60)
        except Exception as e:
            # For other unexpected errors, log the class name to avoid mega-tracebacks
            logger.error(f"AIS ingestion disrupted ({e.__class__.__name__}). Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 60)


async def _connect_and_consume():
    """Connect to aisstream.io WebSocket and consume messages."""
    # Focused bounding boxes around the 8 key oil tanker chokepoints
    # Format: [[top-left lat, top-left lon], [bottom-right lat, bottom-right lon]]
    # Each box has ~2-3 degree padding beyond the chokepoint polygon for approach corridors
    subscribe_msg = {
        "APIKey": settings.AIS_API_KEY,
        "BoundingBoxes": [
            # Strait of Hormuz (incl. Gulf of Oman approach + Persian Gulf)
            [[27.5, 54.0], [22.0, 60.5]],
            # Strait of Malacca (Singapore to Andaman Sea)
            [[7.0, 98.0], [0.5, 105.5]],
            # Suez Canal (incl. Red Sea north + Med south)
            [[32.0, 31.5], [27.0, 34.0]],
            # Bab el-Mandeb (Gulf of Aden + southern Red Sea)
            [[14.0, 41.0], [10.5, 46.5]],
            # Turkish Straits / Bosphorus (Black Sea mouth + Marmara Sea)
            [[42.0, 27.5], [40.0, 30.5]],
            # Danish Straits (Baltic Sea exits)
            [[57.5, 9.5], [54.0, 14.5]],
            # Cape of Good Hope (southern Africa rounding)
            [[-32.0, 16.5], [-37.0, 21.5]],
            # Panama Canal (Pacific + Caribbean approaches)
            [[10.5, -80.5], [7.5, -77.5]],
        ],
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }

    logger.info(f"Connecting to AIS stream at {settings.AIS_WS_URL}...")
    key_masked = settings.AIS_API_KEY[:4] + "***" + settings.AIS_API_KEY[-4:] if len(settings.AIS_API_KEY) > 8 else "***"
    logger.info(f"Using API Key: {key_masked}")

    async with websockets.connect(
        settings.AIS_WS_URL, 
        open_timeout=30
    ) as ws:
        await ws.send(json.dumps(subscribe_msg))
        logger.info("✅ Connected to aisstream.io — receiving AIS data")

        async for raw_msg in ws:
            try:
                msg = json.loads(raw_msg)
                await _process_message(msg)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from AIS stream")
            except Exception as e:
                logger.error(f"Error processing AIS message: {e}")


async def _process_message(msg: dict):
    """Process an incoming AIS message."""
    msg_type = msg.get("MessageType")

    if msg_type == "PositionReport":
        await _handle_position_report(msg)
    elif msg_type == "ShipStaticData":
        await _handle_static_data(msg)


async def _handle_position_report(msg: dict):
    """Handle an AIS position report — insert position and update vessel."""
    meta = msg.get("MetaData", {})
    position = msg.get("Message", {}).get("PositionReport", {})
    if not position or not meta:
        return

    mmsi = int(meta.get("MMSI", 0))
    if mmsi == 0:
        return

    # Extract ship type from metadata if available
    ship_type = meta.get("ShipType", None)

    # ── Strict Whitelist Filter ──
    # Only process if it's already a known tanker OR this message explicitly confirms it is one
    if mmsi not in _known_tankers:
        if ship_type is not None and ship_type in TANKER_TYPES:
            _known_tankers.add(mmsi)
            logger.info(f"Added new tanker to whitelist from position metadata: MMSI {mmsi}")
        else:
            # Unknown vessel or confirmed non-tanker -> Ignore completely
            return
    elif ship_type is not None and ship_type not in TANKER_TYPES:
        # Edge case: It was in our whitelist but is now reporting as a non-tanker
        _known_tankers.discard(mmsi)
        logger.info(f"Removed MMSI {mmsi} from whitelist (type changed to {ship_type})")
        return

    lat = position.get("Latitude", 0)
    lon = position.get("Longitude", 0)
    speed = position.get("Sog", None)  # Speed over ground
    course = position.get("Cog", None)  # Course over ground
    heading = position.get("TrueHeading", None)
    nav_status = position.get("NavigationalStatus", None)

    # Validate coordinates
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return

    now = datetime.now(timezone.utc)
    timestamp_str = meta.get("time_utc", None)
    if timestamp_str:
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            timestamp = now
    else:
        timestamp = now

    async with _batch_lock:
        v_data = _vessel_batch.setdefault(mmsi, {"mmsi": mmsi})
        v_data["last_seen"] = timestamp
        v_data["last_lat"] = lat
        v_data["last_lon"] = lon
        v_data["last_speed"] = speed
        v_data["last_course"] = course
        name_val = meta.get("ShipName", "").strip()
        if name_val:
            v_data["name"] = name_val
        if ship_type is not None:
            v_data["ship_type"] = ship_type

    # ── Smart Position Save Logic ──
    last_saved = _last_saved_positions.get(mmsi)
    save_position = False

    if not last_saved:
        save_position = True
    else:
        time_diff = (timestamp - last_saved["time"]).total_seconds() / 60.0
        dist_km = haversine_km(last_saved["lat"], last_saved["lon"], lat, lon)

        # Save if moved more than 10km or more than 30 minutes have passed
        if dist_km > 10.0 or time_diff > 1.0 or time_diff < 0: # time_diff < 0 handles clock skew/resets
            save_position = True

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
                "position": f"ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography",
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
        "timestamp": timestamp.isoformat(),
        "update_ui_trail": update_ui_trail
    })

async def _handle_static_data(msg: dict):
    """Handle AIS static/voyage data — update vessel registry."""
    meta = msg.get("MetaData", {})
    static = msg.get("Message", {}).get("ShipStaticData", {})
    if not static or not meta:
        return

    mmsi = int(meta.get("MMSI", 0))
    if mmsi == 0:
        return

    ship_type = static.get("Type", None)

    # Only process tankers
    if ship_type is not None and ship_type not in TANKER_TYPES:
        if mmsi in _known_tankers:
            _known_tankers.remove(mmsi)
            logger.info(f"Removed MMSI {mmsi} from whitelist (static data confirmed type {ship_type})")
        return

    # If it is a tanker, explicitly add it to the whitelist so we start tracking its positions
    if ship_type is not None and ship_type in TANKER_TYPES and mmsi not in _known_tankers:
        _known_tankers.add(mmsi)
        logger.info(f"Added new tanker to whitelist from static data: MMSI {mmsi}")

    dimension = static.get("Dimension", {})
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
    callsign = static.get("CallSign", "").strip() or None
    destination = static.get("Destination", "").strip() or None
    name = static.get("Name", "").strip() or meta.get("ShipName", "").strip() or None

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
        if draft is not None: v_data["draft"] = draft
        if destination: v_data["destination"] = destination
        if eta is not None: v_data["eta"] = eta

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
                    set_=set_clause
                )
                await session.execute(upsert_stmt)
                
            # 2. Bulk Insert Positions
            if current_positions:
                for p in current_positions:
                    p["position"] = text(p["position"])
                
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
    while True:
        try:
            await asyncio.sleep(2)
            await flush_batch()
        except asyncio.CancelledError:
            logger.info("Batch flush task cancelled")
            # Final flush before exit
            await flush_batch()
            break
        except Exception as e:
            logger.error(f"Flush task error: {e}")
