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
    def __init__(self, state, closed_at):
        self._status = (state, closed_at)
        self.created_issues = []
        self.comments = []

    async def get_issue_status(self, _number):
        return self._status

    async def create_issue(self, title, body, labels):
        self.created_issues.append((title, body, labels))
        return 99

    async def add_comment(self, number, body):
        self.comments.append((number, body))
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

