import { HISTORY_CLOSE_TYPE_LABELS, historyDurationHTML, historyFilter, historyTotal, historyTrades } from './history-core.js';
import { seriesMeta } from './polling-and-websocket.js';
import { $, comboLegsHTML, esc, eventLiveDataLineHTML, eventTitles, fmt, liveBadgeHTML, marketContext, marketLabel, marketTaxonomyHTML, marketTitles, priceChangeHTML, seriesLabel, seriesOf, uniqueSorted } from './shared-utils.js';

// Part of index.html's JS split - see shared-utils.js's header for the
// load-order/shared-global-scope rationale common to all these files.
// This file: equity/P&L chart rendering, the divergence/whale-lean/
// win-rate helpers, and the Markets-tab market/event card renderers
// (marketCardHTML, eventGroupCardHTML, renderMarketCards).
function renderHistoryTrades() {
  const el = $('history-trades');
  $('history-trades-count').textContent = historyTotal ? `(${historyTotal})` : '';
  if (!historyTrades.length) {
    el.innerHTML = '<div class="empty">No closed trades yet — once a position is closed (take-profit, stop-loss, sentiment reversal, auto-exit, or settlement) it will show up here.</div>';
    return;
  }
  const rows = historyTrades.map(t => {
    const label = marketLabel(t.ticker);
    const pnl = t.realized_pnl;
    const pnlColor = pnl > 0 ? 'var(--yes)' : (pnl < 0 ? 'var(--no)' : 'var(--text)');
    const closeTypeLabel = HISTORY_CLOSE_TYPE_LABELS[t.close_type] || t.close_type || 'unknown';
    const conf = t.entry_confidence !== null && t.entry_confidence !== undefined ? `${(t.entry_confidence * 100).toFixed(0)}%` : '—';
    const title = `Entry: ${t.entry_reason || 'unknown'}\nExit: ${t.exit_reason} — click to view full market detail`;
    const et = (marketTitles[t.ticker] && marketTitles[t.ticker].event_ticker) || '';
    // Direct request: closed positions in History should open to full
    // market detail too, matching the same click-through every open
    // position/market card already has - entry/exit price and close
    // type/P&L (the "did this win or lose" answer) are already shown
    // inline in this row; the modal adds the market's fuller context.
    // Smaller second line under the title (2026-08-10 report: rows going
    // extremely wide, plus "YES"/"NO" alone not saying who/what it means).
    const ctx = marketContext(t.ticker, t.side);
    const subLine = ctx.positionMeans
      ? `<div style="font-size:10px; color:var(--muted); margin-top:2px;">Betting: ${esc(ctx.positionMeans)}</div>` : '';
    // Side-aware unit cost, not raw yes-price (2026-08-17 direct report:
    // "wins are showing up as losses (0c exit when the result is 100c)").
    // entry_price/exit_price are always stored in the yes-price convention
    // (WhaleSignal.price's own documented meaning), so a "no" position that
    // WINS settles with yes-price at 0.0 - correct data, but this column is
    // labeled "Exit" and a viewer reads "0¢" as a total wipeout, not as
    // "the yes side went to zero, which is exactly what a no-side win looks
    // like." Confirmed against real trade history: KXATPMATCH-26AUG17FERDE
    // is a no-side win, +$96.92, that rendered as "22¢ -> 0¢" here while
    // cost_basis/cash_back a few columns over already showed the correct
    // $363.48 -> $466.00 - same underlying data, displayed two different
    // ways, only one of them right. Same bug class CLAUDE.md's "a displayed
    // value must match its label" section already documents once.
    const entryUnitCost = t.entry_price !== null && t.entry_price !== undefined
      ? (t.side === 'yes' ? t.entry_price : 1 - t.entry_price) : null;
    const exitUnitCost = t.side === 'yes' ? t.exit_price : 1 - t.exit_price;
    return `<tr title="${esc(title)}" style="cursor:pointer;" onclick="openMarketDetail('${esc(t.ticker)}', '${esc(et)}')">
      <td>${esc(label.short)}${subLine}</td>
      <td><span class="side-tag ${t.side}">${esc(t.side)}</span></td>
      <td>${t.size.toLocaleString()}</td>
      <td>${entryUnitCost !== null ? (entryUnitCost * 100).toFixed(0) + '¢' : '—'}</td>
      <td>${(exitUnitCost * 100).toFixed(0)}¢</td>
      <td>${t.cost_basis !== null && t.cost_basis !== undefined ? fmt(t.cost_basis) : '—'}</td>
      <td>${fmt(t.cash_back ?? 0)}</td>
      <td style="color:var(--muted);">${fmt(t.fees_paid ?? 0)}</td>
      <td>${esc(closeTypeLabel)}</td>
      <td>${historyDurationHTML(t.hold_sec)}</td>
      <td>${conf}</td>
      <td style="color:${pnlColor}">${pnl !== null ? fmt(pnl) : '—'}</td>
      <td>${t.left_on_table ? fmt(t.left_on_table) : '—'}</td>
    </tr>`;
  }).join('');

  const totalPages = Math.max(1, Math.ceil(historyTotal / historyFilter.limit));
  const currentPage = Math.floor(historyFilter.offset / historyFilter.limit) + 1;
  const pager = `<div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px; font-size:11px; color:var(--muted);">
    <span>Page ${currentPage} of ${totalPages}</span>
    <span>
      <button ${historyFilter.offset <= 0 ? 'disabled' : ''}
        onclick="historyFilter.offset = Math.max(0, historyFilter.offset - historyFilter.limit); loadTradingHistory();">← Prev</button>
      <button ${currentPage >= totalPages ? 'disabled' : ''}
        onclick="historyFilter.offset += historyFilter.limit; loadTradingHistory();">Next →</button>
    </span>
  </div>`;

  el.innerHTML = `<table class="positions-table sortable">
    <thead><tr>
      <th>Market</th><th>Side</th><th>Size</th><th>Entry</th><th>Exit</th>
      <th title="Contracts x entry price, before fees (see Fees column) - the raw cost basis">Cost</th><th title="Contracts x exit price, before fees (see Fees column) - the raw cash back">Payout</th>
      <th title="Real Kalshi taker fees, both legs combined - already netted into Realized P&amp;L, shown separately here so the fee drag is visible on its own">Fees</th>
      <th>Closed By</th><th>Held</th><th title="Whale confidence at entry">Entry Conf.</th>
      <th>Realized P&amp;L</th><th title="Hypothetical: size*(1-exit price) for yes / size*exit price for no - what a full $1 win would have paid beyond what this early exit actually banked">Left on Table</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>` + pager;
}

