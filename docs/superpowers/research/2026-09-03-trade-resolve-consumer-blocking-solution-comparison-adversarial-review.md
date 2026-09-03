# Adversarial review: trade-resolve consumer-blocking solution-comparison doc

Independent pass per CLAUDE.md's "nothing advances on one pass" HARD RULE — fresh
Agent call, no memory of the authoring session. Every load-bearing claim below was
re-derived from primary sources (current source at this branch's HEAD `f0b1903`,
`git log`/`git show`/`git merge-base`, live `GET` calls against
`https://kalshi-whale-poc.ddev.site:8443`, and `gh issue view --json` for #541/#542),
never taken from either document's own tables, prose summary, or citations. Read-only
throughout — no code, config, `config/settings.yaml`, or `data/*.db` writes; no
restart/reload; no edits to either document under review.

## Method

1. Checked out `origin/docs/trade-resolve-blocking-solution-comparison` detached
   (`f0b1903`) in this isolated worktree.
2. Read both documents under review in full: `2026-09-03-trade-resolve-consumer-
   blocking-solution-comparison.md` (the comparison) and its `-self-review.md`.
3. Read the house-style reference (`2026-09-03-worker-cpu-pin-and-loop-stalls-
   adversarial-review.md`) for format.
4. Pulled issues #541 and #542 with `gh issue view <n> --json
   title,body,state,createdAt,comments` — full body + every comment, not `--comments`
   alone (which omits the body) — and read every word rather than trusting the
   comparison doc's paraphrase.
5. Diffed the self-review's own commit against the doc's prior commit
   (`git diff cb80392 f0b1903 -- <doc>`) to verify exactly what the self-review
   changed, rather than trusting its own bullet list of what it claims to have fixed.
6. Re-read every source file the comparison doc cites as evidence for its central
   mechanism, in full or by targeted `grep -n`/`Read`: `services/whale_stream/
   whale_stream_handlers.py`, `services/whalewatchers/kalshi_trade_tape.py`,
   `services/whalewatchers/_scoring_pool.py`, `services/candidate_retry.py`,
   `services/kalshi/websocket.py`, `services/http_client.py`, `services/exits/
   exit_engine.py`, `services/strategy_engine.py`, `main.py`, `config/settings.yaml`,
   `docs/next-action.md`.
7. Made my own live `GET` calls (not reused from the doc) against
   `/api/observability/summary?hours=0.15`, `/api/observability/summary` (24h),
   `/api/observability/history` (resolve `window_avg_ms`/`window_max_ms`, n=644),
   `/api/health/pipeline`, and `/api/state`, at 2026-09-03T21:50–21:56Z — roughly
   30–40 minutes after the doc's own ~21:15–22:00Z window, on a **different** process
   generation (the container reloaded again at 21:48:09Z for unrelated observability.py
   work; confirmed via `docker logs` timestamps) — specifically to check whether the
   claimed *relationships* and *order of magnitude* survive a generation change, not to
   reproduce identical numbers.
8. Re-ran `git log -1 --format="%ci %s" 3c3ff67`/`e5bf56b` and `git merge-base
   --is-ancestor e5bf56b 3c3ff67` directly against current repo history.
9. Traced the `opened_since` guard's actual call graph (`strategy_engine.py` →
   `exit_engine.check_exits`) rather than trusting the doc's file citation for it.
10. Traced whether `_scoring_pool`'s 4-worker pool already receives concurrent
    submissions today from two independent call paths (the WS stream consumer and
    `_candidate_retry_loop`), since this bears directly on the "never guess" instruction
    to check whether something *else* makes the document's concurrency-race reasoning
    unsound in either direction.

## Findings

### F1 — CONFIRMED (source, strongest tier): the central mechanism is exactly as described

Read `services/whale_stream/whale_stream_handlers.py` end to end.
`_process_stream_trade` (lines 184–260) calls, unconditionally on every trade message
that survives the trade_id/state checks:

```python
signals = await whale_provider.fetch_signals(
    market_context={"markets": state["markets"], "trade_tape": [trade], "cfg": cfg_now, "client": ...},
)
```

`trade_tape=[trade]` — a genuine single-element list, byte-for-byte matching the
doc's quote. `_streaming_trade_tape_enabled()` (lines 99–104) is exactly
`whale_provider.name == "kalshi_trade_tape" and trade_stream.enabled`, and
`trade_stream.enabled` (`services/kalshi/websocket.py:424–426`) is exactly
`self._private_key is not None and bool(self.key_id)` — real WS credentials loaded.
Live-confirmed right now: `GET /api/state` → `whale_source: "kalshi_trade_tape
(websocket)"`, `trade_stream_status: {enabled: true, connected: true, mode: "stream"}`.
This is the app's normal running state, not an edge case.

`_resolve_unknown_markets` (`kalshi_trade_tape.py:367–498`) does await
`client.get_markets_by_tickers(batch)` inline (line 469) before `fetch_signals`
returns, and the sole consumer draining the queue that carries trade messages
(`_consume_market_from`, `websocket.py:1127–1155`, spawned exactly once per queue at
`websocket.py:672–693`) does not resume `queue.get()` until `_process_item` — and
therefore the entire `await whale_provider.fetch_signals(...)` chain — completes. The
doc's central structural claim is exactly right, and is the strongest evidence tier
under CLAUDE.md's data-plane HARD RULE (direct source state-transition proof).

### F2 — CONFIRMED (source, live telemetry): the 1:1 no-batching claim, live right now on a different process generation

My own live pull (`/api/observability/summary?hours=0.15`, 2026-09-03T21:5x UTC, a
generation that started later than the doc's, per `docker logs`):

```
kalshi_rest_class.background_catalog.calls  {count:5, min:62,  max:176, avg:89.0}
kalshi_rest_class.critical_whale.calls      {count:5, min:42,  max:161, avg:79.6}
whale_pipeline.counter.offlist_candidates   {count:5, min:42,  max:161, avg:79.6}
whale_pipeline.counter.resolve_calls        {count:5, min:42,  max:161, avg:79.6}
```

`offlist_candidates == resolve_calls == critical_whale.calls` on **every one** of
min/max/avg/count, on a live pull the authoring session never saw, on a process that
wasn't even running when the doc was written. This independently reproduces the
self-review's corrected ("not just averages — min/max/avg all identical") version of
the claim, not just the original draft's weaker "avg 87" version — the self-review's
fix here was correct and the underlying phenomenon is robust, not a one-off artifact
of the specific sampling window the doc happened to catch.

`resolve.window_avg_ms`/`window_max_ms` 24h history (n=644, vs. the doc's n=633):
median 0.587ms / 1405.19ms, p90 1.44ms / 3012.15ms, max 96.86ms / 15379.21ms — the
**max values are identical to 4 significant figures** against the doc's table
(96.8581 vs. "96.86", 15379.2078 vs. "15379.21" — same underlying spike, still inside
the 24h window), median/p90 within a few percent (expected drift from ~11 more
samples). `critical_whale.network.window_max_ms` 24h: my pull gives max
44,580.2362ms against the doc's cited 44,580.24ms — matches to the reported
precision. All of this confirms not just the order of magnitude but the *specific*
historical events the doc's argument leans on are still the same events, re-derivable
independently.

### F3 — CONFIRMED (source): the ticker/trade shared-consumer finding (§1.1) is real, live, and correctly cited

`_CRITICAL_CLASSES = frozenset({"fill", "position", "lifecycle", "control"})` at
`websocket.py:135` — exact match. `config/settings.yaml:72` is `two_consumer_mode:
true` — exact match, and independently confirmed *live* (not just present in the
file) via `/api/health/pipeline`'s `queue.coalesced_tickers: 4233` field, which only
exists when `two_consumer_mode` is actually active. `_ingest_raw` (`websocket.py:
1021–1065`) routes "ticker" through `_coalesce_ticker` into `market_queue` and every
non-critical class (including "trade") through the same `market_queue` via
`queue = critical_queue if cls in _CRITICAL_CLASSES else market_queue`. Exactly one
consumer (`_consume_market_from`) drains it. The doc's §1.1 finding — that a
multi-second resolve stall on a trade message also delays the next queued ticker
update — is a direct, correct read of the current source.

### F4 — CONFIRMED (source): `main.py`'s tick-loop call site is genuinely mutually exclusive with the streaming call site, and genuinely batchable

`main.py:1216–1225`: `if _streaming_trade_tape_enabled(): state["whale_source"] = ...`
(no `fetch_signals` call at all) / `elif whale_provider.enabled: ... await
whale_provider.fetch_signals(market_context={..., "trade_tape": trade_tape, ...})`
at line 1221 — the **full** tick's trade tape, not a single trade. Confirmed: today,
when streaming is live (the normal state), the tick-loop's `fetch_signals` call is
never reached at all — exactly one call shape is live at a time by construction. This
also directly supports (independent of the doc, from source alone) the invariant
`_process_trades_sync`'s own docstring leans on: the *tick-loop* path and the
*streaming* path cannot both call `fetch_signals` concurrently. See F8 below for why
this invariant is nonetheless incomplete.

### F5 — CONFIRMED (source): git commit-ordering proof, re-run independently

```
git log -1 --format="%ci %s" 3c3ff67 → 2026-08-17 00:18:31 -0500 "Subscribe the trade
  websocket exchange-wide, and resolve off-watchlist markets on demand"
git log -1 --format="%ci %s" e5bf56b → 2026-08-11 23:12:05 -0500 "Add Kalshi
  streaming and enriched metadata UI"
git merge-base --is-ancestor e5bf56b 3c3ff67 → true
```

Matches the doc's (and #542's own comment's) claim exactly.

### F6 — MUST-FIX (source, strongest tier): Option B's correctness section significantly undercounts the shared-mutable-state hazard — it is not limited to `_resolve_failed_tickers`, and it is not purely hypothetical/introduced-by-Option-B

The doc calls the `self._resolve_failed_tickers = set()` unconditional reset
(`kalshi_trade_tape.py:401`) "the load-bearing finding of this comparison," fixes it
as a must-fix precondition for Option B, and separately dismisses two other pieces of
shared mutable state:

> `self._seen_trade_ids` (mark-seen ring) and `self._market_cache` (the TTL cache)
> both look safe under concurrent access by inspection — pure dict/set reads and
> writes with no cross-call reset — but were not exhaustively audited here.

I did the audit the self-review explicitly deferred. The result contradicts "look
safe by inspection" for `_seen_trade_ids`/`_seen_order` specifically (not
`_market_cache` — see below), using direct source evidence, not speculation:

**`services/whalewatchers/_scoring_pool.py`'s own module docstring** (lines 1–19)
states the shared 4-worker `ThreadPoolExecutor` exists for "**both** the WS-message
path, `_process_stream_trade -> fetch_signals`, **and** the candidate-retry path,
`score_recovered_trade`" and explains its sizing as "headroom for legitimate brief
overlap **plus the candidate-retry path**" — i.e., the pool's own design explicitly
anticipates and accommodates concurrent execution between these two call paths.

**`kalshi_trade_tape.py`'s `_process_trades_sync` docstring** (lines 503–513) claims
the opposite: "Safe to run on a worker thread: `self._seen_trade_ids`/`self.
_seen_order` are only ever touched from within **one in-flight `fetch_signals()`
call at a time** (the trading loop awaits each tick's whale-provider call before
starting the next)."

These two docstrings, in the same package, directly contradict each other.
`score_recovered_trade` (`kalshi_trade_tape.py:342–364`) calls `_process_trades_sync`
**directly** — it is not a `fetch_signals()` call at all, so the safety argument's own
stated scope ("one in-flight `fetch_signals()` call") doesn't cover it by its own
wording, regardless of intent.

Tracing the actual call graph: `_candidate_retry_loop` (`main.py:777–811`) is
launched as its own independently-supervised task
(`task_supervisor.supervise(_candidate_retry_loop, ...)`, `main.py:1435–1436`),
running concurrently with the WS stream's own supervised task
(`trade_stream_task = task_supervisor.supervise(lambda: trade_stream.run(...))`,
immediately below it in the same function). It wakes every
`_SCHEDULER_TRIGGER_INTERVAL_SEC` (5.0s, `main.py:545`) and, when
`_streaming_trade_tape_enabled()` (true right now, per F1) and work is pending, calls
`candidate_retry.run_pending(...)`, which for each recovered trade does `await
client.get_markets_by_tickers([ticker])` (its own, separate REST resolve — not
`_resolve_unknown_markets`, so `_resolve_failed_tickers`/`_market_cache` are
genuinely untouched by this path) and then `await provider.score_recovered_trade(...)`
— which submits to the **same** `_scoring_pool` the WS stream path uses
(`services/candidate_retry.py:167`).

Net: **today, with zero code changes and independent of anything Option B
introduces**, the WS stream's single-consumer `_process_trades_sync` call and
`_candidate_retry_loop`'s `_process_trades_sync` call (via `score_recovered_trade`)
can already run concurrently on two different worker threads of the same
4-worker pool, both mutating the same unlocked `self._seen_trade_ids`
(`set.add`/`in`) and `self._seen_order` (`deque.append`/`popleft`, with an
eviction loop that reads `len()` then conditionally mutates both structures) —
exactly the class of hazard the doc identified for `_resolve_failed_tickers`, via a
call path (`score_recovered_trade`) that already exists in production, gated live
right now (`_streaming_trade_tape_enabled()` is true).

I did not reproduce actual data corruption live — that would require either a code
change (out of scope for this review) or catching a rare timing window in production,
and CPython's GIL does make each individual `set.add`/`deque.append`/`deque.popleft`
call atomic, so the failure mode is a narrow interleaving inside `_mark_seen`'s
multi-statement eviction check, not a certainty on every overlap. This finding is
therefore source-derived reasoning from two directly-contradicting docstrings plus a
traced call graph (a strong tier under "never guess," short of empirical
reproduction), not a live-reproduced bug.

**What this changes:** `_market_cache` genuinely is safe as characterized — it is
only ever touched inside `_resolve_unknown_markets`, which is only ever called from
`fetch_signals()`, which (per F4) has exactly one live caller at a time today. The
self-review's blanket "both... look safe... not exhaustively audited" should be split:
`_market_cache` — audited here, confirmed safe under today's call graph.
`_seen_trade_ids`/`_seen_order` — audited here, found **already exposed** to a
concurrency shape neither document identified, that predates and is independent of
Option B. The design/spec stage should treat this as a real, currently-latent
correctness gap worth its own scoped fix (likely a lock around `_mark_seen`, or
restructuring the dedupe ring to tolerate concurrent callers) — not solely bundle it
into "the must-fix precondition for Option B," since fixing only `_resolve_failed_
tickers` for Option B would leave this hazard in place, unaddressed, exactly as
today.

### F7 — SHOULD-FIX (source): the self-review overclaims its own fix count — "four instances" is not supported by its own diff

The self-review's first bullet claims: "Wrong issue attribution, **four instances**.
The intro, §3's benchmark, §5's benchmark, and **§4's precedent reference** all
attributed [it] to '#542's own comment thread'... Corrected all four references."

`git diff cb80392 f0b1903 -- <the comparison doc>` shows exactly **three** such
corrections (intro line, §3's benchmark line, §5's benchmark line — all `#542's own`
→ `#541's own`). There is no fourth diff hunk changing an issue-attribution error in
§4. The only change inside §4 in that diff is a **different**, correctly-described
fix (a section cross-reference, `(§2 above)` → `(§1.1 above)`, itself accurate and
already listed as the self-review's *second*, separate bullet). The self-review's own
bullet 1 conflates its bullet 2's fix with a fourth instance of bullet 1's fix that
does not exist in the commit. This is a real inaccuracy in the self-review's own audit
trail — not a technical error in the comparison doc itself, but exactly the kind of
unverified self-description CLAUDE.md's "a claim ships with its evidence" line warns
about, caught here only by diffing the actual commit rather than trusting the
self-review's prose.

### F8 — SHOULD-FIX (source): two residual `#541/#542` dual-citations for analysis that lives only in #541

Read both issues' bodies and every comment in full (`gh issue view --json`, not
`--comments`, so the body is included). Two places in the comparison doc still cite
both issues jointly for material that is specifically and only in #541:

- §1.2 (line 110): "...the sharpest queue-depth jump in `#541/#542`'s
  incident-window analysis." The specific correlated event (`18:58:08` resolve/
  queue-depth-jump timestamp match) is entirely inside #541's second comment ("(a)
  resolved: link to #542 tested against a reconnect-free stretch"). #542 never
  discusses queue-depth timestamps or the reconnect-free-window falsifier at all —
  its own comment is a *different* analysis (resolve-volume vs. design baseline).
