# Adversarial Review: Tier 1 Backend Hygiene Implementation Plan

**Reviewer:** independent adversarial pass per CLAUDE.md's "nothing advances
on one pass" HARD RULE — no memory of the session that wrote the artifact.
Every load-bearing claim below was re-derived from primary sources (live
source in the `agent-a0b24773660025286` worktree at commit `128caf2`, the
live app at `https://kalshi-whale-poc.ddev.site:8443`, the running
`ddev-kalshi-whale-poc-fastapi` container, `git log`/`git blame`, and a
standalone ruamel.yaml repro run inside that container), not from the
plan's own tables, self-review, or citations.

**Artifact reviewed:**
`docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-tier1-backend-hygiene.md (moved there 2026-09-06, planning-lanes migration)`
(3,214 lines, 9 tasks: stall attribution, dashboard de-polling, three DRY
fixes, one more event-loop-blocking write, config comment-wipe fix,
pagination + TTL caching, `event_live_data`/`bump_generation` throttling,
task-handle/AsyncClient hygiene, final regression).

**Scope of this review:** whether the plan's re-verification claims (the
five bullets in its "Live re-verification" section, its two "corrected
stale audit citations," and its per-task "Confirm X still matches" claims)
actually hold up against current source and live state, and whether the
proposed diffs are correct, safe, and complete — the standard this repo's
own Tier 0 plan review used.

---

## Method

All commands read-only; nothing in this review touched app state, git
state, or any file under `data/`. Worked directly in the target worktree
(`agent-a0b24773660025286`, branch `docs/tier1-backend-hygiene-plan`,
confirmed at `git status --short` empty except this new file).

| Check | Command / action | Purpose |
|---|---|---|
| Tier 0 code-not-landed premise | `grep -n "def _connect"` × 5 modules + lines around each; `grep -n "STORE_PROBE_TIMEOUT_SEC\|_bounded\|open_fds" services/diagnostics/routes.py` | Confirm the plan's foundational "diffs are against current, un-fixed source" claim |
| `config_store.update()` mechanism | Read `services/config/config_store.py` in full; **independent ruamel.yaml repro** run inside `ddev-kalshi-whale-poc-fastapi` (3 variants: today's one-level `dict.update()`, a naive full recursive merge, the plan's actual `_FULL_REPLACE_PATHS` fix) against a scratch file matching the plan's claimed real shape | Falsify or confirm the claimed comment-wipe mechanism, the claimed orphaned-key hazard, and that the plan's actual proposed fix closes both without regressing `enabled` |
| `event_live_data` payload share | `curl -sk https://kalshi-whale-poc.ddev.site:8443/api/state`, then measured each top-level key's serialized byte size | Independently re-measure the plan's live 87.3%/23-events claim |
| `bump_generation()` call sites | `grep -n "bump_generation" services/whale_stream/whale_stream_handlers.py`; read `_process_stream_trade`'s full body/exit paths | Confirm "fires unconditionally on all 3 exit paths" |
| Trading-decision isolation of `state["generation"]`/`_build_state_body` | `grep -rn 'state\["generation"\]'` and `_build_state_body\|_state_body_cache` across `services/` and `main.py` | Confirm Task 7's safety claim that nothing trading-decision-facing reads the memoized HTTP snapshot |
| httpx defaults | `docker exec ddev-kalshi-whale-poc-fastapi python3 -c "import httpx; ..."` against the real installed package | Confirm Task 8b's `Timeout(timeout=5.0)` / `Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0)` claim |
| `alerting.py` task-handle mechanism | `grep -rn "create_task\|ensure_future" services/alerting/`; read `task_supervisor.py`'s `supervise()`; read all 3 call sites in `alerting.py` | Confirm the plan's own correction of the audit's citation (real hits are zero, mechanism is one layer removed) |
| `resolved_signals_with_factors()` / `population_gate_summary()` | Read both functions' full docstrings and every call site (`grep -rn`) | Test the plan's central "cannot be `since_ts`-bounded" claim and its call-site counts |
| PR #424 citation | `git log --all --oneline \| grep 424` (the plan's own method) **and** `gh pr view 424 --json title,body,...` (a cheaper, more authoritative check the plan didn't run) | Test whether the plan's "flagged as unreliable" correction is itself accurate |
| Citation `§9.2 #3` vs `§8 Tier-1 item 12` | Read both exact passages in `docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md` | Confirm the plan's other citation correction |
| ~25 individual line-number/signature/call-site claims | `grep -n`/`sed -n` against `main.py`, `services/fault_log.py`, `services/loop_watchdog.py`, `services/risk_manager.py`, `services/shadow_mode.py`, `services/advisory/advisory_engine.py`, `services/analytics/market_analyst_orchestrator.py`, `services/analytics/routes.py`, `services/whale_calibration/routes.py`, `services/alerting/alerting.py`, `services/task_supervisor.py`, `services/market_watch/event_metadata.py`, `services/state_view.py`, and the 7 pagination-target route files | Spot-check the plan's "confirmed by direct read" transcriptions |
| Tier 0 PR status | `gh pr view 441 --json state,mergedAt,title` | Confirm the plan's premise that PR #441 (Tier 0's plan) is merged as a document |

