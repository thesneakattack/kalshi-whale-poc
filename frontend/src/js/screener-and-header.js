import { computeWhaleLean } from './equity-and-cards.js';
import { $, accountMode, advToggleHTML, comboLegsHTML, contextLineHTML, esc, eventLiveDataLineHTML, fetchJSON, fmt, isAdvanced, liveBadgeHTML, marketContext, marketLabel, marketTaxonomyHTML, priceChangeHTML, seriesLabel, seriesOf, sortHeaderHTML, sortRows } from './shared-utils.js';
import { TRADING_PHRASE, tradingGateHTML, tradingGateOpen } from './trading-gate-and-connectivity.js';

// Part of index.html's JS split - see shared-utils.js's header for the
// load-order/shared-global-scope rationale common to all these files.
// This file: Markets-tab screener table, the market-detail modal (info,
// trades, orderbook, candlesticks, market-analyst section, help/glossary),
// the Portfolio header strip, and the real-money account/banner renderers.
let screenerState = {
  'markets-list': { markets: [], prices: {}, signals: [], includeWhale: false, sortKey: 'volume_24h_fp', sortDir: 'desc' },
  'markets':      { markets: [], prices: {}, signals: [], includeWhale: false, sortKey: 'volume_24h_fp', sortDir: 'desc' },
  'whale-cards':  { markets: [], prices: {}, signals: [], includeWhale: true,  sortKey: 'volume_24h_fp', sortDir: 'desc' },
};

function screenerRowData(m, prices, signals) {
  const yesBid = prices[m.ticker];
  const yesAsk = m.yes_ask_dollars != null && m.yes_ask_dollars !== '' ? parseFloat(m.yes_ask_dollars) : null;
  const spread = (yesBid != null && yesAsk != null) ? Math.max(0, yesAsk - yesBid) : null;
  const lean = signals ? computeWhaleLean(m.ticker, signals) : null;
  return {
    ticker: m.ticker,
    series: seriesLabel(seriesOf(m.ticker)),  // the real watchlist "parent" unit - see round_robin_select
    volume_24h_fp: parseFloat(m.volume_24h_fp) || 0,
    yesBid: yesBid ?? null,
    spread,
    leanPct: lean ? lean.yesPct : null,
    leanCount: lean ? lean.count : 0,
    m,
  };
}

function renderScreenerTable(panelKey, containerId, countId, markets, prices, includeWhale, signals) {
  const st = screenerState[panelKey];
  st.markets = markets || [];
  st.prices = prices || {};
  st.signals = signals || [];
  st.includeWhale = includeWhale;
  renderScreenerTableFromState(panelKey, containerId, countId);
}

function renderScreenerTableFromState(panelKey, containerId, countId) {
  const st = screenerState[panelKey];
  const el = $(containerId);
  el.classList.remove('cards-grid');
  if (countId) $(countId).textContent = st.markets.length ? `(${st.markets.length})` : '';
  if (!st.markets.length) { el.innerHTML = '<div class="empty">No markets loaded</div>'; return; }

  let rows = st.markets.map(m => screenerRowData(m, st.prices, st.signals));
  rows = sortRows(rows, st.sortKey, st.sortDir);

  const rerender = `renderScreenerTableFromState('${panelKey}', '${containerId}', ${countId ? `'${countId}'` : 'null'})`;
  const bodyRows = rows.map(r => {
    const m = r.m;
    const label = marketLabel(m.ticker);
    const ctx = marketContext(m.ticker, null);
    const category = ctx.category ? esc(ctx.category) : esc(m.ticker.split('-')[0]);
    const yesPrice = r.yesBid != null ? Math.round(r.yesBid * 100) : null;
    const noPrice = yesPrice != null ? 100 - yesPrice : null;
    const spreadStr = r.spread != null ? (r.spread * 100).toFixed(0) + '¢' : '—';
    const volStr = r.volume_24h_fp ? Math.round(r.volume_24h_fp).toLocaleString() : '—';
    const leanCell = !st.includeWhale ? '' : r.leanCount
      ? `<td><span class="side-tag ${r.leanPct >= 50 ? 'yes' : 'no'}">${r.leanPct >= 50 ? 'YES' : 'NO'} ${Math.round(r.leanPct >= 50 ? r.leanPct : 100 - r.leanPct)}%</span> <span style="color:var(--muted);">(${r.leanCount})</span></td>`
      : '<td style="color:var(--muted);">—</td>';
    return `<tr title="${esc(label.full)}" style="cursor:pointer;" onclick="openMarketDetail('${esc(m.ticker)}', '${esc(m.event_ticker || '')}')">
      <td style="text-align:left;">${esc(label.short)} ${liveBadgeHTML(m)}</td>
      <td style="text-align:left;">${esc(r.series)}</td>
      <td>${category}</td>
      <td>${yesPrice != null ? yesPrice + '¢' : '—'} ${priceChangeHTML(m.ticker, r.yesBid)}</td>
      <td>${noPrice != null ? noPrice + '¢' : '—'}</td>
      <td>${spreadStr}</td>
      <td>${volStr}</td>
      ${leanCell}
    </tr>`;
  }).join('');

  const sortCall = (key) => `toggleSort(screenerState['${panelKey}'], '${key}', () => ${rerender})`;
  el.innerHTML = `<table class="positions-table sortable">
    <thead><tr>
      ${sortHeaderHTML(st, 'ticker', 'Market', sortCall('ticker'))}
      ${sortHeaderHTML(st, 'series', 'Series', sortCall('series'))}
      <th>Category</th>
      ${sortHeaderHTML(st, 'yesBid', 'Yes', sortCall('yesBid'))}
      <th>No</th>
      ${sortHeaderHTML(st, 'spread', 'Spread', sortCall('spread'))}
      ${sortHeaderHTML(st, 'volume_24h_fp', '24h Vol', sortCall('volume_24h_fp'))}
      ${st.includeWhale ? sortHeaderHTML(st, 'leanPct', 'Whale Lean', sortCall('leanPct')) : ''}
    </tr></thead>
    <tbody>${bodyRows}</tbody>
  </table>`;
}

