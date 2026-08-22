import { loadConfig } from './config-panel.js';
import { renderEquityChart } from './equity-and-cards.js';
import { HISTORY_CLOSE_TYPE_LABELS, historyDurationHTML, renderAutoApplyToggle, renderSuggestionCard, updateHistoryActionLine } from './history-core.js';
import { showView } from './main.js';
import { $, contextLineHTML, esc, fetchJSON, fmt, formatConfigValue, marketLabel, marketTitles, seriesLabel } from './shared-utils.js';
import { sourceLabel } from './trading-gate-and-connectivity.js';

// Part of index.html's JS split - see shared-utils.js's header for the
// load-order/shared-global-scope rationale common to all these files.
// This file: History tab's advisory engine, suggestion cards, calibration
// report/history, cross-strategy comparison, regime segmentation,
// candidate-log summary, and backtest-sweep panels.
let advisoryApplyInFlight = false;

async function loadAdvisory() {
  if (advisoryApplyInFlight) return;
  const badge = $('advisory-status-badge');
  const el = $('advisory-recommendations');
  try {
    const status = await fetchJSON('/api/advisory/status');
    renderAutoApplyToggle(
      'advisory-auto-apply-toggle', status.auto_apply_enabled,
      '/api/advisory/auto-apply/enable', '/api/advisory/auto-apply/disable',
      'ENABLE ADVISORY AUTO APPLY',
    );
    if (!status.enabled) {
      badge.innerHTML = '<span style="color:var(--muted); font-size:11px; text-transform:uppercase;">disabled</span>';
      el.innerHTML = '<div class="empty">Advisory recommendations are disabled — enable them in Config → Advisory Engine.</div>';
      updateHistoryActionLine('Nothing needs your attention right now.', true);
      return;
    }
    const resp = await fetchJSON('/api/advisory/recommendations');
    const recs = resp.recommendations || [];
    badge.innerHTML = recs.length
      ? `<span style="color:var(--yes); font-size:11px; text-transform:uppercase;">${recs.length} active</span>`
      : '<span style="color:var(--muted); font-size:11px; text-transform:uppercase;">enabled</span>';
    if (!recs.length) {
      el.innerHTML = `<div class="empty">No suggestions yet — keep trading to build up history. Cross-config comparisons need ` +
        `${esc(String(resp.resolved_count ?? 0))}/${esc(String(resp.min_resolved_trades_per_variant ?? '?'))} resolved trades under the current config.</div>`;
      updateHistoryActionLine('Nothing needs your attention right now.', true);
      return;
    }
    el.innerHTML = recs.map(r => renderSuggestionCard(r, `applyAdvisoryRecommendation('${esc(r.id)}', this)`)).join('');
    el.querySelectorAll('[data-jump-path]').forEach(chip => {
      chip.addEventListener('click', () => jumpToConfigSetting(chip.dataset.jumpPath));
    });
    updateHistoryActionLine(
      recs.length === 1 ? "There's 1 suggestion below you could look at — nothing requires action." :
        `There are ${recs.length} suggestions below you could look at — nothing requires action.`,
      false,
    );
  } catch (e) {
    badge.innerHTML = '';
    el.innerHTML = '<div class="empty">Failed to load advisory recommendations.</div>';
    updateHistoryActionLine("Couldn't check for suggestions right now — try refreshing.", false);
  }
}

// Direct follow-on from the Config tab overhaul (Item 5): every field there
// already shows the same dotted .config-path chip a suggestion card shows
// here, so "jump to this setting" needs no per-field id-mapping table -
// just find the Config tab's own chip with matching text and scroll to it.
function jumpToConfigSetting(path) {
  const chip = Array.from(document.querySelectorAll('#view-config .config-path'))
    .find(el => el.textContent.trim() === path);
  if (!chip) return;
  showView('config');
  const details = chip.closest('details.config-section');
  if (details) details.open = true;
  requestAnimationFrame(() => {
    const field = chip.closest('.field') || chip;
    field.scrollIntoView({behavior: 'smooth', block: 'center'});
    field.classList.add('jump-highlight');
    setTimeout(() => field.classList.remove('jump-highlight'), 1600);
  });
}

async function applyAdvisoryRecommendation(id, btn) {
  // No confirm() dialog (2026-08-14/15 History tab redesign) - clicking
  // the unified suggestion card's own clearly-labeled "Yes, make this
  // change" button IS the deliberate choice; a native popup on top of it
  // would be both redundant friction and inconsistent with "No thanks"
  // right next to it having none.
  btn.disabled = true;
  advisoryApplyInFlight = true;
  try {
    await fetchJSON('/api/advisory/recommendations/apply', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}),
    });
    btn.textContent = 'Applied ✓';
    loadConfig();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Apply failed — retry';
  } finally {
    advisoryApplyInFlight = false;
  }
  setTimeout(loadAdvisory, 800);
  setTimeout(loadChangeHistory, 800);
}

async function applyCalibrationSuggestion(btn) {
  // Manual apply path for whale-signal calibration (2026-08-17 direct
  // report: "doesn't actually refine itself over time. just does
  // nothing."). Before this, suggested_weights could only ever reach the
  // live config via the auto-apply toggle above - typed-confirmation
  // gated, and only even checked once per snapshot_interval_sec. Same
  // no-confirm()-dialog reasoning as applyAdvisoryRecommendation: clicking
  // this clearly-labeled button IS the deliberate choice.
  btn.disabled = true;
  calibrationApplyInFlight = true;
  try {
    await fetchJSON('/api/confidence-calibration/apply', {method: 'POST'});
    btn.textContent = 'Applied ✓';
    loadConfig();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Apply failed — retry';
  } finally {
    calibrationApplyInFlight = false;
  }
  setTimeout(loadCalibrationReport, 800);
  setTimeout(loadChangeHistory, 800);
}

// Change History (Item 3D, 2026-08-10) - every config-change source in
// this app logs here now (see main.py's update_config/enable_trading/
// disable_trading and the Advisory apply route), not just Advisory-applied
// suggestions. effect is attached server-side (services/advisory_engine.py's
// change_effect()) only for strategy.* changes that actually created a new
// config variant with real trades on both sides - null otherwise, rendered
// as a plain logged row with no fabricated delta.
const CHANGE_SOURCE_LABEL = {
  manual: 'manual edit', 'unified-advisory': 'advisory', 'series-analyst': 'AI (series)',
  'full-spectrum-analyst': 'AI (full-spectrum)', 'calibration-manual': 'whale-signal calibration',
};

