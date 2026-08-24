import { loadChangeHistory } from './advisory-calibration.js';
import { lastWhaleSource, refresh, updateWhaleProviderStatus } from './polling-and-websocket.js';
import { closeHelp, closeMarketDetail, marketDetailTicker } from './screener-and-header.js';
import { $, esc, fetchJSON, marketLabel, marketTitles } from './shared-utils.js';
import { scheduleRefreshTimer } from './trading-gate-and-connectivity.js';

// Part of index.html's JS split - see shared-utils.js's header for the
// load-order/shared-global-scope rationale common to all these files.
// This file: Config tab - loadConfig() (also drives the poll interval and
// the Markets tab's search/watchlist controls) and the strategy-overrides
// editor (by_category/by_series), plus loadSession().
async function loadConfig() {
  const cfg = await fetchJSON('/api/config');
  $('cfg-mode').value = cfg.mode || 'paper';
  $('cfg-threshold').value = cfg.strategy.entry_threshold;
  $('cfg-longshot-zone').value = cfg.strategy.longshot_price_threshold ?? 0.15;
  $('cfg-longshot-bonus').value = cfg.strategy.longshot_entry_threshold_bonus ?? 0.15;
  $('cfg-position-pct').value = cfg.strategy.max_position_pct;
  $('cfg-cooldown').value = cfg.strategy.cooldown_sec;
  $('cfg-max-open-per-series').value = cfg.strategy.max_open_positions_per_series ?? '';
  $('cfg-kelly-fraction').value = cfg.strategy.kelly_fraction_of_cap ?? 0;
  $('cfg-use-limit-orders').checked = !!cfg.strategy.use_limit_orders;
  $('cfg-limit-order-timeout').value = cfg.strategy.limit_order_timeout_sec ?? 60;
  $('cfg-min-unit-cost').value = cfg.strategy.min_unit_cost ?? 0;
  $('cfg-max-unit-cost').value = cfg.strategy.max_unit_cost ?? 1;
  $('cfg-close-window').value = cfg.strategy.close_window_sec ?? '';
  $('cfg-min-seconds-to-close').value = cfg.strategy.min_seconds_to_close ?? '';
  $('cfg-special-market-min-sec').value = cfg.strategy.special_market_min_seconds_to_close ?? 300;
  $('cfg-longshot-close-window').value = cfg.strategy.longshot_close_window_sec ?? 900;
  $('cfg-min-winrate').value = cfg.strategy.min_whale_winrate_pct;
  $('cfg-min-resolved').value = cfg.strategy.min_resolved_for_whale_filter;
  $('cfg-live-only').checked = !!cfg.strategy.live_markets_only;
  $('cfg-excluded-series').value = (cfg.strategy.excluded_series || []).join(', ');
  $('cfg-take-profit').value = cfg.strategy.take_profit_pct ?? '';
  $('cfg-stop-loss').value = cfg.strategy.stop_loss_pct ?? '';
  $('cfg-exit-min-seconds-to-close').value = cfg.strategy.exit_min_seconds_to_close ?? '';
  $('cfg-exit-sentiment-reversal').checked = !!cfg.strategy.exit_on_sentiment_reversal;
  $('cfg-exit-sentiment-min-signals').value = cfg.strategy.exit_sentiment_min_signals ?? 3;
  $('cfg-exit-sentiment-lean-pct').value = cfg.strategy.exit_sentiment_lean_pct ?? 65;
  $('cfg-auto-exit-enabled').checked = !!cfg.strategy.auto_exit_enabled;
  $('cfg-auto-exit-threshold').value = cfg.strategy.auto_exit_threshold ?? 0.6;
  $('cfg-auto-exit-pnl-weight').value = cfg.strategy.auto_exit_pnl_weight ?? 1.0;
  $('cfg-auto-exit-sentiment-weight').value = cfg.strategy.auto_exit_sentiment_weight ?? 1.0;
  $('cfg-auto-exit-staleness-weight').value = cfg.strategy.auto_exit_staleness_weight ?? 0.5;
  $('cfg-auto-exit-analyst-weight').value = cfg.strategy.auto_exit_analyst_weight ?? 0.5;
  $('cfg-auto-exit-gain-ref').value = cfg.strategy.auto_exit_gain_reference_pct ?? 0.5;
  $('cfg-auto-exit-loss-ref').value = cfg.strategy.auto_exit_loss_reference_pct ?? 0.3;
  $('cfg-auto-exit-stale-sec').value = cfg.strategy.auto_exit_stale_after_sec ?? 1800;
  $('cfg-auto-exit-track-record-weight').value = cfg.strategy.auto_exit_series_track_record_weight ?? 0;
  $('cfg-auto-exit-normal-vol').value = cfg.strategy.auto_exit_normal_volatility ?? '';
  $('cfg-auto-exit-vol-lookback').value = cfg.strategy.auto_exit_volatility_lookback_sec ?? 1800;
  const netCfg = cfg.position_netting || {};
  $('cfg-netting-enabled').checked = !!netCfg.enabled;
  $('cfg-netting-min-dwell').value = netCfg.min_dwell_sec ?? 300;
  $('cfg-netting-min-edge').value = netCfg.min_edge_improvement_usd ?? 1.0;
  $('cfg-netting-normal-vol').value = netCfg.normal_volatility ?? '';
  $('cfg-netting-vol-lookback').value = netCfg.volatility_lookback_sec ?? 1800;
  $('cfg-advisory-enabled').checked = !!(cfg.advisory && cfg.advisory.enabled);
  $('cfg-advisory-min-resolved').value = (cfg.advisory && cfg.advisory.min_resolved_trades_per_variant) ?? 30;
  $('cfg-advisory-auto-apply-min-n').value = (cfg.advisory && cfg.advisory.auto_apply_min_n) ?? 25;
  const ccCfg = cfg.confidence_calibration || {};
  $('cfg-calibration-enabled').checked = !!ccCfg.enabled;
  $('cfg-calibration-min-resolved').value = ccCfg.min_resolved_signals ?? 50;
  $('cfg-calibration-auto-apply-min-resolved').value = ccCfg.auto_apply_min_resolved_signals ?? 150;
  const marketAnalystCfg = cfg.market_analyst || {};
  $('cfg-market-analyst-enabled').checked = !!marketAnalystCfg.enabled;
  $('cfg-market-analyst-model').value = marketAnalystCfg.model ?? 'claude-sonnet-5';
  $('cfg-market-analyst-cooldown').value = marketAnalystCfg.reanalyze_cooldown_sec ?? 1800;
  $('cfg-freq').value = cfg.whale_signal.signal_frequency_sec;
  $('cfg-size-min').value = cfg.whale_signal.whale_size_range[0];
  $('cfg-size-max').value = cfg.whale_signal.whale_size_range[1];
  $('cfg-whale-live-only').checked = !!cfg.whale_signal.live_markets_only;
  $('cfg-whale-min-contracts').value = (cfg.whale_watcher_kalshi || {}).min_contracts ?? 5000;
  $('cfg-whale-min-contracts-by-series').value = Object.entries((cfg.whale_watcher_kalshi || {}).min_contracts_by_series || {})
    .map(([series, amount]) => `${series}:${amount}`).join(', ');
  updateWhaleProviderStatus(lastWhaleSource);
  const pollIntervalMs = ((cfg.kalshi && cfg.kalshi.poll_interval_sec) || 15) * 1000;
  scheduleRefreshTimer(pollIntervalMs);
  const seCfg = cfg.series_evaluator || {};
  $('cfg-series-evaluator-enabled').checked = !!seCfg.enabled;
  $('cfg-series-evaluator-min-observation-sec').value = seCfg.min_observation_sec ?? 3600;
  $('cfg-series-evaluator-min-trades-observed').value = seCfg.min_trades_observed ?? 20;
  $('cfg-series-evaluator-max-observation-sec').value = seCfg.max_observation_sec ?? 21600;
  $('cfg-series-evaluator-min-qualify-rate').value = seCfg.min_qualify_rate ?? 0.01;
  $('cfg-series-evaluator-backoff-base-sec').value = seCfg.backoff_base_sec ?? 1800;
  $('cfg-series-evaluator-backoff-multiplier').value = seCfg.backoff_multiplier ?? 2.0;
  $('cfg-series-evaluator-backoff-max-sec').value = seCfg.backoff_max_sec ?? 86400;
  $('cfg-max-loss').value = cfg.risk.max_daily_loss_pct;
  $('cfg-min-volume').value = cfg.kalshi.min_volume_24h ?? 1;
  $('cfg-categories').value = (cfg.kalshi.categories || []).join(', ');
  $('cfg-discovery-live-only').checked = !!cfg.kalshi.live_markets_only;
  $('cfg-watchlist-mode').value = cfg.kalshi.markets_watchlist_mode || 'merge';
  $('cfg-max-children-per-parent').value = cfg.kalshi.max_children_per_parent ?? '';
  if (!$('market-search-category').value) $('market-search-category').value = (cfg.kalshi.categories || [])[0] || '';
  renderPinnedWatchlist(cfg.kalshi.markets_watchlist || []);
  renderStrategyOverrides(cfg.strategy_overrides || {});
}