// Fixed a real bug: this used to unconditionally show paper broker numbers
// under a static "PAPER" tag regardless of which account Portfolio's toggle
// had selected — someone in "Real Kalshi Account" mode saw the wrong
// account's numbers in the header the whole time. Now follows accountMode
// the same way the Portfolio tab body already does.
function renderHeaderStrip(broker, account, realBalanceHistory) {
  const real = accountMode === 'real' && account && account.connected;
  const tag = $('header-mode-tag');
  tag.textContent = real ? 'REAL' : 'PAPER';
  tag.className = 'mode-tag' + (real ? ' real' : '');

  if (real) {
    const bal = account.balance || {};
    const cash = bal.balance != null ? bal.balance / 100 : null;
    const portfolioValue = bal.portfolio_value != null ? bal.portfolio_value / 100 : null;
    const first = realBalanceHistory[0];
    // Real bug fixed 2026-08-15: this used to diff the current
    // portfolio_value (cash + open positions) against first.balance
    // (cash-only) - two genuinely different scopes, not interchangeable
    // (see the "balance vs portfolio_value" info-icon copy below). Now
    // diffs like-for-like against first.portfolio_value, which is only
    // present on history entries recorded after this fix - null (not a
    // guessed number) until one exists.
    const firstPortfolioValue = (first && first.portfolio_value != null) ? first.portfolio_value : null;
    const pnl = (portfolioValue != null && firstPortfolioValue != null) ? portfolioValue - firstPortfolioValue : null;
    // "(all shards)" (2026-08-30, issue #252): Trade API 3.29.0 changed
    // GET /portfolio/balance to aggregate balance/portfolio_value across
    // every exchange shard by default (docs/kalshi/get-balance.md - "Both
    // values include all exchange indexes unless exchange_index is
    // provided"). Pre-3.29.0 this was shard-0-only. The app deliberately
    // keeps the new aggregate for this whole-account display (see the
    // comment on the account.get_balance() call in
    // services/position/account_positions.py) - the label just has to say
    // so, per CLAUDE.md's "a displayed value must match its label".
    $('header-bankroll-label').textContent = 'Cash Balance (all shards)';
    $('header-equity-label').textContent = 'Portfolio Value (all shards)';
    $('header-pnl-label').textContent = 'Change (session)';
    $('bankroll').textContent = cash != null ? fmt(cash) : '—';
    $('equity').textContent = portfolioValue != null ? fmt(portfolioValue) : '—';
    const pnlEl = $('pnl');
    pnlEl.textContent = pnl != null ? fmt(pnl) : '—';
    pnlEl.className = 'value' + (pnl != null ? (pnl >= 0 ? ' pos' : ' neg') : '');
  } else {
    $('header-bankroll-label').textContent = 'Bankroll';
    $('header-equity-label').textContent = 'Equity';
    $('header-pnl-label').textContent = 'Unrealized P&L';
    $('bankroll').textContent = fmt(broker.bankroll);
    $('equity').textContent = fmt(broker.equity);
    // broker.unrealized_pnl is its own explicit backend field (see
    // PaperBroker.state()), not re-derived here as equity - bankroll -
    // that re-derivation is exactly how this figure broke once already
    // (direct report, 2026-08-09: "I get values for bankroll, equity, and
    // unrealized P&L, but the open positions themselves arent shown" - it
    // was computing equity - starting_bankroll, cumulative all-time P&L
    // including realized gains, not equity - bankroll). It would break
    // again the same way now that equity() itself includes open positions'
    // full current value (cost basis + gain/loss), not just the gain/loss
    // component alone (a separate, deeper bug in equity() itself, found
    // and fixed the same day) - equity - bankroll no longer equals
    // unrealized P&L at all. All-time P&L already has its own correctly-
    // labeled home on the Trading History tab.
    const pnl = broker.unrealized_pnl;
    const pnlEl = $('pnl');
    pnlEl.textContent = fmt(pnl);
    pnlEl.className = 'value ' + (pnl >= 0 ? 'pos' : 'neg');
  }
}

// Per-market drill-down (ROADMAP.md Phase 0.5) — order book depth via
// GET /api/markets/{ticker}/orderbook, which wraps services/kalshi_client.py's
// get_orderbook() (implemented for a while, never called from the dashboard
// until now). A modal, not a card-level toggle: a full depth ladder doesn't
// fit inline in a grid of market cards.
let marketDetailTicker = null;
let marketDetailEventTicker = null;  // needed for the candlesticks endpoint's required series_ticker lookup
// refreshMarketDetail() replaces market-detail-body's innerHTML on every
// poll tick while the modal is open. If a click's mousedown lands on real
// content and a refresh tick swaps that content out before mouseup, the
// browser retargets the click event to whatever ancestor is still in the
// tree - the backdrop - which would otherwise satisfy the naive
// "event.target === this" outside-click check and slam the modal shut
// mid-interaction (confirmed live: this was happening while examining a
// modal, not a hypothetical). Requiring mousedown to ALSO have started on
// the backdrop itself closes that gap: only a genuine press-and-release on
// the dark overlay counts, regardless of what got re-rendered in between.
// Only ever read/written from the two backdrop divs' inline
// onmousedown=/onclick= attributes in index.html directly (never touched
// by any module code) - a real module-scoped `let` here would be dead
// weight the inline handlers can't see anyway (they resolve bare names
// against `window`, not this module's scope), so it's declared straight
// on `window` instead of pretending it's a normal module-local binding.
window.__backdropMouseDownOnSelf = false;

async function openMarketDetail(ticker, eventTicker) {
  marketDetailTicker = ticker;
  marketDetailEventTicker = eventTicker || null;
  renderMarketDetailTitle(ticker);
  $('market-detail-backdrop').classList.add('open');
  $('market-detail-body').innerHTML = '<div class="empty">Loading…</div>';
  renderMarketAnalystSection(ticker);
  await refreshMarketDetail();
}

