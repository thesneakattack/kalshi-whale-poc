# Review of the category data-shape audit (Stage 2: independent verification)

**Subject.** `docs/archive/lane-1-kalshi-ingestion/research/2026-08-30-kalshi-category-data-shape-audit.md`
(commit `e5da187`). Stage 2 of a 9-stage pipeline: verify or falsify Stage 1
independently, do not design.

**Method.** Fresh context from Stage 1's. Every claim below was re-derived from the
primary source, not from Stage 1's citation: the mirrored page reopened at the cited
line, the code line reopened at the cited call site, and every live probe re-run
against `https://external-api.kalshi.com/trade-api/v2` and the running app rather than
trusted. Where Stage 1 gave a number, this review recomputed it from its own fetch.
All probes below: 2026-08-30 18:33–18:55 CDT (2026-08-30T23:33Z–2026-08-31T00:00Z).

**Verdict vocabulary.** `CONFIRMED` — independently re-derived, matches. `CONFIRMED-
INCOMPLETE` — true as stated, but the evidence behind it does not support the scope
claimed. `OVERSTATED` — core claim true, a qualifier attached to it is false.
`WRONG` — falsified by measurement. `UNVERIFIED` — could not be checked here; not
confirmed wrong.

**Headline.** The quantitative spine of this document is unusually good — I reproduced
more than thirty of its figures *exactly*, from independent fetches, including every
row of the §0 tag table and every count in §2.7. The failures are concentrated in one
place: the milestone live-data probes (S3, S4, P3), which were drawn from a single page
of a paginated endpoint and generalized to type-level properties. Two of them are
falsified outright. One of those, **P3**, is the most consequential error in the
document, because Stage 1 recorded it as standing guidance for future sessions.

---

## 1. Spot-checked findings

### 1.1 §0 — the taxonomy is not the vocabulary — **CONFIRMED**

Re-ran `GET /search/tags_by_categories` and a full `GET /series?include_volume=true`
myself (13,632 series, 16.7 MB, HTTP 200).

| Stage 1 claim | My independent measurement | |
|---|---|---|
| taxonomy names 14 categories | 14 | ✅ |
| `Companies`, `Social`, `Transportation` null/empty | all three `None` | ✅ |
| `World`, `Health`, `Education` absent from taxonomy | absent | ✅ |
| `/series` carries 17 distinct categories | 17 | ✅ |
| 10,351 volume-positive series | 10,351 | ✅ |
| 8,329 carry non-empty tags | 8,329 | ✅ |
| `additional_prohibitions` on 10,350 of 10,351 | 10,350 | ✅ |
| `product_metadata` null on all 10,351 | 10,351 null | ✅ |

The entire per-category "+N tags" row of the §0 table reproduces exactly — Politics
+23, Entertainment +22, Financials +13, Elections +12, Economics +8, Sports +8,
Science and Technology +7, Health +6, Commodities +5, World +5, Climate and Weather
+4, Mentions +4, Companies +3, Crypto +2, Transportation +1. So do the §2.7 series
counts (Companies 90, World 87, Health 48, Social 34, Transportation 19, Education 1)
and the spot figures in P5 (Politics 31 vs 8), EN1 (Entertainment 49 vs 27; `Morgan
Wallen` 42), CM3 (`Agriculture` 14, `Metals` 19) and MN1 (`Earnings` 179,
`Politicians` 52).

The authority ordering Stage 1 derives from this is sound, and its doc citations hold:
`terms.md:13` ("subcategories are often represented as tags; use [Get Tags for Series
Categories] to **discover** tags grouped by category") and `terms.md:29` ("do not parse
ticker strings to infer relationships… rely on fields like `series_ticker`,
`event_ticker`, `category`, and `tags`") are both verbatim at those exact lines.
`services/signal_log.py:152-161`'s `series_of` does `ticker.split("-")[0]` as described.

