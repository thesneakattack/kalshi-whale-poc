# Self-review — CI pipeline audit (2026-09-02)

Same-session review of `docs/superpowers/research/2026-09-02-ci-pipeline-audit.md`
and its three companion documents, per CLAUDE.md's "nothing advances on one
pass" HARD RULE (self-review layer: internal consistency + unaddressed
scope, before independent adversarial review).

## Internal consistency

- Numbers cross-checked across the four documents agree: 3074 tests / 168
  files / 477.66s summed test time appears identically in the pytest-profile
  doc and is consistent with the cost-analysis doc's independent
  `tests-pytest` wall-time figures (202s median push→feature, which is
  wall time on 4 workers, not summed — 477.66/4 = 119.4s ideal vs 127.6s
  measured, both cited).
- The testmon finding (27/27 logs, zero selections) in the cost-analysis doc
  and the mechanism explanation (`configure.py:65-85`, `-m` deactivates
  unless `--testmon-forceselect`) in the mechanics doc agree and the fix
  recommended in the main doc's Tier 1 §1 names the exact flag the mechanics
  doc identified from source, not from testmon's docs alone.
- The main doc's "what this audit could not answer" section matches the two
  gaps actually left open in the companion docs (cron token scope; the
  never-started-workflow edge case) — no other companion-doc "unverified"
  item was silently dropped from the main synthesis; each Tier 2 item names
  which companion-doc section backs it.

## Spot-verified directly against current source (not trusted from the
subagent reports alone)

1. `scripts/ci-testmon-run.sh:53` — read directly, confirmed
   `python -m pytest --testmon -n 4 -m "not slow"`, no `--testmon-forceselect`
   or `--testmon-noselect`.
2. `tests/support/runtime_isolation.py` — read directly, confirmed
   `_guarded_connect` at the cited location wraps every test-issued
   `sqlite3.connect` call and already distinguishes real `data/` paths from
   test paths, so adding a `PRAGMA` there cannot reach live data.
3. `pytest.ini` — read directly, confirmed the `slow` marker's own
   docstring: "scans the real repo tree end-to-end (redundant with a
   dedicated CI job); excluded by default."
4. `.claude/hooks/run_tests.py` — read directly (not from the subagent
   report's line numbers, which could have been stale even though HEAD is
   unchanged): traced `tests_for()`'s actual stem-matching logic by hand for
   both `services/app_state.py` (package-name branch never fires because the
   file sits one level too shallow — `len(p.parts) > i+2` is false — so only
   the bare `app_state` stem is globbed) and `services/kalshi/websocket.py`
   (package-name branch fires, adds `kalshi` to the stem set, globs
   `test_kalshi*.py`). Then independently confirmed on disk: `ls tests/ |
   grep -i app_state` → no match (zero tests, as claimed); `ls
   tests/test_kalshi*.py | wc -l` → 18 (matches the cited figure exactly).
   This is the audit's highest-stakes "the local hook has a real gap" claim
   and it now rests on my own direct trace of the current code plus a live
   directory listing, not a subagent's summary table.
5. `.claude/rules/branching-and-ci.md` (loaded into context directly, not
   summarized) — confirmed the 5 required branch-protection contexts and
   the `quality-frontend-build`-excluded-because-path-filtered-posts-no-
   status reasoning cited in Tier 2 §8 verbatim.
6. `.woodpecker/tests-dependency-audit.yml` — read directly, confirmed no
   `when.path` filter exists today, supporting Tier 2 §8's framing as a
   live tradeoff rather than an already-solved problem.

## New evidence gathered this session, not present in any original subagent
report (recorded so the adversarial reviewer knows to check these fresh
rather than assume they were pre-existing)

- `--collect-only`, `--dist loadfile`, and the fsync falsification command
  were all run for the first time on resume (originally blocked/NOT RUN).
- The `/dev/shm` `noexec` discovery is new: found by running the full
  suite under tmpfs and diagnosing 12 unexpected failures, not predicted by
  any subagent. This changed Tier 1 §2's recommendation from "tmpfs" to
  "`PRAGMA synchronous=OFF`."
- Four live Woodpecker API calls (pipeline #340 detail, its GitHub commit
  status, a log-entry fetch, a repeat manual-events check) were made with
  the same pull-only token used throughout, confirming §9.3's cancel/status
  inferences on one real example (with the caveat that the never-started-
  workflow sub-case wasn't exercised by that particular example).

## Unaddressed scope / things this review chose not to chase further

- The mechanics doc's ~10 smaller "inference, falsifier given" items (e.g.,
  the GitHub 300-file compare-API cap interacting with Woodpecker's
  pagination, the `RunsOn` copy-paste-slip observation) were not
  independently re-verified here — they're flagged in the mechanics doc
  itself as low-stakes and none of them back a Tier 1/2 recommendation in
  the main document. Not chasing these further follows this repo's own
  "decide, don't over-investigate" standing guidance: re-verifying a claim
  that wouldn't change any recommendation isn't worth the API/token budget.
- Did not attempt to obtain a push-scoped Woodpecker token to close the
  cron-access gap — that requires the user's action, not more investigation
  time, and is named explicitly as such in the main document.

## Verdict

No internal contradiction found; the highest-stakes claims (testmon
inertness, the app_state/kalshi mapping gaps, the fsync-then-noexec
discovery) are now independently re-derived from current source/live data
in this session, not merely inherited from the original subagent reports.
Ready for independent adversarial review.
