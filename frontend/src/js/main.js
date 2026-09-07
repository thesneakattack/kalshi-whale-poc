import { loadAdvisory, loadBacktestSweeps, loadCalibrationHistory, loadCalibrationReport, loadCandidateLogSummary, loadChangeHistory, loadMarketAnalyst, loadRegimeSegmentation, loadSeriesEvaluator } from './advisory-calibration.js';
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
const VIEWS = ['portfolio', 'markets', 'whale', 'terminal', 'history', 'config'];
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
}
showView(VIEWS.includes(localStorage.getItem('whale-signal-view')) ? localStorage.getItem('whale-signal-view') : 'portfolio');

// History tab staleness fix (direct report 2026-08-10: "the advisory
// recommendations seem out-of-date, and should update") - loadTradingHistory()
// only ever ran once, on tab-open, deliberately preserving the paginated
// trade-log table's current page. But the rest of the tab is live data and
// should still refresh while the History tab remains visible.
//
// A separate refreshActiveViewPanels() used to sit right after this
// function's own call site in polling-and-websocket.js's refresh() and
// called loadTradingHistory()/loadSignalHistory()/loadSignalClusters() on
// every single poll while the History/Whale Watch tab was open - directly
// contradicting the "only ever ran once, on tab-open" comment two lines
// up, and showView()'s own comment below ("load fresh whenever the tab is
// opened, rather than every 5s poll, so paging/sorting doesn't reset
// itself out from under someone actively browsing it"). Real, live
// reported symptom (2026-08-23): scrollbar position resetting on the
// History tab and a laggy Apply button - loadTradingHistory() alone tears
// down and rebuilds the P&L chart, the trade table, and several other
// panels wholesale (el.innerHTML = ...) on top of everything
// refreshHistoryInsightsIfActive() already refreshes below, every 5s,
// duplicating ~10 of its own fetches in the process. Removed entirely
// (2026-08-23) rather than fixed in place - it had no purpose this
// function and showView()'s initial tab-open call didn't already cover.
// De-polled (2026-09-03, Task 2 of docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-tier1-backend-hygiene.md (moved there 2026-09-06, planning-lanes migration), §3.3 of the architecture-audit-second-pass
// research): this used to fire all 9 loaders on EVERY /api/state poll
// (every 6s at this app's default kalshi.poll_interval_sec) while the
// History tab was open, measured live as the dominant cause of a
// 2,305-per-hour nginx 504 storm during one foreground hour. 30000ms is
// 5x the base poll interval - a stated data-plane tradeoff (staleness of
// secondary analytics data on a non-trading-surface tab), not a change to
// /api/state's own cadence or anything trading-decision-facing.
const HISTORY_INSIGHTS_REFRESH_MS = 30000;
let _lastHistoryInsightsRefreshAt = 0;

function refreshHistoryInsightsIfActive() {
  if (currentView !== 'history') return;
  const now = Date.now();
  if (now - _lastHistoryInsightsRefreshAt < HISTORY_INSIGHTS_REFRESH_MS) return;
  _lastHistoryInsightsRefreshAt = now;
  loadAdvisory();
  loadDeclinedSuggestions();
  loadChangeHistory();
  loadCalibrationReport();
  loadCalibrationHistory();
  loadRegimeSegmentation();
  loadCandidateLogSummary();
  loadBacktestSweeps();
  loadSeriesEvaluator();
  loadMarketAnalyst();
}

// Event-driven push (docs/archive/lane-8-frontend-dashboard/specs/2026-09-03-
// history-event-driven-design.md, moved there 2026-09-06, planning-lanes
// migration; services/history_push.py + polling-and-websocket.js's
// 'history_updated' WS branch) replaces refresh()'s old unconditional
// every-poll call into refreshHistoryInsightsIfActive() - History's
// refresh is now decoupled from /api/state's own cadence entirely. This
// safety-net poll is the design's own stated mitigation (§6) for the one
// gap that push can't close: a future write path that doesn't call
// history_push.mark_history_changed() fails silently (no error, the tab
// just looks stale) rather than being detected. 300000ms (5min) is a
// design-time choice, not a measurement (§4.4 says so explicitly): an
// order of magnitude above the WS client's own worst reconnect backoff
// (wsReconnectDelayMs, capped at 30000ms) - "the WS is down and nobody
// noticed" bounds at a few minutes of staleness, not longer.
const HISTORY_SAFETY_NET_POLL_MS = 300000;
setInterval(refreshHistoryInsightsIfActive, HISTORY_SAFETY_NET_POLL_MS);

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

export { VIEWS, currentView, refreshHistoryInsightsIfActive, setAccountMode, showView };

// Exposed for inline HTML event handlers (onclick=/onchange=/oninput=,
// including ones built indirectly via a caller-supplied onclick-string
// parameter - see shared-utils.js's header). Every top-level function in
// this file is exposed (cheap, harmless if unused); state objects only the
// specific ones confirmed to be read/mutated directly from a handler.
window.refreshHistoryInsightsIfActive = refreshHistoryInsightsIfActive;
window.setAccountMode = setAccountMode;
window.showView = showView;
