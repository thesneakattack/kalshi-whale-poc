# Adversarial review: 2026-09-05-issue-530-sync-dispatch-sweep.md

Independent pass, no memory of the authoring session. Every load-bearing claim below
was re-derived from primary sources (git log/show, direct source reads, live HTTP
measurements against the running app) — none accepted from the document's own tables,
citations, or self-review.

## 1. Prior census exists and is really merged to `main`

**PASS.** `docs/event-loop-blocking-routes-census-2026-09-03.md` exists in this
checkout. `git log --follow` on it resolves to commit `7c32c2d`. `git fetch origin
main` + `git merge-base --is-ancestor 7c32c2d origin/main` → **true**. The PR #552 fix
commits (`edcf1d5`, merge commit `db35df0`) also verify **true** against
`origin/main`. The document under review's own commit (`18af21b`) correctly verifies
**false** (not yet merged — it's the artifact being reviewed, sitting on
`docs/530-sync-dispatch-sweep`). `git log --oneline --all | grep -i 530` independently
turned up the same commit chain the document cites (`7c32c2d` census →
`3e0dc1f`/`29559e9` adversarial-review corrections → `edcf1d5`/`db35df0` PR #552 fix →
`db3ade6` coordination note → `18af21b` this document).

## 2. PR #552 fix is real and the code on disk reflects it

**PASS.** Read `services/quality/routes.py` directly (not the commit message). All
seven calls the document names are wrapped in `await asyncio.to_thread(...)` right
now, current HEAD:

- `observability.runtime_findings` (line 89-91)
- `storage_health.inventory_data_dir` (line 92)
- `backup.latest` (line 94)
- `storage_health.storage_findings` (line 95-96)
- `alerting.active_alerts` (line 103)
- `research.latest` (line 115)
- `fault_log.summary` (line 147)

`gh pr view 552` confirms `state: MERGED`, `mergedAt: 2026-09-04T03:46:27Z`, title
"fix: dispatch /api/quality/summary's 7 blocking calls off the event loop (issue
#530)" — matches the document's "7 undispatched sync calls" claim exactly, from
GitHub's own record, not the commit message.

## 3. Live re-measurement of both endpoints

**PASS — numbers land in the same ballpark, independently obtained.**

In-container (`docker exec ddev-kalshi-whale-poc-fastapi python3 -c "...urllib..."`,
`http://localhost:8000`, run fresh in this review, not copy-pasted):

| Call | This review | Document | Census (pre-fix / baseline) |
|---|---|---|---|
| `GET /api/quality/summary` ×3 | 3.661s, 3.388s, 3.447s | 3.432s, 3.754s, 3.423s | 11.59s-33.35s (pre-fix) |
| `GET /api/observability/summary` (default) ×2 | 0.823s, 0.793s | 0.816s, 0.892s | 0.79-0.91s |
| `GET /api/observability/summary?hours=720` | 6.534s | 7.014s | 7.66s |

Every figure is within normal run-to-run variance of the document's own numbers. No
material discrepancy — the document's numbers are trustworthy, not cherry-picked or
stale.

## 4. Concurrency test — the document's single strongest claim

