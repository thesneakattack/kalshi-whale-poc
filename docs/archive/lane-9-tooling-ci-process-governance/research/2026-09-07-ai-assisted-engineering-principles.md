# AI-assisted engineering principles — research (2026-09-07)

Lane 9 (tooling, CI & process governance). Stage 1 of a research → spec →
plan pipeline under CLAUDE.md's "nothing advances on one pass" HARD RULE.
Revision 2: the first version (`c028a05`) went through self-review and
an independent adversarial review (NO-GO, eleven merged fixes in
`…-consolidation.md`); this revision applies them and is rechecked item
by item in `…-recheck.md`.

Direct request (David, 2026-09-07): "read [the two ChatGPT drafts] along
with the original article that these were built from ... the idea is to
use this article to develop and apply a set of principles to the project
workflow that can help us improve and meet the goals outlined in the
article. the .md files are a ChatGPT review and instructions document for
you to use as reference, a kind of rough draft of this task." Mid-task
addition: "keep in mind we recently updated the workflow to this lane
policy that is supposed to help keep bloat in check and keep things
pointed toward the end goal."

**Decision on record (David, 2026-09-07):** the initiative *will* amend
the "nothing advances on one pass" HARD RULE to tier review depth by
consequence, as the article prescribes. David chose "tier by
consequence" over "keep the uniform cycle" and "measure first, decide
later" when asked directly in session `autotrade-d9`; the record is the
2026-09-07 line in `docs/open-decisions.md` (added on this branch) and
this initiative's PR body. The spec stage defines the tier boundary;
this document supplies the evidence and the candidate principle set.

## Inputs and method

| Input | Where | How it was read |
|---|---|---|
| Rodrigo Gardin, "Code Production Is Faster Than Ever. Why Isn't Productivity Booming?", *Built In*, 2026-07-29 | https://builtin.com/articles/ai-coding-productivity-paradox | Fetched live 2026-09-07 (WebFetch extract, quotes preserved verbatim; the adversarial pass re-fetched the page twice and confirmed every quote; the full page is not mirrored in this repo) |
| ChatGPT analysis draft (David, 2026-09-07) | `2026-09-07-ai-assisted-engineering-input-chatgpt-analysis.md` (this directory; copied unchanged from the repo root) | Read in full |
| ChatGPT instruction draft (David, 2026-09-07) | `2026-09-07-ai-assisted-engineering-input-chatgpt-instructions.md` (same) | Read in full |
| Planning-lanes design (round 2, GO) | `../specs/2026-09-06-planning-lanes-design.md` | §1–§9 read; anti-bloat rules in §8 are a constraint on this work |
| 2026-08-27 workflow audit (its budgets withdrawn 2026-08-28) | `../specs/2026-08-27-workflow-audit.md` | Numbers + root causes read as the prior baseline |
| Repo state at `main` = `6a63309` | `gh pr list` (317 merged PRs), `gh pr view --json reviews,comments,files,body,commits`, `git log`/`git ls-tree` since the 2026-08-07 cutover, `.woodpecker/`, `.claude/hooks/guard_workflow.py`, `tools/kanban_sync/labels.py`, `docs/open-decisions.md`, memory files | Measured, commands recorded per finding below |

Every number below was measured this session at `6a63309` (one PR, #663,
merged during the review; counts are stated at the base); none is
recalled. Title prefixes are classified with one scope-tolerant regex
(`^([a-z]+)(\([^)]*\))?!?:`) throughout; titles without a prefix are
"untyped". Where a number is a proxy, the section says what it can and
cannot establish and what would falsify it.

## 1. What the article claims, by proposition type

The ChatGPT analysis's discipline (its §3) is adopted here: a reported
event, an explanation, a proposed rule, a number, and a forecast need
different handling, and a receiving model tends to flatten them into
facts. Quotes are Gardin's own words from the live page.

