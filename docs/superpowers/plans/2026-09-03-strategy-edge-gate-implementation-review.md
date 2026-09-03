# Adversarial Review: Strategy Edge Gate Implementation Plan

## Status

Adversarial review (2026-09-03) of
`docs/superpowers/plans/2026-09-03-strategy-edge-gate-implementation.md`
(commit `950ea12`), per CLAUDE.md's "nothing advances on one pass" HARD
RULE. This is a fresh pass with no memory of the plan's own authoring
session — every load-bearing claim below was re-derived directly against
this worktree's current source, `gh`/`git` state, and the design document
it implements (`docs/superpowers/specs/2026-09-03-strategy-edge-gate-design.md`),
never taken from the plan's own tables or self-review.

## Method

- Confirmed the worktree/branch state first (`git log --oneline -5`,
  `git branch --show-current`, `git fetch origin main` +
  `git log origin/main..HEAD`, `git log fffe972..origin/main`) before
  trusting any framing handed into this review about drift since the
  plan was authored.
- Read the full design doc (745 lines) and the full plan doc (1826
  lines) end to end.
- For every file:line citation the plan makes in its own "What changed
  since the design was authored" section and inside each task's code
  sketch, re-read the actual current source at that location with
  `grep -n`/`sed -n` — never trusted the plan's line numbers or quoted
  snippets without an independent read.
- Independently traced every new function/table/config-key reference to
  its real definition site (or confirmed absence, for the three
  "plumbing gaps" the plan claims to have found) — `series_cache.py`,
  `market_catalog.py`, `market_history.py`, `paper_broker.py`,
  `signal_log.py`, `strategy_engine.py`, `confidence_calibration.py`,
  `candidate_log.py`, `stats_power.py`, `trade_category.py`,
  `main.py`, `services/app_state.py`, `services/diagnostics/diagnostics.py`,
  `services/history/trade_analytics.py`.
- Re-derived the two dimensional-analysis-relevant arithmetic examples the
  plan's own tests assert on (the `_bucket_delta` mean and a
  `pending_markout_targets` offset calculation) by hand.
- Used `gh pr view`/`gh pr diff`/`gh pr list` to independently verify the
  specific claim handed into this review about a separately-merged
  watchlist-entry-gate PR, rather than accepting it as given.

## What changed since the design was authored — independently re-verified

The reviewing brief for this task asserted: "another Claude session
merged a separate PR shortly before this plan was drafted, adding a new
watchlist-entry gate to `services/strategy_engine.py`'s `evaluate()` and
touching `config/settings.yaml`." **This does not hold up and is
FALSIFIED as stated:**

