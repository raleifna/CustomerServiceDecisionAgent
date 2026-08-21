"""Lightweight in-process counters for LLM API call health.

Not a metrics platform — no Prometheus, no persistence, no per-instance
aggregation. This project runs as a single backend instance, so an in-memory
snapshot answering "is the LLM dependency degrading right now" is enough;
a multi-instance deployment would need this shared (e.g. Redis) to mean
anything across processes.
"""
import threading
from typing import Any


class _CallTelemetry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._success = 0
        self._total_latency_ms = 0.0
        self._circuit_trips = 0

    def record(self, *, success: bool, latency_ms: float) -> None:
        with self._lock:
            self._total += 1
            if success:
                self._success += 1
            self._total_latency_ms += latency_ms

    def record_circuit_trip(self) -> None:
        with self._lock:
            self._circuit_trips += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            total = self._total
            success = self._success
            return {
                "total_calls": total,
                "success_count": success,
                "failure_count": total - success,
                "success_rate": round(success / total, 4) if total else None,
                "avg_latency_ms": (
                    round(self._total_latency_ms / total, 1) if total else None
                ),
                "circuit_breaker_trips": self._circuit_trips,
            }


telemetry = _CallTelemetry()