$('toggle-btn').addEventListener('click', async () => { await fetchJSON('/api/toggle', {method:'POST'}); refresh(); });
$('reset-btn').addEventListener('click', async () => { await fetchJSON('/api/reset', {method:'POST'}); refresh(); });
$('config-reset-btn').addEventListener('click', async () => {
  const body = {
    paper: $('reset-cb-paper').checked,
    shadow: $('reset-cb-shadow').checked,
    signal_log: $('reset-cb-signal-log').checked,
    market_analyst: $('reset-cb-market-analyst').checked,
    market_catalog: $('reset-cb-market-catalog').checked,
    market_history: $('reset-cb-market-history').checked,
    series_evaluator: $('reset-cb-series-evaluator').checked,
  };
  if (!Object.values(body).some(Boolean)) return;
  const picked = Object.entries(body).filter(([, v]) => v).map(([k]) => k).join(', ');
  if (!confirm(`Reset ${picked}? This cannot be undone.`)) return;
  const btn = $('config-reset-btn');
  const result = await fetchJSON('/api/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  btn.textContent = result.ok ? 'Reset ✓' : 'Reset failed';
  setTimeout(() => btn.textContent = 'Reset Selected', 1500);
  refresh();
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && marketDetailTicker) closeMarketDetail(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('help-backdrop').classList.contains('open')) closeHelp(); });
$('halt-btn').addEventListener('click', async () => { await fetchJSON('/api/risk/halt', {method:'POST'}); refresh(); });
$('resume-btn').addEventListener('click', async () => { await fetchJSON('/api/risk/resume', {method:'POST'}); refresh(); });

