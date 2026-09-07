import { VIEWS, currentView, refreshHistoryInsightsIfActive } from './main.js';
import { marketDetailTicker, refreshMarketDetail, renderAccount, renderHeaderStrip, renderRealMoneyBanner, renderScreenerTable } from './screener-and-header.js';
import { $, advToggleHTML, eventLiveData, eventTitles, isAdvanced, marketPanelState, marketTitles, renderMarketCategorySuggestions, renderMarkets, rerenderMarketPanel } from './shared-utils.js';
import { loadPositionNettingGroups, renderSignalDecisionFeed } from './signals-feed.js';
import { loadSystemHealth } from './system-health.js';
import { renderFunnel } from './trade-log-and-real.js';
import { renderConnectivity, renderExchangeStatus, renderHalted, renderPortfolio, renderTickHealth, renderTradeStreamStatus, sourceLabel } from './trading-gate-and-connectivity.js';
import { renderTradeTape, renderWhaleTrackRecord } from './whale-watch.js';

// Part of index.html's JS split - see shared-utils.js's header for the
// load-order/shared-global-scope rationale common to all these files.
// This file: the core refresh() poll loop (drives every view's data via
// /api/state), the live-trade websocket connection, and the two small
// UI-state helpers refresh()/loadConfig() share.

// These all live here (not shared-utils.js/trading-gate-and-connectivity.js,
// where they originally landed from the plain byte-range split) because
// this is the only file that ever reassigns them, from refresh()/
// connectWebSocket() itself. ES modules only let the declaring file
// reassign its own binding - an importer only gets a read-only live view -
// so ownership follows the mutator, not wherever the split first put them.
let terminalSignalFeed = [];
let terminalDecisionFeed = [];
let terminalLatestPrices = {};  // last known state.latest_prices - renderSignalDecisionFeed's divergencePts needs a live price to compare a signal's own price against
let categoryMetadata = { tags_by_categories: {}, filters_by_sports: {}, sport_ordering: [] };
let seriesMeta = {};  // series ticker -> {title, category, tags} — real Kalshi series metadata, see main.py's _series_meta_map
let liveStatus = {};  // event_ticker -> "live" | "finished" | "none" | undefined
let wsReconnectDelayMs = 1000;
let lastSuccessfulRefresh = Date.now();
let consecutiveRefreshFailures = 0;
// The actual Error object from refresh()'s most recent failure - lets
// renderConnectivity() (trading-gate-and-connectivity.js) tell a permanent,
// same-every-time failure (e.g. the page itself was loaded as
// https://user:pass@host/..., which makes the Fetch spec throw on every
// same-origin fetch() from then on - "Request cannot be constructed from a
// URL that includes credentials") apart from an ordinary transient network
// blip, which consecutiveRefreshFailures/lastSuccessfulRefresh alone can't
// distinguish. Never cleared to a stale error - reset to null the moment a
// refresh succeeds, same lifecycle as consecutiveRefreshFailures.
let lastRefreshError = null;
let lastWhaleSource = null;  // set from each poll's state.whale_source - see loadConfig()'s real-provider status note

// De-polled (2026-09-03, Task 2 of docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-tier1-backend-hygiene.md, moved there 2026-09-06, planning-lanes migration): loadSystemHealth() call site below, throttled
// to 20s - see that call site's own comment for the full rationale.
const SYSTEM_HEALTH_REFRESH_MS = 20000;
let _lastSystemHealthLoadAt = 0;

// terminalSignalFeed/terminalDecisionFeed get wholesale-reassigned on every
// refresh() (see below) - a one-time `window.x = x` exposure would go
// stale after the first poll, so the Terminal tab's inline onchange
// filters call this wrapper instead of reading them directly, guaranteeing
// they always see the current value.
function _rerenderTerminalFeed() {
  renderSignalDecisionFeed(terminalSignalFeed, terminalDecisionFeed);
}

