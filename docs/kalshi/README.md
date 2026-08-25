# Kalshi Docs Snapshot

Local copies of the docs.kalshi.com pages and live response snapshots fetched during this session.

## Source Pages

- `llms.txt`
  Source: `https://docs.kalshi.com/llms.txt`
- `quick_start_websockets.md`
  Source: `https://docs.kalshi.com/getting_started/quick_start_websockets.md`
- `websocket-connection.md`
  Source: `https://docs.kalshi.com/websockets/websocket-connection.md`
- `public-trades.md`
  Source: `https://docs.kalshi.com/websockets/public-trades.md`
- `market-ticker.md`
  Source: `https://docs.kalshi.com/websockets/market-ticker.md`
- `get-event-live-data.md`
  Source: `https://docs.kalshi.com/api-reference/live-data/get-event-live-data.md`
- `get-filters-for-sports.md`
  Source: `https://docs.kalshi.com/api-reference/search/get-filters-for-sports.md`
- `get-tags-for-series-categories.md`
  Source: `https://docs.kalshi.com/api-reference/search/get-tags-for-series-categories.md`
- `get-event.md`
  Source: `https://docs.kalshi.com/api-reference/events/get-event.md`
- `get-market.md`
  Source: `https://docs.kalshi.com/api-reference/market/get-market.md`
- `api_environments.md` (2026-08-15)
  Source: `https://docs.kalshi.com/getting_started/api_environments.md` -
  confirms no category-specific hosts exist; recommended default host is
  `external-api.kalshi.com`, not the legacy `api.elections.kalshi.com`
  alias `kalshi.base_url` was switched from on this date.
- `get-events.md` (2026-08-15)
  Source: `https://docs.kalshi.com/api-reference/events/get-events.md` -
  the list/batch form of Get Event, `tickers=` comma-separated param.
- `get-event-metadata.md` (2026-08-15)
  Source: `https://docs.kalshi.com/api-reference/events/get-event-metadata.md`
- `get-milestones.md` (2026-08-15)
  Source: `https://docs.kalshi.com/api-reference/milestone/get-milestones.md` -
  the list form, `category`/`min_updated_ts` filters.
- `get-live-data-with-type.md` (2026-08-24, Phase A Task A1 - split from the
  old merged `get-live-data.md`, verbatim now)
  Source: `https://docs.kalshi.com/api-reference/live-data/get-live-data-with-type.md`
- `get-multiple-live-data.md` (2026-08-24, Phase A Task A1 - split from the
  old merged `get-live-data.md`, verbatim now)
  Source: `https://docs.kalshi.com/api-reference/live-data/get-multiple-live-data.md`
- `get-game-stats.md` (2026-08-15)
  Source: `https://docs.kalshi.com/api-reference/live-data/get-game-stats.md`
  - deliberately left as a curated summary: not a production-used contract
    (no call site anywhere reads this endpoint - `game_state.py`'s own
    score/period fields come from the live-data milestone endpoints above,
    confirmed via A0's census finding zero `get_game_stats`-shaped call
    sites), so Phase A's Finding A scope note ("global mirror completeness
    is an initiative goal, not a reason to block unrelated migration
    indefinitely") applies - not an oversight.
- `rate_limits.md` (2026-08-24, Phase A Task A1 - now a verbatim single-
  source mirror; this account's own live tier/cost data and the flagged
  rate-limiter tuning finding moved to CHEATSHEET.md instead of living in
  the mirrored body)
  Source: `https://docs.kalshi.com/getting_started/rate_limits.md`
- `list-non-default-endpoint-costs.md` (2026-08-24, Phase A Task A1 - split
  from the old merged `rate_limits.md`, verbatim now)
  Source: `https://docs.kalshi.com/api-reference/account/list-non-default-endpoint-costs.md`

### 2026-08-16 — full-index gap-fill

The above was a deliberately curated subset. Per direct instruction to mirror
literally every API documentation page, the real, current `llms.txt` index
was re-fetched from `https://docs.kalshi.com/llms.txt` (215 unique `.md`
pages; 18 already covered above) and every remaining page was fetched below
in one pass. `llms.txt` in this directory now holds that full index verbatim
instead of the old curated subset — see its own `## Notes` section for
details, including naming collisions between the standard trading API and
its margin-exchange counterpart (`margin-rest/`, `margin-ws/`, `fix-margin/`),
which are resolved with a `margin-rest-`/`margin-ws-`/`fix-margin-` filename
prefix, and two `welcome/index.md` vs `changelog/index.md` pages resolved as
`welcome-index.md` / `changelog-index.md`.

