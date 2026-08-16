Source: https://docs.kalshi.com/getting_started/api_environments.md

# API Environments and Endpoints

## REST API Base URLs

**Production:**
- `https://external-api.kalshi.com/trade-api/v2` (recommended)
- `https://api.elections.kalshi.com/trade-api/v2` (also supported)

**Demo:**
- `https://external-api.demo.kalshi.co/trade-api/v2` (recommended)
- `https://demo-api.kalshi.co/trade-api/v2` (also supported)

## WebSocket URLs

**Production:**
- `wss://external-api-ws.kalshi.com/trade-api/ws/v2` (recommended)
- `wss://api.elections.kalshi.com/trade-api/ws/v2` (also supported)

**Demo:**
- `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2` (recommended)
- `wss://demo-api.kalshi.co/trade-api/ws/v2` (also supported)

## Default recommendation

The `external-api` hosts are the recommended endpoints for general API
access. Despite the "elections" subdomain naming, the API provides access
to all Kalshi markets: "the production Trade API provides access to all
Kalshi markets, not only election-related markets." The alternative hosts
remain supported for backward compatibility but are not preferred for new
implementations.

**No category-specific hosts exist** (no separate sports/crypto/mentions/
politics API hosts) - this definitively resolves an earlier open question
from this project's own investigation (2026-08-15 session: "its not JUST
api.elections.kalshi.com, each category should have their own category
specific endpoint"). There is only ever one general-purpose REST host and
one general-purpose WS host per environment; `api.elections.kalshi.com` is
a legacy alias for the exact same backend, not a scoped one.

**Action taken 2026-08-15**: `config/settings.yaml`'s `kalshi.base_url` was
switched from the legacy `api.elections.kalshi.com` alias to the
recommended `external-api.kalshi.com` host, per direct instruction ("i
insist you use the default api endpoint whenever possible not this
elections one"). Verified live afterward: markets/account/exchange-status
all continued working normally against the new host, zero errors.

This file is a local copy of the fetched page content used during this
session.
