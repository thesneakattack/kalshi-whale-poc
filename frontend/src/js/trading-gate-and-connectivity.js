import { computeWhaleLean, renderEquityChart } from './equity-and-cards.js';
import { consecutiveRefreshFailures, lastSuccessfulRefresh, refresh } from './polling-and-websocket.js';
import { $, _setAccountMode, accountMode, contextLineHTML, esc, fetchJSON, fmt, marketLabel } from './shared-utils.js';
import { renderPositions } from './signals-feed.js';
import { loadRealOrders, realOrdersLoaded, renderRealFills, renderRealPositions, renderTrades } from './trade-log-and-real.js';

// Part of index.html's JS split - see shared-utils.js's header for the
// load-order/shared-global-scope rationale common to all these files.
// This file: real-trading confirmation-phrase gate, exchange-status
// display, connectivity/tick-health/risk-halt indicators, and the
// Portfolio tab's dummies/portfolio/shadow-mode panel renderers.
const TRADING_PHRASE = 'ENABLE REAL TRADING';
let tradingGateOpen = false;

function toggleTradingGate() {
  tradingGateOpen = !tradingGateOpen;
  refresh();
}

function tradingGateHTML() {
  return `<div class="trading-gate">
    <div class="warn">⚠ This places real orders with real money on your Kalshi account.</div>
    <div class="sub">Type <b>${TRADING_PHRASE}</b> exactly to confirm, or Cancel.</div>
    <input id="trading-confirm-input" type="text" autocomplete="off" placeholder="${TRADING_PHRASE}"
      oninput="document.getElementById('trading-confirm-btn').disabled = (this.value !== '${TRADING_PHRASE}')">
    <button class="danger" id="trading-confirm-btn" disabled onclick="confirmEnableTrading()">Confirm</button>
    <button onclick="toggleTradingGate()">Cancel</button>
    <div class="sub" id="trading-gate-error" style="color: var(--danger); margin-top: 8px;"></div>
  </div>`;
}

async function confirmEnableTrading() {
  const phrase = document.getElementById('trading-confirm-input').value;
  try {
    await fetchJSON('/api/trading/enable', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({confirmation_phrase: phrase}),
    });
    tradingGateOpen = false;
    refresh();
  } catch (e) {
    // fetchJSON now throws on the backend's own 400 (wrong phrase / no
    // real account connected) instead of silently resolving with a
    // {detail: ...} body (2026-08-17 fetchJSON fix) - e.message carries
    // the same text that used to be read as result.detail.
    const err = document.getElementById('trading-gate-error');
    if (err) err.textContent = e.message || 'Failed to enable real trading.';
  }
}

async function disableTrading() {
  await fetchJSON('/api/trading/disable', {method: 'POST'});
  refresh();
}

function sourceLabel(raw) {
  if (!raw) return '';
  if (raw === 'simulated') return 'signal: simulated';
  if (raw.startsWith('simulated (')) return 'signal: simulated (feed unavailable, using backup)';
  return `signal: live feed (${raw})`;
}

// Distinguishes "the real Kalshi exchange is closed" from "the strategy just
// hasn't found anything" - otherwise those look identical from the outside.
// GET /exchange/status is public/unauthenticated, so this is always
// available regardless of whether a real account is connected.
function renderExchangeStatus(status) {
  const el = $('exchange-status-badge');
  if (!status) { el.textContent = ''; el.className = ''; return; }
  if (status.trading_active) {
    el.textContent = '● exchange open';
    el.className = 'open';
  } else {
    el.textContent = '● exchange closed';
    el.className = 'closed';
    el.title = 'Kalshi\'s real exchange is not currently accepting trades — paper trading is unaffected, but a quiet signal feed may just mean the market is shut, not that the strategy is stuck.';
  }
}

// Visible staleness/connectivity state (ROADMAP.md Phase 0.5) - tracks
// consecutive refresh() failures and how long it's been since the last one
// that actually succeeded, so a dead connection is as loud as the
// exchange-closed badge rather than a silent console.error. Declared in
// polling-and-websocket.js (the only file that reassigns them, from
// refresh() itself) and imported here for reading.
let refreshTimer = null;
let refreshIntervalMs = 5000;

