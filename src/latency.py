"""
Latency instrumentation for every pipeline stage.
"""
import time
import statistics
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional


@dataclass
class LatencySample:
    stage: str
    elapsed_ms: float
    timestamp: float = field(default_factory=time.time)


class LatencyTracker:
    """Thread-safe rolling latency tracker for pipeline stages."""

    def __init__(self, window: int = 1000):
        self._window = window
        self._samples: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=window))

    @contextmanager
    def measure(self, stage: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._samples[stage].append(elapsed_ms)

    def record(self, stage: str, elapsed_ms: float):
        self._samples[stage].append(elapsed_ms)

    def stats(self, stage: str) -> Dict[str, float]:
        samples = list(self._samples.get(stage, []))
        if not samples:
            return {"count": 0, "mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
        sorted_s = sorted(samples)
        n = len(sorted_s)
        return {
            "count": n,
            "mean_ms": round(statistics.mean(sorted_s), 3),
            "p50_ms": round(sorted_s[int(n * 0.50)], 3),
            "p95_ms": round(sorted_s[int(n * 0.95)], 3),
            "p99_ms": round(sorted_s[min(int(n * 0.99), n - 1)], 3),
            "max_ms": round(sorted_s[-1], 3),
        }

    def all_stats(self) -> Dict[str, Dict[str, float]]:
        return {stage: self.stats(stage) for stage in self._samples}

    def summary_table(self) -> str:
        rows = ["| Stage | Count | Mean | p50 | p95 | p99 | Max |",
                "|-------|-------|------|-----|-----|-----|-----|"]
        for stage, s in sorted(self.all_stats().items()):
            rows.append(
                f"| {stage} | {s['count']} | {s['mean_ms']}ms | "
                f"{s['p50_ms']}ms | {s['p95_ms']}ms | {s['p99_ms']}ms | {s['max_ms']}ms |"
            )
        return "\n".join(rows)

    def append_csv_snapshot(self, path) -> None:
        """Append one row per tracked stage to a CSV file (creates header if new)."""
        import csv
        from datetime import datetime, timezone
        from pathlib import Path

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        headers = ["timestamp", "stage", "count", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"]
        write_header = not p.exists() or p.stat().st_size == 0
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        with open(p, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            if write_header:
                w.writeheader()
            for stage, s in sorted(self.all_stats().items()):
                if s["count"] == 0:
                    continue
                w.writerow({"timestamp": now, "stage": stage, **s})


# Module-level singleton used by all pipeline stages
tracker = LatencyTracker()
