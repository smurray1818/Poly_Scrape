"""
Tests for the latency instrumentation module.
"""
import time

import pytest

from src.latency import LatencyTracker


def test_measure_context_manager():
    t = LatencyTracker()
    with t.measure("test_stage"):
        time.sleep(0.01)
    stats = t.stats("test_stage")
    assert stats["count"] == 1
    assert stats["mean_ms"] >= 10.0  # at least 10 ms


def test_multiple_samples():
    t = LatencyTracker()
    for _ in range(5):
        t.record("stage_a", 10.0)
    t.record("stage_a", 50.0)  # outlier
    stats = t.stats("stage_a")
    assert stats["count"] == 6
    assert stats["max_ms"] == 50.0
    assert stats["p95_ms"] >= 10.0


def test_empty_stage():
    t = LatencyTracker()
    stats = t.stats("nonexistent")
    assert stats["count"] == 0
    assert stats["mean_ms"] == 0.0


def test_window_rolling():
    t = LatencyTracker(window=5)
    for i in range(10):
        t.record("w", float(i))
    stats = t.stats("w")
    assert stats["count"] == 5  # only last 5 kept
    assert stats["max_ms"] == 9.0


def test_all_stats_multiple_stages():
    t = LatencyTracker()
    t.record("alpha", 1.0)
    t.record("beta", 2.0)
    all_s = t.all_stats()
    assert "alpha" in all_s
    assert "beta" in all_s


def test_summary_table_contains_stage():
    t = LatencyTracker()
    t.record("my_stage", 42.0)
    table = t.summary_table()
    assert "my_stage" in table
    assert "42.0" in table


def test_p99_bounded_by_max():
    t = LatencyTracker()
    for v in range(1, 101):
        t.record("s", float(v))
    stats = t.stats("s")
    assert stats["p99_ms"] <= stats["max_ms"]
    assert stats["p50_ms"] <= stats["p99_ms"]


@pytest.mark.asyncio
async def test_measure_async_context():
    """Verify measure() works correctly in an async context."""
    import asyncio
    t = LatencyTracker()
    with t.measure("async_stage"):
        await asyncio.sleep(0.005)
    stats = t.stats("async_stage")
    assert stats["count"] == 1
    assert stats["mean_ms"] >= 5.0
