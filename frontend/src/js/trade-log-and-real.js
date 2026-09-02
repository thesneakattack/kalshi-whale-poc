import { HISTORY_CLOSE_TYPE_LABELS } from './history-core.js';
import { $, advToggleHTML, comboLegsHTML, contextLineHTML, costHTML, esc, eventTitles, fetchJSON, fmt, isAdvanced, marketContext, marketLabel, marketTitles, parseConfidence, payoutHTML, sideAdjustedPrice, sortHeaderHTML, sortRows } from './shared-utils.js';

// Part of index.html's JS split - see shared-utils.js's header for the
// load-order/shared-global-scope rationale common to all these files.
// This file: Portfolio-tab trade-log table, and the "real money" (Kalshi
// account) positions/fills/orders panels + the trading funnel-stats panel.
let lastTradeLog = [];
let lastTradeLogPrices = {};
let tradeLogFilter = { query: '', minSize: 0, sortKey: 'timestamp', sortDir: 'desc' };

function renderTrades(trades, prices) {
  lastTradeLog = trades || [];
  lastTradeLogPrices = prices || {};
  $('tradelog-toggle').innerHTML = advToggleHTML('trade-log');
  const el = $('trades-list');
  if (!lastTradeLog.length) {
    el.innerHTML = '<div class="empty">No trades placed yet — check Strategy Decisions below to see why signals are being skipped</div>';
    return;
  }
  if (!isAdvanced('trade-log')) {
    el.innerHTML = lastTradeLog.map(t => {
      const label = marketLabel(t.ticker);
      const when = new Date(t.timestamp * 1000).toLocaleTimeString();
      const tEt = (marketTitles[t.ticker] && marketTitles[t.ticker].event_ticker) || '';
      // A close trade (t.close_type set server-side - see main.py's
      // _enrich_recent_trades) gets its real result shown, not the entry-
      // style cost/payout framing that made every closed position look
      // identical to a still-open one (real bug, direct report 2026-08-10:
      // "not seeing the results of the positions in the trade log").
      const isClose = t.close_type !== undefined && t.close_type !== null && t.close_type !== '';
      const resultHtml = isClose
        ? `<span style="color:${t.won ? 'var(--yes)' : 'var(--no)'}; font-weight:600;">${t.won ? '✅ Won' : '❌ Lost'} ${fmt(t.realized_pnl ?? 0)}</span>
           <span style="color:var(--muted); font-size:11px;">${esc(HISTORY_CLOSE_TYPE_LABELS[t.close_type] || t.close_type)}</span>`
        : `${costHTML(t.size, sideAdjustedPrice(t.side, t.price))} ${payoutHTML(t.size)}`;
      return `
      <div class="trade-row" style="cursor:pointer;" title="${esc(label.full)} — click to view full market detail" onclick="openMarketDetail('${esc(t.ticker)}', '${esc(tEt)}')">
        <div class="name">${esc(label.short)} <span class="side-tag ${t.side}">${t.side}</span>
          ${contextLineHTML(t.ticker, t.side)}
        </div>
        <div class="nums">
          <span title="Contracts filled at this price">${t.size.toLocaleString()} contracts @ ${(t.price*100).toFixed(0)}¢ fill price</span>
          <span style="color: var(--muted)">${when}</span>
          ${resultHtml}
        </div>
      </div>`;
    }).join('');
    return;
  }
  renderTradeLogTable();
}

