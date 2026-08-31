# `whale_confidence_weights` Remediation Design — Independent Review (Stage 4)

**Task:** Stage 4 of the 9-stage pipeline — independently verify Stage 3's design
(`docs/superpowers/specs/2026-08-30-whale-confidence-scoring-remediation-design.md`, commit
`b506188`) against the audit it implements
(`docs/superpowers/research/2026-08-30-whale-confidence-weights-factor-audit.md`, commit
`d81d56d`) and the actual current source. Not a re-litigation of the audit's own findings
(out of scope, per Stage 3's own framing) — a check of whether Stage 3's design is sound
enough to hand to Stage 5 (implementation plan) as-is.

**Method:** every claim below was checked against the live worktree source at the commit
this design was written against — `git log`, `grep -rn`, direct file reads, and one
independent Python re-derivation of the renormalization arithmetic. Nothing here is taken
on the design document's word alone; CLAUDE.md's "never guess, verify or falsify" applies to
this review as much as to the design itself.

## Verdict

**Needs targeted revision, not a rebuild.** Phases 0–2 (the measurement-instrument fix, the
fabrication fixes, the shared diagnostic) and the bulk of Phases 4–5 (the dual-score config
split, the schema addition, the report restructuring, the D3 exclusion) verify as sound,
evidence-traceable, and consistent with this repo's own conventions — safe for Stage 5 to
build on directly. **§9's `measurement_valid` safety gate — the design's own headline
protection against D4's named risk — has two concrete defects that make it unsafe to
implement as literally specified**, both found by tracing the actual call graph rather than
trusting the design's prose. Both are below. Everything else in this review is confirmatory.

## Finding 1 (blocking) — `measurement_valid` is wired to only one of two real write paths

§9 says the block goes "at its call site (the auto-apply route...)" — singular — and the
design's own "Touched files" section (which states it was "verified against current source,
not the audit's line numbers alone") names neither file involved. Tracing every real writer
of `whale_confidence_weights` via `grep -rn`, there are **two**, not one:

1. `main.py:453-459` (`_maybe_run_auto_apply`) — the automatic path, gated by
   `confidence_calibration.auto_apply_enabled` (default `false`). Calls
   `confidence_calibration.blended_weights_for_auto_apply(...)` then
   `config_store.update({"whale_confidence_weights": blended})`.
2. `services/whale_calibration/routes.py:115-155`
   (`POST /api/confidence-calibration/apply`, `apply_confidence_calibration_suggestion`) —
   the manual path, wired to the dashboard's "Apply" button
   (`frontend/src/js/advisory-calibration.js:113`,
   `fetchJSON('/api/confidence-calibration/apply', {method: 'POST'})`). Its own comment
   states it is "always available regardless of `auto_apply_enabled`" — confirmed by reading
   the route: it never checks that flag. It calls the *same*
   `blended_weights_for_auto_apply(...)` and the *same* `config_store.update(...)`.

Both call sites turn a `suggested_weights` blend (derived from `_bucket_win_rates`, exactly
the function D1a is fixing) into live config. §9's mechanism, as worded, describes gating
"the auto-apply route" — if an implementer reads that literally and patches only
`main.py`'s automatic path (the more obviously "auto-apply"-named one), the manual `/apply`
route stays wide open: a human clicking the dashboard's existing Apply button while
`measurement_valid: False` would still write a contaminated blend into
`whale_accuracy_weights`, with **no config flip required at all** — a more direct path to
exactly the outcome D4 warns about ("hardening the artifact into production") than the
automatic route the design's prose focuses on. This is not hypothetical: the button and the
unguarded route both exist and are already reachable today.

**What the design needs to state explicitly:** both `main.py:453-459` and
`services/whale_calibration/routes.py`'s `/apply` route must check `measurement_valid` before
calling `config_store.update(...)`, and both files belong in "Touched files." §11's
GitNexus-impact-check list should add this as a fourth required check — it currently lists
three edits requiring `impact`/`context`/`trace` and omits this one, which is itself part of
why an implementer following §11 mechanically wouldn't be forced to discover the second call
site.

## Finding 2 (blocking) — `measurement_valid`'s own definition can't distinguish what it needs to distinguish

§9 defines `measurement_valid` as `True` only when **none** of the report's
`_bucket_win_rates` calls "hit the boundary-in-tie guard" for any factor in either score's
factor set. But §3 explicitly specifies the contaminated-boundary case returns `{}` from
`_bucket_win_rates`, **"identical to today's `len(distinct_values) < _BUCKET_COUNT` early
exit"** — a deliberate reuse of the existing degenerate-return shape, stated as such.

That reuse is the right call for `_factor_report`'s own purposes (both cases legitimately
mean "can't report a gap for this factor"). It breaks `measurement_valid`'s purpose, because
the two cases it collapses need to be told apart there: "genuinely insufficient variance"
(benign, permanent, expected) is not the same fact as "contaminated tie" (the exact defect
D4 exists to gate against).

