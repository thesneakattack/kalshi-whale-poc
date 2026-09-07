# Plan: claudesuperpower.com plugin pilot rollout — 2026-08-31

Status: awaiting execution. This plan is written and reviewed but **not
executed** — no plugin is installed, `.claude/settings.json` is unchanged.
Per the design stage's non-goals, execution starts only after an explicit
human go-ahead (see Task 1); nothing in this pipeline installs anything on
its own authority.

## Input

Design stage, reviewed and consolidated GO:
`docs/archive/lane-9-tooling-ci-process-governance/specs/2026-08-31-claudesuperpower-plugin-pilot-design.md`
+ `...-design-review.md` + `...-design-consolidation.md`. This plan turns
that design directly into ordered, executable tasks — no new decisions are
made here beyond sequencing and exact file diffs.

## Constraints

- One commit per task (this repo's "meaningful checkpoint commits"
  convention — not one giant commit for all 4 plugins).
- Each `enabledPlugins` edit is followed immediately by
  `pytest tests/test_mcp_and_plugin_wiring.py tests/test_hooks_wiring.py`
  before committing — confirmed via direct source read
  (`tests/test_mcp_and_plugin_wiring.py:51-55`,
  `test_every_enabled_plugin_has_a_known_marketplace`) that all 4 target
  plugins use the `@claude-plugins-official` suffix, which that test
  already treats as always-known (hardcoded, no `extraKnownMarketplaces`
  entry needed) — so this check is expected to pass without any other
  settings.json change, but it runs every time regardless of expectation,
  per "never guess; verify or falsify."
- This is workflow/tooling config, not application code — `services/`,
  `main.py`, and `config/settings.yaml` are untouched by every task below,
  consistent with CLAUDE.md's "workflow/tooling and application code never
  overlap."
- Pilot-run outputs (reports, patches, benchmark numbers) are recorded in a
  results doc (Task 6) — never auto-applied. Any suggested CLAUDE.md edit
  or code patch a pilot produces is a separate, explicit human-approved
  step, outside this plan's scope.

### Task 1: Obtain go-ahead for the pilot (plugins 1–3)

