"""Stage-by-stage timing and counters for the whale-trade pipeline
(realtime data-plane investigation task I2, docs/superpowers/plans/
2026-08-25-realtime-data-plane-investigation.md).

Answers "where does a trade message's time actually go" with measurements
instead of code-shape inference: the application handler
(services/whale_stream/whale_stream_handlers.py) records its capture /
config / provider / signals stages and the end-to-end receive->decision
figure; the provider (services/whalewatchers/kalshi_trade_tape.py) records
market resolution, thread-hop wait, sync analysis time, and the counters
that put those timings in proportion (how many messages enter the thread
hop versus how many are actually candidates; how many ordinary prints
trigger a rejection write).

Pure and bounded: a fixed set of stage and counter names (an unknown name
raises KeyError so a typo can never grow the label set), O(1) per sample
(services/latency_agg.py), no persistence of its own. Persistence and
window ownership are services/observability's: it flattens snapshot() as
whale_pipeline.* every minute and calls reset_window() only after a sample
is durably written, exactly like the WebSocket gateway's ingest metrics.

Thread-safety note: everything here is written from the event loop. The
provider's worker thread returns its timings and counts to the loop rather
than recording from the thread, so snapshot() never races a writer.
"""
from services.latency_agg import LatencyAgg, bucket_for, empty_buckets, p95_upper_bound

STAGES: tuple[str, ...] = (
    "capture",                 # trade_tape insert + series_watcher.record_trade
    "config",                  # config_store.get() + config_performance.fingerprint
    "provider",                # whole whale_provider.fetch_signals call (= resolve + thread_wait + sync)
    "resolve",                 # _resolve_unknown_markets (REST when a whale-sized off-list print needs a market)
    "thread_wait",             # asyncio.to_thread submitted -> worker actually started
    "sync",                    # _process_trades_sync on the worker thread (all SQLite work)
    "signals",                 # _handle_signal + check_exits, only when a signal was emitted
    "handler_total",           # whole _process_stream_trade
    "receive_to_handler_end",  # gateway enqueue timestamp -> handler end, every stream trade
    "receive_to_decision",     # gateway enqueue timestamp -> handler end, candidates that produced a signal
)

COUNTERS: tuple[str, ...] = (
    "trades",             # unseen trades evaluated by _process_trades_sync
    "below_threshold",    # watched/resolved-market prints under min_contracts
    "offlist_skipped",    # unknown-market prints under min_contracts (the exchange-wide majority)
    "unresolved_market",  # whale-sized prints whose market could not be resolved (H4's path)
    "candidates",         # prints that cleared the contract-count gate
    "offlist_candidates", # whale-sized prints on markets outside the watchlist
    "to_thread_entries",  # asyncio.to_thread hops (one per fetch_signals call)
    "rejection_writes",   # candidate_log.record_rejection calls (each a real SQLite write)
    "resolve_calls",      # get_markets_by_tickers REST calls issued for enrichment
    "resolve_failures",   # ...of which raised
    "batch_capacity_truncated",  # off-list tickers bumped out of a resolve batch by _MAX_ONDEMAND_MARKET_FETCH (finding #7)
    "signals_emitted",    # WhaleSignals returned to the handler
)


class WhalePipelinePerf:
    def __init__(self) -> None:
        self._stage_window: dict[str, LatencyAgg] = {s: LatencyAgg() for s in STAGES}
        self._stage_lifetime: dict[str, LatencyAgg] = {s: LatencyAgg() for s in STAGES}
        self._counter_window: dict[str, int] = dict.fromkeys(COUNTERS, 0)
        self._counter_lifetime: dict[str, int] = dict.fromkeys(COUNTERS, 0)
        self._e2e_buckets: dict[str, int] = empty_buckets()

    def record_stage(self, stage: str, seconds: float) -> None:
        self._stage_window[stage].add(seconds)
        self._stage_lifetime[stage].add(seconds)
        if stage == "receive_to_decision":
            self._e2e_buckets[bucket_for(seconds)] += 1

    def record_count(self, counter: str, n: int = 1) -> None:
        self._counter_window[counter] += n
        self._counter_lifetime[counter] += n

    def record_counts(self, counts: dict) -> None:
        for counter, n in counts.items():
            if n:
                self.record_count(counter, n)

    def snapshot(self) -> dict:
        e2e_count = self._stage_window["receive_to_decision"].count
        return {
            "stages": {
                s: {
                    "window": self._stage_window[s].snapshot(1000.0, "ms"),
                    "lifetime": self._stage_lifetime[s].snapshot(1000.0, "ms"),
                }
                for s in STAGES
            },
            "counters": {
                "window": dict(self._counter_window),
                "lifetime": dict(self._counter_lifetime),
            },
            "receive_to_decision": {
                "buckets": dict(self._e2e_buckets),
                "window_p95_upper_bound_sec": p95_upper_bound(self._e2e_buckets, e2e_count),
            },
        }

    def reset_window(self) -> None:
        self._stage_window = {s: LatencyAgg() for s in STAGES}
        self._counter_window = dict.fromkeys(COUNTERS, 0)
        self._e2e_buckets = empty_buckets()


perf = WhalePipelinePerf()
