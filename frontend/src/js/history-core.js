import { loadAdvisory, loadBacktestSweeps, loadCalibrationHistory, loadCalibrationReport, loadCandidateLogSummary, loadChangeHistory, loadCrossStrategyComparison, loadMarketAnalyst, loadRegimeSegmentation, loadSeriesEvaluator } from './advisory-calibration.js';
import { renderEquityChart, renderHistoryTrades } from './equity-and-cards.js';
import { $, esc, fetchJSON, fmt, formatConfigValue, marketTitles } from './shared-utils.js';

// Part of index.html's JS split - see shared-utils.js's header for the
// load-order/shared-global-scope rationale common to all these files.
// This file: History tab's core trade-history load/render (narrative,
// summary, P&L curve, close-type breakdown) and its plain-language phrase
// maps used by the suggestion/advisory cards in advisory-calibration.js.
let historyTrades = [];
let historyTotal = 0;
let historyFilter = { offset: 0, limit: 25 };

async function loadTradingHistory() {
  const el = $('history-trades');
  if (!el) return;
  const params = new URLSearchParams({ limit: historyFilter.limit, offset: historyFilter.offset });
  try {
    const resp = await fetchJSON(`/api/trading-history?${params}`);
    historyTrades = resp.trades || [];
    historyTotal = resp.total || 0;
    Object.assign(marketTitles, resp.market_titles || {});
    renderHistoryNarrative(resp.summary);
    renderHistorySummary(resp.summary);
    renderHistoryPnlChart(resp.cumulative_pnl_curve || []);
    renderHistoryCloseTypeBreakdown((resp.summary || {}).by_close_type || {});
    renderExitManagementSplit(resp.exit_management_split || null);
    renderHistoryTrades();
    loadAdvisory();
    loadDeclinedSuggestions();
    loadMarketAnalyst();
    loadSeriesEvaluator();
    loadChangeHistory();
    loadCalibrationReport();
    loadCalibrationHistory();
    loadCrossStrategyComparison();
    loadRegimeSegmentation();
    loadCandidateLogSummary();
    loadBacktestSweeps();
  } catch (e) {
    el.innerHTML = '<div class="empty">Failed to load trading history.</div>';
  }
}

function historyDurationHTML(sec) {
  if (sec === null || sec === undefined) return '—';
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  if (sec < 86400) return `${(sec / 3600).toFixed(1)}h`;
  return `${(sec / 86400).toFixed(1)}d`;
}

const HISTORY_CLOSE_TYPE_LABELS = {
  take_profit: 'Take-Profit', stop_loss: 'Stop-Loss', sentiment_reversal: 'Sentiment Reversal',
  momentum_reversal: 'Momentum Reversal', auto_exit: 'Auto-Exit', settled_win: 'Settled (Won)',
  settled_loss: 'Settled (Lost)', unknown: 'Unknown',
  // Matches trade_analytics.py's runway_exhausted pattern, added 2026-08-17
  // alongside it - without an entry here this would still render, just as
  // the raw key instead of "Unknown", since HISTORY_CLOSE_TYPE_LABELS[key]
  // falls back to the key itself (not to 'unknown') when missing.
  runway_exhausted: 'Runway Exhausted', position_netting: 'Position Netting',
};