$('save-config-btn').addEventListener('click', async () => {
  const patch = {
    mode: $('cfg-mode').value,
    strategy: {
      entry_threshold: parseFloat($('cfg-threshold').value),
      longshot_price_threshold: parseFloat($('cfg-longshot-zone').value),
      longshot_entry_threshold_bonus: parseFloat($('cfg-longshot-bonus').value),
      max_position_pct: parseFloat($('cfg-position-pct').value),
      cooldown_sec: parseInt($('cfg-cooldown').value),
      max_open_positions_per_series: $('cfg-max-open-per-series').value === '' ? null : parseInt($('cfg-max-open-per-series').value),
      kelly_fraction_of_cap: parseFloat($('cfg-kelly-fraction').value),
      use_limit_orders: $('cfg-use-limit-orders').checked,
      limit_order_timeout_sec: parseInt($('cfg-limit-order-timeout').value),
      min_unit_cost: parseFloat($('cfg-min-unit-cost').value),
      max_unit_cost: parseFloat($('cfg-max-unit-cost').value),
      close_window_sec: $('cfg-close-window').value === '' ? null : parseFloat($('cfg-close-window').value),
      min_seconds_to_close: $('cfg-min-seconds-to-close').value === '' ? null : parseFloat($('cfg-min-seconds-to-close').value),
      special_market_min_seconds_to_close: parseFloat($('cfg-special-market-min-sec').value),
      longshot_close_window_sec: parseInt($('cfg-longshot-close-window').value),
      min_whale_winrate_pct: parseFloat($('cfg-min-winrate').value),
      min_resolved_for_whale_filter: parseInt($('cfg-min-resolved').value),
      live_markets_only: $('cfg-live-only').checked,
      excluded_series: $('cfg-excluded-series').value.split(',').map(s => s.trim().toUpperCase()).filter(Boolean),
      take_profit_pct: $('cfg-take-profit').value === '' ? null : parseFloat($('cfg-take-profit').value),
      stop_loss_pct: $('cfg-stop-loss').value === '' ? null : parseFloat($('cfg-stop-loss').value),
      exit_min_seconds_to_close: $('cfg-exit-min-seconds-to-close').value === '' ? null : parseFloat($('cfg-exit-min-seconds-to-close').value),
      exit_on_sentiment_reversal: $('cfg-exit-sentiment-reversal').checked,
      exit_sentiment_min_signals: parseInt($('cfg-exit-sentiment-min-signals').value),
      exit_sentiment_lean_pct: parseFloat($('cfg-exit-sentiment-lean-pct').value),
      auto_exit_enabled: $('cfg-auto-exit-enabled').checked,
      auto_exit_threshold: parseFloat($('cfg-auto-exit-threshold').value),
      auto_exit_pnl_weight: parseFloat($('cfg-auto-exit-pnl-weight').value),
      auto_exit_sentiment_weight: parseFloat($('cfg-auto-exit-sentiment-weight').value),
      auto_exit_staleness_weight: parseFloat($('cfg-auto-exit-staleness-weight').value),
      auto_exit_analyst_weight: parseFloat($('cfg-auto-exit-analyst-weight').value),
      auto_exit_gain_reference_pct: parseFloat($('cfg-auto-exit-gain-ref').value),
      auto_exit_loss_reference_pct: parseFloat($('cfg-auto-exit-loss-ref').value),
      auto_exit_stale_after_sec: parseInt($('cfg-auto-exit-stale-sec').value),
      auto_exit_series_track_record_weight: parseFloat($('cfg-auto-exit-track-record-weight').value),
      auto_exit_normal_volatility: $('cfg-auto-exit-normal-vol').value === '' ? null : parseFloat($('cfg-auto-exit-normal-vol').value),
      auto_exit_volatility_lookback_sec: parseInt($('cfg-auto-exit-vol-lookback').value),
    },
    position_netting: {
      enabled: $('cfg-netting-enabled').checked,
      min_dwell_sec: parseInt($('cfg-netting-min-dwell').value),
      min_edge_improvement_usd: parseFloat($('cfg-netting-min-edge').value),
      normal_volatility: $('cfg-netting-normal-vol').value === '' ? null : parseFloat($('cfg-netting-normal-vol').value),
      volatility_lookback_sec: parseInt($('cfg-netting-vol-lookback').value),
    },
    advisory: {
      enabled: $('cfg-advisory-enabled').checked,
      min_resolved_trades_per_variant: parseInt($('cfg-advisory-min-resolved').value),
      auto_apply_min_n: parseInt($('cfg-advisory-auto-apply-min-n').value),
    },
    confidence_calibration: {
      enabled: $('cfg-calibration-enabled').checked,
      min_resolved_signals: parseInt($('cfg-calibration-min-resolved').value),
      auto_apply_min_resolved_signals: parseInt($('cfg-calibration-auto-apply-min-resolved').value),
    },
    market_analyst: {
      enabled: $('cfg-market-analyst-enabled').checked,
      model: $('cfg-market-analyst-model').value || 'claude-sonnet-5',
      reanalyze_cooldown_sec: parseInt($('cfg-market-analyst-cooldown').value),
    },
    whale_signal: {
      signal_frequency_sec: parseInt($('cfg-freq').value),
      whale_size_range: [parseInt($('cfg-size-min').value), parseInt($('cfg-size-max').value)],
      live_markets_only: $('cfg-whale-live-only').checked,
    },
    whale_watcher_kalshi: {
      min_contracts: parseFloat($('cfg-whale-min-contracts').value) || 0,
      min_contracts_by_series: Object.fromEntries(
        $('cfg-whale-min-contracts-by-series').value.split(',')
          .map(pair => pair.split(':').map(s => s.trim()))
          .filter(([series, amount]) => series && amount && !isNaN(parseFloat(amount)))
          .map(([series, amount]) => [series.toUpperCase(), parseFloat(amount)])
      ),
    },
    series_evaluator: {
      enabled: $('cfg-series-evaluator-enabled').checked,
      min_observation_sec: parseInt($('cfg-series-evaluator-min-observation-sec').value),
      min_trades_observed: parseInt($('cfg-series-evaluator-min-trades-observed').value),
      max_observation_sec: parseInt($('cfg-series-evaluator-max-observation-sec').value),
      min_qualify_rate: parseFloat($('cfg-series-evaluator-min-qualify-rate').value),
      backoff_base_sec: parseInt($('cfg-series-evaluator-backoff-base-sec').value),
      backoff_multiplier: parseFloat($('cfg-series-evaluator-backoff-multiplier').value),
      backoff_max_sec: parseInt($('cfg-series-evaluator-backoff-max-sec').value),
    },
    risk: {
      max_daily_loss_pct: parseFloat($('cfg-max-loss').value),
    },
    kalshi: {
      min_volume_24h: parseFloat($('cfg-min-volume').value) || 0,
      categories: $('cfg-categories').value.split(',').map(s => s.trim()).filter(Boolean),
      live_markets_only: $('cfg-discovery-live-only').checked,
      markets_watchlist_mode: $('cfg-watchlist-mode').value,
      max_children_per_parent: $('cfg-max-children-per-parent').value === '' ? null : parseInt($('cfg-max-children-per-parent').value),
    },
  };
  await fetchJSON('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({patch})});
  const btn = $('save-config-btn');
  btn.textContent = 'Saved ✓';
  setTimeout(() => btn.textContent = 'Save Config', 1200);
  loadChangeHistory();  // a manual save now logs to Change History too (Item 3D)
});

// Search/browse directly in the Markets tab, direct request ("you never
// included an ability to search or browse markets myself in the markets
// tab") - distinct purpose from Config's search below, which exists to
// manage the strategy's watchlist. This one is for looking, not
// configuring: click any result to open its full detail
// (openMarketDetail), same modal every other market row in this app opens
// into. A "+ Pin" button on each result is a bonus bridge into the
// watchlist-management flow, not the point of this search - reuses the
// same GET /api/markets/search endpoint and kalshi.markets_watchlist patch
// Config's search already established.
let marketsTabSearchResults = [];

async function searchMarketsTab() {
  const q = $('markets-tab-search-q').value.trim();
  const category = $('markets-tab-search-category').value.trim();
  const includeDormant = $('markets-tab-search-dormant').checked;
  const liveOnly = $('markets-tab-search-live-only').checked;
  const btn = $('markets-tab-search-btn');
  btn.disabled = true;
  btn.textContent = 'Searching…';
  $('markets-tab-search-results').innerHTML = '<div class="empty">Searching…</div>';
  try {
    const params = new URLSearchParams({limit: '30'});
    if (q) params.set('q', q);
    if (category) params.set('category', category);
    if (!includeDormant) params.set('min_volume', '1');
    if (liveOnly) params.set('live_only', 'true');
    const resp = await fetchJSON('/api/markets/search?' + params.toString());
    marketsTabSearchResults = resp.markets || [];
    Object.assign(marketTitles, resp.market_titles || {});
    renderMarketsTabSearchResults();
  } catch (e) {
    $('markets-tab-search-results').innerHTML = '<div class="empty">Search failed.</div>';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Search';
  }
}

function renderMarketsTabSearchResults() {
  const el = $('markets-tab-search-results');
  if (!marketsTabSearchResults.length) {
    el.innerHTML = '<div class="empty">No results. Try a broader query, check "Include dormant," or uncheck "LIVE only."</div>';
    return;
  }
  el.innerHTML = `<div class="scroll-panel" style="max-height:320px; margin-bottom:16px;">` + marketsTabSearchResults.map(m => {
    const label = marketLabel(m.ticker);
    const vol = m.volume_24h_fp != null ? Math.round(parseFloat(m.volume_24h_fp)).toLocaleString() : '—';
    return `<div class="market-row" style="cursor:pointer; display:flex; align-items:center; gap:10px;" title="${esc(label.full)}"
        onclick="openMarketDetail('${esc(m.ticker)}', '${esc(m.event_ticker || '')}')">
      <span class="ticker" style="flex:1;">${esc(label.short)}</span>
      <span class="price">vol ${vol}</span>
      <button onclick="event.stopPropagation(); pinSingleMarketToWatchlist('${esc(m.ticker)}', this);" title="Add to watchlist" style="padding:3px 8px; font-size:11px;">+ Pin</button>
    </div>`;
  }).join('') + `</div>`;
}

async function pinSingleMarketToWatchlist(ticker, btn) {
  const cfg = await fetchJSON('/api/config');
  const current = cfg.kalshi.markets_watchlist || [];
  if (current.includes(ticker)) { btn.textContent = 'Pinned ✓'; btn.disabled = true; return; }
  const merged = [...current, ticker];
  await fetchJSON('/api/config', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({patch: {kalshi: {markets_watchlist: merged}}}),
  });
  btn.textContent = 'Pinned ✓';
  btn.disabled = true;
}

