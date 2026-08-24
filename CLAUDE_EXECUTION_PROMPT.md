# Claude Execution Prompt — Quality Control Plane

You are implementing the Kalshi autotrader Quality Control Plane in `thesneakattack/kalshi-whale-poc`.

Do not begin coding from this prompt alone.

1. Read `CLAUDE.md` in full.
2. Read `docs/superpowers/specs/2026-08-24-quality-control-plane-design.md`.
3. Read `docs/superpowers/plans/2026-08-24-quality-control-plane.md`.
4. Re-ground against current HEAD: `git status`, `git log -12 --oneline`, `ROADMAP.md`, the relevant module `CHEATSHEET.md` files, current workflows, and recent audit/diagnostic commits.
5. Use `superpowers:subagent-driven-development` if available; otherwise use `superpowers:executing-plans`.
6. Execute one task at a time, TDD-first, with one reviewable commit per independently testable task.
7. If the repo has already implemented part of a task since this plan was written, verify the acceptance criteria and implement only the missing portion. Do not create duplicate services.
8. Never read/write live `data/*.db` from tests. Never reset or delete accumulated project data for verification.
9. Before touching Kalshi-shaped data or protocol behavior, read the relevant current file in `docs/kalshi/`.
10. Do not enable real trading or weaken any real-money safety gate.
11. When implementation uncovers a new real bug, explicitly decide whether the measurement that exposed it should become a runtime diagnostic, CI guard, existing-guard test, or documented one-off before closing that task.
12. After each task: run targeted tests, the relevant quality audit/build check, inspect the diff, then commit.
13. At major checkpoints: run the full pytest suite, frontend lint/build, and browser/API checks defined in the plan.
14. When roadmap work ships, update both `ROADMAP.md` and `static/status.html` using the repo’s established sync process.
15. Before claiming completion, perform Task 22 in full and request code review.

The goal is not merely to add more tests. The goal is to convert the project’s repeated manual investigative work into accumulated engineering capability: always-on runtime evidence where live state matters, deterministic CI/CD guardrails where source/fixtures are sufficient, and shared pure checks where both need the same logic.
