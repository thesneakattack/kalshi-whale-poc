# Consolidation — AI-assisted engineering principles design (2026-09-07)

Reconciles the stage-2 artifact
(`2026-09-07-ai-assisted-engineering-principles-design.md` at `afbb516`),
its same-session self-review, and the independent adversarial review
(fresh Agent, no session memory, did not read the self-review) per
CLAUDE.md's "nothing advances on one pass" HARD RULE. Every adversarial
finding adopted below was re-derived by the authoring session first
(#613's rule); the re-derivation commands and numbers are in this
document, the revised spec cites them.

## Agreement

Both reviews found the spec internally consistent on tier obligations
and the line anchors correct (adversarial §2 CONFIRMED all eleven). The
self-review's five pre-pass corrections (23/7 split, #272, marginals,
anchors, `github_client` readers) were not contested; the adversarial
pass independently confirmed 23/7 and the `main.py`/`frontend/src/js/`/
`LANES[1]+[2]` marginals.

## Adjudication of the adversarial findings

| # | Finding | Re-derived? | Decision |
|---|---|---|---|
| 1 | Six unchanged lines (CLAUDE.md:38 :43 :48 :49 :120; `branching-and-ci.md:76-78`, `:88-94`) still say every in-scope PR gets the three-artifact cycle, contradicting Tier B; the "any PR … PASS only" bullet ignores the exemption and escalation | Yes — each line read at HEAD; all six say "self-review, adversarial review, consolidation" for any in-scope PR | **Adopt.** §5 gains verbatim replacements for all six, each scoped to "Tier A"; the merge bullet says the exemption is applied first and `review-tier` takes `--exempt`/`--tier A`. |
| 2 | §3.1 rule 1's suffix list excludes `.md`, so CLAUDE.md-only PRs are Tier B as worded; the table's numbers need §6.1's prose list | Yes — 13 PRs flip on this reading | **Adopt.** The prose-always list becomes rule 2 of the definition. Counts re-run from the definition as reworded: 133 A / 67 B at `6a63309` (the adversarial's 134/66 includes #663, merged during review). |
| 3 | `config/settings.yaml` is the sole trigger for 3 PRs (#389 #397 #596), not 0; #273 and #559 also flip under rule 3 | Yes — my earlier marginal removed the extras entry but not Lane 7's copy of the same path; re-run removing both gives exactly #389 #397 #596. Rule-3 flips are the adversarial's `gh pr diff` finding, cited as its | **Adopt.** D3 corrected; the flips listed. |
| 4 | `settlement_edge.py` (called at `settlement_edge_entry.py:108`), `candidate_log.py` (eight call sites in `strategy_engine.py`), `services/history/` (fees/P&L; #423) are Tier B | Yes — re-simulated: adding the three explicitly moves #423 #522 #557 #617 #620 #631 #636 to A; code split becomes 67 A / 17 B | **Adopt, and generalise:** a module directly imported by a Lane 3 module is Tier A, enforced by a test that scans Lane 3 imports (today: `app_state.py`, `signal_log.py`, `kalshi/interfaces.py`, `kalshi/contracts/lifecycle.py`, `position/account_positions.py`, plus already-A paths; moves 0 further PRs in the window). `services/history/` is added by name (money computation, not imported by Lane 3). |
| 5 | `tools/kanban_sync/` (the tier decider) and `tests/` of Tier A modules are Tier B | Yes | **Adopt partially:** `tools/kanban_sync/labels.py` → A (+#646); `tests/test_<stem>*.py` whose stem is a Tier A module → A (0 PRs in window). `tests/` entirely was measured (code B falls to 3 of 84) and rejected — it would return the uniform rule by the back door. Recorded as D8. |
| 6 | The `^#+\s*(self-review\|…)` heading regex fails a fully compliant PR (#625) and 7 others since 09-05 | Yes — 261 comment first lines fetched: strict matches 170, a first-line word match with optional `#`/`**` prefix matches 224; the 37 it misses are CI re-triggers, corrections, checkpoints, rechecks — none a review artifact | **Adopt** the loose form; §7's fixtures become the real first lines (`## Independent adversarial review`, `**Consolidation — GO**`, `**Self-review (lean, per PR #587)**`, …). |
| 7 | Defect predicate (c) excludes 16/16 recent `fix` bodies (every compliant body mentions its review) and is gameable; (a) needs a commit reader | Yes on the mechanism (under P3 every body names a review) | **Adopt:** (c) is dropped as a *defect*; "cited follow-ups" becomes its own non-defect column. (a) reads `git log origin/main` locally — the tool runs inside the repo; no `gh` reader needed. |
| 8 | `--days 14` cannot name fixed windows; "no decision cites the report" is a judgment | Yes | **Adopt:** `--since/--until`; the retire rule is stated as David's judgment on a dated line. |
| 9 | #613(2) replacement must start at "not yet by the hook"; line 117 must not assert a cause #615 calls unrecoverable | Yes — CLAUDE.md:60 and #615's text read | **Adopt.** |
| 10 | Lanes-design citations wrong (§1 line 30, §9 lines 359–360); D6's "same bullets" false for #613(2) and #615 | Yes | **Adopt:** citations fixed; D6 restated honestly — #613 is a decided CLAUDE.md docs PR that this PR *is*; the line-117/`:100` path fixes are adjacent by file and are listed as such for David to strike. |
| 11 | Hook tuples name three deleted files; a subset test would freeze them | Yes — `ls` confirms none of the three exists; `services/kalshi/` holds `account_client.py`, `transport.py`, `websocket.py` | **Adopt:** the plan removes the dead entries from the hook (Tier A edit), `REVIEW_TIER_A_PATHS` gets the exists-on-disk test `LANES` has, CLAUDE.md:100's stale path is corrected in the same edit. |
| — | `gh --json files` caps at 100 (PR #660: 124 files) | Trusted as the adversarial's finding (REST page 2 cited) | **Adopt:** files reader paginates via REST. |
| — | Silent decisions: claim-asserting docs outside pipeline dirs are Tier B; `.ddev/`, `Dockerfile`, `requirements*.txt` Tier B; independence of an artifact is not mechanically verifiable (all 261 comments are by one login) | Yes | **Adopt:** `.ddev/` → A (Basic-Auth proxy gate); the rest recorded as D9–D11 with rationale. |

## Where the self-review and adversarial review differ

None in substance. The self-review flagged rule 3 (data model) as an
un-tabled decision; the adversarial pass did not object to rule 3 but
found it under-applied (two more flips). Both stand: rule 3 stays and is
now D12 in the table.

## Merged fix list (applied in one revision, rechecked item by item)

1. §5: verbatim replacements for CLAUDE.md:38, :43, :48, :49, :100, :120
   and `branching-and-ci.md:76-78`, `:88-94`, each scoped to Tier A;
   merge bullet: exemption first, `--exempt`/`--tier A`.
2. §3.1: prose-always list as rule 2; §3.2 counts re-stated from that
   wording with the `6a63309`/#663 note.
3. D3 → sole trigger 3 (#389 #397 #596); rule-3 flips include #273 #559.
4. Tier A gains `settlement_edge.py`, `candidate_log.py`,
   `services/history/`, and the Lane-3-direct-import rule with its test;
   §3.2 re-stated (140 A / 60 B; code 67/17 before, 68/16 after item 5).
5. Tier A gains `tools/kanban_sync/labels.py` and `tests/test_<stem>*`
   for Tier A stems; `tests/` entirely rejected with the measurement.
6. §6.2 artifact regex → first-line word match with optional prefix; §7
   fixtures → the real first lines.
7. §6.3: (c) demoted to a non-defect column; (a) via local `git log`;
   `--since/--until`; retire rule as a dated judgment line.
8. §6.2: `--exempt <reason>` / `--tier A`; files reader paginates.
9. #613(2) replacement from "not yet by the hook"; line 117 without the
   cause; `:100` path fix.
10. Citations → lanes §1 line 30 and §9 lines 359–360; D6 restated.
11. Dead hook paths removed by the plan; exists-on-disk test for the
    constant.
12. §9 gains D8 (`tests/` not wholesale), D9 (claim-asserting docs
    outside pipeline dirs are Tier B unless escalated), D10 (`.ddev/`
    A; `Dockerfile`/`requirements*` B under CI's dependency audit), D11
    (independence not mechanically verifiable), D12 (rule 3).
13. §8: the branch merges `origin/main` before the PR opens and the plan
    re-anchors line numbers at that commit.

## Verdict

**NO-GO on `afbb516`; GO expected on the revision.** Fixes 1 and 4
change the spec materially — the rule text now touches eight lines
instead of four, and Tier B shrinks from 24 to 16 of 84 code PRs — but
neither changes scope (the same seven principles, the same three homes)
nor introduces a claim the two reviews never saw, so the fix-list
recheck suffices; a second full cycle is not required.