- [ ] Not a code task. Present this plan (and the design it's built from) to
      the user for the explicit go-ahead CLAUDE.md's Toolchain section and
      this plan's own header require before Task 2 starts. Record the
      go-ahead (or a "not now") as a dated `docs/open-decisions.md` line
      resolving the existing 2026-08-31 open-decisions entry for this
      research thread.
- [ ] Blocks Tasks 2–4. Does not block Task 5 (which is its own,
      independent go-ahead for plugin 4's external account — see below).

### Task 2: Install and pilot `pr-review-toolkit`

- [ ] Edit `.claude/settings.json`'s `enabledPlugins`, adding
      `"pr-review-toolkit@claude-plugins-official": true`.
- [ ] Run `pytest tests/test_mcp_and_plugin_wiring.py tests/test_hooks_wiring.py`;
      confirm pass before committing.
- [ ] Commit as its own checkpoint (`chore: pilot pr-review-toolkit@claude-plugins-official`).
- [ ] Run the design's Tier A pilot: invoke `silent-failure-hunter` and
      `pr-test-analyzer` against one real, already-in-flight PR in this
      repo, as an addition to — not a replacement for — that PR's normal
      self-review/adversarial-review/consolidation cycle.
- [ ] Run the design's Tier B comparison once: invoke `code-reviewer`/
      `code-simplifier` against a diff already reviewed by this session's
      built-in `/code-review`/`/simplify` skills; note whether findings are
      a strict subset (redundant) or add anything.
- [ ] Record the outcome in the Task 6 results doc (signal found / signal
      confirmed absent / Tier B redundant-or-not) — per the design's
      corrected acceptance-criteria language, this records evaluable
      signal, not a plugin-quality verdict; the keep/drop call is made in
      Task 6, by a human, from this record.

### Task 3: Install and pilot `claude-security`

- [ ] Edit `.claude/settings.json`'s `enabledPlugins`, adding
      `"claude-security@claude-plugins-official": true`.
- [ ] Run the same two wiring tests as Task 2; confirm pass before
      committing.
- [ ] Commit as its own checkpoint (`chore: pilot claude-security@claude-plugins-official`).
- [ ] Run one narrow-scope pilot scan (per the design: `services/kalshi_account_client.py`
      + `services/kalshi/signing.py`, the repo's own auth/credential-handling
      code — not a full-repo sweep), specifying scope and a moderate (not
      `xhigh`) effort explicitly per the plugin's own "say what you want"
      option (confirmed in its README — see design doc).
- [ ] Record the report (findings, or a clean result — both are useful) and
      the measured cost (tokens, wall-clock duration) in the Task 6 results
      doc. Any patch the plugin proposes is left unapplied pending separate,
      explicit human review — this repo's automation-restriction invariant
      applies regardless of pilot outcome.

### Task 4: Install and pilot `claude-md-management`

- [ ] Edit `.claude/settings.json`'s `enabledPlugins`, adding
      `"claude-md-management@claude-plugins-official": true`.
- [ ] Run the same two wiring tests as Task 2; confirm pass before
      committing.
- [ ] Commit as its own checkpoint (`chore: pilot claude-md-management@claude-plugins-official`).
- [ ] Run `claude-md-improver` through its Phase 3 quality report; when it
      asks for confirmation before any Edit (Phase 4/5, per the design's
      corrected description of the actual approval-gate mechanism —
      there's no separate "audit-only mode" to select), decline the edit
      for the pilot itself.
- [ ] Evaluate the report's suggestions against this repo's explicit
      terseness convention (CLAUDE.md's own opening paragraph: one line per
      rule, no incident narrative, `git log -S` provenance instead of inline
      story). Record, in the Task 6 results doc, at least one suggestion
      explicitly accepted or explicitly rejected as generic-template bloat,
      per the design's acceptance criteria.

### Task 5: Obtain the separate go-ahead for `codspeed`'s external account

- [ ] Not a code task, and independent of Task 1. Present the design
      stage's resolved finding to the user: `codspeed`'s designed workflow
      requires an external CodSpeed account (`codspeed auth login`, its
      packaged plugin registers a remote MCP server at
      `https://mcp.codspeed.io/mcp`) — this is a go-ahead decision of the
      same shape as the already-parked `sentry`/`grafana` open-decisions
      item, not a plain plugin install. Record the decision (go-ahead,
      decline, or defer) as its own `docs/open-decisions.md` line.
- [ ] If declined or deferred: stop here. Tasks 2–4 (and this plan overall)
      are still fully actionable without Task 6/7; `codspeed` simply stays
      unpiloted until/unless this go-ahead is revisited.

### Task 6: Install and pilot `codspeed` (only if Task 5's go-ahead is given)

- [ ] The account itself (creation, `codspeed auth login`) is a human
      action — this task does not create external accounts on the user's
      behalf.
- [ ] Confirm `codspeed auth login` has actually succeeded (human-run,
      interactive) before proceeding to the benchmark step below — the
      account go-ahead in Task 5 authorizes creating/using the account, it
      is not itself confirmation the login step is done.
- [ ] Edit `.claude/settings.json`'s `enabledPlugins`, adding
      `"codspeed@claude-plugins-official": true`.
- [ ] Run the same two wiring tests as Task 2; confirm pass before
      committing.
- [ ] Commit as its own checkpoint (`chore: pilot codspeed@claude-plugins-official`).
- [ ] Run one benchmark against a real hot-path module already named by the
      data-plane HARD RULE (`services/kalshi/` or `whale_stream`),
      producing a flamegraph and a measured baseline — used as the HARD
      RULE's own "measured... before it ships" evidence, not a
      replacement for the judgment step that follows a measurement.

### Task 7: Consolidate pilot results

- [ ] Write a dated results doc,
      `docs/superpowers/research/<date>-claudesuperpower-plugin-pilot-results.md`,
      summarizing each piloted plugin's recorded outcome (Tasks 2–4, and 6
      if run) and a keep/drop/reconsider-later recommendation per plugin —
      a human decision informed by, not dictated by, the pilot signal (per
      the design's corrected acceptance-criteria language).
- [ ] Resolve the `docs/open-decisions.md` line this research thread has
      carried since 2026-08-31, replacing it with the actual outcome
      (which plugins are kept enabled, which were dropped and why).
- [ ] For any plugin dropped: `claude plugin disable <name>` (or remove its
      `enabledPlugins` entry) and revert to `false`/absent in a commit —
      the rollback path the design stage specified, exercised for real
      rather than left theoretical.

## What this plan deliberately does not do

Execute itself. Tasks 1 and 5 are explicit human go-ahead gates, not
formalities to check off quickly — this whole pipeline (research → design →
this plan) exists so that when the go-ahead is given, execution is a
already-reviewed, already-sequenced set of small, revertible steps, not a
fresh decision made under time pressure.
