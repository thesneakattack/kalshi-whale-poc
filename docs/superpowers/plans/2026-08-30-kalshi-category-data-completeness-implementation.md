# Kalshi Category Data Completeness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking — per `docs/superpowers/plans/README.md`, the
> checkboxes are not reliable completion evidence; `git log`/the source are. Cite a task as
> `kalshi-category-data-completeness Task N`, never `Task N` alone (two other plans already
> number their own Task 1).

**Goal:** Land the four ranked directions (D1→D2→D3→D4) from the finalized design in the
order its own §6 rollout specifies — a real `series_metadata`/`series_tags` store, a shared
milestone live-data extractor that stops silently misreading `details`, the REST-shape
change that turns the hourly series refetch into a real delta poll, the Commodities Pyth
feed, and `political_race`'s structured-candidate resolution. Read/store-more-data work
only: no task here touches `kalshi_account.trading_enabled`, weakens a safety gate,
changes sizing/calibration/strategy logic, or resets live data.

**Architecture:** Additive SQLite tables written from data this app already fetches (D1);
one new `services/market_watch/milestone_live_data.py` dispatch module consumed by two
existing call sites (D2); a config seed plus one new WS discovery method reusing the
already-wired Pyth consumer (D3); D2's extractor and D1's structured-target/category
plumbing reused, not reinvented, for election-night data (D4). Weather (§3.1) is out of
scope — it proceeds on its own already-approved, separately-planned spec.

**Tech Stack:** Python/FastAPI app, SQLite additive migrations (`CREATE TABLE IF NOT
EXISTS` + `_add_column_if_missing`), pytest (sync tests + `asyncio.run`, no
pytest-asyncio), Kalshi REST/WS via `services/kalshi/public.py` / `services/kalshi/
websocket.py`.

**Spec:** `docs/superpowers/specs/2026-08-30-kalshi-category-data-completeness-design.md`
(finalized, GO verdict, two revision+review cycles). Evidence base:
`docs/superpowers/research/2026-08-30-kalshi-category-data-shape-audit.md` (the census
S3/S4 rows this plan cites directly in Task 5 — see the note there on why).

## Global Constraints

