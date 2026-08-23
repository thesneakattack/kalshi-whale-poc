"""
Live CF Benchmarks / Pyth index feed - the actual quantity several Kalshi
series settle against, streamed over websocket instead of inferred from
contract prices, plus the settlement-projection math built on top of it.

Split 2026-08-23 (per-module audit) from one flat services/index_feed.py
into cohesion-based siblings, the same shape as the earlier market_watch/
split:

- ingestion.py - tick capture and storage (record_cfbenchmarks/record_pyth,
  the buffered-write DB layer, latest()/snapshot()/tick_stats()).
- settlement_algebra.py - the settlement-projection math built on top
  (required_remaining_average/settlement_projection/settlement_spec/
  resolve_index_id/window_matches_close/recent_volatility). Reads
  ingestion's live state through its public latest()/_connect(), not by
  reaching into ingestion's module globals directly.

Every name external callers need is re-exported here, so
`from services import index_feed` / `from services.index_feed import X`
keep working exactly as before the split - no call site outside this
package needed to change.

REAL GOTCHA, worth reading before touching this package's own tests: the
re-exports below (`from services.index_feed.ingestion import DB_PATH,
_latest, ...`) are name *bindings*, not live references back to
ingestion.py's own module namespace. A function defined in ingestion.py
(record_cfbenchmarks, flush, ...) always resolves its own global lookups
(DB_PATH, _latest, _tick_buffer, _dropped_rows) against ingestion.py's
module dict, regardless of which import path was used to *call* it - so
`monkeypatch.setattr(index_feed, "DB_PATH", tmp_path)` (patching this
package's own copied binding) does NOT change what those functions
actually read/write; only `monkeypatch.setattr(index_feed.ingestion,
"DB_PATH", tmp_path)` does. This is exactly the shape of bug this
session's own tests/conftest.py fix and phase-137 incident were about -
confirmed directly against this package, not assumed: tests/test_index_
feed.py and tests/test_settlement_edge.py both used to monkeypatch the
former (harmless only because nothing in this app's test suite happened
to exercise the code paths that would have exposed it), fixed to target
`ingestion` directly as part of this split.
"""
from services.index_feed import ingestion  # noqa: F401
from services.index_feed.ingestion import (  # noqa: F401
    DB_PATH, DEFAULT_INDEX_IDS, flush, latest, prune, record_cfbenchmarks, record_pyth, snapshot,
    tick_stats,
)
from services.index_feed.settlement_algebra import (  # noqa: F401
    SETTLEMENT_WINDOW_TICKS, _INDEX_ALIASES, _normalise_index_token, _SIXTY_SECOND_PATTERNS,
    recent_volatility, required_remaining_average, resolve_index_id, settlement_projection, settlement_spec,
    window_matches_close,
)
