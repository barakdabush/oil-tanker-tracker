"""Unit tests for BufferedDBLogHandler and system health freshness helpers."""

import logging
from datetime import datetime, timedelta, timezone

from app.api.system import _freshness_status
from app.services.db_log_handler import BufferedDBLogHandler


def _make_record(name: str, level: int, msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_db_log_handler_filters_noise_and_buffers_records():
    handler = BufferedDBLogHandler("scheduler")

    # Ignored loggers should be dropped
    handler.emit(_make_record("sqlalchemy.engine", logging.INFO, "SELECT 1"))
    handler.emit(_make_record("uvicorn.access", logging.INFO, "GET /api/health"))
    handler.emit(_make_record("app.services.db_log_handler", logging.ERROR, "internal"))

    # High-frequency INFO flush noise should be dropped
    handler.emit(_make_record("app.services.ais_ingestion", logging.INFO, "Flushed 42 positions to DB"))

    # Real application INFO / ERROR logs should be buffered
    handler.emit(_make_record("app.services.portwatch_fetcher", logging.INFO, "Synced 120 rows"))
    handler.emit(_make_record("app.services.oil_price_fetcher", logging.ERROR, "Yahoo Finance timeout"))

    batch = handler.drain()
    assert len(batch) == 2
    assert batch[0]["service"] == "scheduler"
    assert batch[0]["level"] == "INFO"
    assert batch[0]["logger_name"] == "app.services.portwatch_fetcher"
    assert batch[0]["message"] == "Synced 120 rows"

    assert batch[1]["level"] == "ERROR"
    assert batch[1]["message"] == "Yahoo Finance timeout"

    # Drain again should be empty
    assert handler.drain() == []


def test_freshness_status_thresholds():
    now = datetime.now(timezone.utc)
    assert _freshness_status(None, 24.0) == "empty"
    assert _freshness_status(now - timedelta(hours=2), 24.0) == "healthy"
    assert _freshness_status(now - timedelta(hours=30), 24.0) == "stale"

    # Naive datetime should also be handled safely
    naive_recent = datetime.utcnow() - timedelta(minutes=10)
    assert _freshness_status(naive_recent, 1.0) == "healthy"