**One error inside a correct section.** Line 48 and W4 both cite `Heatwaves`
(12 Climate series) as a tag "the taxonomy omits". It is **not** omitted — the live
taxonomy's `Climate and Weather` list is
`['Daily temperature', 'Hurricanes', 'Snow and rain', 'Heatwaves', 'Climate change',
'Natural disasters', 'Hourly temperature']`. `Heatwaves` is the fourth entry.
The Climate extras are exactly `Companies`, `Energy`, `KPIs`, `Space` — which is 4, so
the table's "+4" is right and only the prose example leaked. Twelve of the thirteen
other named examples (`Agriculture`, `Foreign Elections`, `Trump Agenda`,
`Trump Policies`, `Culture war`, `Retail/consumer spending`, `Mortgages`,
`FDA Approval`, `Airlines & aviation`, `Olympics`, `UFC`, `Head to Heads`) I confirmed
genuinely absent. **Fix the one example; the section's conclusion is unaffected.**

### 1.2 X6 — structured targets and the UUID no-op — **CONFIRMED (exactly)**

Recomputed from `docs/kalshi/markets.json` myself: 149 markets, `strike_type`
`structured` **134**, `greater` 14, `greater_or_equal` 1; `custom_strike` keys
`golf_competitor` 68, `baseball_team` 27, `tennis_competitor` 23, `football_team` 11,
`basketball_team` 10, `ufc_competitor` 9, `round_digits` 1. Every figure matches Stage 1
digit for digit. Values are UUIDs (`{'baseball_team': '087f0ae7-c231-4dcd-b7e5-…'}`).

`services/market_watch/catalog_scan.py:142-148` reads exactly as cited —
`any(str(winner).lower() in str(v).lower() for v in cs.values())` — so on a structured
market it compares a team/competitor name against a UUID and can never match. The
"demonstrable no-op today" claim is correct, and this is the single best-evidenced
defect in the audit. `grep -rn structured_target services main.py tools tests` returns
nothing: no resolution path exists.

### 1.3 X9 / X10 / X11 — sharding and the lifecycle channel — **CONFIRMED**

`exchange_sharding.md` verified at the exact cited lines: `:12` ("Exchange instances
will correspond to a specific category"), `:22` (Aug 24 crypto→shard 2, tennis and
baseball→shard 3), `:26` ("Programmatic traders must preallocate collateral on a given
exchange shard before order placement"), and the `:73-83` table (0 catch-all,
1 Exotics/Combos, 2 Crypto, 3 Sports∩{Tennis, Baseball}) — Stage 1's shard mapping is
exact. `services/market_watch/market_fetch.py:33-48`'s `_MARKET_FIELDS` whitelist
contains no `exchange_index`, so the field is dropped as claimed.

`market-and-event-lifecycle.md` carries all three message types at precisely the cited
lines — `market_lifecycle_v2` `:313`, `event_lifecycle` `:681`, `event_fee_update`
`:821` — on the single `address: market_lifecycle_v2` channel, with `event_lifecycle`'s
required set (`event_ticker`, `exchange_index`, `title`, `subtitle`,
`collateral_return_type`, `series_ticker`) as described.
`services/kalshi/websocket.py:109-123`'s `_CLASS_BY_MESSAGE_TYPE` has no entry for
either, and `:747` handles only `market_lifecycle_v2`. X11 confirmed too: the subscribe
builder at `:1402-1416` sends `market_lifecycle_v2` alone, and I re-read
`docs/kalshi/CHEATSHEET.md:814` — `/events/multivariate` genuinely has no status or
recency filter (its only params are `limit`, `cursor`, `collection_ticker`,
`with_nested_markets`; the `status` filter I found lives on the *collections* endpoint,
which the CHEATSHEET already distinguishes at `:780-784`).

**New supporting runtime evidence Stage 1 did not have.** `GET /api/health/pipeline`
right now reports `ingest.queue_health.handler_time_by_class.other.lifetime.count =
229` against `lifecycle.lifetime.count = 14515`. Messages *are* landing in
`_OTHER_CLASS` and being discarded. That is correlation, not proof they are
`event_lifecycle`/`event_fee_update` — but it is a cheap, already-instrumented
falsification hook Stage 3 should use rather than assuming.

### 1.4 X2 / X3 / X4 / X7 / X8 — the unread-data cluster — **CONFIRMED**

- `services/kalshi/public.py:134` sends `params={"include_volume": True}` only, and
  filters category client-side at `:136-137`. `tags`, `include_product_metadata` and
  `min_updated_ts` are never sent — and my own fetch confirms the consequence:
  `product_metadata` is null on **all** 10,351 volume-positive series.