- `accept-block-trade-proposal.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/accept-block-trade-proposal.md` - Endpoint for accepting a block trade proposal.
- `accept-quote.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/accept-quote.md` - DEPRECATED: Use PUT /communications/rfqs/{rfq_id}/quotes/{quote_id}/accept instead. Endpoint for accepting a quote. This will require the quoter to confirm.
- `accept-rfq-quote.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/accept-rfq-quote.md` - Endpoint for accepting a quote scoped to its RFQ. This will require the quoter to confirm.
- `amend-order.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/orders/amend-order.md` - Endpoint for amending the price and/or max number of fillable contracts in an existing margin order.
- `amend-order-v2.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/orders/amend-order-v2.md` - Endpoint for amending the price and/or max fillable count of an existing event-market order using the V2 request/response shape. The request `count` is the updated total/max fillable count, equal to already filled count plus desired resting remaining count. This behavior matches the v1 amend endpoin…
- `api_keys.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/api_keys.md` - API Key usage
- `authentication.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix/authentication.md` - API key creation, logon, session lifecycle, and message retransmission
- `batch-cancel-orders-v2.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/orders/batch-cancel-orders-v2.md` - Endpoint for cancelling a batch of event-market orders using the V2 response shape. The maximum batch size scales with your tier's write budget — see [Rate Limits and Tiers](/getting_started/rate_limits).
- `batch-create-orders-v2.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/orders/batch-create-orders-v2.md` - Endpoint for submitting a batch of event-market orders using the V2 request/response shape. The maximum batch size scales with your tier's write budget — see [Rate Limits and Tiers](/getting_started/rate_limits).
- `batch-get-market-candlesticks.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/market/batch-get-market-candlesticks.md` - Endpoint for retrieving candlestick data for multiple markets.
- `cancel-order.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/orders/cancel-order.md` - Endpoint for canceling an order. Cancels all remaining resting contracts and returns the canceled order details.
- `cancel-order-v2.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/orders/cancel-order-v2.md` - Endpoint for cancelling event-market orders using the V2 response shape. Returns `{order_id, client_order_id, reduced_by}` rather than a full order object.
- `cfbenchmarks-value.md` (2026-08-16)
  Source: `https://docs.kalshi.com/websockets/cfbenchmarks-value.md` - Real-time CF Benchmarks index value updates, each carrying the raw upstream frame plus trailing 60-second and quarter-hour final-minute averages. Requires authentication.
- `common-components.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix/common-components.md` - Standard header, trailer, and shared components across all FIX messages
- `communications.md` (2026-08-16)
  Source: `https://docs.kalshi.com/websockets/communications.md` - Real-time Request for Quote (RFQ) and quote notifications. Requires authentication.
- `confirm-quote.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/confirm-quote.md` - DEPRECATED: Use PUT /communications/rfqs/{rfq_id}/quotes/{quote_id}/confirm instead. Endpoint for confirming a quote. This will start a timer for order execution.
- `confirm-rfq-quote.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/confirm-rfq-quote.md` - Endpoint for confirming a quote scoped to its RFQ. This will start a timer for order execution.
- `connection-keep-alive.md` (2026-08-16)
  Source: `https://docs.kalshi.com/websockets/connection-keep-alive.md` - WebSocket control frames for connection management.
