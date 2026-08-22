import { computeWhaleLean, divergencePts } from './equity-and-cards.js';
import { seriesMeta, terminalLatestPrices } from './polling-and-websocket.js';
import { $, advToggleHTML, comboLegsHTML, contextLineHTML, esc, eventLiveDataLineHTML, eventTitles, fetchJSON, fmt, isAdvanced, liveBadgeHTML, marketContext, marketLabel, marketTaxonomyHTML, marketTitles, priceChangeHTML, sortHeaderHTML, sortRows } from './shared-utils.js';

// Part of index.html's JS split - see shared-utils.js's header for the
// load-order/shared-global-scope rationale common to all these files.
// This file: feed-list smooth-rerender helper, Terminal-tab signal feed +
// signal/decision table rendering and their filter state, and the
// Portfolio-tab open-positions rendering (whale-confidence badges,
// reason-details, netting groups) that reuses the same feed-list helper.
const _feedListState = {};

function renderFeedListSmooth(containerId, items, buildCardHtml, getId) {
  const el = $(containerId);
  const prevIds = _feedListState[containerId] || [];
  const newIds = items.map(getId);
  _feedListState[containerId] = newIds;

  if (prevIds.length && newIds.length >= prevIds.length) {
    const offset = newIds.length - prevIds.length;
    let tailMatches = true;
    for (let i = 0; i < prevIds.length; i++) {
      if (newIds[i + offset] !== prevIds[i]) { tailMatches = false; break; }
    }
    if (tailMatches) {
      if (offset > 0) el.insertAdjacentHTML('afterbegin', items.slice(0, offset).map(buildCardHtml).join(''));
      return;
    }
  }
  el.innerHTML = items.map(buildCardHtml).join('');
}

// Signal feed filters (ROADMAP.md Phase 0.5), matched against Polywhaler's
// proven set rather than invented from scratch: time range, buy/sell, sort
// by recency or "impact," a position-grouping toggle. "Impact" isn't a
// field this app tracks directly - defined here as size x confidence, a
// reasonable proxy for "how much this print should matter to you" that
// rewards neither a huge-but-low-confidence print nor a tiny-but-certain
// one on its own.
let signalFilter = { timeRange: 'all', side: '', sortKey: 'timestamp', sortDir: 'desc', groupByPosition: false };
let signalDecisionFilter = { timeRange: 'all', side: '', status: '', sortKey: 'timestamp', sortDir: 'desc', groupByPosition: false };
let lastSignals = [];

const SIGNAL_TIME_RANGES = { 'all': null, '1h': 3600, '6h': 21600, '24h': 86400, '7d': 7 * 86400, '30d': 30 * 86400 };

function signalImpact(s) {
  return s.size * s.confidence;
}