function scheduleRefreshTimer(ms) {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshIntervalMs = ms;
  refreshTimer = setInterval(refresh, refreshIntervalMs);
}

function renderConnectivity() {
  const el = $('connectivity-badge');
  if (!el) return;
  if (consecutiveRefreshFailures === 0) {
    el.textContent = '';
    el.className = '';
    el.title = '';
    return;
  }
  const secsAgo = Math.max(0, Math.round((Date.now() - lastSuccessfulRefresh) / 1000));
  el.textContent = `⚠ CONNECTION LOST · ${secsAgo}s`;
  el.className = 'stale';
  el.title = `The dashboard hasn't been able to reach the server in ${secsAgo}s `
    + `(${consecutiveRefreshFailures} failed attempt${consecutiveRefreshFailures === 1 ? '' : 's'}) — `
    + `what you're seeing may be out of date. This is a connectivity problem, not a sign the strategy is stuck.`;
}

function renderTradeStreamStatus(status) {
  const el = $('trade-stream-badge');
  if (!el) return;
  if (!status) {
    el.textContent = '';
    el.className = '';
    el.title = '';
    return;
  }
  if (status.mode === 'poll' || !status.enabled) {
    el.textContent = 'trade feed: poll';
    el.className = 'degraded';
    el.title = 'Using the older polled trade-tape path instead of the upstream Kalshi websocket stream.';
    return;
  }
  if (status.connected) {
    el.textContent = 'trade feed: live';
    el.className = 'connected';
    el.title = `Connected to the upstream Kalshi trade websocket (${status.ws_url || 'configured endpoint'}).`;
    return;
  }
  el.textContent = 'trade feed: degraded';
  el.className = status.error ? 'error' : 'degraded';
  el.title = status.error
    ? `Upstream Kalshi trade websocket problem: ${status.error}`
    : 'Upstream Kalshi trade websocket is not currently connected.';
}

// Data-robustness audit finding (2026-08-10): the entire trading-loop tick
// runs under one umbrella try/except that sets state["error"] on failure -
// that field has existed in /api/state's response the whole time, but had
// ZERO frontend consumers anywhere (confirmed by grep), unlike
// renderConnectivity above, which only ever tracks whether the browser's
// own poll reaches the server at all - a completely different failure mode.
// A backend tick could fail repeatedly, forever - aborting real data
// collection (record_snapshots/log_signal/the catalog scan) mid-tick - with
// no visible indication anywhere in the app. Same visual language as
// renderConnectivity (the .stale pulsing badge), not a new pattern.
function renderTickHealth(state) {
  const el = $('tick-health-badge');
  if (!el) return;
  if (!state.error) {
    el.textContent = '';
    el.className = '';
    el.title = '';
    return;
  }
  el.textContent = '⚠ BACKEND TICK FAILING';
  el.className = 'stale';
  el.title = `The server's own trading-loop tick threw an error and skipped its work this cycle - `
    + `real data collection (price snapshots, whale signals, market catalog scanning) may be paused `
    + `until this clears. Last error: ${state.error}`;
}

function renderHalted(risk) {
  const slot = $('halted-banner-slot');
  slot.innerHTML = risk.halted
    ? `<div class="halted-banner">⏸ Trading halted — ${risk.halt_reason}</div>`
    : '';
}

