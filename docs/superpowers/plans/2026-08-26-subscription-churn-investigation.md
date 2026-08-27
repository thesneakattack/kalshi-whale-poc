# WebSocket Subscription-Churn Investigation — Execution Plan

> **Agentic execution:** Use `.claude/skills/realtime-data-plane-investigation/SKILL.md`
> as the orchestrator (this is a follow-on thread of that same investigation, not a
> separate initiative). Execute exactly one numbered task, verify, commit, report, and
> stop.

**Goal:** Determine whether market-discovery-driven WebSocket subscription churn
(Hypothesis H11) is a real, material bottleneck — and if so, select an evidence-backed
fix — rather than assume the user's report names the mechanism correctly.

**Finding so far:** `docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md`,
Hypothesis H11 (added 2026-08-26). Mechanism traced, churn confirmed real but bursty
(~15s cadence, not per-tick), first observability added and live-verified (PR #37,
merged). No cost measurement, no root cause for the co-occurring instability event, and
no architecture decision exist yet — that is what this plan covers.

## Global constraints

- Current HEAD is implementation truth; re-ground against it at the start of every task.
- Real trading stays disabled; this is dev-instance measurement only.
- No tests against live `data/*.db`.
- No subscription/queue/rate/worker tuning before CH3 classifies a real bottleneck.
- No structural production redesign before CH5's solution-comparison step.
- `docs/kalshi/` is authoritative for any Kalshi WS/REST semantics question that comes up
  (per `.claude/rules/kalshi-integration-authority.md`).
- One task = one independently reviewable commit.
- If CH1/CH2 falsify H11 (churn burst is not the cause of the observed instability),
  record that as a real result and stop — a negative result is a valid outcome, not a
  reason to keep digging for a way to confirm the original report.

---

## CH1 — Measure a churn burst's actual downstream/upstream cost

**Read**
- `services/kalshi/websocket.py`'s `_sync_subscriptions`/`ingest_metrics` (the
  `subscription_churn` counters added in PR #37)
- `docs/kalshi/websocket-connection.md` (subscribe/update_subscription semantics,
  `send_initial_snapshot` behavior)
- current `data/observability.db` history for `*.ingest.subscription_churn.*`,
  `*.ingest.queue_depth`, `*.ingest.queue_wait.*`

