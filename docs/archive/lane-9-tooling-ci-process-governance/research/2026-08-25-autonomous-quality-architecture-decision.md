# Autonomous Quality Coordination — Architecture Decision (I10)

**Task:** I10 of `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
**Branch / HEAD at start:** `chore/autonomous-quality-coordination-investigation` @ `a3e6cc9`
(worktree `.claude/worktrees/aqc-investigation`). Re-grounded: synced with `origin/main`, no open
PRs, `origin/chore/realtime-data-plane-investigation` remains the same stale, inactive tip noted in
I8/I9.

Evidence classes: **[E1]** source/CI · **[E2]** git/PR history · **[E3]** deterministic experiment ·
**[E4]** live runtime · **[E5]** upstream docs · **[E6]** inference.

Per the plan's acceptance criterion, this decision is permitted to conclude that report-only is the
correct current architecture — that is not a failure mode of the investigation, it is one of its
two valid outcomes. What follows scores all four candidates on the accumulated I0–I9 evidence,
states a provisional winner, and then subjects that winner to an independent adversarial pass
before anything is finalized.

---

## 1. Scoring matrix — carried forward from I4, updated with I5/I6/I7/I9 evidence

I4 §4 already built the full 12-criterion matrix from platform semantics (I4 §2) and the four
candidate sketches (I4 §3). Reproduced here with three updates made explicit, each citing the task
that produced it:

| Criterion | A Woodpecker write lane | B hybrid | C GitHub-native + Woodpecker verifier | D report-only |
|---|---|---|---|---|
| Contention avoidance (I3 policy, topology-independent) | 4 | 4 | 4 | 5 |
| False-positive escalation risk | 3 | 3 | 3 | 5 |
| False-negative / hidden-defect risk | 3 | 3 | 3 | 2 |
| **Credential exposure** | **1** | 2 | **5** | 5 |
| **Untrusted-PR isolation** | 3 | 3 | **5** | 5 |
| Implementation complexity | 3 | 2 | 3 | 5 |
| Operational complexity | 2 | 2 | 4 | 5 |
| Recovery / idempotence | 3 (design-level; I9 proved the write-gate half is buildable) | 3 | 4 | 5 |
| Observability / explainability | 3 | 3 | 4 | 3 |
| Maintenance burden | 2 | 2 | 4 | 5 |
| Fit with current branch/CI policy | 4 | 3 | 5 | 5 |
| Ability to stage / disable safely | 3 | 3 | **5** | 5 |
| **Veto conditions (I6 §5)** | V1/V2 unless 4 out-of-GitHub controls added | inherits A's | none (V6/V7 are I11 design obligations) | none |

**Updates since I4 (not re-scored, contextualized):**
1. **Deterministic-remediation surface is smaller than assumed at I4's writing.** I7 found exactly
   one survivor (`tools/project_manifest.py --write`) after inspecting every write-capable tool in
   the repo — the frontend bundle, Kalshi docs sync, and every formatter/codemod candidate were
   structurally eliminated. This does not change any cell above (the matrix already scores
   *topology* properties, not remediation-candidate count), but it directly weakens the case for
   building C's `propose` job (draft PRs) now: there is one narrow, low-stakes fixer to run through
   it, not a backlog.
2. **I9 proved the mechanics a write lane would need are achievable**, not that one is needed. The
   event-filter hazard (`branch: main` matching a `pull_request` targeting `main`) is real and
   confirmed against this repo's own live pipeline history (I9 §2.2), and a correct filter that
   avoids it is a one-line `when:` entry, validated against the same real data. The write-gate
   (stale-SHA refusal, idempotent retries) is ~80 lines and 6 tests. Neither finding moves the
   *decision* — they remove "is this even buildable" as an objection to C, which is a precondition
   for C being viable later, not a reason to build it now.
3. **I5's noise economics are the strongest single argument against any write lane today**, and are
   folded into the "False-positive escalation risk" and "Maintenance burden" rows above: even the
   most conservative policy this investigation could design (I3's precedence + I2's floors) still
   produces the volumes I5 §6 measured for anything other than a single, silent,
   escalate-only-at-`ESCALATION_ELIGIBLE` emission — and I2 §6 found **zero** findings that ever
   reached persistence-floor age on integrated `main` in the entire measured window. Under the same
   I2 sample and the same I3 policy, the *policy* — not the topology carrying it — is what produces
   zero escalations; A, B, and C would each have received the identical zero-count input from the
   identical audits. That equivalence is evidence against building a write lane **yet**, on
   cost/benefit grounds; it is not evidence that topology C specifically was tested and found
   wanting, and §3 below should not be read as implying otherwise.

**Two scope caveats on the "zero" finding itself, surfaced by the adversarial review (§5) and
conceded here rather than left implicit:** I2's 41-hour sample is this repository's first day-and-a-
half as a git repo with a QCP scanner framework that was *itself* built and extended inside that same
window (`git log -- tools/quality_audit/` shows the scanner set originating 2026-08-24 and still
growing on 2026-08-25) — Episode A's 0.23h lifetime is partly explained by the same session that
wrote the scanner also fixing what it found in real time, which is not a steady-state guarantee.
Separately, "zero *new* findings" is measured against a baseline that already excludes 193
`accepted_finding_ids` — the metric was never designed to say anything about whether that
pre-existing backlog itself contains something that should be escalated, only whether new drift
appears. Both caveats narrow the confidence behind "zero," not the direction of the decision (see §6
for why they change scope, not the topology choice).

Averages are not the deciding factor (I4 already said so); the load-bearing facts are: **A carries
two open vetoes that require four external controls to close; C carries none, but every property
that makes C safe is also a property D already has by not existing; and the entire investigation's
own measurement (I2) found no case in the sample where D's absence of a write lane actually cost
anything** — subject to the two caveats immediately above.

## 2. Provisional decision

**D (report-only control), extended with two specific, already-scoped enrichments, is the
provisional winner. No write lane (A, B, or C) is adopted at this time.**

Concretely, D as decided here is not "leave everything exactly as it is today" — it is:

1. **A persisted coordinator observation series** — the *policy* I8 proved (I1 identity + I2 floors
   + I3 precedence), run read-only against every `main` audit, writing its state history to a new
   `data/*.db` file (per the repo's own persistence idiom) rather than to GitHub. This gives a human
   or a future Claude session exactly what I2's replay had to reconstruct by hand — first-seen time,
   observation count, current state, full explanation log — as a queryable artifact, with **zero**
   GitHub writes and **zero** new credentials. **Not a direct port of I8's literal code**, which is
   explicitly labeled EXPERIMENTAL/THROWAWAY and was never written against this repo's persistence
   discipline — I11 must design this store fresh, subject to the same rules every other `data/*.db`
   file in this repo already follows: `.claude/skills/persistence-safety`'s checklist, `DB_PATH`
   test isolation, a row in `/api/health/storage`'s inventory, and the "first-class asset, not
   disposable state" rule in `CLAUDE.md` (which names the exact 2026-08-23 incident — a test run
   writing fake rows into a live `data/*.db` — that rule exists to prevent). A store nobody
   monitors for staleness is worse than no store; I11 must also decide how this component surfaces
   its own health (an entry in `/api/quality/summary` or `/api/health/pipeline`, matching this
   repo's existing informativeness pattern for every other background scheduler), not just that it
   persists.
2. **Class-1 SARIF export remains a candidate, not a selection** (I5 §9 already said this — GitHub's
   exact alert-lifecycle semantics were never confirmed against a real upload in this investigation,
   by design: doing so would itself be a write). If adopted later, it needs its own small, explicitly
   approved stage — not bundled into this decision.
3. **No scheduled `main` audit is added yet.** I2 §9 flagged the overnight commit gap (up to 7.5h)
   as a real observation-cadence gap, but closing it only matters once something is waiting to
   observe — today, nothing is.
4. **Any future decision to activate the Issue or draft-PR stage must re-run I2's cadence replay
   against then-current `main`, not cite this document's "zero durable findings" number
   secondhand.** This is the direct fix for the adversarial review's observation that the "zero"
   fact was already being reused across I4→I5→I10 without a re-verification requirement attached —
   stated once here as a binding condition on the next decision, not left to whoever writes it to
   remember on their own.

**Rollout, per the design spec's staged requirement:** the only stage this decision activates is
**dry-run → reporting** (observation series persisted, nothing external). **Issue** and **draft-PR**
stages remain designed (I5's silent-intermediate-state rule, I7's one proven fixer, I8's policy,
I9's event/credential mechanics) but **not activated** — each would need its own future decision
citing new evidence (a real durable-finding episode I2 didn't see, or a demonstrated cost from
running without one), not a default progression on a timer.

## 3. Why not C, given C scored best among the write-lane candidates

C is the correct *next* step **if** a write lane is ever justified — nothing in this decision
weakens that conclusion from I4/I6. It is not selected *now* because nothing in I0–I9 established
need: I2's entire measured sample produced zero durable `main`-level findings requiring escalation,
and the one durable problem in the whole 41-hour window (the `project_manifest.py --check` CI
incident) was a status failure a human fixed from existing signals in 12.4h — not a case a
`QualityFinding` coordinator would ever have seen, because it was never a finding. Building C's
`escalate`/`propose` jobs now would be automation in search of a problem, which is precisely what
the evidence rule's "optimize for durable quality enforcement... not maximum autonomy" opens with.

---

## 4. Independent adversarial review — process

Dispatched as an isolated `general-purpose` subagent (no memory of this investigation's own
reasoning), given: the full §1–§3 draft above, explicit instructions to read I2/I4/I5/I6/I7 (and I0/
I1/I3/I8/I9 if needed) directly from the worktree rather than trust this document's summaries, the
repo's governing rules (`autonomous-quality-coordination-evidence.md`, `branching-and-ci.md`,
CLAUDE.md's safety invariants) quoted explicitly, and five specific angles of attack (sample-size/
base-rate objections to I2, hidden maintenance cost of "designed but not activated," an attempt to
break the "zero credential exposure" claim, the direct case for Candidate C, and whether the
"zero issues under any topology" reasoning begs the question). Explicitly told not to propose a
replacement decision, only to attack this one. The dispatch was interrupted once by a session usage
limit mid-task (after it had read I10/I2/I4/I5/I6 and started I7) and resumed via the same agent
identity with its partial progress intact, per this environment's continuation mechanism, rather
than restarted — its final report reflects the full requested scope.

## 5. Adversarial findings (verbatim summary)

Nine objections, each graded by the reviewer itself as **GROUNDED** (supported by something found in
the repo/docs) or **HYPOTHETICAL/PRECAUTIONARY** (a real design concern, not an observed defect):

1. **[GROUNDED]** The "zero durable findings" window is the scanner framework's own construction
   period (`git log -- tools/quality_audit/` shows the scanner set born 2026-08-24, still growing
   2026-08-25), not a demonstrated steady state; I2 §11 flags this but I10's original draft didn't
   carry the caveat forward.
2. **[GROUNDED]** "Zero durable findings" is measured net of a 193-item `accepted_finding_ids`
   baseline — it says nothing about whether that pre-existing backlog contains something that should
   already be escalated.
3. **[GROUNDED]** "Designed but not activated" has real uncosted maintenance debt: I6 §7's protected-
   path-pinning test doesn't exist yet, I8's `Coordinator` is explicitly throwaway code that the
   original draft proposed porting straight to production, and no owner was assigned to re-run I1's
   mutation harness as new scanners appear.
4. **[GROUNDED]** The proposed new `data/*.db` store, as originally drafted, never mentioned this
   repo's own persistence-safety discipline (`/api/health/storage`, `DB_PATH` isolation, the
   CLAUDE.md "first-class asset" rule with its named 2026-08-23 incident) — the exact gate this repo
   built after being burned once already.
5. **[HYPOTHETICAL]** "Zero credential exposure" is true narrowly (no GitHub write credential) but
   doesn't cover the new component's own staleness/integrity failure mode — nothing alerts if the
   observation series silently stops updating.
6. **[HYPOTHETICAL]** Report-only risks becoming a quiet ratchet: the "zero" fact was already being
   reused across I4→I5→I10 without an explicit obligation to re-verify it before it's cited again by
   whoever makes the next decision.
7. **[GROUNDED, factual]** C actually beats D on Observability/explainability (4 vs 3) in I10's own
   §1 table — any framing implying D dominates or ties every row is not supported by the table it
   cites.
8. **[GROUNDED, interpretive]** The 12-criterion matrix is structurally weighted toward inaction (8
   of 12 rows measure safety/operational burden; only 1 measures whether real problems get caught,
   and D scores worst there) — defensible as the evidence rule's own stated priority operationalized
   in tabular form, but not neutral, independent evidence for the conclusion.
9. **[GROUNDED, precision]** The original §1 sentence "a coordinator built today would have written
   zero issues... whether topology A, B, or C backed it" overstates its own implication — the zero
   count is policy-driven and identical for any topology under the same I2 sample, so it argues
   against building a write lane *yet*, not that topology C specifically was tested and found
   wanting.

The reviewer found no objection that broke the credential/veto reasoning itself (A's two open
vetoes, C's clean veto sheet, D's non-existent attack surface) and explicitly declined to pad the
list beyond these nine.

## 6. Reconciliation

**Conceded, and the decision document above already edited to reflect it** (not a to-do list —
these are done in §1–§2 as written): objections 1, 2 (both folded into a new §1 caveat paragraph
narrowing confidence in "zero" without changing its direction), 3 and 4 (§2 item 1 rewritten to
require a fresh, persistence-safety-compliant design at I11 rather than a literal port of I8's
throwaway code, plus an explicit health-surfacing requirement), 6 (§2 gains item 4, a binding
re-verification condition on any future write-stage activation), 7 and 9 (both were wording/framing
overstatements — corrected in §1 directly; the underlying scoring table was already numerically
correct, only the prose around it overclaimed).

**Acknowledged but does not change the decision:** objection 5 — the new observation store's own
staleness-detection need is real and is now folded into §2 item 1's health-surfacing requirement,
but it does not weaken D's "5" credential-exposure score, which was always a narrow claim about
GitHub write credentials specifically, not a claim of zero risk of any kind. Objection 8 — the
matrix's weighting toward narrow-authority criteria is accurate and now stated explicitly rather
than left implicit (this paragraph is that statement): the evidence rule this whole investigation
operates under already instructs optimizing for narrow authority over maximum autonomy, so a matrix
built mostly from narrow-authority axes is applying the governing rule, not smuggling in a hidden
preference. The honest framing is that this matrix cannot itself be cited as independent proof that
inaction was correct — it is a structured application of a decision already made at the charter
level — and the *actual* independent evidence for "nothing to act on yet" is I2's measurement
(itself now caveated per objections 1–2), not the matrix's arithmetic.

**Rejected:** none of the nine objections argued for overturning the D-over-C topology choice
outright, and none were dismissed without the edits above — every GROUNDED objection produced a
concrete document change; every HYPOTHETICAL one produced an explicit acknowledgment rather than
silent omission.

**Remaining uncertainty, carried forward to I11 rather than resolved here:**
- Whether I2's 41-hour, single-developer, scanner-construction-week sample generalizes to this
  repo's steady state is genuinely unknown until a longer, calmer window is measured — I11 should
  not treat "zero durable findings" as re-confirmed without a fresh check, per §2 item 4.
- Whether any of the 193 pre-existing baseline-accepted findings would independently justify
  escalation is unmeasured by this investigation and was never in scope for I0–I9 (the baseline-
  ratchet is a reviewed, dated decision per CLAUDE.md's own baseline semantics — re-litigating it is
  a separate task, not a gap in this one).
- The Woodpecker-webhook-on-`GITHUB_TOKEN`-created-PR question (I4 §8 #1, I6 §7) remains open and
  now provably doesn't need resolving for this decision, since no write lane is being built — it
  becomes load-bearing again only if a future decision selects C.

---

**Commit for this task:** `docs: decide autonomous quality coordination architecture`.
