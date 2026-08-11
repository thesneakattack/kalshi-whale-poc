"""
Cross-strategy comparison - Gap 7 of docs/config-tuning-data-gaps-2026-08-
10.md, and the user's own direct question this session ("comparing whale
watch strategy historical data with the market-native strategy historical
data"). The two strategies write to fully separate storage (paper_broker.db
vs market_broker.db, own bankroll/risk state) and nothing had ever joined
them before this - the only comparison available was eyeballing two
separate History-tab summaries side by side.

Two real questions this answers:
(1) How do their aggregate track records compare?
(2) Did they ever independently open a position on the *same* ticker - two
    unrelated strategies (whale order-flow vs. real-market momentum), each
    with their own capital, their own gates, their own entry logic - and if
    so, did they agree or fight each other, and who was actually right.

Scoped to completed trades only (trade_analytics.build_trade_history()
rows). A stronger version - joining candidate_log's rejected candidates
too, so a ticker where one strategy traded and the other merely
*considered and rejected* it also counts - is noted in the gaps doc as a
real extension once enough candidate_log history accumulates; not
attempted here, disclosed rather than silently implied.
"""
from services import trade_analytics


def ticker_overlap(whale_rows: list[dict], market_rows: list[dict]) -> list[dict]:
    """whale_rows/market_rows: trade_analytics.build_trade_history()-shaped
    rows (one per closed position). Finds every ticker where BOTH
    strategies independently opened a position. agreed = same side (both
    strategies leaned the same direction on the same market, from
    completely independent signals) - a real natural experiment, not
    something either strategy is aware the other is doing."""
    whale_by_ticker: dict[str, list[dict]] = {}
    for r in whale_rows:
        whale_by_ticker.setdefault(r["ticker"], []).append(r)
    out = []
    for r in market_rows:
        for w in whale_by_ticker.get(r["ticker"], []):
            out.append({
                "ticker": r["ticker"],
                "whale_side": w["side"], "whale_won": w["won"], "whale_realized_pnl": w["realized_pnl"],
                "market_side": r["side"], "market_won": r["won"], "market_realized_pnl": r["realized_pnl"],
                "agreed": w["side"] == r["side"],
            })
    return out


def aggregate_comparison(whale_rows: list[dict], market_rows: list[dict]) -> dict:
    """The honest comparison available today without needing any overlap to
    exist - reuses compute_summary() as-is so this can never silently drift
    from what each strategy's own History-tab summary already says."""
    return {
        "whale_follow": trade_analytics.compute_summary(whale_rows),
        "market_native": trade_analytics.compute_summary(market_rows),
    }