**Questions to answer, each with a stated confirm/falsify criterion**
- [ ] How many raw WS frames does one `_sync_subscriptions` call actually send? (Sent
  once per market-channel sid — trade + ticker in scoped mode — so an N-ticker diff is
  N tickers × up to 2 messages, not N messages; confirm this against the real code path,
  don't assume.)
- [ ] Does Kalshi's `send_initial_snapshot: True` behavior on the `ticker` channel apply
  per newly-added ticker, or only on first subscribe? Check the exact mirrored doc, not
  memory.
- [ ] Does a churn burst measurably correlate with a rise in
  `trade_stream.ingest.queue_depth` / `queue_wait` / handler latency in the same or
  immediately following window? Use `data/observability.db`'s own history plus a live
  correlated sample if the dev instance is running.
- [ ] Does a churn burst add measurable REST demand (e.g. via `_resolve_unknown_markets`,
  `_fetch_live_status`, or hydration calls) beyond what's already bounded/cached?
- [ ] Commit: `docs: measure subscription-churn burst cost (CH1)` — update the H11 finding
  with real numbers, not another hypothesis.

**Acceptance**
A reader can see actual measured cost (or "measured: negligible") for a churn burst, not
architectural speculation.

**Scale caveat (2026-08-27):** this measurement is bounded to today's live watchlist scale
(8-13 tickers). `docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md`'s
Phase P3.5 (Task 17a/17b) later stresses the same `trade_stream.ingest.subscription_churn.*`
counters this measurement uses, at a real widened-scope scale, and feeds that larger-scale
data point back here (and into CH3, below) - see that phase's own header note for the
reuse contract.

---

## CH2 — Root-cause the still-untraced third instability event

Use `root-cause-debugging` explicitly — this is exactly its trigger (unexpected/live
incident, contradictory behavior).

- [ ] Reproduce the app-unresponsiveness event live (the same `SYS_PTRACE`/`py-spy`
  method used for the two bugs fixed in PR #35/#36 this session).
- [ ] Capture a stack trace during the stall; identify the actual blocking call.
- [ ] Classify the result explicitly: (a) the same shape of bug as PR #35/#36 in a third,
  unrelated module: (b) directly caused by subscription-churn/H11's mechanism; (c)
  something else entirely.
- [ ] If (a) or (c): fix or document following this session's established pattern
  (`tick_executor` offload, or SQL-side aggregation if the bottleneck is Python-object
  construction over a large row count — see PR #35's own lesson that a thread offload
  alone doesn't help GIL-bound work).
- [ ] If (b): do not fix yet — feed the confirmed mechanism into CH3/CH4 below instead of
  patching reactively.
- [ ] Commit: `fix: <root cause>` or `docs: root-cause the third instability event (CH2)`,
  whichever applies.

**Acceptance**
The instability event has a proven cause, not a guess, and this plan's classification of
H11 (CH3 below) rests on that proof rather than coincidence.

---

## CH3 — Reconcile CH1 + CH2 into a classification of H11

- [ ] State plainly: is subscription churn (i) a confirmed, material bottleneck, (ii) a
  real but currently-negligible cost, or (iii) unrelated to the observed instability?
- [ ] Update H11 in the known-findings doc with this classification and the evidence
  behind it.
- [ ] If (ii) or (iii): stop this plan here. Record why, and what would change that
  classification later (e.g. a larger watchlist, a wider category set, more concurrent
  live events). Do not proceed to redesign work against an unconfirmed or negligible
  bottleneck.
- [ ] If (i): proceed to CH4.
- [ ] Commit: `docs: classify H11 (CH3)`.

**Not necessarily final once (ii)/(iii) stops the plan (2026-08-27):** the realtime-
data-plane-remediation plan's Phase P3.5 (Task 17a/17b) runs after this task in current
Track A sequencing and is the literal "larger watchlist" experiment this bullet names -
it reuses this investigation's own `subscription_churn` counters at real widened-scope
scale and, per its own Step 5, posts a dated addendum here (reopening or confirming this
classification) once it lands. If that addendum hasn't been added yet, treat this
entry's stop as provisional pending P3.5, not permanent - check for it before assuming
CH4/CH5 are still out of scope.

**Acceptance**
A reviewer can tell, from the doc alone, whether the rest of this plan should ever run.

---

## CH4 — Research and benchmark solution families *(only if CH3 = confirmed bottleneck)*

**Note (2026-08-27):** a broader, churn-independent live watchlist-scale stress test now
exists at `docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md`'s Phase
P3.5 (`tools/watchlist_scale_stress_test.py`), sequenced ahead of that plan's P4/P5. If
CH4 ever runs, reuse that tool rather than building a second scale-up harness — P3.5
answers the general queue-depth/REST-demand/loop-health question at scale, CH4 (if it
ever becomes relevant) would still need its own churn-specific benchmarking on top of it.

Follow the parent skill's solution-selection workflow exactly (enumerate ≥3 families,
research current authoritative practice, prototype outside the production path, benchmark
against the same representative workload, fault-inject burst traffic/reconnect/queue
pressure). Do not conclude "batch discovery less often," "cap churn," "hysteresis on
rank changes," or any other specific mechanism before this step runs — those are
candidates to evaluate, not a foregone conclusion.

- [ ] Enumerate candidate families (e.g.: dampen `round_robin_select`'s output with
  hysteresis/minimum-dwell-time; separate a stable "core" watchlist from a smaller
  rapidly-scanned discovery pool; batch subscription diffs rather than sending them
  the instant they're computed; something else — do not anchor on this list).
- [ ] Prototype each outside the production path.
- [ ] Benchmark against CH1's real measured workload.
- [ ] Fault-inject: burst traffic, a reconnect mid-churn, malformed diff.
- [ ] Score against the design spec's matrix (correctness, capture completeness, latency,
  operational simplicity — same axes the parent investigation already established).
- [ ] Commit: `docs: benchmark subscription-churn solution candidates (CH4)`.

**Acceptance**
At least three real candidates, benchmarked on the same workload, with rejected options
explained.

---

## CH5 — Architecture decision and implementation plan *(only after CH4)*

- [ ] Write the architecture decision (winner, rejected alternatives, why).
- [ ] Invoke `superpowers:writing-plans` to produce a separate, numbered implementation
  plan — this document does not authorize implementation itself.
- [ ] Commit: `docs: subscription-churn architecture decision + implementation plan (CH5)`.

**Acceptance**
A human can review and approve (or reject) a concrete plan before any implementation
task starts.
