"""Unit tests for the incident detector's pure logic (no DB / network)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.incident import Incident
from app.services.incident_detector import (
    build_issue_body,
    fingerprint_for,
    group_log_rows,
    normalize_message,
    sanitize_for_markdown,
    sanitize_title,
    should_comment,
    should_open_issue,
)


def _row(message, service="scheduler", logger_name="app.services.global_oil_feature_builder"):
    return SimpleNamespace(service=service, logger_name=logger_name, message=message)


def test_normalize_strips_volatile_parts_but_keeps_identifiers():
    a = normalize_message("Flush failed for 4213 rows at 2026-11-17T15:03:18Z from https://api.x.com/a?b=1")
    b = normalize_message("Flush failed for 17 rows at 2026-11-18T01:00:00+00:00 from https://api.x.com/zzz")
    assert a == b
    assert "<n>" in a and "<ts>" in a and "<url>" in a

    msg = "Feature aggregation error: type object 'AISGap' has no attribute 'gap_hours'"
    assert normalize_message(msg) == msg  # meaningful names are preserved


def test_normalize_uses_first_line_only():
    assert normalize_message("boom 1\nTraceback line 2") == "boom <n>"


def test_group_log_rows_dedupes_same_bug_and_skips_self_loggers():
    rows = [
        _row("Feature aggregation error: type object 'AISGap' has no attribute 'gap_hours'"),
        _row("Feature aggregation error: type object 'AISGap' has no attribute 'gap_hours'"),
        _row("IMF PortWatch fetch error: timeout after 60s", logger_name="app.services.portwatch_fetcher"),
        _row("IMF PortWatch fetch error: timeout after 61s", logger_name="app.services.portwatch_fetcher"),
        _row("Incident detector run failed", logger_name="app.services.incident_detector"),
    ]
    signals = group_log_rows(rows)
    assert len(signals) == 2
    counts = sorted(s.count for s in signals)
    assert counts == [2, 2]
    assert all(s.kind == "log_error" for s in signals)


def test_fingerprint_differs_by_service():
    assert fingerprint_for("log_error", "scheduler", "x", "m") != fingerprint_for("log_error", "backend", "x", "m")


def test_sanitize_neutralizes_fences_mentions_and_control_chars():
    out = sanitize_for_markdown("```evil``` @barakdabush \x07 ok", max_len=1000)
    assert "```" not in out
    assert "@barakdabush" not in out  # zero-width space inserted
    assert "\x07" not in out
    long = sanitize_for_markdown("a" * 50, max_len=10)
    assert long.startswith("a" * 10) and long.endswith("[truncated]")
    assert "\n" not in sanitize_title("line1\nline2")


def test_thresholds():
    assert not should_open_issue("log_error", 2, 3, 2)
    assert should_open_issue("log_error", 3, 3, 2)
    assert not should_open_issue("pipeline_stale", 1, 3, 2)
    assert should_open_issue("pipeline_stale", 2, 3, 2)


def test_should_comment_throttles():
    now = datetime.now(timezone.utc)
    assert not should_comment(0, None, now, 6)
    assert should_comment(1, None, now, 6)
    assert not should_comment(5, now - timedelta(hours=2), now, 6)
    assert should_comment(5, now - timedelta(hours=7), now, 6)


def test_issue_body_contains_metadata_and_fenced_sample():
    now = datetime(2026, 11, 17, 15, 3, tzinfo=timezone.utc)
    inc = Incident(
        fingerprint="abc123",
        kind="log_error",
        service="scheduler",
        title="[scheduler] boom",
        sample_message="type object 'AISGap' has no attribute 'gap_hours' ```",
        logger_name="app.services.global_oil_feature_builder",
        first_seen=now,
        last_seen=now,
        occurrences=5,
    )
    body = build_issue_body(inc, "http://1.2.3.4:3000/")
    assert "`abc123`" in body
    assert "| **Occurrences** | 5 |" in body
    assert "http://1.2.3.4:3000/system" in body
    # only our own two fences remain
    assert body.count("```") == 2


class _FakeScalarResult:
    def __init__(self, scalar_val=None, items=None):
        self._scalar = scalar_val
        self._items = items or []

    def scalar(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._items


class _FakeSession:
    def __init__(self, incidents):
        self._incidents = incidents
        self._calls = 0

    async def execute(self, _stmt):
        self._calls += 1
        if self._calls == 1:
            return _FakeScalarResult(scalar_val=0)
        return _FakeScalarResult(items=self._incidents)


class _FakeGitHubClient:
    def __init__(self, state, closed_at, existing_open_issue=None):
        self._status = (state, closed_at)
        self._existing_open_issue = existing_open_issue
        self.created_issues = []
        self.comments = []
        self.closed_issues = []

    async def get_issue_status(self, _number):
        return self._status

    async def find_open_issue_by_fingerprint(self, _fingerprint):
        return self._existing_open_issue

    async def create_issue(self, title, body, labels):
        self.created_issues.append((title, body, labels))
        return 99

    async def add_comment(self, number, body):
        self.comments.append((number, body))
        return True

    async def close_issue(self, number, comment=None):
        self.closed_issues.append((number, comment))
        return True


import pytest
from app.services.incident_detector import IncidentDetector


@pytest.mark.asyncio
async def test_notify_marks_resolved_when_last_seen_before_close():
    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    closed_at = now - timedelta(hours=4)
    inc = Incident(
        fingerprint="fp-old",
        kind="log_error",
        service="scheduler",
        title="[scheduler] old bug",
        sample_message="old error",
        logger_name="app.services.x",
        first_seen=now - timedelta(hours=10),
        last_seen=closed_at + timedelta(minutes=2),  # within 10m deploy window
        occurrences=15,
        occurrences_at_notify=3,
        status="issued",
        github_issue_number=17,
        issue_created_at=now - timedelta(hours=8),
        last_notified_at=now - timedelta(hours=7),
    )
    client = _FakeGitHubClient("closed", closed_at)
    detector = IncidentDetector()
    detector._client = client
    await detector._notify(_FakeSession([inc]), now)

    assert inc.status == "resolved"
    assert inc.occurrences_at_notify == 15
    assert client.created_issues == []


@pytest.mark.asyncio
async def test_notify_reopens_when_error_recurs_after_close():
    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    closed_at = now - timedelta(hours=4)
    inc = Incident(
        fingerprint="fp-recur",
        kind="log_error",
        service="scheduler",
        title="[scheduler] recurring bug",
        sample_message="new error",
        logger_name="app.services.x",
        first_seen=now - timedelta(hours=10),
        last_seen=closed_at + timedelta(minutes=30),  # genuinely recurred after close
        occurrences=10,
        occurrences_at_notify=3,
        status="issued",
        github_issue_number=17,
        issue_created_at=now - timedelta(hours=8),
        last_notified_at=now - timedelta(hours=7),
    )
    client = _FakeGitHubClient("closed", closed_at)
    detector = IncidentDetector()
    detector._client = client
    await detector._notify(_FakeSession([inc]), now)

    assert inc.status == "open"
    assert inc.github_issue_number is None
    assert inc.issue_created_at is None
    assert inc.occurrences == 7
    assert inc.occurrences_at_notify == 0


@pytest.mark.asyncio
async def test_notify_links_existing_open_issue_from_other_tier():
    now = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
    inc = Incident(
        fingerprint="fp-watchdog-opened",
        kind="service_down",
        service="backend",
        title="[backend] service is down",
        sample_message="Backend down",
        logger_name=None,
        first_seen=now - timedelta(minutes=20),
        last_seen=now,
        occurrences=2,
        occurrences_at_notify=0,
        status="open",
    )
    client = _FakeGitHubClient("open", None, existing_open_issue=42)
    detector = IncidentDetector()
    detector._client = client
    await detector._notify(_FakeSession([inc]), now)

    assert inc.status == "issued"
    assert inc.github_issue_number == 42
    assert client.created_issues == []


@pytest.mark.asyncio
async def test_build_system_health_detects_down_backend_and_stale_scheduler(monkeypatch):
    from unittest.mock import AsyncMock
    from app.api import system as system_api

    class _FakeRow:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _FakeExecResult:
        def __init__(self, first_row=None, all_rows=None):
            self._first = first_row
            self._all = all_rows or []

        def first(self):
            return self._first

        def all(self):
            return self._all

    now = datetime.now(timezone.utc)
    stale_time = now - timedelta(minutes=45)
    # 1: SELECT 1, 2-9: 8 pipeline queries, 10: log_stats
    results = [
        _FakeExecResult(),
        _FakeExecResult(first_row=_FakeRow(latest_time=now, count_24h=10)),
        _FakeExecResult(first_row=_FakeRow(latest_time=now, cnt=5)),
        _FakeExecResult(first_row=_FakeRow(latest_date=now.date(), latest_fetch=now, cnt=10)),
        _FakeExecResult(first_row=_FakeRow(latest_date=now.date(), latest_fetch=now, cnt=10)),
        _FakeExecResult(first_row=_FakeRow(latest_fetch=now, cnt=10)),
        _FakeExecResult(first_row=_FakeRow(latest_at=now, cnt=1)),
        _FakeExecResult(first_row=_FakeRow(latest_at=now, cnt=1)),
        _FakeExecResult(first_row=_FakeRow(latest_date=now.date(), cnt=1)),
        _FakeExecResult(all_rows=[_FakeRow(service="scheduler", last_log_at=stale_time, errors_1h=0, warnings_1h=0)]),
    ]
    db = SimpleNamespace(execute=AsyncMock(side_effect=results))
    monkeypatch.setattr(system_api, "_fetch_websocket_health", AsyncMock(return_value={"reachable": True, "data": {}}))
    monkeypatch.setattr(system_api, "_fetch_backend_health", AsyncMock(return_value=False))

    health = await system_api._build_system_health(db, check_backend=True)
    by_name = {s["name"]: s["status"] for s in health["services"]}
    assert by_name["backend"] == "down"
    assert by_name["scheduler"] == "down"
    assert by_name["websocket"] == "healthy"
    assert by_name["db"] == "healthy"


def test_warning_logs_grouped_and_thresholded():
    rows = [
        SimpleNamespace(
            level="WARNING",
            service="scheduler",
            logger_name="app.services.news_fetcher",
            message="NewsFetcher GDELT[chokepoints] rate-limited (429)",
        )
        for _ in range(4)
    ]
    signals = group_log_rows(rows)
    assert len(signals) == 1
    assert signals[0].kind == "log_warning"
    assert signals[0].count == 4
    assert not should_open_issue("log_warning", 4, 3, 2, 5)
    assert should_open_issue("log_warning", 5, 3, 2, 5)


def test_normalize_collapses_chokepoint_entity_ids():
    a = normalize_message("IMF PortWatch ArcGIS error for chokepoint2: {'code': 400, 'message': 'Invalid URL'}")
    b = normalize_message("IMF PortWatch ArcGIS error for chokepoint15: {'code': 400, 'message': 'Invalid URL'}")
    assert a == b
    assert "chokepoint<n>" in a


@pytest.mark.asyncio
async def test_notify_deduplicates_same_logger_in_batch_and_existing():
    now = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
    inc1 = Incident(
        fingerprint="fp-pw-1",
        kind="log_error",
        service="scheduler",
        title="[scheduler] IMF PortWatch ArcGIS error: Invalid URL",
        sample_message="Invalid URL",
        logger_name="app.services.portwatch_fetcher",
        first_seen=now - timedelta(minutes=5),
        last_seen=now,
        occurrences=3,
        occurrences_at_notify=0,
        status="open",
    )
    inc2 = Incident(
        fingerprint="fp-pw-2",
        kind="log_error",
        service="scheduler",
        title="[scheduler] IMF PortWatch ArcGIS error: Token Required",
        sample_message="Token Required",
        logger_name="app.services.portwatch_fetcher",
        first_seen=now - timedelta(minutes=4),
        last_seen=now,
        occurrences=3,
        occurrences_at_notify=0,
        status="open",
    )

    class _LoggerDedupClient(_FakeGitHubClient):
        def __init__(self):
            super().__init__("open", None, existing_open_issue=None)

        async def find_open_issue_for_logger(self, _service, _logger_name):
            return None

    client = _LoggerDedupClient()
    detector = IncidentDetector()
    detector._client = client
    await detector._notify(_FakeSession([inc1, inc2]), now)

    assert len(client.created_issues) == 1
    assert inc1.github_issue_number == 99
    assert inc2.github_issue_number == 99
    assert inc2.status == "issued"
    assert len(client.comments) == 1
    assert client.comments[0][0] == 99


def test_upstream_transient_errors_are_filtered_from_log_signals():
    from types import SimpleNamespace
    from app.services.incident_detector import group_log_rows, is_upstream_transient_error

    assert is_upstream_transient_error("AIS (aisstream.io) connection timeout/refused. Reconnecting in 5s...")
    assert is_upstream_transient_error("IMF PortWatch ArcGIS error: {'code': 429, 'message': 'Too many requests.'}")
    assert is_upstream_transient_error("HTTP 503 Service Unavailable")

    # Our own bugs / config errors must NOT be filtered
    assert not is_upstream_transient_error("IMF PortWatch ArcGIS error: {'code': 400, 'message': 'Invalid URL'}")
    assert not is_upstream_transient_error("IMF PortWatch ArcGIS error: {'code': 499, 'message': 'Token Required'}")
    assert not is_upstream_transient_error("AttributeError: 'NoneType' object has no attribute 'id'")

    rows = [
        SimpleNamespace(level="WARNING", service="websocket", logger_name="app.services.ais_ingestion", message="AIS (aisstream.io) connection timeout/refused. Reconnecting in 5s..."),
        SimpleNamespace(level="ERROR", service="scheduler", logger_name="app.services.portwatch_fetcher", message="IMF PortWatch ArcGIS error: {'code': 429, 'message': 'Unable to perform query. Too many requests.'}"),
        SimpleNamespace(level="ERROR", service="scheduler", logger_name="app.services.portwatch_fetcher", message="IMF PortWatch ArcGIS error: {'code': 400, 'message': 'Invalid URL'}"),
    ]
    signals = group_log_rows(rows)
    assert len(signals) == 1
    assert "Invalid URL" in signals[0].sample


def test_estimate_quiet_window_uses_dynamic_job_cadence():
    from app.services.incident_detector import estimate_quiet_window, should_auto_resolve

    now = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)

    # Known fast job (ais_ingestion 60s -> 4*60s=4m, clamped to MIN_QUIET_SECONDS=40m)
    ws_inc = Incident(
        fingerprint="fp-ws",
        kind="log_warning",
        service="websocket",
        title="ws",
        sample_message="msg",
        logger_name="app.services.ais_ingestion",
        first_seen=now - timedelta(hours=2),
        last_seen=now - timedelta(minutes=45),
        occurrences=5,
        status="issued",
        github_issue_number=76,
    )
    assert estimate_quiet_window(ws_inc) == timedelta(minutes=40)
    assert should_auto_resolve(ws_inc, now)

    # Known 6h job (portwatch_fetcher -> 4 * 6h = 24h)
    pw_inc = Incident(
        fingerprint="fp-pw",
        kind="log_error",
        service="scheduler",
        title="pw",
        sample_message="msg",
        logger_name="app.services.portwatch_fetcher",
        first_seen=now - timedelta(hours=30),
        last_seen=now - timedelta(hours=10),
        occurrences=3,
        status="issued",
        github_issue_number=77,
    )
    assert estimate_quiet_window(pw_inc) == timedelta(hours=24)
    assert not should_auto_resolve(pw_inc, now)

    # Unknown logger infers cadence from observed spacing: 2h apart -> 4 * 2h = 8h
    custom_inc = Incident(
        fingerprint="fp-custom",
        kind="log_error",
        service="backend",
        title="custom",
        sample_message="msg",
        logger_name="app.services.custom_job",
        first_seen=now - timedelta(hours=14),
        last_seen=now - timedelta(hours=10),
        occurrences=3,
        status="issued",
        github_issue_number=78,
    )
    assert estimate_quiet_window(custom_inc) == timedelta(hours=8)
    assert should_auto_resolve(custom_inc, now)


@pytest.mark.asyncio
async def test_notify_auto_closes_quiet_issue_after_4_rounds():
    now = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    inc = Incident(
        fingerprint="fp-quiet",
        kind="log_error",
        service="scheduler",
        title="[scheduler] news bug",
        sample_message="err",
        logger_name="app.services.news_fetcher",  # 30m cadence -> 4 rounds = 2h
        first_seen=now - timedelta(hours=5),
        last_seen=now - timedelta(hours=2, minutes=5),
        occurrences=4,
        occurrences_at_notify=4,
        status="issued",
        github_issue_number=88,
        issue_created_at=now - timedelta(hours=4),
        last_notified_at=now - timedelta(hours=4),
    )
    client = _FakeGitHubClient("open", None)
    detector = IncidentDetector()
    detector._client = client
    await detector._notify(_FakeSession([inc]), now)

    assert inc.status == "resolved"
    assert len(client.closed_issues) == 1
    assert client.closed_issues[0][0] == 88
    assert "Auto-resolved" in client.closed_issues[0][1]





