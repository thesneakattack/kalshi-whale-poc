import { $, advToggleHTML, contextLineHTML, esc, fetchJSON, isAdvanced, marketContext, marketLabel, marketTitles, sortHeaderHTML, sortRows } from './shared-utils.js';

// Part of index.html's JS split - see shared-utils.js's header for the
// load-order/shared-global-scope rationale common to all these files.
// This file: Whale Watch tab - live trade tape, whale track-record stats,
// and the signal-history/signal-clusters on-demand loaders.
let lastTradeTape = [];
let tradeTapeMinSize = 0;

function tradeRowHTML(t, plainEnglish) {
  const label = marketLabel(t.ticker);
  const side = t.taker_side === 'no' ? 'no' : 'yes';
  const priceDollars = side === 'no' ? t.no_price_dollars : t.yes_price_dollars;
  const price = priceDollars != null ? (parseFloat(priceDollars) * 100).toFixed(0) + '¢' : '—';
  const count = t.count_fp != null ? Math.round(parseFloat(t.count_fp)).toLocaleString() : '—';
  const when = t.created_time ? new Date(t.created_time).toLocaleTimeString() : '';
  const name = plainEnglish
    ? `Someone bought <b>${count}</b> <span class="side-tag ${side}">${side}</span> on ${esc(label.short)}`
    : `${esc(label.short)} <span class="side-tag ${side}">${side}</span>`;
  return `<div class="trade-row" title="${esc(label.full)}">
    <div class="name">${name}
      ${contextLineHTML(t.ticker, side)}
    </div>
    <div class="nums"><span>${count} ct @ ${price}</span><span style="color: var(--muted)">${esc(when)}</span></div>
  </div>`;
}

// tradeTapeMinSize/lastTradeTape are read (and tradeTapeMinSize written)
// straight from the Whale Watch tab's min-size filter input - a bare
// inline reassignment of a module-scoped `let` wouldn't touch this
// module's own binding (it'd create/hit a same-named property on `window`
// instead), and lastTradeTape gets wholesale-reassigned on every
// renderTradeTape() call, so a static `window.lastTradeTape = ...` would
// go stale after the first trade-tape update. This wrapper keeps both
// reads/writes flowing through the real bindings.
function _setTradeTapeMinSize(v) {
  tradeTapeMinSize = v;
  renderTradeTape(lastTradeTape);
}

function renderTradeTape(trades) {
  lastTradeTape = trades || [];
  $('trade-tape-toggle').innerHTML = advToggleHTML('trade-tape');
  const el = $('trade-tape');
  if (!lastTradeTape.length) {
    el.innerHTML = '<div class="empty">No recent trades on watched markets.</div>';
    return;
  }

  if (!isAdvanced('trade-tape')) {
    // Simple: only the notably large prints (top quartile by size in this
    // batch), described in plain English rather than a raw table.
    const sizes = lastTradeTape.map(t => parseFloat(t.count_fp) || 0).sort((a, b) => b - a);
    const cutoff = sizes[Math.floor(sizes.length * 0.25)] || 0;
    const big = cutoff > 0 ? lastTradeTape.filter(t => (parseFloat(t.count_fp) || 0) >= cutoff) : lastTradeTape;
    el.innerHTML = big.slice(0, 8).map(t => tradeRowHTML(t, true)).join('');
    return;
  }

  // Advanced: the full tape, filterable by minimum size - matches how
  // dedicated whale-tracker products (Polywhaler, WhaleScanr) let you filter
  // their tape, researched directly rather than invented (see ROADMAP.md).
  const filterHtml = `<div style="margin-bottom:10px;">
    <label style="font-size:11px; color:var(--muted);">Min size (contracts):
      <input type="number" id="trade-tape-min-size" min="0" step="1" value="${tradeTapeMinSize}" style="width:80px; margin-left:6px; background:var(--panel); border:1px solid var(--panel-border); color:var(--text); padding:4px 6px; border-radius:4px; font-family:var(--mono);"
        onchange="_setTradeTapeMinSize(parseFloat(this.value) || 0);">
    </label>
  </div>`;
  const filtered = lastTradeTape.filter(t => (parseFloat(t.count_fp) || 0) >= tradeTapeMinSize);
  el.innerHTML = filterHtml + (filtered.length
    ? filtered.map(t => tradeRowHTML(t, false)).join('')
    : '<div class="empty">No trades at or above this size.</div>');
}