---

## Findings

### F1 — CONFIRMED: Tier 0's code genuinely has not landed; this plan's diffs are against real current source

All five `_connect()` functions (`market_history.py:86`, `title_cache.py:56`,
`market_catalog/market_catalog.py:65`, `signal_log.py:150`,
`fault_log.py:58`) are still plain `def _connect(...) -> sqlite3.Connection:`
with no `@contextlib.contextmanager` immediately above. `services/
diagnostics/routes.py` has zero hits for `STORE_PROBE_TIMEOUT_SEC`,
`_bounded`, or `open_fds`. `gh pr view 441` confirms PR #441 ("docs:
implementation plan for Tier 0 live-incident remediation") is merged as a
**document**, `mergedAt: 2026-09-03T03:10:44Z`. This is the plan's most
foundational claim and it holds exactly.

### F2 — CONFIRMED (strong, independently reproduced): the `config_store.update()` comment-wipe mechanism, and the plan's fix

Ran the plan's exact claimed experiment independently (not copied from the
plan — built from scratch against `config_store.py`'s real `_yaml`
settings and `update()`'s real current body, both read directly first):

- Today's one-level `dict.update()` on a synthetic file shaped like the
  real incident (`whale_watcher_kalshi.min_contracts_by_series` followed
  by a comment block) **reproducibly destroys the comment** — confirmed
  live in the container, not assumed.
- A recursive merge-in-place (mutating the existing child map object
  in-place rather than replacing it) **preserves the comment** — confirmed.
- A naive "recursive merge, no deletion at any level" fix, given a patch
  that drops one series key and adds another, **leaves the dropped key
  orphaned** (`KXETH15M: 90` survives in the output) — confirmed, exactly
  the hazard the plan claims the audit's "just deep-merge" suggestion would
  introduce.
- The plan's **actual proposed fix** (`_merge_in_place` + one
  `_FULL_REPLACE_PATHS` entry for `whale_watcher_kalshi.min_contracts_by_series`)
  was run verbatim against both of the plan's own test scenarios: it
  preserves the comment, correctly drops the orphaned key for the one
  allow-listed path, and leaves the untouched sibling field (`enabled`)
  unaffected. All three properties hold simultaneously.

This is the plan's single most load-bearing technical claim, and it is now
independently, experimentally confirmed rather than merely re-asserted.

One implementation-fidelity note, not a defect: Step 3's "Change: / to:"
diff for `update()` only shows the merge-loop being swapped, not the
atomic-write block that follows it (lines 167-181, per the plan's own
citation). This is intentional and explicitly flagged by the plan
("inserts new code directly above that block") — and I confirmed the old
snippet is a genuine, unique substring of the current file, so a literal
find/replace (the mechanical way `superpowers:executing-plans` applies
these diffs) leaves the atomic-write block correctly nested under the new
`with self._lock:`. Verified correct, but worth calling out explicitly in
the plan itself rather than only in this review, since it's easy for an
implementer to eyeball the "to:" block as a complete method and wonder
where the `return dict(self._data)` went.