function closeMarketDetail() {
  marketDetailTicker = null;
  marketDetailEventTicker = null;
  $('market-detail-backdrop').classList.remove('open');
}

// On-demand Market Analyst trigger (direct request, 2026-08-09) - a real
// API call the user deliberately spends on one specific market, not
// something that runs on a schedule. Rendered once per openMarketDetail()
// call (see the HTML comment above market-detail-analyst) so its own
// Analyzing/result state survives refreshMarketDetail()'s ~5s poll cycle.
function renderMarketAnalystSection(ticker) {
  $('market-detail-analyst').innerHTML = `
    <div class="panel-title" style="margin-top:0;">Market Analyst <span class="data-badge new">🆕 new</span></div>
    <div style="font-size:12px; color:var(--muted); margin-bottom:10px;">
      An LLM forms its own independent probability estimate for this market, grounded in this app's own
      whale/advisory track record. Spends one real API call - click only when you actually want its opinion.
    </div>
    <button id="market-analyst-analyze-btn" class="primary" onclick="triggerMarketAnalystAnalysis('${esc(ticker)}')">🔎 Analyze this market</button>
    <div id="market-analyst-result" style="margin-top:12px;"></div>
  `;
}

async function triggerMarketAnalystAnalysis(ticker) {
  const btn = $('market-analyst-analyze-btn');
  const resultEl = $('market-analyst-result');
  if (btn) { btn.disabled = true; btn.textContent = 'Analyzing…'; }
  resultEl.innerHTML = '<div class="empty">Calling the model…</div>';
  try {
    const resp = await fetch('/api/market-analyst/analyze', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker }),
    });
    const result = await resp.json();
    // Stale response guard - the modal may have moved to a different
    // market (or closed) while this real network call was in flight.
    if (marketDetailTicker !== ticker) return;

    if (!result.ok) {
      resultEl.innerHTML = `<div class="empty">${esc(result.reason || 'Analysis unavailable.')}</div>`;
    } else {
      const lean = result.estimated_probability >= 0.5 ? 'YES' : 'NO';
      const leanPct = Math.round(result.estimated_probability >= 0.5 ? result.estimated_probability * 100 : (1 - result.estimated_probability) * 100);
      const marketPct = Math.round((result.market_price ?? 0.5) * 100);
      resultEl.innerHTML = `
        <div class="signal-card">
          <div class="top-row">
            <span>Model estimate: <b class="side-tag ${lean.toLowerCase()}">${lean} ${leanPct}%</b></span>
            <span style="color:var(--muted); font-size:12px;">market was pricing YES ${marketPct}% · model confidence ${Math.round(result.confidence * 100)}%</span>
          </div>
          ${contextLineHTML(ticker, 'yes')}
          <div style="color:var(--muted); font-size:13px; margin-top:6px;">${esc(result.reasoning)}</div>
        </div>
      `;
    }
  } catch (e) {
    if (marketDetailTicker === ticker) {
      resultEl.innerHTML = '<div class="empty">Request failed - see console/server logs.</div>';
    }
  } finally {
    if (btn && marketDetailTicker === ticker) { btn.disabled = false; btn.textContent = '🔎 Re-analyze this market'; }
  }
}

// Glossary (ROADMAP.md P1) - every piece of jargon this app actually uses,
// defined in terms of how THIS app uses it, not a generic textbook
// definition. "Basis points" is included for general prediction-market
// literacy even though nothing in this UI currently displays a number in
// bps - listed explicitly in the roadmap item this addresses, so covered
// here rather than silently dropped for not (yet) appearing on screen.
const GLOSSARY_TERMS = [
  ['Confidence', 'A 0-100% score this app assigns to each whale print by blending several signals into one number, not just "how big was the trade": how large it is relative to that market\'s own trading volume, how unusual the price is, how close the market is to closing, whether other recent prints agree with it or look like part of the same building position, whether it fits with (or fights) the market\'s recent price trend, and - only for a market someone has manually asked an AI analyst about - whether that analyst\'s own independent read agrees. A smaller, well-corroborated print can score higher than a single isolated giant one. The strategy only acts on prints above a configured confidence threshold.'],
  ['Whale "lean"', 'Which side - Yes or No - recent large trades have mostly been betting on, weighted by how confident this app is in each individual print, not just raw trade size - so a few well-corroborated prints can outweigh a bigger pile of shaky ones. This is the core signal this app follows: when whales lean hard one way, the strategy considers following them.'],
  ['Cooldown', 'How long the strategy waits before trading the same market again after already trading it once, so one whale print doesn\'t turn into repeatedly piling into the same market.'],
  ['Kill switch', 'An automatic pause that halts all new trades for the day once total account value - cash plus whatever any open positions are currently worth, not just cash on hand - drops a configured percentage below where the day started. A safety net that fires on its own, not a strategy decision.'],
  ['Implied probability', 'What a market\'s price says about the odds, restated as a percent chance - a market trading at 30¢ implies roughly a 30% chance of the event happening.'],
  ['Basis points (bps)', 'A unit equal to 0.01 percentage points (100 bps = 1 percentage point) - a finer-grained way to talk about small probability/price moves than whole percent.'],
  ['Series', 'Kalshi\'s term for a recurring template of similar markets (e.g. "will it rain in NYC tomorrow" repeats daily as its own series). This app groups whale accuracy by series so "how have whales done on this type of market" means something, instead of judging a whale off one single market.'],
  ['Combo / parlay market', 'A market whose outcome depends on more than one underlying event at once (e.g. "Team A wins AND scores over 100"). Kalshi auto-generates these in bulk - they vastly outnumber single-outcome markets and rarely have real trading activity, so automatic discovery finds real single-outcome markets by design rather than needing to filter these out after the fact. If one does show up (e.g. in a real position), this app shows its real per-selection breakdown - each leg\'s own market and current price - rather than just a collapsed title.'],
  ['Shadow mode', 'Runs the exact same trading logic this app would use for real, sized against what a connected real account\'s money would allow, and logs what it would have done - without ever placing a real order. The step to trust before ever enabling real trading.'],
  ['Paper trading', 'Trading with fake money against real, live market prices - the default mode this app runs in, so a strategy can be tested with zero financial risk.'],
  ['Live market', 'A market tied to a real-world event that\'s currently in progress right now (a game being played, etc.), as opposed to one that hasn\'t started yet or has already finished. Shown with a LIVE badge.'],
];