// Advanced: sortable/filterable table over the same trade data (ROADMAP.md
// Phase 0.5's "Trade log / decision feed" item) - ticker/side/size/price
// plus unrealized P&L (same current-vs-entry-price math as renderPositions,
// current price from state.latest_prices) and the raw confidence number
// that triggered the trade, pulled out of Trade.reason's formatted text
// (see parseConfidence) instead of a text blob.
function renderTradeLogTable() {
  const el = $('trades-list');
  const filterHtml = `<div class="table-filter-row">
    <label>Ticker: <input type="text" value="${esc(tradeLogFilter.query)}"
      oninput="tradeLogFilter.query = this.value; renderTradeLogTable();" placeholder="search…" style="width:120px;"></label>
    <label>Min size: <input type="number" min="0" step="1" value="${tradeLogFilter.minSize}"
      onchange="tradeLogFilter.minSize = parseFloat(this.value) || 0; renderTradeLogTable();" style="width:80px;"></label>
  </div>`;

  const q = tradeLogFilter.query.trim().toLowerCase();
  let rows = lastTradeLog.filter(t => {
    if (q && !t.ticker.toLowerCase().includes(q)) return false;
    if (t.size < tradeLogFilter.minSize) return false;
    return true;
  }).map(t => {
    const isClose = t.close_type !== undefined && t.close_type !== null && t.close_type !== '';
    let pnl;
    if (isClose) {
      // Real bug (direct report 2026-08-10): this used to run the same
      // mark-to-market formula below for EVERY row, close or not - for a
      // close row that compares the close price against whatever the
      // market happens to be trading at *now*, not the P&L actually
      // realized when the position closed. t.realized_pnl (server-side,
      // see main.py's _enrich_recent_trades) is the real number.
      pnl = t.realized_pnl ?? 0;
    } else {
      const current = lastTradeLogPrices[t.ticker] ?? t.price;
      // direction-aware, same as PaperBroker.mark_to_market - without it a
      // "no" row's gain/loss showed with the sign flipped (a no position
      // gaining when price falls read as a loss here, and vice versa).
      const direction = t.side === 'yes' ? 1 : -1;
      pnl = direction * (current - t.price) * t.size;
    }
    return Object.assign({}, t, { pnl, isClose, confidence: parseConfidence(t.reason) });
  });

  rows = sortRows(rows, tradeLogFilter.sortKey, tradeLogFilter.sortDir);

  if (!rows.length) {
    el.innerHTML = filterHtml + '<div class="empty">No trades match this filter.</div>';
    return;
  }

  const cols = [['timestamp', 'Time'], ['ticker', 'Ticker'], ['side', 'Side'], ['size', 'Size'],
    ['price', 'Price'], ['confidence', 'Confidence'], ['pnl', 'P&L'], ['close_type', 'Result'],
    ['market_result', 'Market Settled']];
  const headerHtml = cols.map(([key, label]) =>
    sortHeaderHTML(tradeLogFilter, key, label, `toggleSort(tradeLogFilter,'${key}',renderTradeLogTable)`)
  ).join('');

  // Row background is a P&L gradient - transparent near $0, ramping up to
  // a shade of --yes (win) or --no (loss) capped at a max alpha chosen so
  // --text stays readable on top of it even at the cap (WCAG contrast
  // checked directly against this app's real panel/text/yes/no colors:
  // 0.40 alpha still holds ~5.9:1 on --yes and ~7.5:1 on --no, both well
  // past the 4.5:1 AA floor). Scaled per-render against the largest |P&L|
  // in the currently filtered/sorted rows, not a hardcoded dollar figure,
  // so the gradient stays meaningful as position sizing changes over time.
  const ROW_TINT_MIN_ALPHA = 0.06;
  const ROW_TINT_MAX_ALPHA = 0.40;
  const ROW_TINT_YES_RGB = '45,212,191';  // --yes, #2DD4BF
  const ROW_TINT_NO_RGB = '249,112,102';  // --no, #F97066
  const maxAbsPnl = Math.max(1, ...rows.map(r => Math.abs(r.pnl)));

  const bodyRows = rows.map(r => {
    const rEt = (marketTitles[r.ticker] && marketTitles[r.ticker].event_ticker) || '';
    const resultCell = r.isClose
      ? `<span style="color:${r.won ? 'var(--yes)' : 'var(--no)'};">${r.won ? 'Won' : 'Lost'} · ${esc(HISTORY_CLOSE_TYPE_LABELS[r.close_type] || r.close_type)}</span>`
      : '<span style="color:var(--muted);">open</span>';
    // Independent of resultCell above: what the market itself ultimately
    // settled as (services/market_history.py's real outcomes table), vs.
    // resultCell's own won/lost-at-exit framing. Most useful for an early
    // exit (stop-loss, take-profit, sentiment reversal, ...) - the market
    // kept trading after this position closed, so this is what tells you
    // whether that early exit was the right call in hindsight. "pending"
    // covers both a still-open position and a closed one whose market
    // hasn't settled yet - this app has no way to tell those apart here.
    const marketResultCell = r.market_result
      ? `<span style="color:${r.market_result === r.side ? 'var(--yes)' : 'var(--no)'};" title="Market settled ${esc(r.market_result.toUpperCase())} — this position held ${esc(String(r.side).toUpperCase())}">${esc(r.market_result.toUpperCase())} ${r.market_result === r.side ? '✓' : '✗'}</span>`
      : '<span style="color:var(--muted);">pending</span>';
    const tintRgb = r.pnl > 0 ? ROW_TINT_YES_RGB : r.pnl < 0 ? ROW_TINT_NO_RGB : null;
    const tintAlpha = ROW_TINT_MIN_ALPHA + Math.min(1, Math.abs(r.pnl) / maxAbsPnl) * (ROW_TINT_MAX_ALPHA - ROW_TINT_MIN_ALPHA);
    const rowBg = tintRgb ? `background:rgba(${tintRgb},${tintAlpha.toFixed(3)});` : '';
    // Smaller second line under the title (2026-08-10 report: "YES"/"NO"
    // alone not saying who/what it means, plus rows going extremely wide).
    const rCtx = marketContext(r.ticker, r.side);
    const rSubLine = rCtx.positionMeans
      ? `<div style="font-size:10px; color:var(--muted); margin-top:2px;">Betting: ${esc(rCtx.positionMeans)}</div>` : '';
    return `<tr title="${esc(marketLabel(r.ticker).full)} — click to view full market detail" style="cursor:pointer;${rowBg}" onclick="openMarketDetail('${esc(r.ticker)}', '${esc(rEt)}')">
    <td>${new Date(r.timestamp * 1000).toLocaleTimeString()}</td>
    <td>${esc(marketLabel(r.ticker).short)}${rSubLine}</td>
    <td><span class="side-tag ${r.side}">${r.side}</span></td>
    <td>${r.size.toLocaleString()}</td>
    <td>${(r.price*100).toFixed(0)}¢</td>
    <td>${r.confidence != null ? (r.confidence*100).toFixed(0) + '%' : '—'}</td>
    <td class="${r.pnl >= 0 ? 'pos' : 'neg'}">${fmt(r.pnl)}</td>
    <td>${resultCell}</td>
    <td>${marketResultCell}</td>
  </tr>`;
  }).join('');

  el.innerHTML = filterHtml + `<table class="positions-table sortable">
    <thead><tr>${headerHtml}</tr></thead>
    <tbody>${bodyRows}</tbody>
  </table>`;
}

