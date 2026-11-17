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
