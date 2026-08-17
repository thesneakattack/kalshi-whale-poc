"""
Live CF Benchmarks / Pyth index values - the actual quantity several Kalshi
series settle against, streamed over websocket instead of inferred from
contract prices.

Direct goal (2026-08-17): "get ready to make this a microsecond-reactive
trading platform." This module is the reason that phrase can mean something
concrete here, because for the 15-minute crypto series it is not a signal -
it is partial knowledge of the answer, delivered before the market closes.

WHY THIS IS DIFFERENT FROM EVERY OTHER INPUT IN THIS APP

KXBTC15M's own rules_primary, read live from the API 2026-08-17:

    "If the simple average of the sixty seconds of CF Benchmarks' BRTI
     before 1:30 AM EDT on Aug 17, 2026 is at least the simple average of
     the sixty seconds of CF Benchmarks' BRTI before 1:15 AM EDT on
     August 17, 2026, then the market resolves to Yes."

and that market carried `floor_strike: 63485.16` - the previous quarter's
60-second average, already fixed and known. So settlement is decided by one
number: the mean of sixty one-second BRTI observations in the final minute.

docs/kalshi/cfbenchmarks-value.md's `last_60s_windowed_average_15min` is
that exact average, streamed as it accumulates:

    "present only in the final minute before quarter-hour close (:00, :15,
     :30, :45). Active accumulation window is (quarter_close_ts_ms - 60000,
     quarter_close_ts_ms]. The start-boundary tick is excluded and the
     close tick is included. This produces second-indexed counts:
     :01 -> 1, :14 -> 14, :59 -> 59, close tick -> 60"

`window_size` is therefore literally how many of the sixty settlement
observations are already known, and `value` is their running mean. At
window_size 30 the settlement average is half-determined and the residual
uncertainty is arithmetic, not opinion - see required_remaining_average(),
which inverts it exactly.

Everything else in this app estimates what a market will do. This measures
how much of it has already happened.

DISCIPLINE

- Read-only with respect to trading: this module records and computes,
  nothing here places or closes anything.
- Buffered writes, same reasoning as services/series_watcher.py - ticks
  arrive ~1/second per index, and a per-tick sqlite3.connect() on the event
  loop is the pattern that froze the app on 2026-08-11.
- Projections degrade to None with a stated reason rather than guessing.
  A number that says "89% likely" when it is really "no data yet" is worse
  than no number, and this module is specifically the one that would be
  trusted.
"""
import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "index_feed.db"

# Sixty one-second observations per settlement window - the "sixty seconds"
# named in the market rules and the 60 in cfbenchmarks-value.md's own
# ":59 -> 59, close tick -> 60" enumeration. Not a tuning knob.
SETTLEMENT_WINDOW_TICKS = 60

# CF Benchmarks index IDs this app cares about by default. BRTI is the one
# KXBTC15M/KXBTCD settle against (confirmed from rules_primary, above);
# ETHUSD_RTI is its ETH counterpart. "all" is supported by the channel and
# is what the config uses when nothing narrower is set.
DEFAULT_INDEX_IDS = ["BRTI", "ETHUSD_RTI"]

_latest: dict[str, dict] = {}
_tick_buffer: list[tuple] = []
_FLUSH_BATCH = 200
_dropped_rows = 0


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS index_ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_id TEXT NOT NULL,
            source TEXT NOT NULL,
            observed_at REAL NOT NULL,
            received_at_ms INTEGER,
            source_ts_ms INTEGER,
            value REAL,
            avg_60s_value REAL,
            avg_60s_window_size INTEGER,
            q15_value REAL,
            q15_window_size INTEGER,
            raw_json TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_index_ticks ON index_ticks (index_id, observed_at)")
    # Only rows inside a settlement window carry q15_window_size, and those
    # are the ones every later reconstruction of "what did we know, when"
    # will query - worth their own partial index rather than scanning the
    # full ~86k rows/day/index that the 1Hz stream produces.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_index_ticks_q15 ON index_ticks (index_id, observed_at) "
        "WHERE q15_window_size IS NOT NULL"
    )
    return conn