- `additional_prohibitions` is at `get-series.md:191` as cited, and
  `grep -rn additional_prohibitions services main.py tools tests frontend static`
  returns nothing. Present on 10,350 of 10,351 series in my own fetch.
- `get-events.md:114-118` documents `with_milestones`; `public.py:223` calls
  `get_events(tickers=…, limit=…)` without it.
- `get_milestones_bulk` exists at `public.py:241-254` and its docstring does claim to
  feed `_sync_milestones_bulk`. That consumer does not exist: grep across
  `services main.py tools tests` finds only the docstring, and
  `git log -S"_sync_milestones_bulk" -- main.py` is empty. Dead code, confirmed.
- X1 confirmed: `main.py:870-873` stamps the constant `tags_by_categories.get(category)`
  onto every event, and `docs/kalshi/CHEATSHEET.md:32` already records the gotcha.

### 1.5 S3 — `widget_status` absent from tennis — **WRONG**

Stage 1: *"`details.widget_status` … is **absent from `tennis_tournament_singles`**
(keys: `advantage`, `competitor1_*`, `competitor2_*`, `completed_rounds`,
`round_winners`, `*_overall_score` — no `status`, no `widget_status`)"*, sized at
"102 of 200 milestones — the single largest type", and concluding *"Every tennis event
silently falls through to `live_status.py:216`'s schedule guess."*

Falsified. I probed **57 of the 102** tennis milestones on that same page, then a
random sample of 8 drawn from the full 288-milestone tennis population:

| type | sampled | has `widget_status` | has `status` | has `winner` |
|---|---|---|---|---|
| `tennis_tournament_singles` | 57 (page) | **57** | 57 | **57** |
| `tennis_tournament_singles` | 8 (population) | **8** | 8 | **8** |

A representative payload's `details` keys are
`['advantage', 'away_overall_score', 'competitor1_*', 'competitor2_*',
'completed_rounds', 'home_overall_score', 'round_winners', 'server', 'status',
'widget_status', 'winner']` — Stage 1's transcribed key list is a strict subset of the
real one with `status`, `widget_status` and `winner` dropped from it. Since
`live_status.py:168-169` finds `widget_status`, no tennis event falls through to the
`:216` schedule guess. The stated impact is not 102/200; it is zero.

### 1.6 S4 — `winner` absent from golf and tennis — **golf CONFIRMED, tennis WRONG, sizing OVERSTATED**

Golf half stands and is clean: `winner` absent on **8 of 8** `golf_tournament`
milestones sampled from the full 20-milestone population; keys are
`['current_round', 'leaderboard', 'round_label', 'status', 'widget_status']`. That is
mechanistically sensible — a tournament resolves off a leaderboard, not a head-to-head
winner — so it reads as a genuine type-level property rather than a state artifact.
(Note Stage 1's key list for golf also omits `status`/`widget_status`, which are
present.)

Tennis half is falsified by the table above: `winner` present 57/57 and 8/8.

Sizing is therefore overstated by roughly thirty-fold. "Golf + tennis were 109 of 200"
becomes golf alone: **20 of 1,200 milestones (1.7 %)**, not 54.5 %.

### 1.7 P3 — "milestone live data is Sports-only in practice" — **WRONG, and the most consequential error here**

Stage 1: *"live probe … for one `political_race` and one `company_report` — **both
return `{"error": {"code": "not_found"}}`** … **GAP-SKIP** … Milestone *live data* is
Sports-only in practice; extending `live_status.py` to Elections would fetch nothing.
Worth recording so a later session does not build it on the assumption that it
generalizes."*

I reproduced that result for one ID of each — and then sampled more, which reverses it:

| type | population | probed | returned live data | `winner` | `widget_status` |
|---|---|---|---|---|---|
| `company_report` | 19 | 8 | **8** | 0 | **0** |
| `political_race` | 3 | 1 | **1** | 1 | **0** |

`company_report` live data carries `['company_name', 'events', 'latest_event',
'next_event', 'provider', 'quartr_company_id', 'status']`. `political_race` carries
`['candidates', 'last_updated', 'race_call_status', 'reporting_percentage', 'status',
'tabulation_status', 'winner', 'winners']` — live election-night tabulation, reporting
percentage and race-call state, unauthenticated, against a category holding 1,389
volume-positive series.

