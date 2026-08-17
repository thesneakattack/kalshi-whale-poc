"""
Performance/integrity diagnostics - measures what the app ACTUALLY did
against what its settings say it should have done, and against real
exchange-wide ground truth.

Direct request (2026-08-17): "develop a diagnostic tool to measure
performance and integrity against settings/history", after a live session
found three separate problems that all looked like "bad signals" from the
dashboard but had unrelated root causes:
  1. COVERAGE - the app only sees trades for markets in its own discovery
     watchlist (the trade websocket is subscribed with an explicit
     market_tickers list). Measured 2026-08-17T00:37Z: 425 distinct markets
     traded in a 30-second window, 8 were watched, and 5 of 5 whale prints
     >=$2,500 were invisible. Nothing in the app reported this, because a
     trade on an unwatched market is never received at all - it isn't
     filtered, logged, or counted as rejected.
  2. INTEGRITY - a threshold can be set in settings.yaml and still be
     violated in the stored history, because history outlives config.
     20,147 signals in one 24h window sat below the then-current
     min_notional gate purely as residue from an earlier stress-test
     value, silently skewing every downstream win-rate/segmentation stat
     that reads signal_log.
  3. ATTRIBUTION - "we had a ~70% win rate once" is unfalsifiable without
     binding each trade to the config epoch it was actually placed under.
     config_performance.applied_changes already records every live tuning
     change with a timestamp, so epochs are reconstructable exactly rather
     than guessed at from memory.

Every check is READ-ONLY (no writes to any data/*.db, no config mutation,
no order placement) and degrades honestly - a check that cannot be computed
reports status "unknown" with the reason, never a fabricated number. That
matters more here than usual: this module exists to be trusted when other
numbers are in doubt.
"""
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from services import config_performance, signal_log
from services import paper_broker as pb_module

_OK = "ok"
_WARN = "warn"
_FAIL = "fail"
_UNKNOWN = "unknown"


@dataclass
class Check:
    """One diagnostic finding. `status` is deliberately coarse (ok/warn/
    fail/unknown) so a caller can triage without parsing prose, while
    `detail` carries the real numbers and `evidence` the raw rows behind
    them - same "show the work" convention the advisory engine already
    uses, so a surprising result can be audited instead of trusted."""
    name: str
    status: str
    summary: str
    detail: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "status": self.status, "summary": self.summary,
            "detail": self.detail, "evidence": self.evidence[:25],
        }


