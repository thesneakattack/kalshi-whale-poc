# Plan: config-tuning advisory engine (rule-based, disabled until data-qualified)

Direct request (2026-08-08): a real recommendation/advisory engine, built now
but kept inert until there's a trustworthy amount of resolved-trade data
behind it. This resolves two things at once:

- ROADMAP.md P2's still-open **"Config-versioned performance tracking"**
  item (the fingerprinting/persistence groundwork it describes but
  deliberately didn't build).
- The **"Explicitly not a recommendation engine"** line in the Active
  Position Management / Trading History item — that line stands for
  everything shipped *so far* (`services/trade_analytics.py`'s
  `compute_insights()`), not a permanent boundary. This plan is the
  follow-up that was intentionally deferred there.

Two decisions already made (asked directly, not assumed):

1. **Rule-based heuristic engine**, not ML. Deterministic, explainable
   statistics over resolved trades per config-variant — same spirit as
   `compute_insights()`, upgraded from hedged prose to concrete suggested
   values. An ML model was considered and rejected for now: needs a
   training pipeline this app doesn't have and far more resolved-trade
   volume than a paper-trading POC is likely to have soon, and would be a
   black box inside an otherwise fully-explainable, safety-first app.
   Revisit only once real trade volume is large.
2. **Opt-in auto-apply is in scope**, not just read-only recommendations —
   but as the highest-risk piece of this plan (it's the one part of the
   whole feature that can mutate `config/settings.yaml` with no human
   click in the loop), it gets its own extra confirmation gate, its own
   flag independent of the base feature, and a full audit trail. Build it
   last, after the read-only path has run for a while.

Everything here follows this codebase's existing idioms rather than
inventing new ones: one SQLite file per concern (CLAUDE.md's Persistence
idiom), `services/trade_analytics.py`'s "derive from existing data, no new
schema unless truly needed" philosophy, and the `kalshi_account.trading_enabled`
confirmation-phrase precedent (`main.py:1115-1131`) for anything that gates a
behavior change with real consequences.

---

## 1. Foundation: config-variant fingerprinting

**Problem today:** `trade_analytics.build_trade_history()` (`services/trade_analytics.py:47`)
derives closed-trade rows from `broker.trade_log`, but nothing tags a trade
with *which strategy config was active when it was opened*. `compute_insights()`
(`trade_analytics.py:171`) therefore blends results across every config the
user has ever run — a real regression if the user changes `entry_threshold`
today and the insight is silently averaging that against last week's
different value. This is exactly the gap ROADMAP.md's P2 item names.

**Fingerprint definition:** `cfg["strategy"]` minus `name` (`name` is always
`"follow_the_whale"` today, not a tunable knob — everything else in that
dict already is one). Using the whole subset rather than hand-listing
fields is deliberate: a newly added tunable strategy field automatically
joins the fingerprint with no code change here, which is the behavior you
want (ROADMAP's own field list — `entry_threshold`, `max_position_pct`,
`cooldown_sec`, `min_whale_winrate_pct`, `min_resolved_for_whale_filter`,
`live_markets_only`, `excluded_series`, all seven `auto_exit_*` fields,
etc. — is just "all of `strategy` except `name`" today anyway).

```python
def fingerprint(cfg: dict) -> str:
    subset = {k: v for k, v in cfg["strategy"].items() if k != "name"}
    canonical = json.dumps(subset, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
```

**New module `services/config_performance.py`**, new file
`data/config_performance.db` (same `_connect()`/`CREATE TABLE IF NOT EXISTS`
idiom as `signal_log.py`/`paper_broker.py`):

```sql
CREATE TABLE IF NOT EXISTS config_variants (
    fingerprint   TEXT PRIMARY KEY,
    config_json   TEXT NOT NULL,   -- the actual strategy subset, so a hash is never opaque
    first_seen_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS applied_changes (   -- see §4, audit trail for auto-apply
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    applied_at        REAL NOT NULL,
    config_path       TEXT NOT NULL,
    old_value         TEXT NOT NULL,
    new_value         TEXT NOT NULL,
    rationale         TEXT NOT NULL,
    trade_count       INTEGER NOT NULL,
    fingerprint_before TEXT NOT NULL,
    fingerprint_after  TEXT NOT NULL
);
```

Functions: `fingerprint(cfg)`, `record_variant(fp, cfg_subset)` (`INSERT OR
IGNORE`, so `first_seen_at` is only ever set once), `get_variant(fp)`,
`all_variants()`, `log_applied_change(...)`, `recent_applied_changes(limit,
offset)`.

**Tagging trades with the fingerprint active at entry** — the one genuine
design wrinkle: a position can stay open while the user tweaks config
(e.g. changes `take_profit_pct` mid-hold), so "which variant does this
trade's outcome belong to" needs a explicit answer. Resolved as: **always
the fingerprint active when the position was *opened***, for both the
entry row and its eventual close row. Rationale: `entry_threshold`/the
whale-filter gates/sizing are all decided once, at entry — that's the
decision a recommendation is actually about. Exit-config drift mid-hold
(user changes `stop_loss_pct` while a position from an older variant is
still open) is real but rare for a single user occasionally tuning knobs,
and is called out here explicitly rather than silently glossed over — same
honesty-over-false-precision pattern as this codebase's other "confirmed
directly, not assumed" notes. Not solved by this plan (would need a second
"exit-time variant" column and materially more complexity for a heuristic
advisory layer); flag as a known limitation in the module docstring.

