# Kalshi Dual-Phase Plan Bundle

This bundle contains planning/orchestration files only. It does not implement the refactor.

## Files

- `.claude/rules/kalshi-integration-authority.md`
- `.claude/skills/kalshi-integration-refactor/SKILL.md`
- `docs/superpowers/research/2026-08-24-kalshi-integration-audit.md`
- `docs/superpowers/specs/2026-08-24-kalshi-integration-boundary-design.md`
- `docs/superpowers/plans/2026-08-24-kalshi-integration-dual-phase.md`
- `docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md`
- `docs/superpowers/plans/2026-08-24-kalshi-integration-phase-c.md`
- `docs/superpowers/kalshi-integration-kickoff.md`

## Install

From the repository root:

```bash
unzip -o /path/to/kalshi-dual-phase-plan-bundle.zip -d .
git status --short
git diff --check
```

Review the files, then:

```bash
git add \
  .claude/rules/kalshi-integration-authority.md \
  .claude/skills/kalshi-integration-refactor/SKILL.md \
  docs/superpowers/research/2026-08-24-kalshi-integration-audit.md \
  docs/superpowers/specs/2026-08-24-kalshi-integration-boundary-design.md \
  docs/superpowers/plans/2026-08-24-kalshi-integration-dual-phase.md \
  docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md \
  docs/superpowers/plans/2026-08-24-kalshi-integration-phase-c.md \
  docs/superpowers/kalshi-integration-kickoff.md \
  docs/superpowers/kalshi-integration-bundle-readme.md

git diff --cached --check
git diff --cached --stat
git commit -m "Plan document-backed Kalshi integration refactor"
git push
```

Then start a fresh Claude session and paste the prompt in
`docs/superpowers/kalshi-integration-kickoff.md`.