$('markets-tab-search-btn').addEventListener('click', searchMarketsTab);

// Market search & pinned-watchlist management (ROADMAP.md Phase 0.5) - the
// automatic watchlist only surfaces active markets by design (see
// GET /api/markets/search's docstring for why a flat browse can't be
// trusted to do that reliably); this is the escape hatch to find and pin
// anything else, dormant markets included.
let lastSearchResults = [];

async function searchMarkets() {
  const q = $('market-search-q').value.trim();
  const category = $('market-search-category').value.trim();
  const includeDormant = $('market-search-dormant').checked;
  const liveOnly = $('market-search-live-only').checked;
  const btn = $('market-search-btn');
  btn.disabled = true;
  btn.textContent = 'Searching…';
  $('market-search-results').innerHTML = '<div class="empty">Searching…</div>';
  try {
    const params = new URLSearchParams({limit: '30'});
    if (q) params.set('q', q);
    if (category) params.set('category', category);
    if (!includeDormant) params.set('min_volume', '1');
    if (liveOnly) params.set('live_only', 'true');
    const resp = await fetchJSON('/api/markets/search?' + params.toString());
    lastSearchResults = resp.markets || [];
    Object.assign(marketTitles, resp.market_titles || {});
    renderSearchResults();
  } catch (e) {
    $('market-search-results').innerHTML = '<div class="empty">Search failed.</div>';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Search';
  }
}

