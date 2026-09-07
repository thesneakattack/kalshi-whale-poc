# Self-review: 2026-09-03 worker-CPU-pin-and-loop-stalls research doc

Reviewing my own artifact (`2026-09-03-worker-cpu-pin-and-loop-stalls.md`) for
internal consistency, unverified claims, and unaddressed scope before handing
it to independent adversarial review, per the "nothing advances on one pass"
HARD RULE's first (cheapest) layer.

## Errors found and fixed during this pass

- **§4.2's read count was wrong on first draft.** The intro and §4.2 both
  originally claimed "up to 4/5 distinct DB-backed reads per position"
  including `signal_log.series_stats`. Re-checked `config/settings.yaml:94`
  directly (`auto_exit_series_track_record_weight: 0`) against
  `exit_engine.py`'s own `if w_series > 0:` guard around the `series_stats`
  call (line 586) — that guard means `series_stats` does **not** fire live
  today, even though the code path exists and is real. Corrected both the
  intro paragraph and §4.2 to state 3 active reads from `check_exits`
  (`recent_price`, `volatility`, `analyst_lean`) plus 1 more from
  `position_netting.review` (a second `volatility` call), and to flag
  `series_stats` explicitly as inert-by-config rather than silently dropping
  it or leaving the wrong count standing. This is exactly the kind of
  "verify every gate, don't just find the call site" checking the "never
  guess" HARD RULE asks for, and I'd missed it on the first pass through
  `_exit_confidence`.
- **§2's "24 remaining threads at 0.0–0.2%" overstated precision.** Rechecking
  the raw sample data (three `thread_cpu.py` runs) against that sentence: one
  run showed a non-main, non-top-2 thread at 0.4% (`tid 25542`). Corrected to
  "0.0–0.4%" and reworded to make clear this is "never a third meaningfully
  active thread," which is the actual, defensible claim — the original
  0.0–0.2% range was written from memory of the *typical* row rather than
  re-scanned against all three tables that are already sitting right there in
  the same document.
- **Two citation timestamps were estimated, not read.** §2.1's table
  originally said "~12:56Z" / "~13:16Z" for the two `handler_time_by_class`
  pulls, and the appendix said "~20 minutes apart" — both were rough
  guesses from wall-clock feel, not the JSON's own `generated_at` field.
  Recomputed precisely (12:49:10Z and 12:59:34Z, 10.4 minutes apart) and
  corrected both places. The original numbers weren't wrong in a way that
  breaks the argument (order of magnitude and direction were fine), but an
  estimate presented without a hedge, sitting next to a document full of
  exact figures, reads as more precise than it was — exactly the kind of gap
  the "never guess" rule and the dimensional-analysis HARD RULE (timestamps
  are an explicitly named unit) exist to catch.