function renderHelpGlossary() {
  $('help-glossary').innerHTML = GLOSSARY_TERMS.map(([term, def]) =>
    `<p style="margin:0 0 10px;"><b>${esc(term)}</b> — ${esc(def)}</p>`
  ).join('');
}

function openHelp() {
  renderHelpGlossary();
  $('help-backdrop').classList.add('open');
  localStorage.setItem('whale-signal-seen-intro', '1');
}
function closeHelp() {
  $('help-backdrop').classList.remove('open');
}

// Separate from refreshMarketDetail's body (order book/chart/trades) since
// this needs to re-run on every poll too, not just on open - the event's
// category/matchup may not be cached yet the first time a market is opened
// (event lookups happen once per poll tick, server-side), so the title
// would otherwise stay stuck without it until the modal is closed and
// reopened.
function renderMarketDetailTitle(ticker) {
  const label = marketLabel(ticker);
  const ctx = marketContext(ticker, null);
  const ctxLine = ctx.category || ctx.matchup
    ? `<div class="context-line" style="margin-top:4px;">${ctx.category ? `<span class="cat-tag">${esc(ctx.category)}</span> · ` : ''}${ctx.matchup ? esc(ctx.matchup) : ''}</div>`
    : '';
  $('market-detail-title').innerHTML = `${esc(label.short)}${ctxLine}${comboLegsHTML(ticker)}`;
}

async function refreshMarketDetail() {
  if (!marketDetailTicker) return;
  const ticker = marketDetailTicker;
  const eventTicker = marketDetailEventTicker;
  renderMarketDetailTitle(ticker);

  let detail = null, detailFailed = false;
  try {
    detail = await fetchJSON(`/api/markets/${encodeURIComponent(ticker)}/detail`);
  } catch (e) {
    detailFailed = true;
  }

  let book = null, bookFailed = false;
  try {
    book = await fetchJSON(`/api/markets/${encodeURIComponent(ticker)}/orderbook`);
  } catch (e) {
    bookFailed = true;
  }

  let candles = null, candlesFailed = false;
  if (eventTicker) {
    try {
      const resp = await fetchJSON(`/api/markets/${encodeURIComponent(ticker)}/candlesticks?event_ticker=${encodeURIComponent(eventTicker)}`);
      candles = resp.candlesticks || [];
    } catch (e) {
      candlesFailed = true;
    }
  }

  let trades = null, tradesFailed = false;
  try {
    const resp = await fetchJSON(`/api/markets/${encodeURIComponent(ticker)}/trades`);
    trades = resp.trades || [];
  } catch (e) {
    tradesFailed = true;
  }

  if (marketDetailTicker !== ticker) return; // closed or switched while requests were in flight

  const infoHtml = detailFailed ? '<div class="empty">Failed to load market detail.</div>' : renderMarketInfoHTML(detail);
  const bookHtml = bookFailed ? '<div class="empty">Failed to load order book.</div>' : renderOrderbookHTML(book);
  const chartHtml = !eventTicker
    ? ''  // no event_ticker known (shouldn't normally happen - every call site passes one) - skip rather than guess
    : candlesFailed ? '<div class="empty">Failed to load price history.</div>' : renderCandlestickHTML(candles);
  const tradesHtml = tradesFailed ? '<div class="empty">Failed to load recent trades.</div>' : renderMarketTradesHTML(trades, ticker);
  const hr = '<hr style="border-color: var(--panel-border); margin: 20px 0;">';
  $('market-detail-body').innerHTML = infoHtml + hr + chartHtml + hr + bookHtml + hr + tradesHtml;
}

