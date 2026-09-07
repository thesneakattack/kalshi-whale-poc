# Quality Coordination Cadence — I2 (repository concurrency and persistence candidates)

**Task:** I2 of `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
**Branch / HEAD at start:** `chore/autonomous-quality-coordination-investigation` @ `07398bd` (worktree
`.claude/worktrees/aqc-investigation`, base `origin/main` @ `8d1796b`). Re-ground: no open PRs;
`origin/chore/realtime-dp-investigation` advanced to `da865ba` (12 commits, still no PR); this branch does
not touch any path that branch owns.
**Analysis instant:** 2026-08-26 01:32Z (every "and counting" figure is relative to this).

Evidence classes: **[E1]** source/CI · **[E2]** git/PR history · **[E3]** deterministic experiment ·
**[E4]** live runtime · **[E5]** upstream docs · **[E6]** inference.

---

## 1. Question and falsification criteria

**Question.** What persistence / grace policy prevents an escalation automation from acting on transitional
findings *without* hiding durable ones — derived from this repository's measured cadence, not a guessed
24/48-hour window?

A candidate policy is acceptable on this data only if, replayed against the measured series, it
(a) escalates **zero** findings that later cleared without external action (false-early), and
(b) would have escalated the one durable problem in the window materially before it was fixed by hand
(excessive delay). Both sides are reported; neither is optimised alone.

## 2. Reproducible sampling window [E2/E4]

| Source | Window | Size | How captured |
|---|---|---|---|
| git, `origin/main` | 2026-08-07 → 2026-08-25 | 356 commits | `git log --first-parent --format='%H %ct %ci %s' origin/main` |
| **ratchet era** (`d63a5e5`, QCP Task 3, first `baseline.json`) | 08-24 07:08Z → 08-25 23:42Z (**40.6 h**) | 54 first-parent commits, 11 merges | replayed audit, §6 |
| Woodpecker | pipeline #1 08-24 08:38Z → #140 08-26 01:23Z | 131 retrievable (105 push, 26 pull_request) | anonymous `GET /api/repos/1/pipelines` + per-pipeline detail (Appendix B) |
| PRs | #1 08-25 04:14Z → #11 23:42Z (**19.5 h**); branch protection live from #1's merge 04:30Z | 11, all merged | `gh pr list --state all --json …` |
| unmerged branches | `chore/realtime-dp-investigation` (12 commits from 21:29Z), this branch (from 23:56Z) | 2 | `git log origin/main..<branch>` |

Commits per day on `main` since the epoch (context for how atypical this window's velocity is): 11, 34,
13, 51, 8, 3, 13, 45, 30, 5, 32, 49, **59, 58** (08-24, 08-25). The sampled window is the two busiest days
of the repo's life, with two Claude sessions active in parallel for part of it. **Limits:** ~41 h of
ratchet history, ~20 h of PR history, one developer. Every number below is a range or a count with n
attached, never a population estimate.

## 3. PR lifecycle [E2]

| PR | branch | first commit | PR opened | merged | commit→open h | open→merge h | branch life h | files |
|---|---|---|---|---|---|---|---|---|
| #1 | `docs/branch-ci-workflow-policy` | 08-25 04:14Z | 08-25 04:15Z | 08-25 04:30Z | 0.02 | 0.25 | 0.27 | 10 |
| #2 | `chore/apply-live-tuning-and-kickoff-note` | 08-25 04:45Z | 08-25 04:46Z | 08-25 04:58Z | 0.02 | 0.19 | 0.21 | 2 |
| #3 | `refactor/kalshi-integration-boundary` | 08-25 04:40Z | 08-25 05:55Z | 08-25 16:42Z | 1.24 | 10.78 | 12.02 | 75 |
| #4 | `refactor/project-manifest-drift-tolerance` | 08-25 07:41Z | 08-25 07:44Z | 08-25 07:49Z | 0.04 | 0.09 | 0.13 | 4 |
| #5 | `chore/claude-toolchain` | 08-25 06:30Z | 08-25 07:44Z | 08-25 07:53Z | 1.25 | 0.14 | 1.38 | 6 |
| #6 | `fix/kelly-fraction-bounds` | 08-25 07:25Z | 08-25 07:45Z | 08-25 08:09Z | 0.33 | 0.41 | 0.74 | 4 |
| #7 | `docs/woodpecker-concurrency-4` | 08-25 08:12Z | 08-25 08:15Z | 08-25 08:24Z | 0.05 | 0.14 | 0.19 | 1 |
| #8 | `perf/ci-faster-dependency-installs` | 08-25 08:23Z | 08-25 09:10Z | 08-25 09:14Z | 0.78 | 0.06 | 0.84 | 9 |
| #9 | `refactor/kalshi-integration-phase-c` | 08-25 16:51Z | 08-25 19:56Z | 08-25 20:00Z | 3.09 | 0.07 | 3.15 | 56 |
| #10 | `chore/realtime-data-plane-investigation` | 08-25 20:57Z | 08-25 20:58Z | 08-25 21:06Z | 0.02 | 0.13 | 0.15 | 6 |
| #11 | `docs/frontend-modularization-design` | 08-25 22:42Z | 08-25 22:42Z | 08-25 23:42Z | 0.01 | 1.00 | 1.00 | 6 |

- commit→open: median **0.05 h**, p90 1.25 h, max 3.09 h.
- open→merge: median **0.14 h**, p90 1.00 h, max 10.78 h (#3 is the single long-lived PR).
- branch life (first commit→merge): median 0.74 h, p90 3.15 h, max 12.02 h.

**Finding — PRs here are merge ceremonies, not work-in-progress signals.** A PR is opened a median of
3 minutes before it merges. Summed over all eleven, PR-open time covers 13.26 of 20.08 branch-life hours
(66 %), but 10.78 of those 13.26 are PR #3; **outside #3 the coverage is 2.48 of 8.06 h (31 %)**, and the
two initiatives active at the analysis instant (5.6 branch-hours between them) have no PR at all. Any
"open PR" active-work signal would have been blind to most of the real concurrency in this window; the
pushed **remote branch** is the signal that actually exists while work is in flight. (Feeds I3.)

## 4. Overlap between initiatives [E2]

Intervals = first commit → merge (or → now for the two unmerged branches). 13 intervals, 78 pairs.

| A | B | overlap h | shared paths |
|---|---|---|---|
| #2 | #3 `refactor/kalshi-integration-boundary` | 0.21 | 0 |
| #3 | #4 `refactor/project-manifest-drift-tolerance` | 0.13 | 1: `static/status.html` |
| #3 | #5 `chore/claude-toolchain` | 1.38 | 1: `static/project-manifest.json` |
| #3 | #6 `fix/kelly-fraction-bounds` | 0.74 | 0 |
| #3 | #7 `docs/woodpecker-concurrency-4` | 0.19 | 0 |
| #3 | #8 `perf/ci-faster-dependency-installs` | 0.84 | 0 |
| #4 | #5 | 0.13 | 0 |
| #4 | #6 | 0.13 | 0 |
| #5 | #6 | 0.46 | 0 |
| #7 | #8 | 0.01 | 1: `docs/woodpecker-ci.md` |
| #11 `docs/frontend-modularization-design` | `chore/realtime-dp-investigation` | 1.00 | 0 |
| `chore/realtime-dp-investigation` | this branch | 1.59 (→ now) | 0 |

**12 of 78 pairs overlapped in time; peak concurrency 4 (during PR #3's 12 h); only 3 overlapping pairs
shared a path, and every shared path is a generated or documentation file** (`static/status.html`,
`static/project-manifest.json`, `docs/woodpecker-ci.md`). Two readings: (1) path-overlap between
concurrent *code* work has not happened in this window — the branching policy's "one coherent initiative
per branch" is holding; (2) `static/project-manifest.json` — the leading I7 mechanical-remediation
candidate — is precisely the file two concurrent PRs did collide on. A regenerating fixer would have
contended with a human PR within its first day. The zero-overlap between the two currently active
branches is by construction (I0 §2.2's avoidance list), not luck.

## 5. Observation cadence on integrated `main` [E2/E3]

The QCP audit runs only on pushes; there is no scheduled `main` audit (`docs/woodpecker-ci.md`, I0 §3.3).
So the observation cadence for any `main`-observing automation **is** the integration cadence:

- 54 first-parent commits in 40.6 h; inter-commit gap median **0.27 h**, p75 0.63 h, p90 2.30 h, max
  **7.46 h** (overnight).
- PR era only (19 commits): median 0.46 h, p90 3.00 h, max 7.46 h.
- Successful pipeline wall time median 3.6 min, p90 5.4 min → observation latency after a push.
- 9 of 105 push pipelines were `killed` (superseded by a newer push before finishing) — an observation
  that never completed, which a policy must count as *no* observation, not as "still failing".

Commits arrive in **bursts** (median 16 min apart) separated by multi-hour gaps. That structure decides
the policy comparison in §9.

## 6. Transitional findings on integrated `main` — the replayed series [E3]

Method (Appendix A): for each of the 54 first-parent commits, `git archive` the tree, run **that
commit's own** `tools.quality_audit` against **that commit's own** `baseline.json` — exactly what a
`main`-observing automation would have seen after each integration. 164 s total, all 54 runs exit 0.

**The finding gate has never been red on integrated `main`** (0 new high-confidence errors in 54
observations). "New" findings appeared in two episodes:

| finding_id | first seen | observations | cleared | lifetime | class |
|---|---|---|---|---|---|
| `config-unread:alerting.crash_auto_resolve_after_sec` | 08-24 20:57Z (`c8a4529`) | 3 | 08-24 21:11Z (`0b7902c`) | **0.23 h** | warning/medium |
| `backend-route-unused:POST:/api/alerts/*/resolve` | 08-24 21:05Z (`be1cb67`) | 2 | 08-24 21:11Z (`0b7902c`) | **0.10 h** | info/medium |
| `api-usage:account.create_order` | 08-25 16:42Z (`9f53cc0`) | 4 | **open** | 8.83 h and counting | info/high |

Episode A was cleared by a baseline-sync commit ("Baseline 2 quality-audit findings from the crash-alert
resolution fix") 14 minutes after the first observation — three observations inside one commit burst.
Episode B is the I0 specimen: the fix (`aa1eb37`, "quality: sync api-usage baseline…") has been sitting on
`chore/realtime-dp-investigation` since 08-25 22:05Z — i.e. an active-work signal for that path became
available **5.4 h after** the first observation, and only as a remote branch, never a PR.

**Workflow-red is not finding-red.** `main`'s `quality-architecture-audit` *workflow* failed on 8 pushes
between 08-24 19:24Z and 08-25 03:21Z (pipelines 15, 16, 17, 21, 24, 25, 26, 27) and `quality-browser-e2e`
once (pipeline 2), while the replay proves the finding gate was green at every one of those commits. The
8 reds are the `tools.project_manifest --check` exact-equality incident that `project_manifest.py`'s own
docstring records ("sat red across three consecutive pushes unnoticed"), fixed by PR #4 at 07:49Z —
**12.4 h from first red to fix.** That is the only durable problem in the window, and it is a CI-status
problem, not a `QualityFinding`. It is used below as the "excessive delay" reference precisely because
nothing else in the window lasted long enough to be one.

## 7. Branch-level CI: how fast a red push goes green [E4]

| branch | push pipelines | failure | killed | failed workflows | failure→next green (h) |
|---|---|---|---|---|---|
| `main` (pre-protection pushes included) | 39 | 9 | 2 | architecture-audit ×8, browser-e2e ×1 | 0.16, 0.57, 0.36, 0.24, 0.17, **6.30**, 1.48, 1.21, 0.71 |
| `refactor/kalshi-integration-boundary` | 21 | 5 | 1 | architecture-audit, tests-pytest | 0.28, 0.31, 0.35, 0.22, 0.13 |
| `chore/realtime-dp-investigation` | 11 | 2 | 1 | architecture-audit | 0.50, 0.45 |
| `chore/claude-toolchain` | 6 | 1 | 2 | architecture-audit | 0.11 |
| `chore/autonomous-quality-coordination-investigation` | 3 | 1 | 0 | tests-pytest (the midnight-race flake, I0) | 0.98 |
| 9 other branches | 25 | 0 | 3 | – | – |

- 105 push pipelines → **18 failures, 9 killed**; failure→green median **0.35 h** (n = 18), max 6.30 h
  (the manifest incident's longest stretch).
- The `quality-architecture-audit` workflow failed 15 times overall: 9 on `main` (all pre-protection),
  6 on branches (35, 45, 52, 53, 124, 125) — every branch instance fixed by the next or second push.
- Pipelines 92/94/96 show `event: pull_request` with **`branch: main`** (the PR's *target*): live
  confirmation of the evidence rule's warning that a Woodpecker `branch: main` condition alone also
  matches PRs targeting main. (Feeds I4/I9.)

**"What would have generated noisy escalation if acted on immediately":** 18 pipeline failures + 3
transitional-or-in-flight `main` finding IDs in 41 h ≈ **0.5 escalation-worthy events per hour**, of
which exactly one (the manifest incident) lasted longer than 1.5 h — and that one was not a finding.

## 8. Stale tail versus active duration [E2]

- **Merged-but-undeleted:** `chore/realtime-data-plane-investigation` merged 21:06Z via PR #10 and is
  still on `origin` 4.4 h later (`delete_branch_on_merge: false`; the other ten merged branches were
  deleted by hand). A remote-branch active-work signal must therefore **exclude branches already
  contained in `main`** (`git branch -r --merged origin/main`), or a stale tail masquerades as work.
- **Active without a PR:** the realtime branch has been active 4.0 h across 12 commits (inter-commit
  gaps up to 1.4 h) with no PR; this branch 1.6 h. "No PR" is the normal state of in-flight work here
  (§3).
- Longest observed branch life: 12.02 h (PR #3). Longest idle gap inside a live branch: 1.4 h.

## 9. Persistence-policy simulation [E3]

Replayed against the three `main` episodes (§6) using the actual observation timestamps. "Escalation"
fires at the first observation that satisfies the rule; a time floor `T` fires on the next observation
after `T` has elapsed (there is no scheduled audit to fire it sooner).

| policy | rule | false-early (escalated, later cleared) | escalated, still open | delay to escalation (open episode) |
|---|---|---|---|---|
| P0 immediate | k=1 | **2** | 1 | 0.00 h |
| P-k2 | 2 observations | **2** | 1 | 3.31 h |
| P-k3 | 3 observations | **1** | 1 | 4.41 h |
| P-k4 | 4 observations | 0 | 1 | 7.00 h |
| P-T2h | elapsed ≥ 2 h | 0 | 1 | 3.31 h |
| P-T6h | elapsed ≥ 6 h | 0 | 1 | 7.00 h |
| P-T12h | elapsed ≥ 12 h | 0 | 0 (not yet) | – |
| P-T24h | elapsed ≥ 24 h | 0 | 0 (not yet) | – |
| P-k2+T6h | both | 0 | 1 | 7.00 h |
| P-k3+T12h | both | 0 | 0 (not yet) | – |

Reading, with the burst structure from §5 in mind:

1. **Observation counts are a broken proxy for persistence in this repo.** Both transitional episodes
   accumulated 2–3 observations within 14 minutes because commits land in bursts; `k=2` is as noisy as
   "immediate", and `k=4` only works by accident (it happens to exceed the burst length). Count-based
   rules must at least de-burst (count observations ≥ 1 h apart), at which point they are a time floor
   in disguise.
2. **Any elapsed-time floor ≥ 2 h produced zero false-early escalations**, with ≥ 8× margin over the
   longest transitional lifetime (0.23 h) and ≥ 5× over the longest branch failure→green (0.35 h median,
   1.48 h next-longest after the manifest incident).
3. **Adding the one active-work signal that actually existed** (a remote branch touching
   `tools/quality_audit/baseline.json` from 22:05Z, +5.4 h into episode B) changes the picture at the top
   of the range: with `T=6h` the escalation at +7.0 h falls inside the suppression window → no
   escalation, which is the correct outcome (fix in flight). With `T=2h` it escalates at +3.3 h, before any
   signal existed — defensible as "nobody had claimed it", but it is an **info-level inventory line**
   escalated as work, which is the noise the spec (§12) wants kept out of the issue tracker.
4. **Excessive-delay check against the only durable specimen** (manifest incident, 12.4 h to fix, §6):
   `T=2h`/`6h` would have surfaced it 6–10 h before the hand fix; `T=12h` roughly at the moment of the
   fix; **`T=24h` never** — 24 h is measurably too slow for this repo, and 48 h doubly so.

### Candidate ranges (derived, not chosen — I3/I10 select)

| Control | Candidate range | Derivation |
|---|---|---|
| Elapsed-time floor, error/high findings | **2–6 h** | lower bound ≥ 8× longest transitional lifetime and ≥ p90 `main` commit gap (2.3–3.0 h) so a second observation is likely inside it; upper bound ≤ half the only durable incident's time-to-fix |
| Elapsed-time floor, warning/info findings | **6–24 h** | these classes have only ever been baseline-sync noise here (all three episodes); nothing in the window shows an info/warning finding that deserved an issue at all |
| Observation requirement | ≥ 2 observations **≥ 1 h apart** (de-bursted), or none if the time floor stands alone | bursts of 2–3 commits in < 15 min are the norm |
| Active-work suppression signal | pushed **remote branch** whose changed paths overlap the finding's path/scope; exclude branches merged into `main` | PRs cover 31 % of branch life outside #3 and 0 % of current work (§3); stale merged tail exists (§8) |
| Suppression staleness expiry | branch idle (no new commits) for **≥ 2× the longest observed live-branch idle gap ≈ 3 h**, hard-capped at **≤ 2× the longest observed branch life ≈ 24 h** | §8; the cap keeps a forgotten branch from suppressing forever — suppression never resolves (evidence rule) |
| `killed` pipeline | not an observation | 9 of 105 pushes |
| Scheduled `main` audit | recommended to decouple observation cadence from commit cadence (overnight gap 7.5 h) | §5; a topology question for I4, noted here because every time floor above assumes an observation will occur |

None of these are to be encoded anywhere until I10 selects an architecture and I11 specifies it.

## 10. Rejected hypotheses and alternatives

- *A fixed 24 h or 48 h grace window is a safe conservative default.* **Rejected by measurement:** it
  never escalates the one durable incident in the window before the hand fix; on this repo's cadence it
  is excessive delay, not conservatism.
- *Observation count is a natural persistence measure.* **Falsified** by commit bursts (§9.1).
- *An open PR is the primary active-work signal.* **Falsified** for this repo (§3): PRs are opened
  minutes before merge; the remote branch is the real signal.
- *Immediate escalation is fine because findings are rare.* **Rejected:** ~0.5 noisy events/hour in the
  window, almost all cleared within 1.5 h.
- *Branch-level findings are a good proxy for `main`-level durability.* Not tested as a policy — the
  evidence rule already excludes branch findings from repository escalation; the data here (§7) shows
  why: 18 red pushes, median fix 0.35 h.

## 11. Bounded unknowns and data limits

- n = 3 `main` finding episodes, 1 durable CI incident, 11 PRs, 41 h, one developer, two parallel
  sessions. The ranges above are floors with margins, not estimates of a distribution. Re-run
  Appendix A/B after a week of post-protection history before I11 fixes numbers.
- The pre-protection `main` pushes (pipelines 1–29) mix "developer pushed straight to main" behaviour
  with the post-policy world; they were kept because they are the only source of a durable red.
- Woodpecker's list omits pipeline numbers that no longer exist (9 of 140 not retrievable); nothing in
  §7 depends on them.
- Whether a scheduled `main` audit is added is an I4 topology decision; every time floor above assumes
  *some* observation occurs after the floor, which today means "someone pushed to main".

## 12. Handoff

**Next: I3 — compare active-work detection and suppression strategies.** Inputs ready: the
PR-as-merge-ceremony finding (§3) which demotes "open PR" from primary to secondary signal; the remote-
branch overlap + merged-branch exclusion + idle-expiry candidates (§8–9); the `killed`-is-not-an-
observation rule; the generated-file collision specimen for I7 (§4); and the `branch: main`-on-PR live
proof for I4/I9 (§7). The replay/fetch/analysis scripts (Appendices A–C) are re-runnable against a longer
window.

---

## Appendix A — `main` audit replay (verbatim)

```python
"""Replay tools.quality_audit at every first-parent commit of origin/main
since the baseline ratchet was born (d63a5e5, QCP Task 3), using THAT
commit's own scanner code and THAT commit's own baseline.json - i.e. exactly
what a main-observing automation would have seen after each integration.

Read-only against the repository: each tree is `git archive`d into a temp
dir, audited there, and deleted. Writes <out>/series.json plus one
<out>/<sha7>.json per commit (the CLI's own --json-out payload).

usage: python3 replay_main_audit.py <worktree> <out_dir>
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WT = sys.argv[1]
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
RANGE = "d63a5e5~1..origin/main"

log = subprocess.check_output(
    ["git", "-C", WT, "log", "--first-parent", "--reverse", "--format=%H|%ct|%P|%s", RANGE], text=True
).splitlines()
print(f"{len(log)} first-parent commits in {RANGE}", file=sys.stderr)

series = []
t0 = time.time()
for line in log:
    sha, ct, parents, subject = line.split("|", 3)
    tmp = Path(tempfile.mkdtemp(prefix="replay-"))
    rec = {"sha": sha, "sha7": sha[:7], "ct": int(ct), "is_merge": len(parents.split()) > 1, "subject": subject[:90]}
    try:
        subprocess.run(f"git -C {WT} archive {sha} | tar -x -C {tmp}", shell=True, check=True)
        baseline = tmp / "tools" / "quality_audit" / "baseline.json"
        out = OUT / f"{sha[:7]}.json"
        env = dict(os.environ, PYTHONPATH=str(tmp))
        r = subprocess.run(
            [sys.executable, "-m", "tools.quality_audit", "--repo-root", str(tmp), "--baseline", str(baseline), "--json-out", str(out)],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=120,
        )
        rec["exit"] = r.returncode
        rec["stdout"] = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
        if out.exists():
            d = json.loads(out.read_text())
            new = set(d["new"])
            by_id = {f["finding_id"]: f for f in d["findings"]}
            rec["n_findings"] = len(d["findings"])
            rec["new"] = sorted(new)
            rec["resolved"] = d["resolved"]
            rec["new_error_high"] = sorted(i for i in new if by_id[i]["severity"] == "error" and by_id[i]["confidence"] == "high")
            rec["new_by_severity"] = {
                s: sum(1 for i in new if by_id[i]["severity"] == s) for s in ("error", "warning", "info")
            }
        else:
            rec["error"] = (r.stderr or "")[-400:]
    except Exception as e:  # noqa: BLE001 - measurement only
        rec["error"] = str(e)[-400:]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    series.append(rec)
    print(f"{rec['sha7']} exit={rec.get('exit')} new={len(rec.get('new', []))} resolved={len(rec.get('resolved', []))} {rec.get('error', '')[:60]}", file=sys.stderr)

(OUT / "series.json").write_text(json.dumps(series, indent=1))
print(f"done in {time.time() - t0:.0f}s -> {OUT / 'series.json'}", file=sys.stderr)
```

## Appendix B — Woodpecker history fetch (verbatim)

```python
"""Pull the complete Woodpecker pipeline history for this repo anonymously
(public repo; no token) including per-workflow/step states, for the I2
cadence measurement. Read-only GETs. Writes pipelines_all.json next to this
script."""
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://ci.webfoundry.dev/api/repos/1"
OUT = Path(__file__).with_name("pipelines_all.json")


def get(url):
    # The proxy in front of ci.webfoundry.dev returned 403 to urllib's default
    # "Python-urllib/3.x" agent while curl succeeded on the same URLs; a plain
    # UA string is all that differs.
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


pipelines = []
page = 1
while True:
    batch = get(f"{BASE}/pipelines?perPage=50&page={page}")
    if not batch:
        break
    pipelines.extend(batch)
    page += 1
    if page > 20:
        break

print(f"listed {len(pipelines)} pipelines over {page - 1} pages", file=sys.stderr)

detailed = []
for p in pipelines:
    try:
        d = get(f"{BASE}/pipelines/{p['number']}")
    except Exception as e:  # noqa: BLE001 - measurement only
        d = dict(p, _detail_error=str(e))
    detailed.append({
        "number": d.get("number"),
        "status": d.get("status"),
        "event": d.get("event"),
        "branch": d.get("branch"),
        "commit": d.get("commit"),
        "message": (d.get("message") or "").split("\n", 1)[0][:100],
        "created": d.get("created"),
        "started": d.get("started"),
        "finished": d.get("finished"),
        "changed_files": d.get("changed_files") or [],
        "workflows": [
            {
                "name": w.get("name"),
                "state": w.get("state"),
                "started": w.get("started"),
                "finished": w.get("finished"),
                "steps": [{"name": c.get("name"), "state": c.get("state")} for c in w.get("children", [])],
            }
            for w in d.get("workflows", [])
        ],
    })
    time.sleep(0.05)

OUT.write_text(json.dumps(detailed, indent=1))
print(f"wrote {OUT} ({len(detailed)} pipelines)", file=sys.stderr)
```

## Appendix C — policy simulation core (excerpt of the analysis script)

The full analysis script also computes §3, §4, §5 and §7 from `prs_commits.json` / `prs_files.json` /
`pipelines_all.json` / `replay/series.json`; only the policy replay is reproduced here because it is
the part whose semantics matter for I3/I10.

```python
policies = [("P0 immediate", 1, 0), ("P-k2", 2, 0), ("P-k3", 3, 0), ("P-k4", 4, 0),
            ("P-T2h", 1, 2), ("P-T6h", 1, 6), ("P-T12h", 1, 12), ("P-T24h", 1, 24),
            ("P-k2+T6h", 2, 6), ("P-k3+T12h", 3, 12)]
# obs_times[fid] = observation timestamps from series.json; episodes[fid] = first/last/cleared
for name, k, T in policies:
    false_early = still_open = 0
    delays = []
    for fid, e in episodes.items():
        obs = obs_times[fid.split(" (")[0]]
        esc = None
        for i, t in enumerate(obs, 1):                 # first observation with i >= k and elapsed >= T
            if i >= k and hrs(obs[0], t) >= T:
                esc = t
                break
        if esc is None and e["cleared"] is None and T > 0 and hrs(obs[0], NOW) >= T and len(obs) >= k:
            esc = obs[0] + (NOW - obs[0])              # would fire on the next observation after T
        if esc is None:
            continue
        if e["cleared"] is not None and esc < e["cleared"]:
            false_early += 1                           # escalated, then cleared without external action
        elif e["cleared"] is None:
            still_open += 1
            delays.append(hrs(obs[0], esc))
```

## Appendix D — commands

```
gh pr list --state all --limit 20 --json number,title,headRefName,baseRefName,state,isDraft,createdAt,mergedAt,closedAt,additions,deletions,changedFiles,commits
gh pr list --state all --limit 20 --json number,files
git log --first-parent --format='%H %ct %ci %s' origin/main
git log --format=%cd --date=short origin/main | sort | uniq -c
git log --format='%h %ct %ci %s' origin/main..origin/chore/realtime-dp-investigation
python3 replay_main_audit.py <worktree> <out>      # 164 s, 54 commits, all exit 0
python3 fetch_pipelines.py                          # 131 pipelines
python3 cadence_analysis.py                         # tables §3–§9
```