Concretely: `analyst_factor` and `block_trade_factor` are structurally single-valued —
confirmed by the audit as a full census, not a sample (§1.8: "All 95,355 rows carry exactly
0.5 — 1 distinct value"; §1.9: "a single group, `(0, 33,221,747)`" across the entire raw
trade table) — and both remain weight-0 members of `whale_accuracy_weights` in §7.1's own
provisional YAML. `_bucket_win_rates` will return `{}` for these two factors on **every**
future report, forever, via the *old* `len(distinct_values) < 3` branch — nothing to do with
tie contamination, nothing D1 can ever fix (there is no second value to discover). If
`measurement_valid`'s check reads "did `_bucket_win_rates` return `{}` for this factor" as
its proxy for "hit the guard" (the only signal the design gives it to read), it cannot
distinguish this permanent, benign case from real contamination — `measurement_valid` would
be `False` on every real report indefinitely.

This directly contradicts the design's own closing sentence in §9 ("Once D1... has actually
landed and no factor trips the guard on live data, `measurement_valid` becomes `True` again
on its own") and would make Phase 3's stated gate criterion (`measurement_valid: true` on a
live report) permanently unsatisfiable — the rollout table's own Phase 3 row can never pass
as written.

**What the design needs to add:** a way for the contaminated-boundary case to be
distinguishable from the insufficient-variance case at the point `measurement_valid` is
computed — e.g., `_bucket_win_rates` (or a thin wrapper) returning a reason
(`"ok"` / `"insufficient_variance"` / `"contaminated"`) alongside its buckets, with
`measurement_valid` only reacting to `"contaminated"`. This is a small addition, not a
redesign, but it is not optional — as specified, §9's gate either never engages (finding 1)
or never releases (finding 2), and a design that fails permanently-closed on its own
structurally-sparse factors is not the "becomes valid again on its own" behavior §9 claims
for it.

## Finding 3 (non-blocking) — the `blended_weights_for_auto_apply` citation overstates the precedent

§5 justifies the present/absent per-row renormalization by calling it "not a new idiom — it
is `blended_weights_for_auto_apply`'s own existing pattern... applied per-row instead of
per-config-update." Reading that function
(`services/whale_calibration/confidence_calibration.py:153-177`) directly: it takes
`current_weights` (every factor key always present), overlays `suggested_weights` on the
subset with real discrimination data, and renormalizes the sum over **every** key — factors
"left unchanged" still contribute their old value to the denominator; nothing is ever
dropped from the set.

§5.2's per-row formula does something different: `present`/`absent` factors are computed per
row, and `absent` factors are excluded from **both** the numerator and the denominator
(`sum(w[n] for n in present)`), so the surviving factors' weights get renormalized over a
genuine subset, not the full set with some values held constant. That is standard
missing-data-aware weighted averaging, and the formula as written in §5.2 is correct and
self-contained — this finding is about the citation, not the math. Calling it "the same
pattern" risks an implementer looking for reusable code in
`blended_weights_for_auto_apply` that isn't actually applicable (it operates on a
config-level weights dict, not a per-row factor-value dict, and its renormalization never
excludes a key). Recommend softening the claim to "the same underlying idea (renormalize
after some inputs go untouched/absent), a different operation over a different input shape"
rather than "not a new idiom."

## Finding 4 (non-blocking, connected to Finding 1) — the config-field-edit skill is relevant and unmentioned

`whale_confidence_weights` → `whale_accuracy_weights`/`whale_edge_weights` (§7.1) is exactly
the scenario `.claude/skills/config-field-edit/SKILL.md` exists for: "adding, renaming, or
restructuring a field in `config/settings.yaml` while the live dev server may have its own
concurrent tuning applied (dashboard Controls-panel edits, applied Advisory suggestions) that
must not be clobbered." Finding 1 establishes this field has exactly that shape — two live
write paths that apply calibration suggestions into it. §10's Phase 4 row doesn't reference
the skill. This is an implementation-plan-level gap, not a design-soundness one, but worth
naming now so Stage 5 doesn't rediscover it independently.

## Verified sound (no revision needed)