async function loadChangeHistory() {
  const el = $('change-history-list');
  if (!el) return;
  try {
    const resp = await fetchJSON('/api/advisory/applied-changes?limit=20');
    const changes = resp.changes || [];
    if (!changes.length) {
      el.innerHTML = '<div class="empty">No config changes recorded yet.</div>';
      return;
    }
    el.innerHTML = changes.map(c => {
      const when = new Date(c.applied_at * 1000).toLocaleString();
      const sourceLabel = CHANGE_SOURCE_LABEL[c.source] || esc(c.source);
      let effectHTML = '';
      if (c.effect) {
        const wrDelta = c.effect.after_win_rate_pct - c.effect.before_win_rate_pct;
        const wrColor = wrDelta > 0 ? 'var(--yes)' : (wrDelta < 0 ? 'var(--no)' : 'var(--text)');
        effectHTML = `<div style="margin-top:6px; font-size:12px; color:${wrColor};">
          ${c.effect.before_win_rate_pct.toFixed(0)}% win rate (n=${c.effect.before_n}) →
          ${c.effect.after_win_rate_pct.toFixed(0)}% (n=${c.effect.after_n}) since this change
        </div>`;
      } else if (c.effect_windowed) {
        // Gap 3 (docs/config-tuning-data-gaps-2026-08-10.md) - looser than
        // c.effect above (no exact-fingerprint requirement, so it's not
        // starved by config-variant fragmentation), but weaker causal
        // attribution (other fields may have changed in the same window) -
        // only shown as a fallback when the strict measurement has nothing.
        const ew = c.effect_windowed;
        const wrDelta = ew.after_win_rate_pct - ew.before_win_rate_pct;
        const wrColor = wrDelta > 0 ? 'var(--yes)' : (wrDelta < 0 ? 'var(--no)' : 'var(--text)');
        effectHTML = `<div style="margin-top:6px; font-size:12px; color:${wrColor};">
          ${ew.before_win_rate_pct.toFixed(0)}% win rate (n=${ew.before_n}) →
          ${ew.after_win_rate_pct.toFixed(0)}% (n=${ew.after_n}) before/after this change (time-windowed, not config-isolated)
        </div>`;
      }
      return `
        <div class="signal-card">
          <div class="top-row">
            <span class="config-path">${esc(c.config_path)}</span>
            <span style="color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:0.05em;">${esc(sourceLabel)} · ${esc(when)}</span>
          </div>
          <div style="margin:4px 0;"><b>${esc(formatConfigValue(c.old_value))}</b> → <b style="color:var(--whale);">${esc(formatConfigValue(c.new_value))}</b></div>
          <div style="color:var(--muted); font-size:13px;">${esc(c.rationale)}</div>
          ${effectHTML}
        </div>
      `;
    }).join('');
  } catch (e) {
    el.innerHTML = '<div class="empty">Failed to load change history.</div>';
  }
}

// Whale-Signal Calibration (services/confidence_calibration.py) - read-only,
// same two-fetch shape (status always safe, report gated) as advisory/
// market-analyst above. Direct finding while enabling this against real
// data for the first time (2026-08-10): unusualness_factor/agreement_factor
// both discriminated negatively - this panel is what makes that visible
// without needing to hit the API directly.
// In-flight guard - same real bug and same fix as advisoryApplyInFlight
// above (found here first, 2026-08-17): this panel also re-renders
// wholesale every 5s while the History tab is active, which could replace
// the "Apply suggested weights" button out from under a click before its
// own feedback landed, making a click that worked (or failed) look like it
// silently did nothing.
let calibrationApplyInFlight = false;

async function loadCalibrationReport() {
  if (calibrationApplyInFlight) return;
  const badge = $('calibration-status-badge');
  const el = $('calibration-report');
  try {
    const status = await fetchJSON('/api/confidence-calibration/status');
    renderAutoApplyToggle(
      'calibration-auto-apply-toggle', status.auto_apply_enabled,
      '/api/confidence-calibration/auto-apply/enable', '/api/confidence-calibration/auto-apply/disable',
      'ENABLE CALIBRATION AUTO APPLY',
    );
    if (!status.enabled) {
      badge.innerHTML = '<span style="color:var(--muted); font-size:11px; text-transform:uppercase;">disabled</span>';
      el.innerHTML = '<div class="empty">Whale-signal calibration is disabled — enable confidence_calibration.enabled in config to start judging real signal factors against real outcomes.</div>';
      return;
    }
    const resp = await fetchJSON('/api/confidence-calibration/report');
    if (!resp.report) {
      badge.innerHTML = '<span style="color:var(--muted); font-size:11px; text-transform:uppercase;">enabled</span>';
      el.innerHTML = `<div class="empty">${esc(resp.gated_reason || 'No report yet.')}</div>`;
      return;
    }
    const r = resp.report;
    badge.innerHTML = `<span style="color:var(--yes); font-size:11px; text-transform:uppercase;">${r.resolved_count} signals</span>`;
    const suggested = r.suggested_weights || {};
    const factorRows = r.per_factor.map(f => {
      const gapColor = f.discriminates === true ? 'var(--yes)' : (f.discriminates === false && f.gap_pts < 0 ? 'var(--no)' : 'var(--muted)');
      const gapText = f.gap_pts === null ? 'not enough data' : `${f.gap_pts > 0 ? '+' : ''}${f.gap_pts.toFixed(1)}pts`;
      const current = r.current_weights[f.factor];
      const suggest = suggested[f.factor];
      return `<tr>
        <td>${esc(f.factor)}</td>
        <td style="color:${gapColor};">${gapText}</td>
        <td>${f.discriminates === null ? '—' : (f.discriminates ? 'yes' : 'no')}</td>
        <td>${current !== undefined ? current.toFixed(2) : '—'}</td>
        <td>${suggest !== undefined ? suggest.toFixed(2) : '—'}</td>
      </tr>`;
    }).join('');
    const bandsRows = (r.confidence_calibration || []).map(b => `<tr>
      <td>${esc(b.band)}</td><td>${b.n}</td><td>${b.predicted_pct.toFixed(1)}%</td>
      <td>${b.observed_win_rate_pct.toFixed(1)}%</td>
      <td style="color:${Math.abs(b.gap_pts) <= 10 ? 'var(--yes)' : 'var(--no)'};">${b.gap_pts > 0 ? '+' : ''}${b.gap_pts.toFixed(1)}pts</td>
    </tr>`).join('');
    el.innerHTML = `
      <div class="stat-cards" style="margin-bottom:14px;">
        <div class="cell"><div class="lbl">Resolved Signals</div><div class="val">${r.resolved_count}</div></div>
        <div class="cell"><div class="lbl">Overall Win Rate</div><div class="val">${r.overall_win_rate.toFixed(1)}%</div></div>
        <div class="cell"><div class="lbl">Confidence</div><div class="val">${esc(r.confidence_label)}</div></div>
      </div>
      <table class="positions-table" style="margin-bottom:14px;">
        <thead><tr><th>Factor</th><th>High−Low Gap</th><th>Discriminates</th><th>Current Weight</th><th>Suggested Weight</th></tr></thead>
        <tbody>${factorRows}</tbody>
      </table>
      ${Object.keys(suggested).length ? `<div style="margin-bottom:14px;">
        <button onclick="applyCalibrationSuggestion(this)">Apply suggested weights</button>
        <span style="font-size:11px; color:var(--muted); margin-left:8px;">Writes the Suggested Weight column above into whale_confidence_weights right now - the only other way these numbers reach the live config is the auto-apply toggle above, which only checks once per confidence_calibration.snapshot_interval_sec and then waits auto_apply_cooldown_sec between real applies.</span>
      </div>` : ''}
      ${bandsRows ? `<div style="font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:6px;">Confidence bands (predicted vs. observed)</div>
      <table class="positions-table">
        <thead><tr><th>Band</th><th>n</th><th>Predicted</th><th>Observed</th><th>Gap</th></tr></thead>
        <tbody>${bandsRows}</tbody>
      </table>` : ''}
    `;
  } catch (e) {
    badge.innerHTML = '';
    el.innerHTML = '<div class="empty">Failed to load calibration report.</div>';
  }
}