- §7 (line 358): "...exactly the manual falsifier `#541/#542`'s own comment thread
  used to distinguish 'resolve-blocking' from 'restart resubscribe surge'." The
  "restart resubscribe surge" hypothesis and its falsifier (reconnect timing vs.
  queue-depth saturation) is posed and tested exclusively in #541's body and its
  second comment. #542's own "Falsifiers" section (in its body) tests something
  different (whether the resolve tail is rate-limit-driven vs. genuine network
  latency) and never mentions restarts or resubscribe surges.

Both read as defensible in isolation ("the incident, tracked across both issues") but
both are less precise than the self-review's own standard for the three instances it
did fix, and both should have been caught by the same grep-based check
(`gh issue view <n> --json comments --jq '...' | grep -c 'reconnect-free|falsifier'`)
the self-review used to find the first three. Combined with F7, this suggests the
self-review's citation sweep was not as exhaustive as its own text implies.

### F9 — SHOULD-FIX (source): `CALLER_CLASSES` has 9 members; the doc's enumeration is missing one (`background_discovery`) and undercounts "other classes"

`grep -n` of `services/http_client.py`'s `CALLER_CLASSES` tuple (lines 36–50) gives
**nine** entries: `critical_whale`, `critical_position`, `interactive`,
`background_discovery`, `background_catalog`, `background_live_status`,
`background_resolution`, `background_index_backfill`, `other`.

