"""Non-blocking buffered Python logging handler that persists logs to PostgreSQL."""

import asyncio
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import insert, text

from app.database import async_session
from app.models.system_log import SystemLog

MAX_BUFFERED_LOGS = 500
MAX_STORED_LOGS = 1500
FLUSH_INTERVAL_SECONDS = 3

_IGNORED_LOGGERS = (
    "sqlalchemy",
    "app.services.db_log_handler",
    "uvicorn.access",
)


class BufferedDBLogHandler(logging.Handler):
    """Thread-safe, non-blocking ring-buffered log handler."""

    def __init__(self, service_name: str):
        super().__init__(level=logging.INFO)
        self.service_name = service_name
        self._buffer: deque[dict] = deque(maxlen=MAX_BUFFERED_LOGS)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord):
        try:
            name = record.name or ""
            if any(name.startswith(prefix) for prefix in _IGNORED_LOGGERS):
                return

            msg = self.format(record) if self.formatter else record.getMessage()
            # Skip high-frequency flush noise at INFO level
            if record.levelno < logging.WARNING and "Flushed " in msg and "positions to DB" in msg:
                return

            entry = {
                "created_at": datetime.fromtimestamp(record.created, tz=timezone.utc),
                "service": self.service_name[:32],
                "level": record.levelname[:16],
                "logger_name": name[:128],
                "message": msg[:4000],
            }
            with self._lock:
                self._buffer.append(entry)
        except Exception:
            # Never raise from logging handler
            pass

    def drain(self) -> list[dict]:
        with self._lock:
            if not self._buffer:
                return []
            items = list(self._buffer)
            self._buffer.clear()
            return items


_handler_instance: Optional[BufferedDBLogHandler] = None
_flush_task: Optional[asyncio.Task] = None


async def _flush_loop(handler: BufferedDBLogHandler):
    tick = 0
    while True:
        try:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            batch = handler.drain()
            if not batch:
                continue

            async with async_session() as session:
                await session.execute(insert(SystemLog).values(batch))
                tick += 1
                if tick % 20 == 0:
                    await session.execute(
                        text(
                            "DELETE FROM system_logs "
                            "WHERE id < ("
                            "  SELECT id FROM system_logs ORDER BY id DESC OFFSET :keep LIMIT 1"
                            ")"
                        ),
                        {"keep": MAX_STORED_LOGS},
                    )
                await session.commit()
        except asyncio.CancelledError:
            # Best-effort final flush
            batch = handler.drain()
            if batch:
                try:
                    async with async_session() as session:
                        await session.execute(insert(SystemLog).values(batch))
                        await session.commit()
                except Exception:
                    pass
            break
        except Exception:
            # Avoid recursive logging on DB errors
            pass


def setup_db_logging(service_name: str) -> asyncio.Task:
    """Attach BufferedDBLogHandler to root logger and start background flush loop."""
    global _handler_instance, _flush_task

    if _handler_instance is None:
        _handler_instance = BufferedDBLogHandler(service_name)
        root_logger = logging.getLogger()
        root_logger.addHandler(_handler_instance)

    if _flush_task is None or _flush_task.done():
        _flush_task = asyncio.create_task(_flush_loop(_handler_instance))

    return _flush_task
