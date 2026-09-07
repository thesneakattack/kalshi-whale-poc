# Quality Reporting Surfaces — I5 (surfaces and noise economics)

**Task:** I5 of `docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
**Branch / HEAD at start:** `chore/autonomous-quality-coordination-investigation` @ `a4521e7` (worktree
`.claude/worktrees/aqc-investigation`, base `origin/main` @ `8d1796b`). No open PRs.

Evidence classes: **[E1]** source/CI · **[E2]** git/PR history · **[E3]** deterministic experiment ·
**[E4]** live API/runtime · **[E5]** upstream docs · **[E6]** inference. Chrome DevTools: **NOT_NEEDED** — no
browser-facing presentation was chosen; the one existing UI consumer was read from source.
**Nothing was uploaded to GitHub; the SARIF prototype is offline only.**

---

## 1. Question and falsification criteria

**Question.** For each class of `QualityFinding`, which reporting surface carries it with the least noise
— and under what rule do intermediate coordinator states stay silent?

A surface/policy pairing survives only if, replayed against a week scaled from I2's measured series, it
emits **zero** human-visible events for the transitional and in-flight findings that dominate this
repository's history, while still surfacing a durable defect if one appears.

## 2. What is visible today (inventory) [E1/E4]

| Surface | On a branch push | On a PR | Identity carried | Who can see it |
|---|---|---|---|---|
| GitHub commit status `ci/woodpecker/push|pr/<pipeline>` (5 posted, 1 path-filtered) | ✔ | ✔ (required contexts gate merge) | pipeline only — no finding identity | anyone |
| Woodpecker pipeline log; `quality-architecture-audit`'s `print-report` step cats `build/quality-audit.json` on success **and** failure | ✔ | ✔ | full `findings/new/existing/resolved` JSON, per push | **anyone** (public repo; I2 fetched logs anonymously) |
| `python -m tools.quality_audit` stdout (`N new, M existing, K resolved` + one line per new error/warning) | ✔ | ✔ | `finding_id` | in the log above |
| PR annotations / check summaries | ✘ | ✘ | – | – |
| GitHub issues | ✘ (0 exist) | ✘ | – | – |
| Code scanning alerts / SARIF | ✘ (never uploaded) | ✘ | – | – |
| Runtime: `GET /api/quality/summary` → `frontend/src/js/system-health.js` (System Health panel; renders warning/error `summary` lines, storage and dropped-message rollups; "full report →" link) | n/a | n/a | runtime `finding_id`s | logged-in dashboard user |
| Runtime: `services/alerting` webhook alerts | n/a | n/a | its own alert ids — **does not consume `QualityFinding`** (grep: no reference) | webhook target |

So the entire CI reporting surface today is *one status per pipeline plus a public log*, with the
finding-level detail buried in the log. That is already a report-only surface (I4 candidate D); the
question is what, if anything, to add.

## 3. Finding-class matrix

| # | Class | Rules (I1 §3) | Has `path`+`line`? | Persistence pattern (I2) | Natural surface |
|---|---|---|---|---|---|
| 1 | Source-located deterministic error | `kalshi-boundary-*` (4), `resource-unclosed`, `background-unwired` | yes (`evidence.line`) | 0 on `main` in the ratchet's life; branch-only, fixed in 0.35 h median | CI gate (already) → **SARIF** if ever on `main` |
| 2 | Aggregate/architecture error | `router-unmounted`, `persistence-unisolated`, `frontend-route-missing`, `kalshi-contract-docs-*` | path only (module) | 0 on `main` | CI gate; SARIF possible at the module/def line (converter extension) |
| 3 | Heuristic warning | `config-unread` (115 live, all baselined) | `config/settings.yaml`, no line | 1 transitional episode (0.23 h) | CI log only; never an issue; SARIF only if the key line is located |
| 4 | Inventory / info | `api-usage`, `backend-route-unused`, `frontend-route-unknown` | mixed (5 with line) | 2 transitional/in-flight episodes — **all of this repo's `main` findings so far** | CI log only; **never** SARIF or issues (they are baseline-sync bookkeeping) |
| 5 | Runtime anomaly | `observability:*`, `storage-*`, `backup-overdue` | n/a | state, not defect | runtime UI + `services/alerting`; **never GitHub** |
| 6 | Persistent actionable defect | any class-1/2 finding that survives the I2 floor and I3 suppression on integrated `main` | as above | **0 observed** | GitHub issue (I4/I10 decide whether automation may open it) |

The matrix's most important cell is class 6's count: **zero**. Every `main` finding in 41 h of ratchet
history was class 3 or 4 bookkeeping. Any surface designed for class 6 must therefore be judged on what
it does *when class 6 is empty*, which is the noise economics below.

## 4. Offline SARIF prototype [E3]

Converter (Appendix A) maps a `QualityFinding` to one SARIF result per **occurrence** with
`ruleId = <check>/<rule>`, `level` from severity (`error→error`, `warning→warning`, `info→note`),
`message.text = summary`, `physicalLocation{artifactLocation.uri, region.startLine}`,
`properties{automation_key, finding_id}` (I1 §9 contract), `automationDetails.id = "quality-audit/main"`,
and **no `partialFingerprints`** so GitHub computes `primaryLocationLineHash` from source (docs, I1 §7).
Input: the real `origin/main` audit (190 findings) plus three boundary findings produced by running the
**real** `kalshi_boundary` scanner on a temp fixture (the clean tree emits none).

```
input findings: 190 real + 3 synthetic
SARIF results: 8; rules: 3; skipped (no source location): 185
skipped by check: {'config-usage': 115, 'api-usage-inventory': 26, 'frontend-api-contract': 44}
results by ruleId: {'frontend-api-contract/frontend-route-unknown': 5,
                    'kalshi-boundary/kalshi-boundary-sdk-import': 1,
                    'kalshi-boundary/kalshi-boundary-deprecated-read': 2}
