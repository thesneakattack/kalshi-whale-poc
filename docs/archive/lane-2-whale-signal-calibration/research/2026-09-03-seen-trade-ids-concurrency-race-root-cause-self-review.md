# Self-review: `_seen_trade_ids`/`_seen_order` concurrency race root-cause doc

Reviewing my own artifact
(`2026-09-03-seen-trade-ids-concurrency-race-root-cause.md`) for internal
consistency, unverified claims, and citation accuracy before handing it to
independent adversarial review, per the "nothing advances on one pass" HARD RULE's
first (cheapest) layer. This session's own prior document (the trade-resolve
solution comparison, PR #547) had 4 citation errors caught by its self-review and
6 more by adversarial review — going in expecting the same discipline is warranted
here, not assuming a first draft is clean.

**Note: this self-review pass was interrupted partway through by a live production
incident** (the fastapi worker process died, reported by peer sessions autotrade-96
and autotrade-ce; I was pulled in to diagnose per the live-incident practice, which
explicitly permits skipping the review cycle mid-diagnosis). The incident was
independently resolved by the coordinator; this self-review resumed afterward with
no changes to the underlying investigation from the incident itself — the two are
unrelated (this document's own subject is a separate, latent bug, not the crash
that occurred).

## Errors found and fixed during this pass

Re-verified every cited line number against the actual current source with `grep -n`
rather than trusting my own count while writing the first draft — this exact
discipline (checking citations, not just remembering writing them correctly) is
what caught the #542 document's own errors, and it worked again here:

- **`_scoring_pool.py`'s `_executor` line number was off by 4.** Cited as line 31;
  `grep -n "_executor = ThreadPoolExecutor"` gives line 35. Corrected. (`run()`'s
  own citation, `:39-41`, was independently verified correct.)
- **Two `candidate_retry.enqueue` citations pointed at the wrong ranges, and one
  specific line number (486) fell outside its own cited range.** The original text
  cited `kalshi_trade_tape.py:434-448` for the `truncated`-branch enqueue and
  `:479-485` for the exception-branch enqueue while separately saying "line 486" —
  486 is not inside 479-485. Re-derived precisely via
  `grep -n "candidate_retry.enqueue\|truncated = wanted\|except Exception as exc"`:
  the truncated branch runs `456-465` (enqueue at 463), the exception branch runs
  `470-486` (enqueue at 486). Corrected both ranges to contain their own quoted
  line numbers.
- **The H4/Task-11 design-comment citation pointed at the wrong function
  entirely.** The original text cited `kalshi_trade_tape.py:452-458` for the quote
  "a real chance on a later presentation instead (this trade_id's next appearance
  in the trade tape, or Task 12's retry queue)" — but `grep -n "a real chance on a
  later presentation"` shows that exact text at line 559, inside
  `_process_trades_sync`, not inside `_resolve_unknown_markets` where lines
  452-458 actually live (a *different*, related H4/Task-11 comment about the
  exception-handling case, not the one quoted). This was a real citation error, not
  a minor line-count slip — it pointed a reader to the wrong function to find a
  quote that lives somewhere else. Corrected to `:559-561`, and added a note
  distinguishing the two related-but-distinct H4/Task-11 comments in this file so a
  reader isn't confused about which one is being quoted.
- **The class-`__init__` citation range ended before the constructor actually
  does.** Cited as `kalshi_trade_tape.py:222-249`; `grep -n
  "self._resolve_failed_tickers: set\[str\] = set()"` shows that field (explicitly
  named in the same sentence as being declared in `__init__`) is actually assigned
  at line 253, past the cited range's end. Corrected to `223-253` (constructor body
  start through its last field assignment).

## Checked and found correct (no change)

- `_mark_seen`'s own line range (`263-270`) and the exact `_process_trades_sync`
  gate/mark line numbers (541 for the seen-check, 564 for the `_mark_seen` call) —
  re-verified via `grep -n` against the live file; both correct as originally
  written. (My own manual line-count-by-eye during this review briefly suggested
  564 might be off by one — re-checked with `grep -n` rather than trusting the
  manual count, and the original citation was right; the manual recount was the
  error, not the document. Worth noting as a reminder that `grep -n` beats
  eyeballing a `sed` range even during self-review.)
- `candidate_retry.py:167` (the `score_recovered_trade` call site inside
  `run_pending`'s loop) and `main.py:1436` (the `_candidate_retry_loop` supervise
  call) — both confirmed exact via direct `sed`/`grep`.
- The three-commit git-historical ordering (`a84d35c` 2026-08-11, `7beb658`
  2026-08-28, `3ff41d4` 2026-09-01) — all three re-run directly against current
  repo history, dates and commit messages match exactly what's cited.
- `git show 3ff41d4 --stat` confirming that commit only added `_scoring_pool.py` +
  its test file, not touching `kalshi_trade_tape.py` — re-verified, matches.
- The `series_watcher.py`/`game_state.py` precedent quotes (§6) — re-read both
  files directly, quotes are accurate and not distorted by trimming.
- The central correction to the adversarial review's own hedge (that the eviction
  loop specifically is *not* the hazard, thread-local `oldest` keeps popleft/discard
  pairs safe, and the real risk is the narrower check-then-act gate race) — re-traced
  the reasoning once more end to end rather than assuming the first pass was right:
  holds. `set.add`, `deque.append`, `deque.popleft`, `len()` are each individually
  atomic under the GIL; `oldest` is a stack-local variable never read from shared
  state; the gate-race conclusion follows from the actual code structure at
  `kalshi_trade_tape.py:539-564`, not from an assumption.

## Not fixed, flagged for adversarial review

- **Whether the container-level incident this document's own investigation was
  interrupted by has any bearing on this document's subject.** I believe not (the
  crash symptom — a dead worker process, silent, no traceback — doesn't obviously
  match a `_seen_trade_ids` race, which would produce a duplicate signal or subtly
  corrupted dedupe state, not a process death) but I did not exhaustively rule out
  a connection, and an independent pass with fresh eyes on both the incident's own
  timeline and this document's claims would be a reasonable check.
- **The concrete "duplicate signal" failure mode (§4) is reasoned from source, not
  empirically observed** — same evidentiary tier and same honest hedge as the
  original F6 finding this document builds on. I did not attempt to find live
  evidence of an actual duplicate signal in `signal_log.db` (the prior session
  already checked and found no `trade_id` column there to check against — cited in
  "Not resolved here," not re-attempted here). Worth an adversarial pass
  double-checking whether any *other* persisted store (e.g. `candidate_ledger.db`,
  not checked by either this document or the prior session) might have a `trade_id`
  column that could actually answer the "has this ever fired" question directly.
- **The Kalshi-redelivery-probability question (§4's last paragraph, and "Not
  resolved here")** — explicitly flagged as unchecked against `docs/kalshi/`, per
  this repo's Kalshi-integration-authority rule. Not resolved in this self-review
  pass either; still open for whoever picks this up next.
- **Fix option B's characterization** (`asyncio.Lock` at the dispatch sites) as
  "weaker... fragile to a new caller forgetting to acquire it" is my own
  architectural judgment, not verified against any existing bug this repo has had
  from exactly that failure shape (a caller forgetting to acquire a coordination
  lock). Worth an adversarial check on whether this reasoning is sound or whether
  I'm underselling option B relative to option A.