function renderSearchResults() {
  const el = $('market-search-results');
  if (!lastSearchResults.length) {
    el.innerHTML = '<div class="empty">No results. Try a broader query, check "Include dormant," or uncheck "LIVE only."</div>';
    updateWatchlistAddButton();
    return;
  }
  el.innerHTML = `<div class="scroll-panel" style="max-height:320px;">` + lastSearchResults.map(m => {
    const label = marketLabel(m.ticker);
    const vol = m.volume_24h_fp != null ? Math.round(parseFloat(m.volume_24h_fp)).toLocaleString() : '—';
    return `<label class="market-row" style="cursor:pointer; display:flex; align-items:center; gap:10px;" title="${esc(label.full)}">
      <input type="checkbox" class="search-result-check" value="${esc(m.ticker)}" onchange="updateWatchlistAddButton()">
      <span class="ticker" style="flex:1;">${esc(label.short)}</span>
      <span class="price">vol ${vol}</span>
    </label>`;
  }).join('') + `</div>`;
  updateWatchlistAddButton();
}

function updateWatchlistAddButton() {
  const checked = document.querySelectorAll('.search-result-check:checked').length;
  const btn = $('watchlist-add-btn');
  btn.disabled = checked === 0;
  btn.textContent = checked ? `Add ${checked} Selected to Watchlist` : 'Add Selected to Watchlist';
}