GitHub structural requirements met: True
size bytes: 7809 (limit 10 MB gzipped; results cap 25,000/run)
schema validation: PASS against sarif-2.1.0.json      (jsonschema 4.10.3, official schemastore schema)
```

What the prototype shows:
- **Only 8 of 193 findings are SARIF material** on today's `main`, and 5 of those are class-4 info lines
  (`frontend-route-unknown`) that should be *excluded* from any upload — leaving the 3 synthetic
  boundary results, i.e. class 1 only. SARIF is a surface for a class that is currently empty on `main`.
- **Identity interaction with I1:** the two `deprecated-read` results sit on the same line
  (`services/rogue_alias.py:2`) with different `automation_key`s (`taker_side` vs `taker_outcome_side`).
  GitHub deduplicates by `primaryLocationLineHash` — identical line text → **one alert** for two defects,
  the surface-side twin of I1's raw-ID collision. Carrying `automation_key` in `properties` preserves the
  distinction for the coordinator; the alert list will still show one. Acceptable for a UI, which is why
  I1 §9 keeps the two keys separate.
- The `frontend-route-unknown` subject (`evidence.raw`) is a raw source slice (one spans a multi-line
  call) — the I1 contract's "normalised call expression" needs an explicit normalisation (first
  argument token, whitespace-collapsed) before it is used as a key. Recorded for I11.
- Level mapping matters: GitHub's PR check "fails if the code scanning results check finds any problems
  with a severity of `error`, `critical`, or `high`" [E5]; mapping class-1 errors to `level: error` would
  create a **second merge gate** duplicating the ratchet. If SARIF is ever adopted, either map to
  `warning` or set the repository's check-failure threshold deliberately — decision for I11.

## 5. Surface comparison per class

| Surface | Class 1 source-located | Class 2 aggregate | Class 3 heuristic | Class 4 inventory | Class 5 runtime | Class 6 durable |
|---|---|---|---|---|---|---|
| **CI status + public log** (exists) | gate + detail; no per-finding identity across runs | same | same | same | n/a | invisible unless someone reads logs |
| **SARIF / code scanning** | best fit: per-location, PR annotations only for alerts on changed lines [E5], fixed-on-disappearance, dismiss-in-all-branches [E5] | possible at module line; low value | only with key-line location; noise risk | **exclude** | never | alerts persist per branch; still needs a human to act |
| **PR annotation / check via Actions** | duplicates SARIF; needs `checks: write` | same | – | – | never | – |
| **PR/commit comments** | high noise (every observation) | same | – | – | never | – |
| **GitHub issue** | only after floor + suppression | same | never | never | never | **the** surface, and the only one that represents a work item |
| **Runtime UI + alerting** | n/a | n/a | n/a | n/a | exists; keep | n/a |

Two GitHub facts constrain the SARIF column [E5]: alerts appear on a PR only for new alerts on lines the
PR changed (so branch-local class-1 regressions would surface as annotations exactly where the ratchet
already fails the build), and **dismissing an alert dismisses it in all branches** — a human action with
repository-wide effect that a coordinator must treat as a claim, not a resolution (resolution stays with
the fresh `main` audit, evidence rule).

## 6. Noise economics — one week, scaled from I2 [E3]

Rates scaled ×4.14 from the 40.6 h window: 223 `main` observations, 12 transitional IDs (cleared ≤ 0.25 h),
4 in-flight IDs (fix on a branch for ~9 h), 74 red branch pushes. Durable findings: 0 observed;
sensitivity run at 1/week. Counts are *human-visible emissions* (issue open/close, comments, alert
open/close).

| policy | durable/week | issues opened | issues closed | comments | alert events | **emissions** |
|---|---|---|---|---|---|---|
| P1 issue on first `main` observation (raw `finding_id`) | 0 | 16 | 16 | 0 | 0 | **32** |
| P2 comment per observation | 0 | 0 | 0 | 40 | 0 | 40 |
| P3 issue per branch red push (forbidden by policy; scale only) | 0 | 74 | 74 | 0 | 0 | 148 |
| P4 I2 floor 6 h + I3 suppression + silent intermediate | 0 | 0 | 0 | 0 | 0 | **0** |
| P5 SARIF alerts only, class 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| P6 report-only | 0 | 0 | 0 | 0 | 0 | 0 |
| P1 | 1 | 17 | 16 | 0 | 0 | 33 |
| P2 | 1 | 0 | 0 | 95 | 0 | 95 |
| P4 | 1 | 1 | 0 | 0 | 0 | **1** |
| P5 | 1 | 0 | 0 | 0 | 2 | 2 |
| P6 | 1 | 0 | 0 | 0 | 0 | 0 |

Reading: on this repository's cadence, an issue-on-observation policy turns the tracker into a
**16-open/16-close-per-week telemetry stream for bookkeeping drift**, which is the outcome spec §12
names as the failure. The I2/I3 policy emits nothing until a durable defect exists, then exactly one
issue. SARIF's class-1 alerts cost nothing while `main` is clean and two alert events per durable
source-located defect. The scaling is linear and the base is 41 h; the *ratio* between policies is the
finding, not the absolute counts.

## 7. Silent-intermediate-state rule (what the evidence supports)

```
state transition                      external emission
─────────────────────────────────     ─────────────────────────────────────────────
branch observation                    none (branch CI status/log only — existing)
main OBSERVED (first seen)            none
main PERSISTENT (floor reached)       none
SUPPRESSED_PENDING_WORK (claim/path)  none — no comment on the PR, no label churn
suppression expiry                    none
ESCALATION_ELIGIBLE → escalated       ONE issue (class 6), body carries automation_key,
                                      first-seen, observation count, current locations