Two things follow. First, Stage 1 generalized from n=1 per type to a type-level
property — the same error as S3, and the one `CLAUDE.md` names directly ("one passing
observation is not a property"). Some milestone IDs 404 and others do not; the audit
happened to draw two that did. Second, because the finding was deliberately written as
a durable warning to future sessions, leaving it uncorrected would steer Stage 3 and
everything after it away from real, free, decision-relevant data. **This must be
corrected before Stage 3 reads the document.**

The genuinely correct S3-shaped finding is here rather than in tennis: `company_report`
and `political_race` are the two types that **do** lack `widget_status` (while carrying
`status`), so those are the types on which `live_status.py:168-169` yields nothing and
`:216`'s schedule guess takes over.

### 1.8 P1 / P2 — milestone categories and Elections structured targets — **CONFIRMED-INCOMPLETE / CONFIRMED**

P1's counts reproduce exactly on the page Stage 1 used (Sports 196, Elections 3,
Companies 1) — but see §3 on why that page is not the population.

P2 is confirmed: of the three `political_race` milestones, the 2024 Presidential
Election one carries exactly `candidate_id_mapping`, `candidate_ids`,
`main_game_event_ticker`, `state`, with `candidate_ids` holding 24 UUIDs — structurally
the Elections twin of Sports' competitor IDs, as claimed. Worth recording that the
other two carry only `main_game_event_ticker`, so the shape varies *within* the type;
that reinforces rather than weakens Stage 1's point, and it is the kind of nuance a
one-instance probe cannot see.

### 1.9 X14 — forecast percentile history is authenticated — **OVERSTATED**

The operative caveat is correct and verified: `get-event-forecast-percentile-history.md`
declares `security: - kalshiAccessKey: []` at `:141-143`, against a document-level
`security: []` at `:30`. So the endpoint is authenticated while the app's public read
path is not — Stage 1's "verify its authentication requirement first" instruction to
Stage 3 stands.

The qualifier around it is wrong. Stage 1 calls it *"the one market-data endpoint in
the mirror that declares `security: kalshiAccessKey`."* `get-market-orderbook.md` and
`get-multiple-market-orderbooks.md` both declare it as well. Delete the uniqueness
clause; keep the caveat.

### 1.10 C3 / C4 / C5 / CM1 — the dormant Pyth feed — **CONFIRMED**

Verified against the *running* app rather than the file: `GET /api/config` returns
`index_feed: {"index_ids": ["BRTI", "ETHUSD_RTI"], "underlying_tickers": []}`. With
`underlying_tickers` empty, `websocket.py:1431`'s `if self.underlying_tickers` guard
never fires and no `pyth_value` subscription is ever created — exactly as claimed.
`pyth-value.md` confirms `underlying_tickers: ["all"]` at `:27`, the `underlying_list`
action at `:30`, and `Metal.XAU/USD` / `Metal.XAG/USD` at `:164` and `:289-290`, so the
"Pyth is also the Commodities feed" reclassification is sound. `grep -n underlying_list
services/kalshi/websocket.py` returns only the passive message-class entry at `:122`, as
stated.

### 1.11 Figures I could not check — **UNVERIFIED (not confirmed wrong)**

`data/*.db` does not exist in this worktree (`data/*.db` is gitignored; only the primary
checkout holds it, and this review was scoped to stay out of it). So the
**97,541-signal total**, the **14.87 % MVE share**, and the **zero-signal counts** in
§2.7 could not be independently recomputed, and the app's API exposes no signal-log
aggregate to reach them through. I found no reason to doubt them — every sibling figure
Stage 1 drew from `series_cache` reproduced exactly against my own live fetch — but they
should be labelled as resting on a single unreplicated read. Stage 1's `market_catalog`
and `title_cache` schema claims are unverified for the same reason.

---

## 2. Staleness check: the widened `kalshi.categories`

**Stage 1 is not stale on this — but its argument is circular, which is worse.**