// Plain-language narrative (2026-08-14/15 direct request) - the very first
// thing on the History tab, before any stat card or panel. The action line
// ("nothing needs your attention" / "N suggestions below") is filled in by
// loadAdvisory() below once it knows the real, declined-filtered count -
// this function only ever renders the trade-performance half.
function renderHistoryNarrative(summary) {
  const el = $('history-narrative');
  if (!el) return;
  summary = summary || {};
  const n = summary.total_closed ?? 0;
  if (n === 0) {
    el.innerHTML = `<div>You haven't finished any trades yet — once some close, a plain-English summary of how things are going will show up here.</div>`;
    return;
  }
  const winRate = summary.win_rate_pct;
  const pnl = summary.total_realized_pnl ?? 0;
  const pnlPhrase = pnl > 0.005 ? `made about ${fmt(pnl)} overall`
    : (pnl < -0.005 ? `lost about ${fmt(Math.abs(pnl))} overall` : `roughly broken even overall`);
  const winRatePhrase = winRate != null ? `won about ${Math.round(winRate)} out of every 100 trades` : `not enough resolved trades yet to show a real win rate`;
  // Direct callback to a real finding from this app's own trade-history
  // review: a high win rate does not reliably mean big profits - winning
  // trades can be small and capped while the occasional loss is much
  // larger. Called out explicitly here rather than left for someone to
  // notice the mismatch themselves.
  const deployed = summary.total_capital_deployed || 0;
  const thin = deployed > 0 && Math.abs(pnl) < deployed * 0.03;
  const divergenceNote = (winRate != null && winRate >= 55 && thin)
    ? ` A high win rate doesn't always mean big profits — winning trades here have generally been smaller than the occasional bigger loss, which is why the overall total is still modest relative to how much has been put to work.`
    : '';
  el.innerHTML = `
    <div>Out of <b>${n}</b> finished trades, you ${winRatePhrase}, and ${pnlPhrase}.${divergenceNote}</div>
    <div id="history-narrative-action-line" style="margin-top:8px;">Checking whether anything needs your attention…</div>
  `;
}

function renderHistorySummary(summary) {
  summary = summary || {};
  const winRate = summary.win_rate_pct !== null && summary.win_rate_pct !== undefined ? `${summary.win_rate_pct.toFixed(0)}%` : '—';
  const pnl = summary.total_realized_pnl ?? 0;
  const pnlColor = pnl > 0 ? 'var(--yes)' : (pnl < 0 ? 'var(--no)' : 'var(--text)');
  $('history-summary-cards').innerHTML = `
    <div class="cell"><div class="lbl">Closed Trades</div><div class="val">${summary.total_closed ?? 0}</div></div>
    <div class="cell"><div class="lbl">Win Rate</div><div class="val">${winRate}</div></div>
    <div class="cell"><div class="lbl">Total Capital Deployed</div><div class="val">${fmt(summary.total_capital_deployed ?? 0)}</div></div>
    <div class="cell"><div class="lbl">Total Realized P&amp;L</div><div class="val" style="color:${pnlColor}">${fmt(pnl)}</div></div>
    <div class="cell"><div class="lbl">Left on Table (early exits)</div><div class="val">${fmt(summary.total_left_on_table ?? 0)}</div></div>
    <div class="cell" title="Real Kalshi taker fees (0.07 * contracts * price * (1-price) per leg), already netted into Total Realized P&amp;L above - shown separately so the fee drag itself is visible, not just its effect on the bottom line."><div class="lbl">Fees Paid</div><div class="val">${fmt(summary.total_fees_paid ?? 0)}</div></div>
    <div class="cell"><div class="lbl">Avg Hold Time</div><div class="val">${historyDurationHTML(summary.avg_hold_sec)}</div></div>
  `;
}

function renderHistoryPnlChart(curve) {
  renderEquityChart(curve, 0, 'cumulative_pnl', 'breakeven', 'history-pnl-chart');
}