escalated item resolved by main audit ONE close comment with the resolving commit
escalated item reappears              reopen the SAME issue (I1 recurrence semantics), one comment
everything else (occurrence count,    edit the issue body in place; never a new comment
line moves, evidence changes)
```

Corollaries: per-observation comments (P2) are excluded outright; SARIF, if adopted, is confined to
class 1 with `level ≤ warning` and no `partialFingerprints`; runtime findings never cross into GitHub;
class 3/4 never leave the CI log; a human dismissal of a code-scanning alert is recorded as a claim.

## 8. Rejected on evidence

- *Issue per new `main` finding*: 32 emissions/week for zero durable defects (P1).
- *Comment per observation*: 40–95/week (P2).
- *Escalating branch findings*: 148/week (P3), and forbidden by the evidence rule anyway.
- *SARIF for class 3/4*: 5 real `frontend-route-unknown` info lines and 115 `config-unread` warnings
  would become alerts with no actionable content; excluded by class.
- *PR annotations/checks via Actions as a separate surface*: duplicates what SARIF gives natively and
  what the ratchet already does with a red required context.
- *Making the coordinator's own emissions a merge gate*: the ratchet is the gate; a second gate through
  SARIF `level: error` would double-count the same finding.

## 9. Bounded unknowns

- GitHub's exact "fixed" transition for code-scanning alerts is still not quoted by any page fetched
  (the PR-triage page says an alert "is closed and the annotation removed" when a PR fixes it; the
  resolving page says dismissal is global) — an offline dry run cannot settle it; only a real upload in a
  later, explicitly approved stage can. Until then class-1 SARIF stays a *candidate*, not a selection.
- The week simulation scales a 41 h window linearly; a single durable defect changes the absolute counts
  by ±1–2 and no ranking.
- Whether the coordinator's persisted observation series should itself be a visible artifact (a rolling
  digest) is a D-vs-C question for I10, not a reporting-class question.

## 10. Handoff

**Next: I6 — threat-model the proposed authority boundary.** Inputs ready: the public-log observation
(§2 — every finding is already world-readable, so "reporting" has no confidentiality axis, only a noise
axis); the SARIF level/gate interaction (§4); the dismiss-in-all-branches rule as a claim-not-resolution
case; and the emission table as the storm baseline for the "unstable identity → issue storm" threat.

---

## Appendix A — offline QCP→SARIF converter (verbatim)

```python
"""I5 offline prototype: convert QualityFinding records to a SARIF 2.1.0 log
the way GitHub code scanning expects it (ruleId, message.text, one
physicalLocation per occurrence with artifactLocation.uri + region.startLine,
level from severity, and the I1 automation_key carried in result.properties).
partialFingerprints are deliberately omitted so GitHub computes
primaryLocationLineHash from source (docs: it "attempts to populate" them).

Inputs: (a) the real origin/main audit JSON (I0) - only its source-located
findings are convertible; (b) synthetic kalshi-boundary findings produced by
running the REAL scanner on a temp fixture (same shapes as I1), because the
clean tree emits none. Output: sarif.json in the scratchpad + a structural
report. Optionally validates against the official 2.1.0 schema if jsonschema
is importable. NEVER uploads anything."""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
WT = Path(sys.argv[1])
sys.path.insert(0, str(WT))
from tools.quality_audit import kalshi_boundary  # noqa: E402  (real scanner)

