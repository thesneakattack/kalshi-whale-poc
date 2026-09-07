# Consolidation — AI-assisted engineering principles implementation plan (2026-09-07)

Reconciles the stage-3 artifact
(`2026-09-07-ai-assisted-engineering-principles.md` at `a1d2fc0`), its
same-session self-review (`…-self-review.md`, `fe60a9d`), and the independent
adversarial review (`…-adversarial-review.md`, fresh Agent, no session memory,
did not read the self-review) per CLAUDE.md's "nothing advances on one pass"
HARD RULE.

Every adversarial finding adopted below was re-derived by this session first
(#613's rule: a finding is a claim, not a verdict). The re-derivation commands
and their output are recorded here; the revised plan cites them.

## Where the two reviews agree

Both flagged the artifact regex's false-positive direction as the weakest
point — the self-review named it as the thing to attack hardest, the
adversarial pass measured it and found eight. Both accept the plan's task
structure, its TDD shape, and the scope cut. Neither found a Tier B
classification that endangers trading, risk, money, auth, or reset behaviour
(the adversarial pass checked all 200 PRs for this explicitly).

## Adjudication of the adversarial findings

| # | Finding | Re-derived? | Decision |
|---|---|---|---|
| B1 | `test_count_review_artifacts_uses_only_the_first_line_of_a_comment` fails against the plan's own regex: its fixture is a single-line comment, so the first line *is* the body and `\badversarial\b` matches → 2, not 1 | Yes — ran both fixture strings against the pattern: the narrating one returns `True` | **Adopt.** The test encodes the requirement correctly (spec D2) and the regex did not implement it. Fixed by B4's anchored form, which returns `False` on that line. |
| B2 | The Lane 3 import scanner is blind to `from services import a, b, c`, the form Lane 3 actually uses; six direct dependencies are neither Tier A nor visible to the test | Yes — `grep` confirms the form at `strategy_engine.py:9`, `exits/exit_engine.py:17`, `settlement_resolver.py:38-39`. An `ast` re-derivation finds **27** direct imports where the regex found 13, with exactly the six the review named uncovered: `fault_log.py`, `history_push.py`, `http_client.py`, `index_feed/`, `market_analyst_agent/`, `market_lookup.py` | **Adopt in full**, including the consequence the review named. See "The Lane 3 correction" below. |
| B3 | Counting review-named **files** lets a PR's earlier-stage artifacts satisfy its PR-stage requirement — this branch's own PR would print `PASS` with zero PR-stage comments | Yes — `git diff --name-only origin/main...HEAD` on this branch lists **7** review-named documents; the counter returns ≥3 with an empty comment list | **Adopt, and go further than the review asked:** the counter takes comments only. A committed document is a *stage* artifact; the PR-stage cycle reviews the PR as submitted, so its artifacts are PR comments. This is simpler than the review's "new in the PR and stage matches" and closes the hole completely. Supersedes spec §6.2's "plus files in the diff" clause. |
| B4 | The regex has a measured false-positive class (~3.5%), and #632 (Tier A) reaches 3 only through one | Yes — fetched the first lines live for #516 #646 #632 #581 #597 and confirmed all of them verbatim (e.g. #646 `## Fix-list recheck (adversarial review returned NO-GO)`, #632 `**Response to the independent adversarial review's finding**`) | **Adopt.** Anchored form designed and measured (below). |

The adversarial pass's own count of the live corpus (265 first lines, 229
match) differs from the plan's snapshot (261/224) because comments were posted
between the two fetches. Not a discrepancy in either measurement; the revised
plan states the snapshot's date and stops asserting a live figure.

### The regex fix, measured rather than asserted

The review prescribed "anchor to a heading/bold prefix and exclude
`response|correction|recheck|…`". Adopted in the anchoring half only — a
disqualifier blocklist is a list of the mistakes made so far, and the next
comment will start with a word nobody listed. What was implemented instead:
strip leading markdown noise, then require the line to **start** with an
optional qualifier and then the keyword.

