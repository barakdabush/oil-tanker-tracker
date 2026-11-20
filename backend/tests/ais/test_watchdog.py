"""Unit tests for the Tier 2 host watchdog (ops/watchdog.py)."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "ops"))

import watchdog  # noqa: E402


class _FakeWatchdogGitHub:
    def __init__(self, existing_issue=None):
        self.existing_issue = existing_issue
        self.created = []
        self.commented = []

    def find_open_issue_by_fingerprint(self, _fp):
        return self.existing_issue

    def create_issue(self, title, body, labels):
        self.created.append((title, body, labels))
        return 101

    def add_comment(self, number, body):
        self.commented.append((number, body))
        return True


def test_evaluate_container_state():
    assert watchdog.evaluate_container_state("backend", None) == "container not found"
    assert watchdog.evaluate_container_state("backend", {"State": {"Running": True, "Health": {"Status": "healthy"}}}) is None
    assert "OOMKilled" in watchdog.evaluate_container_state("scheduler", {"State": {"Running": False, "OOMKilled": True, "ExitCode": 137}})
    assert "crash-looping" in watchdog.evaluate_container_state("backend", {"State": {"Running": True, "Restarting": True, "ExitCode": 1}})
    assert "unhealthy" in watchdog.evaluate_container_state("backend", {"State": {"Running": True, "Health": {"Status": "unhealthy"}}})
    # One-shot migrate service
    assert watchdog.evaluate_container_state("migrate", {"State": {"Running": False, "ExitCode": 0}}, is_oneshot=True) is None
    assert "exit_code=1" in watchdog.evaluate_container_state("migrate", {"State": {"Running": False, "ExitCode": 1}}, is_oneshot=True)


def test_process_signals_hysteresis_dedup_and_cooldown():
    sig = watchdog.FailureSignal(
        fingerprint=watchdog.fingerprint_for("service_down", "backend"),
        kind="service_down",
        service="backend",
        title="[backend] service is down",
        summary="Container backend check failed: not running",
        logs="Traceback...\nImportError: boom",
    )
    t0 = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
    client = _FakeWatchdogGitHub(existing_issue=None)

    # First failure: hysteresis suppresses issue creation
    state = watchdog.process_signals([sig], {}, client, t0)
    assert client.created == []
    assert state[sig.fingerprint]["consecutive"] == 1

    # Second consecutive failure: opens 1 issue
    state = watchdog.process_signals([sig], state, client, t0 + timedelta(minutes=2))
    assert len(client.created) == 1
    assert sig.fingerprint in client.created[0][1]

    # Third failure within 6h cooldown: suppressed
    client.existing_issue = 101
    state = watchdog.process_signals([sig], state, client, t0 + timedelta(minutes=4))
    assert len(client.created) == 1
    assert client.commented == []

    # After 6h cooldown: comments on existing issue instead of creating a duplicate
    state = watchdog.process_signals([sig], state, client, t0 + timedelta(hours=7))
    assert len(client.created) == 1
    assert len(client.commented) == 1
    assert client.commented[0][0] == 101

    # Recovery resets consecutive counter
    state = watchdog.process_signals([], state, client, t0 + timedelta(hours=7, minutes=2))
    assert state[sig.fingerprint]["consecutive"] == 0
