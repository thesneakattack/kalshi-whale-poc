# Self-review: weather index ingestion design (2026-08-31)

Reviews `docs/superpowers/specs/2026-08-30-weather-index-ingestion-design.md`
(merged via PR #263, 2026-08-30) per CLAUDE.md's "nothing advances on one
pass" HARD RULE, before this design is used as `writing-plans` input. The
doc's own status line says "brainstormed, corrected during self-review
after two verification passes, ready for user review before `writing-
plans`" — that self-review predates this repo's formal
self-review-then-independent-adversarial-review-then-consolidation cycle
(HARD RULE dated 2026-08-31, PR #304), so this is the first pass through
the now-standard cycle, not a duplicate of work already done.

## What was checked

- **Mechanism claims** (REST-only, `GET /trade-api/v2/live_data/weather/{city}`,
  no WebSocket channel, `last_sec` trailing-window parameter, minute
  resolution): the design doc already cites primary sources for each
  (`docs/kalshi/get-weather-index.md`, `docs/kalshi/websockets.md`, a live
  market's own `rules_primary`/`rules_secondary` text) rather than asserting
  from memory. Spot-checked `docs/kalshi/get-weather-index.md` directly —
  the "minute-resolution series", quorum-gap, and `last_sec` claims quoted
  in the design doc match the file verbatim.
- **Settlement-statistic and source-relationship claims** (max/min over a
  calendar day, not a fixed 60-observation average; Kalshi's index is a
  related-but-unconfirmed proxy for The Weather Company's settlement
  source): consistent with `services/index_feed/settlement_algebra.py`'s
  own docstring (checked directly) describing crypto's fixed-window average
  — the weather design's contrast claim holds up.
- **The load-bearing city-ranking caveat — found materially outdated.**
  The design doc flags its own city ranking (`KXHIGHLAX` > `KXHIGHNY` >
  `KXHIGHMIA` > `KXHIGHCHI`) as built on catalog data "4 days stale"
  because `kalshi.categories` was `[Sports]`-only at scan time, and
  explicitly says "re-confirm this city ranking against fresh data before
  implementation." That precondition can now be checked directly: `config/
  settings.yaml`'s `kalshi.categories` was widened to all 11 categories
  (including "Climate and Weather") on 2026-08-30, the same day this
  design was written — re-querying `data/market_catalog.db` live via
  `ddev exec` (read-only) shows `KXHIGH%` rows now `updated_at`
  2026-08-30T18:11:54Z (≈14h stale at check time, not 4 days) with a
  **different ranking**: `KXHIGHLAX` (1,474,143.9) > `KXHIGHMIA`
  (359,090.28) > `KXHIGHNY` (343,419.24) > `KXHIGHCHI` (267,427.45) — MIA
  and NY have swapped places — plus several previously-invisible cities
  with comparable-or-greater volume than CHI's 4th-place pick:
  `KXHIGHTHOU` (209,520.81), `KXHIGHTDAL` (199,374.75), `KXHIGHAUS`
  (187,801.77). The design's own "re-confirm before implementation"
  instruction was correct to include; it just hadn't been acted on yet.
  This is exactly the kind of thing the implementation-plan stage needs to
  fold in as a correction, not treat the original 4-city list as settled.
- **Schema/write-path reasoning** (upsert vs. append-only, `raw_json`
  fidelity, no-row-not-null for quorum gaps): internally consistent with
  cited precedents (`capture_writer.py`'s upsert semantics,
  `index_feed.ingestion`'s raw-payload storage convention) — read both
  cited files directly, descriptions match.

## Unaddressed scope

- The design doc explicitly defers "exact interval and per-city REST cost
  against this account's real rate-limit budget" to implementation time —
  reasonable to leave open (matches the data-plane HARD RULE's "identify
  the measured bottleneck first" rather than guessing a polling frequency),
  but the implementation plan needs to actually do that measurement as a
  task, not silently skip it.
- The `city` path-parameter spelling is flagged as unverified against
  Kalshi's real city-ID list (`get-weather-index.md`'s example uses
  lowercase `miami`, not the `KXHIGH*` ticker suffix convention) — still
  unverified as of this review; carried forward as an implementation-plan
  task, not resolved here (would require a live API call with real
  candidate city names, appropriately a `writing-plans`/implementation-time
  check rather than a design-review one).

## Verdict

**GO, with one correction to carry forward**: the city-ranking caveat is
confirmed real (not hypothetical) and now has fresh, primary-source data to
resolve it — the implementation plan should use the current ranking (LAX,
MIA, NY, CHI) and explicitly reconsider whether THOU/TDAL/AUS belong in the
starting set given how close their volume now sits to CHI's, rather than
carrying forward the original doc's now-superseded 4-day-stale numbers.
Proceed to independent adversarial review.