### F3 — CONFIRMED (via independent live probe): `event_live_data` genuinely dominates `/api/state`, close to the claimed magnitude

Live probe of `GET /api/state` at review time: **3,255,389 of 3,793,646
total bytes (85.8%) in `event_live_data`, across 15 scoped events**
(≈217 KB/event). The plan's own probe (drafted earlier the same day)
found **87.3% across 23 events** (≈205 KB/event). The percentage and
per-event size are close; the absolute event count and total payload
differ because which events are currently "relevant" fluctuates through
the day (this is expected, not a red flag — the underlying mechanism and
rough magnitude are confirmed, not merely the exact number). `_scoped_
event_live_data` (`services/state_view.py:198-200`) is confirmed
introduced in commit `4b0858b`, dated 2026-08-21, exactly as the plan
states.

### F4 — CONFIRMED exactly: `bump_generation()` fires unconditionally on all 3 of `_process_stream_trade`'s exit paths

Read `services/whale_stream/whale_stream_handlers.py:170-244` in full.
`bump_generation()` appears at line 201 (not-running/disabled early
return), line 224 (no-signals early return), and line 240 (end of the
signals-emitted path, inside the `try` before `finally`). All three are
unconditional. Matches the plan's claim verbatim.

### F5 — CONFIRMED (safety-critical): `state["generation"]`/`_build_state_body` have zero trading-decision readers

`grep -rn 'state\["generation"\]'` and a search for `_build_state_body`/
`_state_body_cache` across `services/*.py`, `services/**/*.py`, and
`main.py` show both are read/written only inside `main.py`'s HTTP-serving
code (`_build_state_body`, the ETag construction at `main.py:1645`, and
one `bump_generation()` invalidation call from `market_catalog/routes.py`
that exists for the same reason). No trading/risk/strategy/broker code
reads either. This independently confirms Task 7's central safety claim:
coarsening `bump_generation()` to 1/sec and throttling `event_live_data`'s
send cadence cannot affect anything that places, sizes, or gates a trade.

### F6 — CONFIRMED exactly: httpx 0.27.2's real installed defaults match Task 8b's pinned values

`docker exec ddev-kalshi-whale-poc-fastapi python3` against the real
installed package: `Timeout(timeout=5.0)` and `Limits(max_connections=100,
max_keepalive_connections=20, keepalive_expiry=5.0)`, byte-for-byte what
the plan cites and pins. This is the plan's most rigorously stated
arithmetic/dimensional claim, and it checks out perfectly — a genuine
example of the "never guess" HARD RULE done right.

### F7 — CONFIRMED: `alerting.py`'s own correction of the audit's citation is accurate, and is a real, valuable catch

`grep -rn "create_task\|ensure_future" services/alerting/` returns **zero
hits** — confirming the audit's original citation (literal `asyncio.
create_task` calls in `alerting.py`) does not match current source, and
that the plan's own re-derivation (the real mechanism is one layer removed
through `task_supervisor.supervise()`, which itself calls `asyncio.
create_task(_run())` and returns the `Task`, discarded at all 3 call
sites) is correct. All 3 call sites (`record_alert`, `_check_transition`,
`_expire_stale_crash_alerts`) match the plan's transcription exactly. This
is a genuine improvement over the audit, not a cosmetic one — implementing
the audit's literal citation would have found nothing to fix.

Minor citation drift found while checking this: the plan cites `services/
task_supervisor.py:89` for the internal `asyncio.create_task(_run())`
call; it is actually at **line 73**. Non-load-bearing (Step 1 already
instructs re-confirming before editing, and the mechanism claim itself is
right), but worth a one-line fix.

### F8 — CONFIRMED: the `§9.2 #3` → first-audit `Tier-1 #3/#4` citation correction is accurate

