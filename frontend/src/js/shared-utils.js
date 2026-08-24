import { renderMarketCards } from './equity-and-cards.js';
import { categoryMetadata, liveStatus, refresh, seriesMeta, terminalLatestPrices } from './polling-and-websocket.js';
import { marketDetailTicker, refreshMarketDetail, renderScreenerTable } from './screener-and-header.js';

// static/index.html's inline <script> was split into these static/js/*.js
// files by concern (2026-08-22 modularization, mirroring main.py's own
// 7-phase split earlier the same session) - index.html loads all 12 in a
// fixed order via plain <script src> tags, NOT type="module". They are
// classic scripts, not ES modules: every top-level function/let/const here
// is a real shared global, exactly as if this were still one file, and
// inline onclick="..."/onchange="..." handlers in index.html's markup
// still resolve by that shared global name. Load order in index.html only
// matters for code that runs immediately at parse time (there is none);
// every render/refresh/event-handler call happens after all 12 files have
// finished loading, so forward references across files are safe. This is
// deliberately NOT real module-boundary separation (no import/export, no
// enforced dependency graph) - see ROADMAP.md's frontend-modularization
// item for that as a distinct, not-yet-started follow-up.
//
// This file: DOM/formatting primitives, the fetchJSON wrapper, shared
// market-metadata caches (marketTitles/eventTitles/seriesMeta/...) and the
// series/market label + event-live-data helpers almost every other file
// calls into, and the Markets-tab table renderer.
const $ = (id) => document.getElementById(id);
const fmt = (n) => n === undefined || n === null ? '—' : Number(n).toLocaleString(undefined, {style:'currency', currency:'USD'});

async function fetchJSON(url, opts) {
  // Real bug found 2026-08-17 investigating "the whale calibration tool...
  // doesn't actually refine itself... just does nothing": fetch() only
  // rejects on a network failure, never on an HTTP error status - a 400/404/
  // 500 is still a "successful" fetch as far as the Promise is concerned.
  // This function used to hand res.json() straight back regardless of
  // status, so every catch(e) block anywhere in this file that calls
  // fetchJSON - and there are dozens, all written assuming a failed request
  // throws - was silently dead code for any error the backend reports via
  // its own status code (virtually every raise HTTPException(...) in
  // main.py). A button whose action hit a 400 (disabled feature, nothing to
  // apply, stale/not-found recommendation) would read the response body,
  // find no exception, and run its own success path anyway - "Applied ✓" on
  // a request that outright failed, with the backend's own detail message
  // never even surfaced. Confirmed live via the calibration panel's own new
  // Apply button: a 400 "nothing to apply" response rendered as "Applied ✓".
  const res = await fetch(url, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch (e) { /* body wasn't JSON or was empty - keep statusText */ }
    throw new Error(detail);
  }
  return res.json();
}

let marketTitles = {};
let eventTitles = {};  // event_ticker -> {title, subtitle, category} — see renderMarketCards' grouping
let eventLiveData = {};
let marketPanelState = {
  markets: { markets: [], prices: {}, signals: [], includeWhale: false, seriesTrackRecord: {} },
  'whale-cards': { markets: [], prices: {}, signals: [], includeWhale: true, seriesTrackRecord: {} },
};
let marketPanelFilters = {
  markets: { category: '', scope: '', tag: '' },
  'whale-cards': { category: '', scope: '', tag: '' },
};
// accountMode is reassigned from two other files (main.js's setAccountMode
// and trading-gate-and-connectivity.js's disableTrading) - ES modules only
// let the declaring file reassign its own binding (an importer's binding
// is read-only), so cross-file writers go through this setter instead of
// a bare `accountMode = ...`.
let accountMode = localStorage.getItem('whale-signal-account-mode') || 'paper'; // 'paper' | 'real' — Portfolio view toggle
function _setAccountMode(v) {
  accountMode = v;
}