// The Portfolio view's hot-swap: same three panels (equity chart, positions,
// trade history), sourced from either the paper broker or your real Kalshi
// account depending on accountMode.
// Plain-English version of the same comparison, always about paper bets
// specifically — that's what the whale-following strategy actually acts on,
// so it's the thing worth explaining in beginner terms. One sentence per bet:
// what you bet, what the market thinks the odds are, how much you'd lose if
// wrong, and whether whales are doing the same thing.
function renderDummiesPanel(positions, prices, signals, startingBankroll) {
  const el = $('dummies-panel');
  const intro = `<div class="dummies-intro">
    <b>Your Bet</b> — what you actually put paper money on.
    <b>Likelihood</b> — the market's own guess at the odds, from its current price.
    <b>Risk</b> — how much you'd lose if you're wrong.
    <b>🐋 Whale Prediction</b> — what large traders are doing on the same market right now.
  </div>`;

  if (!positions.length) {
    el.innerHTML = intro + '<div class="empty">No open paper bets yet — once the strategy places one, it will be explained here.</div>';
    return;
  }

  const cards = positions.map(p => {
    const label = marketLabel(p.ticker);
    const sideWord = p.side === 'yes' ? 'YES' : 'NO';
    const sideClass = p.side === 'yes' ? 'hl-yes' : 'hl-no';
    const currentYes = prices[p.ticker] ?? p.entry_price;
    const likelihood = Math.round((p.side === 'yes' ? currentYes : (1 - currentYes)) * 100);
    // No local fallback - see the identical fix/comment in renderPositionsTable above.
    const capitalAtRisk = p.cost_basis;
    const riskPct = startingBankroll ? (capitalAtRisk / startingBankroll * 100) : 0;
    const lean = computeWhaleLean(p.ticker, signals);

    let whaleSentence, verdictHtml;
    if (!lean) {
      whaleSentence = `No whales have printed on this market recently, so there's nothing to compare against.`;
      verdictHtml = `<div class="dummies-verdict unknown">— no whale data to compare yet</div>`;
    } else {
      const leanDir = lean.yesPct >= 50 ? 'YES' : 'NO';
      const leanPct = Math.round(leanDir === 'YES' ? lean.yesPct : 100 - lean.yesPct);
      const leanClass = leanDir === 'YES' ? 'hl-yes' : 'hl-no';
      whaleSentence = `🐋 Whales have been leaning <span class="${leanClass}">${leanDir}</span> too
        (<span class="hl-whale">${leanPct}%</span> of ${lean.count} recent big print${lean.count === 1 ? '' : 's'}).`;
      verdictHtml = leanDir === sideWord
        ? `<div class="dummies-verdict aligned">✅ You're betting the same way as the whales.</div>`
        : `<div class="dummies-verdict against">⚠️ You're betting against what the whales are doing.</div>`;
    }

    return `<div class="dummies-card" title="${esc(label.full)}">
      On <b>${esc(label.short)}</b>, you bet <span class="${sideClass}">${sideWord}</span> with
      <b>${p.size.toLocaleString()} contracts</b>. The market thinks this has about a
      <b>${likelihood}% chance</b> of happening. You've put <b>${fmt(capitalAtRisk)}</b> at risk —
      that's <b>${riskPct.toFixed(1)}%</b> of your starting bankroll. ${whaleSentence}
      ${verdictHtml}
    </div>`;
  }).join('');

  el.innerHTML = intro + cards;
}

function renderPortfolio(state, broker) {
  const connected = !!(state.account && state.account.connected);
  $('toggle-btn-real').disabled = !connected;
  if (!connected && accountMode === 'real') {
    _setAccountMode('paper'); // don't strand the view in a mode that's no longer available
    localStorage.setItem('whale-signal-account-mode', 'paper');
  }
  $('toggle-btn-paper').classList.toggle('active', accountMode === 'paper');
  $('toggle-btn-real').classList.toggle('active', accountMode === 'real');

  if (accountMode === 'real') {
    $('equity-title-text').textContent = 'Balance over time (real)';
    $('positions-title-text').textContent = 'Real Positions';
    $('tradelog-title-text').textContent = 'Recent Fills';
    const firstBalance = (state.real_balance_history || [])[0];
    renderEquityChart(state.real_balance_history, firstBalance ? firstBalance.balance : null, 'balance', 'first recorded balance');
    renderRealPositions(state.account ? state.account.positions : null);
    renderRealFills(state.account ? state.account.fills : null);
    $('real-orders-panel').style.display = 'block';
    if (!realOrdersLoaded) loadRealOrders();  // direct page-load landing straight in real mode
  } else {
    $('equity-title-text').textContent = 'Equity over time (paper)';
    $('positions-title-text').textContent = 'Open Positions';
    $('tradelog-title-text').textContent = 'Trade Log';
    renderPositions(broker.positions || [], state.latest_prices || {}, broker.recent_trades || [], state.signal_feed || []);
    renderTrades(broker.recent_trades || [], state.latest_prices || {});
    renderEquityChart(state.equity_history, broker.starting_bankroll, 'equity', 'starting bankroll');
    $('real-orders-panel').style.display = 'none';
  }

  // Always about paper bets specifically, regardless of which mode is toggled
  // above — this explains what the whale-following strategy itself is doing.
  renderDummiesPanel(broker.positions || [], state.latest_prices || {}, state.signal_feed || [], broker.starting_bankroll);

  renderShadow(state.shadow);
}