Implementation:
- `services/paper_broker.py`: `Position` and `Trade` dataclasses each gain
  `config_fingerprint: str | None = None`. `trades` and `positions` tables
  each get the column via an **idempotent migration** (`PRAGMA
  table_info(...)` check + `ALTER TABLE ... ADD COLUMN` if missing) — not
  a bare `CREATE TABLE IF NOT EXISTS`, since `data/paper_broker.db` is a
  live file with existing rows (CLAUDE.md's "`data/*.db` files are live"
  note applies directly here; this needs to survive a real `ddev restart`
  against the current on-disk db without data loss, verified, not
  assumed).
- `open_position(..., config_fingerprint: str | None = None)` stores it on
  both the new `Position` and the entry `Trade`.
- `close_position()` reads `pos.config_fingerprint` (the position being
  closed already carries it) and stamps the close `Trade` with the same
  value — no new parameter needed there, and entry/close rows for one
  round-trip are guaranteed to match by construction.
- `services/strategy_engine.py`: `evaluate()` gains an optional
  `config_fingerprint: str | None = None` param, threaded straight into
  `self.broker.open_position(...)`. `strategy_engine.py` does **not**
  import `config_performance` itself — recording variants is orchestration,
  not a trade decision, so it stays out of "the only file that decides
  whether a signal becomes a trade" per that module's own docstring.
- `main.py`'s `trading_loop()` (`main.py:440`) computes
  `fp = config_performance.fingerprint(cfg)` once per tick (right where
  `cfg = config_store.get()` already happens, `main.py:442`), calls
  `config_performance.record_variant(fp, cfg["strategy"])` (cheap,
  idempotent — safe every tick), and passes `config_fingerprint=fp` into
  `strategy.evaluate(...)` (`main.py:589`).
- `services/trade_analytics.py`'s `build_trade_history()` row dict gains
  `"config_fingerprint": entry["config_fingerprint"] if entry else None`.
- Out of scope for v1, flagged explicitly: `ShadowTrader` gets the same
  treatment later if shadow-mode data turns out to be worth feeding into
  the same engine — shipping paper-only first keeps this bounded.

## 2. The recommendation engine — `services/advisory_engine.py`

Pure functions over `trade_analytics`-shaped rows (now carrying
`config_fingerprint`) plus `config_performance`'s variant metadata. No new
persistence beyond what §1 already added.