function rawJsonDetails(data) {
  return `<details class="raw-json-wrap"><summary>raw response</summary><pre class="raw-json">${esc(JSON.stringify(data, null, 2))}</pre></details>`;
}

// Real field names verified 2026-08-07 against a real connected account
// (see ROADMAP.md/status.html) — position_fp/market_exposure_dollars/
// total_traded_dollars on market_positions, count_fp/yes_price_dollars/
// no_price_dollars on fills. The earlier guesses (position, market_exposure,
// count, yes_price, ...) didn't exist on any real response and silently
// rendered "—"/blank for every row. Raw response stays visible regardless,
// since Kalshi's docs can still drift again.
// fees_paid_dollars/total_traded_dollars/last_updated_ts (positions) and
// created_time/fee_cost/is_taker/order_id (fills) - direct data-usage
// review finding: real fields, already reaching the backend on every real
// get_positions()/get_fills() call, previously trimmed by
// _POSITION_FIELDS/_FILL_FIELDS before ever reaching the frontend. Fees
// directly eat into a real position's P&L, and a fill with no timestamp
// can't be read in time order or checked for recency - both shown in
// Simple mode now, not held back for Advanced, since they're basic
// context for a panel that's specifically about real money. Advanced adds
// a sortable table with the rest (total traded, taker/maker, order id).
let realPositionsState = { sortKey: 'ticker', sortDir: 'asc' };
let realFillsState = { sortKey: 'created_time', sortDir: 'desc' };