LEVEL = {"error": "error", "warning": "warning", "info": "note"}
LOC_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+)$")


def automation_key(f: dict) -> str:
    """I1 §9 contract, computed here for the prototype only."""
    check, fid, ev = f["check"], f["finding_id"], f.get("evidence") or {}
    rule = fid.split(":", 1)[0]
    scope = f["scope"]
    m = LOC_RE.match(scope)
    if m:
        scope = m.group("path")
    subject = ""
    for k in ("imported", "module", "field", "raw"):
        if ev.get(k):
            subject = str(ev[k])
            break
    if rule == "kalshi-boundary-host" and ev.get("snippet"):
        subject = "kalshi-host"
    return f"{check}/{rule}|{scope}|{subject}"


def location(f: dict):
    ev = f.get("evidence") or {}
    path, line = ev.get("path"), ev.get("line")
    if path is None or line is None:
        m = LOC_RE.match(f["scope"])
        if m:
            path, line = m.group("path"), int(m.group("line"))
    if path is None or line is None:
        return None
    return {"physicalLocation": {"artifactLocation": {"uri": path, "uriBaseId": "%SRCROOT%"},
                                 "region": {"startLine": int(line)}}}


def to_sarif(findings: list[dict]) -> tuple[dict, list[dict]]:
    rules: dict[str, dict] = {}
    results = []
    skipped = []
    for f in findings:
        loc = location(f)
        if loc is None:
            skipped.append(f)
            continue
        rule_id = f"{f['check']}/{f['finding_id'].split(':', 1)[0]}"
        rules.setdefault(rule_id, {
            "id": rule_id,
            "shortDescription": {"text": f["check"]},
            "fullDescription": {"text": f.get("remediation") or f["summary"]},
            "defaultConfiguration": {"level": LEVEL[f["severity"]]},
            "properties": {"confidence": f["confidence"], "source": f["source"]},
        })
        results.append({
            "ruleId": rule_id,
            "level": LEVEL[f["severity"]],
            "message": {"text": f["summary"]},
            "locations": [loc],
            "properties": {"automation_key": automation_key(f), "finding_id": f["finding_id"]},
        })
    log = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "kalshi-whale-poc quality_audit", "version": "0",
                                 "informationUri": "https://github.com/thesneakattack/kalshi-whale-poc",
                                 "rules": list(rules.values())}},
            "automationDetails": {"id": "quality-audit/main"},
            "results": results,
        }],
    }
    return log, skipped