// The Kalshi-landing-page-style summary at the top of the drill-down modal
// (category/title/subtitle, current Yes/No prices, volume/open interest/
// liquidity, rules text, and — for multi-outcome events — every sibling
// market's own price, exactly the outcome table Kalshi's own market page
// shows). Backed by GET /api/markets/{ticker}/detail, which bundles
// get_market + get_event (siblings come back "for free" as part of the
// event payload rather than needing one call per sibling).
function renderMarketInfoHTML(d) {
  if (!d) return '';
  const category = d.event ? d.event.category : null;
  const title = (d.event && d.event.title) || d.title;
  const subtitle = (d.event && d.event.sub_title) || d.subtitle;
  const eventTicker = d.event_ticker || (d.event && d.event.event_ticker);
  const catLine = category || subtitle
    ? `<div class="context-line">${category ? `<span class="cat-tag">${esc(category)}</span> · ` : ''}${subtitle ? esc(subtitle) : ''}</div>`
    : '';
  const taxonomyHtml = marketTaxonomyHTML(d.ticker);
  const liveDataHtml = eventLiveDataLineHTML(eventTicker, false);

  const yesPrice = d.yes_bid != null ? Math.round(d.yes_bid * 100) : null;
  const noPrice = d.no_bid != null ? Math.round(d.no_bid * 100) : (yesPrice != null ? 100 - yesPrice : null);
  const prevYes = d.previous_price != null ? Math.round(d.previous_price * 100) : null;
  const change = (yesPrice != null && prevYes) ? yesPrice - prevYes : null;
  const changeHtml = change != null && change !== 0
    ? ` <span style="font-size:11px; color:${change > 0 ? 'var(--yes)' : 'var(--no)'};">${change > 0 ? '▲' : '▼'} ${Math.abs(change)}¢</span>`
    : '';

  const prices = `<div class="market-card prices" style="display:flex; gap:10px; margin:12px 0;">
    <div class="price-btn yes"><div class="lbl">Yes</div><div class="val">${yesPrice != null ? yesPrice + '¢' : '—'}${changeHtml}</div></div>
    <div class="price-btn no"><div class="lbl">No</div><div class="val">${noPrice != null ? noPrice + '¢' : '—'}</div></div>
  </div>`;

  const statRow = (label, val) => `<div><div class="lbl" style="font-size:10px; text-transform:uppercase; letter-spacing:0.06em; color:var(--muted);">${esc(label)}</div><div style="font-family:var(--mono); font-size:13px;">${val}</div></div>`;
  const closeStr = d.close_time ? new Date(d.close_time).toLocaleString() : '—';
  const openStr = d.open_time ? new Date(d.open_time).toLocaleString() : '—';
  // last_price/liquidity/open_time - direct data-usage review finding: all
  // three were already on this endpoint's own response
  // (_slim_detail_market in main.py) and simply never read here.
  // last_price is the most recent real trade, distinct from yes_bid (the
  // current best resting bid) - useful specifically when a market is thin
  // enough that the bid is stale or zero but a real trade happened
  // recently at a different price.
  const lastPriceStr = d.last_price != null ? Math.round(d.last_price * 100) + '¢' : '—';
  const liquidityStr = d.liquidity != null ? '$' + Math.round(d.liquidity).toLocaleString() : '—';
  const stats = `<div style="display:flex; gap:20px; margin-bottom:14px; flex-wrap:wrap;">
    ${statRow('24h Volume', d.volume_24h != null ? '$' + Math.round(d.volume_24h).toLocaleString() : '—')}
    ${statRow('Open Interest', d.open_interest != null ? Math.round(d.open_interest).toLocaleString() : '—')}
    ${statRow('Liquidity', liquidityStr)}
    ${statRow('Last Trade', lastPriceStr)}
    ${statRow('Status', esc(d.status || '—'))}
    ${statRow('Opened', esc(openStr))}
    ${statRow('Closes', esc(closeStr))}
  </div>`;

  // rules_secondary carries real, sometimes load-bearing resolution detail
  // rules_primary alone doesn't (e.g. "only that round's score counts, not
  // the cumulative tournament score") - fetched alongside rules_primary by
  // this same endpoint already, previously dropped on the floor here.
  const rules = (d.rules_primary || d.rules_secondary)
    ? `<div style="font-size:12px; color:var(--muted); line-height:1.5; margin-bottom:14px;">
        ${d.rules_primary ? `<p style="margin:0 0 8px;">${esc(d.rules_primary)}</p>` : ''}
        ${d.rules_secondary ? `<p style="margin:0;">${esc(d.rules_secondary)}</p>` : ''}
      </div>`
    : '';

  let siblingsHtml = '';
  if (d.siblings && d.siblings.length) {
    const rows = d.siblings.map(s => {
      const sYes = s.yes_bid != null ? Math.round(s.yes_bid * 100) : null;
      const sNo = s.no_bid != null ? Math.round(s.no_bid * 100) : (sYes != null ? 100 - sYes : null);
      const name = s.yes_sub_title || s.title || s.ticker;
      return `<div class="outcome-row" onclick="openMarketDetail('${esc(s.ticker)}', '${esc(d.event_ticker || '')}')" title="${esc(s.title || s.ticker)}">
        <span class="outcome-name">${esc(name)}</span>
        <span class="outcome-prices">
          <span class="mini-price yes">${sYes !== null ? sYes + '¢' : '—'}</span>
          <span class="mini-price no">${sNo !== null ? sNo + '¢' : '—'}</span>
        </span>
      </div>`;
    }).join('');
    siblingsHtml = `<div class="panel-title" style="margin-top:0;">Other Outcomes In This Event</div><div class="outcome-rows" style="margin-bottom:14px;">${rows}</div>`;
  }

  return catLine + taxonomyHtml + liveDataHtml + prices + stats + rules + siblingsHtml;
}

// Recent trades for this one market - distinct from the full-exchange trade
// tape (a separate, not-yet-built feed across every watched market, see
// ROADMAP.md). No Simple/Advanced split here, unlike the book/chart above -
// a short recent-trades list doesn't have a meaningfully denser "Advanced"
// form the way a full order book or candlestick history does.
function renderMarketTradesHTML(trades, ticker) {
  if (!trades || !trades.length) {
    return '<div class="panel-title" style="margin-top:0;">Recent Trades</div><div class="empty">No recent trades on this market.</div>';
  }
  const rows = trades.map(t => {
    const side = t.taker_side === 'no' ? 'no' : 'yes';
    const priceDollars = side === 'no' ? t.no_price_dollars : t.yes_price_dollars;
    const price = priceDollars != null ? (parseFloat(priceDollars) * 100).toFixed(0) + '¢' : '—';
    const count = t.count_fp != null ? parseFloat(t.count_fp).toLocaleString() : '—';
    const when = t.created_time ? new Date(t.created_time).toLocaleTimeString() : '';
    return `<div class="trade-row">
      <div class="name">Taker bought <span class="side-tag ${side}">${side}</span>
        ${ticker ? contextLineHTML(ticker, side) : ''}
      </div>
      <div class="nums"><span>${count} contracts @ ${price}</span><span style="color: var(--muted)">${esc(when)}</span></div>
    </div>`;
  }).join('');
  return `<div class="panel-title" style="margin-top:0;">Recent Trades</div><div class="scroll-panel" style="max-height:240px;">${rows}</div>`;
}