function renderRealPositions(positions) {
  lastRealPositions = positions;
  const el = $('positions-list');
  if (positions === null || positions === undefined) {
    $('position-count').textContent = '';
    $('positions-toggle').innerHTML = '';
    el.innerHTML = '<div class="empty">Not connected — set KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH in .env</div>';
    return;
  }
  $('positions-toggle').innerHTML = advToggleHTML('real-positions');
  const list = Array.isArray(positions) ? positions : (positions.market_positions || []);
  $('position-count').textContent = list.length ? `(${list.length})` : '';
  if (!list.length) {
    el.innerHTML = '<div class="empty">No open real positions</div>' + rawJsonDetails(positions);
    return;
  }

  if (isAdvanced('real-positions')) {
    const rows = sortRows(list.map(p => {
      const qty = p.position_fp != null ? parseFloat(p.position_fp) : NaN;
      const side = isNaN(qty) ? null : (qty >= 0 ? 'yes' : 'no');
      // current_yes_price_dollars is always the yes-side price by convention
      // (same as every other price in this app) - invert for a no position,
      // the exact no-side math this codebase has gotten wrong before
      // (CLAUDE.md's documented bug pattern).
      const yesPrice = p.current_yes_price_dollars;
      const heldPrice = yesPrice == null ? null : sideAdjustedPrice(side, yesPrice);
      return {
        ticker: p.ticker || p.market_ticker || 'unknown',
        qty, side, heldPrice,
        exposure: p.market_exposure_dollars != null ? parseFloat(p.market_exposure_dollars) : null,
        fees: p.fees_paid_dollars != null ? parseFloat(p.fees_paid_dollars) : null,
        totalTraded: p.total_traded_dollars != null ? parseFloat(p.total_traded_dollars) : null,
        pnl: p.realized_pnl_dollars != null ? parseFloat(p.realized_pnl_dollars) : null,
        updated: p.last_updated_ts || null,
        p,
      };
    }), realPositionsState.sortKey, realPositionsState.sortDir);
    const sortCall = (key) => `toggleSort(realPositionsState, '${key}', renderRealPositionsFromLast)`;
    const bodyRows = rows.map(r => {
      const label = marketLabel(r.ticker);
      const et = (marketTitles[r.ticker] && marketTitles[r.ticker].event_ticker) || '';
      // Direct request: real positions should open to full market detail the
      // same way paper positions/every market card already do - this table
      // was the one place in Portfolio missing it.
      const rSubLine = r.side && marketContext(r.ticker, r.side).positionMeans
        ? `<div style="font-size:10px; color:var(--muted); margin-top:2px;">Betting: ${esc(marketContext(r.ticker, r.side).positionMeans)}</div>` : '';
      return `<tr title="${esc(JSON.stringify(r.p))} — click to view full market detail" style="cursor:pointer;" onclick="openMarketDetail('${esc(r.ticker)}', '${esc(et)}')">
        <td style="text-align:left;">${esc(label.short)} ${r.side ? `<span class="side-tag ${r.side}">${r.side}</span>` : ''}${rSubLine}</td>
        <td>${isNaN(r.qty) ? '—' : r.qty.toLocaleString()}</td>
        <td>${r.heldPrice != null ? (r.heldPrice*100).toFixed(0)+'¢' : '—'}</td>
        <td>${r.exposure != null ? fmt(r.exposure) : '—'}</td>
        <td>${r.fees != null ? fmt(r.fees) : '—'}</td>
        <td>${r.totalTraded != null ? fmt(r.totalTraded) : '—'}</td>
        <td style="color:${r.pnl != null && r.pnl < 0 ? 'var(--no)' : 'var(--yes)'};">${r.pnl != null ? fmt(r.pnl) : '—'}</td>
        <td>${r.updated ? new Date(r.updated).toLocaleString() : '—'}</td>
      </tr>`;
    }).join('');
    el.innerHTML = `<table class="positions-table sortable">
      <thead><tr>
        ${sortHeaderHTML(realPositionsState, 'ticker', 'Market', sortCall('ticker'))}
        ${sortHeaderHTML(realPositionsState, 'qty', 'Contracts', sortCall('qty'))}
        ${sortHeaderHTML(realPositionsState, 'heldPrice', 'Current Price', sortCall('heldPrice'))}
        ${sortHeaderHTML(realPositionsState, 'exposure', 'Exposure', sortCall('exposure'))}
        ${sortHeaderHTML(realPositionsState, 'fees', 'Fees Paid', sortCall('fees'))}
        ${sortHeaderHTML(realPositionsState, 'totalTraded', 'Total Traded', sortCall('totalTraded'))}
        ${sortHeaderHTML(realPositionsState, 'pnl', 'Realized P&L', sortCall('pnl'))}
        ${sortHeaderHTML(realPositionsState, 'updated', 'Last Updated', sortCall('updated'))}
      </tr></thead>
      <tbody>${bodyRows}</tbody>
    </table>` + rawJsonDetails(positions);
    return;
  }

  // Grouped by parent event (Kalshi's own event_positions, previously
  // fetched every tick and dropped entirely - direct report: real
  // positions on sibling child markets of the same event, e.g. two
  // different "what will they say" markets under one mention event, showed
  // as unrelated flat rows with no indication they shared an event) -
  // same grouping key marketTitles already carries, same pattern
  // renderPositions (paper) already uses for the identical reason.
  const groups = new Map();
  list.forEach(p => {
    const ticker = p.ticker || p.market_ticker || 'unknown';
    const info = marketTitles[ticker];
    const et = info && info.event_ticker;
    const key = et || ('__solo__' + ticker);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  });

  el.innerHTML = Array.from(groups.entries()).map(([key, group]) => {
    const first = group[0];
    const firstTicker = first.ticker || first.market_ticker || 'unknown';
    const et = marketTitles[firstTicker] && marketTitles[firstTicker].event_ticker;
    const groupHeader = (et && group.length > 1)
      ? `<div class="series-header"><span>${esc((eventTitles[et] && eventTitles[et].title) || marketLabel(firstTicker).short)}</span><span class="count">${group.length} positions</span></div>`
      : '';
    const rows = group.map(p => {
      const ticker = p.ticker || p.market_ticker || 'unknown';
      const label = marketLabel(ticker);
      const qty = p.position_fp ?? '—';
      const qtyNum = p.position_fp != null ? parseFloat(p.position_fp) : NaN;
      const exposure = p.market_exposure_dollars != null ? fmt(parseFloat(p.market_exposure_dollars)) : null;
      const fees = p.fees_paid_dollars != null ? parseFloat(p.fees_paid_dollars) : null;
      const feesHtml = fees != null && fees > 0 ? ` · ${fmt(fees)} fees` : '';
      const pnl = p.realized_pnl_dollars != null ? parseFloat(p.realized_pnl_dollars) : null;
      const pnlHtml = pnl != null ? ` <span style="color: var(${pnl >= 0 ? '--yes' : '--no'})">${fmt(pnl)} realized</span>` : '';
      const side = isNaN(qtyNum) ? null : (qtyNum >= 0 ? 'yes' : 'no');  // Kalshi's real convention: signed count, positive = Yes
      // current_yes_price_dollars is always the yes-side price by
      // convention - invert for a no position (CLAUDE.md's documented
      // no-side-math bug pattern).
      const yesPrice = p.current_yes_price_dollars;
      const heldPrice = yesPrice == null ? null : sideAdjustedPrice(side, yesPrice);
      const priceHtml = heldPrice != null ? ` · ${(heldPrice*100).toFixed(0)}¢ now` : '';
      const updated = p.last_updated_ts ? new Date(p.last_updated_ts).toLocaleTimeString() : null;
      const tickerEt = (marketTitles[ticker] && marketTitles[ticker].event_ticker) || '';
      return `<div class="position-row" style="cursor:pointer;" title="${esc(JSON.stringify(p))} — click to view full market detail" onclick="openMarketDetail('${esc(ticker)}', '${esc(tickerEt)}')">
        <div class="name">${esc(label.short)}${side ? ` <span class="side-tag ${side}">${side}</span>` : ''}
          ${side ? contextLineHTML(ticker, side) : ''}
          ${comboLegsHTML(ticker)}
        </div>
        <div class="nums"><span title="Signed contract count - positive is a Yes position, negative is No">${esc(String(qty))} contracts${priceHtml}${exposure != null ? ' · ' + exposure + ' put in' : ''}${feesHtml}${pnlHtml}</span>
          ${updated ? `<span style="color:var(--muted);">updated ${esc(updated)}</span>` : ''}
          ${payoutHTML(qtyNum)}</div>
      </div>`;
    }).join('');
    return groupHeader + rows;
  }).join('') + rawJsonDetails(positions);
}