function renderHistoryCloseTypeBreakdown(byCloseType) {
  const el = $('history-close-type-breakdown');
  const keys = Object.keys(byCloseType);
  if (!keys.length) {
    el.innerHTML = '<div class="empty">No closed trades yet.</div>';
    return;
  }
  const rows = keys.map(key => {
    const c = byCloseType[key];
    const avgColor = (c.avg_pnl ?? 0) > 0 ? 'var(--yes)' : ((c.avg_pnl ?? 0) < 0 ? 'var(--no)' : 'var(--text)');
    return `<tr>
      <td>${esc(HISTORY_CLOSE_TYPE_LABELS[key] || key)}</td>
      <td>${c.count}</td>
      <td>${c.wins}/${c.count}</td>
      <td>${c.total_pnl !== null ? fmt(c.total_pnl) : '—'}</td>
      <td style="color:${avgColor}">${c.avg_pnl !== null ? fmt(c.avg_pnl) : '—'}</td>
    </tr>`;
  }).join('');
  el.innerHTML = `<table class="positions-table">
    <thead><tr><th>Close Type</th><th>Count</th><th>Wins</th><th>Total P&amp;L</th><th>Avg P&amp;L</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

const HISTORY_CONFIDENCE_COLOR = { low: 'var(--muted)', moderate: 'var(--whale)', higher: 'var(--yes)' };

// ---- History tab redesign (2026-08-14/15 direct request) --------------
// "a like-I'm-5 person who knows nothing about trading" needs every
// suggestion, regardless of which engine produced it, to read as one plain
// sentence with an honest confidence read - not a raw dotted config path.
// Deliberately NOT exhaustive (full-spectrum analysis can suggest literally
// any config field, see main.py's _full_spectrum_suggestions_from_raw) -
// covers the paths the rule-based engines actually produce today; anything
// else falls through to a readable, honest fallback rather than showing
// nothing or guessing.
const PLAIN_CONFIG_PATH_PHRASES = {
  'strategy.entry_threshold': 'how picky the whale-follow strategy is before it enters a trade — a whale print has to look at least this convincing',
  'strategy.min_whale_winrate_pct': "the bar a market's own past whale track record has to clear before this strategy will trust a new signal there",
  'strategy.stop_loss_pct': 'how much of a loss the whale-follow strategy will tolerate on a position before automatically closing it',
  'strategy.take_profit_pct': "how much of a gain the whale-follow strategy locks in early, instead of waiting to see if the market fully resolves in its favor",
  'strategy.auto_exit_threshold': 'how confident the automatic exit system needs to be before it closes a position early',
  'strategy.cooldown_sec': 'how long the strategy waits before trading the same market again',
  'strategy.longshot_entry_threshold_bonus': "how much extra caution applies to very cheap or very expensive (“longshot”) markets before entering",
  'strategy_overrides.by_category': 'a different pickiness level for one specific type of market (like politics or sports), instead of the same bar everywhere',
  'strategy.close_window_sec': "how close to a market's closing time the strategy is willing to enter a new trade",
  'strategy.special_market_min_seconds_to_close': 'how cautious the strategy is right before an unusual market (early-close or special settlement rules) closes',
  'strategy.excluded_series': 'the list of market types the strategy is told to skip entirely',
  'strategy.exit_sentiment_min_signals': "how many recent whale trades are needed before “whale sentiment reversed” is trusted enough to close a position",
  'strategy.exit_sentiment_lean_pct': 'how one-sided recent whale activity has to be before treated as a real reversal',
  'market_strategy.min_momentum_delta': 'how big a recent price move has to be before the market-native strategy treats it as real momentum',
  'market_strategy.entry_confidence_threshold': 'how confident the market-native strategy needs to be before entering a trade',
  'market_strategy.stop_loss_pct': 'how much of a loss the market-native strategy will tolerate before automatically closing a position',
  'market_strategy.take_profit_pct': 'how much of a gain the market-native strategy locks in early',
};
const PLAIN_SECTION_PHRASES = {
  strategy: 'the whale-follow strategy', market_strategy: 'the market-native strategy',
  risk: 'risk controls', whale_watcher_kalshi: 'whale-print detection',
  confidence_calibration: 'whale-signal calibration', advisory: 'the suggestion engine itself',
  kalshi_account: 'your real-money account settings',
};
function plainLanguageConfigPath(path) {
  if (PLAIN_CONFIG_PATH_PHRASES[path]) return PLAIN_CONFIG_PATH_PHRASES[path];
  const [section, ...rest] = path.split('.');
  const field = rest.join('.').replace(/_/g, ' ');
  const sectionPhrase = PLAIN_SECTION_PHRASES[section] || section.replace(/_/g, ' ');
  return `a setting called “${field}” (part of ${sectionPhrase})`;
}
function suggestionHeadlineHTML(rec) {
  return `This would change ${plainLanguageConfigPath(rec.config_path)}.`;
}
const PLAIN_CONFIDENCE_PHRASES = {
  low: 'Based on only a little data so far — treat this as a hint, not a fact.',
  moderate: 'Based on a decent amount of data.',
  higher: 'Based on a lot of real data — this is a solid signal.',
};
function plainConfidencePhrase(confidenceLabel) {
  return PLAIN_CONFIDENCE_PHRASES[confidenceLabel]
    || 'This came from an AI reading your data, not a strict statistical test — use your own judgment.';
}

// One card for every suggestion source (Advisory, per-series analyst,
// full-spectrum analyst) - acceptOnclickJS is the literal onclick string
// for the "Yes" button, reusing each source's own existing, already-
// working apply function untouched (applyAdvisoryRecommendation/
// applySeriesSuggestion/applyFullSpectrumSuggestion each already handle
// their own route + button feedback correctly). Only the card markup and
// the decline path are unified/new.
// Staleness + significance (2026-08-15 direct request: "the advisory
// should also take into consideration how many samples have been logged
// after the change... give me a statistical significance score in
// addition to the semantics"). fresh_samples_since_change/significance_z/
// significance_t are computed server-side (services/advisory_engine.py's
// _drop_stale_recommendations + every per-field recommendation function -
// see services/stats_power.py's two_proportion_z_score/one_sample_t_score)
// - this is purely how they're surfaced, not where they're derived.
function freshSamplesHTML(rec) {
  const n = rec.fresh_samples_since_change;
  if (n == null) return '';  // never changed before - no "since your last change" to caveat
  const thin = n < 5;
  return `<div style="font-size:12px; margin-bottom:6px; color:${thin ? 'var(--danger)' : 'var(--muted)'};">
    ${thin ? '⚠ ' : ''}${n} sample${n === 1 ? '' : 's'} logged since you last changed this setting${thin ? ' — treat this suggestion cautiously' : ''}.
  </div>`;
}
function significanceScoreHTML(rec) {
  const z = rec.significance_z, t = rec.significance_t;
  const score = z != null ? z : t;
  if (score == null) return '';
  const label = z != null ? 'z' : 't';
  const testName = z != null ? 'a two-proportion z-test (comparing two win rates)' : 'a one-sample t-test (comparing an average dollar result against zero)';
  const abs = Math.abs(score);
  let verdict, color;
  if (abs >= 2.576) { verdict = 'strongly significant (99% confidence)'; color = 'var(--yes)'; }
  else if (abs >= 1.96) { verdict = 'significant (95% confidence)'; color = 'var(--yes)'; }
  else if (abs >= 1.645) { verdict = 'borderline (90% confidence)'; color = 'var(--whale)'; }
  else { verdict = 'not statistically significant yet'; color = 'var(--muted)'; }
  return `<div style="font-size:12px; margin-bottom:10px;" title="${esc(testName)} - the standard statistical test for whether this gap is real or just sampling noise.">
    ${label} = ${score.toFixed(2)} — <span style="color:${color};">${verdict}</span>
  </div>`;
}
function renderSuggestionCard(rec, acceptOnclickJS) {
  const confBadge = rec.confidence_label
    ? `<span style="color:${HISTORY_CONFIDENCE_COLOR[rec.confidence_label] || 'var(--muted)'};">${plainConfidencePhrase(rec.confidence_label)}</span>`
    : plainConfidencePhrase(null);
  // Direct report (2026-08-11): "the apply button should only appear next
  // to config change options the system agrees with" - a 'low' confidence
  // suggestion still shows (transparency: here's what the data hints at)
  // but offers no one-click way to act on something the system itself is
  // hedging on. "No thanks" stays available regardless - holding back is
  // always a valid choice, even on a suggestion with no working Yes.
  const yesHTML = rec.confidence_label === 'low'
    ? `<button class="suggestion-yes-btn" disabled title="Not enough data yet for the system to endorse this">Not enough data to act on yet</button>`
    : `<button class="suggestion-yes-btn" onclick="${acceptOnclickJS}">✅ Yes, make this change</button>`;
  return `
    <div class="suggestion-card" data-suggestion-id="${esc(rec.id)}">
      <div class="suggestion-headline">${suggestionHeadlineHTML(rec)}</div>
      <div class="suggestion-confidence">${confBadge}</div>
      <div style="font-size:12px; color:var(--muted); margin-bottom:2px;"><span class="config-path" style="cursor:pointer; text-decoration:underline dotted;" data-jump-path="${esc(rec.config_path)}" title="Jump to this setting on the Config tab">${esc(rec.config_path)}</span></div>
      <div style="margin-bottom:6px;">Currently: <b>${esc(formatConfigValue(rec.current_value))}</b> &nbsp;→&nbsp; Suggested: <b style="color:var(--whale);">${esc(formatConfigValue(rec.suggested_value))}</b></div>
      ${freshSamplesHTML(rec)}
      ${significanceScoreHTML(rec)}
      <details class="suggestion-why">
        <summary>Why is this suggested? (technical details)</summary>
        <div class="suggestion-why-body">
          <div>${esc(rec.rationale || '')}</div>
          <div style="margin-top:6px;">Setting: <span class="config-path" style="cursor:pointer; text-decoration:underline dotted;" data-jump-path="${esc(rec.config_path)}" title="Jump to this setting on the Config tab">${esc(rec.config_path)}</span>${rec.n != null ? ` · n=${esc(String(rec.n))}` : ''}</div>
        </div>
      </details>
      <div class="suggestion-actions">
        ${yesHTML}
        <button class="suggestion-no-btn" onclick="declineSuggestion('${esc(rec.id)}', '${esc(rec.config_path)}', this)">No thanks, leave it</button>
      </div>
      <div class="suggestion-decided" style="display:none;"></div>
    </div>
  `;
}

async function declineSuggestion(id, configPath, btn) {
  const card = btn.closest('.suggestion-card');
  btn.disabled = true;
  const yesBtn = card.querySelector('.suggestion-yes-btn');
  if (yesBtn) yesBtn.disabled = true;
  try {
    await fetchJSON('/api/suggestions/decline', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, config_path: configPath}),
    });
    card.querySelectorAll('.suggestion-actions, .suggestion-why, .suggestion-confidence').forEach(el => el.style.display = 'none');
    const decided = card.querySelector('.suggestion-decided');
    decided.style.display = '';
    decided.textContent = 'Okay — nothing changed. You can revisit this anytime (Advanced → Previously declined).';
  } catch (e) {
    btn.disabled = false;
    if (yesBtn) yesBtn.disabled = false;
  }
}

async function loadDeclinedSuggestions() {
  const el = $('declined-suggestions-list');
  if (!el) return;
  try {
    const resp = await fetchJSON('/api/suggestions/declined');
    const rows = resp.declined || [];
    el.innerHTML = !rows.length ? '<div class="empty">Nothing declined yet.</div>' : rows.map(r => `
      <div class="suggestion-decided" style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
        <span>${esc(plainLanguageConfigPath(r.config_path))}</span>
        <button style="flex-shrink:0;" onclick="undeclineSuggestion('${esc(r.id)}', this)">Undo — show this again</button>
      </div>
    `).join('');
  } catch (e) {
    el.innerHTML = '<div class="empty">Failed to load.</div>';
  }
}

async function undeclineSuggestion(id, btn) {
  btn.disabled = true;
  try {
    await fetchJSON('/api/suggestions/undecline', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}),
    });
    loadDeclinedSuggestions();
    setTimeout(loadAdvisory, 400);
  } catch (e) {
    btn.disabled = false;
  }
}

function renderExitManagementSplit(split) {
  const el = $('history-exit-mgmt');
  if (!split) {
    el.innerHTML = '<div class="empty">Not enough closed trades yet in both groups.</div>';
    return;
  }
  el.innerHTML = `
    <div class="stat-cards">
      <div class="cell"><div class="lbl">Held to Resolution</div><div class="val">${split.settled_win_rate_pct.toFixed(0)}% <span style="font-size:11px; color:var(--muted); font-weight:400;">(n=${split.n_settled})</span></div></div>
      <div class="cell"><div class="lbl">Actively Managed</div><div class="val">${split.managed_win_rate_pct.toFixed(0)}% <span style="font-size:11px; color:var(--muted); font-weight:400;">(n=${split.n_managed})</span></div></div>
    </div>`;
}

// Advisory Recommendations (docs/advisory-engine-plan.md) - loaded
// alongside the rest of the History tab whenever it opens (showView). Two
// separate fetches: /api/advisory/status (always safe, reports progress)
// and /api/advisory/recommendations. Unified 2026-08-10 with the old
// separate "Config Tuning Hints" panel - see services/advisory_engine.py's
// docstring. No blanket gate anymore: an empty list here just means there's
// nothing to suggest yet, not that a per-variant floor is blocking output -
// resolved_count/min_resolved_trades_per_variant are still shown as
// progress toward unlocking cross-variant ("a fully different past config
// did better") comparisons specifically, the one suggestion type that still
// needs it.
// Shared auto-apply toggle (2026-08-10) - used by both Advisory
// Recommendations and Whale-Signal Calibration. Real bug found live:
// advisory.auto_apply_enabled was already a protected config field with an
// error message pointing at /api/advisory/auto-apply/enable|disable, but
// those routes never existed and nothing ever read auto_apply_min_confidence/
// auto_apply_cooldown_sec - the feature was reachable from no path at all.
// Fixed alongside adding the equivalent for confidence_calibration (direct
// request: "i want the option to enable auto whale-signal calibration").
// Disabling never needs the phrase (asymmetric friction, same as real
// trading); enabling does, via a plain prompt() rather than a full modal -
// this is real-money-adjacent but paper-mode-only, the same severity tier
// full-spectrum scanning's confirm() dialog already established, not real
// trading's own typed-input modal.
async function renderAutoApplyToggle(elId, enabled, enableUrl, disableUrl, confirmPhrase, warningText) {
  const el = $(elId);
  if (!el) return;
  if (enabled) {
    el.innerHTML = `<span style="color:var(--yes); font-size:12px;">✅ Auto-apply is ON</span>
      <button style="margin-left:8px; font-size:11px; padding:2px 8px;" onclick="toggleAutoApply('${elId}', false, '${enableUrl}', '${disableUrl}', '', '')">Turn off</button>`;
  } else {
    // Plain-language default-off framing (2026-08-14/15 direct request) -
    // "if you leave this off (the default), nothing changes automatically
    // - you'll keep seeing suggestions and choosing for yourself."
    el.innerHTML = `<div><span style="color:var(--muted); font-size:12px;">Auto-apply is off</span>
      <button style="margin-left:8px; font-size:11px; padding:2px 8px;" onclick="toggleAutoApply('${elId}', true, '${enableUrl}', '${disableUrl}', '${esc(confirmPhrase)}', this)">Turn on…</button></div>
      <div style="font-size:11px; color:var(--muted); margin-top:4px;">This is the default. Nothing changes automatically — you'll keep seeing suggestions above and choosing for yourself, same as now.</div>`;
  }
}

