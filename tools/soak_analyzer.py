"""Soak-window analyzer: reads the app's own diagnostic API and judges a
soak against criteria that are split, deliberately, into two layers.

Why this exists as a tool and not a feature: the soak for
`two_consumer_mode` was being checked by hand against a prose checklist in
`docs/next-action.md`, and hand-checking missed a criterion that had
already failed (`settlement_resolver.dropped_total` was 64, not 0). A
checklist a human reads is not a mechanism.

## The two layers, and why they stay apart

**Data layer** (ingest, websocket, queues, resolver) *produces* telemetry.
Its checks ask one question: does its own accounting hold together?

**Trade/history/logging layer** (trade archive, series stats, P&L,
advisory) *consumes* that telemetry to draw conclusions about money. Its
checks ask a different question: are the inputs complete enough that an
analysis run against them would be true?

The layers inform each other in one direction each, and the code keeps
them apart so neither silently absorbs the other's job:

- A gap in the trade/history/logging layer defines a metric the data layer
  must expose. `ANALYSIS_READINESS` checks below each name the analysis
  they invalidate, so a missing metric shows up as an analysis that cannot
  be trusted rather than as a silently wrong number.
- A new data-layer metric enables a sharper analysis. `pending_tickers`
  is the worked example: until it existed, `oldest_message_age_sec` could
  report 0.0 with a wedged coalescing map and nothing could tell. With it,
  `staleness_metric_trustworthy` cross-checks the one against the other.

A check that cannot be evaluated returns UNKNOWN or BLIND. Neither is a
pass. BLIND specifically means "the metric this criterion reads is capable
of lying right now" - the failure mode that motivated this file.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.request
from dataclasses import dataclass, field, asdict

DEFAULT_BASE_URL = "https://kalshi-whale-poc.ddev.site:8443"
PIPELINE_PATH = "/api/health/pipeline"

# Queue occupancy above this fraction of capacity is reported as pressure.
# Not a drop - a drop is already its own check - but the headroom that
# stands between the current load and one.
QUEUE_PRESSURE_FRACTION = 0.70

PASS, FAIL, BLIND, UNKNOWN = "PASS", "FAIL", "BLIND", "UNKNOWN"
DATA_PLANE = "data"
ANALYSIS_READINESS = "analysis"


@dataclass
class Check:
    id: str
    layer: str
    status: str
    detail: str
    measured: dict = field(default_factory=dict)
    # ANALYSIS_READINESS only: which downstream analysis this compromises.
    invalidates: str | None = None


def fetch_pipeline(base_url: str, timeout: float = 15.0) -> dict:
    """Read the app through its real API - a tool never imports the app."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # local ddev cert
    req = urllib.request.Request(base_url.rstrip("/") + PIPELINE_PATH)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode())


# --------------------------------------------------------------------------
# Data-layer checks: does the data layer's own accounting hold together?
# --------------------------------------------------------------------------

def check_ingest_drops(qh: dict) -> Check:
    lifetime = qh.get("dropped_messages")
    window = qh.get("dropped_window")
    ok = lifetime == 0 and window == 0
    return Check(
        "ingest_no_drops", DATA_PLANE, PASS if ok else FAIL,
        f"lifetime={lifetime} window={window} (both must be 0)",
        {"dropped_messages": lifetime, "dropped_window": window},
    )


def check_ticker_conservation(qh: dict) -> Check:
    """received == processed + coalesced + pending + dropped, for tickers.

    Only decidable on a QUIESCENT sample: with items still in the queue,
    the difference is legitimately in-flight and proves nothing. Reporting
    UNKNOWN there is the point - a conservation check that fires on
    in-flight work would be noise, and noise gets baselined away.
    """
    q = qh.get("queue") or {}
    depth = q.get("depth")
    pending = q.get("pending_tickers")
    recv = (qh.get("received_by_class") or {}).get("ticker")
    proc = (qh.get("processed_by_class") or {}).get("ticker")
    coal = q.get("coalesced_tickers")
    dropped = (qh.get("dropped_by_class") or {}).get("ticker", 0)
    if None in (depth, pending, recv, proc, coal):
        return Check("ticker_conservation", DATA_PLANE, UNKNOWN,
                     "one or more counters absent from the API response")
    if depth != 0:
        return Check("ticker_conservation", DATA_PLANE, UNKNOWN,
                     f"queue depth {depth} != 0 - sample is not quiescent, "
                     "difference may be legitimately in-flight",
                     {"depth": depth})
    accounted = proc + coal + pending + dropped
    gap = recv - accounted
    ok = gap == 0
    pct = (gap / recv * 100) if recv else 0.0
    return Check(
        "ticker_conservation", DATA_PLANE, PASS if ok else FAIL,
        f"received={recv} accounted={accounted} gap={gap} ({pct:.4f}%) "
        "[processed+coalesced+pending+dropped]",
        {"received": recv, "processed": proc, "coalesced": coal,
         "pending": pending, "dropped": dropped, "gap": gap},
    )