// toggleAdvanced's rerender path is refresh() (re-fetches /api/state and
// re-renders everything) - fine everywhere else, but real positions/fills
// are handed data already present in the last poll response, so re-running
// the same render off the last-seen data avoids waiting on a fresh network
// round trip just to flip a display toggle.
let lastRealPositions = null, lastRealFills = null;
function renderRealPositionsFromLast() { renderRealPositions(lastRealPositions); }
function renderRealFillsFromLast() { renderRealFills(lastRealFills); }

function renderRealFills(fills) {
  lastRealFills = fills;
  const el = $('trades-list');
  if (fills === null || fills === undefined) {
    $('tradelog-toggle').innerHTML = '';
    el.innerHTML = '<div class="empty">Not connected — set KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH in .env</div>';
    return;
  }
  $('tradelog-toggle').innerHTML = advToggleHTML('trade-log');
  const list = Array.isArray(fills) ? fills : (fills.fills || []);
  if (!list.length) {
    el.innerHTML = '<div class="empty">No fills recorded on this account</div>' + rawJsonDetails(fills);
    return;
  }

  if (isAdvanced('trade-log')) {
    const rows = sortRows(list.map(f => {
      const priceDollarsRaw = f.side === 'no' ? f.no_price_dollars : f.yes_price_dollars;
      return {
        ticker: f.ticker || f.market_ticker || 'unknown',
        side: f.side || f.action || '',
        action: f.action || '',
        count: f.count_fp != null ? parseFloat(f.count_fp) : NaN,
        price: priceDollarsRaw != null ? parseFloat(priceDollarsRaw) : NaN,
        fee: f.fee_cost != null ? parseFloat(f.fee_cost) : null,
        isTaker: f.is_taker,
        created_time: f.created_time || null,
        orderId: f.order_id || null,
        f,
      };
    }), realFillsState.sortKey, realFillsState.sortDir);
    const sortCall = (key) => `toggleSort(realFillsState, '${key}', renderRealFillsFromLast)`;
    const bodyRows = rows.map(r => {
      const label = marketLabel(r.ticker);
      const rSubLine = r.side && marketContext(r.ticker, r.side).positionMeans
        ? `<div style="font-size:10px; color:var(--muted); margin-top:2px;">Betting: ${esc(marketContext(r.ticker, r.side).positionMeans)}</div>` : '';
      return `<tr title="${esc(JSON.stringify(r.f))}">
        <td style="text-align:left;">${esc(label.short)} ${r.side ? `<span class="side-tag ${esc(r.side)}">${esc(r.side)}</span>` : ''}${rSubLine}</td>
        <td>${esc(r.action || '—')}</td>
        <td>${isNaN(r.count) ? '—' : r.count.toLocaleString()}</td>
        <td>${isNaN(r.price) ? '—' : (r.price * 100).toFixed(0) + '¢'}</td>
        <td>${r.fee != null ? fmt(r.fee) : '—'}</td>
        <td>${r.isTaker === true ? 'Taker' : r.isTaker === false ? 'Maker' : '—'}</td>
        <td>${r.created_time ? new Date(r.created_time).toLocaleString() : '—'}</td>
        <td style="font-family:var(--mono); font-size:11px; color:var(--muted);" title="${esc(r.orderId || '')}">${r.orderId ? esc(r.orderId.slice(0, 8)) : '—'}</td>
      </tr>`;
    }).join('');
    el.innerHTML = `<table class="positions-table sortable">
      <thead><tr>
        ${sortHeaderHTML(realFillsState, 'ticker', 'Market', sortCall('ticker'))}
        ${sortHeaderHTML(realFillsState, 'action', 'Action', sortCall('action'))}
        ${sortHeaderHTML(realFillsState, 'count', 'Contracts', sortCall('count'))}
        ${sortHeaderHTML(realFillsState, 'price', 'Fill Price', sortCall('price'))}
        ${sortHeaderHTML(realFillsState, 'fee', 'Fee', sortCall('fee'))}
        ${sortHeaderHTML(realFillsState, 'isTaker', 'Role', sortCall('isTaker'))}
        ${sortHeaderHTML(realFillsState, 'created_time', 'When', sortCall('created_time'))}
        <th>Order ID</th>
      </tr></thead>
      <tbody>${bodyRows}</tbody>
    </table>` + rawJsonDetails(fills);
    return;
  }

  el.innerHTML = list.slice(0, 25).map(f => {
    const ticker = f.ticker || f.market_ticker || 'unknown';
    const label = marketLabel(ticker);
    const side = f.side || f.action || '';
    const count = f.count_fp ?? '—';
    const countNum = f.count_fp != null ? parseFloat(f.count_fp) : NaN;
    const priceDollarsRaw = f.side === 'no' ? f.no_price_dollars : f.yes_price_dollars;
    const priceNum = priceDollarsRaw != null ? parseFloat(priceDollarsRaw) : NaN;
    const price = priceDollarsRaw != null ? `${(priceNum * 100).toFixed(0)}¢` : null;
    const fee = f.fee_cost != null ? parseFloat(f.fee_cost) : null;
    const when = f.created_time ? new Date(f.created_time).toLocaleString() : null;
    return `<div class="trade-row" title="${esc(JSON.stringify(f))}">
      <div class="name">${esc(label.short)} ${side ? `<span class="side-tag ${esc(side)}">${esc(side)}</span>` : ''}
        ${side ? contextLineHTML(ticker, side) : ''}
      </div>
      <div class="nums"><span title="Contracts filled at this price">${esc(String(count))} contracts${price != null ? ' @ ' + price + ' fill price' : ''}${fee != null && fee > 0 ? ' · ' + fmt(fee) + ' fee' : ''}</span>
        ${when ? `<span style="color:var(--muted);">${esc(when)}</span>` : ''}
        ${costHTML(countNum, priceNum)}
        ${payoutHTML(countNum)}</div>
    </div>`;
  }).join('') + rawJsonDetails(fills);
}

