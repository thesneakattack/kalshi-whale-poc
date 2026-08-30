# Next action

**Restart boundary done** (2026-08-30 16:11 UTC): `origin/main` merged into
the primary, `ddev restart`, verified live — `/api/health/pipeline` 41-131s
blocking → 0.375s. Every fix from the 2026-08-30 audit (#205-#214, #71/#72,
#211, #229/#233/#234, #232/#240/#242, #251/#253) is now running.

Run `python -m tools.soak_analyzer` (add `--json` for machine output) at
each boundary — it is the pass/fail mechanism, not hand-checking. First
post-restart read: **8 of 10 checks PASS.** All 6 data-layer checks clean
(0 drops, ticker conservation exact at 0 gap, staleness metric corroborated,
queue headroom, resolver accounting). The 2 FAILs
(`capture_writer_health`, `exit_engine_faults`) are both explained: every
fault behind them is timestamped *before* 16:11:20 UTC — zero of either kind
occurred after the restart. They live in the 24h fault-log window and will
age out on their own; re-run in a few hours and again the next day to
confirm no new ones accumulate.

**Next check:** run the analyzer again a few hours from now, then once more
the next day. If both stay clean, close `docs/open-decisions.md`'s item (3)
— `two_consumer_mode` permanence — by updating the file comment in
`config/settings.yaml` to say so.

Layer contract behind the tool: `docs/data-layer-analysis-layer-contract.md`.
Full audit history if picking this up cold:
`docs/superpowers/research/2026-08-30-test-coverage-audit-handoff.md`.