async function addSelectedToWatchlist() {
  const selected = Array.from(document.querySelectorAll('.search-result-check:checked')).map(el => el.value);
  if (!selected.length) return;
  const cfg = await fetchJSON('/api/config');
  const current = cfg.kalshi.markets_watchlist || [];
  const merged = Array.from(new Set([...current, ...selected]));
  await fetchJSON('/api/config', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({patch: {kalshi: {markets_watchlist: merged}}}),
  });
  document.querySelectorAll('.search-result-check:checked').forEach(el => el.checked = false);
  updateWatchlistAddButton();
  renderPinnedWatchlist(merged);
}

async function removeFromWatchlist(ticker) {
  const cfg = await fetchJSON('/api/config');
  const remaining = (cfg.kalshi.markets_watchlist || []).filter(t => t !== ticker);
  await fetchJSON('/api/config', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({patch: {kalshi: {markets_watchlist: remaining}}}),
  });
  renderPinnedWatchlist(remaining);
}

function renderPinnedWatchlist(tickers) {
  $('pinned-watchlist-count').textContent = tickers.length ? `(${tickers.length})` : '';
  const el = $('pinned-watchlist-list');
  if (!tickers.length) {
    // Mode-aware (2026-08-17): in exclusive mode there IS no discovery
    // fallback - an empty pin list means an empty watchlist, not "using
    // automatic discovery above," which would be actively wrong to claim.
    const exclusive = $('cfg-watchlist-mode').value === 'exclusive';
    el.innerHTML = exclusive
      ? '<div class="empty">Nothing pinned — exclusive mode means the watchlist is empty until you add something.</div>'
      : '<div class="empty">Nothing pinned — using automatic discovery above.</div>';
    return;
  }
  el.innerHTML = tickers.map(t => {
    const label = marketLabel(t);
    return `<div class="market-row" title="${esc(label.full)}">
      <span class="ticker">${esc(label.short)}</span>
      <button class="danger" style="padding:4px 8px; font-size:11px;" onclick="removeFromWatchlist('${esc(t)}')">Remove</button>
    </div>`;
  }).join('');
}

// Per-series/category strategy_overrides editor (2026-08-15 direct
// request: "these strategies need to be able to be tweaked for individual
// series"). Only strategy.* fields.
// Curated field list rather than a free-text field name: config_overrides.
// resolve() would silently accept any field name and just never have
// anything read it back out - a typo here would create a dead, invisible
// no-op override with no error anywhere. Every add/remove re-fetches
// /api/config fresh immediately before merging (not the last-rendered copy
// in memory) - same "don't trust a stale in-tab snapshot" lesson this
// session's earlier concurrent-edit confusion already taught elsewhere.
const OVERRIDE_FIELDS = {
  entry_threshold: {label: 'Entry confidence threshold (0–1)', type: 'number'},
  max_position_pct: {label: 'Max position size (% of bankroll)', type: 'number'},
  cooldown_sec: {label: 'Cooldown per market (seconds)', type: 'number'},
  kelly_fraction_of_cap: {label: 'Confidence-scaled position sizing (0–1)', type: 'number'},
  min_whale_winrate_pct: {label: 'Min whale win rate to trust (%)', type: 'number'},
  min_unit_cost: {label: 'Min price to consider trading (0–1)', type: 'number'},
  max_unit_cost: {label: 'Max price to consider trading (0–1)', type: 'number'},
  take_profit_pct: {label: 'Take-profit (fraction of cost basis, 0–1)', type: 'number'},
  stop_loss_pct: {label: 'Stop-loss (fraction of cost basis, 0–1)', type: 'number'},
  max_open_positions_per_series: {label: 'Max simultaneously-open positions', type: 'number'},
  close_window_sec: {label: 'Entry cutoff before close (seconds)', type: 'number'},
  live_markets_only: {label: 'Only trade markets currently flagged LIVE', type: 'bool'},
};
let currentStrategyOverrides = {by_category: {}, by_series: {}};