| Type | Claim | Handling here |
|---|---|---|
| Reported event | "generating more code than ever, but delivery speed had barely moved"; "Pull requests got bigger and more frequent, review queues backed up and the share of changes that needed a second fix crept up." | Attributed to Gardin. Checked against this repo's own numbers in §3.1. |
| Explanation | "Generating code faster didn't remove our bottleneck. Instead, it relocated it." — to review, testing, deployment. | Plausible mechanism; §3 tests whether it operates here. |
| Reported incident | Billing reconciliation code "assumed a currency field was always populated, which held in staging and then quietly didn't for a handful of legacy accounts in production"; it "passed every test we had and read cleanly in review", the reviewer waving it through "because the queue was long that week." | This repo has two shipped incidents of the same shape (§3.6). |
| Proposed rule (from that incident) | "any change to money-handling code gets a second reviewer and a check against real production data, no matter how good the diff looks." | The one rule the article derives from evidence. Candidate P2 in §5. |
| Proposed rule | Guardrails before autonomy: "real test coverage on whatever module it was touching, plus a CI gate that blocked any merge where coverage on the changed lines slipped." | Partially transfers (§3.2, §4). |
| Proposed rule | Autonomy matched to complexity: "add pagination ... goes to an agent with a human skim at the end. Anything that touches a service boundary, the data model or an auth path stays with a senior who ... keeps the final call." | Directly the tiering David decided on. Candidate P1. |
| Proposed rule | Verification as first-class: "Pull requests got capped in size and a reviewer was named before the code was even written, and we pointed our tooling straight at ... the confident-looking edge case that falls apart the moment production data hits it." | Reviewer-named-in-advance is already structurally satisfied here (the adversarial pass is always a fresh Agent). Size cap: see §4. Production-data edge case: P2. |
| Numerical claim | "Teams that stay stuck at 20 to 30 percent gains usually aren't using worse AI; they're applying copilots where the work needs agents." | No denominator, population, or method given. Not used. |
| Staffing claim | One team reached "meaningfully higher throughput with lower headcount." | Single account, no counterfactual. Not used (§4). |
| Outcome measures | "is your change-failure rate holding steady or falling? Is review keeping pace with generation, or has it quietly become the new bottleneck? Are your most experienced people spending their hours on judgment calls rather than on typing?" | These three questions are the article's actual goals. §2 turns them into testable properties. |
| Forecast | "the teams who win ... best verify what it produces and ... put their most experienced people in an orchestration role." | Treated as a forecast, consistent with how this repo already works (David decides, sessions execute). |
| Closing principle | "Faster code was never the goal. Faster, safer, shippable outcomes are." | Adopted as the framing. |

The ChatGPT analysis adds external evidence (Cui et al. field experiments,
METR 2025/2026, DORA 2026) and concludes "conditional usefulness and local
measurement"; its §5.2/§5.3 illustrative arithmetic (a local 3× speedup on
30% of a cycle yields 20% total; a queue grows when validation capacity is
below generation) is labeled illustrative there and is treated as such
here. The most useful contributions of the two drafts for this repo are:
the six autonomy dimensions (goal clarity, context quality, verification
strength, consequences, reversibility, existing authority — the
*instructions* draft's wording; the analysis draft's §7.1 table lists the
same six under slightly different names), the analysis's warning that
"verification can itself become wasteful" (§6.7), and its "charge process
against its benefit" principle (§7). The instruction draft is generic by
design and says so ("apply it within the active instruction hierarchy");
it is not adopted as text, only mined.

## 2. The article's goals as testable properties for this repo

| Article question | Property here | Measured by |
|---|---|---|
| Is change-failure rate holding or falling? | Merged changes that need a corrective change or cause a live incident | Reverts, incident-tagged fixes, behaviour-changing follow-ups (§3.5) |
| Is review keeping pace with generation? | Review depth actually applied per merged PR, and PR timing | `gh pr view --json comments,files,commits` per PR; timestamps (§3.4) |
| Are the experienced people spending hours on judgment, not typing? | Human minutes David spends specifying, correcting, and re-directing, versus deciding | **Not measurable from the repo.** Only David can supply it (§3.7). |
| Guardrails before autonomy | Automated gates that block a merge | `.woodpecker/`, branch protection (§3.2) |
| Autonomy matched to consequence | Review/authority depth varies with blast radius | CLAUDE.md rule text vs. the path lists that already exist (§3.3) |

## 3. Evidence: the repo against each goal

### 3.1 Volume versus delivery (the article's headline finding)

```
gh pr list --state merged --limit 1000 --json number,title,mergedAt,additions,deletions,changedFiles
git rev-list --count 6a63309 ; git log --format=%s 6a63309 | grep -c '^fix[:(!]'
git ls-tree -r 6a63309 --name-only docs | grep '\.md$' | grep -v '^docs/kalshi/'
```