function esc(str) {
  return String(str).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// A config value being logged/shown isn't always a scalar - calibration
// auto-apply replaces the whole whale_confidence_weights dict in one shot
// (direct report, 2026-08-11: "the config change log shows [Object object]
// instead"), and a plain String(v) on an object/array just calls its
// default toString(), which is literally the string "[object Object]".
// JSON.stringify is a good-enough plain-text rendering for a dict this
// small (8 weight fields) without needing per-field formatting logic.
function formatConfigValue(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

// Kalshi contracts settle to $1 or $0 each - a position/trade/fill row's max
// payout if it resolves your way is just its contract count, in dollars.
// Shown but never previously computed anywhere in the app. Simple tier only
// (one number) - the Advanced breakdown (cost/mark-to-market/breakeven) is a
// follow-up, see ROADMAP.md.
function payoutHTML(count) {
  if (count === undefined || count === null || isNaN(count)) return '';
  return `<span class="payout">pays ${fmt(Math.abs(count))} if right</span>`;
}

// The actual dollar amount put into a position/trade (contracts x price) -
// requested directly: "the actual money total put into the position," not
// just contract count and price per contract, which requires doing that
// multiplication in your head. Expects an already side-adjusted price -
// see sideAdjustedPrice below for callers starting from a raw yes-price.
function costHTML(count, price) {
  if (count == null || price == null || isNaN(count) || isNaN(price)) return '';
  return `<span class="cost" title="Contracts x price, before fees">${fmt(Math.abs(count) * price)} put in</span>`;
}

// The no-side inversion every no-side dollar/percentage/likelihood figure
// needs (CLAUDE.md's "no-side dollar math" bug pattern) - price is always
// expressed in yes terms; a "no" position's real unit cost/value is
// (1 - price), never price itself. One place this math happens now,
// instead of reimplemented inline at every call site.
function sideAdjustedPrice(side, price) {
  return side === 'no' ? (1 - price) : price;
}

// Shared Simple/Advanced toggle — see ROADMAP.md Phase 0.5. panelId is a
// short stable key per panel (e.g. "positions", "orderbook-<ticker>"), not
// tied to which tab it's on, so the choice persists independently per panel.
function isAdvanced(panelId) {
  return localStorage.getItem('adv-' + panelId) === '1';
}
function toggleAdvanced(panelId) {
  localStorage.setItem('adv-' + panelId, isAdvanced(panelId) ? '0' : '1');
  refresh();
  // The market-detail modal isn't part of refresh()'s normal tab rendering
  // (it can be open regardless of which tab is behind it), so it needs its
  // own re-render when its own toggle (panelId 'orderbook') flips.
  if (marketDetailTicker) refreshMarketDetail();
}
function advToggleHTML(panelId) {
  const on = isAdvanced(panelId);
  return `<button class="adv-toggle ${on ? 'on' : ''}" onclick="toggleAdvanced('${esc(panelId)}')" title="Switch between a simple summary and full detail">${on ? 'Advanced' : 'Simple'}</button>`;
}

// Shared sortable-table-header infrastructure - used by the Trade Log and
// Strategy Decisions Advanced tables (ROADMAP.md Phase 0.5). state is a
// small {sortKey, sortDir} object owned by the calling render function;
// onclickCall is literal JS source (e.g. "toggleSort(tradeLogFilter,'ticker',renderTradeLogTable)")
// since this has to end up as an onclick="..." attribute, not a live reference.
function sortRows(rows, key, dir) {
  const sorted = rows.slice().sort((a, b) => {
    let av = a[key], bv = b[key];
    if (typeof av === 'string') { av = av.toLowerCase(); bv = (bv || '').toLowerCase(); }
    if (av == null) return 1;
    if (bv == null) return -1;
    if (av < bv) return -1;
    if (av > bv) return 1;
    return 0;
  });
  if (dir === 'desc') sorted.reverse();
  return sorted;
}
function toggleSort(state, key, rerenderFn) {
  if (state.sortKey === key) {
    state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
  } else {
    state.sortKey = key;
    state.sortDir = 'desc';
  }
  rerenderFn();
}
function sortHeaderHTML(state, key, label, onclickCall) {
  const active = state.sortKey === key;
  const arrow = active ? (state.sortDir === 'asc' ? ' ▲' : ' ▼') : '';
  return `<th class="sortable" onclick="${onclickCall}">${esc(label)}${arrow}</th>`;
}

// Trade.reason is a formatted string ("whale print 5230 @ 0.62 (conf 0.78)"),
// not a separate field - services/strategy_engine.py's exact format
// (verified directly, not guessed). Pulled out here so the Advanced trade
// table can sort/show it as its own numeric column instead of a text blob.
function parseConfidence(reason) {
  const m = /\(conf ([\d.]+)\)/.exec(reason || '');
  return m ? parseFloat(m[1]) : null;
}

// Previously guessed "this is a combo/parlay market" from a comma-split of
// the title text - a real, confirmed-live bug: an ORDINARY single-outcome
// market whose title just happens to contain a comma (e.g. a date, "before
// Aug 10, 2026?") was misdetected as a multi-leg combo and its whole title
// silently mangled into "2-leg parlay: ...". Fixed to use the real,
// authoritative signal instead - a ticker's `legs` field (from Kalshi's own
// Market.mve_selected_legs, see main.py) is only ever populated for a
// genuine MVE/combo market, never guessed from text. See comboLegsHTML for
// the real per-leg breakdown; this function no longer does any collapsing,
// just returns the title as-is.
function formatTitle(full) {
  return full;
}

// Real per-leg breakdown for a genuine combo/MVE market (Market.
// mve_selected_legs - {event_ticker, market_ticker, side} per leg, real
// Kalshi field). Each leg's own label/price resolves off the same
// marketTitles/latest global `prices` caches every other ticker reference
// already uses - eventually-consistent (falls back to the raw ticker until
// that leg's own market data has been fetched at least once), not a new
// per-leg API call. Returns '' when this ticker isn't a combo market at all
// (the common case) - never a guess.
function comboLegsHTML(ticker) {
  const info = marketTitles[ticker];
  const legs = info && info.legs;
  if (!legs || !legs.length) return '';
  // Was `state.latest_prices` - `state` was never a real variable anywhere
  // in this codebase (pre-existing bug, predates this session's JS split;
  // caught by eslint's no-undef during the ES-module conversion, not by
  // manual testing - this function only reaches this line for genuine
  // multi-leg combo markets, a narrow path). terminalLatestPrices is this
  // module's actual cache of the same data the comment above intends.
  const prices = terminalLatestPrices || {};
  const rows = legs.map(leg => {
    const legLabel = marketLabel(leg.market_ticker);
    const legPrice = prices[leg.market_ticker];
    const priceHtml = legPrice != null
      ? ` — ${(sideAdjustedPrice(leg.side, legPrice) * 100).toFixed(0)}¢`
      : '';
    return `<div class="combo-leg"><span class="side-tag ${esc(leg.side)}">${esc(leg.side)}</span> ${esc(legLabel.short)}${priceHtml}</div>`;
  }).join('');
  // Same stopPropagation() this app already needed once for the identical
  // shape of bug (a disclosure inside a clickable row also opening the
  // market-detail modal underneath it on click).
  return `<details class="combo-legs" onclick="event.stopPropagation()"><summary>${legs.length}-leg combo — click to see each selection</summary>${rows}</details>`;
}

// Whale signals/decisions only carry a raw ticker (e.g. "KXMVESPORTS...-FC34E0243A1")
// — market_titles (from Kalshi's own title/yes_sub_title field) is what makes that
// mean something to a person. Falls back to the ticker if a title isn't known yet.
// market_titles[ticker] is an object ({title, yes_sub_title, no_sub_title,
// event_ticker}), not a bare string - main.py used to collapse it to one
// string before sending it, which lost yes_sub_title/no_sub_title entirely
// (there was no way to say what a Yes vs. No position actually meant).
function marketLabel(ticker) {
  const info = marketTitles[ticker];
  const raw = (info && info.title) || ticker || 'unknown market';
  const formatted = formatTitle(raw);
  const short = formatted.length > 64 ? formatted.slice(0, 63) + '…' : formatted;
  return { short, full: raw };
}

// The watchlist's real "parent" unit as of the round_robin_select redesign
// (ROADMAP.md) - a whole tournament/series costs one watchlist slot, not
// each of its individual events/pairings. Mirrors
// services/signal_log.series_of's exact ticker-prefix definition (kept
// textually identical rather than a second one that could drift) so the UI
// groups things exactly the way the backend already selected them.
function seriesOf(ticker) {
  return ticker ? ticker.split('-')[0] : ticker;
}
function seriesLabel(seriesTicker) {
  const meta = seriesMeta[seriesTicker];
  return (meta && meta.title) ? meta.title : seriesTicker;
}

// A 2-sibling *mutually exclusive* event (event.mutually_exclusive, real
// Kalshi field - see main.py's _fetch_event_titles) is a real inversion
// pair, not two independent pieces of information - confirmed live, direct
// report: "they are just inversions of each other" ("Toronto vs
// Philadelphia Winner"'s two sibling markets' yes_bid prices sum to
// ~1.0). Drops the lower-probability half, keeping the higher one (its own
// Yes/No pair already implies the other side's odds by complement) -
// shared with eventGroupCardHTML's own version of this same rule for the
// Markets/Whale card view. Does NOT touch a genuine multi-outcome market
// (more than 2 siblings - each row is real, independent information) or
// sibling markets that merely share an event without being mutually
// exclusive at all (independent props - confirmed live, "Max Scherzer 15+
// outs" and "Aaron Nola 18+ outs" do NOT sum to 1.0). Order-preserving
// otherwise - only ever removes an item, never reorders the rest, so
// callers relying on markets' existing series/volume ordering
// (round_robin_select) keep working unchanged.
function dedupeInversionPairs(markets, prices) {
  const byEvent = new Map();
  markets.forEach(m => {
    if (!m.event_ticker) return;
    if (!byEvent.has(m.event_ticker)) byEvent.set(m.event_ticker, []);
    byEvent.get(m.event_ticker).push(m);
  });
  const drop = new Set();
  byEvent.forEach((group, eventTicker) => {
    const info = eventTitles[eventTicker];
    if (group.length === 2 && info && info.mutually_exclusive) {
      const lower = (prices[group[0].ticker] ?? -1) >= (prices[group[1].ticker] ?? -1) ? group[1] : group[0];
      drop.add(lower.ticker);
    }
  });
  return markets.filter(m => !drop.has(m.ticker));
}

// What sport/category this is, the actual matchup (from the event, not the
// often-messy per-market title), and what a Yes/No position on this specific
// market means in plain words - e.g. "San Diego wins" rather than just a
// "yes" tag. Every piece degrades gracefully: a market's event may not be
// cached yet, or may not have a category/sub_title at all.
function marketContext(ticker, side) {
  const info = marketTitles[ticker] || {};
  const event = info.event_ticker ? eventTitles[info.event_ticker] : null;
  // Real bug found live (2026-08-10, direct report: "Cleveland vs Detroit
  // Winner? YES but not the winner... semantically it doesnt even make
  // sense") - confirmed directly against Kalshi's real API (not a caching
  // bug in this app): for a simple 2-way matchup market, Kalshi's own
  // no_sub_title often comes back IDENTICAL to yes_sub_title (e.g. both
  // say "Baltimore" for "Baltimore vs Minnesota Winner?"). Showing that
  // duplicate value for the NO side would be actively wrong - it would
  // read as "NO also means Baltimore." When detected, falls back to an
  // honest "not {yes label}" instead of asserting a specific opposing
  // name Kalshi never actually gave this app.
  //
  // Second real Kalshi quirk found live (2026-08-24, direct report: "no
  // positions" section "very buggy" - meant side=no positions, not zero
  // positions): confirmed directly against /api/state's real market_titles
  // cache, not assumed - the entire KXBTC15M series (11/45 cached tickers
  // at the time of checking, every one a BTC15M ticker) returns a literal
  // "Target price: TBD" no_sub_title while yes_sub_title carries the real
  // number ("Target Price: $77,220.13"). Not a caching bug in this app,
  // and not the same shape as the duplicate-title case above (the two
  // values differ, so the `degenerate` check alone doesn't catch it) - but
  // just as uninformative/misleading to show a "no" holder verbatim, since
  // the real number is sitting right there in yes_sub_title. Same fallback
  // applies for the same reason: "not {yes label}" is always true and
  // informative, regardless of which specific placeholder string Kalshi
  // sends for a given series.
  const degenerate = info.no_sub_title && (
    info.no_sub_title === info.yes_sub_title || /\btbd\b/i.test(info.no_sub_title)
  );
  const positionMeans = side === 'no'
    ? (degenerate ? (info.yes_sub_title ? `not ${info.yes_sub_title}` : null) : info.no_sub_title)
    : info.yes_sub_title;
  return {
    category: event ? event.category : null,
    matchup: event ? (event.sub_title || event.title) : null,
    competition: event ? event.competition : null,
    competitionScope: event ? event.competition_scope : null,
    categoryTags: event ? (event.category_tags || []) : [],
    positionMeans,
  };
}

function eventLiveDataForTicker(ticker) {
  const info = marketTitles[ticker] || {};
  return info.event_ticker ? eventLiveData[info.event_ticker] : null;
}

function compactLiveValue(v) {
  if (typeof v === 'number') return Math.abs(v) >= 1000 ? v.toLocaleString() : String(Math.round(v * 100) / 100);
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  return String(v);
}

function formatTsMs(tsMs) {
  if (tsMs === undefined || tsMs === null || tsMs === '') return null;
  const n = Number(tsMs);
  if (!Number.isFinite(n)) return null;
  return new Date(n).toLocaleString();
}

function cryptoLiveSummary(details) {
  const coin = details.coin || 'crypto';
  const candles = details.candlesticks || {};
  const preferred = candles['1M'] || candles['5M'] || candles['15M'] || Object.values(candles)[0] || [];
  const last = preferred.length ? preferred[preferred.length - 1] : null;
  const lastClose = last && last.close != null ? Number(last.close) : null;
  const maturity = formatTsMs(details.maturity_ts_ms);
  const bits = [String(coin).toUpperCase()];
  if (lastClose != null) bits.push(`spot ${compactLiveValue(lastClose)}`);
  if (preferred.length) bits.push(`${preferred.length} candles`);
  if (maturity) bits.push(`matures ${maturity}`);
  return bits.join(' · ');
}

function sportsLiveSummary(details) {
  const parts = [];
  const home = details.home_team || details.home || details.team_a || details.team1;
  const away = details.away_team || details.away || details.team_b || details.team2;
  const homeScore = details.home_score ?? details.score_home ?? details.team_a_score ?? details.team1_score;
  const awayScore = details.away_score ?? details.score_away ?? details.team_b_score ?? details.team2_score;
  if (home || away) {
    const matchup = [home, away].filter(Boolean).join(' vs ');
    if (matchup) parts.push(matchup);
  }
  if (homeScore != null || awayScore != null) parts.push(`${homeScore ?? '—'}-${awayScore ?? '—'}`);
  if (details.period || details.inning || details.quarter || details.half) {
    parts.push(details.period || details.inning || details.quarter || details.half);
  }
  if (details.clock || details.time_remaining) parts.push(details.clock || details.time_remaining);
  if (details.status || details.widget_status) parts.push(details.status || details.widget_status);
  if (details.winner) parts.push(`winner ${details.winner}`);
  return parts.filter(Boolean).join(' · ');
}

function weatherLiveSummary(details) {
  const parts = [];
  if (details.location || details.city || details.station) parts.push(details.location || details.city || details.station);
  if (details.temperature != null) parts.push(`temp ${compactLiveValue(details.temperature)}`);
  if (details.observed_value != null) parts.push(`value ${compactLiveValue(details.observed_value)}`);
  if (details.humidity != null) parts.push(`humidity ${compactLiveValue(details.humidity)}`);
  if (details.wind_speed != null) parts.push(`wind ${compactLiveValue(details.wind_speed)}`);
  if (details.status || details.widget_status) parts.push(details.status || details.widget_status);
  return parts.filter(Boolean).join(' · ');
}

function genericLiveSummary(details) {
  const bits = [];
  const seen = new Set();
  ['widget_status', 'status', 'winner'].forEach(k => {
    if (details[k] !== undefined && details[k] !== null && details[k] !== '') {
      bits.push(`${k.replaceAll('_', ' ')} ${esc(compactLiveValue(details[k]))}`);
      seen.add(k);
    }
  });
  Object.entries(details).forEach(([k, v]) => {
    if (bits.length >= 4 || seen.has(k)) return;
    if (v === null || v === '' || Array.isArray(v) || typeof v === 'object') return;
    bits.push(`${k.replaceAll('_', ' ')} ${esc(compactLiveValue(v))}`);
  });
  return bits.join(' · ');
}

function eventLiveDataSummary(eventTicker) {
  const live = eventTicker ? eventLiveData[eventTicker] : null;
  if (!live) return null;
  const details = live.details || {};
  const type = String(live.type || 'live-data').toLowerCase();
  let summary = '';
  if (type.includes('crypto')) {
    summary = cryptoLiveSummary(details);
  } else if (type.includes('weather')) {
    summary = weatherLiveSummary(details);
  } else if (type.includes('sport') || details.home_score != null || details.away_score != null || details.winner) {
    summary = sportsLiveSummary(details);
  }
  if (!summary) summary = genericLiveSummary(details);
  return {
    type: live.type || 'live-data',
    summary,
    defaultRange: live.default_range || null,
  };
}

function eventLiveDataLineHTML(eventTicker, compact = false) {
  const summary = eventLiveDataSummary(eventTicker);
  if (!summary) return '';
  const range = summary.defaultRange ? ` · default ${esc(summary.defaultRange)}` : '';
  const line = `${summary.summary || 'Live event feed available'}${range}`;
  const separator = compact ? ' · ' : ' ';
  return `<div class="${compact ? 'context-line' : 'live-data-box'}"><span class="live-data-tag">${esc(summary.type)}</span>${separator}${line}</div>`;
}

function marketTaxonomyHTML(ticker) {
  const ctx = marketContext(ticker, null);
  const bits = [];
  if (ctx.competitionScope) bits.push(`<span class="scope-tag">${esc(ctx.competitionScope)}</span>`);
  if (ctx.competition) bits.push(esc(ctx.competition));
  const tags = (ctx.categoryTags || []).slice(0, 3).map(tag => `<span class="meta-chip">${esc(tag)}</span>`).join('');
  if (!bits.length && !tags) return '';
  return `<div class="context-line">${bits.join(' · ')}${tags ? (bits.length ? ' · ' : '') + tags : ''}</div>`;
}

function uniqueSorted(items) {
  return Array.from(new Set(items.filter(Boolean))).sort((a, b) => a.localeCompare(b));
}

function applyMarketPanelFilters(panelKey, markets) {
  const f = marketPanelFilters[panelKey];
  return (markets || []).filter(m => {
    const ctx = marketContext(m.ticker, null);
    if (f.category && ctx.category !== f.category) return false;
    if (f.scope && ctx.competitionScope !== f.scope) return false;
    if (f.tag && !(ctx.categoryTags || []).includes(f.tag)) return false;
    return true;
  });
}

function renderMarketCategorySuggestions() {
  $('market-category-suggestions').innerHTML = uniqueSorted(Object.keys(categoryMetadata.tags_by_categories || {}))
    .map(c => `<option value="${esc(c)}"></option>`).join('');
}

function renderMarketPanelFilters(panelKey, markets, shownCount) {
  const targetId = panelKey === 'markets' ? 'markets-watchlist-filter' : 'whale-watch-filter';
  const el = $(targetId);
  const f = marketPanelFilters[panelKey];
  const categories = uniqueSorted((markets || []).map(m => marketContext(m.ticker, null).category));
  const scopes = uniqueSorted((markets || []).map(m => marketContext(m.ticker, null).competitionScope));
  const tags = uniqueSorted((markets || []).flatMap(m => marketContext(m.ticker, null).categoryTags || []));
  el.innerHTML = `<div class="table-filter-row" style="margin:0 0 12px;">
    <select onchange="marketPanelFilters['${panelKey}'].category = this.value; rerenderMarketPanel('${panelKey}')">
      <option value="">All categories</option>
      ${categories.map(c => `<option value="${esc(c)}" ${f.category === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}
    </select>
    <select onchange="marketPanelFilters['${panelKey}'].scope = this.value; rerenderMarketPanel('${panelKey}')">
      <option value="">All scopes</option>
      ${scopes.map(s => `<option value="${esc(s)}" ${f.scope === s ? 'selected' : ''}>${esc(s)}</option>`).join('')}
    </select>
    <select onchange="marketPanelFilters['${panelKey}'].tag = this.value; rerenderMarketPanel('${panelKey}')">
      <option value="">All tags</option>
      ${tags.map(t => `<option value="${esc(t)}" ${f.tag === t ? 'selected' : ''}>${esc(t)}</option>`).join('')}
    </select>
    <span style="margin-left:auto;">Showing ${shownCount} of ${(markets || []).length}</span>
  </div>`;
}

function rerenderMarketPanel(panelKey) {
  const st = marketPanelState[panelKey];
  const filtered = applyMarketPanelFilters(panelKey, st.markets);
  renderMarketPanelFilters(panelKey, st.markets, filtered.length);
  if (panelKey === 'markets') {
    if (isAdvanced('markets')) {
      renderScreenerTable('markets', 'markets-cards', 'markets-card-count', filtered, st.prices, false, st.signals || []);
    } else {
      renderMarketCards('markets-cards', 'markets-card-count', filtered, st.prices, false, st.signals || [], st.seriesTrackRecord || {});
    }
    return;
  }
  if (isAdvanced('whale-cards')) {
    renderScreenerTable('whale-cards', 'whale-cards', null, filtered, st.prices, true, st.signals || []);
  } else {
    renderMarketCards('whale-cards', null, filtered, st.prices, true, st.signals || [], st.seriesTrackRecord || {});
  }
}

// "Live" the way Kalshi's own UI shows it - the real signal, not a
// timestamp guess. Backed by Kalshi's actual milestone/live-data system
// (main.py's _fetch_live_status): confirmed directly against a real AFL
// match at its actual live start time that widget_status goes
// "none" -> "live" -> "finished", not assumed from field names. Only
// populated for markets whose occurrence_datetime was recently plausible
// (see _fetch_live_status's window) - most won't have an entry at all,
// which correctly means "not live," not "unknown." Declared in
// polling-and-websocket.js (the only file that ever reassigns it, from
// refresh()'s polled state) and imported here for reading.
function isLive(m) {
  return m.event_ticker ? liveStatus[m.event_ticker] === 'live' : false;
}

function liveBadgeHTML(m) {
  return isLive(m) ? '<span class="live-badge">● LIVE</span>' : '';
}

// Price-change indicators (ROADMAP.md Phase 0.5). Originally compared
// against just the previous poll and only showed up for the one tick a
// price actually moved on, then vanished - direct correction: "they
// shouldn't be transient... they should stay visible and update as they
// change." Redesigned around a persistent per-ticker baseline (the first
// price seen for that ticker this browser session) instead of the last
// poll's price - the badge is visible continuously once a baseline
// exists, and its number updates in place every tick rather than flashing
// once and disappearing.
let baselinePrices = {};

function priceChangeHTML(ticker, currentPrice) {
  if (currentPrice === undefined || currentPrice === null) return '';
  if (!(ticker in baselinePrices)) {
    baselinePrices[ticker] = currentPrice;
    return ''; // nothing to compare against on the very first sighting
  }
  const base = baselinePrices[ticker];
  const deltaCents = Math.round((currentPrice - base) * 100);
  if (deltaCents === 0) return '';
  const up = deltaCents > 0;
  const pct = base > 0 ? ((currentPrice - base) / base) * 100 : 0;
  const arrow = up ? '▲' : '▼';
  return `<span class="price-flash ${up ? 'up' : 'down'}" title="${up ? '+' : ''}${deltaCents}¢ (${up ? '+' : ''}${pct.toFixed(1)}%) since first seen this session">${arrow} ${up ? '+' : ''}${deltaCents}¢</span>`;
}

function contextLineHTML(ticker, side) {
  const ctx = marketContext(ticker, side);
  const bits = [];
  if (ctx.category) bits.push(`<span class="cat-tag">${esc(ctx.category)}</span>`);
  if (ctx.competitionScope) bits.push(`<span class="scope-tag">${esc(ctx.competitionScope)}</span>`);
  if (ctx.matchup) bits.push(esc(ctx.matchup));
  if (ctx.positionMeans) bits.push(`<span class="position-means">Betting: ${esc(ctx.positionMeans)}</span>`);
  return bits.length ? `<div class="context-line">${bits.join(' · ')}</div>` : '';
}

function marketRowHTML(m, prices) {
  const label = marketLabel(m.ticker);
  const price = prices[m.ticker];
  const vol = m.volume_24h_fp;
  return `
    <div class="market-row" title="${esc(label.full)}\n${esc(m.ticker)}" onclick="openMarketDetail('${esc(m.ticker)}', '${esc(m.event_ticker || '')}')" style="cursor:pointer;">
      <span class="ticker">${esc(label.short)}</span>
      <span class="price">YES ${price !== undefined ? (price * 100).toFixed(0) : '—'}¢ ${priceChangeHTML(m.ticker, price)} · vol ${vol !== undefined ? Math.round(vol).toLocaleString() : '—'}</span>
    </div>
  `;
}

function renderMarkets(markets, prices) {
  const el = $('markets-list');
  // Badge stays the real total (matches state.markets.length/watchlist_size
  // exactly) even though dedupeInversionPairs below may render fewer rows -
  // same "honest counts, simplified display" split eventGroupCardHTML uses.
  $('market-count').textContent = markets.length ? `(${markets.length})` : '';
  if (!markets.length) { el.innerHTML = '<div class="empty">No markets loaded</div>'; return; }

  const visibleTickers = new Set(dedupeInversionPairs(markets, prices).map(m => m.ticker));

  // markets arrives already grouped consecutively by parent series (see
  // KalshiClient.round_robin_select - every market under a selected series
  // is appended together before the next series starts), so a "series
  // changed since the last row" check reproduces that grouping without
  // re-sorting or re-bucketing anything client-side. A header only shows
  // for a series with more than one market - a lone market renders exactly
  // as a plain row, same as before this grouping existed. Header counts
  // stay the real per-series total, same reasoning as the badge above -
  // only which rows actually render gets deduped.
  let html = '';
  let currentSeries = null;
  let group = [];
  const flush = () => {
    if (!group.length) return;
    if (group.length > 1) {
      const vol = group.reduce((sum, m) => sum + (parseFloat(m.volume_24h_fp) || 0), 0);
      html += `<div class="series-header"><span>${esc(seriesLabel(currentSeries))}</span><span class="count">${group.length} markets · vol ${Math.round(vol).toLocaleString()}</span></div>`;
    }
    html += group.filter(m => visibleTickers.has(m.ticker)).map(m => marketRowHTML(m, prices)).join('');
  };
  markets.forEach(m => {
    const s = seriesOf(m.ticker);
    if (s !== currentSeries) {
      flush();
      currentSeries = s;
      group = [];
    }
    group.push(m);
  });
  flush();
  el.innerHTML = html;
}

// Smooth feed updates, direct request ("flicker on every update rather than
// appending/updating rows smoothly") - a full innerHTML replace on every 5s
// poll tore down and rebuilt every card even when nothing about it changed,
// which both flashes visibly and resets scrollTop to 0, making a list that
// actually is scrollable feel broken (any scroll position gets wiped out
// within 5 seconds). New/decided/resolved signals and decisions are only
// ever prepended server-side (main.py inserts at index 0) and only ever
// fall off the tail once the cap is hit - so the common case is "the same
// items as last time, plus some new ones in front." When that invariant
// holds, only the new items' HTML gets inserted; every existing DOM node
// for an unchanged row is left completely alone (no flash, scroll position
// naturally preserved). Falls back to a full rebuild whenever it doesn't
// hold - a filter/sort/grouping change legitimately changes which items
// are visible or their order, and forcing a "smooth" diff onto that would
// just be wrong, not smoother.

export { $, _setAccountMode, accountMode, advToggleHTML, applyMarketPanelFilters, baselinePrices, comboLegsHTML, compactLiveValue, contextLineHTML, costHTML, cryptoLiveSummary, dedupeInversionPairs, esc, eventLiveData, eventLiveDataForTicker, eventLiveDataLineHTML, eventLiveDataSummary, eventTitles, fetchJSON, fmt, formatConfigValue, formatTitle, formatTsMs, genericLiveSummary, isAdvanced, isLive, liveBadgeHTML, marketContext, marketLabel, marketPanelFilters, marketPanelState, marketRowHTML, marketTaxonomyHTML, marketTitles, parseConfidence, payoutHTML, priceChangeHTML, renderMarketCategorySuggestions, renderMarketPanelFilters, renderMarkets, rerenderMarketPanel, seriesLabel, seriesOf, sideAdjustedPrice, sortHeaderHTML, sortRows, sportsLiveSummary, toggleAdvanced, toggleSort, uniqueSorted, weatherLiveSummary };

// Exposed for inline HTML event handlers (onclick=/onchange=/oninput=,
// including ones built indirectly via a caller-supplied onclick-string
// parameter - see shared-utils.js's header). Every top-level function in
// this file is exposed (cheap, harmless if unused); state objects only the
// specific ones confirmed to be read/mutated directly from a handler.
window._setAccountMode = _setAccountMode;
window.advToggleHTML = advToggleHTML;
window.applyMarketPanelFilters = applyMarketPanelFilters;
window.comboLegsHTML = comboLegsHTML;
window.compactLiveValue = compactLiveValue;
window.contextLineHTML = contextLineHTML;
window.costHTML = costHTML;
window.cryptoLiveSummary = cryptoLiveSummary;
window.dedupeInversionPairs = dedupeInversionPairs;
window.esc = esc;
window.eventLiveDataForTicker = eventLiveDataForTicker;
window.eventLiveDataLineHTML = eventLiveDataLineHTML;
window.eventLiveDataSummary = eventLiveDataSummary;
window.fetchJSON = fetchJSON;
window.formatConfigValue = formatConfigValue;
window.formatTitle = formatTitle;
window.formatTsMs = formatTsMs;
window.genericLiveSummary = genericLiveSummary;
window.isAdvanced = isAdvanced;
window.isLive = isLive;
window.liveBadgeHTML = liveBadgeHTML;
window.marketContext = marketContext;
window.marketLabel = marketLabel;
window.marketRowHTML = marketRowHTML;
window.marketTaxonomyHTML = marketTaxonomyHTML;
window.parseConfidence = parseConfidence;
window.payoutHTML = payoutHTML;
window.priceChangeHTML = priceChangeHTML;
window.renderMarketCategorySuggestions = renderMarketCategorySuggestions;
window.renderMarketPanelFilters = renderMarketPanelFilters;
window.renderMarkets = renderMarkets;
window.rerenderMarketPanel = rerenderMarketPanel;
window.seriesLabel = seriesLabel;
window.seriesOf = seriesOf;
window.sideAdjustedPrice = sideAdjustedPrice;
window.sortHeaderHTML = sortHeaderHTML;
window.sortRows = sortRows;
window.sportsLiveSummary = sportsLiveSummary;
window.toggleAdvanced = toggleAdvanced;
window.toggleSort = toggleSort;
window.uniqueSorted = uniqueSorted;
window.weatherLiveSummary = weatherLiveSummary;
window.marketPanelFilters = marketPanelFilters;
