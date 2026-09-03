# Self-review: trade-resolve consumer-blocking solution-comparison doc

Reviewing my own artifact
(`2026-09-03-trade-resolve-consumer-blocking-solution-comparison.md`) for internal
consistency, unverified claims, and unaddressed scope before handing it to
independent adversarial review, per the "nothing advances on one pass" HARD RULE's
first (cheapest) layer.

## Errors found and fixed during this pass

- **Wrong issue attribution, four instances.** The intro, §3's benchmark, §5's
  benchmark, and §4's precedent reference all attributed the reconnect-free-window
  falsifier test and the 8,854.6ms/queue-depth-jump correlation to "#542's own
  comment thread." Checked directly rather than trusting memory:
  `gh issue view 541 --json comments --jq '.comments[].body' | grep -c
  'reconnect-free|falsifier'` → 4 matches; the same grep against #542 → 0 matches.
  That analysis was posted to **#541**, not #542 — #542 holds the original
  mechanism root-cause and the later resolve-volume-vs-baseline comment, #541 holds
  the dropped-message finding and the falsifier test that links the two. Corrected
  all four references. This is exactly the kind of citation error that's easy to
  make when writing quickly from memory of "the incident" as one undifferentiated
  thing rather than checking which specific issue thread a specific claim actually
  lives on — the "never guess" HARD RULE applies to citing my own prior work, not
  just to source code.
- **Wrong section cross-reference.** §4 (Option A) referenced `_coalesce_ticker` as
  discussed "(§2 above)" — but `_coalesce_ticker` is introduced and discussed in
  §1.1, not §2 (§2 only mentions it in passing, itself citing §1.1). Corrected to
  "(§1.1 above)".
- **A benchmark figure (§4's ~1.01/s resolve-triggering rate) was cross-referenced
  to the wrong evidence.** First draft cited it as coming from "§1's live counters,"
  but §1's own figures are a *different* measurement (the 6-sample, ~9-minute
  `offlist_candidates` average of 87, itself sourced from
  `/api/observability/summary?hours=0.15`) than the 530-events/525s figure actually
  used in §4 (sourced from a single `/api/health/pipeline` snapshot's cumulative
  `provider_stats` counter, normalized by generation uptime). The two are
  independently consistent (≈1.01/s vs. ≈0.97/s implied by the other) but they are
  not the same measurement, and citing one as the source for the other was a false
  cross-reference even though the numbers happen to agree. Rewrote §4's citation to
  point to its actual source (`GET /api/health/pipeline` at the specific timestamp)
  and note the independent consistency check rather than claiming shared sourcing.
  Added the missing appendix entry for that `curl` call — it had never been logged.
- **Appendix was missing two citations.** The `/api/observability/summary` default
  24h call (source of §1.2's `critical_whale.network.window_avg_ms` = 290.02ms and
  §5's `network.window_max_ms` max = 44,580.24ms — a load-bearing figure for Option
  C's whole "the tail is untouched" argument) had no appendix line at all, and
  neither did the `/api/health/pipeline` pull behind §4's rate figure (see above).
  Both added.
- **§1's "all 87" claim understated the actual match.** Re-checked the raw JSON
  (`obs2.json`) rather than trusting my earlier prose summary: `count`, `min`,
  `max`, and `avg` are *all four* identical across `offlist_candidates`,
  `resolve_calls`, and `critical_whale.calls` (67.0/110.0/87.0, 6 samples each) —
  not just the averages, which is what "all 87" implied. This is a stronger, more
  precise claim than what I'd written, and re-verifying against the raw file rather
  than my own earlier paraphrase is what caught the gap. Corrected to state the
  full min/max/avg match.

## Checked and found correct (no change)

- The §1.2 table's four figures (median/p90/max for `resolve.window_avg_ms` and
  `window_max_ms`, n=633 each) were re-run from the raw `st_resolve_window_*.json`
  files rather than trusted from memory of the earlier printout — matched exactly.
- The §5 "78.79-290.02ms" range: re-verified both source files (`obs2.json` for
  78.79, `obs1.json` for 290.02) independently — both are genuinely
  `network.window_avg_ms` values from two different window sizes, correctly
  described as "two different windows measured."
- The `_scoring_pool.py` docstring quote in §3 (trimmed with ellipses) — re-read
  the actual file to confirm no material distortion from the trim.
- The `self._resolve_failed_tickers` concurrency-race claim (§3's "load-bearing
  finding") — re-traced the control flow once more: the reset
  (`kalshi_trade_tape.py:401`) is a synchronous statement executed atomically
  relative to other coroutines up to the method's own first `await` point; a
  second call's reset genuinely can interleave with a first call's still-pending
  `await client.get_markets_by_tickers(batch)` and wipe state the first call
  populated before its own await. This is sound source-derived reasoning (an
  accepted evidentiary tier under CLAUDE.md's "never guess" rule), correctly
  hedged in the doc as "a bug any naive concurrent implementation must account
  for" rather than an empirically-reproduced live failure (concurrency doesn't
  exist in this call path yet, so it can't have been reproduced).
- The commit-ordering proof (`e5bf56b` predates `3c3ff67`, `merge-base
  --is-ancestor`) — re-ran both commands directly against current repo state
  rather than trusting the earlier session output; unchanged.
- No option in §3-5 proposes dropping, merging, or coalescing a trade print itself
  — re-read all three sections specifically hunting for language that might read
  as violating the completeness constraint stated in §2, since that's the one
  constraint a data-plane-adjacent design doc absolutely cannot get wrong. None
  found; §4 (Option A) is explicit that only the *resolve calls* batch, never the
  trades.

## Not fixed, flagged for adversarial review

- §3's ordering analysis (same-ticker sequencing, `opened_since=now` cross-stream
  guard) is reasoned from the existing docstrings and comments cited, not from
  independently re-deriving `strategy_engine.py`'s actual guard logic line-by-line.
  I read the *citation* (`_process_stream_ticker`'s comment referencing the design
  spec §3) but did not re-open `strategy_engine.py` itself to verify the guard
  behaves the way the comment describes. Worth an independent check.
- The N=4 concurrency figure in §3's benchmark is presented as "matching
  `_scoring_pool`'s existing precedent," which is true as a precedent citation, but
  I did not independently derive whether 4 is actually well-sized against the
  measured ~1/s resolve-triggering rate and the shared 8 req/s limiter — the doc
  itself says "sized conservatively here, not validated against a real burst,"
  which is honest, but an adversarial pass could usefully sanity-check whether that
  hedge undersells or oversells the risk.
- I did not independently verify that `services/alerting/alerting.py` (cited in §7
  as "the home for this class of check") actually has a shape that could take a
  two-metric compound condition (queue depth sustained + reconnects flat) without
  restructuring — I know the module exists and handles fault-log-style checks from
  earlier context in this session, but didn't re-open it for this specific claim.