| Measure | Value |
|---|---|
| Merged PRs, all time (first merge 2026-08-25) | 317 |
| By title prefix, all 317 | docs 145 (46%) · fix 94 · untyped 32 · feat 23 · chore 14 · refactor 4 · perf 2 · config 1 · test 1 · ci 1 |
| Last 200 merged PRs, date span | 2026-08-30 → 2026-09-07 (9 calendar days, ~22/day; 54 on 2026-09-03) |
| Last 200 by prefix | docs 104 (52%) · fix 66 · untyped 11 · feat 10 · chore 7 · config 1 · refactor 1 |
| The 104 docs PRs by title subject (first keyword match) | audit/research/census/investigation 26 · benchmarks, root-cause and fix-family write-ups ("other") 23 · lanes migration/classification 20 · next-action/status/decision records 20 · plan/spec/design 13 · **review-cycle artifacts 3** (one `docs+feat` title makes 105 by a looser match) |
| Additions per PR, last 200 | p50 +200 · p90 +1,527 (nearest rank) · max +6,045 |
| Commits on `main` since cutover | 1,925; 270 (14%) titled `fix` |
| Markdown files under `docs/` excluding the `docs/kalshi/` mirror | 321; **122 (38%) are self-review / adversarial-review / consolidation artifacts** |
| Top-level `docs/*.md` | 47 (the lanes design measured 67 → 47 after PR #635) |
| `CLAUDE.md` + `.claude/rules/*.md` | 134 + 147 + 54 = 335 lines (the 2026-08-27 audit measured `CLAUDE.md` alone at 759) |

Reading: throughput is high and more than half of it is documentation.
That documentation is *not* mostly the review process as separate PRs —
only 3 of the 104 docs PRs are titled as review artifacts; the rest are
requested audits, the lanes migration, status records, and plans. The
review process shows up at the *file* level instead: 38% of the markdown
files under `docs/` are review artifacts riding inside the PRs whose
research or plan they review. This is the article's "output rose,
delivery didn't" pattern with a local twist: the bottleneck did not
relocate to a review *queue* (PR latency is minutes, §3.4); it relocated
into artifacts and process documents that consume session time and
David's attention. The 2026-08-27 audit made the same observation ("44%
of commits touch only docs/ or .claude/"); its prose-bloat remedy worked
(`CLAUDE.md` 759 → 134 lines); the artifact-volume side did not shrink
because the 2026-08-31 review rule then added three documents per stage.

What this does *not* establish: that the docs share is waste. The two
largest docs initiatives of the week (the planning-lanes migration, the
DB-foundation audit) were explicitly requested. The measure that matters
is whether shipped code needs correcting less (§3.5), not the docs ratio.

### 3.2 Guardrails before autonomy

- Six Woodpecker contexts are required by convention (`tests-pytest-app`,
  `tests-pytest-tooling`, `tests-dependency-audit`,
  `quality-architecture-audit`, `quality-browser-e2e`,
  `kalshi-contract-fixtures`), ~3,070 tests, the architecture audit's
  `kalshi_boundary` scanner, and `tools/quality_audit/price_fabrication.py`
  (a CI guard for the fabricated-price class, `34badf1`, 2026-09-05,
  issue #577).
- **No changed-lines coverage gate exists** (`grep -rn -i 'coverage\|--cov'
  .woodpecker/ scripts/ pyproject.toml` → two comment hits). The article's
  "coverage on the changed lines slipped" gate is absent.
- **`main` branch protection is off** (`gh api …/branches/main` →
  `protected: false`, `enforcement_level: off`; #615, live-verified
  2026-09-05 and again by the adversarial pass); the six contexts are
  enforced by each merging session reading `commits/<sha>/status`, i.e.
  in prose. That is David's open call in `docs/open-decisions.md` and this
  initiative does not re-open it. **Two rule files still assert the old
  state**: `CLAUDE.md:117` ("`main` is protected") and
  `branching-and-ci.md:96-110` (the `enforce_admins: true` /
  `required_status_checks.contexts` paragraph). The spec that edits those
  files for P1 corrects them in the same PR, or it ships rule text that
  contradicts `open-decisions.md`.
- Safety gates around real money are code, not prose (`trading_enabled`
  default false + typed confirmation, kill switch persisted). Those are
  the guardrails the article would call prerequisites, and they are in
  place.

### 3.3 Autonomy matched to consequence

The article's tier boundary ("service boundary, the data model or an
auth path") already exists here as *path lists*, in four places built
for other purposes:

- `.claude/hooks/guard_workflow.py:76-80` — `KALSHI_PATHS` (`services/
  kalshi/`, `kalshi_client.py`, `kalshi_account_client.py`,
  `kalshi_trade_ws.py`, `kalshi_fees.py`, `market_catalog/`,
  `market_watch/`, `market_events/`, `whale_stream/`), used at `:342` to
  **deny** an edit until a `docs/kalshi/` page has been read — the only
  list that already varies enforcement rather than a nudge.
- `guard_workflow.py:81-87` — `HOT_PATHS` (strategy_engine, risk_manager,
  paper_broker, confidence_scoring, shadow_mode, advisory/,
  whale_calibration/, exits/, position/, kalshi_client,
  kalshi_account_client) and `:89` `MONEY_UI_PATHS` (`frontend/src/js/`),
  used at `:397` and `:334` only to fire the dimensional-analysis nudge
  (`:351`, the R4 GitNexus prompt, is commented out).
- `tools/kanban_sync/labels.py:85-` — `LANES` (lane → package list) and
  `:79-83` `CONCERNS` (`concern:hotpath`), the lanes design's single
  source of truth for "closest primary fit".
- CLAUDE.md "Safety invariants" — the prose list "trading, risk, sizing,
  calibration, strategy, settlement, auth, or CI-credential code" that
  automation never edits; no path list backs it (`grep -rn -i
  'protected\|PROTECTED_DOMAIN' .claude/hooks/*.py
  tools/quality_coordination.py` → only the `main` branch-name set).

What varies with these lists today: one deny, one nudge, and a label.
What does *not* vary: review depth. CLAUDE.md's Scope bullet (line 40,
since `41f5b6d` 2026-08-31, reaffirmed `b994a5a` 2026-09-07) puts
"anything asserting a claim, a design decision, new logic, or a
process/rule change" in scope "regardless of diff size", and defines the
obligation for a code PR as **one** cycle — self-review, adversarial
review, consolidation — at the PR, "never an individual commit or push".
The memory `scale-review-effort-to-blast-radius` records David calling
the resulting cost "extreme overkill" for "a single line change...
anywhere" on 2026-09-03 and asking for discernment, records that in
*practice* sessions were running the cycle twice (pre-push and pre-merge,
which the rule text does not ask for), and concludes that the rule had to
be formally amended rather than quietly ignored. It also records that the
hour-plus cost of a simple change is multi-causal: the review cycle, the
"no flake classification" mandate (any unrelated CI red blocks a merge
until root-caused), and the skill-chain ceremony around bug-shaped tasks.
Tiering addresses the first; the spec should say explicitly that it does
not address the other two.

### 3.4 Verification as first-class: the rule versus its compliance

The written rule exceeds the article: every in-scope PR gets a
self-review, an independent adversarial review from a memory-less Agent,
and a consolidation, each persisted as a PR comment or document. The
article asks for "a reviewer named before the code was even written";
here the reviewer is always a fresh Agent, so that is structurally met.

Compliance, measured over the last 200 merged PRs
(`gh pr view <n> --json comments,files` for every PR whose title prefix
is `fix|feat|refactor|chore`; formal GitHub reviews are **0 of 200** on
every PR in the window — all 265 comments are by `thesneakattack` — so
the `reviews` field carries no signal and is not used):

| | Count |
|---|---|
| Code-typed PRs (prefix definition) | 84 |
| With at least one PR comment or a review/consolidation-named file in the diff | 60 |
| — of which with **three or more** such artifacts | 31 (25 counting comments alone); 12 have exactly one, 17 exactly two |
| **With no artifact at all** | **24 (29%)**: #255 #269 #270 #271 #274 #275 #296 #298 #301 #308 #389 #396 #415 #445 #446 #460 #498 #499 #500 #502 #531 #558 #573 #623 |
| Of those 24, whose *body* narrates a self/adversarial/consolidation review | 12 (#301 #308 #445 #446 #498 #499 #500 #502 #531 #558 #573 #623 — the "narrative-in-body" shape memory `persist-code-pr-reviews-as-comments` already flagged four times) |
| **Untyped-title code PRs with no artifact, outside the 84** | 6: #272 (account diagnostics, 6 code files), #273 (trades backfill tool), #297 (advisory/calibration provenance, 14 code files — a Tier A path), #302 (backup retention), #421, #422 (trade-log P&L UI) |
| Zero-comment PRs, all types | 98 of 200 (docs PRs: 60 of 105) |

So the prefix-based 29% is a floor. Counting every PR that touches a
non-`docs/` code or config file, the adversarial pass measured 34 of 110
(31%) with no artifact; that figure is its, not re-derived here. Full
compliance (three artifacts) and zero compliance are similar-sized groups.

Falsification attempted: for all 24, every issue number cited in the PR
body and every GitHub closing reference was opened and its comments
searched for `adversarial review|self-review|consolidation` — 0 hits
(the authoring session swept 21; the adversarial pass swept all 24 plus
a `git grep` of `docs/` and `.claude/` on `origin/main`, finding only
incidental mentions of these PR numbers inside *other* initiatives'
review documents, never a review of the PR). Some of the 24 may be
legitimately exempt as mechanical (#623 is a 4-line comment fix, #415
adds git to a container image). The consequential ones are not exempt
on any reading: #502 (strategy edge/EV gate, all 10 tasks, +992/19
files), #298 (entry-gate ME-pairing, +2,821/28 files), #499/#500 (Tier
0/1 live-incident remediation), #558 (trade-path throttle, live
incident), #460 (CI split), and untyped #297.