async function toggleAutoApply(elId, enable, enableUrl, disableUrl, confirmPhrase, btn) {
  if (enable) {
    const phrase = prompt(
      `Type exactly: ${confirmPhrase}\n\nThis lets the app automatically apply a real suggested config change on ` +
      `its own cooldown, with no manual review step each time. You can turn it back off anytime with one click.`
    );
    if (phrase === null) return;
    try {
      await fetchJSON(enableUrl, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({confirmation_phrase: phrase}),
      });
    } catch (e) {
      // fetchJSON now throws on the backend's own 400 (wrong phrase)
      // instead of silently resolving with a {detail: ...} body
      // (2026-08-17 fetchJSON fix) - e.message carries the same text that
      // used to be read as result.detail.
      alert(e.message || 'Confirmation phrase did not match.');
      return;
    }
  } else {
    await fetchJSON(disableUrl, {method: 'POST'});
  }
  loadAdvisory();
  loadCalibrationReport();
}

// Direct part of the narrative box (renderHistoryNarrative above) - "is
// there anything for me to look at" is answered in one place, in plain
// language, not left implicit in an empty-vs-populated panel below.
function updateHistoryActionLine(text, isGood) {
  const el = $('history-narrative-action-line');
  if (!el) return;
  el.innerHTML = isGood ? `<span class="nothing-needed">✓ ${esc(text)}</span>` : esc(text);
}