// Real order history (direct data-usage review finding - see the HTML
// comment above #real-orders-panel). On-demand, not part of the 5s poll
// loop - loadRealOrders() is called from setAccountMode('real') and once
// from renderPortfolio() if the page loads directly into real mode.
// Kalshi's own cursor-based pagination, passed through opaquely (append,
// "Load more") rather than this app's usual limit/offset - real order
// history is Kalshi's data, not ours to re-paginate.
let realOrders = [];
let realOrdersCursor = null;
let realOrdersLoaded = false;
let realOrdersState = { sortKey: 'created_time', sortDir: 'desc' };

async function loadRealOrders(reset = true) {
  if (reset) { realOrders = []; realOrdersCursor = null; }
  const params = new URLSearchParams({ limit: 25 });
  if (!reset && realOrdersCursor) params.set('cursor', realOrdersCursor);
  try {
    const resp = await fetchJSON(`/api/account/orders?${params}`);
    if (!resp.connected) {
      $('real-orders-list').innerHTML = '<div class="empty">Not connected — set KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH in .env</div>';
      $('real-orders-more-row').style.display = 'none';
      return;
    }
    realOrders = reset ? (resp.orders || []) : realOrders.concat(resp.orders || []);
    realOrdersCursor = resp.cursor || null;
    realOrdersLoaded = true;
    renderRealOrders();
    $('real-orders-more-row').style.display = realOrdersCursor ? 'flex' : 'none';
  } catch (e) {
    $('real-orders-list').innerHTML = '<div class="empty">Failed to load order history.</div>';
  }
}

