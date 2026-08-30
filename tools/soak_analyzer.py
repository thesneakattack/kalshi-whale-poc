"""Soak-window analyzer: reads the app's own diagnostic API and judges a
soak against criteria that are split, deliberately, into two layers.

Why this exists as a tool and not a feature: the soak for
`two_consumer_mode` was being checked by hand against a prose checklist in
`docs/next-action.md`, and hand-checking missed a criterion that had
already failed (`settlement_resolver.dropped_total` was 64, not 0). A
checklist a human reads is not a mechanism.

That criterion was itself wrong, which is the second lesson: `dropped_total`
conflated retry give-ups with correctly-skipped non-binary markets, and all
64 were the latter (issue #208). `check_settlement_completeness` now gates on
`dropped_after_max_attempts`. A mechanism reading a mislabelled number is
still a mechanism reading a mislabelled number.

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
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict

DEFAULT_BASE_URL = "https://kalshi-whale-poc.ddev.site:8443"
PIPELINE_PATH = "/api/health/pipeline"

# /api/health/pipeline is a diagnostic endpoint that walks every store and
# scheduler, and it gets slower exactly when the app is unhealthy - the
# case this tool is for. Measured at 41.9s on a loaded app (2026-08-30)
# against an earlier 15s default that turned a degraded app into an
# unhandled traceback. Generous by design; override with --timeout.
DEFAULT_TIMEOUT_SEC = 120.0

# Hosts whose certificates are local development artifacts.
LOCAL_HOST_MARKERS = (".ddev.site", "localhost", "127.0.0.1")

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
    # ids of checks whose metrics this one is derived from. A derived check
    # inherits its inputs' untrustworthiness - see resolve_dependencies().
    depends_on: tuple[str, ...] = ()
    # False when the data source behind this check is known to be partial
    # (a truncated list, a sampled window). A partial source can produce
    # FAIL - what it found is real - but never PASS, because "nothing found
    # in an incomplete sample" is not evidence that nothing is there.
    source_complete: bool = True


def resolve_dependencies(checks: list[Check]) -> list[Check]:
    """Propagate untrustworthiness from inputs to the metrics built on them.

    An instrument that derives a metric from other metrics is only as
    trustworthy as its worst input. A derived check that reports PASS while
    an input is BLIND or UNKNOWN is making exactly the claim this tool
    exists to catch: reporting health it did not measure.

    Applied after all checks run, so ordering in run_checks() does not
    matter. Downgrades only - it never promotes a FAIL.
    """
    by_id = {c.id: c for c in checks}
    for c in checks:
        # A partial source cannot clear a check on its own.
        if not c.source_complete and c.status == PASS:
            c.status = UNKNOWN
            c.detail += (" [source is partial - cannot distinguish 'nothing "
                         "found' from 'not all of it was looked at']")
        for dep_id in c.depends_on:
            dep = by_id.get(dep_id)
            if dep is None:
                continue
            if dep.status == BLIND and c.status != BLIND:
                c.status = BLIND
                c.detail += (f" [inherited BLIND from {dep_id}: this metric "
                             "is derived from one that can lie]")
            elif dep.status == UNKNOWN and c.status == PASS:
                c.status = UNKNOWN
                c.detail += (f" [inherited UNKNOWN from {dep_id}: an input "
                             "was not measured, so this cannot claim to pass]")
    return checks


def _is_local(base_url: str) -> bool:
    host = urllib.parse.urlsplit(base_url).hostname or ""
    return any(m in host for m in LOCAL_HOST_MARKERS)


def fetch_pipeline(base_url: str, timeout: float = DEFAULT_TIMEOUT_SEC,
                   insecure: bool = False) -> dict:
    """Read the app through its real API - a tool never imports the app.

    TLS verification is dropped only for a local development host (whose
    cert is self-signed by ddev) or when explicitly forced. Pointing this
    at a real host keeps verification on, rather than silently trusting
    anything that answers.
    """
    ctx = None
    if insecure or _is_local(base_url):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
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


def check_backlog_timeliness(qh: dict) -> Check:
    """Timeliness judged from oldest_message_age_sec - which is exactly the
    metric staleness_metric_trustworthy audits. Declaring the dependency is
    the point: when that metric is BLIND this inherits BLIND instead of
    reporting a reassuring 0.0s, which is how the soak's own pass criterion
    came to be believed in the first place."""
    q = qh.get("queue") or {}
    age = q.get("oldest_message_age_sec")
    if age is None:
        return Check("backlog_timeliness", DATA_PLANE, UNKNOWN,
                     "oldest_message_age_sec absent",
                     depends_on=("staleness_metric_trustworthy",))
    return Check(
        "backlog_timeliness", DATA_PLANE,
        PASS if age < 1.0 else FAIL,
        f"oldest unconsumed message is {age}s old (threshold 1.0s)",
        {"oldest_message_age_sec": age},
        depends_on=("staleness_metric_trustworthy",),
    )


def check_queue_headroom(qh: dict) -> Check:
    """Gate on CURRENT occupancy; report the lifetime peak as context.

    `high_water` is monotonic and never reset, so gating on it latches the
    check FAIL forever after a single historical spike - a permanently red
    check is one that gets ignored or baselined, which is the outcome this
    file's own conservation check is written to avoid. The peak is real
    information and stays in the output; it is just not a statement about
    health now.
    """
    q = qh.get("queue") or {}
    depth, hw, cap = q.get("depth"), q.get("high_water"), q.get("capacity")
    if not cap or depth is None:
        return Check("queue_headroom", DATA_PLANE, UNKNOWN,
                     "capacity or depth absent")
    frac = depth / cap
    ok = frac < QUEUE_PRESSURE_FRACTION
    peak = f", lifetime peak {hw}/{cap} = {hw/cap*100:.1f}%" if hw else ""
    return Check(
        "queue_headroom", DATA_PLANE, PASS if ok else FAIL,
        f"current depth {depth}/{cap} = {frac*100:.1f}% of capacity "
        f"(pressure threshold {QUEUE_PRESSURE_FRACTION*100:.0f}%){peak}",
        {"depth": depth, "high_water": hw, "capacity": cap,
         "fraction": round(frac, 4)},
    )


def check_resolver_accounting(sched: dict) -> Check:
    r = (sched or {}).get("settlement_resolver") or {}
    enq, res = r.get("enqueued_total"), r.get("resolved_total")
    pend, drop = r.get("pending"), r.get("dropped_total")
    if None in (enq, res, pend, drop):
        return Check("resolver_accounting", DATA_PLANE, UNKNOWN,
                     "settlement_resolver counters absent")
    # dropped_total is the conservation term - the sum of the give-up and
    # the expected-skip counters (issue #208) - which is exactly what this
    # identity needs, and the reason the name was kept rather than retired.
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
    """Gates on the give-up counter, never on `dropped_total` (issue #208).

    This check used to read `dropped_total`, which the resolver bumped from
    two branches meaning opposite things: retry exhaustion (markets whose
    outcome really was lost) and a finalized market with no binary result
    (correctly skipped - `result` is `yes`, `no`, or `scalar`, per
    docs/kalshi/market_lifecycle.md:68 and
    docs/kalshi/market-settlement.md:23). Every one of the 64 drops that
    failed this criterion live on 2026-08-30 turned out to be the second
    kind, so the criterion was unsatisfiable whenever a scalar market
    settled - a number whose name did not match its meaning, used as a
    pass/fail gate. The skips are still reported here, just not gated on.

    An app predating the split exposes only `dropped_total`; that reads
    UNKNOWN rather than being reinterpreted as the defect count, because
    reinterpreting it is the bug.
    """
    r = (sched or {}).get("settlement_resolver") or {}
    drop, enq = r.get("dropped_after_max_attempts"), r.get("enqueued_total")
    skipped = r.get("skipped_non_binary_result")
    if drop is None:
        return Check("settlement_completeness", ANALYSIS_READINESS, UNKNOWN,
                     "settlement_resolver.dropped_after_max_attempts absent "
                     "(dropped_total is the conflated sum and must not be "
                     "read as the defect count - issue #208)")
    pct = (drop / enq * 100) if enq else 0.0
    skip_note = (
        f"; {skipped} more were skipped for a non-binary result, which is "
        "expected and not gated on"
    ) if skipped else ""
    return Check(
        "settlement_completeness", ANALYSIS_READINESS,
        PASS if drop == 0 else FAIL,
        f"{drop} of {enq} settled markets ({pct:.3f}%) were dropped after "
        f"exhausting retries and carry no resolved outcome{skip_note}",
        {"dropped_after_max_attempts": drop, "enqueued_total": enq,
         "skipped_non_binary_result": skipped,
         "dropped_total": r.get("dropped_total")},
        invalidates=(
            "Realized P&L, win rate, and every per-series statistic derived "
            "from settled outcomes. A dropped market's positions never "
            "receive their settlement result, so the trade history is "
            "short by up to this many markets and the shortfall is "
            "invisible from inside the trade layer."
        ),
    )


def check_price_completeness(qh: dict) -> Check:
    """Derived from ticker_conservation - the dependency is declared, not
    re-computed, so resolve_dependencies() propagates BLIND/UNKNOWN from it
    automatically rather than each derived check re-implementing that."""
    cons = check_ticker_conservation(qh)
    gap = cons.measured.get("gap", 0)
    status = UNKNOWN if cons.status == UNKNOWN else (PASS if gap == 0 else FAIL)
    return Check(
        "price_completeness", ANALYSIS_READINESS, status,
        f"{gap} ticker updates received but never accounted for",
        {"gap": gap},
        depends_on=("ticker_conservation",),
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


def check_exit_engine_faults(faults: dict) -> Check:
    """Gate on the COMPLETE per-component fault count.

    `most_frequent` is a truncated top-N list, not a total. Summing the
    matching entries out of it and calling the result a count reported 3 on
    one sample and 1 on the next while the component had ~175 faults - a
    number that swings on which operations happen to make the top N, wrong
    by two orders of magnitude. That is precisely the "reports health it
    did not measure" failure this tool exists to catch, so the gate reads
    `by_component`, which is complete.

    The API exposes no complete per-OPERATION breakdown, so the
    stale-price figure below is reported as an explicit floor rather than
    a total. An incomplete number is labeled, never silently gated on.
    """
    by_comp = (faults or {}).get("by_component") or {}
    if "exit_engine" not in by_comp:
        return Check("exit_engine_faults", ANALYSIS_READINESS, UNKNOWN,
                     "faults_last_24h.by_component absent - cannot count")
    n = by_comp["exit_engine"]
    freq = (faults or {}).get("most_frequent") or []
    stale_floor = sum(f.get("count", 0) for f in freq
                      if f.get("operation") == "stale_price_uncorroborated")
    floor_note = (f"; at least {stale_floor} of them are "
                  "stale_price_uncorroborated (floor - the API exposes no "
                  "complete per-operation breakdown)") if stale_floor else ""
    return Check(
        "exit_engine_faults", ANALYSIS_READINESS,
        PASS if n == 0 else FAIL,
        f"{n} exit_engine faults in the last 24h{floor_note}",
        {"exit_engine_faults_24h": n,
         "stale_price_uncorroborated_floor": stale_floor},
        invalidates=(
            "Exit-reason attribution and realized P&L per exit type. An "
            "exit taken on a stale or uncorroborated price is recorded as a "
            "decision, but the price that justified it was never confirmed."
        ),
    )


def run_checks(payload: dict) -> list[Check]:
    """Run every check, then resolve derived-metric trust."""
    ingest = payload.get("ingest") or {}
    qh = ingest.get("queue_health") or {}
    sched = payload.get("schedulers") or {}
    faults = payload.get("faults_last_24h") or {}
    checks = [
        check_ingest_drops(qh),
        check_ticker_conservation(qh),
        check_staleness_metric_trustworthy(qh),
        check_backlog_timeliness(qh),
        check_queue_headroom(qh),
        check_resolver_accounting(sched),
        check_settlement_completeness(sched),
        check_price_completeness(qh),
        check_capture_writer_health(faults),
        check_exit_engine_faults(faults),
    ]
    return resolve_dependencies(checks)


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
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SEC,
                   help=f"seconds to wait for the API (default {DEFAULT_TIMEOUT_SEC:g})")
    p.add_argument("--insecure", action="store_true",
                   help="skip TLS verification against a non-local host")
    p.add_argument("--from-file", help="analyze a saved pipeline payload")
    a = p.parse_args(argv)

    if a.from_file:
        with open(a.from_file) as fh:
            payload = json.load(fh)
    else:
        try:
            payload = fetch_pipeline(a.base_url, timeout=a.timeout,
                                     insecure=a.insecure)
        except Exception as exc:
            # A tool that reports health must never report a traceback as
            # an absence of problems. Unreachable is UNKNOWN, and UNKNOWN
            # exits non-zero.
            checks = [Check("api_reachable", DATA_PLANE, UNKNOWN,
                            f"{a.base_url}{PIPELINE_PATH} unreachable: "
                            f"{type(exc).__name__}: {exc}")]
            print(json.dumps({"verdict": UNKNOWN,
                              "checks": [asdict(c) for c in checks]}, indent=2)
                  if a.json else render(checks))
            return 1

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