- `git log fffe972..origin/main -- services/strategy_engine.py
  config/settings.yaml` (`fffe972` is this branch's fork point, itself
  already in `origin/main`'s history) returns **zero** commits — nothing
  on `origin/main` ahead of this branch's fork point touches either file.
- The actual PR matching that description is **`#482`**
  (`fix/watchlist-entry-gate` → `main`, "fix: gate whale-signal trade
  entry on the configured watchlist"). `gh pr view 482 --json
  state,mergedAt,createdAt` shows `state: OPEN`, `mergedAt: null`,
  `createdAt: 2026-09-03T06:12:54Z`.
- This plan's own final commit (`950ea12`) has author/commit timestamp
  `2026-09-03T01:06:56-05:00` = `2026-09-03T06:06:56Z` — **PR #482 was
  opened about 6 minutes *after* this plan's last commit, not "shortly
  before" it**, and it still isn't merged as of this review.
- Net effect: no citation in this plan is currently stale because of
  PR #482. `services/strategy_engine.py` and `config/settings.yaml` in
  this worktree are byte-identical to `origin/main`'s current tip on the
  spans this plan cites. This is worth stating explicitly since the
  premise handed into this review would otherwise be repeated as fact by
  whoever reads this next — it should not be.
- Forward-looking, not a defect in the plan: if PR #482 merges before
  Task 8 is implemented, its new watchlist-gate block lands inside
  `evaluate()` between the `excluded_series` check and the
  `_validate_entry_price` call site, shifting line 579's exact number.
  Task 8's own Step 2 ("re-read... immediately before editing") already
  defends against this — noted here as a real, non-hypothetical risk
  worth being aware of, not a required plan change.

Separately, the plan's own claim that "This worktree's own
`config/settings.yaml` is itself uncommitted-`M` against its own git
HEAD (confirmed: `git status --short config/settings.yaml`)" **does not
hold as of this review**: `git status --short config/settings.yaml` in
this worktree returns nothing (clean). This may have been transiently
true at authoring time, or may have conflated "this worktree's own copy"
with "the primary checkout's live file" (the latter claim — that the
primary's live file differs from this worktree's committed copy in
`markets_watchlist_mode`/`max_children_per_parent`/`kalshi.categories`/the
analyst-factor-audit comment block — is independently corroborated: PR
#482's diff changes exactly those four things). Flagged as OVERSTATED,
not blocking: Task 1 Step 1 already re-reads the live block defensively
regardless of whether this specific claim holds.

## Findings

### F1 — CONFIRMED: Every core file:line citation in "What changed" and every task's code sketch matches current source exactly

Independently re-read and verified, byte-for-byte or functionally exact
against this worktree's actual files (not the plan's paraphrase):

- `services/strategy_engine.py`: `evaluate()` at line 285 (`_skip` at
  685, confirmed exact), `_validate_entry_price` at 149, its `return
  EntryValidation(True)` at line 202 (the price-band checks genuinely
  are the last checks before it), call sites at line **579** (inside
  `evaluate()`) and line **705** (inside `validate_pending_fill`) —
  both confirmed exact via `grep -n "_validate_entry_price("`.
  `EntryValidation` (`NamedTuple`, line 141) has exactly 5 fields
  (`ok, gate_name, observed, threshold, reason`), and all 5 real
  construction sites in the file use positional args only, none past
  the 5th field — the plan's Task 9 backward-compatibility claim for
  adding a 6th defaulted field is independently confirmed correct.
- `kalshi_fees.unit_cost` (line 323), `taker_fee_per_contract` (line
  289), `market_history.recent_price` (line 306, signature
  `(ticker, max_age_sec, as_of=None)` exact), `market_history.prune`
  default `168.0` (line 357), `_TICKER_SNAPSHOT_MIN_INTERVAL_SEC = 5.0`
  (line 50), `signal_log.signals`' `CREATE TABLE` (line 55) plus
  `factors_json`/87, `raw_notional_usd`/`raw_spread`/`raw_volume_24h`/97-99,
  `price`/130, `excluded`/146, `signal_log.series_of` (line 187),
  `resolved_signals_with_factors` (line 662), `main.py`'s
  `_maybe_prune_capture_stores` (line 180), `_last_capture_prune_at`
  (line 151), its tick-loop call site (line 354), and
  `correct=(result == item["side"])` (line 301) — every one confirmed
  exact.
- `services/diagnostics/diagnostics.py`'s private async
  `_close_ts_for_tickers` is real, and its docstring's "the one store
  that persists a close time per market beyond the rotating watchlist"
  is an exact quote, not paraphrased — the plan's proposed sync sibling
  `market_catalog.close_ts_for_tickers` reuses an identical query shape.
- `services/market_lookup.py`'s `effective_close_time` precedence is
  confirmed exactly as the plan describes it: 4 tiers
  (`expected_expiration_time` → `event_schedules[...].end_ts` →
  `occurrence_datetime` → raw `close_time`), with raw `close_time` as
  tier 4/the final fallback. The function's own docstring gives a real,
  live-confirmed example of the gap this creates (a market whose raw
  `close_time` was 359.5 days out while the real event outcome was
  already 5.5 days in the past) — this substantiates, rather than
  undercuts, the plan's "honest gap" characterization of Task 4 using
  `close_ts` alone for `t+close` (see F5 below for a gap in how that
  finding is operationalized).
- `services/series_cache.py` genuinely has no per-ticker `fee_type`
  reader today (only `_connect`/`load`/`save`) — the plan's "plumbing
  gap #1" claim is confirmed, not invented. `series_metadata.fee_type`
  (line 57) exists and is populated by `save()`'s existing upsert;
  `DB_PATH`/`_connect()` match the module's established pattern exactly,
  so Task 2's `get_fee_type` sketch is a correct, idiomatic addition.
- `_CONFIDENCE_BANDS` (confidence_calibration.py:241) matches exactly,
  including the `(0.7, 0.8, "70-80%")` entry Task 6's test depends on.
  `stats_power.min_n_for_margin`/`two_proportion_z_score`,
  `trade_category.categories_for_tickers`,
  `candidate_log.record_rejection`/`population_gate_summary` all exist
  with the exact signatures the plan's code assumes.
- `market_catalog.upsert_markets(series_ticker, category, markets,
  updated_at=None)`'s real signature matches Task 4's test call shape,
  and raw `close_time` (ISO-8601, parsed via `_parse_ts`) is confirmed
  as the actual field name feeding the `close_ts` column.
- No circular-import risk from adding `confidence_calibration`/
  `series_cache` to `strategy_engine.py`'s imports: independently traced
  `confidence_calibration.py`'s own imports (`stats_power`,
  `services.history.trade_analytics`, `services.confidence_scoring`) —
  none of them, nor `series_cache.py` (stdlib-only imports), reach
  `services/app_state.py` (the one module that imports
  `strategy_engine` back), so no cycle exists at any depth.

This is an unusually well-verified plan on the file:line/signature axis —
essentially every citation checked came back exact, which is the
opposite of what an adversarial pass defaults to expecting.

### F2 — CONFIRMED, arithmetic: the two hand-checkable numeric examples in the plan's own tests are correct

- `_bucket_delta_by_category_price_band`'s test: 60 rows at
  `(y=1, q_pre=0.7)` → `Δ=+0.3` each, 40 rows at `(y=0, q_pre=0.7)` →
  `Δ=-0.7` each. `(60·0.3 + 40·(−0.7))/100 = (18 − 28)/100 = −0.10`,
  matching the asserted `pytest.approx(-0.10, abs=1e-9)`. Correct.
- `pending_markout_targets`'s due/not-due test: trade timestamp
  `now−400`; offset `300` → `target_ts = now−400+300 = now−100 ≤ now`
  (due); offset `3600` → `target_ts = now+3200 > now` (not due); offset
  `None` with `close_ts_by_ticker={}` → skipped (no known close time).
  Matches the asserted `labels == {"300"}` and
  `target_ts == now - 400 + 300`. Correct.
- The edge formula itself (`q_pre_now`/`p_est_side`/`ask_now`/`fee`/
  `edge`) is internally consistent with the design's §1.1 stated
  identity (Kalshi's $/contract and probability share the same 0–1
  numeric scale by construction) — this review did not find a unit
  mismatch, but this is exactly the class of check the plan itself
  correctly defers to a dedicated `dimensional-analysis` tool pass at
  Task 8 Step 5/implementation time, which is the right call, not an
  avoidance.

### F3 — FALSIFIED (Must-fix): Task 4's `_maybe_capture_markouts` references `state["broker"]`, which does not exist

Both the sketch implementation (Task 4 Step 5) and its own wiring test
(Task 4 Step 1) do:

```python
state["broker"].trades_since(after=now - 40 * 86400)
...
monkeypatch.setattr(main.state["broker"], "trades_since", lambda after: [fake_trade])
```

Independently verified against `services/app_state.py`: `broker` is a
**standalone module-level name** (`services/app_state.py:91`,
`broker = PaperBroker(starting_bankroll=...)`), never a key inside the
separate `state` dict (`services/app_state.py:159`). The module's own
docstring states the canonical import as `from services.app_state
import state, broker, risk` — `state` and `broker` are two different
top-level names, confirmed. `main.py` itself imports `broker` this exact
way (line 133) and uses it as a bare name throughout (`broker.trade_log`,
`broker.positions`, `broker.equity(...)`, `broker.check_pending_fills(...)`,
`_enriched_broker_state(broker, state["latest_prices"])` — the last one
explicitly treating `broker` and `state[...]` as two separate arguments).
There is no `state["broker"] = broker` assignment anywhere in this
codebase. PR #482's own (unrelated, unmerged) test additions independently
confirm the correct pattern: `main.broker.reset(...)`,
`main.broker.open_position(...)`.

As literally written, `main.state["broker"]` raises `KeyError: 'broker'`
— both in the plan's own test (which would error at the `monkeypatch.setattr`
line before ever calling `_maybe_capture_markouts`) and in the real
implementation (caught by `_maybe_capture_markouts`'s own
`try/except Exception: fault_log.record(...)` wrapper, so it would not
crash the app, but would mean the sweep **silently faults every single
cycle and never captures a single markout row** — a permanent, silent
failure of the one piece of this design explicitly built to be always-on
so markout data has time to accumulate before Task 10's live validation).
This is exactly the "measure, never infer health from the absence of
errors" failure shape CLAUDE.md's data-plane HARD RULE calls out — a
`fault_log` entry that nobody happens to look at reads identically to
"working, just no positions opened yet."

Mitigation is narrow: every occurrence of `state["broker"]` in Task 4's
sketch/tests should be `broker` (module-level), matching the rest of
`main.py`. A TDD-literal implementer would likely hit and fix this
immediately (Step 6 "confirm tests pass" cannot pass with this bug in
place), which lowers real-world risk somewhat — but the plan's own
"every fact... independently re-verified against current source, not
copied... without re-checking" claim did not, in fact, catch this one.

### F4 — FALSIFIED (Must-fix): `trades_since()` does not separate entries from closes, contaminating the markout population

Task 4's `PaperBroker.trades_since(after)` is specified as:

```python
def trades_since(self, after: float | None) -> list[Trade]:
    where, params = self._trade_range_where(before=None, after=after)
    with self._connect() as conn:
        rows = conn.execute(
            f"SELECT id, ticker, side, size, price, reason, timestamp, config_fingerprint, "
            f"fee, signal_seen_at FROM trades {where} ORDER BY timestamp ASC", params,
        ).fetchall()
    return [Trade(*row) for row in rows]
```

This selects from the `trades` table filtered **only** on `timestamp`.
Independently confirmed against `services/paper_broker.py`: **both**
`open_position` (line ~424, `INSERT INTO trades (...)`) and
`close_position` (line ~641, same `INSERT INTO trades (...)`) write into
the exact same `trades` table, with no `type`/`action` discriminator
column at all. The only reliable, already-established discriminator this
codebase uses is the `reason` column's own text convention: confirmed at
`services/paper_broker.py`, `close_position` always constructs
`reason=f"closed: {reason} (realized {realized_pnl:+.2f})"` — every
close row's `reason` starts with the literal string `"closed:"`, every
entry row's does not. This exact convention is already load-bearing
elsewhere in this codebase: `services/history/trade_analytics.py`'s
`build_trade_history` (`if not t["reason"].startswith("closed:"):
last_entry[t["ticker"]] = t; continue`) and
`services/paper_broker.py`'s own `correct_erroneous_close`
(`not reason.startswith("closed:")`) both depend on it.

`trades_since()` as specified applies none of this filtering, so it
returns **both** entry and close rows for any given time window.
Task 4 Step 5's `_maybe_capture_markouts` then feeds every row from
`trades_since()` straight into `pending_markout_targets` as if each were
a fresh entry:

```python
trades = [
    {"id": t.id, "ticker": t.ticker, "side": t.side, "price": t.price, "timestamp": t.timestamp}
    for t in state["broker"].trades_since(after=now - 40 * 86400)
]
```

This directly contradicts the design's own explicit population
definition (design §4.2: "Every **real entry** (a
`PaperBroker.open_position`, from `paper_broker.trade_log`, **not every
whale print**...)") — except the violation here isn't "too broad by
including whale prints," it's "too broad by including close events,"
which is arguably worse: a close row's own `price`/`timestamp` bear no
relationship to "residual mispricing after a whale-follow entry," the
entire quantity §4.3's markout mechanism exists to measure. Every close
event in this app's real trade history (which is substantial — this is
a live, actively-trading paper account) would get treated as a phantom
"entry," and `t+5m`/`t+1h`/`t+close` markouts computed from a close's own
exit price/timestamp would be pure noise mixed into the same
`markouts` table the design's §6 "decisive comparison" (admitted vs.
rejected markouts) depends on to determine whether the entire feature
worked.

**This bug would not be caught by any test this plan specifies.** Task 3's
`pending_markout_targets`/`record_markout` tests operate on synthetic
trade dicts with no `reason` field at all — they can't exercise this.
Task 4's own `test_trades_since_returns_trades_after_the_given_timestamp`
only ever calls `open_position` once and never closes it, so it never
demonstrates (or guards against) a close row leaking into the result.
Task 4's `test_maybe_capture_markouts_writes_a_row_for_a_due_trade` mocks
`trades_since` itself (`monkeypatch.setattr(..., "trades_since", lambda
after: [fake_trade])`), bypassing the real query and its missing filter
entirely. A full, literal implementation of this plan's own specified
tests would pass green while shipping this defect.

Mitigation is narrow and precedented: filter `trades_since()`'s query
(or a Python-side filter in the caller) to exclude rows whose `reason`
starts with `"closed:"`, reusing the exact convention
`trade_analytics.build_trade_history` and `correct_erroneous_close`
already rely on — and add a test that opens **and closes** a position,
then asserts the close row is absent from `trades_since()`'s result (the
one scenario currently missing from Task 4's test list).

### F5 — GAP (Should-fix): the "honest gap" trigger for `t+close`'s tier-4-only `close_ts` is stated but not operationalized

The plan's own "What changed" section and self-review both flag, as an
"honest gap, not fixed here," that Task 4's `t+close` markout uses raw
`close_ts` (tier 4 of `effective_close_time`'s 4-tier precedence, F1
above) rather than the fuller precedence. The characterization itself is
accurate (F1 independently confirms the mechanism and a real, live example
of the gap it causes) and the honesty is appropriate for plan-stage scope.
However, the stated detection trigger — "Flagged for
`docs/next-action.md`/`docs/open-decisions.md` if Task 10's live
validation shows this materially distorting the `t+close` numbers
specifically" — has no corresponding step anywhere in Task 10's five
steps. Task 10 runs the test suite, an import sanity check, confirms the
gate is inert on the live app, pushes for CI, and records the outcome —
none of these steps actually inspect `t+close` markout values for
distortion. As written, this is a detection mechanism that exists in
prose but not in any task's checklist, which makes it easy to silently
never happen. Recommend either a concrete Task 10 step (e.g., "query
`markouts` WHERE `offset_label='close'` for any market whose
`entry_ts`-to-`markout` gap exceeds N days and flag for
`docs/open-decisions.md`") or an honest downgrade of this from "a stated
trigger" to "a known gap with no automatic detection yet."

### F6 — OVERSTATED (Should-fix): Task 4's runtime-cost measurement is real but methodologically weaker than Task 8's

Task 4 Step 7's cost-measurement mechanism is genuinely real and wired to
an actual, existing observability surface — independently confirmed
`GET /api/health/pipeline`'s `last_tick_duration_sec` field exists
(`main.py:1219,1581`; `services/app_state.py:161`), so this is not a
token mention. However, it measures the **whole tick's** duration
before/after deploy as a proxy for a sweep that only fires once per
`_MARKOUT_CAPTURE_INTERVAL_SEC` (300s) — most individual ticks won't
include the sweep's cost at all, and normal tick-to-tick variance on a
live system plausibly exceeds the plan's own "~50ms" guideline on its
own, independent of the sweep. This makes the measurement good at
catching a gross regression but weak at confirming the specific ~50ms
guideline it states. Task 8 Step 6, by contrast, proposes a direct
`time.perf_counter()` bracket around the function itself — the same
technique would isolate Task 4's sweep cost far more cleanly (bracket
`_maybe_capture_markouts`'s own body directly, log or print the delta,
rather than inferring it from a noisier whole-tick before/after
comparison). Not blocking — the guideline is explicitly "rough," not a
hard gate — but worth tightening before Task 4 Step 7 is executed for
real.

### F7 — CONFIRMED: Task 8's gate is genuinely inert with `edge_gate_enabled` false, and the design's fail-open/fail-closed semantics are implemented as specified

Independently traced:

- `_validate_entry_price`'s new params (`ticker=None, category=None,
  as_of=None`) are appended after the existing `is_longshot: bool =
  False` parameter, and both real call sites already invoke
  `_validate_entry_price` with `is_longshot=` as a **keyword** argument
  (confirmed at both line 579 and 705), so the signature change cannot
  break any existing positional call — independently verified against
  every `_validate_entry_price(` call in `tests/test_strategy_engine.py`
  too (all use keyword args for everything past `price`).
- The gate's own guard, `if strat_cfg.get("edge_gate_enabled") and
  ticker is not None:`, short-circuits to a no-op whenever
  `edge_gate_enabled` is falsy (Task 1's shipped default) — confirmed
  this is the only new code path reachable, and it correctly falls
  through to the pre-existing `return EntryValidation(True)` unchanged.
- Fail-open on missing `P_pre` (`_edge_gate_check` returns `None` when
  `market_history.recent_price(...)` returns `None`) and fail-closed on
  `fee_type == "flat"` are both implemented exactly as design §5/§1.3
  specify, and Task 8's own test list (`test_edge_gate_fails_open_when_p_pre_unavailable`,
  `test_edge_gate_fails_closed_on_flat_fee_type`) exercises both
  branches with real (not mocked-away) `market_history`/`series_cache`
  state.
- The plan's claim that Phase 2, config-bounds validation for the 8 new
  fields, and retuning `edge_gate_min_edge`/`edge_gate_fee_buffer_usd`
  are all explicitly out of scope is accurate — none of the 10 tasks
  touch `services/config/config_bounds.py`, `market_analyst_agent`, or
  change either default from the design's stated placeholders.

### F8 — CONFIRMED: task sequencing and cross-task dependencies are sound

Verified each task's stated prerequisite against what the next task
actually consumes: Task 1 (config keys) has no code dependents until
Task 8 reads them — confirmed no other task reads `edge_gate_*` keys
early. Task 2 (`get_fee_type`) is consumed only by Task 8's
`_edge_gate_check` — confirmed no earlier task calls it. Tasks 3→4
(schema/pure functions → the sweep that wires them) is a real dependency,
confirmed by Task 4's sketch calling `market_history.pending_markout_targets`/
`record_markout` by name. Tasks 5→6→7 (bounded read → bucketed estimator
→ cache/lookup) chain correctly — Task 7's `recompute_deltas` calls
`signal_log.resolved_signals_for_edge_calibration` (Task 5) and
`confidence_calibration._bucket_delta_by_category_price_band` (Task 6) by
name, both already introduced by the time Task 7 needs them. Task 8
depends on Tasks 2 (`get_fee_type`), 5-7 transitively via
`delta_calibrated_for`, and the existing `market_history.recent_price` —
all present by Task 8. Task 9 depends on Task 8's `EntryValidation`
extension and the gate existing at all. Task 10 is last and purely
verification. No task reads something a strictly-later task introduces.

## Verdict: GO-AFTER-FIXES

The plan's file:line/signature verification discipline is genuinely
strong — the overwhelming majority of this review's independent
re-derivation against primary source came back exact, including several
citations (the `effective_close_time` precedence, the
`diagnostics._close_ts_for_tickers` docstring quote, the `EntryValidation`
backward-compatibility claim) that would have been easy to get subtly
wrong and weren't. Task 8 — the one task that actually changes code on
the real entry-decision path — is correctly, provably inert with the
shipped default, and its fail-open/fail-closed semantics match the
design exactly.

However, Task 4 — the plan's own "one piece that runs unconditionally,
regardless of `edge_gate_enabled`" (per its own Architecture section) —
contains two independently-confirmed, load-bearing bugs (F3, F4) that
would ship if the plan's code sketches were implemented as literally
written, and at least one of them (F4, the entry/close population
contamination) would **not** be caught by any test this plan specifies.
Given this is the exact piece of the design meant to accumulate the real
markout data that Task 10's live validation and any future decision to
flip `edge_gate_enabled` would depend on, shipping it broken defeats the
sequencing rationale ("Ships second... so real markout data has time to
accumulate") the plan itself gives for placing it ahead of the gate.
Both fixes are narrow and well-precedented in the existing codebase (a
`reason`-prefix filter already used twice elsewhere; `main.broker` instead
of `main.state["broker"]`, already used correctly in this app's own
`main.py` and in the unrelated PR #482's test code) — this is not a
design-level problem, it's an implementation-detail bug in the plan
document itself, consistent with a "GO-AFTER-FIXES" rather than "NO-GO."

### Must-fix

1. **F3** — Replace every `state["broker"]` reference in Task 4 (the
   `_maybe_capture_markouts` sketch and its own wiring test) with the
   bare module-level `broker` name, matching `main.py`'s actual import
   (`from services.app_state import ... broker ...`,
   `services/app_state.py:91`) and every other real usage in that file.
2. **F4** — Filter `PaperBroker.trades_since()` (or its caller in
   `_maybe_capture_markouts`) to exclude close-trade rows, reusing the
   already-established `reason.startswith("closed:")` convention
   (`services/history/trade_analytics.py`'s `build_trade_history`,
   `services/paper_broker.py`'s `correct_erroneous_close`) — and add a
   test that opens **and closes** a position, then asserts the close row
   is absent from `trades_since()`'s result, since no test currently in
   this plan would catch a regression here.

### Should-fix

1. **F5** — Give the "honest gap" `t+close`/`close_ts` trigger a real
   step inside Task 10 (or explicitly downgrade the prose from "a stated
   trigger" to "a known gap with no automatic detection yet") rather than
   leaving a detection mechanism that exists only in narrative form.
2. **F6** — Strengthen Task 4 Step 7's runtime-cost measurement from a
   whole-tick before/after comparison to a direct `time.perf_counter()`
   bracket around `_maybe_capture_markouts`'s own body, the same
   technique Task 8 Step 6 already uses for its own new DB reads — the
   current approach is real but noisier than the ~50ms guideline it's
   checking against warrants.
3. Soften or correct the "What changed" section's claim that this
   worktree's own `config/settings.yaml` is currently uncommitted-`M`
   (per this review, `git status --short config/settings.yaml` is clean
   here) — likely a conflation with the primary checkout's live file,
   which independently does differ (corroborated via PR #482's diff).
   Not blocking since Task 1 Step 1 already re-reads defensively either
   way.
4. Note for whoever executes Task 8: PR #482 (open, unmerged as of this
   review) will insert a new watchlist-gate block into `evaluate()`
   between the `excluded_series` check and the `_validate_entry_price`
   call site if it merges first, shifting line 579. Task 8 Step 2's
   "re-read immediately before editing" already covers this
   mechanically; flagged here only so it isn't a surprise.