function renderRealOrders() {
  $('real-orders-count').textContent = realOrders.length ? `(${realOrders.length})` : '';
  $('real-orders-toggle').innerHTML = advToggleHTML('real-orders');
  const el = $('real-orders-list');
  if (!realOrders.length) {
    el.innerHTML = '<div class="empty">No orders on this account yet.</div>';
    return;
  }

  if (isAdvanced('real-orders')) {
    const rows = sortRows(realOrders.map(o => {
      const priceDollarsRaw = o.side === 'no' ? o.no_price_dollars : o.yes_price_dollars;
      return {
        ticker: o.ticker || 'unknown',
        side: o.side || '',
        action: o.action || '',
        type: o.type || '',
        status: o.status || '',
        price: priceDollarsRaw != null ? parseFloat(priceDollarsRaw) : NaN,
        filled: o.fill_count_fp != null ? parseFloat(o.fill_count_fp) : NaN,
        remaining: o.remaining_count_fp != null ? parseFloat(o.remaining_count_fp) : NaN,
        fees: (parseFloat(o.taker_fees_dollars || 0) + parseFloat(o.maker_fees_dollars || 0)) || null,
        created_time: o.created_time || null,
        orderId: o.order_id || null,
        o,
      };
    }), realOrdersState.sortKey, realOrdersState.sortDir);
    const sortCall = (key) => `toggleSort(realOrdersState, '${key}', renderRealOrders)`;
    const bodyRows = rows.map(r => {
      const label = marketLabel(r.ticker);
      const rSubLine = r.side && marketContext(r.ticker, r.side).positionMeans
        ? `<div style="font-size:10px; color:var(--muted); margin-top:2px;">Betting: ${esc(marketContext(r.ticker, r.side).positionMeans)}</div>` : '';
      return `<tr title="${esc(JSON.stringify(r.o))}">
        <td style="text-align:left;">${esc(label.short)} ${r.side ? `<span class="side-tag ${esc(r.side)}">${esc(r.side)}</span>` : ''}${rSubLine}</td>
        <td>${esc(r.action || '—')}</td>
        <td>${esc(r.type || '—')}</td>
        <td>${esc(r.status || '—')}</td>
        <td>${isNaN(r.price) ? '—' : (r.price * 100).toFixed(0) + '¢'}</td>
        <td>${isNaN(r.filled) ? '—' : r.filled.toLocaleString()} / ${isNaN(r.remaining) ? '—' : r.remaining.toLocaleString()}</td>
        <td>${r.fees != null ? fmt(r.fees) : '—'}</td>
        <td>${r.created_time ? new Date(r.created_time).toLocaleString() : '—'}</td>
        <td style="font-family:var(--mono); font-size:11px; color:var(--muted);" title="${esc(r.orderId || '')}">${r.orderId ? esc(r.orderId.slice(0, 8)) : '—'}</td>
      </tr>`;
    }).join('');
    el.innerHTML = `<table class="positions-table sortable">
      <thead><tr>
        ${sortHeaderHTML(realOrdersState, 'ticker', 'Market', sortCall('ticker'))}
        ${sortHeaderHTML(realOrdersState, 'action', 'Action', sortCall('action'))}
        ${sortHeaderHTML(realOrdersState, 'type', 'Type', sortCall('type'))}
        ${sortHeaderHTML(realOrdersState, 'status', 'Status', sortCall('status'))}
        ${sortHeaderHTML(realOrdersState, 'price', 'Price', sortCall('price'))}
        <th>Filled / Remaining</th>
        ${sortHeaderHTML(realOrdersState, 'fees', 'Fees', sortCall('fees'))}
        ${sortHeaderHTML(realOrdersState, 'created_time', 'When', sortCall('created_time'))}
        <th>Order ID</th>
      </tr></thead>
      <tbody>${bodyRows}</tbody>
    </table>`;
    return;
  }

  el.innerHTML = realOrders.map(o => {
    const label = marketLabel(o.ticker || 'unknown');
    const priceDollarsRaw = o.side === 'no' ? o.no_price_dollars : o.yes_price_dollars;
    const priceNum = priceDollarsRaw != null ? parseFloat(priceDollarsRaw) : NaN;
    const filled = o.fill_count_fp != null ? parseFloat(o.fill_count_fp) : null;
    const fees = (parseFloat(o.taker_fees_dollars || 0) + parseFloat(o.maker_fees_dollars || 0)) || null;
    const when = o.created_time ? new Date(o.created_time).toLocaleString() : null;
    return `<div class="trade-row" title="${esc(JSON.stringify(o))}">
      <div class="name">${esc(label.short)} ${o.side ? `<span class="side-tag ${esc(o.side)}">${esc(o.side)}</span>` : ''}
        <span class="side-tag" style="background:var(--panel-2); color:var(--muted);">${esc(o.status || 'unknown')}</span>
        ${o.side ? contextLineHTML(o.ticker, o.side) : ''}
      </div>
      <div class="nums"><span>${filled != null ? filled.toLocaleString() : '—'} contracts${!isNaN(priceNum) ? ' @ ' + (priceNum * 100).toFixed(0) + '¢' : ''}${fees != null && fees > 0 ? ' · ' + fmt(fees) + ' fees' : ''}</span>
        ${when ? `<span style="color:var(--muted);">${esc(when)}</span>` : ''}
      </div>
    </div>`;
  }).join('');
}