function renderShadow(shadow) {
  const intro = $('shadow-intro');
  const count = $('shadow-count');
  const list = $('shadow-list');
  if (!shadow || !shadow.active) {
    intro.style.display = 'none';
    count.textContent = '';
    list.innerHTML = `<div class="empty">Off — set <code>mode: shadow</code> (or <code>live</code>) in Controls to start logging what real trades this strategy would have made.</div>`;
    return;
  }
  intro.style.display = '';
  count.textContent = `(${shadow.total_shadow_trades})`;
  const trades = shadow.recent_trades || [];

  const halted = shadow.halted
    ? `<div class="halted-banner" style="margin-bottom:10px;">⏸ Shadow trading halted — ${esc(shadow.halt_reason || '')}</div>`
    : '';

  if (!trades.length) {
    list.innerHTML = halted + '<div class="empty">No intended trades logged yet — check Strategy Decisions to see why signals are being skipped</div>';
    return;
  }

  list.innerHTML = halted + trades.map(t => {
    const label = marketLabel(t.ticker);
    const when = new Date(t.timestamp * 1000).toLocaleTimeString();
    const usingRealBankroll = t.bankroll_source === 'real_account';
    const sourceNote = usingRealBankroll ? 'real balance' : 'fallback: configured bankroll, no real account connected';
    return `
    <div class="trade-row" title="${esc(label.full)}">
      <div class="name">${esc(label.short)} <span class="side-tag ${t.side}">${t.side}</span>
        ${contextLineHTML(t.ticker, t.side)}
      </div>
      <div class="nums">
        <span>${t.size.toLocaleString()} ct @ ${(t.price*100).toFixed(0)}¢ · sized against ${fmt(t.reference_bankroll)} (${sourceNote})</span>
        <span style="color: var(--muted)">${when}</span>
      </div>
    </div>`;
  }).join('');
}

export { TRADING_PHRASE, confirmEnableTrading, disableTrading, refreshIntervalMs, refreshTimer, renderConnectivity, renderDummiesPanel, renderExchangeStatus, renderHalted, renderPortfolio, renderShadow, renderTickHealth, renderTradeStreamStatus, scheduleRefreshTimer, sourceLabel, toggleTradingGate, tradingGateHTML, tradingGateOpen };

// Exposed for inline HTML event handlers (onclick=/onchange=/oninput=,
// including ones built indirectly via a caller-supplied onclick-string
// parameter - see shared-utils.js's header). Every top-level function in
// this file is exposed (cheap, harmless if unused); state objects only the
// specific ones confirmed to be read/mutated directly from a handler.
window.confirmEnableTrading = confirmEnableTrading;
window.disableTrading = disableTrading;
window.renderConnectivity = renderConnectivity;
window.renderDummiesPanel = renderDummiesPanel;
window.renderExchangeStatus = renderExchangeStatus;
window.renderHalted = renderHalted;
window.renderPortfolio = renderPortfolio;
window.renderShadow = renderShadow;
window.renderTickHealth = renderTickHealth;
window.renderTradeStreamStatus = renderTradeStreamStatus;
window.scheduleRefreshTimer = scheduleRefreshTimer;
window.sourceLabel = sourceLabel;
window.toggleTradingGate = toggleTradingGate;
window.tradingGateHTML = tradingGateHTML;