```
^[\s#*_>`]*(?:(?:independent|pr(?:[-\s](?:level|stage))?|dispatching[-\s]session
|tier\s+b|final|lean|post[-\s]merge|stage\s+\d+|review[-\s]cycle)[\s,:—–-]+)*
(self[-\s]?review|adversarial|consolidation)\b
```

Measured against the 261-line snapshot plus the eight named false positives
and fourteen real headings taken verbatim from merged PRs:

- All **8** named false positives now fail to match.
- All **14** real headings still match, including #625's three `**…**` lines
  that broke the spec's first-revision form.
- Snapshot matches drop 224 → 213. Inspected all 11 dropped lines
  individually: 8 are the named false positives, 2 are
  `**PR review cycle complete** (self-review + adversarial review + …)` —
  one comment claiming all three stages, which is correctly *not* three
  artifacts — and 1 is `## INCOMPLETE — independent adversarial review
  terminated mid-pass`, correctly not counted.
- **One genuine false negative:** `## Review outcome (independent adversarial
  review, fresh Agent call)` was a real artifact and no longer matches.
  Accepted: it fails **closed** (the PR reads `FAIL` and the author fixes the
  heading), and the fix turns the pattern into a stated convention rather than
  a guess at every historical form. The rule text now says what the first line
  must look like.

### The Lane 3 correction, and what it costs

The `ast` re-derivation is the authority; the regex the plan proposed — and
the same blind reading the **spec** used to derive its §3.1 list — missed the
dominant import form. Deciding the six surfaced paths on the merits rather
than adopting them mechanically:

| Path | Imported by | Tier A because |
|---|---|---|
| `services/fault_log.py` | `strategy_engine`, `exit_engine`, `settlement_resolver` | fault recording is the data plane's completeness evidence; a defect here loses records silently |
| `services/http_client.py` | `settlement_resolver`, `account_positions` | REST backoff and rate limiting — flow rate and timeliness |
| `services/index_feed/` | `settlement_edge_entry` | Lane 1 ingestion; two of its files were already Tier A, the package now is |
| `services/market_analyst_agent/` | `exit_engine`, `settlement_resolver` | feeds exit decisions |
| `services/market_lookup.py` | `strategy_engine` | resolves the market a decision is about |
| `services/history_push.py` | `paper_broker` | pushes money figures to the dashboard |

Measured cost over the recorded window: **two PRs move B → A (#614, #498)**.
Code-typed split becomes 70 A / 14 B; overall 144/56 in the recorded window.
**#498 leaves the fixture's Tier B set**, which becomes six, not seven:
#273 #301 #308 #415 #445 #623.

This supersedes the spec in two places, and the spec is amended with a dated
correction rather than left to disagree with its own plan:

- §3.1's Lane-3 import list (five paths, four of which were already covered)
  → the `ast`-derived set, of which six are genuinely new.
- §3.2's counts → 70/14 code-typed, 24/6 unreviewed.

The plan's deviation 4, which claimed the spec over-stated its list, was
**right about the four already-covered entries and wrong about the
conclusion** — the list was not too long, it was derived from an incomplete
scan. Deviation 4 is rewritten to say that.

## Self-review findings, adjudicated

All three of the self-review's "fix before execution" items stand; none was
contested by the adversarial pass.

1. Tier B comment template has no copyable home → Task 8 adds the five-field
   snippet, and it now also states the required first-line form (B4).
2. `#615` near close-family verbs in the PR body → Task 9 step 6 says to check
   `#615`'s state after merge, not only `#613`'s.
3. `_tier_a_prefix` returns the first sorted match, not the most specific →
   noted in the docstring.

Findings 4–6 (dotfile handling, D9 citation, open-decisions provenance) are
adopted as written.

## Merged fix list (applied in one revision, then rechecked item by item)

1. **Regex → the anchored form above**; Task 4's fixtures gain the eight named
   false positives as explicit negative cases and the fourteen real headings
   as positive ones; the snapshot assertion becomes 213 of 261 with the date
   stated; the one accepted false negative is named in the plan.
2. **`count_review_artifacts(comments)` — comments only.** The `files`
   parameter and the filename pattern are removed; Task 4's two file-based
   tests are replaced by one asserting that committed review documents do
   **not** satisfy the PR gate, using this branch's own file list.
3. **Task 2's scanner becomes `ast`-based** and handles all three import
   forms; its test gains a `from services import a, b` case and asserts the
   real repo yields ≥ 25 targets (it yields 27), so it cannot pass vacuously.
4. **Six paths added to `_REVIEW_TIER_A_EXTRA_PATHS`** with the rationale
   table above.
5. **Task 3's fixture corpus: six Tier B PRs, not seven**; #498 moves to a
   Tier A fixture with `services/index_feed/` named as its trigger; the
   verification note's numbers become 70/14 and 24/6.
6. **Spec amended** with a dated correction block in §3.1 and §3.2, and in
   §6.2 for the comments-only counting.
7. **Deviation 4 rewritten**; **deviation 6 added** for the comments-only
   change and the anchored regex.
8. Task 8 adds the Tier B template snippet and the required first-line form.
9. Task 9 step 6: check `#615` state post-merge; keep `#615` out of
   close-verb sentences.
10. Task 1 step 4's grep claim corrected (N1): ten hits exist for
    `kalshi_client` under `tests/`; none reads the hook's tuples, which is the
    claim that matters.
11. Task 3's note: #663 is Tier A through two triggers, not one (N2).
12. Task 7 step 15's expected survivors include CLAUDE.md L38's second
    sentence (N3), so an executor does not "fix" a line that is already
    coherent.
13. Task 7 gains one clause making `review-tier` the stated exception to
    CLAUDE.md L130's handspun-tool rule (N4) — otherwise the new rule text
    contradicts an unchanged line.
14. Task 8 notes which quoted phrases wrap across lines (N5).
15. Task 3's fixture docstring stops claiming the six touch nothing in the
    data plane (N7 — #273 writes recovered `raw_trades` rows).

## Verdict

**NO-GO on `a1d2fc0`; GO expected on the revision.**

Fix 2 (comments-only) and fix 4 (six new Tier A paths) change the artifact
materially — the boundary is wider than the spec measured and the merge gate
is stricter — but neither changes the plan's **scope**: the same nine tasks,
the same three homes, the same one command. Nor does either introduce a claim
the two reviews never saw; both are direct consequences of findings both
passes saw. The fix-list recheck is therefore sufficient under CLAUDE.md's own
recheck clause; a second full cycle is not required.

The spec amendment is the exception worth naming: it corrects a stage-2
artifact that had already passed its own cycle. It is recorded as a dated
correction with its derivation, not a silent edit, and the PR body will say
that stage 3 corrected stage 2.