// Real, persisted history (services/signal_log.py) — not derived from the
// last-50-in-memory signal feed, so it survives restarts and actually means
// something as "last 30 days" once enough time has passed.
function renderWhaleTrackRecord(stats, whaleSource) {
  if (!stats) return;
  const winRate = (stats.win_rate !== null && stats.win_rate !== undefined) ? `${stats.win_rate.toFixed(0)}%` : '—';
  const since = stats.tracking_since ? new Date(stats.tracking_since * 1000).toLocaleDateString() : '—';
  $('whale-track-record').innerHTML = `
    <div class="cell"><div class="lbl">Signals Logged (30d)</div><div class="val">${stats.total_signals}</div></div>
    <div class="cell"><div class="lbl">Resolved</div><div class="val">${stats.resolved}</div></div>
    <div class="cell"><div class="lbl">Win Rate</div><div class="val">${winRate}</div></div>
    <div class="cell"><div class="lbl">Tracking Since</div><div class="val" style="font-size:13px;">${since}</div></div>
  `;
  const simulated = (whaleSource || '').startsWith('simulated');
  const note = $('whale-track-note');
  if (simulated) {
    note.textContent = 'Currently tracking the built-in simulator, not real whales — connect a live feed in Connected Accounts to start tracking real whale accuracy.';
  } else if (stats.resolved === 0) {
    note.textContent = 'No signals have resolved yet — markets need to settle before "correct" can be measured. Check back later.';
  } else {
    note.textContent = `Based on ${stats.resolved} resolved signal${stats.resolved === 1 ? '' : 's'} out of ${stats.total_signals} logged in the last ${stats.window_days} days.`;
  }
}

// Real, browsable signal history (ROADMAP.md Phase 0.5) - WhaleScanr's
// framing, copied directly: "every flag and how it settled, misses
// included," not just renderWhaleTrackRecord's rolled-up percentage above.
// Backed by GET /api/signals/history, a separate on-demand/paginated fetch
// (services/signal_log.py's persisted data), not part of the /api/state
// poll cycle - loaded once when the Whale Watch tab is opened (see
// showView()) and again on filter/sort/page changes, not every 5s poll,
// so paging through history doesn't get reset out from under you.
let signalHistoryRows = [];
let signalHistoryTotal = 0;
let signalHistoryFilter = { resolvedOnly: false, sortKey: 'seen_at', sortDir: 'desc', offset: 0, limit: 25 };

async function loadSignalHistory() {
  const el = $('signal-history');
  if (!el) return;
  const params = new URLSearchParams({
    limit: signalHistoryFilter.limit,
    offset: signalHistoryFilter.offset,
    resolved_only: signalHistoryFilter.resolvedOnly,
  });
  try {
    const resp = await fetchJSON(`/api/signals/history?${params}`);
    signalHistoryRows = resp.signals || [];
    signalHistoryTotal = resp.total || 0;
    // Older/resolved signals often reference tickers that have long since
    // rotated off the live watchlist and won't be in /api/state's scoped
    // market_titles - the backend enriches this response with titles for
    // exactly the tickers on this page, merged in the same way.
    Object.assign(marketTitles, resp.market_titles || {});
  } catch (e) {
    el.innerHTML = '<div class="empty">Failed to load signal history.</div>';
    return;
  }
  renderSignalHistory();
}