function populateOverrideFieldOptions() {
  $('override-add-field').innerHTML = Object.entries(OVERRIDE_FIELDS)
    .map(([field, meta]) => `<option value="${esc(field)}">${esc(meta.label)}</option>`).join('');
  onOverrideFieldChange();
}

function onOverrideFieldChange() {
  const meta = OVERRIDE_FIELDS[$('override-add-field').value];
  const valueInput = $('override-add-value');
  if (meta && meta.type === 'bool' && valueInput.tagName !== 'SELECT') {
    valueInput.outerHTML = `<select id="override-add-value" style="width:100px;"><option value="true">true</option><option value="false">false</option></select>`;
  } else if ((!meta || meta.type !== 'bool') && valueInput.tagName !== 'INPUT') {
    valueInput.outerHTML = `<input id="override-add-value" type="text" style="width:100px;">`;
  }
}

function onOverrideScopeChange() {
  const isSeries = $('override-add-scope').value === 'by_series';
  $('override-add-key-label').textContent = isSeries ? 'Series ticker prefix' : 'Category';
  $('override-add-key').placeholder = isSeries ? 'KXBTC15M' : 'Sports';
}

function mergeOverrideClientSide(overrides, scope, key, field, value) {
  const result = {by_category: {}, by_series: {}};
  for (const tier of ['by_category', 'by_series']) {
    for (const [k, v] of Object.entries((overrides && overrides[tier]) || {})) result[tier][k] = {...v};
  }
  result[scope][key] = {...(result[scope][key] || {}), [field]: value};
  return result;
}

function removeOverrideClientSide(overrides, scope, key, field) {
  const result = {by_category: {}, by_series: {}};
  for (const tier of ['by_category', 'by_series']) {
    for (const [k, v] of Object.entries((overrides && overrides[tier]) || {})) result[tier][k] = {...v};
  }
  if (!result[scope][key]) return result;
  if (field == null) {
    delete result[scope][key];
    return result;
  }
  delete result[scope][key][field];
  if (Object.keys(result[scope][key]).length === 0) delete result[scope][key];
  return result;
}

function renderStrategyOverrides(overrides) {
  currentStrategyOverrides = {by_category: (overrides || {}).by_category || {}, by_series: (overrides || {}).by_series || {}};
  const rows = [
    ...Object.entries(currentStrategyOverrides.by_category).map(([key, fields]) => ({scope: 'by_category', key, fields, label: `Category: ${key}`})),
    ...Object.entries(currentStrategyOverrides.by_series).map(([key, fields]) => ({scope: 'by_series', key, fields, label: `Series: ${key}`})),
  ];
  const el = $('overrides-list');
  if (!rows.length) {
    el.innerHTML = '<div class="empty">No overrides yet — every series/category uses the global Strategy settings above.</div>';
    return;
  }
  el.innerHTML = rows.map(row => `
    <div class="market-row" style="flex-direction:column; align-items:stretch; gap:6px; height:auto; padding:8px 10px;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <strong>${esc(row.label)}</strong>
        <button class="danger" style="padding:3px 8px; font-size:11px;" onclick="removeStrategyOverrideKey('${row.scope}', '${esc(row.key)}')">Remove all</button>
      </div>
      <div style="display:flex; flex-wrap:wrap; gap:6px;">
        ${Object.entries(row.fields).map(([field, value]) => `
          <span style="display:inline-flex; align-items:center; gap:4px; background:rgba(127,127,127,0.12); border:1px solid var(--panel-border); border-radius:4px; padding:2px 4px 2px 8px; font-size:12px;">
            ${esc((OVERRIDE_FIELDS[field] || {}).label || field)}: <b>${esc(String(value))}</b>
            <button onclick="removeStrategyOverrideField('${row.scope}', '${esc(row.key)}', '${esc(field)}')" title="Remove this field" style="padding:1px 6px; font-size:11px; background:none; border:none; cursor:pointer; color:var(--muted);">×</button>
          </span>
        `).join('')}
      </div>
    </div>
  `).join('');
}

async function saveStrategyOverrides(newOverrides) {
  await fetchJSON('/api/config', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({patch: {strategy_overrides: newOverrides}}),
  });
  renderStrategyOverrides(newOverrides);
  loadChangeHistory();
}

