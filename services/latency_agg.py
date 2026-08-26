"""Bounded, allocation-free latency accumulators for hot-path telemetry.

Shared by services/kalshi/websocket.py's ingest queue-health metrics
(realtime data-plane investigation task I1) and kept outside the Kalshi
boundary package on purpose: nothing here interprets vendor data, and
tools/quality_audit's kalshi-contract-docs scanner rightly treats every
public method inside services/kalshi/ as a documented Kalshi operation.

Design constraints (services/observability/CHEATSHEET.md "Hot-path
impact"): O(1) per sample, a fixed label set, no per-event persistence.
The fixed log-spaced buckets are the cheapest way to bound a p95 when the
observability store only persists one scalar per metric per minute - the
design spec forbids judging latency on averages alone.
"""
import math

# (upper bound in seconds, bucket name), ascending. Anything above the last
# bound lands in BUCKET_OVERFLOW and is reported as a count rather than
# silently capped to a finite bound.
BUCKET_BOUNDS: tuple[tuple[float, str], ...] = (
    (0.001, "le_1ms"), (0.01, "le_10ms"), (0.1, "le_100ms"), (1.0, "le_1s"), (10.0, "le_10s"),
)
BUCKET_OVERFLOW = "gt_10s"
BUCKET_NAMES = tuple(name for _, name in BUCKET_BOUNDS) + (BUCKET_OVERFLOW,)


class LatencyAgg:
    """count/total/max - the whole per-class state, so recording a sample is
    three attribute updates and nothing else."""
    __slots__ = ("count", "total", "max")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.max = 0.0

    def add(self, value: float) -> None:
        self.count += 1
        self.total += value
        if value > self.max:
            self.max = value

    def snapshot(self, scale: float, unit: str) -> dict:
        """{"count", "avg_<unit>", "max_<unit>"} with values multiplied by
        `scale` (1.0 for seconds, 1000.0 for milliseconds). None, not 0,
        for the averages of an empty window - unknown over fabricated."""
        if not self.count:
            return {"count": 0, f"avg_{unit}": None, f"max_{unit}": None}
        return {
            "count": self.count,
            f"avg_{unit}": round(self.total / self.count * scale, 4),
            f"max_{unit}": round(self.max * scale, 4),
        }


def empty_buckets() -> dict[str, int]:
    return dict.fromkeys(BUCKET_NAMES, 0)


def bucket_for(seconds: float) -> str:
    for bound, name in BUCKET_BOUNDS:
        if seconds <= bound:
            return name
    return BUCKET_OVERFLOW


def p95_upper_bound(buckets: dict[str, int], count: int) -> float | None:
    """Smallest finite bucket bound at or above the 95th-percentile rank.
    None when nothing was sampled or the p95 sits in the open-ended
    overflow bucket (whose count is still reported, so that case stays
    visible rather than being capped to 10 s)."""
    if not count:
        return None
    target = math.ceil(0.95 * count)
    cumulative = 0
    for bound, name in BUCKET_BOUNDS:
        cumulative += buckets.get(name, 0)
        if cumulative >= target:
            return bound
    return None