// Explains at a glance why the bankroll may not be moving: most whale signals
// are expected to be skipped (that's the strategy's confidence filter working).
function renderFunnel(stats) {
  if (!stats) return;
  $('funnel-bar').innerHTML = `
    <span><b>${stats.signals_seen}</b> signals seen</span>
    <span>→ <b>${stats.trades_placed}</b> trades placed</span>
    <span>→ <b>${stats.skipped}</b> skipped (below confidence threshold, cooldown, or halted)</span>
  `;
}

// Full-exchange trade tape, scoped to the current watchlist (see
// _fetch_trade_tape in main.py) - distinct from the per-market drill-down's
// own recent-trades list. Ties into this app's whale concept: a big trade
// on the tape and a whale print are close to the same idea.

export { lastRealFills, lastRealPositions, lastTradeLog, lastTradeLogPrices, loadRealOrders, rawJsonDetails, realFillsState, realOrders, realOrdersCursor, realOrdersLoaded, realOrdersState, realPositionsState, renderFunnel, renderRealFills, renderRealFillsFromLast, renderRealOrders, renderRealPositions, renderRealPositionsFromLast, renderTradeLogTable, renderTrades, tradeLogFilter };

// Exposed for inline HTML event handlers (onclick=/onchange=/oninput=,
// including ones built indirectly via a caller-supplied onclick-string
// parameter - see shared-utils.js's header). Every top-level function in
// this file is exposed (cheap, harmless if unused); state objects only the
// specific ones confirmed to be read/mutated directly from a handler.
window.loadRealOrders = loadRealOrders;
window.rawJsonDetails = rawJsonDetails;
window.renderFunnel = renderFunnel;
window.renderRealFills = renderRealFills;
window.renderRealFillsFromLast = renderRealFillsFromLast;
window.renderRealOrders = renderRealOrders;
window.renderRealPositions = renderRealPositions;
window.renderRealPositionsFromLast = renderRealPositionsFromLast;
window.renderTradeLogTable = renderTradeLogTable;
window.renderTrades = renderTrades;
window.realFillsState = realFillsState;
window.realOrdersState = realOrdersState;
window.realPositionsState = realPositionsState;
window.tradeLogFilter = tradeLogFilter;