- **The worktree-count citation (`git worktree list | wc -l` → 12) was
  written before I had actually run that exact command successfully** (an
  earlier `git worktree list` full listing had 12 rows, which I'd hand-counted
  correctly, but the doc cited a command I hadn't run in that exact form).
  Re-ran it directly after noticing the gap: confirmed 12. No change to the
  number, but the citation is now to a command actually executed, not one
  inferred to produce the same answer.

## Checked and held (no change needed)

- Every `services/*.py` line number cited (`exit_engine.py:65,81,108,272,
  523,571,586`; `position_netting.py:251,274,374`; `paper_broker.py:480`;
  `strategy_engine.py:872`; `whale_stream_handlers.py:251`;
  `market_history.py:309,343`; `candidate_log.py:76`; `main.py:1288-1299,
  756-774`; `loop_watchdog.py:62-100`) — re-grepped against the actual file
  content at this document's HEAD during this pass, not re-trusted from the
  first read. All matched exactly.
- `config/settings.yaml` line numbers for every flag cited (`27`, `77`, `78`,
  `79`, `83`, `94`, `220`) — re-grepped in this pass, all matched.
- The `_cached()` bypass logic (§4.2's quoted snippet) — re-read against the
  live file, verbatim match.
- The `loop_watchdog._tick()` same-loop-coroutine argument (§3) — re-derived
  the reasoning from scratch during this review rather than re-reading my own
  prior paragraph, to check I hadn't talked myself into a conclusion the code
  doesn't actually support. It holds: `_tick` is scheduled via
  `asyncio.ensure_future(_tick())` inside `start()`, and `_tick` itself is
  `async def` with the sampling loop as its body — a plain asyncio Task, not
  a `threading.Thread`. The 400-row empirical check (§3) is independent,
  direct evidence for the same conclusion, not circular with the source
  argument — one is "why it must be this way" (source), the other is "and
  here is 400/400 confirming observations" (data), and they agree.
- The PR #515 reconciliation (§2) — re-read the fetched PR body text quoted
  in the document against what I actually pulled via `gh pr view 515`, no
  drift.

## Known, disclosed scope gaps (already stated in the doc's own §8/§5.2)

- §4.2's "tens of milliseconds" cross-check against the live 46-113ms figure
  is explicitly labeled a naive linear scaling of one pre-existing benchmark,
  not a direct measurement of the WS-path call — §8 names the concrete next
  step (re-run `tests/test_check_exits_scale_benchmark.py`-style benchmark at
  today's actual position count and config). I did not attempt that
  benchmark myself in this session; flagged as a real limitation, not
  quietly treated as closed.
- §5.2 (write_bytes/cancelled_write_bytes) is explicitly left as "plausible,
  not traced" — I did not attempt a WAL-checkpoint-level trace to close that
  gap in this pass, on the judgment that the task's two named conditions
  (CPU pin, loop stalls) do not depend on resolving it, and chasing it
  further risked exactly the "decide, don't over-investigate" failure mode
  this repo's own standing guidance warns against for a secondary curiosity
  that doesn't change the primary answer.
- §6 (1,311 zombie `git` processes) is explicitly labeled not traced to a
  specific spawning mechanism, and explicitly labeled not a contributor to
  either of the task's two conditions (verified zero current CPU, zero new
  zombies forming across this session's multiple samples) — included for
  completeness per the "don't discard a real finding silently" instinct, not
  because it bears on the mechanism.
- Every claim resting on the coordinator's escalation report (§5) is
  labeled, in the section header and inline, as reported-not-independently-
  observed, with each element cross-checked only against *this* document's
  own independently-gathered evidence where a cross-check was possible
  (thread state, thread count, the general "CPU flat during a stall"
  shape) — never presented as if this document re-observed the specific
  12:51Z event itself.

## One thing I did not re-verify in this self-review, flagged for the adversarial pass

- I did not re-run `gh pr view 414/409/420/424` a second time during this
  self-review to re-confirm the body text quoted/paraphrased in the
  document's own reading of them (§7's rule-out table) — I'm relying on the
  first read from earlier in this session. The adversarial reviewer should
  re-pull these four PRs independently rather than trust this document's
  characterization of what each one fixed.
- I did not independently re-derive whether `market_history.py`'s
  `idx_snapshots_ticker_ts` index actually serves `recent_price`'s exact
  query shape (`WHERE ticker = ? AND timestamp <= ? ORDER BY timestamp DESC
  LIMIT 1`) via `EXPLAIN QUERY PLAN` against the live `market_history.db` —
  I read the `CREATE INDEX` statement and reasoned from the index's column
  order that it should serve this query, but did not run the live
  `EXPLAIN QUERY PLAN` that would settle it beyond doubt. This affects only
  the framing of *where* the per-call cost concentrates (connect/pragma
  overhead vs. a genuine scan) — it does not affect §4's core claim (the
  calls are uncached and run synchronously on the loop) either way, but a
  more precise "why 50-100ms" story would want this confirmed.

## Verdict

Three real errors found and fixed (the read-count overclaim, the thread-
percentage overclaim, the estimated timestamps); one citation strengthened
by actually running the command it claimed. No change to the document's
top-line mechanism or conclusion — every fix narrowed an overclaim toward
what the evidence actually supports, none of them touched the load-bearing
argument in §3/§4. Proceeding to adversarial review with the two flagged
items above (PR body re-verification, `EXPLAIN QUERY PLAN` on the index) as
specific things for that independent pass to check from primary sources.
