# Self-review: planning-lanes design (round 1)

> Provenance note (2026-09-06): this round-1 artifact was overwritten on
> disk when the round-2 self-review was written to the same filename
> before either had been committed. Restored verbatim from the authoring
> session's record so that `…-design-consolidation.md` (round 1), which
> adjudicates this document by name, points at a real artifact. Content
> below is unchanged from the original.

Date: 2026-09-06. Same author/context as the design doc (stage 1 of the
required cycle — cheapest layer, catches sloppiness before an
independent pass spends effort on it).

**Verified, not assumed:** `tools/kanban_sync/labels.py`'s `phase:*`
vocabulary (`PHASE_RESEARCH`...`PHASE_DONE`, lines 54-59) — the design's
§7 claim that a parallel `lane:*` set is cheap and consistent with
existing practice is accurate, checked directly against source, not
recalled.

**Real gap, flagged not hidden:** §9's first open question
(`whale_stream/` straddling Lane 1/Lane 2) is a genuine, unresolved
boundary problem, not a rhetorical question — `whale_stream_handlers.py`
processes trade/ticker WS messages (transport, Lane 1's job) AND feeds
`decision_bridge.py` (signal generation, Lane 2's job) in the same
module. The design doc's "transport-level only" carve-out for
`whale_stream/` in Lane 1 is a real attempt to resolve this by
sub-module rather than whole-package assignment, but it isn't stated
explicitly as the resolution — only implied by the parenthetical. This
should be made explicit in consolidation: **the boundary is drawn at the
function/module level, not the package level, whenever a package's
own internal split already exists** (as it does here:
`whale_stream_handlers.py` vs `decision_bridge.py` are already separate
files).

**Untested claim:** §6's anti-drift rules are all *designed* to work via
session discipline alone, per David's explicit preference — but none of
them have actually been tried yet. The doc doesn't claim they'll work,
it proposes them as the first attempt per the established preference
order, with escalation explicitly conditioned on "provably not
followed." That's honest, but the adversarial reviewer should stress-
test whether any of the 5 is likely to fail in an obviously predictable
way (e.g., rule 2's "check before starting work" is easy to skip under
time pressure, same failure mode as the abandoned `guard_workflow.py`
automated guards, just manual now instead of automated — is a rule
actually different from a removed automated check if nothing makes it
harder to skip?).

**Consistency check:** Lane priority (P0: Lanes 1+5; P1: Lanes 2/3/4;
P2: Lanes 6/7/8/9) is internally consistent with David's stated priority
("everything downstream from and including the initial REST/WS
connection") — Lane 1 is the connection itself, Lane 5 is the shared
hot-path infrastructure that Lane 1 (and everything else) runs on top of,
both are prerequisites rather than "downstream," which is why both are
P0 rather than only Lane 1. This reasoning isn't spelled out in the main
doc — worth adding in consolidation if the adversarial reviewer agrees
it's correct.

**What I did not do:** I did not re-run any of the 5 inventory agents'
work to re-derive the 154-issue/62-plan/98-research/79-spec counts —
those are cited from tonight's already-completed, already-verified
audit (see `docs/next-action.md`'s history), not re-verified here. The
adversarial reviewer should spot-check at least a sample of those
citations against the actual GitHub/filesystem state directly, not trust
this doc's summary of a summary.

**Verdict:** no self-identified blocker to sending this to adversarial
review. Two items flagged above (the whale_stream boundary resolution,
and the P0-for-both-Lane-1-and-5 reasoning) should be made explicit in
the main doc during consolidation regardless of what the adversarial pass
finds, since they're real gaps in the doc's own clarity, not just review
bait.