```python
def variant_summaries(rows: list[dict]) -> dict[str, dict]:
    """Groups rows by config_fingerprint, runs the existing
    trade_analytics.compute_summary() per group (reused, not
    reimplemented), and attaches each variant's actual config values from
    config_performance.get_variant()."""

def generate_recommendations(
    rows: list[dict], cfg: dict, min_resolved_trades: int,
) -> list[dict]:
    """The gated entrypoint - see §3. Returns [] whenever the current
    variant hasn't cleared min_resolved_trades, regardless of what the
    heuristics below would otherwise say."""
```

Two recommendation shapes, both concrete-value (not hedged-prose like
today's `compute_insights`), each still individually confidence-labeled
via the existing `_confidence_label()` (`trade_analytics.py:161`, reused
as-is — low/moderate/higher by sample size, not a statistical test):

**a) Within-variant heuristics** — same three families
`compute_insights()` already computes (confidence-bucket win rate →
`entry_threshold`; stop-loss/take-profit/auto-exit close-type outcomes →
their respective knobs; settled-vs-actively-managed win rate), but scoped
strictly to the *current* variant's own resolved trades (not blended
across every variant ever run, which is today's real gap) and upgraded to
emit an actual suggested value alongside the prose, e.g.:

```python
{
  "config_path": "strategy.entry_threshold",
  "current_value": 0.05,
  "suggested_value": 0.11,   # the confidence value separating the worst
                              # bucket from the best, not a fitted model -
                              # stays explainable
  "rationale": "Trades entered at 0.5-0.75 confidence won 38% vs 71% at "
               ">0.75 (n=23) under the current config.",
  "n": 23, "confidence_label": "moderate",
  "auto_apply_eligible": False,  # confidence_label != "higher"
}
```

Worked formulas for the two clearest cases (the rest — the `auto_exit_*`
weights, `stop_loss_pct`, `take_profit_pct` — follow the same
"pull the suggested value directly out of the observed data, don't fit a
model" shape, spec'd at implementation time rather than exhaustively here):
- `entry_threshold`: suggest the confidence value at the boundary between
  the worst- and best-performing buckets (already computed for the prose
  version) as the new floor.
- `take_profit_pct` / `stop_loss_pct`: suggest the current value adjusted
  by the average `left_on_table` / average overshoot-past-the-limit
  observed in that variant's own `take_profit`/`stop_loss` closes.

**b) Cross-variant comparison** — only fires once **two or more** variants
each individually clear `min_resolved_trades`. Compares aggregate win
rate / total realized P&L / avg P&L per trade between them, and for every
`strategy.*` field where the two variants' `config_json` actually differ,
recommends adopting the better-performing variant's value — never a field
that's identical between them (nothing to recommend there). Confidence
label uses the *smaller* of the two variants' N, so a comparison is never
more confident than its weakest side.

## 3. Safety gating — three independent layers

This is the part the request was actually about: **build it now, keep it
inert until the data justifies it.** Modeled directly on the existing
`kalshi_account.trading_enabled` pattern (CLAUDE.md's Safety invariants
section; `main.py:1115-1141`), which is this codebase's own precedent for
"a powerful, opt-in behavior change gated behind more than a single
boolean."

New `config/settings.yaml` section:

```yaml
advisory:
  enabled: false                       # layer 1
  min_resolved_trades_per_variant: 30  # layer 2 - tune once real data exists
  auto_apply_enabled: false            # layer 3a - separate flag, see below
  auto_apply_min_confidence: higher    # only "higher"-labeled recs qualify
  auto_apply_cooldown_sec: 86400       # at most one auto-applied change/day
```

**Layer 1 — global flag (`advisory.enabled`, default `false`).** Gates
whether `/api/advisory/*` returns real recommendation content at all vs. a
structured "disabled" response. A plain `PATCH` via the existing
`POST /api/config` (`main.py:1099`) is fine for this one — unlike
`trading_enabled`/`auto_apply_enabled`, flipping it on can't itself change
what the strategy does; it only changes what's *displayed*, since layer 2
still gates content underneath it regardless.

