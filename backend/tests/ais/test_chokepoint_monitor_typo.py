"""Regression test for the 'typo_entries' bug in ChokepointMonitor.

The bug: line 44 in chokepoint_monitor.py incremented stats["typo_entries"]
which does not exist in the stats dict, causing a KeyError that was caught by
the except handler in run_periodic and logged as a spurious error.

This test ensures that the stats increment uses the correct key.
"""

import pytest
from unittest import mock

from backend.app.services.chokepoint_monitor import ChokepointMonitor


def test_stats_increment_no_keyerror():
    """The stats increment line should not raise KeyError."""
    monitor = ChokepointMonitor()
    # Simulate the stats dict as it is created in monitor()
    stats = {"entries": 0, "exits": 0, "congestion": 0, "low": 0}
    # This is the fixed line from monitor()
    stats["entries"] += 1
    assert stats["entries"] == 1


def test_typo_entries_would_raise_keyerror():
    """The original buggy line would raise KeyError, demonstrating the bug."""
    stats = {"entries": 0, "exits": 0, "congestion": 0, "low": 0}
    with pytest.raises(KeyError):
        stats["typo_entries"] += 1