// Calibration history (services/calibration_history.py, Gap 6 of
// docs/config-tuning-data-gaps-2026-08-10.md) - a trend line the live
// report above can't show on its own: is overall win rate / a given
// factor's discrimination gap improving as more data (and retuned
// weights) accumulate, or stuck? One row is recorded automatically per
// confidence_calibration.snapshot_interval_sec (default 6h) - this panel
// stays empty until at least two snapshots exist.
async function loadCalibrationHistory() {
  const el = $('calibration-history');
  try {
    const resp = await fetchJSON('/api/confidence-calibration/history?limit=30');
    const snaps = resp.snapshots || [];
    if (snaps.length < 2) {
      el.innerHTML = '';
      $('calibration-history-chart').innerHTML = '';
      return;
    }
    const rows = snaps.map(s => {
      const when = new Date(s.recorded_at * 1000).toLocaleString();
      const depth = s.per_factor.depth_factor;
      const depthText = depth && depth.gap_pts !== null ? `${depth.gap_pts > 0 ? '+' : ''}${depth.gap_pts.toFixed(1)}pts` : '—';
      return `<tr>
        <td>${esc(when)}</td>
        <td>${s.resolved_count}</td>
        <td>${s.overall_win_rate_pct !== null ? s.overall_win_rate_pct.toFixed(1) + '%' : '—'}</td>
        <td>${depthText}</td>
      </tr>`;
    }).join('');
    el.innerHTML = `
      <div style="font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:0.05em; margin:14px 0 6px;">History (one snapshot per confidence_calibration.snapshot_interval_sec)</div>
      <table class="positions-table">
        <thead><tr><th>Recorded</th><th>Resolved n</th><th>Overall Win Rate</th><th>depth_factor Gap</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
    // Graph view (direct request 2026-08-10: "useful graph views") - is
    // calibration improving as more data accumulates, or stuck? snaps is
    // newest-first (services/calibration_history.py's own order); the
    // chart needs chronological (oldest-first) left-to-right.
    renderEquityChart(
      [...snaps].reverse(), 50, 'overall_win_rate_pct', 'coin-flip baseline', 'calibration-history-chart',
      {emptyMessage: 'Not enough calibration snapshots yet for a trend chart.', valueFormatter: v => `${v.toFixed(1)}%`},
    );
  } catch (e) {
    el.innerHTML = '';
  }
}

// Cross-Strategy Comparison (services/cross_strategy.py) - read-only,
// always safe to call.
async function loadCrossStrategyComparison() {
  const el = $('cross-strategy-comparison');
  try {
    const resp = await fetchJSON('/api/cross-strategy/comparison');
    const wf = resp.aggregate.whale_follow, mn = resp.aggregate.market_native;
    const cell = (label, wfVal, mnVal) => `
      <tr><td>${esc(label)}</td><td>${wfVal}</td><td>${mnVal}</td></tr>`;
    const wrColor = v => v === null ? 'inherit' : (v >= 50 ? 'var(--yes)' : 'var(--no)');
    let html = `
      <table class="positions-table" style="margin-bottom:14px;">
        <thead><tr><th></th><th>Whale-Follow</th><th>Market-Native</th></tr></thead>
        <tbody>
          ${cell('Closed positions', wf.total_closed, mn.total_closed)}
          <tr><td>Win rate</td>
            <td style="color:${wrColor(wf.win_rate_pct)};">${wf.win_rate_pct !== null ? wf.win_rate_pct.toFixed(1) + '%' : '—'}</td>
            <td style="color:${wrColor(mn.win_rate_pct)};">${mn.win_rate_pct !== null ? mn.win_rate_pct.toFixed(1) + '%' : '—'}</td>
          </tr>
          ${cell('Total realized P&L', fmt(wf.total_realized_pnl ?? 0), fmt(mn.total_realized_pnl ?? 0))}
          ${cell('Avg hold time', historyDurationHTML(wf.avg_hold_sec), historyDurationHTML(mn.avg_hold_sec))}
        </tbody>
      </table>
    `;
    const overlap = resp.ticker_overlap || [];
    if (overlap.length) {
      const rows = overlap.map(o => `<tr>
        <td>${esc(seriesLabel(o.ticker))}</td>
        <td>${esc(o.whale_side)} ${o.whale_won ? '✅' : '❌'} (${fmt(o.whale_realized_pnl ?? 0)})</td>
        <td>${esc(o.market_side)} ${o.market_won ? '✅' : '❌'} (${fmt(o.market_realized_pnl ?? 0)})</td>
        <td style="color:${o.agreed ? 'var(--yes)' : 'var(--no)'};">${o.agreed ? 'agreed' : 'disagreed'}</td>
      </tr>`).join('');
      html += `
        <div style="font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:6px;">Same-ticker overlap — both strategies traded this market independently</div>
        <table class="positions-table">
          <thead><tr><th>Ticker</th><th>Whale-Follow</th><th>Market-Native</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    } else {
      html += '<div class="empty">No ticker overlap yet — the two strategies haven\'t independently traded the same market.</div>';
    }
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = '<div class="empty">Failed to load cross-strategy comparison.</div>';
  }
}

const DAY_OF_WEEK_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