Timing (n=84 code PRs; nearest-rank percentiles):

| Interval | p50 | p90 |
|---|---|---|
| PR opened → merged, all 200 | 18 min | 154 min (max 813) |
| PR opened → merged, docs / code | 12 / 23 min | 153 / 190 min |
| PR opened → merged, code with artifact / without | 27 / 10 min | — |
| **First commit on the branch → PR opened** | **1 min** | 88 min |

The last row is the measurement that matters and it says the timestamps
cannot see the cost: sessions commit at the end and open the PR in the
same minute (commits per PR p50 = 2), so the working time before the
first commit — where memory places PR #482's ~2 h — is invisible to git
and GitHub alike. Review is not a queue problem here: it is fast when it
runs, it silently does not run on roughly three code PRs in ten
(including strategy code), and the time cost David feels is upstream of
any timestamp.

The rule's own supporting evidence (memory `nothing-advances-on-one-pass`):
11 of 12 review checkpoints on two docs-only, data-shape audit pipelines
found a real defect. That evidence is genuine and narrow; it says nothing
about the catch rate on a 40-line code fix, which is exactly what the
uniform rule assumes.

### 3.5 Outcome measurement — nothing tracks it, and the obvious proxy misleads

No script, endpoint, or document measures change-failure or rework
(`grep -rln -i 'change.fail\|rework\|revert\|lead.time\|mergedAt' tools/
scripts/` → only `watchlist_scale_stress_test.py` and `kanban_sync/sync.py`,
neither of which measures it). The outcome measures the repo has are for
the *trading system* (`/api/quality/summary`, soak analyzer), not for the
*engineering process*. Proxies computed this session:

