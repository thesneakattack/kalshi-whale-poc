# Consolidation — Tier 0 live-incident remediation plan (2026-09-03)

Reconciles the plan artifact
(`docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation.md`),
its embedded self-review (the "Plan self-review" section at the end of
that document), and an independent adversarial review
(`docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation-plan-review.md`),
per CLAUDE.md's "nothing advances on one pass" HARD RULE.

## Verdict: GO, after this revision

The adversarial review's own verdict: **GO-AFTER-FIXES** — "Tasks 2-5 (the
bulk of the plan)... are independently sound... None of this needs to be
blocked," with a short, specific must-fix/should-fix list. No disagreement
to adjudicate between self-review and adversarial review: the self-review
caught internal cross-reference drift from the plan's own mid-draft
restructuring (splitting one connection-fix task into four); the
adversarial review found a deterministic bug in proposed test code, two
real scope gaps, and one already-executed diagnostic result the plan had
only described hypothetically. Both are additive, not contradictory. Every
item on the review's list is applied below, plus the two nice-to-have
items that were cheap to fix.

## Merged fix list, applied

**Must-fix (1/1 applied):**

1. **F5** — Task 1's `_bounded()` helper captured `STORE_PROBE_TIMEOUT_SEC`
   as a default-parameter expression, frozen at `async def`/module-import
   time. Task 1 Step 5's own regression test reassigns the module
   attribute via `monkeypatch.setattr(routes, "STORE_PROBE_TIMEOUT_SEC",
   0.2)` — a frozen default would never see that change, and the review's
   standalone reproduction confirmed the test would error (an unhandled
   `AssertionError` propagating through `gather()`) rather than pass or
   fail cleanly. Fixed: `_bounded` now takes `timeout: float | None = None`
   and resolves it from the module constant live, inside the function
   body, on every call. Step 5's test needed no changes — it was already
   written correctly for a live-reading implementation; only the
   implementation was wrong.

**Should-fix (5/5 applied):**

2. **F3** — Live evidence gathered during the review (not available when
   the plan was first drafted) showed only ~5s of `/api/health/pipeline`'s
   ~24-28s total response time is inside the block Task 1 bounds
   (`stores_probe_ms` read 4.65s and 5.00s on two probes; total
   server-side time, isolated from network via `curl`'s
   `time_starttransfer`, was 23.9-28.1s). Task 10 Step 3's pass/fail
   criterion originally conflated "store probes bounded" with "route is
   fast." Rewritten into two explicitly separate claims: `stores_probe_ms`
   staying bounded is Task 1's own, checkable claim; total route latency
   is named as a separate, still-open question this plan does not close,
   with the unaccounted time's likely location (functions outside the
   `gather()` block) named as a lead for whoever picks that up next.
3. **F4** — the plan's own Live re-verification section already named
   `GET /api/health/faults` as one of only two currently-stuck routes,
   alongside `/api/health/pipeline`, but the first draft fixed only the
   second, without disclosing the exclusion the way it explicitly
   disclosed excluding 22 other `_connect()`-owning modules. Brought into
   scope rather than excluded: new Task 6 (`fault_log.py`'s own
   non-closing `_connect()` — the identical shape as Tasks 2-5, not
   previously counted among them) and new Task 7 (`get_faults()` moves
   its fully-synchronous, no-thread-hop SQLite reads off the event loop —
   the same #210 bug class `services/diagnostics/store_stats.py`'s own
   docstring already documents as fixed once for the sibling route).
   Inserting these shifted the former Tasks 6-8 to 8-10; every
   cross-reference to the old numbering across the whole document
   (Global Constraints, and inside Tasks 1, 4, 9, and 10) was searched
   for and corrected — not assumed complete after the first pass, since
   that exact mistake (a stale cross-reference surviving a renumbering)
   is what a different document's own PR-stage review caught earlier the
   same day.
4. **F6** — Task 8's (the integrity check) "Expected output" text only
   anticipated `PRAGMA integrity_check` returning rows (`ok`, or a list of
   corruption descriptions). The review actually ran the exact command
   against the live `market_history.db` and found it **raises**
   `sqlite3.DatabaseError` directly while fetching, reproduced identically
   twice, ~90 seconds apart. The command now wraps the query in
   `try/except sqlite3.DatabaseError` so the diagnostic itself doesn't
   crash on exactly the file it exists to check, and Step 3 records the
   real finding — persistent, reproducible corruption, not a hypothetical
   — rather than presenting the check as not-yet-run.
5. **F7** — `docs/next-action.md`, this plan's own cited Tier-0 source,
   asks for the integrity check on **both** `market_history.db` and
   `market_catalog.db`; the first draft checked only the first. Added the
   second everywhere Task 8 references it — the review already obtained
   the result (`ok`, clean), folded in as real evidence rather than left
   as an unexecuted step, and used as partial evidence against the
   structural-corruption hypothesis for `markets_watched: 0` (§4.7 of the
   architecture audit's second pass).
6. **F10** — Task 9's (fd visibility) "Why the test patches..." note
   claimed `grep -n 'from services import fault_log'
   services/diagnostics/routes.py` returns matches only at lines 161,
   173, 332; the review found a fourth at line 494 (inside `get_faults()`,
   directly relevant now that Task 7 also touches that function).
   Corrected.

**Nice-to-have (accepted as cosmetic, not changed — matches the review's
own classification):**

- F8 (Task 4's Files-section undercounting `market_catalog.py`'s DDL
  statements) and F11 (Task 1's imprecise Files-section line range) are
  both already superseded by each task's own Step 2 instruction to read
  the real current body before editing — the review confirmed the
  operative diff instructions are exact even where the summary prose
  isn't.
- F13 (Tasks 2, 4, 5's tests inlining `monkeypatch.setattr(module,
  "DB_PATH", ...)` instead of calling their module's existing fixture
  helper) is functionally equivalent to what each test does instead — the
  review itself did not treat this as blocking.

## Why this didn't trigger a third full review cycle

CLAUDE.md's fix-list-recheck provision: revising per a review's findings
"is not a second full self-review-plus-adversarial-review pass" unless the
revision "changes the artifact's scope or introduces a claim the original
two reviews never saw." Tasks 6-7 are new content the adversarial review
did not see in this exact written form — but their shape (bind a blocking
call off the event loop via `asyncio.to_thread`; close a leaking
connection via `@contextlib.contextmanager`) is mechanically identical to
patterns that same review already verified sound for Task 1 and Tasks 2-5
respectively, and finding F4 itself specified the mechanism to fix
(`fault_log.py`'s own `_connect()`, `get_faults()`'s missing thread hop),
not a fresh design question this plan had to invent. This revision is
scoped as the fix-list recheck the HARD RULE's carve-out describes, not a
scope change. The judgment call is stated here explicitly rather than
assumed, and this repository's process still runs its own independent
PR-stage review cycle (`.claude/rules/branching-and-ci.md`) on this plan
once it's pushed, before merge — that is the venue that checks whether
this call was the right one, not skipped in favor of it.

## What was not changed

The plan's central conclusions (the fd-leak mechanism and its minimal-form
fix; the diagnosis that fd exhaustion cannot explain `/api/health/pipeline`'s
current hang; the corruption-check-before-any-write safety gate; the
explicit scope boundaries around Tier 2 and `markets_watched: 0`) — the
adversarial review explicitly confirmed all of these as sound (its own
words: "every line number, function body, and call-site count was
re-derived from current source and matches exactly") and did not ask for
changes to any of them.
