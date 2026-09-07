# Review of the revision (Stage 2b: verify the full-census correction)

**Subject.** Commit `f063be0`, "fix: correct milestone-pagination sampling bug (P3/S3/S4),
per Stage 2 review" — the revision of
`docs/archive/lane-1-kalshi-ingestion/research/2026-08-30-kalshi-category-data-shape-audit.md` made in
response to `…-kalshi-category-data-shape-audit-review.md` (Stage 2, `17a926b`).

**Scope.** Verify *the revision*, not Stage 1 or Stage 2. The question is narrow: are the
full-census claims accurate, and is the document now solid enough for Stage 3 to design
against? No Stage 3 design is proposed here.

**Method.** I re-ran the census from scratch rather than trusting its totals. Independently:
paginated `GET /milestones?limit=500` by `cursor` to exhaustion; ran `GET /live_data/batch`
at 100 ids per call over **every** id returned; re-fetched `GET /series?include_volume=true`;
walked `event_ticker → GET /events/{ticker} → series_ticker → Series.category` for every
type the document calls deviating; re-read every cited code line. Probes
2026-08-30 21:0x–21:3x CDT (2026-08-31T02:0xZ–02:3xZ), roughly two hours after the
revision's own census, so small upward drift is expected and is reported rather than
smoothed.