| Proxy | Value | What it can and cannot say |
|---|---|---|
| Later `fix:` PR citing "PR #N" in title/body, all 135 code PRs | 24–25 (18%) | Counts named follow-ups only; misses fixes that cite an issue instead. |
| Same proxy, last 200, split by review artifact | with artifact 15/60 (25%); without 2/24 (8%) | Inverted from the naive expectation. |
| Same, banded by additions (with / without) | 0–100: 2/12 vs 0/8 · 100–500: 5/30 vs 0/7 · 500+: 8/18 vs 2/9 (authoring session); 0–100: 2/12 vs 0/8 · 100–400: 5/25 vs 0/5 · 400–1000: 4/14 vs 1/7 · 1000+: 5/9 vs 1/4 and by files 0–3: 4/17 vs 0/7 · 3–8: 6/26 vs 0/6 · 8–20: 2/12 vs 1/9 · 20+: 4/5 vs 1/2 (adversarial pass) | **The inversion persists in every band**, and the unreviewed PRs are neither smaller (additions p50 +286 vs +266) nor younger (median 6 vs 3 days since merge). Size is not the explanation. |
| Commits whose subject *is* a revert (`^revert|^fix: revert|^chore: revert`) since cutover | 7 of 1,925 (9 mention the word) | Two were live-incident reverts (#424's pool, min_contracts overrides). |
| `incident|outage|regression` in commit message | 34 | Mentions, not incidents; several commits describe one incident. |
| Rework-shaped PR titles, last 200 (`revert|regress|incident|missed|follow-up|lost by|dropped from`) | 15 (7.5%) | Title-only; undercounts. |

The mechanism the data supports is the one both reviews converged on: a
review artifact *generates* cited follow-ups — a gap the adversarial pass
finds becomes a later PR that says "PR #N follow-up" — and reviewed PRs
carry more written context and are cited more. The "cites PR #N" proxy
therefore measures citation behaviour, not defects, and cannot be used
for the article's first question in either direction. n is small (24
unreviewed PRs) and no band is significant on its own; the *direction*
in every band is the finding.

Conclusion: the article's first question ("is change-failure holding?")
cannot be answered here today. A usable measure must count **defects**
— a revert, an incident-tagged fix, or a later fix that changes behaviour
the original PR claimed — and must **exclude follow-ups that cite a
review finding**, or it will reward skipping review. It must be computed
the same way each time and split by tier and size band, which is a
precondition for judging whether tiering (P1) helped or hurt. This is
the ChatGPT analysis's §11 point, and the lanes design's "pre-built over
hand-rolled" constraint applies: extend what runs at `/checkpoint`
(`tools/quality_coordination.py`, `kanban_sync`) rather than a parallel
tool, and keep it read-only.

### 3.6 The billing-anecdote class already shipped here, twice

Gardin's incident: tests encoded an assumption (currency always
populated) that held in staging and failed on legacy production data.
This repo's equivalents:

- The no-side `1 − price` cost inversion: the "70% era" win rate was
  real and the gains were a 49× accounting artifact (memory
  `the-70-percent-era-was-the-no-side-cost-bug`; CLAUDE.md "A displayed
  value must match its label"; CLAUDE.md at `5745d9a`, 2026-08-09,
  already describes the display-side reimplementation of the same class).
  How it was originally found predates the 2026-08-07 cutover's
  maintained history; the 2026-08-17 593-signal measurement quantified it
  after the fact.