async function refresh() {
  try {
    // Not fetchJSON here on purpose - fetchJSON doesn't check res.ok, so a
    // 500 with a valid-JSON error body (FastAPI's default unhandled-
    // exception response is exactly that shape) would otherwise parse
    // "successfully" and be treated as a real update instead of the
    // connectivity failure it actually is.
    const res = await fetch('/api/state');
    if (res.status === 304) {
      lastSuccessfulRefresh = Date.now();
      consecutiveRefreshFailures = 0;
      lastRefreshError = null;
      renderConnectivity();
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const state = await res.json();

    lastSuccessfulRefresh = Date.now();
    lastRefreshError = null;
    consecutiveRefreshFailures = 0;
    renderConnectivity();
    renderTickHealth(state);

    $('live-dot').classList.toggle('live', state.running);
    $('whale-source').textContent = `· ${sourceLabel(state.whale_source)}`;
    lastWhaleSource = state.whale_source;
    updateWhaleProviderStatus(lastWhaleSource);
    renderExchangeStatus(state.exchange_status);
    renderTradeStreamStatus(state.trade_stream_status);
    $('toggle-btn').textContent = state.running ? 'Pause' : 'Resume Loop';

    terminalSignalFeed = state.signal_feed || [];
    terminalDecisionFeed = state.decision_feed || [];
    terminalLatestPrices = state.latest_prices || {};
    const broker = state.broker;
    renderHeaderStrip(broker, state.account, state.real_balance_history || []);

    // Merge, don't replace - /api/state's market_titles/event_titles are
    // scoped to what's currently relevant (see _build_state_body) to keep
    // the payload bounded, not the full accumulated history. A title
    // learned once (here, or via loadSignalHistory/loadSignalClusters
    // merging their own responses in below) should stay known for the rest
    // of this browser session even after its ticker drops out of scope,
    // instead of disappearing the next time this wholesale-replaced.
    Object.assign(marketTitles, state.market_titles || {});
    Object.assign(eventTitles, state.event_titles || {});
    Object.assign(eventLiveData, state.event_live_data || {});
    categoryMetadata = state.category_metadata || { tags_by_categories: {}, filters_by_sports: {}, sport_ordering: [] };
    seriesMeta = state.series_meta || {};
    liveStatus = state.live_status || {};
    renderMarketCategorySuggestions();
    renderRealMoneyBanner(state.account); // always visible, above everything
    renderAccount(state.account); // always visible, above the tabs

    // Only build the DOM for whichever view is actually on screen — the other
    // three don't need fresh HTML every poll if nobody's looking at them.
    const active = VIEWS.find(v => $('view-' + v).classList.contains('active'));
    if (active === 'terminal') {
      $('markets-list-toggle').innerHTML = advToggleHTML('markets-list');
      if (isAdvanced('markets-list')) {
        renderScreenerTable('markets-list', 'markets-list', 'market-count', state.markets, state.latest_prices || {}, false, []);
      } else {
        renderMarkets(state.markets, state.latest_prices || {});
      }
      renderFunnel(state.stats);
      renderSignalDecisionFeed(state.signal_feed, state.decision_feed);
      renderHalted(state.risk);
      // De-polled (2026-09-03, Task 2 of docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-tier1-backend-hygiene.md, moved there 2026-09-06, planning-lanes migration): §3.3 of the architecture-audit-second-
      // pass research found this route is the LEAST-fetched of the three
      // named for de-polling (35 browser rows in 13h - the Terminal tab is
      // rarely open) and the MOST important to keep responsive, since it's
      // the endpoint CLAUDE.md tells every session to start an
      // investigation from. Smaller throttle (20s, vs. the History tab
      // loaders' 30s) reflects that corrected priority, not a uniform
      // "de-poll everything the same" reading.
      const _now = Date.now();
      if (_now - _lastSystemHealthLoadAt >= SYSTEM_HEALTH_REFRESH_MS) {
        _lastSystemHealthLoadAt = _now;
        loadSystemHealth(state);
      }
    } else if (active === 'portfolio') {
      renderPortfolio(state, broker);
      loadPositionNettingGroups();
    } else if (active === 'markets') {
      marketPanelState.markets = {
        markets: state.markets || [], prices: state.latest_prices || {}, signals: [], includeWhale: false,
        seriesTrackRecord: state.series_track_record || {},
      };
      $('markets-toggle').innerHTML = advToggleHTML('markets');
      rerenderMarketPanel('markets');
    } else if (active === 'whale') {
      renderWhaleTrackRecord(state.whale_track_record, state.whale_source);
      renderTradeTape(state.trade_tape);
      marketPanelState['whale-cards'] = {
        markets: state.markets || [], prices: state.latest_prices || {}, signals: state.signal_feed || [], includeWhale: true,
        seriesTrackRecord: state.series_track_record || {},
      };
      $('whale-cards-toggle').innerHTML = advToggleHTML('whale-cards');
      rerenderMarketPanel('whale-cards');
    }

    // The market-detail modal can be open regardless of which tab is behind
    // it, so it isn't covered by the active-tab branches above.
    if (marketDetailTicker) refreshMarketDetail();
  } catch (e) {
    console.error('refresh failed', e);
    consecutiveRefreshFailures++;
    lastRefreshError = e;
    renderConnectivity();
  }
}

function connectWebSocket() {
  const wsUrl = new URL('/api/ws', location.href);
  wsUrl.protocol = wsUrl.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(wsUrl.toString());

  socket.addEventListener('open', () => {
    console.log('WebSocket connected');
    wsReconnectDelayMs = 1000;
  });

  socket.addEventListener('message', (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.type === 'signal_decision') {
        if (payload.signal) {
          terminalSignalFeed.unshift(payload.signal);
          terminalSignalFeed = terminalSignalFeed.slice(0, 50);
        }
        if (payload.decision) {
          terminalDecisionFeed.unshift(payload.decision);
          terminalDecisionFeed = terminalDecisionFeed.slice(0, 50);
        }
        if (currentView === 'terminal') {
          renderSignalDecisionFeed(terminalSignalFeed, terminalDecisionFeed);
        }
      } else if (payload.type === 'trade_stream_status') {
        renderTradeStreamStatus(payload.status);
      } else if (payload.type === 'history_updated') {
        // Backend push (services/history_push.py) - a real write happened
        // to trade_log/signal_log/candidate_ledger/calibration_history/
        // config_performance. No payload beyond the type tag (design
        // §4.2) - just re-run the same full-batch refetch
        // refreshHistoryInsightsIfActive() already performs, which itself
        // no-ops unless currentView === 'history'. Its own
        // HISTORY_INSIGHTS_REFRESH_MS throttle still applies here too, so
        // a burst of history_updated messages degrades to the same
        // cadence as before, just only while there's real activity.
        refreshHistoryInsightsIfActive();
      }
    } catch (err) {
      console.error('Invalid websocket event', err, event.data);
    }
  });

  socket.addEventListener('close', (event) => {
    console.warn('WebSocket closed', event.code, event.reason);
    setTimeout(connectWebSocket, wsReconnectDelayMs);
    wsReconnectDelayMs = Math.min(wsReconnectDelayMs * 1.5, 30000);
  });

  socket.addEventListener('error', (event) => {
    console.error('WebSocket error', event);
    socket.close();
  });
}