- `connectivity.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix/connectivity.md` - Endpoints, transport configuration, and rate limits for the Kalshi FIX API
- `create-api-key.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/api-keys/create-api-key.md` - Endpoint for creating a new API key with a user-provided public key.  This endpoint allows users with Premier or Market Maker API usage levels to create API keys by providing their own RSA public key. The platform will use this public key to verify signatures on API requests.
- `create-margin-fcm-subtrader.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/fcm/create-margin-fcm-subtrader.md` - Endpoint for FCM members to create a margin subtrader.
- `create-market-in-multivariate-event-collection.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/multivariate/create-market-in-multivariate-event-collection.md` - Endpoint for creating an individual market in a multivariate event collection. This endpoint must be hit at least once before trading or looking up a market. Users are limited to 5000 creations per week.
- `create-order.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/orders/create-order.md` - Endpoint for submitting orders in a market.
- `create-order-group.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/order-groups/create-order-group.md` - Creates a new order group with a contracts limit measured over a rolling 15-second window. Users can have up to 100,000 order groups at a time. When the limit is hit, all orders in the group are cancelled and no new orders can be placed until reset.
- `create-order-v2.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/orders/create-order-v2.md` - Endpoint for submitting event-market orders using the V2 request/response shape (single-book `bid`/`ask` side and fixed-point dollar prices). The legacy `/portfolio/orders` endpoint will be deprecated no earlier than May 6, 2026 — clients should migrate to this path.
- `create-quote.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/create-quote.md` - Endpoint for creating a quote in response to an RFQ
- `create-rfq.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/create-rfq.md` - Endpoint for creating a new RFQ. You can have a maximum of 100 open RFQs at a time.
- `create-subaccount.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/create-subaccount.md` - Creates a new subaccount for the authenticated user. This endpoint is available to all users on the Advanced API tier and above. Subaccounts are numbered sequentially starting from 1. Maximum 63 numbered subaccounts per user (64 including the primary account).
- `decrease-order.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/orders/decrease-order.md` - Endpoint for decreasing the number of contracts in an existing order. Exactly one of `reduce_by` or `reduce_to` must be provided. Canceling an order is equivalent to decreasing to zero.
- `decrease-order-v2.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/orders/decrease-order-v2.md` - Endpoint for decreasing the remaining count of an existing event-market order using the V2 request/response shape. Exactly one of `reduce_by` or `reduce_to` must be provided.
- `delete-api-key.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/api-keys/delete-api-key.md` - Endpoint for deleting an existing API key.  This endpoint permanently deletes an API key. Once deleted, the key can no longer be used for authentication. This action cannot be undone.
- `delete-fcm-subtrader-risk-controls.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/fcm/delete-fcm-subtrader-risk-controls.md` - Removes the initial margin cap for an FCM member's subtrader on the margined exchange.
- `delete-order-group.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/order-groups/delete-order-group.md` - Deletes an order group and cancels all orders within it. This permanently removes the group.
- `delete-quote.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/delete-quote.md` - DEPRECATED: Use DELETE /communications/rfqs/{rfq_id}/quotes/{quote_id} instead. Endpoint for deleting a quote, which means it can no longer be accepted.
- `delete-rfq.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/delete-rfq.md` - Endpoint for deleting an RFQ by ID
- `delete-rfq-quote.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/delete-rfq-quote.md` - Endpoint for deleting a quote scoped to its RFQ, which means it can no longer be accepted.
- `demo_env.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/demo_env.md` - Set up and test with Kalshi's demo environment
- `drop-copy.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix/drop-copy.md` - Recover missed execution reports and query historical order events
- `error-handling.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix/error-handling.md` - Understanding and handling errors in the FIX protocol
- `exchange_sharding.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/exchange_sharding.md` - Exchange sharding in the Predictions API
- `fee_rounding.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/fee_rounding.md` - How the exchange rounds fees to maintain balance precision.
- `fix-margin-authentication.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix-margin/authentication.md` - API key creation, logon, session lifecycle, and message retransmission
- `fix-margin-connectivity.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix-margin/connectivity.md` - Endpoints, transport configuration, and rate limits for the Kalshi Margin FIX API
- `fix-margin-drop-copy.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix-margin/drop-copy.md` - Recover missed margin execution reports and query historical order events
- `fix-margin-error-handling.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix-margin/error-handling.md` - Understanding and handling errors on margin FIX sessions
- `fix-margin-listener-sessions.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix-margin/listener-sessions.md` - Real-time read-only feed of margin execution reports from your trading session
- `fix-margin-market-data.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix-margin/market-data.md` - Request margin order book snapshots and incremental updates through FIX
- `fix-margin-order-entry.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix-margin/order-entry.md` - Submit, modify, and cancel margin orders through FIX messages
- `fix-margin-order-groups.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix-margin/order-groups.md` - Manage order groups for automatic position management
- `fixed_point_migration.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/fixed_point_migration.md` - Fixed-point prices, price level structures, and fractional contract quantities.
- `generate-api-key.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/api-keys/generate-api-key.md` - Endpoint for generating a new API key with an automatically created key pair.  This endpoint generates both a public and private RSA key pair. The public key is stored on the platform, while the private key is returned to the user and must be stored securely. The private key cannot be retrieved agai…
- `get-account-api-limits.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/account/get-account-api-limits.md` - Endpoint to retrieve the authenticated user's Predictions API usage tier and token-bucket limits. Public Predictions tiers include Basic, Advanced, Expert, Premier, Paragon, Prime, and Prestige.
- `get-account-api-usage-level-volume-progress.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/account/get-account-api-usage-level-volume-progress.md` - Returns the authenticated user's latest cron-computed trading volume progress toward volume-based API usage tiers for the predictions (event_contract) lane. Volume figures are reported as fixed-point contract counts.
- `get-all-subaccount-balances.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/get-all-subaccount-balances.md` - Gets balances for all subaccounts including the primary account.
- `get-api-keys.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/api-keys/get-api-keys.md` - Endpoint for retrieving all API keys associated with the authenticated user.  API keys allow programmatic access to the platform without requiring username/password authentication. Each key has a unique identifier and name.
- `get-balance.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/get-balance.md` - Endpoint for getting the balance and portfolio value of a member. `portfolio_value` is always scoped to the requested `exchange_index` (defaulting to 0). When `subaccount` is omitted, `balance` is the primary account's aggregate available balance; pass `subaccount` explicitly (0 for primary, 1-63 fo…
- `get-block-trade-proposals.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/get-block-trade-proposals.md` - Endpoint for getting block trade proposals visible to the authenticated user.
- `get-communications-id.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/get-communications-id.md` - Endpoint for getting the communications ID of the logged-in user.
- `get-deposits.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/get-deposits.md` - Endpoint for getting the member's deposit history.
- `get-enabled-status.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/exchange/get-enabled-status.md` - Endpoint for checking if margin trading is enabled for the authenticated user.
- `get-event-candlesticks.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/events/get-event-candlesticks.md` - End-point for returning aggregated data across all markets corresponding to an event.
- `get-event-fee-changes.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/events/get-event-fee-changes.md` - Event fees are an override layered on top of the parent series' fee structure. If `fee_type_override` and `fee_multiplier_override` are null, that indicates the override is cleared.
- `get-event-forecast-percentile-history.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/events/get-event-forecast-percentile-history.md` - Endpoint for getting the historical raw and formatted forecast numbers for an event at specific percentiles.
- `get-exchange-schedule.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/exchange/get-exchange-schedule.md` - Endpoint for getting the exchange schedule.
- `get-exchange-status.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/exchange/get-exchange-status.md` - Endpoint for getting the exchange status.
- `get-fcm-orders.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/fcm/get-fcm-orders.md` - Endpoint for FCM members to get orders filtered by subtrader ID. This endpoint requires FCM member access level and allows filtering orders by subtrader ID.
- `get-fcm-positions.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/fcm/get-fcm-positions.md` - Endpoint for FCM members to get market positions filtered by subtrader ID. This endpoint requires FCM member access level and allows filtering positions by subtrader ID.
- `get-fcm-subtrader-risk-controls.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/fcm/get-fcm-subtrader-risk-controls.md` - Returns the initial margin caps configured for an FCM member's subtrader on the margined exchange. A cap with no market_ticker applies across all markets; the remaining caps are scoped to a single market each. Markets without a cap are omitted.
- `get-fee-tiers.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/fees/get-fee-tiers.md` - Endpoint for retrieving the margin fee tiers for the authenticated direct margin user. Returns a map of margin market tickers to their fee tier strings.
- `get-fills.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/get-fills.md` - Endpoint for getting all fills for the member. A fill is when a trade you have is matched. Fills that occurred before the historical cutoff are only available via `GET /historical/fills`. See [Historical Data](https://docs.kalshi.com/getting_started/historical_data) for details.
- `get-funding-history.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/funding/get-funding-history.md` - Endpoint for retrieving the authenticated user's historical margin funding payments joined with funding rates for a specific market, or across all markets when ticker is empty, over an inclusive UTC date range.
- `get-funding-rate-estimate.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/funding/get-funding-rate-estimate.md` - Returns the estimated funding rate for the current, in-progress funding period. The value is a time-weighted average of the premium index computed over `[last_funding_time, now)`, so it continues to move as new data accumulates through the window and is only finalized at `next_funding_time`.
- `get-historical-cutoff-timestamps.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/historical/get-historical-cutoff-timestamps.md` - Returns the cutoff timestamps that define the boundary between **live** and **historical** data.
- `get-historical-fills.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/historical/get-historical-fills.md` - Endpoint for getting all historical fills for the member. A fill is when a trade you have is matched.
- `get-historical-funding-rates.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/funding/get-historical-funding-rates.md` - Endpoint for retrieving historical margin funding rates for a market, or across all markets when ticker is empty.
- `get-historical-market.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/historical/get-historical-market.md` - Endpoint for getting data about a specific market by its ticker from the historical database.
- `get-historical-market-candlesticks.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks.md` - Endpoint for fetching historical candlestick data for markets that have been archived from the live data set. Time period length of each candlestick in minutes. Valid values: 1 (1 minute), 60 (1 hour), 1440 (1 day).
- `get-historical-markets.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/historical/get-historical-markets.md` - Endpoint for getting markets that have been archived to the historical database. Filters are mutually exclusive.
- `get-historical-orders.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/historical/get-historical-orders.md` - Endpoint for getting orders that have been archived to the historical database.
- `get-historical-positions.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/historical/get-historical-positions.md` - Endpoint for getting settled market positions that have been archived to the historical database. Positions whose markets were archived before `market_positions_last_updated_ts` on `GET /historical/cutoff` are available via this endpoint. Positions are archived per whole event: a settled event's pos…
- `get-historical-trades.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/historical/get-historical-trades.md` - Endpoint for getting all historical trades for all markets. Trades that were filled before the historical cutoff are available via this endpoint. Block trades are included by default and identified by the `is_block_trade` field; use the `is_block_trade` query parameter to filter by block / non-block…
- `get-incentives.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/incentive-programs/get-incentives.md` - List incentives with optional filters. Incentives are rewards programs for trading activity on specific markets.
- `get-intra-account-transfer.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/get-intra-account-transfer.md` - Endpoint for getting a single intra-account transfer by id.
- `get-intra-account-transfers.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/get-intra-account-transfers.md` - Endpoint for fetching intra-exchange account transfer history.
- `get-market-candlesticks.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/market/get-market-candlesticks.md` - Time period length of each candlestick in minutes. Valid values: 1 (1 minute), 60 (1 hour), 1440 (1 day). Candlesticks for markets that settled before the historical cutoff are only available via `GET /historical/markets/{ticker}/candlesticks`. See [Historical Data](https://docs.kalshi.com/getting_s…
- `get-market-orderbook.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/market/get-market-orderbook.md` - Endpoint for getting the current order book for a specific market.  The order book shows all active bid orders for both yes and no sides of a binary market. It returns yes bids and no bids only (no asks are returned). This is because in binary markets, a bid for yes at price X is equivalent to an as…
- `get-markets.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/market/get-markets.md` - Filter by market status. Possible values: `unopened`, `open`, `closed`, `settled`. Leave empty to return markets with any status.  - Only one `status` filter may be supplied at a time.  - Timestamp filters will be mutually exclusive from other timestamp filters and certain status filters.
- `get-milestone.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/milestone/get-milestone.md` - Endpoint for getting data about a specific milestone by its ID.
- `get-multiple-market-orderbooks.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/market/get-multiple-market-orderbooks.md` - Endpoint for getting the current order books for multiple markets in a single request. The order book shows all active bid orders for both yes and no sides of a binary market. It returns yes bids and no bids only (no asks are returned). This is because in binary markets, a bid for yes at price X is…
- `get-multivariate-event-collection.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/multivariate/get-multivariate-event-collection.md` - Endpoint for getting data about a multivariate event collection by its ticker.
- `get-multivariate-event-collections.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/multivariate/get-multivariate-event-collections.md` - Endpoint for getting data about multivariate event collections.
- `get-multivariate-events.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/events/get-multivariate-events.md` - Retrieve multivariate (combo) events. These are dynamically created events from multivariate event collections. Supports filtering by series and collection ticker.
- `get-notional-risk-limit.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/risk/get-notional-risk-limit.md` - Endpoint for retrieving the notional value risk limit for the authenticated margin user.
- `get-order.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/orders/get-order.md` - Endpoint for getting a single order.
- `get-order-group.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/order-groups/get-order-group.md` - Retrieves details for a single order group including all order IDs and auto-cancel status.
- `get-order-groups.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/order-groups/get-order-groups.md` - Retrieves all order groups for the authenticated user.
- `get-order-queue-position.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/orders/get-order-queue-position.md` - Endpoint for getting an order's queue position in the order book. This represents the amount of orders that need to be matched before this order receives a partial or full match. Queue position is determined using a price-time priority.
- `get-orders.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/orders/get-orders.md` - Restricts the response to orders that have a certain status: resting, canceled, or executed. Orders that have been canceled or fully executed before the historical cutoff are only available via `GET /historical/orders`. Resting orders will always be available through this endpoint. See [Historical D…
- `get-perps-account-api-limits.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/account/get-perps-account-api-limits.md` - Endpoint to retrieve the Perps (margin) API tier limits associated with the authenticated user.
- `get-positions.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/get-positions.md` - Restricts the positions to those with any of following fields with non-zero values, as a comma separated list. The following values are accepted: position, total_traded
- `get-queue-positions-for-orders.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/orders/get-queue-positions-for-orders.md` - Endpoint for getting queue positions for all resting orders. Queue position represents the number of contracts that need to be matched before an order receives a partial or full match, determined using price-time priority.
- `get-quote.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/get-quote.md` - DEPRECATED: Use GET /communications/rfqs/{rfq_id}/quotes/{quote_id} instead. Endpoint for getting a particular quote.
- `get-quotes.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/get-quotes.md` - Endpoint for getting quotes
- `get-rfq.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/get-rfq.md` - Endpoint for getting a single RFQ by id
- `get-rfq-quote.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/get-rfq-quote.md` - Endpoint for getting a particular quote scoped to its RFQ.
- `get-rfqs.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/get-rfqs.md` - Endpoint for getting RFQs
- `get-risk.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/risk/get-risk.md` - Endpoint for retrieving leverage and liquidation price data for the authenticated direct margin user. Returns account-level leverage plus per-position leverage and liquidation prices, grouped by subaccount and market.
- `get-risk-parameters.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/risk/get-risk-parameters.md` - Returns system-wide margin risk parameters including liquidation thresholds and per-market initial margin multipliers.
- `get-series.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/market/get-series.md` - Endpoint for getting data about a specific series by its ticker.  A series represents a template for recurring events that follow the same format and rules (e.g., "Monthly Jobs Report", "Weekly Initial Jobless Claims", "Daily Weather in NYC"). Series define the structure, settlement sources, and met…
- `get-series-fee-changes.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes.md`
- `get-series-list.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/market/get-series-list.md` - Endpoint for getting data about multiple series with specified filters.  A series represents a template for recurring events that follow the same format and rules (e.g., "Monthly Jobs Report", "Weekly Initial Jobless Claims", "Daily Weather in NYC"). This endpoint allows you to browse and discover a…
- `get-settlements.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/get-settlements.md` - Endpoint for getting the member's settlements historical track.
- `get-structured-target.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/structured-targets/get-structured-target.md` - Endpoint for getting data about a specific structured target by its ID.
- `get-structured-targets.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/structured-targets/get-structured-targets.md` - Page size (min: 1, max: 2000)
- `get-subaccount-netting.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/get-subaccount-netting.md` - Gets the netting enabled settings for all subaccounts.
- `get-subaccount-transfers.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/get-subaccount-transfers.md` - Gets a paginated list of all transfers between subaccounts for the authenticated user.
- `get-total-resting-order-value.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/get-total-resting-order-value.md` - Endpoint for getting the total value, in cents, of resting orders. This endpoint is only intended for use by FCM members (rare). Note: If you're uncertain about this endpoint, it likely does not apply to you.
- `get-trades.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/market/get-trades.md` - Endpoint for getting all trades for all markets. A trade represents a completed transaction between two users on a specific market. Each trade includes the market ticker, price, quantity, and timestamp information. Block trades are included in the response by default and identified by the `is_block_…
- `get-user-data-timestamp.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/exchange/get-user-data-timestamp.md` - There is typically a short delay before exchange events are reflected in the API endpoints. Whenever possible, combine API responses to PUT/POST/DELETE requests with WebSocket data to obtain the most accurate view of the exchange state. This endpoint provides an approximate indication of when the da…
- `get-withdrawals.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/get-withdrawals.md` - Endpoint for getting the member's withdrawal history.
- `historical_data.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/historical_data.md` - Accessing historical exchange data via the Kalshi API.
- `changelog-index.md` (2026-08-16)
  Source: `https://docs.kalshi.com/changelog/index.md` - Stay updated with API changes and version history
- `intra-account-transfer.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/intra-account-transfer.md` - Endpoint for transferring funds within the same account.
- `listener-sessions.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix/listener-sessions.md` - Real-time read-only feed of execution reports from your trading session
- `live-data-get-live-data.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/live-data/get-live-data.md` - Get live data for a specific milestone.
- `maintenance_and_pauses.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/maintenance_and_pauses.md` - Scheduled maintenance windows, trading pauses, and exchange pauses
- `making_your_first_request.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/making_your_first_request.md` - Start trading with Kalshi API in under 5 minutes
- `margin.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin.md` - Getting started with Kalshi's perpetual-futures (perps) trading API
- `margin-rest-create-order-group.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/order-groups/create-order-group.md` - Creates a new order group on the margin exchange with a contracts limit measured over a rolling window. When the limit is hit, all orders in the group are cancelled and no new orders can be placed until reset.
- `margin-rest-create-subaccount.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/portfolio/create-subaccount.md` - Creates a new subaccount for the authenticated user in the margin exchange. Subaccounts are numbered sequentially starting from 1. Maximum 63 numbered subaccounts per user (64 including the primary account).
- `margin-rest-delete-order-group.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/order-groups/delete-order-group.md` - Deletes an order group on the margin exchange and cancels all orders within it.
- `margin-rest-get-balance.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/portfolio/get-balance.md` - Endpoint for retrieving the balance breakdown for the authenticated direct margin user. Returns cash balance (aggregate and per-subaccount), position value, total balance, and maintenance margin requirement.
- `margin-rest-get-exchange-status.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/exchange/get-exchange-status.md` - Endpoint for getting the margin exchange status.
- `margin-rest-get-fills.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/portfolio/get-fills.md` - Endpoint for retrieving the authenticated user's margin fills.
- `margin-rest-get-market.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/market/get-market.md` - Endpoint for fetching a margin market with trading stats (price, volume, open interest).
- `margin-rest-get-market-candlesticks.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/market/get-market-candlesticks.md` - Endpoint for fetching candlestick data for a margin market.
- `margin-rest-get-market-orderbook.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/market/get-market-orderbook.md` - Endpoint for getting the orderbook for a margin market.
- `margin-rest-get-markets.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/market/get-markets.md` - Endpoint for listing available margin markets.
- `margin-rest-get-order.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/orders/get-order.md` - Endpoint for retrieving a specific margin order.
- `margin-rest-get-order-group.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/order-groups/get-order-group.md` - Retrieves details for a single order group on the margin exchange including all order IDs and auto-cancel status.
- `margin-rest-get-order-groups.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/order-groups/get-order-groups.md` - Retrieves all order groups for the authenticated user on the margin exchange.
- `margin-rest-get-orders.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/orders/get-orders.md` - Endpoint for listing margin orders with optional filtering.
- `margin-rest-get-positions.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/portfolio/get-positions.md` - Endpoint for retrieving the authenticated user's margin positions.
- `margin-rest-get-trades.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/market/get-trades.md` - Endpoint for retrieving public margin trades for a given market ticker. Returns a paginated response. Use the cursor value from the previous response to get the next page.
- `margin-rest-intra-account-transfer.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/portfolio/intra-account-transfer.md` - Endpoint for transferring funds within the same account.
- `margin-rest-reset-order-group.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/order-groups/reset-order-group.md` - Resets the order group matched contracts counter to zero on the margin exchange, allowing new orders to be placed again after the limit was hit.
- `margin-rest-transfer-between-subaccounts.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/portfolio/transfer-between-subaccounts.md` - Transfers funds between the authenticated user's margin subaccounts. Use 0 for the primary account, or 1-63 for numbered subaccounts.
- `margin-rest-trigger-order-group.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/order-groups/trigger-order-group.md` - Triggers the order group on the margin exchange, canceling all orders in the group and preventing new orders until the group is reset.
- `margin-rest-update-order-group-limit.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/order-groups/update-order-group-limit.md` - Updates the order group contracts limit on the margin exchange. If the updated limit would immediately trigger the group, all orders in the group are canceled and the group is triggered.
- `margin-ws-connection-keep-alive.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-ws/websockets/connection-keep-alive.md` - Kalshi sends Ping frames every 10 seconds with body `heartbeat`. Clients should respond with Pong frames.
- `margin-ws-market-ticker.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-ws/websockets/market-ticker.md` - Margin market updates are delivered on a single channel. `ticker` messages include price, top-of-book size, volume, open-interest, and optional reference/mark prices.
- `margin-ws-order-group-updates.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-ws/websockets/order-group-updates.md` - Real-time order group lifecycle and limit updates. Requires authentication.
- `margin-ws-orderbook-updates.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-ws/websockets/orderbook-updates.md` - Real-time margin orderbook price-level changes.
- `margin-ws-public-trades.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-ws/websockets/public-trades.md` - Public notifications for executed margin trades.
- `margin-ws-user-fills.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-ws/websockets/user-fills.md` - Private fill notifications for the authenticated user on the margin exchange.
- `margin-ws-user-orders.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-ws/websockets/user-orders.md` - Private order created/updated notifications for the authenticated user on the margin exchange.
- `margin-ws-websocket-connection.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-ws/websockets/websocket-connection.md` - Main WebSocket connection endpoint. Authentication is required during the WebSocket handshake.
- `market-and-event-lifecycle.md` (2026-08-16)
  Source: `https://docs.kalshi.com/websockets/market-and-event-lifecycle.md` - Market state changes and event creation notifications.
- `market-data.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix/market-data.md` - Request order book snapshots and incremental updates through FIX
- `market-positions.md` (2026-08-16)
  Source: `https://docs.kalshi.com/websockets/market-positions.md` - Real-time updates of your positions in markets. Requires authentication.
- `market-settlement.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix/market-settlement.md` - Settlement reports for market outcomes and position resolution
- `market_lifecycle.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/market_lifecycle.md` - How markets move from creation to settlement
- `market_settlement.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/market_settlement.md` - How market outcomes are determined and positions are resolved
- `multivariate-market-and-event-lifecycle.md` (2026-08-16)
  Source: `https://docs.kalshi.com/websockets/multivariate-market-and-event-lifecycle.md` - Multivariate event (MVE) market state changes and event creation notifications.
- `order-entry.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix/order-entry.md` - Submit, modify, and cancel orders through FIX messages
- `order-group-updates.md` (2026-08-16)
  Source: `https://docs.kalshi.com/websockets/order-group-updates.md` - Real-time order group lifecycle and limit updates. Requires authentication.
- `order-groups.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix/order-groups.md` - Manage order groups for automatic position management
- `order_direction.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/order_direction.md` - How direction is expressed on Order, Fill, and Trade responses, and how to migrate from the legacy action/side fields.
- `order_groups.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/order_groups.md` - Automatic order cancellation based on rolling contract limits
- `orderbook-updates.md` (2026-08-16)
  Source: `https://docs.kalshi.com/websockets/orderbook-updates.md` - Real-time orderbook price level changes. Provides incremental updates to maintain a live orderbook.
- `orderbook_responses.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/orderbook_responses.md` - Understanding Kalshi orderbook structure and binary prediction market mechanics
- `overview.md` (2026-08-16)
  Source: `https://docs.kalshi.com/sdks/overview.md` - Official Python and TypeScript SDKs for the Kalshi API
- `pagination.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/pagination.md` - Learn how to navigate through large datasets using cursor-based pagination
- `price-banding.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin/price-banding.md` - How price banding works for Kalshi margin markets
- `propose-block-trade.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/communications/propose-block-trade.md` - Endpoint for creating a block trade proposal.
- `pyth-value.md` (2026-08-16)
  Source: `https://docs.kalshi.com/websockets/pyth-value.md` - Real-time Pyth price updates for configured underlying tickers
- `quick_start_authenticated_requests.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/quick_start_authenticated_requests.md` - Three simple steps to make your first authenticated API request to Kalshi
- `quick_start_create_order.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/quick_start_create_order.md` - Learn how to find markets, place orders, check status, and cancel orders on Kalshi
- `quick_start_market_data.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/quick_start_market_data.md` - Learn how to access real-time market data without authentication
- `reset-order-group.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/order-groups/reset-order-group.md` - Resets the order group's matched contracts counter to zero, allowing new orders to be placed again after the limit was hit.
- `rest-passthrough.md` (2026-08-16)
  Source: `https://docs.kalshi.com/cfbenchmarks/rest-passthrough.md` - Query CF Benchmarks REST data using your existing Kalshi API credentials
- `rfq-messages.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix/rfq-messages.md` - Request for Quote functionality for RFQ creators and market makers
- `rfqs.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/rfqs.md` - How the Kalshi RFQ system works
- `subaccounts.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/subaccounts.md` - Isolate balances and positions within a single Direct account
- `subpenny-pricing.md` (2026-08-16)
  Source: `https://docs.kalshi.com/fix/subpenny-pricing.md` - Dollar-based pricing format for subpenny precision
- `targets_and_milestones.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/targets_and_milestones.md` - Using milestones and structured targets in the Trade API
- `terms.md` (2026-08-16)
  Source: `https://docs.kalshi.com/getting_started/terms.md` - Core terminology used in the Kalshi exchange
- `transfer-between-subaccounts.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/transfer-between-subaccounts.md` - Transfers funds between the authenticated user's subaccounts. Use 0 for the primary account, or 1-63 for numbered subaccounts. Set exchange_index to apply the transfer on a specific exchange shard (defaults to 0).
- `trigger-order-group.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/order-groups/trigger-order-group.md` - Triggers the order group, canceling all orders in the group and preventing new orders until the group is reset.
- `update-fcm-subtrader-risk-controls.md` (2026-08-16)
  Source: `https://docs.kalshi.com/margin-rest/fcm/update-fcm-subtrader-risk-controls.md` - Sets the initial margin cap for an FCM member's subtrader on the margined exchange.
- `update-order-group-limit.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/order-groups/update-order-group-limit.md` - Updates the order group contracts limit (rolling 15-second window). If the updated limit would immediately trigger the group, all orders in the group are canceled and the group is triggered.
- `update-subaccount-netting.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/portfolio/update-subaccount-netting.md` - Updates the netting enabled setting for a specific subaccount. Use 0 for the primary account, or 1-63 for numbered subaccounts.
- `upgrade-account-api-usage-level.md` (2026-08-16)
  Source: `https://docs.kalshi.com/api-reference/account/upgrade-account-api-usage-level.md` - Grants a permanent Advanced API usage-level grant. Currently only the Predictions exchange instance is supported. Criteria: at least 1 of the user's last 100 Predictions orders was created via API. Use Get Account API Limits to inspect the resulting usage tier and grants.
- `user-fills.md` (2026-08-16)
  Source: `https://docs.kalshi.com/websockets/user-fills.md` - Your order fill notifications. Requires authentication.
- `user-orders.md` (2026-08-16)
  Source: `https://docs.kalshi.com/websockets/user-orders.md` - Real-time order created and updated notifications. Requires authentication.
- `websockets.md` (2026-08-16)
  Source: `https://docs.kalshi.com/websockets.md` - Trade API WebSocket endpoint and schema reference
- `welcome-index.md` (2026-08-16)
  Source: `https://docs.kalshi.com/welcome/index.md` - Welcome to the Kalshi API documentation

## Fetched Response Snapshots

- `manifest.json`
- `markets.json`
- `event_titles.json`
- `event_live_data.json`
- `category_metadata.json`
- `sample_market_response.json`
- `sample_event_response.json`

These markdown/text files are session-local copies of the fetched content surfaced by the tooling, not guaranteed byte-for-byte raw upstream source exports.