The comparison doc's §5 states: "one shared `_TokenBucketRateLimiter`... across all
**six** read-side caller classes: `critical_whale`, `critical_position`,
`interactive`, `background_catalog`, `background_live_status`,
`background_resolution` (plus `background_index_backfill`, `other`)" — 8 named
total, `background_discovery` never appears anywhere in either document, despite the
doc's own appendix claiming a direct `Read`/`grep -n` of `CALLER_CLASSES`. §3's
benchmark separately says N=4 concurrent resolves compete "against the shared 8 req/s
burst-8 token bucket... shared with 5 other classes" — actually 8 other classes, not
5. Both figures trace back to issue #542's own comment ("shares one global
read-token bucket... with 5 other REST classes: `critical_position`, `interactive`,
`background_catalog`, `background_live_status`, `background_resolution`") — the
research doc inherited #542's own miscount rather than independently re-verifying
`CALLER_CLASSES` against current source, exactly the "never guess... citing my own
prior work" failure mode the self-review's F7-area fixes were meant to catch, just
missed here. Doesn't change the qualitative conclusion (still real, multi-class
contention over one bucket; Option C is still valid) but the count should be
corrected to 9 total / 8 other classes.

### F10 — SHOULD-FIX (source): the `opened_since` stale-price guard is cited to the wrong file, and its purpose is somewhat overstated

