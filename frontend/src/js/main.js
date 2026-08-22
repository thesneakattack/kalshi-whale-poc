import { loadAdvisory, loadBacktestSweeps, loadCalibrationHistory, loadCalibrationReport, loadCandidateLogSummary, loadChangeHistory, loadCrossStrategyComparison, loadMarketAnalyst, loadMarketNativeState, loadRegimeSegmentation, loadSeriesEvaluator } from './advisory-calibration.js';
import { loadConfig, loadSession } from './config-panel.js';
import { loadDeclinedSuggestions, loadTradingHistory } from './history-core.js';
import { connectWebSocket, refresh } from './polling-and-websocket.js';
import { openHelp } from './screener-and-header.js';
import { $, _setAccountMode } from './shared-utils.js';
import { _feedListState } from './signals-feed.js';
import { loadRealOrders } from './trade-log-and-real.js';
import { refreshIntervalMs, refreshTimer, scheduleRefreshTimer } from './trading-gate-and-connectivity.js';
import { loadSignalClusters, loadSignalHistory } from './whale-watch.js';

// Part of index.html's JS split - see shared-utils.js's header for the
// load-order/shared-global-scope rationale common to all these files.
// This file: loads LAST. Tab-switching (showView), the per-view refresh
// dispatchers, the first-run walkthrough, and the actual bootstrap calls
// (loadConfig/loadSession/refresh/connectWebSocket/scheduleRefreshTimer)
// that kick off everything the other 11 files define.
const VIEWS = ['portfolio', 'markets', 'whale', 'terminal', 'market-native', 'history', 'config'];
let currentView = 'portfolio';

function showView(name) {
  currentView = name;
  VIEWS.forEach(v => {
    $('view-' + v).classList.toggle('active', v === name);
    $('tab-btn-' + v).classList.toggle('active', v === name);
  });
  localStorage.setItem('whale-signal-view', name);
  // Clear feed-list render caches when switching away from the Terminal
  // view to avoid keeping stale id arrays that can prevent a later full
  // rebuild when the view or rendering mode changes.
  function clearTerminalFeedCaches() {
    try {
      delete _feedListState['signal-feed-list'];
      delete _feedListState['decision-feed-list'];
    } catch (e) {
      // defensive: ignore if _feedListState not present
    }
  }
  if (name !== 'terminal') clearTerminalFeedCaches();
  // Signal history / trading history are separate on-demand fetches, not
  // part of the /api/state poll cycle (see loadSignalHistory,
  // loadTradingHistory) - load fresh whenever the tab is opened, rather
  // than every 5s poll, so paging/sorting doesn't reset itself out from
  // under someone actively browsing it.
  if (name === 'whale') { loadSignalHistory(); loadSignalClusters(); }
  if (name === 'history') { loadTradingHistory(); }
  if (name === 'market-native') { loadMarketNativeState(); }
}
showView(VIEWS.includes(localStorage.getItem('whale-signal-view')) ? localStorage.getItem('whale-signal-view') : 'portfolio');

// History tab staleness fix (direct report 2026-08-10: "the advisory
// recommendations seem out-of-date, and should update") - loadTradingHistory()
// only ever ran once, on tab-open, deliberately preserving the paginated
// trade-log table's current page. But the rest of the tab is live data and
// should still refresh while the History tab remains visible.
function refreshHistoryInsightsIfActive() {
  if (currentView !== 'history') return;
  loadAdvisory();
  loadDeclinedSuggestions();
  loadChangeHistory();
  loadCalibrationReport();
  loadCalibrationHistory();
  loadCrossStrategyComparison();
  loadRegimeSegmentation();
  loadCandidateLogSummary();
  loadBacktestSweeps();
  loadSeriesEvaluator();
  loadMarketAnalyst();
}

function refreshActiveViewPanels() {
  if (currentView === 'whale') {
    loadSignalHistory();
    loadSignalClusters();
  }
  if (currentView === 'history') {
    loadTradingHistory();
  }
}

// First-run walkthrough (ROADMAP.md P1) - auto-opens the Help modal once,
// on a browser that's never seen this app before (localStorage flag set by
// openHelp() itself, so both the automatic and manual paths mark it seen
// the same way). Never reopens itself automatically after that - reachable
// anytime afterward via the util-bar's Help link.
if (!localStorage.getItem('whale-signal-seen-intro')) {
  openHelp();
}

function setAccountMode(mode) {
  _setAccountMode(mode);
  localStorage.setItem('whale-signal-account-mode', mode);
  if (mode === 'real') loadRealOrders();
  refresh();
}

loadConfig();
loadSession();
refresh();
connectWebSocket();
// refresh interval is scheduled from the config load so it matches
// kalshi.poll_interval_sec instead of remaining hardcoded.
if (!refreshTimer) scheduleRefreshTimer(refreshIntervalMs);

export { VIEWS, currentView, refreshActiveViewPanels, refreshHistoryInsightsIfActive, setAccountMode, showView };

// Exposed for inline HTML event handlers (onclick=/onchange=/oninput=,
// including ones built indirectly via a caller-supplied onclick-string
// parameter - see shared-utils.js's header). Every top-level function in
// this file is exposed (cheap, harmless if unused); state objects only the
// specific ones confirmed to be read/mutated directly from a handler.
window.refreshActiveViewPanels = refreshActiveViewPanels;
window.refreshHistoryInsightsIfActive = refreshHistoryInsightsIfActive;
window.setAccountMode = setAccountMode;
window.showView = showView;
