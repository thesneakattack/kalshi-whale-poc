# Strategy Edge Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the approved edge/EV entry gate design into working, tested,
paper-mode-only code: an inert-by-default config surface, an always-on
markout-capture sweep, a bucketed `Δ_calibrated` estimator, the gate itself
inside `_validate_entry_price`, and the informativeness/reporting surfaces
the design's own success criteria (§9) require — in that order, because
each later piece depends on data or plumbing the earlier ones create.

**Design basis (GO, cleared for this stage):**
`docs/superpowers/specs/2026-09-03-strategy-edge-gate-design.md` (design,
post-fix), `docs/superpowers/specs/2026-09-03-strategy-edge-gate-design-review.md`
(independent adversarial review, verdict GO-AFTER-FIXES, all must-fix items
applied), `docs/superpowers/specs/2026-09-03-strategy-edge-gate-design-consolidation.md`
(verdict GO). Per CLAUDE.md's "nothing advances on one pass" HARD RULE,
"implementation plan" is this pipeline's third, separate stage — this
document, not the code it describes. **Zero code/config/data changed while
drafting this document; every fact below was independently re-verified
against current source (2026-09-03), not copied from the design's own
citations without re-checking.**

## What changed since the design was authored (re-verified 2026-09-03, not assumed)

The design was authored and reviewed the same day this plan is written, so
most of its file:line citations still hold — spot-checked directly, not
trusted:

- `services/strategy_engine.py`: `evaluate()` still spans lines 285-684
  (`_skip` starts at 685); `_validate_entry_price` still spans 149-202;
  its call site inside `evaluate()` is still at line 579; the price-band
  checks (`min_unit_cost`/`max_unit_cost`) are still the last checks
  before `return EntryValidation(True)`. All confirmed by direct
  `grep`/`sed` reads, not carried over from the design doc.
- `kalshi_fees.unit_cost` (line 323), `taker_fee_per_contract` (line 289),
  `market_history.recent_price` (line 306), `market_history.prune`
  default `168.0` (line 357), `market_history._TICKER_SNAPSHOT_MIN_INTERVAL_SEC
  = 5.0` (line 50 — **defined in `market_history.py` itself, not
  `whale_stream_handlers.py`** as the design's prose implied by citing only
  the call site; the call site is real, confirmed at
  `services/whale_stream/whale_stream_handlers.py:334`, but there is
  currently only **one** live call site there, not the two line numbers
  the design cited — a minor, non-blocking citation looseness, noted so
  this plan doesn't repeat it), `signal_log.signals`' schema (base
  `CREATE TABLE` at line 55 plus five `_add_column_if_missing` calls —
  `factors_json` line 87, `raw_notional_usd`/`raw_spread`/`raw_volume_24h`
  lines 97-99, `price` line 130, `excluded` line 146),
  `main.py`'s `_maybe_prune_capture_stores` (line 180, module-global
  `_last_capture_prune_at` at 151, called from the tick loop at line 354)
  and `correct=(result == item["side"])` (line 301) — all reconfirmed by
  direct read.
