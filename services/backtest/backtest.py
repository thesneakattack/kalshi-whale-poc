"""
Stateless backtest replay - Gap 2 of docs/config-tuning-data-gaps-2026-08-
10.md. Answers "if this one gate's threshold were a different value, what
would the win rate of the signals it accepted have looked like" by
re-running just that gate's own comparison against every already-logged
resolved signal, pure functions over data services/signal_log.py already
has - no new persistence, no replay of the trading loop itself.

Real, disclosed scope boundary, found while building this rather than
assumed up front: services/signal_log.py's `signals` table stores
`confidence`/`side`/`series`/`correct`/`size` but never `price`, `spread`,
or `volume_24h` - so a stateless replay can only ever cover gates whose
comparison is a pure function of what's actually stored. That covers
`strategy.entry_threshold`, `strategy.min_whale_winrate_pct`, and (unlike
when this was first written) `whale_watcher_kalshi.min_contracts` cleanly
too - `size` is a NOT NULL column populated since the table's inception,
so a contract-count sweep is retroactively replayable across every logged
signal, not just ones logged after some later column was added. It does
NOT cover `strategy.longshot_price_threshold`/`longshot_entry_threshold_
bonus` (needs price) or any price-band/spread/volume/momentum-style gate
(needs market_history data joined at the exact signal timestamp, which
isn't retained). A stateful replay covering those would need either richer
historical logging or a full trading-loop replay harness - out of scope
here, noted in the gaps doc as the harder "stateful" half deliberately not
attempted this pass.
"""


def entry_threshold_sweep(rows: list[dict], thresholds: list[float] | None = None) -> list[dict]:
    """rows: signal_log.resolved_signals_with_factors()-shaped ({confidence,
    correct, ...}) or any list of dicts with those two keys. For each
    candidate threshold, "accepted" = every signal whose confidence would
    have cleared it (signal.confidence < effective_threshold is
    strategy_engine.py's actual comparison - this mirrors it exactly, just
    without the longshot-zone adjustment those signals' rows don't retain
    enough data to replay). win_rate_pct is the real resolved win rate
    among the accepted set - None (not 0) when nothing clears that
    threshold, same "don't show a number you can't back" convention as
    every other hedged stat in this app.

    Default sweep is 0.00 through 0.95 in steps of 0.05 - fine enough to
    see a real curve, coarse enough to keep every bucket's n legible."""
    if thresholds is None:
        thresholds = [round(i * 0.05, 2) for i in range(20)]
    out = []
    for threshold in thresholds:
        accepted = [r for r in rows if r["confidence"] >= threshold]
        n = len(accepted)
        wins = sum(1 for r in accepted if r["correct"])
        out.append({
            "threshold": threshold,
            "n": n,
            "win_rate_pct": round(100 * wins / n, 1) if n else None,
        })
    return out


def min_whale_winrate_pct_sweep(
    series_stats: dict[str, dict], signal_rows: list[dict],
    floors: list[float] | None = None, min_resolved_for_filter: int = 10,
) -> list[dict]:
    """series_stats: signal_log.all_series_stats()-shaped ({series: {resolved,
    win_rate}}). signal_rows: signal_log.resolved_signals_with_series()-
    shaped ({series, correct}). For each candidate floor, a series is
    excluded exactly the way strategy_engine.py's real gate excludes it
    today (resolved count >= min_resolved_for_filter AND win_rate < floor -
    mirrors FollowTheWhaleStrategy.evaluate()'s own comparison), then
    win_rate_pct is recomputed over every signal belonging to a
    non-excluded series. Answers "if the floor were X instead of Y, what
    would the aggregate win rate of what's left have looked like" - the
    real, disclosed limitation being this recombines at the *aggregate*
    level (all surviving series pooled), not a faithful tick-by-tick replay
    of which individual trades would have fired, since signal_rows doesn't
    carry a timestamp-ordered notion of "would this exact trade's cooldown/
    concentration state have still allowed it."""
    if floors is None:
        floors = list(range(0, 105, 5))
    out = []
    for floor in floors:
        excluded = {
            series for series, stat in series_stats.items()
            if stat["resolved"] >= min_resolved_for_filter
            and stat["win_rate"] is not None and stat["win_rate"] < floor
        }
        included = [r for r in signal_rows if r["series"] not in excluded]
        n = len(included)
        wins = sum(1 for r in included if r["correct"])
        out.append({
            "floor": floor,
            "excluded_series_count": len(excluded),
            "n": n,
            "win_rate_pct": round(100 * wins / n, 1) if n else None,
        })
    return out