**Layer 2 — per-variant data threshold, enforced inside the engine, not
the UI.** `generate_recommendations()` (§2) checks
`resolved_count >= min_resolved_trades_per_variant` for the current
variant *before* computing anything, and returns `[]` (plus a machine-
readable reason) if not. This means there is no code path — UI toggle,
direct API call, whatever — that can surface an under-sampled
recommendation even with `advisory.enabled=true`. This is the concrete
mechanism behind "keep it disabled until the data threshold is reached":
it's a property of the function's return value, not a display-layer
choice. `GET /api/advisory/status` (§5) exposes progress toward this
threshold honestly even while gated, e.g. "current config: 18/30 resolved
trades" — informative without ever leaking premature advice content,
same pattern as the rest of this app's real-data-or-honest-fallback idiom.

**Layer 3 — opt-in auto-apply, the highest-risk piece, extra-gated:**
- Requires **both** `advisory.enabled=true` **and**
  `advisory.auto_apply_enabled=true` — two independent flags so auto-apply
  can never be reachable while the base feature is off.
- `auto_apply_enabled` can **not** be flipped through `POST /api/config` —
  add the same rejection guard `/api/config` already has for
  `kalshi_account.trading_enabled` (`main.py:1101-1109`). Instead:
  `POST /api/advisory/auto-apply/enable`, requiring a typed confirmation
  phrase (new constant, e.g. `ADVISORY_CONFIRMATION_PHRASE = "ENABLE AUTO
  CONFIG CHANGES"`), same shape as `EnableTradingBody`/
  `TRADING_CONFIRMATION_PHRASE` (`main.py:724-727`, `1115-1131`).
  `POST /api/advisory/auto-apply/disable` is always allowed, no
  confirmation — same asymmetry as `trading/disable` (turning the
  dangerous thing off is never the direction that needs friction).
- Even with both flags on, a recommendation only qualifies for auto-apply
  if its `confidence_label == auto_apply_min_confidence` (default
  `"higher"`, i.e. `n >= 15` per the existing `_confidence_label`
  thresholds) **and** its variant has cleared layer 2's threshold **and**
  the cross-variant/within-variant rationale isn't stale (recomputed fresh
  each check, not cached).
- `auto_apply_cooldown_sec` prevents thrashing: at most one auto-applied
  change goes live per cooldown window, and the trading loop applies at
  most one per tick (never multiple simultaneous config mutations racing
  `evaluate()`/`check_exits()` mid-tick).
- Every auto-applied change writes a row to `applied_changes` (§1) *before*
  calling `config_store.update(...)` — old value, new value, the exact
  rationale/n/confidence that justified it, and the fingerprint before and
  after. This is the accountability trail: auto-apply is the one place
  this feature silently changes live strategy behavior, so "what changed
  and why" must be reconstructable after the fact without relying on
  memory or logs.
- Recommendation on sequencing (not a hard requirement, since the user
  explicitly asked for auto-apply to be in scope): **build and ship layers
  1-2 plus the manual "Apply to config" button first**, let it run for a
  while against real usage, and build layer 3's actual `POST
  /api/advisory/auto-apply/enable` wiring as a distinct, separately-
  reviewed follow-up. The data model (`applied_changes` table, the two-flag
  design) is worth laying down now so it doesn't need a schema change
  later, but flipping the feature live is the one part of this plan worth
  a second look before it ships.

## 4. Manual apply path (always available, independent of auto-apply)

`POST /api/advisory/recommendations/apply` — body identifies which
recommendation (e.g. by a stable id: `hash(config_path + suggested_value +
fingerprint)`), re-validates it's still eligible (same threshold/staleness
checks as auto-apply minus the confidence-label floor), writes the same
`applied_changes` audit row, then calls `config_store.update(...)`. This is
the path layer-3's UI button (§6) hits — human clicks "Apply", same
end state as auto-apply, same audit trail, just with a click instead of a
background trigger. Never silently applies anything on its own initiative;
`trade_analytics.py`'s existing "never writes config" line stays true for
the *insights* panel — this is genuinely new, separate behavior, not a
quiet expansion of what compute_insights already does.

## 5. New API surface (`main.py`)

- `GET /api/advisory/status` → `{enabled, min_resolved_trades_per_variant,
  auto_apply_enabled, variants: [{fingerprint, first_seen_at,
  resolved_count, ready, is_current}]}`.
- `GET /api/advisory/recommendations` → `{recommendations: [...],
  gated_reason: str | None}` (e.g. `"current config has 18/30 resolved
  trades"` when withheld) — always safe to call regardless of flags; the
  gating lives in the engine, not the route.