function renderSignalHistory() {
  const el = $('signal-history');
  if (!el) return;
  $('signal-history-count').textContent = signalHistoryTotal ? `(${signalHistoryTotal})` : '';

  const filterHtml = `<div class="table-filter-row">
    <label><input type="checkbox" ${signalHistoryFilter.resolvedOnly ? 'checked' : ''}
      onchange="signalHistoryFilter.resolvedOnly = this.checked; signalHistoryFilter.offset = 0; loadSignalHistory();">
      Resolved only</label>
  </div>`;

  if (!signalHistoryRows.length) {
    el.innerHTML = filterHtml + `<div class="empty">${signalHistoryFilter.resolvedOnly ? 'No resolved signals yet.' : 'No signals logged yet.'}</div>`;
    return;
  }

  const rows = sortRows(signalHistoryRows, signalHistoryFilter.sortKey, signalHistoryFilter.sortDir);
  const rowsHtml = rows.map(r => {
    const label = marketLabel(r.ticker);
    const when = new Date(r.seen_at * 1000).toLocaleString();
    let outcome;
    if (!r.resolved) outcome = '<span style="color:var(--muted);">pending</span>';
    else if (r.correct) outcome = '<span style="color:var(--yes); font-weight:700;">✓ correct</span>';
    else outcome = '<span style="color:var(--no); font-weight:700;">✗ miss</span>';
    // Smaller second line under the title, not crammed onto one - direct
    // report (2026-08-10): "Cleveland vs Detroit Winner? YES" with no
    // indication of who YES means, plus a separate report that cramming
    // everything onto one line was making rows extremely wide.
    const ctx = marketContext(r.ticker, r.side);
    const subLine = ctx.positionMeans
      ? `<div style="font-size:10px; color:var(--muted); margin-top:2px;">Betting: ${esc(ctx.positionMeans)}</div>` : '';
    return `<tr title="${esc(label.full)}">
      <td>${esc(label.short)}${subLine}</td>
      <td><span class="side-tag ${r.side}">${esc(r.side)}</span></td>
      <td>${r.size.toLocaleString()}</td>
      <td>${r.price != null ? (r.price * 100).toFixed(0) + '¢' : '—'}</td>
      <td>${(r.confidence * 100).toFixed(0)}%</td>
      <td>${esc(when)}</td>
      <td>${outcome}</td>
    </tr>`;
  }).join('');

  const totalPages = Math.max(1, Math.ceil(signalHistoryTotal / signalHistoryFilter.limit));
  const currentPage = Math.floor(signalHistoryFilter.offset / signalHistoryFilter.limit) + 1;
  const pager = `<div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px; font-size:11px; color:var(--muted);">
    <span>Page ${currentPage} of ${totalPages}</span>
    <span>
      <button ${signalHistoryFilter.offset <= 0 ? 'disabled' : ''}
        onclick="signalHistoryFilter.offset = Math.max(0, signalHistoryFilter.offset - signalHistoryFilter.limit); loadSignalHistory();">← Prev</button>
      <button ${currentPage >= totalPages ? 'disabled' : ''}
        onclick="signalHistoryFilter.offset += signalHistoryFilter.limit; loadSignalHistory();">Next →</button>
    </span>
  </div>`;

  el.innerHTML = filterHtml + `<table class="positions-table sortable">
    <thead><tr>
      ${sortHeaderHTML(signalHistoryFilter, 'ticker', 'Market', "toggleSort(signalHistoryFilter, 'ticker', renderSignalHistory)")}
      <th>Side</th>
      ${sortHeaderHTML(signalHistoryFilter, 'size', 'Size', "toggleSort(signalHistoryFilter, 'size', renderSignalHistory)")}
      ${sortHeaderHTML(signalHistoryFilter, 'price', 'Price', "toggleSort(signalHistoryFilter, 'price', renderSignalHistory)")}
      ${sortHeaderHTML(signalHistoryFilter, 'confidence', 'Confidence', "toggleSort(signalHistoryFilter, 'confidence', renderSignalHistory)")}
      ${sortHeaderHTML(signalHistoryFilter, 'seen_at', 'Seen', "toggleSort(signalHistoryFilter, 'seen_at', renderSignalHistory)")}
      <th>Outcome</th>
    </tr></thead>
    <tbody>${rowsHtml}</tbody>
  </table>` + pager;
}

