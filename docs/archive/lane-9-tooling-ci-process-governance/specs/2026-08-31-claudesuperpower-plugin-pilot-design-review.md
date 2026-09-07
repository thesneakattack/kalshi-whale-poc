# Self-review: claudesuperpower.com plugin pilot design (2026-08-31)

Reviews `2026-08-31-claudesuperpower-plugin-pilot-design.md` for internal
consistency and unaddressed scope, per CLAUDE.md's "nothing advances on one
pass" HARD RULE.

## Checked

- **Both research-consolidation open questions are answered**, not just
  restated: the `pr-review-toolkit` overlap question gets a concrete
  two-tier evaluation plan rather than a guessed answer; the
  `claude-security` cost question is resolved by a primary-source-verified
  fact (the plugin's own README documents scope/effort as choosable, not
  fixed at `xhigh`).
- **The CLAUDE.md-write distinction from the research consolidation's fix
  #1 is correctly carried through**: `claude-md-management`'s pilot is
  explicitly audit-only (no write during the pilot); `claude-security`'s
  section correctly states it does *not* have that risk profile, matching
  the adversarial review's finding, not the original research doc's
  overstated claim.
- **Non-goals section is honest about what this stage is not deciding** —
  explicitly defers the actual install to a human go-ahead, consistent with
  the research doc's own "Process note" and CLAUDE.md's Toolchain-decision
  convention. This design doc does not itself constitute approval to
  install anything.
- **New finding surfaced mid-stage (codspeed's account requirement) is
  flagged as new, not folded in silently** — the doc is explicit that
  this wasn't visible in the prior research/review stage, which matters
  for anyone later asking "why does the design doc know something the
  research doc didn't."

## Unaddressed scope / weaknesses to flag for adversarial review

- The "install order" section moves `codspeed` last conditionally
  ("may end up needing its own separate go-ahead") but doesn't fully spell
  out what happens if the account question resolves *unfavorably*
  (mandatory login, no local-only path) — does the pilot simply stop at 3
  plugins, or does someone explicitly decide whether creating a CodSpeed
  account is worth it? Left as an implementation-plan-stage decision
  point rather than resolved here; worth the adversarial pass confirming
  that's an acceptable place to leave it rather than a gap.
- `pr-review-toolkit`'s Tier A acceptance criteria depend on there being a
  "real, already-in-flight PR" available to pilot against at
  implementation time — this document doesn't verify one exists right now
  (this repo's PR queue changes daily). Reasonable to leave as an
  implementation-time condition rather than a design-time fact, but flagged
  in case the adversarial pass judges it under-specified.
- No explicit disk/token-budget ceiling is set for any pilot run (e.g. "if
  `claude-security`'s narrow-scope run exceeds N tokens, stop and
  reconsider before going wider") — the design says cost is "measured... so
  a decision is made on real numbers," which is directionally right per
  the data-plane HARD RULE's own "measure, don't guess" spirit, but doesn't
  commit to a specific threshold. Worth the adversarial pass judging
  whether that's appropriately left open (numbers before judgment, not a
  premature guessed limit) or whether a concrete ceiling should exist.

## Verdict

**GO.** The design resolves every open question the research-stage
consolidation handed it, with a new primary-source-verified fact (codspeed's
account requirement) correctly flagged rather than assumed. Proceed to
adversarial review.
