# SESSION HANDOFF — Claude toolchain (Session B complete)

**Temporary file.** One action remains (WSL restart + Chrome re-verify, below).
Delete it once that passes. `.claude/rules/tooling-plugins.md` is the
permanent artifact and stays.

## Verified capability states (Session B, 2026-08-25)

Every state below was established by a real invocation, not by
`claude plugin list`. A connected MCP does not prove the underlying
capability works — Chrome DevTools proved exactly that.

| Capability | State | Evidence |
|---|---|---|
| Superpowers 6.3.0 | **ACTIVE** | skills load + execute |
| GitNexus | **ACTIVE** (repaired this session) | accurate impact query after index rebuild |
| dimensional-analysis 3.0.1 | **ACTIVE** | analyzed `kelly_scaled_max_size`, local skill, no creds |
| Context7 | **LIMITED** | anonymous/shared limits; retrieved FastAPI 0.134.0 docs |
| Chrome DevTools MCP 1.7.0 | **BROKEN_LOCAL** | headful launch fails: no X server (see below) |
| 42Crunch 1.15.0 | **BLOCKED_EXTERNAL** | no `~/.42crunch/`, no `42c-ast` binary |
| second-opinion 1.7.0 | **BLOCKED_EXTERNAL** | neither Codex nor Gemini CLI installed |

Blocked plugins are excluded from active routing and must not be
re-litigated each session. Both have documented fallbacks in
`.claude/rules/tooling-plugins.md`.

## GitNexus was genuinely broken and is now fixed

Session A's index was left in a bad state (`incrementalInProgress` flag +
`LadybugDB ... WAL checkpoint` failure). Symptom: queries returned
`status: ok` / `epistemic: "exact"` while emitting a **target-independent
whole-repo blob** — `composite_confidence` and `kelly_scaled_max_size`
produced byte-identical impact sets (51 direct, `risk: CRITICAL`), including
a JavaScript file `CALLS`-ing a Python function. Session A's smoke test
passed only because the true answer happened to sit inside that blob.

Repaired with:
```bash
npx gitnexus@latest clean
GITNEXUS_WAL_CHECKPOINT_THRESHOLD=67108864 npx gitnexus@latest analyze --force --skip-agents-md
```
Index now 6,514 nodes / 14,228 edges / 250 clusters / 300 flows.
Post-repair the same query correctly returns `impactedCount: 2, risk: LOW`
with real callers only. The detection heuristic and repair command are
recorded in `.claude/rules/tooling-plugins.md`.

## WSL resources — the `.wslconfig` diagnosis was inverted

Session A predicted "NO CHANGE" on the theory that trailing inline `#`
comments made `.wslconfig` invalid. **That was wrong** — the file applied in
full on this boot and *downgraded* the VM:

```
Before (Session A):  24 CPU | 15.6 GiB RAM | 4 GiB swap | WSLg on
After  (Session B):   8 CPU |  7.8 GiB RAM | 4 GiB swap | WSLg OFF
```

`guiApplications=false` is what removed WSLg, which is the direct cause of
the Chrome DevTools failure. Inline `#` comments are in fact tolerated.

`C:\Users\davidf\.wslconfig` has been rewritten (backup:
`.wslconfig.bak-2026-08-25`) to 16 CPU / 16 GB / 8 GB swap /
`guiApplications=true`, plus `autoMemoryReclaim=gradual` and `sparseVhd=true`
so idle memory returns to Windows. **Not yet applied — needs
`wsl --shutdown`.**

## THE ONE REMAINING ACTION

1. `wsl --shutdown` from Windows, then reopen WSL.
2. Confirm: `nproc` → 16, `free -h` → ~16 GiB, and `echo $DISPLAY` non-empty
   (WSLg back).
3. Re-run the Chrome DevTools acceptance test against
   `https://kalshi-whale-poc.ddev.site:8443` (`ddev start` first if down):
   `new_page` → `list_console_messages` → `list_network_requests` →
   `get_network_request` on an `/api/*` call.
4. If it passes, flip Chrome DevTools to **ACTIVE** in
   `.claude/rules/tooling-plugins.md` and delete this file.

Everything except headful launch is already proven working headless against
the live app: 200 + correct title, valid mkcert TLS chain (no
`--acceptInsecureCerts`), DOM queries, console capture, full network table,
and browser-originated FastAPI calls (`/api/config`, `/api/session`,
`/api/state`, `/api/position-netting/groups` — all `200 application/json`
through the nginx proxy). The only missing piece is the X server.

## Still outstanding — needs the user, blocked by the permission classifier

**GitNexus's always-on hooks are still in `~/.claude/settings.json`:**
- `PreToolUse` matcher `Grep|Glob|Bash` → `gitnexus-hook.cjs`
- `PostToolUse` matcher `Bash` → `gitnexus-hook.cjs`

These spawn a node process on *every* search and Bash call — the exact idle
overhead the routing policy forbids. Session A and Session B both attempted
removal (Bash and Edit) and were **blocked by the permission classifier** on
the user-global settings file. Needs manual removal or a granted permission.
**Preserve the `sql_guard.py` PreToolUse hook in the same file.** The
GitNexus hook script stays on disk, so this is reversible.
Backup: `~/.claude/settings.json.bak-*` was not created (the copy was also
blocked); the pre-install original is at
`~/claude-toolchain-backup-2026-08-25/user-settings.json`.

## Observation for later (not acted on — out of scope)

`kelly_scaled_max_size` (`services/strategy_engine.py:45`) is dimensionally
consistent, but `scale = 1.0 - kelly_fraction * (1.0 - raw_scale)` goes
**negative when `kelly_fraction > 1`**, returning a negative position
ceiling. The line-80 guard only rejects `None` and `<= 0`; `config_bounds.py`
bounds `take_profit_pct`/`stop_loss_pct`/`min_unit_cost`/`max_unit_cost` but
has **no upper bound for `kelly_fraction_of_cap`**, and that field is live-
editable from the dashboard Controls panel (committed value: `0.3`). A typo
of `3` would invert sizing. Same family as the two bugs in CLAUDE.md's "Bug
pattern to watch for". No code was changed for this audit.

## Git state

- Branch: `chore/claude-toolchain`.
- Session B changed: `.claude/rules/tooling-plugins.md` (rewritten to verified
  reality), `.claude/SESSION_HANDOFF.md` (this file).
- No application code touched. Project hooks verified **byte-identical** to
  the pre-install backup; all six `.claude/hooks/` scripts present.
- **Do not disturb `refactor/kalshi-integration-boundary`** — open PR #3,
  unrelated.