def check_staleness_metric_trustworthy(qh: dict) -> Check:
    """Cross-check `oldest_message_age_sec` against the coalescing map.

    `_oldest_message_age` inspects only the three queues, never
    `_ticker_by_market`. A non-empty pending map with drained queues
    therefore reports exactly 0.0 - perfect health - which is the value the
    soak's own pass criterion reads. This check exists so that the lie is
    detected rather than believed.
    """
    q = qh.get("queue") or {}
    age = q.get("oldest_message_age_sec")
    pending = q.get("pending_tickers")
    depth = q.get("depth")
    if age is None or pending is None:
        return Check("staleness_metric_trustworthy", DATA_PLANE, UNKNOWN,
                     "oldest_message_age_sec or pending_tickers absent")
    if pending > 0 and depth == 0 and age == 0.0:
        return Check(
            "staleness_metric_trustworthy", DATA_PLANE, BLIND,
            f"pending_tickers={pending} with all queues drained, yet "
            f"oldest_message_age_sec={age}. The staleness metric cannot see "
            "the coalescing map, so it is reporting health it did not "
            "measure. Any soak verdict resting on this number is void.",
            {"oldest_message_age_sec": age, "pending_tickers": pending},
        )
    return Check(
        "staleness_metric_trustworthy", DATA_PLANE, PASS,
        f"age={age}s corroborated (pending_tickers={pending}, depth={depth})",
        {"oldest_message_age_sec": age, "pending_tickers": pending},
    )


def check_queue_headroom(qh: dict) -> Check:
    q = qh.get("queue") or {}
    hw, cap = q.get("high_water"), q.get("capacity")
    if not cap:
        return Check("queue_headroom", DATA_PLANE, UNKNOWN, "capacity absent")
    frac = hw / cap
    ok = frac < QUEUE_PRESSURE_FRACTION
    return Check(
        "queue_headroom", DATA_PLANE, PASS if ok else FAIL,
        f"high_water {hw}/{cap} = {frac*100:.1f}% of capacity "
        f"(pressure threshold {QUEUE_PRESSURE_FRACTION*100:.0f}%)",
        {"high_water": hw, "capacity": cap, "fraction": round(frac, 4)},
    )


def check_resolver_accounting(sched: dict) -> Check:
    r = (sched or {}).get("settlement_resolver") or {}
    enq, res = r.get("enqueued_total"), r.get("resolved_total")
    pend, drop = r.get("pending"), r.get("dropped_total")
    if None in (enq, res, pend, drop):
        return Check("resolver_accounting", DATA_PLANE, UNKNOWN,
                     "settlement_resolver counters absent")
    gap = enq - (res + pend + drop)
    return Check(
        "resolver_accounting", DATA_PLANE, PASS if gap == 0 else FAIL,
        f"enqueued={enq} resolved+pending+dropped={res+pend+drop} gap={gap}",
        {"enqueued": enq, "resolved": res, "pending": pend, "dropped": drop},
    )


# --------------------------------------------------------------------------
# Analysis-readiness checks: can the trade/history/logging layer trust its
# inputs? Each names the analysis it invalidates.
# --------------------------------------------------------------------------

def check_settlement_completeness(sched: dict) -> Check:
    r = (sched or {}).get("settlement_resolver") or {}
    drop, enq = r.get("dropped_total"), r.get("enqueued_total")
    if drop is None:
        return Check("settlement_completeness", ANALYSIS_READINESS, UNKNOWN,
                     "settlement_resolver.dropped_total absent")
    pct = (drop / enq * 100) if enq else 0.0
    return Check(
        "settlement_completeness", ANALYSIS_READINESS,
        PASS if drop == 0 else FAIL,
        f"{drop} of {enq} settled markets ({pct:.3f}%) were dropped after "
        "exhausting retries and carry no resolved outcome",
        {"dropped_total": drop, "enqueued_total": enq},
        invalidates=(
            "Realized P&L, win rate, and every per-series statistic derived "
            "from settled outcomes. A dropped market's positions never "
            "receive their settlement result, so the trade history is "
            "short by up to this many markets and the shortfall is "
            "invisible from inside the trade layer."
        ),
    )


