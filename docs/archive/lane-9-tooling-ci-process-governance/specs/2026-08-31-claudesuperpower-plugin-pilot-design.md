# Design: claudesuperpower.com plugin pilot rollout — 2026-08-31

Status: REVIEWED, GO (self-review + independent adversarial review +
consolidation complete, 4 fixes applied in place — see
`2026-08-31-claudesuperpower-plugin-pilot-design-consolidation.md`).

## Input

Research stage, reviewed and consolidated GO:
`docs/archive/lane-9-tooling-ci-process-governance/research/2026-08-31-claudesuperpower-toolkit-assessment.md`
+ `...-review.md` + `...-consolidation.md`. That cycle's authoritative
recommendation: pilot 4 `claude-plugins-official` plugins, in priority
order — `pr-review-toolkit`, `claude-security`, `claude-md-management`,
`codspeed` — with a 3-item fix list this design folds in as inputs (item 3
of that list, the two open questions, are answered below).

## Goal

Turn "pilot these 4 plugins" into a concrete, safe, sequenced design: what
"pilot" means operationally per plugin, acceptance criteria, cost/safety
constraints, and a rollback path — so an implementation plan can be written
directly from this document without re-deriving any of it.

## Non-goals

- **Not deciding whether to actually install anything.** Installing changes
  `.claude/settings.json` (shared, version-controlled) and, for
  `claude-md-management`, grants direct CLAUDE.md write access — both are
  toolchain decisions this repo's CLAUDE.md Toolchain section says need an
  explicit go-ahead, not a unilateral change. This document — and the
  implementation plan built from it — are prepared so that go-ahead is a
  single decision away, not something this pipeline decides on its own.
- **Not writing the implementation plan itself** — that is the next,
  separate stage.

## Constraints carried in from repo rules

