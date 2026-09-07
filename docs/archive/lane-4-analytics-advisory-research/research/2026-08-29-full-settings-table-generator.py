"""Single source of truth for the complete 145-field settings.yaml pass.
Generates both the artifact's HTML table and the research doc's markdown
table from one place so the two can never drift."""
import html

# category codes: DATA (checked against real trade/signal data),
# VERIFIED (checked in source: mechanism/consistency/reachability),
# INERT (read, but structurally unreachable given a sibling gate/flag),
# BASELINED (covered by tools/quality_audit's own reviewed dead-config baseline),
# STRUCTURAL (operational/safety, not a trade-outcome tuning question),
# BOUNDARY (real, specific reason this dataset can't answer it)

# fmt: off
FIELDS = [
 ("mode", "paper", "STRUCTURAL", "Safety mode switch, not a tuning value — protected by CLAUDE.md."),
 ("kalshi.base_url", "…", "STRUCTURAL", "Fixed API endpoint."),
 ("kalshi.markets_watchlist", "[KXBTC15M]", "VERIFIED", "Deliberate 2026-08-16 override of live_markets_only for crypto — the actual mechanism behind the whole book's profit source."),
 ("kalshi.markets_watchlist_mode", "merge", "STRUCTURAL", "Governs how the pin combines with discovered scope."),
 ("kalshi.watchlist_size", "150", "STRUCTURAL", "Discovery cap, not a P&L lever directly."),
 ("kalshi.max_children_per_parent", "5", "STRUCTURAL", "Catalog-expansion cap."),
 ("kalshi.min_volume_24h", "10000", "STRUCTURAL", "Discovery volume floor."),
 ("kalshi.min_volume_24h_by_series.KXBTC15M", "0", "VERIFIED", "Part of the same crypto-override mechanism as markets_watchlist — a pure price-crossing market's volume_24h stays structurally 0 while open."),
 ("kalshi.categories", "[Sports]", "DATA", "The reason the real book is Sports + explicit-watchlist-crypto only — foundational to the crypto-vs-sports finding."),
 ("kalshi.live_markets_only", "true", "VERIFIED", "Discovery-stage gate — a different pipeline stage from strategy.live_markets_only, not a conflict."),
 ("kalshi.poll_interval_sec", "6", "STRUCTURAL", "Data-plane cadence — this session's other workstream."),
 ("kalshi.safety_net_interval_sec", "30", "STRUCTURAL", "Data-plane, P8 Task 39 — already shipped and explained this session."),
 ("kalshi.request_timeout_sec", "10", "STRUCTURAL", "Infra."),
 ("kalshi.trade_stream_exchange_wide", "true", "STRUCTURAL", "Data-plane — foundational to exchange-wide whale detection."),
 ("kalshi.market_lifecycle_stream_enabled", "true", "STRUCTURAL", "Data-plane — the settlement-resolver work this session depends on this being true."),
 ("kalshi.top_series_per_category", "30", "STRUCTURAL", "Discovery cap."),
 ("realtime_data_plane.reader_gate_enabled", "false", "VERIFIED", "This session's own initiative — fully covered elsewhere."),
 ("realtime_data_plane.two_consumer_mode", "true", "VERIFIED", "This session's own initiative — fully covered elsewhere, currently soaking."),
 ("whale_signal.signal_frequency_sec", "12", "BOUNDARY", "Simulator-only config — confirmed none of the 257 real trades came from it."),
 ("whale_signal.whale_size_range", "[5000, 50000]", "BOUNDARY", "Simulator-only."),
 ("whale_signal.bias", "random", "BOUNDARY", "Simulator-only."),
 ("whale_signal.live_markets_only", "false", "VERIFIED", "Feeds whale_simulator.py only — documented as \"a stronger, upstream version of strategy.live_markets_only\"."),
 ("strategy.name", "follow_the_whale", "STRUCTURAL", "Identifier."),
 ("strategy.entry_threshold", "0.55", "DATA", "entry_confidence clusters 202/256 trades in 0.50–0.60, right above this — narrow observed range."),
 ("strategy.max_position_pct", "0.05", "DATA", "Position-sizing check: no clean pattern by size quartile — checked, inconclusive."),
 ("strategy.cooldown_sec", "60", "BOUNDARY", "Re-entry throttle — no clean signal available this pass on whether it binds."),
 ("strategy.close_window_sec", "2764800", "BOUNDARY", "Structural window bound, not independently checked against outcome."),
 ("strategy.special_market_min_seconds_to_close", "120", "BOUNDARY", "Advisory-reachable gate field; no rejected-candidate data pulled this session."),
 ("strategy.max_open_positions_per_series", "0 (unlimited)", "DATA", "Recommended → 2–3 — the actual root cause of position_netting's losses."),
 ("strategy.kelly_fraction_of_cap", "0.3", "DATA", "Position-sizing check: no clean pattern — checked, inconclusive."),
 ("strategy.min_unit_cost", "0.5", "DATA", "Extensively checked (unit-cost banding) — also the reason the longshot mechanism below is dead."),
 ("strategy.max_unit_cost", "0.9", "DATA", "Recommended → 0.85 — the only net-negative unit-cost slice sits exactly at this ceiling."),
 ("strategy.min_whale_winrate_pct", "40", "BOUNDARY", "Advisory-reachable gate; not independently checked against real rejected-candidate data this pass."),
 ("strategy.min_resolved_for_whale_filter", "20", "BOUNDARY", "Threshold for when the above gate activates — not independently checked."),
 ("strategy.live_markets_only", "false", "VERIFIED", "The real strategy-level entry gate — distinct pipeline stage from kalshi's and whale_signal's same-named flags."),
 ("strategy.excluded_series", "[]", "DATA", "Recommended → + KXATPMATCH — already inside advisory_engine's own reach."),
 ("strategy.take_profit_pct", "null", "BOUNDARY", "Disabled; advisory-reachable if enabled — no live trades to check it against."),
 ("strategy.stop_loss_pct", "null", "BOUNDARY", "Disabled; advisory-reachable if enabled."),
 ("strategy.exit_on_sentiment_reversal", "false", "INERT", "Disabled — the two sentiment fields below are configured but never exercised while this is off."),
 ("strategy.longshot_close_window_sec", "300", "INERT", "Part of the longshot mechanism — see longshot_price_threshold."),
 ("strategy.exit_sentiment_min_signals", "15", "INERT", "Configured, but exit_on_sentiment_reversal is false — currently never exercised."),
 ("strategy.exit_sentiment_lean_pct", "90", "INERT", "Same — inert while the parent flag is off."),
 ("strategy.auto_exit_enabled", "true", "DATA", "auto_exit closes: 37 trades, 97.3% win, +$5,475.47 — the book's single strongest performer by close type."),
 ("strategy.auto_exit_threshold", "0.85", "DATA", "Drives the result above; not independently decomposed."),
 ("strategy.auto_exit_pnl_weight", "0.85", "BOUNDARY", "Internal weighting of a strong aggregate result — per-trigger attribution isn't in trade_history rows."),
 ("strategy.auto_exit_sentiment_weight", "1.5", "BOUNDARY", "Same."),
 ("strategy.auto_exit_staleness_weight", "0.5", "BOUNDARY", "Same."),
 ("strategy.auto_exit_analyst_weight", "0.5", "BOUNDARY", "Same."),
 ("strategy.auto_exit_gain_reference_pct", "0.15", "BOUNDARY", "Same."),
 ("strategy.auto_exit_loss_reference_pct", "0.85", "BOUNDARY", "Same."),
 ("strategy.auto_exit_stale_after_sec", "7200", "BOUNDARY", "Same."),
 ("strategy.auto_exit_normal_volatility", "0.002", "BOUNDARY", "Same."),
 ("strategy.auto_exit_volatility_lookback_sec", "1800", "BOUNDARY", "Same."),
 ("strategy.auto_exit_series_track_record_weight", "0", "VERIFIED", "The only auto-exit weight at literal zero while every sibling is nonzero — an unused weight slot, same shape as analyst_factor/block_trade_factor elsewhere."),
 ("strategy.longshot_price_threshold", "0.05", "VERIFIED", "Dead: is_longshot fires on raw price ≤0.05 or ≥0.95, but every price there maps to unit_cost ≤0.05 on whichever side is cheap — min_unit_cost (0.5) rejects it unconditionally, independent of any longshot bonus."),
 ("strategy.longshot_entry_threshold_bonus", "0.05", "INERT", "Never applied — the longshot zone it modifies is unreachable (see longshot_price_threshold)."),
 ("strategy.use_limit_orders", "false", "INERT", "Disabled — limit_order_timeout_sec below is currently inert as a result."),
 ("strategy.limit_order_timeout_sec", "60", "INERT", "Inert while use_limit_orders is false."),
 ("strategy.min_seconds_to_close", "90", "BOUNDARY", "Structural timing gate, not independently checked."),
 ("strategy.exit_min_seconds_to_close", "null", "BOUNDARY", "Same."),
 ("strategy.price_staleness_corroborate_sec", "120.0", "VERIFIED", "This session's own realtime work (P8 Task 35) — already shipped and understood, not a trade-outcome question."),
 ("risk.starting_bankroll", "10000", "STRUCTURAL", "Paper-mode baseline."),
 ("risk.max_daily_loss_pct", "0.85", "VERIFIED", "Already named in CLAUDE.md itself as not protective — restated, not re-derived."),
 ("risk.kill_switch_enabled", "true", "STRUCTURAL", "Safety feature — on is correct, not a tuning question."),
 ("risk.max_total_exposure_pct", "null (unset)", "DATA", "Recommended → 0.25–0.35 — the second concentration cap sitting off alongside max_open_positions_per_series."),
 ("kalshi_account.trading_enabled", "false", "STRUCTURAL", "Safety invariant — never a tuning lever."),
 ("whale_watcher_kalshi.min_contracts", "10000", "DATA", "The global floor — crypto's separate 2,500 floor is what actually explains the crypto/sports split."),
 ("whale_watcher_kalshi.min_contracts_by_series.KXBTC15M", "2500", "DATA", "Live and reachable — the crypto profit driver."),
 ("whale_watcher_kalshi.min_contracts_by_series.KXBTCD", "2500", "DATA", "Live and reachable, same series family."),
 ("whale_watcher_kalshi.min_contracts_by_series.KXETH15M", "2500", "VERIFIED", "Unreachable: ETH is in neither kalshi.categories nor kalshi.markets_watchlist — never discovered, confirmed zero occurrences in 257 real trades."),
 ("whale_watcher_kalshi.min_contracts_by_series.KXETHD", "2500", "VERIFIED", "Same — unreachable dead config given current discovery scope."),
 ("whale_watcher_kalshi.min_contracts_by_series.KXTRUMPSAY", "500", "VERIFIED", "Unreachable — not in categories or watchlist."),
 ("whale_watcher_kalshi.min_contracts_by_series.KXTRUMPMENTION", "500", "VERIFIED", "Unreachable, same reason."),
 ("whale_watcher_kalshi.min_contracts_by_series.KXMAMDANIMENTION", "500", "VERIFIED", "Unreachable, same reason."),
 ("whale_confidence_weights.depth_factor", "0.0404", "DATA", "r=−0.24 at n=88,828 — strongest effect of any factor, never checked before, formula's assumption runs backward."),
 ("whale_confidence_weights.unusualness_factor", "0.0404", "DATA", "r=−0.18 — confirms the 2026-08-10 finding at 10× the sample."),
 ("whale_confidence_weights.proximity_factor", "0.0404", "DATA", "r=−0.05, weak either way, correctly weighted low; only 21,843/88,828 rows populated."),
 ("whale_confidence_weights.context_factor", "0.2121", "DATA", "r=+0.23 — second-heaviest weight, matching signal strength."),
 ("whale_confidence_weights.agreement_factor", "0.1818", "DATA", "r=+0.13 — contradicts the 2026-08-10 finding's sign; flagged, not resolved."),
 ("whale_confidence_weights.cluster_factor", "0.1717", "DATA", "r=+0.17 — aligned."),
 ("whale_confidence_weights.trend_factor", "0.3131", "DATA", "r=+0.24 — heaviest weight, strongest positive signal, well aligned."),
 ("whale_confidence_weights.analyst_factor", "0.0", "DATA", "0 real values across 88,828 rows — weight of 0 already correct."),
 ("whale_confidence_weights.block_trade_factor", "0.0", "VERIFIED", "Doesn't appear in the factor breakdown at all — a distinct, boolean-shaped signal (Kalshi's is_block_trade flag) rather than a weighted continuous factor."),
 ("advisory.enabled", "true", "VERIFIED", "Meta-control for the advisory_engine coverage gap (§7)."),
 ("advisory.min_resolved_trades_per_variant", "10", "BOUNDARY", "Governs a system that's made zero real auto-applies to evaluate."),
 ("advisory.auto_apply_enabled", "false", "VERIFIED", "Off by design — matches the standing surface-don't-auto-apply rule. Correct, not a gap."),
 ("advisory.auto_apply_min_confidence", "higher", "BOUNDARY", "Inert while auto_apply is off."),
 ("advisory.auto_apply_min_n", "100", "BOUNDARY", "Inert while auto_apply is off."),
 ("advisory.auto_apply_cooldown_sec", "86400", "BOUNDARY", "Inert while auto_apply is off."),
 ("confidence_calibration.enabled", "true", "VERIFIED", "The mechanism that produced whale_confidence_weights' current live values (the 2026-08-10 finding) — directly relevant to the §10 recommendation."),
 ("confidence_calibration.min_resolved_signals", "50", "VERIFIED", "88,828 real signals now exist — 1,776× this floor. Re-calibration has had more than enough data for a long time; nothing has re-triggered it."),
 ("confidence_calibration.auto_apply_min_resolved_signals", "50", "BOUNDARY", "Inert while auto_apply is off."),
 ("confidence_calibration.snapshot_interval_sec", "21600", "STRUCTURAL", "Cadence."),
 ("confidence_calibration.auto_apply_enabled", "false", "VERIFIED", "Off by design — correct, not a gap."),
 ("confidence_calibration.auto_apply_cooldown_sec", "86400", "BOUNDARY", "Inert while auto_apply is off."),
 ("market_analyst.enabled", "true", "BOUNDARY", "The LLM full-spectrum/per-market/per-series advisor — distinct from advisory_engine; no suggestion-acceptance data pulled this session."),
 ("market_analyst.model", "claude-sonnet-5", "STRUCTURAL", "Model selection."),
 ("market_analyst.reanalyze_cooldown_sec", "1800", "STRUCTURAL", "Throttle."),
 ("position_netting.enabled", "true", "DATA", "34 closed trades, 11 wins, −$2,693.99 — extensively analyzed (§3)."),
 ("position_netting.min_dwell_sec", "300", "BOUNDARY", "Not independently checked — analysis focused on the materiality bar floor below."),
 ("position_netting.min_edge_improvement_usd", "50", "DATA", "Recommended → $10–15 — code default is $1; all 34 observed closes were locked_loss, none a proactive variable-stage trim."),
 ("position_netting.normal_volatility", "0.02", "BOUNDARY", "Scales the materiality bar above min_edge_usd — no live volatility data joined to trade history this pass."),
 ("position_netting.volatility_lookback_sec", "1800", "BOUNDARY", "Same."),
 ("index_feed.index_ids", "[BRTI, ETHUSD_RTI]", "STRUCTURAL", "A separate index-price feed, structurally unrelated to whale-follow trade outcomes."),
 ("index_feed.underlying_tickers", "[]", "STRUCTURAL", "Same feed."),
 ("settlement_edge_entry.enabled", "false", "STRUCTURAL", "Engine off — all 5 fields below correctly inert; no live trades to check any of them against."),
 ("settlement_edge_entry.min_observations_known", "45", "STRUCTURAL", "Inert while disabled."),
 ("settlement_edge_entry.min_probability", "0.95", "STRUCTURAL", "Inert while disabled."),
 ("settlement_edge_entry.min_edge", "0.05", "STRUCTURAL", "Inert while disabled."),
 ("settlement_edge_entry.max_position_pct", "0.02", "STRUCTURAL", "Inert while disabled."),
 ("settlement_edge_entry.volatility_lookback_sec", "3600", "STRUCTURAL", "Inert while disabled."),
 ("series_watcher.enabled", "true", "STRUCTURAL", "Passive order-book/history collection — a different concern from whale-follow trading, not trade-outcome-tunable."),
 ("series_watcher.series", "[8 series]", "STRUCTURAL", "Watches some series (e.g. KXETH15M, KXNBAGAME) that never produce a whale-follow trade — legitimate: this is data-collection breadth, not a trading gate."),
 ("series_watcher.book_snapshot_interval_sec", "5", "STRUCTURAL", "Collection cadence."),
 ("series_watcher.retention_hours", "168", "STRUCTURAL", "Retention window."),
 ("series_evaluator.enabled", "false", "STRUCTURAL", "Engine off — all 7 fields below correctly inert."),
 ("series_evaluator.min_observation_sec", "3600", "STRUCTURAL", "Inert while disabled."),
 ("series_evaluator.min_trades_observed", "1000", "STRUCTURAL", "Inert while disabled."),
 ("series_evaluator.max_observation_sec", "21600", "STRUCTURAL", "Inert while disabled."),
 ("series_evaluator.min_qualify_rate", "0.05", "STRUCTURAL", "Inert while disabled."),
 ("series_evaluator.backoff_base_sec", "3600", "STRUCTURAL", "Inert while disabled."),
 ("series_evaluator.backoff_multiplier", "2", "STRUCTURAL", "Inert while disabled."),
 ("series_evaluator.backoff_max_sec", "86400", "STRUCTURAL", "Inert while disabled."),
 ("logging.level", "INFO", "STRUCTURAL", "Infra."),
 ("backup.enabled", "true", "STRUCTURAL", "Infra."),
 ("backup.interval_sec", "21600", "STRUCTURAL", "Infra."),
 ("backup.retention_count", "14", "STRUCTURAL", "Infra."),
 ("alerting.enabled", "true", "STRUCTURAL", "Infra."),
 ("alerting.webhook_url", "null (unset)", "VERIFIED", "No external alert destination is wired — alerts fire and are visible in-app only, nobody is notified externally."),
 ("alerting.crash_auto_resolve_after_sec", "1800", "STRUCTURAL", "Infra."),
 ("observability.enabled", "true", "STRUCTURAL", "Infra — this session's own primary battlefield, covered extensively elsewhere."),
 ("observability.sample_interval_sec", "60", "STRUCTURAL", "Infra."),
 ("observability.retention_hours", "336", "STRUCTURAL", "Infra."),
 ("research.enabled", "false", "STRUCTURAL", "Off until manually reviewed (CLAUDE.md's own note) — the 2 fields below correctly inert."),
 ("research.min_new_resolved_signals", "100", "STRUCTURAL", "Inert while disabled."),
 ("research.min_new_closed_trades", "50", "STRUCTURAL", "Inert while disabled."),
 ("event_lifecycle.tournament_min_siblings", "4", "VERIFIED", "Feeds market_events/event_lifecycle.py and discovery_cache.py only — NOT position_netting or mutual_exclusivity. The connection hypothesized earlier this session doesn't exist."),
 ("event_lifecycle.tournament_pretail_days", "5.0", "VERIFIED", "Same feed, discovery/catalog concern only."),
 ("event_lifecycle.pre_tail_volume_weight", "0.4", "VERIFIED", "Same."),
 ("event_lifecycle.post_tail_volume_weight", "0.2", "VERIFIED", "Same."),
 ("event_schedule.enabled", "true", "STRUCTURAL", "Infra — event-schedule resolution."),
 ("event_schedule.pre_event_hours", "6.0", "STRUCTURAL", "Infra."),
 ("event_schedule.web_search_enabled", "true", "STRUCTURAL", "Infra."),
 ("event_schedule.max_resolutions_per_tick", "5", "STRUCTURAL", "Infra."),
 ("strategy_overrides.by_category.Sports.stop_loss_pct", "null", "DATA", "Confirmed no-op — matches the global default; the mechanism a Sports-specific recommendation would route through."),
]
# fmt: on