// Regime Segmentation (services/regime_analytics.py) - read-only, always
// safe to call. Whale-follow only (see the panel's own HTML comment for why).
async function loadRegimeSegmentation() {
  const el = $('regime-segmentation');
  try {
    const [byHour, byDow, bySeries, bySubcategory, byCategory] = await Promise.all([
      fetchJSON('/api/regime/by-hour?strategy=whale_follow'),
      fetchJSON('/api/regime/by-day-of-week?strategy=whale_follow'),
      fetchJSON('/api/regime/by-series?strategy=whale_follow'),
      fetchJSON('/api/regime/by-subcategory?strategy=whale_follow'),
      fetchJSON('/api/regime/by-category?strategy=whale_follow'),
    ]);
    const wrColor = v => v === null || v === undefined ? 'var(--muted)' : (v >= 50 ? 'var(--yes)' : 'var(--no)');
    // Win rate alone can't distinguish a real edge from the "high win
    // rate, thin edge" trap CLAUDE.md already documents once (a segment
    // can win 80%+ of the time and still lose money if wins are small and
    // losses are large) - 2026-08-16 direct request: "not just winrate
    // either, but whatever else would be statistically useful". avg P&L/
    // trade catches that trap directly; the margin-of-error suffix on win
    // rate (same stats_power math services/advisory_engine.py already
    // gates real suggestions on, not a separate/simplified number) is
    // "how much to trust this number given the sample size" made visible
    // instead of implicit.
    const pnlColor = v => v === null || v === undefined ? 'var(--muted)' : (v >= 0 ? 'var(--yes)' : 'var(--no)');
    const bucketRows = (buckets, labelFn) => buckets.map(b => `<tr>
      <td>${esc(labelFn(b))}</td>
      <td>${b.total_closed}</td>
      <td style="color:${wrColor(b.win_rate_pct)};" title="${b.win_rate_margin_pts != null ? `95% confidence interval: +/-${b.win_rate_margin_pts}pts (${b.confidence_label} confidence, n=${b.total_closed})` : ''}">${b.win_rate_pct !== null ? b.win_rate_pct.toFixed(1) + '%' : '—'}${b.win_rate_margin_pts != null ? ` <span style="color:var(--muted); font-size:10px;">±${b.win_rate_margin_pts}</span>` : ''}</td>
      <td style="color:${pnlColor(b.avg_realized_pnl)};">${b.avg_realized_pnl != null ? fmt(b.avg_realized_pnl) : '—'}</td>
      <td style="color:${pnlColor(b.total_realized_pnl)};">${b.total_realized_pnl != null ? fmt(b.total_realized_pnl) : '—'}</td>
    </tr>`).join('');
    const statHeaders = '<th>Closed</th><th>Win Rate</th><th title="Average realized P&amp;L per closed trade - catches a segment that wins often but small and loses rarely but big">Avg P&amp;L</th><th>Total P&amp;L</th>';
    const hourRows = bucketRows(byHour.buckets || [], b => `${String(b.hour_utc).padStart(2, '0')}:00 UTC`);
    const dowRows = bucketRows(byDow.buckets || [], b => DAY_OF_WEEK_LABELS[b.day_of_week]);
    // Finest to coarsest (2026-08-16 direct request: series -> subcategory
    // -> category, the same fallback order services/advisory_engine.py's
    // _series_conditional_recommendations now uses for real suggestions -
    // this panel is that same evidence, made visible) - series first since
    // it's the tier that's always populated (pure function of the ticker,
    // no trade_category.py dependency), subcategory/category naturally
    // thin out for non-Sports trades.
    const seriesRows = bucketRows(bySeries.buckets || [], b => b.series);
    const subcategoryRows = bucketRows(bySubcategory.buckets || [], b => b.subcategory);
    const categoryRows = bucketRows(byCategory.buckets || [], b => b.category);
    el.innerHTML = `
      ${hourRows ? `<div style="font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:6px;">By hour of day</div>
      <table class="positions-table" style="margin-bottom:14px;">
        <thead><tr><th>Hour</th>${statHeaders}</tr></thead>
        <tbody>${hourRows}</tbody>
      </table>` : '<div class="empty">Not enough closed positions yet to segment by hour.</div>'}
      ${dowRows ? `<div style="font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:6px;">By day of week</div>
      <table class="positions-table" style="margin-bottom:14px;">
        <thead><tr><th>Day</th>${statHeaders}</tr></thead>
        <tbody>${dowRows}</tbody>
      </table>` : ''}
      ${seriesRows ? `<div style="font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:6px;">By series</div>
      <table class="positions-table" style="margin-bottom:14px;">
        <thead><tr><th>Series</th>${statHeaders}</tr></thead>
        <tbody>${seriesRows}</tbody>
      </table>` : '<div class="empty">Not enough closed positions yet to segment by series.</div>'}
      ${subcategoryRows ? `<div style="font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:6px;">By subcategory (sport)</div>
      <table class="positions-table" style="margin-bottom:14px;">
        <thead><tr><th>Subcategory</th>${statHeaders}</tr></thead>
        <tbody>${subcategoryRows}</tbody>
      </table>` : ''}
      ${categoryRows ? `<div style="font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:6px;">By category</div>
      <table class="positions-table">
        <thead><tr><th>Category</th>${statHeaders}</tr></thead>
        <tbody>${categoryRows}</tbody>
      </table>` : '<div class="empty" style="margin-top:8px;">No category data yet — only trades placed since this shipped have a recorded category (services/trade_category.py).</div>'}
    `;
    // Graph view (direct request 2026-08-10: "useful graph views") - real
    // hour-to-hour variation is much easier to see as a curve than
    // scanning a ~19-row table.
    renderEquityChart(
      byHour.buckets || [], 50, 'win_rate_pct', 'coin-flip baseline', 'regime-hour-chart',
      {emptyMessage: 'Not enough closed positions yet for an hour-of-day chart.', valueFormatter: v => `${v.toFixed(1)}%`},
    );
  } catch (e) {
    el.innerHTML = '<div class="empty">Failed to load regime segmentation.</div>';
  }
}

