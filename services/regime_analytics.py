"""
Time-of-day / day-of-week performance segmentation - Gap 9 of docs/config-
tuning-data-gaps-2026-08-10.md, and the "time-of-day/liquidity-regime
awareness" question deep-scan Finding 7 (docs/platform-deep-scan-findings-
2026-08-10.md) already flagged as lower priority. A partial implementation,
disclosed rather than silently narrowed: this covers hour-of-day/day-of-
week, both derivable directly from entry_timestamp, a field every trade
row already carries. Category segmentation (the other half Finding 7/Gap 9
both mention) is NOT attempted here - market_catalog.category is
watchlist-scoped and rotates, so joining a historical trade back to the
category its market belonged to at entry time would need a real new
persistence layer (a category snapshot at trade-open time), not just an
aggregation pass over data that already exists. Noted as the natural next
step, not implemented.
"""
import time
from collections import defaultdict

from services import trade_analytics


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