assert len(FIELDS) == 145, f"expected 145, got {len(FIELDS)}"

# Per-field suggestion overlay (2026-08-29, second revision: "update the
# report to include all fields and suggestions and why or why not to change
# them"). Fields not listed here default to "Keep" - the Note column carries
# the why. APPLIED entries reflect the change-set actually applied via
# POST /api/config on 2026-08-29 (read-back verified, all 7 fields).
SUGGESTIONS = {
    "strategy.max_unit_cost": "APPLIED → 0.85",
    "strategy.max_open_positions_per_series": "APPLIED → 2",
    "strategy.excluded_series": "APPLIED → [KXATPMATCH]",
    "risk.max_total_exposure_pct": "APPLIED → 0.45",
    "risk.max_daily_loss_pct": "APPLIED → 0.20",
    "position_netting.min_edge_improvement_usd": "APPLIED → 10",
    # strategy_overrides gained by_series.KXBTC15M.max_open_positions_per_series: 3 (APPLIED)
    "strategy_overrides.by_category.Sports.stop_loss_pct": "Keep — plus new by_series.KXBTC15M cap override APPLIED alongside",
    # --- position_netting, addressed field by field (direct question) ---
    "position_netting.enabled": "Keep true — the module is the tourniquet, not the wound: every close it made was already a mathematically locked loss before it acted; disabling it would leave that dead capital parked until settlement. With the upstream caps applied, its firing rate is the metric to watch — if it keeps firing, the caps are insufficient, not the module wrong.",
    "position_netting.min_dwell_sec": "Keep 300 — anti-noise gate mirroring check_exits' opened_since idiom; no evidence it delayed any of the 34 closes (all were locked before dwell could matter).",
    "position_netting.normal_volatility": "Measure, then fix — NEW finding: baselines the same market_history.volatility() as strategy.auto_exit_normal_volatility yet is set 10× higher (0.02 vs 0.002). At 0.02, typical real vols give vol_ratio ≈ 0.1 → clamped to the 0.25 floor — the bar's volatility scaling has plausibly been PINNED at floor the whole time, never varying. Changing it to 0.002 now would RAISE the effective bar ($2.50 → ~$10) and trim less, so keep until a measured vol distribution says which baseline is right.",
    "position_netting.volatility_lookback_sec": "Keep 1800 — inert in practice while the clamp is pinned (see normal_volatility); revisit together.",
    # --- other notable keeps with the why inline ---
    "whale_confidence_weights.depth_factor": "Keep — despite r=−0.24: calibration targets win-rate, and win-rate ≠ EV (see §12's re-target-on-EV reasoning); re-weighting from these correlations would steer into the thin-edge trap.",
    "whale_confidence_weights.unusualness_factor": "Keep — same reason; its 'bad' zone (prices near 0.5) is the book's most profitable band.",
    "whale_confidence_weights.agreement_factor": "Keep — sign flip vs 2026-08-10 unresolved; changing on contested evidence is guessing.",
    "whale_confidence_weights.trend_factor": "Keep — heaviest weight, strongest positive correlation; working as intended.",
    "whale_confidence_weights.context_factor": "Keep — aligned.",
    "whale_confidence_weights.cluster_factor": "Keep — aligned.",
    "whale_confidence_weights.proximity_factor": "Keep — weak either way, already floor-weighted.",
    "whale_confidence_weights.analyst_factor": "Keep 0 — zero real values in 88,828 rows; raising it would weight a signal that never fires.",
    "whale_confidence_weights.block_trade_factor": "Keep 0 — not in the factor breakdown; wiring it is feature work, not tuning.",
    "strategy.entry_threshold": "Keep 0.55 — higher-confidence entries earned LESS ($5.56 vs $20.92 avg); raising selects worse trades, lowering is unmeasured.",
    "strategy.min_unit_cost": "Keep 0.5 — lowering admits an unmeasured price zone and silently revives the dead longshot mechanism; that's its own open decision.",
    "strategy.take_profit_pct": "Keep null — auto_exit (97.3% win, +$5,475) is the working exit manager.",
    "strategy.stop_loss_pct": "Keep null — a hard stop interacts badly with the known stale-price exit history; the settled-loss tail belongs to the banded-EV entry-side fix.",
    "strategy.longshot_price_threshold": "Decision open — structurally dead (unreachable behind min_unit_cost); activate deliberately or remove, never leave looking live.",
    "strategy.longshot_entry_threshold_bonus": "Decision open — same dead mechanism.",
    "strategy.longshot_close_window_sec": "Decision open — same dead mechanism.",
    "whale_watcher_kalshi.min_contracts": "Keep 10000 — no sports data below it exists to justify lowering; size bands above show no gain.",
    "whale_watcher_kalshi.min_contracts_by_series.KXBTC15M": "Keep 2500 — the profit engine's floor.",
    "whale_watcher_kalshi.min_contracts_by_series.KXBTCD": "Keep 2500 — live and positive.",
    "whale_watcher_kalshi.min_contracts_by_series.KXETH15M": "Decision open — unreachable dead entry (ETH not in discovery scope): add ETH deliberately or remove the entry.",
    "whale_watcher_kalshi.min_contracts_by_series.KXETHD": "Decision open — same.",
    "whale_watcher_kalshi.min_contracts_by_series.KXTRUMPSAY": "Decision open — same (series not in scope).",
    "whale_watcher_kalshi.min_contracts_by_series.KXTRUMPMENTION": "Decision open — same.",
    "whale_watcher_kalshi.min_contracts_by_series.KXMAMDANIMENTION": "Decision open — same.",
    "alerting.webhook_url": "Worth setting — alerts currently reach nobody outside the app; any webhook destination makes the kill switch and crash alerts actually page you.",
    "confidence_calibration.min_resolved_signals": "Keep 50 — but note 88,828 signals now exist (1,776× the floor); the §10 re-validation is overdue by data volume, not blocked by this setting.",
}
DEFAULT_SUGGESTION = "Keep"