- `POST /api/advisory/recommendations/apply` (§4).
- `POST /api/advisory/auto-apply/enable` / `.../disable` (§3, confirmation-
  phrase gated).
- `GET /api/advisory/applied-changes?limit&offset` — the audit log (§1/§3).
- `POST /api/config` gains one more rejected key, `advisory.auto_apply_enabled`,
  matching the existing `kalshi_account.trading_enabled` guard
  (`main.py:1101-1109`) in the same function.
- Every advisory route that mutates state calls `_bump_generation()`
  (`main.py:100`), same as every other mutating route.

## 6. UI (`static/index.html`)

Lives on the **existing Trading History tab** — that tab's whole stated job
(per its own ROADMAP entry) is "help inform how to change management
config, whale config, etc.," so this is an extension of it, not a new tab:

- The existing "Config Tuning Hints" panel (`history-insights`,
  `static/index.html:894`, rendered by `renderHistoryInsights` at
  `:2192`) stays exactly as-is — the permanent, always-on descriptive
  layer. Nothing here removes or waters that down.
- New "Advisory Recommendations" panel directly below it. Three states,
  matching layers 1-2:
  - `advisory.enabled=false`: a plain "Advisory recommendations are
    disabled" line plus a link to the Config tab toggle — no data shown,
    no teaser.
  - Enabled but under threshold: per-variant progress (e.g. "current
    config: 18/30 resolved trades needed"), pulled from
    `GET /api/advisory/status`.
  - Threshold cleared: recommendation cards (config path, current →
    suggested value, rationale, n, confidence label) each with an "Apply
    to config" button calling `POST /api/advisory/recommendations/apply`,
    with a confirm() prompt before firing (same lightweight-confirm
    pattern as the existing Danger Zone reset button,
    `static/index.html` Config tab).
- Config tab gets a new "Advisory Engine" section (parallel to "Position
  Management (Exits)" at `static/index.html:933`): `enabled` checkbox and
  `min_resolved_trades_per_variant` number input via the normal
  `/api/config` patch flow, plus an `auto_apply_enabled` control that is
  **not** a plain checkbox — a button opening a typed-confirmation-phrase
  prompt (same UX as the real-trading-enable flow) hitting
  `POST /api/advisory/auto-apply/enable`.
- Small persistent badge when `auto_apply_enabled` is true (not a full
  banner like `#real-money-banner` — no real money is at risk here, but a
  live strategy config silently able to change itself deserves more than
  zero ambient indication). Exact placement/styling is an implementation
  detail, not a planning decision.

## 7. Tests

- `tests/test_config_performance.py` (new): fingerprint stability (same
  subset → same hash regardless of dict key order; changing any one
  `strategy.*` field → different hash; `name` excluded); `record_variant`
  upsert idempotency (`first_seen_at` set once, not overwritten);
  `applied_changes` insert/read.
- `tests/test_advisory_engine.py` (new): per-variant grouping correctness
  against `trade_analytics`-shaped fixtures; layer-2 threshold gating
  (withheld below N, present at/above N, boundary at exactly N); the two
  recommendation families' suggested-value math on known fixtures;
  cross-variant comparison only firing when *both* sides clear the
  threshold; auto-apply eligibility as a pure function of
  (flags, confidence_label, threshold, cooldown) — every flag combination
  gets its own case, including "auto_apply_enabled=true but enabled=false"
  → never eligible.
- `tests/test_paper_broker.py` (extend): `config_fingerprint` flows from
  `open_position` through to `close_position`'s trade row unchanged even
  if `cfg` changes mid-hold; the idempotent migration adds the column to a
  pre-existing on-disk db (a fixture seeded with the *old* schema) without
  losing existing rows — this one matters given CLAUDE.md's live-db
  warning, verify with a real `ddev restart` too, not just the test.
- `tests/test_strategy_engine.py` (extend): `evaluate()` threads
  `config_fingerprint` into `open_position` correctly.
- New route tests (extend `tests/test_trading_gate.py`'s existing
  confirmation-phrase pattern, whatever it is today — check it before
  duplicating): the auto-apply confirmation endpoint and `/api/config`'s
  new rejection guard for `advisory.auto_apply_enabled`.

## 8. Build order

1. §1 foundation (fingerprinting, migration, wiring) — this alone already
   closes ROADMAP's P2 item, independently useful even before the engine
   exists (variant-scoped stats become inspectable via a debug endpoint).
2. §2 engine, read-only, plus §5's `status`/`recommendations` routes.
3. §6 UI, enabled-but-manual-apply-only (§4) — no auto-apply yet.
4. Run it for real, against real accumulating paper-trade volume, before
   touching §3 layer 3.
5. §3 layer 3 (auto-apply) as a distinct follow-up, once the manual path
   has proven the recommendations themselves are sound.

## 9. Future: an ML agent working alongside the heuristic engine (scaffolding only)

Direct request, added mid-build (2026-08-08): "eventually I want to be able
to feed this heuristic suggestion data, market data, historical data,
portfolio data, recommendation engine data, to a machine-learning agent
that will work WITH these things, not as a replacement... add some
scaffolding for that but let's not pursue that until the project is
already finished."

`services/ml_feed.py` is that scaffolding: one pure function,
`build_context_snapshot(cfg, portfolio, market_snapshot, trade_history_rows,
whale_track_record, advisory)`, that assembles a single JSON-serializable
bundle out of pieces every one of which is already produced by an existing
service (`PaperBroker.state()`, `trade_analytics.build_trade_history()`,
`signal_log.stats()`, `advisory_engine`'s own output) — nothing reshaped,
nothing re-derived. This is deliberately **not wired into any route** and
**not called from anywhere in the running app** — it exists so the shape of
a future export doesn't need to be reverse-engineered from five services
later, not to start that work now. `tests/test_ml_feed.py` is the only
thing that currently exercises it, specifically so it can't silently drift
out of sync with the shapes it wraps while nothing else calls it.

"Work WITH, not replace" is the framing that matters for whenever this
actually gets built: the rule-based `advisory_engine` recommendations are
one more input *into* the bundle (`advisory.recommendations`), not
something a future model silently overrides. When that work starts:

1. Decide whether the ML agent needs historical snapshots (would need real
   persistence — a new SQLite file, same one-file-per-concern idiom) or
   only ever needs the current point-in-time bundle (no persistence needed,
   `build_context_snapshot()` is enough as-is).
2. Add a route (e.g. `GET /api/advisory/ml-feed`) if a live consumer needs
   HTTP access to the bundle; skip it if the agent runs in-process.
3. Build the actual model/agent as a new module that *reads* this bundle —
   `ml_feed.py` itself should stay a dependency-free, model-free assembler
   indefinitely, the same way `trade_analytics.py` stayed pure description
   even after `advisory_engine.py` was built on top of it.

## 10. What this deliberately does not do

- No ML model — explicit decision, not a placeholder for later in this
  plan. §9's `ml_feed.py` scaffolding exists so the *data shape* a future
  model would consume is settled, not to start building the model itself.
- No `ShadowTrader` integration in v1 — paper-broker trades only.
- No change to `compute_insights()`'s existing behavior or its "never
  writes config" framing — that panel stays the permanent descriptive
  layer; this plan adds a distinct, clearly-labeled advisory layer next to
  it, not a replacement.
- Does not touch real-money trading (`kalshi_account.trading_enabled`) in
  any way — recommendations only ever target `strategy.*` fields, which
  govern paper-broker behavior regardless of live/real mode.
