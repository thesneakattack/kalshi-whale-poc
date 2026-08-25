"""market_lifecycle_v2 WS channel semantics — Phase A Task A10.

Semantics owned:

- The determined/finalized/settled distinction (docs/kalshi/
  market_lifecycle.md + market-and-event-lifecycle.md, cross-checked
  2026-08-23 against thousands of real captured events): `determined` is
  NOT terminal — "the result may be disputed" during the settlement-timer
  window and can flip via determined -> disputed -> amended before
  "finalized" ("Settlement complete... Terminal state"). Resolving an
  outcome at `determined` graded whale signals/analyst calls/candidate
  rows against a potentially-wrong result with no correction path (real
  shipped bug, fixed 2026-08-23 — services/exits/CHEATSHEET.md). Only
  `settled` may trigger resolution, and even then the WS payload itself
  carries NO result field (confirmed against real traffic), so resolution
  requires a fresh single-ticker REST read re-checked for
  status == TERMINAL_REST_STATUS before trusting its result.
- market_ticker -> ticker alias + raw-payload pass-through, same rule as
  every normalizer here.

Catalog-status mapping and the resolver orchestration remain application
concerns (services/market_catalog/, the stream handlers) until consumer
migration (A13/A14).
"""
from __future__ import annotations

from services.kalshi.provenance import ContractDocs

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "normalize_lifecycle": ("docs/kalshi/market-and-event-lifecycle.md",),
    "resolves_outcome": (
        "docs/kalshi/market-and-event-lifecycle.md",
        "docs/kalshi/market_lifecycle.md",
    ),
}

# The only REST `status` value that means settlement is complete and the
# result can no longer flip - see module docstring.
TERMINAL_REST_STATUS = "finalized"

# The one lifecycle event type allowed to trigger outcome resolution -
# and only via a fresh REST read gated on TERMINAL_REST_STATUS, because
# the settled WS payload itself carries no result field.
_RESOLVING_EVENT_TYPES = frozenset({"settled"})


def resolves_outcome(event_type: str | None) -> bool:
    """Whether this lifecycle event type may trigger outcome resolution.
    False for `determined` on purpose — see module docstring."""
    return event_type in _RESOLVING_EVENT_TYPES


def normalize_lifecycle(msg: dict) -> dict:
    return {
        **msg,
        "ticker": msg.get("market_ticker") or msg.get("ticker"),
    }