// Kalshi's book is two independent bid ladders (yes_dollars/no_dollars),
// each [price_dollars_string, size_string] pairs — there's no separate "ask"
// array. A resting NO bid at price P is equivalent to an offer to sell YES
// at (1 - P), which is where the "YES ask" in the Simple summary comes from;
// the Advanced ladder shows both bid ladders as-is (Kalshi's own terms)
// rather than converting, so it can't silently get that conversion wrong in
// a way that's hard to notice.
function renderOrderbookHTML(book) {
  const ob = (book && book.orderbook_fp) || {};
  const yesLevels = (ob.yes_dollars || []).map(([p, s]) => ({price: parseFloat(p), size: parseFloat(s)}))
    .sort((a, b) => b.price - a.price);
  const noLevels = (ob.no_dollars || []).map(([p, s]) => ({price: parseFloat(p), size: parseFloat(s)}))
    .sort((a, b) => b.price - a.price);

  const bestYesBid = yesLevels.length ? yesLevels[0].price : null;
  const bestYesAsk = noLevels.length ? 1 - noLevels[0].price : null;
  const spread = (bestYesBid != null && bestYesAsk != null) ? (bestYesAsk - bestYesBid) : null;

  const summary = `<div class="book-summary">
    <div class="price-btn yes"><div class="lbl">Best Yes Bid</div><div class="val">${bestYesBid != null ? (bestYesBid*100).toFixed(0)+'¢' : '—'}</div></div>
    <div class="spread"><div class="lbl">Spread</div><div class="val">${spread != null ? (spread*100).toFixed(0)+'¢' : '—'}</div></div>
    <div class="price-btn no"><div class="lbl">Best Yes Ask</div><div class="val">${bestYesAsk != null ? (bestYesAsk*100).toFixed(0)+'¢' : '—'}</div></div>
  </div>`;

  const toggle = `<div style="text-align:right; margin-bottom:12px;">${advToggleHTML('orderbook')}</div>`;

  if (!isAdvanced('orderbook')) {
    return toggle + summary;
  }

  const levelHTML = (l) => `<div class="level"><span class="price">${(l.price*100).toFixed(0)}¢</span><span class="size">${l.size.toLocaleString()} ct</span></div>`;
  const ladder = `<div class="book-ladder">
    <div class="side yes"><h4>Yes bids</h4><div class="levels">${yesLevels.length ? yesLevels.map(levelHTML).join('') : '<div class="empty">No resting Yes bids</div>'}</div></div>
    <div class="side no"><h4>No bids</h4><div class="levels">${noLevels.length ? noLevels.map(levelHTML).join('') : '<div class="empty">No resting No bids</div>'}</div></div>
  </div>`;

  return toggle + summary + ladder;
}

// Kalshi's candlestick price.*_dollars fields are null for any period with
// no actual trade (common on thin markets - confirmed on real data) - falls
// back to the yes_bid/yes_ask midpoint for those periods so the line/chart
// doesn't just vanish, and carries the last known value forward if even
// that's unavailable, rather than plotting a misleading gap-to-zero.
function candleClose(c) {
  const price = c.price && c.price.close_dollars != null ? parseFloat(c.price.close_dollars) : null;
  if (price != null) return price;
  const bid = c.yes_bid && c.yes_bid.close_dollars != null ? parseFloat(c.yes_bid.close_dollars) : null;
  const ask = c.yes_ask && c.yes_ask.close_dollars != null ? parseFloat(c.yes_ask.close_dollars) : null;
  if (bid != null && ask != null) return (bid + ask) / 2;
  return bid ?? ask ?? null;
}

