# Next action

**No active task.** PR #665 merged 2026-09-07 22:25Z (`e7f89a0`), pulled into
the primary, worktree and branches cleaned up. David has not given a new
instruction since. Don't self-assign — report status and let him decide.

---

## Safety (check every session start — grep the values, don't trust `git status`)

`strategy.auto_exit_enabled: false` and `risk.max_daily_loss_pct: 0` in
`config/settings.yaml`, both **uncommitted** (David's own edits) — must stay
uncommitted and unchanged. `kalshi_account.trading_enabled` stays `false`. Kill
switch TRIPPED by design; David confirmed that's fine.

**Real incident, 2026-09-06:** both lines were found silently reverted to their
unsafe defaults with **zero git diff** — a bare `git checkout <branch>` on the
primary, even read-only, can drop them if unstashed. **Standing check:** `grep -n
'auto_exit_enabled\|max_daily_loss_pct' config/settings.yaml` after any checkout
or pull on the primary. Memory: `bare-checkout-can-drop-uncommitted-safety-config`.
Re-grepped and correct after the #664 and #665 pulls.

**Also standing:** the git stash stack is shared across the primary and every
worktree. Never bare `git stash` / `git stash pop`. Memory:
`stash-stack-shared-across-worktrees`.

**Untouched by design:** `ai-coding-productivity-analysis.md` and
`ai-engineering-llm-instructions.md` in the working directory are for a **peer
session**, not this one. Don't read, move or offer to act on them.

---

## Review tiering — DONE, both PRs merged

**#664** (`1546401`) shipped the decision: review depth tiers by consequence, a
mechanical Tier A/B path boundary in `REVIEW_TIER_A_PATHS`
(`tools/kanban_sync/labels.py`), enforced by `python -m tools.kanban_sync
review-tier --pr N`. Tier A keeps the full self-review → adversarial → consolidation
cycle; Tier B owes one persisted `Tier B self-review` comment plus green CI.
Merge only on `PASS` or `EXEMPT`; doubt escalates to A, never down. Memory:
`scale-review-effort-to-blast-radius` (marked RESOLVED — do not re-open the
conflict, run the tool).

**#665** (`e7f89a0`) answered the residual #664 left open: the Lane 3 dependency
scan now follows **two** import hops (`LANE3_SCAN_DEPTH = 2`), plus hand-listed
modules the graph can't express.

**Its adversarial pass returned NO-GO on two blocking findings — both are the
reason this PR is worth remembering:**

1. **A measured-sounding rationale that was a scanner artifact.** The shipped
   justification for rejecting depth 3 ("hop 3 reaches `observability/`,
   `research/`, `storage_health/`, `alerting/` through `app_state` and
   `fault_log`") was produced by `_resolve_import_target` widening
   `from services.<pkg> import <sub>` to the whole package — 140 such lines
   under `services/`. `fault_log` reaches none of the eight second-hop paths; a
   file-precise resolver never reaches those three directories at all. The
   **decision** survived (its PR-outcome measurement re-derived independently
   over #256–#664: 149/51 → 153/47 → 156/44); the **explanation** did not.
   Lesson: a number being measured does not make the story told about it measured.
2. **The answer omitted an item its own question listed.**
   `services/whale_pipeline_perf.py` was one of eight modules
   `docs/open-decisions.md` question 3 named as mattering, is imported by
   `kalshi_trade_tape.py:36` and `whale_stream_handlers.py:23` on the whale hot
   path, and is not reachable in two hops — so the depth-2 answer silently
   dropped it. Now hand-listed with a test.

Also fixed: the scan **failed open** (swallowed `SyntaxError`, quietly shrinking
the Tier A set — the direction "doubt escalates to A, never down" forbids; of 52
files scanned, 16 change the count and only `app_state.py` would have tripped the
non-vacuous floor); `candidate_ledger`'s rationale was wrong (a completeness gate
via `claim()`, not destructive); `services/quality/` drags 15 Tier B tooling test
files in through the `quality` test stem (disclosed, not reverted).

---

## Open follow-ons, none blocking

- **`docs/open-decisions.md` question 4 (new, 2026-09-07):** should
  `services/observability/` be Tier A? Its `prune()` runs `DELETE FROM
  metric_samples` on a retention cutoff — the same "destroys recorded data" test
  that put `backup/` on the list — yet it is Tier B, and it is the *only* module
  behind all four PRs depth 3 would have moved. Deliberately not folded into
  #665: adding it reclassifies PRs that PR never measured.
- **The resolver itself is package-granular.** #665 corrected the words, not the
  code. The shipped list is a safe **superset** of what a precise resolver gives
  (only `alerting/alerting.py` would be left uncovered), so the error direction is
  escalation. Fixing it is a scope change that must re-measure — question 3 says so.
- **The tier measurement is not a fixture.** No committed script, no frozen
  window; the numbers are point-in-time observations reproducible only by
  re-running the classifier against live `gh` data. Question 3's retirement
  clause ("re-measure rather than assuming depth 3") depends on someone
  rebuilding that.
- **`#661`** — 31 file-site + 38 issue-site cross-lane citation debt inside
  already-merged lanes. **`#605`** — four contributors fixed and live, magnitude
  gap still unexplained; `#639` and `#648` unassigned follow-ons. **`#642`** —
  open on the reframing that the app has a frequent, unattributed ≥10s-stall
  pattern independent of backups.

---

## Standing lessons (apply, don't re-litigate)

- **Run a new gate against its own PR, and against a frozen corpus, before
  believing it.** `review-tier` passed its own PR on a technicality twice, each
  time caught only by an independent adversarial pass. Memory:
  `run-a-new-gate-against-its-own-pr`.
- **A gate that fails open is worse than no gate** — it reports safety it isn't
  providing. Check which direction every guard errs in.
- **A found bug is a prompt to look for the same defect class elsewhere.**
- **An unverified "fix" is worse than an honest open gap.**
- **Three distinct, separately-posted PR comments are mandatory for Tier A and
  must be counted directly** (`gh pr view <n> --json comments`), never inferred
  from the body's narrative. Memory: `persist-code-pr-reviews-as-comments`.
- **Merged is not deployed — the pull is the deploy.** Memory:
  `merged-is-not-deployed-pull-is-the-deploy`.
- **This file holds the single next action — rewrite it, don't append.**