- The NO-side exit valuation artifact (2026-09-04): exits and marks
  valued at 1 − yes_bid, $1.00 on an empty book, producing a 10× paper
  run that was never edge (memory
  `paper-10x-run-is-no-side-exit-valuation-artifact`). Found from the
  equity curve and confirmed against raw ticker rows in
  `series_watcher.db`; the bookkeeping "always reconciled to the cent";
  the fix (#574) took six rounds and one defect in the fix was caught by
  a test, not by review.

Neither was caught by the suite; the second was found from recorded real
data. The standing responses so far are the dimensional-analysis HARD
RULE (units and scale), the "displayed value must match its label" rule,
and, for the fabricated-price sub-class of the second incident, the
`price_fabrication.py` CI guard. None says "check the change against
recorded real data". The repo has the tooling for that check
(`tools/realtime_pipeline_replay.py`, `tools/rest_scheduler_replay.py`,
`services/backtest/` routes, and the live `data/*.db` history that
CLAUDE.md calls a first-class asset), so the article's one
evidence-derived rule is cheap to adopt here. It is also the rule the
tiering must not weaken: money-path code is Tier A.

### 3.7 Human burden — the thing that prompted this

Not instrumented, and **not measurable from the repository**: git and
GitHub timestamps see only committed work (§3.4), and no artifact records
minutes spent directing, correcting, or waiting. The article's third
question can only be answered by David noting time per task class; P4
does not and cannot substitute for that. The memory directory is the
only record, and it is one-sided (corrections, not successes). Since
2026-08-26 there are direct corrections for: over-investigation (08-26),
generating adjacent work (08-29), effort caps as kneecapping (08-28),
planning running a full session without shipping (09-03), uniform review
as overkill (09-03), review artifacts missing (09-03, 09-06 ×3). The last
two point in opposite directions — less ceremony on small changes, and
no missing ceremony on consequential ones — and are exactly what a
consequence tier reconciles. The 2026-08-27 audit's ranked root causes
(enforcement in prose; no budget — withdrawn; redundant ownership; five
knowledge layers with no promotion or expiry) still describe the process;
the lanes migration addressed the last one for planning docs.

## 4. Where the article's prescriptions do not transfer

- **Staffing conclusions** ("higher throughput with lower headcount"):
  single-developer repo; nothing to apply, and the ChatGPT analysis's §6.8
  caution stands.
- **Copilot-versus-agent hierarchy**: the toolchain decision here was made
  (Claude Code + superpowers + pinned MCP servers) and is not a maturity
  ladder question.
- **Changed-lines coverage gate as a universal merge prerequisite**: the
  suite is large (~3,070 tests, two required pytest contexts), no coverage
  tooling is wired, and the ChatGPT analysis's §6.4 point holds — coverage
  shows execution, not correctness. A coverage gate scoped to Tier A paths
  is a candidate for the spec, not a research conclusion; adding one
  repo-wide contradicts "charge process against its benefit".
- **PR size cap**: David withdrew every effort budget on 2026-08-28
  (memory `effort-caps-are-kneecapping`). A numeric PR size cap is the
  same shape. The article's underlying point — a PR must be reviewable as
  one coherent change — is already in memory (`deliver-dont-generate-
  adjacent-work`: one initiative, one branch, no adjacent work) and in the
  lanes design's straddler rule; the spec can restate coherence without a
  number.
- **"Second reviewer" for money code**: there is one human. The
  functional equivalent is already the fresh-Agent adversarial pass; what
  is missing is the *real data* half of Gardin's rule, not the reviewer
  half.

## 5. Candidate principles for the spec stage

Each names the failure it prevents, its cost, its scope, its home in the
existing rule set, and what would show it is not earning its keep. The
spec decides final wording and may merge or drop items; nothing here is a
new document hierarchy, tracker, or orchestrator (lanes design §8, ChatGPT
analysis §10).

**Today's baseline, stated precisely** (CLAUDE.md Scope bullet): a code
PR in scope owes one cycle at the PR — self-review, adversarial review,
consolidation, three persisted artifacts; a planning pipeline owes that
cycle at every stage boundary and once more at the PR; trivial/mechanical
changes are exempt. Tier A below keeps exactly that. Tier B is defined so
it is genuinely lighter than that, which the first draft of this document
failed to do.

| # | Principle (draft) | Failure prevented | Cost | Home | Falsifier / retire when |
|---|---|---|---|---|---|
| P1 | **Review depth follows consequence, decided by path, not by feel.** Tier A (today's cycle, unchanged): Lane 3 packages, `concern:hotpath`, `KALSHI_PATHS`, `HOT_PATHS` + `MONEY_UI_PATHS`, `auth.py`/`accounts_store.py`, `services/db.py` and schema/migration code, safety-gate code, `CLAUDE.md`/`.claude/rules/`/hooks/CI config, and every planning-pipeline stage. Tier B (everything else, code or docs): **one persisted artifact** — the author's self-review as a PR comment stating what changed, which check was run and what it showed, the falsifier, and why the PR is Tier B — plus green CI; no adversarial Agent, no consolidation. Trivial exemption unchanged. A PR touching any Tier A path is Tier A. | Hour-plus cycles on low-blast-radius changes (09-03 complaint); silent skipping born of that cost (§3.4) | One-time rule edit; a path list that already exists in four places must become one | CLAUDE.md HARD RULE scope bullet; `branching-and-ci.md`; the path list lives in `labels.py` next to `LANES`, with `guard_workflow.py` importing it | On the defect measure P4 defines (reverts, incident fixes, behaviour-changing follow-ups; review-cited follow-ups excluded): if Tier B's defect rate exceeds Tier A's over two consecutive 14-day windows, the boundary is wrong, not the idea |
| P2 | **Money-path changes are checked against recorded real data, not only fixtures.** A Tier A PR touching pricing, P&L, fees, sizing, or settlement cites in its body one read-only query or replay over `data/*.db` history that exercises the changed path, and what it showed. | The billing-anecdote class, shipped here twice (§3.6) | Minutes per money-path PR; tooling exists | "A displayed value must match its label" section of CLAUDE.md, one added line; `kalshi-contract-review` skill already covers the exchange side | If a year passes with no such check ever catching anything, drop it — but the two precedents say otherwise |
| P3 | **A review that is not a persisted artifact did not happen; the merging session counts artifacts, never reads the body's narrative.** Tier A: three distinct PR comments or committed documents. Tier B: one. Count `comments` and review-named files in the diff; GitHub formal `reviews` are always zero here and are not a signal. | 24 typed + 6 untyped code PRs with no artifact (§3.4); four dated recurrences; 12 bodies narrating a review that does not exist | One `gh pr view --json comments,files` per merge, already asked for in memory `persist-code-pr-reviews-as-comments` | `branching-and-ci.md` merge steps (already lists "read the PR body"); optionally a `/checkpoint` line | If the count stays at zero misses for a month, keep it anyway — it is a one-command check |
| P4 | **Measure outcomes, not volume, the same way every time.** One read-only report: merged PRs by type and tier; PR timing by tier; **defects** per tier and size band, defined as reverts, incident-tagged fixes, and behaviour-changing follow-ups, with review-cited follow-ups excluded; review-artifact compliance by tier (P3's count). It does **not** measure human hours; only David can (§3.7). | The article's first question is unanswerable today and the naive proxy inverts (§3.5) | A small script run at `/checkpoint` or weekly; extends `quality_coordination` or `kanban_sync` per lanes §8 | `tools/` (handspun tools default disabled until run history proves value — this one is read-only reporting, so "disabled" means "not gating") | If no decision changes because of the report across two consecutive 14-day windows, retire it |
| P5 | **Expected values come from outside the implementation.** For Tier A tests and PR evidence, name the source of the expected result (a Kalshi doc page, a recorded row, an invariant, an independent calculation), never the code's own output. | Tests that encode the implementation's assumption (Gardin's incident; ChatGPT analysis §8.3) | A sentence per Tier A test file or PR body | TDD skill already covers red-first; this is one line in CLAUDE.md's dimensional-analysis or label-match section | If Tier A PRs routinely cite sources already and the line adds nothing, fold it into P2 |
| P6 | **Charge every rule, hook, and tool against a named failure and a falsifier.** New process text states what it prevents and when to remove it; the 2026-08-30 "disabled until proven" stance for handspun tools is restated, not duplicated. | Process accretion that the 08-27 audit measured and the lanes design was built to stop | Zero runtime; a discipline on rule edits | `CLAUDE.md` header sentence ("This file is a rulebook") already implies it; make it explicit in one clause | If it produces rule-edit ceremony rather than fewer rules, drop it |
| P7 | **Tiering never lowers a safety gate.** Nothing in P1–P6 touches `trading_enabled`, the kill switch, `data/*.db` handling, or Kalshi docs-first (`KALSHI_PATHS` stays a deny regardless of tier). | Reading "lighter review" as "lighter safety" | None | Already CLAUDE.md "Safety invariants"; the spec cross-references rather than restates | n/a — permanent |

Not proposed, deliberately: a new principles document to be loaded every
session (the ChatGPT drafts stay as research inputs), a changed-lines
coverage gate outside Tier A, a PR size number, any effort budget, any new
hook that fires on every edit (#613's reasoning on nudge fatigue applies),
and any retroactive backfill of the 30 unreviewed PRs (record the list,
apply P3 going forward; a reconstructed review "that looks original" is
the failure mode memory `persist-code-pr-reviews-as-comments` names).
Five of the ChatGPT drafts' twelve principles are also not carried —
start from the intended effect, find the limiting activity, make
uncertainty actionable, preserve authority boundaries, the five-question
handoff — because they describe per-task assistant behaviour that the
harness's own instructions and CLAUDE.md's never-guess rule already
cover; writing them into the repo would duplicate, not add. The spec may
contest that.

## 6. Decisions already on record that this research inherits

- Tier by consequence: David, 2026-09-07 — recorded in
  `docs/open-decisions.md` on this branch and in this PR's body.
- Effort caps withdrawn: David, 2026-08-28 — P1 is a *depth* tier, not a
  budget; the Tier A cycle is unchanged and uncapped.
- Handspun tooling defaults to disabled until its run history proves
  value: David, 2026-08-30 — P4 is read-only reporting, never a gate.
- #613 (adversarial finding is a claim, not a verdict; dimensional hook
  stays session-enforced): decided 2026-09-05, docs PR not yet opened.
  P1's rule edit must land after or together with it, on the same rule
  text, not as a competing edit.
- #615 (`main` protection off): David's open call; untouched here, but
  the stale text in `CLAUDE.md:117` and `branching-and-ci.md:96-110` is
  corrected by the spec's PR (§3.2).
- Lanes design §8: written rules over tools, pre-built over hand-rolled,
  extend `kanban_sync`/`quality_coordination` rather than parallel tools.

## 7. Left for the spec stage

1. The exact Tier A path list, as one machine-readable constant next to
   `LANES`, with `guard_workflow.py`'s `KALSHI_PATHS`, `HOT_PATHS`, and
   `MONEY_UI_PATHS` importing from it (one list, not four).
2. A multi-lane PR's tier is decided by "any Tier A file touched" (the
   safe reading); the research recommends yes. This is a different
   question from the lanes design §5's "label a PR by its own primary
   purpose, not file count" — that rule picks the *lane*, this one picks
   the *review depth* — and the spec should say so to prevent conflation.
3. Exactly what Tier B's one artifact must contain, and whether a PR
   body section can satisfy it or only a comment can (P3 says comment or
   committed document; the body's narrative is what failed twelve times).
4. P4's exact definitions (window, size bands, the defect predicate, the
   review-cited exclusion) and where the report is written (PR comment at
   `/checkpoint`, `docs/next-action.md`, or stdout only).
5. Which CLAUDE.md and `branching-and-ci.md` lines change, verbatim,
   including the stale branch-protection text, and how the change
   coordinates with #613's pending edit to the same rule.
6. Whether P5 stands alone or folds into P2.
7. The two other hour-plus contributors (no-flake mandate scope; skill
   ceremony on bug-shaped tasks) are out of scope here and should be
   named as such in the spec so the tiering is not blamed for them.

## 8. Falsifiers for this document's load-bearing claims

| Claim | Would be falsified by |
|---|---|
| 24 typed + 6 untyped code PRs merged with no review artifact | A review found in a place neither pass checked: a comment on an issue the PR body does not cite and that GitHub does not link, or a chat relay David accepted as the record for a named PR. The lists in §3.4 are the input for that check. |
| Review artifacts are 38% of `docs/` markdown files, and docs PRs are mostly requested work, not review PRs | Ran: the docs-PR reclassification (§3.1) confirmed the second half and corrected the first draft's PR-level wording. A different keyword scheme could move a few titles between "audit" and "other"; it cannot move the 3 review-artifact PRs into a majority. |
| Review is not a queue problem; the cost is upstream of any timestamp | Ran: first-commit→PR-open p50 1 min. Falsified only by a record of working time per PR that git does not hold — David's own minutes. |
| The follow-up-fix proxy is unusable as a defect rate because review generates citations | A recomputation that excludes follow-ups whose body cites a review finding and still shows reviewed PRs with more follow-ups per band. Not run — it requires classifying each follow-up's cause by hand, which is P4's job. |
| The two shipped incidents are the billing-anecdote class | Both are in memory and git history; the second's discovery from real data is documented. The first's discovery predates maintained history — a pre-cutover record (`docs/status-archive-2026-08-26.html`) showing a failing test found it would weaken §3.6 by half. |
| Tiering addresses the 09-03 complaint | The memory itself says the cost is multi-causal; if the no-flake mandate dominates, P1 alone will not move the hour-plus number. Only David's time record would show it; P4 cannot. |