# (a) real findings at origin/main
real = json.loads((HERE / "qa-main.json").read_text())["findings"]

# (b) synthetic boundary findings from the real scanner on a temp fixture
tmp = Path(tempfile.mkdtemp(prefix="i5-"))
(tmp / "services").mkdir()
(tmp / "services" / "rogue_sdk.py").write_text("import kalshi_python_async as kpa\n")
(tmp / "services" / "rogue_alias.py").write_text('def side(t):\n    return t.get("taker_side") or t["taker_outcome_side"]\n')
synthetic = [f.to_dict() for f in kalshi_boundary.scan_kalshi_boundary(tmp)]

log, skipped = to_sarif(real + synthetic)
out = HERE / "sarif.json"
out.write_text(json.dumps(log, indent=1))
print(f"input findings: {len(real)} real + {len(synthetic)} synthetic")
print(f"SARIF results: {len(log['runs'][0]['results'])}; rules: {len(log['runs'][0]['tool']['driver']['rules'])}; skipped (no source location): {len(skipped)}")
from collections import Counter
print("skipped by check:", dict(Counter(f["check"] for f in skipped)))
print("results by ruleId:", dict(Counter(r["ruleId"] for r in log["runs"][0]["results"])))
for r in log["runs"][0]["results"]:
    l = r["locations"][0]["physicalLocation"]
    print(f"  {r['ruleId']:<45} {l['artifactLocation']['uri']}:{l['region']['startLine']}  key={r['properties']['automation_key']}")

# GitHub structural requirements (docs: ruleId recommended; message.text, locations, region.startLine required)
ok = all(r.get("ruleId") and r["message"].get("text") and r["locations"][0]["physicalLocation"]["region"].get("startLine") for r in log["runs"][0]["results"])
print("GitHub structural requirements met:", ok)
print("size bytes:", out.stat().st_size, "(limit 10 MB gzipped; results cap 25,000/run)")
try:
    import jsonschema
    schema = json.loads((HERE / "sarif-2.1.0.json").read_text())
    jsonschema.validate(log, schema)
    print("schema validation: PASS against sarif-2.1.0.json")