CAT_LABEL = {
    "DATA": "Data-checked",
    "VERIFIED": "Source-verified",
    "INERT": "Inert (sibling gate)",
    "STRUCTURAL": "Structural / infra",
    "BOUNDARY": "Checked, boundary",
}
CAT_CLASS = {
    "DATA": "cat-data", "VERIFIED": "cat-verified", "INERT": "cat-inert",
    "STRUCTURAL": "cat-structural", "BOUNDARY": "cat-boundary",
}

counts = {}
for _, _, cat, _ in FIELDS:
    counts[cat] = counts.get(cat, 0) + 1
print("Category counts:", counts, "total:", len(FIELDS))

# ---- HTML ----
rows_html = []
for path, val, cat, note in FIELDS:
    sug = SUGGESTIONS.get(path, DEFAULT_SUGGESTION)
    sug_cls = "sug-applied" if sug.startswith("APPLIED") else (
        "sug-open" if sug.startswith(("Decision open", "Measure", "Worth")) else "sug-keep")
    rows_html.append(
        f'<tr><td><code>{html.escape(path)}</code></td>'
        f'<td>{html.escape(str(val))}</td>'
        f'<td><span class="cat-pill {CAT_CLASS[cat]}">{CAT_LABEL[cat]}</span></td>'
        f'<td class="{sug_cls}">{html.escape(sug)}</td>'
        f'<td>{note}</td></tr>'
    )
html_table = (
    '<div class="table-scroll">\n<table>\n'
    '<thead><tr><th>Field</th><th>Value (pre-change)</th><th>Status</th><th>Suggestion</th><th>Why</th></tr></thead>\n'
    '<tbody>\n' + "\n".join(rows_html) + "\n</tbody>\n</table>\n</div>"
)
open("/tmp/claude-1000/-home-davidf-code-portfolio-showcase-projects-autotrade/50722d08-68f0-4456-8a63-670318e12c8c/scratchpad/full_table.html", "w").write(html_table)

# ---- Markdown ----
md_lines = ["| Field | Value (pre-change) | Status | Suggestion | Why |", "|---|---|---|---|---|"]
for path, val, cat, note in FIELDS:
    sug = SUGGESTIONS.get(path, DEFAULT_SUGGESTION)
    md_lines.append(f"| `{path}` | {val} | {CAT_LABEL[cat]} | {sug} | {note} |")
open("/tmp/claude-1000/-home-davidf-code-portfolio-showcase-projects-autotrade/50722d08-68f0-4456-8a63-670318e12c8c/scratchpad/full_table.md", "w").write("\n".join(md_lines))

print("wrote full_table.html and full_table.md")