function renderCandlestickHTML(candles) {
  const toggle = `<div style="text-align:right; margin-bottom:12px;">${advToggleHTML('price-chart')}</div>`;
  if (!candles || !candles.length) {
    return toggle + '<div class="empty">No price history yet for this market.</div>';
  }

  let lastClose = null;
  const points = candles.map(c => {
    let close = candleClose(c);
    if (close == null) close = lastClose;  // still null on the very first candle - filtered out below
    lastClose = close;
    return {
      ts: c.end_period_ts,
      close,
      open: c.price && c.price.open_dollars != null ? parseFloat(c.price.open_dollars) : close,
      high: c.price && c.price.high_dollars != null ? parseFloat(c.price.high_dollars) : close,
      low: c.price && c.price.low_dollars != null ? parseFloat(c.price.low_dollars) : close,
      volume: c.volume_fp != null ? parseFloat(c.volume_fp) : 0,
      // yes_bid/yes_ask low-high bands and open_interest_fp - direct
      // data-usage review finding: fetched on every candlestick already,
      // never used. Bands render as a shaded spread behind the candles in
      // Advanced (real, visible liquidity context distinct from the single
      // mid-price line - e.g. spread tightening/widening over time);
      // open_interest is summarized as a first-vs-last delta in the
      // caption rather than a full second chart series (its scale is
      // usually orders of magnitude off from a 0-1 price axis, and a
      // second Y-axis would add real complexity for a nice-to-have).
      bidLow: c.yes_bid && c.yes_bid.low_dollars != null ? parseFloat(c.yes_bid.low_dollars) : null,
      bidHigh: c.yes_bid && c.yes_bid.high_dollars != null ? parseFloat(c.yes_bid.high_dollars) : null,
      askLow: c.yes_ask && c.yes_ask.low_dollars != null ? parseFloat(c.yes_ask.low_dollars) : null,
      askHigh: c.yes_ask && c.yes_ask.high_dollars != null ? parseFloat(c.yes_ask.high_dollars) : null,
      openInterest: c.open_interest_fp != null ? parseFloat(c.open_interest_fp) : null,
    };
  }).filter(p => p.close != null);

  if (!points.length) {
    return toggle + '<div class="empty">No price history yet for this market.</div>';
  }

  if (!isAdvanced('price-chart')) {
    // Simple: a compact sparkline, same hand-rolled inline-SVG approach as
    // the Portfolio equity chart (see renderEquityChart) - no library, just
    // a trend line, no axes.
    const w = 560, h = 100, pad = 6;
    const closes = points.map(p => p.close);
    const min = Math.min(...closes), max = Math.max(...closes);
    const range = (max - min) || 0.01;
    const xStep = (w - pad * 2) / (Math.max(points.length - 1, 1));
    const svgPoints = points.map((p, i) => {
      const x = pad + i * xStep;
      const y = pad + (1 - (p.close - min) / range) * (h - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    const last = closes[closes.length - 1], first = closes[0];
    const color = last >= first ? 'var(--yes)' : 'var(--no)';
    return toggle + `
      <svg viewBox="0 0 ${w} ${h}" style="width:100%; height:100px; display:block;">
        <polyline points="${svgPoints}" style="fill:none; stroke:${color}; stroke-width:2;" />
      </svg>
      <div style="font-family:var(--mono); font-size:10px; color:var(--muted); display:flex; justify-content:space-between; margin-top:4px;">
        <span>${(first*100).toFixed(0)}¢ 7d ago</span>
        <span>${(last*100).toFixed(0)}¢ now</span>
      </div>`;
  }

  // Advanced: real OHLC candlesticks + a volume bar per period, hand-rolled
  // SVG (no charting library, matching this app's existing chart approach).
  const w = 640, h = 260, volH = 50, pad = 8;
  const priceH = h - volH - pad * 2;
  const highs = points.map(p => p.high), lows = points.map(p => p.low);
  const bandHighs = points.map(p => p.askHigh).filter(v => v != null);
  const bandLows = points.map(p => p.bidLow).filter(v => v != null);
  const min = Math.min(...lows, ...bandLows), max = Math.max(...highs, ...bandHighs);
  const range = (max - min) || 0.01;
  const volMax = Math.max(...points.map(p => p.volume), 1);
  const n = points.length;
  const slotW = (w - pad * 2) / n;
  const bodyW = Math.max(slotW * 0.6, 1);
  const yFor = (price) => pad + (1 - (price - min) / range) * priceH;
  const xFor = (i) => pad + i * slotW + slotW / 2;

  // Bid/ask spread bands - real, visible liquidity context (spread
  // tightening/widening over time) distinct from the single mid-price line
  // above. Drawn first so the candles paint on top of them. Skips gaps
  // (quiet periods with no bid/ask data) rather than distorting the shape
  // by treating a missing value as zero.
  const bandPolygon = (lowKey, highKey) => {
    const valid = points.map((p, i) => ({ i, low: p[lowKey], high: p[highKey] })).filter(p => p.low != null && p.high != null);
    if (valid.length < 2) return '';
    const top = valid.map(p => `${xFor(p.i).toFixed(1)},${yFor(p.high).toFixed(1)}`).join(' ');
    const bottom = valid.slice().reverse().map(p => `${xFor(p.i).toFixed(1)},${yFor(p.low).toFixed(1)}`).join(' ');
    return `${top} ${bottom}`;
  };
  const bidBand = bandPolygon('bidLow', 'bidHigh');
  const askBand = bandPolygon('askLow', 'askHigh');
  const bands = `
    ${bidBand ? `<polygon points="${bidBand}" fill="var(--yes)" opacity="0.08" />` : ''}
    ${askBand ? `<polygon points="${askBand}" fill="var(--no)" opacity="0.08" />` : ''}
  `;

  const bars = points.map((p, i) => {
    const x = xFor(i);
    const up = p.close >= p.open;
    const color = up ? 'var(--yes)' : 'var(--no)';
    const yHigh = yFor(p.high), yLow = yFor(p.low);
    const yOpen = yFor(p.open), yClose = yFor(p.close);
    const bodyTop = Math.min(yOpen, yClose), bodyBot = Math.max(yOpen, yClose);
    const volBarH = (p.volume / volMax) * volH;
    return `
      <line x1="${x}" y1="${yHigh.toFixed(1)}" x2="${x}" y2="${yLow.toFixed(1)}" stroke="${color}" stroke-width="1" />
      <rect x="${(x - bodyW/2).toFixed(1)}" y="${bodyTop.toFixed(1)}" width="${bodyW.toFixed(1)}" height="${Math.max(bodyBot - bodyTop, 1).toFixed(1)}" fill="${color}" />
      <rect x="${(x - bodyW/2).toFixed(1)}" y="${(h - volBarH).toFixed(1)}" width="${bodyW.toFixed(1)}" height="${volBarH.toFixed(1)}" fill="${color}" opacity="0.35" />
    `;
  }).join('');

  // open_interest_fp - shown as a first-vs-last delta rather than a full
  // second chart series: its scale is usually orders of magnitude off from
  // a 0-1 price axis, and a second Y-axis would add real complexity for
  // what this app's own data-usage review flagged as a nice-to-have.
  const oiPoints = points.filter(p => p.openInterest != null);
  const oiLabel = oiPoints.length >= 2
    ? `OI ${Math.round(oiPoints[0].openInterest).toLocaleString()} → ${Math.round(oiPoints[oiPoints.length-1].openInterest).toLocaleString()}`
    : 'hourly candles, volume below';

  return toggle + `
    <svg viewBox="0 0 ${w} ${h}" style="width:100%; height:260px; display:block;">${bands}${bars}</svg>
    <div style="font-family:var(--mono); font-size:10px; color:var(--muted); display:flex; justify-content:space-between; margin-top:4px;">
      <span>${new Date(points[0].ts*1000).toLocaleDateString()}</span>
      <span>${esc(oiLabel)}</span>
      <span>${new Date(points[points.length-1].ts*1000).toLocaleDateString()}</span>
    </div>`;
}

// Global "is this real money" banner (ROADMAP.md P1) - real orders can only
// ever be placed when kalshi_account.trading_enabled is true (see
// services/kalshi_account_client.py's _require_trading_enabled), so that
// flag - not just "is a real account connected" - is the genuinely correct
// signal for "real money is actually at risk right now." A connected-but-
// not-enabled account is still 100% safe under this app's architecture.
function renderRealMoneyBanner(account) {
  const el = $('real-money-banner');
  const tradingEnabled = !!(account && account.trading_enabled);
  if (tradingEnabled) {
    el.className = 'real-money-banner danger';
    el.textContent = '⚠️ REAL TRADING IS ENABLED — real orders can be placed with real money';
  } else {
    el.className = 'real-money-banner safe';
    el.textContent = '🧪 PAPER TRADING — no real money is at risk right now';
  }
}

function renderAccount(account) {
  const el = $('account-panel');
  // refresh() calls this every 5s and always rebuilds this panel's innerHTML
  // from scratch - if the trading confirmation gate is open, that would wipe
  // whatever the user has typed (and their focus/cursor) mid-confirmation.
  // Capture it here and restore it after the rebuild below.
  const priorInput = document.getElementById('trading-confirm-input');
  const priorValue = priorInput ? priorInput.value : '';
  const hadFocus = priorInput && priorInput === document.activeElement;
  const priorSelStart = hadFocus ? priorInput.selectionStart : null;
  const priorSelEnd = hadFocus ? priorInput.selectionEnd : null;

  if (!account || !account.connected) {
    el.innerHTML = `<div class="account-bar disconnected">
      <span class="tag">○ REAL KALSHI ACCOUNT — not connected</span>
      <span class="msg">set KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH in .env to see your real balance here (read-only; separate from the paper numbers above)</span>
    </div>`;
    return;
  }
  if (account.error) {
    el.innerHTML = `<div class="account-bar">
      <span class="tag">⚠ REAL KALSHI ACCOUNT</span>
      <span class="msg" style="color: var(--danger)">error: ${account.error}</span>
    </div>`;
    return;
  }
  const bal = account.balance || {};
  // "balance" is uninvested cash; "portfolio_value" is cash + open positions'
  // value - both verified real fields (2026-08-07), genuinely different
  // numbers, not a fallback for each other. Showing only "balance" reads as
  // "you have basically nothing" right next to a positions list proving
  // otherwise, so both are shown explicitly instead of picking one.
  // Both are also cross-shard aggregates as of Trade API 3.29.0 (2026-08-30,
  // issue #252) - see the "(all shards)" comment in renderHeaderStrip above
  // for the full contract citation; labels below say so too.
  const balanceCents = bal.balance ?? null;
  const portfolioCents = bal.portfolio_value ?? null;
  const positions = account.positions;
  let positionCount = '—';
  if (Array.isArray(positions)) positionCount = positions.length;
  else if (positions && Array.isArray(positions.market_positions)) positionCount = positions.market_positions.length;

  const tradingHtml = account.trading_enabled
    ? `<span class="trading-status"><span class="badge-on">🔴 REAL TRADING ENABLED</span>
        <button class="danger" onclick="disableTrading()">Disable</button></span>`
    : `<span class="trading-status"><span class="badge-off">real trading off</span>
        <button class="danger" onclick="toggleTradingGate()">Enable Real Trading…</button></span>`;

  el.innerHTML = `<div class="account-bar">
    <span class="tag">● REAL KALSHI ACCOUNT — REAL MONEY, not the paper numbers above</span>
    <span class="field"><span class="label">Cash Balance (all shards)</span>${balanceCents != null ? fmt(balanceCents / 100) : 'see raw ↴'}</span>
    <span class="field"><span class="label">Portfolio Value (all shards)</span>${portfolioCents != null ? fmt(portfolioCents / 100) : 'see raw ↴'}</span>
    <span class="field"><span class="label">Positions</span>${positionCount}</span>
    ${tradingHtml}
    <details>
      <summary>raw response</summary>
      <pre>${JSON.stringify({balance: account.balance, positions: account.positions}, null, 2)}</pre>
    </details>
    ${tradingGateOpen ? tradingGateHTML() : ''}
  </div>`;

  if (tradingGateOpen) {
    const input = document.getElementById('trading-confirm-input');
    if (input && priorValue) {
      input.value = priorValue;
      document.getElementById('trading-confirm-btn').disabled = (priorValue !== TRADING_PHRASE);
    }
    if (input && hadFocus) {
      input.focus();
      input.setSelectionRange(priorSelStart, priorSelEnd);
    }
  }
}

// Real trading is off by default and only ever turns on through this gate —
// POST /api/config explicitly refuses to touch kalshi_account.trading_enabled
// (see main.py), so this typed-phrase confirmation is the only path. Typing
// it out (not a checkbox) is deliberate friction for a decision that places
// real orders with real money.

export { GLOSSARY_TERMS, candleClose, closeHelp, closeMarketDetail, marketDetailEventTicker, marketDetailTicker, openHelp, openMarketDetail, refreshMarketDetail, renderAccount, renderCandlestickHTML, renderHeaderStrip, renderHelpGlossary, renderMarketAnalystSection, renderMarketDetailTitle, renderMarketInfoHTML, renderMarketTradesHTML, renderOrderbookHTML, renderRealMoneyBanner, renderScreenerTable, renderScreenerTableFromState, screenerRowData, screenerState, triggerMarketAnalystAnalysis };

// Exposed for inline HTML event handlers (onclick=/onchange=/oninput=,
// including ones built indirectly via a caller-supplied onclick-string
// parameter - see shared-utils.js's header). Every top-level function in
// this file is exposed (cheap, harmless if unused); state objects only the
// specific ones confirmed to be read/mutated directly from a handler.
window.candleClose = candleClose;
window.closeHelp = closeHelp;
window.closeMarketDetail = closeMarketDetail;
window.openHelp = openHelp;
window.openMarketDetail = openMarketDetail;
window.refreshMarketDetail = refreshMarketDetail;
window.renderAccount = renderAccount;
window.renderCandlestickHTML = renderCandlestickHTML;
window.renderHeaderStrip = renderHeaderStrip;
window.renderHelpGlossary = renderHelpGlossary;
window.renderMarketAnalystSection = renderMarketAnalystSection;
window.renderMarketDetailTitle = renderMarketDetailTitle;
window.renderMarketInfoHTML = renderMarketInfoHTML;
window.renderMarketTradesHTML = renderMarketTradesHTML;
window.renderOrderbookHTML = renderOrderbookHTML;
window.renderRealMoneyBanner = renderRealMoneyBanner;
window.renderScreenerTable = renderScreenerTable;
window.renderScreenerTableFromState = renderScreenerTableFromState;
window.screenerRowData = screenerRowData;
window.triggerMarketAnalystAnalysis = triggerMarketAnalystAnalysis;
window.screenerState = screenerState;