except ImportError:
    print("schema validation: skipped (jsonschema not importable on host)")
except Exception as e:  # noqa: BLE001
    print("schema validation: FAIL", str(e)[:300])
```

## Appendix B — one-week churn simulation (verbatim)

```python
"""I5 noise economics: replay one week of observations scaled from I2's
measured series and count what each reporting policy would have emitted.

Base rates (I2, 40.6 h window):
  main observations (pushes)            54        -> per week x4.14
  transitional main finding episodes     2 (3 IDs) -> lifetimes 0.10 h / 0.23 h
  in-flight main episode                 1 (8.8 h, fix on a branch)
  branch red pushes                     18         (median fix 0.35 h)
  durable finding episodes               0 observed -> sensitivity: 0 and 1 per week

Policies count *external emissions* (issue opened/closed, PR comment,
alert opened/closed) - the things a human sees. Deterministic: rates are
scaled, not sampled."""
SCALE = 168 / 40.6
main_obs = round(54 * SCALE)
transitional_ids = round(3 * SCALE)          # info/warning, cleared within 0.25 h by a baseline commit
inflight_ids = round(1 * SCALE)              # fix in flight on a branch for ~9 h
branch_red = round(18 * SCALE)
scenarios = {"durable=0/week": 0, "durable=1/week": 1}

print(f"scaled week: main observations {main_obs}, transitional IDs {transitional_ids}, in-flight IDs {inflight_ids}, branch red pushes {branch_red}")
print()
print("| policy | durable/week | issues opened | issues closed | PR/commit comments | alert open/close events | human-visible emissions |")
print("|---|---|---|---|---|---|---|")
for label, durable in scenarios.items():
    rows = []
    # P1 immediate issue per new main finding (RAW finding_id), close when resolved
    opened = transitional_ids + inflight_ids + durable
    closed = transitional_ids + inflight_ids          # cleared by baseline sync / merge
    rows.append(("P1 issue on first main observation", opened, closed, 0, 0))
    # P2 comment on every main observation of any new finding
    comments = main_obs * 0 + (transitional_ids * 2) + (inflight_ids * 4) + durable * main_obs // 4
    rows.append(("P2 comment per observation", 0, 0, comments, 0))
    # P3 branch findings escalated (forbidden by policy; shown for scale)
    rows.append(("P3 issue per branch red push", branch_red, branch_red, 0, 0))
    # P4 I2/I3 policy: 6 h floor + branch/PR suppression, silent intermediate state
    #    transitional: never reach floor; in-flight: suppressed by branch from +5.4 h, floor 6 h -> 0
    rows.append(("P4 floor 6h + suppression + silent intermediate", durable, 0, 0, 0))
    # P5 SARIF only (source-located rules): alerts open/close as line-numbered findings churn.
    #    None of this week's main findings are source-located (all inventory/config) -> 0 unless a boundary finding appears.
    rows.append(("P5 SARIF alerts only (source-located rules)", 0, 0, 0, durable * 2))
    # P6 report-only (D): nothing emitted; logs/status only
    rows.append(("P6 report-only", 0, 0, 0, 0))
    for name, o, c, cm, al in rows:
        print(f"| {name} | {durable} | {o} | {c} | {cm} | {al} | {o + c + cm + al} |")
    print("|  |  |  |  |  |  |  |")
```

## Appendix C — commands

```
python3 -c "import jsonschema; print(jsonschema.__version__)"       # 4.10.3
curl -sL https://json.schemastore.org/sarif-2.1.0.json -o sarif-2.1.0.json
python3 qcp_to_sarif.py <worktree>                                  # offline; writes sarif.json; never uploads
python3 week_churn.py
grep -n 'findings\|quality/summary' frontend/src/js/system-health.js
grep -rn 'QualityFinding\|runtime_findings' services/alerting/*.py  # empty: alerting is independent
```
