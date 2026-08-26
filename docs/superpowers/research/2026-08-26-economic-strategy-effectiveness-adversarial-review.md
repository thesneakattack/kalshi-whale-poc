# Economic Strategy Effectiveness — Adversarial Review (E11)

Internal pass, not an external-model review — `second-opinion` and the Devil's Advocate
MCP are both `BLOCKED_EXTERNAL` per `.claude/rules/tooling-plugins.md` (no installed/
authenticated backing CLI) and prior session experience respectively; per that memory and
`.claude/rules/tooling-plugins.md`'s fail-open policy, this is done as a genuine internal
adversarial pass, not skipped and not silently downgraded to a rubber stamp. Each finding
from `docs/superpowers/research/2026-08-26-economic-population-and-replay-gaps.md` and
`docs/superpowers/research/2026-08-26-economic-gate-marginal-contribution.md` is attacked
directly: alternative explanation, what would falsify it, and — where cheap and safe to
do — an actual falsification attempt run against real data rather than left as a rhetorical
hedge.

## Attack on E1 (population staleness / 08-23 regime break)

**Claim under attack:** the pre/post-cutover accuracy break (85.9%→53.0% daily) is
attributable to the whale-gate definition change (dollar-notional → contract-count), not a
coincidence or a different confound (e.g. a real BTC volatility regime shift around the
same date).

**Attempt to falsify:** if the gate-definition mechanism is real, the pre-cutover
population should show a heavy skew toward near-certainty unit cost (since a dollar
threshold is easiest to clear at extreme prices, per the already-published 2026-08-17
measurement) and the post-cutover population should not. Queried `signal_log.db` directly
for the unit-cost distribution (side-adjusted `price`) of KXBTC15M signals on each side of
the cutover:

| | n | Mean unit cost | Share in 0.80-1.0 |
|---|---:|---:|---:|
| Before cutover | 866 | 0.858 | 75.5% (43.2% in 0.95-1.0 alone) |
| After cutover | 4,319 | 0.509 | 21.5% |

**Result: survives, and sharpens.** The skew is real and large — three-quarters of
pre-cutover signals sit in the near-certainty band. This adds a mechanism E1's original
text didn't spell out explicitly: a signal priced at 0.95+ is close to *tautologically*
likely to resolve toward the favored side regardless of whether the whale print behind it
carried any real directional information — a market already pricing 95%+ on one side rarely
flips. **The original 88.8% figure was measured on a population where "accuracy" is
substantially inflated by price-level concentration, not price-independent whale skill** —
a sharper and more damaging characterization of the original figure than "it's just old
data," and one this investigation would not have surfaced without directly checking rather
than trusting the cited mechanism secondhand.

## Attack on E2 (replay gap — "unrecoverable")

**Claim under attack:** the original 12-trade sample cannot be reconstructed from any
current data.

**Attempt to falsify:** the investigation plan's own text flagged `data/backups/` as
unchecked. Checked in this pass: `ls -la data/backups/` (read-only directory listing, no
file contents opened) shows 14 snapshot directories, **earliest timestamp
2026-08-25T21:50:18 UTC** — a full day *after* the last full `paper` reset
(2026-08-24T22:22:44) and 8 days after the original 2026-08-17 measurement.

**Result: claim survives.** The backup mechanism exists and is running, but its retention
window does not reach anywhere near far enough back to help — it does not even predate the
most recent reset, let alone the original measurement. E2's "unrecoverable" conclusion is
now checked, not merely asserted; this also surfaces a real, separate observation worth
flagging in the status report's guard-disposition section: **`data/backups/` currently
holds no snapshot from before 2026-08-25**, meaning any single-`ddev`-restart-adjacent data
loss before that date (of which this investigation found several: the 08-17, 08-18, and
08-24 `paper` resets) had no backup safety net at all. Whether backup retention/interval is
itself adequate is outside this investigation's scope (that's `services/backup/`'s own
concern), but the fact pattern is now on record rather than assumed away.

## Attack on E3 (capture-health trough, 08-18–08-22)

**Claim under attack:** the near-zero signal-density trough is genuinely ambiguous between
a capture-health problem and low real market whale activity, and cannot be resolved with
data already in hand.

**Attempt to falsify "the app was just down":** if the app were offline for most of that
window, active development (commits) should also show a comparable gap. `git log --all
--since=2026-08-18 --until=2026-08-23 --oneline` returns **45 commits** across all branches
in that window — active, continuous development, not a multi-day outage. This weakens (does
not eliminate) the "process was down" hypothesis specifically, but does not distinguish
between "real capture degradation while the process ran" and "real low whale activity" —
the two hypotheses E3 already left open. **Result: claim survives in its stated (honestly
unresolved) form** — this attack narrowed the hypothesis space without closing it, which is
itself useful and is recorded, not discarded because it didn't produce a clean answer.

## Attack on E4 (banded gate EV)

**Claim under attack:** the 0.60-0.95 unit-cost band shows genuine negative EV for rejected
candidates across `min_contracts`/`entry_threshold`/`min_unit_cost`, not an artifact of how
`unit_cost` is captured at rejection time.

