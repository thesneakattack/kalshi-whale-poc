# Consolidation: claudesuperpower.com plugin pilot design stage (2026-08-31)

Reconciles the self-review and independent adversarial review of
`2026-08-31-claudesuperpower-plugin-pilot-design.md` per CLAUDE.md's
"nothing advances on one pass" HARD RULE, before the implementation-plan
stage starts.

- Self-review: `...-design-review.md` (this session) — verdict GO, flagged
  3 items for the adversarial pass to weigh in on (codspeed's unresolved
  branch, PR-availability assumption, no numeric cost ceiling).
- Adversarial review: independent Agent call (general-purpose, no memory of
  this conversation), re-derived every load-bearing claim from primary
  sources (the plugins' own README/SKILL/agent files, and — going further
  than the design doc itself did — `CodSpeedHQ/codspeed`'s `.mcp.json` and
  `skills/codspeed-optimize/SKILL.md`) — verdict GO-WITH-FIXES, 4 items.

## Key new finding (adjudicated)

The adversarial review **resolved** what the design doc had left open: does
`codspeed` require an external account for local-only use? Design doc said
"unknown, verify at implementation time." Adversarial review found the
plugin's packaged `.mcp.json` registers a remote MCP server
(`https://mcp.codspeed.io/mcp`) and its own `codspeed-optimize` skill
explicitly instructs against falling back to raw local benchmarking without
CodSpeed auth — i.e., the plugin's *designed* workflow is built around the
hosted service, not an optional add-on to it. This is a stronger, more
useful answer than the design doc reached, sourced from files the design
doc's own investigation didn't check (it stopped at the top-level CLI
README). Adjudication: adopt the adversarial review's finding as fact —
it's directly evidenced (quoted plugin source), not a judgment call, so
there's nothing to weigh against the design doc's weaker "unresolved"
stance beyond simply having looked further.

The self-review's own flagged item (codspeed's "what if the account
question resolves unfavorably" branch) is now moot in the way the self-review
worried about becoming moot: the account requirement is not "unfavorable
if," it "is" — so the design's install-order section needs to state a
go-ahead-for-external-account decision point for plugin 4, not a
verification step.

## Merged fix list (applied directly to the design doc — see diff)

1. Rewrite the `codspeed` section: external-account requirement is
   resolved-yes (cite the MCP server registration + skill's auth-mandatory
   language), not an open verification item. The go-ahead this pilot needs
   for plugin 4 includes "create/use a CodSpeed account," parallel to how
   `sentry`/`grafana` are already parked pending a different open decision
   — this is now the same shape of blocker, made explicit rather than
   discovered later.
2. Fix `claude-md-management`'s "audit-only mode" language: there is no
   selectable mode — it's one workflow with a mandatory human-confirmation
   gate before any Edit (Phase 4/5). Reword to describe the approval gate,
   not a "mode."
3. Add explicit language to every per-plugin acceptance-criteria block:
   these criteria measure whether the pilot produced *evaluable signal*,
   not a mechanical pass/fail on plugin quality — the install-or-keep
   decision after a pilot is a separate, human judgment call the criteria
   don't automatically resolve. (Self-review had already flagged the
   qualitative-criteria risk; adversarial review sharpened it into a
   concrete rewrite instruction rather than leaving it as a question.)
4. Restate, in the closing "what the implementation plan must contain"
   checklist, that plugin 4's step requires the account go-ahead as an
   explicit precondition — not just implied by the per-plugin section.

The self-review's other two flagged items (PR-availability assumption for
`pr-review-toolkit`'s Tier A pilot; no numeric cost ceiling for
`claude-security`'s narrow-scope run) were reviewed by the adversarial pass
only indirectly (via the general internal-consistency check) and not
separately contradicted or confirmed as needing a fix — left as-is:
reasonable to leave as implementation-time conditions rather than
design-time facts, per the self-review's own reasoning, which nothing in
the adversarial pass undermined.

## GO / no-go

**GO, with the 4 fixes applied** (see the corresponding edits to
`2026-08-31-claudesuperpower-plugin-pilot-design.md` in this same commit).
Proceed to the implementation-plan stage.
