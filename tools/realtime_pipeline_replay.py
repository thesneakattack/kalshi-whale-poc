"""Deterministic replay/load harness for the Kalshi realtime data plane
(realtime data-plane investigation task I6, docs/superpowers/plans/
2026-08-25-realtime-data-plane-investigation.md).

A seeded discrete-event simulation of the production ingest topology -
reader -> bounded application queue -> one serial consumer
(services/kalshi/websocket.py) - driven by arrival and service-time
distributions measured in tasks I0-I2, so queue/worker/topology ideas can
be tested against the same workload without touching production code or
opening a socket. A virtual clock makes every run deterministic and fast
(~100k messages in well under a second).

Two stall scopes are modelled because they are different mechanisms with
different signatures in the I1/I2 metrics:

- ``consumer``: only the consumer is blocked (an inline REST await, a
  slow SQLite call on the worker's result path). The reader keeps
  draining the socket, so the APPLICATION queue grows.
- ``loop``: the whole event loop is blocked (synchronous work in the
  trading tick). The reader stops too, so nothing reaches the app queue;
  arrivals park in the ``websockets``/TCP side (``upstream``) and land in
  the app queue in one dump when the loop frees.

A reconnect models what run() actually does: the old consumer task is
cancelled and a fresh queue is created, so whatever was queued is lost,
and nothing is received during the backoff gap.

Topologies are pluggable (``Topology`` protocol) so task I10 can benchmark
competing designs on identical workloads; ``CriticalFirstTopology`` exists
here only to prove that pluggability, not as a recommendation.

Usage:
    python -m tools.realtime_pipeline_replay --preset measured_normal --json
    python -m tools.realtime_pipeline_replay --all
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import random
import sys
from collections import deque
from dataclasses import dataclass
from typing import Callable, Protocol

CRITICAL_KINDS = frozenset({"ticker", "fill", "position", "lifecycle"})
DEFAULT_QUEUE_CAPACITY = 20000  # services/kalshi/websocket.py _INGEST_QUEUE_MAX
_SUSTAINED_REMAINING_FRACTION = 0.05


# --- service-time models -----------------------------------------------------

class ServiceModel(Protocol):
    def sample(self, rng: random.Random) -> float: ...


@dataclass(frozen=True)
class Fixed:
    seconds: float

    def sample(self, rng: random.Random) -> float:
        return self.seconds


@dataclass(frozen=True)
class LogNormal:
    """Log-normal service time parameterised by its mean and p95 (the two
    figures the observability CHEATSHEET records per stage), solved for
    mu/sigma. If the requested tail is heavier than a log-normal can carry
    for that mean, sigma is clamped at the p95 z-score and the mean drifts
    upward - deliberately conservative for a capacity model."""
    mean_sec: float
    p95_sec: float

    def _params(self) -> tuple[float, float]:
        m, q = math.log(self.mean_sec), math.log(self.p95_sec)
        z = 1.6448536269514722
        disc = z * z - 2.0 * (q - m)
        sigma = z - math.sqrt(disc) if disc > 0 else z
        return m - sigma * sigma / 2.0, sigma

    def sample(self, rng: random.Random) -> float:
        mu, sigma = self._params()
        return rng.lognormvariate(mu, sigma)


# --- workload description ----------------------------------------------------

@dataclass(frozen=True)
class Burst:
    start: float
    end: float
    multiplier: float  # applied to the trade rate only


@dataclass(frozen=True)
class Stall:
    at: float
    duration: float
    scope: str = "consumer"  # "consumer" | "loop"


@dataclass(frozen=True)
class Enrichment:
    probability: float  # fraction of trades that need a market lookup
    latency_sec: float  # inline await added to that trade's service time


@dataclass(frozen=True)
class Reconnect:
    at: float
    gap_sec: float


@dataclass(frozen=True)
class Msg:
    seq: int
    kind: str
    arrival: float
    service: float
    critical: bool


class Workload:
    def __init__(self, seed: int, duration_sec: float, rates: dict[str, float],
                 service: dict[str, ServiceModel], bursts=(), stalls=(),
                 enrichment: Enrichment | None = None, reconnect: Reconnect | None = None):
        self.seed = seed
        self.duration_sec = float(duration_sec)
        self.rates = dict(rates)
        self.service = dict(service)
        self.bursts = tuple(bursts)
        self.stalls = tuple(stalls)
        self.enrichment = enrichment
        self.reconnect = reconnect

    def _multiplier(self, kind: str, t: float) -> float:
        if kind != "trade":
            return 1.0
        mult = 1.0
        for burst in self.bursts:
            if burst.start <= t < burst.end:
                mult *= burst.multiplier
        return mult

    def messages(self) -> list[Msg]:
        rng = random.Random(self.seed)
        raw: list[tuple[float, str, float]] = []
        for kind in sorted(self.rates):  # fixed order keeps the stream deterministic per seed
            rate = float(self.rates[kind])
            model = self.service.get(kind) or Fixed(0.001)
            t = 0.0
            while rate > 0:
                t += rng.expovariate(rate * self._multiplier(kind, t))
                if t >= self.duration_sec:
                    break
                service = model.sample(rng)
                if kind == "trade" and self.enrichment and rng.random() < self.enrichment.probability:
                    service += self.enrichment.latency_sec
                raw.append((t, kind, service))
        raw.sort(key=lambda r: (r[0], r[1]))
        return [Msg(i, kind, t, service, kind in CRITICAL_KINDS) for i, (t, kind, service) in enumerate(raw)]


# --- topologies --------------------------------------------------------------

class Topology(Protocol):
    name: str
    capacity: int

    def enqueue(self, msg: Msg, now: float) -> bool: ...
    def pop(self) -> tuple[Msg, float]: ...
    def size(self) -> int: ...
    def head_enqueued_at(self) -> float | None: ...
    def clear(self) -> int: ...


class SingleQueueTopology:
    """The production topology: one bounded FIFO, one serial consumer."""
    name = "single_queue"

    def __init__(self, capacity: int = DEFAULT_QUEUE_CAPACITY):
        self.capacity = capacity
        self._q: deque[tuple[Msg, float]] = deque()

    def enqueue(self, msg: Msg, now: float) -> bool:
        if len(self._q) >= self.capacity:
            return False
        self._q.append((msg, now))
        return True

    def pop(self) -> tuple[Msg, float]:
        return self._q.popleft()

    def size(self) -> int:
        return len(self._q)

    def head_enqueued_at(self) -> float | None:
        return self._q[0][1] if self._q else None

    def clear(self) -> int:
        n = len(self._q)
        self._q.clear()
        return n


class CriticalFirstTopology:
    """Two FIFOs under one shared capacity, critical kinds served first.
    Here only to prove the harness compares designs - not a selection."""
    name = "critical_first"

    def __init__(self, capacity: int = DEFAULT_QUEUE_CAPACITY):
        self.capacity = capacity
        self._critical: deque[tuple[Msg, float]] = deque()
        self._bulk: deque[tuple[Msg, float]] = deque()

    def enqueue(self, msg: Msg, now: float) -> bool:
        if self.size() >= self.capacity:
            return False
        (self._critical if msg.critical else self._bulk).append((msg, now))
        return True

    def pop(self) -> tuple[Msg, float]:
        return (self._critical or self._bulk).popleft()

    def size(self) -> int:
        return len(self._critical) + len(self._bulk)

    def head_enqueued_at(self) -> float | None:
        heads = [q[0][1] for q in (self._critical, self._bulk) if q]
        return min(heads) if heads else None

    def clear(self) -> int:
        n = self.size()
        self._critical.clear()
        self._bulk.clear()
        return n


TOPOLOGIES: dict[str, Callable[[], Topology]] = {
    "single_queue": SingleQueueTopology,
    "critical_first": CriticalFirstTopology,
}


# --- the simulation ----------------------------------------------------------

_ARRIVAL, _DONE, _STALL_START, _STALL_END, _RECONNECT = 0, 1, 2, 3, 4


def _stats(samples: list[float]) -> dict:
    if not samples:
        return {"count": 0, "avg_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}
    s = sorted(samples)
    n = len(s)

    def pct(q: float) -> float:
        return s[min(n - 1, int(q * (n - 1)))]

    return {
        "count": n,
        "avg_ms": round(sum(s) / n * 1000.0, 3),
        "p50_ms": round(pct(0.50) * 1000.0, 3),
        "p95_ms": round(pct(0.95) * 1000.0, 3),
        "p99_ms": round(pct(0.99) * 1000.0, 3),
        "max_ms": round(s[-1] * 1000.0, 3),
    }


def simulate(workload: Workload, topology: Topology) -> dict:
    msgs = workload.messages()
    events: list[tuple[float, int, int, object]] = []
    for m in msgs:
        heapq.heappush(events, (m.arrival, _ARRIVAL, m.seq, m))
    for i, stall in enumerate(workload.stalls):
        heapq.heappush(events, (stall.at, _STALL_START, i, stall))
        heapq.heappush(events, (stall.at + stall.duration, _STALL_END, i, stall))
    if workload.reconnect is not None:
        heapq.heappush(events, (workload.reconnect.at, _RECONNECT, 0, workload.reconnect))

    received_by_kind: dict[str, int] = {}
    processed_by_kind: dict[str, int] = {}
    dropped_by_kind: dict[str, int] = {}
    waits: dict[str, list[float]] = {}        # enqueue -> service start (what I1's queue_wait sees)
    latencies: dict[str, list[float]] = {}    # arrival -> service start (the truth; loop stalls hide here)
    critical_waits: list[float] = []
    critical_latencies: list[float] = []
    loop_stall_end = 0.0
    queue_high_water = 0
    upstream_high_water = 0
    oldest_age_max = 0.0
    lost_on_reconnect = 0
    missed_during_reconnect = 0
    upstream: deque[Msg] = deque()
    consumer_busy_until: float | None = None
    stalled_consumer = 0
    stalled_loop = 0
    reconnect_gap_until: float | None = None

    def enqueue(msg: Msg, now: float) -> None:
        nonlocal queue_high_water
        if topology.enqueue(msg, now):
            queue_high_water = max(queue_high_water, topology.size())
        else:
            dropped_by_kind[msg.kind] = dropped_by_kind.get(msg.kind, 0) + 1

    def note_oldest(now: float) -> None:
        nonlocal oldest_age_max
        head = topology.head_enqueued_at()
        if head is not None:
            oldest_age_max = max(oldest_age_max, now - head)

    def try_start(now: float) -> None:
        nonlocal consumer_busy_until
        if consumer_busy_until is not None or stalled_consumer or stalled_loop or not topology.size():
            return
        msg, enqueued_at = topology.pop()
        wait = now - enqueued_at
        latency = now - msg.arrival
        waits.setdefault(msg.kind, []).append(wait)
        latencies.setdefault(msg.kind, []).append(latency)
        if msg.critical:
            critical_waits.append(wait)
            critical_latencies.append(latency)
        processed_by_kind[msg.kind] = processed_by_kind.get(msg.kind, 0) + 1
        consumer_busy_until = now + msg.service
        heapq.heappush(events, (consumer_busy_until, _DONE, msg.seq, msg))

    horizon = workload.duration_sec
    while events:
        now, etype, _, payload = heapq.heappop(events)
        if now > horizon:
            break  # measure the backlog AT the horizon - draining afterwards would hide it
        if etype == _ARRIVAL:
            msg = payload
            if reconnect_gap_until is not None and now < reconnect_gap_until:
                missed_during_reconnect += 1
                continue
            received_by_kind[msg.kind] = received_by_kind.get(msg.kind, 0) + 1
            if stalled_loop:
                upstream.append(msg)
                upstream_high_water = max(upstream_high_water, len(upstream))
            else:
                enqueue(msg, now)
                note_oldest(now)
                try_start(now)
        elif etype == _DONE:
            if stalled_loop:
                # A blocked loop cannot run the completion either - defer it.
                heapq.heappush(events, (max(now, loop_stall_end) + 1e-9, _DONE, payload.seq, payload))
                continue
            consumer_busy_until = None
            note_oldest(now)
            try_start(now)
        elif etype == _STALL_START:
            if payload.scope == "loop":
                stalled_loop += 1
                loop_stall_end = max(loop_stall_end, now + payload.duration)
            else:
                stalled_consumer += 1
        elif etype == _STALL_END:
            if payload.scope == "loop":
                stalled_loop -= 1
                if not stalled_loop:
                    while upstream:
                        enqueue(upstream.popleft(), now)
            else:
                stalled_consumer -= 1
            note_oldest(now)
            try_start(now)
        elif etype == _RECONNECT:
            lost_on_reconnect += topology.clear()
            reconnect_gap_until = now + payload.gap_sec
            upstream.clear()

    received = sum(received_by_kind.values())
    processed = sum(processed_by_kind.values())
    dropped = sum(dropped_by_kind.values())
    remaining_by_kind: dict[str, int] = {}
    while topology.size():
        msg, _ = topology.pop()
        remaining_by_kind[msg.kind] = remaining_by_kind.get(msg.kind, 0) + 1
    remaining = sum(remaining_by_kind.values())
    sustained = dropped == 0 and remaining <= _SUSTAINED_REMAINING_FRACTION * max(received, 1)
    return {
        "topology": topology.name,
        "duration_sec": workload.duration_sec,
        "received": received,
        "received_by_kind": received_by_kind,
        "processed": processed,
        "processed_by_kind": processed_by_kind,
        "dropped": dropped,
        "dropped_by_kind": dropped_by_kind,
        "remaining": remaining,
        "remaining_by_kind": remaining_by_kind,
        "lost_on_reconnect": lost_on_reconnect,
        "missed_during_reconnect": missed_during_reconnect,
        "queue_high_water": queue_high_water,
        "queue_capacity": topology.capacity,
        "upstream_high_water": upstream_high_water,
        "oldest_age_max_sec": round(oldest_age_max, 4),
        "throughput_per_sec": round(processed / workload.duration_sec, 2) if workload.duration_sec else None,
        "wait": {kind: _stats(samples) for kind, samples in sorted(waits.items())},
        "latency": {kind: _stats(samples) for kind, samples in sorted(latencies.items())},
        "critical_wait": _stats(critical_waits),
        "critical_latency": _stats(critical_latencies),
        "sustained": sustained,
    }


def sustainable_trade_rate(workload_for_rate: Callable[[float], Workload], topology_factory: Callable[[], Topology],
                           lo: float, hi: float, tolerance: float = 10.0) -> float:
    """Highest trade rate (binary search, msg/s) at which the topology stays
    'sustained' - no drops and the queue drained by the end of the run."""
    while hi - lo > tolerance:
        mid = (lo + hi) / 2.0
        if simulate(workload_for_rate(mid), topology_factory())["sustained"]:
            lo = mid
        else:
            hi = mid
    return lo


# --- measured presets --------------------------------------------------------
#
# Numbers from the I0 baseline (docs/superpowers/research/2026-08-25-realtime-
# data-plane-baseline.md section 6) and the I1/I2 windows recorded in
# services/observability/CHEATSHEET.md: trade-channel arrival p50 148 / p95
# 322 / max 383 msg/s; trade handler mean ~3.3 ms with a p95 near 10 ms
# (window p95); ticker handler ~2-15 ms; lifecycle ~2-6 ms; provider stalls
# of 3.8-4.9 s coinciding with 4-8 s ticks; inline market enrichment on
# ~0.25% of trades with a measured worst case of 1.1-4.3 s.

_TRADE_SERVICE = LogNormal(mean_sec=0.0033, p95_sec=0.010)
_TICKER_SERVICE = LogNormal(mean_sec=0.005, p95_sec=0.030)
_LIFECYCLE_SERVICE = LogNormal(mean_sec=0.002, p95_sec=0.006)
_ACCOUNT_SERVICE = Fixed(0.001)
_BASE_SERVICE = {"trade": _TRADE_SERVICE, "ticker": _TICKER_SERVICE, "lifecycle": _LIFECYCLE_SERVICE,
                 "fill": _ACCOUNT_SERVICE, "position": _ACCOUNT_SERVICE}
_BASE_RATES = {"trade": 148.0, "ticker": 5.0, "lifecycle": 2.0, "fill": 0.1}

PRESETS: dict[str, dict] = {
    "measured_normal": dict(duration_sec=120.0, rates=_BASE_RATES, service=_BASE_SERVICE),
    "measured_p95": dict(duration_sec=120.0, rates={**_BASE_RATES, "trade": 322.0}, service=_BASE_SERVICE),
    "measured_burst": dict(duration_sec=120.0, rates=_BASE_RATES, service=_BASE_SERVICE,
                           bursts=[Burst(start=30.0, end=50.0, multiplier=383.0 / 148.0)]),
    "mixed_trade_ticker": dict(duration_sec=120.0, rates={**_BASE_RATES, "ticker": 50.0}, service=_BASE_SERVICE),
    "mixed_critical": dict(duration_sec=120.0,
                           rates={"trade": 322.0, "ticker": 10.0, "lifecycle": 5.0, "fill": 2.0, "position": 2.0},
                           service=_BASE_SERVICE),
    "slow_handler": dict(duration_sec=120.0, rates=_BASE_RATES, service=_BASE_SERVICE,
                         stalls=[Stall(at=30.0, duration=4.0, scope="consumer"),
                                 Stall(at=70.0, duration=4.9, scope="consumer")]),
    "enrichment_stall": dict(duration_sec=120.0, rates=_BASE_RATES, service=_BASE_SERVICE,
                             enrichment=Enrichment(probability=0.0025, latency_sec=1.1)),
    "reconnect": dict(duration_sec=120.0, rates=_BASE_RATES, service=_BASE_SERVICE,
                      stalls=[Stall(at=37.0, duration=3.0, scope="consumer")],
                      reconnect=Reconnect(at=40.0, gap_sec=1.0)),
    "loop_stall": dict(duration_sec=120.0, rates=_BASE_RATES, service=_BASE_SERVICE,
                       stalls=[Stall(at=30.0, duration=4.0, scope="loop"),
                               Stall(at=70.0, duration=8.0, scope="loop")]),
}


def run_preset(name: str, topology: str = "single_queue", seed: int = 1) -> dict:
    spec = PRESETS[name]
    return simulate(Workload(seed=seed, **spec), TOPOLOGIES[topology]())


def _format_table(results: dict[str, dict]) -> str:
    lines = [f"{'preset':20s} {'recv':>7s} {'proc':>7s} {'drop':>6s} {'qhw':>6s} {'oldest':>8s} "
             f"{'trade p95':>10s} {'trade max':>10s} {'crit p95':>9s} {'sustained':>9s}"]
    for name, r in results.items():
        tw = r["wait"].get("trade") or _stats([])
        lines.append(
            f"{name:20s} {r['received']:7d} {r['processed']:7d} {r['dropped']:6d} {r['queue_high_water']:6d} "
            f"{r['oldest_age_max_sec']:8.2f} {(tw['p95_ms'] or 0):10.1f} {(tw['max_ms'] or 0):10.1f} "
            f"{(r['critical_wait']['p95_ms'] or 0):9.1f} {str(r['sustained']):>9s}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.realtime_pipeline_replay")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preset", choices=sorted(PRESETS))
    group.add_argument("--all", action="store_true")
    parser.add_argument("--topology", choices=sorted(TOPOLOGIES), default="single_queue")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.all:
        results = {name: run_preset(name, args.topology, args.seed) for name in PRESETS}
        print(json.dumps(results, indent=1) if args.json else _format_table(results))
    else:
        result = run_preset(args.preset, args.topology, args.seed)
        payload = {"preset": args.preset, "topology": args.topology, "seed": args.seed, "result": result}
        print(json.dumps(payload, indent=1) if args.json else _format_table({args.preset: result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