async function addStrategyOverride() {
  const scope = $('override-add-scope').value;
  const key = $('override-add-key').value.trim().toUpperCase();
  const field = $('override-add-field').value;
  const rawValue = $('override-add-value').value;
  const errEl = $('override-add-error');
  errEl.textContent = '';
  if (!key) { errEl.textContent = scope === 'by_series' ? 'Enter a series ticker prefix.' : 'Enter a category.'; return; }
  const meta = OVERRIDE_FIELDS[field];
  let value;
  if (meta && meta.type === 'bool') {
    value = rawValue === 'true';
  } else {
    value = parseFloat(rawValue);
    if (Number.isNaN(value)) { errEl.textContent = 'Enter a numeric value.'; return; }
  }
  const btn = $('override-add-btn');
  btn.disabled = true;
  try {
    const cfg = await fetchJSON('/api/config');
    await saveStrategyOverrides(mergeOverrideClientSide(cfg.strategy_overrides, scope, key, field, value));
    $('override-add-key').value = '';
  } catch (e) {
    errEl.textContent = 'Save failed — try again.';
  } finally {
    btn.disabled = false;
  }
}

async function removeStrategyOverrideField(scope, key, field) {
  const cfg = await fetchJSON('/api/config');
  await saveStrategyOverrides(removeOverrideClientSide(cfg.strategy_overrides, scope, key, field));
}

async function removeStrategyOverrideKey(scope, key) {
  const cfg = await fetchJSON('/api/config');
  await saveStrategyOverrides(removeOverrideClientSide(cfg.strategy_overrides, scope, key, null));
}

populateOverrideFieldOptions();
onOverrideScopeChange();

$('market-search-btn').addEventListener('click', searchMarkets);
$('watchlist-add-btn').addEventListener('click', addSelectedToWatchlist);

async function loadSession() {
  const el = $('session-info');
  try {
    const session = await fetchJSON('/api/session');
    if (!session.auth_configured) {
      el.innerHTML = `<span title="Set GOOGLE_CLIENT_ID/SECRET + APP_SECRET_KEY in .env to require sign-in">Open access · no login required</span>`;
      return;
    }
    if (session.user) {
      el.innerHTML = `<span>Signed in as ${session.user.email}</span>
        <button onclick="fetch('/auth/logout',{method:'POST'}).then(()=>location.href='/login')">Logout</button>`;
    } else {
      el.innerHTML = `<a href="/login">Not signed in</a>`;
    }
  } catch (e) {
    el.textContent = 'session status unavailable';
  }
}

export { OVERRIDE_FIELDS, addSelectedToWatchlist, addStrategyOverride, currentStrategyOverrides, lastSearchResults, loadConfig, loadSession, marketsTabSearchResults, mergeOverrideClientSide, onOverrideFieldChange, onOverrideScopeChange, pinSingleMarketToWatchlist, populateOverrideFieldOptions, removeFromWatchlist, removeOverrideClientSide, removeStrategyOverrideField, removeStrategyOverrideKey, renderMarketsTabSearchResults, renderPinnedWatchlist, renderSearchResults, renderStrategyOverrides, saveStrategyOverrides, searchMarkets, searchMarketsTab, updateWatchlistAddButton };

// Exposed for inline HTML event handlers (onclick=/onchange=/oninput=,
// including ones built indirectly via a caller-supplied onclick-string
// parameter - see shared-utils.js's header). Every top-level function in
// this file is exposed (cheap, harmless if unused); state objects only the
// specific ones confirmed to be read/mutated directly from a handler.
window.addSelectedToWatchlist = addSelectedToWatchlist;
window.addStrategyOverride = addStrategyOverride;
window.loadConfig = loadConfig;
window.loadSession = loadSession;
window.mergeOverrideClientSide = mergeOverrideClientSide;
window.onOverrideFieldChange = onOverrideFieldChange;
window.onOverrideScopeChange = onOverrideScopeChange;
window.pinSingleMarketToWatchlist = pinSingleMarketToWatchlist;
window.populateOverrideFieldOptions = populateOverrideFieldOptions;
window.removeFromWatchlist = removeFromWatchlist;
window.removeOverrideClientSide = removeOverrideClientSide;
window.removeStrategyOverrideField = removeStrategyOverrideField;
window.removeStrategyOverrideKey = removeStrategyOverrideKey;
window.renderMarketsTabSearchResults = renderMarketsTabSearchResults;
window.renderPinnedWatchlist = renderPinnedWatchlist;
window.renderSearchResults = renderSearchResults;
window.renderStrategyOverrides = renderStrategyOverrides;
window.saveStrategyOverrides = saveStrategyOverrides;
window.searchMarkets = searchMarkets;
window.searchMarketsTab = searchMarketsTab;
window.updateWatchlistAddButton = updateWatchlistAddButton;
