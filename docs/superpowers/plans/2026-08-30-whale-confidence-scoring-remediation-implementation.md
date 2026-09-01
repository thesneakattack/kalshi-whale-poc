# Whale-Confidence Scoring Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `whale_confidence_weights`' four fabricated-input sites and its tie-blind
tertile measurement (D1), add the shared input-coverage diagnostic (D2's prerequisite),
then split the single composite score into an accuracy score and a real-time edge score
(D2) — without ever letting an unmeasured or contaminated report reach either of the two
live weight-write paths.

**Architecture:** Phase 0 makes the measurement instrument itself trustworthy (a
materiality-floored boundary-in-tie predicate, a three-way `data_status` return shape,
deterministic ordering) with no behavior change. Phase 1 replaces four fabricated
neutral-default factor values with honest `None`, threading present/absent-aware
renormalization through the composite formula. Phase 2 surfaces input coverage as a
report field and a `GET /api/quality/summary` check. **Phase 3 is a hard, no-code
re-measurement gate** — nothing in Phase 4/5 may start until it confirms real,
post-fix data is clean. Phase 4 splits the config/scoring/persistence plumbing into
`whale_accuracy_weights` + `whale_edge_weights` and immediately re-gates both live
write paths behind a `measurement_is_valid` check (brought forward from the design's
literal Phase-5 slot — see that task's own rationale). Phase 5 splits the calibration
report into accuracy/edge/populations sections and extends the gate to cover both.
Phase 6 (real weight retuning) is explicitly not planned here.

**Tech Stack:** Python/FastAPI app (`services/`), SQLite additive migrations
(`_add_column_if_missing`), pytest (sync tests; `asyncio.run` only where the module
under test is itself async — none of this design's touched functions are).
`config/settings.yaml` schema edits go through the `config-field-edit` skill.

**Spec:** `docs/superpowers/specs/2026-08-30-whale-confidence-scoring-remediation-design.md`
(commit `06e4392`, GO verdict — Stage 3 design, Stage 4 review, a safety-critical
revision, and that revision's own review, all clean). Every `§N` reference below is a
section of that document; this plan does not re-derive its reasoning, only sequences it.

## Global Constraints

- **Read-before-write, every task.** Every file/line cited below was verified against
  this worktree's current source this session (not carried over from the design doc's
  own citations, though they matched in every case checked). Re-verify signatures and
  line numbers immediately before editing anyway — normal drift between planning and
  execution is expected, per CLAUDE.md's "never guess" rule.
- **Kalshi-sourced fields.** No task here adds new Kalshi field parsing (the design's own
  §0/"Touched files" statement). Tasks 3, 4, and 6 do touch already-parsed raw fields
  (`volume_24h_fp`, `yes_ask_dollars`) inside existing call sites — per
  `.claude/rules/kalshi-integration-authority.md`'s literal scope ("reads... Kalshi-
  sourced data"), each of those tasks' Step 3 opens with a one-line `docs/kalshi/
  CHEATSHEET.md` check before editing, even though the change itself is "return None on
  absence," not new field derivation.
- **Safety, never regressed.** No task touches `kalshi_account.trading_enabled`,
  `POST /api/trading/enable`, `services/risk_manager.py`, or any kill-switch value.
  `confidence_calibration.auto_apply_enabled` stays `false` throughout this plan — no
  task flips it. Every schema change is additive (`_add_column_if_missing`); no
  `data/*.db` file is dropped, moved, or backfilled; tests always `monkeypatch(DB_PATH)`
  to a tmp path.
- **The Phase 3 gate is load-bearing for this plan, not just the design.** Task 10 is a
  hard prerequisite for every task in Phase 4 and Phase 5 (Tasks 11–16). No later task
  may be started, in any order, before Task 10's own checkbox is checked with recorded
  evidence (§10's D4 gate; this plan's own sequencing, below). **Caveat found during PR
  review, worth stating plainly: this is process discipline (a documented task
  dependency plus a required-evidence checkbox), not a code- or CI-enforced block** — no
  test or runtime check stops an implementer (human or agent) from starting Task 11's
  code before Task 10's checkbox is checked. That is a different, weaker guarantee than
  Task 12's `measurement_is_valid` gate below, which *is* code-level and test-enforced
  (a runtime check with an adversarial test proving it fires). Whoever executes this plan
  needs to actually honor the ordering; nothing here will catch it if they don't.
- **The `measurement_valid` gate ships atomically with the write-path retarget it
  protects, not as a follow-up (see Task 12's rationale for why this plan brings it
  forward from the design's literal Phase 5 slot into Phase 4).**
- **GitNexus impact checks** (`mcp__gitnexus__impact`/`context`/`trace`) are required,
  per CLAUDE.md's standing toolchain rule and §11's own list, before the multi-file edits
  in Tasks 1, 3, 11, 12, and 13 — each task below names the exact GitNexus impact-check
  item from §11 it satisfies. Running the check is implementation work (not performed by
  this planning stage); the task text says where it belongs in the sequence.
- **`config/settings.yaml`'s schema split (Task 11)** uses `.claude/skills/config-field-
  edit/SKILL.md`'s 7-step procedure verbatim (snapshot → reset-to-HEAD → edit → commit →
  restore live tuning → re-apply the same edit → `git diff` sanity check) — the design's
  own §7.1 implementation-plan note names this skill explicitly.
- **Cite a task from this plan as `whale-confidence-scoring-remediation Task N`**, never
  bare `Task N` — `docs/superpowers/plans/README.md`'s own standing warning: eight other
  plans number their own tasks from 1.
- **Run per-task tests** via `ddev exec -s fastapi sh -c "cd /app/.claude/worktrees/agent-
  a5110e2d3016b26a8 && python3 -m pytest -q -p no:testmon <files>"` from the primary
  root (this worktree's own `ddev exec` restriction — CLAUDE.md's dev-workflow section).
- **No task changes any `strategy.*` gate value** (`entry_threshold`, `min_unit_cost`,
  `max_unit_cost`, `min_contracts`) — both new scores are reported, never wired into a
  live entry gate (design §2's explicit non-goal).
- **Do not plan Phase 6.** Task 16 is this plan's last task; real weight-value retuning
  is a distinct, later, human-reviewed change (§10 Phase 6), out of scope here by design.

---

## Phase 0 — The measurement instrument (report-only, no weight/score/schema change)

### Task 1: `_bucket_win_rates` gains a materiality-floored boundary-in-tie predicate and a three-way `data_status`

**Files:**
- Modify: `services/whale_calibration/confidence_calibration.py` (`_bucket_win_rates` at
  `:67-116`, `_factor_report` at `:119-129`)
- Test: `tests/test_confidence_calibration.py` (replace the existing bare-`{}` assertions
  in `test_constant_factor_does_not_discriminate` and `test_missing_factor_key_excluded_
  not_crashed` with the new tuple return; add the new fixtures below)

**Interfaces:**
- Produces: `_bucket_win_rates(rows, factor_name) -> tuple[dict, str]` — `data_status` is
  one of `"ok"` / `"insufficient_variance"` / `"contaminated"` (§3). `_factor_report`
  forwards it as a fourth `"data_status"` key. Every existing caller of `_bucket_win_
  rates` (`_factor_report` here) and every future one (`_bucket_mean_edge`, Task 15) must
  unpack the pair, not a bare dict — **GitNexus impact-check item (b)**: run `impact`/
  `context` on `_bucket_win_rates` before this edit; its own note is that a plan following
  §11's list mechanically must not undercount `_factor_report` and `_bucket_mean_edge` as
  readers, even though the latter doesn't exist yet at this task (Task 15 will re-run the
  same check before reusing this shape).

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_confidence_calibration.py`:

```python
def _tie_dataset(values, correct_fn=lambda i: i % 2 == 0):
    """Rows whose depth_factor equals `values[i]` in the given order -
    _bucket_win_rates sorts internally, so insertion order doesn't matter.
    correct_fn is an arbitrary, non-degenerate correctness pattern; this
    predicate only cares about win_rate once buckets form, which these
    tests don't assert on."""
    return [_row(depth=v, unusualness=0.5, proximity=0.5, context=0.5,
                 agreement=0.5, correct=correct_fn(i)) for i, v in enumerate(values)]


def test_small_incidental_tie_at_a_cut_boundary_is_not_contaminated():
    # n=1000, materiality floor is max(30, 0.005*1000)=30. A 5-row tie
    # straddling the low cut (index 1000//3=333) mirrors depth_factor's
    # real 2-6-row float ties (audit table row 1, design §2) - must NOT trip.
    values = [i / 1000 for i in range(1000)]
    values[330:335] = [0.5] * 5
    buckets, status = cc._bucket_win_rates(_tie_dataset(values), "depth_factor")
    assert status == "ok"
    assert buckets  # a real split happened


def test_large_structural_tie_at_a_cut_boundary_is_contaminated():
    # n=90, cuts at index 30 and 60. A 40-row tie spans indices 20-59,
    # straddling the low cut - 40 >= max(30, 0.45)=30. Mirrors
    # agreement_factor's real 24,357-row tie (audit table row 5, design §2).
    values = [i / 100 for i in range(20)] + [0.5] * 40 + [0.9 + i / 1000 for i in range(30)]
    buckets, status = cc._bucket_win_rates(_tie_dataset(values), "depth_factor")
    assert status == "contaminated"
    assert buckets == {}


def test_single_valued_factor_is_insufficient_variance_never_contaminated():
    # analyst_factor/block_trade_factor's real shape (audit §1.8/§1.9):
    # always present, zero variance. This is Stage 4's Finding 2 regression -
    # a permanently sparse factor's {} must not read as a real tie.
    rows = [_row(depth=0.1, unusualness=0.5, proximity=0.5, context=0.5,
                 agreement=0.5, correct=(i % 2 == 0)) for i in range(50)]
    buckets, status = cc._bucket_win_rates(rows, "depth_factor")
    assert status == "insufficient_variance"
    assert buckets == {}


def test_factor_report_carries_data_status_through():
    rows = _discriminating_dataset(n_per_bucket=10)
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    depth_report = next(f for f in result["report"]["per_factor"] if f["factor"] == "depth_factor")
    assert depth_report["data_status"] == "ok"
    unusual_report = next(f for f in result["report"]["per_factor"] if f["factor"] == "unusualness_factor")
    assert unusual_report["data_status"] == "insufficient_variance"
```

Update the two existing tests whose assertions target the old bare-`{}` shape:
`test_constant_factor_does_not_discriminate` and `test_missing_factor_key_excluded_not_
crashed` currently assert `cluster_report["buckets"] == {}` — unaffected by the tuple
change directly (they read `per_factor` entries, not `_bucket_win_rates`'s raw return),
but add `assert ...["data_status"] == "insufficient_variance"` to each so the superseded
bare-`{}`-only assertion doesn't stand alone (design §3's own instruction: "the existing
... test is replaced, not supplemented").

- [ ] **Step 2: Run to verify FAIL** (`_bucket_win_rates` still returns a bare dict;
  `data_status` KeyError on `per_factor` entries).

- [ ] **Step 3: Implement.** In `services/whale_calibration/confidence_calibration.py`:

```python
def _tied_run_size(sorted_vals: list[float], cut_idx: int) -> int:
    """Size of the contiguous run of equal, rounded values straddling the
    cut at cut_idx (the boundary between sorted_vals[cut_idx-1] and
    sorted_vals[cut_idx]). 0 if the two neighbors differ - no tie at this
    cut. Rounded comparison matches the existing 2026-08-14 float-jitter
    dedup fix a few lines up (distinct_values)."""
    n = len(sorted_vals)
    if cut_idx <= 0 or cut_idx >= n:
        return 0
    boundary_val = sorted_vals[cut_idx - 1]
    if sorted_vals[cut_idx] != boundary_val:
        return 0
    lo = cut_idx - 1
    while lo > 0 and sorted_vals[lo - 1] == boundary_val:
        lo -= 1
    hi = cut_idx
    while hi < n and sorted_vals[hi] == boundary_val:
        hi += 1
    return hi - lo


def _bucket_win_rates(rows: list[dict], factor_name: str) -> tuple[dict, str]:
    """... (existing docstring, extended:) Returns (buckets, data_status) -
    see design §3. data_status distinguishes a permanently sparse factor
    (analyst_factor/block_trade_factor-shaped: "insufficient_variance",
    benign forever) from a transient tie-contaminated cut
    ("contaminated", the condition this predicate exists to catch) - only
    the second should ever gate anything downstream (§9)."""
    applicable_rows = [r for r in rows if factor_name in r["factors"]]
    sorted_rows = sorted(applicable_rows, key=lambda r: r["factors"][factor_name])
    n = len(sorted_rows)
    rounded_vals = [round(r["factors"][factor_name], 6) for r in sorted_rows]
    distinct_values = set(rounded_vals)
    if n < _BUCKET_COUNT or len(distinct_values) < _BUCKET_COUNT:
        return {}, "insufficient_variance"

    third = n // _BUCKET_COUNT
    materiality_floor = max(30, 0.005 * n)
    for cut_idx in (third, n - third):
        if _tied_run_size(rounded_vals, cut_idx) >= materiality_floor:
            return {}, "contaminated"

    buckets = {
        "low": sorted_rows[:third],
        "mid": sorted_rows[third:n - third],
        "high": sorted_rows[n - third:],
    }
    return {
        key: {"n": len(group), "win_rate": round(sum(1 for r in group if r["correct"]) / len(group) * 100, 1)}
        for key, group in buckets.items() if group
    }, "ok"


def _factor_report(rows: list[dict], factor_name: str) -> dict:
    buckets, data_status = _bucket_win_rates(rows, factor_name)
    if "low" not in buckets or "high" not in buckets:
        return {"factor": factor_name, "buckets": buckets, "gap_pts": None,
                "discriminates": None, "data_status": data_status}
    gap = round(buckets["high"]["win_rate"] - buckets["low"]["win_rate"], 1)
    return {
        "factor": factor_name, "buckets": buckets, "gap_pts": gap,
        "discriminates": gap >= _MIN_DISCRIMINATION_GAP, "data_status": data_status,
    }
```

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_confidence_calibration.py` in full** for regressions (any
  other test reading `_bucket_win_rates`' return shape directly, if one exists beyond
  what Step 1 already updated).
- [ ] **Step 6: Commit:** `feat: materiality-floored boundary-in-tie predicate + three-way data_status in _bucket_win_rates (whale-confidence-scoring-remediation Task 1)`

---

### Task 2: `resolved_signals_with_factors()` gets deterministic order and optional date scoping

**Files:**
- Modify: `services/signal_log.py` (`resolved_signals_with_factors` at `:600-630`)
- Test: `tests/test_signal_log.py`

**Interfaces:**
- Produces: `resolved_signals_with_factors(since_ts: float | None = None) -> list[dict]`
  — default unchanged (full history, no date scoping — the calibration gate's own "total
  resolved count, not recency" reasoning, design §4). Rows now arrive `ORDER BY seen_at
  ASC`, not SQLite's incidental rowid order.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_signal_log.py` (matching
  its existing `_log(tmp_path, monkeypatch)` fixture idiom):

```python
def test_resolved_signals_with_factors_orders_by_seen_at_ascending(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    # Logged out of chronological order - the old behavior (unspecified
    # rowid order) would return them in insertion order here, which is
    # coincidentally reverse-chronological; ORDER BY seen_at ASC must not
    # depend on insertion order at all.
    log.log_signal("B", "yes", 100, 0.6, "real-provider", seen_at=200, factors={"depth_factor": 0.5})
    log.log_signal("A", "yes", 100, 0.6, "real-provider", seen_at=100, factors={"depth_factor": 0.5})
    log.log_signal("C", "yes", 100, 0.6, "real-provider", seen_at=300, factors={"depth_factor": 0.5})
    for row_id in (1, 2, 3):
        log.mark_resolved(row_id, correct=True)
    rows = log.resolved_signals_with_factors()
    # Tightened 2026-08-31 adversarial review: the prior "assert len(rows) == 3"
    # passes identically with or without ORDER BY seen_at ASC - it doesn't test
    # ordering despite the test's name. seen_at itself isn't in the returned
    # dict shape, but `series` now is (this task's SELECT was corrected to
    # keep it, see the query above) - tickers "A"/"B"/"C" have no hyphen, so
    # today's series_of() (ticker.split("-")[0]) maps each straight through
    # to its own name, giving a real per-row ordering probe with no schema
    # change needed:
    assert [r["series"] for r in rows] == ["A", "B", "C"]


def test_resolved_signals_with_factors_since_ts_scopes_the_window(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("OLD", "yes", 100, 0.6, "real-provider", seen_at=100, factors={"depth_factor": 0.5})
    log.log_signal("NEW", "yes", 100, 0.6, "real-provider", seen_at=500, factors={"depth_factor": 0.5})
    for row_id in (1, 2):
        log.mark_resolved(row_id, correct=True)
    assert len(log.resolved_signals_with_factors()) == 2  # default: unscoped, unchanged
    assert len(log.resolved_signals_with_factors(since_ts=300)) == 1  # only NEW
```

(Verified against the real `_log`/`log_signal`/`mark_resolved` signatures in
`services/signal_log.py` and `tests/test_signal_log.py`'s existing fixture this session.)

- [ ] **Step 2: Run to verify FAIL** (`since_ts` unexpected keyword; ordering test may
  pass accidentally on SQLite's current incidental order — the second assertion in the
  ordering test's docstring explains why it doesn't rely on that).
- [ ] **Step 3: Implement.**

```python
def resolved_signals_with_factors(since_ts: float | None = None) -> list[dict]:
    """... (existing docstring, extended:) since_ts=None keeps today's full-
    history behavior (design §4: the calibration gate cares about total
    resolved count, not recency) - a caller wanting a recency-scoped view
    (e.g. a future report asking "does the gap look different in the last
    30 days") passes it explicitly. ORDER BY seen_at ASC makes today's de
    facto row order (SQLite rowid order) an explicit, stated property
    instead of an accident a future VACUUM/migration could silently
    reorder history out from under."""
    # `series` corrected into this SELECT during the 2026-08-31 catch-up review: a
    # same-day but unrelated commit (5bb29be, "expose the series dimension") already
    # added it to the real current query before this task's own commit ever landed -
    # dropping it here would silently regress a column services/whale_calibration/
    # README.md:97-101 documents as feeding the by_series report field.
    query = (
        "SELECT confidence, correct, factors_json, raw_notional_usd, raw_spread, raw_volume_24h, series "
        "FROM signals WHERE resolved = 1 AND excluded = 0 AND factors_json IS NOT NULL"
    )
    params: tuple = ()
    if since_ts is not None:
        query += " AND seen_at >= ?"
        params = (since_ts,)
    query += " ORDER BY seen_at ASC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    # ... unchanged loop body below
```

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_signal_log.py` in full** for regressions.
- [ ] **Step 6: Commit:** `feat: resolved_signals_with_factors gains ORDER BY seen_at + optional since_ts (whale-confidence-scoring-remediation Task 2)`

---

## Phase 1 — Fabricated inputs: one bug class, four sites

### Task 3: `composite_confidence_breakdown`'s honest-absence handling + the hard-paired `_bucket_win_rates` filter fix

**Files:**
- Modify: `services/confidence_scoring.py` (`composite_confidence_breakdown` at
  `:123-316`, `composite_confidence` at `:319-333`)
- Modify: `services/whale_calibration/confidence_calibration.py` (`_bucket_win_rates`'s
  applicability filter at `:94` — **ships in the same commit as this task, not later**;
  design §10 Phase 1 row states this explicitly: shipping the renormalization without
  this filter breaks the calibration report the instant any real row carries an absent
  factor, which happens as soon as this very task's `depth_factor` fix lands)
- Test: `tests/test_confidence_scoring.py`, `tests/test_confidence_calibration.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `composite_confidence_breakdown(..., agreement_factor: float | None = 0.5,
  trend_factor: float | None = 0.5, ...)` — defaults unchanged (simulator callers with no
  concept to offer still get neutral 0.5); an explicit `None` now means "a real provider
  looked and found nothing" and flows through unscored, not coerced to 0.5.
  `depth_factor` becomes `None` internally when `market_volume <= 0` (no signature
  change — computed inside the function, design §5.2). `ConfidenceBreakdown.depth_
  factor`/`agreement_factor`/`trend_factor` become `float | None` in practice.
  `_bucket_win_rates`'s applicability filter changes from key-presence to key-presence-
  and-not-None.
- **GitNexus impact-check item, ad hoc but required**: this is a real signature-shape
  change to a money/strategy hot-path function with multiple callers (`kalshi_trade_
  tape.py`, `whale_simulator.py` via `composite_confidence`, `confidence_calibration.py`
  via the filter). Run `mcp__gitnexus__impact` on `composite_confidence_breakdown`
  before Step 3.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_confidence_scoring.py`
  (using its existing `_market(...)` helper):

```python
def test_depth_factor_is_none_not_one_when_market_has_zero_volume():
    market = _market(ticker="TICK-A", volume_24h_fp="0")
    breakdown = composite_confidence_breakdown(market, [market], size=1000, price=0.5, now=time.time())
    assert breakdown.depth_factor is None


def test_explicit_none_agreement_and_trend_are_not_coerced_to_neutral():
    market = _market()
    breakdown = composite_confidence_breakdown(
        market, [market], size=1000, price=0.5, now=time.time(),
        agreement_factor=None, trend_factor=None,
    )
    assert breakdown.agreement_factor is None
    assert breakdown.trend_factor is None


def test_omitted_agreement_and_trend_still_default_to_neutral():
    # The simulator's own no-concept-at-all case (design §5.2) - unlike an
    # explicit None, simply not passing the kwarg must be unchanged.
    market = _market()
    breakdown = composite_confidence_breakdown(market, [market], size=1000, price=0.5, now=time.time())
    assert breakdown.agreement_factor == 0.5
    assert breakdown.trend_factor == 0.5


def test_score_renormalizes_over_present_factors_when_one_is_absent():
    # A synthetic all-but-one-present case (design §5.2's required unit
    # test): depth_factor absent (None), every other factor at a known
    # value, weights all equal - score must equal the weighted mean of the
    # PRESENT eight factors only, not treat the absent one as 0.
    market = _market(volume_24h_fp="0")  # forces depth_factor -> None
    equal_weights = {name: 1 / 8 for name in [
        "unusualness_factor", "proximity_factor", "context_factor", "agreement_factor",
        "cluster_factor", "trend_factor", "analyst_factor", "block_trade_factor",
    ]}
    equal_weights["depth_factor"] = 1 / 8  # present in weights dict even though value is absent
    breakdown = composite_confidence_breakdown(
        market, [market], size=1000, price=0.5, now=time.time(),
        agreement_factor=0.7, cluster_factor=0.7, trend_factor=0.7, analyst_factor=0.7,
        block_trade_factor=0.7, weights=equal_weights,
    )
    assert breakdown.depth_factor is None
    # unusualness/proximity/context aren't caller-controlled directly here;
    # this asserts the renormalized score differs from (and is higher
    # than) what a naive "absent scores 0" implementation would produce -
    # a tighter bound than an exact literal, since the market fixture's
    # own unusualness/proximity/context values aren't hand-picked above.
    assert breakdown.score > 0.5


def test_degenerate_all_absent_falls_back_to_maximally_uncertain():
    # Design §5.2's documented degenerate case - every weighted factor
    # absent for one row falls back to 0.5, not a crash or a 0.
    market = _market(volume_24h_fp="0", close_time=None)
    breakdown = composite_confidence_breakdown(
        market, [market], size=1000, price=0.5, now=time.time(),
        agreement_factor=None, trend_factor=None,
        weights={"depth_factor": 1.0, "unusualness_factor": 0.0, "proximity_factor": 0.0,
                 "context_factor": 0.0, "agreement_factor": 0.0, "cluster_factor": 0.0,
                 "trend_factor": 0.0, "analyst_factor": 0.0, "block_trade_factor": 0.0},
    )
    # depth_factor (the only nonzero-weight factor) is None; every other
    # weighted factor is 0-weight - the sum of weights over PRESENT
    # factors is 0, forcing the degenerate fallback regardless of their
    # actual computed values.
    assert breakdown.score == 0.5
```

(`_market` signature confirmed this session: `_market(ticker="TICK-A",
volume_24h_fp="10000", yes_bid_dollars="0.5", close_time=None, event_ticker=None)` at
`tests/test_confidence_scoring.py:9`.)

Append to `tests/test_confidence_calibration.py`:

```python
def test_bucket_win_rates_excludes_rows_with_an_explicit_none_value():
    # The hard dependency this task exists to close: post-fix, a row's
    # factors dict always HAS every key, but the value can be None. The
    # old key-presence-only filter would pass such a row into sorted(),
    # crashing on None-vs-float comparison the first time it runs.
    rows = [_row(depth=0.1 + i * 0.05, unusualness=0.5, proximity=0.5, context=0.5,
                 agreement=0.5, correct=(i % 2 == 0)) for i in range(10)]
    rows.append({"confidence": 0.5, "correct": True,
                 "factors": {"depth_factor": None, "unusualness_factor": 0.5}})
    buckets, status = cc._bucket_win_rates(rows, "depth_factor")  # must not raise
    assert status == "ok"
```

- [ ] **Step 2: Run to verify FAIL** (`None` comparison `TypeError` on the calibration
  test before the fix; `AssertionError`s on the confidence_scoring tests before the
  implementation).
- [ ] **Step 3: Implement.** First, per the Kalshi-fields Global Constraint: `grep -rn
  volume_24h_fp docs/kalshi/CHEATSHEET.md` (confirming the field's existing documented
  meaning hasn't changed since `depth_ratio`'s original implementation — this task only
  changes what happens when it's `<= 0`, not how it's read).

  In `services/confidence_scoring.py`, inside `composite_confidence_breakdown`:

```python
    market_volume = float(market.get("volume_24h_fp") or 0)
    if market_volume <= 0:
        depth_factor = None  # undefined, not badly-defined - no reportable
                              # 24h volume means no depth ratio to compute
    else:
        depth_ratio = size / market_volume
        depth_factor = 1.0 - math.exp(-_DEPTH_SATURATION_K * depth_ratio)
```

  Change the `agreement_factor`/`trend_factor` parameter annotations to `float | None =
  0.5` (default unchanged; annotation now honestly allows `None`). After all nine factor
  values are computed, replace the flat weighted-sum block with:

```python
    factor_values = {
        "depth_factor": depth_factor, "unusualness_factor": unusualness_factor,
        "proximity_factor": proximity_factor, "context_factor": context_factor,
        "agreement_factor": agreement_factor, "cluster_factor": cluster_factor,
        "trend_factor": trend_factor, "analyst_factor": analyst_factor,
        "block_trade_factor": block_trade_factor,
    }
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    present = {name: value for name, value in factor_values.items() if value is not None}
    weight_sum = sum(w[name] for name in present)
    if weight_sum > 0:
        score = sum(w[name] * value for name, value in present.items()) / weight_sum
    else:
        score = 0.5  # every weighted factor absent for this row - maximally
                      # uncertain, not a crash or a fabricated 0 (design §5.2)
    return ConfidenceBreakdown(
        depth_factor=depth_factor, unusualness_factor=unusualness_factor,
        proximity_factor=proximity_factor, context_factor=context_factor,
        agreement_factor=agreement_factor, cluster_factor=cluster_factor,
        trend_factor=trend_factor, analyst_factor=analyst_factor,
        block_trade_factor=block_trade_factor,
        score=min(max(score, 0.0), 1.0),
    )
```

  In `services/whale_calibration/confidence_calibration.py`, `_bucket_win_rates`'s
  applicability filter:

```python
    applicable_rows = [
        r for r in rows
        if factor_name in r["factors"] and r["factors"][factor_name] is not None
    ]
```

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_confidence_scoring.py` and `tests/test_confidence_
  calibration.py` in full** for regressions (both files, one command, since this task's
  commit touches both).
- [ ] **Step 6: Commit:** `fix: composite_confidence_breakdown renormalizes over present factors; None-safe bucket filter (whale-confidence-scoring-remediation Task 3)`

---

### Task 4: `raw_spread` — stop fabricating a 0.0 spread when there's no ask

**Files:**
- Modify: `services/whalewatchers/kalshi_trade_tape.py` (`raw_context` construction at
  `:746-757`)
- Test: `tests/test_whalewatchers_kalshi_trade_tape.py`

**Interfaces:**
- Not a scored factor — `raw_spread` never entered `ConfidenceBreakdown`; this is
  confined to `kalshi_trade_tape.py`'s `raw_context` dict, which `signal_log.log_signal`
  already writes straight into the existing nullable `raw_spread REAL` column
  (`signal_log.py:184`) — no `signal_log.py` change needed (design §5.1).

- [ ] **Step 1: Write the failing test.** Append to `tests/test_whalewatchers_kalshi_
  trade_tape.py` (matching its `_market`/`_trade` helpers):

```python
def test_raw_spread_is_none_not_zero_when_market_has_no_ask():
    provider = KalshiTradeTapeProvider()
    market = _market(ticker="K1")
    market.pop("yes_ask_dollars", None)  # no ask field at all
    trade = _trade(ticker="K1", count_fp="50000.00", taker_side="yes")
    signals = asyncio.run(provider.fetch_signals(market_context={"markets": [market], "trade_tape": [trade], "cfg": {}}))
    assert signals[0].raw_context["spread"] is None


def test_raw_spread_still_computed_when_a_real_ask_exists():
    provider = KalshiTradeTapeProvider()
    market = _market(ticker="K1")
    market["yes_ask_dollars"] = "0.65"
    trade = _trade(ticker="K1", count_fp="50000.00", taker_side="yes")
    signals = asyncio.run(provider.fetch_signals(market_context={"markets": [market], "trade_tape": [trade], "cfg": {}}))
    assert signals[0].raw_context["spread"] is not None
```

(Signature verified against `test_whalewatchers_kalshi_trade_tape.py`'s own `_market`/
`_trade` helpers and the `KalshiTradeTapeProvider().fetch_signals(market_context={...})`
idiom this session — same pattern the module's existing tests already use.)

- [ ] **Step 2: Run to verify FAIL** (`spread` is currently `0.0`, not `None`, on the
  no-ask case — `yes_ask = float(market.get("yes_ask_dollars") or price)` falls back to
  `price`, making `spread` exactly `0.0`).
- [ ] **Step 3: Implement.** `grep -rn yes_ask_dollars docs/kalshi/CHEATSHEET.md` first
  (Kalshi-fields Global Constraint). Then in `services/whalewatchers/kalshi_trade_
  tape.py`:

```python
            yes_ask = _price_dollars(market, "yes_ask_dollars")
            raw_context = {
                "notional_usd": round(notional, 2) if notional is not None else None,
                "spread": None if yes_ask is None else round(max(yes_ask - price, 0.0), 4),
                "volume_24h": float(market.get("volume_24h_fp") or 0.0),
            }
```

  (`_price_dollars` is this module's existing fixed-point-cents-to-dollars parser,
  already used at `:173`, `:583`, `:600`, `:630` — reused here, not reimplemented.)

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_whalewatchers_kalshi_trade_tape.py` in full.**
- [ ] **Step 6: Commit:** `fix: raw_spread is None, not a fabricated 0.0, when a market has no ask (whale-confidence-scoring-remediation Task 4)`

---

### Task 5: `trend_factor` — stop defaulting to neutral when there's no real momentum

**Files:**
- Modify: `services/whalewatchers/kalshi_trade_tape.py` (`_trend_factor` at `:177-194`,
  its call site in the `WhaleSignal`-scoring block at `:714`)
- Test: `tests/test_whalewatchers_kalshi_trade_tape.py`

**Interfaces:**
- Depends on Task 3 (`composite_confidence_breakdown` must already accept an explicit
  `trend_factor=None`).

- [ ] **Step 1: Write the failing test.**

```python
def test_trend_factor_is_none_when_no_momentum_history_exists(monkeypatch):
    from services import market_history
    monkeypatch.setattr(market_history, "momentum", lambda *a, **k: None)
    result = _trend_factor("TICK-A", "yes", time.time())
    assert result is None


def test_signal_carries_none_trend_through_to_the_breakdown(monkeypatch):
    from services import market_history
    monkeypatch.setattr(market_history, "momentum", lambda *a, **k: None)
    provider = KalshiTradeTapeProvider()
    market = _market(ticker="K1")
    trade = _trade(ticker="K1", count_fp="50000.00", taker_side="yes")
    signals = asyncio.run(provider.fetch_signals(market_context={"markets": [market], "trade_tape": [trade], "cfg": {}}))
    assert signals[0].factors["trend_factor"] is None
```

- [ ] **Step 2: Run to verify FAIL** (`_trend_factor` currently returns `0.5`).
- [ ] **Step 3: Implement.**

```python
def _trend_factor(ticker: str, side: str, now: float) -> float | None:
    """... (existing docstring, updated:) Returns None - not the old
    neutral 0.5 - when there isn't yet enough real price history to judge:
    a real provider looked and found nothing, which composite_confidence_
    breakdown (services/confidence_scoring.py) now knows how to exclude
    from the weighted sum rather than blend in as a fabricated neutral
    (design §5.1)."""
    mom = market_history.momentum(ticker, _TREND_LOOKBACK_SEC, as_of=now)
    if mom is None:
        return None
    signed_delta = mom["delta"] if side == "yes" else -mom["delta"]
    return 0.5 + 0.5 * min(max(signed_delta / _TREND_FULL_SCALE, -1.0), 1.0)
```

  The call site at `:714` (`trend = _trend_factor(ticker, side, now)`) needs no change —
  `trend` already flows straight into `composite_confidence_breakdown(..., trend_
  factor=trend, ...)` at `:733`, and Task 3 already made that parameter `None`-safe.

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_whalewatchers_kalshi_trade_tape.py` in full.**
- [ ] **Step 6: Commit:** `fix: trend_factor is None, not neutral, with no real momentum history (whale-confidence-scoring-remediation Task 5)`

---

### Task 6: `agreement_factor` — stop defaulting to neutral when there's no recent print history

**Files:**
- Modify: `services/whalewatchers/kalshi_trade_tape.py` (`agreement_factor` computation
  at `:696-700`)
- Test: `tests/test_whalewatchers_kalshi_trade_tape.py`

**Interfaces:**
- Depends on Task 3 (`composite_confidence_breakdown` must already accept an explicit
  `agreement_factor=None`).

- [ ] **Step 1: Write the failing test.**

```python
def test_agreement_factor_is_none_when_no_recent_prints_exist(monkeypatch):
    from services import signal_log
    monkeypatch.setattr(signal_log, "recent_sides_for_ticker", lambda *a, **k: [])
    provider = KalshiTradeTapeProvider()
    market = _market(ticker="K1")
    trade = _trade(ticker="K1", count_fp="50000.00", taker_side="yes")
    signals = asyncio.run(provider.fetch_signals(market_context={"markets": [market], "trade_tape": [trade], "cfg": {}}))
    assert signals[0].factors["agreement_factor"] is None


def test_agreement_factor_still_computed_when_recent_prints_exist(monkeypatch):
    from services import signal_log
    monkeypatch.setattr(signal_log, "recent_sides_for_ticker", lambda *a, **k: ["yes", "yes", "no"])
    provider = KalshiTradeTapeProvider()
    market = _market(ticker="K1")
    trade = _trade(ticker="K1", count_fp="50000.00", taker_side="yes")
    signals = asyncio.run(provider.fetch_signals(market_context={"markets": [market], "trade_tape": [trade], "cfg": {}}))
    assert signals[0].factors["agreement_factor"] == pytest.approx(2 / 3)
```

- [ ] **Step 2: Run to verify FAIL** (currently `0.5` on empty history).
- [ ] **Step 3: Implement.** In `services/whalewatchers/kalshi_trade_tape.py`:

```python
            recent_sides = signal_log.recent_sides_for_ticker(ticker, since_ts=now - _AGREEMENT_LOOKBACK_SEC)
            agreement_factor = (
                sum(1 for s in recent_sides if s == side) / len(recent_sides)
                if recent_sides else None
            )
```

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_whalewatchers_kalshi_trade_tape.py` in full.**
- [ ] **Step 6: Commit:** `fix: agreement_factor is None, not neutral, with no recent print history (whale-confidence-scoring-remediation Task 6)`

---

### Task 7: Grep-and-fix every other reader of `factors_json`/`factors[...]` that assumes a float

**Files:**
- Grep, then modify as found: `static/`, `frontend/`, `tools/` (design §5.3 — "not
  itemized file-by-file since a fresh grep at implementation time is more reliable than a
  list frozen now")
- Test: whatever test file(s) already cover the found call site(s); add one per fix if
  none exists

**Interfaces:**
- Depends on Tasks 3, 5, 6 (there must be real `None` values in `factors_json` for this
  task's fixes to matter against).

- [ ] **Step 1: Run the grep.** `grep -rn "factors\[" static/ frontend/ tools/` and
  `grep -rn "factors_json" static/ frontend/ tools/` from the repo root. For each hit
  that does arithmetic, comparison, or formatting on a per-factor value without an
  `is not None`/truthiness guard, write a failing test first (a fixture row with one
  factor `None`, asserting the call site degrades honestly — skips the row, renders
  "n/a", or equivalent — rather than raising or silently coercing `None` to `0`).
- [ ] **Step 2: Run each new test to verify FAIL.**
- [ ] **Step 3: Implement** the minimal `is not None` guard at each real hit found. If
  the grep finds nothing beyond `_bucket_win_rates` (already fixed, Task 3) and the
  report-building code Task 8/16 will touch anyway, record that explicitly in the commit
  message rather than silently closing this task with no diff — a documented "grep found
  nothing new" is different from skipping the task.
- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run the full suite for every touched file.**
- [ ] **Step 6: Commit:** `fix: guard remaining factors_json/factors[] readers against honest None values (whale-confidence-scoring-remediation Task 7)`

---

## Phase 2 — The shared fabricated-input diagnostic

### Task 8: `input_coverage` on the calibration report

**Files:**
- Modify: `services/whale_calibration/confidence_calibration.py`
  (`generate_calibration_report` at `:254-310` -- corrected 2026-08-31 catch-up review)
- Test: `tests/test_confidence_calibration.py`

**Interfaces:**
- Produces: `report["input_coverage"]` — `{"depth_factor": {"n": ..., "absent_pct":
  ...}, "trend_factor": {...}, "agreement_factor": {...}, "raw_spread": {...},
  "score_fallback_pct": ...}` (design §6.1's exact shape), computed from the same `rows`
  the function already receives — no new query.
- `raw_spread` reads from `r["raw_spread"]` (already one of `resolved_signals_with_
  factors()`'s selected columns, `signal_log.py:612`), not from `r["factors"]`.
- `score_fallback_pct` needs a way to tell "the degenerate-all-absent §5.2 fallback
  fired for this row" from "this row's real score is coincidentally 0.5" — Task 3's
  fallback and a genuine 0.5 composite are indistinguishable from `confidence` alone.
  Resolve by treating any row where **every** one of `depth_factor`/`trend_factor`/
  `agreement_factor`/the six always-present factors is either `None` or the row's
  `confidence` is exactly `0.5` AND `depth_factor is None` AND `trend_factor is None`
  AND `agreement_factor is None` as a fallback candidate — this is an approximation
  (§6.1 calls this observational, not a pass/fail check), and is out of scope to make
  exact without a persisted fallback flag the design doesn't add. Document the
  approximation inline in the implementation and in the report field's own key naming if
  it needs a caveat.

- [ ] **Step 1: Write the failing tests.**

```python
def test_input_coverage_reports_absent_pct_per_fabrication_site():
    rows = _discriminating_dataset(n_per_bucket=10)  # 30 rows, all factors present
    for r in rows[:5]:
        r["factors"]["depth_factor"] = None
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    coverage = result["report"]["input_coverage"]
    assert coverage["depth_factor"]["n"] == 30
    assert coverage["depth_factor"]["absent_pct"] == pytest.approx(5 / 30 * 100, abs=0.1)


def test_input_coverage_raw_spread_reads_the_row_level_column_not_factors():
    rows = _discriminating_dataset(n_per_bucket=10)
    for r in rows:
        r["raw_spread"] = None
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    assert result["report"]["input_coverage"]["raw_spread"]["absent_pct"] == 100.0
```

(`_row`'s current fixture shape doesn't set `raw_spread` at all — confirm/add it as a
top-level key alongside `"confidence"`/`"correct"`/`"factors"` in `_row()` or the test's
own row-construction, matching `resolved_signals_with_factors()`'s real returned dict
shape from `services/signal_log.py:621-629`, which this task's implementation reads.)

- [ ] **Step 2: Run to verify FAIL** (`KeyError: 'input_coverage'`).
- [ ] **Step 3: Implement.** In `generate_calibration_report`, before the final `return`:

```python
    def _coverage(factor_name):
        n = resolved_count
        absent = sum(1 for r in rows if r["factors"].get(factor_name) is None)
        return {"n": n, "absent_pct": round(absent / n * 100, 1) if n else 0.0}

    input_coverage = {
        "depth_factor": _coverage("depth_factor"),
        "trend_factor": _coverage("trend_factor"),
        "agreement_factor": _coverage("agreement_factor"),
        "raw_spread": {
            "n": resolved_count,
            "absent_pct": round(sum(1 for r in rows if r.get("raw_spread") is None) / resolved_count * 100, 1)
            if resolved_count else 0.0,
        },
        "score_fallback_pct": round(
            sum(1 for r in rows if r["factors"].get("depth_factor") is None
                and r["factors"].get("trend_factor") is None
                and r["factors"].get("agreement_factor") is None
                and r["confidence"] == 0.5) / resolved_count * 100, 1,
        ) if resolved_count else 0.0,
    }
```

  Add `"input_coverage": input_coverage` to the returned `report` dict.

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_confidence_calibration.py` in full.**
- [ ] **Step 6: Commit:** `feat: input_coverage diagnostic on the calibration report (whale-confidence-scoring-remediation Task 8)`

---

### Task 9: `check_confidence_input_coverage` folded into `run_offline`/`GET /api/quality/summary`

**Files:**
- Modify: `services/diagnostics/diagnostics.py` (new check function; registered in
  `run_offline` at `:714-721`)
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- Produces: `check_confidence_input_coverage(cfg: dict, since_ts: float | None = None,
  now: float | None = None) -> Check`, same `Check(name, status, summary, detail,
  evidence)` dataclass every sibling check already returns. `status` is `_OK` whenever
  `generate_calibration_report`'s own gate is cleared (this check reports numbers, it
  doesn't judge them — design §6.2), `_UNKNOWN` when calibration is below its resolved-
  signal floor (reusing that gate, not re-implementing a sample-size check).

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_diagnostics.py`
  (extending its `dbs` fixture/`_log`-style seeding — reuse `_seed_signals`-style direct
  SQLite inserts with real `factors_json`):

```python
def test_confidence_input_coverage_ok_once_calibration_is_ungated(dbs, monkeypatch):
    import json
    sl_module._connect().close()
    with sqlite3.connect(sl_module.DB_PATH) as conn:
        for i in range(60):
            conn.execute(
                "INSERT INTO signals (ticker, series, side, size, confidence, source, seen_at, "
                "resolved, correct, factors_json) VALUES (?,?,?,?,?,?,?,1,?,?)",
                ("T", "T", "yes", 100, 0.6, "real-provider", time.time(), i % 2,
                 json.dumps({"depth_factor": None, "unusualness_factor": 0.5})),
            )
    cfg = _cfg(confidence_calibration={"enabled": True, "min_resolved_signals": 50})
    c = diagnostics.check_confidence_input_coverage(cfg)
    assert c.status == "ok"
    assert c.detail["input_coverage"]["depth_factor"]["absent_pct"] == 100.0


def test_confidence_input_coverage_unknown_below_the_resolved_floor(dbs):
    cfg = _cfg(confidence_calibration={"enabled": True, "min_resolved_signals": 50})
    c = diagnostics.check_confidence_input_coverage(cfg)
    assert c.status == "unknown"
```

- [ ] **Step 2: Run to verify FAIL** (`AttributeError: check_confidence_input_
  coverage`).
- [ ] **Step 3: Implement.** In `services/diagnostics/diagnostics.py`:

```python
def check_confidence_input_coverage(cfg: dict, since_ts: float | None = None, now: float | None = None) -> Check:
    """How often each of the four fabrication-fixed factors (depth_factor,
    trend_factor, agreement_factor, raw_spread) is honestly absent -
    surfaced from CLAUDE.md's own "Start investigations here" step 1, no
    need to know to poll the calibration-specific route. Observational,
    not pass/fail (design §6.1) - a structural absence rate (a 15-minute
    market has no 24h volume) isn't itself a defect; the value is
    visibility and trend, not a threshold."""
    from services.whale_calibration import confidence_calibration

    cc_cfg = cfg.get("confidence_calibration") or {}
    rows = signal_log.resolved_signals_with_factors(since_ts=since_ts)
    result = confidence_calibration.generate_calibration_report(
        rows, cc_cfg.get("min_resolved_signals", 50), cfg.get("whale_confidence_weights"),
    )
    if result["report"] is None:
        return Check("confidence_input_coverage", _UNKNOWN, result["gated_reason"])
    coverage = result["report"]["input_coverage"]
    return Check(
        "confidence_input_coverage", _OK,
        f"depth {coverage['depth_factor']['absent_pct']}%, trend {coverage['trend_factor']['absent_pct']}%, "
        f"agreement {coverage['agreement_factor']['absent_pct']}%, spread {coverage['raw_spread']['absent_pct']}% "
        f"absent (n={result['report']['resolved_count']})",
        detail={"input_coverage": coverage},
    )
```

  Register it in `run_offline` (`:714-721`), alongside the other local-only checks:

```python
    checks = [
        check_threshold_integrity(cfg, since_ts, now),
        check_price_band_adherence(cfg, since_ts, now),
        check_runway_at_entry(cfg, since_ts, now),
        check_config_bounds(cfg),
        performance_by_epoch(since_ts, now),
        selectivity_curve(since_ts=since_ts, now=now),
        check_confidence_input_coverage(cfg, since_ts, now),
    ]
```

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_diagnostics.py` and `tests/test_diagnostics_routes.py`
  in full** (the latter exercises `GET /api/quality/summary`'s wiring through `run_
  offline`).
- [ ] **Step 6: Commit:** `feat: check_confidence_input_coverage in run_offline/quality summary (whale-confidence-scoring-remediation Task 9)`

---

## Phase 3 — Re-measurement (D4's blocking gate — no code change)

### Task 10: Confirm the honest, tie-safe measurement is clean on real, post-fix data

**This task has no code to write.** It is a verification gate, not a TDD task — Phases
0–2 must already be merged and running for long enough to accumulate real resolved
signals under the fixed formula before this task can produce a meaningful answer (design
§10: "each phase... is soaked before the next begins").

**Blocks:** every task in Phase 4 and Phase 5 (Tasks 11–16). None of them may start
until this task's checkbox below is checked with the evidence recorded, per this plan's
own Global Constraints and design §1/§10's D4 dependency.

- [ ] **Step 1:** Confirm Phases 0–2 are merged to `main` and have been running for a
  real soak period (not a fixed duration — design §10 doesn't set one; use judgment
  against how quickly `signal_log.db` accumulates real resolved signals, matching this
  repo's own re-verification precedent in `docs/superpowers/plans/2026-08-26-economic-
  strategy-remediation.md`'s "re-run E1-E7... against then-current data" instruction).
- [ ] **Step 2:** Pull a live report — `GET /api/confidence-calibration/report`, or
  `confidence_calibration.generate_calibration_report(signal_log.resolved_signals_with_
  factors(), ...)` directly against the real `data/signal_log.db` (read-only, per
  CLAUDE.md's `data/*.db` manual-verification convention).
- [ ] **Step 3:** For every entry in `report["per_factor"]`, confirm `data_status !=
  "contaminated"`. `"insufficient_variance"` entries (expected for `analyst_factor`/
  `block_trade_factor`, permanently) do not block this gate — only `"contaminated"`
  does (Task 1's own three-way distinction exists specifically so this step can tell the
  two apart).
- [ ] **Step 4:** Record the actual per-factor gaps observed (design §10's own reference
  numbers to compare order-of-magnitude against: agreement +7.4–8.9pts, depth residual
  ≈ −17.4pts before further fix, trend's with-vs-neutral gap ≈ +30pts once `momentum()`
  coverage allows a clean split) into a dated note — either an addendum to this plan file
  or a short entry in `docs/next-action.md`/`docs/open-decisions.md` if Phase 4/5 won't
  start in the same session. Do not merely conclude "looks plausible" — the design's own
  instruction is to record the numbers, not describe them qualitatively (§10 Phase 3
  gate row).
- [ ] **Step 5:** Check this task's own box only once Steps 2–4 are done with real,
  dated evidence attached (in the commit message or the note from Step 4) — this is the
  literal gate; checking it without that evidence defeats its purpose (`docs/superpowers/
  plans/README.md`'s own "checkboxes lie" warning applies with extra force to a task
  whose entire job is being a truthful checkbox).
- [ ] **Step 6: Commit** (docs-only, no code): `docs: Phase 3 re-measurement gate confirmed clean on real post-fix data (whale-confidence-scoring-remediation Task 10)` — the commit message itself should carry the Step 4 numbers, so the gate's evidence lives in `git log`, not only in prose elsewhere.

---

## Phase 4 — Dual-score plumbing

### Task 11: Config schema split + `confidence_scoring.py`'s accuracy/edge rename

**Files:**
- Modify: `config/settings.yaml` (`whale_confidence_weights` at `:165-174` — corrected
  2026-08-31 catch-up review; a same-day but unrelated commit (`7b91436`) inserted a
  24-line "SUPERSEDED 2026-08-30" audit-note comment above the block, which itself
  independently corroborates Task 1's tie-contamination finding and says "do not trust
  this module's gap_pts output... until that measurement-instrument bug is fixed" — fold
  this task's edit into/update that note rather than silently deleting it — → two new
  keys, via the `config-field-edit` skill)
- Modify: `services/confidence_scoring.py` (`DEFAULT_WEIGHTS` at `:116-120` →
  `DEFAULT_ACCURACY_WEIGHTS` + new `DEFAULT_EDGE_WEIGHTS`; `composite_confidence_
  breakdown`'s `weights` param → `accuracy_weights` + new `edge_weights`; `Confidence
  Breakdown` gains `edge_score: float`; `WhaleSignal` gains `edge_score: float | None =
  None`; `composite_confidence`'s internal forwarding call)
- Modify: `services/whalewatchers/kalshi_trade_tape.py` (`composite_confidence_
  breakdown(...)` call at `:731-736`: `weights=` → `accuracy_weights=`/`edge_weights=`;
  `WhaleSignal(...)` construction at `:759-770` gains `edge_score=breakdown.edge_score`)
  — the `edge_score` population half of this line depends on Task 13's `WhaleSignal.
  edge_score` field also existing; if sequenced as written (Task 11 before Task 13) add
  the field access here referencing the dataclass field this task itself adds.
- Modify: `services/whale_calibration/confidence_calibration.py` (`from services.
  confidence_scoring import DEFAULT_WEIGHTS` at `:45` → `DEFAULT_ACCURACY_WEIGHTS`;
  `_FACTOR_NAMES` at `:54` → `_ACCURACY_FACTOR_NAMES = tuple(DEFAULT_ACCURACY_WEIGHTS)` +
  new `_EDGE_FACTOR_NAMES = tuple(DEFAULT_EDGE_WEIGHTS)`)
- Verify (no change expected, confirm via impact check): `services/whale_simulator.py`
  imports `composite_confidence` (the plain-float wrapper, unchanged signature per design
  §7.2), not `composite_confidence_breakdown` or `DEFAULT_WEIGHTS` directly, and passes
  no `weights=` kwarg — verified this session it needs no edit, but the GitNexus check
  below confirms this rather than trusting this note alone.
- Test: `tests/test_confidence_scoring.py`, `tests/test_confidence_calibration.py`,
  `tests/test_trading_gate.py` (references `DEFAULT_WEIGHTS`/`whale_confidence_weights`
  at lines 118, 1413, 1475, 1492, 1496, 1499 — corrected 2026-08-31 catch-up review, 6
  occurrences today not the original 4; update all to the new names; this file's tests are Task 12's
  territory too, since several assert on the auto-apply write target, but the import
  rename itself belongs here)

**Interfaces:**
- Produces: `config/settings.yaml`'s `whale_accuracy_weights` (§7.1's exact provisional
  7-key table, mechanically renormalized from the current 9-key config's retained seven
  keys) and `whale_edge_weights` (equal thirds among `unusualness_factor`/`depth_factor`/
  `agreement_factor`). `composite_confidence_breakdown(..., accuracy_weights: dict | None
  = None, edge_weights: dict | None = None, ...)`. `ConfidenceBreakdown.edge_score`
  computed identically in shape to `score` (Task 3's present/absent-aware renormalization
  logic, reused) but over `edge_weights` and its own three-factor membership only.
- **GitNexus impact-check item (a)** (§11): "the weights → accuracy_weights rename and
  DEFAULT_WEIGHTS → DEFAULT_ACCURACY_WEIGHTS rename — a real signature/name change with
  multiple call sites: kalshi_trade_tape.py, whale_simulator.py, confidence_calibration.
  py, and the test suite." Run `mcp__gitnexus__impact` on `DEFAULT_WEIGHTS` and on
  `composite_confidence_breakdown` before Step 3 of this task — confirm the call-site list
  above is complete (this session's own grep found `services/research/research.py:158`
  as an additional real reader of `whale_confidence_weights`, not named in the design
  doc's "Touched files" list — a live finding this impact check should reproduce
  independently; if it doesn't, the check itself needs escalating, not this note).

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_confidence_scoring.py`:

```python
def test_default_accuracy_weights_replaces_default_weights_name():
    from services.confidence_scoring import DEFAULT_ACCURACY_WEIGHTS
    assert "unusualness_factor" not in DEFAULT_ACCURACY_WEIGHTS
    assert "proximity_factor" not in DEFAULT_ACCURACY_WEIGHTS
    assert set(DEFAULT_ACCURACY_WEIGHTS) == {
        "depth_factor", "context_factor", "agreement_factor", "cluster_factor",
        "trend_factor", "analyst_factor", "block_trade_factor",
    }


def test_default_edge_weights_covers_exactly_three_factors():
    from services.confidence_scoring import DEFAULT_EDGE_WEIGHTS
    assert set(DEFAULT_EDGE_WEIGHTS) == {"unusualness_factor", "depth_factor", "agreement_factor"}
    assert sum(DEFAULT_EDGE_WEIGHTS.values()) == pytest.approx(1.0)


def test_breakdown_has_both_score_and_edge_score():
    market = _market()
    breakdown = composite_confidence_breakdown(market, [market], size=1000, price=0.5, now=time.time())
    assert 0.0 <= breakdown.score <= 1.0
    assert 0.0 <= breakdown.edge_score <= 1.0


def test_edge_score_ignores_accuracy_only_factors():
    # trend_factor is accuracy-only (not in DEFAULT_EDGE_WEIGHTS) - swinging
    # it should move score but never edge_score.
    market = _market()
    low = composite_confidence_breakdown(market, [market], size=1000, price=0.5, now=time.time(), trend_factor=0.0)
    high = composite_confidence_breakdown(market, [market], size=1000, price=0.5, now=time.time(), trend_factor=1.0)
    assert low.edge_score == high.edge_score
    assert low.score != high.score


def test_accuracy_weights_param_replaces_weights_param():
    market = _market()
    custom = {"depth_factor": 1.0}
    breakdown = composite_confidence_breakdown(market, [market], size=1000, price=0.5, now=time.time(), accuracy_weights=custom)
    assert breakdown.score is not None  # merges against DEFAULT_ACCURACY_WEIGHTS for the rest
```

- [ ] **Step 2: Run to verify FAIL.**
- [ ] **Step 3: Implement, in this order** (each sub-step keeps the tree runnable, even
  though the final commit is one atomic change):

  a. `config/settings.yaml` — apply `.claude/skills/config-field-edit/SKILL.md`'s 7-step
     procedure: snapshot the live file, reset to `HEAD`, replace the `whale_confidence_
     weights:` block (`:165-174`, corrected 2026-08-31 adversarial review) with:

```yaml
whale_accuracy_weights:
  depth_factor: 0.0440
  context_factor: 0.2308
  agreement_factor: 0.1978
  cluster_factor: 0.1868
  trend_factor: 0.3407
  analyst_factor: 0.0
  block_trade_factor: 0.0
whale_edge_weights:
  unusualness_factor: 0.3333
  depth_factor: 0.3333
  agreement_factor: 0.3334
```

     (design §7.1's exact provisional numbers — mechanical migration, not a re-weighting;
     do not adjust these values even if they look tunable). Commit the schema-only change,
     then restore live tuning and re-apply the same block per the skill's steps 5–7,
     ending with the skill's own `git diff` sanity check.

  b. `services/confidence_scoring.py`: rename `DEFAULT_WEIGHTS` → `DEFAULT_ACCURACY_
     WEIGHTS` (matching the new 7-key set above verbatim — no alias kept, design §7.2);
     add `DEFAULT_EDGE_WEIGHTS` (the 3-key set above). Rename `composite_confidence_
     breakdown`'s `weights` param to `accuracy_weights`; add `edge_weights: dict | None =
     None`. Add `edge_score: float` to `ConfidenceBreakdown`. Compute it using the same
     present/absent partition Task 3 built, restricted to `edge_weights`' own three
     factors:

```python
    ew = {**DEFAULT_EDGE_WEIGHTS, **(edge_weights or {})}
    edge_present = {n: v for n, v in factor_values.items() if n in ew and v is not None}
    edge_weight_sum = sum(ew[n] for n in edge_present)
    edge_score = (
        sum(ew[n] * v for n, v in edge_present.items()) / edge_weight_sum
        if edge_weight_sum > 0 else 0.5
    )
```

     add `edge_score=min(max(edge_score, 0.0), 1.0)` to the `ConfidenceBreakdown(...)`
     return. Add `edge_score: float | None = None` to `WhaleSignal`. In `composite_
     confidence`'s own body, change its internal forwarding call's `weights=weights` to
     `accuracy_weights=weights` (its own external parameter name stays `weights` — design
     §7.2: "composite_confidence... is unchanged" refers to its own signature, not its
     internal call).

  c. `services/whalewatchers/kalshi_trade_tape.py:731-736`: `weights=cfg.get("whale_
     confidence_weights")` → `accuracy_weights=cfg.get("whale_accuracy_weights"),
     edge_weights=cfg.get("whale_edge_weights")`. `WhaleSignal(...)` construction gains
     `edge_score=breakdown.edge_score,`.

  d. `services/whale_calibration/confidence_calibration.py:45,54`: update the import and
     rename `_FACTOR_NAMES` to `_ACCURACY_FACTOR_NAMES`; add `_EDGE_FACTOR_NAMES =
     tuple(DEFAULT_EDGE_WEIGHTS)`. (`generate_calibration_report`'s own use of `_FACTOR_
     NAMES` is Task 16's territory — this step only renames the constant and adds its
     sibling, it doesn't yet restructure the report.)

  e. `services/research/research.py:158`: `cfg.get("whale_confidence_weights")` →
     `cfg.get("whale_accuracy_weights")` (this session's own grep finding — a real,
     read-only report caller the design's file list didn't name; confirm no other
     stray reference remains via `grep -rn whale_confidence_weights services/ main.py`
     after this step — it should return nothing outside comments/docstrings referencing
     the old name historically, which don't need editing).

  f. `tests/test_trading_gate.py:1325,1387,1404,1411`: update `DEFAULT_WEIGHTS` →
     `DEFAULT_ACCURACY_WEIGHTS` and `"whale_confidence_weights"` → `"whale_accuracy_
     weights"` in the fixture/assertions this task's rename affects — leave the
     auto-apply *behavior* assertions (which Task 12 changes) alone for now if they
     don't reference the config key by name directly.

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_confidence_scoring.py`, `tests/test_confidence_
  calibration.py`, `tests/test_whalewatchers_kalshi_trade_tape.py` in full.**
- [ ] **Step 6: Commit:** `feat: whale_confidence_weights splits into whale_accuracy_weights + whale_edge_weights (whale-confidence-scoring-remediation Task 11)`

---

### Task 12: `measurement_is_valid` gate — wired at both write paths, brought forward with the retarget it makes necessary

**Files:**
- Modify: `services/whale_calibration/confidence_calibration.py` (new
  `measurement_is_valid` function)
- Modify: `main.py` (`_maybe_run_auto_apply` — corrected 2026-08-31 catch-up review, now
  starting `:405`, not `:420-493`: retarget `whale_confidence_weights` reads/writes at
  `:432,460,466,489` (not `:427,453,459,482`) to `whale_accuracy_weights`; add the gate
  check before the write; `fault_log` import already present at `:46`)
- Modify: `services/whale_calibration/routes.py` (`apply_confidence_calibration_
  suggestion` — corrected 2026-08-31 catch-up review: retarget reads/writes at
  `:109,144,162,165` (not `:104,137,155,158`) to `whale_accuracy_weights`; add the gate
  check before the write; add a `fault_log` import — this file does not import it today)
- Test: `tests/test_main_scheduler_loops.py`, `tests/test_trading_gate.py`

**Interfaces:**
- Produces: `confidence_calibration.measurement_is_valid(*per_factor_lists: list[dict])
  -> bool` — `True` only when no entry across every given `per_factor` list carries
  `data_status == "contaminated"` (an `"insufficient_variance"` entry never blocks —
  Task 1's own distinction). Called with a single list here (`report["per_factor"]`,
  today's flat shape); Task 16 extends the call site to pass both `accuracy.per_factor`
  and `edge.per_factor` once that split exists — same function, no second definition.
- **Why this task exists here, ahead of the design's literal Phase 5 slot** (design §10's
  own table lists `measurement_valid` wiring under Phase 5): Task 11 must retarget both
  write paths' config key from `whale_confidence_weights` to `whale_accuracy_weights` in
  order for auto-apply/manual-apply to keep doing anything real post-split — leaving them
  writing to the now-schema-absent old key would make every future auto-apply a silent
  no-op (`cfg.get("whale_confidence_weights")` returns `None`/`{}` post-split, corrupting
  the blend math even before considering D4's risk) while *also* misreporting `current_
  weights` in the calibration report as `DEFAULT_ACCURACY_WEIGHTS` rather than the real
  live-tuned config — a "displayed value must match its label" violation, not merely a
  cosmetic gap. Retargeting is therefore mandatory in the same phase as the schema split,
  which reactivates exactly the D4 risk §9 exists to close (the retargeted write path now
  writes into the *real* `whale_accuracy_weights` for the first time). This plan's own
  Global Constraint — "the gate ships atomically with the retarget it protects, not as a
  follow-up task" — is therefore satisfied by landing an accuracy-only `measurement_is_
  valid` (using the `data_status` field Task 1 already shipped in Phase 0) in this same
  task, and *extending* its coverage to `edge.per_factor` in Task 16 once that list
  exists. This is an implementation-sequencing decision this plan makes explicitly, not a
  reinterpretation of the design's architecture or values — flagged here for review
  since the design's own phase table doesn't spell out this exact sub-sequencing.
- **GitNexus impact-check item (d)** (§11, brought forward with this task): "both
  `measurement_valid` call sites — `main.py:453-459` and `services/whale_calibration/
  routes.py`'s `/apply` route." Run `mcp__gitnexus__impact` on `blended_weights_for_
  auto_apply` before Step 3 — confirm these are still the only two real write paths (no
  third call site introduced since the design's own verification).

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_confidence_calibration.
  py`:

```python
def test_measurement_is_valid_true_when_nothing_is_contaminated():
    per_factor = [{"factor": "depth_factor", "data_status": "ok"},
                  {"factor": "analyst_factor", "data_status": "insufficient_variance"}]
    assert cc.measurement_is_valid(per_factor) is True


def test_measurement_is_valid_false_when_anything_is_contaminated():
    per_factor = [{"factor": "depth_factor", "data_status": "ok"},
                  {"factor": "agreement_factor", "data_status": "contaminated"}]
    assert cc.measurement_is_valid(per_factor) is False


def test_measurement_is_valid_checks_every_list_given():
    ok_list = [{"factor": "depth_factor", "data_status": "ok"}]
    bad_list = [{"factor": "unusualness_factor", "data_status": "contaminated"}]
    assert cc.measurement_is_valid(ok_list, bad_list) is False
```

Append to `tests/test_main_scheduler_loops.py` (matching its existing monkeypatch
style):

```python
def test_maybe_run_auto_apply_refuses_a_contaminated_measurement_even_with_the_gate_open(monkeypatch):
    monkeypatch.setattr(main.calibration_history, "due", lambda *a, **k: True)
    monkeypatch.setattr(main.signal_log, "resolved_signals_with_factors", lambda: [{"dummy": True}])
    contaminated_report = {
        "resolved_count": 999,
        "per_factor": [{"factor": "depth_factor", "data_status": "contaminated"}],
        "suggested_weights": {"depth_factor": 0.9},
    }
    monkeypatch.setattr(main.confidence_calibration, "generate_calibration_report",
                        lambda *a, **k: {"report": contaminated_report})
    updates = []
    monkeypatch.setattr(main.config_store, "update", lambda patch: updates.append(patch))
    main._maybe_run_auto_apply({
        "confidence_calibration": {
            "enabled": True, "min_resolved_signals": 1, "auto_apply_enabled": True,
            "auto_apply_min_resolved_signals": 1, "auto_apply_cooldown_sec": 0,
        },
        "advisory": {"enabled": False},
        "whale_accuracy_weights": {"depth_factor": 0.5},
    })
    assert not any("whale_accuracy_weights" in u for u in updates)
```

(Testing at the `main._maybe_run_auto_apply` unit level, per this file's own existing
convention — `auto_apply_enabled: True` is deliberately forced so this test proves the
new gate fires even when the *other* gate is wide open, matching design §9's own required
proof shape; the full HTTP-level route test for the manual path belongs in `tests/
test_trading_gate.py`, added next.)

Append to `tests/test_trading_gate.py` (near its existing calibration-apply tests):

```python
def test_calibration_apply_refuses_a_contaminated_measurement(tmp_path, monkeypatch):
    _reset_calibration_state()
    main.config_store.update({"confidence_calibration": {"enabled": True, "min_resolved_signals": 1}})
    contaminated_report = {
        "resolved_count": 999,
        "per_factor": [{"factor": "agreement_factor", "data_status": "contaminated"}],
        "suggested_weights": {"agreement_factor": 0.9},
    }
    monkeypatch.setattr(
        "services.whale_calibration.routes.confidence_calibration.generate_calibration_report",
        lambda *a, **k: {"report": contaminated_report},
    )
    resp = client.post("/api/confidence-calibration/apply")
    assert resp.status_code == 400
    assert "measurement" in resp.json()["detail"].lower()
```

(`_reset_calibration_state`/`client` are this file's existing fixtures, confirmed this
session at `tests/test_trading_gate.py`'s calibration-apply test block.)

- [ ] **Step 2: Run to verify FAIL.**
- [ ] **Step 3: Implement.**

  In `services/whale_calibration/confidence_calibration.py`:

```python
def measurement_is_valid(*per_factor_lists: list[dict]) -> bool:
    """True only when NONE of the given per-factor lists carry a
    "contaminated" data_status (design §9) - an "insufficient_variance"
    entry (analyst_factor/block_trade_factor's permanent shape) never
    blocks this. Both live write paths (main.py's auto-apply,
    routes.py's manual /apply) must check this before ever writing a
    blended suggestion into whale_accuracy_weights - see each call
    site's own comment for why. Takes *lists (not a single flat list) so
    Task 16 can extend the call sites to check both accuracy.per_factor
    and edge.per_factor once the report splits, without a second
    function or a call-site reshape."""
    for per_factor in per_factor_lists:
        if any(f.get("data_status") == "contaminated" for f in per_factor):
            return False
    return True
```

  In `main.py`'s `_maybe_run_auto_apply` (starting `:405`, corrected 2026-08-31
  adversarial review): change `cfg.get("whale_confidence_
  weights")` (`:432`) and `cfg.get("whale_confidence_weights") or {}` (`:460`) to `cfg.
  get("whale_accuracy_weights")`/`... or {}`; before the `if blended is not None and
  blended != current_weights:` block (`:464`), add:

```python
                    if not confidence_calibration.measurement_is_valid(cc_result["report"]["per_factor"]):
                        fault_log.record_fault(
                            "confidence_calibration", "measurement_invalid",
                            "auto-apply refused: a per-factor measurement is tie-contaminated",
                            context="calibration-auto-apply", severity="warn",
                        )
                    elif blended is not None and blended != current_weights:
                        fp_before = config_performance.fingerprint(cfg)
                        config_store.update({"whale_accuracy_weights": blended})
                        # ... rest of the existing block unchanged, its own
                        # config_path="whale_confidence_weights" at :482
                        # becomes config_path="whale_accuracy_weights"
```

  (`fault_log` is already imported in `main.py` at `:46` — no new import needed there.)

  In `services/whale_calibration/routes.py`'s `apply_confidence_calibration_suggestion`
  (`:115-167`): add `from services import fault_log` to the imports; change `cfg.
  get("whale_confidence_weights")` (`:109,144`, corrected 2026-08-31 adversarial review)
  to `cfg.get("whale_accuracy_weights")`;
  before the `config_store.update({"whale_confidence_weights": blended})` line (`:162`),
  add:

```python
    if not confidence_calibration.measurement_is_valid(result["report"]["per_factor"]):
        fault_log.record_fault(
            "confidence_calibration", "measurement_invalid",
            "manual apply refused: a per-factor measurement is tie-contaminated",
            context="calibration-manual", severity="warn",
        )
        raise HTTPException(status_code=400, detail="measurement is not currently valid - a per-factor gap is tie-contaminated, try again once more data resolves")
    config_store.update({"whale_accuracy_weights": blended})
```

  (and its own `config_path="whale_confidence_weights"` at `:165` (corrected 2026-08-31
  adversarial review) becomes `"whale_accuracy_weights"`.)

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_main_scheduler_loops.py`, `tests/test_trading_gate.py`,
  `tests/test_confidence_calibration.py` in full.**
- [ ] **Step 6: Commit:** `feat: measurement_is_valid gates both calibration write paths; retarget to whale_accuracy_weights (whale-confidence-scoring-remediation Task 12)`

---

### Task 13: `edge_score` persistence — additive column, one new field on the one real writer

**Files:**
- Modify: `services/signal_log.py` (`_connect` migrations at `:33-149`; `log_signal` at
  `:164-187`)
- Modify: `services/whale_stream/decision_bridge.py` (`log_signal(...)` call at
  `:79-84`)
- Test: `tests/test_signal_log.py`

**Interfaces:**
- Consumes: `WhaleSignal.edge_score` (Task 11).
- Produces: `signals.edge_score REAL` (nullable, additive); `log_signal(..., edge_score:
  float | None = None)`.
- **GitNexus impact-check item (c)** (§11): "log_signal's new parameter and the signals
  schema addition — every writer and reader of that table." Run `mcp__gitnexus__impact`
  on `log_signal` before Step 3 — confirm `decision_bridge.py:79-84` is still the only
  real (non-test) caller.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_signal_log.py`:

```python
def test_log_signal_persists_and_round_trips_edge_score(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("K1", "yes", 100, 0.6, "real-provider", edge_score=0.73)
    row_id = 1
    log.mark_resolved(row_id, correct=True)
    rows = log.resolved_signals_with_factors()
    # edge_score isn't in resolved_signals_with_factors' current return
    # shape (Task 15 adds it there for _bucket_mean_edge) - this test only
    # proves the column round-trips via a direct read, not via that helper.
    with sqlite3.connect(log.DB_PATH) as conn:
        stored = conn.execute("SELECT edge_score FROM signals WHERE id = ?", (row_id,)).fetchone()[0]
    assert stored == pytest.approx(0.73)


def test_log_signal_edge_score_defaults_to_null(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("K1", "yes", 100, 0.6, "real-provider")  # no edge_score passed
    with sqlite3.connect(log.DB_PATH) as conn:
        stored = conn.execute("SELECT edge_score FROM signals WHERE id = 1").fetchone()[0]
    assert stored is None
```

- [ ] **Step 2: Run to verify FAIL** (`no such column: edge_score` / unexpected
  keyword).
- [ ] **Step 3: Implement.** In `services/signal_log.py`'s `_connect`, after the
  `excluded` column block (`:147-148`):

```python
    # edge_score (2026-08-30, design §7.4) - the new real-time, per-signal
    # 0-1 edge indicator (services/confidence_scoring.py's ConfidenceBreakdown.
    # edge_score), distinct from score/confidence (the accuracy composite).
    # Nullable: only real providers that compute one populate it; a row
    # logged before this column existed, or from the simulator if its own
    # scoring path isn't updated, leaves it null - same convention as
    # factors_json/raw_context.
    _add_column_if_missing(conn, "signals", "edge_score", "REAL")
```

  `log_signal`'s signature gains `edge_score: float | None = None`; add it to the
  `INSERT`'s column list and values tuple:

```python
def log_signal(
    ticker: str, side: str, size: int, confidence: float, source: str,
    seen_at: float | None = None, factors: dict | None = None, raw_context: dict | None = None,
    price: float | None = None, excluded: bool = False, edge_score: float | None = None,
):
    raw_context = raw_context or {}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO signals "
            "(ticker, series, side, size, confidence, source, seen_at, factors_json, "
            "raw_notional_usd, raw_spread, raw_volume_24h, price, excluded, edge_score) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticker, series_of(ticker), side, size, confidence, source, seen_at or time.time(),
                json.dumps(factors) if factors is not None else None,
                raw_context.get("notional_usd"), raw_context.get("spread"), raw_context.get("volume_24h"),
                price, 1 if excluded else 0, edge_score,
            ),
        )
```

  In `services/whale_stream/decision_bridge.py:79-84`, add `edge_score=signal.edge_
  score,` to the `signal_log.log_signal(...)` call.

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_signal_log.py` in full**, plus a targeted run of
  whatever test covers `decision_bridge._handle_signal` (grep `tests/` for `_handle_
  signal` if not already known) to confirm the new kwarg doesn't break that call site's
  own tests.
- [ ] **Step 6: Commit:** `feat: signals.edge_score additive column, populated by the real signal-handling path (whale-confidence-scoring-remediation Task 13)`

---

## Phase 5 — Calibration report split

### Task 14: `stats_power.brier_score` — the shared Brier-computation helper

**Files:**
- Modify: `services/stats_power.py` (new `brier_score` function)
- Test: `tests/test_stats_power.py`

**Interfaces:**
- Produces: `brier_score(predictions: list[tuple[float, bool]]) -> float` — mean squared
  error between each predicted probability and its binary outcome (`0`/`1`). Design §7.5
  confirms via grep that no shared helper exists today; three call sites (`market_
  analyst_agent/per_market.py:275-292`, `settlement_edge.py`, `index_feed/settlement_
  algebra.py`) each inline the same formula independently and are **deliberately left
  untouched** by this task — this is a new shared helper for `confidence_calibration.
  py`'s new use (Task 16), not a retrofit of the three existing copies (design §7.5's own
  explicit scope boundary).

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_stats_power.py`:

```python
def test_brier_score_perfect_predictions_score_zero():
    assert stats_power.brier_score([(1.0, True), (0.0, False)]) == 0.0


def test_brier_score_worst_predictions_score_one():
    assert stats_power.brier_score([(0.0, True), (1.0, False)]) == 1.0


def test_brier_score_matches_the_textbook_formula():
    preds = [(0.7, True), (0.3, False), (0.6, True)]
    expected = ((0.7 - 1) ** 2 + (0.3 - 0) ** 2 + (0.6 - 1) ** 2) / 3
    assert stats_power.brier_score(preds) == pytest.approx(expected)


def test_brier_score_empty_list_returns_none_not_a_crash():
    # Same "no data means no basis for a number" idiom as margin_of_error_pts.
    assert stats_power.brier_score([]) is None
```

- [ ] **Step 2: Run to verify FAIL.**
- [ ] **Step 3: Implement.**

```python
def brier_score(predictions: list[tuple[float, bool]]) -> float | None:
    """Mean squared error between predicted probability and binary
    outcome - the standard calibration metric (Kalshi's own argued lens
    for a prediction market's real accuracy, docs/prediction-markets-
    research-reference.md Part 1.4). Extracted as the one shared helper
    three existing call sites each inline independently (design §7.5) -
    those three are deliberately left untouched; this is for confidence_
    calibration.py's new brier_skill_vs_market use. Returns None for an
    empty list - no data means no basis for a number, not a fabricated 0."""
    if not predictions:
        return None
    n = len(predictions)
    return sum((p - (1.0 if outcome else 0.0)) ** 2 for p, outcome in predictions) / n
```

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_stats_power.py` in full.**
- [ ] **Step 6: Commit:** `feat: stats_power.brier_score - the shared Brier helper for confidence_calibration's new use (whale-confidence-scoring-remediation Task 14)`

---

### Task 15: `_bucket_mean_edge` — reusing Phase 0's shared tie-check core, plus the row-level `side`/`price` `resolved_signals_with_factors()` needs to compute it

**Files:**
- Modify: `services/signal_log.py` (`resolved_signals_with_factors` — extend its
  `SELECT`/return dict to include `side` and `price`, verified this session as **not**
  already selected, needed for the edge formula below — a concrete requirement beyond
  what the design doc's prose spelled out, confirmed by reading the current function
  body directly)
- Modify: `services/whale_calibration/confidence_calibration.py` (new `_bucket_mean_
  edge` function, reusing Task 1's `_tied_run_size`/materiality-floor core)
- Test: `tests/test_signal_log.py`, `tests/test_confidence_calibration.py`

**Interfaces:**
- Consumes: `kalshi_fees.unit_cost(side, price) -> float | None`, `kalshi_fees.taker_
  fee_per_contract(price, ticker=None) -> float` (both confirmed present in `services/
  kalshi_fees.py` this session, `:323` and `:289`).
- Produces: `_bucket_mean_edge(rows, factor_name) -> tuple[dict, str]` — same `(buckets,
  data_status)` shape as `_bucket_win_rates`, grouped by mean `edge = payoff - unit_cost -
  taker_fee_per_contract(unit_cost)` (`payoff = 1.0 if correct else 0.0`) per bucket
  instead of win rate. Rows without a real `price` (pre-`price`-column history, design
  §7.6) are excluded before bucketing — the "priced subset."
- **GitNexus impact-check item (b), revisited**: this is the new reader of `_bucket_win_
  rates`'s return shape Task 1's own impact-check note anticipated. Re-run `mcp__
  gitnexus__context` on `_bucket_win_rates`/`_tied_run_size` before Step 3 to confirm
  nothing else changed shape underneath this reuse since Task 1 landed.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_signal_log.py`:

```python
def test_resolved_signals_with_factors_includes_side_and_price(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("K1", "yes", 100, 0.6, "real-provider", price=0.62, factors={"depth_factor": 0.5})
    log.mark_resolved(1, correct=True)
    row = log.resolved_signals_with_factors()[0]
    assert row["side"] == "yes"
    assert row["price"] == pytest.approx(0.62)
```

Append to `tests/test_confidence_calibration.py` (a local `_edge_row` helper mirroring
`_row`'s shape but adding `side`/`price`):

```python
def _edge_row(depth, side, price, correct):
    return {"correct": correct, "side": side, "price": price,
            "factors": {"depth_factor": depth, "unusualness_factor": 0.5, "agreement_factor": 0.5}}


def test_bucket_mean_edge_groups_by_dollar_gap_not_win_rate():
    rows = (
        [_edge_row(depth=0.1 + i * 0.01, side="yes", price=0.5, correct=False) for i in range(10)]
        + [_edge_row(depth=0.9 + i * 0.001, side="yes", price=0.5, correct=True) for i in range(10)]
    )
    buckets, status = cc._bucket_mean_edge(rows, "depth_factor")
    assert status == "ok"
    assert buckets["high"]["mean_edge_usd_per_contract"] > buckets["low"]["mean_edge_usd_per_contract"]


def test_bucket_mean_edge_excludes_rows_with_no_price():
    rows = [_edge_row(depth=0.5, side="yes", price=None, correct=True) for _ in range(40)]
    buckets, status = cc._bucket_mean_edge(rows, "depth_factor")
    assert status == "insufficient_variance"  # nothing left to bucket, same as too-few-rows
```

- [ ] **Step 2: Run to verify FAIL.**
- [ ] **Step 3: Implement.** `services/signal_log.py`'s `resolved_signals_with_
  factors`: extend the `SELECT` to `"SELECT confidence, correct, factors_json, raw_
  notional_usd, raw_spread, raw_volume_24h, series, side, price FROM signals WHERE ..."`
  — **`series` included here too, corrected during the 2026-08-31 catch-up review, same
  reason as Task 2's SELECT: dropping it would regress the by_series report field** —
  the tuple-unpack loop to match, and the appended dict to add `"side": side, "price":
  price`.

  `services/whale_calibration/confidence_calibration.py`:

```python
def _bucket_mean_edge(rows: list[dict], factor_name: str) -> tuple[dict, str]:
    """Same tertile-split-plus-materiality-floor-contamination-check core
    as _bucket_win_rates (design §7.5 - explicitly reused, not
    re-derived), grouped by mean realized $/contract edge instead of win
    rate. Rows with no real price (pre-price-column history, design §7.6)
    are excluded before bucketing - the "priced subset" the edge report's
    populations block (Task 16) reports separately from the accuracy
    score's full resolved_count."""
    from services import kalshi_fees

    priced_rows = [r for r in rows if factor_name in r["factors"]
                   and r["factors"][factor_name] is not None and r.get("price") is not None]
    for r in priced_rows:
        unit_cost = kalshi_fees.unit_cost(r["side"], r["price"])
        payoff = 1.0 if r["correct"] else 0.0
        r["_edge"] = payoff - unit_cost - kalshi_fees.taker_fee_per_contract(unit_cost)
    sorted_rows = sorted(priced_rows, key=lambda r: r["factors"][factor_name])
    n = len(sorted_rows)
    rounded_vals = [round(r["factors"][factor_name], 6) for r in sorted_rows]
    if n < _BUCKET_COUNT or len(set(rounded_vals)) < _BUCKET_COUNT:
        return {}, "insufficient_variance"
    third = n // _BUCKET_COUNT
    materiality_floor = max(30, 0.005 * n)
    for cut_idx in (third, n - third):
        if _tied_run_size(rounded_vals, cut_idx) >= materiality_floor:
            return {}, "contaminated"
    buckets = {
        "low": sorted_rows[:third], "mid": sorted_rows[third:n - third], "high": sorted_rows[n - third:],
    }
    return {
        key: {"n": len(group), "mean_edge_usd_per_contract": round(sum(r["_edge"] for r in group) / len(group), 4)}
        for key, group in buckets.items() if group
    }, "ok"
```

  (`_tied_run_size` is Task 1's existing helper, reused unchanged — this is the "not a
  second copy of the boundary-in-tie logic" design §7.5 requires.)

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_signal_log.py`, `tests/test_confidence_calibration.py`
  in full.**
- [ ] **Step 6: Commit:** `feat: _bucket_mean_edge reuses D1a's tie-check core for the edge score's $/contract report (whale-confidence-scoring-remediation Task 15)`

---

### Task 16: `generate_calibration_report`'s accuracy/edge/populations split, `brier_skill_vs_market`, and extending `measurement_is_valid` to cover both scores

**Files:**
- Modify: `services/whale_calibration/confidence_calibration.py`
  (`generate_calibration_report` at `:254-310` -- corrected 2026-08-31 catch-up review, full restructuring)
- Modify: `main.py`, `services/whale_calibration/routes.py`, `services/research/
  research.py` (the four callers of `generate_calibration_report` — signature now
  needs both weight dicts, and Task 12's gate call site extends its coverage)
- **Modify, added 2026-08-31 adversarial review (real regressions, not hypothetical
  — confirmed against current source, not caught by the earlier catch-up pass):**
  `services/whale_calibration/calibration_history.py:66-82`'s `record_snapshot(report,
  ...)` reads `report["per_factor"]`/`report["current_weights"]` directly — these keys
  move under `report["accuracy"]` in this task's new shape; without this fix,
  `_maybe_run_auto_apply`'s next scheduled snapshot raises `KeyError`, not a silent
  degradation. `frontend/src/js/advisory-calibration.js:229-292`'s `loadCalibrationReport()`
  (reads `r.per_factor`, `r.current_weights`, `r.overall_win_rate`, `r.confidence_label`,
  `r.suggested_weights`, `r.confidence_calibration` at top level) and
  `loadCalibrationHistory()` (reads `s.per_factor.depth_factor` from history snapshots) —
  without this fix, the entire whale-confidence dashboard panel breaks on the next
  deploy, live.
- Test: `tests/test_confidence_calibration.py`, `tests/test_trading_gate.py`,
  `tests/test_main_scheduler_loops.py`, and a new test for `calibration_history.
  record_snapshot` against the new nested shape (none of this plan's existing tests
  exercise that function)

**Interfaces:**
- Produces: `generate_calibration_report(rows, min_resolved_signals, current_accuracy_
  weights=None, current_edge_weights=None) -> dict` — `report` now shaped
  `{"resolved_count": ..., "accuracy": {...}, "edge": {...}, "populations": {...},
  "measurement_valid": bool}` per design §7.5's exact JSON shape. `measurement_valid` is
  Task 12's `measurement_is_valid(accuracy["per_factor"], edge["per_factor"])` — the
  extension this task's own docstring promised.
- Every caller updates in the same commit: `main.py:431` (corrected 2026-08-31 adversarial review; the snapshot/auto-apply
  block), `services/whale_calibration/routes.py:113,148` (corrected 2026-08-31 adversarial review) (the report route and
  the apply route's own internal `_build_report` closures), `services/research/
  research.py:157-159`.
- The integration test proving **both** write paths refuse independently (design §12's
  own required test) belongs here, now exercised against the real split shape rather than
  Task 12's flat-shape stand-in.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_confidence_calibration.
  py`:

```python
def test_report_splits_into_accuracy_edge_and_populations():
    rows = _discriminating_dataset(n_per_bucket=10)
    for r in rows:
        r["side"], r["price"] = "yes", 0.5
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    report = result["report"]
    assert set(report) >= {"resolved_count", "accuracy", "edge", "populations", "by_series", "measurement_valid"}
    assert "per_factor" in report["accuracy"]
    assert "per_factor" in report["edge"]
    assert "brier_skill_vs_market" in report["accuracy"]
    assert report["populations"]["accuracy_n"] == report["resolved_count"]
    # regression guard added 2026-08-31 catch-up review: by_series (commit 5bb29be,
    # a same-day but unrelated plan) must survive this task's restructuring, not be
    # silently dropped - covered separately from the set() membership check above so
    # a future refactor that removes just this key still fails loudly here.
    assert report["by_series"] == cc._series_win_rates(rows)


def test_measurement_valid_true_when_only_insufficient_variance_factors_exist():
    # The Stage 4 Finding 2 regression, re-proven against the real split
    # shape: analyst_factor/block_trade_factor-shaped sparsity must not
    # read as measurement_valid: False.
    rows = _discriminating_dataset(n_per_bucket=10)
    for r in rows:
        r["side"], r["price"] = "yes", 0.5
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    assert result["report"]["measurement_valid"] is True  # unusualness_factor etc. are insufficient_variance, not contaminated
```

Extend Task 12's `test_maybe_run_auto_apply_refuses_a_contaminated_measurement_even_
with_the_gate_open` and `test_calibration_apply_refuses_a_contaminated_measurement` to
mock `generate_calibration_report`'s return using the **real split shape**
(`{"report": {"resolved_count": ..., "accuracy": {"per_factor": [...]}, "edge":
{"per_factor": [...]}, "measurement_valid": False, ...}}`) instead of Task 12's flat
stand-in — same assertions, updated fixture, proving the wiring survives the shape
change rather than only ever having been tested against the placeholder.

- [ ] **Step 2: Run to verify FAIL.**
- [ ] **Step 3: Implement.** In `services/whale_calibration/confidence_calibration.py`:

```python
def _edge_factor_report(rows: list[dict], factor_name: str) -> dict:
    buckets, data_status = _bucket_mean_edge(rows, factor_name)
    if "low" not in buckets or "high" not in buckets:
        return {"factor": factor_name, "buckets": buckets, "gap_usd_per_contract": None, "data_status": data_status}
    gap = round(buckets["high"]["mean_edge_usd_per_contract"] - buckets["low"]["mean_edge_usd_per_contract"], 4)
    return {"factor": factor_name, "buckets": buckets, "gap_usd_per_contract": gap, "data_status": data_status}


def generate_calibration_report(
    rows: list[dict], min_resolved_signals: int,
    current_accuracy_weights: dict | None = None, current_edge_weights: dict | None = None,
) -> dict:
    from services.confidence_scoring import DEFAULT_EDGE_WEIGHTS
    from services import stats_power

    current_accuracy_weights = {**DEFAULT_ACCURACY_WEIGHTS, **(current_accuracy_weights or {})}
    current_edge_weights = {**DEFAULT_EDGE_WEIGHTS, **(current_edge_weights or {})}
    resolved_count = len(rows)
    if resolved_count < min_resolved_signals:
        return {"report": None, "gated_reason": f"{resolved_count}/{min_resolved_signals} ...", "resolved_count": resolved_count}

    accuracy_per_factor = [_factor_report(rows, name) for name in _ACCURACY_FACTOR_NAMES]
    edge_per_factor = [_edge_factor_report(rows, name) for name in _EDGE_FACTOR_NAMES]
    priced_rows = [r for r in rows if r.get("price") is not None]

    overall_win_rate = round(sum(1 for r in rows if r["correct"]) / resolved_count * 100, 1)
    ranked = sorted((f for f in accuracy_per_factor if f["gap_pts"] is not None), key=lambda f: f["gap_pts"], reverse=True)

    composite_preds = [(r["confidence"], r["correct"]) for r in priced_rows]
    market_preds = [(kalshi_fees.unit_cost(r["side"], r["price"]) or 0.5, r["correct"]) for r in priced_rows]
    composite_brier = stats_power.brier_score(composite_preds)
    market_brier = stats_power.brier_score(market_preds)
    brier_skill = (
        round(1 - composite_brier / market_brier, 4)
        if composite_brier is not None and market_brier not in (None, 0) else None
    )

    accuracy = {
        "overall_win_rate": overall_win_rate,
        "overall_win_rate_margin_pts": round(stats_power.margin_of_error_pts(resolved_count, overall_win_rate), 1),
        "confidence_label": trade_analytics.confidence_label(resolved_count),
        "current_weights": current_accuracy_weights,
        "per_factor": accuracy_per_factor,
        "ranked_by_discrimination": [f["factor"] for f in ranked],
        "suggested_weights": _suggested_weights(accuracy_per_factor),
        "confidence_calibration": _confidence_calibration_bands(rows),
        "brier_skill_vs_market": brier_skill,
    }
    edge_base_rate = round(sum(1 for r in priced_rows if r["correct"]) / len(priced_rows) * 100, 1) if priced_rows else None
    edge = {
        "n": len(priced_rows), "base_rate": edge_base_rate,
        "mean_realized_edge_usd_per_contract": (
            round(sum(_edge_of(r) for r in priced_rows) / len(priced_rows), 4) if priced_rows else None
        ),
        "current_weights": current_edge_weights,
        "per_factor": edge_per_factor,
    }
    populations = {
        "accuracy_n": resolved_count, "accuracy_base_rate": overall_win_rate,
        "edge_n": len(priced_rows), "edge_base_rate": edge_base_rate,
    }
    return {
        "report": {
            "resolved_count": resolved_count, "accuracy": accuracy, "edge": edge,
            "populations": populations,
            "by_series": _series_win_rates(rows),
            "measurement_valid": measurement_is_valid(accuracy_per_factor, edge_per_factor),
        },
        "gated_reason": None, "resolved_count": resolved_count,
    }
```

**`by_series` added above — corrected 2026-08-31 catch-up review.** The pre-existing
`generate_calibration_report` (as of a same-day but unrelated commit, `5bb29be`, "expose
the series dimension") already returns `"by_series": _series_win_rates(rows)` at the
report's top level, documented at `services/whale_calibration/README.md:100` and
directly asserted by `tests/test_confidence_calibration.py:93`
(`result["report"]["by_series"]`). This task's original rewrite had no field for it
anywhere in the new `accuracy`/`edge`/`populations` shape — a real, silent regression of
a currently-shipped field the plan's own test suite would not have caught, since none of
Task 16's new tests asserted on it. Kept at the top level (not nested under `accuracy`)
to preserve the exact access path `report["by_series"]` every existing reader uses. The
regression guard test is already added to
`test_report_splits_into_accuracy_edge_and_populations` above.

  (`_edge_of(r)` factors out the same `payoff - unit_cost - taker_fee_per_contract`
  formula `_bucket_mean_edge` already computes per row — extract it as a tiny shared
  helper both functions call, rather than a third inline copy.) Note `per_factor`'s old
  flat key on the top-level report is gone — **the dashboard consumer is real and
  confirmed, not hypothetical (2026-08-31 adversarial review)**: `frontend/src/js/
  advisory-calibration.js:229-292` reads `r.per_factor`/`r.current_weights`/
  `r.overall_win_rate`/`r.confidence_label`/`r.suggested_weights`/
  `r.confidence_calibration` at the top level, and `s.per_factor.<name>` from history
  snapshots — update every one of those to the new `r.accuracy.*`/`r.edge.*` paths, same
  grep-and-fix pass Task 7 already established the pattern for. Also update
  `services/whale_calibration/calibration_history.py`'s `record_snapshot` (see Files
  above) — a second real consumer of the old flat shape, not just the frontend.

  Update the four callers: `main.py:431` (corrected 2026-08-31 adversarial review) → `confidence_calibration.generate_calibration_
  report(cc_rows, cc_cfg["min_resolved_signals"], cfg.get("whale_accuracy_weights"),
  cfg.get("whale_edge_weights"))`; and read `cc_result["report"]["accuracy"]["per_
  factor"]`/`["suggested_weights"]` wherever Task 12's code reads the old flat `per_
  factor`/`suggested_weights` keys (the `measurement_is_valid` call itself already moved
  inside `generate_calibration_report`, so Task 12's own explicit gate-check call at the
  `main.py`/`routes.py` write sites is now redundant with the report's own `measurement_
  valid` field — simplify those two sites to read `cc_result["report"]["measurement_
  valid"]` directly instead of re-calling `measurement_is_valid` themselves). Same
  updates in `services/whale_calibration/routes.py`'s two `_build_report` closures and
  `services/research/research.py:157-159`.

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Run `tests/test_confidence_calibration.py`, `tests/test_trading_gate.py`,
  `tests/test_main_scheduler_loops.py`, `tests/test_diagnostics.py` (Task 9's check reads
  `report["input_coverage"]`, still a top-level key, unaffected — confirm) in full.**
- [ ] **Step 6: Commit:** `feat: calibration report splits into accuracy/edge/populations; measurement_valid covers both scores (whale-confidence-scoring-remediation Task 16)`

---

## Phase 6 (not planned)

Real weight-value retuning — using Task 10's re-measurement plus Task 16's dual-
objective data, a human reviews and sets real `whale_accuracy_weights`/`whale_edge_
weights` values. Design §10's own table marks this "not designed in this document";
this plan does not invent tasks for it either. When it happens, it is a config value
change through this repo's normal live-tuning path, not a code change — no TDD task
shape applies.

---

## Self-review

**Spec coverage:** §3 (D1a) → Task 1. §4 (D1b) → Task 2. §5 (fabrication fixes, all
four sites + renormalization + the hard-paired filter fix) → Tasks 3–7. §6 (shared
diagnostic) → Tasks 8–9. §9/§10 Phase 3 (D4 gate) → Task 10. §7.1 (schema split) +
§7.2 (rename/signature) → Task 11. §9 (measurement_valid, both write paths) → Task 12
(brought forward, reasoned explicitly in that task). §7.4 (edge_score persistence) →
Task 13. §7.5 (Brier helper) → Task 14. §7.5 (`_bucket_mean_edge`) → Task 15. §7.5
(report split) + §9 (gate extension) → Task 16. §8 (D3, scope exclusions) → not
planned, matches the design's own deferral. §10 Phase 6 → not planned, noted above.

**Sequencing/dependency graph, explicit:**

```
Task 1 ──┬──────────────────────────────────────────► Task 15 (reuses _tied_run_size)
Task 2   │
         │
Task 1,2 ──► Tasks 3-7 (Phase 1) ──► Tasks 8-9 (Phase 2) ──► Task 10 (HARD GATE)
                                                                    │
                          ┌─────────────────────────────────────────┘
                          ▼
                     Task 11 ──► Task 12 (same-phase pair: schema split, then the
                          │        retarget + gate it makes necessary - no task may
                          │        land between them)
                          ▼
                     Task 13
                          │
                          ▼
              Task 14 ──► Task 15 ──► Task 16 (extends Task 12's gate to both scores)
```

No task in Phase 4 or 5 (Tasks 11–16) is schedulable before Task 10's checkbox is
checked with recorded evidence — stated as a plan-level constraint above, not only as
prose in Task 10 itself. As noted there too: this "HARD GATE" is enforced by whoever
executes the plan following the checkbox order, not by a test or CI check that would
catch a Task 11 started early — unlike Task 12's `measurement_is_valid`, there is no
code-level trip-wire here.

**Safety check:** no task's Files list touches `services/risk_manager.py`,
`services/kalshi_account_client.py`, or any `kalshi_account.trading_enabled`/
`POST /api/trading/enable` code path. `confidence_calibration.auto_apply_enabled`
is read (Task 12's gate check runs regardless of its value) but never set to `true` by
any task. Every schema change (Task 11's config keys, Task 13's `edge_score` column)
is additive; no task drops a column, deletes a row, or replaces a `data/*.db` file.

**Type/name consistency:** `data_status` values (`"ok"`/`"insufficient_variance"`/
`"contaminated"`) identical across Tasks 1, 15, 16. `measurement_is_valid` signature
and semantics identical across Tasks 12 and 16 (Task 16 only widens its call-site
arguments). `whale_accuracy_weights`/`whale_edge_weights` key names identical across
Tasks 11, 12, 16. `edge_score` field name identical across Tasks 11 and 13.

**Left for a human decision, explicitly not resolved by this plan:**
- **Task 12's sequencing deviation** (bringing `measurement_valid` forward from the
  design's literal Phase 5 slot into Phase 4, immediately after the schema split) is
  this plan's own reasoned call, not a design-doc instruction — flagged prominently in
  Task 12 itself and here, for a reviewer to confirm or override before execution.
- **Task 10's soak duration** is deliberately left to judgment at execution time (no
  fixed day/row-count target is set here, matching this repo's own "re-run against
  then-current data" precedent) — a human executing this plan decides when enough
  real post-fix history exists.
- **Task 7's grep-and-fix scope** is genuinely open until the grep runs; this plan
  does not pre-guess which `static/`/`frontend/`/`tools/` files need edits.
- **Phase 6** (real weight retuning) is not planned here at all, per the task brief's
  own instruction — a distinct, later, human-reviewed initiative.

**Placeholder scan:** no `TBD`/`TODO` remains; every code block above is grounded
against this session's direct reads of the current source (file paths and line numbers
cited throughout, current as of commit `06e4392`'s worktree state) — normal drift by
execution time is expected and each task's own Step 1/Step 3 instructs re-verifying
signatures before editing, not treating these citations as frozen truth.
