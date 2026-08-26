"""Deterministic replay/load harness for the Kalshi realtime data plane
(realtime data-plane investigation tasks I6 and I10, docs/superpowers/plans/
2026-08-25-realtime-data-plane-investigation.md).

A seeded discrete-event simulation of the production ingest topology -
reader -> bounded application queue -> serial consumer(s)
(services/kalshi/websocket.py) - driven by arrival and service-time
distributions measured in tasks I0-I2/I7, so queue/worker/topology ideas
can be tested against the same workload without touching production code
or opening a socket. A virtual clock makes every run deterministic and
fast (~100k messages in well under a second).

Two stall scopes are modelled because they are different mechanisms with
different signatures in the I1/I2 metrics:

- ``consumer``: only the consumer serving trades is blocked (an inline REST
  await, a slow SQLite call on the worker's result path). The reader keeps
  draining the socket, so the APPLICATION queue grows.
- ``loop``: the whole event loop is blocked (synchronous work in the
  trading tick). The reader and every consumer stop, so nothing reaches
  the app queue; arrivals park in the ``websockets``/TCP side
  (``upstream``) and land in the app queue in one dump when the loop frees.

A reconnect models what run() actually does today: the old consumer task is
cancelled and a fresh queue is created, so whatever was queued is lost, and
nothing is received during the backoff gap (``Reconnect.discard_queue``
lets a candidate design keep the queue instead).

Topologies are pluggable (``Topology`` protocol) so candidate designs are
benchmarked on identical workloads (I10). The candidates here are the
families the I9 research judged credible for the confirmed bottlenecks;
none of them is a selection.

Usage:
    python -m tools.realtime_pipeline_replay --preset measured_normal --json
    python -m tools.realtime_pipeline_replay --all
    python -m tools.realtime_pipeline_replay --compare --presets measured_p95,busy_hour
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import random
import sys
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable, Protocol

CRITICAL_KINDS = frozenset({"ticker", "fill", "position", "lifecycle"})
DEFAULT_QUEUE_CAPACITY = 20000  # services/kalshi/websocket.py _INGEST_QUEUE_MAX
_SUSTAINED_REMAINING_FRACTION = 0.05
_RECOVERY_THRESHOLD = 10  # queued messages at which a stall counts as recovered


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
    scope: str = "consumer"  # "consumer" (the trade-serving consumer) | "loop" (everything)


@dataclass(frozen=True)
class Enrichment:
    probability: float  # fraction of trades that need a market lookup
    latency_sec: float  # inline await added to that trade's service time


@dataclass(frozen=True)
class Reconnect:
    at: float
    gap_sec: float
    discard_queue: bool = True  # today's run() behaviour; False models keeping the queue


@dataclass(frozen=True)
class Msg:
    seq: int
    kind: str
    arrival: float
    service: float
    critical: bool
    key: str | None = None      # market key for ordering / coalescing
    candidate: bool = False     # whale-sized trade (the decision path)


def periodic_stalls(every: float, duration: float, scope: str, start: float, until: float) -> list[Stall]:
    return [Stall(at=t, duration=duration, scope=scope) for t in _frange(start, until, every)]


def _frange(start: float, stop: float, step: float) -> list[float]:
    out, t = [], start
    while t < stop:
        out.append(round(t, 6))
        t += step
    return out


class Workload:
    def __init__(self, seed: int, duration_sec: float, rates: dict[str, float],
                 service: dict[str, ServiceModel], bursts=(), stalls=(),
                 enrichment: Enrichment | None = None, reconnect: Reconnect | None = None,
                 candidate_fraction: float = 0.0, candidate_service: ServiceModel | None = None,
                 keys: int = 200):
        self.seed = seed
        self.duration_sec = float(duration_sec)
        self.rates = dict(rates)
        self.service = dict(service)
        self.bursts = tuple(bursts)
        self.stalls = tuple(stalls)
        self.enrichment = enrichment
        self.reconnect = reconnect
        self.candidate_fraction = float(candidate_fraction)
        self.candidate_service = candidate_service
        self.keys = int(keys)

    def _multiplier(self, kind: str, t: float) -> float:
        if kind != "trade":
            return 1.0
        mult = 1.0
        for burst in self.bursts:
            if burst.start <= t < burst.end:
                mult *= burst.multiplier
        return mult

    def without_loop_stalls(self) -> "Workload":
        """The same workload with every loop-scope stall removed - the
        'loop hygiene' variant compare() runs beside the measured one."""
        clone = Workload.__new__(Workload)
        clone.__dict__.update(self.__dict__)
        clone.stalls = tuple(s for s in self.stalls if s.scope != "loop")
        return clone

    def keeping_queue_on_reconnect(self) -> "Workload":
        """The same workload with reconnects keeping the queued backlog
        instead of discarding it (the B4 candidate)."""
        clone = Workload.__new__(Workload)
        clone.__dict__.update(self.__dict__)
        if self.reconnect is not None:
            clone.reconnect = replace(self.reconnect, discard_queue=False)
        return clone

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
        # Keys and candidate flags come from a second generator so the base
        # arrival/service streams (and every I6 baseline number) stay
        # byte-identical whether or not a workload uses them.
        aux = random.Random(self.seed ^ 0x9E3779B9)
        msgs: list[Msg] = []
        for i, (t, kind, service) in enumerate(raw):
            key = f"K{aux.randrange(self.keys)}" if kind in ("trade", "ticker") else None
            candidate = False
            if kind == "trade" and self.candidate_fraction > 0.0 and aux.random() < self.candidate_fraction:
                candidate = True
                if self.candidate_service is not None:
                    service = self.candidate_service.sample(aux)
            msgs.append(Msg(i, kind, t, service, kind in CRITICAL_KINDS, key, candidate))
        return msgs

    def fingerprint(self) -> str:
        h = hashlib.sha1()
        for m in self.messages():
            h.update(f"{m.seq}|{m.kind}|{m.arrival:.6f}|{m.service:.9f}|{m.key}|{int(m.candidate)}\n".encode())
        return h.hexdigest()[:16]


# --- topologies --------------------------------------------------------------

QUEUED, DROPPED, COALESCED, PREFILTERED = "queued", "dropped", "coalesced", "prefiltered"


class Topology(Protocol):
    name: str
    capacity: int
    prefilter_cost_sec: float

    def groups(self) -> tuple[str, ...]: ...
    def trade_group(self) -> str: ...
    def enqueue(self, msg: Msg, now: float) -> str: ...
    def pop(self, group: str) -> tuple[Msg, float]: ...
    def size(self, group: str | None = None) -> int: ...
    def head_enqueued_at(self, group: str | None = None) -> float | None: ...
    def clear(self) -> int: ...


class _Entry:
    __slots__ = ("msg", "enqueued_at", "dead")

    def __init__(self, msg: Msg, enqueued_at: float):
        self.msg = msg
        self.enqueued_at = enqueued_at
        self.dead = False


class _Fifo:
    """Bounded FIFO with optional per-key coalescing (latest wins)."""

    def __init__(self, capacity: int, coalesce: bool = False):
        self.capacity = capacity
        self.coalesce = coalesce
        self._q: deque[_Entry] = deque()
        self._live = 0
        self._by_key: dict[str, _Entry] = {}

    def push(self, msg: Msg, now: float) -> str:
        if self.coalesce and msg.key is not None:
            old = self._by_key.get(msg.key)
            if old is not None and not old.dead:
                old.dead = True
                self._live -= 1
                entry = _Entry(msg, now)
                self._q.append(entry)
                self._by_key[msg.key] = entry
                self._live += 1
                return COALESCED
        if self._live >= self.capacity:
            return DROPPED
        entry = _Entry(msg, now)
        self._q.append(entry)
        self._live += 1
        if self.coalesce and msg.key is not None:
            self._by_key[msg.key] = entry
        return QUEUED

    def _skip_dead(self) -> None:
        while self._q and self._q[0].dead:
            self._q.popleft()

    def pop(self) -> tuple[Msg, float]:
        self._skip_dead()
        entry = self._q.popleft()
        self._live -= 1
        if self.coalesce and entry.msg.key is not None and self._by_key.get(entry.msg.key) is entry:
            del self._by_key[entry.msg.key]
        return entry.msg, entry.enqueued_at

    def size(self) -> int:
        return self._live

    def head_enqueued_at(self) -> float | None:
        self._skip_dead()
        return self._q[0].enqueued_at if self._q else None

    def clear(self) -> int:
        n = self._live
        self._q.clear()
        self._by_key.clear()
        self._live = 0
        return n


class SingleQueueTopology:
    """The production topology: one bounded FIFO, one serial consumer."""
    name = "single_queue"
    prefilter_cost_sec = 0.0

    def __init__(self, capacity: int = DEFAULT_QUEUE_CAPACITY):
        self.capacity = capacity
        self._q = _Fifo(capacity)

    def groups(self) -> tuple[str, ...]:
        return ("all",)

    def trade_group(self) -> str:
        return "all"

    def enqueue(self, msg: Msg, now: float) -> str:
        return self._q.push(msg, now)

    def pop(self, group: str) -> tuple[Msg, float]:
        return self._q.pop()

    def size(self, group: str | None = None) -> int:
        return self._q.size()

    def head_enqueued_at(self, group: str | None = None) -> float | None:
        return self._q.head_enqueued_at()

    def clear(self) -> int:
        return self._q.clear()


class CriticalFirstTopology:
    """Two FIFOs under one shared capacity, one consumer, critical kinds
    served first (I6's pluggability proof; a candidate for B3 in I10)."""
    name = "critical_first"
    prefilter_cost_sec = 0.0

    def __init__(self, capacity: int = DEFAULT_QUEUE_CAPACITY):
        self.capacity = capacity
        self._critical = _Fifo(capacity)
        self._bulk = _Fifo(capacity)

    def groups(self) -> tuple[str, ...]:
        return ("all",)

    def trade_group(self) -> str:
        return "all"

    def enqueue(self, msg: Msg, now: float) -> str:
        if self.size() >= self.capacity:
            return DROPPED
        return (self._critical if msg.critical else self._bulk).push(msg, now)

    def pop(self, group: str) -> tuple[Msg, float]:
        return (self._critical if self._critical.size() else self._bulk).pop()

    def size(self, group: str | None = None) -> int:
        return self._critical.size() + self._bulk.size()

    def head_enqueued_at(self, group: str | None = None) -> float | None:
        heads = [q.head_enqueued_at() for q in (self._critical, self._bulk) if q.size()]
        return min(heads) if heads else None

    def clear(self) -> int:
        return self._critical.clear() + self._bulk.clear()


_CLASS_GROUP = {"trade": "trade", "ticker": "ticker"}  # everything else -> "critical"


class ClassGroupTopology:
    """Per-class bounded queues, each with its own consumer: `trade`,
    `ticker` (optionally coalescing per key, latest wins) and `critical`
    (fill/position/lifecycle/control - never shed by class policy, only by
    its own capacity). Models separate consumers on one loop, which is also
    what physically separate connections give (I9 track A/B)."""
    name = "class_groups"
    prefilter_cost_sec = 0.0

    def __init__(self, capacity: int = DEFAULT_QUEUE_CAPACITY, coalesce_ticker: bool = False):
        self.capacity = capacity
        self.coalesce_ticker = coalesce_ticker
        if coalesce_ticker:
            self.name = "class_groups_coalesce"
        self._queues = {"trade": _Fifo(capacity), "ticker": _Fifo(capacity, coalesce=coalesce_ticker),
                        "critical": _Fifo(capacity)}

    def groups(self) -> tuple[str, ...]:
        return ("trade", "ticker", "critical")

    def trade_group(self) -> str:
        return "trade"

    def _group_of(self, msg: Msg) -> str:
        return _CLASS_GROUP.get(msg.kind, "critical")

    def enqueue(self, msg: Msg, now: float) -> str:
        return self._queues[self._group_of(msg)].push(msg, now)

    def pop(self, group: str) -> tuple[Msg, float]:
        return self._queues[group].pop()

    def size(self, group: str | None = None) -> int:
        if group is not None:
            return self._queues[group].size()
        return sum(q.size() for q in self._queues.values())

    def head_enqueued_at(self, group: str | None = None) -> float | None:
        queues = [self._queues[group]] if group is not None else list(self._queues.values())
        heads = [q.head_enqueued_at() for q in queues if q.size()]
        return min(heads) if heads else None

    def clear(self) -> int:
        return sum(q.clear() for q in self._queues.values())


class PrefilterTopology(SingleQueueTopology):
    """Reader-side cheap classifier: non-candidate trades are rejected at
    enqueue (I9 track A 'pre-filter' / track B family 1) for
    `prefilter_cost_sec` of reader time each; everything else is queued as
    in the production topology. Prefiltered prints are neither processed
    nor lost - they are the ~99.7% the consumer decides in microseconds
    today after paying a thread hop."""
    name = "prefilter"

    def __init__(self, capacity: int = DEFAULT_QUEUE_CAPACITY, prefilter_cost_sec: float = 0.000002):
        super().__init__(capacity)
        self.prefilter_cost_sec = prefilter_cost_sec

    def enqueue(self, msg: Msg, now: float) -> str:
        if msg.kind == "trade" and not msg.candidate:
            return PREFILTERED
        return super().enqueue(msg, now)


class StagedTopology(ClassGroupTopology):
    """Prefilter + per-class consumers + coalesced tickers - the combination
    of the in-process families, benchmarked as one candidate."""
    name = "staged"

    def __init__(self, capacity: int = DEFAULT_QUEUE_CAPACITY, prefilter_cost_sec: float = 0.000002):
        super().__init__(capacity, coalesce_ticker=True)
        self.name = "staged"
        self.prefilter_cost_sec = prefilter_cost_sec

    def enqueue(self, msg: Msg, now: float) -> str:
        if msg.kind == "trade" and not msg.candidate:
            return PREFILTERED
        return super().enqueue(msg, now)


TOPOLOGIES: dict[str, Callable[[], Topology]] = {
    "single_queue": SingleQueueTopology,
    "critical_first": CriticalFirstTopology,
    "class_groups": ClassGroupTopology,
    "class_groups_coalesce": lambda: ClassGroupTopology(coalesce_ticker=True),
    "prefilter": PrefilterTopology,
    "staged": StagedTopology,
}
CANDIDATES: tuple[str, ...] = tuple(TOPOLOGIES)


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

    groups = topology.groups()
    trade_group = topology.trade_group()
    received_by_kind: dict[str, int] = {}
    processed_by_kind: dict[str, int] = {}
    dropped_by_kind: dict[str, int] = {}
    waits: dict[str, list[float]] = {}        # enqueue -> service start (what I1's queue_wait sees)
    latencies: dict[str, list[float]] = {}    # arrival -> service start (the truth; loop stalls hide here)
    critical_waits: list[float] = []
    critical_latencies: list[float] = []
    candidate_latencies: list[float] = []
    busy_sec: dict[str, float] = dict.fromkeys(groups, 0.0)
    queue_high_water = 0
    upstream_high_water = 0
    oldest_age_max = 0.0
    lost_on_reconnect = 0
    missed_during_reconnect = 0
    coalesced = 0
    prefiltered = 0
    reader_busy_sec = 0.0
    ordering_violations = 0
    duplicate_processed = 0
    last_seq_by_key: dict[str, int] = {}
    processed_seqs: set[int] = set()
    upstream: deque[Msg] = deque()
    busy_until: dict[str, float | None] = dict.fromkeys(groups, None)
    stalled_consumer = 0
    stalled_loop = 0
    loop_stall_end = 0.0
    reconnect_gap_until: float | None = None
    pending_recovery: list[float] = []  # stall end times awaiting recovery
    stall_recovery: list[float] = []
    horizon = workload.duration_sec

    def enqueue(msg: Msg, now: float) -> None:
        nonlocal queue_high_water, coalesced, prefiltered, reader_busy_sec
        outcome = topology.enqueue(msg, now)
        if outcome == QUEUED:
            queue_high_water = max(queue_high_water, topology.size())
        elif outcome == COALESCED:
            coalesced += 1
        elif outcome == PREFILTERED:
            prefiltered += 1
            reader_busy_sec += topology.prefilter_cost_sec
        else:
            dropped_by_kind[msg.kind] = dropped_by_kind.get(msg.kind, 0) + 1

    def note_oldest(now: float) -> None:
        nonlocal oldest_age_max
        head = topology.head_enqueued_at()
        if head is not None:
            oldest_age_max = max(oldest_age_max, now - head)

    def note_recovery(now: float) -> None:
        while pending_recovery and topology.size() <= _RECOVERY_THRESHOLD:
            stall_recovery.append(round(now - pending_recovery.pop(0), 4))

    def consumer_stalled(group: str) -> bool:
        return bool(stalled_loop) or (bool(stalled_consumer) and group == trade_group)

    def try_start(group: str, now: float) -> None:
        nonlocal ordering_violations, duplicate_processed
        if busy_until[group] is not None or consumer_stalled(group) or not topology.size(group):
            return
        msg, enqueued_at = topology.pop(group)
        wait = now - enqueued_at
        latency = now - msg.arrival
        waits.setdefault(msg.kind, []).append(wait)
        latencies.setdefault(msg.kind, []).append(latency)
        if msg.critical:
            critical_waits.append(wait)
            critical_latencies.append(latency)
        if msg.candidate:
            candidate_latencies.append(latency)
        if msg.kind == "trade" and msg.key is not None:
            last = last_seq_by_key.get(msg.key)
            if last is not None and msg.seq < last:
                ordering_violations += 1
            last_seq_by_key[msg.key] = max(last or -1, msg.seq)
        if msg.seq in processed_seqs:
            duplicate_processed += 1
        processed_seqs.add(msg.seq)
        processed_by_kind[msg.kind] = processed_by_kind.get(msg.kind, 0) + 1
        busy_sec[group] += msg.service
        busy_until[group] = now + msg.service
        heapq.heappush(events, (busy_until[group], _DONE, msg.seq, (group, msg)))

    def try_start_all(now: float) -> None:
        for group in groups:
            try_start(group, now)

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
                try_start_all(now)
        elif etype == _DONE:
            group, msg = payload
            if stalled_loop:
                heapq.heappush(events, (max(now, loop_stall_end) + 1e-9, _DONE, msg.seq, payload))
                continue
            busy_until[group] = None
            note_oldest(now)
            note_recovery(now)
            try_start(group, now)
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
            pending_recovery.append(now)
            note_oldest(now)
            note_recovery(now)
            try_start_all(now)
        elif etype == _RECONNECT:
            if payload.discard_queue:
                lost_on_reconnect += topology.clear()
            reconnect_gap_until = now + payload.gap_sec
            upstream.clear()

    received = sum(received_by_kind.values())
    processed = sum(processed_by_kind.values())
    dropped = sum(dropped_by_kind.values())
    remaining_by_kind: dict[str, int] = {}
    for group in groups:
        while topology.size(group):
            msg, _ = topology.pop(group)
            remaining_by_kind[msg.kind] = remaining_by_kind.get(msg.kind, 0) + 1
    remaining = sum(remaining_by_kind.values())
    sustained = dropped == 0 and remaining <= _SUSTAINED_REMAINING_FRACTION * max(received, 1)
    duration = workload.duration_sec
    return {
        "topology": topology.name,
        "duration_sec": duration,
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
        "coalesced": coalesced,
        "prefiltered": prefiltered,
        "reader_busy_sec": round(reader_busy_sec, 6),
        "queue_high_water": queue_high_water,
        "queue_capacity": topology.capacity,
        "upstream_high_water": upstream_high_water,
        "oldest_age_max_sec": round(oldest_age_max, 4),
        "throughput_per_sec": round(processed / duration, 2) if duration else None,
        "wait": {kind: _stats(samples) for kind, samples in sorted(waits.items())},
        "latency": {kind: _stats(samples) for kind, samples in sorted(latencies.items())},
        "critical_wait": _stats(critical_waits),
        "critical_latency": _stats(critical_latencies),
        "candidate_latency": _stats(candidate_latencies),
        "ticker_latency": _stats(latencies.get("ticker", [])),
        "ordering_violations": ordering_violations,
        "duplicate_processed": duplicate_processed,
        "busy_fraction": {g: round(b / duration, 4) for g, b in busy_sec.items()} if duration else {},
        "stall_recovery_sec": stall_recovery,
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
# data-plane-baseline.md section 6), the I1/I2 windows recorded in
# services/observability/CHEATSHEET.md, and the I7 busy hour
# (2026-08-25-realtime-live-baseline.md): trade-channel arrival p50 148 / p95
# 322 / max 383 msg/s; trade handler mean ~3.3 ms (5.25 ms in the busy hour)
# with a p95 near 10 ms; ticker handler ~2-25 ms; lifecycle ~2-6 ms; provider
# stalls of 3.5-4.9 s in every busy-hour sample coinciding with 4-8 s ticks;
# inline market enrichment on ~0.25% of trades with a worst case of 1.1-4.3 s;
# whale candidates ~0.2-0.3% of trades with a ~30-70 ms decision path.

_TRADE_SERVICE = LogNormal(mean_sec=0.0033, p95_sec=0.010)
_TICKER_SERVICE = LogNormal(mean_sec=0.005, p95_sec=0.030)
_LIFECYCLE_SERVICE = LogNormal(mean_sec=0.002, p95_sec=0.006)
_ACCOUNT_SERVICE = Fixed(0.001)
_CANDIDATE_SERVICE = LogNormal(mean_sec=0.030, p95_sec=0.100)
_BASE_SERVICE = {"trade": _TRADE_SERVICE, "ticker": _TICKER_SERVICE, "lifecycle": _LIFECYCLE_SERVICE,
                 "fill": _ACCOUNT_SERVICE, "position": _ACCOUNT_SERVICE}
_BASE_RATES = {"trade": 148.0, "ticker": 5.0, "lifecycle": 2.0, "fill": 0.1}
_CANDIDATES = dict(candidate_fraction=0.0025, candidate_service=_CANDIDATE_SERVICE, keys=400)

PRESETS: dict[str, dict] = {
    "measured_normal": dict(duration_sec=120.0, rates=_BASE_RATES, service=_BASE_SERVICE, **_CANDIDATES),
    "measured_p95": dict(duration_sec=120.0, rates={**_BASE_RATES, "trade": 322.0}, service=_BASE_SERVICE, **_CANDIDATES),
    "measured_burst": dict(duration_sec=120.0, rates=_BASE_RATES, service=_BASE_SERVICE,
                           bursts=[Burst(start=30.0, end=50.0, multiplier=383.0 / 148.0)], **_CANDIDATES),
    "mixed_trade_ticker": dict(duration_sec=120.0, rates={**_BASE_RATES, "ticker": 50.0}, service=_BASE_SERVICE, **_CANDIDATES),
    "mixed_critical": dict(duration_sec=120.0,
                           rates={"trade": 322.0, "ticker": 10.0, "lifecycle": 5.0, "fill": 2.0, "position": 2.0},
                           service=_BASE_SERVICE, **_CANDIDATES),
    "slow_handler": dict(duration_sec=120.0, rates=_BASE_RATES, service=_BASE_SERVICE,
                         stalls=[Stall(at=30.0, duration=4.0, scope="consumer"),
                                 Stall(at=70.0, duration=4.9, scope="consumer")], **_CANDIDATES),
    "enrichment_stall": dict(duration_sec=120.0, rates=_BASE_RATES, service=_BASE_SERVICE,
                             enrichment=Enrichment(probability=0.0025, latency_sec=1.1), **_CANDIDATES),
    "reconnect": dict(duration_sec=120.0, rates=_BASE_RATES, service=_BASE_SERVICE,
                      stalls=[Stall(at=37.0, duration=3.0, scope="consumer")],
                      reconnect=Reconnect(at=40.0, gap_sec=1.0), **_CANDIDATES),
    "loop_stall": dict(duration_sec=120.0, rates=_BASE_RATES, service=_BASE_SERVICE,
                       stalls=[Stall(at=30.0, duration=4.0, scope="loop"),
                               Stall(at=70.0, duration=8.0, scope="loop")], **_CANDIDATES),
    # I7's busy hour, compressed to 10 minutes: 129 trades/s + 7 ticker/s +
    # 0.75 lifecycle/s with the per-class handler costs measured in that
    # window (trade window-avg p50 5.25 ms, ticker 25.2 ms, lifecycle
    # 22.9 ms), a ~4.5 s loop stall every 10 s (the per-sample provider
    # maxima), and one reconnect that discards the backlog (as run() does
    # today).
    "busy_hour": dict(duration_sec=600.0,
                      rates={"trade": 129.0, "ticker": 7.0, "lifecycle": 0.75, "fill": 0.1},
                      service={**_BASE_SERVICE, "trade": LogNormal(mean_sec=0.00525, p95_sec=0.015),
                               "ticker": LogNormal(mean_sec=0.0252, p95_sec=0.065),
                               "lifecycle": LogNormal(mean_sec=0.0229, p95_sec=0.060)},
                      stalls=periodic_stalls(every=10.0, duration=4.5, scope="loop", start=10.0, until=600.0),
                      reconnect=Reconnect(at=300.0, gap_sec=1.0), **_CANDIDATES),
}


def run_preset(name: str, topology: str = "single_queue", seed: int = 1) -> dict:
    spec = PRESETS[name]
    return simulate(Workload(seed=seed, **spec), TOPOLOGIES[topology]())


# --- candidate comparison (I10) --------------------------------------------------

VARIANTS: dict[str, Callable[[Workload], Workload]] = {
    "as_measured": lambda w: w,
    "loop_hygiene": lambda w: w.without_loop_stalls(),
    "keep_queue": lambda w: w.keeping_queue_on_reconnect(),
    "hygiene_keep_queue": lambda w: w.without_loop_stalls().keeping_queue_on_reconnect(),
}


def compare(presets: list[str], seed: int = 1, candidates: tuple[str, ...] = CANDIDATES,
            variants: tuple[str, ...] | None = None) -> dict:
    """Every candidate topology against every preset (and variant) on
    literally the same message stream - the fingerprint proves it. With no
    `variants`, results[candidate][preset] is a result; with variants,
    results[candidate][preset][variant]."""
    fingerprints = {}
    results: dict[str, dict] = {name: {} for name in candidates}
    for preset in presets:
        base = Workload(seed=seed, **PRESETS[preset])
        fingerprints[preset] = base.fingerprint()
        for name in candidates:
            if variants is None:
                results[name][preset] = simulate(base, TOPOLOGIES[name]())
            else:
                results[name][preset] = {
                    variant: simulate(VARIANTS[variant](base), TOPOLOGIES[name]()) for variant in variants
                }
    combined = hashlib.sha1("|".join(f"{p}:{h}" for p, h in sorted(fingerprints.items())).encode()).hexdigest()[:16]
    return {"seed": seed, "presets": list(presets), "candidates": list(candidates),
            "variants": list(variants) if variants else ["as_measured"],
            "workload_fingerprints": fingerprints, "workload_hash": combined, "results": results}


def _format_table(results: dict[str, dict]) -> str:
    lines = [f"{'preset':20s} {'recv':>7s} {'proc':>7s} {'drop':>6s} {'qhw':>6s} {'oldest':>8s} "
             f"{'cand p95':>9s} {'crit p95':>9s} {'sustained':>9s}"]
    for name, r in results.items():
        lines.append(
            f"{name:20s} {r['received']:7d} {r['processed']:7d} {r['dropped']:6d} {r['queue_high_water']:6d} "
            f"{r['oldest_age_max_sec']:8.2f} {(r['candidate_latency']['p95_ms'] or 0):9.1f} "
            f"{(r['critical_latency']['p95_ms'] or 0):9.1f} {str(r['sustained']):>9s}"
        )
    return "\n".join(lines)


def _format_matrix(matrix: dict) -> str:
    lines = []
    for preset in matrix["presets"]:
        lines.append(f"== {preset} (workload {matrix['workload_fingerprints'][preset]})")
        lines.append(f"{'candidate':22s} {'variant':13s} {'drop':>6s} {'qhw':>6s} {'oldest':>8s} {'cand p50':>9s} "
                     f"{'cand p95':>9s} {'cand p99':>9s} {'crit p95':>9s} {'order':>5s} {'coal':>6s} {'prefilt':>7s} {'sust':>5s}")
        for name in matrix["candidates"]:
            cell = matrix["results"][name][preset]
            per_variant = cell if any(v in cell for v in VARIANTS) else {"as_measured": cell}
            for variant, r in per_variant.items():
                c = r["candidate_latency"]
                lines.append(
                    f"{name:22s} {variant:13s} {r['dropped']:6d} {r['queue_high_water']:6d} {r['oldest_age_max_sec']:8.2f} "
                    f"{(c['p50_ms'] or 0):9.1f} {(c['p95_ms'] or 0):9.1f} {(c['p99_ms'] or 0):9.1f} "
                    f"{(r['critical_latency']['p95_ms'] or 0):9.1f} {r['ordering_violations']:5d} {r['coalesced']:6d} "
                    f"{r['prefiltered']:7d} {str(r['sustained'])[:1]:>5s}"
                )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.realtime_pipeline_replay")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preset", choices=sorted(PRESETS))
    group.add_argument("--all", action="store_true")
    group.add_argument("--compare", action="store_true")
    parser.add_argument("--presets", default="measured_p95,measured_burst,mixed_critical,loop_stall,reconnect,busy_hour")
    parser.add_argument("--variants", default="as_measured,loop_hygiene")
    parser.add_argument("--topology", choices=sorted(TOPOLOGIES), default="single_queue")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.compare:
        matrix = compare([p for p in args.presets.split(",") if p], seed=args.seed,
                         variants=tuple(v for v in args.variants.split(",") if v))
        print(json.dumps(matrix, indent=1) if args.json else _format_matrix(matrix))
    elif args.all:
        results = {name: run_preset(name, args.topology, args.seed) for name in PRESETS}
        print(json.dumps(results, indent=1) if args.json else _format_table(results))
    else:
        result = run_preset(args.preset, args.topology, args.seed)
        payload = {"preset": args.preset, "topology": args.topology, "seed": args.seed, "result": result}
        print(json.dumps(payload, indent=1) if args.json else _format_table({args.preset: result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