def _float(value) -> float | None:
    """CF Benchmarks and Pyth both emit values as STRINGS formatted to 8
    decimal places (cfbenchmarks-value.md / pyth-value.md), never as JSON
    numbers - so every one is converted explicitly rather than assumed
    numeric because one sample happened to parse."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_cf_data(raw: str | None) -> float | None:
    """The `data` field is "the raw CF Benchmarks JSON frame, as a string" -
    a nested JSON document, not an object, so it needs a second parse. Its
    own `value` is the untouched upstream index level, which is the thing
    to compare a projection against; Kalshi's own averages sit beside it."""
    if not raw:
        return None
    try:
        return _float((json.loads(raw) or {}).get("value"))
    except (ValueError, TypeError, AttributeError):
        return None


def record_cfbenchmarks(msg: dict, now: float | None = None) -> bool:
    """Persist one `cfbenchmarks_value` message and update the in-memory
    latest. Never raises - this runs on the websocket handler."""
    try:
        index_id = msg.get("index_id")
        if not index_id:
            return False
        now = now if now is not None else time.time()
        avg60 = msg.get("avg_60s_data") or {}
        q15 = msg.get("last_60s_windowed_average_15min") or {}
        spot = _parse_cf_data(msg.get("data"))
        entry = {
            "index_id": index_id,
            "source": "cfbenchmarks",
            "observed_at": now,
            "received_at_ms": msg.get("received_at"),
            "value": spot,
            "avg_60s_value": _float(avg60.get("value")),
            "avg_60s_window_size": avg60.get("window_size"),
            # Present ONLY during the final minute before a quarter-hour
            # close. Its absence is meaningful (we are not in a settlement
            # window), so it is stored as NULL rather than defaulted to
            # anything.
            "q15_value": _float(q15.get("value")) if q15 else None,
            "q15_window_size": q15.get("window_size") if q15 else None,
            "q15_window_end_ts_ms": q15.get("window_end_ts_exclusive") if q15 else None,
        }
        _latest[index_id] = entry
        _tick_buffer.append((
            index_id, "cfbenchmarks", now, msg.get("received_at"), None, spot,
            entry["avg_60s_value"], entry["avg_60s_window_size"],
            entry["q15_value"], entry["q15_window_size"],
            json.dumps(msg, default=str),
        ))
        if len(_tick_buffer) >= _FLUSH_BATCH:
            flush()
        return True
    except Exception:
        return False


def record_pyth(msg: dict, now: float | None = None) -> bool:
    """Persist one `pyth_value` message. Pyth carries no windowed averages -
    it is a straight price for an underlying ticker - so the settlement-
    projection fields stay NULL and only `value` is populated."""
    try:
        ticker = msg.get("underlying_ticker")
        if not ticker:
            return False
        now = now if now is not None else time.time()
        value = _float(msg.get("value_usd"))
        _latest[ticker] = {
            "index_id": ticker, "source": "pyth", "observed_at": now,
            "received_at_ms": msg.get("received_at"), "source_ts_ms": msg.get("source_ts_ms"),
            "value": value, "avg_60s_value": None, "avg_60s_window_size": None,
            "q15_value": None, "q15_window_size": None,
        }
        _tick_buffer.append((
            ticker, "pyth", now, msg.get("received_at"), msg.get("source_ts_ms"),
            value, None, None, None, None, json.dumps(msg, default=str),
        ))
        if len(_tick_buffer) >= _FLUSH_BATCH:
            flush()
        return True
    except Exception:
        return False