- `config/settings.yaml`'s `strategy:` block (lines 71-105 in this
  worktree's own working copy) ends at `price_staleness_corroborate_sec`,
  immediately before `risk:` — this is where Task 1 appends the 8 new
  fields. **This worktree's own committed `config/settings.yaml` differs
  from the primary checkout's live file** (confirmed: `diff` between the
  two shows `markets_watchlist_mode`, `max_children_per_parent`,
  `kalshi.categories`, and the analyst-factor-audit comment block all
  differ) — the same already-tracked instability the design's own
  §0/Finding 7 documents (second-pass audit §4.4, "third data-wipe of the
  same shape"). (Corrected after independent adversarial review, should-fix
  item 3: an earlier version of this line additionally claimed this
  worktree's own copy was uncommitted-`M` against its own git HEAD — that
  is not true; `git status --short config/settings.yaml` is clean in this
  worktree. The divergence that matters is against the *primary checkout's
  live file*, not this worktree's own history.) Task 1's own Step 1
  re-reads the live file immediately before editing rather than trusting
  the line numbers stated here, for exactly this reason.

**Three concrete plumbing gaps this plan found that the design did not
fully specify (its own self-review flagged bucket boundaries and this
class of detail as deliberately deferred to plan stage — these are that
deferral being resolved, not a defect in the design):**

1. **No per-ticker `fee_type` reader exists yet.** `services/series_cache.py`
   (confirmed by `grep -n '^def ' services/series_cache.py`) has `load()`
   (the whole cached series list) and raw table writes, but no function
   that answers "what's this one series' `fee_type`." The design's §1.3
   point 3 says the gate "reads that ticker's series `fee_type` from
   `services/series_cache.py` (already persisted, zero new fetch)" — true
   for the data (the `series_metadata` table already has a `fee_type`
   column, `services/series_cache.py:57`, keyed by series ticker), false
   for there already being a function to read it. Task 2 adds one.
2. **`market_lookup.effective_close_time(m)` needs a live per-tick market
   dict (`m`) and isn't usable for `t+close` markout capture weeks after
   entry**, when the ticker is very likely no longer in the current tick's
   fetched `markets` list. The design's §4.2 cites this function as "the
   same close time `evaluate()` already resolves — reused, not
   re-derived," which is correct for *entry-time* gating inside
   `evaluate()` but doesn't transfer to a sweep that runs independently,
   later, over old tickers. The actual durable, ticker-scoped store is
   `market_catalog.py`'s `markets.close_ts` column — confirmed by reading
   `services/diagnostics/diagnostics.py:74-96`'s `_close_ts_for_tickers`,
   whose own docstring states plainly: "ticker -> close_ts, from
   market_catalog (**the one store that persists a close time per market
   beyond the rotating watchlist**)." Task 4 adds a small, public,
   synchronous sibling of that private async helper directly on
   `market_catalog.py` (the module that owns the table), rather than
   reusing the private async one across a sync/async boundary it wasn't
   built for. **Stated limitation, not silently smoothed over:** this
   uses only `close_ts` (Kalshi's raw administrative close time, tier 4 of
   `effective_close_time`'s own precedence), not the fuller
   `expected_expiration_time`/`event_schedule`/`occurrence_datetime`
   precedence — `market_catalog.markets` doesn't persist those upstream
   fields. For the same event-style markets `effective_close_time` was
   built to correct for (a market whose administrative close is weeks
   after its real-world outcome is known), a `t+close` markout captured
   against raw `close_ts` will be captured later than the "real" close —
   diluting, not invalidating, that one offset's signal. `t+5m`/`t+1h`
   markouts are unaffected (pure `entry_ts + offset_sec` arithmetic, no
   close-time lookup at all). Flagged for `docs/next-action.md`/
   `docs/open-decisions.md` if Task 10's live validation shows this
   materially distorting the `t+close` numbers specifically.
3. **`_bucket_delta`'s bucketing dimension is category × price-band, not a
   single factor's rank-based tertile split**, so it cannot be a literal
   clone of `_bucket_win_rates` (which splits by *rank* on one continuous
   factor specifically to stay robust against a near-constant factor).
   Category and price-band are a natural, already-meaningful, externally
   fixed grid (`trade_category`'s own category strings; unit-cost bands
   matching this app's already-shipped `_CONFIDENCE_BANDS` fixed-width
   convention in the same module) — Task 6 uses fixed bins for both
   dimensions, not rank tertiles, and says so as a deliberate difference
   from `_bucket_win_rates`'s mechanism, reusing that function's *guard
   philosophy* (insufficient-data falls back to a neutral, documented
   value; never fabricate a split) rather than its bucketing algorithm.

## Architecture — five layers, each testable and shippable independently

1. **Config surface** (Task 1): 8 new `strategy.edge_gate_*` fields, every
   one defaulting to a value that changes nothing (`edge_gate_enabled:
   false` is the master switch). Ships first because everything after it
   reads these fields but nothing downstream can do anything until they
   exist.
2. **Markout capture** (Tasks 3-4): a new `markouts` table in
   `market_history.db` (same file, per the design's own alternative-2
   choice) and a low-frequency sweep matching the `_maybe_prune_capture_stores`
   idiom exactly. **This is the one piece that runs unconditionally,
   regardless of `edge_gate_enabled`** — the design's §5/§8 are explicit
   that markout data has to exist before there's anything to decide
   whether to turn the gate on with. Ships second, ahead of the gate
   itself, so real markout data has time to accumulate before Task 10's
   live validation needs to look at it.
3. **`Δ_calibrated`** (Tasks 5-6): a new bounded read (`signal_log.
   resolved_signals_for_edge_calibration`) plus a new bucketed-mean
   estimator (`confidence_calibration._bucket_delta_by_category_price_band`),
   sample-size-gated via the already-shipped `stats_power.min_n_for_margin`
   exactly as the design's §2.4 specifies.
4. **Recompute cache + the gate itself** (Tasks 7-8): an hourly
   recompute-and-cache step (same idiom as layer 2's sweep) feeding a
   lookup the gate calls synchronously, then the gate itself, appended
   inside `_validate_entry_price` after the existing price-band checks —
   never a separate call in `evaluate()`, per the design's §3.3 "closes
   the four-entry-gate-bypass shape for edge too" reasoning.
5. **Informativeness** (Task 9): persisting `p_est`/`Δ_calibrated`/`edge`
   per-signal and confirming `candidate_log.population_gate_summary()`
   already picks up the new `"edge_gate"` rejection name with zero extra
   plumbing (the design's own claim — Task 9 is where that claim gets a
   real test, not just trusted).

**Tech Stack:** Python 3 stdlib + this repo's own existing modules only
(`sqlite3`, `stats_power`, `kalshi_fees`, `market_history`, `signal_log`,
`trade_category`, `candidate_log`) — no new dependency, matching this
repo's existing minimalism and the design's own "reuse, never re-derive"
framing (§1.3).

## Global Constraints

- **Paper mode only, no trading-gate changes.** Nothing in this plan
  touches `kalshi_account.trading_enabled`, `services/risk_manager.py`'s
  daily-loss kill switch, `_effective_entry_threshold`'s confidence bar,
  `kelly_scaled_max_size`, or any existing gate's semantics — the design's
  own Non-goals, carried forward unchanged. Every task below is additive:
  a new config field, a new table, a new function, or a new check
  appended after existing checks — nothing existing is deleted or
  reordered.
- **`edge_gate_enabled` stays `false` at the end of this plan.** Task 10
  validates the mechanism (new tests pass, the route/route-adjacent
  reporting surfaces show the new data, `_validate_entry_price`'s
  behavior with the gate off is provably unchanged) but does **not** flip
  the switch in the live `config/settings.yaml` — that's a separate,
  later, human decision (design §6 point 1: "a deliberate, logged config
  change"), out of this plan's scope the same way the tier0 precedent
  plan's Task 8 recorded a finding without acting on it.
- **The markout-capture sweep (Tasks 3-4) is a real, new, always-on
  runtime cost, not covered by the opt-in framing above** — per the
  design's own §5/§8 disclosure. Task 4 has its own explicit runtime-cost
  measurement step, per the data-plane HARD RULE's "any diagnostic or
  abstraction on the exchange-wide hot path is measured for runtime cost
  before it ships" — this sweep is off the hot request path (it's a
  background tick-loop task, not `evaluate()` itself) but still a new,
  unconditional SQLite write pattern and gets measured, not assumed
  cheap by analogy.
- **`_validate_entry_price`'s new DB reads (Task 8) are also measured**,
  separately, per the design's §3.3 note that this function currently has
  zero network/DB access and this plan changes that — measured with
  `edge_gate_enabled: true` in a local/test run (never in the live
  primary config), not asserted safe because the tick loop already does
  other SQLite reads elsewhere.
- **`dimensional-analysis` pass required before Task 8/Task 10 are
  considered done**, per CLAUDE.md's HARD RULE — every quantity in the
  edge formula (`q_pre_now`, `p_est_side`, `ask_now`, `fee`, `edge`,
  `min_edge`) is a side-relative probability/dollar-per-contract value;
  Task 8's own Step 5 runs this explicitly rather than treating Task 8's
  own tests as a substitute for it.
- **Fee model: reuse only.** `f(ask_now)` is always
  `kalshi_fees.taker_fee_per_contract(price, ticker)` plus
  `strategy.edge_gate_fee_buffer_usd` — never a hand-rolled
  `0.07 * p * (1-p)` literal anywhere in this plan's code, per the
  design's §1.3 point 1.
- **The `flat`-fee-type fail-closed check and the `P_pre`-unavailable
  fail-open default are both implemented exactly as the design specifies
  them (§5)** — this plan does not re-litigate either choice. The
  `P_pre`-fail-open default specifically is a judgment call the design's
  own §5 explicitly asks `docs/open-decisions.md` to carry forward rather
  than settling unilaterally; Task 8 implements the stated default
  (fail-open) **and** adds the `docs/open-decisions.md` line the design
  asked for, rather than silently treating the design's default as the
  final word. This is not a blocking prerequisite — the design already
  made the call; the open-decisions line is about *revisiting* it later
  with real markout data (§6), not about whether to ship with it now.
- **No Kalshi field, endpoint, or schema is newly parsed.** Every Kalshi
  fact this plan depends on (`unit_cost`'s side convention, the fee
  formula, `fee_type`'s enum values) was already verified against
  `docs/kalshi/` by the design (§1.2) and is only *reused* here via
  already-tested functions (`kalshi_fees.unit_cost`,
  `kalshi_fees.taker_fee_per_contract`) — no task in this plan needs a
  fresh `kalshi-contract-review` pass, since none adds a new Kalshi
  field/endpoint/message type.
- **This repo has no `pytest-asyncio`.** Every test in this plan uses
  plain `def test_...` with direct function calls or `monkeypatch`, never
  `async def test_...`/`@pytest.mark.asyncio` — matching every existing
  test file this plan extends.
- **TDD throughout**, per `superpowers:test-driven-development`: every
  task's first step is a failing test against current source, confirmed
  failing, before any implementation step.

---

### Task 1: Config surface — 8 new `strategy.edge_gate_*` fields, all inert

**Files:**
- Modify: `config/settings.yaml` (`strategy:` block, immediately before
  `risk:` — confirm this is still the exact insertion point by re-reading
  the live block first, since this file is independently confirmed
  unstable under uncommitted edits, see "What changed" above)
- Test: `tests/test_strategy_engine.py` (extend `_cfg`'s docstring/usage —
  no new test file; this task's own regression check is Step 3 below)

**Interfaces:** Adds these keys to `strat_cfg` (read via `strat_cfg.get(...)`,
same as every other `strategy.*` field): `edge_gate_enabled` (bool, default
`false`), `edge_gate_min_edge` (float, default `0.04`),
`edge_gate_fee_buffer_usd` (float, default `0.005`),
`edge_gate_pre_print_offset_sec` (float, default `10.0`),
`edge_gate_p_pre_max_age_sec` (float, default `600.0`),
`edge_gate_min_bucket_n` (int, default `50`),
`edge_gate_recompute_interval_sec` (float, default `3600`),
`edge_gate_markout_offsets_sec` (list, default `[300, 3600, null]`) — exact
names, defaults, and rationale from the design's §5 table, not re-derived
here. Because `strat_cfg` already passes through `config_overrides.resolve()`
(confirmed: `services/strategy_engine.py:345,703`), every one of these 8
fields automatically supports the existing per-category/per-series
override mechanism with zero extra plumbing — worth noting in the PR body,
not a claim this task needs to test separately (the resolver is
field-agnostic and already tested).

- [ ] **Step 1: Confirm the current end of the `strategy:` block**

Run: `grep -n '^strategy:\|^risk:' config/settings.yaml` and
`sed -n '/^strategy:/,/^risk:/p' config/settings.yaml`. Confirm the block
still ends with `price_staleness_corroborate_sec: 120.0` immediately
before `risk:` — if not, insert after whatever the actual last
`strategy.*` key is instead of assuming this plan's citation is still
current.

- [ ] **Step 2: Add the 8 fields with inline rationale comments**

Insert, immediately before `risk:`:

```yaml
  # Strategy edge gate (docs/superpowers/plans/2026-09-03-strategy-edge-
  # gate-implementation.md) - compares this app's own belief about a
  # market (p_est) to the price it would actually pay (ask_now + fees),
  # instead of gating entry on confidence alone. Every field below
  # defaults to a value that changes nothing about current
  # _validate_entry_price behavior until edge_gate_enabled is explicitly
  # flipped true - same "ships fully built, opt-in" precedent as
  # kelly_fraction_of_cap. See the design doc's §5 for full rationale;
  # not restated per-field here to avoid two drifting copies of the same
  # explanation.
  edge_gate_enabled: false
  edge_gate_min_edge: 0.04
  edge_gate_fee_buffer_usd: 0.005
  edge_gate_pre_print_offset_sec: 10.0
  edge_gate_p_pre_max_age_sec: 600.0
  edge_gate_min_bucket_n: 50
  edge_gate_recompute_interval_sec: 3600
  edge_gate_markout_offsets_sec: [300, 3600, null]
```

- [ ] **Step 3: Confirm nothing else changed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_strategy_engine.py tests/test_config_bounds.py tests/test_config_store.py -q"`
Expected: same pass count as before this edit — a new, unreferenced config
key changes nothing about any existing code path (nothing reads these
keys yet; that's Task 8). Also run `docker exec ddev-kalshi-whale-poc-fastapi
sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -c \"import yaml; yaml.safe_load(open('config/settings.yaml'))\" && echo YAML_OK"`
to confirm the file still parses (a `null` inside a flow-style list is
valid YAML, but confirm rather than assume).

**Safety:** Config-only, no code path reads these keys until Task 8 —
provably zero behavior change (Step 3's regression run is the proof).

---

### Task 2: `series_cache.get_fee_type()` — the missing per-series accessor

**Files:**
- Modify: `services/series_cache.py` (new function, after `load()`)
- Test: `tests/test_series_cache.py` (extend existing file)

**Interfaces:** `get_fee_type(series_ticker: str) -> str | None` — a
single-row `SELECT fee_type FROM series_metadata WHERE ticker = ?` against
the already-populated table (`services/series_cache.py:44-58`'s schema,
written by `save()`'s existing `series_metadata` upsert). Returns `None`
when the series isn't cached yet (never fetched, or fetched before this
column existed) — the caller (Task 8) treats `None` the same as an
unknown/missing value, not as "not flat," per the fail-open-but-logged
idiom the rest of this plan follows.

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_series_cache.py` (check its existing fixture/import
style first: `grep -n '^def \|^import\|^from' tests/test_series_cache.py`):

```python
def test_get_fee_type_returns_the_stored_value(tmp_path, monkeypatch):
    """series_metadata.fee_type is already written by save() - this is
    just the read side that Task 8's fail-closed flat-fee-type check
    needs and that didn't exist before this task (confirmed by grep -n
    '^def ' services/series_cache.py before writing this plan - only
    load()/save() existed, no per-series read)."""
    from services import series_cache as sc

    monkeypatch.setattr(sc, "DB_PATH", tmp_path / "series_cache.db")
    sc.save(1234.0, [{"ticker": "KXBTC15M", "fee_type": "quadratic"}])

    assert sc.get_fee_type("KXBTC15M") == "quadratic"
    assert sc.get_fee_type("NOT-CACHED-YET") is None
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_series_cache.py -k test_get_fee_type -q"`
Expected: **fails** with `AttributeError: module 'services.series_cache'
has no attribute 'get_fee_type'`.

- [ ] **Step 2: Implement**

Add to `services/series_cache.py`, after `load()`:

```python
def get_fee_type(series_ticker: str) -> str | None:
    """One series' fee_type from series_metadata (already populated by
    save() - see the module docstring's `docs/kalshi/get-series-list.md`
    FeeType schema). None when this ticker was never fetched/cached, not
    "not flat" - Task 8 (strategy-edge-gate-implementation.md) treats an
    unknown fee_type the same as any other missing-data case: logged,
    fails open on the OTHER checks, but this specific gate's own
    fail-closed rule (§1.3 of the design) only fires on a CONFIRMED
    "flat" value, never on absence."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT fee_type FROM series_metadata WHERE ticker = ?", (series_ticker,)
        ).fetchone()
    return row[0] if row else None
```

- [ ] **Step 3: Confirm the test passes and nothing else regressed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_series_cache.py -q"`
Expected: all tests pass, including the new one.

**Safety:** Read-only addition to an already-persisted table; no writer
changes, no call site wired yet (Task 8 wires it) — cannot affect any
running behavior on its own.

---

### Task 3: `markouts` table + capture-eligible-entries query in `market_history.py`

**Files:**
- Modify: `services/market_history.py` (`_init_schema`, new functions
  after `compute_hypothetical_trades`)
- Test: `tests/test_market_history.py` (extend existing file)

**Interfaces:**
- `_init_schema` gains one new `CREATE TABLE IF NOT EXISTS markouts` (same
  function every `_connect(DB_PATH)` call already runs, so both the
  regular and `_scoring_read_connection` paths pick it up for free, no
  separate migration call site).
- `pending_markout_targets(trades: list[dict], offsets_sec: list[float | None], now: float, close_ts_by_ticker: dict[str, float]) -> list[dict]`
  — pure function, no DB access itself: given a list of trade dicts
  (`id`, `ticker`, `side`, `price`, `timestamp`), the configured offsets,
  and a ticker->close_ts map (Task 4 supplies both `trades` and
  `close_ts_by_ticker`), returns the `(trade_id, offset_label, target_ts)`
  tuples that are (a) due (`target_ts <= now`) and (b) not already
  captured (checked against `already_captured` below). Kept DB-free so
  it's trivially unit-testable without a live database.
- `already_captured_offset_labels(trade_ids: list[str]) -> dict[str, set[str]]`
  — one query, `trade_id -> set of offset_labels already in markouts`,
  for `pending_markout_targets`'s caller to filter against.
- `record_markout(trade_id, ticker, entry_side, entry_price, entry_ts, offset_label, offset_sec, target_ts, markout_price, captured_at) -> None`
  — one `INSERT OR IGNORE` row (idempotent — a re-run sweep that races
  itself, or restarts mid-cycle, never double-captures the same
  `(trade_id, offset_label)`).

Schema (added to `_init_schema`, same function/connection every existing
`snapshots`/`outcomes` table uses):

```python
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS markouts (
            trade_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            entry_side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            entry_ts REAL NOT NULL,
            offset_label TEXT NOT NULL,
            offset_sec REAL,
            target_ts REAL NOT NULL,
            markout_price REAL,
            captured_at REAL NOT NULL,
            PRIMARY KEY (trade_id, offset_label)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_markouts_ticker ON markouts (ticker)")
```

**Why `offset_label` (a string: `"300"`, `"3600"`, `"close"`) instead of
using `offset_sec` (nullable, for `t+close`) as part of the primary key:**
SQLite (like standard SQL) treats `NULL` as distinct-from-itself inside a
`PRIMARY KEY`/`UNIQUE` constraint — two `t+close` rows for the same trade
would both insert successfully instead of the second being ignored,
silently defeating this table's own idempotency guarantee. `offset_label`
is always non-null (`str(int(offset_sec))` for a numeric offset, the
literal string `"close"` when `offset_sec` is `None`), so the composite
key actually enforces "one row per trade per configured offset." This is
standard, well-established SQLite constraint semantics (not a Kalshi fact,
not something `docs/kalshi/` bears on) — not a guess.

- [ ] **Step 1: Write the failing tests first**

Add to `tests/test_market_history.py` (check its existing `_mh(tmp_path,
monkeypatch)` fixture first):

```python
def test_pending_markout_targets_selects_due_uncaptured_offsets(tmp_path, monkeypatch):
    mh = _mh(tmp_path, monkeypatch)
    now = 1_000_000.0
    trades = [{"id": "t1", "ticker": "TICK-A", "side": "yes", "price": 0.6, "timestamp": now - 400}]
    # 300s offset is due (400 > 300); 3600s is not (400 < 3600); close_ts unknown for this ticker.
    targets = mh.pending_markout_targets(
        trades, offsets_sec=[300, 3600, None], now=now, close_ts_by_ticker={},
    )
    labels = {t["offset_label"] for t in targets}
    assert labels == {"300"}
    assert targets[0]["target_ts"] == now - 400 + 300


def test_pending_markout_targets_uses_close_ts_for_the_close_offset(tmp_path, monkeypatch):
    mh = _mh(tmp_path, monkeypatch)
    now = 1_000_000.0
    trades = [{"id": "t1", "ticker": "TICK-A", "side": "yes", "price": 0.6, "timestamp": now - 400}]
    targets = mh.pending_markout_targets(
        trades, offsets_sec=[None], now=now, close_ts_by_ticker={"TICK-A": now - 100},
    )
    assert len(targets) == 1
    assert targets[0]["offset_label"] == "close"
    assert targets[0]["target_ts"] == now - 100


def test_pending_markout_targets_skips_close_offset_with_no_known_close_ts(tmp_path, monkeypatch):
    mh = _mh(tmp_path, monkeypatch)
    now = 1_000_000.0
    trades = [{"id": "t1", "ticker": "TICK-A", "side": "yes", "price": 0.6, "timestamp": now - 400}]
    targets = mh.pending_markout_targets(
        trades, offsets_sec=[None], now=now, close_ts_by_ticker={},  # no close_ts known
    )
    assert targets == []


def test_pending_markout_targets_excludes_already_captured(tmp_path, monkeypatch):
    mh = _mh(tmp_path, monkeypatch)
    now = 1_000_000.0
    mh.record_markout("t1", "TICK-A", "yes", 0.6, now - 400, "300", 300.0, now - 100, 0.62, now)
    already = mh.already_captured_offset_labels(["t1"])
    assert already == {"t1": {"300"}}
    trades = [{"id": "t1", "ticker": "TICK-A", "side": "yes", "price": 0.6, "timestamp": now - 400}]
    targets = mh.pending_markout_targets(
        trades, offsets_sec=[300, 3600], now=now + 10_000, close_ts_by_ticker={},
        already_captured=already,
    )
    labels = {t["offset_label"] for t in targets}
    assert labels == {"3600"}  # "300" already captured, excluded even though now it's also due


def test_record_markout_is_idempotent(tmp_path, monkeypatch):
    mh = _mh(tmp_path, monkeypatch)
    now = 1_000_000.0
    mh.record_markout("t1", "TICK-A", "yes", 0.6, now - 400, "300", 300.0, now - 100, 0.62, now)
    mh.record_markout("t1", "TICK-A", "yes", 0.6, now - 400, "300", 300.0, now - 100, 0.99, now + 5)
    with mh._connect(mh.DB_PATH) as conn:
        rows = conn.execute("SELECT markout_price FROM markouts WHERE trade_id = ?", ("t1",)).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 0.62  # first write wins, second is silently ignored
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_market_history.py -k markout -q"`
Expected: **fails** — none of these functions exist yet.

- [ ] **Step 2: Add the schema (see block above) to `_init_schema`**

- [ ] **Step 3: Implement `pending_markout_targets`, `already_captured_offset_labels`, `record_markout`**

```python
def already_captured_offset_labels(trade_ids: list[str]) -> dict[str, set[str]]:
    if not trade_ids:
        return {}
    with _connect(DB_PATH) as conn:
        placeholders = ",".join("?" for _ in trade_ids)
        rows = conn.execute(
            f"SELECT trade_id, offset_label FROM markouts WHERE trade_id IN ({placeholders})",
            trade_ids,
        ).fetchall()
    out: dict[str, set[str]] = {}
    for trade_id, label in rows:
        out.setdefault(trade_id, set()).add(label)
    return out


def pending_markout_targets(
    trades: list[dict], offsets_sec: list[float | None], now: float,
    close_ts_by_ticker: dict[str, float], already_captured: dict[str, set[str]] | None = None,
) -> list[dict]:
    """Which (trade, offset) pairs are due for markout capture right now
    and don't have a row yet. Pure - no DB access - so Task 4's sweep can
    unit-test the "what's due" logic (this function) separately from the
    "go read snapshots and write rows" side effects (the sweep itself)."""
    already_captured = already_captured if already_captured is not None else already_captured_offset_labels(
        [t["id"] for t in trades]
    )
    out = []
    for t in trades:
        captured = already_captured.get(t["id"], set())
        for offset_sec in offsets_sec:
            if offset_sec is None:
                label = "close"
                close_ts = close_ts_by_ticker.get(t["ticker"])
                if close_ts is None:
                    continue  # no known close time for this ticker yet - skip, don't guess
                target_ts = close_ts
            else:
                label = str(int(offset_sec))
                target_ts = t["timestamp"] + offset_sec
            if label in captured or target_ts > now:
                continue
            out.append({
                "trade_id": t["id"], "ticker": t["ticker"], "entry_side": t["side"],
                "entry_price": t["price"], "entry_ts": t["timestamp"],
                "offset_label": label, "offset_sec": offset_sec, "target_ts": target_ts,
            })
    return out


def record_markout(
    trade_id: str, ticker: str, entry_side: str, entry_price: float, entry_ts: float,
    offset_label: str, offset_sec: float | None, target_ts: float,
    markout_price: float | None, captured_at: float,
) -> None:
    with _connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO markouts "
            "(trade_id, ticker, entry_side, entry_price, entry_ts, offset_label, offset_sec, "
            "target_ts, markout_price, captured_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (trade_id, ticker, entry_side, entry_price, entry_ts, offset_label, offset_sec,
             target_ts, markout_price, captured_at),
        )
```

- [ ] **Step 4: Confirm all tests pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_market_history.py -q"`
Expected: all pass, including the five new tests.

**Safety:** New table, new pure functions, no call site wired yet (Task 4
wires it) — additive only, cannot change any existing behavior. The
`markouts` table costs disk space growing over time but nothing reads or
writes it until Task 4 runs.

---

### Task 4: Wire the markout-capture sweep into `main.py`'s tick loop + runtime-cost measurement

**Files:**
- Modify: `services/market_catalog/market_catalog.py` (new
  `close_ts_for_tickers` function), `services/paper_broker.py` (new
  `trades_since` instance method), `main.py` (new
  `_maybe_capture_markouts`, wired next to `_maybe_prune_capture_stores`)
- Test: `tests/test_market_catalog.py`, `tests/test_paper_broker.py`,
  a new focused test in `tests/test_main_maintenance.py` if that file
  exists (`ls tests/ | grep -i main`; if no such file exists, add the
  sweep-wiring test to whichever existing test file already covers
  `_maybe_prune_capture_stores`-shaped functions — confirm with
  `grep -rln "_maybe_prune_capture_stores\|_maybe_check_signal_resolutions" tests/`
  before picking a file, don't create a new one if an existing one
  already covers this class of function)

**Interfaces:**
- `market_catalog.close_ts_for_tickers(tickers: list[str]) -> dict[str, float]`
  — synchronous public sibling of `services/diagnostics/diagnostics.py`'s
  private async `_close_ts_for_tickers`, same query shape (`SELECT ticker,
  close_ts FROM markets WHERE ticker IN (...) AND close_ts IS NOT NULL`),
  reused by the sweep and available for future callers without needing
  the diagnostics module's `_aio_db` async connection pool.
- `PaperBroker.trades_since(after: float | None) -> list[Trade]` — reuses
  the existing `_trade_range_where(before, after)` staticmethod
  (`services/paper_broker.py:794`) with `before=None`, same idiom as
  `count_trade_range`/`clear_trade_range` (lines 768, 778) but returning
  full rows instead of a count/delete.
- `main._maybe_capture_markouts(cfg: dict, now: float) -> None` — module-
  global `_last_markout_capture_at` guard, same shape as
  `_last_capture_prune_at`/`_maybe_prune_capture_stores`, called from the
  same place in the tick loop (near line 354), on its own interval
  (`edge_gate_recompute_interval_sec`'s sibling constant, but markout
  capture runs far more often than the hourly Δ-recompute — a fixed 300s
  matches the finest configured offset, `edge_gate_markout_offsets_sec`'s
  own `300` entry, so a `t+5m` markout is never captured more than 5
  minutes late; not gated behind `edge_gate_enabled`, per the design's own
  §5/§8 exception).

- [ ] **Step 1: Write the failing tests first, at each new function's own layer**

`tests/test_market_catalog.py` (check its fixture style with `grep -n
'^def _' tests/test_market_catalog.py` first):

```python
def test_close_ts_for_tickers_returns_persisted_close_times(tmp_path, monkeypatch):
    """Same query shape as services/diagnostics/diagnostics.py's private
    async _close_ts_for_tickers - see its own docstring: 'the one store
    that persists a close time per market beyond the rotating watchlist.'
    This is the sync, public sibling the markout sweep needs (Task 4 of
    docs/superpowers/plans/2026-09-03-strategy-edge-gate-implementation.md) -
    it did not exist before this task (confirmed: grep -n '^def '
    services/market_catalog/market_catalog.py before writing this plan)."""
    from services.market_catalog import market_catalog as mc

    monkeypatch.setattr(mc, "DB_PATH", tmp_path / "market_catalog.db")
    mc.upsert_markets("KXTEST", "Test", [
        {"ticker": "TICK-A", "close_time": "2026-09-10T00:00:00Z"},
        {"ticker": "TICK-B", "close_time": None},
    ])
    result = mc.close_ts_for_tickers(["TICK-A", "TICK-B", "NOT-CACHED"])
    assert "TICK-A" in result and isinstance(result["TICK-A"], float)
    assert "TICK-B" not in result  # close_ts IS NOT NULL filter
    assert "NOT-CACHED" not in result
```

(Confirm `upsert_markets`'s real signature/field names with `sed -n
'250,280p' services/market_catalog/market_catalog.py` before trusting this
call shape — it takes a `series_ticker`/`category`/`markets` triple per
the earlier grep of its signature; adjust the test's call to match exactly
what Step 2's read confirms, not this plan's guess at the field name
Kalshi's raw payload uses for close time inside `markets[i]`.)

`tests/test_paper_broker.py`:

```python
def test_trades_since_returns_trades_after_the_given_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(pb_module, "DB_PATH", tmp_path / "paper_broker.db")
    broker = PaperBroker(starting_bankroll=1000.0, db_path=tmp_path / "paper_broker.db")
    broker.open_position("TICK-A", "yes", 10, 0.5, "test")
    trades = broker.trades_since(after=None)
    assert len(trades) == 1
    assert trades[0].ticker == "TICK-A"
    assert broker.trades_since(after=time.time() + 100) == []  # nothing after the future


def test_trades_since_excludes_close_rows(tmp_path, monkeypatch):
    """Adversarial review Finding F4: open_position and close_position
    write into the exact same trades table with no type/action
    discriminator column - trades_since() must filter out close rows via
    the already-established reason.startswith("closed:") convention
    (services/history/trade_analytics.py's build_trade_history,
    this module's own correct_erroneous_close), or a close row's own
    exit price/timestamp would be fed into the markout-capture sweep as
    if it were a fresh entry - pure noise in the exact population the
    design's §6 decisive comparison depends on."""
    monkeypatch.setattr(pb_module, "DB_PATH", tmp_path / "paper_broker.db")
    broker = PaperBroker(starting_bankroll=1000.0, db_path=tmp_path / "paper_broker.db")
    broker.open_position("TICK-A", "yes", 10, 0.5, "test")
    broker.close_position("TICK-A", 0.55, "test-close")
    trades = broker.trades_since(after=None)
    assert len(trades) == 1  # the close row is excluded, only the entry remains
    assert not trades[0].reason.startswith("closed:")
```

(Match this file's actual existing import aliases — `grep -n '^from
services import paper_broker\|^import time' tests/test_paper_broker.py`
first; confirm `close_position`'s exact call signature with `grep -n
"def close_position" services/paper_broker.py` before trusting the call
shape above.)

The main.py wiring test (file TBD per the `grep -rln` check in Files
above):

```python
def test_maybe_capture_markouts_writes_a_row_for_a_due_trade(monkeypatch):
    """Pins the sweep's own wiring - not a re-test of pending_markout_targets/
    record_markout's own logic (Task 3 already covers that), just that
    main.py's sweep actually calls them with real broker/market_catalog/
    market_history data, on its own interval, unconditionally (not gated
    on edge_gate_enabled - design §5/§8's stated exception)."""
    import main
    from services import market_history as mh, market_catalog as mc

    now = time.time()
    fake_trade = SimpleNamespace(id="t1", ticker="TICK-A", side="yes", price=0.6, timestamp=now - 400)
    monkeypatch.setattr(main.broker, "trades_since", lambda after: [fake_trade])
    monkeypatch.setattr(mc, "close_ts_for_tickers", lambda tickers: {})
    monkeypatch.setattr(mh, "recent_price", lambda ticker, max_age_sec, as_of=None: 0.63)
    recorded = []
    monkeypatch.setattr(mh, "record_markout", lambda *a, **kw: recorded.append(a))

    cfg = {"strategy": {"edge_gate_markout_offsets_sec": [300, 3600, None]}}
    main._last_markout_capture_at = 0.0
    main._maybe_capture_markouts(cfg, now)

    assert recorded  # at least the due 300s offset was captured
```

(`main.broker` is the correct reference — `broker` is a standalone
module-level name imported via `from services.app_state import ...
broker ...` (`services/app_state.py:91`), never a key inside the
separate `state` dict; `main.py` itself uses the bare `broker` name
throughout, e.g. `_enriched_broker_state(broker, state["latest_prices"])`
treats them as two distinct arguments. An earlier version of this task
wrote `state["broker"]`, which does not exist anywhere in this codebase
and would raise `KeyError` — corrected after independent adversarial
review, Finding F3. Match `tests/test_pipeline_health_cost.py`'s `import
main` + monkeypatch style for the rest of this test's shape.)

Run all four: expected **fail** (functions/attributes don't exist yet).

- [ ] **Step 2: Read `upsert_markets`'s actual current signature and the raw `markets[i]` close-time field name before implementing `close_ts_for_tickers`**

Run: `sed -n '250,280p' services/market_catalog/market_catalog.py` and
`grep -n '_parse_ts\|close_time' services/market_catalog/market_catalog.py`.
Confirm the exact upsert call shape and that `close_time` (Kalshi's raw
field, ISO-8601 string, per `_parse_ts`'s own handling at line 112) is
what feeds the `close_ts` column, matching what Step 1's test already
assumes — correct the test in place if this read finds a different shape
than assumed above, per this plan's own "verify before trusting a
citation" standard.

- [ ] **Step 3: Implement `close_ts_for_tickers`**

```python
def close_ts_for_tickers(tickers: list[str]) -> dict[str, float]:
    """Sync, public sibling of services/diagnostics/diagnostics.py's
    private async _close_ts_for_tickers - same query, same docstring
    reasoning ('the one store that persists a close time per market
    beyond the rotating watchlist'), added for services/market_history.py's
    markout-capture sweep (Task 4, docs/superpowers/plans/2026-09-03-
    strategy-edge-gate-implementation.md), which runs synchronously from
    main.py's tick-loop maintenance path, not from an async route."""
    if not tickers:
        return {}
    unique = list({t for t in tickers if t})
    with _connect(DB_PATH) as conn:
        placeholders = ",".join("?" for _ in unique)
        rows = conn.execute(
            f"SELECT ticker, close_ts FROM markets WHERE ticker IN ({placeholders}) "
            "AND close_ts IS NOT NULL",
            unique,
        ).fetchall()
    return {t: ts for t, ts in rows}
```

- [ ] **Step 4: Implement `PaperBroker.trades_since`**

Add near `count_trade_range`/`clear_trade_range` (`services/paper_broker.py:768`):

```python
    def trades_since(self, after: float | None) -> list[Trade]:
        """Every real ENTRY (a PaperBroker.open_position row) with
        timestamp > after - the markout-capture sweep's own read of 'what
        entries exist to capture markouts for' (Task 4,
        strategy-edge-gate-implementation.md). open_position and
        close_position write into this exact same trades table with no
        type/action discriminator column, so close rows are filtered out
        here via the reason column's own established convention -
        close_position always prefixes reason with "closed: "
        (services/history/trade_analytics.py's build_trade_history and
        this module's own correct_erroneous_close both already depend on
        the identical convention). Without this filter a close row's own
        exit price/timestamp would be fed into the markout sweep as a
        phantom entry (adversarial review Finding F4). Unlike
        count_trade_range/clear_trade_range, this returns full rows, not
        just a count."""
        where, params = self._trade_range_where(before=None, after=after)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, ticker, side, size, price, reason, timestamp, config_fingerprint, "
                f"fee, signal_seen_at FROM trades {where} ORDER BY timestamp ASC", params,
            ).fetchall()
        return [Trade(*row) for row in rows if not row[5].startswith("closed:")]
```

(Confirm `Trade`'s exact field order matches this SELECT — `grep -n
"^class Trade\|^Trade = " services/paper_broker.py` before trusting this
column order, and confirm `reason` really is index 5 in that order — 
adjust the filter's index to match exactly, don't assume the INSERT
statement's column order from Task-writing time is still current.)

- [ ] **Step 5: Implement and wire `_maybe_capture_markouts` in `main.py`**

```python
_last_markout_capture_at = 0.0
_MARKOUT_CAPTURE_INTERVAL_SEC = 300  # matches edge_gate_markout_offsets_sec's
# finest configured offset (300s/5min) - see strategy-edge-gate-
# implementation.md Task 4. Runs unconditionally (design §5/§8's stated
# exception to the opt-in pattern - markout data has to exist before
# there's anything to decide whether to turn edge_gate_enabled on with).


def _maybe_capture_markouts(cfg: dict, now: float) -> None:
    global _last_markout_capture_at
    if now - _last_markout_capture_at < _MARKOUT_CAPTURE_INTERVAL_SEC:
        return
    _last_markout_capture_at = now
    try:
        offsets = (cfg.get("strategy") or {}).get(
            "edge_gate_markout_offsets_sec", [300, 3600, None]
        )
        trades = [
            {"id": t.id, "ticker": t.ticker, "side": t.side, "price": t.price, "timestamp": t.timestamp}
            for t in broker.trades_since(after=now - 40 * 86400)  # 40d: covers close_window_sec's 32d default with margin
        ]
        if not trades:
            return
        close_ts_by_ticker = market_catalog.close_ts_for_tickers([t["ticker"] for t in trades])
        targets = market_history.pending_markout_targets(trades, offsets, now, close_ts_by_ticker)
        for target in targets:
            price = market_history.recent_price(
                target["ticker"], max_age_sec=_MARKOUT_CAPTURE_INTERVAL_SEC * 2, as_of=target["target_ts"],
            )
            market_history.record_markout(
                target["trade_id"], target["ticker"], target["entry_side"], target["entry_price"],
                target["entry_ts"], target["offset_label"], target["offset_sec"], target["target_ts"],
                price, now,
            )
    except Exception as exc:
        fault_log.record("market_history", "capture_markouts", exc)
```

Wire the call next to `_maybe_prune_capture_stores(cfg, now)`
(`main.py:354`): add `_maybe_capture_markouts(cfg, now)` immediately after
it. Confirm `market_catalog`/`fault_log` are already imported at module
scope in `main.py` (`grep -n '^from services import\|^import' main.py |
grep -i "market_catalog\|fault_log"`) before adding a new import — reuse
the existing one if present, add one matching the file's existing import
style if not.

- [ ] **Step 6: Confirm tests pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_market_catalog.py tests/test_paper_broker.py -q"`
and the main.py wiring test file identified in Step 1.
Expected: all pass, including the new ones.

- [ ] **Step 7: Runtime-cost measurement, per the data-plane HARD RULE**

This sweep is new, unconditional cost on the tick-loop maintenance path
(not the exchange-wide hot path itself, but still measured, not assumed
cheap). **Measured directly, not inferred from whole-tick before/after
noise** (corrected after independent adversarial review, Finding F6: a
whole-tick `last_tick_duration_sec` comparison is a weak signal here
since the sweep only fires once per `_MARKOUT_CAPTURE_INTERVAL_SEC` —
most individual ticks won't include its cost at all, and ordinary
tick-to-tick variance can exceed the ~50ms guideline on its own,
independent of the sweep) — bracket `_maybe_capture_markouts`'s own body
directly with `time.perf_counter()`, the same technique Task 8 Step 6
already uses for its own new DB reads:

```python
def _maybe_capture_markouts(cfg: dict, now: float) -> None:
    global _last_markout_capture_at
    if now - _last_markout_capture_at < _MARKOUT_CAPTURE_INTERVAL_SEC:
        return
    _last_markout_capture_at = now
    _t0 = time.perf_counter()
    try:
        ...  # Step 5's body, unchanged
    except Exception as exc:
        fault_log.record("market_history", "capture_markouts", exc)
    finally:
        _elapsed_ms = (time.perf_counter() - _t0) * 1000
        if _elapsed_ms > 50:
            fault_log.record(
                "market_history", "capture_markouts_slow",
                f"{_elapsed_ms:.1f}ms", severity="warn",
            )
```

(This wraps Step 5's existing `try/except` body — merge into that
implementation rather than duplicating it; the `finally` block is new.)
Deploy to the running dev app (`https://kalshi-whale-poc.ddev.site:8443`),
let it run for at least one full `_MARKOUT_CAPTURE_INTERVAL_SEC` cycle
with real open positions present, then check `fault_log` for any
`capture_markouts_slow` rows and record the actual measured delta in this
task's own commit message. Anything under ~50ms is consistent with "a few
extra SQLite reads/writes on a background maintenance path," not a new
bottleneck (a rough guideline, not a hard gate this plan invents); if
it's materially larger, that's a real finding for
`docs/open-decisions.md`, not something this task silently ships past.

**Safety:** Reads only `paper_broker.db` (trades) and `market_catalog.db`
(close times) and writes only the new `markouts` table in
`market_history.db` — never positions, bankroll, or any trading-decision
state. `fault_log`-wrapped, non-raising, matching every other `_maybe_*`
sweep in `main.py`. Runs unconditionally per the design's own stated
exception, but touches no trading/risk/sizing/strategy code.

---

### Task 5: `signal_log.resolved_signals_for_edge_calibration(since_ts=None)`

**Files:**
- Modify: `services/signal_log.py` (new function, after
  `resolved_signals_with_factors`)
- Test: `tests/test_signal_log.py` (extend existing file)

**Interfaces:** `resolved_signals_for_edge_calibration(since_ts: float | None = None) -> list[dict]`
— per the design's §2.1, extends (does not replace)
`resolved_signals_with_factors`'s query shape: `SELECT ticker, side, price,
seen_at, correct, factors_json, series FROM signals WHERE resolved = 1 AND
excluded = 0 ORDER BY seen_at ASC` (deliberately **no** `factors_json IS
NOT NULL` filter — unlike its sibling, this function's job is Δ_calibrated
over the *whole* settled-signal population, not just the subset with a
real per-factor confidence breakdown, per the design's §2.1 "carries...
the completeness the data-plane rule asks for"), with `since_ts` bounding
it exactly like its sibling. Always called with a bounded `since_ts` from
this plan's own call site (Task 6/7), per the design's own note that an
unscoped scan measured ~1s at 103k+ rows.

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_signal_log.py` (check `_log(tmp_path, monkeypatch)`'s
existing shape first):

```python
def test_resolved_signals_for_edge_calibration_includes_rows_without_factors(tmp_path, monkeypatch):
    """Deliberately broader than resolved_signals_with_factors - this
    function's job is the FULL resolved population (design §2.1's
    completeness argument), not just the factors_json-populated subset."""
    sl = _log(tmp_path, monkeypatch)
    sl.log_signal(WhaleSignal(id="s1", ticker="TICK-A", side="yes", size=10, price=0.6,
                               confidence=0.7, timestamp=1000.0))
    sl.mark_resolved("s1", correct=True)
    rows = sl.resolved_signals_for_edge_calibration()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "TICK-A"
    assert rows[0]["price"] == 0.6
    assert rows[0]["correct"] == 1


def test_resolved_signals_for_edge_calibration_respects_since_ts(tmp_path, monkeypatch):
    sl = _log(tmp_path, monkeypatch)
    sl.log_signal(WhaleSignal(id="s1", ticker="TICK-A", side="yes", size=10, price=0.6,
                               confidence=0.7, timestamp=1000.0))
    sl.mark_resolved("s1", correct=True)
    assert sl.resolved_signals_for_edge_calibration(since_ts=2000.0) == []
    assert len(sl.resolved_signals_for_edge_calibration(since_ts=500.0)) == 1
```

(Confirm `log_signal`'s exact real signature and `mark_resolved`'s exact
real signature with `grep -n 'def log_signal\|def mark_resolved'
services/signal_log.py` before trusting this call shape — adjust to match
exactly.)

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_signal_log.py -k edge_calibration -q"`
Expected: **fails** — function doesn't exist yet.

- [ ] **Step 2: Read `resolved_signals_with_factors` in full immediately before editing**

Run: `sed -n '662,700p' services/signal_log.py` (its exact current body,
including the 2026-08-31 `series` catch-up comment). Confirm it still
matches what's quoted in this plan's own "What changed" section above.

- [ ] **Step 3: Implement**

```python
def resolved_signals_for_edge_calibration(since_ts: float | None = None) -> list[dict]:
    """Δ_calibrated's entire input population (services/whale_calibration/
    confidence_calibration.py's _bucket_delta_by_category_price_band) -
    every resolved, non-excluded signal, NOT filtered to factors_json IS
    NOT NULL like resolved_signals_with_factors above, because
    Δ_calibrated only needs price/seen_at/correct/series, not a per-factor
    breakdown - restricting to the factors-populated subset would silently
    under-cover the data-plane HARD RULE's completeness requirement for no
    reason this function's own job needs. Same since_ts-bounding contract
    as its sibling - always call with a bounded since_ts in production
    (resolved_signals_with_factors' own docstring measured ~1s/103k+ rows
    unscoped); this function keeps the unscoped default for parity, not
    because an unscoped call here is cheap."""
    query = (
        "SELECT ticker, side, price, seen_at, correct, factors_json, series "
        "FROM signals WHERE resolved = 1 AND excluded = 0"
    )
    params: tuple = ()
    if since_ts is not None:
        query += " AND seen_at >= ?"
        params = (since_ts,)
    query += " ORDER BY seen_at ASC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {"ticker": t, "side": s, "price": p, "seen_at": ts, "correct": c, "factors_json": fj, "series": sr}
        for t, s, p, ts, c, fj, sr in rows
    ]
```

- [ ] **Step 4: Confirm tests pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_signal_log.py -q"`
Expected: all pass, including the two new ones.

**Safety:** Read-only new query function; no writer, no call site wired
yet (Task 6/7 wires it) — cannot affect any existing behavior.

---

### Task 6: `Δ_calibrated` — `_bucket_delta_by_category_price_band` in `confidence_calibration.py`

**Files:**
- Modify: `services/whale_calibration/confidence_calibration.py` (new
  function, new import of `trade_category` and `market_history`)
- Test: `tests/test_confidence_calibration.py` (extend existing file)

**Interfaces:** `_bucket_delta_by_category_price_band(rows: list[dict], min_bucket_n: int) -> dict[tuple[str, str], float]`
— given rows already carrying `q_pre` (computed by the caller, Task 7,
via `market_history.recent_price` + `kalshi_fees.unit_cost`, since
per-row `P_pre` reconstruction needs `market_history` and a historical
`as_of`, which this statistics-only module shouldn't need to know how to
do itself) and `category` (attached by the caller via
`trade_category.categories_for_tickers`), returns
`(category, price_band) -> Δ_calibrated`, using **fixed** unit-cost bands
(reusing this same module's existing `_CONFIDENCE_BANDS` boundaries,
applied to `q_pre` instead of `confidence` — same 0.0-0.5/0.5-0.6/.../
0.9-1.01 six-band scheme, for consistency and because both quantities are
0-1 probabilities where the existing bands are already a reasonable,
already-shipped partition), not rank-based tertiles like
`_bucket_win_rates` — **a deliberate, stated difference from
`_bucket_win_rates`'s mechanism** (see "What changed" above for why: this
dimension is a natural fixed grid, not a single continuous factor needing
rank-robustness against near-constant values). Reuses `_bucket_win_rates`'s
*guard philosophy*: a `(category, price_band)` cell with fewer than
`min_bucket_n` resolved signals is simply absent from the returned dict
(the caller's lookup then falls back to `Δ_calibrated = 0`, per design
§2.4 — never a fabricated small-sample estimate).

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_confidence_calibration.py` (check its `_row(...)`
helper's shape first — this task's rows have a different shape, so write
a small local helper rather than forcing `_row` to fit):

```python
def _edge_row(category, q_pre, correct):
    return {"category": category, "q_pre": q_pre, "correct": 1 if correct else 0}


def test_bucket_delta_computes_mean_y_minus_q_pre_per_cell():
    from services.whale_calibration import confidence_calibration as cc

    rows = (
        [_edge_row("Crypto", 0.7, True)] * 60   # y=1, q_pre=0.7 -> delta +0.3 each
        + [_edge_row("Crypto", 0.7, False)] * 40  # y=0, q_pre=0.7 -> delta -0.7 each
        # mean = (60*0.3 + 40*-0.7) / 100 = (18 - 28) / 100 = -0.10
    )
    deltas = cc._bucket_delta_by_category_price_band(rows, min_bucket_n=50)
    assert ("Crypto", "70-80%") in deltas
    assert deltas[("Crypto", "70-80%")] == pytest.approx(-0.10, abs=1e-9)


def test_bucket_delta_omits_cells_under_min_bucket_n():
    from services.whale_calibration import confidence_calibration as cc

    rows = [_edge_row("Sports", 0.55, True)] * 10  # under min_bucket_n=50
    deltas = cc._bucket_delta_by_category_price_band(rows, min_bucket_n=50)
    assert ("Sports", "50-60%") not in deltas
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_confidence_calibration.py -k bucket_delta -q"`
Expected: **fails** — function doesn't exist yet.

- [ ] **Step 2: Read `_CONFIDENCE_BANDS`/`_MIN_BAND_SIZE`/`_bucket_win_rates` in full before writing the new function**

Run: `sed -n '230,254p' services/whale_calibration/confidence_calibration.py`
to confirm `_CONFIDENCE_BANDS`'s exact current band boundaries/labels
before reusing them (this plan quotes them from an earlier read; confirm
unchanged).

- [ ] **Step 3: Implement**

```python
def _price_band_label(q_pre: float) -> str:
    """Same fixed bands as _CONFIDENCE_BANDS above, applied to q_pre
    instead of composite_confidence - both are 0-1 probabilities, and
    reusing an already-shipped, already-labeled partition avoids a second,
    independently-tuned banding scheme for what is structurally the same
    kind of quantity. Falls back to the last band for q_pre == 1.0 exactly,
    same 1.01-upper-bound trick _CONFIDENCE_BANDS already uses."""
    for lo, hi, label in _CONFIDENCE_BANDS:
        if lo <= q_pre < hi:
            return label
    return _CONFIDENCE_BANDS[-1][2]


def _bucket_delta_by_category_price_band(
    rows: list[dict], min_bucket_n: int,
) -> dict[tuple[str, str], float]:
    """Δ_calibrated(category, price_band) = mean(y - q_pre) over resolved
    signals in that cell, per design §2.2/§2.3 Alternative A. Each row
    needs "category" (trade_category.categories_for_tickers, attached by
    the caller - Task 7) and "q_pre" (kalshi_fees.unit_cost(side,
    P_pre_at_seen_at), attached by the caller via market_history.recent_price -
    this module deliberately stays statistics-only, not a second place
    that knows how to reconstruct P_pre) and "correct" (0/1, already on
    every row from signal_log).

    Deliberately fixed unit-cost bands, not _bucket_win_rates' rank-based
    tertiles - see this plan's own "What changed" section for why: this
    dimension (category x price-band) is a natural, externally fixed grid,
    not a single continuous factor that needs protecting against a
    near-constant value the way _bucket_win_rates' rank-tertile mechanism
    does. Reuses that function's GUARD philosophy, not its algorithm: a
    cell with fewer than min_bucket_n resolved signals is simply absent
    from the result (never a fabricated small-sample estimate) - the
    caller's own lookup (Task 7) falls back to 0.0 (design §2.4's neutral
    'no measurable edge yet' default) for any missing cell."""
    cells: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        if r.get("category") is None or r.get("q_pre") is None:
            continue
        key = (r["category"], _price_band_label(r["q_pre"]))
        cells.setdefault(key, []).append(r["correct"] - r["q_pre"])
    return {
        key: sum(deltas) / len(deltas)
        for key, deltas in cells.items()
        if len(deltas) >= min_bucket_n
    }
```

- [ ] **Step 4: Confirm tests pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_confidence_calibration.py -q"`
Expected: all pass, including the two new ones.

**Safety:** New, pure, private statistics function — no DB access, no
call site wired yet (Task 7 wires it), cannot affect any existing
behavior. `stats_power.min_n_for_margin` (design §2.4) is applied by
Task 7's caller when deriving `min_bucket_n` from
`edge_gate_min_bucket_n`/`min_n_for_margin`, not duplicated here — this
function takes the already-resolved `min_bucket_n` as a plain int.

---

### Task 7: Hourly recompute-and-cache + `delta_calibrated_for(...)` lookup

**Files:**
- Modify: `services/whale_calibration/confidence_calibration.py` (new
  module-level cache + `delta_calibrated_for`, new `recompute_deltas`
  orchestration function), `main.py` (wire `_maybe_recompute_edge_gate_deltas`
  next to `_maybe_prune_capture_stores`)
- Test: `tests/test_confidence_calibration.py`, the same main.py
  maintenance-test file Task 4 used

**Interfaces:**
- `recompute_deltas(cfg: dict, now: float) -> dict[tuple[str, str], float]`
  — assembles Task 5's rows (bounded `since_ts`, e.g. `now -
  30*86400` — 30 days, an explicit, stated starting estimate for how much
  history is "recent enough" to calibrate against, not derived from real
  data yet since none exists before Task 8 ships; flagged as a
  placeholder the same way the design flags `edge_gate_min_edge`),
  attaches `category` (`trade_category.categories_for_tickers`) and
  `q_pre` (`market_history.recent_price` at each row's own `seen_at`,
  offset by `edge_gate_pre_print_offset_sec`, matching Task 8's own
  `P_pre` mechanism exactly — same function, same offset convention, no
  second implementation) to each row, then calls Task 6's
  `_bucket_delta_by_category_price_band` with `min_bucket_n =
  stats_power.min_n_for_margin(...)` fed from `edge_gate_min_bucket_n`'s
  configured margin. Stores the result in a module-level dict.
- `delta_calibrated_for(category: str | None, q_pre: float) -> float`
  — the gate's own lookup (Task 8): `0.0` if the module cache is empty,
  `category` is `None`, or the `(category, band)` cell isn't in the
  cache (design §2.4's stated neutral fallback) — otherwise the cached
  value.
- `main._maybe_recompute_edge_gate_deltas(cfg, now)` — same
  `_last_*_at` module-global-guard idiom, interval from
  `strategy.edge_gate_recompute_interval_sec` (default 3600s, per design
  §5), **not** gated behind `edge_gate_enabled` either — recomputing an
  unused cache is cheap and harmless, and this keeps `delta_calibrated_for`
  populated from the moment `edge_gate_enabled` is eventually flipped on,
  rather than needing a cold-start warm-up delay the first time someone
  turns the gate on.

- [ ] **Step 1: Write the failing tests first**

```python
def test_delta_calibrated_for_returns_zero_when_cache_is_empty():
    from services.whale_calibration import confidence_calibration as cc
    cc._delta_cache.clear()
    assert cc.delta_calibrated_for("Crypto", 0.7) == 0.0


def test_delta_calibrated_for_returns_cached_value_for_matching_cell():
    from services.whale_calibration import confidence_calibration as cc
    cc._delta_cache.clear()
    cc._delta_cache[("Crypto", "70-80%")] = -0.05
    assert cc.delta_calibrated_for("Crypto", 0.72) == -0.05
    assert cc.delta_calibrated_for("Crypto", 0.30) == 0.0  # different band, not cached
    assert cc.delta_calibrated_for(None, 0.72) == 0.0  # no category, fail-safe neutral
```

(`recompute_deltas`'s own integration test belongs at Task 8's level,
where a real `signal_log`/`market_history` fixture already exists for
the gate's own tests — avoid duplicating that fixture setup here; this
task's own tests are scoped to the cache/lookup contract, which is
independently testable without a live DB.)

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_confidence_calibration.py -k delta_calibrated_for -q"`
Expected: **fails** — `_delta_cache`/`delta_calibrated_for` don't exist yet.

- [ ] **Step 2: Implement the cache + lookup + recompute orchestration**

```python
_delta_cache: dict[tuple[str, str], float] = {}


def delta_calibrated_for(category: str | None, q_pre: float) -> float:
    """The gate's own lookup (services/strategy_engine.py's edge-gate
    check, Task 8 of docs/superpowers/plans/2026-09-03-strategy-edge-gate-
    implementation.md). 0.0 (design §2.4's stated neutral 'no measurable
    edge yet' default) whenever category is unknown or this exact
    (category, price_band) cell has never accumulated enough resolved
    signal history - never a fabricated estimate."""
    if category is None:
        return 0.0
    return _delta_cache.get((category, _price_band_label(q_pre)), 0.0)


def recompute_deltas(cfg: dict, now: float) -> dict:
    """Rebuilds _delta_cache from signal_log's resolved population -
    called on strategy.edge_gate_recompute_interval_sec's cadence by
    main.py's _maybe_recompute_edge_gate_deltas, unconditionally (cheap;
    keeps the cache warm even before edge_gate_enabled is ever flipped
    true)."""
    from services import market_history, signal_log, stats_power, trade_category
    from services.config.config_bounds import MIN_TRADEABLE_UNIT_COST  # noqa: F401 (parity import, see Task 8 note)

    strat_cfg = cfg.get("strategy") or {}
    offset_sec = strat_cfg.get("edge_gate_pre_print_offset_sec", 10.0)
    p_pre_max_age_sec = strat_cfg.get("edge_gate_p_pre_max_age_sec", 600.0)
    since_ts = now - 30 * 86400  # 30d - explicit starting estimate, see this task's own Interfaces note
    rows = signal_log.resolved_signals_for_edge_calibration(since_ts=since_ts)
    if not rows:
        return {}
    categories = trade_category.categories_for_tickers([r["ticker"] for r in rows])
    edge_rows = []
    for r in rows:
        p_pre = market_history.recent_price(
            r["ticker"], max_age_sec=p_pre_max_age_sec, as_of=r["seen_at"] - offset_sec,
        )
        if p_pre is None:
            continue  # can't reconstruct q_pre for this historical row - excluded, not guessed
        q_pre = kalshi_fees.unit_cost(r["side"], p_pre)
        if q_pre is None:
            continue
        edge_rows.append({"category": categories.get(r["ticker"]), "q_pre": q_pre, "correct": r["correct"]})
    min_bucket_n = strat_cfg.get("edge_gate_min_bucket_n", 50)
    global _delta_cache
    _delta_cache = _bucket_delta_by_category_price_band(edge_rows, min_bucket_n)
    return _delta_cache
```

Add `from services import kalshi_fees` to this module's imports if not
already present (`grep -n '^from services import\|^import' services/whale_calibration/confidence_calibration.py`
first — reuse the existing import block's style).

**Note on `stats_power.min_n_for_margin` vs. the plain `edge_gate_min_bucket_n`
value:** the design's §2.4 describes deriving the bucket-count floor
*from* `min_n_for_margin(margin_pts, ...)`, with `edge_gate_min_bucket_n`
as the resulting count. This plan treats `edge_gate_min_bucket_n` (§5's
table: "Fed to `stats_power.min_n_for_margin`") as **already** the
resolved integer a human/future tuning pass derives via that function
offline, not something recomputed live on every cache refresh — `50` is
`min_n_for_margin`'s own output for a specific margin choice, and
`recompute_deltas` just reads it as a plain config int. This is a
plan-stage simplification worth stating explicitly: `stats_power.
min_n_for_margin` is still the tool that *justifies* the 50 default, it's
just not re-invoked at runtime for something that doesn't need to be
recomputed every hour. Remove the unused `MIN_TRADEABLE_UNIT_COST` parity
import above if a full implementation pass shows it's genuinely unneeded
(kept as a placeholder here since this task's own scope stops short of a
full runtime margin recomputation; flag in the PR if this note turns out
to be wrong once written for real).

- [ ] **Step 3: Wire `_maybe_recompute_edge_gate_deltas` into `main.py`**

```python
_last_edge_gate_delta_recompute_at = 0.0


def _maybe_recompute_edge_gate_deltas(cfg: dict, now: float) -> None:
    global _last_edge_gate_delta_recompute_at
    interval = (cfg.get("strategy") or {}).get("edge_gate_recompute_interval_sec", 3600)
    if now - _last_edge_gate_delta_recompute_at < interval:
        return
    _last_edge_gate_delta_recompute_at = now
    try:
        confidence_calibration.recompute_deltas(cfg, now)
    except Exception as exc:
        fault_log.record("whale_calibration", "recompute_edge_gate_deltas", exc)
```

Wire next to `_maybe_prune_capture_stores(cfg, now)`/`_maybe_capture_markouts(cfg,
now)` in the tick loop. Confirm `confidence_calibration` is already
imported at module scope in `main.py` before adding a new import.

- [ ] **Step 4: Confirm tests pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_confidence_calibration.py -q"`
Expected: all pass.

**Safety:** New module-level cache + read-only recompute (reads
`signal_log`/`market_history`/`trade_category`, writes nothing) — no
write to any live trading state. Not yet consulted by anything (Task 8
wires the consumer) — this task alone cannot change `evaluate()`'s
behavior.

---

### Task 8: The gate itself, inside `_validate_entry_price`

**Files:**
- Modify: `services/strategy_engine.py` (`_validate_entry_price`'s
  signature and body; both call sites — `evaluate()` line 579,
  `validate_pending_fill` line 705; new imports)
- Test: `tests/test_strategy_engine.py` (extend existing file)
- Docs: `docs/open-decisions.md` (new line, per this plan's Global
  Constraints note on the `P_pre`-fail-open judgment call)

**Interfaces:** `_validate_entry_price` gains three new, defaulted (so
every existing caller/test keeps working unchanged) parameters:

```python
def _validate_entry_price(
    side: str, price: float, confidence: float, effective_threshold: float,
    strat_cfg: dict, is_longshot: bool = False,
    ticker: str | None = None, category: str | None = None, as_of: float | None = None,
) -> EntryValidation:
```

`ticker=None` (the default) means the edge gate step is skipped entirely
(nothing to look up `P_pre`/`fee_type` against) — this is what every
existing call site not yet updated, and every existing test, gets for
free, matching the "changes nothing until deliberately wired" pattern.
Both real call sites (`evaluate()`, `validate_pending_fill`) are updated
in this task to pass `ticker`/`category`/`as_of` — `evaluate()` passes
`signal.ticker`, `category` (already a parameter of `evaluate()` itself),
`as_of=signal.timestamp`; `validate_pending_fill` passes `ticker`
(already a parameter), `category` (already a parameter), `as_of=time.time()`
(the fill is happening now, there's no "signal timestamp" at this call
site — `time` is already imported at module scope, confirmed).

Gate logic, appended after the existing `max_unit_cost` check and before
`return EntryValidation(True)`:

```python
    if strat_cfg.get("edge_gate_enabled") and ticker is not None:
        edge_result = _edge_gate_check(side, price, ticker, category, as_of, strat_cfg)
        if edge_result is not None and not edge_result.ok:
            return edge_result
        # edge_result is None: gate could not be evaluated (missing P_pre,
        # or flat fee_type) - fails open per design §5, falls through to
        # return EntryValidation(True) below like every other pass.

    return EntryValidation(True)
```

New private helper, same module:

```python
def _edge_gate_check(
    side: str, price: float, ticker: str, category: str | None,
    as_of: float | None, strat_cfg: dict,
) -> EntryValidation | None:
    """The edge/EV gate (docs/superpowers/specs/2026-09-03-strategy-edge-
    gate-design.md §3.2), appended inside _validate_entry_price rather
    than called separately from evaluate() so a resting limit order's
    fill-time re-check is held to the same bar (validate_pending_fill's
    call site) - same reasoning as the existing price-band checks this
    function already applies (see this function's own docstring, the
    "four-entry gate bypass" fix).

    Returns None when the gate cannot be evaluated at all (missing P_pre,
    or a flat-type series whose fee this app doesn't model) - the caller
    treats None as fail-open, per design §5's explicit statement that this
    is a stated, not-yet-settled judgment call (docs/open-decisions.md).
    Returns an EntryValidation with ok=False when it CAN be evaluated and
    the computed edge is below strategy.edge_gate_min_edge."""
    as_of = as_of if as_of is not None else time.time()
    series_ticker = signal_log.series_of(ticker)

    fee_type = series_cache.get_fee_type(series_ticker)
    if fee_type == "flat":
        # Fail-closed for THIS gate specifically (design §1.3 point 3) -
        # this app's fee model (kalshi_fees.taker_fee_per_contract) doesn't
        # cover the flat FeeType's own "Specific Trading Fees Table",
        # which docs/kalshi/ doesn't actually contain under that or any
        # recognizable name (design's adversarial review, Finding 2,
        # independently re-derived from the raw PDF bytes). Not evaluated,
        # not admitted through this mechanism - other gates are untouched.
        return None

    pre_print_offset = strat_cfg.get("edge_gate_pre_print_offset_sec", 10.0)
    p_pre_max_age_sec = strat_cfg.get("edge_gate_p_pre_max_age_sec", 600.0)
    p_pre = market_history.recent_price(
        ticker, max_age_sec=p_pre_max_age_sec, as_of=as_of - pre_print_offset,
    )
    if p_pre is None:
        # Fails open (design §5's stated default; docs/open-decisions.md
        # carries the "should this instead fail closed" question forward
        # for revisit once real markout data exists, per this plan's
        # Global Constraints note).
        return None

    q_pre_now = kalshi_fees.unit_cost(side, p_pre)
    if q_pre_now is None:
        return None
    delta = confidence_calibration.delta_calibrated_for(category, q_pre_now)
    p_est_side = min(max(q_pre_now + delta, 1e-6), 1 - 1e-6)  # clamped to (0, 1), design §2.2

    ask_now = kalshi_fees.unit_cost(side, price)
    if ask_now is None:
        return None
    fee_buffer = strat_cfg.get("edge_gate_fee_buffer_usd", 0.005)
    fee = kalshi_fees.taker_fee_per_contract(price, ticker) + fee_buffer
    edge = p_est_side - ask_now - fee

    min_edge = strat_cfg.get("edge_gate_min_edge", 0.04)
    if edge < min_edge:
        return EntryValidation(
            False, "edge_gate", edge, min_edge,
            f"edge {edge:.4f} is below the minimum edge of {min_edge:.4f} "
            f"(p_est={p_est_side:.4f}, ask={ask_now:.4f}, fee={fee:.4f})",
        )
    return EntryValidation(True)
```

`candidate_log.record_rejection` needs **no changes** — both call sites
already forward `validation.gate_name`/`validation.observed`/
`validation.threshold` generically (`services/strategy_engine.py:583-585`,
`709-712`), so an `"edge_gate"` rejection is recorded automatically the
instant `_edge_gate_check` returns one, exactly the design's §3.3 claim —
Task 9 is where this gets a real test rather than staying a trusted claim.

Add `from services.whale_calibration import confidence_calibration` and
`from services import series_cache` to `strategy_engine.py`'s import
block (confirm no circular import: `confidence_calibration.py` imports
`stats_power`/`trade_analytics`/`confidence_scoring` — none of which
import `strategy_engine`, confirmed by `grep -rln
"import strategy_engine\|from services.strategy_engine"
services/whale_calibration/ services/history/ services/confidence_scoring.py`
returning nothing, before adding the import).

- [ ] **Step 1: Write the failing tests first**

Add to `tests/test_strategy_engine.py` (this file's `_strategy` fixture
already monkeypatches `mh_module.DB_PATH` — confirmed above — so these
tests can write real `market_history` snapshot rows):

```python
def test_edge_gate_off_by_default_changes_nothing(tmp_path, monkeypatch):
    """edge_gate_enabled defaults to false (Task 1) - confirms the gate
    truly is a no-op until deliberately turned on, even with a ticker
    passed through."""
    from services import strategy_engine as se

    result = se._validate_entry_price(
        "yes", 0.5, 0.9, 0.5, {}, ticker="TICK-A", category="Crypto", as_of=time.time(),
    )
    assert result.ok


def test_edge_gate_rejects_when_edge_below_min_edge(tmp_path, monkeypatch):
    from services import strategy_engine as se, market_history as mh, series_cache as sc

    monkeypatch.setattr(mh, "DB_PATH", tmp_path / "market_history.db")
    monkeypatch.setattr(sc, "DB_PATH", tmp_path / "series_cache.db")
    now = time.time()
    # P_pre snapshot: yes_price 0.50 at (now - offset - 1s), so q_pre_now ~= 0.50
    with mh._connect(mh.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO snapshots (ticker, series, yes_price, timestamp) VALUES (?,?,?,?)",
            ("TICK-A", "TICK", 0.50, now - 11),
        )
    strat_cfg = {"edge_gate_enabled": True, "edge_gate_min_edge": 0.04,
                 "edge_gate_pre_print_offset_sec": 10.0, "edge_gate_p_pre_max_age_sec": 600.0}
    # No Δ_calibrated cache populated -> delta defaults to 0.0 -> p_est_side ~= q_pre_now ~= 0.50
    # ask_now = price = 0.55 (bought 5c above P_pre) -> edge is clearly negative
    result = se._validate_entry_price(
        "yes", 0.55, 0.9, 0.5, strat_cfg, ticker="TICK-A", category="Crypto", as_of=now,
    )
    assert not result.ok
    assert result.gate_name == "edge_gate"


def test_edge_gate_fails_open_when_p_pre_unavailable(tmp_path, monkeypatch):
    from services import strategy_engine as se, market_history as mh, series_cache as sc

    monkeypatch.setattr(mh, "DB_PATH", tmp_path / "market_history.db")
    monkeypatch.setattr(sc, "DB_PATH", tmp_path / "series_cache.db")
    strat_cfg = {"edge_gate_enabled": True, "edge_gate_min_edge": 0.04}
    # No snapshot rows at all -> P_pre unavailable -> fails open, gate not evaluated
    result = se._validate_entry_price(
        "yes", 0.55, 0.9, 0.5, strat_cfg, ticker="TICK-A", category="Crypto", as_of=time.time(),
    )
    assert result.ok  # falls through to EntryValidation(True)


def test_edge_gate_fails_closed_on_flat_fee_type(tmp_path, monkeypatch):
    from services import strategy_engine as se, market_history as mh, series_cache as sc

    monkeypatch.setattr(mh, "DB_PATH", tmp_path / "market_history.db")
    monkeypatch.setattr(sc, "DB_PATH", tmp_path / "series_cache.db")
    sc.save(time.time(), [{"ticker": "TICK", "fee_type": "flat"}])
    strat_cfg = {"edge_gate_enabled": True, "edge_gate_min_edge": 0.0}
    result = se._validate_entry_price(
        "yes", 0.55, 0.9, 0.5, strat_cfg, ticker="TICK-A", category="Crypto", as_of=time.time(),
    )
    assert result.ok  # not evaluated (flat), falls open the same way as missing P_pre


def test_evaluate_and_validate_pending_fill_pass_ticker_category_as_of_through(tmp_path, monkeypatch):
    """Wiring test, not a re-test of the gate's own logic (covered above) -
    confirms both real call sites actually forward the new parameters,
    not just that _validate_entry_price itself accepts them."""
    from services import strategy_engine as se

    calls = []
    real = se._validate_entry_price

    def _spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(se, "_validate_entry_price", _spy)
    # ... (use this file's existing _strategy/_signal/_cfg fixtures to drive
    # one real evaluate() call and one real validate_pending_fill call;
    # match this file's own established fixture-construction pattern rather
    # than inventing a new one - see _strategy(tmp_path, monkeypatch) above)
    strategy = _strategy(tmp_path, monkeypatch)
    strategy.evaluate(_signal(), _cfg(), category="Crypto")
    assert calls[-1].get("ticker") == "TICK-A" and calls[-1].get("category") == "Crypto"
    calls.clear()
    strategy.validate_pending_fill("TICK-A", "yes", 0.5, 0.9, _cfg(), category="Crypto")
    assert calls[-1].get("ticker") == "TICK-A" and calls[-1].get("category") == "Crypto"
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_strategy_engine.py -k edge_gate -q"`
Expected: **fails** — `_validate_entry_price` doesn't accept the new
kwargs yet, `_edge_gate_check` doesn't exist.

- [ ] **Step 2: Re-read `_validate_entry_price` and both call sites immediately before editing**

Run: `sed -n '141,202p' services/strategy_engine.py` and
`sed -n '575,586p;700,712p' services/strategy_engine.py`. Confirm they
still match what's quoted in this plan's "What changed" section — if not,
re-derive the diff from current source before proceeding. **This check
matters more than usual for this specific task:** PR #482
(`fix/watchlist-entry-gate`, open/unmerged as of this plan's own review
cycle) adds a new watchlist-gate block into `evaluate()` between the
`excluded_series` check and the `_validate_entry_price` call site — if it
merges before this task runs, line 579's exact number shifts. This step's
`sed`-and-confirm already defends against that mechanically; called out
explicitly here (per independent adversarial review) so a stale line
number isn't a surprise if it happens.

- [ ] **Step 3: Apply the signature change, the new helper, and both call-site updates**

(As specified in Interfaces above — signature change, `_edge_gate_check`,
the two call-site kwarg additions, the two new module imports.)

- [ ] **Step 4: Confirm tests pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_strategy_engine.py -q"`
Expected: all pass, including the five new tests, and every pre-existing
`_validate_entry_price`/`evaluate`/`validate_pending_fill` test unchanged
(ticker/category/as_of all default to values that skip the new check
entirely).

- [ ] **Step 5: `dimensional-analysis` pass on the new arithmetic**

Per CLAUDE.md's HARD RULE, run the `dimensional-analysis` skill/plugin
over `_edge_gate_check`'s new arithmetic specifically (`q_pre_now`,
`p_est_side`, `ask_now`, `fee`, `edge`, the comparison against
`min_edge`) before this task is considered done — confirm every quantity
is the side-relative `[$/contract]`/`[prob]` value its variable name
implies, per §1.1's stated identity (price and probability numerically
interchangeable for a Kalshi contract), not re-derive it from scratch.

- [ ] **Step 6: Runtime-cost measurement of `_validate_entry_price`'s new DB reads**

Per the data-plane HARD RULE and design §3.3's own note: with
`edge_gate_enabled: true` in a **local test config only** (never the live
primary `config/settings.yaml` — Global Constraints), measure
`_validate_entry_price`'s wall-clock cost with the gate on vs. off (a
simple `time.perf_counter()` bracket around a call in a throwaway local
script or an existing benchmark-shaped test, matching whatever pattern
`tests/test_pipeline_health_cost.py` already uses for measuring route
cost). Record the delta in this task's commit message. Two new DB reads
(`market_history.recent_price`, `series_cache.get_fee_type`) plus one
in-memory dict lookup (`delta_calibrated_for`) is the expected shape —
confirm it's actually that cheap, don't assume.

- [ ] **Step 7: Add the `docs/open-decisions.md` line**

Per this plan's Global Constraints note and the design's own §5 request,
add one line matching the file's existing format (`- <item> · <next
action> · <who decides> · <since>`):

```
- Edge gate's P_pre-unavailable fail-open default (services/strategy_engine.py's _edge_gate_check, design §5) is the shipped behavior, not yet revisited against real data · revisit once Task 10's live validation / §6's markout data exists · you · 2026-09-03
```

**Safety:** `edge_gate_enabled` stays `false` in the live config through
this whole task (Task 1's default, untouched) — every new code path here
is provably unreachable in production until a separate, later, human
config change. No trading-gate/kill-switch/kelly-sizing code touched.

---

### Task 9: Informativeness — persist `p_est`/`Δ_calibrated`/`edge` per-signal, confirm reporting picks it up

**Files:**
- Modify: `services/strategy_engine.py` (`_edge_gate_check` returns the
  intermediate values, not just pass/fail — via `EntryValidation`'s
  existing `observed`/`threshold` fields, already sufficient for
  `edge`/`min_edge`; `p_est_side`/`delta`/`q_pre_now` need a place to
  land for per-signal inspection)
- Test: `tests/test_candidate_log.py`, `tests/test_strategy_engine.py`

**Interfaces:** Design §9's informativeness criterion: "`p_est`/
`Δ_calibrated`/`edge` are inspectable per-signal... not only a boolean
pass/fail." `EntryValidation.observed`/`.threshold` already carry
`edge`/`min_edge` for the reject case (Task 8), and
`candidate_log.record_rejection`'s existing `observed`/`threshold`
columns already persist those two numbers for every rejected signal —
**this task's real job is confirming that claim with a test**, and
extending coverage to the **admitted** case (`p_est`/`Δ_calibrated`/
`edge` for a signal that passed the gate aren't visible anywhere today,
since `EntryValidation(True)` carries no extra fields). Rather than adding
new columns to `signal_log.signals` (a schema migration for a nice-to-have
inspection view, more invasive than this criterion needs), thread the
computed values through `evaluate()`'s own existing decision-dict return
shape instead — cheaper, and consistent with `_skip`'s own existing
"returns a decision dict describing what happened" contract
(`evaluate()`'s own docstring).

- [ ] **Step 1: Write the failing tests first**

```python
def test_population_gate_summary_includes_edge_gate_rejections(tmp_path, monkeypatch):
    """Confirms design §3.3/§6's claim that candidate_log's existing
    counterfactual-tracking machinery picks up edge_gate rejections with
    zero new plumbing - a real test of that claim, not a repeat of the
    trust the design document already extended it."""
    from services import candidate_log as cl

    monkeypatch.setattr(cl, "DB_PATH", tmp_path / "candidate_log.db")
    cl.record_rejection("TICK-A", "whale_follow", "edge_gate", -0.02, 0.04, side="yes", unit_cost=0.55)
    summary = cl.population_gate_summary(min_samples=1)
    gate_names = {row["gate_name"] for row in summary}
    assert "edge_gate" in gate_names


def test_evaluate_admitted_decision_includes_edge_gate_fields_when_computed(tmp_path, monkeypatch):
    """For an ADMITTED signal (edge_gate passed or wasn't evaluated), the
    decision dict evaluate() returns should still surface p_est/delta/edge
    when the gate was actually computed - not only on rejection. When the
    gate short-circuited (fail-open on missing P_pre, or edge_gate_enabled
    is false), these fields are simply absent/None - never a fabricated
    number for a check that didn't run."""
    # ... uses this file's existing _strategy/_signal/_cfg fixtures, with
    # edge_gate_enabled: true and a real P_pre snapshot present (same
    # setup shape as Task 8's test_edge_gate_rejects_when_edge_below_min_edge,
    # but with a p_est/ask/fee combination that clears edge_gate_min_edge
    # instead of failing it) - assert the returned decision dict has an
    # "edge_gate" sub-dict with p_est/delta/edge/q_pre keys, not just "ok": True.
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_candidate_log.py tests/test_strategy_engine.py -k 'population_gate_summary_includes_edge_gate or admitted_decision_includes_edge_gate' -q"`
Expected: the `candidate_log` test likely **passes immediately** (design's
own claim — confirming, not implementing); the `evaluate()` test **fails**
until Step 2 lands.

- [ ] **Step 2: Thread the computed values through**

Change `_edge_gate_check`'s return type from `EntryValidation | None` to
`tuple[EntryValidation | None, dict | None]` — the second element is
`{"p_est": p_est_side, "q_pre": q_pre_now, "delta": delta, "edge": edge,
"min_edge": min_edge}` whenever the gate was actually computed (i.e.
whenever the function got past the `P_pre`/`flat`-fee-type early-outs),
`None` when it wasn't. Thread this second element up through
`_validate_entry_price` (a new field on `EntryValidation`? — simpler:
`_validate_entry_price` itself keeps returning just `EntryValidation`,
and a **new, separate** small helper `_edge_gate_debug` is not needed;
instead, have `_edge_gate_check`'s caller inside `_validate_entry_price`
stash the debug dict on a module-level "last computed" slot is the wrong
shape (not thread-safe, and this app already has a per-call, not
per-process, semantics elsewhere) — **the correct fix is a fourth field
on `EntryValidation` itself**: add `edge_gate_detail: dict | None = None`
to the `NamedTuple` (backward compatible — `NamedTuple` fields with
defaults don't break any existing `EntryValidation(...)` construction
anywhere in the codebase, confirmed by `grep -rn "EntryValidation(" services/`
before this change — every existing call site uses positional args for
the first 1-5 fields only). `evaluate()`'s and `validate_pending_fill`'s
existing `if not validation.ok: candidate_log.record_rejection(...)`
blocks are unchanged; add, in `evaluate()`'s decision-dict construction
for the admitted path, `"edge_gate": validation.edge_gate_detail` (only
when `_edge_gate_check` actually ran).

- [ ] **Step 3: Confirm tests pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/test_candidate_log.py tests/test_strategy_engine.py -q"`
Expected: all pass.

**Safety:** Purely additive fields on an existing `NamedTuple` and an
existing decision dict — no existing consumer of either reads a field
that changed meaning, only new fields that default to `None`/absent.

---

### Task 10: Full regression suite + required live validation

**Not a code task** — the empirical confirmation every prior task's
claims hold together, and that `edge_gate_enabled: false` genuinely means
zero behavior change end to end, matching every precedent plan's own
final task (e.g. `docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation.md`'s
Task 10).

- [ ] **Step 1: Run the full local test suite**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -m pytest tests/ -q -m 'not slow'"`
Expected: the pre-existing baseline pass count (confirm fresh via `git log
-1 --format=%h` before trusting a number from earlier in this plan's own
authoring) plus this plan's net new tests across Tasks 1-9, all passing.
Per CLAUDE.md's CI-authority note, this local run is for fast iteration
during this task only — the real full-suite confirmation is Woodpecker's
result on the pushed branch (Step 4).

- [ ] **Step 2: `import main` sanity check**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-a0cfb3e1e724c2431 && python -c 'import main' && echo IMPORT_OK"`
Expected: `IMPORT_OK` — no import errors from any new module import
(`series_cache`, `confidence_calibration`, `trade_category`,
`market_catalog` inside `strategy_engine.py`/`main.py`).

- [ ] **Step 3: Confirm `edge_gate_enabled: false` is provably inert on the live dev app**

Deploy to the running app (`https://kalshi-whale-poc.ddev.site:8443`).
Read `GET /api/state` and `GET /api/health/pipeline` before and after this
deploy: `last_tick_duration_sec` should show only the Task 4/Task 7 sweep
overhead already measured in those tasks' own Step 7/Step 3 (small,
background, unconditional) — **not** any change attributable to the gate
itself, since `edge_gate_enabled` stays `false`. Let at least one full
trading cycle run and confirm via `GET /api/health/faults` that no new
fault component (`edge_gate`, `capture_markouts`,
`recompute_edge_gate_deltas`) is firing repeatedly (a one-time fault
during deploy/reload is not itself a failure; a sustained, repeating one
is).

- [ ] **Step 4: Check the `t+close` markout population for the `close_ts`-tier-4-only gap Task 4 flagged**

**Gives Task 4's "honest gap" a real detection step, not only prose**
(corrected after independent adversarial review, Finding F5: the plan's
own self-review named a detection trigger — "flag if Task 10's live
validation shows this materially distorting the `t+close` numbers" — but
no step anywhere actually checked for it, which made the trigger easy to
silently never fire). After Step 3's live cycle has run long enough for
at least a few `t+close` markout rows to exist: query
`market_history.db`'s `markouts` table for `offset_label='close'` rows
and compute each one's `target_ts - entry_ts` in days. If any exceeds a
generous threshold (30 days — `close_window_sec`'s own 32-day default is
the app's own stated upper bound for how far out a market can legitimately
close, so a `t+close` gap materially beyond that is a signal `close_ts`'s
tier-4-only imprecision, not a real long-dated market, is driving the
number), add an explicit line to `docs/open-decisions.md` naming the
affected ticker(s) and the measured gap — per the design's own
`effective_close_time` precedence (design read directly: a real,
live-confirmed example exists where raw `close_time` was 359.5 days out
while the actual event outcome was already 5.5 days in the past). If no
row exceeds the threshold, record that explicitly too (a checked, passing
condition, not silence) — either outcome is a real finding, not a
skippable step.

- [ ] **Step 5: Push and confirm CI**

Push this branch, open the PR, confirm Woodpecker's real result via `gh
api repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status` — per
CLAUDE.md's CI-authority note, this is the full-suite confirmation this
plan relies on, not a duplicate local run.

- [ ] **Step 6: Record the result**

Update `docs/next-action.md` with the outcome (this plan's own tasks
done; the next action being "decide whether/when to flip
`edge_gate_enabled: true`," a separate human decision this plan
deliberately does not make) and confirm the `docs/open-decisions.md` line
Task 8 Step 7 added is still present and accurate.

---

## Plan self-review

**Sequencing matches the design's own dependency logic, verified against
the design doc's text, not re-derived independently:** config surface
first (Task 1, inert), then the markout sweep (Tasks 3-4, the one
always-on piece, ahead of the gate so data has time to accumulate before
Task 10), then `Δ_calibrated` (Tasks 5-7), then the gate itself (Task 8),
then informativeness (Task 9), then validation (Task 10) — this is
exactly the order the task's own instructions specified and the design's
§3.3/§5 argued for (markout capture "the one piece of this design's new
surface that is not gated behind edge_gate_enabled," §5's own words).
Task 2 (the `series_cache.get_fee_type` accessor) sits early because
Task 8 depends on it and it has no dependencies of its own — placed
right after config (both are small, independent prerequisites) rather
than immediately before Task 8, so a reader executing tasks in order
isn't blocked waiting on it at the last moment.

**The open `P_pre`-fail-open question is handled per the task's own
instruction, not silently resolved:** Task 8 implements the design's
stated fail-open default (§5's explicit text) **and** Step 7 adds the
`docs/open-decisions.md` line the design itself asked for, rather than
either (a) silently treating the design's default as final, or (b)
inventing a new blocking-prerequisite gate the design didn't ask for. This
plan does not present fail-open as settled beyond what the design already
settled it to.

**Three concrete implementation gaps the design didn't fully specify were
found and resolved here, each with primary-source evidence, not
guessed:** the missing `series_cache` per-ticker `fee_type` reader (Task
2), `effective_close_time`'s unsuitability for a sweep that runs
independently of the live tick's market list (Task 4's
`market_catalog.close_ts_for_tickers`, grounded in
`diagnostics.py`'s own docstring naming `market_catalog` as "the one store
that persists a close time... beyond the rotating watchlist"), and
`_bucket_delta`'s actual bucketing dimension being a fixed category/
price-band grid rather than a literal `_bucket_win_rates` clone (Task 6).
Each is flagged in the "What changed since the design was authored"
section up top, not buried inside its task, so a reader auditing this
plan against the design doesn't have to hunt for where it diverges from a
literal reading of the design's prose.

**TDD is real, not decorative, in every code task:** Tasks 2-9 each
write a failing test against current/no-op behavior before any
implementation step, matching this repo's `superpowers:test-driven-development`
convention and the tier0 precedent plan's own shape. Task 9's second test
is left partially sketched (a comment describing the fixture reuse rather
than a fully transcribed test body) — flagged here explicitly as the one
place this plan didn't fully write out test code, because it depends on
Task 8's own fixture setup existing first and duplicating that setup
verbatim into this document would drift the moment Task 8's real
implementation differs even slightly from this plan's own sketch; the
task's own Step 1 instruction is specific enough (reuse `_strategy`/
`_signal`/`_cfg`, assert on a named sub-dict's keys) that this isn't a
placeholder in the sense Task 3/4's "confirm before trusting" callouts
warn about — it's a deliberate choice to write the assertion contract
without pre-committing to fixture code that Task 8's real edit might
invalidate.

**Type/interface consistency across tasks:** every new function this plan
adds to an existing module follows that module's own established
signature/docstring conventions (module-level functions with `DB_PATH`
module attribute for market_history/series_cache/market_catalog;
instance methods reusing `_trade_range_where`/`_connect()` for
paper_broker; `strat_cfg.get(field, default)` for every new config read,
never a bare `strat_cfg[field]` that would KeyError on an unresolved
override). `EntryValidation`'s extension (Task 9's `edge_gate_detail`
field) is additive to a `NamedTuple` with defaults, confirmed
backward-compatible by checking every existing construction site uses
positional args only for the pre-existing fields.

**Scope boundary, stated plainly:** this plan ships the edge gate
mechanism fully wired and tested, with `edge_gate_enabled: false` in the
live config throughout — it does **not** flip that switch, does not
implement Phase 2 (`market_analyst_agent`/category fair-value anchors,
design §7, explicitly deferred), does not add config-bounds validation
for the 8 new fields (`services/config/config_bounds.py` isn't touched —
the design's own 8-field table doesn't call for it, and adding it here
would be scope creep beyond what the design/its reviews actually
approved), and does not retune `edge_gate_min_edge`/`edge_gate_fee_buffer_usd`
away from their design-stated external-methodology placeholders (design
§6/§8 already name this as future work once real markout data exists).
Each of these is named here explicitly, not left implicit.

**Honest gap, not fixed here:** Task 4's `t+close` markout offset uses
raw `close_ts` (tier 4 of `effective_close_time`'s own precedence), not
the fuller precedence that function implements — stated as a known
limitation in "What changed" above with a concrete trigger for revisiting
it (Task 10's live validation showing the `t+close` numbers specifically
distorted), not silently smoothed over as equivalent to what the design's
prose implied.
