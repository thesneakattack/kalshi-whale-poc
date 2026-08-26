# Deterministic Remediation Candidate Inventory (I7)

**Task:** I7 of `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
**Branch / HEAD at start:** `chore/autonomous-quality-coordination-investigation` @ `c8fe088`
(worktree `.claude/worktrees/aqc-investigation`). Merged `origin/main` @ `fed3fee` (PR #13, the CI
fast-path fix, plus PR #12's realtime-data-plane investigation) forward into this branch first —
zero conflicts, no path overlap with the investigation's own files — so this task's own evidence and
any future push on this branch benefit from the just-shipped docs-only CI skip. HEAD after merge:
`dc32013`. No open PRs at task start.

Evidence classes: **[E1]** source/CI · **[E2]** git/PR history · **[E3]** deterministic experiment ·
**[E4]** live runtime · **[E5]** upstream docs · **[E6]** inference.

**Capabilities used:** none of GitNexus/dimensional-analysis were invoked. GitNexus was judged
NOT_NEEDED — every candidate's blast radius below was already directly measured five independent
ways (E2 field diff, E3/E3' repeat-run diff, E4 dirty-tree probe, E5 wrapper containment, E6
realistic-regen bound), which is stronger evidence than a structural graph query would add for a
single-file CLI tool. dimensional-analysis was NOT_NEEDED — no candidate below touches
probability/price/contract/bankroll/P&L math.

---

## 1. Method

Per `.claude/rules/autonomous-quality-coordination-evidence.md`'s "Remediation authority rule," a
candidate earns even provisional AUTO_DRAFT_PR status only by proving determinism, idempotence,
explicit path containment, and a bounded diff — not by looking plausible. Every proof below ran
inside a disposable tree extracted from `origin/main` via `git archive` + a fresh `git init`, never
inside this worktree or the primary checkout. The harness (`fixer_proof.py`, Appendix A) was written
mid-session, then **re-run fresh against current `origin/main` (`fed3fee`)** for this doc rather than
trusting numbers computed before the CI-fix merge — the merge changed nothing the tool reads, but
re-running costs one command and removes any doubt.

## 2. Inventory of everything in the repo that writes a repo-tracked file

Grepped every `tools/*.py` for `add_argument`/write-style flags [E1], plus `frontend/package.json`'s
`scripts` block and every `pyproject.toml`/`setup.cfg`/`.flake8`/eslint config for an autofix tool
[E1]. Five things exist; four are eliminated before any experiment is needed.

| Candidate | Writes | Network? | Verdict | Why |
|---|---|---|---|---|
| `tools/project_manifest.py --write` | `static/project-manifest.json` | no | **investigated below** | only real candidate that survives inspection |
| `tools/kalshi_docs_sync.py --write` | `docs/kalshi/upstream-manifest.json`, `docs/kalshi/README.md` | **yes** (fetches `llms.txt`) | REJECTED | network-dependent write path — a PR-triggered autonomous fixer must never make an outbound call before or during a commit-producing step, independent of whether the output is itself deterministic for a fixed input. Also **designed** to require a human action for a new page (its own docstring: "deliberately mirroring a new page stays a separate, deliberate human action") — the tool's author already drew this exact boundary |
| `tools/kalshi_docs_drift.py --check` | nothing repo-tracked (only an optional `--json-out` report file, not under version control) | yes | REJECTED (not applicable) | detector only, no write mode exists to test |
| `tools/quality_audit/__main__.py` | nothing (`--json-out` optional report only) | no | REJECTED (not applicable) | confirms by inspection what the evidence rule already assumes: the detector has no self-remediation path, so there is no way for an autonomous actor to "fix" a failed guard by touching the guard itself — structurally absent, not merely forbidden |
| Frontend bundle (`npm run build`, esbuild) | `static/js/dashboard.bundle.js` | no | REJECTED (not applicable) | **no committed target exists to remediate.** `.woodpecker/quality-frontend-build.yml`'s own header explains why: the bundle is gitignored with zero commits ever, so a "regenerate and diff against git" fixer has nothing to diff against — this was already tried once as a CI check and found to be a silent no-op (confirmed live 2026-08-24 by deliberately corrupting the built file; the git-diff check reported no difference regardless). The bundle is already rebuilt from source on every push by two independent CI steps (`quality-frontend-build`'s own `npm run build`, `quality-browser-e2e`'s `build-frontend`), and a broken build already fails those directly — a stronger signal than any autofixer PR would add. Re-grounded against PR #11 first: that PR was **docs-only** (the modularization *design*, not implementation), so there is no newer frontend build surface to re-test |
| Formatter/import codemod (black/isort/ruff/prettier `--fix`) | — | — | REJECTED (not applicable) | **none configured.** No `pyproject.toml`, `setup.cfg`, or `.flake8` exists at the repo root; `frontend/package.json`'s `lint` script runs plain `eslint src/js` with no `--fix`. Nothing to test — inventing one would violate this task's own instruction to "reject any merely hypothetical fixer" |

This leaves exactly **one** candidate worth the full proof matrix, which matches I4/I5's repeated
finding that this repo's deterministic-remediation surface is much smaller than its detection
surface.

## 3. `tools/project_manifest.py --write` — full proof [E3]

Harness: `fixer_proof.py` (Appendix A), run fresh against `origin/main` @ `fed3fee`.

```json
{
 "E1_check_at_main":               {"exit": 0, "tail": "project-manifest: up to date (no drift beyond 10%)"},
 "E2_paths_touched":                ["static/project-manifest.json"],
 "E2_fields_changed_vs_committed":  ["generated_at", "generated_from_head"],
 "E2_diff_lines": 4,
 "E2_check_after_write": 0,
 "E3_second_run_fields_changed":    ["generated_at", "generated_from_head"],
 "E3_second_run_git_diff_empty":    false,
 "E3_second_run_diff_lines": 4,
 "E3prime_semantic_idempotent":     true,
 "E4_dirty_tree_fields_changed":    ["generated_at", "generated_from_head", "lines.python"],
 "E4_dirty_tree_status":            ["services/risk_manager.py", "static/project-manifest.json"],
 "E5_real_fixer":     ["OK",    "1 path(s), 4 diff lines", ["static/project-manifest.json"]],
 "E5_rogue_fixer":    ["ABORT", "denylisted path(s) touched: ['tools/quality_audit/baseline.json']", [...]],
 "E5_tree_clean_after_rogue_abort": true,
 "E5_sprawl_fixer":   ["ABORT", "path(s) outside allowlist: ['docs/generated-extra.md']", [...]],
 "E5_dirty_precondition": ["ABORT", "dirty tree before run", []],
 "E6_regen_after_code_change": {"paths": ["static/project-manifest.json"], "diff_lines": 6,
                                  "fields": ["generated_at", "generated_from_head", "lines.python"]}
}
```

### What this proves

- **Path containment (E2, E5):** a real `--write` touches exactly one file, always
  `static/project-manifest.json`. A wrapper enforcing an allowlist of `{static/project-manifest.json}`
  and a denylist prefix-set covering `tools/quality_audit/`, `.woodpecker/`, `.github/`, `.claude/`,
  `services/`, `config/`, `main.py` etc. never has to actually catch a violation from the real tool —
  it's provably a single-file writer. The denylist/allowlist machinery was proven to work at all by
  feeding it two synthetic rogue fixers (below), not by trusting the real tool's good behavior.
- **Idempotence is semantic, not byte-level (E3/E3').** Running `--write` twice back-to-back does
  **not** produce an empty second `git diff` — `generated_at` is a wall-clock timestamp embedded in
  the manifest by design, so it differs on every invocation. The tool's own `--check` already builds
  in exactly this exclusion (`_CHURN_FIELDS`, referenced in `CLAUDE.md`'s baseline-ratchet section and
  confirmed by reading `tools/project_manifest.py` directly) — comparing after subtracting
  `{generated_at, generated_from_head}` **is** empty every time. **Conclusion for I7's own checklist
  item** ("for each candidate run twice and require an empty second diff"): the literal instruction as
  written is unsatisfiable for any generator with an embedded timestamp, and treating that as a
  disqualifying failure would be wrong — the correct bar, matching the tool's own documented contract,
  is semantic idempotence over non-churn fields. Recorded as a negative result on the plan's literal
  wording, not a plan violation: any I8 simulator or production wrapper must diff post-write output
  against pre-write output **excluding the tool's own declared churn fields**, not a raw `git diff
  --quiet`.
- **The tool has no dirty-tree guard of its own (E4).** Handed a tree with an unrelated uncommitted
  edit to `services/risk_manager.py`, `--write` ran anyway and silently baked the dirty file's line
  count into the manifest (`lines.python` changed). This is not a bug in the tool — nothing in its
  contract promises a clean-tree precondition, and CLAUDE.md's own remedy for legitimate drift is
  exactly "regenerate on the host and commit," which assumes a clean tree by convention rather than by
  enforcement. It **is** a requirement on any autonomous wrapper: clean-tree must be checked
  *before* invoking `--write`, by the wrapper, not inferred from the tool's behavior. Proven separately
  as `E5_dirty_precondition`, where the wrapper (not the tool) correctly aborted before running.
- **Wrapper containment holds under adversarial input (E5).** Two synthetic rogue fixers were run
  through the same wrapper that ran the real one: one that also edits
  `tools/quality_audit/baseline.json` (the exact self-remediation-of-a-guard case the evidence rule
  forbids) and one that writes an untracked file outside the allowlist. Both were caught and the tree
  was restored to clean (`git checkout -- . && git clean -fdq`) before returning `ABORT` — proven by
  `E5_tree_clean_after_rogue_abort: true`, not assumed.
- **Diff-size bound holds under a realistic trigger (E6).** A real code change (one line added to
  `services/risk_manager.py`, committed) followed by `--write` produces a 6-line diff — the actual
  historical event that triggers this fixer in production (a scanner/route/table count changing by
  enough to cross `--check`'s 10% tolerance, e.g. this session's own `489eebe`→`b436988` regeneration
  after adding 2 pipeline steps) stays far under any reasonable bound (harness used 50 lines; the real
  historical trigger this session hit was itself a handful of lines).

### Verdict

**`tools/project_manifest.py --write` → AUTO_DRAFT_PR-eligible**, with three conditions any production
wrapper must implement (not optional hardening — each was proven necessary by an experiment above,
not asserted):

1. refuse to run against a dirty tree (the tool won't refuse for you — E4);
2. compare idempotence/output-drift excluding `{generated_at, generated_from_head}` (raw diff is
   never empty by design — E3);
3. enforce the denylist *before* the allowlist and hard-abort-and-clean on either violation, exactly
   as the harness wrapper does (proven against two adversarial fixers, not just the real one — E5).

Scope stays draft-PR-only per the evidence rule's "no auto-merge assumption" — nothing here changes
that; determinism and idempotence are necessary but not sufficient for merge authority, which is an
explicit later decision (I10+) with its own review.

## 4. Everything else stays REJECTED / NOT_APPLICABLE

No further experiments were run for the four eliminated candidates in §2 — each was eliminated by a
structural fact (no write mode, no committed target, network dependency, or the tool simply doesn't
exist) that no experiment changes. Running an experiment anyway would have been exactly the
"invent a fixer merely because a scanner category exists" failure mode the evidence rule warns
against.

## 5. Answers to I7's acceptance criterion

"Every proposed fixer has executable proof of determinism/idempotence/path scope; no semantic
'engineering' fix is mislabeled mechanical."

- Exactly one fixer is proposed (`tools/project_manifest.py --write`); it has executable proof for
  all three properties above, with the idempotence property stated precisely (semantic, not raw-diff)
  rather than glossed over.
- No candidate above requires judgment about *what* to change — `--write` recomputes counts from
  static repo facts (route registrations, table names, workflow step names) with no interpretation
  step. This is the dividing line from every domain in the evidence rule's protected-domain list
  (trading, risk, calibration, strategy, CI policy, the coordinator's own guard) — none of which have
  a mechanical "recompute from source" operation at all, only judgment calls.

## Appendix A — `fixer_proof.py`

```python
"""I7 deterministic-remediation proof harness for tools.project_manifest.

Everything runs in a disposable tree extracted with `git archive origin/main`
(plus `git init` inside it so the tool sees a HEAD and so dirty-tree checks
are real). Nothing touches the worktree or the primary checkout.

Experiments:
  E1  --check at origin/main
  E2  --write once: which paths changed, which JSON fields changed
  E3  --write twice: is the second diff empty? (idempotence, raw)
  E3' same, ignoring the tool's own _CHURN_FIELDS (idempotence, semantic)
  E4  dirty tree: unrelated uncommitted edit, then --write -> what leaks into the manifest
  E5  wrapper: denylist/allowlist containment, tested with the real fixer and a rogue one
  E6  diff-size bound: lines changed by a legitimate regeneration
  E7  host-vs-container: what differs when git is unavailable (CI's python:3.13-slim has none)

usage: python3 fixer_proof.py <worktree> [container_result_json]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WT = Path(sys.argv[1])
ALLOW = {"static/project-manifest.json"}
DENY_PREFIXES = ("tools/quality_audit/", "tools/kalshi_census.py", ".woodpecker/", ".github/", ".claude/",
                 "tests/support/", "config/", "services/", "main.py", "mypy.ini", "requirements")
MAX_DIFF_LINES = 50


def sh(cmd, cwd, env=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


def fresh_tree() -> Path:
    t = Path(tempfile.mkdtemp(prefix="i7-"))
    sh(f"git -C {WT} archive origin/main | tar -x -C {t}", cwd=WT)
    sh("git init -q && git add -A && git -c user.email=i7@x -c user.name=i7 commit -qm base", cwd=t)
    return t


def status(t: Path) -> list[str]:
    return [l[3:] for l in sh("git status --porcelain", cwd=t).stdout.splitlines()]


def diff_lines(t: Path) -> int:
    return sum(1 for l in sh("git diff --unified=0", cwd=t).stdout.splitlines() if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))


def manifest(t: Path) -> dict:
    return json.loads((t / "static/project-manifest.json").read_text())


def changed_fields(a: dict, b: dict, prefix="") -> list[str]:
    out = []
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        if isinstance(va, dict) and isinstance(vb, dict):
            out += changed_fields(va, vb, f"{prefix}{k}.")
        elif va != vb:
            out.append(f"{prefix}{k}")
    return out


def write(t: Path, env=None):
    return sh([sys.executable, "-m", "tools.project_manifest", "--write", "static/project-manifest.json", "--repo-root", "."], cwd=t, env=env)


def check(t: Path):
    return sh([sys.executable, "-m", "tools.project_manifest", "--check", "static/project-manifest.json", "--repo-root", "."], cwd=t, check=False)


def run_fixer(t: Path, fixer, allow=ALLOW, deny=DENY_PREFIXES):
    """The wrapper semantics a coordinator would enforce (deny before allow)."""
    if status(t):
        return "ABORT", "dirty tree before run", []
    fixer(t)
    touched = status(t)
    denied = [p for p in touched if p.startswith(deny)]
    if denied:
        sh("git checkout -- . && git clean -fdq", cwd=t)
        return "ABORT", f"denylisted path(s) touched: {denied}", touched
    outside = [p for p in touched if p not in allow]
    if outside:
        sh("git checkout -- . && git clean -fdq", cwd=t)
        return "ABORT", f"path(s) outside allowlist: {outside}", touched
    n = diff_lines(t)
    if n > MAX_DIFF_LINES:
        sh("git checkout -- .", cwd=t)
        return "ABORT", f"diff {n} lines > bound {MAX_DIFF_LINES}", touched
    return "OK", f"{len(touched)} path(s), {n} diff lines", touched


R = {}

# E1
t = fresh_tree()
r = check(t)
R["E1_check_at_main"] = {"exit": r.returncode, "tail": r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()[-200:]}
committed = manifest(t)

# E2
write(t)
R["E2_paths_touched"] = status(t)
m1 = manifest(t)
R["E2_fields_changed_vs_committed"] = changed_fields(committed, m1)
R["E2_diff_lines"] = diff_lines(t)
R["E2_check_after_write"] = check(t).returncode

# E3 / E3': commit run 1 FIRST, then run again, then ask git whether run 2 changed anything
sh("git add -A && git -c user.email=i7@x -c user.name=i7 commit -qm run1 --allow-empty", cwd=t)
write(t)
m2 = manifest(t)
R["E3_second_run_fields_changed"] = changed_fields(m1, m2)
R["E3_second_run_git_diff_empty"] = not status(t)
R["E3_second_run_diff_lines"] = diff_lines(t)
churn = {"generated_at", "generated_from_head"}
R["E3prime_semantic_idempotent"] = not (set(R["E3_second_run_fields_changed"]) - churn)

# E4 dirty tree
t2 = fresh_tree()
(t2 / "services" / "risk_manager.py").write_text((t2 / "services" / "risk_manager.py").read_text() + "\n# uncommitted local edit\n")
before = manifest(t2)
write(t2)
after = manifest(t2)
R["E4_dirty_tree_fields_changed"] = changed_fields(before, after)
R["E4_dirty_tree_status"] = status(t2)

# E5 wrapper containment
t3 = fresh_tree()
R["E5_real_fixer"] = run_fixer(t3, write)
sh("git add -A && git -c user.email=i7@x -c user.name=i7 commit -qm ok --allow-empty", cwd=t3)


def rogue(t: Path):
    write(t)
    b = t / "tools/quality_audit/baseline.json"
    b.write_text(b.read_text().replace('"accepted_finding_ids": [', '"accepted_finding_ids": [\n    "kalshi-boundary-sdk-import:services/x.py:1",'))


R["E5_rogue_fixer"] = run_fixer(t3, rogue)
R["E5_tree_clean_after_rogue_abort"] = not status(t3)


def sprawl(t: Path):
    write(t)
    (t / "docs" / "generated-extra.md").write_text("x\n")


R["E5_sprawl_fixer"] = run_fixer(t3, sprawl)
t4 = fresh_tree()
(t4 / "README.md").write_text((t4 / "README.md").read_text() + "\n")
R["E5_dirty_precondition"] = run_fixer(t4, write)

# E6 diff bound: realistic regeneration after a code change committed
t5 = fresh_tree()
(t5 / "services" / "risk_manager.py").write_text((t5 / "services" / "risk_manager.py").read_text() + "\n# one more line\n")
sh("git add -A && git -c user.email=i7@x -c user.name=i7 commit -qm change", cwd=t5)
write(t5)
R["E6_regen_after_code_change"] = {"paths": status(t5), "diff_lines": diff_lines(t5), "fields": changed_fields(manifest(fresh_tree()), manifest(t5))}

# E7 host vs container
if len(sys.argv) > 2 and Path(sys.argv[2]).exists():
    cont = json.loads(Path(sys.argv[2]).read_text())
    R["E7_container_vs_host_fields"] = changed_fields(m1, cont)
    R["E7_container_generated_from_head"] = cont.get("generated_from_head")
else:
    R["E7_container_vs_host_fields"] = "(container result not supplied)"

print(json.dumps(R, indent=1, default=str))
```

E7 (host-vs-container git availability) was not run for this task — no container comparison result
was supplied, and it is not load-bearing for the §3 verdict above (all of E1–E6 already ran against a
real `git`-backed disposable tree, which is the environment any production wrapper would also use).
Recorded as not-run rather than fabricated.
