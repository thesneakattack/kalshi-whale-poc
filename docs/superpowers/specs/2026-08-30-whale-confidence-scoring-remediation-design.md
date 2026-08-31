# `whale_confidence_weights` — Scoring Remediation Design (Stage 3)

**Status:** proposed for user review. Stage 3 of a 9-stage delegated pipeline; the
investigation this design implements
(`docs/superpowers/research/2026-08-30-whale-confidence-weights-factor-audit.md`, commit
`d81d56d`, two review rounds folded in) is finalized and out of scope to re-litigate here.
Written headless (no live human in this stage) — every decision point below that the
investigation left open is resolved with stated reasoning rather than left as a question,
per this stage's own instructions.

**Evidence:** the factor audit above, §0–§7. Every finding cited below is that document's,
not re-derived.

**Revision note:** Stage 4's independent review
(`docs/superpowers/specs/2026-08-30-whale-confidence-scoring-remediation-design-review.md`,
commit `5e092d2`) found two blocking defects in §9's `measurement_valid` gate — it was wired
to only one of two real write paths, and its own definition could not distinguish
tie-contamination from permanent structural sparsity — plus two non-blocking findings. All
four are folded into this revision; §3, §5, §7.1, §7.5, §9, §10, §11 changed as a result. See
those sections for what changed and why; everything the review confirmed sound (Phases 0–2,
most of 4–5, the arithmetic correction, D3's exclusion) is unchanged.

**Touched files** (verified against current source, not the audit's line numbers alone):
`services/confidence_scoring.py`, `services/whale_calibration/confidence_calibration.py`,
`services/whalewatchers/kalshi_trade_tape.py`, `config/settings.yaml`'s
`whale_confidence_weights` section, plus two real call/write sites the audit's file list
didn't name but this design's wiring requires: `services/whale_stream/decision_bridge.py`
(the only caller of `signal_log.log_signal`, `:79-84`) and `services/signal_log.py`
(`log_signal`, `resolved_signals_with_factors`, `_bucket_win_rates`'s consumer — the
`signals` table schema). `services/diagnostics/diagnostics.py` gains one new check.
`services/config/config_bounds.py` is where the audit's `config_bounds.py:77-87` citation
actually resolves (`services/config/`, not `config/` — a path the audit stated informally;
noted here so the implementation plan doesn't go looking in the wrong directory). Two more
files, found resolving Stage 4's review: `main.py` (`_maybe_run_auto_apply`, the automatic
auto-apply write at `:453-459`, gated by `confidence_calibration.auto_apply_enabled`) and
`services/whale_calibration/routes.py` (`apply_confidence_calibration_suggestion`, the manual
`POST /api/confidence-calibration/apply` route, write at `:155` — wired to the dashboard's
existing Apply button, `frontend/src/js/advisory-calibration.js:113`, and reachable
regardless of `auto_apply_enabled`). Both call `blended_weights_for_auto_apply(...)` and then
`config_store.update({"whale_confidence_weights": ...})` — the design's original "the
auto-apply route" (singular) named only the first; §9 now gates both.

**Constraints honoured:** `mode: paper` stays the default; nothing here touches
`kalshi_account.trading_enabled`, `POST /api/trading/enable`, `risk_manager.py`, or any
kill-switch value. No `data/*.db` file is dropped, moved, or backfilled — every schema
change is additive (`_add_column_if_missing`, matching `services/signal_log.py`'s existing
idiom). No new engine is wired into `main.py`'s tick loop or `services/` from `tools/`, and
nothing here is a `tools/` change. Kalshi semantics stay inside `services/kalshi/` — this
design touches no Kalshi field parsing, only this app's own scoring/calibration math over
already-parsed values.

## 1. Why this is one design, not four

The audit's own D1–D4 are ordered by dependency, not by convenience: D4 states plainly that
no weight may move until D1 lands and everything is re-measured. That makes D1 a hard
prerequisite for D2 (§4.1's dual-score directive) and for the weight-value work D4 gates —
not a parallel workstream. This design is therefore one phased architecture (§10), not four
independent proposals: §3–§6 are D1 plus the shared diagnostic it produces as a byproduct;
§7 is D2's dual-score plumbing, seeded with provisional membership/weights that the D4
re-measurement gate (§6, §10 Phase 3) explicitly revisits before anything ships weight
values a human would rely on.

## 2. Non-goals (stated explicitly, per this repo's safety posture)

- **Real trading stays untouched.** `kalshi_account.trading_enabled`, the typed
  confirmation gate, `risk_manager.py`'s kill switch, and `paper_broker.db`'s persisted
  bankroll are not read, written, or reasoned about anywhere in this design.
- **No entry-gate retuning.** `strategy.entry_threshold`, `min_unit_cost`/`max_unit_cost`,
  `min_contracts`, and every other `strategy.*` field stay exactly as configured. Both new
  scores are *reported*; using either to retune a live gate is a separate, later,
  human-reviewed step — the same restraint `docs/superpowers/specs/2026-08-26-economic-
  strategy-remediation-design.md`'s D1 already established for its own banded-gate
  diagnostic, and consistent with `CLAUDE.md`'s "Surface strategy-tuning suggestions, don't
  auto-apply" standing rule.
- **No weight-value change ships in this design.** §7 seeds two provisional weight sets by
  mechanical migration (§7.1) — not by adopting any number the audit measured pre-D1. Real
  retuning is Phase 6 (§10), after re-measurement, and is explicitly not designed here (the
  audit's own numbers are pre-fix and D4-gated from being acted on).
- **No auto-apply of anything designed here.** `confidence_calibration.auto_apply_enabled`
  stays `false`; §6 adds a structural block against it ever firing on a contaminated
  measurement, it does not enable it.
- **D3 (replacing the volume-derived depth/context pair) is deferred**, not designed here —
  see §8.

## 3. D1a — the measurement instrument: boundary-in-tie predicate

`_bucket_win_rates` (`services/whale_calibration/confidence_calibration.py:67-116`) must
stop accepting a tertile split whose cut lands inside a materially large tied run. The
audit's own corrected predicate (§2) is "does the boundary value appear on both sides of the
cut, at each of the two cuts independently" — but flagged its own implementation caveat: a
bare `low == high` check flaps on `depth_factor`'s incidental 2-6-row float ties, which are
not the same failure as `agreement_factor`'s 24,357-row tie. The guard needs a materiality
floor, and the audit does not choose one — this design does:

**A boundary is contaminated when the tied run straddling it has `n >= max(30, 0.005 * total_n)` rows.**

Reasoning: 30 matches this codebase's own recurring minimum-sample-size convention (this
same module's sibling design, `2026-08-26-economic-strategy-remediation-design.md`'s D1,
uses `min_samples=30` for the same kind of judgment; `confidence_calibration.py`'s own
`_MIN_BAND_SIZE`-style gates use small fixed floors for the same reason: below it, a stat is
noise, not a finding). The `0.5%`-of-n proportional term is added because a fixed 30-row
floor stops being meaningful as `signal_log.db` grows past hundreds of thousands of rows — a
30-row tie at n=10M is genuinely immaterial in a way it is not at n=1,000. Both terms apply
(`max`, not `min`): a fixed floor protects small future datasets (a fresh series with only a
few hundred resolved signals), the proportional floor protects the current and future large
one. This is a proposed default, not re-derived from a distribution the audit measured — the
implementation plan's test suite should confirm it against a fixture reproducing exactly the
audit's own two reference points (`depth_factor`'s 2-6-row ties must NOT trip the guard;
`agreement_factor`'s 24,357-row tie MUST).

**Behavior when a boundary is contaminated, and why the return shape must say why:**
`_bucket_win_rates` still returns an empty bucket dict for `_factor_report`'s purposes when a
boundary is contaminated — "not enough clean variance to say" is still the right degraded
answer for the report table. But a bare `{}` is not enough information for §9's
`measurement_valid` gate, which must tell apart two conditions that both produce `{}` today:
"this factor is permanently, structurally sparse" (benign — `analyst_factor`/
`block_trade_factor` are single-valued across the *entire* raw trade table per the audit's
own full census, §1.8/§1.9, and will return `{}` via the pre-existing
`len(distinct_values) < _BUCKET_COUNT` branch on every future report forever, with no tie to
ever resolve) from "this factor's tertile cut landed inside a materially large tie" (the
actual, transient contamination this predicate exists to catch). Only the second should ever
gate anything downstream.

The fix: `_bucket_win_rates` — and the tertile-split-plus-contamination-check core it shares
with `_bucket_mean_edge` (§7.5), which reuses this same logic unchanged — returns a
`(buckets: dict, data_status: str)` pair instead of a bare `dict`, where `data_status` is one
of:

- `"ok"` — a real, uncontaminated tertile split; `buckets` is non-empty.
- `"insufficient_variance"` — `n < _BUCKET_COUNT` or `len(distinct_values) < _BUCKET_COUNT`
  (today's existing early exit, trigger condition unchanged); `buckets` is `{}`. Permanent and
  structural: a factor with fewer than three distinct values has nothing D1a, more data
  volume, or any future report can change about that fact if it is architecturally
  single-valued.
- `"contaminated"` — the boundary-in-tie predicate fired: a cut lands inside a tied run of
  `n_tied >= max(30, 0.005 * total_n)` rows (this section's own materiality floor); `buckets`
  is `{}`. Transient and data-quality-driven: the one condition D1a exists to catch, and the
  only one §9 reacts to.

`_factor_report` forwards `data_status` into its own return dict as a fourth key —
`{"factor": ..., "buckets": ..., "gap_pts": ..., "discriminates": ..., "data_status": ...}` —
so every caller building a `per_factor` list already has this distinction available without a
second pass over `rows`. Nothing else in this design (the report table, `_suggested_weights`,
`ranked_by_discrimination`) changes behavior based on it — they already treat `gap_pts is
None` as "nothing to report" regardless of which `data_status` produced it; only §9 reads
`data_status` itself.

**Test surface:** the existing `low_edge != high_edge`-only test (if one exists pinning that
predicate) is replaced, not supplemented — a design that leaves the old, insufficient
assertion standing alongside the new one gives false confidence that both matter. A fixture
per audit table row (§2) — `depth_factor` clean, the other six contaminated at at least one
boundary — is the direct regression test; it should assert `_bucket_win_rates` returns
`(real_buckets, "ok")` for `depth_factor` and `({}, "contaminated")` for the other six against
the *current* (pre-Phase-1) fabrication-still-present data shape, since D1a and the
fabrication fix (§4) are validated independently before either depends on the other. A second
fixture, added specifically for the `data_status` distinction above: a single-valued factor
(`analyst_factor`/`block_trade_factor`-shaped — one distinct value across every row) must
assert `({}, "insufficient_variance")`, never `"contaminated"` — this is the exact regression
Stage 4's review caught, where a permanently sparse factor's `{}` was indistinguishable from a
real tie.

## 4. D1b — date scoping and deterministic order

`resolved_signals_with_factors()` (`services/signal_log.py:600-613`) has no `ORDER BY` and
no time dimension, which the audit identifies as compounding D1a's contamination (row order
is unspecified-by-SQL-semantics rowid order in practice, and that order correlates with the
71%-to-40% win-rate drift over time). Two additive changes:

1. **Explicit `ORDER BY seen_at ASC`** on the query. This does not fix the tie contamination
   by itself (D1a does) — it makes the current behavior a *stated* property instead of an
   accident of SQLite's internal row storage, so a future schema change (a `VACUUM`, a
   migration) cannot silently reorder history out from under any code that assumes today's
   order.
2. **An optional `since_ts: float | None = None` parameter**, default `None` (today's
   full-history behavior, unchanged for the calibration gate's own "total resolved count,
   not recency" reasoning, which the audit does not dispute). This is the minimum needed to
   let a future report ask "does the honest gap look different in the last 30 days than over
   all history" without adding a general capture-health/era-tagging system — that system
   (windowed health tagging, `healthy`/`measurably_degraded`/etc.) is
   `2026-08-26-economic-strategy-remediation-design.md`'s D2, a different investigation's
   territory; duplicating it here would be scope creep this design explicitly declines.

## 5. Fabricated inputs — one bug class, four sites, one architectural fix

The audit's own framing is the right one to preserve: this is a single bug class
(`_price_dollars`'s idiom not yet applied at four sites), not four unrelated bugs. The
architectural question Stage 3 must answer that the audit leaves open: **once an input is
honestly absent, what does the weighted composite do with a factor that has no value for
this row?**

**Decision: the absent factor is excluded from the weighted sum and the remaining present
factors' weights are renormalized to sum to 1.0 for that row**, not replaced by any sentinel
value (0.5, 0.0, or otherwise). This mirrors the same underlying idea
`blended_weights_for_auto_apply` already applies elsewhere in this module
(`confidence_calibration.py:153-177`: renormalize after some inputs go untouched/absent) —
worth naming as precedent for the *shape* of this decision, though it is a different
operation over a different input shape: that function's renormalization is over a
config-level weights dict where every key is always present and an untouched factor still
holds its old value in the sum, while this section's is over a per-row factor-value dict
where an absent factor is dropped from the sum entirely, not held constant. The formula below
(§5.2) is correct and self-contained on its own terms regardless of that precedent. Rejected
alternative: keep the old default value for scoring purposes and only track absence
separately for the diagnostic. Rejected because the audit measured, for two of the four
sites, that the default is *not* neutral in outcome (`agreement_factor`'s 0.5 bucket wins
44.8%, below both real buckets — §1.5; the zero-volume `depth_factor` population wins 40.1%
vs 65.8% — §1.1) — continuing to blend a non-neutral fabricated value into a live score
after having proven it is not neutral would be shipping the same bug with better
documentation, not fixing it.

### 5.1 The four sites

| site | file:line | current fabrication | fix |
|---|---|---|---|
| `depth_factor` | `confidence_scoring.py:194` | `size / max(volume_24h, 1.0)` turns a real, reported 0 into a 1-contract denominator | when `market_volume <= 0`, `depth_factor = None` (undefined, not badly defined — a market with no reportable 24h volume has no depth ratio to compute, the same "absent, not invented" standard `_price_dollars` sets) |
| `raw_spread` | `kalshi_trade_tape.py:746` | `yes_ask = float(market.get("yes_ask_dollars") or price)` fabricates a 0.0 spread | `yes_ask = _price_dollars(market, "yes_ask_dollars")`; `raw_context["spread"] = None if yes_ask is None else round(max(yes_ask - price, 0.0), 4)`. **Not a scored factor** (`raw_spread` never entered `ConfidenceBreakdown`) — this fix is confined to `kalshi_trade_tape.py`'s `raw_context` capture, no `confidence_scoring.py` change needed here |
| `trend_factor` | `kalshi_trade_tape.py:177-194` (`_trend_factor`) | returns `0.5` when `momentum()` returns `None` | returns `None` instead; the caller passes `trend_factor=None` into `composite_confidence_breakdown` when absent, same as the other two |
| `agreement_factor` | `kalshi_trade_tape.py:696-700` | `0.5` when `recent_sides` is empty | `agreement_factor = None if not recent_sides else sum(1 for s in recent_sides if s == side) / len(recent_sides)` |

`raw_spread`'s fix needs no new column and no `signal_log.py` change: `log_signal` already
writes `raw_context.get("spread")` straight into the existing nullable `raw_spread REAL`
column (`signal_log.py:184`) — a `None` from the fixed call site becomes a real SQL `NULL`
with zero schema work. The other three flow the same way through the existing `factors_json`
TEXT column: `ConfidenceBreakdown.to_dict()` already serializes whatever Python value each
field holds, and `json.dumps({..., "depth_factor": None, ...})` produces valid JSON `null` —
**no new column, no new field, for any of the four.** This is the additive-schema idiom
taken to its logical minimum: the schema does not need to grow because the existing nullable
shape (`float | None` in Python, `TEXT` holding arbitrary JSON, `REAL` already nullable) was
already capable of representing "unknown"; only the *values written into it* were dishonest.

### 5.2 `composite_confidence_breakdown` signature and return-shape change

`services/confidence_scoring.py`:

- `agreement_factor`, `trend_factor` parameters become `float | None = None`... but changing
  the *default* would silently break every caller that still wants "no opinion, treat as
  neutral" for a case that is genuinely neutral rather than absent (the simulator has no
  `momentum()`/agreement concept at all — it is not "missing data," it never had a concept
  of trend/agreement to be missing). Resolution: keep the **default** at the current neutral
  values (`0.5`) for callers with no concept to offer at all (unchanged simulator behavior),
  but allow an explicit `None` to mean "a real provider looked and found nothing" — the
  caller (`kalshi_trade_tape.py`) is the only one that can tell those two cases apart, and
  now has a way to say so. `cluster_factor`, `analyst_factor`, `block_trade_factor` are
  **not** touched — the audit does not name them in this bug class (cluster's 0.0 default is
  established as intentionally informative, §1.6; analyst/block_trade are correctly-wired
  zero-data factors, §1.8/§1.9, not fabrications).
- `depth_factor`'s `None` case is computed *inside* the function (it already reads
  `market.get("volume_24h_fp")`), not passed in — no signature change needed for it, only a
  branch before the `1.0 - math.exp(...)` line.
- **New internal step, after all nine factor values are computed:** partition into
  `present = {name: value for name, value in factors.items() if value is not None}` and
  `absent = set(all_names) - set(present)`. `score = sum(w[n] * v for n, v in present) /
  sum(w[n] for n in present)` when `sum(w[n] for n in present) > 0`; falls back to `0.5`
  (documented "cannot be scored, treat as maximally uncertain," the same neutral value this
  function already uses for its own no-data defaults) in the degenerate case where every
  weighted factor is absent for one row — measured joint-absence rate is not established by
  the audit (only marginal rates per factor are), so this fallback's frequency is unverified
  and should be counted by the diagnostic (§6.1), not assumed rare.
- `ConfidenceBreakdown` gains no new fields for this part (§7 adds `edge_score` separately).
  `depth_factor`, `agreement_factor`, `trend_factor` become documented as `float | None` in
  its dataclass annotation, matching the values it now actually holds.

### 5.3 Consumers that must be updated to treat `None` as absent, not as a value

- `_bucket_win_rates`'s applicability filter (`confidence_calibration.py:94`) is currently
  `factor_name in r["factors"]` (key presence). Post-fix, the key is *always* present
  (`ConfidenceBreakdown.to_dict()` always emits all nine keys) but the value can be `None`.
  The filter must become `factor_name in r["factors"] and r["factors"][factor_name] is not
  None` — without this change, `sorted_rows = sorted(..., key=lambda r: r["factors"][name])`
  crashes the first time it compares `None` to a `float` (Python raises `TypeError` on that
  comparison), the first time any real row carries the honestly-absent value this whole
  section exists to produce. This is not optional cleanup, it is a hard dependency: shipping
  §5's fabrication fix without this one-line change breaks the calibration report outright
  on the very next resolved signal with an absent factor.
- Every other reader of `factors_json` that assumes every value is a `float` (dashboard
  display code, any future analysis script) needs the same `is not None` guard before doing
  arithmetic on a per-factor value. Not enumerated exhaustively here — this is an
  implementation-plan grep-and-fix task (`grep -rn 'factors\[' `/`factors_json` across
  `static/`, `frontend/`, `tools/`), flagged so it is not missed, not itemized file-by-file
  since a fresh grep at implementation time is more reliable than a list frozen now.

## 6. The shared fabricated-input diagnostic

One diagnostic, reused across the four sites (matching the audit's own disposition: "the
finding with the most reuse in it"), not four ad hoc counters.

### 6.1 Report-side: `GET /api/confidence-calibration/report`

`generate_calibration_report()` gains a new top-level `input_coverage` field, computed
directly from the same `rows` it already receives —
no new query, no new DB read:

```
input_coverage: {
  "depth_factor":     {"n": <resolved_count>, "absent_pct": <share where factors["depth_factor"] is None>},
  "trend_factor":      {"n": ..., "absent_pct": ...},
  "agreement_factor":  {"n": ..., "absent_pct": ...},
  "raw_spread":        {"n": ..., "absent_pct": <share of rows where raw_spread IS NULL>},
  "score_fallback_pct": <share of rows where the degenerate all-absent §5.2 fallback fired>,
}
```

`raw_spread` is included even though it is not a `ConfidenceBreakdown` field — it is read
from the same row dict `resolved_signals_with_factors()` already returns
(`raw_spread` is already one of its selected columns, `signal_log.py:612`). This is
deliberately **observational, not a pass/fail check** — the audit's own numbers show
absence rates of 23.9%/57.4%/64.2%/25.5% that are largely structural (a 15-minute market
genuinely has no 24h volume; a catalog-sourced market genuinely has no ask field), not
themselves defects once honestly encoded. Turning "24% of rows lack `depth_factor`" into an
alert would either fire permanently (useless) or need an arbitrary threshold the audit never
established (inventing precision it doesn't have). Value is in *visibility and trend*: a
sudden jump (a future regression reintroducing fabrication, a coverage collapse in
`market_history` snapshots) is visible by comparing successive reports, which the existing
`GET /api/confidence-calibration/status`/report polling and
`services/whale_calibration/calibration_history.py`'s point-in-time snapshots already support
with no new machinery.

### 6.2 Pipeline-side: `GET /api/quality/summary`

A new `check_confidence_input_coverage(cfg, since_ts=None, now=None) -> Check` in
`services/diagnostics/diagnostics.py`, registered in `run_offline()` alongside
`check_threshold_integrity`/`check_price_band_adherence`/`check_config_bounds` — same
`Check` dataclass (`status`, `detail`, `evidence`), same file, same registration pattern,
not a new module. `status` is `"ok"` whenever the report itself is available (this check
reports numbers, it does not judge them, per §6.1's reasoning) and `"unknown"` when
calibration is below its resolved-signal floor — reusing `generate_calibration_report`'s own
gating rather than re-implementing a sample-size check. This makes the new coverage numbers
visible from CLAUDE.md's own "Start investigations here" step 1, without requiring anyone to
know to poll the calibration-specific route.

## 7. Accuracy/edge dual-score architecture (D2)

### 7.1 Config schema: `whale_confidence_weights` splits in two

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

**Why these exact numbers, and why they are provisional:** `whale_accuracy_weights` is the
*mechanical* result of dropping `unusualness_factor` and `proximity_factor` from the current
live `whale_confidence_weights` (both `0.0404`, per §1.2/§1.3 — an identity and a
tie-invalid measurement respectively, neither belongs in the accuracy score) and
renormalizing the remaining seven keys' existing relative proportions to sum to 1.0 again —
dividing each by the **actual sum of those seven raw values, 0.9191** (verified with exact
decimal arithmetic, not `1.0 - 2*0.0404 = 0.9192`: the live config's nine weights already
sum to `0.9999`, not exactly `1.0`, from their own prior independent rounding, so the correct
renormalization denominator is the retained values' real sum, not an assumption that the
full set summed to exactly one). `0.0404/0.9191 = 0.0440`, `0.2121/0.9191 = 0.2308`,
`0.1818/0.9191 = 0.1978`, `0.1717/0.9191 = 0.1868`, `0.3131/0.9191 = 0.3407`. This is
deliberately *not* a
re-weighting toward the audit's residual gaps (§1's per-factor "honest" numbers) — doing
that now would be moving weight values before D1's re-measurement, exactly what D4
forbids; it only removes membership that both the identity argument (§1.2) and the
tie-invalidity argument (§1.3) settle independently of any re-measurement. `whale_edge_weights`
has no prior production value to carry forward (it is a new score) — seeded at equal thirds
among its three §4.1-table members (`unusualness_factor`, `depth_factor`, `agreement_factor`;
`proximity_factor` excluded, "no — negative on both" per the table; `context_factor`/
`cluster_factor` excluded, negative on edge; `trend_factor` excluded, "unmeasurable" until
its own coverage fix lands), matching this same file's own precedent for a first-shipped
weight set (`DEFAULT_WEIGHTS`' docstring: "ships with a reasoned value, gets recalibrated
once real data exists," the exact language already used for `cluster_factor`/`trend_factor`/
`analyst_factor`/`block_trade_factor` when each was first added). **Both weight sets are
Phase 6 material (§10)** — re-measured and very likely changed once D1's fix has enough
resolved signals behind it; nothing in this design treats them as final.

**Implementation-plan note:** this split is exactly the scenario
`.claude/skills/config-field-edit/SKILL.md` exists for — adding/restructuring a
`config/settings.yaml` field while the live dev server may have its own concurrent tuning
applied (dashboard Controls-panel edits, applied calibration suggestions). §9 establishes
this field has exactly that shape: two live write paths (`main.py`'s auto-apply, `routes.py`'s
manual `/apply` route) already apply suggestions into `whale_confidence_weights` today.
Stage 5's implementation plan should drive this migration through that skill rather than a
hand-rolled rename/split, so neither write path's in-flight config state is clobbered
mid-migration.

### 7.2 `services/confidence_scoring.py` changes

- `DEFAULT_WEIGHTS` → `DEFAULT_ACCURACY_WEIGHTS` (rename; a `DEFAULT_WEIGHTS = DEFAULT_ACCURACY_WEIGHTS`
  alias is not kept — `confidence_calibration.py:45`'s `from services.confidence_scoring
  import DEFAULT_WEIGHTS` is a controlled, small, listed call site, not a public API this
  design needs to keep compatible for external code). New `DEFAULT_EDGE_WEIGHTS` constant
  (§7.1's table). `_FACTOR_NAMES` in `confidence_calibration.py` (`tuple(DEFAULT_WEIGHTS)`,
  `:54`) must be re-derived per score (`tuple(DEFAULT_ACCURACY_WEIGHTS)` /
  `tuple(DEFAULT_EDGE_WEIGHTS)`) rather than assuming one shared factor list — the two scores
  no longer share the same factor set.
- `composite_confidence_breakdown(...)`'s `weights: dict | None = None` parameter is renamed
  `accuracy_weights: dict | None = None` (a real, breaking parameter-rename — every call
  site updates in the same commit, which is exactly the "multi-file edit to a money/strategy
  hot-path module" §11 requires a GitNexus impact check before) and gains
  `edge_weights: dict | None = None`, merged against `DEFAULT_EDGE_WEIGHTS` the same
  partial-override way `accuracy_weights` already merges against `DEFAULT_ACCURACY_WEIGHTS`.
- `ConfidenceBreakdown` keeps its `score` field name and meaning unchanged (the accuracy
  composite — no downstream consumer of `WhaleSignal.confidence`/`breakdown.score` needs to
  know a rename happened) and gains **`edge_score: float`**, computed identically in shape
  to `score` (§5.2's present/absent-aware weighted-and-renormalized sum) but over
  `edge_weights` and the edge score's own factor membership — **not including
  `proximity_factor`, `context_factor`, `cluster_factor`, `trend_factor`, `analyst_factor`,
  `block_trade_factor`**, which never receive an edge weight at all (absent from
  `DEFAULT_EDGE_WEIGHTS`, so their contribution is architecturally zero, not merely
  down-weighted). `unusualness_factor` — removed from the accuracy score's factor set
  entirely (§7.1) — is still *computed* (its raw 0-1 value is cheap, pure, and needed for the
  edge score) but no longer appears in `whale_accuracy_weights`, so it contributes nothing to
  `score`.
- `composite_confidence(...)` (the plain-float wrapper) is unchanged — it still returns
  `.score` only. No `composite_edge_score(...)` convenience wrapper is added: no current
  caller needs a bare edge float outside a `WhaleSignal` (YAGNI, per the brainstorming
  skill's own guidance) — add one if and when a real caller needs it.

### 7.3 `edge_score` is a 0-1 composite, not a dollar figure — and that distinction matters

`edge_score` is architecturally identical in shape to `score`: a weighted blend of the same
already-computed 0-1 factor values, real-time-computable at signal-creation time (every
factor it uses is available then). It is **not** the audit's `$/contract` realized-edge
number (`payoff - unit_cost - fee`, §4.1) — that quantity requires `correct`, which does not
exist until the market resolves, so it can never be a live per-signal field. Conflating the
two would be inventing a live dollar-prediction model the audit never built and never
validated. The two stay distinct and serve different purposes:

- **`edge_score`** (this section): a live, per-signal, 0-1 directional indicator, persisted
  alongside `confidence` for every new signal, available immediately.
- **realized `$/contract` edge** (§7.4): computed only retrospectively, over *resolved*
  signals, for calibrating whether `edge_score` (or any individual edge-assigned factor)
  actually tracks real dollar outcomes — the same relationship `confidence`/`score` already
  has to `_confidence_calibration_bands`' observed-vs-predicted comparison.

### 7.4 Persistence: one new column, no new database

`services/signal_log.py`: `_add_column_if_missing(conn, "signals", "edge_score", "REAL")`,
nullable (only real providers populate it, same convention as `factors_json`/`raw_context`
fields — simulator-sourced rows may leave it null if `whale_simulator.py`'s own scoring path
is not updated to call the new signature meaningfully; that call site's exact behavior is an
implementation-time decision, not designed further here, since the simulator's synthetic
data was never part of this investigation's evidence). `log_signal(...)` gains
`edge_score: float | None = None`, added to its `INSERT` column list and its `CREATE TABLE`.
The one real caller, `services/whale_stream/decision_bridge.py:79-84`, passes
`edge_score=signal.edge_score`; `WhaleSignal` (`services/confidence_scoring.py`) gains
`edge_score: float | None = None` alongside `factors`/`raw_context`, populated by
`kalshi_trade_tape.py` (and, if updated, `whale_simulator.py`) at signal-construction time
from `breakdown.edge_score`, same shape as how `confidence` is already populated from
`breakdown.score`. No `data/*.db` file is touched by anything but this one additive column.

### 7.5 `services/whale_calibration/confidence_calibration.py` report restructuring

`generate_calibration_report()`'s return shape splits into `accuracy` and `edge` sub-reports
that are never combined into one number, per the audit's own explicit design constraint
(§4.1: "Report them side by side, never as one figure, and never compare their magnitudes
directly"):

```
{
  "report": {
    "resolved_count": ...,               # unchanged — total resolved signals
    "accuracy": {
      "overall_win_rate": ..., "overall_win_rate_margin_pts": ..., "confidence_label": ...,
      "current_weights": <whale_accuracy_weights>, "per_factor": [...], "ranked_by_discrimination": [...],
      "suggested_weights": ..., "confidence_calibration": [...],   # same shape as today's report
      "brier_skill_vs_market": <see below>,
    },
    "edge": {
      "n": ..., "base_rate": ...,                          # the priced subset only — §7.6
      "mean_realized_edge_usd_per_contract": ...,
      "current_weights": <whale_edge_weights>, "per_factor": [...],   # $/contract gap per bucket, not win-rate gap
    },
    "populations": {
      "accuracy_n": <resolved_count>, "accuracy_base_rate": ...,
      "edge_n": <priced & resolved count>, "edge_base_rate": ...,
    },
    "measurement_valid": <bool, §9>,
  },
  "gated_reason": ..., "resolved_count": ...,
}
```

`edge.per_factor` needs a new bucketing function, `_bucket_mean_edge(rows, factor_name)` —
**reusing** §3's shared tertile-split-plus-contamination-check core (the same
`(buckets, data_status)` pair `_bucket_win_rates` returns, §3), not a second copy of the
boundary-in-tie logic, grouped instead by mean `edge = kalshi_fees.unit_cost(side, price) →
payoff - unit_cost - taker_fee_per_contract(unit_cost)` per bucket rather than win rate.
`edge.per_factor`'s entries carry `data_status` through the same way `accuracy.per_factor`'s
do — §9's `measurement_valid` reads both lists, and `unusualness_factor`/`depth_factor`/
`agreement_factor` (the edge score's only members) need this signal available here just as
much as the accuracy score's factors do. This is new code (the audit never designed a
general-purpose version of this, only ran the specific in-band numbers in §4.1 by hand) but
its shape is fully determined by reusing D1a's guard and the audit's own stated formula — not
a new idea being introduced here.

**`brier_skill_vs_market`** (D2's other explicit recommendation): `1 -
(composite_brier / market_price_brier)`, both computed over the same priced-subset rows
using the market's own unit cost and the composite score as the two predictors (mirrors
§0.2's table exactly). There is, verified by grep, **no existing shared Brier-computation
helper** — `market_analyst_agent/per_market.py:275-292`, and (by the audit's own citation)
`settlement_edge.py` and `index_feed/settlement_algebra.py`, each inline the same
`sum(e**2 for e in errors) / n` formula independently. "Reuse... don't re-derive" (D2) is
honored by extracting one small helper — `services/stats_power.py.brier_score(predictions:
list[tuple[float, bool]]) -> float`, alongside the existing `margin_of_error_pts` this same
module already imports — for `confidence_calibration.py`'s new use. **The three existing
inline copies are deliberately left untouched**: `settlement_edge.py`,
`market_analyst_agent/per_market.py`, and `index_feed/settlement_algebra.py` are outside
this design's stated scope, and retrofitting them to the new helper is an unrelated
refactor this design declines to fold in (CLAUDE.md/brainstorming-skill: "don't propose
unrelated refactoring, stay focused on what serves the current goal").

### 7.6 The population mismatch is a historical-data problem, not a live-data gap

The audit's §4.1 population table (accuracy 100%/59.6%, edge 65.7%/52.1%) describes
**historical** rows: 34.3% of `signal_log.db` predates the `price` column and can never
retroactively gain one. Going forward, every new real signal *always* carries `price` (it is
read directly off the live trade print, before any signal is constructed) — so
`edge_score` populates on 100% of new signals, same coverage as `score`. The population gap
is real but bounded to backward-looking calibration (§7.5's `edge.n`/`populations` block,
computed only over already-resolved history) and does not recur for anything logged after
this design ships. Stated explicitly here because it is easy to misread the audit's 65.7%
figure as a permanent structural ceiling on edge-score coverage — it is not.

## 8. Deferred: D3 and other explicit scope boundaries

**D3 (replacing the volume-derived `depth_factor`/`context_factor` pair with open interest
or book-depth-at-touch) is not designed here.** It is real, ranked third by the audit, and
this stage's own brief lists what must be covered — the D1 fix, the shared diagnostic, the
dual-score architecture, and a D4-respecting rollout — without naming D3. Two reasons beyond
that instruction to actually defer it, not merely omit it by oversight:

- D3's own lead candidate (open interest, N3) needs a real code change first
  (`open_interest_fp` added to `market_watch/market_fetch.py`'s `_MARKET_FIELDS`
  allowlist, pinned by `tests/test_kalshi_contracts.py`) before it can be measured at all —
  a genuine data-plane change with its own review, not a scoring-formula change. Bundling it
  into this design would mix a Kalshi-integration-boundary change into a scoring-remediation
  spec.
- `depth_factor`'s accuracy-score membership is already resolved for this design's purposes
  without D3: §5's fix makes it *honest* (no more fabricated 1.0), which is what unblocks
  its Phase 3 re-measurement (§10) and lets `whale_accuracy_weights` decide its real weight
  based on real data. Whether it is later *replaced* by a better metric (D3) is a separate,
  follow-on design question this document explicitly flags rather than silently folds in.

**Also explicitly out of scope, considered and rejected the same way the sibling economic-
strategy design rejected its own D-adjacent ideas:**

- Using `edge_score` or the new `edge` calibration report to auto-retune
  `strategy.entry_threshold`/`min_unit_cost`/`max_unit_cost` directly. Rejected: a live
  strategy-tuning decision on protected economic parameters, same restraint as §2.
- Deleting `unusualness_factor` outright. Rejected explicitly by the audit (§4.1) and not
  reopened here — it moves score, it is not removed.
- Inverting any factor's sign. Rejected by the audit twice, independently, for
  `unusualness_factor` (§1.2, §4.1) — not reopened, and no other factor's inversion is
  considered anywhere in this design.
- A general capture-health/era-tagging system for `resolved_signals_with_factors()`.
  §4's `since_ts` parameter is the minimum needed; the general system is
  `2026-08-26-economic-strategy-remediation-design.md`'s D2, not duplicated here.

## 9. `measurement_valid` — the structural D4 safeguard

D4 names a specific, non-hypothetical risk: `auto_apply_enabled` (currently `false`) could,
if ever flipped, raise `context_factor` to 0.34 (hardening the zero-volume artifact) and
simultaneously nearly halve `trend_factor` (0.3131 → 0.1818) off a contaminated measurement.
A written warning against this is necessary but not sufficient — a future session enabling
auto-apply for an unrelated reason would not know to re-read this design first. This design
adds a mechanical block instead — at **both** of `whale_confidence_weights`'s real write
paths, not one (Stage 4's review found the original wording named only one; both are listed
below and in "Touched files").

`generate_calibration_report()`'s `report` gains `"measurement_valid": bool` — `True` only
when **none** of the current report's per-factor entries, across both `accuracy.per_factor`
and `edge.per_factor` (§7.5), carry `data_status == "contaminated"` (§3's three-way
`"ok"`/`"insufficient_variance"`/`"contaminated"` distinction). A factor parked permanently at
`"insufficient_variance"` — `analyst_factor`/`block_trade_factor`, structurally single-valued
per the audit's own full census, §1.8/§1.9 — never blocks this: it has no tie to detect and no
future report can change that, so it is a benign, permanent condition, not a measurement
defect. Only `"contaminated"` (a tertile cut landing inside a materially large tie, §3's own
guard) does. This is the distinction §3 exists to compute, and the reason its return shape
needed to say more than empty-vs-not.

`blended_weights_for_auto_apply(...)` (`confidence_calibration.py:153-177`) gains a required
check at **both** of its real call sites — not buried inside the pure function itself,
consistent with this module's existing "the caller writes config, this module never writes it
itself" boundary:

1. `main.py:453-459` (`_maybe_run_auto_apply`'s automatic path), itself already gated behind
   `confidence_calibration.auto_apply_enabled` (default `false`).
2. `services/whale_calibration/routes.py`'s `POST /api/confidence-calibration/apply`
   (`apply_confidence_calibration_suggestion`, write at `:155`) — the manual path wired to the
   dashboard's existing Apply button (`frontend/src/js/advisory-calibration.js:113`). By its
   own comment this route is "always available regardless of `auto_apply_enabled`" — it
   checks no flag at all today, which makes it the *more* direct path to D4's named risk: one
   human click writes a contaminated blend into `whale_accuracy_weights`, no config flip, no
   cooldown, no confirmation phrase required.

Both refuse and log a `fault_log` entry (same idiom each site already uses for its own
applied-change logging, `source` distinguishing `"calibration-auto-apply"` vs
`"calibration-manual"`) when `measurement_valid` is `False`, regardless of what
`auto_apply_enabled` says or which of the two paths a human or the tick loop reaches. §11's
GitNexus-impact-check list requires an impact check on both sites for exactly this reason —
the review noted that a list naming only one site would let an implementer discover the
second the same way the review did, by accident. This makes "don't apply `suggested_weights`
until D1 lands and re-measurement confirms" (D4) enforced by the code path itself at every
write path, not only by this document's own instruction or by whichever call site an
implementer happens to patch first — the same "a real gate, not a comment" standard
`kalshi_account.trading_enabled` already sets for real trading.

Once D1 (§3, §4) has actually landed and no factor's cut lands inside a materially large tie
on live data, `measurement_valid` becomes `True` again on its own — no manual flag to flip, no
config value to remember to change back, and (per the `data_status` distinction above) no
false-forever state from `analyst_factor`/`block_trade_factor`'s permanent single-valuedness.
As literally specified against a bare `{}` check, `measurement_valid` could never turn `True`
on real data — those two factors return `{}` via the benign, permanent branch on every future
report, indistinguishable from real contamination without `data_status`. The three-way
distinction is what makes "becomes valid again on its own" an actually true claim rather than
an aspiration the mechanism couldn't deliver.

## 10. Phased rollout

Each phase ships independently reviewable, is soaked before the next begins (matching this
repo's own realtime-remediation precedent), and nothing before Phase 3 changes any weight
value or any live-visible score.

| Phase | Change | Gate |
|---|---|---|
| 0 | D1a: boundary-in-tie predicate + materiality floor + three-way `data_status` return in `_bucket_win_rates` (§3); D1b: `ORDER BY seen_at`, optional `since_ts` on `resolved_signals_with_factors()` (§4). Report-only — no weight, no score, no schema change | fixture test: `depth_factor` returns `(real_buckets, "ok")`, the other six contaminated factors return `({}, "contaminated")` on current (pre-Phase-1) data; a single-valued-factor fixture returns `({}, "insufficient_variance")`, never `"contaminated"` (the defect-2 regression); the superseded `low_edge != high_edge`-only test is removed, not left standing |
| 1 | Fabrication fixes at all four sites (§5.1); `composite_confidence_breakdown`'s present/absent renormalization (§5.2); `_bucket_win_rates`'s `is not None` filter fix (§5.3, hard dependency on this phase, ships in the same commit) | unit test per site asserting `None` flows through instead of the old sentinel; renormalization unit test (synthetic all-but-one-present case); spot-check (read-only) that freshly-logged real signals now carry real `null`s where expected |
| 2 | Shared diagnostic: `input_coverage` on the calibration report (§6.1); `check_confidence_input_coverage` folded into `run_offline`/`GET /api/quality/summary` (§6.2) | new report field present and populated; new `Check` appears in `/api/quality/summary`'s output with `status: ok` once calibration is ungated |
| 3 | **Re-measurement (D4's blocking gate).** No code change — re-run `generate_calibration_report()` against real accumulated post-Phase-1 history; confirm the honest, tie-safe per-factor gaps land in the same order of magnitude as the audit's own corrected numbers (agreement +7.4–8.9, depth residual ≈ −17.4 before any further fix, trend's with-vs-neutral gap ≈ +30 once `momentum()` coverage allows a clean split, etc.) | `measurement_valid: true` on a live report over real data; the specific numbers are recorded, not merely "looks plausible" |
| 4 | Dual-score plumbing: config schema split (§7.1, via the `config-field-edit` skill given the two live write paths named in §9), `confidence_scoring.py` signature/return-shape change (§7.2), `edge_score` persistence (§7.4). `score`/`confidence`'s value and meaning are unchanged for every existing consumer | unit tests for the new signature (renamed/added params, `edge_score` field); live signals populate `edge_score` with no dashboard-visible change to `confidence` |
| 5 | Calibration report split (§7.5): `accuracy`/`edge`/`populations` sections, `_bucket_mean_edge` (reusing §3's shared tie-check core), `brier_skill_vs_market` (via the new `stats_power.brier_score` helper), `measurement_valid` wired to §9's block at **both** write paths | report shape matches §7.5, including each per-factor entry's `data_status`; an integration test confirms **both** write paths refuse independently when `measurement_valid` is `False` (`main.py`'s auto-apply, with `auto_apply_enabled: true` forced so the test proves this block fires even when that other gate is open; `routes.py`'s manual `/apply` route, called directly, since it reads no such flag); a further test confirms a report where the only non-`"ok"` factors are `"insufficient_variance"` yields `measurement_valid: true` |
| 6 | **Weight-value retuning (not designed in this document).** Using Phase 3's re-measurement plus Phase 5's dual-objective data, a human reviews and sets real `whale_accuracy_weights`/`whale_edge_weights` values — the audit's own numbers, honestly re-measured, inform this but this design does not pre-decide it | human-reviewed config change, same discipline as any other live strategy tuning; explicitly not `auto_apply_enabled` |

Phases 0–2 can ship together as one initiative-branch (they share no dependency ordering
risk and are individually small); Phase 3 is a pure verification step with no code; Phases
4–5 are naturally one branch (the dual-score plumbing and its reporting are tightly
coupled); Phase 6 is its own later, human-gated change. This mapping is a recommendation for
the implementation plan, not a constraint this design enforces.

## 11. GitNexus impact-check requirement (for the implementation plan, not this stage)

`services/confidence_scoring.py`, `services/whale_calibration/confidence_calibration.py`,
`services/whalewatchers/kalshi_trade_tape.py`, `main.py`, and
`services/whale_calibration/routes.py` are all on `CLAUDE.md`'s money/strategy hot path (the
last two by virtue of being the two real writers of `whale_confidence_weights`/
`whale_accuracy_weights` identified in §9). Per this repo's own standing toolchain rule, the
implementation plan must run `mcp__gitnexus__impact` (or `context`/`trace`) on each of the
following before making the corresponding multi-file edit, not after: (a) the `weights` →
`accuracy_weights` rename and `DEFAULT_WEIGHTS` → `DEFAULT_ACCURACY_WEIGHTS` rename (§7.2 — a
real signature/name change with multiple call sites: `kalshi_trade_tape.py`,
`whale_simulator.py`, `confidence_calibration.py`, and the test suite); (b) `_bucket_win_
rates`'s applicability filter change and its return-shape change to `(buckets, data_status)`
(§5.3, §3 — every reader of its return shape, including `_factor_report` and the new
`_bucket_mean_edge`); (c) `log_signal`'s new parameter and the `signals` schema addition
(§7.4 — every writer and reader of that table); (d) **both** `measurement_valid` call sites
added by §9 — `main.py:453-459` and `services/whale_calibration/routes.py`'s `/apply` route.
This fourth item exists because Stage 4's review found the design's original wording
undercounted these to one call site; an implementation plan following this list mechanically
must not be able to repeat that mistake. This is noted here as a requirement the plan must
satisfy; running the checks themselves is implementation work, not design work, and is not
performed in this stage.

## 12. Testing strategy (design-level, not test code)

- **D1a/D1b (§3-§4):** pure-function fixture tests against synthetic tied/untied data —
  no live DB needed, matching this module's existing test style.
- **Fabrication fixes (§5):** each of the four sites gets a focused test asserting the
  honest-absence behavior (a zero-volume market yields `depth_factor is None`; an
  ask-less market yields `raw_context["spread"] is None`; no momentum yields `trend_factor
  is None`; no recent prints yields `agreement_factor is None`), plus the renormalization
  unit test and the degenerate-all-absent fallback test (§5.2).
- **Dual-score (§7):** signature tests for the renamed/new parameters; a persistence test
  (`monkeypatch DB_PATH` to a tmp path, per this repo's standing convention — `log_signal`
  with `edge_score` set, read back, assert round-trip); a report-shape test for the new
  `accuracy`/`edge`/`populations` split.
- **`measurement_valid` (§9):** an integration test that a report built from a fixture with a
  known contaminated boundary produces `measurement_valid: False`, and that **both** write
  paths are refused (not merely that the pure function returns something falsy) — `main.py`'s
  auto-apply call, with `auto_apply_enabled: true` forced in the test fixture so the test
  proves the block fires even when the *other* gate is open; and `routes.py`'s manual
  `/apply` route, called directly with `auto_apply_enabled` left at its default, since that
  route reads no such flag at all. A second test: a report fixture where the only non-`"ok"`
  factors are `"insufficient_variance"` (single-valued, `analyst_factor`/`block_trade_factor`-
  shaped) asserts `measurement_valid: True` — the regression Stage 4's review caught, where a
  permanently sparse factor's `{}` return was indistinguishable from real contamination.
- All new/modified tests follow this repo's existing `monkeypatch(DB_PATH)`/read-only-or-
  set-confirm-revert conventions for anything touching `data/*.db`; none of this design's
  test surface writes to a live database.

## 13. Rollback

Every phase's schema addition is additive and inert when unused (an unread `edge_score`
column, an unpopulated `input_coverage` report field cost nothing to leave in place). Phases
0-2 and 4-5 are each revertible by reverting their commit(s) — no data migration, no
backfill, nothing destructive to undo. Phase 3 has no code to roll back. Phase 6 is a config
value change, reversible the same way any `config/settings.yaml` edit already is (the file
is live-reloadable and version-controlled). No phase drops a column, deletes a row, or
replaces a `data/*.db` file.

## 14. Spec self-review

**Placeholder scan:** no `TBD`/`TODO` remains; every provisional number (§7.1's weight
values, §3's materiality-floor constants) is labeled provisional in its own text with the
phase (Phase 6, Phase 0 test suite) that revisits it, not left as an open blank.

**Internal consistency:**
- §5.2 and §7.2 both modify `composite_confidence_breakdown`'s internals — checked they
  don't conflict: §5's present/absent renormalization runs first (over whichever factor set
  a given score uses), §7's dual-score split determines *which* factors and weights feed
  that renormalization for `score` vs. `edge_score` respectively. Applied together correctly:
  `edge_score`'s renormalization only ever considers `unusualness_factor`/`depth_factor`/
  `agreement_factor` (§7.2's edge factor set) — `depth_factor`'s §5 absence handling applies
  identically inside that smaller set.
- §6.1's `input_coverage` is specified against the *current* (pre-dual-score) factor names;
  confirmed it does not need a Phase-4 update, since all four fabrication sites (`depth`,
  `trend`, `agreement`, `raw_spread`) exist in both the accuracy and edge factor sets or
  outside scored factors entirely (`raw_spread`), so the diagnostic's meaning is unaffected
  by the Phase 4 split.
- §9's `measurement_valid` is defined in §9 (D4 discussion) before its report-shape
  appearance in §7.5's example JSON — reordered mentally but left in this document order
  deliberately: §7.5 is where it's *seen* in the report, §9 is where it's *justified*; a
  forward reference in §7.5 to "§9" makes the ordering unambiguous rather than requiring a
  reorder of two already-large sections.
- §3's `data_status` (per-factor, report-wide: "can this factor's tertile split be trusted at
  all") and §5.2's per-row `None` (per-row, per-signal: "does this one row have a value for
  this factor") are checked to not be confusable despite both meaning some flavor of
  "missing" — they operate at different levels (one factor-and-report, one row-and-signal)
  and neither substitutes for the other: a factor can be `"insufficient_variance"` while every
  row that does carry it holds a real, non-`None` value (that is exactly
  `analyst_factor`/`block_trade_factor`'s actual shape — always present when scored, just
  never varying).

**Scope check:** this document covers one coherent architecture (D1's fix, the diagnostic it
enables, D2's dual-score plumbing, and the phased sequencing that respects D4) — decomposing
further would separate genuinely interdependent pieces (D2 cannot be seeded sensibly without
D1's factor-set decisions already settled in §7.1). D3 is the one candidate piece that could
stand alone and is explicitly deferred (§8) rather than folded in, keeping this spec to a
single implementation plan's worth of work (Phases 0-5; Phase 6 is intentionally a separate,
later, human-gated change).

**Ambiguity check, resolved inline during drafting rather than left standing:**
- "What does an absent factor do to the composite score" (not specified by the audit) —
  resolved in §5 (renormalize over present factors, matching existing `blended_weights_for_
  auto_apply` precedent) with the rejected alternative and reasoning stated.
- "Is edge_score a dollar figure or a 0-1 score" — resolved explicitly in §7.3, since the
  audit's own $/contract numbers could otherwise be misread as the field's literal units.
- "Does the edge-score population gap ever close, or is 65.7% a permanent ceiling" —
  resolved in §7.6 (it's a historical-data artifact, not a live-coverage limit).
- The materiality-floor threshold in §3 is stated as a concrete formula
  (`max(30, 0.005 * n)`) rather than "a reasonable floor," with its own two-sided
  justification and an explicit note that the implementation plan's test suite is what
  actually validates it against the audit's two reference points — avoiding both a vague
  requirement and a false claim of measured precision.
- "How many real write paths does `measurement_valid` need to gate" (Stage 4 review, Finding
  1) — resolved in §9: both `main.py:453-459` and `services/whale_calibration/routes.py`'s
  `/apply` route, named explicitly rather than described as "the auto-apply route" and left
  for an implementer to enumerate.
- "How does `measurement_valid` tell a permanently sparse factor apart from a contaminated
  one, when both return `{}` from `_bucket_win_rates`" (Stage 4 review, Finding 2) — resolved
  in §3: a three-way `data_status` (`"ok"`/`"insufficient_variance"`/`"contaminated"`) carried
  through `_factor_report` and `_bucket_mean_edge`, with `measurement_valid` (§9) reacting
  only to `"contaminated"`.
