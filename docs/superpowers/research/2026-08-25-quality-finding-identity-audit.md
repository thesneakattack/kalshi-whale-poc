# Quality Finding Identity Audit — I1 (durable automation identity)

**Task:** I1 of `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
**Branch / HEAD at start:** `chore/autonomous-quality-coordination-investigation` @ `8403660`
(worktree `.claude/worktrees/aqc-investigation`, base `origin/main` @ `8d1796b`). Re-ground: no open PRs;
`origin/chore/realtime-dp-investigation` advanced to `c3e3d11` (still owns `tools/quality_audit/baseline.json`,
`tests/conftest.py`, `services/observability/**`; none of those are touched here).
**Date:** 2026-08-25/26.

Evidence classes (`.claude/rules/autonomous-quality-coordination-evidence.md`): **[E1]** source/test/CI ·
**[E2]** git/PR history · **[E3]** deterministic experiment · **[E4]** live runtime · **[E5]** upstream docs ·
**[E6]** inference.

---

## 1. Question and what would confirm or falsify it

**Question.** Which existing `QualityFinding.finding_id` values are safe to use as the durable key for
external state (issue dedup, suppression, recurrence tracking), and is a separate automation identity
required?

**Falsification criteria, per candidate key** (all tested by experiment, not argued):

| A key is *durable* only if… | Experiment |
|---|---|
| a harmless line inserted above the defect leaves it unchanged | E1 insert |
| two distinct same-rule defects in one scope get two distinct keys | E2 multi |
| removing the defect and re-introducing it at a new line yields the **same** key | E3 recur |
| changing the defect *in place* (same line) yields a **different** key | E5 content |
| a rename yields a new key (accepted — a rename is a new location by definition) | E4 rename |

The known-findings document (§4) hypothesised that some IDs are line-sensitive. This task measures which,
how, and what the alternatives cost.

## 2. Method

A mutation harness (Appendix A, ~330 lines, stdlib only) builds a fresh temporary repo per scanner —
fixture shapes copied from `tests/test_quality_audit.py` so they are known to trigger each rule — runs the
**real** scanner function from `tools/quality_audit/*`, applies each mutation to a clone, re-runs, and
compares four keys:

| Key | Definition | Stands in for |
|---|---|---|
| **RAW** | `finding_id` exactly as emitted today | strategy 1 (reuse `finding_id`) |
| **K1** | `check` + `scope` | "just drop the line number" |
| **K2** | `check` + scope-without-line + rule subject from `evidence` (`imported`/`module`/`field`/`raw`/`snippet`/`doc_path`) | strategy 2 (separate `automation_key`) |
| **K3** | `check` + path + `sha1(text of the reported line)` | strategy 3 (SARIF `primaryLocationLineHash`) |

Run: `PYTHONPATH=<worktree> python3 identity_mutation.py` on host Python 3.12 (no app deps needed —
only `pyyaml`, same as `python -m tools.quality_audit`). 52 rows, deterministic, ~1 s. [E3]

Tools used: host Python (harness); GitNexus on the worktree index (`impact QualityFinding` → 14 direct
consumers, matches grep; `impact run_audit` → exactly one consumer, `__main__.main`, so the CLI JSON is the
only place a `finding_id` leaves the process) [E3]; `git log -p` on `baseline.json` [E2]; GitHub docs
via WebFetch for SARIF/alert semantics [E5]. Context7, Chrome DevTools, dimensional-analysis:
NOT_NEEDED (no library, browser, or trading-math question arose). Blocked externals skipped.

## 3. Enumeration — every `QualityFinding` shape the repository can emit

### 3.1 CI scanners (`tools/quality_audit/__main__.py::_SCANNERS`, 9 scanners, 16 constructor sites) [E1]

| # | Scanner · rule prefix | check | sev/conf | `scope` | `evidence` keys | remed. | `finding_id` inputs | Identity class |
|---|---|---|---|---|---|---|---|---|
| 1 | routers · `router-unmounted` | router-registration | error/high | dotted module | path | yes | module | **semantic** (one per module by construction — `break` after first `APIRouter`) |
| 2 | background · `background-unwired` | background-wiring | error/high | module.func | path, line | yes | module, func | **semantic** |
| 3 | persistence · `persistence-unisolated` | persistence-isolation | error/high | module | path | yes | module | **semantic** |
| 4 | resources · `resource-unclosed` | resource-lifecycle | error/high | module.func | path, line | yes | module, func, **var name** | **semantic, symbol-sensitive** |
| 5 | config_usage · `config-unread` | config-usage | warning/medium | leaf path | path (constant) | yes | yaml leaf path | **semantic** (config key) |
| 6 | api_usage · `api-usage` | api-usage-inventory | info/high | receiver.method | call_sites[] | no | receiver, method | **aggregate/inventory** (N sites → 1 ID) |
| 7 | frontend_contract · `frontend-route-unknown` | frontend-api-contract | info/low | `file:line` | raw | no | file, **line** | **location-sensitive** |
| 8 | frontend_contract · `frontend-route-missing` | frontend-api-contract | error/high | `METHOD path` | call_sites[] | yes | method, path template | **semantic** (aggregate over sites) |
| 9 | frontend_contract · `backend-route-unused` | frontend-api-contract | info/medium | `METHOD path` | {} | no | method, path template | **semantic** |
| 10 | kalshi_contract_docs · `-missing` | kalshi-contract-docs | error/high | module.op | path | yes | module, op | **semantic** |
| 11 | kalshi_contract_docs · `-stale` | kalshi-contract-docs | warning/medium | module.key | path | yes | module, key | **semantic** |
| 12 | kalshi_contract_docs · `-missing-file` | kalshi-contract-docs | error/high | module.key | path, doc_path | yes | module, key, doc path | **semantic** |
| 13 | kalshi_boundary · `-sdk-import` | kalshi-boundary | error/high | file | path, line, imported | yes | file, **line** | **location-sensitive** |
| 14 | kalshi_boundary · `-host` | kalshi-boundary | error/high | file | path, line, snippet | yes | file, **line** | **location-sensitive** |
| 15 | kalshi_boundary · `-legacy-import` | kalshi-boundary | error/high | file | path, line, module | yes | file, **line** | **location-sensitive** |
| 16 | kalshi_boundary · `-deprecated-read` | kalshi-boundary | error/high | file | path, line, field | yes | file, **line** | **location-sensitive** (field *not* in ID → same-line collision, §5.2) |

### 3.2 Runtime producers (`source="runtime"`, composed by `GET /api/quality/summary`) [E1]

| Producer | `finding_id` | Identity class |
|---|---|---|
| observability `_tick_duration_finding` | `observability:tick-duration-exceeded:trading_loop` (constant) | **runtime observation**, stable |
| observability `_dropped_messages_findings` | `observability:ws-dropped-messages:{scope}` | runtime observation, stable per stream |
| observability `_stream_disconnected_findings` | `observability:stream-disconnected:{scope}` | runtime observation, stable per stream |
| observability `_repeated_rate_limit_hits_finding` | `observability:repeated-rate-limit-hits:kalshi_client` (constant) | runtime observation, stable |
| storage_health `growth` | `storage-growth:data/{name}` | runtime observation, stable per file |
| storage_health `integrity` | `storage-integrity:data/{name}` | runtime observation, stable per file |
| storage_health `backup_overdue` | `backup-overdue` (constant) | runtime observation, stable |

Runtime IDs are perfectly stable *and* perfectly uninformative as work-item keys: they name a condition,
not a defect, and are expected to appear and disappear with system state. They are candidates for
alerting windows (already owned by `services/alerting`), never for repository issues. Not tested further
here; classified **unsuitable for external defect state** by construction, not by measurement.

## 4. Experiment results (harness output, verbatim) [E3]

`findings a->b` = total findings before→after; `distinct ids` = distinct RAW IDs; per-key column = whether that
key's *set* was unchanged by the mutation (`same`) or not (`CHANGED`). E2 rows report distinct-key counts.

| scanner | rule | experiment | findings | distinct ids | RAW | K1 check+scope | K2 +subject | K3 line-hash | note |
|---|---|---|---|---|---|---|---|---|---|
| routers | router-unmounted | E1 insert | 1->1 | 1->1 | same | same | same | same |  |
| routers | router-unmounted | E3 recur | 1->1 | 1->1 | same | same | same | same | resolved=True |
| routers | router-unmounted | E4 rename | 1->1 | 1->1 | CHANGED | CHANGED | CHANGED | CHANGED |  |
| background | background-unwired | E1 insert | 1->1 | 1->1 | same | same | same | same |  |
| background | background-unwired | E2 multi | 2 findings | raw=2 | raw distinct=2 | K1 distinct=2 | K2 distinct=2 | K3 distinct=2 |  |
| background | background-unwired | E3 recur | 1->1 | 1->1 | same | same | same | same | resolved=True |
| background | background-unwired | E4 rename | 1->1 | 1->1 | CHANGED | CHANGED | CHANGED | CHANGED |  |
| persistence | persistence-unisolated | E1 insert | 1->1 | 1->1 | same | same | same | same |  |
| persistence | persistence-unisolated | E3 recur | 1->1 | 1->1 | same | same | same | same | resolved=True |
| persistence | persistence-unisolated | E4 rename | 1->1 | 1->1 | CHANGED | CHANGED | CHANGED | CHANGED |  |
| resources | resource-unclosed | E1 insert | 1->1 | 1->1 | same | same | same | same |  |
| resources | resource-unclosed | E2 multi | 2 findings | raw=2 | raw distinct=2 | K1 distinct=1 | K2 distinct=1 | K3 distinct=2 |  |
| resources | resource-unclosed | E3 recur | 1->1 | 1->1 | same | same | same | same | resolved=True |
| resources | resource-unclosed | E4 rename | 1->1 | 1->1 | CHANGED | same | same | CHANGED |  |
| resources | resource-unclosed | E5 content | 1->1 | 1->1 | same | same | same | CHANGED |  |
| config_usage | config-unread | E1 insert | 1->1 | 1->1 | same | same | same | same |  |
| config_usage | config-unread | E2 multi | 2 findings | raw=2 | raw distinct=2 | K1 distinct=2 | K2 distinct=2 | K3 distinct=2 |  |
| config_usage | config-unread | E3 recur | 1->1 | 1->1 | same | same | same | same | resolved=True |
| config_usage | config-unread | E4 rename | 1->1 | 1->1 | CHANGED | CHANGED | CHANGED | CHANGED |  |
| api_usage | api-usage | E1 insert | 1->1 | 1->1 | same | same | same | same |  |
| api_usage | api-usage | E2 multi | 1 findings | raw=1 | raw distinct=1 | K1 distinct=1 | K2 distinct=1 | K3 distinct=1 |  |
| api_usage | api-usage | E3 recur | 1->1 | 1->1 | same | same | same | same | resolved=True |
| api_usage | api-usage | E4 rename | 1->1 | 1->1 | CHANGED | CHANGED | CHANGED | CHANGED |  |
| frontend_contract | route-unknown/missing/unused | E1 insert | 3->3 | 3->3 | CHANGED | CHANGED | same | same |  |
| frontend_contract | route-unknown/missing/unused | E2 multi | 5 findings | raw=4 | raw distinct=4 | K1 distinct=4 | K2 distinct=3 | K3 distinct=4 |  |
| frontend_contract | route-unknown/missing/unused | E3 recur | 3->3 | 3->3 | CHANGED | CHANGED | same | same | resolved=False |
| frontend_contract | route-unknown/missing/unused | E4 rename | 3->3 | 3->3 | CHANGED | CHANGED | CHANGED | CHANGED |  |
| frontend_contract | route-unknown/missing/unused | E5 content | 3->3 | 3->3 | same | same | CHANGED | CHANGED |  |
| kalshi_contract_docs | kalshi-contract-docs-missing | E1 insert | 1->1 | 1->1 | same | same | same | same |  |
| kalshi_contract_docs | kalshi-contract-docs-missing | E2 multi | 2 findings | raw=2 | raw distinct=2 | K1 distinct=2 | K2 distinct=2 | K3 distinct=2 |  |
| kalshi_contract_docs | kalshi-contract-docs-missing | E3 recur | 1->1 | 1->1 | same | same | same | same | resolved=True |
| kalshi_contract_docs | kalshi-contract-docs-missing | E4 rename | 1->1 | 1->1 | CHANGED | CHANGED | CHANGED | CHANGED |  |
| kalshi_boundary | kalshi-boundary-sdk-import | E1 insert | 1->1 | 1->1 | CHANGED | same | same | same |  |
| kalshi_boundary | kalshi-boundary-sdk-import | E2 multi | 2 findings | raw=2 | raw distinct=2 | K1 distinct=1 | K2 distinct=1 | K3 distinct=2 |  |
| kalshi_boundary | kalshi-boundary-sdk-import | E3 recur | 1->1 | 1->1 | CHANGED | same | same | same | resolved=True |
| kalshi_boundary | kalshi-boundary-sdk-import | E4 rename | 1->1 | 1->1 | CHANGED | CHANGED | CHANGED | CHANGED |  |
| kalshi_boundary | kalshi-boundary-sdk-import | E5 content | 1->1 | 1->1 | same | same | same | CHANGED |  |
| kalshi_boundary | kalshi-boundary-host | E1 insert | 1->1 | 1->1 | CHANGED | same | same | same |  |
| kalshi_boundary | kalshi-boundary-host | E2 multi | 2 findings | raw=2 | raw distinct=2 | K1 distinct=1 | K2 distinct=2 | K3 distinct=2 |  |
| kalshi_boundary | kalshi-boundary-host | E3 recur | 1->1 | 1->1 | CHANGED | same | same | same | resolved=True |
| kalshi_boundary | kalshi-boundary-host | E4 rename | 1->1 | 1->1 | CHANGED | CHANGED | CHANGED | CHANGED |  |
| kalshi_boundary | kalshi-boundary-host | E5 content | 1->1 | 1->1 | same | same | CHANGED | CHANGED |  |
| kalshi_boundary | kalshi-boundary-legacy-import | E1 insert | 1->1 | 1->1 | CHANGED | same | same | same |  |
| kalshi_boundary | kalshi-boundary-legacy-import | E2 multi | 1 findings | raw=1 | raw distinct=1 | K1 distinct=1 | K2 distinct=1 | K3 distinct=1 |  |
| kalshi_boundary | kalshi-boundary-legacy-import | E3 recur | 1->1 | 1->1 | CHANGED | same | same | same | resolved=True |
| kalshi_boundary | kalshi-boundary-legacy-import | E4 rename | 1->1 | 1->1 | CHANGED | CHANGED | CHANGED | CHANGED |  |
| kalshi_boundary | kalshi-boundary-legacy-import | E5 content | 1->1 | 1->1 | same | same | CHANGED | CHANGED |  |
| kalshi_boundary | kalshi-boundary-deprecated-read | E1 insert | 1->1 | 1->1 | CHANGED | same | same | same |  |
| kalshi_boundary | kalshi-boundary-deprecated-read | E2 multi | 2 findings | raw=1 | raw distinct=1 | K1 distinct=1 | K2 distinct=2 | K3 distinct=1 |  |
| kalshi_boundary | kalshi-boundary-deprecated-read | E3 recur | 1->1 | 1->1 | CHANGED | same | same | same | resolved=True |
| kalshi_boundary | kalshi-boundary-deprecated-read | E4 rename | 1->1 | 1->1 | CHANGED | CHANGED | CHANGED | CHANGED |  |
| kalshi_boundary | kalshi-boundary-deprecated-read | E5 content | 1->1 | 1->1 | same | same | CHANGED | CHANGED |  |

Two rows need a reading note. `frontend_contract E3 resolved=False` is correct behaviour, not a harness
bug: the "resolved" mutation removed both frontend calls, so `route-unknown`/`route-missing` did resolve,
but `backend-route-unused:GET:/api/only-backend` (a different rule, same scanner) is *created* by that
removal. `kalshi-boundary-legacy-import E2` produced one finding for two banned imports — see §6.3.

## 5. Analysis by identity class

### 5.1 Semantic IDs — durable (rules 1, 2, 3, 5, 8, 9, 10, 11, 12)

E1 same · E3 same (`resolved=True` then the identical ID returns) · E4 CHANGED (rename = new identity,
acceptable and unavoidable for any key) · E2 distinct. **These `finding_id`s are safe as durable keys
as-is.** They are exactly the shapes the baseline ratchet's docstring intended ("a finding whose evidence
changes … without a real fix doesn't spuriously look new").

### 5.2 Location-sensitive IDs — not durable (rules 7, 13, 14, 15, 16)

Every one fails three of the five criteria, the same way:

- **E1 CHANGED** — three padding lines above the site produce a "new" ID and a "resolved" ID. Under the
  current ratchet that is only noise for rule 7 (info, baselined) and a *baseline edit* for 13–16 only if
  a boundary violation is ever deliberately baselined (none are today). Under an escalation automation it
  would be a spurious close + spurious open.
- **E3 CHANGED** — a defect fixed and later re-introduced at a different line is **not recognised as a
  recurrence**; it looks like a brand-new finding. An issue tracker keyed on RAW would open a second
  issue rather than reopen the first.
- **E5 same** — a *different* defect landing on the same line **inherits the old finding's identity**
  (`fetchJSON(url)` → `fetchJSON(otherUrl)`; `import kalshi_python_async as kpa` → `as sdk`). Any
  suppression/claim attached to the old finding silently transfers to the new one.
- **E2 same-line collision** — `kalshi-boundary-deprecated-read`: `t.get("taker_side") or
  t["taker_outcome_side"]` on one line yields **2 findings with 1 distinct `finding_id`** (the field is in
  `evidence`, not the ID). This fixture is `tests/test_quality_audit.py::
  test_boundary_deprecated_direction_read_outside_boundary_fails`'s own input; the test asserts
  `any(...)` and so never noticed. `compare_to_baseline` works on ID *sets*, so accepting one would accept
  both. `frontend-route-unknown` collides the same way for two calls on one line (5 findings → 4 IDs).

**Severity of the real exposure today:** small. At `origin/main` exactly 5 of 190 live IDs are
location-sensitive (all `frontend-route-unknown`, info/low, baselined) and the four `kalshi-boundary`
rules emit zero findings on a clean tree. The exposure is structural, not current — but any automation
that keys durable state on RAW inherits all four failure modes for these five constructors.

### 5.3 Aggregate / inventory (rule 6, and the aggregate half of rule 8)

`api-usage:{receiver}.{method}` collapses N call sites into one ID by design (E2: 2 sites → 1 finding,
`call_sites` evidence grows). Recurrence = the method being called again anywhere. Suitable as a stable
key for what it is — an inventory line, `severity=info`, never gates — but an automation must treat its
evidence, not its identity, as the changing thing. `frontend-route-missing` aggregates sites the same way
under a semantic key, which is the right shape for an actionable defect ("this path has no route").

### 5.4 Symbol-sensitive (rule 4)

`resource-unclosed:{module}:{func}:{var}` is semantic but keyed on a *local variable name*: renaming
`client` → `c` (E4) changes the ID although the leak is identical, while K1/K2 (which ignore the variable)
stay stable — but then collapse two distinct unclosed clients in one function (E2: K1/K2 distinct=1 vs
RAW=2). The variable is the correct subject; the harness's generic K2 simply did not extract it because it
lives only in the ID string, not in `evidence`. Contract implication (§9): the subject for this rule must
be the variable name **and** it belongs in `evidence`.

### 5.5 What K3 (SARIF-style line hash) buys and costs

K3 is stable under E1 and E3 (line text unchanged) and distinct for distinct lines — the properties GitHub
needs to dedupe alerts across uploads. But it **changes on any edit of the line** (E5, and E4 for a
variable rename), and it **collapses identical lines** (deprecated-read E2: two reads on one line → 1).
Good for a UI alert list; insufficient as the coordinator's work-item key.

## 6. Real-repository corroboration

### 6.1 Live census at `origin/main` [E3, from I0]
190 findings, 190 distinct IDs, 5 line-numbered — all `frontend-route-unknown`, all in `baseline.json`.

### 6.2 Has line-shift churn actually happened yet? No — it is latent [E2]
`git log -p -- tools/quality_audit/baseline.json` over its 10 commits (2026-08-24 02:08 → 2026-08-25
00:43): every added/removed ID was a genuine add/remove of a semantic ID (`backend-route-unused`,
`config-unread`, `api-usage`). The five `frontend-route-unknown:<file>:<line>` entries were added once
(`7081321`) and have never moved, i.e. no line above `history-core.js:380/392` or
`shared-utils.js:27/29/43` has been touched in that window. The first such edit will produce up to five
"new" + five "resolved" info lines and a baseline commit — harmless to the gate, but exactly the noise an
issue automation must not act on.

### 6.3 Side-finding: the C9 "any legacy import" invariant has a gap [E1/E3]
`kalshi_boundary.py`'s docstring: "ANY import of them — with or without a reintroduced file — is a hard
error". The detection it borrows, `tools/kalshi_census.py::_scan_legacy_wrapper`, only records an
`ImportFrom` whose imported name **equals the mapped class name** (`alias.name == expected`), and ignores
plain `import services.kalshi_client` entirely. The harness's E2 fixture `from services.kalshi_trade_ws
import X` therefore produced no finding. Not an identity question; recorded here because the experiment
surfaced it. Disposition under the investigation-to-guard rule: **permanent CI guard already exists but
is under-scoped** — fix is a two-line change plus a regression test in `tools/kalshi_census.py`
(`_scan_legacy_wrapper`), on its own small branch; deliberately not made in this research commit.

### 6.4 Side-finding: the scanners walk `docs/**/*.py` and flag fixture strings [E3]
Trying to commit the harness as `docs/superpowers/research/experiments/i1_identity_mutation.py` made
`python -m tools.quality_audit` exit 1 with three `error/high`
`kalshi-boundary-host:docs/…/i1_identity_mutation.py:393..395` findings — string literals in a *test
fixture table*, not vendor usage. Two lessons for later tasks: (a) `source.iter_python_files` has no
`docs/` exclusion, so research code with Kalshi-shaped strings must not be committed as `.py`; (b) this is
a concrete false-positive class that a naive escalation would file as three line-numbered "new errors".
The harness therefore lives in Appendix A (markdown is not scanned).

### 6.5 The live transitional specimen from I0 still stands [E3]
`api-usage:account.create_order` (new) / `api-usage:account.flatten_all` (resolved) on integrated `main`,
with the fix unmerged on the realtime branch — a semantic-ID *true* transition that is nonetheless not
actionable. Identity stability is necessary, not sufficient; I2/I3 own the suppression side.

## 7. Three identity strategies compared

| Criterion | 1 · reuse `finding_id` (RAW) | 2 · separate `automation_key` (K2-refined) | 3 · SARIF `partialFingerprints` (K3 / GitHub-computed) |
|---|---|---|---|
| E1 line insertion | fails for 5 constructors | stable | stable |
| E2 distinct same-rule defects | collides on same line (rules 7, 16) | distinct when the subject differs; **identical** defects (same rule+scope+subject) merge — handled as one work item with N occurrences (§9) | distinct unless the line text is identical |
| E3 recurrence at a new line | **not recognised** for 5 constructors | recognised | recognised (same text) |
| E4 rename | new ID (acceptable) | new key (acceptable) | new fingerprint; GitHub requires consistent paths [E5] |
| E5 defect changed in place | **inherits identity** for 5 constructors | new key (correct) | new fingerprint (correct) |
| Baseline migration cost | 0 | **0** (additive field) | 0 (export-only) |
| Implementation cost | 0 | additive field on `QualityFinding` + per-rule subject; no scanner ID changes | converter for source-located rules only; GitHub computes the hash if absent [E5] |
| External fit | none | coordinator/state key | GitHub code-scanning alerts only; not a coordinator key |
| Failure mode | issue storms / wrong-issue suppression on line shifts | a badly chosen subject collapses distinct defects (mitigated by per-rule subject table + occurrence list) | line edits reopen alerts; identical lines merge |

**GitHub facts used above [E5]** (docs.github.com, "SARIF support for code scanning" and "About code
scanning alerts", fetched 2026-08-26): code scanning "only uses the `primaryLocationLineHash`" from
`partialFingerprints` to match results across uploads; if absent "GitHub attempts to populate the
`partialFingerprints` field from the source files"; "the filepath has to be consistent across the runs";
alerts are tracked per branch, with the alert page reflecting the default branch and non-default-branch
state shown under "Affected branches"; limits — 10 MB gzipped per upload, 25,000 results per run (top
5,000 kept), 20 runs per file, 1,000 locations per result; `runAutomationDetails.id` = `category/run-id`
distinguishes multiple uploads per commit. **Not documented on those pages:** when an alert flips to
"fixed", whether a reappearing result reopens the old alert or creates a new one, and retention — bounded
unknowns for I5, not assumptions.

## 8. Migration cost, quantified [E1/E2]

| Change | `baseline.json` | tests | other consumers |
|---|---|---|---|
| Re-key only the 5 location-sensitive constructors' `finding_id` to a position-free form | 5 entries (`frontend-route-unknown:*`) + their `notes` line | ≤ 6 assertions; all six are `startswith("<rule>:<file>")` checks that survive if the `rule:file` prefix is kept, so likely **0** | none (`run_audit` has one consumer, `__main__.main`; `frontend/src/js/system-health.js` reads runtime findings only) |
| Re-key every `finding_id` (global scheme change) | all 190 entries + 4 wildcard `notes` keys | 19 literal pins in `tests/test_quality_audit.py` + 2 in `test_quality_models.py` | the contested `baseline.json` on the realtime branch would conflict on merge |
| Add a separate `automation_key` field | **0** | 0 (frozen dataclass gains a defaulted field; `to_dict` gains a key) | `/api/quality/summary` JSON gains a key — additive, tolerated by the current UI |

Conclusion: changing `finding_id` is cheap in absolute terms but not free of contention (the file is
owned by an active branch), and it buys nothing the additive field does not.

## 9. Recommended identity contract (specification only — not implemented here)

1. **Keep `finding_id` unchanged** as the baseline-ratchet key. The ratchet is a *gate*, its five
   location-sensitive IDs are info/low or currently empty, and re-keying them would touch a contested
   file for no coordinator benefit.
2. **Add `automation_key: str | None = None`** (name provisional) to `QualityFinding`, populated by each
   scanner from the same inputs it already has, of the form
   `<check>/<rule>|<scope-without-line>|<subject>` where:

   | rule | scope | subject |
   |---|---|---|
   | semantic rules (1, 2, 3, 5, 8, 9, 10, 11, 12) | as today | `""` (the scope is the subject) |
   | 4 `resource-unclosed` | `module.func` | variable name **+ class name** (both to `evidence`) |
   | 6 `api-usage` | `receiver.method` | `""` (inventory) |
   | 7 `frontend-route-unknown` | file | normalised raw call expression (`evidence.raw`) |
   | 13 `-sdk-import` | file | imported module name |
   | 14 `-host` | file | host token matched (not the whole snippet) |
   | 15 `-legacy-import` | file | banned module name |
   | 16 `-deprecated-read` | file | field name |

3. **Collision semantics:** two findings with the same `automation_key` are *the same work item with
   multiple occurrences*. The coordinator stores the occurrence list (path:line pairs, from `evidence`)
   on the item; it never derives a second key from an ordinal (ordinals shift when an identical defect is
   inserted above — E1 applied to ordinals).
4. **Resolution semantics:** a key is resolved only when absent from a fresh audit of integrated `main`
   (the evidence rule's standing requirement); occurrence-count changes are updates, not resolutions.
5. **Recurrence semantics:** a key that was resolved and reappears is a *reopen* of the same item,
   regardless of line (E3). The old item's history (claims, suppressions, prior resolution) stays attached
   — which is exactly what RAW cannot provide for rules 7 and 13–16.
6. **Rename semantics:** a rename is a new key (E4). Optional, later: if a key disappears and a key with
   identical `rule|subject` appears in the same run under a different scope, tag the new item "moved
   from …" — a heuristic for I3/I8 to evaluate, not part of the base contract.
7. **SARIF (I5) stays per-location:** export source-located rules as one result per occurrence with
   `ruleId = <check>/<rule>`, let GitHub compute `partialFingerprints`, and carry `automation_key` in
   `result.properties` so an alert can be cross-referenced to the coordinator item. The two keys serve
   different consumers and should not be unified.
8. **Two scanner bug fixes to schedule (not in this commit):** put the field in the
   `kalshi-boundary-deprecated-read` ID and the call expression (or column) in `frontend-route-unknown`
   so same-line defects stop sharing a `finding_id`; and the `_scan_legacy_wrapper` name filter (§6.3).
   All are production-identity changes and belong after I10 or in a stand-alone fix branch with tests.

## 10. Rejected hypotheses and alternatives

- *"`finding_id` is uniformly usable as a durable key."* **Falsified** for constructors 7, 13, 14, 15, 16
  by E1/E3/E5, and by the E2 same-line collision.
- *"Line-sensitive IDs have already been churning the baseline."* **Not supported** by `git log -p` —
  latent so far (§6.2). The risk is structural, not historical.
- *K1 (drop the line number, no subject).* **Rejected**: collapses two SDK imports or two unclosed
  clients in one scope into one key (E2 distinct=1).
- *SARIF line-hash as the coordinator key.* **Rejected** for that role: any edit to the line — including
  an alias rename — reopens it, and identical lines merge; it remains the right fingerprint for GitHub's
  own alert list.
- *Re-keying `finding_id` now.* **Deferred**: zero coordinator benefit over the additive field, and the
  baseline file is owned by an active branch.
- *Committing the harness under `docs/` as `.py`.* **Rejected** by experiment (§6.4).

## 11. Bounded unknowns

- GitHub alert fixed/reopen/retention semantics per branch — pages fetched are silent; **I5** verifies
  against the code-scanning API reference or by an offline SARIF dry run.
- Whether a Woodpecker pipeline skipped by a `path` condition can be made to post a status the branch
  protection would accept — Woodpecker's workflow-syntax page documents `include/exclude/ignore_message/
  on_empty` and that PR path filters use *all* changed files of the PR, but says nothing about status for
  skipped workflows; `docs/woodpecker-ci.md` records "posts no status at all" from live observation.
  Raised by the user during this task ("a full battery of tests is being run where certain tests aren't
  needed, such as an e2e browser test when only merging research docs and planning") — it is the coupling
  between required contexts and skip-means-silent that forces the battery. **I4/I5 item**, not changed
  here; recorded in memory as `ci-battery-should-match-change-class`.
- The realtime branch's `baseline.json` change will merge before or after this branch; either order is
  safe because this task changes no IDs.

## 12. Handoff

**Next task: I2 — measure repository concurrency and derive persistence candidates.** Inputs ready: the
PR create→merge durations already sampled in I0 (11 PRs, 3–646 min), the Woodpecker pipeline table with
`killed`/`failure` transitions, and the `baseline.json` churn log above as the first "transitional
finding" series. I2 must not hard-code any window; it derives candidate ranges from this repo's cadence.

---

## Appendix A — mutation harness (verbatim; run from the worktree root with `PYTHONPATH=.`)

Kept here rather than as a `.py` in the tree because its fixture strings trip the boundary ratchet (§6.4).
Copy to a scratch path, then `PYTHONPATH=<worktree> python3 <path>`.

```python
"""I1 identity-stability mutation harness (autonomous quality coordination
investigation). Drives every registered tools.quality_audit scanner against
small synthetic repos and records how each finding_id - and three candidate
identity keys - behave under five mutations:

  E1 insert   harmless lines inserted ABOVE the offending site
  E2 multi    two same-rule defects in one scope (file / function / module)
  E3 recur    defect removed (resolved), then re-introduced at a new line
  E4 rename   file/module or symbol renamed, defect otherwise identical
  E5 content  the offending statement changed in place, same line kept

Candidate keys scored per finding:
  RAW  = finding_id as emitted today
  K1   = check + scope                       (drop location, no subject)
  K2   = check + scope + rule subject        (semantic subject from evidence)
  K3   = check + path + sha1(line text)      (SARIF-style primaryLocationLineHash)

Read-only against the repo: every fixture lives in a fresh temp dir. Run from
the investigation worktree root with PYTHONPATH set to it. Prints a markdown
report to stdout; exit 0 always (this measures, it does not gate).
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

from tools.quality_audit import (
    api_usage,
    background,
    config_usage,
    frontend_contract,
    kalshi_boundary,
    kalshi_contract_docs,
    persistence,
    resources,
    routers,
)

# The persistence scanner cross-checks the real registry; for synthetic
# modules an empty registry is the honest fixture (same as its unit test).
persistence.PERSISTENCE_MODULE_PATHS = ()

PAD = "# padding line inserted above the site\n"
CLIENT_STUB = "class KalshiClient:\n    async def close(self):\n        pass\n"
SCHEDULER = (
    "import task_supervisor\n\n\n"
    "def _maybe_do_work(cfg):\n"
    "    state = {}\n"
    '    state["task"] = task_supervisor.supervise(lambda: None)\n'
)
ROUTES_PY = (
    "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n"
    '@router.get("/api/only-backend")\nasync def only_backend():\n    return {}\n'
)


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def prepend(root: Path, rel: str, n: int = 3) -> None:
    p = root / rel
    p.write_text(PAD * n + p.read_text())


def run(scanner, root: Path) -> dict[str, object]:
    out: dict[str, object] = {}
    for f in scanner(root):
        out.setdefault(f.finding_id, []).append(f)
    return out


# --- candidate keys ---------------------------------------------------------

def subject(f) -> str:
    ev = f.evidence or {}
    for key in ("imported", "module", "field", "raw", "snippet", "doc_path"):
        if key in ev and ev[key] is not None:
            return f"{key}={ev[key]}"
    return ""


def k1(f) -> str:
    return f"{f.check}|{f.scope}"


def k2(f) -> str:
    # For location-shaped scopes ("file:line") strip the line so the key is
    # position-free; semantic scopes pass through unchanged.
    scope = f.scope
    if ":" in scope and scope.rsplit(":", 1)[1].isdigit():
        scope = scope.rsplit(":", 1)[0]
    return f"{f.check}|{scope}|{subject(f)}"


def k3(f, root: Path) -> str:
    ev = f.evidence or {}
    path = ev.get("path")
    line = ev.get("line")
    if (path is None or line is None) and ":" in f.scope and f.scope.rsplit(":", 1)[1].isdigit():
        path, line = f.scope.rsplit(":", 1)
    if path is None or line is None or path == "config/settings.yaml":
        return f"{f.check}|{f.scope}|(no source line)"
    try:
        text = (root / path).read_text().splitlines()[int(line) - 1].strip()
    except Exception:  # noqa: BLE001 - measurement only
        text = "?"
    return f"{f.check}|{path}|{hashlib.sha1(text.encode()).hexdigest()[:10]}"


def keyset(findings: dict, root: Path, fn):
    return {fn(f, root) if fn is k3 else fn(f) for fs in findings.values() for f in fs}


def total(findings: dict) -> int:
    return sum(len(v) for v in findings.values())


# --- experiments ------------------------------------------------------------

ROWS: list[tuple] = []


def record(scanner_name, rule, exp, before: dict, after: dict, root_before: Path, root_after: Path, note=""):
    def stable(fn):
        b = keyset(before, root_before, fn)
        a = keyset(after, root_after, fn)
        return "same" if a == b else "CHANGED"
    ROWS.append((
        scanner_name, rule, exp,
        f"{total(before)}->{total(after)}",
        f"{len(before)}->{len(after)}",
        stable(lambda f: f.finding_id), stable(k1), stable(k2), stable(k3), note,
    ))


def collision(scanner_name, rule, exp, findings: dict, root: Path, note=""):
    n = total(findings)
    ROWS.append((
        scanner_name, rule, exp, f"{n} findings",
        f"raw={len(findings)}",
        f"raw distinct={len(findings)}",
        f"K1 distinct={len(keyset(findings, root, k1))}",
        f"K2 distinct={len(keyset(findings, root, k2))}",
        f"K3 distinct={len(keyset(findings, root, k3))}",
        note,
    ))


def fresh() -> Path:
    return Path(tempfile.mkdtemp(prefix="i1-"))


def clone(root: Path) -> Path:
    dst = fresh()
    shutil.rmtree(dst)
    shutil.copytree(root, dst)
    return dst


def experiment(name, scanner, rule, build, site, mutate_multi=None, mutate_recur=None, mutate_rename=None, mutate_content=None):
    base = fresh()
    build(base)
    before = run(scanner, base)
    assert before, f"{name}: fixture produced no finding"

    # E1 insert
    ins = clone(base)
    prepend(ins, site)
    record(name, rule, "E1 insert", before, run(scanner, ins), base, ins)

    # E2 multi
    if mutate_multi:
        multi = clone(base)
        mutate_multi(multi)
        collision(name, rule, "E2 multi", run(scanner, multi), multi)

    # E3 recur: remove, then re-add at a shifted line
    if mutate_recur:
        rec = clone(base)
        mutate_recur(rec, remove=True)
        resolved = run(scanner, rec)
        mutate_recur(rec, remove=False)
        prepend(rec, site, n=5)
        recurred = run(scanner, rec)
        note = f"resolved={total(resolved)==0}"
        record(name, rule, "E3 recur", before, recurred, base, rec, note)

    # E4 rename
    if mutate_rename:
        ren = clone(base)
        mutate_rename(ren)
        record(name, rule, "E4 rename", before, run(scanner, ren), base, ren)

    # E5 content
    if mutate_content:
        con = clone(base)
        mutate_content(con)
        record(name, rule, "E5 content", before, run(scanner, con), base, con)


# 1. routers -----------------------------------------------------------------
def build_routers(r):
    write(r, "services/foo/routes.py", "from fastapi import APIRouter\n\nrouter = APIRouter()\n")
    write(r, "main.py", "app = None\n")


def rename_routers(r):
    shutil.move(r / "services/foo", r / "services/bar")


def recur_routers(r, remove):
    p = r / "services/foo/routes.py"
    if remove:
        p.write_text("x = 1\n")
    else:
        p.write_text("from fastapi import APIRouter\n\nrouter = APIRouter()\n")


experiment("routers", routers.scan_router_registration, "router-unmounted", build_routers,
           "services/foo/routes.py", mutate_recur=recur_routers, mutate_rename=rename_routers)

# 2. background --------------------------------------------------------------
def build_bg(r):
    write(r, "services/foo/scheduler.py", SCHEDULER)
    write(r, "main.py", "x = 1\n")


def multi_bg(r):
    p = r / "services/foo/scheduler.py"
    p.write_text(p.read_text() + "\n\n" + SCHEDULER.replace("_maybe_do_work", "_maybe_do_other").split("\n\n\n", 1)[1])


def recur_bg(r, remove):
    p = r / "services/foo/scheduler.py"
    p.write_text("x = 1\n" if remove else SCHEDULER)


def rename_bg(r):
    p = r / "services/foo/scheduler.py"
    p.write_text(p.read_text().replace("_maybe_do_work", "_maybe_do_work_v2"))


experiment("background", background.scan_background_wiring, "background-unwired", build_bg,
           "services/foo/scheduler.py", multi_bg, recur_bg, rename_bg)

# 3. persistence -------------------------------------------------------------
def build_persist(r):
    write(r, "services/new_store.py", 'DB_PATH = "new_store.db"\n')


def rename_persist(r):
    shutil.move(r / "services/new_store.py", r / "services/renamed_store.py")


def recur_persist(r, remove):
    (r / "services/new_store.py").write_text("x = 1\n" if remove else 'DB_PATH = "new_store.db"\n')


experiment("persistence", persistence.scan_persistence_isolation, "persistence-unisolated", build_persist,
           "services/new_store.py", mutate_recur=recur_persist, mutate_rename=rename_persist)

# 4. resources ---------------------------------------------------------------
LEAK = CLIENT_STUB + '\n\nasync def leak():\n    client = KalshiClient()\n    return await client.get_market("X")\n'


def build_res(r):
    write(r, "services/leaky.py", LEAK)


def multi_res(r):
    write(r, "services/leaky.py", CLIENT_STUB + '\n\nasync def leak():\n    client = KalshiClient()\n    client2 = KalshiClient()\n    return await client.get_market("X")\n')


def recur_res(r, remove):
    write(r, "services/leaky.py", CLIENT_STUB + "\n\nasync def leak():\n    return 1\n" if remove else LEAK)


def rename_res(r):
    write(r, "services/leaky.py", LEAK.replace("client", "c"))


def content_res(r):
    write(r, "services/leaky.py", LEAK.replace("KalshiClient()", "KalshiClient(base_url='x')"))


experiment("resources", resources.scan_resource_lifecycle, "resource-unclosed", build_res,
           "services/leaky.py", multi_res, recur_res, rename_res, content_res)

# 5. config_usage ------------------------------------------------------------
def build_cfg(r):
    write(r, "config/settings.yaml", "strategy:\n  entry_threshold: 0.6\n")
    write(r, "app.py", "x = 1\n")


def multi_cfg(r):
    write(r, "config/settings.yaml", "strategy:\n  entry_threshold: 0.6\n  exit_threshold: 0.4\n")


def recur_cfg(r, remove):
    write(r, "app.py", 'def f(cfg):\n    return cfg["strategy"]["entry_threshold"]\n' if remove else "x = 1\n")


def rename_cfg(r):
    write(r, "config/settings.yaml", "strategy:\n  entry_threshold_v2: 0.6\n")


experiment("config_usage", config_usage.scan_config_usage, "config-unread", build_cfg,
           "config/settings.yaml", multi_cfg, recur_cfg, rename_cfg)

# 6. api_usage ---------------------------------------------------------------
def build_api(r):
    write(r, "app.py", 'async def fetch():\n    await client.get_market("X")\n')


def multi_api(r):
    write(r, "app.py", 'async def fetch():\n    await client.get_market("X")\n    await client.get_market("Y")\n')


def recur_api(r, remove):
    write(r, "app.py", "x = 1\n" if remove else 'async def fetch():\n    await client.get_market("X")\n')


def rename_api(r):
    write(r, "app.py", 'async def fetch():\n    await client.get_market_v2("X")\n')


experiment("api_usage", api_usage.scan_api_usage, "api-usage", build_api,
           "app.py", multi_api, recur_api, rename_api)

# 7. frontend_contract -------------------------------------------------------
JS = "const url = '/x';\nfetchJSON(url);\nfetchJSON('/api/does-not-exist');\n"


def build_fe(r):
    write(r, "frontend/src/js/app.js", JS)
    write(r, "services/a/routes.py", ROUTES_PY)


def multi_fe(r):
    # two unresolvable calls on DIFFERENT lines, then two on the SAME line
    write(r, "frontend/src/js/app.js", "const a = '/x'; const b = '/y';\nfetchJSON(a);\nfetchJSON(b);\nfetchJSON(a); fetchJSON(b);\n")


def recur_fe(r, remove):
    write(r, "frontend/src/js/app.js", "const x = 1;\n" if remove else JS)


def rename_fe(r):
    shutil.move(r / "frontend/src/js/app.js", r / "frontend/src/js/panel.js")


def content_fe(r):
    write(r, "frontend/src/js/app.js", JS.replace("fetchJSON(url)", "fetchJSON(otherUrl)"))


experiment("frontend_contract", frontend_contract.scan_frontend_contract, "route-unknown/missing/unused", build_fe,
           "frontend/src/js/app.js", multi_fe, recur_fe, rename_fe, content_fe)

# 8. kalshi_contract_docs ----------------------------------------------------
KPUB = "CONTRACT_DOCS = {}\n\n\ndef get_markets():\n    pass\n"


def build_kdocs(r):
    write(r, "services/kalshi/public.py", KPUB)


def multi_kdocs(r):
    write(r, "services/kalshi/public.py", KPUB + "\n\ndef get_market():\n    pass\n")


def recur_kdocs(r, remove):
    write(r, "services/kalshi/public.py", "CONTRACT_DOCS = {}\n" if remove else KPUB)


def rename_kdocs(r):
    write(r, "services/kalshi/public.py", KPUB.replace("get_markets", "list_markets"))


experiment("kalshi_contract_docs", kalshi_contract_docs.scan_kalshi_contract_docs, "kalshi-contract-docs-missing",
           build_kdocs, "services/kalshi/public.py", multi_kdocs, recur_kdocs, rename_kdocs)

# 9. kalshi_boundary (four rules, one fixture each) ---------------------------
BOUNDARY = {
    "sdk-import": ("services/rogue_sdk.py", "import kalshi_python_async as kpa\n",
                   "import kalshi_python_async as sdk\n",
                   "import kalshi_python_async as kpa\nimport kalshi_python_async as kpa2\n"),
    "host": ("services/rogue_host.py", 'URL = "https://external-api.kalshi.com/trade-api/v2"\n',
             'URL = "https://api.elections.kalshi.com/trade-api/v2"\n',
             'URL = "https://external-api.kalshi.com/trade-api/v2"\nURL2 = "https://api.elections.kalshi.com/x"\n'),
    "legacy-import": ("services/new_consumer.py", "from services.kalshi_client import KalshiClient\n",
                      "from services.kalshi_account_client import KalshiAccountClient\n",
                      "from services.kalshi_client import KalshiClient\nfrom services.kalshi_trade_ws import X\n"),
    "deprecated-read": ("services/rogue_alias.py", 'def side(t):\n    return t.get("taker_side")\n',
                        'def side(t):\n    return t.get("taker_book_side")\n',
                        'def side(t):\n    return t.get("taker_side") or t["taker_outcome_side"]\n'),
}

for rule, (path, good, content_variant, multi_variant) in BOUNDARY.items():
    def build_b(r, path=path, good=good):
        write(r, path, good)

    def multi_b(r, path=path, multi_variant=multi_variant):
        write(r, path, multi_variant)

    def recur_b(r, remove, path=path, good=good):
        write(r, path, "x = 1\n" if remove else good)

    def rename_b(r, path=path):
        shutil.move(r / path, r / path.replace(".py", "_moved.py"))

    def content_b(r, path=path, content_variant=content_variant):
        write(r, path, content_variant)

    experiment("kalshi_boundary", kalshi_boundary.scan_kalshi_boundary, f"kalshi-boundary-{rule}", build_b,
               path, multi_b, recur_b, rename_b, content_b)

# --- report -----------------------------------------------------------------
print("| scanner | rule | experiment | findings | distinct ids | RAW | K1 check+scope | K2 +subject | K3 line-hash | note |")
print("|---|---|---|---|---|---|---|---|---|---|")
for row in ROWS:
    print("| " + " | ".join(str(c) for c in row) + " |")
```

## Appendix B — commands used

```
# re-ground
git fetch origin --prune; git worktree list; gh pr list --state open; git branch -r
# identity inputs
grep -n 'finding_id=' tools/quality_audit/*.py
git log -p --format='COMMIT %h %s' -- tools/quality_audit/baseline.json | grep -E '^COMMIT|^[+-]\s+"'
# experiment
PYTHONPATH=<worktree> python3 identity_mutation.py
# placement test (§6.4) - exit 1, three kalshi-boundary-host errors
python3 -m tools.quality_audit --repo-root . --baseline tools/quality_audit/baseline.json
# structure
npx gitnexus@latest impact QualityFinding --summary-only --repo <worktree>
npx gitnexus@latest impact run_audit --summary-only --repo <worktree>
```