- Safety invariants: none of these plugins' output may touch
  trading/risk/sizing/calibration/strategy/settlement/auth/CI-credential
  code automatically. Every plugin here produces *proposals* (a report, a
  patch file, a suggested edit) that a human (or a Claude session acting on
  the human's behalf, same as any other PR) reviews before it lands —
  never an auto-apply path.
- "Nothing advances on one pass": this design doc, and the implementation
  plan after it, each get their own self-review + adversarial-review +
  consolidation before the next stage starts, same as the research stage
  already did.
- CLAUDE.md Toolchain section: plugin enable/disable lives in
  `.claude/settings.json`'s `enabledPlugins` — the implementation plan's
  actual edit target.

## New findings from this stage's own primary-source checks

(Design-stage investigation surfaced these; they were not visible from the
research/review docs alone — noted here rather than silently assumed.)

- **`claude-security` supports choosing scope and effort explicitly**
  (confirmed: plugin `README.md`, "Choosing scope and effort" — "say what
  you want if you know; if you don't, the plugin works it out with you
  rather than making you guess"). This resolves the research
  consolidation's open question about `effort: xhigh` cost: the pilot run
  does not have to default to a full-repo `xhigh` scan — scope and effort
  are pilot-run parameters, addressed in acceptance criteria below.
- **`codspeed` requires an external CodSpeed account — resolved, not just
  flagged** (per the design-stage adversarial review, which checked
  further than this doc's own first pass: `CodSpeedHQ/codspeed`'s packaged
  Claude Code plugin registers a remote MCP server
  (`https://mcp.codspeed.io/mcp` in `.mcp.json`), and its bundled
  `skills/codspeed-optimize/SKILL.md` explicitly instructs against falling
  back to raw local benchmarking without CodSpeed auth — "the CodSpeed CLI
  must be authenticated to upload results and use MCP tools"). This is a
  materially different adoption shape than the other 3 plugins (pure local
  Claude Code plugins/skills, no external service): piloting `codspeed` as
  designed requires creating/using an external CodSpeed account, which is
  its own go-ahead decision, parallel to how `sentry`/`grafana` are parked
  pending the "no deployment target" open decision — not a verification
  step to run at implementation time, but a decision to make before
  plugin 4's install step at all.

## Per-plugin pilot design (priority order preserved from research stage)

### 1. `pr-review-toolkit@claude-plugins-official`

- **Resolves research consolidation's open question** (does its bundled
  `code-reviewer`/`code-simplifier` duplicate the existing `/code-review`/
  `/simplify` skills?): don't decide up front by inspection — the plugin
  bundles 6 subagents as one install, they can't be selectively
  uninstalled, so the pilot installs all 6 but *evaluates* them in two
  tiers. Tier A (primary pilot target, novel per the research doc's own
  reasoning): `silent-failure-hunter`, `pr-test-analyzer`. Tier B
  (secondary, likely overlapping): `code-reviewer`, `code-simplifier`,
  `comment-analyzer`, `type-design-analyzer`.
- **Acceptance criteria:** run Tier A's two subagents against one real,
  already-in-flight PR in this repo (not a synthetic test) as part of that
  PR's normal review cycle, alongside — not instead of — the existing
  self-review/adversarial-review/consolidation cycle. Pass: Tier A finds at
  least one thing the existing cycle's own passes did not (a genuine
  silent-failure or test-coverage gap), OR clearly confirms the existing
  cycle already has full coverage (a negative result is still useful
  information, not a failed pilot). For Tier B, one comparison run against
  a diff already reviewed by `/code-review`/`/simplify`: if its findings are
  a strict subset of what those skills already produced, mark Tier B
  redundant and stop invoking it going forward (plugin stays installed for
  Tier A; Tier B subagents simply go unused, same as any installed-but-
  unused tool). **These criteria measure whether the pilot produced
  evaluable signal, not a mechanical pass/fail on the plugin's quality** —
  almost any concrete outcome (found something / confirmed nothing missed
  / confirmed Tier B redundant) satisfies them by design; the actual
  keep-or-drop decision after the pilot is a separate human judgment call
  the criteria don't automatically resolve.
- **Rollback:** `claude plugin disable pr-review-toolkit` (or remove its
  `enabledPlugins` entry), revert the settings.json commit.

### 2. `claude-security@claude-plugins-official`

- **Resolves research consolidation's open question** (xhigh cost): first
  pilot run specifies an explicit, narrow scope (one or two of this repo's
  own security-sensitive modules — `services/kalshi_account_client.py`,
  `services/kalshi/signing.py` — chosen because they're exactly the kind of
  auth/credential-handling code this rule cares most about) and a
  lower-than-default effort tier if the plugin's own scope/effort prompt
  offers one, rather than a full-repo `xhigh` sweep. Cost is measured
  (tokens/duration reported by the run) on this first narrow pilot before
  ever considering a full-repo scan.
- **Safety, reaffirmed:** every output is a report + optional patch file a
  human applies deliberately — this repo's automation-restriction
  invariant (never auto-touch trading/risk/sizing/calibration/strategy/
  settlement/auth/CI-credential code) already matches how the plugin is
  designed to work (scratch-workspace-scoped patch generation, confirmed in
  the adversarial review of the research stage), so no additional gate is
  needed beyond "a human reviews and applies, same as any other PR."
- **Acceptance criteria:** one narrow-scope pilot scan completes, produces
  a report (findings may be zero — a clean report on real
  credential-handling code is itself useful signal, not a failed pilot),
  and the measured cost (tokens, wall-clock time) is recorded so a
  decision about periodic/full-repo use is made on real numbers, not a
  guess. **As with `pr-review-toolkit` above, this criterion measures
  whether the pilot produced usable signal (a report + a real cost number),
  not a pass/fail verdict on the plugin** — whether to use it periodically
  is a separate human decision made from that signal, not implied by
  "the pilot completed."
- **Rollback:** same mechanism as above.

### 3. `claude-md-management@claude-plugins-official`

- **Confirmed direct CLAUDE.md write access** (per the research
  consolidation's fix #1 — this is the one of the two CLAUDE.md-adjacent
  plugins that actually has it, unlike `claude-security`). **Correction
  from this design stage's own adversarial review:** there is no
  selectable "audit-only mode" versus "apply-fixes mode" — `claude-md-
  improver` is one workflow that always produces the quality report first
  (its Phase 3) and then explicitly asks for human confirmation before
  making any Edit (Phase 4/5). The pilot achieves "no CLAUDE.md write
  during the pilot" simply by not giving that confirmation, not by
  selecting a different mode — the report is produced either way; the
  write only happens if and when a human approves it.
- **Acceptance criteria:** the audit report's suggestions are read against
  this repo's explicit, self-described terseness convention (one line per
  rule, no incident narrative, provenance via `git log -S` instead of
  inline story — CLAUDE.md's own opening paragraph). Pass: at least one
  suggestion is either (a) accepted as a genuine improvement consistent
  with that convention, or (b) clearly and specifically rejected as
  generic-template bloat that would violate it — either outcome is a
  successful pilot; the failure mode being tested for is a tool whose
  suggestions can't be evaluated against this repo's deliberate style at
  all. **As with the other two plugins above, this measures evaluable
  signal, not a plugin-quality verdict** — whether to actually approve any
  suggested edit stays a separate, explicit human confirmation regardless
  of how the pilot evaluation itself comes out.
- **Rollback:** disable/remove entry; since the pilot never writes
  CLAUDE.md, no content rollback is needed regardless.

### 4. `codspeed@claude-plugins-official`

- **Resolved, not open (updated per this design stage's adversarial
  review):** the plugin's designed workflow requires an external CodSpeed
  account — its packaged `.mcp.json` registers a remote MCP server
  (`https://mcp.codspeed.io/mcp`), and its own `codspeed-optimize` skill
  explicitly instructs against falling back to raw local benchmarking
  without CodSpeed auth. This is not a verification step to run at
  implementation time; it is a go-ahead decision to make before plugin 4's
  install step at all — the same shape of blocker as `sentry`/`grafana`
  being parked on the deployment-target decision, made explicit here
  instead of discovered later. Install-order already places this pilot
  last so it doesn't block the 3 plugins with no external-account
  question.
- **Acceptance criteria (once/if the external-account go-ahead is given):**
  one benchmark
  run against a real hot-path module already named by the data-plane HARD
  RULE (`services/kalshi/` or `whale_stream`), producing a flamegraph and a
  measured baseline number — used exactly as the HARD RULE already
  requires ("identify the measured bottleneck and its mechanism first"),
  not as a replacement for that judgment step.
- **Rollback:** disable/remove entry; if an account was created, that is a
  separate, explicit un-decision the human makes (deleting a CodSpeed
  account is outside this repo's scope to automate).

## Install order

1. `pr-review-toolkit` — no external dependencies, cheapest pilot, highest
   confidence per the research stage.
2. `claude-security` — no external dependencies; narrow-scope first run
   bounds cost.
3. `claude-md-management` — no external dependencies; audit-only first run,
   zero write risk during the pilot itself.
4. `codspeed` — gated on a separate, explicit go-ahead for creating/using
   an external CodSpeed account, beyond the plugin-install go-ahead the
   other three need (see above — this is confirmed required, not
   contingent).

## Rollback (applies to all four)

`.claude/settings.json`'s `enabledPlugins` is the single source of truth;
removing/falsifying an entry and reverting that commit fully undoes a pilot
with no other repo state affected (none of the four plugins touch
`data/*.db`, trading state, or CI config).

## What the implementation plan (next stage) must contain

- The exact `enabledPlugins` diff, staged per the install order above (one
  commit per plugin, matching this repo's "meaningful checkpoint commits"
  convention — not one giant commit).
- For plugin 4, an explicit precondition step: obtain the external
  CodSpeed-account go-ahead *before* attempting its install — this is a
  required gate, not an optional check, per the resolved finding above.
- The concrete pilot-run steps and where results get recorded (proposed:
  a dated results doc under `docs/superpowers/research/`, or a new
  `docs/open-decisions.md` line per plugin summarizing pilot outcome).
- Confirmation that each step still ends with "install/pilot done, human
  go-ahead obtained *before* this step ran" rather than the plan executing
  installs on its own authority.
