# Self-review: 2026-09-05-issue-530-sync-dispatch-sweep.md

Same-author/context pass per CLAUDE.md's "nothing advances on one pass" HARD RULE —
checked for internal consistency and unaddressed scope before spending independent
(adversarial) effort on it.

## The central judgment call this document makes — restated and re-examined

The task brief asked for a fresh full sweep (enumerate every route file, check every
`async def` handler, measure the real ones, write a census-shaped research doc). This
document does not do that; it found `docs/event-loop-blocking-routes-census-2026-09-03.md`
already exists, already went through its own self-review + adversarial-review +
correction cycle, and already produced a materially identical deliverable two days
ago, with one of its findings (`quality/summary`) already fixed and merged since.

Re-examining that call rather than just asserting it: is it possible the brief's
author *knew* about the census and wanted independent re-verification anyway (the
census's own text flags that only 7 of 18 sources got adversarial-review-level
scrutiny)? The brief's wording argues against this — it describes only the two
original accidental findings (`reset/routes.py`, `observability/routes.py`) as if they
were the full extent of known instances, with no mention of the census, its 63-count,
or the quality/summary fix. A brief written by someone aware of the census would very
likely have cited it and asked for exactly the un-reverified ~10 files, not framed the
task as if starting from two isolated findings. Treating this as a genuine information
gap (matching the `next-action.md` evidence found separately) rather than a deliberate
request for a duplicate independent check was the right read, but it is a read, not a
certainty — stated plainly so the adversarial pass or a human reader can override it if
they know otherwise.

Given that, redoing 88 handlers' worth of grep-and-read work would have: (a) added no
information for the ~62 unchanged instances (nothing about them changed since the
adversarially-reviewed census), (b) risked a lower-rigor solo re-check silently
disagreeing with a document that already survived independent correction, and (c) used
this task's effort budget on the wrong thing when the actual news — one fix landed
live-verified, one flagged instance is confirmedly still broken with a stronger
(concurrency-tested) proof than the original census had — is the part nobody had yet
measured today. Standing by the call as made; flagging the reasoning so it can be
checked rather than trusted.

## Consistency check

- The "Headline finding" section's claim (census exists, one instance already fixed)
  is load-bearing for the entire document's structure. Verified twice independently
  within this session: once via `git log --all --oneline` turning up the census and
  fix commits, once via `git merge-base --is-ancestor` confirming both are actually
  on `main`'s ancestry (not on an unmerged branch) — not taken from the commit
  messages' own claims alone.
- The `88 checked / 63 BLOCKING / 25 SAFE / 0 UNCLEAR` figures are quoted, not
  recomputed — correctly attributed to the census throughout, never presented as this
  document's own finding.
- "Enumeration is still current" re-derived the file/handler counts independently
  (`find`, `grep`) rather than trusting the census's 2026-09-03 counts still hold
  today; got identical numbers (17 route files, 13 main.py handlers) by name, not
  just count — a count match alone wouldn't rule out one file added and one removed.
- The quality/summary and observability/summary measurements were each taken two
  ways (host curl vs. in-container) specifically because the task brief itself warned
  host curl might be unreliable. The discrepancy found (10.27s vs. 0.82s-0.89s for
  the *same* observability/summary default-window call) is reported as a real,
  measured finding confirming that warning, not just repeated from the brief as an
  assumption — this is exactly the "one probe beats a paragraph of inference"
  standard from CLAUDE.md's verify-or-falsify rule.
- The concurrency tests (quality/summary fixed vs. observability/summary still
  broken) use the identical harness and identical `/api/state`-as-canary design for
  both, so the contrast between them (canary stays fast vs. canary fully stalls) is a
  genuine apples-to-apples comparison, not two differently-shaped tests being
  compared informally.

## Issue found and fixed during this pass

**None requiring correction.** One number worth flagging as imprecise rather than
wrong: "`observability.db` growth (406MB currently)" in the observability section uses
decimal-MB rounding of the raw byte count (406368256 bytes) already cited precisely in
the Dimensional Analysis section. This is not a load-bearing calculation (nothing is
derived from the 406MB figure; it's contextual color for "the table has real data in
it"), so left as informal decimal-MB shorthand rather than reworded, but noting the
imprecision explicitly rather than presenting "406MB" as a measured, unit-exact value.

## Unaddressed scope, named honestly rather than silently dropped

- **No independent live probe of `tick_executor`'s worker occupancy** was attempted
  for the `candidate_log.population_gate_summary()` item — correctly out of scope
  (issue #410's track per the census's own framing, restated here rather than
  re-investigated), but worth naming so a reader doesn't assume this document checked
  it and found nothing.
- **`record_variant()` (`main.py:908`)** — confirmed by direct code read to still be
  an undispatched synchronous `INSERT OR IGNORE` inside the trading-loop tick, called
  every tick per its own docstring ("Safe to call every trading-loop tick"). This
  document correctly excludes it from the "route handler" enumeration (it isn't one —
  no `@app`/`@router` decorator, called from the tick loop, not from HTTP request
  handling), but the exclusion is a scope-boundary judgment worth someone
  double-checking: `main.py`'s trading loop itself is very likely an `async def`
  function per the codebase's established async-tick-loop pattern (not verified in
  this pass — the call site at `main.py:908` was read for the call itself, not for
  whether its enclosing function is `async def`), so if it *is* async, this is
  structurally the exact same defect class this document is about, just reached from
  a scheduler loop instead of a route decorator. Already tracked separately per
  `docs/open-decisions.md` line 41 regardless of that technicality, so not chased
  further here — but the "not a route handler, therefore out of scope" reasoning is
  a narrower reading of "async caller of sync DB code" than the defect class itself,
  and should not be read as "this isn't the same bug."
- **The `c4`/`next-action.md` coordination-gap paragraph is speculative** about what
  `c4` currently knows — this document has no way to confirm or deny it (no
  `ListAgents`/`SendMessage` access from this isolated worktree agent, stated
  explicitly in the doc itself). Flagged as a flag, not resolved as a fact.
- **Did not re-verify the census's other un-adversarially-checked files**
  (`diagnostics`, `storage_health`, `reset`, `history`, `config`, `research`,
  `backup`, `exits` — the ~10 the census's own adversarial pass didn't spot-check).
  This document inherits the census's own caveat about them rather than closing it.
  If the next reader's actual need is "which of the ~62 remaining instances should be
  fixed next," this document does not answer that beyond restating the census's
  existing priority table — it answers "is the sweep still needed" (no) and "what is
  today's live status of the two named instances" (one fixed, one not).

## GO / no-go self-assessment

Internally consistent; the one scope-boundary judgment worth a second, independent
look is the `record_variant()` async-caller question above (whether `main.py`'s tick
loop is itself `async def`, which this pass did not check). The two live measurement
sets (quality/summary fixed, observability/summary still broken, both with
concurrency proof) are the load-bearing claims most worth an independent
re-derivation — re-run the same in-container `urllib.request` harness against the
live app rather than trusting these numbers, since app state (data volume, concurrent
load) can shift between this pass and the next. Recommend proceeding to the
adversarial-review stage before treating this document's verdict on
`quality/summary`/`observability/summary` as settled.