Stage 1 caught the change. §2.7 lines 170-174 explicitly record that
`config/settings.yaml`'s *live* value lists 11 categories while `origin/main`'s
committed copy still reads `categories: [Sports]`, and flag the divergence. I confirmed
both halves independently: `GET /api/config` returns

```
['Sports', 'Crypto', 'Climate and Weather', 'Entertainment', 'Economics', 'Politics',
 'Mentions', 'Commodities', 'Financials', 'Science and Technology', 'Elections']
```

and this worktree's committed `config/settings.yaml:12-13` still reads `[Sports]`. None
of the six categories Stage 1 dispositions as unconfigured — Companies, World, Health,
Social, Transportation, Education — is among the 11. **No category Stage 1 called
irrelevant is now live.** The disposition is not stale.

The reasoning behind it, however, does not survive review. Stage 1 justifies GAP-SKIP
with *"Measured across all 97,541 logged signals, **zero** originate from any of them"*
and concludes *"nothing in the data says it would produce a signal today."* That zero is
guaranteed by construction. `services/market_watch/catalog_scan.py:337-341` — which
Stage 1 itself cites two lines later — filters the series universe to
`cfg["kalshi"]["categories"]` *before* discovery runs:

```python
categories = cfg["kalshi"].get("categories")
if categories:
    all_series = [s for s in all_series if s.get("category") in categories]
```

An unconfigured category cannot emit a signal. The measurement is a restatement of the
filter, not evidence about the value of widening. Two aggravating factors: the widening
to 11 landed only hours before the audit, so the 97,541-signal history is overwhelmingly
the Sports-only era and *any* per-category count drawn from it under-represents the ten
newly-live categories as well; and the conclusion is load-bearing, since "widening
`kalshi.categories`" sits in Stage 1's **Deliberately not proposed** list.

The non-circular argument is available and Stage 1 already has the data for it: by my
own fetch the six carry **279 volume-positive series** between them (90+87+48+34+19+1)
against **10,072** for the eleven configured — about 2.7 % of the volume-positive
universe, with `Education` at a single series. That is a real basis for deprioritising,
and it is what Stage 3 should be handed instead of the tautology.

---

## 3. Date inconsistency

**Real, but benign in origin — a timezone-labelling defect, not a fabricated citation.**

This machine is `America/Chicago` (CDT, UTC-5). Stage 1's commit `e5da187` is timestamped
`2026-08-30 18:31:27 -0500`, i.e. **2026-08-30T23:31Z**. Any live probe run in that
session's final half-hour genuinely falls on **2026-08-31 in UTC**. The appendix's
"Live unauthenticated GETs … 2026-08-31 UTC" is therefore accurate and correctly
labelled, and my own probes an hour later crossed the same boundary.

The defect is that the document does not hold one convention. Inside a single doc:
DB reads are dated `2026-08-30`, live probes `2026-08-31`, the appendix compromises with
`2026-08-30/31`, and the S3/S4/P1/P3 rows say bare "live probe 2026-08-31" with no zone
marker at all. The repo's own convention is local — `docs/kalshi/CHEATSHEET.md:856`
reads "live-verified 2026-08-30". A reader next week cannot tell whether a bare
"2026-08-31" is a UTC rendering of this session or a later, separate verification.

**Not a citation-discipline failure**, and I want to say that plainly given the premise
I was handed: the date is right, the zone is just unstated. Fix by normalising the
in-line rows to the session's local date, or by writing them as explicit UTC timestamps
the way the appendix does. One-line change; no finding depends on it.

---

## 4. Completeness

### 4.1 The `llms.txt` sweep held up; the live sampling did not

Stage 1 claims to have read all of `llms.txt` and opened every conceptually
category-scoped page. On the documentation axis I largely believe it: I diffed all 230
mirrored `.md` pages against the document, and of the 184 never named, only a handful
are even plausibly category-relevant by slug, and most of those are correctly out of
scope (`welcome-index`, `changelog-index`, `connection-keep-alive`,
`get-settlements`, `market-settlement` — the last already in the CHEATSHEET, as §3
says). Its "genuinely category-neutral" list in §3 is a real, checkable audit trail.

The completeness failure is on the **live-measurement** axis, and it is the same root
cause as S3/S4/P3. `GET /milestones?limit=200` is a paginated endpoint and Stage 1 read
exactly one page, then used its composition as the population. Six pages give a
materially different picture:

| | Stage 1 (1 page, 200) | This review (6 pages, 1,200) |
|---|---|---|
| plurality type | `tennis_tournament_singles` 102 | **`baseball_tournament` 469** |
| types observed | 9 | 12 |
| absent from Stage 1's sample | — | `baseball_tournament` 469, `hockey_tournament` 72, `cricket_match` 1 |
| categories | Sports 196, Elections 3, Companies 1 | Sports 1178, Companies 18, Elections 3, **Financials 1** |

The single largest milestone type on the exchange never appeared in the sample the
audit's sharpest findings were sized from, and a whole category (`Financials`) went
unobserved. Every "of 200" denominator in S3, S4 and P1 should be struck. This is a
data-plane completeness defect *in the audit itself* — precisely the property
`CLAUDE.md` says fails silently and must be measured rather than assumed.

For the record, the per-type census the document should have contained (8 sampled per
type, drawn from all 1,200):

| type | population | `winner` | `widget_status` |
|---|---|---|---|
| `baseball_tournament` | 469 | 8/8 | 8/8 |
| `tennis_tournament_singles` | 288 | 8/8 | 8/8 |
| `basketball_game` | 282 | 8/8 | 8/8 |
| `hockey_tournament` | 72 | 8/8 | 8/8 |
| `soccer_tournament_multi_leg` | 24 | 8/8 | 8/8 |
| **`golf_tournament`** | 20 | **0/8** | 8/8 |
| **`company_report`** | 19 | **0/8** | **0/8** |
| `mma_match` | 12 | 8/8 | 8/8 |
| `racing_tournament` | 7 | 7/7 | 7/7 |
| **`political_race`** | 3 | 1/1 | **0/1** |
| `football_game` | 3 | 3/3 | 3/3 |
| `cricket_match` | 1 | 1/1 | 1/1 |

Three types deviate, none of them tennis. This table is what D2 should be designed
against.

### 4.2 Pages Stage 1 missed

**`get-series-fee-changes.md` (`GET /series/fee_changes`) and `get-event-fee-changes.md`
(`GET /events/fee_changes`)** — named nowhere in the document. Stage 1 covers the
*push* side of fee overrides (X10's `event_fee_update`, `fee_type_override` /
`fee_multiplier_override`) and refers to "the override the app just wired from REST in
issue #264", but never reaches the two REST endpoints that expose the fee-change surface
itself. `get-event-fee-changes.md` states the semantics Stage 1's X10 row needs and does
not cite: *"Event fees are an override layered on top of the parent series' fee
structure. If `fee_type_override` and `fee_multiplier_override` are null, that indicates
the override is cleared."* `get-series-fee-changes.md` takes `series_ticker` and
`show_historical`, i.e. it exposes scheduled and historical fee changes, not just the
current value.

This is category-relevant rather than category-specific: `get-series.md:170-180`
documents `fee_type` values that are structurally category-shaped —
`quadratic_with_combo_maker_fees` is described as the combo variant, and the CHEATSHEET
already carries a shipped bug about combo maker-fee exemption. It is a modest addition,
not a headline, and it belongs alongside D1 (which already proposes reading `fee_type`
off the series object) rather than as a fourth direction.