// Called from refresh() (every poll, so this stays live rather than frozen
// at whatever it was on page load) and from loadConfig() (so it's correct
// immediately if the Config tab happens to already be open). Which section
// is "inactive" depends on runtime config/.env, not a fixed default, so
// this reads the real current source rather than assuming the simulator.
function setSectionInputsDisabled(section, disabled) {
  section.querySelectorAll('.section-body input, .section-body select, .section-body textarea')
    .forEach(el => { el.disabled = disabled; });
}

function updateWhaleProviderStatus(src) {
  const statusEl = $('whale-provider-status');
  const simSection = $('whale-sim-section');
  const realSection = $('whale-real-section');
  if (!statusEl || !simSection || !realSection) return;  // Config tab markup not present yet
  const isRealActive = src != null && src !== 'simulated' && !src.startsWith('simulated (');
  const isSimulatorPrimary = src === 'simulated';  // not "any non-real state" - a transient
  // fetch-failure fallback still means the real provider is selected and its config still
  // matters for when it recovers, so that case leaves both sections alone rather than
  // disabling either one.
  simSection.classList.toggle('provider-inactive', isRealActive);
  setSectionInputsDisabled(simSection, isRealActive);
  realSection.classList.toggle('provider-inactive', isSimulatorPrimary);
  setSectionInputsDisabled(realSection, isSimulatorPrimary);
  // The Strategy section's whale-filter/exit fields aren't simulator-only
  // mechanics (unlike whale_signal.* above) - they apply to whichever
  // signal source is actually feeding them, real or simulated. Their "sim
  // data" badges should only show while the simulator is genuinely the
  // one supplying data, matching the legend's own "appears/disappears
  // live" wording - not disabled inputs, just a badge, since the fields
  // themselves stay fully meaningful either way.
  document.querySelectorAll('.whale-dependent-badge').forEach(el => {
    el.style.display = isRealActive ? 'none' : '';
  });
  if (src == null) {
    statusEl.textContent = 'Checking which whale-signal source is active…';
    statusEl.style.color = 'var(--muted)';
  } else if (isRealActive) {
    statusEl.textContent = `📡 Currently active: real data from "${src}"`;
    statusEl.style.color = 'var(--yes)';
  } else if (src.startsWith('simulated (')) {
    statusEl.textContent = `⚠️ Real provider's last fetch failed - using simulator as backup this tick (${src})`;
    statusEl.style.color = 'var(--no)';
  } else {
    statusEl.textContent = '🧪 Currently active: the built-in simulator (no real provider selected)';
    statusEl.style.color = 'var(--muted)';
  }
}

export { _rerenderTerminalFeed, categoryMetadata, connectWebSocket, consecutiveRefreshFailures, lastRefreshError, lastSuccessfulRefresh, lastWhaleSource, liveStatus, refresh, seriesMeta, setSectionInputsDisabled, terminalDecisionFeed, terminalLatestPrices, terminalSignalFeed, updateWhaleProviderStatus, wsReconnectDelayMs };

// Exposed for inline HTML event handlers (onclick=/onchange=/oninput=,
// including ones built indirectly via a caller-supplied onclick-string
// parameter - see shared-utils.js's header). Every top-level function in
// this file is exposed (cheap, harmless if unused); state objects only the
// specific ones confirmed to be read/mutated directly from a handler.
window._rerenderTerminalFeed = _rerenderTerminalFeed;
window.connectWebSocket = connectWebSocket;
window.refresh = refresh;
window.setSectionInputsDisabled = setSectionInputsDisabled;
window.updateWhaleProviderStatus = updateWhaleProviderStatus;