// Traditional trading-view element: bankroll trajectory over time, not just
// the current number. Plain inline SVG, no charting library.
// Shared by both the paper equity curve and the real-balance curve — valueKey
// picks which field each point uses ("equity" vs "balance"), baselineLabel
// just changes the caption underneath.
function renderEquityChart(history, baseline, valueKey, baselineLabel, elId, opts) {
  // opts (2026-08-10, direct request: "useful graph views") - optional so
  // every existing call site (Portfolio/Market-Native equity charts) is
  // unaffected: {emptyMessage, valueFormatter}. valueFormatter defaults to
  // fmt() (dollar formatting) since that's what every pre-existing caller
  // actually wants; the new History-tab charts below pass a percent
  // formatter instead - this is a threshold-sweep/win-rate curve, not a
  // dollar figure, and fmt() would render it as literal, nonsensical cents.
  opts = opts || {};
  const valueFormatter = opts.valueFormatter || fmt;
  valueKey = valueKey || 'equity';
  baselineLabel = baselineLabel || 'starting bankroll';
  const el = $(elId || 'equity-chart');
  if (!history || history.length < 2 || baseline === undefined || baseline === null) {
    el.innerHTML = `<div class="empty">${esc(opts.emptyMessage || 'Not enough history yet — check back in a minute')}</div>`;
    return;
  }
  const w = 900, h = 180, pad = 10;
  const values = history.map(p => p[valueKey]);
  const min = Math.min(...values, baseline);
  const max = Math.max(...values, baseline);
  const range = (max - min) || 1;
  const xStep = (w - pad * 2) / (history.length - 1);
  const points = history.map((p, i) => {
    const x = pad + i * xStep;
    const y = pad + (1 - (p[valueKey] - min) / range) * (h - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const baseY = (pad + (1 - (baseline - min) / range) * (h - pad * 2)).toFixed(1);
  const last = values[values.length - 1];
  const color = last >= baseline ? 'var(--yes)' : 'var(--no)';
  el.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" style="width:100%; height:180px; display:block;">
      <line x1="${pad}" y1="${baseY}" x2="${w - pad}" y2="${baseY}" style="stroke:var(--panel-border); stroke-dasharray:4,3;" />
      <polyline points="${points}" style="fill:none; stroke:${color}; stroke-width:2;" />
    </svg>
    <div style="font-family:var(--mono); font-size:10px; color:var(--muted); display:flex; justify-content:space-between;">
      <span>${baselineLabel} ${valueFormatter(baseline)}</span>
      <span>${history.length} samples</span>
    </div>
  `;
}

// Aggregates recent whale prints on one ticker into a single lean — this is
// what the Whale Watch view foregrounds next to each market's plain price.
// Weighted by size * confidence, not size alone - mirrors services/
// strategy_engine.py's _whale_lean() exactly (same inputs, same math, kept
// in one place conceptually even though it has to live in two languages).
// A low-confidence print counts as weaker evidence of real sentiment than
// a high-confidence one, same distinction the entry side already makes.
// Per-signal divergence, in probability points: how far this one whale
// print's implied YES probability (signal.price - "always the yes-side
// price by convention, side carries direction separately", see
// services/whalewatchers/kalshi_trade_tape.py) sits from where the market
// trades right now. Same math/threshold as marketCardHTML's own
// divergenceLine (ROADMAP.md: "Betting is N pts more bullish/bearish than
// the market implies" is already the validated framing) - this is that
// same idea at individual-signal granularity instead of aggregated-lean
// granularity, so the signal/decision feed can sort by it directly. Null
// (not 0) when there's no signal or no live price to compare against - a
// close/skip-only decision row, or a ticker with no current price yet -
// so callers can tell "no data" apart from "genuinely zero divergence."
function divergencePts(d, latestPrices) {
  const price = d.signal && d.signal.price;
  const livePrice = latestPrices && latestPrices[d.ticker];
  if (price == null || livePrice == null) return null;
  return Math.abs(Math.round(price * 100) - Math.round(livePrice * 100));
}

function computeWhaleLean(ticker, signals) {
  const matches = signals.filter(s => s.ticker === ticker);
  if (!matches.length) return null;
  let yesWeight = 0, noWeight = 0;
  matches.forEach(s => {
    const w = s.size * s.confidence;
    if (s.side === 'yes') yesWeight += w; else noWeight += w;
  });
  const total = yesWeight + noWeight;
  return { count: matches.length, yesSize: yesWeight, noSize: noWeight, total, yesPct: total ? (yesWeight / total * 100) : 50 };
}

// "This type of market" win rate — grouped server-side by Kalshi's series/
// ticker prefix (e.g. every Oscar Best Picture market), not the one specific
// instance, which usually has too few resolved signals to mean anything.
function winRateBlockHTML(ticker, seriesTrackRecord) {
  const rec = (seriesTrackRecord || {})[ticker];
  if (!rec || !rec.resolved) {
    return `<div class="winrate-block">
      <div class="winrate-label">🐋 Whale win rate for this type of market (30d)</div>
      <div class="sub">Not enough resolved signals yet to calculate this.</div>
    </div>`;
  }
  const wr = rec.win_rate;
  const color = wr >= 50 ? 'var(--yes)' : 'var(--no)';
  return `<div class="winrate-block">
    <div class="winrate-label">🐋 Whale win rate for this type of market (30d)</div>
    <div class="whale-bar"><div class="yes-seg" style="width:${wr}%; background:${color};"></div><div class="no-seg" style="width:${100 - wr}%"></div></div>
    <div class="sub">${wr.toFixed(0)}% correct · ${rec.resolved} resolved signal${rec.resolved === 1 ? '' : 's'} on ${seriesLabelHTML(rec.series)}</div>
  </div>`;
}

// Real Kalshi series metadata (ROADMAP.md P2 - "not a real category
// taxonomy" until now) instead of a raw ticker prefix like "KXITFWMATCH" -
// falls back to the raw ticker, quoted, exactly like before, whenever this
// series isn't in seriesMeta yet (not every series a whale has printed on
// is necessarily still in the current watchlist's scoped series_meta).
function seriesLabelHTML(seriesTicker) {
  const meta = seriesMeta[seriesTicker];
  if (!meta || !meta.title) return `"${esc(seriesTicker)}"-type markets`;
  const tag = meta.tags && meta.tags.length ? ` (${esc(meta.tags[0])})` : '';
  return `<span title="${esc(seriesTicker)}">${esc(meta.title)}${tag}</span>-type markets`;
}

function marketCardHTML(m, prices, includeWhale, signals, seriesTrackRecord) {
  const label = marketLabel(m.ticker);
  const yesBid = prices[m.ticker];
  const yesPrice = yesBid !== undefined ? Math.round(yesBid * 100) : null;
  const noPrice = yesPrice !== null ? 100 - yesPrice : null;
  const vol = m.volume_24h_fp;

  let whaleHtml = '';
  if (includeWhale) {
    const lean = computeWhaleLean(m.ticker, signals);
    if (!lean) {
      whaleHtml = `<div class="whale-lean"><div class="headline none">🐋 No whale prints seen yet on this market</div></div>`;
    } else {
      const dir = lean.yesPct >= 50 ? 'yes' : 'no';
      const pct = dir === 'yes' ? lean.yesPct : 100 - lean.yesPct;
      // How the whales' own betting split compares to what the market's price
      // already implies — a market can be "60% likely" while whales are piling
      // in 90% one-sided, which is the interesting divergence to surface.
      let divergenceLine = '';
      if (yesPrice !== null) {
        const diff = Math.round(lean.yesPct - yesPrice);
        divergenceLine = Math.abs(diff) >= 5
          ? `<div class="sub">Betting is <b>${Math.abs(diff)} pts</b> more ${diff > 0 ? 'bullish (YES)' : 'bearish (NO)'} than the market price implies.</div>`
          : `<div class="sub">Betting roughly matches the market price.</div>`;
      }
      whaleHtml = `<div class="whale-lean">
        <div class="headline ${dir}">🐋 Whales leaning ${dir.toUpperCase()} — ${pct.toFixed(0)}%</div>
        <div class="whale-bar"><div class="yes-seg" style="width:${lean.yesPct}%"></div><div class="no-seg" style="width:${100 - lean.yesPct}%"></div></div>
        <div class="sub">${lean.count} print${lean.count === 1 ? '' : 's'} seen · ${lean.total.toLocaleString()} ct total</div>
        ${divergenceLine}
      </div>
      ${winRateBlockHTML(m.ticker, seriesTrackRecord)}`;
    }
  }

  // Category/matchup context, same lookup the position/trade rows use - a
  // real "what sport, who vs who" answer instead of a raw ticker fragment.
  // Falls back to the ticker prefix only if the event hasn't been fetched
  // yet (or has no category), never leaving the line blank with no context
  // at all.
  const ctx = marketContext(m.ticker, null);
  const metaRight = ctx.category ? esc(ctx.category) : esc(m.ticker.split('-')[0]);
  const matchupHtml = ctx.matchup ? `<div class="context-line" style="margin:0 0 6px;">${esc(ctx.matchup)}</div>` : '';
  const taxonomyHtml = marketTaxonomyHTML(m.ticker);
  const liveDataHtml = eventLiveDataLineHTML(m.event_ticker || (marketTitles[m.ticker] || {}).event_ticker, true);
  // yes_sub_title/no_sub_title say what each side actually MEANS for a
  // multi-child-market question (e.g. "Golf / Golfer / Golfing" for one
  // "what will they say" child, or "Over 8.5" for one Total Runs
  // threshold) - previously fetched into ctx here and then only
  // ctx.category/ctx.matchup were ever read, discarding exactly the piece
  // that distinguishes this specific child from its siblings (direct
  // report: markets with several child markets showed only the generic
  // series/event title, losing which child a price actually referred to).
  // Only shown when it says something the main title doesn't already say.
  const info = marketTitles[m.ticker] || {};
  const specialFlagHtml = (info.mutually_exclusive || info.collateral_return_type || (m.can_close_early)) ?
    `<div style="margin-top:6px; font-size:11px; color:var(--muted);">` +
      `${info.mutually_exclusive ? '<span title="This event is mutually exclusive">🔁 MEC</span> ' : ''}` +
      `${info.collateral_return_type ? `<span title="Collateral return type: ${esc(info.collateral_return_type)}">⚖️ ${esc(info.collateral_return_type)}</span> ` : ''}` +
      `${m.can_close_early ? '<span title="May close early based on event milestone">⏱️ Early-close</span>' : ''}` +
    `</div>` : '';
  const yesSub = info.yes_sub_title && info.yes_sub_title !== label.full ? esc(info.yes_sub_title) : null;
  // Real bug found live (2026-08-10): Kalshi's own API often returns an
  // IDENTICAL no_sub_title/yes_sub_title for a simple 2-way matchup market
  // (see marketContext()'s own fuller explanation) - falls back to "not
  // {yes label}" in that case rather than just going blank, same honest
  // disambiguation marketContext() now provides everywhere else.
  const degenerate = info.no_sub_title && info.no_sub_title === info.yes_sub_title;
  const noSub = degenerate
    ? (yesSub ? `not ${yesSub}` : null)
    : (info.no_sub_title && info.no_sub_title !== label.full ? esc(info.no_sub_title) : null);

  return `
  <div class="market-card" title="${esc(label.full)}" onclick="openMarketDetail('${esc(m.ticker)}', '${esc(m.event_ticker || '')}')" style="cursor:pointer;">
    <div class="title">${esc(label.short)} ${liveBadgeHTML(m)}</div>
    ${matchupHtml}
    ${taxonomyHtml}
    ${liveDataHtml}
    ${comboLegsHTML(m.ticker)}
    <div class="prices">
      <div class="price-btn yes"><div class="lbl">Yes${yesSub ? ` <span style="font-weight:400; opacity:0.8;">(${yesSub})</span>` : ''}</div><div class="val">${yesPrice !== null ? yesPrice + '¢' : '—'}</div>${priceChangeHTML(m.ticker, yesBid)}</div>
      <div class="price-btn no"><div class="lbl">No${noSub ? ` <span style="font-weight:400; opacity:0.8;">(${noSub})</span>` : ''}</div><div class="val">${noPrice !== null ? noPrice + '¢' : '—'}</div></div>
    </div>
    <div class="prob-bar"><div class="fill" style="width:${yesPrice ?? 50}%"></div></div>
    <div class="meta"><span>vol ${vol !== undefined ? Math.round(vol).toLocaleString() : '—'}</span><span>${metaRight}</span></div>
    ${whaleHtml}
    ${specialFlagHtml}
  </div>`;
}

// Markets sharing an event_ticker are outcomes of the same underlying
// question (confirmed on real data - see ROADMAP.md Phase 0.5), not
// unrelated markets that happen to be in the same watchlist. Groups of 1
// render exactly as before (a standalone card); groups of >1 get wrapped
// under one event header instead of appearing as N disconnected cards.
// One outcome row per sibling market - name, Yes/No price, click to open the
// drill-down for that specific outcome. Matches real Kalshi's own pattern
// for a multi-outcome event (confirmed directly against real Kalshi
// screenshots), not designed from guesswork: one card, N compact rows,
// rather than N full cards awkwardly grouped under one header (an earlier
// version of this did that; a real, well-founded complaint that the
// group-of-full-cards version still showed "a clear lack of data for any
// market in particular" and a raw ticker feel is what prompted this).
//
// A 2-sibling *mutually exclusive* event (event.mutually_exclusive, real
// Kalshi field - see main.py's _fetch_event_titles) is a real inversion
// pair, not two independent pieces of information - "Toronto vs
// Philadelphia Winner"'s two sibling markets' yes_bid prices sum to ~1.0,
// confirmed live, direct report: "they are just inversions of each
// other." Only the higher-probability side's row is shown (already sorted
// first below) - its own Yes/No pair already implies the other side's
// odds by complement, so nothing is lost by not repeating it a second
// time from the opposite angle. This does NOT apply to a genuine
// multi-outcome market (e.g. "Wyndham Championship Winner", 60+ golfers,
// also mutually_exclusive but with no simple pairwise complement - each
// additional row is real, independent information) or to sibling markets
// that merely share an event without being mutually exclusive at all
// (e.g. two different players' independent prop bets - confirmed live,
// "Max Scherzer 15+ outs" and "Aaron Nola 18+ outs" do NOT sum to 1.0).
function eventGroupCardHTML(group, eventInfo, prices) {
  const title = eventInfo ? eventInfo.title : (group[0].event_ticker || 'Related markets');
  const category = eventInfo ? eventInfo.category : null;
  const totalVol = group.reduce((sum, m) => sum + (parseFloat(m.volume_24h_fp) || 0), 0);
  const isInversionPair = group.length === 2 && !!(eventInfo && eventInfo.mutually_exclusive);

  let sortedGroup = group.slice()
    .sort((a, b) => (prices[b.ticker] ?? -1) - (prices[a.ticker] ?? -1));  // most-likely outcome first
  if (isInversionPair) sortedGroup = sortedGroup.slice(0, 1);

  const rows = sortedGroup.map(m => {
      const label = marketLabel(m.ticker);
      const info = marketTitles[m.ticker];
      const outcomeName = (info && info.yes_sub_title) || label.short;
      const yesBid = prices[m.ticker];
      const yesPrice = yesBid !== undefined ? Math.round(yesBid * 100) : null;
      const noPrice = yesPrice !== null ? 100 - yesPrice : null;
      return `<div class="outcome-row" onclick="openMarketDetail('${esc(m.ticker)}', '${esc(m.event_ticker || '')}')" title="${esc(label.full)}">
        <span class="outcome-name">${esc(outcomeName)}</span>
        <span class="outcome-prices">
          <span class="mini-price yes">${yesPrice !== null ? yesPrice + '¢' : '—'}</span>
          <span class="mini-price no">${noPrice !== null ? noPrice + '¢' : '—'}</span>
        </span>
      </div>`;
    }).join('');

  // Live status is per-event, not per-outcome (every sibling here is the
  // same underlying game/match) - shown once on the card header, matching
  // where real Kalshi shows it.
  //
  // competition (e.g. "Wyndham Championship") - direct data-usage review
  // finding: real, already-fetched field (get_event's product_metadata),
  // never displayed anywhere. Shown only when it adds real information
  // beyond the event title itself (a golf pairing's own title doesn't say
  // which tournament it's part of; an esports match's title/series name
  // already does) - skips it when the title already contains the same
  // text, rather than showing "Dota 2" redundantly next to a title that
  // already says Dota 2.
  const competition = eventInfo && eventInfo.competition;
  const competitionScope = eventInfo && eventInfo.competition_scope;
  const liveDataHtml = eventInfo ? eventLiveDataLineHTML(group[0].event_ticker, true) : '';
  const tagsHtml = eventInfo && eventInfo.category_tags && eventInfo.category_tags.length
    ? `<div class="context-line">${eventInfo.category_tags.slice(0, 3).map(tag => `<span class="meta-chip">${esc(tag)}</span>`).join('')}</div>`
    : '';
  const competitionHtml = competition && !title.toLowerCase().includes(competition.toLowerCase())
    ? `<div class="context-line">${competitionScope ? `<span class="scope-tag">${esc(competitionScope)}</span> · ` : ''}${esc(competition)}</div>`
    : (competitionScope ? `<div class="context-line"><span class="scope-tag">${esc(competitionScope)}</span></div>` : '');
  return `<div class="event-card">
    <div class="event-card-header">
      ${category ? `<span class="cat-tag">${esc(category)}</span>` : ''}
      <span class="event-card-title">${esc(title)}</span>
      ${liveBadgeHTML(group[0])}
    </div>
    ${competitionHtml}
    ${tagsHtml}
    ${liveDataHtml}
    <div class="outcome-rows">${rows}</div>
    <div class="meta"><span>vol ${Math.round(totalVol).toLocaleString()}</span><span>${group.length} markets</span></div>
  </div>`;
}

// A series past this many individual markets (e.g. a full tournament
// bracket) renders collapsed by default - direct report that a handful of
// oversized series, not the general card count, was what actually blew the
// page out (one 69-market PGA event alone). Small multi-outcome groups
// (a 2-3-way race) stay fully expanded, matching today's behavior exactly.
const _SERIES_COLLAPSE_THRESHOLD = 8;

function renderMarketCards(containerId, countId, markets, prices, includeWhale, signals, seriesTrackRecord) {
  const el = $(containerId);
  el.classList.add('cards-grid'); // shares its container with the Advanced screener table (renderScreenerTableFromState), which needs this class off
  if (countId) $(countId).textContent = markets.length ? `(${markets.length})` : '';
  if (!markets.length) { el.innerHTML = '<div class="empty">No markets loaded</div>'; return; }

  // A poll-driven rebuild would otherwise silently re-collapse/re-expand
  // every series-section back to its threshold default every ~5s, snapping
  // shut a manually-expanded large series (or re-expanding a manually-
  // collapsed small one) - same bug class already found and fixed once for
  // the whale-reason disclosure toggle. Capture each series' current open
  // state before the rebuild; anything not seen before falls back to the
  // threshold default, anything seen before keeps exactly what the user set.
  const prevSeriesOpenState = new Map(
    Array.from(el.querySelectorAll('details.series-section[data-series]')).map(d => [d.dataset.series, d.open])
  );

  // markets arrives grouped by parent series (see main.py's _fetch_markets,
  // which re-groups after appending any open-position ticker that rotated
  // out of round_robin_select's own selection - direct report, 2026-08-11:
  // "watchlist groupings is broken... likely a result of the active
  // removal of watchlist items"). Grouped here by series key via a Map
  // rather than assumed-contiguous, so a stray same-series ticker landing
  // somewhere non-adjacent (a backend edge case, today or in the future)
  // merges into its series' existing section instead of splitting into a
  // second one - belt-and-suspenders on top of the backend's own fix, not
  // a replacement for it. Still preserves first-occurrence order (a
  // dedicated Map insertion, not a re-sort), same volume-priority ordering
  // round_robin_select produces.
  const seriesRuns = [];
  const _seriesRunByKey = new Map();
  markets.forEach(m => {
    const s = seriesOf(m.ticker);
    let run = _seriesRunByKey.get(s);
    if (!run) {
      run = { series: s, markets: [] };
      _seriesRunByKey.set(s, run);
      seriesRuns.push(run);
    }
    run.markets.push(m);
  });

  el.innerHTML = seriesRuns.map(run => {
    // Within one series, still group by event_ticker - siblings sharing an
    // event are outcomes of the same real-world question (see
    // eventGroupCardHTML), a finer-grained relationship than "same series."
    const eventGroups = new Map();  // event_ticker (or a per-market unique key) -> markets[]
    run.markets.forEach(m => {
      const key = m.event_ticker || ('__solo__' + m.ticker);
      if (!eventGroups.has(key)) eventGroups.set(key, []);
      eventGroups.get(key).push(m);
    });
    const cardsHtml = Array.from(eventGroups.entries()).map(([key, group]) => {
      return group.length === 1
        ? marketCardHTML(group[0], prices, includeWhale, signals, seriesTrackRecord)
        : eventGroupCardHTML(group, eventTitles[key], prices);
    }).join('');

    if (eventGroups.size === 1) return cardsHtml;  // one event - nothing to nest, render exactly as before

    const totalVol = run.markets.reduce((sum, m) => sum + (parseFloat(m.volume_24h_fp) || 0), 0);
    const sharedScope = (() => {
      const scopes = uniqueSorted(run.markets.map(m => marketContext(m.ticker, null).competitionScope));
      return scopes.length === 1 ? scopes[0] : null;
    })();
    const isOpen = prevSeriesOpenState.has(run.series)
      ? prevSeriesOpenState.get(run.series)
      : run.markets.length <= _SERIES_COLLAPSE_THRESHOLD;
    return `<details class="series-section" data-series="${esc(run.series)}"${isOpen ? ' open' : ''}>
      <summary class="series-section-header">
        <span class="series-section-title">${sharedScope ? `<span class="scope-tag">${esc(sharedScope)}</span> · ` : ''}${esc(seriesLabel(run.series))}</span>
        <span class="series-section-meta">${eventGroups.size} events · ${run.markets.length} markets · vol ${Math.round(totalVol).toLocaleString()}</span>
      </summary>
      <div class="cards-grid">${cardsHtml}</div>
    </details>`;
  }).join('');
}

// Dense, sortable screener table (ROADMAP.md Phase 0.5) - one shared,
// configurable renderer instead of three diverging market-list renderers
// (renderMarkets for Terminal's compact column, renderMarketCards for
// Markets/Whale Watch, nothing shared between them). Kalshi Pro-inspired:
// price/spread/volume/whale-lean in one sortable table rather than a card
// grid. Columns actually available without adding new per-tick API calls:
// spread comes from yes_ask_dollars (already fetched on every market
// object, just not previously exposed - see _MARKET_FIELDS) minus the
// existing yes-bid price; "5-minute volume" from Kalshi Pro's own screener
// was deliberately NOT faked - _fetch_trade_tape only pulls 5 trades per
// market capped at 30 total across the whole watchlist, nowhere near
// enough to compute a real rolling 5-minute figure, so 24h volume (already
// reliable) is shown instead rather than a number that would quietly be
// wrong on any market busier than a handful of trades. True order-book
// depth would need one extra API call per market per poll tick - not
// added here for the same reason the earlier /api/state efficiency pass
// avoided widening the poll loop's request fan-out.
// One independent sort/render-cache state object per panel that embeds
// this table (Terminal/Markets/Whale Watch), same pattern as
// tradeLogFilter/decisionFilter above - each panel remembers its own sort
// choice and its own last-rendered dataset, so a header click can
// re-render without needing the caller to pass everything through again.

export { _SERIES_COLLAPSE_THRESHOLD, computeWhaleLean, divergencePts, eventGroupCardHTML, marketCardHTML, renderEquityChart, renderHistoryTrades, renderMarketCards, seriesLabelHTML, winRateBlockHTML };

// Exposed for inline HTML event handlers (onclick=/onchange=/oninput=,
// including ones built indirectly via a caller-supplied onclick-string
// parameter - see shared-utils.js's header). Every top-level function in
// this file is exposed (cheap, harmless if unused); state objects only the
// specific ones confirmed to be read/mutated directly from a handler.
window.computeWhaleLean = computeWhaleLean;
window.divergencePts = divergencePts;
window.eventGroupCardHTML = eventGroupCardHTML;
window.marketCardHTML = marketCardHTML;
window.renderEquityChart = renderEquityChart;
window.renderHistoryTrades = renderHistoryTrades;
window.renderMarketCards = renderMarketCards;
window.seriesLabelHTML = seriesLabelHTML;
window.winRateBlockHTML = winRateBlockHTML;
