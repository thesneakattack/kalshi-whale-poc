# Kalshi Category Data Completeness — Design Spec

**Date:** 2026-08-30. **Status:** drafted headless (no live reviewer this pass — Stage 3 of
a 9-stage delegated pipeline); ready for its own review stage before `writing-plans`.
**Evidence:** `docs/archive/lane-1-kalshi-ingestion/research/2026-08-30-kalshi-category-data-shape-audit.md`
(finalized, two review+revision rounds, commit `2eaa78a`). Every citation below traces to
that document's own measurements or to the `docs/kalshi/` pages it cites — nothing here is
re-derived from memory, per CLAUDE.md's "never guess; verify or falsify."
**Constraints honoured:** paper mode unchanged; no trading/risk/sizing/calibration edit; no
`data/*.db` destroyed or rewritten; additive schema only (`CREATE TABLE IF NOT EXISTS` +
`_add_column_if_missing`, `monkeypatch DB_PATH` in tests); vendor-specific interpretation
stays inside `services/kalshi/` and its documented consumer set
(`.claude/rules/kalshi-integration-authority.md`); no new hot-path cost without measurement
(HARD RULE, "the data plane is the product").

## 0. How the four directions relate, and how deep each is designed here

The investigation ranked **D1 → D2 → D3 → D4** by evidence strength, and named D1 as the
dependency several others need (`Series.category`/`.tags` routing). This spec follows that
shape:

- **D1 gets a full design** (§1): schema, population mechanism, and a concrete migration
  for all three consumers the task named (`catalog_scan.py`, `signal_log.py`'s `series_of`,
  `market_catalog`).
- **D2 gets a full design** (§2): it's ranked #2, it's what makes D3's `truflation`/
  `artist_streams` pieces cheap rather than from-scratch, and its structured-target
  resolution is the same fix (X6) the investigation folds into it.
- **D3 gets a partial design** (§3): Climate-and-Weather (W1/W2) already has its own
  approved, unimplemented spec — `docs/superpowers/specs/2026-08-30-weather-index-ingestion-
  design.md`, committed hours before this investigation finished. This spec does not
  redesign it; it only states where D1/D2 plug into it. Commodities' Pyth feed (CM1/C3/C4)
  gets a real, concrete design — it's a config flip plus one new discovery call, not a new
  subsystem. `truflation`/`artist_streams` get a design that is explicitly "finish what D2
  already captures," not a new capture path. The forecast-percentile-history endpoint (X14)
  is named and explicitly **not designed** — the investigation itself flagged its
  authentication requirement as unverified, and this app's public-read path has never
  called an authenticated endpoint; designing storage/consumption for it before that's
  settled would be exactly the kind of un-falsified assumption the HARD RULE forbids.
- **D4 gets a light design** (§4), sized the way the investigation sized it: real, but
  ranked last because its value is episodic and election-calendar-driven. It reuses D1's
  series-metadata shape and D2's structured-target resolution rather than inventing its own.
- §5 lists what the investigation found but this spec deliberately does not design further,
  matching the investigation's own "Deliberately not proposed" list plus X10/X12/X13/X14 —
  named, not invented, not designed.

Every section below is additive to the running app: no existing consumer's behavior changes
until its migration step runs, and every migration step is described as a change to *how*
a value is produced, not a change to what any caller currently receives.

---

## 1. D1 — a real series-metadata store

### 1.1 What's being replaced and why it's safe to build alongside it, not instead of it

`services/series_cache.py` persists `state["series_cache"]` as one row: `{fetched_at,
series_json}`, the whole ~10,351-series list serialized as one JSON blob
(`services/series_cache.py:40-46`). That shape is exactly right for its one real consumer,
`catalog_scan._get_series_cache()` (`services/market_watch/catalog_scan.py:191-210`), which
always wants "the whole list, freshly TTL-checked" — sorted, filtered, and iterated by
`_get_top_series` and `_scan_catalog_batch`. **D1 does not replace that.** It adds a second,
queryable representation of the same already-fetched data, populated in the same call that
already writes the blob — a derived index, not a competing source of truth. This is the
literal "additive" reading of CLAUDE.md's schema rule: the existing table, its readers, and
its behavior are all untouched.

### 1.2 Schema — `services/series_cache.py`, same file, two new tables