// Rejected Candidates (services/candidate_log.py) - read-only, always safe
// to call (no enabled flag - this collects passively from every gate check).
async function loadCandidateLogSummary() {
  const el = $('candidate-log-summary');
  try {
    const resp = await fetchJSON('/api/candidate-log/summary');
    const gates = resp.gates || [];
    if (!gates.length) {
      el.innerHTML = '<div class="empty">No rejected candidates logged yet.</div>';
      return;
    }
    const rows = gates.map(g => {
      const wr = g.hypothetical_win_rate;
      const wrText = wr === null ? '—' : `${wr.toFixed(1)}% (n=${g.hypothetical_win_rate_n})`;
      const wrColor = wr === null ? 'var(--muted)' : (wr >= 50 ? 'var(--yes)' : 'var(--no)');
      return `<tr>
        <td>${esc(g.strategy)}</td>
        <td><span class="config-path" style="margin:0;">${esc(g.gate_name)}</span></td>
        <td>${g.rejected_count}</td>
        <td>${g.resolved_count}</td>
        <td style="color:${wrColor};">${wrText}</td>
      </tr>`;
    }).join('');
    el.innerHTML = `
      <table class="positions-table">
        <thead><tr><th>Strategy</th><th>Gate</th><th>Rejected</th><th>Resolved</th><th>Hypothetical Win Rate</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  } catch (e) {
    el.innerHTML = '<div class="empty">Failed to load rejected-candidate summary.</div>';
  }
}

// Backtest Sweeps (services/backtest.py) - read-only, always safe to call.
function _sweepTableHTML(title, configPath, current, currentKey, rows, xKey, xLabel) {
  const trs = rows.map(r => {
    const isCurrent = r[xKey] === current;
    const wr = r.win_rate_pct;
    const wrText = wr === null ? '—' : `${wr.toFixed(1)}%`;
    const style = isCurrent ? 'font-weight:600; background:rgba(255,255,255,0.04);' : '';
    // Click-to-apply (direct request 2026-08-10: "i want click to apply
    // buttons throughout... where suggestions are made so i dont have to
    // switch to the config tab") - the current/live row gets no button,
    // applying its own value is a no-op.
    const applyBtn = isCurrent ? '' :
      `<button style="font-size:10px; padding:1px 6px;" onclick="applyBacktestValue('${esc(configPath)}', ${r[xKey]}, this)">Apply</button>`;
    return `<tr style="${style}">
      <td>${r[xKey]}${isCurrent ? ' <span style="color:var(--whale); font-size:10px; text-transform:uppercase;">live</span>' : ''}</td>
      <td>${r.n}</td>
      <td>${wrText}</td>
      <td>${applyBtn}</td>
    </tr>`;
  }).join('');
  return `<div style="font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:0.05em; margin:10px 0 6px;">${esc(title)}</div>
    <table class="positions-table">
      <thead><tr><th>${esc(xLabel)}</th><th>n</th><th>Win Rate</th><th></th></tr></thead>
      <tbody>${trs}</tbody>
    </table>`;
}

async function applyBacktestValue(configPath, value, btn) {
  if (!confirm(`Set ${configPath} to ${value}? This takes effect on the next trading-loop tick. This is a raw backtest sweep value, not a hedged Advisory recommendation - the stateless replay it's based on doesn't account for cooldowns/concentration limits/other config fields changing at the same time (see docs/config-tuning-data-gaps-2026-08-10.md Gap 2).`)) return;
  btn.disabled = true;
  const [section, field] = configPath.split('.');
  try {
    await fetchJSON('/api/config', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({patch: {[section]: {[field]: value}}}),
    });
    btn.textContent = 'Applied ✓';
    loadConfig();
    setTimeout(loadBacktestSweeps, 800);
    setTimeout(loadChangeHistory, 800);
  } catch (e) {
    btn.disabled = false;
  }
}

async function loadBacktestSweeps() {
  const el = $('backtest-sweeps');
  try {
    const [et, wr] = await Promise.all([
      fetchJSON('/api/backtest/entry-threshold'),
      fetchJSON('/api/backtest/min-whale-winrate'),
    ]);
    const etRows = (et.sweep || []).filter(r => r.n > 0);
    const wrRows = (wr.sweep || []).filter(r => r.n > 0);
    el.innerHTML = (etRows.length ? _sweepTableHTML(
      'strategy.entry_threshold', 'strategy.entry_threshold', et.current_threshold, 'threshold', etRows, 'threshold', 'Threshold',
    ) : '<div class="empty">Not enough resolved signals yet for an entry-threshold sweep.</div>')
      + (wrRows.length ? _sweepTableHTML(
        'strategy.min_whale_winrate_pct', 'strategy.min_whale_winrate_pct', wr.current_floor, 'floor', wrRows, 'floor', 'Floor',
      ) : '<div class="empty">Not enough resolved signals yet for a win-rate-floor sweep.</div>');
    // Graph view (direct request 2026-08-10: "useful graph views") - the
    // shape of this curve is the actual finding (a real U-shape was found
    // live this session), much clearer visually than scanning a table of
    // 20 rows for it.
    renderEquityChart(
      etRows, 50, 'win_rate_pct', 'coin-flip baseline', 'backtest-entry-threshold-chart',
      {emptyMessage: 'Not enough resolved signals yet for a sweep chart.', valueFormatter: v => `${v.toFixed(1)}%`},
    );
  } catch (e) {
    el.innerHTML = '<div class="empty">Failed to load backtest sweeps.</div>';
  }
}

// Market-Native tab (direct request 2026-08-10: "the market-native strategy
// seems to have stalled, and i think itd be good to have its own tab now") -
// GET /api/market-strategy/state is the single source for everything on this
// tab: enabled/halt status, bankroll/equity, positions, trade log (real
// close outcomes via the same _enrich_recent_trades() backend fix the
// Portfolio Trade Log got), and its own decision feed (previously computed
// every tick and discarded - see main.py's trading loop).
async function loadMarketNativeState() {
  try {
    const resp = await fetchJSON('/api/market-strategy/state');
    Object.assign(marketTitles, resp.market_titles || {});

    const banner = $('market-native-halt-banner');
    if (resp.risk && resp.risk.halted) {
      banner.innerHTML = `<div class="panel" style="border:1px solid var(--no); border-radius:8px; padding:12px 16px; margin-bottom:16px;">
        <b style="color:var(--no);">🛑 Kill switch tripped:</b> ${esc(resp.risk.halt_reason || '')}
        <button style="margin-left:12px;" onclick="resumeMarketNative(this)">Resume trading</button>
      </div>`;
    } else {
      banner.innerHTML = '';
    }

    const b = resp.broker;
    const cards = $('market-native-stats').children;
    let statusHtml;
    if (!resp.enabled) statusHtml = '<span style="color:var(--muted);">Disabled</span>';
    else if (resp.risk && resp.risk.halted) statusHtml = '<span style="color:var(--no);">Halted</span>';
    else statusHtml = '<span style="color:var(--yes);">Active</span>';
    cards[0].querySelector('.val').innerHTML = statusHtml;
    cards[1].querySelector('.val').textContent = fmt(b.bankroll);
    cards[2].querySelector('.val').textContent = fmt(b.equity);
    const unrealizedColor = b.unrealized_pnl >= 0 ? 'var(--yes)' : 'var(--no)';
    cards[3].querySelector('.val').innerHTML = `<span style="color:${unrealizedColor};">${fmt(b.unrealized_pnl)}</span>`;

    const posEl = $('market-native-positions');
    $('market-native-position-count').textContent = b.positions.length ? `(${b.positions.length})` : '';
    posEl.innerHTML = !b.positions.length ? '<div class="empty">No open positions</div>' : b.positions.map(p => {
      const label = marketLabel(p.ticker);
      const current = (resp.latest_prices || {})[p.ticker] ?? p.entry_price;
      const direction = p.side === 'yes' ? 1 : -1;
      const pnl = direction * (current - p.entry_price) * p.size;
      return `<div class="position-row" style="cursor:pointer;" title="${esc(label.full)} — click to view full market detail" onclick="openMarketDetail('${esc(p.ticker)}', '')">
        <div class="name">${esc(label.short)} <span class="side-tag ${p.side}">${p.side}</span>
          ${contextLineHTML(p.ticker, p.side)}
        </div>
        <div class="nums">
          <span>${p.size.toLocaleString()} @ ${((p.side === 'yes' ? p.entry_price : 1 - p.entry_price)*100).toFixed(0)}¢</span>
          <span style="color:${pnl >= 0 ? 'var(--yes)' : 'var(--no)'};">${fmt(pnl)}</span>
        </div>
      </div>`;
    }).join('');

    const tradesEl = $('market-native-trades');
    const trades = b.recent_trades || [];
    tradesEl.innerHTML = !trades.length ? '<div class="empty">No trades placed yet</div>' : trades.map(t => {
      const label = marketLabel(t.ticker);
      const when = new Date(t.timestamp * 1000).toLocaleString();
      const isClose = t.close_type !== undefined && t.close_type !== null && t.close_type !== '';
      const resultHtml = isClose
        ? `<span style="color:${t.won ? 'var(--yes)' : 'var(--no)'}; font-weight:600;">${t.won ? '✅ Won' : '❌ Lost'} ${fmt(t.realized_pnl ?? 0)}</span>
           <span style="color:var(--muted); font-size:11px;">${esc(HISTORY_CLOSE_TYPE_LABELS[t.close_type] || t.close_type)}</span>`
        : '<span style="color:var(--muted);">open</span>';
      return `<div class="trade-row" style="cursor:pointer;" title="${esc(label.full)} — click to view full market detail" onclick="openMarketDetail('${esc(t.ticker)}', '')">
        <div class="name">${esc(label.short)} <span class="side-tag ${t.side}">${t.side}</span>
          ${contextLineHTML(t.ticker, t.side)}
        </div>
        <div class="nums">
          <span>${t.size.toLocaleString()} @ ${((t.side === 'yes' ? t.price : 1 - t.price)*100).toFixed(0)}¢</span>
          <span style="color:var(--muted);">${when}</span>
          ${resultHtml}
        </div>
      </div>`;
    }).join('');

    const decEl = $('market-native-decisions');
    const decisions = resp.decision_feed || [];
    decEl.innerHTML = !decisions.length ? '<div class="empty">No decisions yet</div>' : decisions.map(d => {
      const label = marketLabel(d.ticker);
      const trade = d.trade || {};
      const when = trade.timestamp ? new Date(trade.timestamp * 1000).toLocaleString() : '';
      return `<div class="decision-row" style="cursor:pointer;" title="${esc(label.full)} — click to view full market detail" onclick="openMarketDetail('${esc(d.ticker)}', '')">
        <div class="name">${esc(label.short)} <span style="color:var(--muted); font-size:11px; text-transform:uppercase;">${esc(d.action)}</span>
          ${trade.side ? contextLineHTML(d.ticker, trade.side) : ''}
        </div>
        <div class="nums"><span style="color:var(--muted); font-size:12px;">${esc(trade.reason || d.reason || '')}</span> <span style="color:var(--muted);">${esc(when)}</span></div>
      </div>`;
    }).join('');
  } catch (e) {
    console.error('loadMarketNativeState failed', e);
  }
}

async function resumeMarketNative(btn) {
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = 'Resuming…';
  try {
    await fetchJSON('/api/market-risk/resume', {method: 'POST'});
    loadMarketNativeState();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = original;
  }
}

// Market Analyst (docs/prediction-market-strategy-alignment-plan.md Part 3) -
// same two-fetch shape as loadAdvisory above (a status call that's always
// safe regardless of enabled, plus the actual data), but distinguishes a
// third state Advisory doesn't have: enabled with no ANTHROPIC_API_KEY set,
// which would otherwise look identical to "enabled and just hasn't analyzed
// anything yet" - worth a different, more actionable message.
async function loadMarketAnalyst() {
  const badge = $('market-analyst-status-badge');
  const el = $('market-analyst-analyses');
  try {
    const status = await fetchJSON('/api/market-analyst/status');
    const cards = $('market-analyst-summary-cards').children;
    cards[0].querySelector('.val').textContent = status.total_analyses ?? '—';
    cards[1].querySelector('.val').textContent = status.resolved ?? '—';
    cards[2].querySelector('.val').textContent = status.hit_rate_pct != null ? `${status.hit_rate_pct}%` : '—';
    cards[3].querySelector('.val').textContent = status.brier_score != null ? status.brier_score.toFixed(3) : '—';

    if (!status.enabled) {
      badge.innerHTML = '<span style="color:var(--muted); font-size:11px; text-transform:uppercase;">disabled</span>';
      el.innerHTML = '<div class="empty">Market Analyst is disabled — enable it in Config → Market Analyst (AI).</div>';
      return;
    }
    if (!status.api_key_configured) {
      badge.innerHTML = '<span style="color:var(--no); font-size:11px; text-transform:uppercase;">no API key</span>';
      el.innerHTML = '<div class="empty">Enabled, but ANTHROPIC_API_KEY isn\'t set in .env — nothing will run until it is.</div>';
      return;
    }
    badge.innerHTML = '<span style="color:var(--yes); font-size:11px; text-transform:uppercase;">enabled</span>';

    const resp = await fetchJSON('/api/market-analyst/analyses?limit=25');
    const rows = resp.rows || [];
    if (!rows.length) {
      el.innerHTML = '<div class="empty">No analyses yet — click "Analyze" on a market\'s detail view to '
        + 'spend a real API call on it (nothing runs automatically in the background).</div>';
      return;
    }
    el.innerHTML = rows.map(r => {
      const label = marketLabel(r.ticker);
      const lean = r.estimated_probability >= 0.5 ? 'YES' : 'NO';
      const leanPct = Math.round(r.estimated_probability >= 0.5 ? r.estimated_probability * 100 : (1 - r.estimated_probability) * 100);
      const marketPct = Math.round(r.market_price * 100);
      let outcomeHtml = '<span style="color:var(--muted); font-size:11px; text-transform:uppercase;">pending</span>';
      if (r.resolved) {
        outcomeHtml = r.correct
          ? '<span style="color:var(--yes); font-size:11px; text-transform:uppercase;">✓ correct</span>'
          : '<span style="color:var(--no); font-size:11px; text-transform:uppercase;">✗ missed</span>';
      }
      return `
        <div class="signal-card">
          <div class="top-row">
            <span>${esc(label.short)}</span>
            ${outcomeHtml}
          </div>
          ${contextLineHTML(r.ticker, 'yes')}
          <div style="margin:4px 0;">
            Model: <b class="side-tag ${lean.toLowerCase()}">${lean} ${leanPct}%</b>
            <span style="color:var(--muted);"> · Market was pricing YES at ${marketPct}% ·
            confidence ${Math.round(r.llm_confidence * 100)}%</span>
          </div>
          <div style="color:var(--muted); font-size:13px;">${esc(r.reasoning)}</div>
        </div>
      `;
    }).join('');
  } catch (e) {
    badge.innerHTML = '';
    el.innerHTML = '<div class="empty">Failed to load Market Analyst data.</div>';
  }
}

// Direct request: the log/history the user wanted - every series ever
// evaluated, not just what's on the current watchlist. The persisted
// series_status table itself doubles as this log (see services/
// series_evaluator.py's overview()) - no separate table needed.
async function loadSeriesEvaluator() {
  const badge = $('series-evaluator-status-badge');
  const el = $('series-evaluator-list');
  try {
    const status = await fetchJSON('/api/series-evaluator/status');
    badge.innerHTML = status.enabled
      ? '<span style="color:var(--yes); font-size:11px; text-transform:uppercase;">enabled</span>'
      : '<span style="color:var(--muted); font-size:11px; text-transform:uppercase;">disabled</span>';
    const rows = status.series || [];
    if (!rows.length) {
      el.innerHTML = '<div class="empty">No series evaluated yet'
        + (status.enabled ? ' — observing real trade activity as it comes in.' : ' — enable it in Config → Series Evaluator to start judging series (trade activity is still being recorded either way).')
        + '</div>';
      return;
    }
    // Sort so the most actionable rows (rejected, then observing) surface
    // above the settled ones (approved) - a human checking this panel most
    // likely wants to know what's currently backed off, not re-confirm
    // what's already been approved.
    const order = { rejected: 0, observing: 1, approved: 2 };
    rows.sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9) || a.series.localeCompare(b.series));
    const now = Date.now() / 1000;
    el.innerHTML = rows.map(r => {
      let statusHtml, canReEvaluate = false;
      if (r.status === 'observing') {
        statusHtml = `<span style="color:var(--muted);">⏳ observing (${r.trades_observed} trade${r.trades_observed === 1 ? '' : 's'} seen)</span>`;
      } else if (r.status === 'approved') {
        statusHtml = '<span style="color:var(--yes);">✅ approved</span>';
        canReEvaluate = true;
      } else {
        const untilStr = r.next_eligible_at ? new Date(r.next_eligible_at * 1000).toLocaleString() : 'unknown';
        const stillWaiting = r.next_eligible_at && r.next_eligible_at > now;
        statusHtml = stillWaiting
          ? `<span style="color:var(--no);">🚫 backed off until ${esc(untilStr)} (strike ${r.strike_count})</span>`
          : `<span style="color:var(--no);">🚫 rejected (strike ${r.strike_count}) — eligible for re-admission now</span>`;
        canReEvaluate = true;
      }
      const reEvalBtn = canReEvaluate
        ? `<button style="font-size:11px; padding:2px 8px;" onclick="reEvaluateSeries('${esc(r.series)}', this)">Re-evaluate</button>`
        : '';
      const analyzeBtn = `<button style="font-size:11px; padding:2px 8px;" onclick="analyzeSeries('${esc(r.series)}', this)">🔎 Analyze</button>`;
      // Gap 4 (docs/config-tuning-data-gaps-2026-08-10.md) - series_evaluator's
      // own qualifying-rate verdict and strategy.min_whale_winrate_pct's real
      // win-rate floor are two independent mechanisms that had never been
      // cross-checked before this - flagging it directly when they disagree
      // (e.g. approved for the watchlist but still below the win-rate floor,
      // so real trades on it are being filtered out by the *other* gate anyway).
      let winRateHtml = '';
      if (r.whale_win_rate !== null) {
        const wrColor = r.below_winrate_floor ? 'var(--no)' : 'var(--muted)';
        // Gap 10 (docs/config-tuning-data-gaps-2026-08-10.md) - the real
        // margin of error, not just a bare win rate - a series barely
        // under the floor with a wide margin (thin n) reads differently
        // than one clearly under it with a tight one.
        const margin = r.whale_win_rate_margin_pts;
        const marginText = margin !== null && isFinite(margin) ? ` ±${margin.toFixed(1)}pts` : '';
        winRateHtml = `<span style="color:${wrColor}; font-size:11px;" title="whale win rate over ${r.whale_resolved} resolved signals, 95% CI margin of error">
          ${r.whale_win_rate.toFixed(1)}%${marginText} win rate${r.below_winrate_floor ? ' ⚠️ below min_whale_winrate_pct floor' : ''}
        </span>`;
      }
      return `<div class="position-row">
        <div class="name">${esc(seriesLabel(r.series))} <span style="font-family:var(--mono); font-size:10px; color:var(--muted);">${esc(r.series)}</span></div>
        <div class="nums">${statusHtml} ${winRateHtml} ${reEvalBtn} ${analyzeBtn}</div>
      </div>`;
    }).join('');
  } catch (e) {
    badge.innerHTML = '';
    el.innerHTML = '<div class="empty">Failed to load Series Evaluator data.</div>';
  }
}

// Per-series agent analysis (Item 3B, 2026-08-10) - same "deliberate human
// click, not background spend" reasoning as the single-market Market
// Analyst button. Renders into #series-analysis-result, not inline per row,
// so a suggestion card can reuse the exact same layout Advisory
// Recommendations already uses instead of a second, cramped variant.
async function analyzeSeries(series, btn) {
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = 'Analyzing…';
  const el = $('series-analysis-result');
  try {
    const result = await fetchJSON('/api/market-analyst/series/analyze', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({series}),
    });
    if (!result.ok) {
      el.innerHTML = `<div class="panel-title">Series Analysis: ${esc(seriesLabel(series))}</div>
        <div class="empty">${esc(result.reason)}</div>`;
      return;
    }
    const suggestionsHTML = result.suggestions.length
      ? result.suggestions.map(s => renderSuggestionCard(
          s, `applySeriesSuggestion('${esc(result.analysis_id)}', '${esc(s.id)}', this)`,
        )).join('')
      : '<div class="empty">No changes suggested — this series looks fine as-is.</div>';
    el.innerHTML = `<div class="panel-title">Series Analysis: ${esc(seriesLabel(series))} <span style="font-family:var(--mono); font-size:10px; color:var(--muted);">${esc(series)}</span></div>
      <div class="suggestion-card" style="margin-bottom:12px;">${esc(result.summary)}</div>
      ${suggestionsHTML}
      <div style="margin-bottom:24px;"></div>`;
    el.querySelectorAll('[data-jump-path]').forEach(chip => {
      chip.addEventListener('click', () => jumpToConfigSetting(chip.dataset.jumpPath));
    });
  } catch (e) {
    el.innerHTML = `<div class="panel-title">Series Analysis: ${esc(seriesLabel(series))}</div><div class="empty">Failed to analyze this series.</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

async function applySeriesSuggestion(analysisId, suggestionId, btn) {
  // No confirm() dialog - see applyAdvisoryRecommendation's own comment.
  btn.disabled = true;
  try {
    await fetchJSON('/api/market-analyst/series/apply', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({analysis_id: analysisId, suggestion_id: suggestionId}),
    });
    btn.textContent = 'Applied ✓';
    loadConfig();
    setTimeout(loadChangeHistory, 800);
    setTimeout(loadSeriesEvaluator, 800);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Apply failed — retry';
  }
}

// "Feed the Analyst" full-spectrum scan (Item 3C, 2026-08-10) - a single
// deep-scan call, confirm-gated since this prompt is materially bigger/
// costlier than the single-market or per-series modes and no cost/latency
// numbers exist anywhere for this agent yet. Same suggestion-card/apply
// pattern as analyzeSeries()/applySeriesSuggestion() above, just against
// the full-spectrum-specific routes and result area.
async function feedTheAnalyst(btn) {
  if (!confirm(
    'Send a full-spectrum snapshot to the AI model? This includes your live config (all sections), ' +
    'aggregate trade performance, whale-signal accuracy, recent config-change history, and the ' +
    "busiest series' own track record - one real API call, materially larger than a single-market or " +
    'per-series analysis.'
  )) return;
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = 'Analyzing…';
  const el = $('full-spectrum-result');
  try {
    const result = await fetchJSON('/api/market-analyst/full-spectrum/analyze', {method: 'POST'});
    if (!result.ok) {
      el.innerHTML = `<div class="empty">${esc(result.reason)}</div>`;
      return;
    }
    const suggestionsHTML = result.suggestions.length
      ? result.suggestions.map(s => renderSuggestionCard(
          s, `applyFullSpectrumSuggestion('${esc(result.analysis_id)}', '${esc(s.id)}', this)`,
        )).join('')
      : '<div class="empty">No changes suggested — the platform looks fine as-is.</div>';
    el.innerHTML = `<div class="suggestion-card" style="margin-bottom:12px;">${esc(result.summary)}</div>${suggestionsHTML}<div style="margin-bottom:12px;"></div>`;
    el.querySelectorAll('[data-jump-path]').forEach(chip => {
      chip.addEventListener('click', () => jumpToConfigSetting(chip.dataset.jumpPath));
    });
  } catch (e) {
    el.innerHTML = '<div class="empty">Failed to run the full-spectrum scan.</div>';
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

async function applyFullSpectrumSuggestion(analysisId, suggestionId, btn) {
  // No confirm() dialog - see applyAdvisoryRecommendation's own comment.
  // feedTheAnalyst()'s own confirm() above is a different, still-necessary
  // gate (consenting to send data to an external LLM API call) - this one
  // was purely the config-apply step, now unified with the other two.
  btn.disabled = true;
  try {
    await fetchJSON('/api/market-analyst/full-spectrum/apply', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({analysis_id: analysisId, suggestion_id: suggestionId}),
    });
    btn.textContent = 'Applied ✓';
    loadConfig();
    setTimeout(loadChangeHistory, 800);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Apply failed — retry';
  }
}

async function reEvaluateSeries(series, btn) {
  if (!confirm(`Re-evaluate ${series}? This resets its observation window and strike count to a fresh start.`)) return;
  btn.disabled = true;
  btn.textContent = 'Resetting…';
  try {
    await fetchJSON('/api/series-evaluator/reset', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({series}),
    });
    loadSeriesEvaluator();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Re-evaluate';
  }
}

export { CHANGE_SOURCE_LABEL, DAY_OF_WEEK_LABELS, _sweepTableHTML, advisoryApplyInFlight, analyzeSeries, applyAdvisoryRecommendation, applyBacktestValue, applyCalibrationSuggestion, applyFullSpectrumSuggestion, applySeriesSuggestion, calibrationApplyInFlight, feedTheAnalyst, jumpToConfigSetting, loadAdvisory, loadBacktestSweeps, loadCalibrationHistory, loadCalibrationReport, loadCandidateLogSummary, loadChangeHistory, loadCrossStrategyComparison, loadMarketAnalyst, loadMarketNativeState, loadRegimeSegmentation, loadSeriesEvaluator, reEvaluateSeries, resumeMarketNative };

// Exposed for inline HTML event handlers (onclick=/onchange=/oninput=,
// including ones built indirectly via a caller-supplied onclick-string
// parameter - see shared-utils.js's header). Every top-level function in
// this file is exposed (cheap, harmless if unused); state objects only the
// specific ones confirmed to be read/mutated directly from a handler.
window._sweepTableHTML = _sweepTableHTML;
window.analyzeSeries = analyzeSeries;
window.applyAdvisoryRecommendation = applyAdvisoryRecommendation;
window.applyBacktestValue = applyBacktestValue;
window.applyCalibrationSuggestion = applyCalibrationSuggestion;
window.applyFullSpectrumSuggestion = applyFullSpectrumSuggestion;
window.applySeriesSuggestion = applySeriesSuggestion;
window.feedTheAnalyst = feedTheAnalyst;
window.jumpToConfigSetting = jumpToConfigSetting;
window.loadAdvisory = loadAdvisory;
window.loadBacktestSweeps = loadBacktestSweeps;
window.loadCalibrationHistory = loadCalibrationHistory;
window.loadCalibrationReport = loadCalibrationReport;
window.loadCandidateLogSummary = loadCandidateLogSummary;
window.loadChangeHistory = loadChangeHistory;
window.loadCrossStrategyComparison = loadCrossStrategyComparison;
window.loadMarketAnalyst = loadMarketAnalyst;
window.loadMarketNativeState = loadMarketNativeState;
window.loadRegimeSegmentation = loadRegimeSegmentation;
window.loadSeriesEvaluator = loadSeriesEvaluator;
window.reEvaluateSeries = reEvaluateSeries;
window.resumeMarketNative = resumeMarketNative;
