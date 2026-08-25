# SESSION HANDOFF — Claude toolchain install (Session A → Session B)

**Temporary file.** Delete once Session B's acceptance tests pass (§35 of the
task). `.claude/rules/tooling-plugins.md` is the permanent artifact and stays.

## Task

Install + wire a specialized plugin/MCP toolchain into this repo, verify it,
restart WSL, and re-verify in a brand-new Claude session. No Kalshi feature
work. Superpowers remains the authoritative engineering process; everything
else supplements it.

## Git state

- Branch: **`chore/claude-toolchain`**, branched off `origin/main` (`a659f0c`).
- HEAD: **`7a46d4a`**, pushed. Working tree clean.
- **Woodpecker CI: all 5 checks green on `7a46d4a`** (architecture-audit,
  kalshi-contract-fixtures, dependency-audit, browser-e2e, pytest).
  Note: the preceding commit `0088822` showed a transient `error` on
  `tests-dependency-audit`; it passed on re-run and `main` was green on the
  same check, so it was infra, not this change.
- No PR opened yet — deliberate, so Session B can finish acceptance testing
  and delete this handoff file before the branch is proposed for merge.
- Toolchain work is committed here.
- **Do not disturb `refactor/kalshi-integration-boundary`** — it has open
  **PR #3** (Kalshi Integration Phase A) and is unrelated to this work. The
  toolchain changes were deliberately kept off it.

## Installed (all project scope except Superpowers)

| Plugin | Version | Marketplace |
|---|---|---|
| superpowers | 6.3.0 | claude-plugins-official (**user** scope, pre-existing) |
| context7 | — | claude-plugins-official |
| dimensional-analysis | 3.0.1 | trailofbits |
| second-opinion | 1.7.0 | trailofbits |
| 42crunch-api-security-testing | 1.15.0 | 42crunch-marketplace |
| chrome-devtools-mcp | 1.7.0 | chrome-devtools-plugins |

Marketplaces are declared in `.claude/settings.json` under
`extraKnownMarketplaces`. The Chrome one required
`--sparse .claude-plugin skills` — a plain clone pulls the Chromium
`devtools-frontend` submodule chain (incl. LLVM) and the checkout fails.
**Keep the sparsePaths entry.**

## MCP state (from `claude mcp list`, Session A)

- `plugin:context7:context7` — ✔ Connected
- `plugin:chrome-devtools-mcp:chrome-devtools` — ✔ Connected
- `plugin:second-opinion:codex` — ✘ Failed (needs OpenAI Codex CLI; not installed)
- Pre-existing claude.ai connectors: Wolfram ✔, Devil's Advocate ✔,
  PagerDuty ! needs auth (pre-existing, unrelated).

**No duplicate standalone `chrome-devtools` MCP exists** — none was present
before install, and the plugin is the single canonical provider.

## Chrome DevTools / WSL (the hard part)

Claude runs in WSL2. Session A resolved the browser stack:

1. Installed **Google Chrome stable 151.0.7922.173** via apt →
   `/usr/bin/google-chrome`. Required, because the plugin's default MCP args
   (`npx chrome-devtools-mcp@1.7.0`) resolve puppeteer `channel: 'chrome'`
   against a system install. No plugin-cache files were modified.
2. ddev serves HTTPS with a **mkcert** CA that the new Chrome didn't trust
   (`ERR_CERT_AUTHORITY_INVALID`). Fixed properly: created Chrome's NSS DB
   (`certutil -d sql:$HOME/.pki/nssdb -N --empty-password`) and ran
   `CAROOT=/mnt/c/Users/davidf/AppData/Local/mkcert mkcert -install`.
   No `--acceptInsecureCerts` hack.

Verified in Session A **at the puppeteer level** (MCP tools aren't exposed
until a fresh session):