**Verdict vocabulary.** `REPRODUCED` — my census returns the same number (exact, or within
two hours' drift with the drift stated). `DEFECT` — the document says something my
measurement contradicts. `IMPRECISE` — the number is right, the sentence around it is not.

**Headline.** The census is real and it was done properly. I re-derived it end to end and
**every structural claim reproduces**: 275 pages, 44 types, 17 category strings, the
documented `limit`/`cursor`/`maxItems` contract, and — remarkably — the exact figures
1,962 · 814 · 810 · 774 · 520 · 337 · 420 · 241 · 139 · 14 · 1 · 279 · 10,073, along with
`golf 0/169` and `30 of 44 types return live data`, digit for digit. This is a genuine
full-population census, not a bigger sample described as one. Three small defects and one
imprecision remain, listed in §4; none of them moves a ranking, and all four are text-level
fixes. **Go for Stage 3.**

---

## 1. The census scale claim — REPRODUCED

The method is documented-correct before it is measured-correct. `get-milestones.md:71-78`
declares `limit` `maximum: 500` and required, with `cursor` as the pagination parameter;
`get-multiple-live-data.md` declares `milestone_ids` with `maxItems: 100`. So 500-per-page
and 100-per-batch are the documented ceilings, not chosen numbers.

| Claim in the revision | My independent run |
|---|---|
| 275 pages at `limit=500` | **275 pages**, terminating on an empty `cursor` |
| 137,200 milestones, 137,200 unique ids | **137,253**, all unique (+53 in ~2 h) |
| `start_date` 2020-01-01 → 2033-02-07 | **identical**, both ends |
| 44 types, 17 distinct `category` strings | **44 / 17** exact |
| 1,372 `/live_data/batch` calls | 137,200 / 100 = **1,372 exactly**; mine 1,373 for 137,253 |
| 112,501 live payloads | **112,551** (+50), 0 failed chunks |
| 30 types return live data, 14 return none | **30 / 14** exact |

275 × 500 = 137,500 ≥ 137,253, so a 275th partial page is the arithmetically necessary
end of the walk — the page count and the milestone count are consistent with each other,
which is the thing a fabricated census usually gets wrong. The stated call count is not
merely plausible: it is 137,200/100 to the unit.

**The batch endpoint silently omits ids that have no live data** — no error, no null entry.
That is what makes 112,551-of-137,253 a completeness measurement rather than an error rate,
and the revision uses it correctly.

## 2. P1, P3, S3, S4 — REPRODUCED

**P1.** All 17 category strings reproduce, 12 of them exactly: Mentions 3,405 · Elections
1,118 · Entertainment 825 · Companies 594 · Commodities 320 · Finance 205 · Science and
Technology 150 · Politics 78 · Climate and Weather 64 · Crypto 34 · Technology 30 · World 2
· Science & Technology 1, with Sports 123,554 (doc 123,521), Esports 2,746 (2,728),
Financials 2,272 (2,271), Economics 1,855 (1,854). The vocabulary defects are real:
`Financials` and `Finance` are distinct strings, `Science and Technology` /
`Science & Technology` / `Technology` are three, `Companies` is a milestone **type** (23,
exact) as well as a category (594, exact), and `Esports` (2,746) exists in `/milestones`
while `/series` has no such category. The conclusion — milestone `category` is not
join-safe against `Series.category` — is correct and load-bearing.
Plurality types reproduce: `tennis_tournament_singles` 49,877 (doc 49,875),
`one_off_milestone` 16,055 (16,054), `basketball_game` 15,564 (exact),
`soccer_tournament_multi_leg` 15,546 (exact), `esports_match` 11,424 (11,406).
`political_race` **917** and `company_report` **530** are exact.

**S3.** `tennis_tournament_singles` carries `widget_status` on **49,766 of 49,766** live
payloads (doc 49,764/49,764). Stage 1's tennis claim is dead, correctly. The replacement
figure is exact in every part: seven types missing `widget_status` totalling **1,962**
(1.7 %), `political_race` **810** of them, and **814** of the forward-dated slice, of which
`political_race` alone is **520**. I reproduce 1,962 · 810 · 814 · 520 to the unit.

**S4 — and the "0 of 169" phrasing.** The document reads: *"`details.winner` … is absent
from `golf_tournament` (**0 of 169** live; keys `current_round`, `leaderboard`,
`round_label`, `status`, `widget_status`)."* This is **not** ambiguous once measured, and
it does not say Stage 1's golf claim failed. It says the opposite:

> **0 of the 169 live `golf_tournament` payloads carry a `winner` key. All 169 lack it.
> Stage 1's golf finding is fully confirmed, at 100 %.**

My census: `golf_tournament` population 172, live 169, `has winner` **0**, `no winner`
**169**, and the observed key set across all 169 is exactly the five keys listed, with
nothing else. Golf also appears in the D2 no-`winner` table at 169, which is the same fact
counted the other way round — the "0" is the have-it count, the "169" is the lack-it count.
Reading "golf survives at 0/169" as "golf turned out not to lack `winner`" inverts it.

What Stage 1 *did* get wrong in S4 was tennis (falsified: `winner` present 49,766/49,766)
and the sizing (109-of-200 → golf is 169 of 112,551 live payloads, 0.15 %). The dominant
hole is elsewhere and reproduces: `esports_match` **9,403** (doc 9,385), total
no-`winner` **10,760** / 9.6 % (doc 10,706 / 9.5 %). The `winner` key on esports and golf
is genuinely *absent*, not present-and-empty — worth stating because `catalog_scan.py:117`
would fail either way but for different reasons.

**P3.** `political_race` live on **810 of 917** — exact. **774** carry the named race-call
fields — exact. `company_report` **337 of 530** — exact. Non-Sports live totals **4,325
across 8 categories** (doc 4,307/8), with seven of the eight per-category counts exact:
Elections 810 · Economics 355 · Companies 341 · Mentions 292 · Entertainment 140 · Crypto
21 · Financials 3, Esports 2,363 (doc 2,345, the same +18 as `esports_match`).
`Elections` holds **1,389** volume-positive series — exact.

**Both new live-data families are real, correctly characterised, and genuinely uncaptured.**
`grep -rni "truflation\|artist_stream" services main.py tools tests frontend static config`
returns nothing.

- **`truflation`** — 245 population, **241 live** (exact). Every one of the 241 carries
  `indicator`, `category`, `latest_value`, `timeseries`, `target_date`, `series_key`, exactly
  as described, plus `truflation_source_id`, `path`, `latest_point_date`, `last_refreshed`.
  `provider: "truflation"` on all 241. Sample: `breakfast_index`, `latest_value 93.023039`,
  a daily `timeseries` back to 2022-04-26. This is an index series in CF-Benchmarks shape,
  as D3 says.
- **`artist_streams`** — 675 population, **139 live** (exact). `timeseries_daily`,
  `timeseries_weekly`, `current_total`, `target_week_finalized` on 138 of the 139 (one
  variant differs); `period_start`/`period_end` on all 139. `provider: "luminate"` — the
  document names `votehub` for `political_race` but leaves this provider unnamed; naming it
  would help Stage 3, since Luminate is the settlement source of record for stream counts.

## 3. The D-ranking reversal — JUSTIFIED

The reversal rests on two legs. Both hold.

**Leg 1 — Stage 2's sample really did miss esports.** `esports_match` is 11,424 milestones,
**8.3 % of the population**; a uniform 1,200-milestone draw would be expected to contain
roughly 100 of them. Stage 2's twelve-type census contains zero. That is not bad luck: it
is the signature of a first-N-pages draw from a `start_date`-ordered endpoint, which is the
same defect Stage 2 correctly diagnosed in Stage 1, one order of magnitude further out.
Stage 2's post-falsification residue for D2 (golf, 20 of 1,200) understates the true defect
by a factor of ~500. The revision's diagnosis of its own reviewer is correct.

**Leg 2 — the category mapping, which is the claim that makes it matter.** I walked the
documented chain independently, 24 events per type, `GET /events/{ticker}` → `series_ticker`
→ the `category` on my own `/series` fetch:

| milestone type | resolves to `Series.category` | configured? |
|---|---|---|
| `esports_match` | **Sports 24/24** | yes |
| `golf_tournament` | Sports 24/24 | yes |
| `political_race` | Elections 23/24 (1 event 404) | yes |
| `truflation` | Economics 24/24 | yes |
| `artist_streams` | Entertainment 23/24 (1 event 404) | yes |
| `company_report` | Mentions 16/24, **Financials 8/24** | yes (both) |
| `one_off_milestone` | Sports 13, Financials 3, Economics 1 (7 rate-limited) | yes |
| `kpis` | Financials 31/37, **Companies 6/37** | **no** for 6 |

`esports_match → Sports` at 24/24 is the load-bearing cell and it is unambiguous. So
`propagate_milestone_winners` is blind on 9,403 live payloads inside a category the app
configures and actively trades — the largest single `winner` hole on the exchange, and one
neither prior pass saw at all. D2 above D3 is the right call: D2 is a measured defect at
9.6 % of live payloads (13–15 % of the forward-dated slice), while D3's items — the weather
index, the Pyth commodity feed — are documented, code-path-free opportunities whose value
is not yet measured at all. Ranking a measured defect above an unmeasured opportunity is
consistent with the document's own stated ordering principle ("by evidence strength rather
than size").

**Is the revision itself overconfident?** Substantially, no — it is the best-evidenced pass
of the three, and it volunteers its own residual gaps in the revision note rather than
quietly closing them. Two honest cautions, neither of which changes the rank:

1. The 10,760 no-`winner` payloads are overwhelmingly **historical** (only 428 are
   forward-dated). The document does give the forward-dated slice, so it is not misleading —
   but a reader could take 9.5 % as a live rate. It is a backfill/`record_outcome` argument
   first and a real-time argument second, which is still a real argument.
2. The census measures **the exchange**, not the app's exposure. `live_status.py` and
   `catalog_scan.py` poll only watchlisted, milestone-bearing events, so exchange-wide
   counts are an upper bound on what the app would encounter. Worth one clause in D2 so
   Stage 3 sizes against the watchlist, not against 112,551.

## 4. Defects found

**DEFECT 1 — P3 attributes `provider: votehub` to the wrong population** (§2.5 P3, and it
propagates into D4's framing). The document says the 774 payloads carry the race-call fields
"…`last_updated`, provider `votehub`". Measured over all 810 `political_race` live payloads:

- **0 of the 774** carry a `provider` key at all.
- `provider: "votehub"` appears on a **disjoint 36** payloads whose entire `details` is
  `{provider, status, votehub}` — i.e. precisely the 36 that *lack* the race-call fields
  (810 − 774 = 36).
- A **nested `votehub` key** — not a provider string — appears on **602** payloads,
  overlapping 566 of the 774.

VoteHub is genuinely the upstream source, so the spirit survives; the sentence as written
attaches a field value to a population that does not have it, which is the "a value means
exactly what its label says" failure mode. Fix: strike "provider `votehub`" from the 774
clause. Worth adding in its place, because it *strengthens* D4: that nested `votehub` object
carries per-candidate FEC records (`chamber`, `party`, `district`, `fec_id`, `state`,
`primary_date`, `general_date`, `comp_bin`) on 602 payloads — richer than the seven fields
the document lists, and entirely unmentioned.

**DEFECT 2 — "every deviating type attaches to a configured category" is false at the
margin** (§4 D-ranking, and S3's "All seven attach to series in categories the app already
configures"). `kpis` — which appears in *both* hole tables — resolves 31/37 to `Financials`
but **6/37 to `Companies`**, one of the six categories §2.7 dispositions as GAP-SKIP.
Separately, S3 lists `company_report → Mentions` when it is Mentions 16/24 **and Financials
8/24**. Both fixes are one word (`every` → `every material`) plus adding Financials to the
`company_report` arrow. Scale: `kpis` is 14 of 10,706 and 14 of 1,962, so nothing moves —
but the absolute quantifier is what a Stage 3 designer would rely on to skip
unconfigured-category handling in the per-type extractor.

**DEFECT 3 — the no-`winner` table omits `political_race` 36.** D2's table lists eight types
summing to 10,706. The ninth is `political_race`: 810 live − 774 with the fields = **36 that
also lack `winner`**, which P3's own text implies two sections earlier. The table's internal
arithmetic is self-consistent, so this is an omission rather than an error, and it is 0.3 %
of the total — but it is a completeness hole in the one table whose purpose is completeness.

**IMPRECISE — "the app reads exactly one key out of it"** (§4 D3, revision addition). The
app *decides* on one key; it does not read only one. `live_status.py:180-190` writes the
whole `details` object into `state["live_game_state"][et]` and calls
`game_state.record(et, details, …)`, which persists **`raw_json TEXT NOT NULL`** — the
entire payload — alongside a column set that is **sports-shaped** (`home_score`,
`away_score`, `period`, `clock`, `winner`, `last_play`). So `truflation.latest_value` and
`artist_streams.timeseries_weekly` already reach disk today, in an unqueried blob under
columns that cannot describe them. This makes D2/D3 *cheaper*, not weaker — the work is
per-type extraction over an existing store, not new capture — and Stage 3 should be told so
rather than discovering `game_state` mid-design.

**Contract nuance the document does not mention, worth a `docs/kalshi/CHEATSHEET.md` line.**
`milestone.type` and the `type` on the live-data payload disagree for exactly one pairing:
**285 milestones typed `company_report` return payloads self-typed `one_off_milestone`.**
Attributing by milestone type gives `company_report` 337 / `one_off_milestone` 420 — the
document's figures, which I reproduce exactly. Attributing by payload type gives 52 / 705.
Same 757 either way. The document's choice is the right one for describing the population,
but a Stage 3 implementer switching on `ld["type"]` at runtime will see the other split, so
the disagreement should be recorded rather than left to be rediscovered as a bug.

**Smaller shape omissions, all in the "listed keys are a subset" direction** (none wrong,
each would mislead a per-type extractor written from the document alone): `company_report`
has **two** shapes, not one — `provider: quartr` (310, the key set the document lists) and
`provider: fiscal` (27, carrying `timeseries`, `latest_metrics_values`, `metric_id`,
`latest_report_date`); `esports_match`'s `series_stats`/`player_stats` are on ~26 % of
payloads (2,522 / 2,447 of 9,403), not all, though `widget_status` and `home_score`/
`away_score` are on 100 % / 98.5 %; `artist_streams`' timeseries keys are on 138 of 139.

## 5. Spot confirms

- **2.7 % unconfigured** — exact, on my own `/series` fetch: 13,632 series, **10,352**
  volume-positive, unconfigured **279** (Companies 90 · World 87 · Health 48 · Social 34 ·
  Transportation 19 · Education 1) against configured **10,073** = **2.70 %**. The live app
  still reports exactly the 11 configured categories the document names
  (`GET /api/config`), so none of the six has since gone live. The de-circularisation is
  correct on its merits: `catalog_scan.py:337-341` does filter by category before discovery,
  so the original "zero signals" was guaranteed by construction.
- **Two fee-changes endpoints added to D1** — `llms.txt:30` and `:45`, both pages present in
  the mirror, and `grep -rn fee_changes services main.py tools tests` returns nothing. The
  quoted override semantics are verbatim from `llms.txt:45`. Correct, and D1 is the right
  home for them.
- **Every cited code line verified at the line**: `catalog_scan.py:117`
  (`winner = details.get("winner")`), `live_status.py:169`
  (`status = details.get("widget_status")`), `live_status.py:216` (the schedule guess),
  `catalog_scan.py:337-341` (the category filter), `discovery_cache.py:234`. Citation
  discipline in this document is excellent and has been through all three passes.
- **Deferred Stage 2 items are still open, exactly as the revision note declares.**
  `Heatwaves` is in the live taxonomy (so §0/W4's example is still wrong); `kalshiAccessKey`
  is declared by `get-market-orderbook.md` and `get-multiple-market-orderbooks.md` as well as
  `get-event-forecast-percentile-history.md` (so X14's and D3's "the one/only market-data
  endpoint" clauses are still wrong); the date convention is still mixed. Declaring them
  rather than silently leaving them is the right handling, but they are editorial debt that
  should not survive another pass.

## 6. Verdict

**Go for Stage 3.** The revision does what Stage 2 asked and does it properly: it replaced
inference-from-a-sample with a genuine full-population census, and that census survives
independent re-execution — 275 pages, 1,373 calls, and better than a dozen figures matching
to the unit two hours later. P3, S3, S4 and P1 are now the best-evidenced rows in the
document. The D-ranking reversal is justified on both of its legs, and the reviewer it
overrules was demonstrably working from a non-uniform sample.

Fold in before designing, all one-line text edits, none of which changes a number that
matters or a rank: strike `provider votehub` from P3's 774 clause (Defect 1) and consider
adding the nested `votehub` FEC object, since it strengthens D4; soften "every deviating
type" and add `Financials` to `company_report`'s arrow (Defect 2); add `political_race` 36
to D2's no-`winner` table (Defect 3); and replace D3's "reads exactly one key" with the fact
that `game_state` already persists `raw_json` under sports-shaped columns, which is the
single most design-relevant correction here. The three editorial items the revision
deliberately deferred — `Heatwaves`, the X14/D3 uniqueness clauses, the date convention —
are still open and should be closed in whatever pass touches this next.