// Persistent flow clustering (ROADMAP.md P2 stretch item) - WhaleScanr's
// real approach to a genuine constraint this app already respects:
// Kalshi's trade tape is anonymous, no account/identity data exists, so
// this groups same-ticker/same-side prints close in time and size into
// probable-same-actor "clusters," never claiming verified identity.
// Backed by GET /api/signals/clusters, the persisted signal log - same
// separate-on-demand-fetch pattern as loadSignalHistory above, loaded
// once when the Whale Watch tab opens.
async function loadSignalClusters() {
  const el = $('signal-clusters');
  if (!el) return;
  try {
    const resp = await fetchJSON('/api/signals/clusters?hours=24');
    Object.assign(marketTitles, resp.market_titles || {});
    renderSignalClusters(resp.clusters || []);
  } catch (e) {
    el.innerHTML = '<div class="empty">Failed to load.</div>';
  }
}

function renderSignalClusters(clusters) {
  const el = $('signal-clusters');
  $('signal-clusters-count').textContent = clusters.length ? `(${clusters.length})` : '';
  if (!clusters.length) {
    el.innerHTML = '<div class="empty">No probable accumulation detected in the last 24h.</div>';
    return;
  }
  el.innerHTML = clusters.map(c => {
    const label = marketLabel(c.ticker);
    const spanMin = Math.max(1, Math.round(c.span_sec / 60));
    const pct = Math.round(c.cluster_confidence * 100);
    return `<div class="signal-card" style="cursor:pointer;"
        title="${esc(label.full)} — inferred from timing/size similarity, not verified identity"
        onclick="openMarketDetail('${esc(c.ticker)}', '')">
      <div class="top-row">
        <span>${esc(label.short)} <span class="side-tag ${c.side}">${c.side}</span></span>
        <span style="color: var(--whale)">${c.total_size.toLocaleString()} ct total</span>
      </div>
      ${contextLineHTML(c.ticker, c.side)}
      <div style="color: var(--muted)">${c.print_count} prints over ${spanMin} min · ~${pct}% cluster confidence</div>
    </div>`;
  }).join('');
}

// History tab (direct request) - closed-trade record, win/loss rates, what
// closed each position, and sample-size-hedged tuning hints. Backed by
// GET /api/trading-history, a separate on-demand/paginated fetch (like
// loadSignalHistory above), loaded when this tab opens.

export { _setTradeTapeMinSize, lastTradeTape, loadSignalClusters, loadSignalHistory, renderSignalClusters, renderSignalHistory, renderTradeTape, renderWhaleTrackRecord, signalHistoryFilter, signalHistoryRows, signalHistoryTotal, tradeRowHTML, tradeTapeMinSize };

// Exposed for inline HTML event handlers (onclick=/onchange=/oninput=,
// including ones built indirectly via a caller-supplied onclick-string
// parameter - see shared-utils.js's header). Every top-level function in
// this file is exposed (cheap, harmless if unused); state objects only the
// specific ones confirmed to be read/mutated directly from a handler.
window._setTradeTapeMinSize = _setTradeTapeMinSize;
window.loadSignalClusters = loadSignalClusters;
window.loadSignalHistory = loadSignalHistory;
window.renderSignalClusters = renderSignalClusters;
window.renderSignalHistory = renderSignalHistory;
window.renderTradeTape = renderTradeTape;
window.renderWhaleTrackRecord = renderWhaleTrackRecord;
window.tradeRowHTML = tradeRowHTML;
window.signalHistoryFilter = signalHistoryFilter;