**PASS — independently reproduced with my own script**, not the document's canned
numbers. Wrote a fresh threading harness (`/tmp/concurrency_test.py`, copied
into the container and run there), same design as the document describes (fire the
heavy call, then three `/api/state` calls 0.2s apart while it's in flight):

```
=== quality/summary concurrent with /api/state ===
quality_summary   5.117s
state_0           0.783s
state_1           1.284s
state_2           1.084s

=== observability/summary?hours=720 concurrent with /api/state ===
obs_summary_720   7.194s
state_0           7.059s
state_1           6.859s
state_2           6.660s
```

This is a clean, independent reproduction of the exact contrast the document claims:
`/api/state` stays fast (sub-1.3s) alongside the fixed `quality/summary` call, but is
**fully stalled for nearly the entire duration** of the still-broken
`observability/summary?hours=720` call. This is real, reproduced evidence of an
event-loop freeze, not an artifact of the document's own harness or timing luck.

## 5. Route/handler enumeration hasn't drifted

**PASS, with one disclosed methodology blind spot (not a defect in this document's
conclusions).**

- `find services -maxdepth 2 -name routes.py | wc -l` → **17**, identical filenames to
  both the document's and the census's list.
- `grep -n '@app\.\(get|post|put|delete|patch\)' main.py | wc -l` → **13**, same
  routes.
- Searched for `@router.`/`@app.` decorators (including `websocket`) outside the
  `*/routes.py` + `main.py` convention: the only extra hits are test fixtures and
  `tools/project_manifest.py` docstrings that embed example route-decorator *strings*
  for testing that tool's own AST scanner — not real production routes. No missed
  route-defining file.

**Blind spot found, not in the document**: `main.py:1842` has
`@app.websocket("/api/ws")`, a real production route decorator that neither the
census's nor this document's counting grep (`@app\.(get|post|put|delete|patch)`)
would ever match — the pattern excludes `websocket`. Checked the handler directly: it
is `await ws_manager.connect(websocket)` / a bare `while True: await
websocket.receive_text()` loop / disconnect — no DB calls at all, so this introduces
**no actual missed BLOCKING instance**. Also consistent with `tools/project_manifest.py`'s
own route-counting convention, which documents the same `get/post/put/delete/patch`
set and also excludes `websocket` — so this is a pre-existing, repo-wide convention
blind spot, not something unique to this document. Worth a footnote for whoever picks
up the census next; does not change this document's verdict.

## 6. `services/observability/routes.py` genuinely untouched since the census

**PASS**, with a nuance the document handles correctly. `git log 7c32c2d..HEAD --
services/observability/routes.py` → **zero commits** — confirms the document's literal
claim ("no commits touching this file since 7c32c2d").

The underlying module `services/observability/observability.py` (not the route file)
*was* touched twice since the census: `ca23ab1` (Task 5, migrates `_connect()` to
`services/db.py` to fix an fd leak) and `75d963d` (persists two new ticker-coalescing
gauges). Read both diffs directly. Neither changes the dispatch shape:
`ca23ab1`'s own commit message states explicitly, and the current `_connect()`
docstring (read directly, current HEAD) still says verbatim: *"history() and summary()
are still called with no dispatch from services/observability/routes.py's async
handlers - tracked as item 2 of issue #530."* Direct reads of `history()` (line 110)
and `summary()` (line 124) confirm both are still plain `def`, still synchronous,
still called with no `await`/`asyncio.to_thread` from the `async def` route handlers
at `routes.py:32`/`:41`. Line numbers (`:110`, `:124`) match the document's citation
exactly. The document's "Scope notes" section discloses the Task 5 migration and
correctly characterizes it as orthogonal to dispatch — this is accurate, not an
omission.

## 7. `record_variant()` / `main.py:908` scope-boundary question

**RESOLVED, confirming the self-review's flagged gap was real.** The self-review
explicitly said it had not checked whether `main.py`'s enclosing tick-loop function is
itself `async def`. Checked directly: `main.py:908`'s
`config_performance.record_variant(config_fp, cfg)` call sits inside `async def
trading_loop():` (defined at `main.py:878`, confirmed via `grep -n '^async def\|^def '
main.py`). `record_variant` itself (`services/config/config_performance.py:104`) is a
plain `def` doing a synchronous sqlite3 write.

**This confirms the exact defect class** (an `async def` caller invoking synchronous
DB code with no dispatch) applies here too — the document's "not a route handler,
therefore out of this document's defined scope" framing is a legitimate scope
boundary for a document titled "route handler sweep," but it is *not* evidence this
isn't the same bug, and the document says as much in its self-review rather than
hiding it. Also verified `docs/open-decisions.md:41` reads exactly as cited:
`record_variant()'s per-tick sync write joins #530's sweep; PR #394's
non-deterministic race test stays as-is · #530 comment · me` — the tracking claim is
real, not invented.

## 8. Other checks: coordination gap, overclaims, missed files

**Coordination-gap claim: PASS, and a real, currently-live gap.** Read
`docs/next-action.md` directly. It currently states, under peer `c4`: *"Now on `#585`
→ `#530`" (David-approved sequencing) ... Then `#530`'s broader sweep, via parallel
dispatched subagents rather than sequentially."* This is phrased exactly as the
document describes — as if the sweep is still pending — while git history
(section 1-2 above) shows the census merged 2026-09-03, one instance fixed and merged
via PR #552, and this very document about to land a second status update. The
document's flag is accurate and not resolved as of this review either (still no
`ListAgents`/`SendMessage` access from this isolated worktree).

