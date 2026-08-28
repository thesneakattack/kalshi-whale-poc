# Domain: workflow (this repo's Claude tooling: hooks, skills, rules, CI, tools/)

Canonical: `docs/superpowers/specs/2026-08-27-workflow-audit.md`,
`docs/superpowers/plans/2026-08-27-workflow-remediation.md`, `docs/open-decisions.md`.

- A rule that needs a paragraph becomes a hook, a printed line, or a test — never a new paragraph in CLAUDE.md.
- Tooling never couples into the app (CLAUDE.md "Workflow/tooling and application code never overlap"); `ci-cd-guardrails` for anything deterministic.
- Verify hooks by running them (`bash .claude/hooks/session_orient.sh`; a payload piped into `.claude/hooks/run_tests.py`) — a hook that "should" fire has silently failed here twice.
- Check `ListAgents` before touching any skill or rule; another session may name it as in use.