**Alternative explanation not falsified — recorded as a real limitation, not attacked away:**
`unit_cost` is captured at the moment a candidate is evaluated and rejected, which is not
necessarily the price a real entry would have executed at (the same signal-to-entry lag
`series_watcher.reconcile()` measures elsewhere, mean 27.17 s in E5's post-cutover window).
A systematically-moving BTC 15-minute market means the rejection-time price and a
hypothetical entry price 20-30 s later are correlated but not identical — banding by
rejection-time `unit_cost` is a reasonable, already-existing-convention proxy (the same one
`record_rejection()`'s own docstring uses), not a perfect one. **Not falsified, because
falsifying it would require the very order-book replay capability E7/E9 already identified
as not yet buildable** — recorded as an open limitation on E4's precision, referenced again
in the status report's insufficient-sample list rather than silently assumed away.

**Second attack — sample size in the tail bands:** several KXBTC15M-specific `ready`
(`n>=30`) bands sit close to the 30-sample floor (`entry_threshold` 0.60-0.80: n=53; the
`0.80-0.95` band for the same gate: n=25, actually *below* the floor and correctly excluded
from the "ready" table). The headline pattern (negative EV in 0.60-0.95, positive in
0.20-0.40) is more securely established by the **all-series** version of the same table
(where the same gates show the same sign pattern at 6-7-figure sample sizes) than by the
KXBTC15M-only numbers alone — the research doc already presents both; this review confirms
the cross-series agreement is what makes the pattern credible, not the KXBTC15M numbers in
isolation.

## Attack on E5 (adverse-selection reversal)

**Claim under attack:** `selection_delta_pts` being positive under the current
configuration is a genuine reversal of the original finding's direction, not a 90-trade,
3.2-day statistical fluke or a mechanical artifact of how trades get selected.

**Mechanical-artifact check:** if the strategy can only hold one open position per ticker
(a real constraint — Kalshi 15-minute BTC markets settle and roll continuously, and
`position_netting`/dedup logic exists specifically to avoid duplicate/conflicting
positions), then when a ticker fires multiple signals before one is acted on, the strategy
mechanically has the opportunity to act on the *best* of them rather than a random one —
this alone would produce a positive `selection_delta` unrelated to any gate's individual
quality. This is a real, legitimate selection mechanism (not a bug), but it means
`selection_delta_pts` measures "the whole pipeline's net selection effect," not "each gate's
individual contribution" — consistent with how `reconcile()`'s own docstring frames it
("did my gates pick better or worse than average," plural, not attributed to one gate). Not
a flaw in E5's claim, but a scope clarification this review adds: E5 answers "is the
pipeline currently adversely selecting," not "which specific gate, if any, would be
adversely selecting on its own" — E4 is the (partial, gate-marginal-only) answer to the
latter question, and even E4 cannot yet answer it exactly because of E8's interaction gap.

**Sample-size check:** 90 trades over roughly 3.2 days is thin. A single volatile day or an
unusually whale-active stretch could shift `selection_delta_pts` substantially. This is
already flagged in the research doc and repeated in the status report's insufficient-sample
list — this review confirms it is a real, not merely pro-forma, caveat: the post-cutover
regime itself is only 3 days old as of this session, meaning **every post-cutover number in
this entire investigation (E3 through E7) describes a genuinely new, short-lived
configuration**, not a mature, stable one. Any remediation decision drawn from these numbers
should re-verify against a materially larger post-cutover sample before being treated as
settled — stated explicitly here rather than left implicit in "n=90."

## Attack on E6 (advisory/calibration objective audit)

**Claim under attack:** the entry/exit split (entry functions win-rate-only, exit functions
cost-aware) is complete and current, not stale relative to the CHEATSHEET it partially
reused.

**Attempt to falsify:** re-ran the grep directly against current `advisory_engine.py`
source in this pass (not trusted from the CHEATSHEET's prior text) — confirmed the same
split holds today. No function was found that contradicts the classification. **Result:
survives** — this is the one E1-E7 claim this review found nothing new to add to beyond
re-confirming it wasn't stale.

## Attack on E7 (execution realism)

**Claim under attack:** the mean depth ratio (8.44×) reflects genuinely healthy typical
liquidity, not an average dominated by a few very deep snapshots masking a fat thin-book
tail.

**Not resolved in this pass** — the research doc reports `entries_with_insufficient_depth:
12` (a count, i.e., the tail is already partially characterized: 16% of matched entries),
but the full depth-ratio distribution (not just mean and a <1.0 count) was not pulled. This
is a legitimate, cheap follow-up (the same `book_context_at_entry()`-style join, reporting
a distribution instead of a mean) that this review did not execute — recorded as a genuine
gap in E7's own precision, not glossed over.

## Scope check (execution program §9.D)

None of the above drifted into architecture redesign, config changes, or new framework
questions — every attack either used data already gathered or a cheap, safe, read-only
query against already-permitted sources (`data/backups/` directory listing, `git log`,
`signal_log.db`'s existing `price` column). No live config, no live DB write, no realtime/
remediation-branch file touched.

## Net effect on the investigation's conclusions

Two claims (E1, E2) came out of this review **strengthened** with new, directly-verified
evidence rather than merely surviving unchanged. One claim (E3) had its hypothesis space
narrowed without being resolved. Two claims (E4, E5) gained explicitly-recorded precision
limitations that did not overturn their headline direction but should gate how much weight
any remediation plan puts on the exact numbers. One claim (E6) was reconfirmed unchanged.
One (E7) has an acknowledged, unexecuted follow-up. No claim was reversed or withdrawn.
