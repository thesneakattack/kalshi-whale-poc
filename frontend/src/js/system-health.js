import { $, esc, fetchJSON } from './shared-utils.js';
import { refreshIntervalMs } from './trading-gate-and-connectivity.js';

// Terminal tab's "System Health" panel (QCP Task 18, docs/superpowers/
// plans/2026-08-24-quality-control-plane.md) - a read-only view of
// GET /api/quality/summary, the composite runtime-health endpoint QCP
// Tasks 9-11/16 built. Deliberately the LAST thing that landed in this
// initiative's rollout order (design spec section 22: "After APIs are
// stable") - every field this renders already existed server-side before
// this file did.
//
// Two data sources, not one: /api/quality/summary supplies status/alerts/
// faults/storage/research (nothing else exposes these), but "tick duration
// vs poll interval" reuses state.last_tick_duration_sec and
// refreshIntervalMs - both already fetched/computed by the existing
// refresh() poll and loadConfig(), per this task's own "reuse existing
// polling conventions" instruction (Step 4) rather than a second source
// for a fact already in hand.
//
// /api/quality/summary's own findings-only-when-nonzero shape (see
// services/observability/observability.py's runtime_findings - a rule
// that can't judge, or a healthy value, simply omits a finding rather than
// asserting health) means "dropped messages" and "storage/backup" below
// are derived from scanning `findings`, not a dedicated always-present
// number - CLAUDE.md's "unknown is better than fabricated" rule applies to
// this panel too, so a metric with no corresponding finding renders as 0/
// "ok", never guessed.

const STATUS_LABEL = { ok: 'HEALTHY', warning: 'WARNING', error: 'ERROR' };

function agoText(ts) {
  if (!ts) return 'never';
  const secs = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (secs < 90) return `${secs}s ago`;
  if (secs < 5400) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

function renderSystemHealth(state, quality) {
  const summaryEl = $('system-health-summary');
  const findingsEl = $('system-health-findings');
  if (!summaryEl || !findingsEl) return;  // panel not on this page (e.g. isolated E2E fixture)

  if (!quality) {
    summaryEl.innerHTML = '<div class="empty">System health unavailable</div>';
    findingsEl.innerHTML = '';
    return;
  }

  const status = quality.status || 'unknown';
  const label = STATUS_LABEL[status] || 'UNKNOWN';
  const findings = quality.findings || [];

  const pollSec = Math.round(refreshIntervalMs / 1000);
  const tickSec = state && state.last_tick_duration_sec;
  const tickText = tickSec != null ? `${tickSec.toFixed(1)}s` : '—';

  const droppedTotal = findings
    .filter(f => f.check === 'ws-dropped-messages')
    .reduce((sum, f) => sum + ((f.evidence && f.evidence.dropped_messages) || 0), 0);

  const activeAlerts = (quality.alerts && quality.alerts.active) || [];
  const faults = quality.faults || {};
  const dbs = (quality.storage && quality.storage.databases) || [];
  const storageFindings = findings.filter(f => f.check === 'storage-growth' || f.check === 'storage-integrity' || f.check === 'backup-overdue');
  const research = quality.research || {};

  summaryEl.innerHTML = `
    <div class="sh-status ${esc(status)}">● ${esc(label)}</div>
    <div class="sh-line">Tick: ${tickText} / ${pollSec}s poll</div>
    <div class="sh-line">Dropped msgs: ${droppedTotal.toLocaleString('en-US')}</div>
    <div class="sh-line">Alerts: ${activeAlerts.length} active</div>
    <div class="sh-line">Faults: ${faults.distinct_faults || 0} kind${faults.distinct_faults === 1 ? '' : 's'}${faults.total_occurrences ? ` (${faults.total_occurrences}x)` : ''}</div>
    <div class="sh-line">Storage: ${dbs.length} db${dbs.length === 1 ? '' : 's'}${storageFindings.length ? `, ${storageFindings.length} flagged` : ', ok'}</div>
    <div class="sh-line">Research: ${research.running ? 'running…' : agoText(research.last_report_at)}</div>
    <a class="sh-details-link" href="/api/quality/summary" target="_blank" rel="noopener">full report →</a>
  `;

  const topFindings = findings
    .filter(f => f.severity === 'warning' || f.severity === 'error')
    .slice(0, 5);
  findingsEl.innerHTML = topFindings.length
    ? topFindings.map(f => `<div class="sh-finding ${esc(f.severity)}">${esc(f.summary)}</div>`).join('')
    : '<div class="empty">No warnings or errors</div>';
}

async function loadSystemHealth(state) {
  const summaryEl = $('system-health-summary');
  if (!summaryEl) return;  // fetch only while the Terminal tab (the panel's container) is active
  try {
    const quality = await fetchJSON('/api/quality/summary');
    renderSystemHealth(state, quality);
  } catch (e) {
    console.error('system health fetch failed', e);
    renderSystemHealth(state, null);
  }
}

export { loadSystemHealth, renderSystemHealth };

// Exposed for inline HTML event handlers, same convention as every other
// file in this split - see shared-utils.js's header.
window.loadSystemHealth = loadSystemHealth;
window.renderSystemHealth = renderSystemHealth;