**Issue #530 verified via `gh issue view 530`**: OPEN, body matches the document's
description of the two accidentally-found instances (`reset/routes.py` via #510/PR
#512, `observability/routes.py`) and the explicit ask to check `/api/quality/summary`
— confirms the whole premise of both the census and this document, from GitHub's own
record rather than the document's paraphrase. `PR #512` independently confirmed
merged and titled "reset/routes.py event-loop-blocking research (issue #510)" —
the document's "tracked elsewhere, not re-verified" framing for `reset/routes.py` is
accurate.

**FAIL — a real, verifiable error found in the "Enumeration is still current"
table.** The document's cross-check of which census sources changed since `7c32c2d`
lists five commit SHAs (`e530ff0, 6a0584c, 2eed9a7, 3ad2431, 2617b17`) under
`services/analytics/routes.py`, concluding "all other 16 sources... Unchanged since
the adversarially-reviewed census." Checked every SHA individually with `git show
--stat`:

| SHA | Actually touches |
|---|---|
| `e530ff0` | `services/analytics/routes.py` ✓ (matches doc) |
| `2617b17` | `services/analytics/routes.py` **and** `services/whale_calibration/routes.py` |
| `6a0584c` | `services/whale_calibration/routes.py`, `services/signal_log.py` — **not** analytics |
| `2eed9a7` | `services/whale_calibration/routes.py`, `services/candidate_log.py` — **not** analytics |
| `3ad2431` | `services/signal_log.py`, `services/whale_calibration/routes.py` — **not** analytics |

Confirmed directly with `git log 7c32c2d..HEAD -- services/whale_calibration/routes.py`:
four commits (`3ad2431`, `2eed9a7`, `6a0584c`, `2617b17`) touch this file — one of the
census's 18 sources, flagged there as **5 BLOCKING / 1 SAFE** ("`report` properly
dispatched via `tick_executor.run`; the other 5 aren't"). This file is **not**
mentioned anywhere in the document as touched; it falls silently inside the "all
other 16 sources: No" row.

Read the current source of `services/whale_calibration/routes.py` to check real
impact: `report` and `apply` (two of the census's five originally-BLOCKING routes)
now call `_build_report_async()`, which does `await
signal_log.resolved_signals_with_factors_async()` and `await asyncio.to_thread(...)`
— genuinely dispatched, under the #410 track (`6a0584c`'s own message: "move
calibration report off tick_executor, both halves off-loop (#410)"). `enable`,
`disable`, `status`, and `history` remain plain synchronous calls with no dispatch —
still BLOCKING.

**Why this matters**: the document's "Verdict on the census's remaining ~62 BLOCKING
instances" section treats that population as static ("no code change since the
adversarially-reviewed census... nothing about them changed"). That's false for at
least `services/whale_calibration/routes.py` — real dispatch fixes landed there since
the census, under a different issue's track, the same pattern the document correctly
caught for `services/analytics/routes.py`'s `population_gate_summary` case but missed
here because the commits were misattributed to the wrong file. This is exactly the
kind of "was this instance already fixed under a different issue" question the
document set out to answer and, for this one file, got wrong. It does **not** affect
either of the document's two headline, live-verified conclusions
(`quality/summary` fixed, `observability/routes.py` still broken) — both were
re-measured directly against the running app, not inferred from this table.

The `services/analytics/routes.py`/`GET /api/candidate-log/summary` →
`population_gate_summary` → issue #410 attribution itself is independently confirmed
correct: `grep -n "population_gate_summary" services/analytics/routes.py` shows the
route now calls `population_gate_summary_async` with `await`.

## Overall verdict

**The document's headline, most load-bearing claims are trustworthy and hold up
under independent re-derivation**: the prior census genuinely exists and is merged to
`main`; PR #552's fix is real, merged, and verifiably on disk (all 7 calls dispatched);
the live timing numbers for both `/api/quality/summary` and
`/api/observability/summary` (default and `hours=720`) are reproducible within normal
variance; and the concurrency test — the document's single strongest piece of
evidence — reproduces cleanly and unambiguously: `/api/state` stays fast next to the
fixed route and is fully stalled next to the still-broken one. The `c4`/`next-action.md`
coordination-gap claim is real and independently confirmed live. The self-review's
one flagged open question (`record_variant()`'s async-caller status) is now resolved:
confirmed the same defect class, reached from `trading_loop()`, an `async def`
function.

**One real error survives independent scrutiny**: the "Enumeration is still current"
table misattributes three commits to `services/analytics/routes.py` when they
actually modify `services/whale_calibration/routes.py`, causing the document to
silently miss that this census source (5 BLOCKING / 1 SAFE) has had at least two of
its BLOCKING routes fixed since 2026-09-03 under issue #410's track. This makes the
document's blanket "62 remaining BLOCKING instances, unchanged" framing inaccurate
for at least one file and means the true remaining-BLOCKING count is lower than
implied. This is a real defect in the document's rigor, not a nitpick — it's the
exact failure mode ("assuming unchanged without checking") the document otherwise
guards against successfully. It does not undermine the document's answer to issue
#530's actual named questions (`/api/quality/summary` and
`services/observability/routes.py`), which are independently verified correct here.

**Recommendation**: fix the "Enumeration is still current" table (attribute
`6a0584c`/`2eed9a7`/`3ad2431` to `services/whale_calibration/routes.py`, not
`services/analytics/routes.py`) and add one line noting that file's BLOCKING count
has dropped from 5 to (at most) 4 under #410, before this document is treated as the
closing word on "what in the census's 63-count is still open." Everything else in the
document stands.

## Dimensional analysis

All timings in this review are wall-clock seconds from `time.perf_counter()` deltas
around single or concurrent HTTP request/response cycles, same units and method as
the document under review, obtained independently rather than copied. No unit
conversions performed. No money/probability/contract-count arithmetic appears in this
review.
