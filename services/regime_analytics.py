"""
Time-of-day / day-of-week / category performance segmentation - Gap 9 of
docs/config-tuning-data-gaps-2026-08-10.md, and the "time-of-day/liquidity-
regime awareness" question deep-scan Finding 7 (docs/platform-deep-scan-
findings-2026-08-10.md) already flagged as lower priority.

by_hour_of_day()/by_day_of_week() were the original, immediately-buildable
half - both derivable directly from entry_timestamp, a field every trade
row already carries. by_category() (2026-08-10, direct follow-up request:
"add those things, and have them auto-enable... once there *is* enough
data") needed a real new persistence layer first - market_catalog.category
is watchlist-scoped and rotates, so a historical trade couldn't be
reliably joined back to the category its market belonged to at entry time
without one. services/trade_category.py now captures that category at the
moment of entry; this module just joins onto it and buckets, same shape
as the other two functions - naturally "auto-enables" the same way every
other real-data-gated panel in this app does: an empty/thin lookup just
means an empty result, not an error, until enough trades have a recorded
category.
"""
import time
from collections import defaultdict

from services import trade_analytics, trade_category


def by_hour_of_day(rows: list[dict]) -> list[dict]:
    """rows: trade_analytics.build_trade_history()-shaped. Buckets by UTC
    hour (0-23) at entry_timestamp, one row per hour that actually has
    data (never a fabricated 24-row table with empty hours). Reuses
    compute_summary() per bucket so this can't silently drift from what
    the aggregate summary already says."""
    buckets: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("entry_timestamp") is None:
            continue
        buckets[time.gmtime(r["entry_timestamp"]).tm_hour].append(r)
    return [
        {"hour_utc": hour, **trade_analytics.compute_summary(buckets[hour])}
        for hour in sorted(buckets)
    ]


def by_day_of_week(rows: list[dict]) -> list[dict]:
    """0=Monday..6=Sunday, Python's time.struct_time.tm_wday convention."""
    buckets: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("entry_timestamp") is None:
            continue
        buckets[time.gmtime(r["entry_timestamp"]).tm_wday].append(r)
    return [
        {"day_of_week": dow, **trade_analytics.compute_summary(buckets[dow])}
        for dow in sorted(buckets)
    ]


def by_category(rows: list[dict]) -> list[dict]:
    """rows: trade_analytics.build_trade_history()-shaped. Joins onto
    services/trade_category.py's ticker->category lookup (populated going
    forward from the moment that module shipped - trades entered before
    then have no recorded category and are excluded, same as any other
    "not enough data yet" gap in this app, not backfilled with a guess).
    Sorted by count descending so the categories with the most real data
    surface first."""
    categories = trade_category.categories_for_tickers([r["ticker"] for r in rows])
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        category = categories.get(r["ticker"])
        if category is None:
            continue
        buckets[category].append(r)
    out = [
        {"category": category, **trade_analytics.compute_summary(group)}
        for category, group in buckets.items()
    ]
    out.sort(key=lambda b: -b["total_closed"])
    return out