// In-flight guard (2026-08-17, real bug found testing the calibration
// panel's own equivalent below): this panel re-renders wholesale
// (el.innerHTML = ...) every 5s while the History tab is active
// (refreshHistoryInsightsIfActive), which was replacing the exact button a
// user had just clicked with a fresh element before the in-flight apply's
// own feedback (`btn.textContent = 'Applied ✓'`) had a chance to land -
// mutating a DOM node that had already been detached from the page is not
// an error, just silently invisible, so a click that genuinely worked (or
// genuinely failed) looked like it "did nothing." Skipping the periodic
// re-render while an apply is in flight keeps the clicked button's own
// identity stable until its own feedback has actually been shown.

export { HISTORY_CLOSE_TYPE_LABELS, HISTORY_CONFIDENCE_COLOR, PLAIN_CONFIDENCE_PHRASES, PLAIN_CONFIG_PATH_PHRASES, PLAIN_SECTION_PHRASES, declineSuggestion, freshSamplesHTML, historyDurationHTML, historyFilter, historyTotal, historyTrades, loadDeclinedSuggestions, loadTradingHistory, plainConfidencePhrase, plainLanguageConfigPath, renderAutoApplyToggle, renderExitManagementSplit, renderHistoryCloseTypeBreakdown, renderHistoryNarrative, renderHistoryPnlChart, renderHistorySummary, renderSuggestionCard, significanceScoreHTML, suggestionHeadlineHTML, toggleAutoApply, undeclineSuggestion, updateHistoryActionLine };

