"""REST scheduling and candidate-recovery replay (realtime data-plane
investigation task I11, docs/archive/lane-1-kalshi-ingestion/plans/2026-08-25-realtime-data-
plane-investigation.md).

Two deterministic simulators, no sockets, no databases:

1. ``simulate(demand, policy, upstream)`` - the REST side. Per-class demand
   shaped like the measured call sites (the tick's ``asyncio.gather``
   fan-outs every 6 s, catalog batches every 15 s, resolution batches
   every 30 s, Poisson whale enrichment), a local scheduling policy in
   front of one shared budget (the production shared FIFO token bucket and
   the I9 track-C families), and an upstream token bucket at the verified
   account budget that answers 429 when empty - retried with the production
   backoff (0.5/1/2/4 s, up to 4 retries) through the same scheduler.
2. ``simulate_recovery(scenario, policy)`` - the loss side. Whale candidates
   whose enrichment fails during transient windows, whose wire copy is lost
   or delayed during saturation windows, or whose pending state dies with a
   restart, under the current mark-seen-first policy and the I9 track-D
   families, scored on evaluated-once / lost / abandoned / duplicated /
   decision latency / extra REST calls.

Nothing here selects a design; ``--compare`` and ``--recovery`` print the
matrices docs/archive/lane-1-kalshi-ingestion/research/2026-08-25-rest-solution-comparison.md
is written from.
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

from tools.realtime_pipeline_replay import Fixed, LogNormal, ServiceModel  # noqa: F401 (re-exported for callers)

MAX_RETRIES = 4          # services/http_client.py call_with_backoff default
BASE_BACKOFF_SEC = 0.5   # ...and its base delay (jitter omitted for determinism)
COST_TOKENS = 10         # docs/kalshi: default endpoint cost for every endpoint this app calls
CRITICAL_PREFIX = "critical_"
_429_RTT_SEC = 0.02      # a 429 comes back fast


# --- demand ------------------------------------------------------------------------

@dataclass(frozen=True)
class ClassDemand:
    rate: float = 0.0            # Poisson arrivals per second
    burst_size: int = 0          # calls that fire at the same instant...
    burst_every: float = 0.0     # ...every this many seconds (a gather / batch)
    network: ServiceModel = Fixed(0.05)
    burst_phase: float = 0.0


@dataclass(frozen=True)
class Req:
    seq: int
    cls: str
    arrival: float
    network: float


# Same-instant tie-break mirrors the trading tick's real launch order
# (main.py): the catalog / resolution / backup / research tasks are spawned
# just before the critical gather (markets, account, exchange status), and
# live-status is fetched a phase later, after the market fetch. The I11
# harness broke ties alphabetically, which put live-status ahead of the
# position fetch - the whole source of its "375 ms position wait" finding
# (I12 review).
LAUNCH_ORDER = ("background_catalog", "background_resolution", "critical_position", "critical_whale",
                "background_live_status", "interactive", "other")


def _launch_rank(cls: str) -> int:
    return LAUNCH_ORDER.index(cls) if cls in LAUNCH_ORDER else len(LAUNCH_ORDER)


class Demand:
    def __init__(self, seed: int, duration_sec: float, classes: dict[str, ClassDemand]):
        self.seed = seed
        self.duration_sec = float(duration_sec)
        self.classes = dict(classes)

    def requests(self) -> list[Req]:
        rng = random.Random(self.seed)
        raw: list[tuple[float, str, float]] = []
        for cls in sorted(self.classes):
            spec = self.classes[cls]
            if spec.rate > 0:
                t = 0.0
                while True:
                    t += rng.expovariate(spec.rate)
                    if t >= self.duration_sec:
                        break
                    raw.append((t, cls, spec.network.sample(rng)))
            if spec.burst_size > 0 and spec.burst_every > 0:
                t = spec.burst_phase
                while t < self.duration_sec:
                    for _ in range(spec.burst_size):
                        raw.append((t, cls, spec.network.sample(rng)))
                    t += spec.burst_every
        raw.sort(key=lambda r: (r[0], _launch_rank(r[1]), r[1]))
        return [Req(i, cls, t, net) for i, (t, cls, net) in enumerate(raw)]


# --- upstream --------------------------------------------------------------------------

class Upstream:
    """Kalshi's bucket as documented: continuous refill, a request costs
    COST_TOKENS, rejected with 429 when the bucket cannot cover it."""

    def __init__(self, rate_per_sec: float, burst: float, timeout_prob: float = 0.0, timeout_sec: float = 10.0):
        self.rate = rate_per_sec * COST_TOKENS
        self.capacity = burst * COST_TOKENS
        self.tokens = self.capacity
        self.last = 0.0
        self.timeout_prob = timeout_prob
        self.timeout_sec = timeout_sec
        self.rate_limited = 0
        self.tokens_used_total = 0
        self._used_by_second: dict[int, int] = {}

    def attempt(self, now: float, rng: random.Random) -> str:
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens < COST_TOKENS:
            self.rate_limited += 1
            return "429"
        self.tokens -= COST_TOKENS
        self.tokens_used_total += COST_TOKENS
        sec = int(now)
        self._used_by_second[sec] = self._used_by_second.get(sec, 0) + COST_TOKENS
        if self.timeout_prob and rng.random() < self.timeout_prob:
            return "timeout"
        return "ok"

    def max_tokens_used_per_sec(self) -> int:
        return max(self._used_by_second.values(), default=0)


# --- local scheduling policies ---------------------------------------------------------

class _Bucket:
    def __init__(self, rate: float, burst: float):
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.last = 0.0

    def refill(self, now: float) -> None:
        self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
        self.last = now

    _EPS = 1e-9  # refill arithmetic can land at 0.999999...; never spin on it

    def take(self, now: float) -> bool:
        self.refill(now)
        if self.tokens >= 1.0 - self._EPS:
            self.tokens = max(0.0, self.tokens - 1.0)
            return True
        return False

    def next_token_at(self, now: float) -> float:
        self.refill(now)
        if self.tokens >= 1.0 - self._EPS:
            return now
        return now + max((1.0 - self.tokens) / self.rate, 1e-6)


@dataclass
class _Waiter:
    req: Req
    enqueued_at: float
    attempt: int


class Policy(Protocol):
    name: str

    def enqueue(self, waiter: _Waiter) -> None: ...
    def dispatch(self, now: float, in_flight_by_class: dict[str, int]) -> _Waiter | None: ...
    def queued(self) -> int: ...
    def next_wakeup(self, now: float) -> float | None: ...


def _is_critical(cls: str) -> bool:
    return cls.startswith(CRITICAL_PREFIX)


class FifoBucket:
    """The production policy: one token bucket, first come first served."""

    def __init__(self, rate: float = 8.0, burst: float = 8.0):
        self.name = f"fifo_{rate:g}" if burst == rate else f"fifo_{rate:g}_{burst:g}"
        self.bucket = _Bucket(rate, burst)
        self.q: deque[_Waiter] = deque()

    def enqueue(self, waiter: _Waiter) -> None:
        self.q.append(waiter)

    def dispatch(self, now, in_flight_by_class):
        if not self.q or not self.bucket.take(now):
            return None
        return self.q.popleft()

    def queued(self) -> int:
        return len(self.q)

    def next_wakeup(self, now):
        return self.bucket.next_token_at(now) if self.q else None


class PriorityAging:
    """Strict priority by caller class (critical 0, interactive 1,
    background 2) with optional aging: a waiter's effective priority
    improves by one band per `aging_sec` queued, so background cannot
    starve behind sustained critical demand."""

    def __init__(self, rate: float = 8.0, burst: float = 8.0, aging_sec: float | None = 5.0):
        self.name = "priority_aging" if aging_sec else "priority_strict"
        self.bucket = _Bucket(rate, burst)
        self.aging_sec = aging_sec
        self.q: list[_Waiter] = []

    @staticmethod
    def base_priority(cls: str) -> int:
        if _is_critical(cls):
            return 0
        return 1 if cls == "interactive" else 2

    def _effective(self, w: _Waiter, now: float) -> int:
        p = self.base_priority(w.req.cls)
        if self.aging_sec:
            p -= int((now - w.enqueued_at) // self.aging_sec)
        return max(0, p)

    def enqueue(self, waiter: _Waiter) -> None:
        self.q.append(waiter)

    def dispatch(self, now, in_flight_by_class):
        if not self.q or not self.bucket.take(now):
            return None
        best = min(range(len(self.q)), key=lambda i: (self._effective(self.q[i], now), self.q[i].enqueued_at, self.q[i].req.seq))
        return self.q.pop(best)

    def queued(self) -> int:
        return len(self.q)

    def next_wakeup(self, now):
        return self.bucket.next_token_at(now) if self.q else None


class DeficitRoundRobin:
    """Weighted fair queuing (DRR) across classes: each class gets `quantum`
    tokens of credit per round (default 1)."""

    def __init__(self, rate: float = 8.0, burst: float = 8.0, quanta: dict[str, int] | None = None, default_quantum: int = 1):
        self.name = "drr"
        self.bucket = _Bucket(rate, burst)
        self.quanta = dict(quanta or {})
        self.default_quantum = default_quantum
        self.queues: dict[str, deque[_Waiter]] = {}
        self.deficit: dict[str, int] = {}
        self.order: deque[str] = deque()

    def enqueue(self, waiter: _Waiter) -> None:
        cls = waiter.req.cls
        if cls not in self.queues:
            self.queues[cls] = deque()
            self.deficit[cls] = 0
            self.order.append(cls)
        self.queues[cls].append(waiter)

    def dispatch(self, now, in_flight_by_class):
        if not any(self.queues.values()) or not self.bucket.take(now):
            return None
        for _ in range(2 * len(self.order) + 1):
            cls = self.order[0]
            self.order.rotate(-1)
            q = self.queues[cls]
            if not q:
                self.deficit[cls] = 0
                continue
            if self.deficit[cls] < 1:
                self.deficit[cls] += self.quanta.get(cls, self.default_quantum)
            if self.deficit[cls] >= 1:
                self.deficit[cls] -= 1
                return q.popleft()
        return self.queues[self.order[0]].popleft()  # pragma: no cover - defensive

    def queued(self) -> int:
        return sum(len(q) for q in self.queues.values())

    def next_wakeup(self, now):
        return self.bucket.next_token_at(now) if self.queued() else None


class ReservedCapacity:
    """Hierarchical bucket: critical classes draw from a reserve first and
    may borrow from the shared bucket; background draws from the shared
    bucket only and can never touch the reserve."""

    def __init__(self, rate: float = 8.0, burst: float = 8.0, reserve_rate: float = 3.0, reserve_burst: float = 10.0):
        self.name = "reserved"
        self.shared = _Bucket(max(rate - reserve_rate, 0.1), burst)
        self.reserve = _Bucket(reserve_rate, reserve_burst)
        self.critical: deque[_Waiter] = deque()
        self.background: deque[_Waiter] = deque()

    def enqueue(self, waiter: _Waiter) -> None:
        (self.critical if _is_critical(waiter.req.cls) else self.background).append(waiter)

    def dispatch(self, now, in_flight_by_class):
        if self.critical:
            if self.reserve.take(now) or self.shared.take(now):
                return self.critical.popleft()
            return None
        if self.background and self.shared.take(now):
            return self.background.popleft()
        return None

    def queued(self) -> int:
        return len(self.critical) + len(self.background)

    def next_wakeup(self, now):
        if self.critical:
            return min(self.reserve.next_token_at(now), self.shared.next_token_at(now))
        return self.shared.next_token_at(now) if self.background else None


class ConcurrencyCapped(FifoBucket):
    """The production FIFO bucket plus per-class in-flight caps: bounds
    burst amplitude, never reorders."""

    def __init__(self, rate: float = 8.0, burst: float = 8.0, caps: dict[str, int] | None = None):
        super().__init__(rate, burst)
        self.name = "concurrency_caps"
        self.caps = dict(caps or {})
        self.held: dict[str, deque[_Waiter]] = {}

    def enqueue(self, waiter: _Waiter) -> None:
        self.held.setdefault(waiter.req.cls, deque()).append(waiter)

    def _admit(self, in_flight_by_class):
        for cls, q in self.held.items():
            cap = self.caps.get(cls)
            while q and (cap is None or in_flight_by_class.get(cls, 0) + sum(1 for w in self.q if w.req.cls == cls) < cap):
                self.q.append(q.popleft())

    def dispatch(self, now, in_flight_by_class):
        self._admit(in_flight_by_class)
        return super().dispatch(now, in_flight_by_class)

    def next_wakeup(self, now):
        if self.q:
            return self.bucket.next_token_at(now)
        return None


# --- the REST simulation ------------------------------------------------------------------

_ARRIVE, _COMPLETE, _WAKE, _RETRY = 0, 1, 2, 3


def _stats(samples: list[float]) -> dict:
    if not samples:
        return {"count": 0, "avg_ms": None, "p50_ms": None, "p95_ms": None, "max_ms": None}
    s = sorted(samples)
    n = len(s)
    pct = lambda q: s[min(n - 1, int(q * (n - 1)))]
    return {"count": n, "avg_ms": round(sum(s) / n * 1000, 3), "p50_ms": round(pct(0.5) * 1000, 3),
            "p95_ms": round(pct(0.95) * 1000, 3), "max_ms": round(s[-1] * 1000, 3)}


def _jain(values: list[float]) -> float | None:
    if not values:
        return None
    total = sum(values)
    squares = sum(v * v for v in values)
    if squares == 0:
        return 1.0
    return round(total * total / (len(values) * squares), 4)


def simulate(demand: Demand, policy: Policy, upstream: Upstream) -> dict:
    rng = random.Random(demand.seed ^ 0x5F3759DF)
    reqs = demand.requests()
    events: list[tuple[float, int, int, object]] = []
    for r in reqs:
        heapq.heappush(events, (r.arrival, _ARRIVE, r.seq, r))
    waits: dict[str, list[float]] = {}
    networks: dict[str, list[float]] = {}
    backoffs: dict[str, list[float]] = {}
    totals: dict[str, list[float]] = {}
    counts: dict[str, dict[str, int]] = {}
    in_flight: dict[str, int] = {}
    backoff_acc: dict[int, float] = {}
    queue_high_water = 0
    completed = errors = 0
    wake_scheduled: float | None = None

    def bump(cls: str, key: str, n: int = 1) -> None:
        counts.setdefault(cls, {"calls": 0, "attempts": 0, "rate_limited": 0, "errors": 0, "completed": 0})[key] += n

    def pump(now: float) -> None:
        nonlocal queue_high_water, wake_scheduled
        queue_high_water = max(queue_high_water, policy.queued())
        while True:
            w = policy.dispatch(now, in_flight)
            if w is None:
                break
            cls = w.req.cls
            waits.setdefault(cls, []).append(now - w.enqueued_at)
            bump(cls, "attempts")
            in_flight[cls] = in_flight.get(cls, 0) + 1
            outcome = upstream.attempt(now, rng)
            if outcome == "ok":
                heapq.heappush(events, (now + w.req.network, _COMPLETE, w.req.seq, (w, "ok", now)))
            elif outcome == "timeout":
                heapq.heappush(events, (now + upstream.timeout_sec, _COMPLETE, w.req.seq, (w, "timeout", now)))
            else:
                heapq.heappush(events, (now + _429_RTT_SEC, _COMPLETE, w.req.seq, (w, "429", now)))
        nxt = policy.next_wakeup(now)
        if nxt is not None and (wake_scheduled is None or nxt < wake_scheduled):
            wake_scheduled = nxt
            heapq.heappush(events, (nxt, _WAKE, 0, None))

    while events:
        now, etype, _, payload = heapq.heappop(events)
        if etype == _ARRIVE:
            r = payload
            bump(r.cls, "calls")
            policy.enqueue(_Waiter(r, now, 1))
            pump(now)
        elif etype == _WAKE:
            wake_scheduled = None
            pump(now)
        elif etype == _RETRY:
            policy.enqueue(payload)
            pump(now)
        elif etype == _COMPLETE:
            w, outcome, started = payload
            cls = w.req.cls
            in_flight[cls] -= 1
            networks.setdefault(cls, []).append(now - started)
            if outcome == "ok":
                bump(cls, "completed")
                completed += 1
                totals.setdefault(cls, []).append(now - w.req.arrival)
                if w.req.seq in backoff_acc:
                    backoffs.setdefault(cls, []).append(backoff_acc.pop(w.req.seq))
            elif outcome == "timeout":
                bump(cls, "errors")
                errors += 1
                totals.setdefault(cls, []).append(now - w.req.arrival)
            else:
                bump(cls, "rate_limited")
                if w.attempt > MAX_RETRIES:
                    bump(cls, "errors")
                    errors += 1
                    totals.setdefault(cls, []).append(now - w.req.arrival)
                else:
                    delay = BASE_BACKOFF_SEC * (2 ** (w.attempt - 1))
                    backoff_acc[w.req.seq] = backoff_acc.get(w.req.seq, 0.0) + delay
                    heapq.heappush(events, (now + delay, _RETRY, w.req.seq, _Waiter(w.req, now + delay, w.attempt + 1)))
            pump(now)

    by_class = {}
    for cls in sorted(counts):
        by_class[cls] = {
            **counts[cls],
            "limiter_wait": _stats(waits.get(cls, [])),
            "network": _stats(networks.get(cls, [])),
            "backoff": _stats(backoffs.get(cls, [])),
            "total": _stats(totals.get(cls, [])),
        }
    background_means = [
        (sum(waits[c]) / len(waits[c])) for c in waits if not _is_critical(c) and waits[c]
    ]
    return {
        "policy": policy.name,
        "duration_sec": demand.duration_sec,
        "requests": len(reqs),
        "completed": completed,
        "errors": errors,
        "by_class": by_class,
        "upstream": {
            "rate_limited": upstream.rate_limited,
            "tokens_used_total": upstream.tokens_used_total,
            "max_tokens_used_per_sec": upstream.max_tokens_used_per_sec(),
        },
        "local_queue_high_water": queue_high_water,
        "background_fairness_jain": _jain(background_means),
        "starvation_max_wait_ms": {cls: (by_class[cls]["limiter_wait"]["max_ms"] or 0.0) for cls in by_class},
    }


# --- candidate recovery --------------------------------------------------------------------

@dataclass
class RecoveryScenario:
    seed: int
    candidates: int
    rate_per_sec: float
    transient_windows: list[tuple[float, float]]
    wire_loss_windows: list[tuple[float, float]]
    restart_at: float | None = None
    wire_delay_sec: float = 90.0        # inside a loss window, half the copies arrive this late instead of never
    wire_delayed_fraction: float = 0.5


def _in_windows(t: float, windows) -> bool:
    return any(a <= t < b for a, b in windows)


class RecoveryPolicy(Protocol):
    name: str
    retries: bool
    journaled: bool
    sweeps: bool
    idempotent: bool
    max_attempts: int
    backoff_sec: float
    sweep_every_sec: float
    sweep_delay_sec: float


@dataclass
class MarkSeenFirst:
    """Today: a failed enrichment marks the trade seen and never retries."""
    name: str = "mark_seen_first"
    retries: bool = False
    journaled: bool = False
    sweeps: bool = False
    idempotent: bool = True
    max_attempts: int = 1
    backoff_sec: float = 0.0
    sweep_every_sec: float = 0.0
    sweep_delay_sec: float = 0.0


@dataclass
class RetryStateMachine:
    """pending -> resolving -> evaluated | abandoned; seen only at a terminal state."""
    max_attempts: int = 6
    backoff_sec: float = 2.0
    journaled: bool = False
    name: str = "retry_state_machine"
    retries: bool = True
    sweeps: bool = False
    idempotent: bool = True
    sweep_every_sec: float = 0.0
    sweep_delay_sec: float = 0.0

    def __post_init__(self):
        if self.journaled:
            self.name = "retry_journaled"


@dataclass
class RetryPlusReconciliation:
    """Retry state machine plus a periodic REST sweep of the window
    [sweep - delay - every, sweep - delay] that evaluates any candidate the
    wire never delivered."""
    max_attempts: int = 6
    backoff_sec: float = 2.0
    sweep_every_sec: float = 30.0
    sweep_delay_sec: float = 60.0
    idempotent: bool = True
    journaled: bool = True
    name: str = "retry_plus_reconciliation"
    retries: bool = True
    sweeps: bool = True

    def __post_init__(self):
        if not self.idempotent:
            self.name = "retry_plus_reconciliation_no_idempotency"


def simulate_recovery(scenario: RecoveryScenario, policy) -> dict:
    rng = random.Random(scenario.seed)
    arrivals: list[float] = []
    t = 0.0
    for _ in range(scenario.candidates):
        t += rng.expovariate(scenario.rate_per_sec)
        arrivals.append(t)
    horizon = arrivals[-1] + 600.0
    # Per-candidate state.
    evaluated_at: dict[int, float] = {}
    evaluations: dict[int, int] = {}
    lost_transient = lost_wire = lost_restart = abandoned = 0
    extra_rest = 0
    sweeps = sweep_rest = recovered_by_sweep = 0
    pending: dict[int, tuple[float, int]] = {}   # cid -> (next_attempt_at, attempts so far)
    wire_events: list[tuple[float, int, int]] = []  # (time, kind, cid): kind 0 = wire delivery, 1 = retry, 2 = sweep, 3 = restart
    delivered_cids: set[int] = set()
    lost_cids: set[int] = set()
    for cid, a in enumerate(arrivals):
        if _in_windows(a, scenario.wire_loss_windows):
            if rng.random() < scenario.wire_delayed_fraction:
                heapq.heappush(wire_events, (a + scenario.wire_delay_sec, 0, cid))
            else:
                lost_cids.add(cid)
        else:
            heapq.heappush(wire_events, (a, 0, cid))
    if policy.sweeps:
        k = 1
        while k * policy.sweep_every_sec < horizon:
            heapq.heappush(wire_events, (k * policy.sweep_every_sec, 2, -k))
            k += 1
    if scenario.restart_at is not None:
        heapq.heappush(wire_events, (scenario.restart_at, 3, -1))

    def evaluate(cid: int, now: float, via_sweep: bool = False) -> None:
        nonlocal extra_rest, recovered_by_sweep
        if cid in evaluated_at:
            if policy.idempotent:
                return
            evaluations[cid] = evaluations.get(cid, 1) + 1
            return
        evaluated_at[cid] = now
        evaluations[cid] = 1
        if via_sweep:
            recovered_by_sweep += 1

    def attempt(cid: int, now: float, attempts: int, via_sweep: bool = False) -> None:
        nonlocal lost_transient, abandoned, extra_rest
        if attempts > 1 or via_sweep:
            extra_rest += 1
        if _in_windows(now, scenario.transient_windows):
            if policy.retries and attempts < policy.max_attempts:
                pending[cid] = (now + policy.backoff_sec, attempts)
                heapq.heappush(wire_events, (now + policy.backoff_sec, 1, cid))
            elif policy.retries:
                abandoned += 1
                pending.pop(cid, None)
            else:
                lost_transient += 1
            return
        pending.pop(cid, None)
        evaluate(cid, now, via_sweep)

    while wire_events:
        now, kind, cid = heapq.heappop(wire_events)
        if kind == 0:
            delivered_cids.add(cid)
            if cid in evaluated_at and policy.idempotent:
                continue
            if cid in evaluated_at and not policy.idempotent:
                evaluations[cid] = evaluations.get(cid, 1) + 1
                continue
            attempt(cid, now, 1)
        elif kind == 1:
            if cid not in pending:
                continue  # dropped by a restart, or already evaluated
            _, attempts = pending[cid]
            attempt(cid, now, attempts + 1)
        elif kind == 2:
            sweeps += 1
            sweep_rest += 1
            lo, hi = now - policy.sweep_delay_sec - policy.sweep_every_sec, now - policy.sweep_delay_sec
            for c, a in enumerate(arrivals):
                if lo <= a < hi and c not in evaluated_at and c not in pending:
                    if c in lost_cids or c not in delivered_cids:
                        attempt(c, now, 1, via_sweep=True)
        elif kind == 3:
            if not policy.journaled:
                lost_restart += len(pending)
                pending.clear()

    evaluated_once = sum(1 for c in evaluated_at)
    lost_wire = sum(1 for c in lost_cids if c not in evaluated_at)
    duplicates = sum(n - 1 for n in evaluations.values() if n > 1)
    latencies = [evaluated_at[c] - arrivals[c] for c in evaluated_at]
    return {
        "policy": policy.name,
        "candidates": scenario.candidates,
        "evaluated_once": evaluated_once,
        "lost_transient": lost_transient,
        "lost_wire": lost_wire,
        "lost_restart": lost_restart,
        "abandoned": abandoned,
        "duplicates": duplicates,
        "extra_rest_calls": extra_rest,
        "sweeps": sweeps,
        "sweep_rest_calls": sweep_rest + recovered_by_sweep,
        "recovered_by_sweep": recovered_by_sweep,
        "sweep_rest_calls_per_recovered": round((sweep_rest + recovered_by_sweep) / recovered_by_sweep, 3) if recovered_by_sweep else None,
        "decision_latency": _stats(latencies),
    }


# --- presets / CLI ---------------------------------------------------------------------------

def _classes(scale: float = 1.0, whale_net=Fixed(0.047), bg_net=None) -> dict[str, ClassDemand]:
    bg_net = bg_net or {}
    return {
        "critical_whale": ClassDemand(rate=0.15 * scale, network=whale_net),
        "critical_position": ClassDemand(burst_size=3, burst_every=6.0, network=bg_net.get("critical_position", Fixed(0.090))),
        # One market-fetch phase after the tick starts: main.py gathers
        # live-status only after the critical gather has returned.
        "background_live_status": ClassDemand(burst_size=int(round(6 * scale)), burst_every=6.0, burst_phase=0.5, network=bg_net.get("background_live_status", Fixed(0.074))),
        "background_catalog": ClassDemand(burst_size=int(round(10 * scale)), burst_every=15.0, network=bg_net.get("background_catalog", Fixed(0.179))),
        "background_resolution": ClassDemand(burst_size=int(round(4 * scale)), burst_every=30.0, network=bg_net.get("background_resolution", Fixed(0.180))),
        "interactive": ClassDemand(rate=0.02, network=Fixed(0.180)),
    }


_BUSY_NET = {
    "critical_position": LogNormal(mean_sec=1.45, p95_sec=5.0),
    "background_live_status": LogNormal(mean_sec=1.2, p95_sec=5.0),
    "background_catalog": LogNormal(mean_sec=1.5, p95_sec=5.0),
    "background_resolution": LogNormal(mean_sec=0.47, p95_sec=2.0),
}

PRESETS: dict[str, dict] = {
    # I5 quiet window: 1.75 calls/s, background 69% - the tick's own gathers.
    "measured_quiet": dict(duration_sec=600.0, classes=_classes(1.0), upstream=dict(rate_per_sec=20.0, burst=60.0)),
    # I7 busy hour: slower upstream (network avgs 0.5-1.5 s), same shapes.
    "measured_busy": dict(duration_sec=600.0, classes=_classes(1.2, bg_net=_BUSY_NET), upstream=dict(rate_per_sec=20.0, burst=60.0)),
    # A catalog scan that fans out a whole batch at once (the I5 waiter high-water 34).
    "background_storm": dict(duration_sec=600.0, classes={**_classes(1.0), "background_catalog": ClassDemand(burst_size=40, burst_every=15.0, network=Fixed(0.179))},
                             upstream=dict(rate_per_sec=20.0, burst=60.0)),
    # The anonymous ceiling turning out far lower than the account's.
    "429_storm": dict(duration_sec=600.0, classes=_classes(1.0), upstream=dict(rate_per_sec=5.0, burst=10.0)),
    # Upstream timeouts on 5% of calls.
    "timeouts": dict(duration_sec=600.0, classes=_classes(1.0), upstream=dict(rate_per_sec=20.0, burst=60.0, timeout_prob=0.05, timeout_sec=10.0)),
}

POLICIES: dict[str, Callable[[], Policy]] = {
    "fifo_8": lambda: FifoBucket(rate=8.0, burst=8.0),
    "fifo_20_60": lambda: FifoBucket(rate=20.0, burst=60.0),
    "priority_aging": lambda: PriorityAging(rate=8.0, burst=8.0, aging_sec=5.0),
    "drr": lambda: DeficitRoundRobin(rate=8.0, burst=8.0, quanta={"critical_whale": 4, "critical_position": 4}),
    "reserved": lambda: ReservedCapacity(rate=8.0, burst=8.0, reserve_rate=3.0, reserve_burst=10.0),
    "concurrency_caps": lambda: ConcurrencyCapped(rate=8.0, burst=8.0, caps={"background_catalog": 3, "background_live_status": 3, "background_resolution": 2}),
    "priority_aging_20_60": lambda: PriorityAging(rate=20.0, burst=60.0, aging_sec=5.0),
    "reserved_20_60": lambda: ReservedCapacity(rate=20.0, burst=60.0, reserve_rate=5.0, reserve_burst=20.0),
}

RECOVERY_SCENARIOS: dict[str, dict] = {
    "transient_and_wire_loss": dict(seed=5, candidates=400, rate_per_sec=0.2,
                                    transient_windows=[(30.0, 40.0), (100.0, 103.0), (900.0, 960.0)],
                                    wire_loss_windows=[(60.0, 70.0), (1200.0, 1260.0)], restart_at=None),
    "restart_mid_retry": dict(seed=5, candidates=400, rate_per_sec=0.2,
                              transient_windows=[(30.0, 45.0)], wire_loss_windows=[], restart_at=35.0),
}
RECOVERY_POLICIES: dict[str, Callable[[], object]] = {
    "mark_seen_first": MarkSeenFirst,
    "retry_state_machine": lambda: RetryStateMachine(max_attempts=6, backoff_sec=2.0),
    "retry_journaled": lambda: RetryStateMachine(max_attempts=6, backoff_sec=2.0, journaled=True),
    "retry_plus_reconciliation": lambda: RetryPlusReconciliation(max_attempts=6, backoff_sec=2.0, sweep_every_sec=30.0, sweep_delay_sec=60.0, idempotent=True),
    "retry_plus_reconciliation_no_idempotency": lambda: RetryPlusReconciliation(max_attempts=6, backoff_sec=2.0, sweep_every_sec=30.0, sweep_delay_sec=60.0, idempotent=False),
}


def compare(presets: list[str], seed: int = 1, policies: tuple[str, ...] | None = None) -> dict:
    policies = policies or tuple(POLICIES)
    out: dict = {"seed": seed, "presets": presets, "policies": list(policies), "results": {}}
    for preset in presets:
        spec = PRESETS[preset]
        out["results"][preset] = {}
        for name in policies:
            demand = Demand(seed=seed, duration_sec=spec["duration_sec"], classes=spec["classes"])
            out["results"][preset][name] = simulate(demand, POLICIES[name](), Upstream(**spec["upstream"]))
    return out


def compare_recovery(scenarios: list[str] | None = None) -> dict:
    scenarios = scenarios or list(RECOVERY_SCENARIOS)
    out: dict = {"scenarios": scenarios, "policies": list(RECOVERY_POLICIES), "results": {}}
    for scenario in scenarios:
        out["results"][scenario] = {name: simulate_recovery(RecoveryScenario(**RECOVERY_SCENARIOS[scenario]), factory())
                                    for name, factory in RECOVERY_POLICIES.items()}
    return out


def _format_compare(matrix: dict) -> str:
    lines = []
    for preset, per_policy in matrix["results"].items():
        lines.append(f"== {preset}")
        lines.append(f"{'policy':22s} {'whale wait p95':>14s} {'whale total p95':>15s} {'pos wait p95':>12s} {'bg wait p95':>11s} "
                     f"{'bg max':>8s} {'429':>5s} {'err':>5s} {'qhw':>5s} {'jain':>6s} {'up max tok/s':>12s}")
        for name, r in per_policy.items():
            b = r["by_class"]
            whale = b.get("critical_whale", {}); pos = b.get("critical_position", {})
            bg = [b[c] for c in b if c.startswith("background_")]
            bg_p95 = max((c["limiter_wait"]["p95_ms"] or 0) for c in bg) if bg else 0
            bg_max = max((c["limiter_wait"]["max_ms"] or 0) for c in bg) if bg else 0
            lines.append(f"{name:22s} {(whale.get('limiter_wait', {}).get('p95_ms') or 0):14.1f} {(whale.get('total', {}).get('p95_ms') or 0):15.1f} "
                         f"{(pos.get('limiter_wait', {}).get('p95_ms') or 0):12.1f} {bg_p95:11.1f} {bg_max:8.0f} "
                         f"{r['upstream']['rate_limited']:5d} {r['errors']:5d} {r['local_queue_high_water']:5d} "
                         f"{(r['background_fairness_jain'] or 0):6.3f} {r['upstream']['max_tokens_used_per_sec']:12d}")
    return "\n".join(lines)


def _format_recovery(matrix: dict) -> str:
    lines = []
    for scenario, per_policy in matrix["results"].items():
        lines.append(f"== {scenario}")
        lines.append(f"{'policy':42s} {'once':>5s} {'lost_tr':>7s} {'lost_wire':>9s} {'lost_rst':>8s} {'aband':>5s} {'dup':>4s} {'extra_rest':>10s} {'sweep_rest':>10s} {'lat p95 ms':>10s} {'lat max':>10s}")
        for name, r in per_policy.items():
            lines.append(f"{name:42s} {r['evaluated_once']:5d} {r['lost_transient']:7d} {r['lost_wire']:9d} {r['lost_restart']:8d} {r['abandoned']:5d} "
                         f"{r['duplicates']:4d} {r['extra_rest_calls']:10d} {r['sweep_rest_calls']:10d} "
                         f"{(r['decision_latency']['p95_ms'] or 0):10.0f} {(r['decision_latency']['max_ms'] or 0):10.0f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.rest_scheduler_replay")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--compare", action="store_true")
    group.add_argument("--recovery", action="store_true")
    parser.add_argument("--presets", default=",".join(PRESETS))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.compare:
        matrix = compare([p for p in args.presets.split(",") if p], seed=args.seed)
        print(json.dumps(matrix, indent=1) if args.json else _format_compare(matrix))
    else:
        matrix = compare_recovery()
        print(json.dumps(matrix, indent=1) if args.json else _format_recovery(matrix))
    return 0


if __name__ == "__main__":
    sys.exit(main())
