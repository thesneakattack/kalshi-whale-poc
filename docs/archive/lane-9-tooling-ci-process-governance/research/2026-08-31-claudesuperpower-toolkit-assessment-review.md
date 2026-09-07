# Self-review: claudesuperpower.com toolkit assessment (2026-08-31)

Reviews `docs/superpowers/research/2026-08-31-claudesuperpower-toolkit-assessment.md`
(the merged PR #312 copy) for internal consistency and unaddressed scope, per
CLAUDE.md's "nothing advances on one pass" HARD RULE. This is the cheap,
same-author pass — primary-source re-verification of the load-bearing claims
happens in the separate adversarial-review pass, not here, but a few checks
were cheap enough to run directly against this machine's own plugin cache
rather than trust the doc's website-scrape numbers blind.

## What was checked

1. **The FINAL VERDICT's 4 pilot candidates actually exist in
   `claude-plugins-official`, under the names/marketplace id claimed.**
   Verified against this machine's locally-synced marketplace clone
   (`~/.claude/plugins/marketplaces/claude-plugins-official/`), not the
   website — the local clone is the artifact `claude plugin install` will
   actually read, so it's a stronger check than re-scraping the site.
   - `pr-review-toolkit`, `claude-security`, `claude-md-management`: present
     as vendored subdirectories under `plugins/`, each with a
     `.claude-plugin/plugin.json`.
   - `pr-review-toolkit`'s 6 claimed subagents (`code-reviewer`,
     `code-simplifier`, `comment-analyzer`, `pr-test-analyzer`,
     `silent-failure-hunter`, `type-design-analyzer`) all exist verbatim
     under `plugins/pr-review-toolkit/agents/*.md`. Exact match, no
     discrepancy.
   - `claude-security`'s claimed subagent trio (`scan-inventory`,
     `scan-researcher`, `scan-verifier`) confirmed present, plus two the
     research doc didn't mention (`patch-generator`, `patch-verifier`) —
     consistent with, not contradicting, the doc's "generates
     confidence-scored patches for human review" description. Its
     `hooks/hooks.json` is a banner/notice hook only, not an auto-patch
     mechanism — confirms the doc's "patches... that you apply when you
     choose" claim; nothing in the plugin auto-writes to CLAUDE.md or
     anywhere else on its own.
   - `claude-md-management`: `skills/claude-md-improver/` confirmed present,
     `commands/` dir present (the doc's claimed `/revise-claude-md`).
   - **`codspeed` initially looked like a fabricated marketplace
     attribution** — it is absent from both `plugins/` and
     `external_plugins/` (the two directories that hold every other
     plugin). Checking the marketplace manifest directly
     (`.claude-plugin/marketplace.json`) resolved this: `codspeed` is a
     real, correctly-attributed entry, just sourced differently — its
     `source` field points to an external git URL
     (`https://github.com/CodSpeedHQ/codspeed.git`, pinned to a specific
     SHA) rather than being vendored in-repo like the other three. Author
     is "CodSpeed", not Anthropic. This is not an error in the research
     doc — its own FINAL VERDICT already carries a lower trust score for
     codspeed (87/100, 236 stars) distinct from the "six... 100/100 trust
     (first-party)" group, so the doc did not misrepresent codspeed as
     first-party. **Worth carrying into the design stage anyway**: unlike
     the other three, installing codspeed pulls code from a third party's
     own repo at install time (pinned SHA, but still a distinct
     supply-chain shape from a fully-vendored Anthropic plugin) — the
     pilot design should treat it with the same install-time scrutiny this
     repo already applies to any third-party dependency, not lump it in
     with the first-party three.

2. **The doc's claim about what's currently enabled in this project's
   `.claude/settings.json`** ("only `context7`, `dimensional-analysis`,
   `chrome-devtools-mcp` are project-enabled") — confirmed accurate against
   the live file: `context7@claude-plugins-official`,
   `dimensional-analysis@trailofbits`, `chrome-devtools-mcp@chrome-devtools-plugins`,
   all `true`, nothing else present.

3. **Internal consistency of the FINAL VERDICT against the doc's own
   findings section** — no contradictions found. Every plugin in the
   "adopt/pilot" list has a findings-section paragraph justifying it; every
   plugin in "explicitly do not adopt" has a stated reason (credential
   custody, redundancy, or "no concrete gap identified first"); the
   "not yet actionable" pair (`sentry`/`grafana`) is correctly gated on the
   already-tracked "no deployment target" open-decisions item rather than
   introducing a new blocker.

## Unaddressed scope

- The doc is a **research artifact only** — it stops at "recommend piloting
  4 plugins," with no design for *how* to pilot them (order of installation,
  what "try the audit-only pass first" concretely means as a verification
  step, what would make a pilot a pass/fail, or a rollback path if a plugin's
  hook chain conflicts with `guard_workflow.py`/`guard_data_db.py` on the
  hot edit path). That is expected — it is explicitly a research-stage
  document — but it means the doc alone is not yet actionable; the design/
  spec stage this review unblocks needs to answer those questions before an
  implementation plan can be written.
- The doc does not verify `claude-md-management`'s and `claude-security`'s
  actual write-scope claim ("grants write access to CLAUDE.md itself") against
  the plugins' own permission declarations — it's asserted as a natural
  consequence of what the skills *do* (CLAUDE.md-editing, patch-generation)
  rather than checked against a manifest field. Low risk (the claim is
  plausible and conservative, not a claim the pilot design would relax), but
  flagged for the adversarial pass to check directly if a `permissions` or
  similar field exists in either plugin's manifest.
- No check yet of whether any of the 4 plugins declare MCP servers requiring
  credentials/network egress beyond what a local Claude Code session already
  has (relevant to whether piloting one needs an `.env`/secrets decision
  before use, separate from the "install changes settings.json" gate the doc
  already flags). Also left to the adversarial pass / design stage.

## Verdict

**GO** — no factual errors found in the research doc's core claims after
independent verification against the local marketplace manifest and this
project's live settings; the one thing that looked like an error (codspeed's
missing directory) turned out to be a different-but-legitimate source
mechanism, not a mistake. Proceed to the adversarial-review pass, then
consolidation, then the design/spec stage.
