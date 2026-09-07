# Kalshi REST Demand, Endpoint Costs and Batching (I8)

**Task:** I8 of `docs/archive/lane-1-kalshi-ingestion/plans/2026-08-25-realtime-data-plane-investigation.md`.
**Tool:** `tools/kalshi_rate_limit_probe.py` (read-only, manual), tests in
`tests/test_kalshi_rate_limit_probe.py`.
**Docs read first (exact mirrored pages):** `get-account-api-limits.md`
(`GET /account/limits`: `usage_tier`, `read`/`write` buckets with `refill_rate` tokens/s and
`bucket_capacity`; "a request is allowed if the bucket holds enough tokens to cover its
cost; otherwise ... 429"), `list-non-default-endpoint-costs.md` (`GET
/account/endpoint_costs`: `default_cost` — "currently 10" — plus `{method, path, cost}` for
every endpoint priced differently), `rate_limits.md` ("Bucket capacity and bursting": Basic
and Advanced Predictions read buckets hold **two seconds** of budget; "Batch endpoints
don't save tokens": every item in a **batch order** request is billed separately),
`get-markets.md` (`tickers` is a comma-separated filter with no per-call cap; page
`limit` maximum 1000).

## What the docs settle before any measurement

- The "batch is billed per item" rule is written for the batch **order** endpoints
  (`25 orders = 25 × 10 tokens`). Nothing in the mirror says a `GET /markets?tickers=…`
  list filter is billed per ticker. `services/kalshi/public.py`'s 50-per-chunk sizing was
  justified as "500 tokens under the 600-token ceiling" — that arithmetic assumes per-item
  billing the docs do not state for this endpoint. Whether the real cost is the flat
  default is exactly what the `costs` and `batch` probes measure.
- Rates in requests/second follow from `refill_rate / cost` and bursts from
  `bucket_capacity / cost`; the probe derives both from the account's own numbers rather
  than from the tier table.

## Measured (2026-08-25, `python -m tools.kalshi_rate_limit_probe --limits --costs --batch`)

### Account limits (`GET /account/limits`, authenticated)

| field | value |
|---|---|
| `usage_tier` | `basic` |
| read bucket | `refill_rate` **200** tokens/s, `bucket_capacity` **600** (3 s of budget — the doc's "two seconds" is the tier-table framing; the account's own figure is what counts) |
| write bucket | 100 tokens/s, capacity 100 |
| grants | none |

### Endpoint costs (`GET /account/endpoint_costs`, authenticated)

`default_cost` **10**. Twelve endpoints carry a non-default cost — **none of them is an
endpoint this app calls** (matched against `APP_ENDPOINTS`: markets, market, trades,
orderbook, candlesticks, series, events, event, milestones, live data ×3, exchange
status, search ×2, balance, positions, fills, orders, create/cancel). Every request this
app makes therefore costs 10 tokens, and the implied read budget is
**20 requests/s sustained, 60 requests in a burst** (600 / 10), recovering fully after
3 idle seconds.

### Batching (`GET /markets?tickers=…`, one request per size, from a full local bucket)

| tickers per request | returned | completeness | network | limiter wait | 429 |
|---|---|---|---|---|---|
| 50 | 50 | 1.00 | 41 ms | 0 | 0 |
| 100 | 100 | 1.00 | 73 ms | 0 | 0 |
| 200 | 200 | 1.00 | 106 ms | 0 | 0 |

Each size was a single request with a single attempt. Together with the cost table this
settles H8: a 200-ticker request is billed exactly like a 50-ticker one (10 tokens) and
returns complete results ~2.6× faster per ticker. `services/kalshi/public.py`'s 50-per-chunk
sizing ("500 tokens under the 600 ceiling") rested on per-item billing the docs describe
only for batch **order** endpoints. The gateway now accepts an explicit `batch_size`
(default unchanged) so this can be revisited by the REST solution comparison (I11), not
changed as a drive-by here.

### Demand by caller class

The authoritative window is I7's 63-minute busy-hour capture (lifetime deltas,
`docs/archive/lane-1-kalshi-ingestion/research/2026-08-25-realtime-live-baseline.md`): background classes held
**72.7%** of 6,332 calls (live status 35.4%, catalog 24.2%, resolution 13.1%) against
critical classes' **24.3%** (position 15.7%, whale enrichment 8.6%), at 1.67 calls/s
overall — roughly 8% of the measured sustained budget, while the local limiter's 8/s cap
and 8-token burst were the binding constraint (read-bucket waiter high-water 34; limiter
waits of 0.4–1.1 s average and up to 25 s for background classes). 15% of
`background_live_status` calls failed (332/2,239) and that class drew the largest share
of limiter time — a demand that is both the biggest and the least productive.

The probe's `--demand` mode reads the same shares from persisted observability samples.
Its first use exposed a defect in I5: the per-class `calls/attempts/rate_limited/errors`
had been persisted as *lifetime* counters, so summing per-minute samples inflated demand
~30×. Fixed in this task (window counts persisted; lifetime kept in the in-memory
snapshot), with exact per-endpoint-family window counts added
(`kalshi_rest_endpoint.<family>.*`) so demand and duplicate-polling questions no longer
depend on the per-tick `kalshi_rest.<family>.*` snapshots, which sample ~1 tick in 10.

### Milestone / live-data duplicate demand (H9) — measured

`python -m tools.kalshi_rate_limit_probe --demand --demand-hours 0.4 --tracked-events 28`,
run after 25 minutes of untouched samples with the exact per-endpoint window counts this
task added (2026-08-25, evening session):

| | |
|---|---|
| calls in the window | 3,359 (2.33/s); background 76.0%, critical 22.1% |
| by class | live_status 30.9% · resolution 23.0% · catalog 22.0% · position 15.4% · whale 6.7% · other 1.9% |
| by endpoint | `get_markets` 1,207 · `get_milestones` 793 · `get_market` **790** · `get_exchange_status` 157 · `get_event_live_data` 135 · `get_live_datas` 99 · balance/fills/positions 56 each |
| milestone-family calls observed | 1,027 (`get_milestones` 793 + `get_live_datas` 99 + `get_event_live_data` 135) |
| minimum for 28 tracked events at one poll per event per 60 s | 672 |
| **duplicate factor** | **1.53** |

Two findings. (1) **H9 is real but moderate**: the three independent caches (catalog scan,
live status, event live data) poll the milestone/live-data surface ~1.5× more than one
shared cache would need — material for a budget that spends its waits in bursts, not a
dominant cost. (2) A demand this study did not expect: `get_market` at 790 calls in
24 minutes (0.55/s) is the lifecycle `settled` re-read in `_process_stream_lifecycle`
(`background_resolution`, 23% of all calls) — one extra REST read per settlement event,
exchange-wide, which the 2026-08-23 docstring estimated at ~0.06/s. It is the second-largest
single endpoint and a candidate for elimination or batching in I11 (the `determined` event
already carries `result`; a batched finalized-status read would replace N singles).

## Hypothesis status after I8

- **H8 answered.** The batching assumption was stale: no per-ticker billing for list
  filters (docs), flat 10-token cost for every endpoint this app calls (account), and a
  200-ticker request measured complete and un-throttled. The local limiter runs at 40% of
  the verified sustained rate and 13% of the verified burst.
- **H9 confirmed, moderate**: duplicate factor 1.53 on the milestone/live-data surface; and the
  lifecycle `settled` re-read is 23% of all REST demand (0.55/s), far above its documented
  estimate — both are demand-reduction inputs for I11.
- **H7 reinforced, not yet causal**: background classes hold ~73% of calls and nearly all
  of the limiter time and errors; the local 8-token burst, not Kalshi's 60-request burst,
  is what critical calls queue behind.