def check_price_completeness(qh: dict) -> Check:
    cons = check_ticker_conservation(qh)
    if cons.status in (UNKNOWN,):
        return Check("price_completeness", ANALYSIS_READINESS, UNKNOWN,
                     f"depends on ticker_conservation: {cons.detail}")
    gap = cons.measured.get("gap", 0)
    return Check(
        "price_completeness", ANALYSIS_READINESS,
        PASS if gap == 0 else FAIL,
        f"{gap} ticker updates received but never accounted for",
        {"gap": gap},
        invalidates=(
            "Mark-to-market, unrealized P&L, exit-engine decisions, and the "
            "netting materiality bar - all read the latest price. Missing "
            "updates mean some of those decisions were taken against a "
            "price the exchange had already superseded."
        ),
    )


def check_capture_writer_health(faults: dict) -> Check:
    by_comp = (faults or {}).get("by_component") or {}
    n = by_comp.get("capture_writer", 0)
    return Check(
        "capture_writer_health", ANALYSIS_READINESS,
        PASS if n == 0 else FAIL,
        f"{n} capture_writer faults in the last 24h",
        {"capture_writer_faults_24h": n},
        invalidates=(
            "The raw_trades archive, and therefore every backtest, replay, "
            "and whale-density statistic computed from it. A failed flush "
            "is a hole in the stored history, not a delayed write."
        ),
    )


def check_exit_price_corroboration(faults: dict) -> Check:
    freq = (faults or {}).get("most_frequent") or []
    n = sum(f.get("count", 0) for f in freq
            if f.get("operation") == "stale_price_uncorroborated")
    return Check(
        "exit_price_corroboration", ANALYSIS_READINESS,
        PASS if n == 0 else FAIL,
        f"{n} exit checks acted on an unstamped, uncorroborated price",
        {"stale_price_exits": n},
        invalidates=(
            "Exit-reason attribution and realized P&L per exit type. An "
            "exit taken on a stale price is recorded as a decision, but the "
            "price that justified it was never confirmed."
        ),
    )


def run_checks(payload: dict) -> list[Check]:
    ingest = payload.get("ingest") or {}
    qh = ingest.get("queue_health") or {}
    sched = payload.get("schedulers") or {}
    faults = payload.get("faults_last_24h") or {}
    return [
        check_ingest_drops(qh),
        check_ticker_conservation(qh),
        check_staleness_metric_trustworthy(qh),
        check_queue_headroom(qh),
        check_resolver_accounting(sched),
        check_settlement_completeness(sched),
        check_price_completeness(qh),
        check_capture_writer_health(faults),
        check_exit_price_corroboration(faults),
    ]


def verdict(checks: list[Check]) -> str:
    if any(c.status == BLIND for c in checks):
        return BLIND
    if any(c.status == FAIL for c in checks):
        return FAIL
    if any(c.status == UNKNOWN for c in checks):
        return UNKNOWN
    return PASS


def render(checks: list[Check]) -> str:
    out: list[str] = []
    for layer, title in ((DATA_PLANE, "DATA LAYER - telemetry integrity"),
                         (ANALYSIS_READINESS,
                          "TRADE/HISTORY/LOGGING LAYER - analysis readiness")):
        out.append("")
        out.append(title)
        out.append("-" * len(title))
        for c in (c for c in checks if c.layer == layer):
            out.append(f"[{c.status:<7}] {c.id}")
            out.append(f"          {c.detail}")
            if c.invalidates and c.status not in (PASS,):
                out.append(f"          INVALIDATES: {c.invalidates}")
    out.append("")
    out.append(f"VERDICT: {verdict(checks)}")
    out.append("(BLIND means a criterion's own metric is capable of lying "
               "right now; it is not a pass.)")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tools.soak_analyzer")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--from-file", help="analyze a saved pipeline payload")
    a = p.parse_args(argv)

    if a.from_file:
        with open(a.from_file) as fh:
            payload = json.load(fh)
    else:
        payload = fetch_pipeline(a.base_url)

    checks = run_checks(payload)
    if a.json:
        print(json.dumps(
            {"verdict": verdict(checks),
             "checks": [asdict(c) for c in checks]}, indent=2))
    else:
        print(render(checks))
    return 0 if verdict(checks) == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