§3's ordering discussion says: "The `opened_since=now` stale-price guard pattern
(`strategy_engine.py`, referenced from `_process_stream_ticker`'s own comments...) is
the existing tool for auditing whether any downstream consumer assumes
trade-before-ticker ordering across *different* tickers."

Traced the actual implementation: `strategy_engine.py`'s `check_exits` (lines
872–892) is a thin passthrough — its own docstring says "real implementation now
lives in `services/exits/exit_engine.py`" — and the guard condition itself is at
`exit_engine.py:232–233`: `if opened_since is not None and pos.opened_at >=
opened_since: continue`. The citation should be `services/exits/exit_engine.py`, not
`strategy_engine.py`.

More substantively: `exit_engine.py`'s own extensive docstring (lines 160–179)
describes this guard's actual, documented purpose as protecting against a
**same-tick, poll-order** staleness bug (a live 2026-08-11 incident: a position
opened this tick can have an `entry_price` newer than the tick's own already-fetched
`latest_prices` snapshot, producing a fabricated mark-to-market swing) — not
specifically a tool for auditing cross-*ticker* ordering assumptions across the WS
stream's downstream consumers, which is how the comparison doc frames it. The guard
is real, is genuinely reused on the WS streaming call sites, and is a reasonable
precedent to point to — but its documented purpose is narrower than "the existing
tool for auditing" implies, and the self-review's own explicitly-flagged open item
("worth an independent check... did not re-open `strategy_engine.py` itself") turns
out to have been looking in the wrong file to begin with.