// Exposed for inline HTML event handlers (onclick=/onchange=/oninput=,
// including ones built indirectly via a caller-supplied onclick-string
// parameter - see shared-utils.js's header). Every top-level function in
// this file is exposed (cheap, harmless if unused); state objects only the
// specific ones confirmed to be read/mutated directly from a handler.
window.declineSuggestion = declineSuggestion;
window.freshSamplesHTML = freshSamplesHTML;
window.historyDurationHTML = historyDurationHTML;
window.loadDeclinedSuggestions = loadDeclinedSuggestions;
window.loadTradingHistory = loadTradingHistory;
window.plainConfidencePhrase = plainConfidencePhrase;
window.plainLanguageConfigPath = plainLanguageConfigPath;
window.renderAutoApplyToggle = renderAutoApplyToggle;
window.renderExitManagementSplit = renderExitManagementSplit;
window.renderHistoryCloseTypeBreakdown = renderHistoryCloseTypeBreakdown;
window.renderHistoryNarrative = renderHistoryNarrative;
window.renderHistoryPnlChart = renderHistoryPnlChart;
window.renderHistorySummary = renderHistorySummary;
window.renderSuggestionCard = renderSuggestionCard;
window.significanceScoreHTML = significanceScoreHTML;
window.suggestionHeadlineHTML = suggestionHeadlineHTML;
window.toggleAutoApply = toggleAutoApply;
window.undeclineSuggestion = undeclineSuggestion;
window.updateHistoryActionLine = updateHistoryActionLine;
window.historyFilter = historyFilter;
