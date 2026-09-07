# Consolidation: claudesuperpower.com toolkit assessment research stage (2026-08-31)

Reconciles the self-review and the independent adversarial review of
`docs/superpowers/research/2026-08-31-claudesuperpower-toolkit-assessment.md`
per CLAUDE.md's "nothing advances on one pass" HARD RULE, before the
design/spec stage starts.

- Self-review: `2026-08-31-claudesuperpower-toolkit-assessment-review.md`
  (this session, same-author pass) — verdict GO.
- Adversarial review: independent Agent call (general-purpose, no memory of
  this conversation), re-derived load-bearing claims from primary sources
  (the locally-synced `claude-plugins-official` marketplace manifest and
  plugin directories, this repo's live `.claude/settings.json`, CLAUDE.md and
  `.claude/rules/kalshi-integration-authority.md`, and live GitHub/web checks
  where reachable) rather than trusting the research doc's own tables —
  verdict GO-WITH-FIXES.

## Agreement

Both passes independently confirmed: all 4 recommended plugins genuinely
exist in `claude-plugins-official` with the specific subagents/skills/
commands claimed (verified file-by-file, not just directory listings); the
project's currently-enabled-plugins claim is accurate; the Kalshi
Integration Authority / data-plane-fidelity reasoning against
`bellwether-mcp` and every Kalshi-named MCP server is sound and matches this
repo's actual rules text; `codspeed`'s external-git-URL sourcing (distinct
from the 3 vendored-in-repo plugins) is real and was already correctly
lower-trust-flagged by the original doc, not an error.

## Disagreement / new findings — adjudicated

No disagreement between the two reviews on the merits (the adversarial pass
resolved an item the self-review had flagged as merely unchecked, rather
than contradicting it). Both feed into one merged fix list; none are
close calls needing tie-breaking.

## Merged fix list (required before/alongside the design stage)

1. **Correct the "grants write access to CLAUDE.md itself" claim.** It is
   accurate for `claude-md-management` (`claude-md-improver`'s SKILL.md
   frontmatter includes `Edit` and its own description states it writes to
   CLAUDE.md directly) but overstated for `claude-security` — its
   patch-generation agent is scoped to a scratch workspace only
   ("Work ONLY inside `WORKSPACE`... The repository itself is not yours to
   touch") and produces a patch file for human application; nothing in its
   documented workflow auto-writes to the live repo's CLAUDE.md. The design
   stage must treat these two plugins' CLAUDE.md-risk profile as distinct,
   not identical.
2. **Relabel unverifiable website figures as unverified, not fact.** Star
   counts and trust scores (32.8–32.9K/100, 87/100, 236, etc.) and the
   `/best/*` category rankings rest on the original scan session's
   now-unreproducible browsing of claudesuperpower.com; a second machine
   could not reach the same pages to confirm them. One spot-checked figure
   (a Kalshi-named MCP server's "0 stars") did not reproduce (1 star found
   independently) — not decision-changing, but shows the star-count claims
   specifically should not be treated as settled. The design stage's
   citations of these numbers should carry an "as scanned 2026-08-31,
   unverified" qualifier rather than being restated as fact.
3. **Add two open questions the research doc didn't raise, for the design
   stage to answer:**
   - Does `pr-review-toolkit`'s bundled `code-reviewer`/`code-simplifier`
     subagents functionally duplicate or conflict with this session's
     already-available `/code-review`/`/simplify` skills when both exist
     side by side (the doc only checked this for the *standalone*
     `code-review`/`code-simplifier` plugins, not the bundled versions
     inside `pr-review-toolkit`)?
   - `claude-security`'s orchestrator/patch agents run at `effort: xhigh`
     (confirmed in agent frontmatter) — what token/latency cost does a
     pilot scan actually incur, and is that acceptable for how often this
     repo would run it?

## Disposition

Rather than rewriting the original research doc's prose (which would blur
what was actually scanned on 2026-08-31 vs. corrected after review — this
repo's convention keeps dated research docs as point-in-time records, layering
correction via review/consolidation documents instead), a short amendment
note is added to the top of the original doc pointing here. The design/spec
stage must treat this consolidation's fix list as authoritative over the
original doc's FINAL VERDICT wherever the two differ.

## GO / no-go

**GO.** The research stage's core recommendation (pilot `pr-review-toolkit`,
`claude-security`, `claude-md-management`, `codspeed`, in that priority
order) survives independent verification. Proceed to the design/spec stage
with the 3-item fix list above folded in as inputs, not as blockers.