**`get-live-data-with-type.md`** (`GET /live_data/{type}/milestone/{id}`) — not named
either, but correctly out of scope: the page declares itself legacy ("This is the legacy
endpoint that requires a type path parameter. Prefer using
`/live_data/milestone/{milestone_id}` instead"). Recording it only so the next pass does
not re-open it.

---

## 5. Verdict on the proposed directions

**D1 — series-metadata store. Endorsed, unchanged, still first.** This is the
best-supported direction in the document and my re-derivation strengthens it rather than
qualifying it: 10,351 series on disk, 8,329 with tags, 10,350 with
`additional_prohibitions`, zero readers for any of it, and `product_metadata` null on
every row purely because one query param is not sent. The `terms.md:29` argument for
replacing `series_of()`'s ticker-prefix hack is a documented contract violation, not a
preference. Add the fee-changes endpoints from §4.2 to its "natural companions" list,
since it already proposes carrying `fee_type` forward.

**D2 — milestone/live-data shapes. Sound instinct, falsified evidence, must be
re-scoped and demoted.** Stage 1 introduces D2 as "the tightest measured defects in the
audit" on the strength of S3 (102/200) and S4 (109/200). Both figures are gone: S3 is
falsified outright, S4 survives only as golf, at 20 of 1,200. What genuinely remains is
still worth doing — golf's missing `winner`, the missing `widget_status` on
`company_report` and `political_race`, and the X6/X7/X8 items, which are independently
strong and which I confirmed exactly. And the *design instinct* is right and worth
preserving verbatim: a per-type extractor with an explicit "this type has no
winner/status key" branch plus a counter so the next unknown type surfaces as a metric
instead of silence is exactly what the §4.1 table shows is needed — Stage 1 reached the
right shape from the wrong sample. But the framing "stop treating Sports as one shape"
should become "the three deviating types are golf, `company_report` and `political_race`,
and two of them are not Sports at all", and D2 should sit **below D3** on evidence
strength.

**D3 — ask each category what its version of the index feed is. Endorsed, and now
relatively stronger than D2.** The weather twin (W1/W2) is a clean structural argument
and `get-weather-index.md` is present in the mirror with no code path anywhere. The Pyth
half (C3/C4/CM1) I verified against the live app, not just the file: the subscription
genuinely never forms. The X14 ladder idea is correctly flagged as highest-ceiling /
lowest-certainty and its auth caveat is real — just strike the "only market-data
endpoint" clause per §1.9.

**Missing: a fourth candidate that P3 wrongly closed.** With P3 falsified, Elections
milestone live data — `race_call_status`, `reporting_percentage`, `tabulation_status`,
`candidates`, `winners`, unauthenticated, over 1,389 volume-positive series — is real,
available, and directly decision-relevant to a whale-signal app. Stage 1's document
currently tells the next session *not* to build it. That disposition should be reopened
and handed to Stage 3 as a candidate, sized honestly (3 milestones live right now, so
its value is episodic and election-calendar-driven — which is an argument about timing,
not about existence).

**Deliberately-not-proposed list.** Game-stats/player-stats (S5/S6) and the
`exchange_index` scope limit are both well-reasoned and should stand. Widening
`kalshi.categories` should stay on the list but with the circular justification replaced
by the 2.7 %-of-universe figure in §2.

---

## 6. Overall recommendation

**Revise before Stage 3 — narrowly, not structurally.**

This is a strong document. Its documentation sweep is real, its code citations are
accurate at the line, and its quantitative claims reproduce exactly against independent
fetches — thirty-plus figures, including every row of two tables, with not one
transcription error among them. X6 in particular is a genuinely excellent find, fully
confirmed. §0's central argument stands. D1 and D3 can be designed against **as they
are, today**.

What needs to change is confined to one methodological failure and its consequences:
Stage 1 probed a paginated live endpoint one page deep and generalized single instances
into type-level properties. Required revisions, in priority order:

1. **Rewrite P3.** It is falsified and it is recorded as durable guidance. `company_report`
   returns live data 8/8; `political_race` returns race-call and tabulation data.
   Reopen it as a Stage 3 candidate.
2. **Rewrite S3 and re-scope S4.** Tennis carries `widget_status` and `winner` on 57/57
   and 8/8. Golf's missing `winner` survives at 20/1,200. Replace both with the per-type
   census in §4.1, and re-rank D2 below D3.
3. **Replace §2.7's circular justification** with the 2.7 %-of-volume-positive-universe
   argument. The GAP-SKIP disposition itself is correct and not stale.
4. **Strike three overstatements:** `Heatwaves` as a taxonomy omission (§0 line 48, W4);
   "the one market-data endpoint … that declares `security`" (X14); every "of 200"
   denominator.
5. **Normalise the date convention** to local or explicit-UTC throughout.
6. **Add** the `series`/`events` fee-changes endpoints to D1's companions, and label the
   `signal_log.db` figures as resting on a single unreplicated read.

Items 1–3 are substantive and should be Stage 1's work before Stage 3 begins. Items 4–6
are editorial and could be folded into the same pass. Nothing here invalidates the
audit's structure, its authority ordering, or its top direction.