function renderSignals(signals) {
  $('signal-feed-filter').innerHTML = `<div class="table-filter-row">
    <select onchange="signalFilter.timeRange = this.value; _rerenderSignalsFilter();">
      ${Object.keys(SIGNAL_TIME_RANGES).map(k => `<option value="${k}" ${signalFilter.timeRange === k ? 'selected' : ''}>${k === 'all' ? 'All time' : 'Last ' + k}</option>`).join('')}
    </select>
    <select onchange="signalFilter.side = this.value; _rerenderSignalsFilter();">
      <option value="" ${signalFilter.side === '' ? 'selected' : ''}>Yes + No</option>
      <option value="yes" ${signalFilter.side === 'yes' ? 'selected' : ''}>Yes only</option>
      <option value="no" ${signalFilter.side === 'no' ? 'selected' : ''}>No only</option>
    </select>
    <select onchange="signalFilter.sortKey = this.value; _rerenderSignalsFilter();">
      <option value="timestamp" ${signalFilter.sortKey === 'timestamp' ? 'selected' : ''}>Sort: recency</option>
      <option value="impact" ${signalFilter.sortKey === 'impact' ? 'selected' : ''}>Sort: impact</option>
    </select>
    <label><input type="checkbox" ${signalFilter.groupByPosition ? 'checked' : ''} onchange="signalFilter.groupByPosition = this.checked; _rerenderSignalsFilter();"> Group by market</label>
  </div>`;

  lastSignals = signals || [];
  const listEl = $('signal-feed-list');
  if (!lastSignals.length) { listEl.innerHTML = '<div class="empty">Waiting for signals…</div>'; _feedListState['signal-feed-list'] = []; return; }

  const rangeSec = SIGNAL_TIME_RANGES[signalFilter.timeRange];
  const cutoff = rangeSec ? (Date.now() / 1000 - rangeSec) : null;
  let rows = lastSignals.filter(s => {
    if (cutoff !== null && s.timestamp < cutoff) return false;
    if (signalFilter.side && s.side !== signalFilter.side) return false;
    return true;
  });

  if (!rows.length) { listEl.innerHTML = '<div class="empty">No signals match these filters.</div>'; _feedListState['signal-feed-list'] = []; return; }

  if (signalFilter.groupByPosition) {
    // One card per ticker+side, aggregating every matching signal into it -
    // "how many separate prints built this" is the same accumulation
    // question WhaleScanr's clustering answers, just without the
    // statistical same-actor inference (see the P2 stretch item).
    const groups = new Map();
    rows.forEach(s => {
      const key = `${s.ticker}::${s.side}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(s);
    });
    rows = Array.from(groups.values()).map(g => {
      const latest = g.reduce((a, b) => (a.timestamp > b.timestamp ? a : b));
      const totalSize = g.reduce((sum, s) => sum + s.size, 0);
      const avgConfidence = g.reduce((sum, s) => sum + s.confidence, 0) / g.length;
      return { ...latest, size: totalSize, confidence: avgConfidence, _printCount: g.length };
    });
  }

  rows = rows.slice().sort((a, b) => {
    const av = signalFilter.sortKey === 'impact' ? signalImpact(a) : a.timestamp;
    const bv = signalFilter.sortKey === 'impact' ? signalImpact(b) : b.timestamp;
    return bv - av; // both recency and impact read best-first descending
  });

  // Impact tag tiers are relative to THIS batch of visible signals, not a
  // flat cutoff - the same lesson learned tuning the whale simulator's own
  // sizing (see ROADMAP.md): what counts as "high impact" depends on what
  // else is on the feed right now, not one fixed number good for every
  // config/market mix.
  const impactValues = rows.map(signalImpact).sort((a, b) => a - b);
  const impactTier = (s) => {
    if (!impactValues.length) return 'low';
    const rank = impactValues.filter(v => v <= signalImpact(s)).length / impactValues.length;
    return rank >= 0.8 ? 'high' : rank >= 0.4 ? 'medium' : 'low';
  };

  const buildSignalCardHtml = (s) => {
    const label = marketLabel(s.ticker);
    const stealthTag = s._printCount > 1
      ? `<span class="cat-tag" title="${s._printCount} separate prints on this market/side, combined">${s._printCount}&times; prints</span> `
      : '';
    const tier = impactTier(s);
    const impactTag = `<span class="impact-tag ${tier}" title="Impact tier relative to what's currently on the feed (size x confidence)">${tier}</span>`;
    // price is always the yes price (see whale_simulator.py) - a "no" print's
    // real dollar size is size*(1-price), not size*price, same convention
    // as PaperBroker.cost_basis/open_position's unit_cost.
    const dollarSize = s.price != null ? fmt(s.size * (s.side === 'yes' ? s.price : (1 - s.price))) : null;
    const sEt = (marketTitles[s.ticker] && marketTitles[s.ticker].event_ticker) || '';
    return `
    <div class="signal-card" style="cursor:pointer;" title="${esc(label.full)} — click to view full market detail" onclick="openMarketDetail('${esc(s.ticker)}', '${esc(sEt)}')">
      <div class="top-row">
        <span>${stealthTag}${esc(label.short)} <span class="side-tag ${s.side}">${s.side}</span></span>
        <span style="color: var(--whale)">${s.size.toLocaleString()} ct${dollarSize ? ' · ' + dollarSize : ''}</span>
      </div>
      ${contextLineHTML(s.ticker, s.side)}
      <div style="color: var(--muted)">@ ${(s.price*100).toFixed(0)}¢ · confidence ${(s.confidence*100).toFixed(0)}% ${impactTag}</div>
      <div class="conf-bar"><div class="conf-fill" style="width:${s.confidence*100}%"></div></div>
    </div>
  `;
  };

  // Fast-path smoothness (see renderFeedListSmooth) only actually applies
  // when rows are in the same append-only order the server produces them
  // in - true for the default recency sort with no grouping, not for
  // impact-sorted or grouped views, which legitimately reorder/recompute
  // every tick and fall back to a full rebuild there on their own.
  renderFeedListSmooth('signal-feed-list', rows, buildSignalCardHtml, s => s.id);
}

let lastDecisions = [];
let decisionFilter = { query: '', action: '', sortKey: 'timestamp', sortDir: 'desc' };

// Plain-English skip reasons (ROADMAP.md P1) - strategy_engine.py's _skip()
// reasons are concise technical strings by design (fine for logs/Advanced
// mode), not sentences a first-timer would understand ("confidence 0.34
// below threshold"). Pattern-matched here rather than changed at the
// source, same Simple/Advanced split as everywhere else in this app:
// Advanced still shows the exact raw string (see renderDecisionTable),
// Simple gets a translated sentence. Falls back to the raw string for
// anything unrecognized rather than hiding or guessing at it - a reason
// this doesn't know how to translate yet should still be visible, not
// silently dropped.
function plainEnglishSkipReason(reason) {
  if (!reason) return 'Skipped for an unspecified reason.';
  let m;
  if ((m = /^halted: (.+)$/.exec(reason))) {
    return `Trading is currently paused — ${m[1]}`;
  }
  if (reason === 'market is not currently live') {
    return `Skipped because this market isn't live right now — this strategy is configured to only trade markets currently in progress.`;
  }
  if ((m = /^series "(.+)" is manually excluded$/.exec(reason))) {
    return `Skipped — "${m[1]}"-type markets are on your manually excluded list in Config, regardless of confidence.`;
  }
  if ((m = /^confidence ([\d.]+) below threshold \(([\d.]+)( - longshot zone)?\)$/.exec(reason))) {
    const pct = Math.round(parseFloat(m[1]) * 100);
    const thresholdPct = Math.round(parseFloat(m[2]) * 100);
    const longshotNote = m[3]
      ? ` This market's price is in "longshot" territory (very close to 0¢ or 100¢), which real Kalshi data shows is `
        + `systematically overpriced — so the bar is raised here specifically, not just the usual minimum.`
      : '';
    return `Skipped — this whale print only scored ${pct}% confidence, below the ${thresholdPct}% minimum needed to act on it.${longshotNote}`;
  }
  if ((m = /^whale win rate for "(.+)"-type markets is ([\d.]+)% over (\d+) resolved signals \(below ([\d.]+)% minimum\) — avoiding$/.exec(reason))) {
    const seriesMetaEntry = seriesMeta[m[1]];
    const seriesName = seriesMetaEntry && seriesMetaEntry.title ? seriesMetaEntry.title : m[1];
    return `Skipped — whales have only been right ${m[2]}% of the time recently on "${seriesName}"-type markets `
      + `(out of ${m[3]} resolved signals), below the ${m[4]}% bar this strategy requires before trusting them here.`;
  }
  if (reason === 'position already open on this market') {
    return `Skipped — this strategy already has an open position on this market and won't open a second one until the first closes.`;
  }
  if (reason === 'cooldown active for this market') {
    return `Skipped — this strategy already traded this market recently and is waiting out its cooldown before trading it again.`;
  }
  if (reason === 'position size rounds to zero') {
    return `Skipped — the bankroll is too small (or the price too high) to afford even one contract at the configured position size.`;
  }
  return reason;
}

// lastSignals gets wholesale-reassigned on every renderSignals() call (see
// above) - a one-time `window.lastSignals = lastSignals` done at module
// load would go stale after the very first refresh, so the Signal Feed's
// inline onchange filters call this wrapper instead of reading lastSignals
// directly, guaranteeing they always see the current value.
function _rerenderSignalsFilter() {
  renderSignals(lastSignals);
}

function renderSignalDecisionFeed(signalFeed, decisionFeed, latestPrices) {
  latestPrices = latestPrices || terminalLatestPrices;
  lastDecisions = decisionFeed || [];
  $('signal-decision-toggle').innerHTML = advToggleHTML('decisions');
  const listEl = $('signal-decision-feed-list');

  if (isAdvanced('decisions')) {
    delete _feedListState['signal-decision-feed-list'];
    renderDecisionTable();
    return;
  }

  $('signal-decision-feed-filter').innerHTML = `<div class="table-filter-row">
    <select onchange="signalDecisionFilter.timeRange = this.value; _rerenderTerminalFeed();">
      ${Object.keys(SIGNAL_TIME_RANGES).map(k => `<option value="${k}" ${signalDecisionFilter.timeRange === k ? 'selected' : ''}>${k === 'all' ? 'All time' : 'Last ' + k}</option>`).join('')}
    </select>
    <select onchange="signalDecisionFilter.side = this.value; _rerenderTerminalFeed();">
      <option value="" ${signalDecisionFilter.side === '' ? 'selected' : ''}>Yes + No</option>
      <option value="yes" ${signalDecisionFilter.side === 'yes' ? 'selected' : ''}>Yes only</option>
      <option value="no" ${signalDecisionFilter.side === 'no' ? 'selected' : ''}>No only</option>
    </select>
    <select onchange="signalDecisionFilter.status = this.value; _rerenderTerminalFeed();">
      <option value="" ${signalDecisionFilter.status === '' ? 'selected' : ''}>All statuses</option>
      <option value="opening" ${signalDecisionFilter.status === 'opening' ? 'selected' : ''}>Opening</option>
      <option value="skipped" ${signalDecisionFilter.status === 'skipped' ? 'selected' : ''}>Skipped</option>
      <option value="won" ${signalDecisionFilter.status === 'won' ? 'selected' : ''}>Won</option>
      <option value="lost" ${signalDecisionFilter.status === 'lost' ? 'selected' : ''}>Lost</option>
    </select>
    <select onchange="signalDecisionFilter.sortKey = this.value; _rerenderTerminalFeed();">
      <option value="timestamp" ${signalDecisionFilter.sortKey === 'timestamp' ? 'selected' : ''}>Sort: recency</option>
      <option value="impact" ${signalDecisionFilter.sortKey === 'impact' ? 'selected' : ''}>Sort: impact</option>
      <option value="divergence" ${signalDecisionFilter.sortKey === 'divergence' ? 'selected' : ''}>Sort: divergence</option>
    </select>
    <label><input type="checkbox" ${signalDecisionFilter.groupByPosition ? 'checked' : ''} onchange="signalDecisionFilter.groupByPosition = this.checked; _rerenderTerminalFeed();"> Group by market</label>
  </div>`;

  const rows = (decisionFeed || []).map((d, idx) => {
    const signal = d.signal || {};
    const ticker = signal.ticker || d.ticker || '';
    const side = signal.side || (d.trade && d.trade.side) || 'yes';
    const timestamp = signal.timestamp || (d.trade && d.trade.timestamp) || 0;
    let status = 'unknown';
    if (d.action === 'trade') {
      status = 'opening';
    } else if (d.action === 'skip') {
      status = 'skipped';
    } else if (d.action === 'close') {
      if (/won/i.test(d.reason || '')) {
        status = 'won';
      } else if (/lost/i.test(d.reason || '')) {
        status = 'lost';
      } else {
        status = 'closed';
      }
    } else {
      status = d.action;
    }
    return { ...d, signal, ticker, side, timestamp, status, _uid: `${d.action}-${ticker}-${timestamp}-${idx}` };
  });

  const rangeSec = SIGNAL_TIME_RANGES[signalDecisionFilter.timeRange];
  const cutoff = rangeSec ? (Date.now() / 1000 - rangeSec) : null;
  let filtered = rows.filter(d => {
    if (cutoff !== null && d.timestamp < cutoff) return false;
    if (signalDecisionFilter.side && d.side !== signalDecisionFilter.side) return false;
    if (signalDecisionFilter.status && d.status !== signalDecisionFilter.status) return false;
    return true;
  });

  if (!filtered.length) { listEl.innerHTML = '<div class="empty">No signals or decisions match these filters.</div>'; _feedListState['signal-decision-feed-list'] = []; return; }

  if (signalDecisionFilter.groupByPosition) {
    const groups = new Map();
    filtered.forEach(d => {
      const key = `${d.ticker}::${d.side}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(d);
    });
    filtered = Array.from(groups.values()).map(group => {
      const latest = group.reduce((a, b) => (a.timestamp > b.timestamp ? a : b));
      const totalSize = group.reduce((sum, d) => sum + ((d.signal && d.signal.size) || (d.trade && d.trade.size) || 0), 0);
      const avgConfidence = group.reduce((sum, d) => sum + ((d.signal && d.signal.confidence) || 0), 0) / group.length;
      return { ...latest, size: totalSize, confidence: avgConfidence, _printCount: group.length };
    });
  }

  filtered = filtered.slice().sort((a, b) => {
    const sortVal = (d) => {
      if (signalDecisionFilter.sortKey === 'impact') return (d.signal && d.signal.size && d.signal.confidence) ? d.signal.size * d.signal.confidence : 0;
      if (signalDecisionFilter.sortKey === 'divergence') return divergencePts(d, latestPrices) ?? 0;
      return d.timestamp;
    };
    const av = sortVal(a);
    const bv = sortVal(b);
    return (signalDecisionFilter.sortDir === 'asc' ? av - bv : bv - av);
  });

  const impactValues = filtered.map(d => ((d.signal && d.signal.size && d.signal.confidence) ? d.signal.size * d.signal.confidence : 0)).sort((a, b) => a - b);
  const impactTier = (d) => {
    if (!impactValues.length) return 'low';
    const rank = impactValues.filter(v => v <= ((d.signal && d.signal.size && d.signal.confidence) ? d.signal.size * d.signal.confidence : 0)).length / impactValues.length;
    return rank >= 0.8 ? 'high' : rank >= 0.4 ? 'medium' : 'low';
  };

  const buildRowHtml = (d) => {
    const label = marketLabel(d.ticker);
    const eventT = (marketTitles[d.ticker] && marketTitles[d.ticker].event_ticker) || '';
    const liveMarker = liveBadgeHTML({ event_ticker: eventT });
    const closeTime = d.signal && d.signal.close_time ? new Date(d.signal.close_time) : null;
    const secondsToClose = closeTime ? (closeTime.getTime() - Date.now()) / 1000 : null;
    const closingBadge = secondsToClose !== null && secondsToClose > 0 && secondsToClose <= 900 ? '<span class="closing-badge">CLOSING SOON</span>' : '';
    const tier = impactTier(d);
    const impactTag = `<span class="impact-tag ${tier}" title="Impact tier relative to what's currently on the feed (size x confidence)">${tier}</span>`;
    const dPts = divergencePts(d, latestPrices);
    const divergenceTag = (dPts !== null && dPts >= 5)
      ? `<span class="impact-tag ${dPts >= 15 ? 'high' : dPts >= 8 ? 'medium' : 'low'}" title="How far this whale print's implied YES probability (${dPts} pts) sits from the market's current price">${dPts}pt diverge</span>`
      : '';
    const size = (d.signal && d.signal.size) || (d.trade && d.trade.size) || 0;
    const price = (d.signal && d.signal.price) || (d.trade && d.trade.price) || 0;
    const conf = (d.signal && d.signal.confidence) || 0;
    const dollarSize = price != null ? fmt(size * ((d.side || 'yes') === 'yes' ? price : (1 - price))) : null;
    const detail = d.action === 'trade'
      ? `${(d.signal && d.signal.side || '').toUpperCase()} × ${size} @ ${(price*100).toFixed(0)}¢`
      : d.action === 'skip'
        ? 'Skipped'
        : d.action === 'close'
          ? `Closed ${(d.trade && d.trade.side || '').toUpperCase()} × ${size} @ ${(price*100).toFixed(0)}¢`
          : '';
    const reasonText = d.action === 'trade'
      ? `Opened because ${esc((d.trade && d.trade.reason) || '')}`
      : d.action === 'skip'
        ? esc(plainEnglishSkipReason(d.reason))
        : d.action === 'close'
          ? esc(d.reason || '')
          : esc(d.reason || '');
    const dEt = (marketTitles[d.ticker] && marketTitles[d.ticker].event_ticker) || '';
    return `
      <div class="signal-card ${d.status}" style="cursor:pointer;" title="${esc(label.full)} — click to view full market detail" onclick="openMarketDetail('${esc(d.ticker)}', '${esc(dEt)}')">
        <div class="top-row">
          <span>${esc(label.short)} <span class="side-tag ${d.side}">${d.side}</span> ${liveMarker} ${closingBadge}</span>
          <span style="color: var(--whale)">${size ? `${size.toLocaleString()} ct${dollarSize ? ' · ' + dollarSize : ''}` : `<span class="action-tag ${d.status}">${d.status}</span>`}</span>
        </div>
        ${contextLineHTML(d.ticker, d.side)}
        ${marketTaxonomyHTML(d.ticker)}
        ${eventLiveDataLineHTML(eventT, true)}
        <div style="color: var(--muted)">${detail}${conf ? ` · confidence ${(conf*100).toFixed(0)}%` : ''} <span class="action-tag ${d.status}">${d.status}</span> ${impactTag} ${divergenceTag}</div>
        ${conf ? `<div class="conf-bar"><div class="conf-fill" style="width:${conf*100}%"></div></div>` : ''}
        <div style="color: var(--muted); font-size:12px; margin-top:6px;">${reasonText}</div>
      </div>
    `;
  };

  renderFeedListSmooth('signal-decision-feed-list', filtered, buildRowHtml, d => d._uid);
}

// Advanced: the same decision data (ROADMAP.md Phase 0.5's "Trade log /
// decision feed" item) as a sortable/filterable table - raw confidence
// numbers and every field instead of today's strict reverse-chronological
// card feed with no sort/filter/search.
function renderDecisionTable() {
  const el = $('signal-decision-feed-list');
  $('signal-decision-feed-filter').innerHTML = `<div class="table-filter-row">
    <label>Ticker: <input type="text" value="${esc(decisionFilter.query)}"
      oninput="decisionFilter.query = this.value; renderDecisionTable();" placeholder="search…" style="width:120px;"></label>
    <label>Action:
      <select onchange="decisionFilter.action = this.value; renderDecisionTable();">
        <option value="" ${decisionFilter.action === '' ? 'selected' : ''}>all</option>
        <option value="trade" ${decisionFilter.action === 'trade' ? 'selected' : ''}>trade</option>
        <option value="skip" ${decisionFilter.action === 'skip' ? 'selected' : ''}>skip</option>
        <option value="close" ${decisionFilter.action === 'close' ? 'selected' : ''}>close</option>
      </select>
    </label>
  </div>`;

  const q = decisionFilter.query.trim().toLowerCase();
  let rows = lastDecisions.filter(d => {
    if (decisionFilter.action && d.action !== decisionFilter.action) return false;
    const ticker = d.action === 'close' ? d.ticker : d.signal.ticker;
    if (q && !ticker.toLowerCase().includes(q)) return false;
    return true;
  }).map(d => {
    const side = d.action === 'close' ? d.trade.side : (d.action === 'trade' ? d.trade.side : d.signal.side);
    const size = d.action === 'close' ? d.trade.size : (d.action === 'trade' ? d.trade.size : d.signal.size);
    const price = d.action === 'close' ? d.trade.price : (d.action === 'trade' ? d.trade.price : d.signal.price);
    const notional = (size != null && price != null)
      ? size * (side === 'yes' ? price : (1 - price))
      : null;
    if (d.action === 'close') {
      return {
        timestamp: d.trade.timestamp,
        ticker: d.ticker,
        action: d.action,
        side,
        size,
        price,
        notional,
        confidence: null,
        reason: d.trade.reason,
      };
    }
    return {
      timestamp: d.signal.timestamp,
      ticker: d.signal.ticker,
      action: d.action,
      side,
      size,
      price,
      notional,
      confidence: d.signal.confidence,
      reason: d.action === 'trade' ? d.trade.reason : d.reason,
    };
  });

  rows = sortRows(rows, decisionFilter.sortKey, decisionFilter.sortDir);

  if (!rows.length) {
    el.innerHTML = '<div class="empty">No decisions match this filter.</div>';
    return;
  }

  const cols = [['timestamp', 'Time'], ['ticker', 'Ticker'], ['action', 'Action'], ['side', 'Side'],
    ['size', 'Shares'], ['notional', 'Bet Value'], ['price', 'Price'], ['confidence', 'Confidence']];
  const headerHtml = cols.map(([key, label]) =>
    sortHeaderHTML(decisionFilter, key, label, `toggleSort(decisionFilter,'${key}',renderDecisionTable)`)
  ).join('');

  const bodyRows = rows.map(r => {
    const rEt = (marketTitles[r.ticker] && marketTitles[r.ticker].event_ticker) || '';
    const rCtx = marketContext(r.ticker, r.side);
    const rBits = [];
    if (rCtx.positionMeans) rBits.push(`Betting: ${esc(rCtx.positionMeans)}`);
    if (rCtx.competitionScope) rBits.push(`Scope: ${esc(rCtx.competitionScope)}`);
    const rSubLine = rBits.length
      ? `<div style="font-size:10px; color:var(--muted); margin-top:2px;">${rBits.join(' · ')}</div>` : '';
    return `<tr class="${r.action}" title="${esc(r.reason || '')} — click to view full market detail" style="cursor:pointer;" onclick="openMarketDetail('${esc(r.ticker)}', '${esc(rEt)}')">
    <td>${new Date(r.timestamp * 1000).toLocaleTimeString()}</td>
    <td>${esc(marketLabel(r.ticker).short)}${rSubLine}</td>
    <td><span class="action-tag ${r.action}">${r.action}</span></td>
    <td><span class="side-tag ${r.side}">${r.side}</span></td>
    <td>${r.size.toLocaleString()}</td>
    <td>${r.notional === null ? '—' : fmt(r.notional)}</td>
    <td>${(r.price*100).toFixed(0)}¢</td>
    <td>${r.confidence === null ? '—' : (r.confidence*100).toFixed(0) + '%'}</td>
  </tr>`;
  }).join('');

  el.innerHTML = `<table class="positions-table sortable">
    <thead><tr>${headerHtml}</tr></thead>
    <tbody>${bodyRows}</tbody>
  </table>`;
}

// How whales are leaning on this specific position's market, and whether
// that agrees with the side actually held - reuses computeWhaleLean (built
// for the Whale Watch tab's market cards, same underlying signal_feed
// data). Confidence here means "how one-sided the whale prints are," not a
// statistical measure - same meaning as everywhere else this app uses the
// word.
function whaleConfidenceBadgeHTML(ticker, side, signals) {
  const lean = computeWhaleLean(ticker, signals || []);
  if (!lean) return '';
  const leanDir = lean.yesPct >= 50 ? 'yes' : 'no';
  const pct = leanDir === 'yes' ? lean.yesPct : 100 - lean.yesPct;
  const aligned = leanDir === side;
  return `<span class="whale-conf ${aligned ? 'aligned' : 'against'}" title="Whales are leaning ${leanDir.toUpperCase()} at ${pct.toFixed(0)}% (${lean.count} recent print${lean.count === 1 ? '' : 's'}) - ${aligned ? 'same side as this position' : 'opposite this position'}">🐋 ${pct.toFixed(0)}%</span>`;
}

// Whale activity SINCE this position was opened, not just the single print
// that triggered entry (that's reasonDetailsHTML below) - 2026-08-16 direct
// report: "my position doesnt show hardly ANY whale signal relationship or
// positions opened since to drive the decision making." Backed by
// main.py's _enrich_positions_with_signal_activity (signal_log.for_ticker/
// count_for_ticker, the FULL persisted signal history since p.opened_at) -
// deliberately not computeWhaleLean(ticker, signals) like the market-card
// badge above, since state.signal_feed is one 50-slot window shared across
// every ticker in the app; a busy ticker crowds out a quiet one within
// seconds, which would make this badge lie about exactly the thing it's
// here to show honestly. This is also the real data
// strategy_engine.check_exits' _whale_lean/_exit_confidence are actually
// reading from, every tick - so "aligned"/"against" here is a preview of
// the same sentiment pressure the auto-exit composite score sees, not a
// separate/simplified metric.
function positionWhaleActivityHTML(p) {
  const count = p.signals_since_entry_count || 0;
  const lean = p.whale_lean_since_entry;
  if (!count || !lean) {
    return `<span class="whale-conf" style="opacity:0.55" title="No whale prints on this market since this position opened">🐋 0 since entry</span>`;
  }
  const leanDir = lean.yes_pct >= 50 ? 'yes' : 'no';
  const pct = leanDir === 'yes' ? lean.yes_pct : 100 - lean.yes_pct;
  const aligned = leanDir === p.side;
  return `<span class="whale-conf ${aligned ? 'aligned' : 'against'}" title="${count} whale print${count === 1 ? '' : 's'} on this market since this position opened - currently leaning ${leanDir.toUpperCase()} at ${pct.toFixed(0)}% - ${aligned ? 'same side as this position (adds exit pressure toward holding)' : 'opposite this position (adds exit pressure via the sentiment factor in auto-exit)'}">🐋 ${count} since · ${pct.toFixed(0)}% ${leanDir.toUpperCase()}</span>`;
}

// Why this position was opened - the whale print (size/price/confidence)
// that triggered the strategy's decision, straight from the trade's own
// `reason` field (services/paper_broker.py's Trade.reason, e.g. "whale
// print 5230 @ 0.62 (conf 0.78)"). Real data already being sent to the
// frontend in broker.recent_trades and simply never displayed anywhere -
// no backend change needed. A position can be built from more than one
// trade (added to over time), so this shows every matching trade, not
// just the first.
function reasonDetailsHTML(ticker, recentTrades) {
  const reasons = (recentTrades || []).filter(t => t.ticker === ticker);
  if (!reasons.length) return '';
  const items = reasons.map(t => {
    const when = new Date(t.timestamp * 1000).toLocaleString();
    return `<div>${esc(t.reason)} <span style="color:var(--muted)">— ${esc(when)}</span></div>`;
  }).join('');
  // stopPropagation - this sits inside a <tr onclick="openMarketDetail(...)">
  // (see renderPositions); without it, opening/reading this disclosure
  // also opened the market-detail modal underneath it, direct report.
  // data-ticker lets renderPositions restore this element's open/closed
  // state across a rebuild (see there) - direct report: it was silently
  // snapping shut on every poll refresh, same class of bug the modal one was.
  return `<details class="whale-reason" data-ticker="${esc(ticker)}" onclick="event.stopPropagation()"><summary title="Why this position was opened" aria-label="Why this position was opened">🐋</summary><div class="whale-reason-body">${items}</div></details>`;
}

// The actual answer to "where did my money go": every open position,
// grouped per market (not one flat line item per position - real Kalshi's
// own positions table groups this way too, confirmed directly against a
// real screenshot, with a Total row per group), what it cost, what it's
// worth now, and the running gain/loss - not just the aggregate P&L number
// in the header.
function renderPositions(positions, prices, recentTrades, signals) {
  const el = $('positions-list');
  $('position-count').textContent = positions.length ? `(${positions.length})` : '';
  if (!positions.length) {
    el.innerHTML = '<div class="empty">No open positions — nothing has been bought yet</div>';
    return;
  }

  // This whole table is rebuilt from scratch below (innerHTML wholesale) on
  // every poll - fine for the table itself, but it was also silently
  // discarding any manually-opened whale-reason <details> disclosure in the
  // process, so it snapped shut on the next refresh, direct report. Capture
  // which tickers were open beforehand and restore them after the rebuild.
  const openWhaleReasonTickers = new Set(
    Array.from(el.querySelectorAll('details.whale-reason[open]')).map(d => d.dataset.ticker)
  );

  // Grouped by event_ticker (same underlying question - e.g. two different
  // outcomes of the same election both held) via marketTitles, not by
  // ticker itself (paper positions are already one-per-ticker). Falls back
  // to a solo group when the event isn't known yet or has no other open
  // positions among its siblings.
  const groups = new Map();
  positions.forEach(p => {
    const info = marketTitles[p.ticker];
    const et = info && info.event_ticker;
    const key = et || ('__solo__' + p.ticker);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  });

  const bodyRows = Array.from(groups.values()).map(group => {
    const first = group[0];
    const firstInfo = marketTitles[first.ticker];
    const et = firstInfo && firstInfo.event_ticker;
    const groupTitle = (et && eventTitles[et]) ? eventTitles[et].title : marketLabel(first.ticker).short;
    const liveMarker = et ? liveBadgeHTML({event_ticker: et}) : '';

    let totalContracts = 0, totalCost = 0, totalValue = 0;

    const positionRows = group.map(p => {
      const current = prices[p.ticker] ?? p.entry_price;
      // cost_basis comes from the backend (PaperBroker.cost_basis) rather
      // than being recomputed here - it's side-aware (a "no" position's
      // real cost is size*(1-entry_price), not size*entry_price) and this
      // way there's exactly one place that math can be wrong, not two.
      // value follows the same side-aware convention for the same reason.
      // No local fallback formula - PaperBroker.state() always populates
      // cost_basis (real bug found 2026-08-15: the old `?? (p.size *
      // p.entry_price)` fallback was never actually reachable, but was
      // missing the no-side (1-price) inversion and would have silently
      // under-costed any "no" position the day it somehow did fire).
      const cost = p.cost_basis;
      const value = p.side === 'yes' ? p.size * current : p.size * (1 - current);
      const ret = value - cost;
      const retPct = cost ? (ret / cost * 100) : 0;
      totalContracts += p.size; totalCost += cost; totalValue += value;

      const ctx = marketContext(p.ticker, p.side);
      const positionLabel = ctx.positionMeans ? `${p.side} · ${ctx.positionMeans}` : p.side;
      const label = marketLabel(p.ticker);

      return `<tr title="${esc(label.full)} — click to view full market detail" style="cursor:pointer;" onclick="openMarketDetail('${esc(p.ticker)}', '${esc(et || '')}')">
        <td><span class="side-tag ${p.side}">${esc(positionLabel)}</span>
          ${positionWhaleActivityHTML(p)}
          ${reasonDetailsHTML(p.ticker, recentTrades)}
          ${comboLegsHTML(p.ticker)}
        </td>
        <td>${p.size.toLocaleString()}</td>
        <td>${((p.side === 'yes' ? p.entry_price : 1 - p.entry_price) * 100).toFixed(0)}¢</td>
        <td>${((p.side === 'yes' ? current : 1 - current) * 100).toFixed(0)}¢ ${priceChangeHTML(p.ticker, prices[p.ticker])}</td>
        <td>${fmt(cost)}${p.entry_fee ? `<div style="font-size:10px; color:var(--muted);" title="Real Kalshi taker fee, already deducted from bankroll at entry - shown separately rather than folded into Cost, same convention as Trading History's own Fees column">+${fmt(p.entry_fee)} fee</div>` : ''}</td>
        <td>${fmt(p.size)}</td>
        <td>${fmt(value)}</td>
        <td class="${ret >= 0 ? 'pos' : 'neg'}">${fmt(ret)} (${retPct.toFixed(0)}%)</td>
      </tr>`;
    }).join('');

    const totalReturn = totalValue - totalCost;
    const totalReturnPct = totalCost ? (totalReturn / totalCost * 100) : 0;
    const totalRow = group.length > 1 ? `<tr class="total-row">
      <td>Total</td>
      <td>${totalContracts.toLocaleString()}</td>
      <td></td><td></td>
      <td>${fmt(totalCost)}</td>
      <td></td>
      <td>${fmt(totalValue)}</td>
      <td class="${totalReturn >= 0 ? 'pos' : 'neg'}">${fmt(totalReturn)} (${totalReturnPct.toFixed(0)}%)</td>
    </tr>` : '';

    return `<tr class="group-header"><td colspan="8"><span style="cursor:pointer;" onclick="openMarketDetail('${esc(first.ticker)}', '${esc(et || '')}')" title="View full market detail">${esc(groupTitle)}</span> ${liveMarker}</td></tr>${positionRows}${totalRow}`;
  }).join('');

  el.innerHTML = `<table class="positions-table">
    <thead><tr>
      <th>Position</th><th>Contracts</th><th>Entry</th><th>Now</th>
      <th>Cost</th><th title="Max payout if this resolves your way">Payout if right</th>
      <th>Value</th><th>Return</th>
    </tr></thead>
    <tbody>${bodyRows}</tbody>
  </table>`;

  if (openWhaleReasonTickers.size) {
    el.querySelectorAll('details.whale-reason').forEach(d => {
      if (openWhaleReasonTickers.has(d.dataset.ticker)) d.open = true;
    });
  }
}

// Position netting (services/position_netting.py) - shows exactly which
// currently-open positions Kalshi confirms share one mutually-exclusive
// real-world event, the payout under every possible outcome (not a
// heuristic), and what - if anything - the system would do about it.
// Read-only fetch, safe regardless of position_netting.enabled (the
// "observe before you choose to act" principle this app's History tab
// already established).
const STATUS_LABEL = {
  locked_profit: { text: 'Locked profit', color: 'var(--yes)' },
  locked_loss: { text: 'Locked loss', color: 'var(--no)' },
  variable: { text: 'Still uncertain', color: 'var(--muted)' },
};

async function loadPositionNettingGroups() {
  try {
    const data = await fetchJSON('/api/position-netting/groups');
    renderPositionNettingGroups(data.groups || []);
  } catch (e) {
    console.error('position-netting fetch failed', e);
  }
}

function renderPositionNettingGroups(groups) {
  const el = $('netting-groups-list');
  if (!groups.length) {
    el.innerHTML = '<div class="empty">No related open positions detected — nothing currently shares a confirmed mutually-exclusive event</div>';
    return;
  }
  el.innerHTML = groups.map(g => {
    const status = STATUS_LABEL[g.status] || STATUS_LABEL.variable;
    const eventTitle = (eventTitles[g.event_ticker] && eventTitles[g.event_ticker].title) || g.event_ticker;
    const memberRows = g.members.map(m => {
      const label = marketLabel(m.ticker);
      const isTarget = (g.recommendation.tickers || []).includes(m.ticker);
      return `<tr${isTarget ? ' style="font-weight:600;"' : ''}>
        <td title="${esc(label.full)}">${esc(label.short)}</td>
        <td><span class="side-tag ${m.side}">${esc(m.side)}</span></td>
        <td>${m.size}</td>
        <td>${((m.side === 'yes' ? m.entry_price : 1 - m.entry_price) * 100).toFixed(0)}¢ → ${((m.side === 'yes' ? m.current_price : 1 - m.current_price) * 100).toFixed(0)}¢</td>
      </tr>`;
    }).join('');
    const rec = g.recommendation;
    const recText = rec.action === 'hold'
      ? `No action — ${esc(rec.reason)}`
      : `${rec.action === 'close_all' ? 'Would close all legs' : 'Would trim'}: ${esc(rec.reason)}`;
    return `<div class="suggestion-card" style="margin-bottom:10px;">
      <div style="display:flex; justify-content:space-between; align-items:baseline;">
        <b title="${esc(g.event_ticker)}">${esc(eventTitle)}</b>
        <span style="color:${status.color}; font-weight:600;">${status.text}</span>
      </div>
      <table style="width:100%; margin-top:6px; font-size:12px;">
        <thead><tr><td>Market</td><td>Side</td><td>Size</td><td>Entry → Now</td></tr></thead>
        <tbody>${memberRows}</tbody>
      </table>
      <div style="margin-top:6px; font-size:12px; color:var(--muted);">${recText}</div>
    </div>`;
  }).join('');
}

// Every fill that's actually happened to the paper bankroll, in order — the
// receipt trail behind the Bankroll/Equity numbers up top.

export { SIGNAL_TIME_RANGES, STATUS_LABEL, _feedListState, _rerenderSignalsFilter, decisionFilter, lastDecisions, lastSignals, loadPositionNettingGroups, plainEnglishSkipReason, positionWhaleActivityHTML, reasonDetailsHTML, renderDecisionTable, renderFeedListSmooth, renderPositionNettingGroups, renderPositions, renderSignalDecisionFeed, renderSignals, signalDecisionFilter, signalFilter, signalImpact, whaleConfidenceBadgeHTML };

// Exposed for inline HTML event handlers (onclick=/onchange=/oninput=,
// including ones built indirectly via a caller-supplied onclick-string
// parameter - see shared-utils.js's header). Every top-level function in
// this file is exposed (cheap, harmless if unused); state objects only the
// specific ones confirmed to be read/mutated directly from a handler.
window._rerenderSignalsFilter = _rerenderSignalsFilter;
window.loadPositionNettingGroups = loadPositionNettingGroups;
window.plainEnglishSkipReason = plainEnglishSkipReason;
window.positionWhaleActivityHTML = positionWhaleActivityHTML;
window.reasonDetailsHTML = reasonDetailsHTML;
window.renderDecisionTable = renderDecisionTable;
window.renderFeedListSmooth = renderFeedListSmooth;
window.renderPositionNettingGroups = renderPositionNettingGroups;
window.renderPositions = renderPositions;
window.renderSignalDecisionFeed = renderSignalDecisionFeed;
window.renderSignals = renderSignals;
window.signalImpact = signalImpact;
window.whaleConfidenceBadgeHTML = whaleConfidenceBadgeHTML;
window.decisionFilter = decisionFilter;
window.signalDecisionFilter = signalDecisionFilter;
window.signalFilter = signalFilter;