- **Kalshi facts come from `docs/kalshi/` and the design's own citations, never memory.**
  Every task below either cites an exact `docs/kalshi/<page>.md` line range (re-verified
  against this worktree's copy while writing this plan) or the spec section that already
  did so. A task whose Kalshi-shaped behavior isn't grounded this way gets a
  `kalshi-contract-review` step before its implementation step, called out explicitly.
- **Safety, stated once, applies to every task:** no task touches
  `kalshi_account.trading_enabled`, `services/kalshi_account_client.py`, order placement,
  risk/sizing/calibration parameters, or settlement/strategy decision logic. Every schema
  change is additive (`CREATE TABLE IF NOT EXISTS`); every test `monkeypatch`es `DB_PATH`
  to a `tmp_path`; no task deletes or rewrites a row in a live `data/*.db`.
- **Vendor-specific interpretation stays inside `services/kalshi/` and its documented
  consumer set** (`.claude/rules/kalshi-integration-authority.md`) — none of these tasks
  add semantic Kalshi interpretation outside `services/kalshi/`, `services/market_watch/`,
  `services/title_cache.py`, `services/signal_log.py`, or `services/series_cache.py`, all
  of which are already inside that boundary or its documented consumers.
- **GitNexus impact/context check** (CLAUDE.md: "before multi-file edits to strategy,
  risk, advisory, calibration, kalshi client, or shared state"): run once per session
  before starting, and specifically re-check before **Task 3** (`signal_log.series_of()`
  is consumed by `strategy_engine.py`'s `excluded_series` gate and position-concurrency
  count — shared state, not a leaf function), **Task 6** (two-call-site rewiring of live
  milestone data feeding both `game_states` persistence and `market_history.
  record_outcome`), and **Task 10** (`KalshiPublicGateway.get_events()` signature change —
  kalshi-client boundary, multiple callers).
- **Run tests via `ddev exec -s fastapi`** from the primary root, `cd`-ing into this
  worktree first (CLAUDE.md: `ddev exec` refuses to run from a linked worktree directory
  directly): `ddev exec -s fastapi sh -c "cd /app/.claude/worktrees/agent-a72f71384c869f628 && python3 -m pytest -q -p no:testmon <files>"`.
- **`services/market_watch/CHEATSHEET.md` and `services/kalshi/CHEATSHEET.md`** get a
  dated entry whenever a task resolves a real Kalshi-contract question (CLAUDE.md) — flagged
  per-task below where one is owed, not left implicit.
- **A significant deviation from the spec's literal §2.2 sketch is made explicit in Task 5,
  not silently implemented.** The spec's `extract()` pseudocode returns `(None, None)` for
  any milestone `type` not in its dispatch table. Cross-checking that against the design's
  own cited evidence (the census in `docs/superpowers/research/2026-08-30-kalshi-category-
  data-shape-audit.md`, rows S3/S4) shows this would silently regress live-data coverage
  for roughly 21 of the census's 30 live-data-bearing milestone types — including
  `basketball_game` (15,564 milestones) and `soccer_tournament_multi_leg` (15,546), the
  2nd/3rd-largest populations after `tennis_tournament_singles` — because those types
  already carry `widget_status`/`winner` today and are not among the 9 types S3/S4 name as
  actually lacking one or both fields. Task 5 below implements a **default pass-through**
  (matching today's behavior for anything not named as deviant) with the spec's dispatch
  table applied only to the 9 named-deviant types, rather than a null default. This is not
  a redesign of D2's goal — every fix the spec's §2.1-§2.3 describes still ships — it is a
  correction to how the "unmapped type" default is implemented, grounded in the same
  evidence document the spec itself cites, per CLAUDE.md's data-plane completeness HARD
  RULE ("a dropped message, skipped candidate, or DB hole is a defect") and the
  never-guess HARD RULE (this is a verified count from the cited census, not a new
  assumption).

---

## D1 Phase 1 — series_metadata/series_tags, series_of() fix, cleanup

Spec §1.2-§1.4, §1.6, §1.8. No behavior change to any existing consumer except
`series_of()`'s corrected output (Task 3) and the deleted `category_tags` stamp (Task 4).

### Task 1: `series_metadata`/`series_tags` schema + write-through population

**Files:**
- Modify: `services/series_cache.py` (`_connect()` schema block; `save()`)
- Test: append to `tests/test_series_cache.py`

**Interfaces:**
- Produces: `series_metadata` (one row per series ticker), `series_tags` (ticker, tag
  junction table), both populated by the same `save(fetched_at, series)` call that already
  writes the existing `series_cache` blob row. No new function signature — `save()`'s
  contract is unchanged.

**Spec:** §1.2 (schema), §1.3 (population — the SQL in that section is authoritative,
reproduced here verbatim rather than re-derived).

- [ ] **Step 1: Write the failing tests**

```python
import json

def test_save_populates_series_metadata_and_series_tags(tmp_path, monkeypatch):
    cache = _sc(tmp_path, monkeypatch)
    series = [{
        "ticker": "KXNFLGAME", "category": "Sports", "frequency": "weekly",
        "tags": ["NFL", "Football"],
        "settlement_sources": [{"name": "NFL.com", "url": "https://nfl.com"}],
        "contract_url": "https://kalshi.com/c/1", "contract_terms_url": "https://kalshi.com/t/1",
        "fee_type": "quadratic", "fee_multiplier": 1.0,
        "additional_prohibitions": ["some_state_official"], "exchange_index": 3,
        "volume_fp": "5000000", "last_updated_ts": "2026-08-30T00:00:00Z",
    }]
    cache.save(1755000000.0, series)
    with cache._connect() as conn:
        row = conn.execute(
            "SELECT category, frequency, tags_json, settlement_sources_json, contract_url, "
            "contract_terms_url, fee_type, fee_multiplier, additional_prohibitions_json, "
            "exchange_index, volume_fp, last_updated_ts, fetched_at "
            "FROM series_metadata WHERE ticker = ?", ("KXNFLGAME",),
        ).fetchone()
        tags = {r[0] for r in conn.execute(
            "SELECT tag FROM series_tags WHERE ticker = ?", ("KXNFLGAME",))}
    assert row[0:2] == ("Sports", "weekly")
    assert json.loads(row[2]) == ["NFL", "Football"]
    assert json.loads(row[3]) == [{"name": "NFL.com", "url": "https://nfl.com"}]
    assert row[4:8] == ("https://kalshi.com/c/1", "https://kalshi.com/t/1", "quadratic", 1.0)
    assert json.loads(row[8]) == ["some_state_official"]
    assert row[9:] == (3, "5000000", "2026-08-30T00:00:00Z", 1755000000.0)
    assert tags == {"NFL", "Football"}


def test_save_replaces_series_tags_on_resave_not_accumulates(tmp_path, monkeypatch):
    # get-series.md's tags array can shrink upstream - a tag removed from a
    # series must not leave a stale series_tags row forever (spec §1.3:
    # "delete-then-reinsert per ticker, not a diff").
    cache = _sc(tmp_path, monkeypatch)
    cache.save(100.0, [{"ticker": "K1", "tags": ["A", "B"]}])
    cache.save(200.0, [{"ticker": "K1", "tags": ["A"]}])
    with cache._connect() as conn:
        tags = {r[0] for r in conn.execute("SELECT tag FROM series_tags WHERE ticker = ?", ("K1",))}
    assert tags == {"A"}


def test_save_handles_series_with_no_optional_fields(tmp_path, monkeypatch):
    # get-series.md marks tags/settlement_sources/additional_prohibitions
    # all nullable - a bare series dict must not raise.
    cache = _sc(tmp_path, monkeypatch)
    cache.save(100.0, [{"ticker": "BARE"}])
    with cache._connect() as conn:
        row = conn.execute(
            "SELECT category, tags_json, fee_multiplier, exchange_index "
            "FROM series_metadata WHERE ticker = ?", ("BARE",),
        ).fetchone()
        tag_count = conn.execute(
            "SELECT COUNT(*) FROM series_tags WHERE ticker = ?", ("BARE",)).fetchone()[0]
    assert row == (None, "[]", None, None)
    assert tag_count == 0


def test_existing_blob_write_is_unchanged(tmp_path, monkeypatch):
    # D1 is additive alongside series_cache, not instead of it (spec §1.1) -
    # this pins that the pre-existing round trip still works untouched.
    cache = _sc(tmp_path, monkeypatch)
    series = [{"ticker": "KXBTCD", "category": "Crypto", "volume_fp": "3000000"}]
    cache.save(1755000000.0, series)
    result = cache.load()
    assert result == {"fetched_at": 1755000000.0, "series": series}
```

(`_sc(tmp_path, monkeypatch)` is the existing helper in `tests/test_series_cache.py`,
returning the `series_cache` module with `DB_PATH` monkeypatched — verified against the
current file. `cache._connect()` used directly for schema verification is an established
idiom in this repo — `tests/test_candidate_log.py`, `tests/test_diagnostics.py`, others.)

- [ ] **Step 2: Run to verify all four new tests FAIL** (no `series_metadata` table).

- [ ] **Step 3: Implement.** In `services/series_cache.py`'s `_connect()`, add after the
  existing `series_cache` `CREATE TABLE`:

```python
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_metadata (
            ticker TEXT PRIMARY KEY,
            category TEXT,
            frequency TEXT,
            tags_json TEXT,
            settlement_sources_json TEXT,
            contract_url TEXT,
            contract_terms_url TEXT,
            fee_type TEXT,
            fee_multiplier REAL,
            additional_prohibitions_json TEXT,
            exchange_index INTEGER,
            volume_fp TEXT,
            last_updated_ts TEXT,
            fetched_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_tags (
            ticker TEXT NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (ticker, tag)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_series_metadata_category ON series_metadata (category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_series_tags_tag ON series_tags (tag)")
```

  Add `_series_metadata_row(s: dict, fetched_at: float) -> tuple`, matching
  `_get_series_cache`'s own defensive `.get()` style (`float(s.get("volume_fp") or 0)`
  precedent in `catalog_scan.py`) — every field `.get()`, `tags_json`/
  `settlement_sources_json`/`additional_prohibitions_json` always `json.dumps(x or [])`
  (never `None`, since `series_tags`' own population loop iterates
  `s.get("tags") or []` and an empty-but-valid JSON array is a cleaner contract than a
  nullable one for a column named `_json`). Extend `save()` with the executemany block per
  spec §1.3 (reproduced there verbatim: upsert `series_metadata` via `ON CONFLICT(ticker)
  DO UPDATE SET ...`, then delete-then-reinsert `series_tags` keyed off `tickers_this_batch
  = [s["ticker"] for s in series]` — not a `fetched_at`-scoped delete, matching the fix the
  spec's own self-review section made).

- [ ] **Step 4: Run to verify all four PASS.**
- [ ] **Step 5: Run `tests/test_series_cache.py` in full** for regressions.
- [ ] **Step 6: Commit:** `feat: series_metadata/series_tags tables, write-through population (kalshi-category-data-completeness Task 1)`

---

### Task 2: Series-level fee-changes bulk call folded into population

**Files:**
- Modify: `services/kalshi/public.py` (new `get_series_fee_changes`)
- Modify: `services/market_watch/catalog_scan.py` (`_get_series_cache()`)
- Test: append to `tests/test_kalshi_client.py`, `tests/test_catalog_scan_pacing.py`

**Interfaces:**
- Produces: `KalshiPublicGateway.get_series_fee_changes(show_historical: bool = True) ->
  list[dict]` — one unpaginated call (`docs/kalshi/get-series-fee-changes.md`:
  `GetSeriesFeeChangesResponse.series_fee_change_arr` carries no `limit`/`cursor`, and
  `series_ticker` is an optional filter — omitting it returns the whole array).
- Consumes into: `_get_series_cache()` merges the resolved current `fee_type`/
  `fee_multiplier` per `series_ticker` into each series dict **before** calling
  `series_cache.save()`, so Task 1's `_series_metadata_row()` picks it up with no
  interface change.

**Kalshi contract note — flagged, not guessed:** `docs/kalshi/get-series-fee-changes.md`'s
schema does not state whether `show_historical=true` returns one row per series that ever
had *any* fee entry (base + changes) or only rows for series with an actual fee *change*
event. This changes whether "ticker absent from the response" means "no override, keep the
raw `Series.fee_type`" (safe — this task's assumption) or "malformed request" (would need a
different check). **Step 0 below is a `kalshi-contract-review` step, not skippable**: read
`docs/kalshi/get-series-fee-changes.md` in full before implementing, and if a live/fixture
response is available, confirm a series known to have never changed its fee still appears
(or confirm it legitimately doesn't, and that "absent = keep the raw value" is therefore
correct) before writing the merge logic below as designed.

- [ ] **Step 0: `kalshi-contract-review`** on `docs/kalshi/get-series-fee-changes.md`,
  resolving the absent-ticker question above. Record the answer in the commit message.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kalshi_client.py
def test_get_series_fee_changes_omits_series_ticker_for_the_full_array(monkeypatch):
    client = _client()
    calls = []

    async def fake_get_series_fee_changes(show_historical):
        calls.append(show_historical)
        return type("R", (), {"series_fee_change_arr": [
            _FakeModel({"id": 1, "series_ticker": "KXNFLGAME", "fee_type": "quadratic",
                        "fee_multiplier": 1.0, "scheduled_ts": 1700000000}),
        ]})()

    monkeypatch.setattr(client._client, "get_series_fee_changes", fake_get_series_fee_changes)
    result = asyncio.run(client.get_series_fee_changes())
    assert calls == [True]  # show_historical defaults True, no series_ticker passed
    assert result == [{"id": 1, "series_ticker": "KXNFLGAME", "fee_type": "quadratic",
                        "fee_multiplier": 1.0, "scheduled_ts": 1700000000}]
```

```python
# tests/test_catalog_scan_pacing.py (new section)
class _FakeFeeChangesClient:
    def __init__(self, series, fee_changes):
        self._series = series
        self._fee_changes = fee_changes

    async def get_series_list(self):
        return self._series

    async def get_series_fee_changes(self, show_historical=True):
        return self._fee_changes


def test_get_series_cache_applies_the_most_recently_scheduled_fee_change(tmp_path, monkeypatch):
    monkeypatch.setattr(series_cache, "DB_PATH", tmp_path / "series_cache.db")
    state["series_cache"] = {"fetched_at": 0.0, "series": []}  # force a refresh
    series = [{"ticker": "K1", "category": "Sports", "volume_fp": "100",
               "fee_type": "flat", "fee_multiplier": 0.5}]  # raw Series-object base fee
    fee_changes = [
        {"series_ticker": "K1", "fee_type": "quadratic", "fee_multiplier": 1.0, "scheduled_ts": 1000},
        {"series_ticker": "K1", "fee_type": "flat", "fee_multiplier": 2.0, "scheduled_ts": 2000},  # more recent, still <= now
    ]
    client = _FakeFeeChangesClient(series, fee_changes)
    monkeypatch.setattr(time, "time", lambda: 3000)

    result = asyncio.run(catalog_scan._get_series_cache(client))

    assert result[0]["fee_type"] == "flat"
    assert result[0]["fee_multiplier"] == 2.0  # scheduled_ts=2000 wins over 1000, not creation order


def test_get_series_cache_keeps_raw_fee_when_ticker_absent_from_fee_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(series_cache, "DB_PATH", tmp_path / "series_cache.db")
    state["series_cache"] = {"fetched_at": 0.0, "series": []}
    series = [{"ticker": "K2", "category": "Sports", "volume_fp": "100",
               "fee_type": "quadratic", "fee_multiplier": 1.0}]
    client = _FakeFeeChangesClient(series, fee_changes=[])  # K2 never appears
    monkeypatch.setattr(time, "time", lambda: 3000)

    result = asyncio.run(catalog_scan._get_series_cache(client))

    assert result[0]["fee_type"] == "quadratic"
    assert result[0]["fee_multiplier"] == 1.0
```

(`state`/`series_cache`/`catalog_scan`/`time` already imported at the top of
`tests/test_catalog_scan_pacing.py`; `_prime_series_cache` isn't reused here since these
tests need `_get_series_cache()`'s real fetch path, not a pre-seeded cache.)

- [ ] **Step 2: Run to verify all three FAIL.**

- [ ] **Step 3: Implement.**
  - `services/kalshi/public.py`: add `get_series_fee_changes(self, show_historical:
    bool = True) -> list[dict]`, calling `self._client.get_series_fee_changes` (verify the
    installed SDK's exact param name during Step 0's contract review — this repo's own
    precedent, `get_series_list`'s docstring, shows the SDK has previously diverged from
    docs) and returning `resp.series_fee_change_arr` dumped the same way every other
    gateway method here does (`[x.model_dump(mode="json") for x in ...]`).
  - `services/market_watch/catalog_scan.py`'s `_get_series_cache()`: after the existing
    `series = await client.get_series_list()` / volume-filter / sort, add one
    `fee_changes = await client.get_series_fee_changes()` call, build `effective_fee:
    dict[str, tuple[str, float]]` keyed by `series_ticker` (per entry, keep the one with
    the greatest `scheduled_ts <= now`, ties broken by `id`, per spec §1.8), then for each
    `s` in `series` whose `s["ticker"]` is in `effective_fee`, overwrite
    `s["fee_type"]`/`s["fee_multiplier"]` in place before `series_cache.save(...)` is
    called. A ticker not in `effective_fee` is left with whatever `fee_type`/
    `fee_multiplier` its raw Series object already carried (Step 0's confirmed contract).

- [ ] **Step 4: Run to verify all three PASS.**
- [ ] **Step 5: Run `tests/test_kalshi_client.py` and `tests/test_catalog_scan_pacing.py` in full.**
- [ ] **Step 6: Commit:** `feat: series-level fee-changes bulk call feeds series_metadata's fee columns (kalshi-category-data-completeness Task 2)` —
  cite docs read: `docs/kalshi/get-series-fee-changes.md`, and Step 0's absent-ticker finding.

---

### Task 3: `title_cache.series_ticker_for()` + `signal_log.series_of()` fix

**Files:**
- Modify: `services/title_cache.py` (new `series_ticker_for`)
- Modify: `services/signal_log.py` (`series_of`)
- Test: append to `tests/test_title_cache.py`, `tests/test_signal_log.py`

**Interfaces:**
- Produces: `title_cache.series_ticker_for(ticker: str) -> str | None` (one indexed join,
  `market_titles.event_ticker ⋈ event_titles.event_ticker`, same shape as the existing
  `fee_override_for_ticker`). `series_of(ticker)`'s signature and every call site
  (`strategy_engine.py`'s `excluded_series` gate, position-concurrency-by-series count,
  `series_stats`/`series_stats_bulk`) are unchanged — only its correctness improves.

**Spec:** §1.4. Includes the documented, deliberate non-backfill decision for
`signals.series`'s pre-cutover rows (measured 0% coverage — see spec §1.4's "Correction,
found in design review" paragraph). No backfill work is in scope for this task.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_title_cache.py
def test_series_ticker_for_resolves_via_market_and_event_titles(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    cache.save_market_titles({"MKT-A": {"title": "T", "yes_sub_title": "", "no_sub_title": "",
                                          "event_ticker": "EVT-A"}})
    cache.save_event_titles({"EVT-A": {"series_ticker": "REAL-SERIES"}})
    assert cache.series_ticker_for("MKT-A") == "REAL-SERIES"


def test_series_ticker_for_returns_none_when_market_uncached(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    assert cache.series_ticker_for("UNSEEN-TICKER") is None


def test_series_ticker_for_returns_none_when_event_series_ticker_is_null(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    cache.save_market_titles({"MKT-B": {"title": "T", "yes_sub_title": "", "no_sub_title": "",
                                          "event_ticker": "EVT-B"}})
    cache.save_event_titles({"EVT-B": {}})  # event cached, series_ticker never populated
    assert cache.series_ticker_for("MKT-B") is None
```

```python
# tests/test_signal_log.py
def test_series_of_uses_title_cache_when_resolvable(tmp_path, monkeypatch):
    from services import title_cache
    monkeypatch.setattr(title_cache, "DB_PATH", tmp_path / "title_cache.db")
    title_cache.save_market_titles({"KXMVECROSSCATEGORY0-SHARD1": {
        "title": "T", "yes_sub_title": "", "no_sub_title": "", "event_ticker": "EVT-X"}})
    title_cache.save_event_titles({"EVT-X": {"series_ticker": "KXMVECROSSCATEGORY0"}})
    # The shipped bug this task fixes (CHEATSHEET.md): ticker-prefix split
    # would have returned "KXMVECROSSCATEGORY0-SHARD1" itself, not the real series.
    assert signal_log.series_of("KXMVECROSSCATEGORY0-SHARD1") == "KXMVECROSSCATEGORY0"


def test_series_of_falls_back_to_prefix_when_unresolvable(tmp_path, monkeypatch):
    from services import title_cache
    monkeypatch.setattr(title_cache, "DB_PATH", tmp_path / "title_cache.db")
    assert signal_log.series_of("KXBTC15M-26AUG29-B1") == "KXBTC15M"


def test_series_of_empty_ticker_unchanged():
    assert signal_log.series_of("") == ""
```

(`_tc(tmp_path, monkeypatch)` is `tests/test_title_cache.py`'s existing helper. Verify
`save_event_titles`'s exact accepted-keys contract before writing the event-titles fixture
dicts above — its `INSERT` statement at `services/title_cache.py:208` names the columns it
accepts; a key outside that set is silently dropped, not an error, so the test fixture must
use `series_ticker` exactly as spelled there.)

- [ ] **Step 2: Run to verify all six FAIL** (`AttributeError: series_ticker_for` for the
  first three; the `signal_log` tests fail because `series_of` doesn't yet consult
  `title_cache`).

- [ ] **Step 3: Implement.**
  - `services/title_cache.py`: add `series_ticker_for(ticker)` per spec §1.4's code block
    verbatim (one `SELECT et.series_ticker FROM market_titles mt JOIN event_titles et ON
    et.event_ticker = mt.event_ticker WHERE mt.ticker = ?`, returning `row[0] if row and
    row[0] else None`).
  - `services/signal_log.py`'s `series_of()`: 

```python
def series_of(ticker: str) -> str:
    """Real series_ticker via title_cache's market_titles -> event_titles join
    when resolvable (services/title_cache.py:series_ticker_for) - the market's
    own documented event_ticker/series_ticker chain (docs/kalshi/get-market.md,
    get-events.md), never the ticker-string prefix. Falls back to the prefix
    heuristic ONLY for a ticker this app hasn't cached an event for yet - the
    general case this used to be, now the exception. Public (not
    underscore-prefixed) since strategy_engine.py's manual excluded_series
    gate needs the exact same series definition the automatic win-rate filter
    already uses - one definition, not two that could quietly drift apart.

    2026-08-30 fix (kalshi-category-data-completeness Task 3): the old
    ticker.split("-")[0] mis-derived MVE/sharded tickers like
    "KXMVECROSSCATEGORY0-SHARD1" as their own series instead of the real
    "KXMVECROSSCATEGORY0" (docs/kalshi/CHEATSHEET.md). Pre-cutover
    signals.series rows are NOT backfilled - measured coverage for the
    join against historical MVE/sharded tickers is 0 of 12,357 tickers
    (see the design spec's §1.4 "Correction, found in design review"), so a
    backfill would rewrite accumulated history (CLAUDE.md) for zero actual
    gain; old and new rows simply disagree for that minority going forward,
    a known, named accounting seam, not a bug to chase further."""
    if not ticker:
        return ticker
    real = title_cache.series_ticker_for(ticker)
    return real or ticker.split("-")[0]
```

    Add `from services import title_cache` at module scope if not already imported
    (verify — `signal_log.py` may not currently import it; check for an import cycle the
    way `services/app_state.py` already documents for `services.whalewatchers` before
    adding this: `title_cache.py` imports only `sqlite3`/`json`/stdlib, so a module-scope
    import here is safe, unlike the function-local pattern Task 4 in the ME-gate plan had
    to use for `app_state`).

- [ ] **Step 4: Run to verify all six PASS.**
- [ ] **Step 5: Run `tests/test_title_cache.py`, `tests/test_signal_log.py`,
  `tests/test_strategy_engine.py` (consumer regression) in full.**
- [ ] **Step 6: Commit:** `fix: series_of() resolves the real series_ticker via title_cache, not a ticker-prefix guess (kalshi-category-data-completeness Task 3)` —
  cite docs read: `docs/kalshi/get-market.md`, `get-events.md` (per `terms.md:29`'s
  standing rule against ticker-string parsing, already recorded in `docs/kalshi/
  CHEATSHEET.md`).

---

### Task 4: Delete the redundant `category_tags` stamp (X1)

**Files:**
- Modify: `main.py` (`trading_loop()`, the stamp at the `event_meta["category_tags"] = ...`
  line)
- Test: append to `tests/test_main_tick_executor_wiring.py`

**Interfaces:** None — pure deletion. `category_tags` has exactly one writer (`main.py`)
and zero real readers (`services/market_lookup.py`, `services/history/regime_analytics.py`,
`services/trade_category.py` each carry a comment explaining why they deliberately do
**not** use it — verified by grep across `services/` and `main.py`; every hit besides the
writer itself is a "NOT category_tags" comment).

**Spec:** §1.6 / investigation finding X1 (`docs/kalshi/CHEATSHEET.md`'s own first entry:
"`category_tags` on an event object LOOKS like per-event tag data but is the same
facet-filter vocabulary for every event in the category").

- [ ] **Step 1: Write the failing test**, using this repo's own established
  source-shape-assertion idiom for a change inside `trading_loop()` that has no
  independent unit-testable seam (`tests/test_main_tick_executor_wiring.py`'s
  `test_candidate_retry_runs_from_its_own_supervised_loop_not_the_tick` is the precedent,
  asserting via `inspect.getsource`):

```python
import inspect
import main

def test_trading_loop_no_longer_stamps_the_redundant_category_tags_field():
    # X1 (2026-08-30 design spec): category_tags carried the same
    # facet-filter vocabulary for every event in a category - zero
    # per-event signal, already flagged as a gotcha in docs/kalshi/
    # CHEATSHEET.md. series_metadata/series_tags (Task 1) are the real,
    # per-series replacement.
    assert 'category_tags' not in inspect.getsource(main.trading_loop)
```

- [ ] **Step 2: Run to verify FAIL.**
- [ ] **Step 3: Implement.** Delete the two lines:
  `tags_by_categories = state["category_metadata"].get("tags_by_categories") or {}` and
  the `for et, event_meta in state["event_titles"].items(): ... event_meta["category_tags"]
  = ...` block, in `main.py`'s `trading_loop()` (verify the exact current line range before
  editing — grep `category_tags` in `main.py` first, since Task 3's edits above may have
  shifted line numbers in this same file's neighborhood).
- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_main_tick_executor_wiring.py` in full; `ddev exec -s fastapi python -c "import main"` to confirm no syntax/import regression** (this repo's own
  precedent for a main.py-only change with no fuller integration test, per
  `docs/superpowers/plans/2026-08-27-backend-services-modularization.md`).
- [ ] **Step 6: Commit:** `chore: drop the redundant category_tags stamp - series_metadata/series_tags supersede it (kalshi-category-data-completeness Task 4)` —
  cross-post one dated line to `docs/kalshi/CHEATSHEET.md`'s existing `category_tags`
  gotcha entry noting the real per-series replacement now exists.

---

## D2 — shared per-type milestone live-data extractor

Spec §2.2-§2.6. **Task 5 implements the corrected default-pass-through architecture
described in Global Constraints, not the spec's literal null-default sketch** — see that
section for the full evidence chain (census S3/S4 rows).

### Task 5: `milestone_live_data.py` — default pass-through + named-deviant overrides

**Files:**
- New: `services/market_watch/milestone_live_data.py`
- Modify: `services/market_watch/__init__.py` (export `extract`,
  `default_path_types_snapshot`)
- Modify: `tests/conftest.py` (new autouse reset fixture)
- Test: new `tests/test_milestone_live_data.py`

**Interfaces:**
- Produces: `extract(milestone_type: str, details: dict) -> dict` returning
  `{"status": str|None, "winner": str|None}`. Consumed by Task 6's two call sites.
  `default_path_types_snapshot() -> dict` for observability (wired properly in Task 5's own
  test only; the `state`/`GET /api/observability/summary` plumbing follows the
  `me_gate_snapshot()` precedent from `docs/superpowers/plans/2026-08-29-event-scoped-me-
  gate.md` Task 5 if a future task wires it in — not required for D2 to ship correctly,
  since `fault_log` already durably records every new type regardless).

- [ ] **Step 1: Write the failing tests**

```python
from services.market_watch import milestone_live_data as mld

def test_tennis_tournament_singles_is_pure_pass_through():
    # The census's control case (docs/superpowers/research/2026-08-30-kalshi-
    # category-data-shape-audit.md S3/S4): 49,764/49,764 payloads carry both
    # fields. Not in _EXTRACTORS - reaches this result via the default path.
    result = mld.extract("tennis_tournament_singles",
                          {"widget_status": "live", "winner": "Player A"})
    assert result == {"status": "live", "winner": "Player A"}


def test_an_unnamed_type_also_gets_pass_through_not_null():
    # basketball_game/soccer_tournament_multi_leg (15,564/15,546 milestones,
    # the census's 2nd/3rd-largest populations) are NOT individually named
    # anywhere in this module and must still resolve their real fields - the
    # regression this task exists to prevent (see Global Constraints).
    result = mld.extract("basketball_game", {"widget_status": "finished", "winner": "Lakers"})
    assert result == {"status": "finished", "winner": "Lakers"}


def test_golf_tournament_passes_through_status_but_not_winner():
    # S4: 0/169 golf_tournament live payloads carry `winner` - explicit None
    # until a real `leaderboard` item shape is read (spec §2.3).
    result = mld.extract("golf_tournament", {"widget_status": "live", "winner": "should not surface"})
    assert result == {"status": "live", "winner": None}


def test_settlement_input_types_are_explicit_no_ops():
    for t in ("company_report", "truflation", "artist_streams", "kpis", "tv_views", "one_off_milestone"):
        assert mld.extract(t, {"status": "reported", "winner": "should not surface"}) == \
            {"status": None, "winner": None}, t


def test_extract_never_raises_on_empty_or_none_details():
    assert mld.extract("tennis_tournament_singles", {}) == {"status": None, "winner": None}
    assert mld.extract("tennis_tournament_singles", None) == {"status": None, "winner": None}


def test_a_new_default_path_type_is_fault_logged_once_per_process(monkeypatch):
    faults = []
    monkeypatch.setattr("services.fault_log.record_fault",
                         lambda *a, **k: faults.append(a) or True)
    mld.extract("some_type_never_seen_before", {"widget_status": "live"})
    mld.extract("some_type_never_seen_before", {"widget_status": "live"})  # same process, same type
    assert len(faults) == 1


def test_default_path_types_snapshot_is_empty_when_nothing_seen():
    assert mld.default_path_types_snapshot() == {}


def test_default_path_types_snapshot_lists_distinct_types_seen():
    mld.extract("a_seen_type", {})
    mld.extract("another_seen_type", {})
    snap = mld.default_path_types_snapshot()
    assert snap["total_distinct_types"] == 2
    assert set(snap["types"]) == {"a_seen_type", "another_seen_type"}
```

- [ ] **Step 2: Run to verify all FAIL** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `services/market_watch/milestone_live_data.py`**, with the
  module docstring citing the S3/S4 evidence from Global Constraints verbatim (this is the
  one place in the codebase that decision needs to be legible without re-reading this
  plan), then:

```python
from services import fault_log

def _pass_through(details: dict) -> dict:
    return {"status": details.get("widget_status"), "winner": details.get("winner")}

def _golf_tournament(details: dict) -> dict:
    return {"status": details.get("widget_status"), "winner": None}

def _no_live_outcome(details: dict) -> dict:
    return {"status": None, "winner": None}

_EXTRACTORS = {
    # Only the census's named-deviant types (S3 ∪ S4) get their own entry.
    # esports_match and political_race are added by Tasks 7/8 once their
    # flagged assumptions (spec §2.3) are live-verified - until then they
    # take the _pass_through default below, which is IDENTICAL to today's
    # behavior for them (both currently read details.get("winner") raw), so
    # this task changes nothing observable for those two types yet.
    "golf_tournament": _golf_tournament,
    "company_report": _no_live_outcome,
    "truflation": _no_live_outcome,      # D3 §3.3 adds its OWN fields separately
    "artist_streams": _no_live_outcome,  # (indicator/timeseries etc.) - not status/winner
    "kpis": _no_live_outcome,
    "tv_views": _no_live_outcome,
    "one_off_milestone": _no_live_outcome,
}

_default_path_types_seen: set[str] = set()

def _record_default_path_type(milestone_type: str) -> None:
    # Fires once per distinct type per process (resets on restart, same
    # shape as the event-scoped-me-gate plan's _me_gate_unknown_logged) -
    # not once per call, so a hot-path type doesn't become a SQLite write
    # per signal (services/fault_log.py dedupes by message, but a write
    # still costs a connection open per call without this gate).
    if milestone_type in _default_path_types_seen:
        return
    _default_path_types_seen.add(milestone_type)
    fault_log.record_fault(
        "milestone_live_data", "default_path_milestone_type",
        f"{milestone_type}: no explicit extractor, using tennis-shaped pass-through "
        "by default - see module docstring for why default != null",
        severity="info",
    )

def default_path_types_snapshot() -> dict:
    if not _default_path_types_seen:
        return {}
    return {"total_distinct_types": len(_default_path_types_seen),
            "types": sorted(_default_path_types_seen)}

def extract(milestone_type: str, details: dict) -> dict:
    """Normalizes ANY milestone type's live-data `details` into
    {"status": ..., "winner": ...}. Default is pass-through
    (details.get("widget_status")/.get("winner")), matching this app's
    pre-existing behavior for the ~21 of 30 live-data-bearing milestone
    types the census found carry both fields with tennis-shaped values -
    NOT a null default (see this module's own docstring / this plan's
    Global Constraints for why)."""
    details = details or {}
    extractor = _EXTRACTORS.get(milestone_type)
    if extractor is None:
        _record_default_path_type(milestone_type)
        return _pass_through(details)
    return extractor(details)
```

  `services/market_watch/__init__.py`: add `from services.market_watch.milestone_live_data
  import extract, default_path_types_snapshot  # noqa: F401`, matching the file's existing
  re-export style.

  `tests/conftest.py`: add a fifth autouse fixture resetting
  `milestone_live_data._default_path_types_seen`, matching the existing four
  (`_fresh_whale_pipeline_perf` etc.) exactly in shape.

- [ ] **Step 4: Run to verify all PASS.**
- [ ] **Step 5: Run `tests/test_milestone_live_data.py` and the full `tests/` collection
  for the conftest fixture (no cross-test leak).**
- [ ] **Step 6: Commit:** `feat: shared milestone live-data extractor, default pass-through not null (kalshi-category-data-completeness Task 5)`

---

### Task 6: Wire `live_status.py`/`catalog_scan.py` to the extractor

**Files:**
- Modify: `services/market_watch/live_status.py` (line ~169)
- Modify: `services/market_watch/catalog_scan.py` (line ~117)
- Test: `tests/test_trading_gate.py`, `tests/test_winner_propagation.py`

**Interfaces:** Consumes `milestone_live_data.extract()` (Task 5). No change to either
function's own signature or return shape.

**Concrete finding, not glossed over:** both existing test files use **synthetic milestone
`type` values that are not real Kalshi types** and are not in Task 5's `_EXTRACTORS` — but
since Task 5's default path is pass-through (not null), these fixtures still work correctly
post-wiring *as long as they keep using a type that stays on the default path*. Verified
this session: `tests/test_trading_gate.py`'s `_FakeLiveClient` returns `{"id": "ms1",
"type": "game"}` (line ~1504) and `_FakeGameStateClient` returns `{"id": "ms1", "type":
"football_game"}` (line ~1706); `tests/test_winner_propagation.py`'s fixtures all use
`{"type": "winner_decl"}`. None of these three strings collide with `_EXTRACTORS`' 7 keys,
so they all resolve via the default pass-through path unchanged — **no fixture rewrite is
required for the wiring itself to pass**, but Step 1 adds one new test per file proving the
mechanism (not just the coincidence) by using a genuinely distinguishing case.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_trading_gate.py — add near _FakeLiveClient
def test_fetch_live_status_returns_none_for_a_settlement_input_type():
    # company_report is one of D2's named no-op types (Task 5) - proves the
    # wiring actually routes through milestone_live_data now, not still
    # reading details.get("widget_status") directly (which would return
    # "live" here since the raw key IS present in this fixture).
    main.state["live_status_cache"].clear()

    class _FakeReportClient:
        async def get_milestones_for_event(self, event_ticker):
            return [{"id": "ms1", "type": "company_report"}]

        async def get_live_datas(self, milestone_ids):
            return {mid: {"details": {"widget_status": "live"}} for mid in milestone_ids}

    markets = [_market_at(offset_sec=-300)]
    result = asyncio.run(main._fetch_live_status(_FakeReportClient(), markets))
    assert result == {}  # extract() returns status=None for company_report -> no confirmed entry
```

```python
# tests/test_winner_propagation.py — add near the existing winner fixtures
def test_propagate_milestone_winner_none_for_a_settlement_input_type(monkeypatch):
    # truflation is a D2 no-op type (Task 5): even with a raw `winner` key
    # present in details, the wired call site must not surface it as a
    # market result - it's an index series, not a resolution event.
    markets = [{"ticker": "M1", "event_ticker": "EVT1", "status": "open"}]
    fake = _milestone_client(
        milestones={"EVT1": [{"id": "ms1", "type": "truflation",
                               "related_event_tickers": ["EVT1-OUTCOME1"]}]},
        live_datas={"ms1": {"details": {"winner": "should not surface",
                                          "related_event_tickers": ["EVT1-OUTCOME1"]}}},
    )
    market_results = asyncio.run(main.propagate_milestone_winners(fake, markets))
    assert "EVT1-OUTCOME1" not in market_results
```

(Verify `test_winner_propagation.py`'s exact fake-client construction helper name — the
earlier grep found inline classes per test, not a shared `_milestone_client` factory;
adapt this test to whichever pattern the file actually uses, matching
`test_propagate_milestone_winner`'s own fixture shape line-for-line.)

- [ ] **Step 2: Run to verify both new tests FAIL** (today's raw `.get()` reads return
  `"live"`/`"should not surface"`, not the no-op result).

- [ ] **Step 3: Implement.**
  - `services/market_watch/live_status.py:169`: replace `status =
    details.get("widget_status")` with `status = milestone_live_data.extract(ms["type"],
    details)["status"]`. Import `from services.market_watch import milestone_live_data` (or
    the direct submodule path, matching this file's existing import style — check whether
    `live_status.py` already imports sibling `market_watch` submodules directly or via the
    package `__init__.py`, and match it).
  - `services/market_watch/catalog_scan.py:117`: replace `winner =
    details.get("winner")` with `winner = milestone_live_data.extract(ms["type"],
    details)["winner"]`.
  - Both call sites already have `ms["type"]` in hand (gated on `ms.get("id") and
    ms.get("type")` in both functions per the design's verified citation) — no new fetch.
- [ ] **Step 4: Run to verify both PASS.**
- [ ] **Step 5: Run `tests/test_trading_gate.py`, `tests/test_winner_propagation.py`,
  `tests/test_game_state.py` in full** (regression: confirm the pre-existing
  `"game"`/`"football_game"`/`"winner_decl"`-typed tests still pass via the default path,
  per the "concrete finding" note above).
- [ ] **Step 6: Commit:** `feat: live_status/catalog_scan read live-data through the shared extractor (kalshi-category-data-completeness Task 6)`

---

### Task 7: `esports_match` branch — gated on live-payload verification

**Files:**
- Modify: `services/market_watch/milestone_live_data.py` (`_EXTRACTORS["esports_match"]`)
- Test: append to `tests/test_milestone_live_data.py`

**Interfaces:** Adds one `_EXTRACTORS` entry. No signature change.

**This task cannot ship its type-specific branch from spec text alone — spec §2.3 and §6
item 2 both say so explicitly.** Two genuinely open questions, neither answerable from
`docs/kalshi/` (the shape is undocumented — `targets_and_milestones.md:28` only states
`details` "varies by milestone type", not the specific keys per type):

1. Does `esports_match`'s own `widget_status` already reliably distinguish live/finished
   (the census — S3 — confirms `esports_match` is *not* in the 7-type "missing
   widget_status" list, i.e. it already carries the field), making Task 5's default
   pass-through *already correct* for **status**, with only **winner** needing real work?
   Or does `widget_status` exist but not reliably mean "finished" for this type
   specifically (the design's own concern, citing `is_live` as a possibly-needed
   supplement)?
2. `winner` is confirmed absent (S4: `esports_match` is the single largest contributor to
   the 9.5% no-winner figure, 9,385 of 10,706) — the census names `home_score`/
   `away_score`/`is_live`/`home_periods`/`away_periods` as present, but not which
   combination reliably means "this match is over, and here's who won."

- [ ] **Step 0: Verification spike, before writing the branch's test.** Query this app's
  own captured history first (cheapest, per the never-guess HARD RULE's "when a check is
  cheap, run it instead of reasoning about it"): `SELECT raw_json FROM game_states WHERE
  event_type = 'esports_match' ORDER BY recorded_at` against `data/game_states.db`
  (read-only query, per CLAUDE.md's manual-verification rule) — `game_state.record()`
  already persists the full raw `details` payload for any event reaching a live-data poll
  (spec §2.4), so real esports_match lifecycle data may already exist without a new live
  watch. If multiple rows for the same `event_ticker` span a live→finished transition,
  read them directly to answer both questions above. If no such rows exist yet (this app's
  watchlist may not currently include an esports_match event), this sub-step is genuinely
  blocked on a live match being available to watch — **do not guess a mapping to unblock
  the plan**; if blocked, skip to Step 5 having done nothing else in this task, and record
  in `docs/open-decisions.md` that `esports_match`'s status/winner mapping is pending a
  live-payload capture (mirrors this repo's own tennis/AFL verification precedent,
  `services/kalshi/public.py:256-263`, which was resolved the same way — watching a real
  match go live, not assumed from the field name).

- [ ] **Step 1: Write the failing test(s)**, shaped by whatever Step 0 found. If Step 0
  confirms `widget_status` alone is reliable:

```python
def test_esports_match_status_passes_through_widget_status():
    result = mld.extract("esports_match", {"widget_status": "finished", "home_score": 2, "away_score": 1})
    assert result["status"] == "finished"

def test_esports_match_winner_derives_from_scores_only_once_finished():
    live = mld.extract("esports_match", {"widget_status": "live", "home_score": 2, "away_score": 1})
    assert live["winner"] is None  # never derive from a mid-match score
    finished = mld.extract("esports_match", {"widget_status": "finished", "home_score": 2, "away_score": 1,
                                               "home_team": "Team A", "away_team": "Team B"})
    assert finished["winner"] == "Team A"
```

  (Exact winner-derivation shape — which key names the winning side — depends entirely on
  Step 0's real payload; adapt field names to match what was actually captured, not this
  sketch.)

- [ ] **Step 2: Run to verify FAIL** (only if Step 0 unblocked this task).
- [ ] **Step 3: Implement** the `_esports_match(details)` function per Step 0's confirmed
  shape and register it in `_EXTRACTORS["esports_match"]`.
- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_milestone_live_data.py` in full.**
- [ ] **Step 6: Commit** (only if the branch shipped): `feat: esports_match live-data mapping, verified against a real match lifecycle (kalshi-category-data-completeness Task 7)` —
  cite the specific captured payload(s) or live observation used as evidence. **If Step 0
  blocked this task, commit nothing for it** and record the block in
  `docs/next-action.md`/`docs/open-decisions.md` instead, continuing to Task 8.

---

### Task 8: `political_race` branch — gated on live-payload verification (D4's base case)

**Files:**
- Modify: `services/market_watch/milestone_live_data.py`
  (`_EXTRACTORS["political_race"]`)
- Test: append to `tests/test_milestone_live_data.py`

**Interfaces:** Adds one `_EXTRACTORS` entry.

**Same shape as Task 7, one field easier.** `winner` is already confirmed present and
pass-through-safe (P3, the investigation: "774 [of 810] carrying `candidates`, `winner`,
`winners`, `race_call_status`, `reporting_percentage`, `tabulation_status`... a disjoint 36
carrying only a bare `provider: votehub`"), so **only `status` needs new logic** — deriving
it from `race_call_status`/`tabulation_status`, whose value vocabulary ("not yet called" vs
"called", per spec §2.3) is not documented anywhere in `docs/kalshi/` and must come from a
real payload.

- [ ] **Step 0: Verification spike.** Same method as Task 7 Step 0: `SELECT raw_json FROM
  game_states WHERE event_type = 'political_race'` first (cheap, may already have real
  data — P3's own census ran `GET /live_data/batch` over the full population and found 810
  real `political_race` live payloads, so captured history plausibly already exists even
  without a live election night). Read several real `race_call_status`/`tabulation_status`
  value combinations to build the status-vocabulary mapping into this app's existing
  tri-state (`"none"`/`"live"`/`"finished"`) — the same vocabulary `live_status.py:216`'s
  schedule fallback already uses (verify that exact mapping's current values before
  reusing it). If genuinely no captured `political_race` data exists and no election is
  imminent, this sub-step blocks the same way Task 7's can — same non-guessing resolution:
  record the block, do not invent a status mapping from field names alone.

- [ ] **Step 1: Write the failing test(s)**, shaped by Step 0's findings:

```python
def test_political_race_winner_is_pass_through():
    result = mld.extract("political_race", {"race_call_status": "called", "winner": "Candidate A"})
    assert result["winner"] == "Candidate A"

def test_political_race_no_winner_when_only_votehub_provider_present():
    # P3: 36 of 810 carry only `provider: votehub`, none of the race-call fields.
    result = mld.extract("political_race", {"provider": "votehub"})
    assert result["winner"] is None
```

  (Status-mapping tests depend on Step 0's real vocabulary — write them against whatever
  values were actually observed, not a guessed enum.)

- [ ] **Step 2: Run to verify FAIL** (if unblocked).
- [ ] **Step 3: Implement** `_political_race(details)` per Step 0's findings, registered
  in `_EXTRACTORS["political_race"]`.
- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_milestone_live_data.py` in full.**
- [ ] **Step 6: Commit** (only if unblocked): `feat: political_race live-data mapping, verified against real race-call payloads (kalshi-category-data-completeness Task 8)`.
  If blocked, same non-commit handling as Task 7.

---

### Task 9: Structured-target batch resolution for `custom_strike` (X6/§2.5)

**Files:**
- Modify: `services/kalshi/public.py` (new `get_structured_targets`)
- Modify: `services/market_watch/catalog_scan.py` (`propagate_milestone_winners`'s
  `custom_strike` matching block, line ~142-148)
- Test: append to `tests/test_kalshi_client.py`, `tests/test_winner_propagation.py`

**Interfaces:**
- Produces: `KalshiPublicGateway.get_structured_targets(ids: list[str]) -> dict[str,
  dict]` (ticker/id-keyed, mirroring `get_markets_by_tickers`'s existing return shape).
- Consumes into: `propagate_milestone_winners` resolves `custom_strike` UUIDs against real
  target names before matching `winner`, instead of the current UUID-vs-string substring
  match that can never succeed for `strike_type: "structured"` markets.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kalshi_client.py
def test_get_structured_targets_repeats_the_ids_param(monkeypatch):
    client = _client()
    calls = []

    async def fake_get(path, endpoint, params):
        calls.append((path, params))
        return {"structured_targets": [{"id": "uuid-1", "name": "Team Alpha", "type": "team"}]}

    monkeypatch.setattr(client, "_get_json", fake_get)
    result = asyncio.run(client.get_structured_targets(["uuid-1"]))
    assert result == {"uuid-1": {"id": "uuid-1", "name": "Team Alpha", "type": "team"}}
```

(`get-structured-targets.md:70-82` documents repeated `ids=` query params, up to 2000 per
call — verify the exact param-repeat mechanism the installed HTTP client uses for a list
value, matching whatever convention `_get_json` already applies elsewhere in this file,
before assuming `httpx`'s native list-param handling is what's wired in.)

```python
# tests/test_winner_propagation.py
def test_propagate_milestone_winner_resolves_structured_custom_strike(monkeypatch):
    # 134/149 sampled real markets are strike_type: "structured" - the
    # existing str(winner).lower() in str(v).lower() substring match can
    # never match a UUID, and silently falls through to the weaker
    # yes_sub_title/title match beneath it (or fails outright).
    markets = [{"ticker": "M1", "event_ticker": "EVT1", "status": "open"}]
    fake = _milestone_client(
        milestones={"EVT1": [{"id": "ms1", "type": "tennis_tournament_singles",
                               "related_event_tickers": ["EVT1-OUTCOME1"]}]},
        live_datas={"ms1": {"details": {"winner": "Team Alpha",
                                          "related_event_tickers": ["EVT1-OUTCOME1"]}}},
        related_markets={"EVT1-OUTCOME1": {
            "ticker": "EVT1-OUTCOME1",
            "strike_type": "structured",
            "custom_strike": {"target": "uuid-1"},
        }},
        structured_targets={"uuid-1": {"id": "uuid-1", "name": "Team Alpha", "type": "team"}},
    )
    market_results = asyncio.run(main.propagate_milestone_winners(fake, markets))
    assert market_results["EVT1-OUTCOME1"] is None or "EVT1-OUTCOME1" in market_results
    # exact assertion depends on the real fixture helper's shape (see note below) -
    # the load-bearing check is that ticker mapping succeeded via the resolved
    # NAME, not the raw uuid.
```

(This file's real fixture-construction pattern must be read and matched exactly — Step 1
above is a sketch of intent, not literal code to paste; the earlier grep of
`test_winner_propagation.py` found inline fake-client classes per test rather than one
parameterized `_milestone_client` helper, so this test needs its own inline fake extending
whichever existing fixture already supplies `get_markets_by_tickers`, adding
`get_structured_targets`.)

- [ ] **Step 2: Run to verify both FAIL.**

- [ ] **Step 3: Implement.**
  - `services/kalshi/public.py`: add `get_structured_targets(self, ids: list[str]) ->
    dict[str, dict]`, following `get_markets_by_tickers`'s exact chunking/return-shape
    precedent (`_get_json` with repeated `ids=` params per `get-structured-targets.md:70-
    82`'s documented up-to-2000-per-call limit; a batch-size constant analogous to
    `_MARKETS_BY_TICKERS_BATCH_SIZE`).
  - `services/market_watch/catalog_scan.py`'s `propagate_milestone_winners`: for each
    `related_market` whose `strike_type == "structured"`, collect every `custom_strike`
    UUID seen this tick (across all events being processed, not per-event — matching the
    existing `all_related`/`related_market_by_ticker` batching pattern already in this
    function), resolve the distinct set via one `get_structured_targets()` call, cache the
    result in `state["structured_targets_cache"]` (same shape/lifetime rationale as
    `state["category_metadata"]` — in-memory, not a new SQLite file, per spec §2.5), then
    match `winner` against the resolved target's `name` field instead of the raw
    `custom_strike` values.
- [ ] **Step 4: Run to verify both PASS.**
- [ ] **Step 5: Run `tests/test_kalshi_client.py`, `tests/test_winner_propagation.py` in full.**
- [ ] **Step 6: Commit:** `fix: structured custom_strike markets resolve via GET /structured_targets, not a UUID substring match (kalshi-category-data-completeness Task 9)` —
  cite docs read: `docs/kalshi/get-structured-targets.md`, `get-structured-target.md`,
  `targets_and_milestones.md:73-86`.

---

### Task 10: `get_events(with_milestones=True)` replaces per-event polling (X7/X8)

**Files:**
- Modify: `services/kalshi/public.py` (`get_events` signature)
- Modify: `services/market_watch/catalog_scan.py` (`propagate_milestone_winners`)
- Modify: `services/market_watch/live_status.py` (`_fetch_live_status`)
- Test: append to `tests/test_kalshi_client.py`, `tests/test_winner_propagation.py`,
  `tests/test_trading_gate.py`

**Interfaces:**
- Changes: `KalshiPublicGateway.get_events(event_tickers: list[str], with_milestones: bool
  = False) -> list[dict]` — additive optional param, default `False` preserves every
  existing caller's behavior untouched.
- Removes call sites' dependency on the per-event `client.get_milestones_for_event(et)`
  gather loop, replacing it with milestones read inline off each event returned by
  `get_events(to_poll, with_milestones=True)`.

**Kalshi contract note:** `docs/kalshi/get-events.md:114-118` documents `with_milestones`
as a real, current query param ("If true, includes related milestones as a field alongside
events"), verified this session. The exact key name the milestones array lands under on
each returned event object is **not confirmed in this pass** — read it directly off
`get-events.md`'s response schema (or a live/fixture response) in Step 0 before writing the
implementation, matching this file's own existing precedent of confirming SDK/doc shape
before coding (`get_series_list`'s docstring: "confirmed via the SDK's own docstring").

- [ ] **Step 0: `kalshi-contract-review`** on `docs/kalshi/get-events.md`, confirming the
  exact field name the inline milestones array uses on a returned event object, and
  whether the installed SDK's `get_events` wrapper exposes `with_milestones` under that
  exact parameter name (check both the Pydantic response model and the SDK call signature
  — this file's own `get_series_list` docstring already records one real case of the SDK
  lagging documented behavior).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kalshi_client.py
def test_get_events_passes_with_milestones_when_requested(monkeypatch):
    client = _client()
    calls = []

    async def fake_get_events(tickers, limit, with_milestones):
        calls.append(with_milestones)
        return type("R", (), {"events": [_FakeModel({"event_ticker": "EVT-A"})]})()

    monkeypatch.setattr(client._client, "get_events", fake_get_events)
    asyncio.run(client.get_events(["EVT-A"], with_milestones=True))
    assert calls == [True]


def test_get_events_default_omits_with_milestones_for_existing_callers(monkeypatch):
    client = _client()
    calls = []

    async def fake_get_events(tickers, limit, with_milestones):
        calls.append(with_milestones)
        return type("R", (), {"events": []})()

    monkeypatch.setattr(client._client, "get_events", fake_get_events)
    asyncio.run(client.get_events(["EVT-A"]))
    assert calls == [False]  # every existing call site keeps today's behavior exactly
```

  Plus one new regression test each in `tests/test_winner_propagation.py` and
  `tests/test_trading_gate.py` confirming `propagate_milestone_winners`/
  `_fetch_live_status` no longer call `get_milestones_for_event` at all once wired (assert
  a fake client without that method still works, or assert a call counter on it stays at
  0) — exact shape depends on Step 0's confirmed field name for the inline milestones.

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Implement.**
  - `services/kalshi/public.py`'s `get_events`: add `with_milestones: bool = False`
    parameter, forwarded to `self._client.get_events(..., with_milestones=with_milestones)`
    per Step 0's confirmed SDK param name; if the SDK doesn't expose it, fall back to the
    documented raw query param via whatever this file's `_get_json`-based precedent
    (`get_series_list`) already establishes for a case where the typed SDK lags.
  - `services/market_watch/catalog_scan.py`'s `propagate_milestone_winners`: replace the
    `client.get_milestones_for_event(et)` gather over `to_poll` with one
    `client.get_events(to_poll, with_milestones=True)` call; build `milestone_by_event`
    from each returned event's inline milestones field (Step 0's confirmed key) instead of
    from the old per-event gather's results — same downstream shape (`ms.get("id") and
    ms.get("type")` gating unchanged).
  - `services/market_watch/live_status.py`'s `_fetch_live_status`: identical
    restructuring for its own `to_poll`/`milestone_by_event` construction.
- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_kalshi_client.py`, `tests/test_winner_propagation.py`,
  `tests/test_trading_gate.py` in full.**
- [ ] **Step 6: Commit:** `perf: batch milestone discovery via get_events(with_milestones=True), replacing N per-event calls (kalshi-category-data-completeness Task 10)` —
  cite docs read: `docs/kalshi/get-events.md:114-118`. Cross-post to `services/market_watch/CHEATSHEET.md`.

---

## D1 Phase 2 — delta series refresh (§1.5)

### Task 11: `min_updated_ts`/`include_product_metadata` on `get_series_list`

**Files:**
- Modify: `services/kalshi/public.py` (`get_series_list`)
- Modify: `services/market_watch/catalog_scan.py` (`_get_series_cache`)
- Test: append to `tests/test_kalshi_client.py`, `tests/test_catalog_scan_pacing.py`

**Interfaces:**
- Changes: `get_series_list(self, category: str | None = None, min_updated_ts: int | None =
  None, include_product_metadata: bool = False) -> list[dict]` — additive optional params,
  raw `_get_json` params dict (this method is deliberately **not** routed through the
  typed SDK — see its own docstring on the `fee_type` enum-validation crash — so these are
  plain dict keys, not SDK kwargs).

**A load-bearing gotcha found this session, not in the spec:** `_get_series_cache()`
currently **replaces** `state["series_cache"]["series"]` wholesale on every refresh
(`cache["series"] = series`, `catalog_scan.py:207`) — correct today because
`get_series_list()` always returns the *complete* series list. Once `min_updated_ts` is
added, a refresh call legitimately returns only the series whose metadata changed since the
watermark; naively assigning that partial list to `cache["series"]` would silently shrink
the cache every series that *didn't* change out of `_get_top_series`, `_scan_catalog_batch`,
and `search_markets` — the exact completeness regression the HARD RULE forbids, and a
distinct, more immediate problem than the one spec §1.5 discusses (which only covers the
new `series_metadata` SQL table's own correctly-upserting behavior, not this pre-existing
in-memory/blob cache's replace-not-merge semantics). This task fixes both.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kalshi_client.py
def test_get_series_list_passes_min_updated_ts_and_product_metadata_when_given(monkeypatch):
    client = _client()
    captured = {}

    async def fake_get_json(path, endpoint, params):
        captured.update(params)
        return {"series": []}

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    asyncio.run(client.get_series_list(min_updated_ts=1700000000, include_product_metadata=True))
    assert captured == {"include_volume": True, "min_updated_ts": 1700000000,
                         "include_product_metadata": True}


def test_get_series_list_omits_new_params_by_default(monkeypatch):
    client = _client()
    captured = {}

    async def fake_get_json(path, endpoint, params):
        captured.update(params)
        return {"series": []}

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    asyncio.run(client.get_series_list())
    assert captured == {"include_volume": True}  # every existing caller unaffected
```

```python
# tests/test_catalog_scan_pacing.py
def test_get_series_cache_merges_a_delta_response_instead_of_replacing(tmp_path, monkeypatch):
    # The gotcha above: a delta response must not shrink the cache down to
    # only the series that changed.
    monkeypatch.setattr(series_cache, "DB_PATH", tmp_path / "series_cache.db")
    state["series_cache"] = {
        "fetched_at": 0.0,
        "series": [{"ticker": "OLD-UNCHANGED", "category": "Sports", "volume_fp": "100"}],
    }

    class _FakeDeltaClient:
        async def get_series_list(self, min_updated_ts=None):
            return [{"ticker": "NEW-CHANGED", "category": "Crypto", "volume_fp": "200"}]

        async def get_series_fee_changes(self, show_historical=True):
            return []

    monkeypatch.setattr(catalog_scan, "_SERIES_CACHE_TTL_SEC", 0)  # force refresh

    result = asyncio.run(catalog_scan._get_series_cache(_FakeDeltaClient()))

    tickers = {s["ticker"] for s in result}
    assert tickers == {"OLD-UNCHANGED", "NEW-CHANGED"}  # merged, not replaced
```

- [ ] **Step 2: Run to verify all three FAIL.**

- [ ] **Step 3: Implement.**
  - `services/kalshi/public.py`'s `get_series_list`: add the two params, extend the
    `params={"include_volume": True}` dict conditionally (only include a key when its
    argument is non-default, matching Step 1's exact-dict assertions above).
  - `services/market_watch/catalog_scan.py`'s `_get_series_cache()`: after the first full
    sync (i.e., once `cache["series"]` is non-empty), pass
    `min_updated_ts=max(int(s.get("last_updated_ts_epoch", 0)) for s in cache["series"] if
    ...)` — **verify during implementation whether `last_updated_ts` on a Series object is
    ISO-8601 (per Task 1's schema comment) or epoch-int; the watermark must be computed in
    whatever unit `get-series-list.md:100-108`'s `min_updated_ts` param actually expects
    (read the doc's exact type before writing this line — do not assume epoch-seconds
    matches the stored ISO string without a conversion)** and `include_product_metadata=
    True`. Rebuild the cache as a `dict[ticker -> series]` from the existing
    `cache["series"]`, update/insert every series in the new (possibly partial) response
    into that dict, then re-derive the sorted list from the dict's values before assigning
    it back to `cache["series"]` and calling `series_cache.save(...)`. First-ever sync
    (`cache["series"]` empty) keeps calling without `min_updated_ts` — a full fetch, exactly
    as today.
- [ ] **Step 4: Run to verify all three PASS.**
- [ ] **Step 5: Run `tests/test_kalshi_client.py`, `tests/test_catalog_scan_pacing.py`,
  `tests/test_series_cache.py` in full.**
- [ ] **Step 6: Commit:** `perf: series refresh polls min_updated_ts deltas after the first sync, merges not replaces (kalshi-category-data-completeness Task 11)` —
  cite docs read: `docs/kalshi/get-series-list.md:71-108`. Note the before/after call-shape
  change explicitly in the commit body per spec §1.5's "deserves its own before/after
  call-count verification rather than riding in on a pure-persistence change."

---

## D3 — Commodities Pyth feed, truflation/artist_streams

### Task 12: Commodities Pyth config flip + `request_underlying_list()` discovery

**Files:**
- Modify: `config/settings.yaml` (`index_feed.underlying_tickers`)
- Modify: `services/kalshi/websocket.py` (new `request_underlying_list`)
- Test: append to `tests/test_kalshi_trade_ws.py`

**Interfaces:**
- Produces: `KalshiStreamGateway.request_underlying_list() -> None`, mirroring
  `request_index_list()` (`websocket.py:546-565`) but keyed to the `pyth_value` sid and the
  `underlying_list` action (`docs/kalshi/pyth-value.md`'s `update_subscription` actions,
  confirmed this session: "Supports `update_subscription` with `subscribe_underlyings`,
  `unsubscribe_underlyings`, and `underlying_list` actions").

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kalshi_trade_ws.py
def _pyth_client():
    return KalshiStreamGateway(
        "https://external-api.kalshi.com/trade-api/v2",
        underlying_tickers=["Metal.XAU/USD", "Metal.XAG/USD"],
    )


def test_pyth_subscribe_fires_once_underlying_tickers_are_configured():
    client = _pyth_client()
    client._ws = _FakeWebSocket()

    asyncio.run(client._sync_subscriptions(force_subscribe=True))

    pyth_subs = [m for m in client._ws.sent if m["params"].get("channels") == ["pyth_value"]]
    assert len(pyth_subs) == 1
    assert pyth_subs[0]["params"]["underlying_tickers"] == ["Metal.XAU/USD", "Metal.XAG/USD"]


def test_request_underlying_list_is_a_noop_before_any_subscription():
    client = _pyth_client()
    client._ws = _FakeWebSocket()
    asyncio.run(client.request_underlying_list())  # no pyth_value sid yet
    assert client._ws.sent == []


def test_request_underlying_list_sends_the_documented_action():
    client = _pyth_client()
    client._ws = _FakeWebSocket()
    asyncio.run(client._sync_subscriptions(force_subscribe=True))
    client._subscription_sids["pyth_value"] = "sid-123"  # simulate a confirmed subscription

    asyncio.run(client.request_underlying_list())

    update_msgs = [m for m in client._ws.sent if m.get("cmd") == "update_subscription"]
    assert update_msgs[-1]["params"] == {"sid": "sid-123", "action": "underlying_list"}
```

(`_client()`/`_lifecycle_client()`/`_FakeWebSocket` are `tests/test_kalshi_trade_ws.py`'s
existing helpers, verified this session; `_subscription_sids` assignment mirrors how the
file's existing subscription-sid tests simulate a confirmed sid without a real WS reply —
verify the exact dict-population precedent for `cfbenchmarks_value` elsewhere in this file
before assuming `_subscription_sids["pyth_value"]` is the right key.)

- [ ] **Step 2: Run to verify the second and third tests FAIL** (the first should already
  pass today, since `websocket.py:1431`'s `if self.underlying_tickers:` gate already exists
  — this pins current correct behavior as a regression guard, not a new feature).

- [ ] **Step 3: Implement.**
  - `config/settings.yaml`: change `index_feed.underlying_tickers: []` to `["Metal.XAU/USD",
    "Metal.XAG/USD"]` (`docs/kalshi/pyth-value.md:164,289-291`'s own documented examples —
    gold and silver, the two Commodities underlyings the doc names). **Check `git status`
    on this file first** — this worktree's `config/settings.yaml` is already modified
    (uncommitted) per this session's own `git status` at start; read the existing diff
    before editing to avoid clobbering unrelated live-tuning changes, per
    `config-field-edit` skill guidance for schema-adjacent edits to a live-reloadable file.
  - `services/kalshi/websocket.py`: add `request_underlying_list(self) -> None`, copying
    `request_index_list`'s structure exactly but reading `self._subscription_sids.get(
    "pyth_value")` and sending `{"cmd": "update_subscription", "params": {"sid": sid,
    "action": "underlying_list"}}`.
- [ ] **Step 4: Run to verify all PASS.**
- [ ] **Step 5: Run `tests/test_kalshi_trade_ws.py` in full.**
- [ ] **Step 6: Commit:** `feat: seed Commodities Pyth underlyings, add underlying_list discovery (kalshi-category-data-completeness Task 12)` —
  cite docs read: `docs/kalshi/pyth-value.md`. Cross-post to `services/kalshi/CHEATSHEET.md`.

---

### Task 13: `truflation`/`artist_streams` extractor branches

**Files:**
- Modify: `services/market_watch/milestone_live_data.py`
- Test: append to `tests/test_milestone_live_data.py`

**Interfaces:** Task 5 registered these two types as explicit no-op for
`status`/`winner` (correct — they aren't resolution events). This task adds a **separate**
accessor for their real fields, not a change to `extract()`'s `status`/`winner` contract —
per spec §3.3, "the only change needed is D2's extractor gaining a branch... that returns
their real fields instead of `(None, None)`" refers to a *new* function, since `winner`/
`status` genuinely don't apply to these two types.

- [ ] **Step 0: Confirm the open scope question spec §3.3 names explicitly** — whether
  `truflation`/`artist_streams` events currently reach `_fetch_live_status`'s watchlist/
  near-term-catalog scope at all (they sit in Economics/Crypto/Entertainment, not the
  Sports-heavy population that path was built around). Check: `SELECT DISTINCT event_type,
  COUNT(*) FROM game_states WHERE event_type IN ('truflation', 'artist_streams')` against
  `data/game_states.db` (read-only). If rows exist, `game_state.record()` already reaches
  them (spec §2.4's capture mechanism is confirmed live) and this task's extractor branch
  is immediately useful. If zero rows, the extractor branch below still ships (it's correct
  and cheap regardless, per spec §3.3: "if it does reach them, this is nearly free... if it
  doesn't, the fix is D2's discovery-scope question, not a new capture mechanism") — but
  record the zero-rows finding in the commit message as the discovery-scope gap it is, not
  silently.

- [ ] **Step 1: Write the failing tests**

```python
def test_extract_index_series_returns_truflation_fields():
    result = mld.extract_index_series("truflation", {
        "indicator": "CPI", "latest_value": 3.2, "target_date": "2026-09-01",
        "series_key": "cpi-us", "timeseries": [{"t": 1, "v": 3.1}],
    })
    assert result == {"indicator": "CPI", "latest_value": 3.2, "target_date": "2026-09-01",
                       "series_key": "cpi-us", "timeseries": [{"t": 1, "v": 3.1}]}


def test_extract_index_series_returns_artist_streams_fields():
    result = mld.extract_index_series("artist_streams", {
        "current_total": 5000000, "timeseries_daily": [{"t": 1, "v": 100}],
        "timeseries_weekly": [{"t": 1, "v": 700}], "period_start": "2026-08-01",
        "period_end": "2026-08-07", "target_week_finalized": False,
    })
    assert result["current_total"] == 5000000
    assert result["target_week_finalized"] is False


def test_extract_index_series_returns_none_for_a_non_index_type():
    assert mld.extract_index_series("tennis_tournament_singles", {"widget_status": "live"}) is None
```

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Implement** `extract_index_series(milestone_type: str, details: dict) ->
  dict | None` in `services/market_watch/milestone_live_data.py`, with a small dispatch
  dict (`{"truflation": [...field names from P3...], "artist_streams": [...]}`) pulling
  exactly the fields the investigation's P3 census named per type (cited in this task's
  Interfaces above), returning `None` for any other type. No call site wiring in this task
  — spec §3.3 doesn't name a consumer yet (it's future D3 work reading `index_feed`-style),
  so this is capture-ready plumbing, not yet connected to a persistence path.
- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_milestone_live_data.py` in full.**
- [ ] **Step 6: Commit:** `feat: truflation/artist_streams field extraction, unwired pending a consumer (kalshi-category-data-completeness Task 13)` —
  state Step 0's watchlist-reach finding explicitly in the commit body.

---

## D4 — political_race structured-candidate resolution

### Task 14: Candidate structured-target resolution for `political_race`

**Files:**
- Modify: `services/market_watch/catalog_scan.py` (`propagate_milestone_winners`)
- Test: append to `tests/test_winner_propagation.py`

**Interfaces:** Extends Task 9's `get_structured_targets`/`state[
"structured_targets_cache"]` mechanism to `candidate_id_mapping`/`candidate_ids` (Elections
milestone `details` fields, per the investigation's P2: "the structural twin of Sports'
`home_team_id`/`away_team_id`"). No new resolution mechanism — one more UUID source flowing
through the same batch call Task 9 built.

**Depends on Task 8 landing first** (this only matters once `political_race`'s
status/winner mapping is real) **and Task 9** (the structured-target batch call this
reuses). Per spec §4, this task is deliberately sized last and thin — real value, but
episodic/calendar-driven, not to be prioritized ahead of D1/D2's steady-state value.

- [ ] **Step 1: Write the failing test**

```python
def test_propagate_milestone_winner_resolves_political_race_candidate_ids(monkeypatch):
    # P2: political_race details carry candidate_id_mapping/candidate_ids -
    # same structured-target-ID shape as Sports' home_team_id/away_team_id,
    # same fix as Task 9/X6, applied to a different milestone type.
    markets = [{"ticker": "M1", "event_ticker": "EVT1", "status": "open"}]
    fake = _milestone_client(
        milestones={"EVT1": [{"id": "ms1", "type": "political_race",
                               "related_event_tickers": ["EVT1-CAND1"]}]},
        live_datas={"ms1": {"details": {
            "winner": "cand-uuid-1",
            "candidate_id_mapping": {"cand-uuid-1": "EVT1-CAND1"},
            "related_event_tickers": ["EVT1-CAND1"],
        }}},
        related_markets={"EVT1-CAND1": {
            "ticker": "EVT1-CAND1", "strike_type": "structured",
            "custom_strike": {"candidate": "cand-uuid-1"},
        }},
        structured_targets={"cand-uuid-1": {"id": "cand-uuid-1", "name": "Candidate A",
                                              "type": "candidate"}},
    )
    market_results = asyncio.run(main.propagate_milestone_winners(fake, markets))
    assert "EVT1-CAND1" in market_results or market_results.get("EVT1-CAND1") is not None
    # exact assertion shape follows Task 9's fixture pattern once that lands
```

(Sketch only, matching Task 9's own caveat — this file's real fixture-construction idiom
must be read and matched at implementation time, and the exact `winner`-to-`custom_strike`
matching key for a `political_race`-shaped `custom_strike` dict needs confirming against a
real payload — Step 0 below.)

- [ ] **Step 0: `kalshi-contract-review` / verification spike** — confirm
  `candidate_id_mapping`'s real key/value orientation (candidate UUID → market ticker, or
  the reverse) against a real `political_race` payload (reuse Task 8's Step 0 capture if
  it's still available; if Task 8 was blocked, this task is blocked too and should record
  the same open-decision note rather than guessing the mapping direction).

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Implement.** Extend `propagate_milestone_winners`'s structured-target
  resolution (Task 9) to also collect `candidate_id_mapping`/`candidate_ids` UUIDs from
  `political_race`-typed milestones into the same distinct-UUID batch resolved via
  `get_structured_targets()`, matching `winner` (a candidate UUID per P2) against the
  resolved target's `name`.
- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_winner_propagation.py` in full.**
- [ ] **Step 6: Commit:** `feat: political_race candidate_id_mapping resolves via structured_targets, same mechanism as X6 (kalshi-category-data-completeness Task 14)`

---

## Self-review

**Spec coverage:** §1.2-§1.4 → Tasks 1, 3; §1.6 → Task 4; §1.8 → Task 2; §1.5 → Task 11;
§2.2-§2.3 → Tasks 5 (corrected architecture), 6, 7, 8; §2.5 → Task 9; §2.6 → Task 10;
§3.2 → Task 12; §3.3 → Task 13; §4 → Task 8 (base case) + Task 14 (structured-candidate
resolution). §3.1 (Weather) and §3.4 (forecast-percentile history, X14) are out of scope
per the task brief and spec §5, respectively — not silently dropped, explicitly excluded
in both places.

**The one substantive deviation from the spec's literal text** is Task 5's default-
pass-through architecture, replacing the spec §2.2 sketch's null default — documented in
Global Constraints with the exact evidence (census S3/S4 rows, `basketball_game`/
`soccer_tournament_multi_leg` population counts) and flagged as the top item in this plan's
completion report, not buried.

**Placeholder scan:** every task has real code for its non-gated steps. Tasks 7, 8, 13 (and
14, which depends on 8) carry genuine execution-time verification gates the spec itself
requires (§2.3, §6 item 2) rather than fabricated mappings — each names the exact query to
run against this app's own already-accumulating data before falling back to "blocked,
record it, don't guess."

**Left for execution-time resolution, not planned around by guessing (reported to the
requester separately, per this pipeline's own headless convention):**
- Tasks 7/8's live-payload verification spikes (esports_match terminal-status marker,
  political_race status vocabulary) — genuinely unknowable without either real captured
  data (may already exist in `data/game_states.db`) or a live match/race to watch. Task 14
  inherits Task 8's dependency.
- Task 2 Step 0's fee-changes absent-ticker semantics (does `/series/fee_changes` list
  every series or only ones with an actual change event) — a `kalshi-contract-review` read
  the plan gates on but doesn't answer here, since it wasn't needed to write the plan
  itself (the merge logic is correct either way once the answer is confirmed).
- Task 10 Step 0's exact inline-milestones field name on a `with_milestones=true` event
  response, and Task 11 Step 3's `min_updated_ts` unit (epoch vs. ISO) — both flagged as
  read-before-write gates rather than assumed.

**Type/interface consistency:** `series_of()`'s signature is unchanged everywhere it's
cited (Tasks 3); `milestone_live_data.extract()`'s `{"status", "winner"}` contract is
identical across Tasks 5-8; `get_structured_targets`'s ticker-keyed dict shape matches
`get_markets_by_tickers`'s established precedent in both Tasks 9 and 14; every new
`series_metadata`/`series_tags` column name in Task 1 matches spec §1.2's schema verbatim.