def _close_ts_for_tickers(tickers: list[str]) -> dict[str, float]:
    """ticker -> close_ts, from market_catalog (the one store that persists a
    close time per market beyond the rotating watchlist). Deliberately NOT
    reconstructed from the ticker string: the YYMMMDDHHMM convention is a
    per-series habit, not an API guarantee, and close_time is mutable
    upstream anyway (docs/kalshi/market_lifecycle.md's close_date_updated).
    Missing tickers are simply absent from the result - callers bucket
    those as "unknown" rather than guessing."""
    from services import market_catalog

    unique = list({t for t in tickers if t})
    if not unique:
        return {}
    try:
        with sqlite3.connect(market_catalog.DB_PATH) as conn:
            placeholders = ",".join("?" for _ in unique)
            rows = conn.execute(
                f"SELECT ticker, close_ts FROM markets WHERE ticker IN ({placeholders}) "
                "AND close_ts IS NOT NULL",
                unique,
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {t: ts for t, ts in rows}


def _unit_cost(side: str, yes_price: float | None) -> float | None:
    """What the taker actually paid per contract. Same side-aware inversion
    PaperBroker.cost_basis/open_position use - re-deriving this as
    size*price without the (1 - price) no-side flip is the exact bug class
    CLAUDE.md's "no-side dollar math" section documents, so it is written
    once here and reused by every check below."""
    if yes_price is None:
        return None
    return yes_price if side == "yes" else 1.0 - yes_price


# ---------------------------------------------------------------- integrity

def check_threshold_integrity(cfg: dict, since_ts: float | None = None, now: float | None = None) -> Check:
    """Do the signals actually in signal_log respect the notional gate the
    config currently declares? A violation is not necessarily a live bug -
    history outlives config, so rows recorded under an older, looser value
    stay exactly as they were - but it is always a live *statistics* bug,
    because every consumer of signal_log (confidence_calibration,
    regime_analytics, the whale-winrate filter) reads those rows without
    knowing which config epoch produced them."""
    now = now if now is not None else time.time()
    since_ts = since_ts if since_ts is not None else now - 24 * 3600
    wwk = cfg.get("whale_watcher_kalshi") or {}
    default_min = float(wwk.get("min_notional_usd", 0) or 0)
    by_series = wwk.get("min_notional_usd_by_series") or {}

    try:
        with sqlite3.connect(signal_log.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT series, ticker, raw_notional_usd, seen_at FROM signals "
                "WHERE seen_at > ? AND raw_notional_usd IS NOT NULL",
                (since_ts,),
            ).fetchall()
    except sqlite3.Error as exc:
        return Check("threshold_integrity", _UNKNOWN, f"signal_log unreadable: {exc}")

    if not rows:
        return Check("threshold_integrity", _UNKNOWN, "no signals with a recorded notional in this window")

    violations, per_series = [], {}
    for r in rows:
        floor = float(by_series.get(r["series"], default_min))
        bucket = per_series.setdefault(r["series"], {"n": 0, "under": 0, "floor": floor, "min_seen": None})
        bucket["n"] += 1
        val = r["raw_notional_usd"]
        if bucket["min_seen"] is None or val < bucket["min_seen"]:
            bucket["min_seen"] = val
        if val < floor:
            bucket["under"] += 1
            violations.append({
                "ticker": r["ticker"], "series": r["series"],
                "notional": round(val, 2), "floor": floor,
                "seen_at": r["seen_at"],
            })

    under = len(violations)
    pct = 100.0 * under / len(rows)
    status = _OK if under == 0 else (_WARN if pct < 5 else _FAIL)
    return Check(
        "threshold_integrity", status,
        f"{under}/{len(rows)} signals ({pct:.1f}%) sit below the currently-configured "
        f"min_notional for their series"
        + ("" if under == 0 else " — stale rows from an older config still skew every stat that reads signal_log"),
        detail={"window_start": since_ts, "total": len(rows), "violations": under,
                "pct": round(pct, 2), "by_series": per_series},
        evidence=violations,
    )


def check_price_band_adherence(cfg: dict, since_ts: float | None = None, now: float | None = None) -> Check:
    """Did entries respect min_unit_cost/max_unit_cost - the price-band gate
    added by commit 52456f0 to fix the "near 70% win rate but only pennies
    earned" problem? Overpaying above the band is the specific failure that
    bug was about: at $0.95/contract a win pays 5c while a loss costs 95c,
    so even a 70% win rate is deeply negative EV. Resolves the band
    per-trade through the same category/series override chain the strategy
    itself uses, so a series with its own override is judged against ITS
    band, not the global default."""
    from services import config_overrides, trade_category

    now = now if now is not None else time.time()
    since_ts = since_ts if since_ts is not None else now - 24 * 3600
    base = cfg.get("strategy") or {}
    overrides = cfg.get("strategy_overrides")

    try:
        with sqlite3.connect(pb_module.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT ticker, side, price, size, reason, timestamp FROM trades "
                "WHERE timestamp > ? AND reason LIKE 'whale print%'",
                (since_ts,),
            ).fetchall()
    except sqlite3.Error as exc:
        return Check("price_band_adherence", _UNKNOWN, f"paper_broker unreadable: {exc}")
    if not rows:
        return Check("price_band_adherence", _UNKNOWN, "no whale-follow entries in this window")

    cats = trade_category.categories_for_tickers([r["ticker"] for r in rows])
    above, below, inside, offenders = 0, 0, 0, []
    for r in rows:
        strat = config_overrides.resolve(
            base, overrides, category=cats.get(r["ticker"]), series=signal_log.series_of(r["ticker"]),
        )
        lo, hi = strat.get("min_unit_cost"), strat.get("max_unit_cost")
        uc = _unit_cost(r["side"], r["price"])
        if uc is None:
            continue
        if hi is not None and uc > hi:
            above += 1
            offenders.append({"ticker": r["ticker"], "unit_cost": round(uc, 3), "max": hi,
                              "max_gain_per_contract": round(1 - uc, 3), "timestamp": r["timestamp"]})
        elif lo is not None and uc < lo:
            below += 1
            offenders.append({"ticker": r["ticker"], "unit_cost": round(uc, 3), "min": lo,
                              "timestamp": r["timestamp"]})
        else:
            inside += 1
    total = above + below + inside
    if total == 0:
        return Check("price_band_adherence", _UNKNOWN, "no entries carried a usable price")
    out_pct = 100.0 * (above + below) / total
    status = _OK if out_pct == 0 else (_WARN if out_pct < 20 else _FAIL)
    return Check(
        "price_band_adherence", status,
        f"{above} entries above max_unit_cost, {below} below min_unit_cost, "
        f"{inside} inside the band ({out_pct:.0f}% out of band)",
        detail={"above": above, "below": below, "inside": inside, "out_of_band_pct": round(out_pct, 1)},
        evidence=offenders,
    )


# ---------------------------------------------------------------- runway

def check_runway_at_entry(cfg: dict, since_ts: float | None = None, now: float | None = None) -> Check:
    """How much time was left to manage each position when it opened, and
    what happened to the ones opened with almost none? This is ROADMAP #1's
    whole thesis, measured rather than asserted: a position opened seconds
    before close cannot be managed by any price-driven exit rule, so it
    rides to settlement regardless of take_profit/stop_loss/auto_exit.

    close_time is read from market_catalog's persisted close_ts rather than
    reconstructed from the ticker string - ticker-encoded times are a
    per-series convention, not an API guarantee, and close_time is mutable
    upstream anyway (market_lifecycle.md's close_date_updated)."""
    now = now if now is not None else time.time()
    since_ts = since_ts if since_ts is not None else now - 24 * 3600
    strat = cfg.get("strategy") or {}
    floor = strat.get("min_seconds_to_close")

    try:
        with sqlite3.connect(pb_module.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            opens = conn.execute(
                "SELECT ticker, side, price, size, timestamp FROM trades "
                "WHERE timestamp > ? AND reason LIKE 'whale print%' ORDER BY timestamp",
                (since_ts,),
            ).fetchall()
            closes = conn.execute(
                "SELECT ticker, reason, timestamp FROM trades "
                "WHERE timestamp > ? AND reason LIKE 'closed:%' ORDER BY timestamp",
                (since_ts,),
            ).fetchall()
    except sqlite3.Error as exc:
        return Check("runway_at_entry", _UNKNOWN, f"paper_broker unreadable: {exc}")
    if not opens:
        return Check("runway_at_entry", _UNKNOWN, "no whale-follow entries in this window")

    close_by_ticker = _close_ts_for_tickers([r["ticker"] for r in opens])

    buckets = {"<60s": 0, "60-300s": 0, "300-900s": 0, ">900s": 0, "unknown": 0}
    short_entries = []
    for r in opens:
        ct = close_by_ticker.get(r["ticker"])
        secs = (ct - r["timestamp"]) if ct else None
        if secs is None:
            buckets["unknown"] += 1
            continue
        if secs < 60:
            buckets["<60s"] += 1
        elif secs < 300:
            buckets["60-300s"] += 1
        elif secs < 900:
            buckets["300-900s"] += 1
        else:
            buckets[">900s"] += 1
        if floor and secs < floor:
            short_entries.append({"ticker": r["ticker"], "runway_sec": round(secs, 1),
                                  "floor": floor, "timestamp": r["timestamp"]})

    known = sum(v for k, v in buckets.items() if k != "unknown")
    if known == 0:
        return Check(
            "runway_at_entry", _UNKNOWN,
            "no entry could be joined to a recorded close_time — market_catalog has no "
            "close_time snapshot for these tickers, so runway is not reconstructable",
            detail={"buckets": buckets, "entries": len(opens)},
        )
    tight = buckets["<60s"] + buckets["60-300s"]
    pct = 100.0 * tight / known
    status = _OK if pct == 0 else (_WARN if pct < 25 else _FAIL)
    return Check(
        "runway_at_entry", status,
        f"{tight}/{known} entries ({pct:.0f}%) opened with under 5 minutes of runway"
        + (f"; {len(short_entries)} below the configured {floor}s floor" if floor else
           "; no strategy.min_seconds_to_close floor is configured"),
        detail={"buckets": buckets, "floor": floor, "closes_seen": len(closes)},
        evidence=short_entries,
    )


# ---------------------------------------------------------------- attribution

def config_epochs(since_ts: float | None = None, now: float | None = None) -> list[dict]:
    """Reconstruct the real config timeline from
    config_performance.applied_changes - every live tuning change is already
    recorded there with applied_at/config_path/old_value/new_value, so
    epoch boundaries are exact rather than remembered. An epoch is the span
    between consecutive change timestamps."""
    now = now if now is not None else time.time()
    since_ts = since_ts if since_ts is not None else now - 7 * 24 * 3600
    try:
        with sqlite3.connect(config_performance.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT applied_at, config_path, old_value, new_value, source "
                "FROM applied_changes WHERE applied_at > ? ORDER BY applied_at",
                (since_ts,),
            ).fetchall()
    except sqlite3.Error:
        return []
    # collapse changes sharing a timestamp - one Config-tab save writes many rows
    by_ts: dict[float, list] = {}
    for r in rows:
        by_ts.setdefault(r["applied_at"], []).append(dict(r))
    stamps = sorted(by_ts)
    epochs = []
    for i, ts in enumerate(stamps):
        end = stamps[i + 1] if i + 1 < len(stamps) else now
        epochs.append({
            "start": ts, "end": end, "duration_sec": end - ts,
            "changes": [{"path": c["config_path"], "old": c["old_value"], "new": c["new_value"]}
                        for c in by_ts[ts]],
        })
    return epochs


def performance_by_epoch(since_ts: float | None = None, now: float | None = None,
                         min_trades: int = 3) -> Check:
    """Win rate and realized P&L per config epoch - the direct test of any
    "we used to win ~70%" claim, and of the 2026-08-17 hypothesis that the
    good stretch came from richer pre-rate-limit market coverage rather
    than from any threshold setting.

    Reads realized P&L out of the close reason string, which is where
    PaperBroker.close_position already writes it ("(realized +8.31)") -
    the trades table has no realized_pnl column of its own, and inventing
    one here by re-deriving cost basis from a closed position's row would
    be exactly the re-derivation CLAUDE.md warns against."""
    import re
    now = now if now is not None else time.time()
    since_ts = since_ts if since_ts is not None else now - 7 * 24 * 3600
    epochs = config_epochs(since_ts, now)
    if not epochs:
        return Check("performance_by_epoch", _UNKNOWN,
                     "no config changes recorded in this window — no epochs to compare")
    try:
        with sqlite3.connect(pb_module.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            closes = conn.execute(
                "SELECT ticker, reason, timestamp FROM trades "
                "WHERE timestamp > ? AND reason LIKE 'closed:%'",
                (since_ts,),
            ).fetchall()
    except sqlite3.Error as exc:
        return Check("performance_by_epoch", _UNKNOWN, f"paper_broker unreadable: {exc}")

    rows = []
    for r in closes:
        m = re.search(r"realized ([+-][\d.]+)", r["reason"] or "")
        if not m:
            continue
        rows.append({"ts": r["timestamp"], "pnl": float(m.group(1)), "ticker": r["ticker"]})

    out = []
    for e in epochs:
        in_epoch = [r for r in rows if e["start"] <= r["ts"] < e["end"]]
        if len(in_epoch) < min_trades:
            continue
        wins = sum(1 for r in in_epoch if r["pnl"] > 0)
        total_pnl = sum(r["pnl"] for r in in_epoch)
        out.append({
            "start": e["start"], "end": e["end"],
            "start_iso": datetime.fromtimestamp(e["start"], timezone.utc).isoformat(),
            "duration_hours": round(e["duration_sec"] / 3600, 2),
            "trades": len(in_epoch), "wins": wins,
            "win_rate_pct": round(100.0 * wins / len(in_epoch), 1),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / len(in_epoch), 2),
            "changes": e["changes"][:8],
        })
    if not out:
        return Check("performance_by_epoch", _UNKNOWN,
                     f"no config epoch had at least {min_trades} resolved trades")
    best = max(out, key=lambda e: e["win_rate_pct"])
    return Check(
        "performance_by_epoch", _OK,
        f"{len(out)} epochs with >={min_trades} trades; best win rate "
        f"{best['win_rate_pct']}% ({best['trades']} trades, ${best['total_pnl']:,.2f}) "
        f"starting {best['start_iso']}",
        detail={"epochs": out, "best": best},
    )


# ---------------------------------------------------------------- coverage

async def check_coverage(cfg: dict, watched_tickers: set[str], client=None,
                         pages: int = 2, min_notional: float | None = None) -> Check:
    """The check nothing else in this app can perform: compare what the app
    SAW against what the exchange actually printed.

    Every other diagnostic here reads the app's own stores, so all of them
    are blind to a trade that never arrived - and trades on unwatched
    markets never arrive at all, because the trade websocket is subscribed
    with an explicit market_tickers list (docs/kalshi/CHEATSHEET.md, "Why
    don't whale trades on most markets ever reach the app?"). This calls
    GET /markets/trades, which is exchange-wide and needs no ticker, and
    reports the miss rate directly.

    Read-only and cheap: 1-2 paginated GETs, no subscription change, no
    writes."""
    from services.kalshi_client import KalshiClient

    owns_client = client is None
    if owns_client:
        client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"].get("request_timeout_sec", 10))
    wwk = cfg.get("whale_watcher_kalshi") or {}
    floor = min_notional if min_notional is not None else float(wwk.get("min_notional_usd", 2500) or 2500)
    by_series = wwk.get("min_notional_usd_by_series") or {}

    try:
        # ticker=None is the exchange-wide form of GET /markets/trades
        # (docs/kalshi/get-trades.md: "all trades for all markets") - the
        # same call _fetch_trade_tape makes per-ticker, just unscoped.
        trades, cursor = [], None
        for _ in range(max(1, pages)):
            page = await client.get_trades(ticker=None, limit=1000, cursor=cursor)
            trades.extend(page.get("trades") or [])
            cursor = page.get("cursor")
            if not cursor:
                break
    except Exception as exc:  # network/API failure - degrade honestly
        return Check("coverage", _UNKNOWN, f"could not fetch exchange-wide trades: {type(exc).__name__}: {exc}")
    finally:
        if owns_client:
            await client.close()

    if not trades:
        return Check("coverage", _UNKNOWN, "exchange-wide trade fetch returned nothing")

    def notional(t):
        count = float(t.get("count_fp") or 0)
        taker = str(t.get("taker_side") or "").lower()
        key = "yes_price_dollars" if taker == "yes" else "no_price_dollars"
        return count * float(t.get(key) or 0)

    def series_floor(ticker):
        return float(by_series.get(signal_log.series_of(ticker), floor))

    big = [t for t in trades if notional(t) >= series_floor(t.get("ticker") or "")]
    missed = [t for t in big if (t.get("ticker") or "") not in watched_tickers]
    distinct = {t.get("ticker") for t in trades if t.get("ticker")}
    watched_active = distinct & watched_tickers

    times = []
    for t in trades:
        try:
            times.append(datetime.fromisoformat((t["created_time"]).replace("Z", "+00:00")).timestamp())
        except (KeyError, ValueError, AttributeError):
            pass
    span_sec = (max(times) - min(times)) if len(times) > 1 else 0.0

    if not big:
        # No qualifying print in the sample is NOT a pass - it's no
        # evidence either way. Reporting "ok" here would be the same
        # "looks plausible, means nothing" failure this module exists to
        # catch; a short sample at a quiet moment must not read as a
        # clean bill of health.
        return Check(
            "coverage", _UNKNOWN,
            f"no whale print cleared the notional floor in this {span_sec:.0f}s sample "
            f"({len(trades)} trades) — insufficient evidence, re-run with more pages",
            detail={"sampled_trades": len(trades), "window_sec": round(span_sec, 1),
                    "distinct_markets_trading": len(distinct),
                    "watched_and_trading": len(watched_active),
                    "watchlist_size": len(watched_tickers), "qualifying_prints": 0},
        )
    miss_pct = 100.0 * len(missed) / len(big)
    status = _OK if miss_pct < 10 else (_WARN if miss_pct < 50 else _FAIL)
    per_hour = (len(missed) / span_sec * 3600) if span_sec > 0 else None
    return Check(
        "coverage", status,
        f"{len(missed)}/{len(big)} qualifying whale prints ({miss_pct:.0f}%) were on markets "
        f"the app does not watch — {len(watched_active)}/{len(distinct)} actively-trading markets are in the watchlist"
        + (f"; ~{per_hour:,.0f} missed prints/hour at this rate" if per_hour else ""),
        detail={
            "sampled_trades": len(trades), "window_sec": round(span_sec, 1),
            "distinct_markets_trading": len(distinct), "watched_and_trading": len(watched_active),
            "watchlist_size": len(watched_tickers),
            "qualifying_prints": len(big), "missed": len(missed),
            "miss_pct": round(miss_pct, 1), "missed_per_hour_est": round(per_hour) if per_hour else None,
        },
        evidence=[{"ticker": t.get("ticker"), "notional": round(notional(t), 2),
                   "yes_price": t.get("yes_price_dollars")} for t in missed[:25]],
    )


# ---------------------------------------------------------------- runner

def run_offline(cfg: dict, since_ts: float | None = None, now: float | None = None) -> dict:
    """Every check that reads only local stores - no network, safe to call
    on any tick. check_coverage is deliberately excluded (it makes real API
    calls); callers that want it await it separately and merge the result."""
    checks = [
        check_threshold_integrity(cfg, since_ts, now),
        check_price_band_adherence(cfg, since_ts, now),
        check_runway_at_entry(cfg, since_ts, now),
        performance_by_epoch(since_ts, now),
    ]
    worst = _OK
    for c in checks:
        if c.status == _FAIL:
            worst = _FAIL
            break
        if c.status == _WARN and worst == _OK:
            worst = _WARN
    return {
        "generated_at": now if now is not None else time.time(),
        "overall": worst,
        "checks": [c.to_dict() for c in checks],
    }