def flush() -> dict:
    global _tick_buffer, _dropped_rows
    rows, _tick_buffer = _tick_buffer, []
    if not rows:
        return {"ticks": 0}
    try:
        with _connect() as conn:
            conn.executemany(
                "INSERT INTO index_ticks (index_id, source, observed_at, received_at_ms, "
                "source_ts_ms, value, avg_60s_value, avg_60s_window_size, q15_value, "
                "q15_window_size, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
    except Exception:
        _dropped_rows += len(rows)
        return {"ticks": 0, "dropped": len(rows)}
    return {"ticks": len(rows)}


def latest(index_id: str) -> dict | None:
    return _latest.get(index_id)


def snapshot() -> dict:
    """Everything currently known, for /api/state and the dashboard."""
    return {
        "indices": dict(_latest),
        "buffered_ticks": len(_tick_buffer),
        "dropped_rows": _dropped_rows,
    }


# ------------------------------------------------------- settlement algebra

def required_remaining_average(
    partial_average: float, window_size: int, strike: float,
    total_ticks: int = SETTLEMENT_WINDOW_TICKS,
) -> float | None:
    """What the remaining seconds of a settlement window must average for
    the final 60-second mean to land exactly on `strike`.

    This is the whole point of the module, and it is exact arithmetic
    rather than a model. Settlement compares

        A = (1/N) * sum of N one-second observations

    against `strike`. After `window_size` = k observations we know their
    mean, so we know their sum, so the condition A >= strike rearranges to
    a condition on the mean of the N - k observations still to come:

        k*P + (N-k)*R >= N*strike
        R >= (N*strike - k*P) / (N - k)

    Returns that R. Compare it to the index's current spot value to read
    off how much has to change, and how fast, for the outcome to flip -
    a distance in dollars, not a confidence score.

    Returns None when k >= N (the window is complete, so there is nothing
    remaining and the outcome is already determined - callers should use
    the final average directly) or when the inputs are unusable."""
    try:
        k = int(window_size)
        n = int(total_ticks)
    except (TypeError, ValueError):
        return None
    if k <= 0 or n <= 0 or k >= n or partial_average is None or strike is None:
        return None
    return (n * float(strike) - k * float(partial_average)) / (n - k)


def settlement_projection(index_id: str, strike: float, side: str = "yes") -> dict:
    """Live read on a quarter-hour settlement, from the partial average
    currently streaming.

    Reports `status`:
      - "outside_window" - not in the final minute; nothing is determined
        yet and no projection is offered (deliberately: the honest answer
        before the window opens is "unknown," not an extrapolation).
      - "accumulating"   - inside the window, k of N observations known.
      - "determined"     - the window is complete; the outcome follows from
                           the final average with no uncertainty left.

    `required_remaining` and `gap_from_spot` are the actionable numbers:
    how far the index must average over the rest of the window, and how far
    that is from where it is trading right now. A large negative gap means
    YES is close to locked in; a large positive gap means the opposite."""
    entry = _latest.get(index_id)
    if entry is None:
        return {"status": "unknown", "reason": f"no tick received yet for {index_id}"}

    k = entry.get("q15_window_size")
    partial = entry.get("q15_value")
    spot = entry.get("value")
    if not k or partial is None:
        return {
            "status": "outside_window",
            "index_id": index_id, "spot": spot,
            "reason": (
                "last_60s_windowed_average_15min is absent, which per "
                "docs/kalshi/cfbenchmarks-value.md means we are not in the final minute "
                "before a quarter-hour close - no part of the settlement average exists yet"
            ),
        }

    n = SETTLEMENT_WINDOW_TICKS
    if k >= n:
        settles_yes = partial >= strike
        return {
            "status": "determined", "index_id": index_id, "strike": strike,
            "observations_known": k, "observations_total": n,
            "final_average": partial, "spot": spot,
            "settles_yes": settles_yes,
            "outcome_for_side": (settles_yes if side == "yes" else not settles_yes),
        }

    required = required_remaining_average(partial, k, strike, n)
    return {
        "status": "accumulating",
        "index_id": index_id, "strike": strike,
        "observations_known": k, "observations_total": n,
        "fraction_known": round(k / n, 4),
        "partial_average": partial,
        "spot": spot,
        "required_remaining": round(required, 4) if required is not None else None,
        # Positive: the index must trade ABOVE where it is now, on average,
        # for the rest of the window to settle YES. Negative: it can fall
        # this far and YES still lands.
        "gap_from_spot": round(required - spot, 4) if (required is not None and spot is not None) else None,
        "seconds_remaining": n - k,
    }


# Index identifiers as they appear in Kalshi's own rules_primary text,
# mapped to the channel's index_ids. This table exists because a live sweep
# of eight crypto series on 2026-08-17 found the rules naming indices
# THREE different ways, none of them guaranteed to match the channel:
#
#   KXBTC15M -> "BRTI"           KXETH15M -> "ETHUSDRTI"   (no underscore)
#   KXBTCD   -> "BRTI"           KXETHD   -> "ERTI"        (a third spelling)
#   KXSOLD   -> "SOLUSD_RTI"     KXXRPD   -> "XRPUSD_RTI"
#   KXDOGED  -> "DOGEUSD_RTI"
#
# So the index is NEVER parsed straight out of the rules and trusted.
# Resolution is: normalise (uppercase, strip non-alphanumerics) and look
# for an exact match, then consult this explicitly-verified alias table,
# then give up and say so. Guessing here would silently project one asset's
# settlement onto another's market.
#
# Verify the right-hand side against the channel's own `indexlist` action
# (KalshiTradeWebSocketClient.request_index_list) before adding entries -
# these are the identifiers the subscription accepts, which is a different
# question from what the prose calls them.
_INDEX_ALIASES = {
    "BRTI": "BRTI",
    "BITCOINREALTIMEINDEX": "BRTI",
    "ETHUSDRTI": "ETHUSD_RTI",
    "ERTI": "ETHUSD_RTI",
    "ETHEREUMREALTIMEINDEX": "ETHUSD_RTI",
    "SOLUSDRTI": "SOLUSD_RTI",
    "XRPUSDRTI": "XRPUSD_RTI",
    "DOGEUSDRTI": "DOGEUSD_RTI",
}

# The settlement structure this module can actually project: a simple mean
# of sixty one-second observations taken before the close. Verified present
# in KXBTC15M/KXETH15M/KXBTCD/KXETHD/KXSOLD/KXXRPD/KXDOGED and verified
# ABSENT in KXBTCMAXY, whose rules are a year-long barrier ("is above X
# starting <date> and before <date>") with no averaging window at all.
# Matching on the structure rather than on a series allowlist is what keeps
# a newly-listed series from being silently mis-projected.
_SIXTY_SECOND_PATTERNS = ("sixty seconds", "60 second", "60-second")


def _normalise_index_token(token: str) -> str:
    return "".join(ch for ch in (token or "").upper() if ch.isalnum())


def resolve_index_id(rules_primary: str) -> str | None:
    """Which streaming index ID this market's rules refer to, or None when
    it cannot be established. Never guesses - see _INDEX_ALIASES."""
    text = rules_primary or ""
    # Longest first, so "ETHUSD_RTI" wins over a bare "RTI"-like substring
    # and "BITCOINREALTIMEINDEX" isn't shadowed by something shorter.
    for alias in sorted(_INDEX_ALIASES, key=len, reverse=True):
        if alias in _normalise_index_token(text):
            return _INDEX_ALIASES[alias]
    return None


def settlement_spec(market: dict) -> dict:
    """Can this market's settlement be projected from the index stream, and
    with what index/strike/comparison? Pure - no network, no DB.

    Every one of these checks exists because a live sweep found a market
    that fails it. Nothing here is assumed:

      - `floor_strike` present    (KXDOGED returns None, strike_type
                                   "custom" - unprojectable)
      - `strike_type` understood  ("greater_or_equal" on the 15-minute
                                   series, "greater" on the dailies - the
                                   operator genuinely differs and cannot be
                                   assumed to be >=)
      - 60-second-average rules   (KXBTCMAXY is "greater" with a real
                                   floor_strike but is a year-long barrier
                                   market with no averaging window; passing
                                   the first two checks is not enough)
      - index resolvable          (see resolve_index_id)"""
    ticker = market.get("ticker")
    rules = market.get("rules_primary") or ""
    strike = market.get("floor_strike")
    strike_type = market.get("strike_type")

    def no(reason: str) -> dict:
        return {"supported": False, "ticker": ticker, "reason": reason,
                "strike_type": strike_type, "floor_strike": strike}

    if strike is None:
        return no("no floor_strike on this market — nothing to compare a projected average against")
    if strike_type not in ("greater", "greater_or_equal"):
        return no(f"strike_type {strike_type!r} is not a simple threshold this module can project")
    lowered = rules.lower()
    if not any(p in lowered for p in _SIXTY_SECOND_PATTERNS):
        return no("rules_primary describes no 60-second averaging window — settlement is not the "
                  "mean of sixty one-second observations, so the streaming partial average does "
                  "not apply to it")
    index_id = resolve_index_id(rules)
    if index_id is None:
        return no("could not establish which index this market settles against from its rules; "
                  "refusing to guess (see index_feed._INDEX_ALIASES)")
    return {
        "supported": True,
        "ticker": ticker,
        "index_id": index_id,
        "strike": float(strike),
        # ">=" for the 15-minute series, ">" for the dailies. Carried
        # through explicitly so a caller comparing a projected average to
        # the strike uses the operator the market actually settles on.
        "comparison": ">=" if strike_type == "greater_or_equal" else ">",
        "settlement_timer_seconds": market.get("settlement_timer_seconds"),
        "close_time": market.get("close_time"),
    }


def window_matches_close(entry: dict | None, close_ts: float | None,
                         tolerance_sec: float = 90.0) -> bool:
    """Is the settlement window currently accumulating the one THIS market
    settles on?

    Real bug, caught 2026-08-17 by checking the recorded data instead of
    trusting the wiring: the q15 window opens before EVERY quarter-hour
    (:00, :15, :30, :45), but a KXBTCD market settles at 17:00 and a
    KXBTC15M market at the next quarter. Matching only on index_id recorded
    59 observations against five KXBTCD markets whose close was still 863
    minutes away - the partial average accumulating toward 06:00 says
    exactly nothing about a market settling at 17:00, and those rows would
    have gone straight into the Brier comparison as if it did.

    The channel hands us the answer directly: `window_end_ts_exclusive` is
    the close this average is accumulating toward. Compare it to the
    market's own close_time and require them to be the same instant.

    Tolerance is deliberately loose (90s, i.e. wider than the 60-second
    window itself but far tighter than the 15-minute spacing between
    windows): close_time and the window boundary are published by different
    systems and need not agree to the millisecond, but they can never be a
    whole quarter-hour apart and still refer to the same settlement."""
    if not entry or close_ts is None:
        return False
    end_ms = entry.get("q15_window_end_ts_ms")
    if not end_ms:
        return False
    return abs(float(end_ms) / 1000.0 - float(close_ts)) <= tolerance_sec


def recent_volatility(index_id: str, lookback_sec: float = 900.0,
                      now: float | None = None) -> float | None:
    """Standard deviation of one-second index moves over the lookback -
    the scale against which `gap_from_spot` becomes meaningful. A $5 gap is
    nearly decided on a quiet index and wide open on a violent one.

    Deliberately NOT folded into settlement_projection: that function is
    exact arithmetic on data Kalshi published, this is an estimate from
    history, and blending the two into one number would hide which half is
    which."""
    now = now if now is not None else time.time()
    try:
        with _connect() as conn:
            rows = [r[0] for r in conn.execute(
                "SELECT value FROM index_ticks WHERE index_id = ? AND observed_at >= ? "
                "AND value IS NOT NULL ORDER BY observed_at",
                (index_id, now - lookback_sec),
            )]
    except sqlite3.Error:
        return None
    if len(rows) < 30:
        return None
    diffs = [b - a for a, b in zip(rows, rows[1:])]
    mean = sum(diffs) / len(diffs)
    var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
    return var ** 0.5


def tick_stats(index_id: str | None = None) -> dict:
    try:
        with _connect() as conn:
            if index_id:
                n, first, last = conn.execute(
                    "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM index_ticks "
                    "WHERE index_id = ?", (index_id,),
                ).fetchone()
            else:
                n, first, last = conn.execute(
                    "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM index_ticks"
                ).fetchone()
            in_window = conn.execute(
                "SELECT COUNT(*) FROM index_ticks WHERE q15_window_size IS NOT NULL"
                + (" AND index_id = ?" if index_id else ""),
                (index_id,) if index_id else (),
            ).fetchone()[0]
    except sqlite3.Error as exc:
        return {"error": str(exc)}
    return {
        "index_id": index_id, "ticks": n, "first_at": first, "last_at": last,
        "settlement_window_ticks": in_window,
        "buffered": len(_tick_buffer), "dropped_rows": _dropped_rows,
    }
