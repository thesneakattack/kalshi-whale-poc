---
name: session-handoff
description: Use when ending a substantial Claude coding session, before /clear, or when preparing work for a fresh session. Leaves exact git/verification state and next work reconstructable from the repository rather than depending on chat memory.
---

# Session Handoff

Finish and verify the current logical unit first; do not start another merely
because context remains.

Collect:

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git log -10 --oneline
```

Report:
- current branch and HEAD, and whether that branch has an open PR (per
  `.claude/rules/branching-and-ci.md`, work in progress normally lives on
  an initiative branch, not `main` — say so if it's still on `main`),
- work completed this session,
- last fully completed plan/task unit,
- first incomplete next unit,
- any partial/uncommitted work,
- full pytest status,
- frontend lint/build status if relevant,
- quality/architecture audit status,
- browser/contract/CI status if relevant,
- new ROADMAP items,
- accepted audit-baseline debt,
- measurements worth rechecking,
- docs/status/CHEATSHEET sync state.

Use the existing `/checkpoint` skill first when verified work should be
committed/pushed/checked in CI. Use `/close-roadmap-item` when a roadmap
item shipped.

The next session should reconstruct progress from git/current HEAD, not depend
on this chat transcript.
