# Design Self-Review — Persistence Layer db.py Migration

Self-review per this project's "nothing advances on one pass" HARD RULE — same author,
checking this artifact's own internal consistency and unaddressed scope before an independent
adversarial review runs (that must be a separate, memory-less Agent call, not done here).

## What I checked

- Every numeric claim in the design (30 vs. 38 vs. 27 module counts, the fault-log occurrence
  counts, `series_watcher.db`'s 29.7 GB figure) is cited to one of the three input documents,
  not re-derived from memory — I did not independently re-run any of the greps or queries those
  documents already ran; I trusted their own already-reviewed numbers (PR #504's research doc
  cleared its own PR-stage review at GO with 0 must-fix; the baseline and audit docs have not
  yet been through their own review cycles — flagged as a real gap below, not glossed over).
- The API-shape comparison table is built from directly reading both `db.py` designs' actual
  code (PR #484's Task 1 code block, already merged and re-read for this spec; the prototype's
  code, described secondhand through the research doc and the db-foundation-audit's own direct
  quotes — I did not personally re-read `17b2e8f`'s source, see gap below).
- Re-checked the module-count arithmetic by hand: research doc's 30 (`_connect()`-only,
  `services/`-only) − 5 Tier0-fixed = 25; baseline's 38 (broader grep, `services/`+`tools/`) − 5
  Tier0 − 5 already-safe − 3 pooled = 25 leaking, matching the research doc's 25 exactly, plus 2
  split-pattern (`backup.py`, `store_stats.py`) already counted inside that 25 for their leaking
  half. My own "27" figure adds `store_stats.py` (genuinely new to the tracked scope) and
  `tools/coordination_engine.py` (outside `services/`, so outside the research doc's 30-count
  entirely) to the research doc's 25 — I did the addition by hand in the document rather than
  trusting a round number; worth an independent recheck.

## Unaddressed scope / weaknesses I can see in my own artifact

1. **I did not personally read `services/db.py` at commit `17b2e8f` or `tests/test_db.py`
   directly** — the API-shape comparison and the "8 passing tests, independently re-executed"
   claim are both taken from PR #504's PR-stage review (which DID directly execute the test
   suite inside the real container, a strong verification) and the db-foundation-audit's own
   direct-source-read claims. This is two layers of secondhand trust for the exact code this
   spec's central decision rests on, not zero. An adversarial review should read the prototype's
   actual source directly rather than trust either document's transcription, the same way this
   spec's own citations expect of downstream readers.
2. **The recommendation to adopt the callback shape is a real design call I made, not something
   any of the three input documents told me to conclude.** The research doc and baseline
   measurement describe both `db.py` designs' *existence* but neither compares them or
   recommends one — that comparison and recommendation is this spec's own contribution, flagged
   explicitly in the document's own "Open question for explicit sign-off" section for exactly
   this reason. I believe the reasoning holds (the callback shape's ability to express
   non-table DDL without an escape hatch, versus PR #484's design needing one in 3 of 3 first
   uses, is the strongest single point) but an adversarial review should scrutinize this on its
   own merits, not defer to my framing of "it's obviously better."
3. **I did not verify the db-foundation-audit's and baseline measurement's own claims from
   primary sources** — both are un-reviewed documents (no self-review, no adversarial review, no
   consolidation exist for either, as far as I found) sitting on separate, unmerged branches. I
   treated their content as reliable input per the coordinator's explicit assignment to use
   them, but I did not re-derive their central claims (the fault-log occurrence counts, the
   38-file grep, the five specific gaps in the prototype) against live data or source myself.
   This is a real gap: this spec's scope-correction section and its "must-fix" list both rest
   load-bearing weight on documents that have not themselves cleared this repo's own review bar
   yet. An adversarial review should treat both feeder documents' claims as unverified, exactly
   as it would treat this spec's own claims, and re-derive at least the load-bearing ones
   (the fault-log counts, the schema-conflict bug, the store_stats.py split-pattern finding)
   from source.
4. **The `series_watcher.db` sequencing question is named but not resolved** — I deliberately
   left this as an implementation-plan decision rather than deciding it here, since nothing in
   any input document measures whether a straight `db.py` migration actually costs anything
   proportional to file size (my own stated reasoning is that it shouldn't, since
   schema-replay-on-connect doesn't touch existing rows) — but I have not verified this
   reasoning against `db.py`'s actual `connect()` implementation, only asserted it from the
   general shape of what a `CREATE TABLE IF NOT EXISTS` does. Worth an adversarial check.
5. **The declined-pooling section's reasoning leans on `tick_executor.connection_for()`'s
   precedent, which I did not independently verify** — I'm citing PR #504's research doc's
   description of that precedent (itself independently re-verified by that PR's own PR-stage
   review, finding 9) rather than reading `tick_executor.py`'s header comment myself. Lower risk
   than gap 1 above, since it's already been through one independent verification pass, but
   still secondhand for this document specifically.
6. **Migration Gate 2's fd-count reuse claim** ("Tier0's Task 9 already added the process-wide
   `open_fds` counter") is based on my own direct knowledge of Tier0's implementation (I
   personally implemented and merged PR #501, which included this counter) — this is the one
   claim in this document I can attest to firsthand rather than citing another document, worth
   noting as a strength, not a gap.

## What I did not find wrong with my own artifact

The document states files/citations for every numeric claim, distinguishes what's verified
(PR #504's research doc, cleared its own review) from what's assumed-reliable-but-unreviewed
(the baseline and audit docs) rather than treating all three input documents as equally solid,
and does not silently decide the one genuine architecture question (API shape) without flagging
it for explicit review — consistent with this repo's stated preference for AI-executed rigor
over an AI-executed final call on a decision this consequential. No task in this document
proposes touching `data/*.db` write paths, enabling real trading, or weakening a safety
invariant — this is a design document with zero code changes.