**1. Phase 0's materiality floor (`max(30, 0.005*n)`).** The 30-row minimum-sample-size
convention is real, not invented: `services/candidate_log.py:311`
(`population_gate_summary(min_samples: int = 30)`), `services/analytics/routes.py:81`
(`min_population_samples: int = 30`), and `min_resolved_trades_per_variant` defaulting to 30
in `services/research/research.py:171` and
`services/analytics/market_analyst_orchestrator.py:278`. `confidence_calibration.py`'s own
`_MIN_BAND_SIZE = 3` confirms the "small fixed floor below which a stat is noise" idiom
generally, as the design cites it (not claiming `_MIN_BAND_SIZE` itself is 30 — it isn't, and
the design doesn't say it is). Arithmetic check at current scale (n ≈ 95,355): the
proportional term dominates (0.5% ≈ 477, versus the 30 floor), and 477 correctly separates
the audit's two reference points — `depth_factor`'s incidental 2–6-row float ties stay well
under it (pass), while `agreement_factor` (24,357), `proximity_factor` (70,151),
`trend_factor` (61,186), `cluster_factor` (~48,361), `context_factor` (~9,058), and
`unusualness_factor` (~3,339) all clear it (fail, correctly).

**2. Phase 1's "no new column" claim.** Read `services/signal_log.py` directly:
`factors_json TEXT` and `raw_spread REAL` are both nullable with no `NOT NULL` constraint
(`_add_column_if_missing` calls at lines 88/99); `log_signal`'s `INSERT` passes
`json.dumps(factors) if factors is not None else None` and
`raw_context.get("spread")` straight through — a `None` value inside the `factors` dict
becomes JSON `null` without incident (`json.dumps` handles nested `None` natively), and a
`None` `raw_context["spread"]` becomes a real SQL `NULL`. The design's claim holds.
**The one real risk it flags is confirmed genuine, not overstated**: `confidence_calibration.py:94`
(`applicable_rows = [r for r in rows if factor_name in r["factors"]]`) is a key-presence
check, and line 95 sorts by `r["factors"][factor_name]` — once any row's value is honestly
`None` instead of the current fabricated float, `sorted()` will compare `None` against a
`float` and raise `TypeError` the first time it does. This is a real, currently-latent bug
the design correctly identifies and schedules as a same-commit hard dependency (§5.3, Phase
1's gate row) — not a gap in the design, a correct catch.

**3. `_price_dollars` reuse for the `raw_spread` fix.** `services/whalewatchers/
kalshi_trade_tape.py:114` confirms the function's actual signature
(`_price_dollars(trade: dict, key: str) -> float | None`) and its existing use at line 173
(`_prescan_count`). The design's proposed `_price_dollars(market, "yes_ask_dollars")` call
passes a market dict where the parameter is named `trade` — functionally identical (the
function only calls `.get(key)`, doesn't care about the dict's semantic type), a naming
mismatch worth a one-line note in implementation but not a defect.

**4. Phases 4–5's additive-schema claim.** `edge_score REAL` follows the exact
`_add_column_if_missing` idiom already used for `price`, `raw_spread`,
`raw_notional_usd`, etc. in the same file — genuinely additive, no migration, no backfill,
matches CLAUDE.md's rule precisely.

**5. The caught arithmetic error.** Read `config/settings.yaml:131-140` directly: the live
nine weights are exactly `0.0404, 0.0404, 0.0404, 0.2121, 0.1818, 0.1717, 0.3131, 0.0, 0.0`,
summing to `0.9999`, confirmed independently in Python — not `1.0`. The design's
renormalization divides by the actual sum of the seven retained values (`0.9191`,
independently recomputed and confirmed), producing `0.0440/0.2308/0.1978/0.1868/0.3407` —
matches §7.1's YAML to all four decimal places. The design's own note that this is *not* a
re-weighting toward the audit's residual gaps, only a membership change, is accurate — the
relative proportions among the retained five are unchanged from their current live values.

**6. Scope check — D3.** `grep -n "open_interest\|_slim_market\|_MARKET_FIELDS"` across the
whole design document returns exactly one hit, inside §8 (the deferral section). Nothing in
Phases 0–6 reads or assumes `open_interest_fp`'s availability; `depth_factor`'s Phase-1 fix
(the zero-volume `None` case) is independent of D3 and doesn't require it. Cleanly excluded.

**7. Safety posture.** `grep -n "trading_enabled\|risk_manager\|kill.switch\|paper_broker"`
across the design returns only the explicit non-goal/exclusion statements in §2 and §9's
own analogy ("the same 'a real gate, not a comment' standard `kalshi_account.trading_enabled`
already sets"). No code change anywhere in the document touches `services/kalshi_account_
client.py`, `services/risk_manager.py`, or `paper_broker.db`. `auto_apply_enabled` itself
stays `false` by default and untouched by this design — only its blast radius (finding 1/2)
needs fixing before the block it adds can be trusted.

## What Stage 5 should do with this

Proceed with Phases 0–2 and the bulk of 4–5 as designed — they verify cleanly against source.
Do not proceed with implementing §9 as literally worded: resolve findings 1 and 2 first (name
both write-path call sites explicitly; give `measurement_valid` a way to distinguish
contamination from structural sparsity), fold finding 4's config-field-edit skill reference
into the Phase 4 plan, and soften finding 3's citation. None of this reopens the audit or the
design's broader architecture — it is a fix to one section's specification, not a redesign.
