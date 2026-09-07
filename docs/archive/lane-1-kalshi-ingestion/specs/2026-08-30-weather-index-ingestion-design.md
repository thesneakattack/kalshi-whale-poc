# Weather Index Ingestion — Design Spec

Date: 2026-08-30. Status: brainstormed, corrected during self-review after
two verification passes; REVIEWED 2026-08-31 (self-review + independent
adversarial review + consolidation, GO with the city-ranking correction
below); implementation plan follows in
`docs/archive/lane-1-kalshi-ingestion/plans/2026-08-31-weather-index-ingestion.md`.

## What this is, and what it deliberately is not

**Ingestion only.** This builds the settlement_edge-**candidate** half of
what already exists for crypto (`services/index_feed/` ingesting CF
Benchmarks/Pyth), and stops there. It does **not** build:

- Any empirical validation study (the `services/settlement_edge.py`
  equivalent — scoring a projection against the market's own price).
- Any entry logic (the `services/settlement_edge_entry.py` equivalent).

Those are explicitly out of scope, by direct decision during brainstorming.
Reason: crypto's entry module was only built after the underlying edge was
proven real with 27,534 scored observations across 478 windows
(`services/settlement_edge_entry.py`'s own docstring). Building anything
past ingestion for weather now would mean acting on an unvalidated
assumption — the opposite of how this codebase treats that risk everywhere
else. Whether an edge exists here at all is an open, genuinely uncertain
question this spec does not answer.

## Why this is not a port of the crypto pattern

Two facts, both verified against source before this design was written,
not assumed from the category name:

1. **Different settlement statistic.** Crypto markets settle on the
   average of the last 60 one-second index observations before close — a
   fixed, known-length window (`services/index_feed/settlement_algebra.py`'s
   own docstring: "the final sixty one-second index observations ARE the
   settlement"). Temperature markets settle on the **maximum or minimum**
   temperature over an entire calendar day (verified against a live
   market's own `rules_primary`: *"If the maximum temperature recorded at
   New York City (CLINYC) for Aug 31, 2026, is greater than 85° fahrenheit
   according to The Weather Company..."*). A running max/min over ~1440
   one-minute readings is an order-statistics problem, not an
   average-of-a-fixed-window problem. Nothing in `settlement_algebra.py`'s
   math transfers.
2. **Different, unconfirmed source relationship.** Crypto's index-to-
   settlement relationship is exact and already proven (that's the whole
   premise `settlement_algebra.py` is built on). For weather, the
   market's settlement source is **The Weather Company**; Kalshi's own new
   index (`GET /live_data/weather/{city}`) is built from Kalshi's own
   "member station" quorum — a *related* but not confirmed-identical data
   source. The market's own `rules_secondary` text says: *"Preliminary
   Weather Company data may be subject to rounding and conversion
   differences from the final reported value."* If even the authoritative
   source disagrees with its own preliminary readings sometimes, Kalshi's
   independently-computed index tracking it closely enough to be
   predictive is an empirical question this spec does not attempt to
   answer — ingestion only accumulates the data a future validation study
   would need.

## Mechanism

**REST polling, not streaming — confirmed, not assumed.** Exhaustively
checked every `docs/kalshi/*.md` file mentioning "weather" (7 files:
`get-weather-index.md`, `get-event-live-data.md`, `get-series.md`,
`get-series-list.md`, `changelog-index.md`, `terms.md`, `README.md`) and
`docs/kalshi/websockets.md` directly — no WebSocket channel exists for this
data. `GET /trade-api/v2/live_data/weather/{city}` is the only mechanism.

**Do not confuse with the existing `get-event-live-data` polling.** This
app already polls a *different*, already-solved endpoint
(`services/market_watch/event_metadata.py`'s `_fetch_event_live_data`,
feeding the frontend's `weatherLiveSummary`) that returns per-*event*
display fields (temperature, humidity, wind) for user-facing convenience.
The new endpoint is per-*city*, independent of any event, and is
specifically "the canonical minute-resolution series behind hourly
temperature markets" — the settlement-relevant one. These are two separate
things; this spec is only about the new one.

**Polling cadence:** the index's own resolution is one minute
(`get-weather-index.md`: "minute-resolution series"), so 60s is the
natural floor for the poll interval. Each poll requests a **trailing
overlapping window** via the endpoint's own `last_sec` parameter
(confirmed: "Trailing window in seconds; equivalent to `from=now-last_sec`,
`to=now`. Mutually exclusive with `from`/`to`") rather than just the newest
point, so a single delayed or missed poll cannot silently create a gap —
the next poll's window still covers it. Exact interval and per-city REST
cost against this account's real rate-limit budget must be checked before
this ships; not decided here, per this repo's rule against picking a
polling frequency without a measured basis.

## Scope: which cities

Recommended starting set, by real 24h volume in the app's own catalog
(`data/market_catalog.db`, `KXHIGH%` series, queried 2026-08-30):
`KXHIGHLAX` (1,286,281.77 — a clear 4.5× gap over the next), `KXHIGHNY`
(286,585.86), `KXHIGHMIA` (281,497.43), `KXHIGHCHI` (247,611.21).

**Caveat, load-bearing (original, 2026-08-30):** this ranking was built on
catalog rows that were **4 days stale** (`KXHIGH%` max `updated_at` =
2026-08-26, vs. minutes-old for `KXNFL%`/`KXMLB%` the same day). Root cause
identified and recorded separately (`docs/open-decisions.md`, 2026-08-30):
`config/settings.yaml`'s `kalshi.categories` was `[Sports]` only, so the
per-category catalog-scan discovery mechanism never touched Climate/weather
at all. **Re-confirm this city ranking against fresh data before
implementation.**

**Re-confirmed 2026-08-31, via this design's review cycle** (self-review +
independent adversarial-review Agent call + consolidation — see
`...-design-review.md` / `...-design-consolidation.md` in this directory):
`kalshi.categories` was widened to all 11 categories the same day this
caveat was written, and a fresh, independently-reproduced live query
(`data/market_catalog.db`, `updated_at` 2026-08-30T18:11:54Z) gives a
**different ranking**: `KXHIGHLAX` (1,474,143.90) > `KXHIGHMIA`
(359,090.28) > `KXHIGHNY` (343,419.24) > `KXHIGHCHI` (267,427.45) — MIA and
NY have swapped — plus several previously-invisible cities close behind
CHI: `KXHIGHTHOU` (209,520.81), `KXHIGHTDAL` (199,374.75), `KXHIGHAUS`
(187,801.77). This read is still mid-cycle (`catalog_scan.py` batches only
10 series/scan and hadn't finished a full pass through the widened category
set at check time), not a settled steady-state number — the implementation
plan re-runs this query once more before finalizing the starting city list.

The exact `city` path-parameter spelling (`get-weather-index.md`'s example
is `miami`, lowercase) must be verified against Kalshi's real city-ID list
before coding — not inferred from the `KXHIGH*` series-ticker suffixes,
which use a different naming convention (`LAX`, `NY`, `MIA`, `CHI`) that
will not directly match the endpoint's own city IDs.

A curated default list, not "every city the app sees markets for" — same
precedent as `index_feed.DEFAULT_INDEX_IDS = ["BRTI", "ETHUSD_RTI"]`, a
short, commented, deliberately narrow set rather than exhaustive coverage
from day one.

## Module and schema

New package `services/weather_index/` (own `ingestion.py`), not an
extension of `services/index_feed/`. `index_feed`'s own docstring calls
itself "the streamed-in half" of its concern — its shape (WS push,
per-second ticks, buffer-then-batch-flush) is built around a mechanism
this data source does not have. Same conceptual role, different mechanism,
so it gets its own package per this repo's "split by responsibility, not
technical layer" convention, without inheriting code that assumes
streaming.

```python
DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "weather_index.db"
```

(Three `.parent`s: `services/weather_index/ingestion.py` → `services/weather_index/`
→ `services/` → repo root → `data/weather_index.db` — verified against
`index_feed.ingestion`'s real `DB_PATH` line, not assumed from a shallower
module's path depth.)

```sql
CREATE TABLE IF NOT EXISTS weather_index_ticks (
    city TEXT NOT NULL,
    minute_ts INTEGER NOT NULL,      -- unix seconds, minute-aligned
    value REAL,                      -- Fahrenheit, 0.01 precision
    raw_json TEXT NOT NULL,          -- the full point as Kalshi sent it (detailed=true)
    polled_at REAL NOT NULL,
    PRIMARY KEY (city, minute_ts)
)
```

- **Fidelity:** poll with `detailed=true` and store the raw JSON point
  verbatim, not just the extracted value — mirrors `index_feed`'s own
  `json.dumps(msg, default=str)` storage of the full CF Benchmarks frame.
  Preserves the per-station quality-control breakdown for a future
  validation study without needing to re-fetch it.
- **A quorum-failed minute gets no row at all** — never a NULL value row,
  never a zero. `get-weather-index.md` is explicit: "Minutes where the
  index quorum failed carry no value and are never returned as points, so
  gaps in the series are real gaps." This app already has the identical
  principle elsewhere (`_record_disconnect`'s negative-gap handling:
  "dropped rather than recorded as fabricated data").
- **Write path: direct upsert per poll, not a batch buffer.** Corrected
  during self-review — `record_cfbenchmarks`'s 200-row buffer-then-flush
  fits a per-second WS tick rate; at a 60s poll cadence it could sit
  unflushed for hours. And unlike a trade print, a preliminary weather
  reading can be revised by quality control after the fact (per
  `get-weather-index.md`'s own quorum/QC framing), so a natural-key
  `INSERT OR IGNORE` (correct for immutable trade prints) would freeze a
  since-corrected value forever. The right precedent is
  `services/capture_writer.py`'s **upsert** store mode: "a second
  submit() for the same key overwrites the first... latest observed value
  wins." `INSERT INTO weather_index_ticks (...) VALUES (...) ON
  CONFLICT(city, minute_ts) DO UPDATE SET value=excluded.value,
  raw_json=excluded.raw_json, polled_at=excluded.polled_at`.

## Error handling

A failed poll (network error, non-200) logs a fault via `fault_log` and
retries on the next cycle — same shape as every other scheduler in this
app. Never crashes the poller; never fabricates a value for a failed poll.

## Testing

TDD, in the established style:

- Raw-payload fidelity: a stored row's `raw_json` round-trips to exactly
  what the (mocked) endpoint returned.
- The quorum-gap-is-really-absent semantic: a response with a missing
  minute results in no row for that minute — not a null, not
  interpolated.
- Overlapping polling windows don't create duplicate/conflicting rows: two
  polls covering the same minute upsert to one row, and if the two reads
  differ (simulating a QC revision), the second (later-polled) value wins.
- A failed poll is fault-logged and does not stop the next scheduled poll.

## Explicitly out of scope (tracked as future decisions, not built here)

- Any settlement-edge validation study for weather (would need its own
  brainstorm once real data has accumulated — the crypto precedent needed
  months of data before the Brier comparison was meaningful).
- Wiring this into any strategy or entry path.
- Widening `kalshi.categories` beyond Sports (tracked separately,
  `docs/open-decisions.md`, 2026-08-30).
