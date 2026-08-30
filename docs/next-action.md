# Next action

**Second restart boundary done** (2026-08-30 ~20:19 UTC): 4 PRs (#272
account diagnostics, #273 historical backfill tool, #274 index_feed
reconnect backfill, #275 MVE combo-event discovery) plus a docs-only spec
(#263) merged to `main`, then `origin/main` merged into the primary and
`ddev restart`. Verified, not assumed: `/api/health/pipeline` responds in
0.39s; `python -m tools.soak_analyzer` — 8 of 10 PASS again, all 6
data-layer checks clean. The same 2 FAILs as the first boundary
(`capture_writer_health`, `exit_engine_faults`) are still open, but every
fault behind both has `last_seen` *before* this restart's timestamp — zero
new occurrences of either kind since either restart. They remain in the
24h fault-log window (`data/fault_log.db`, disk-persisted, ages out on wall
clock regardless of process restarts) and should be fully gone by
~2026-08-31 16:11 UTC.

**MVE discovery (#268) verified live, not just merged:** `mve_scan` ran
within 31s of restart and populated 14 `KXMVECROSSCATEGORY*`/`-SHARD1` rows
in `market_catalog.db` with fresh `updated_at`; `title_cache.db`'s
`event_titles` now carries a real `mutually_exclusive` value (not the old
silent `False` fallback) for events that previously had zero catalog rows.
Closes PR #275's own pending post-merge verification item.

**Next check:** re-run `python -m tools.soak_analyzer` around 2026-08-31
16:11 UTC (24h past the *first* restart) to confirm both FAILs have aged
out with zero new faults accumulated across both restarts. If clean, close
`docs/open-decisions.md`'s item (3) — `two_consumer_mode` permanence — by
updating the file comment in `config/settings.yaml` to say so.

**Parked, needs your read:** `docs/superpowers/specs/2026-08-30-weather-index-ingestion-design.md`
(PR #263, merged as docs-only) is an architectural brainstorm for
temperature-market settlement-edge ingestion via Kalshi's new
`GET /live_data/weather/{city}`. Per the brainstorming skill's own hard
gate, it stops at the spec — no plan, no code — until you've reviewed it;
opens a new market category so it's a real decision, not a routine one.

Layer contract behind the tool: `docs/data-layer-analysis-layer-contract.md`.
Full audit history if picking this up cold:
`docs/superpowers/research/2026-08-30-test-coverage-audit-handoff.md`.