Read `docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md`
directly: `§8`'s Tier-1 item 12 literally reads "= #3, #4 Bound
`resolved_signals_with_factors()` and `population_gate_summary()`..."
(line 1185), and the document's own `§9.2 #3` (line 681) is "the
apply-suggestion block duplicated 6×" — a genuinely unrelated DRY finding
about `config_store`, confirmed by reading the surrounding paragraph. The
plan's correction is right.

### F9 — OVERSTATED: `resolved_signals_with_factors()` already has an unused `since_ts` parameter the plan's "New finding" framing doesn't acknowledge

The plan presents "resolved_signals_with_factors()/population_gate_summary()
cannot simply take a since_ts bound" as a new finding refuting the audit's
suggestion, and states "this task therefore does NOT touch either
function's own signature or query." Reading `services/signal_log.py:662`
directly: **the function's signature already is
`resolved_signals_with_factors(since_ts: float | None = None)`**, added in
a separate, earlier initiative (its own docstring: "measured at ~1s
against 103k+ rows (2026-09-01, whale-confidence-scoring-remediation final
review)"). The same docstring even says "a caller on a tight polling
budget (e.g. a route hit every few seconds) should pass since_ts to bound
it" — language that, read in isolation, could be misread as recommending
exactly what the plan argues against for `services/whale_calibration/
routes.py`'s GET route.

On the merits, the plan's actual design conclusion still holds: that
specific caller **is** the calibration gate itself (it feeds
`generate_calibration_report`'s total-sample threshold), and the same
docstring's next sentence — "the calibration gate itself stays unscoped by
design since it cares about total resolved count, not recency" — settles
that this particular caller must not pass `since_ts`. So the plan's Task
6b design (cache the route, don't scope the query) is correct. But the
plan's own verification claim overstates what it found: it should say
"the parameter already exists, added by a prior initiative for a
different caller shape than this one; every current caller including the
gate route correctly leaves it unset" rather than presenting the
capability as hypothetical.

### F10 — Factually wrong verification claim (though non-load-bearing for the design): `population_gate_summary()` has 2 call sites, not 1

The plan states, twice, "confirmed by reading all 5 call sites of
`resolved_signals_with_factors` and the **1** call site of
`population_gate_summary`." `grep -rn "population_gate_summary("
services/ main.py` shows **two** real call sites:
`services/analytics/routes.py:104` (the one the plan names) **and
`services/research/research.py:221`** (`"candidate_population_gate":
candidate_log.population_gate_summary()`, inside `build_report`).

This doesn't change Task 6's design — `research.py`'s call is inside an
evidence-gated, infrequent sweep (module docstring: "Evidence-triggered
read-only research sweep"; gated by `should_run()`'s `_DEFAULT_MIN_NEW_
RESOLVED_SIGNALS`/`_DEFAULT_MIN_NEW_CLOSED_TRADES` thresholds), not a
tight-polling caller, and Task 6b correctly leaves it untouched either
way. But it is a specific, falsifiable "confirmed by reading" claim that
does not hold up, and the plan's own self-review repeats it as settled
fact ("verified by reading every call site").

### F11 — OVERSTATED: the "17-38s" cost cited to justify Task 6b's cache is very likely the pre-optimization number, not the current one

`git blame` on both the `services/analytics/routes.py:94-98` comment
(citing "17-38s ... proven via a live py-spy stack trace") and `services/
candidate_log.py`'s `population_gate_summary()` docstring traces **both to
the same commit**, `bb5806ca` ("fix: stop candidate_log.
population_gate_summary from blocking the event loop", 2026-08-26). That
same commit's `candidate_log.py` docstring documents the actual fix
applied: the function was converted from a per-row Python loop (measured
at "18.2s total, 15.3s of it just constructing 6.2M row tuples") to a SQL
`GROUP BY` (measured at **"4.8s for the same 6.2M rows"**), plus the
`tick_executor` offload the routes.py comment also describes. The "17-38s"
figure is the range that motivated the 2026-08-26 fix, referenced by the
`candidate_log.py` docstring itself as "ROADMAP.md's 'event loop stalls
for 17-38+ seconds' entry" (past tense, a named historical entry) — not a
current live measurement.

Task 6b's actual code change is still sound and worth doing (a 4.8s
compute cost hit on every dashboard poll, even off the event loop via
`tick_executor`, is still worth caching), but citing "the 17-38s call"
three separate times as the current justification overstates the win by
roughly 4-8x versus the more precise, more recent number sitting in the
very function the plan reads carefully elsewhere (Task 3c's DDL work
already reads this file closely). A fresh live measurement (or citing the
4.8s figure) would be more honest justification.

### F12 — Should-fix: the PR #424 citation-correction used a shallow verification method and, as a result, undersells real, on-topic information

The plan's method: `git log --all --oneline | grep 424`, which surfaces
only the merge commit's subject line (`Merge pull request #424 from
thesneakattack/fix/run-offline-cooperative-yield`) — derived from the
**branch name**, not the PR's real title. From that, the plan concludes
"this citation could not be verified against real PR history and is
flagged as unreliable, not repeated as fact."

Running the cheap, authoritative check the plan skipped —
`gh pr view 424 --json title,body` — shows PR #424's real title is "fix:
revert regressive elastic pool, keep+correct query-bound fast-follow" and
its body describes exactly the pattern the audit gestured at: "`check_
confidence_input_coverage`'s previously-unscoped query is now bound to a
purpose-matched 24h window... Real measured savings: ~1.0s / ~30% per
`run_offline()` call." This is a **real, directly analogous precedent** —
just applied to a different sibling function (`check_confidence_input_
coverage` in `services/diagnostics/diagnostics.py`, not `resolved_signals_
with_factors`/`population_gate_summary`).

The plan's narrow negative conclusion (PR #424 did not touch the two
functions Task 6 is about) is still correct, and does not change Task 6's
design. But "could not be verified... flagged as unreliable" is not an
accurate characterization — a two-second `gh pr view` call (the same class
of "cheap check beats a paragraph of inference" the HARD RULE asks for)
would have let the plan correct the audit's citation precisely (name
`check_confidence_input_coverage` as the actual sibling) instead of
dismissing it as unreliable.

### F13 — Should-fix (new finding, not raised by the plan): Task 1's off-loop write can delay the watchdog's own next sample, risking a self-inflicted phantom stall

`loop_watchdog._tick()`'s loop structure (confirmed by direct read,
45 lines total — the plan's Step 1 says "46 lines," a trivial miscount):

```python
while True:
    await asyncio.sleep(sample_interval_sec)
    now = time.monotonic()
    late = now - expected
    ...
    if late > _STALL_THRESHOLD_SEC:
        ...
        # Task 1 adds here:
        tb = _capture_stall_traceback()
        await asyncio.to_thread(fault_log.record_fault, ...)
    expected = now + sample_interval_sec
```

`expected` is computed from the `now` captured **before** the new
`await`, so the stall-magnitude arithmetic itself is unaffected. But
because `await asyncio.to_thread(...)` is awaited **inline**, inside the
same coroutine that runs the sampling loop, the next iteration's `await
asyncio.sleep(sample_interval_sec)` does not begin until that thread-pool
call actually returns. If `fault_log`'s SQLite write is slow — and this
codebase has repeatedly and recently measured real SQLite write-contention
stalls in the multi-second range elsewhere (`candidate_log.db`'s "163 live
retain-on-lock warnings," the pre-fix `population_gate_summary()`'s
18.2s) — that delay pushes back when the *next* tick's sleep starts,
which can make the *following* tick appear late relative to `expected`
purely because of the watchdog's own diagnostic write, not real external
blocking. Because this happens specifically right after a real stall (a
plausible time for write contention to also be elevated), there's a
believable feedback path where the fix for attributing stalls accurately
could itself inflate `stall_count` — the exact metric Task 1 exists to
make more trustworthy.

This is speculative in magnitude (a small local SQLite insert is usually
sub-10ms, well under the loop's own 100ms default sample interval and the
50ms stall threshold), and the plan does correctly scope `_capture_stall_
traceback()` itself as synchronous/cheap and dispatches the actual write
off-thread specifically to avoid blocking the *rest of the app*. But the
plan's Step 5 only tests that the write happens and happens off-loop — it
does not test or discuss the write's own latency contribution to the
watchdog's *own* subsequent cycle. A safer, equally simple alternative:
fire-and-forget the write (`asyncio.create_task(asyncio.to_thread(...))`,
not retaining/awaiting it inline) so a slow diagnostic write cannot itself
perturb the next sample's timing — consistent with this same plan's Task
8a already establishing the "retain a reference so it isn't GC'd, but
don't block on it" idiom one task earlier.

### F14 — CONFIRMED: dozens of line-number, signature, and call-site citations spot-checked exactly

Independently verified against current source, all exact matches: `main.py`
imports at lines 32 (`regime_analytics`) and 57 (`trade_analytics`), no
existing `suggestion_decisions` import; the `_maybe_run_auto_apply` call
site at `main.py:583-593`; the `resolved_signals_with_factors()` call site
at `main.py:490`; `market_analyst_orchestrator.py`'s `suggestion_decisions`
import at line 18 and its two `generate_recommendations` call sites at
lines 95 and 277; `advisory_engine.generate_recommendations`'s signature
(`declined_ids: set[str] | None = None` as the last parameter) and its
`declined_ids` docstring; `RiskManager.check_daily_loss`'s current body
and `ShadowTrader.check_daily_loss`'s already-guarded sibling, both
byte-exact to the plan's transcriptions; `fault_log.record_fault`'s
current signature and `_write`'s `ON CONFLICT ... DO UPDATE SET count =
count + 1, last_seen = excluded.last_seen` (confirming `first_traceback`
is genuinely not refreshed on a repeat, as Task 1's disclosed limitation
states); `_EVENT_LIVE_DATA_REPOLL_SEC = 60` at `services/market_watch/
event_metadata.py:129`, already imported into `main.py` at the claimed
location; and all 7 of Task 6a's named unbounded routes at their exact
claimed line numbers and default values, plus the `limit = min(max(limit,
1), 200)` clamp confirmed verbatim in all 3 routes the plan cites as the
"200 ceiling" precedent (`get_signal_history`, `get_trading_history`,
`get_advisory_applied_changes`).

### F15 — CONFIRMED: no safety-invariant violation in the two tasks closest to trading-critical code

Task 3b (`RiskManager.check_daily_loss`) is a pure additive `if not self.
day_start_bankroll: return True` guard inserted **before** the existing
division — confirmed to change behavior for no existing non-zero-bankroll
path (every current test keeps passing by construction) and to mirror
`ShadowTrader`'s already-shipped, already-tested equivalent byte-for-byte.
It closes a real `ZeroDivisionError` crash risk in the live kill switch
rather than weakening it — consistent with CLAUDE.md's "never regress" bar
(a crash mid-kill-switch-check is a worse safety outcome than this guard).

Task 4 (`record_snapshot_from_ticker` scheduling) only moves a market-
history analytics write off the event loop; the affected code block in
`_process_stream_ticker` reads `state["latest_prices"][ticker]` and
`matched_market` (captured as plain values before scheduling, avoiding a
stale-read-in-a-deferred-lambda hazard) but does not touch signal
generation, position state, or `strategy.check_exits`. No trading decision
is deferred or reordered by this task.

No task in this plan touches `services/strategy_engine.py`, `services/
kalshi_account_client.py`, calibration/advisory *write* paths (only the
already-verified-safe `declined_ids` read/pass-through in Task 3a), or any
CI-credential/auth code.

### F16 — CONFIRMED: TDD ordering and task independence are sound

All 9 tasks consistently follow read-current-source → write failing test →
confirm the specific failure reason → implement → confirm pass → broader
regression check. Two honest disclosures worth crediting rather than
flagging: Task 3c's Step 2 and Task 8b's Step 2 both explicitly say some
of their new tests will pass immediately (regression pins, not true
red-green) rather than claiming a red step that doesn't apply. Tasks are
genuinely file-independent except one **disclosed** coordination (Task 2's
30s History-tab throttle and Task 6b's 30s cache TTL are explicitly tied
together in the plan's own text, "coordinated, not independently
guessed") — a design link, not a runtime dependency; each task's code
works correctly in isolation regardless of implementation order. Task 9's
full local suite + `import main` + frontend build + live re-measurement
sequence would genuinely catch a regression in any of the other 8 tasks.

---

## Verdict: **GO-AFTER-FIXES**

No must-fix code defects were found. Every proposed diff that this review
spot-checked or fully reproduced (config_store's merge fix, the
`RiskManager`/`ShadowTrader` guard mirror, the `alerting.py` task-handle
wrapper, the httpx pin, the pagination census, the `event_live_data`/
`bump_generation` throttles) is correct against current source, safe with
respect to CLAUDE.md's trading/risk safety invariants, and would work as
written. The plan's foundational premise (Tier 0 merged as a document,
its code not live) is confirmed. Its single most load-bearing technical
claim (the `config_store` comment-wipe mechanism and fix) is now
independently, experimentally confirmed, not merely re-asserted.

What should change before this plan is executed as written — all of these
are documentation/citation-accuracy corrections or one design refinement,
none of which requires re-opening the design stage:

**Must-fix**

None.

**Should-fix**

1. **F9** — Correct Task 6's framing: `resolved_signals_with_factors()`
   already has an unused `since_ts` parameter (added 2026-09-01, a prior
   initiative). State that explicitly rather than presenting the
   capability as hypothetical; the design conclusion (don't scope the
   gate-computing call) is unaffected and is actually consistent with the
   parameter's own docstring for this specific caller.
2. **F10** — Correct "the 1 call site of `population_gate_summary`" to 2
   (`services/analytics/routes.py:104` and `services/research/
   research.py:221`), noting the second is evidence-gated/infrequent and
   so doesn't change Task 6b's design.
3. **F11** — Re-measure `population_gate_summary()`'s current live cost
   (or cite the 4.8s post-optimization figure already in its own
   docstring) rather than the "17-38s" figure, which traces to the same
   2026-08-26 commit that fixed it. Task 6b's cache is still worth doing;
   the justification's magnitude is overstated as written.
4. **F12** — Replace the PR #424 "flagged as unreliable" language with the
   actual, verified citation: PR #424 bound a different sibling function
   (`check_confidence_input_coverage` in `services/diagnostics/
   diagnostics.py`) to a time window, not `resolved_signals_with_factors`/
   `population_gate_summary`. Same design conclusion, more honest sourcing.
5. **F13** — Either measure `fault_log.record_fault`'s own write latency
   as part of Task 1's Step 5/completion note, or change the stall-branch
   write from an inline `await asyncio.to_thread(...)` to a fire-and-forget
   `asyncio.create_task(...)` (matching Task 8a's own "retain a reference,
   don't block on it" idiom) so a slow diagnostic write cannot delay the
   watchdog's own next sample and inflate `stall_count`.
6. **F7** — Correct `services/task_supervisor.py:89` to `:73` for the
   internal `asyncio.create_task(_run())` citation.

**Nice-to-have**

7. **F2** — Add one explicit sentence to Task 5's Step 3 noting that the
   atomic-write block (lines 167-181) is deliberately left out of the
   shown "to:" replacement text and continues unchanged immediately below
   it, for a future reader who might otherwise wonder where the
   persistence/return logic went.
8. **F13** — `services/loop_watchdog.py` is 45 lines total, not "46
   lines" as Task 1's Files section states. Trivial; doesn't affect the
   diff instructions.

None of the above requires re-running the research or design stages —
these are implementation-plan-level corrections a fix-list recheck (per
CLAUDE.md's "nothing advances on one pass" HARD RULE) can resolve without
reopening the whole cycle. Tasks 1-8's actual code changes can proceed
substantively as written once items 1-6 are applied to the plan document
itself.
