# Next action

**The primary checkout is 66+ commits behind `main` and merging is paused by
direct instruction** ("I don't want that merge main to happen yet"). No
session currently owns the primary. Do not merge `origin/main` into it or
restart the live process without a fresh go-ahead — that decision, and the
two that follow it, are recorded as PAUSED / SEQUENCED in
`docs/open-decisions.md`, which every session prints. Read that entry before
touching the primary.

The soak-session's earlier next-action content (checking `two_consumer_mode`
boundaries by hand) is superseded: `tools/soak_analyzer.py` replaces
hand-checking, and every gap it was written to catch — the blind staleness
metric, the conflated settlement-drop counter, the capture_writer batch
drops, the reconnect-discard gap in ticker conservation — is fixed on `main`
as of 2026-08-30 (issues #205-#214, #71/#72, #211, #229/#233/#234, #232/#240/
#242). None of those fixes are live yet; they take effect at the restart in
`docs/open-decisions.md`'s item (1).

**When un-paused:** follow `docs/open-decisions.md`'s SEQUENCED entry,
items (1)-(3), in order.

**Until then:** the reader-side follow-ups from the 2026-08-30 audit are
real, unblocked work — `docs/kalshi/` is current again (Trade API 3.29.0),
and issues #251 (`exchange_index` missing from fill readers), #252 (balance
aggregation semantics changed — needs your decision, see open-decisions.md),
#253 (fee-rounding precision stale) are open and don't touch the primary.