Per-series row table, plus a tag junction table (tags are the investigation's own
highest-value single field — X2: "the single richest category-shaped signal already
sitting in the app's own storage" — and a per-series JSON array is queryable by `LIKE` but
not indexable; a junction table is what makes MN1's "generalize the min_contracts hand-list
to a tag" and EN1's "how have whales done on this artist's markets" real SQL, not scans).

```sql
CREATE TABLE IF NOT EXISTS series_metadata (
    ticker TEXT PRIMARY KEY,
    category TEXT,
    frequency TEXT,
    tags_json TEXT,                    -- full-fidelity round-trip; get-series.md:140-147
    settlement_sources_json TEXT,      -- [{name, url}]; get-series.md:148-156
    contract_url TEXT,
    contract_terms_url TEXT,           -- get-series.md:162-166
    fee_type TEXT,                     -- get-series.md:172-184 (quadratic/quadratic_with_maker_fees/
                                        -- quadratic_with_combo_maker_fees/flat)
    fee_multiplier REAL,
    additional_prohibitions_json TEXT, -- get-series.md:191-198
    exchange_index INTEGER,            -- Series' own exchange_index (get-series.md:208-212) -
                                        -- the series-level counterpart to X9's market/event-level gap;
                                        -- captured here at zero extra cost since it rides the same
                                        -- object, but does NOT substitute for X9's fix (markets/events
                                        -- still need their own exchange_index persisted for the
                                        -- collateral-preallocation requirement X9 names - out of
                                        -- scope here, see §5)
    volume_fp TEXT,                    -- fixed-point string, same convention as market_catalog.markets
    last_updated_ts TEXT,              -- Kalshi's own series-metadata update time (ISO 8601) -
                                        -- the field min_updated_ts filters against (§1.5)
    fetched_at REAL NOT NULL           -- local capture time, same convention as series_cache's blob row
)

CREATE TABLE IF NOT EXISTS series_tags (
    ticker TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (ticker, tag)
)
CREATE INDEX IF NOT EXISTS idx_series_metadata_category ON series_metadata (category)
CREATE INDEX IF NOT EXISTS idx_series_tags_tag ON series_tags (tag)
```

`additional_prohibitions` stays JSON-only (no junction table): its real use (P4/X4 — surface
the legal-exposure list to a human per series) is a per-series read, not a cross-series
query; a junction table there would be schema weight with no query it serves.

This is the same "structured columns + full JSON alongside them" idiom `game_state.py`
(`raw_json` plus `home_score`/`period`/...) and the weather-index spec (`raw_json` plus
`value`) both already use — fidelity (nothing lossy on the way in) and queryability
(indexed columns) aren't in tension here; the design keeps both.

### 1.3 Population — extend the existing hourly refresh, not a new job

`catalog_scan._get_series_cache()` already re-fetches **all** ~13.6k series
(`include_volume=True`) every `_SERIES_CACHE_TTL_SEC` (3600s), unconditionally, regardless
of `kalshi.categories` — the category filter is applied *after*, in `_get_top_series`/
`_scan_catalog_batch` (`catalog_scan.py:239-249`, `:337-341`). That means the fetch D1 needs
already happens, at zero extra API cost, and already covers every one of the real 17
categories the investigation found (§0 of the investigation), not just the 11 configured
ones — D4's `political_race`/Elections coverage and any future category widening both fall
out of this for free.

The population mechanism is therefore a **write-through extension of `series_cache.save()`**
(`services/series_cache.py:64-72`), not a new scan job or a read-through cache: the same
call that writes the one-row blob also upserts one `series_metadata` row and N `series_tags`
rows per series, from the exact same already-parsed `Series` objects. One transaction, one
already-scheduled call site (`catalog_scan.py:209`), zero new REST calls, zero new schedule
to reason about.

```python
def save(fetched_at: float, series: list[dict]) -> None:
    with _connect() as conn:
        conn.execute(  # existing blob write, unchanged
            "INSERT INTO series_cache (id, fetched_at, series_json) VALUES (0, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET fetched_at = excluded.fetched_at, series_json = excluded.series_json",
            (fetched_at, json.dumps(series)),
        )
        conn.executemany(  # new: one upsert per series
            "INSERT INTO series_metadata (ticker, category, frequency, tags_json, "
            "settlement_sources_json, contract_url, contract_terms_url, fee_type, fee_multiplier, "
            "additional_prohibitions_json, exchange_index, volume_fp, last_updated_ts, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(ticker) DO UPDATE SET category=excluded.category, frequency=excluded.frequency, "
            "tags_json=excluded.tags_json, settlement_sources_json=excluded.settlement_sources_json, "
            "contract_url=excluded.contract_url, contract_terms_url=excluded.contract_terms_url, "
            "fee_type=excluded.fee_type, fee_multiplier=excluded.fee_multiplier, "
            "additional_prohibitions_json=excluded.additional_prohibitions_json, "
            "exchange_index=excluded.exchange_index, volume_fp=excluded.volume_fp, "
            "last_updated_ts=excluded.last_updated_ts, fetched_at=excluded.fetched_at",
            [_series_metadata_row(s, fetched_at) for s in series],
        )
        tickers_this_batch = [s["ticker"] for s in series]
        conn.executemany(
            "DELETE FROM series_tags WHERE ticker = ?",
            [(t,) for t in tickers_this_batch],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO series_tags (ticker, tag) VALUES (?, ?)",
            [(s["ticker"], tag) for s in series for tag in (s.get("tags") or [])],
        )
```

(Delete-then-reinsert per ticker, not a diff — this hour's tag list for a series fully
replaces last hour's, since a tag can be removed upstream and a stale `series_tags` row
would otherwise never be cleaned up. `series` here is always the full fetched batch, same
as the existing blob write above it, so `tickers_this_batch` covers every series this
refresh touched.)

`_series_metadata_row()` reads the Series object with the same defensive `.get()` style
`_get_series_cache` already uses (`float(s.get("volume_fp") or 0)`), never assuming a field
is present — `get-series.md`'s own schema marks `tags`/`settlement_sources`/
`additional_prohibitions` all `nullable: true`.

### 1.4 Consumer migration — the three the task named

**`catalog_scan.py` (population side).** No behavior change: `_get_series_cache`,
`_get_top_series`, `_scan_catalog_batch` keep reading `state["series_cache"]`/the blob
exactly as today. They gain nothing and lose nothing from D1 directly — they're the
*producer* D1 taps, not a consumer needing migration. (`market_catalog.upsert_markets`
already receives `s.get("category")`/`s["ticker"]` from this same list per market batch —
unaffected.)

**`market_catalog` (join-key side).** `market_catalog.markets` already carries its own
`series_ticker`/`category` columns per market (`services/market_catalog/market_catalog.py:77-91`,
populated by `upsert_markets(series_ticker, category, markets, ...)`). No schema change
here either: `series_metadata.ticker` and `market_catalog.markets.series_ticker` share the
same value domain (a real series ticker), so any new code that needs a market's series-level
metadata joins `markets.series_ticker = series_metadata.ticker` directly. D1's job for this
consumer is providing the table to join against, not changing `market_catalog` itself.

**`signal_log.series_of()` — the one real behavior change, and the one CLAUDE.md/
`terms.md:29` flags directly.** `series_of(ticker)` (`services/signal_log.py:152-161`) does
`ticker.split("-")[0]` — the exact pattern `terms.md:29` says not to do ("do not parse
ticker strings to infer relationships... rely on fields like `series_ticker`... category,
and tags"), and the CHEATSHEET already records the one shipped bug from it
(`KXMVECROSSCATEGORY0-SHARD1` parsed as its own series instead of the real
`KXMVECROSSCATEGORY0`). The correct chain doesn't need D1 at all — it's already sitting in
`title_cache.py`: `market_titles.event_ticker` → `event_titles.series_ticker`
(`services/title_cache.py:68-84`, `:109`), exactly the join `title_cache.
fee_override_for_ticker()` already performs for a different field
(`services/title_cache.py:246-266`). D1 adds the *destination* table this chain's answer can
be enriched against (category/tags/etc.); the chain itself is a `title_cache.py` addition:

```python
def series_ticker_for(ticker: str) -> str | None:
    """(same shape/precedent as fee_override_for_ticker) - one indexed join,
    market_titles.event_ticker -> event_titles.series_ticker. None when the
    market or its event isn't cached yet - never a guessed value."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT et.series_ticker FROM market_titles mt JOIN event_titles et "
            "ON et.event_ticker = mt.event_ticker WHERE mt.ticker = ?",
            (ticker,),
        ).fetchone()
    return row[0] if row and row[0] else None
```

`series_of()` becomes:

```python
def series_of(ticker: str) -> str:
    if not ticker:
        return ticker
    real = title_cache.series_ticker_for(ticker)
    return real or ticker.split("-")[0]  # unseen-ticker fallback, not the general case anymore
```

Signature and every call site are unchanged: `strategy_engine.py`'s `excluded_series` gate
(`:499-501`), its position-concurrency-by-series count (`:42-43`), `series_stats`/
`series_stats_bulk` (`signal_log.py:339`, `:380`) — all keep working with no edit, and get
a materially more correct answer for any ticker whose real `series_ticker` differs from its
ticker prefix (the MVE/sharded case named above, and any future one this doesn't need to
anticipate by name). This is the "migrate without a flag-day rewrite" the task asked for:
one function body changes, its contract doesn't.

**Named, not glossed over: a real historical discontinuity.** `signals.series`
(`signal_log.py`'s `signals` table) is a **persisted, indexed column** written at
log-time with the old `series_of()` — 97,541+ existing rows already carry
ticker-prefix-derived values. Per CLAUDE.md, accumulated history is never rewritten to make
a number look different, so this design does **not** backfill `signals.series` for existing
rows.

**Correction, found in design review: the reason given above for skipping backfill was
factually backwards, and the real reason is a measured coverage number, not an
assumption.** `title_cache`'s `market_titles`/`event_titles` are *not* a rolling
watchlist cache — `services/state_view.py:142-145` and `main.py:956-958` both state
explicitly that they "accumulate unbounded for the app's whole lifetime." The table
that's actually horizon-bounded is `market_catalog.markets` (`market_catalog.py:216-225`,
"not a 'full' catalog by design... skip anything... scheduled well outside the near-term
horizon") — a different table, not proposed for this join. Ran the actual coverage query
rather than reasoning about it (`data/signal_log.db` × `data/title_cache.db`, live, this
session) — **and corrected in a second review pass, which caught that the first attempt
joined against the wrong table:** `series_ticker_for()` as designed resolves through
`market_titles ⋈ event_titles` (event_ticker → series_ticker), not `market_titles` alone.
Of the **12,357 distinct MVE/sharded tickers** behind the 14.85%-of-signals figure below,
90 have a `market_titles` row today, but running the *actual* proposed join —
`market_titles.event_ticker` against `event_titles.event_ticker` for those 90 — returns
**zero** rows with a non-null `series_ticker`. Real, measured backfill coverage for MVE/
sharded history is **0 of 12,357 tickers, 0 of 14,606 rows (0.0%)**, not the 0.7%/1.5%
an earlier draft of this section reported from the partial join. The **conclusion is
unchanged and now stronger — still don't backfill** — coverage is empirically zero
(MVE/sharded tickers are ephemeral, cycle out, and `market_titles`/`mve_scan.py`'s
MVE-specific discovery only shipped the same day as this investigation — issue #268 — so
none of the historical MVE tickers were ever cached with a resolvable series_ticker in the
first place), not because the cache is bounded. Concretely: for the large majority of series, ticker
prefix already equals the real `series_ticker` (confirmed by `config/settings.yaml`'s own
`excluded_series`/`min_contracts_by_series` keys — `KXBTC15M`, `KXTRUMPSAY`, etc. — which
work today precisely because that equality usually holds), so old and new rows agree and
there's no discontinuity to speak of. For the minority where they diverge — MVE/sharded
tickers are the confirmed case, 14.87% of all logged signals — a `series_stats()` query for
the *correct* series_ticker will undercount pre-cutover history (those old rows are filed
under the wrong prefix-derived key), and a query for the old wrong prefix will stop
accumulating new rows. This is real, bounded, and should be named at the cutover commit the
same way the "70% era" cost bug is named in this repo's history — a known accounting seam,
not a bug to chase further.

### 1.5 Phase 2 (separable, optional): stop the blind hourly full refetch

`get_series_list()` (`services/kalshi/public.py:116-138`) sends only `include_volume=True`.
`get-series-list.md`'s own params (`:71-108`) document `min_updated_ts` — "Filter series
with metadata updated after this Unix timestamp... use this to efficiently poll for
changes" — and `include_product_metadata` (currently always `false`, which is why
`product_metadata` is null on all 10,351 cached series today, X3). Phase 2: after the first
full sync, subsequent hourly refreshes pass `min_updated_ts=<max(last_updated_ts) already on
disk>` and `include_product_metadata=True`, and `series_metadata`'s upsert-by-ticker means a
series that didn't come back in a delta response simply keeps its last-known row — correct,
since `min_updated_ts` only omits series whose metadata *didn't change*, never one that
still exists. This is not a queue/rate/TTL tuning guess (the HARD RULE's own bar) — it's
adopting a parameter the documentation names for exactly this purpose. Kept as Phase 2,
not bundled into Phase 1, because it changes the shape of a real REST call and deserves its
own before/after call-count verification rather than riding in on a pure-persistence change.

### 1.6 D1 cleanup that rides along: X1

`main.py:869-873` stamps `event_meta["category_tags"] = tags_by_categories.get(category,
[])` onto every event — the identical facet-filter vocabulary on every event in a category,
already flagged as carrying zero per-event signal in `docs/kalshi/CHEATSHEET.md`'s own first
entry. Once `series_metadata`/`series_tags` exist, the real per-series tags are one join
away; this stamp should be deleted in the same change that lands D1's consumer migration,
not kept as a second, misleading "tags" field sitting next to a real one.

### 1.7 Real-time complement, explicitly deferred to its own task: X10

`market_lifecycle_v2`'s WS channel carries two message types this app drops today —
`event_lifecycle` (new event, with `series_ticker`/`exchange_index` attached,
`market-and-event-lifecycle.md:313,681`) and `event_fee_update`
(`fee_type_override`/`fee_multiplier_override`, `:821`) — because
`services/kalshi/websocket.py:109-123`'s `_CLASS_BY_MESSAGE_TYPE` has no entry for either,
so both land in `_OTHER_CLASS` and are discarded. This is the real-time complement to D1's
hourly REST population (a new series shows up on the WS the instant it's created, not up to
an hour later) and directly feeds the columns D1 just added (`exchange_index`, `fee_type`
via the already-cited `get-event-fee-changes.md:7` semantics: "If `fee_type_override` and
`fee_multiplier_override` are null, that indicates the override is cleared"). Named here
because it's tightly coupled to D1's data, but **not designed further in this pass**: it's
a hot-path WS handler change (new message classes, a new consumer), which per the HARD RULE
needs its own runtime-cost measurement before it ships, not a design bolted onto a
persistence spec. A future task, scoped narrowly: add two `_CLASS_BY_MESSAGE_TYPE` entries
and their handlers.

### 1.8 REST complement, missed by the original investigation sweep: the fee-changes endpoints

`get-series-fee-changes.md` (`GET /series/fee_changes`) and `get-event-fee-changes.md`
(`GET /events/fee_changes`) have no code path today (`grep -rn fee_changes services main.py
tools tests` → no output). **Correction, found in a second design review — the first pass
here guessed the call shape instead of reading the schemas:**

**Series-level (`/series/fee_changes`) — in scope for D1, one bulk call, not per-series.**
`series_ticker` is an *optional* filter and the response schema
(`GetSeriesFeeChangesResponse.series_fee_change_arr`) has no `limit`/`cursor` field
anywhere — omitting `series_ticker` returns the full array in one call, not ~10,351
calls/hour as the first draft of this section assumed. `show_historical=true` returns every
`{id, series_ticker, fee_type, fee_multiplier, scheduled_ts}` row, scheduled and past; the
selection rule for "what's currently effective" is the entry with the greatest
`scheduled_ts ≤ now` per `series_ticker` (ties broken by `id`, since the schema gives no
other ordering guarantee). One call, once per `_get_series_cache()` refresh cycle, written
into `series_metadata`'s existing `fee_type`/`fee_multiplier` columns (§1.2) — no per-series
pacing needed (`catalog_scan.py`'s `PACE_LIMIT` convention doesn't apply; there's no
per-series call to pace).

**Event-level (`/events/fee_changes`) — explicitly out of scope for this pass, not silently
dropped.** This endpoint *is* paginated (`limit` up to 1000, `cursor`-follow required) and
returns `event_fee_changes` keyed by `event_ticker`/`series_ticker` with
`fee_type_override`/`fee_multiplier_override` — a genuinely different shape (event-level
overrides layered on the series base, `get-event-fee-changes.md:7`) that `series_metadata`
(§1.2, series-scoped) has no column for today. It's the REST-poll twin of X10's
`event_fee_update` WS push (§1.7), and belongs with that deferred work — a future event-level
table, not bolted onto D1's series-level schema here.

**Rollout:** the series-level fee-changes call folds into **D1 Phase 1** (§6, item 1) — same
refresh cycle, same landing as the rest of `series_metadata`'s population; it needs no
separate rollout step. The event-level endpoint stays with X10, already named in §6 as
deferred, no new line needed.

---

## 2. D2 — a shared per-type milestone live-data extractor

### 2.1 The actual shape of the bug, precisely

Two call sites each make one single-key assumption about a `details` object
`targets_and_milestones.md:28` documents as "flexible JSON [that] varies by milestone
type":

- `services/market_watch/live_status.py:169`: `status = details.get("widget_status")` —
  drives `state["live_status_cache"]` and, via `game_state.record(et, details, ...)` two
  lines later, the persisted `game_states` table (`live_status.py:190-191`).
- `services/market_watch/catalog_scan.py:117`: `winner = details.get("winner")` —
  drives `propagate_milestone_winners`'s market-resolution path (`market_history.
  record_outcome`, `candidate_log`'s grading, `check_exits`'s position close).

Measured over the full 137,200-milestone / 112,501-live-payload census: **10,742 live
payloads (9.5%, 13.3%+ forward-dated) carry no `winner`**; **1,962 (1.7%, 28.4%
forward-dated) carry no `widget_status`**. The single largest hole is `esports_match`
(9,385 of the 10,742 winner-less), whose events resolve to **Sports** — a category this app
already trades — so `propagate_milestone_winners` is silently unable to resolve any esports
market today. The rest of the winner-less/status-less set —
`political_race`/`company_report`/`truflation`/`artist_streams`/`golf_tournament`/
`one_off_milestone`/`kpis`/`tv_views` — sit in Elections, Mentions, Economics, Entertainment,
Sports, and (for `kpis` only) the unconfigured Companies category.

### 2.2 Design: a dispatch-table extractor, one module, two call sites

New submodule inside the package both consumers already live in:
`services/market_watch/milestone_live_data.py` (not a new top-level package — both
`live_status.py` and `catalog_scan.py` are already `services/market_watch/*`, so this is an
internal split of that package, the same shape as the 2026-08-22 Phase 9/9 split that
produced both files in the first place).

**Correction, found at Stage 5 planning: the original `(None, None)`-for-unmapped design
below was itself a completeness regression.** Only a minority of the census's 44 types got
an explicit table entry (§2.3); every other type — including `basketball_game` (15,564
milestones) and `soccer_tournament_multi_leg` (15,546), the *2nd and 3rd largest*
populations in the whole census — would have silently gone from "works today via the naive
single-key read" to "explicitly `(None, None)`," a regression larger than the gap D2 exists
to close, directly against the data-plane HARD RULE. Fixed to a **default-pass-through**
architecture: an unmapped type falls through to the *same* naive `widget_status`/`winner`
read `live_status.py`/`catalog_scan.py` already do today, not a hardcoded absence. Only the
types §2.3 names as genuinely deviant (no standard fields, or needing special handling) get
an explicit override; every other type — the ~35 not named — keeps working exactly as it
does now, unchanged, while still being counted if truly novel:

```python
def extract(milestone_type: str, details: dict) -> dict:
    """Normalizes ANY milestone type's live-data `details` into
    {"status": "none"|"live"|"finished"|None, "winner": str|None}.
    A type with an explicit override (§2.3 - genuinely deviant per the
    census) uses it. Every other type - the majority, not the edge case -
    falls through to the SAME naive widget_status/winner pass-through
    live_status.py/catalog_scan.py already do today, so basketball_game/
    soccer_tournament_multi_leg/etc. (15,564/15,546 milestones, currently
    working fine) see zero behavior change. Still counted either way
    (_record_unknown_type) so a genuinely new/never-seen type surfaces as
    a metric instead of silence - targets_and_milestones.md:28 documents
    `details` as varying by type, so "haven't seen this type before" and
    "this type needs special handling" are different signals, not one."""
    extractor = _EXTRACTORS.get(milestone_type)
    if extractor is not None:
        return extractor(details or {})
    if milestone_type not in _KNOWN_TYPES:  # genuinely new, not just unoverridden - see below
        _record_unknown_type(milestone_type)
    return {"status": details.get("widget_status"), "winner": details.get("winner")}
```

**`_KNOWN_TYPES` vs. `_EXTRACTORS`, kept as two separate sets, not one.** `_EXTRACTORS`
(§2.3) is small — only the types genuinely needing bespoke handling. `_KNOWN_TYPES` is the
census's full 44-type catalog (`P1`'s own enumeration) — every type this investigation has
already seen and accounted for, whether or not it needs an override. A type present in
neither set is what `_record_unknown_type` exists to catch: a genuinely new (45th+) type
Kalshi ships later, not `basketball_game` correctly using the default pass-through for the
30,000th time. Firing the counter unconditionally on every unoverridden type (an earlier
draft of this fix did exactly that) would drown the one signal this mechanism exists to
carry in noise from types that are working exactly as intended.

`live_status.py:169` becomes
`milestone_live_data.extract(ms["type"], details)["status"]`; `catalog_scan.py:117`
becomes `milestone_live_data.extract(ms["type"], details)["winner"]`. Both call sites
already have `ms["type"]` in hand (`ms = ms_result[0]` in both functions, gated on
`ms.get("id") and ms.get("type")`) — no new fetch, no new field.

**`_record_unknown_type`** is the "counter so the next unknown type surfaces as a metric
instead of silence" the investigation asked for — a `fault_log` entry or a
`GET /api/observability/summary`-visible counter (matching this repo's existing
`ingest.prefiltered.trade`-style counters), not a print statement that scrolls off. This is
the permanent-detection mechanism for recurrence the HARD RULE requires for any confirmed
bottleneck/gap class: the next time Kalshi ships a 45th milestone type, this counter is how
the team finds out instead of re-running a census.

### 2.3 Per-type extractors — grounded where the census established the shape, flagged where it didn't

| Type | Status | Winner |
|---|---|---|
| `tennis_tournament_singles` | pass-through `widget_status` (49,764/49,764 present — the control case, zero behavior change) | pass-through `winner` |
| `golf_tournament` | pass-through `widget_status` (present per S4's own key list) | **not designed here.** S4 names `leaderboard`/`round_label`/`current_round` as the real signal but the census didn't establish the leaderboard item shape (per-golfer rank/score fields). Ships as an explicit no-winner branch (safe, matches today's behavior) until a live `leaderboard` payload is read. |
| `esports_match` | derive from `is_live` (`"live"` if true) — **assumption, flagged per the HARD RULE**: the census names `is_live`/`home_score`/`away_score`/`home_periods`/`away_periods`/`series_stats`/`player_stats` but not a terminal/finished marker distinct from `is_live` going false. Before this ships: capture a handful of real `esports_match` payloads across one match's lifecycle (mirrors this repo's own tennis/AFL verification precedent, `services/kalshi/public.py:256-263`) and confirm what marks "finished" rather than "between games." | derive from `home_score`/`away_score` **only once status resolves to `"finished"`** by the rule above — never derived while still live, so a mid-match score never gets misread as a final result. |
| `political_race` | derive from `race_call_status`/`tabulation_status` — **assumption, flagged**: P3 names these fields but not their value vocabulary; must be read from a live payload before the status mapping ships (which values mean "not yet called" vs "called"). Maps into this app's existing tri-state vocabulary (`"none"`/`"live"`/`"finished"`, the same one `live_status.py:216`'s schedule fallback already uses). | pass-through `winner` where present (810/917 payloads carry the race-call field set per P3; the disjoint 36 carrying only `provider: votehub` correctly fall through to `None`, not a guess). |
| `company_report`, `truflation`, `artist_streams`, `kpis`, `tv_views`, `one_off_milestone` | explicit `{"status": None, "winner": None}` — these are not sports-shaped outcomes; `company_report`/`truflation`/`artist_streams` are settlement-*input* series (D3's domain, §3.3), not resolution events. No extraction logic invented for them here. | same |

Every type not listed above — the majority of the census's 44, including
`basketball_game`/`soccer_tournament_multi_leg` and every other type currently working via
the naive read — falls through to §2.2's default pass-through, unchanged from today's
behavior. The 14 of 44 types the census found return no live data at all resolve the same
way without any special-casing: an empty/missing `details` dict's `.get("widget_status")`/
`.get("winner")` already returns `None`, which is the correct value for them regardless. A
genuinely new (45th+) type is still caught, via `_KNOWN_TYPES` above, not silently absorbed.

### 2.4 `game_state.record()` already does most of the capture work

`services/game_state.py`'s `record()`/`extract()` (`:167-193`, `:217-287`) already: stores
the complete `details` payload in `raw_json` regardless of shape ("Handles ANY live-data
shape, not just games" — its own docstring); already reads a generic `winner` key
(`extract():189`) independent of `widget_status`; already tags each row with `event_type`
(`details.get("type")`, i.e. the milestone type). It is called from `live_status.py:190-191`
for every event that reaches a live-data poll, **gated only on `if details:`**, not on
`widget_status` being present — so for any milestone-type event already inside this app's
watchlist/near-term catalog, the raw payload is *already* landing in `game_states.raw_json`
today, before this design ships anything. What's missing is not capture, it's *reading it
back correctly* (§2.1-2.3) and *reaching the events that aren't on the watchlist yet* (a
discovery-scope question, separate from this extractor). D3 §3.3 builds on this directly:
`truflation`/`artist_streams` don't need a new persistence path, only D2's extractor to stop
returning `(None, None)` for the fields those two types actually carry (`indicator`/
`latest_value`/`timeseries` and `current_total`/`timeseries_daily`, respectively) — see §3.3
for why that's still marked "needs confirming at implementation time," not assumed.

### 2.5 X6 folded in: structured-target resolution for `custom_strike`

`catalog_scan.py:142-148` matches a milestone's `winner` (a name string) against
`custom_strike` by `str(winner).lower() in str(v).lower()` — a substring match against
values that, for `strike_type: "structured"` markets, are **UUIDs**
(`targets_and_milestones.md:73-86`; live evidence: 134/149 sampled real markets are
`strike_type: "structured"`). This branch cannot ever match on a structured market; it
silently falls through to the `yes_sub_title`/`title` substring match beneath it.

Fix: batch-resolve `custom_strike` UUIDs via `GET /structured_targets?ids=...` — the
endpoint supports up to 2000 ids per call via repeated `ids=` params
(`get-structured-targets.md:70-82`), the same shape `get_markets_by_tickers`
(`services/kalshi/public.py:152`) already uses for a different batch-by-id call. New
`KalshiPublicGateway.get_structured_targets(ids)` method, same pattern. Resolution flow
inside `propagate_milestone_winners`: for each `related_market` whose `strike_type ==
"structured"`, collect every `custom_strike` UUID seen this tick, resolve the distinct set
in one batched call, then match `winner` against the resolved target's `name` field
(`id`/`name`/`type`/`details` per `get-structured-target.md:100-115`) instead of against the
raw id. Cached in-memory (`state["structured_targets_cache"]`, same shape as
`state["category_metadata"]` — a real-world entity's name doesn't change tick-to-tick, and
this data doesn't need to survive a restart for correctness, only avoid re-resolving every
tick), not a new SQLite file — this is a discovery aid feeding a decision made fresh each
time, not accumulated history.

### 2.6 REST-cost cleanup that rides along: X7/X8

`catalog_scan.py`'s `propagate_milestone_winners` and `live_status.py`'s
`_fetch_live_status` both call `get_milestones_for_event()` once per event, every time they
poll. `get-events.md:114-118` documents `GET /events?with_milestones=true` returning
milestone data alongside the batched events call, at zero extra cost
(`targets_and_milestones.md:45`) — **correction found during PR review:** the schema
(`get-events.md:187-201,315-390`) puts `milestones` as a **top-level array sibling to
`events`**, not inline on each event; the join to a specific event is via each
`Milestone`'s `related_event_tickers`/`primary_event_tickers` (plural arrays), which the
caller (or the gateway wrapper) has to build itself. `services/kalshi/public.py:206-225`'s
`get_events()`
doesn't currently accept the flag. Adding it (an optional `with_milestones: bool = False`
parameter, only sent when the caller asks) is the cheapest of the three documented routes
to the same milestone data (the other two: `get_milestones_bulk`, already implemented and
already dead code — `catalog_scan.py`'s own note that its stated consumer,
`_sync_milestones_bulk`, has never existed; and today's per-event `get_milestones_for_event`
call, the one this replaces). Whichever one implementation picks, the effect is fewer REST
calls for the same milestone data D2 already needs — genuine efficiency, not a queue/rate
tuning guess, since it's substituting one documented batched route for N per-event ones.

---

## 3. D3 — each category's version of the crypto index feed

### 3.1 Climate and Weather (W1/W2) — already designed, not redesigned here

`docs/archive/lane-1-kalshi-ingestion/specs/2026-08-30-weather-index-ingestion-design.md` (committed
2026-08-30T12:41, hours before the investigation this spec builds on was finalized)
already covers `GET /live_data/weather/{city}`: REST polling (no WS channel exists), a new
`services/weather_index/` package, `weather_index_ticks` schema with upsert-on-conflict (a
preliminary reading can be QC-revised), and an explicit non-goal boundary (ingestion only,
no settlement-edge validation study, no strategy wiring). It is brainstormed and
self-reviewed but **not yet planned or implemented**. This spec's only addition: once D1's
`series_metadata`/`series_tags` exist, that package's city-selection step (currently ranked
by `market_catalog.db`'s `KXHIGH%` volume, flagged there as 4-days-stale at write time) can
instead route off `series_metadata.category = 'Climate and Weather'` joined to the real tag
set (`Heatwaves`, W4) rather than a ticker-prefix scan — a strict improvement available once
D1 ships, not a blocker for that spec proceeding on its own schedule.

### 3.2 Commodities — Pyth `Metal.XAU/USD`/`Metal.XAG/USD` (CM1/C3/C4)

This is not a new subsystem — `services/index_feed/` already has a working, wired Pyth
consumer (`services/index_feed/ingestion.py`'s `record_pyth`, called from
`services/kalshi/websocket.py:733-735`). The gap is entirely in configuration and discovery,
confirmed by reading the exact lines: `config/settings.yaml:169` sets
`index_feed.underlying_tickers: []`, and `websocket.py:1431` only sends the `pyth_value`
subscribe **`if self.underlying_tickers`** — so today, no Pyth subscription is ever created,
`record_pyth` never fires, and the discovery path (`websocket.py:737-738` already handles
the `pyth_value_underlying_list` reply message class) has nothing to discover *from*, since
discovery itself requires an existing `sid`, which requires a subscription that never opens.

Design, mirroring the CF Benchmarks precedent this app already ships
(`index_feed.DEFAULT_INDEX_IDS = ["BRTI", "ETHUSD_RTI"]`, a short curated default, not
"subscribe to everything"):

1. Seed `index_feed.underlying_tickers` with `pyth-value.md`'s own documented examples,
   `["Metal.XAU/USD", "Metal.XAG/USD"]` — gold and silver, the two Commodities underlyings
   the doc names (`pyth-value.md:164,289-291`). This alone makes the existing
   `websocket.py:1431` subscribe block fire and `record_pyth` start receiving real ticks —
   zero new code, one config value.
2. Add a `request_underlying_list()` method mirroring `request_index_list()`
   (`websocket.py:546-565`) but keyed to the `pyth_value` sid instead of
   `cfbenchmarks_value`, using the doc's own `underlying_list` action
   (`pyth-value.md:25-40`) — needed because, per that same doc, `underlying_tickers:
   ["all"]` receives every available underlying and a bare-subscribe-plus-discovery is how
   the app would find out what else Pyth streams beyond the two seeded ones, the same
   two-step pattern already proven for CF Benchmarks. Genuinely circular before step 1: no
   subscription means no `sid`, so discovery is impossible until something is already
   subscribed — step 1 breaks that circularity.
3. Storage needs no schema change: `index_feed.db`'s existing `record_pyth` write path
   (`services/index_feed/ingestion.py:154`) is already correct for this data; it has simply
   never received a message to record.

### 3.3 `truflation` and `artist_streams` — finish D2's capture, don't rebuild it

The full-population live-data census (P3) surfaced two settlement-input families no earlier
pass saw: **`truflation`** (244 milestones, 241 live — `indicator`, `latest_value`,
`target_date`, `series_key`, `timeseries`, on Economics/Crypto events) and
**`artist_streams`** (675 / 139 live — `timeseries_daily`/`timeseries_weekly`,
`current_total`, `period_start`/`period_end`, `target_week_finalized`, on Entertainment
events, the shape `KXARTISTSTREAMSU`-style markets settle against). Both arrive through the
exact milestone/live-data path D2 already reads (§2.4) — no new endpoint, no new
subscription. The only change needed is D2's extractor gaining a branch for these two types
that returns their real fields instead of `(None, None)` (§2.3's table currently leaves them
as the explicit no-winner case, correctly — they aren't resolution events, they're index
series a future consumer would read the same way `index_feed`/`weather_index` are read).

**What this spec does not claim**, per the investigation's own hedge: whether
`game_state.record()`'s persistence path (§2.4) actually reaches these two types in
practice — i.e., whether `truflation`/`artist_streams` events are currently inside the
watchlist/near-term-catalog scope that feeds `_fetch_live_status` at all, given they sit in
Economics/Crypto/Entertainment rather than the Sports-heavy population that path was built
around — needs confirming at implementation time against real data, not assumed here. If it
does reach them, this is nearly free (an extractor branch); if it doesn't, the fix is
D2's discovery-scope question, not a new capture mechanism.

### 3.4 Forecast-percentile history (X14) — named, not designed

`get-event-forecast-percentile-history.md` is the highest-ceiling, lowest-certainty item in
the whole investigation: the market-implied distribution across a scalar-strike event's
ladder, applying to every ladder category (Economics, Climate and Weather, Crypto,
Financials, Commodities). It is also **the one market-data endpoint in the mirror that
declares `security: kalshiAccessKey`** (`get-event-forecast-percentile-history.md:141-144`)
— authenticated, unlike every other public-read path this app calls. This spec does not
design storage or a consumption shape for it: doing so before confirming the auth
requirement against this app's actual credential setup would be designing around an
unverified assumption, exactly what the HARD RULE forbids. Next step, if pursued: a spike
(per `superpowers:brainstorming`'s own three-path classification) that answers one question
— does this app's existing API-key setup, if any, reach this endpoint at all — before any
design work.

---

## 4. D4 — political_race election-night data

Reopened by the corrected P3 census after being wrongly closed off by a one-sample
falsification: `political_race` live data (`race_call_status`, `reporting_percentage`,
`tabulation_status`, `candidates`, `winner`, `winners`) is real, unauthenticated, and present
on 810 of 917 `political_race` milestones — 18.1% of *all* forward-dated live milestones
across every type, the largest non-Sports forward-dated population. It sits over 1,389
volume-positive Elections series.

**Design, deliberately thin — this reuses D1 and D2's shapes rather than inventing its own:**

- **Capture:** D2's extractor (§2.3) already gives `political_race` a status/winner mapping;
  nothing new to build for the base case. `race_call_status`/`reporting_percentage`/
  `tabulation_status`/`candidates` beyond the single `winner`/`status` pair D2 extracts are
  additional fields worth a dedicated read once D2's status-vocabulary mapping is verified
  live (§2.3's flagged assumption) — not before, since building a second layer on an
  unverified enum mapping compounds the risk rather than bounding it.
- **Structured-candidate resolution:** P2 found Elections milestone `details` carrying
  `candidate_id_mapping`/`candidate_ids` — the same structured-target-ID shape Sports'
  `home_team_id`/`away_team_id` already has. This is the **same fix as X6/§2.5**, applied to
  a different milestone type — no new resolution mechanism, one more `type` value flowing
  through the same `GET /structured_targets` batch call.
- **Category routing:** once D1 ships, `political_race`'s parent events already resolve to
  the Elections category via the existing `event_ticker → event.series_ticker →
  Series.category` chain (the same chain the investigation itself used to spot-check every
  deviating milestone type, §2.7 of the investigation) — no new lookup needed.
- **Sizing, stated plainly and not glossed over:** this is real value sitting behind a
  calendar, not a steady-state feed — the investigation ranks it last "only because its
  value is episodic and election-calendar-driven — a timing argument, not an existence one."
  Nothing here should be prioritized ahead of D1/D2's steady-state value on the strength of
  its per-milestone numbers; it earns its priority only when an actual election is
  approaching.

---

## 5. Deliberately not designed further

Matching the investigation's own "Deliberately not proposed" list, plus the items §0/§1.7/
§3.4 named and explicitly deferred above:

- **Widening `kalshi.categories`** beyond the 11 already live (investigation §2.7) — 2.7% of
  the volume-positive series universe; already resolved as a decision, not reopened here
  (`docs/open-decisions.md`, 2026-08-30 entry).
- **`price_level_structure`/`price_ranges` (X12)** — GAP-SKIP while paper mode continues;
  becomes a real GAP-PURSUE before live trading, since an invalid-price rejection against a
  real broker is a different failure mode than against the simulated one this app runs
  today. Not touched by D1-D4.
- **`incentive_programs` (X13)** — still NEEDS-LIVE; the investigation's own one-call
  measurement (`GET /incentive_programs?status=active` cross-joined to `signal_log.signals.
  ticker`) was never run. A spike, not a design, and only once someone actually runs that
  check.
- **`game-stats`/`include_player_stats` (S5/S6)** — already assessed and declined in-repo;
  not revisited.
- **`exchange_index` on markets/events beyond persistence (X9)** — the collateral
  preallocation `exchange_sharding.md:26` requires is explicitly a Program 3+ (live
  execution) concern, and CLAUDE.md's standing instruction is not to prioritize that over
  paper-mode correctness. D1 captures Series' own `exchange_index` as a side effect (§1.2)
  but that is not a substitute for X9's fix.
- **`event_lifecycle`/`event_fee_update` WS handling (X10)** — named as D1's real-time
  complement (§1.7) but explicitly not designed here; it's a hot-path WS change needing its
  own cost measurement.

---

## 6. Rollout shape

Not a single flag-day migration — four independently-landable pieces, in the ranked order,
each safe to ship alone:

1. **D1 Phase 1** (§1.2-1.4, §1.6, §1.8): new tables, write-through population, `series_of()`
   fix, `category_tags` stamp removal. No behavior change to any existing consumer except
   `series_of()`'s corrected output. Testable in isolation: a fixture `Series` list in,
   confirm both tables populate; a fixture `title_cache` with a known event_ticker→
   series_ticker mapping, confirm `series_ticker_for()`/`series_of()` return it; an
   unmapped ticker, confirm the prefix fallback still fires.
2. **D2** (§2.2-2.6): the extractor module, its two call-site wirings, the unknown-type
   counter, structured-target batch resolution. Testable per type against the census's own
   observed key shapes (fixtures, not live calls) — tennis as the zero-behavior-change
   control case, esports/political_race as the flagged-assumption cases requiring their own
   live-payload confirmation before the type-specific branch ships (not before the
   dispatch-table architecture and the safe unmapped-type fallback ship).
3. **D1 Phase 2** (§1.5): `min_updated_ts`/`include_product_metadata` on the series fetch —
   separable, its own before/after call-count check.
4. **D3** (§3.2-3.3): Commodities Pyth config flip + discovery method (small, independent
   of D1/D2); `truflation`/`artist_streams` extractor branches (depends on D2 landing
   first, and on confirming §3.3's open scope question).
5. **D4** (§4): political_race's D2 branch plus its structured-candidate resolution —
   depends on D2 and D1 both landing; deliberately not scheduled ahead of either.

Weather (§3.1) proceeds on its own already-approved spec's timeline, independent of this
sequence.

---

## Spec self-review

Per `superpowers:brainstorming`'s architectural-path convention, run headless (no live
reviewer this pass — the caveats below are the substitute for a clarifying-question round,
each with its own stated resolution rather than left open):

1. **Placeholder scan.** No "TBD"/"TODO" left in the document. One real placeholder was
   caught and fixed during this pass, not left vague: §1.3's tag-junction upsert originally
   sketched a `DELETE ... WHERE fetched_at = ?` with a literal `...` standing in for an
   unresolved parameter — replaced with a concrete delete-then-reinsert keyed directly off
   the batch's own ticker list, which is both simpler and correct (the fetched_at-based
   subquery was actually redundant, since `series` is always the full fetched batch). The
   two flagged extractor assumptions in §2.3 (`esports_match`'s terminal marker,
   `political_race`'s status-enum values) are a different, deliberate kind of open item —
   not placeholders but named-and-deferred verification steps, explicitly required before
   their specific branch ships (§6 item 2 states this for D2), not left as an implicit
   "figure it out later." (Unrelated to §1.5's "D1 Phase 2" — same word, two different
   deferred pieces; §1.5 is the `min_updated_ts` REST-shape change, this is the
   per-type-extractor verification.)
2. **Internal consistency.** Checked the "zero new API calls" claims in §1.3/§2.6/§3.2
   against each other: D1 rides an existing hourly call (true — `_get_series_cache` already
   fetches unconditionally), D2's X7/X8 cleanup *reduces* call count rather than adding any,
   and D3's Commodities piece adds exactly one new discovery call type on an
   already-open connection. No section claims a zero-cost change that secretly requires a
   new subscription or a new poll loop. Checked D2 §2.4's `game_state.record()` reuse claim
   against D3 §3.3's use of it: §3.3 explicitly does not assert the reuse definitely works
   for non-Sports types, matching §2.4's own hedge — the two sections agree rather than one
   overclaiming what the other only tentatively grants.
3. **Scope check.** This spec covers one investigation's four ranked directions at
   deliberately uneven depth (§0 states why), which is a lot of ground for one document —
   but each direction was already independently phased into its own commit/task boundary in
   §6, so a future `writing-plans` pass can plan D1 alone, or D1+D2 together, without this
   spec needing to be split first. Not decomposed into four separate spec files because D2
   depends on D1's category-routing shape and D3 depends on D2's extractor — splitting them
   would either duplicate the dependency description four times or force artificial
   read-order on whoever plans next.
4. **Ambiguity check.** Two real ambiguities were found and resolved inline rather than left
   for the reader to interpret two ways: (a) whether D1 *replaces* `series_cache.db`'s blob
   table or adds alongside it — resolved explicitly in §1.1 as "alongside, not instead of,"
   with the reasoning stated; (b) what happens to `min_contracts_by_series`-style config
   keys under the corrected `series_of()` — resolved in §1.4 by showing the function's
   output contract (a series-ticker string) is unchanged, only its correctness improves, so
   existing config keys need no edit. A third was found during this review and fixed just
   now: §1.4's discontinuity discussion originally left open whether a backfill should be
   attempted; resolved explicitly against backfilling, with the reason (accumulated-history
   permanence, plus `title_cache`'s own incompleteness as an archive) stated in place.