### F11 — NICE-TO-HAVE (dimensional-analysis tier): §5's "5–15x" figure is closer to "5–18x" against the doc's own cited numbers

§5: "a real, roughly 5-15x improvement to the common case," comparing "the current
1.4s resolve-call median" (i.e., §1.2's `resolve.window_max_ms` median, 1,421.35ms)
against `critical_whale.network.window_avg_ms`'s "measured 78.79-290.02ms."
1421.35 / 290.02 ≈ 4.9x; 1421.35 / 78.79 ≈ 18.0x. The stated range's lower bound
(5x) is fine; the upper bound underreports the doc's own numbers by about 3x
(should read closer to "5–18x"). Doesn't change Option C's conclusion (still a real
common-case win, still leaves the tail untouched) — a rounding/arithmetic slip
CLAUDE.md's dimensional-analysis HARD RULE would have caught had this specific ratio
been run through it explicitly rather than eyeballed.

## Self-review cross-check

The self-review's three genuinely-executed fixes (intro/§3/§5 issue attribution,
§4's `(§2 above)` → `(§1.1 above)` cross-reference, the §1 "all four figures
identical" strengthening) all independently re-verify as correct (F2, F5). Its
"checked and found correct" list holds up on independent re-check for everything I
re-derived (the `_scoring_pool.py` docstring trim, the commit-ordering proof, the
completeness-constraint re-read). Its "not fixed, flagged for adversarial review"
section correctly anticipated exactly the two areas that turned out to matter most —
the `opened_since`/ordering citation (F10) and the shared-mutable-state audit (F6) —
but underestimated both: F10 wasn't just unverified, the file citation was wrong; F6
wasn't just "not exhaustively audited," it's contradicted by direct evidence
(`_scoring_pool.py`'s own docstring) sitting in the same package. The self-review's
own claimed scope of its issue-attribution fix (F7) is the one place this review
found the self-review's *own* work, not just the underlying doc, to be inaccurate.

## Verdict: GO-AFTER-FIXES

The document's central, load-bearing claims — the consumer-blocking mechanism
(F1), the live 1:1 no-batching fact and its robustness across a process restart
(F2), the shared trade/ticker consumer finding (F3), the mutually-exclusive
tick-loop-vs-streaming call shape (F4), and the commit-ordering proof (F5) — all
**fully hold** under independent re-derivation from primary sources: direct current-
source reads with exact line-number matches, live telemetry pulled fresh on a
different process generation than the one the doc measured, and re-run git commands.
This is the data-plane HARD RULE's strongest evidence tier throughout, and nothing in
this review weakens the top-line recommendation (Option B addresses the root
mechanism; Option A is a refinement layered on B, not a standalone alternative;
Option C is a valid, low-risk complement that doesn't touch the tail).

What keeps this from GO is one finding with real teeth (F6) plus a cluster of
citation/scope inaccuracies (F7–F10) that a design/spec stage could otherwise inherit
uncritically:

### Must-fix

1. **Expand Option B's correctness section beyond `_resolve_failed_tickers`.**
   `_seen_trade_ids`/`_seen_order` are not "safe by inspection, unaudited" — they are
   already exposed to real concurrent mutation today, independent of Option B, via
   `_candidate_retry_loop`'s `score_recovered_trade` path sharing `_scoring_pool` with
   the WS stream consumer. `_scoring_pool.py`'s own docstring says this overlap is
   deliberate; `_process_trades_sync`'s own docstring's safety claim doesn't account
   for it. This needs its own scoped fix (a lock, or a concurrency-safe dedupe-ring
   restructure) — bundling it silently into "the Option B precondition" risks it being
   scoped only for Option B's new concurrency and missed as the pre-existing issue it
   already is — F6.

### Should-fix

2. Correct the self-review's own "four instances... corrected all four" claim — the
   diff shows three; §4 never had an issue-attribution error to fix (only the
   separate, correctly-described section-cross-reference fix) — F7.
3. Fix the two residual `#541/#542` dual-citations (§1.2 line 110, §7 line 358) to
   `#541` alone — the specific analyses they describe (the queue-depth-jump
   correlation, the restart-resubscribe-surge falsifier) live only in #541 — F8.
4. Correct `CALLER_CLASSES`'s count: 9 members, not the 8 named across §5 (missing
   `background_discovery`); §3's "shared with 5 other classes" should read "8 other
   classes" — both inherited uncritically from issue #542's own miscount rather than
   independently re-verified against current source, despite the doc's appendix
   claiming a direct read — F9.
5. Re-cite the `opened_since` stale-price guard to `services/exits/exit_engine.py:
   232-233` (not `strategy_engine.py`, a thin passthrough), and narrow its
   characterization to match its own docstring's actual scope (same-tick poll-order
   staleness, not a general cross-ticker-ordering audit tool) — F10.

### Nice-to-have

6. Correct §5's "5-15x" to "~5-18x" against the doc's own cited figures — F11.

None of the above require re-running the mechanism investigation or revisiting the
top-line recommendation, which this review treats as independently confirmed. Items
2–6 are corrections to claims already re-derivable from evidence this review (and the
original doc) already gathered; item 1 is new scope for the eventual design/spec
stage, not a re-investigation of anything already settled here.