- headless launch + nav → `200`, title `Whale Signal — Paper Trading Terminal`
- **headed** launch (the plugin's default mode) via WSLg → `200` ✔
- console capture works (0 errors), network capture works
- FastAPI backend calls captured: `/api/config`, `/api/session`, `/api/state`
  — all `200 application/json`

App URL: **`https://kalshi-whale-poc.ddev.site:8443`** (port 8443, not 443).

⚠️ Headed mode depends on **WSLg**. See the `.wslconfig` warning below.

## GitNexus

- Configured for Claude Code (`gitnexus setup --coding-agent claude`).
- Installed 9 skills to `~/.claude/skills/` (user scope, ~657 tok always-on).
- **Indexed**: 6,615 nodes / 14,355 edges / 255 clusters / 300 flows at
  commit `357d6ef`. Index lives in `.gitnexus/` (now gitignored).
- `.gitnexusrc` = `{"indexOnly": true}` — committed, mandatory. Stops
  GitNexus injecting competing `CLAUDE.md` / `AGENTS.md` / skills.
- Smoke test passed: `impact kelly_scaled_max_size` correctly returned
  `evaluate` → `trading_loop` (main.py), risk LOW, epistemic exact.
- ⚠️ Index was built on the **`refactor/kalshi-integration-boundary`** commit.
  Run `npx gitnexus@latest status` in Session B; re-`analyze` if it drifted.

## Files intentionally changed

- `.claude/rules/tooling-plugins.md` — **new**, permanent routing policy.
- `.gitnexusrc` — **new**, `indexOnly` guard.
- `.gitignore` — added `.gitnexus/`.
- `.claude/settings.json` — **additive only**: `enabledPlugins` +
  `extraKnownMarketplaces`. Hooks block byte-identical to the pre-install
  backup.
- `.claude/SESSION_HANDOFF.md` — this file (temporary).

Backups of the pre-install state: `~/claude-toolchain-backup-2026-08-25/`.

## Existing hooks — confirmed intact

Project `.claude/settings.json` hooks verified unchanged by diff against
backup: SessionStart orientation, `compact` reorientation, UserPromptSubmit
checkpoint nudge, PreToolUse `guard_data_db.py`, PostToolUse
`check_py_syntax.py` + `run_tests.py`. User-scope `sql_guard.py` also intact.

## Outstanding items for Session B

1. **MCP/skill acceptance tests** — plugins installed mid-session, so their
   tools were never exposed to Session A. Session B must actually *call*:
   Chrome DevTools MCP (mandatory, §32), Context7 doc retrieval,
   dimensional-analysis on a real calculation, 42Crunch on the OpenAPI spec,
   second-opinion capability discovery.
2. **GitNexus's always-on hooks are still active and should be removed.**
   `gitnexus setup` appended to **`~/.claude/settings.json`**:
   - `PreToolUse` matcher `Grep|Glob|Bash` → `gitnexus-hook.cjs`
   - `PostToolUse` matcher `Bash` → `gitnexus-hook.cjs`
   These spawn a node process on *every* search and Bash call — exactly the
   idle overhead the task's §22 forbids. Session A tried to remove them via
   both Bash and Edit and was **blocked by the permission classifier** on the
   user-global settings file. Needs the user to remove them by hand (or grant
   permission). The hook script stays on disk, so it's re-enablable.
   The `sql_guard.py` hook in the same file must be preserved.
3. **42Crunch is not usable yet** — needs a platform/trial token in
   `~/.42crunch/conf/env` plus the `42c-ast` binary. Run `/42crunch-setup`;
   requires user credentials (account signup — a user decision).
4. **second-opinion is not usable yet** — needs OpenAI Codex CLI or Google
   Gemini CLI + a paid account. Skill is discoverable; capability discovery
   is all that was required. User decision whether to install.
5. Delete this file once 1 passes (§35).

## WSL resources — IMPORTANT, read before touching `.wslconfig`

Session A measurements (before restart):

```
Host:  AMD Ryzen 9 3900X — 24 logical CPUs, 31.93 GB RAM
WSL:   nproc 24 | MemTotal 16,332,472 kB (~15.6 GiB) | swap 4.0 GiB
       uptime 2 days 13:29
```

`C:\Users\davidf\.wslconfig` exists and says `memory=8GB`, `processors=8`,
`guiApplications=false` — **but none of it is in effect.** WSL is running on
defaults (all 24 CPUs, ~50% of host RAM). Cause: the file uses **trailing
inline `#` comments on value lines**, which WSL's INI parser does not
support, so those lines are discarded.

**Therefore: do NOT "fix" the syntax as-is.** Making that file valid would
*downgrade* the VM from 24 CPUs/15.6 GB to 8 CPUs/8 GB, and
`guiApplications=false` would kill WSLg — which would break **headed** Chrome
(the plugin's default mode). A restart alone changes nothing here.

If more resources are genuinely wanted, this is the corrected form (comments
on their own lines), leaving headroom for Windows/VS Code/Chrome/Docker:

```ini
[wsl2]
# Cap only — WSL2 allocates dynamically and reclaims.
memory=20GB
processors=20
swap=8GB
guiApplications=true
```

This is a **recommendation only** — Session A did not modify the host file.
Expected post-restart reading if left alone: **24 CPUs / ~15.6 GB / 4 GB
swap — i.e. NO CHANGE, which is the correct and desired outcome.**

## Exact next steps after WSL restart

1. `cd /home/davidf/code/portfolio/showcase-projects/autotrade`
2. `claude` — **brand-new session**, not `--continue` / `-c` / `--resume`.
3. Load `CLAUDE.md`, `.claude/rules/` (incl. `tooling-plugins.md`), this file.
4. `git status && git branch --show-current && git log -1 --oneline`
   → expect branch `chore/claude-toolchain`.
5. Report WSL before/after resources (table above).
6. `claude plugin list && claude mcp list` — verify all 6 plugins + MCPs.
7. Run the §32 Chrome DevTools acceptance test against the live dashboard
   (`ddev describe` first; start ddev if down).
8. Run remaining acceptance + routing tests, then delete this file.
