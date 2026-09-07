# Workflow audit — 2026-08-27

Prompted by a direct complaint: rookie-level mistakes repeating across weeks;
more time spent directing, informing, and tooling Claude than on application
code; the result slower sessions, not better ones (example given: the ~24h
realtime data-plane investigation). Every figure below is measured from
session transcripts (127 files, tool calls deduplicated by id), `git log`,
the memory directory, and one live run of the test hook — not recalled.
Full page: https://claude.ai/code/artifact/c0ad3ccf-fb7c-47bd-bf0f-53d183d7f14f

## Numbers

| Measure | Value |
|---|---|
| Edit-hook test command, measured | 188 s vs a **30 s** harness timeout in `.claude/settings.json` → killed on every cold run |
| Manual `pytest` runs, 10 days | 598 (one session: 320 runs, 476 edits, 33 h) on top of hook + CI |
| Skill invocations | 90 of ~15,300 tool calls (0.6 %); top skill `sync-status-docs` ×27, deleted 08-26 |
| GitNexus / dimensional-analysis / Chrome DevTools / GitHub MCP | 0 / 0 / 0 / 0 calls; Context7 15 |
| `superpowers:` systematic-debugging, verification-before-completion, requesting-code-review, using-git-worktrees | 0 each |
| Ad hoc `sqlite3` vs `/api/quality/summary` | 198 : 19 (`python -c` 600) |
| Subagent spawns Explore/Plan (skip CLAUDE.md) | 28 of 57 |
| CLAUDE.md | 135 → 759 lines, 08-07 → 08-27, 38 commits |
| Commits touching only docs/ or .claude/ | 133 of the last 300 (44 %) |
| docs/superpowers | 65 files, 36k lines; ROADMAP 13 "not started / not implemented" |
| Realtime data-plane work | 11 docs, 8,108 lines, 43-task plan, ~30 h session time, 3 code commits on branch |
| AQC tooling | ≈1,560 lines + 13-phase investigation; run once before today, 0 escalations |
| Memory files that are corrections for ignoring existing guidance | 9 of 29 |

## Findings

1. **The per-edit test hook has never been able to finish.** `run_tests.py`'s
   docstring records two prior "silently timed out on every invocation"
   incidents, each fixed by raising the *inner* `subprocess.run` timeout
   (now 240 s); the outer 30 s harness limit was never checked. Six tests
   fail on `feat/realtime-data-plane-remediation` today; the hook could not
   have said so. Sessions compensate with manual full-suite runs.
2. **The installed toolchain is routed in prose and not used.** Routing lives
   in 500 lines of rule files; CLAUDE.md never names a plugin; the
   session-start hook lists project skills and cheatsheet titles but nothing
   about plugin capabilities. `tooling-plugins.md` still claims the GitNexus
   hooks are live in `~/.claude/settings.json`; they were removed.
3. **Evidence rules define "complete" but never "enough".** Both
   `realtime-data-plane-evidence.md` and
   `autonomous-quality-coordination-evidence.md` end with completion rules
   that require matrices, alternatives, and rejected-option write-ups before
   a fix counts. The "decide, don't over-investigate" correction sits in
   relevance-gated memory and did not load.
4. **Findings are filed, linked, and not acted on.** Only 2 of 48 research/
   spec docs are unreferenced; six memory items say "act on the next
   trigger" and none has. Knowledge enters at five layers (memory, CLAUDE.md,
   ROADMAP, docs/superpowers, module READMEs) with no promotion or expiry.
5. **Custom tooling built next to plugins that already do the job.**
   `tools/kanban_sync` (1,100 lines, 54 commits, then PR #124 "defer to
   plugin claims"); `root-cause-debugging` ≈ `superpowers:systematic-
   debugging`; `final-verification` ≈ `verification-before-completion`; six
   orchestrator skills ≈ `superpowers:executing-plans`; `cleanup-worktrees.sh`
   ≈ AQC §6.1 ≈ `using-git-worktrees`.
6. **Directives grew 5.6× and compliance did not move.** The one intervention
   that changed behavior was mechanical (`session_orient.sh` printing
   CHEATSHEET titles unconditionally). Every miss since was answered with a
   longer paragraph.

## Root causes, ranked

1. Enforcement lives in prose, not mechanism.
2. Process has completion criteria but no budget.
3. Redundant ownership (four test owners; five workflow-health tools; two
   debugging skills per job) — so nothing is trusted and everything re-runs.
4. Knowledge enters five layers with no promotion or expiry.

## Non-goals

No change to trading behavior, safety gates, kill switch, CI branch
protection, or `data/*.db` handling. This is a workflow remediation only.
