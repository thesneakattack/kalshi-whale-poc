# Alerting module — cheat sheet

Owns: `alerting.py` (transition-based detection for the three cases
ROADMAP.md's "Path to production" section named directly — kill-switch
trip, crash, connectivity loss — plus best-effort webhook notification)
+ `routes.py` (active/history HTTP surface). New 2026-08-23, same session
as `services/backup/` — direct instruction: build detection/logging now,
the actual notification channel is a separate decision.

## Why transition-based, not level-based

An alert fires once when a condition goes bad, and resolves once when it
clears — never re-fires every tick while something stays broken. That's
deliberately different from `fault_log.py`'s own per-occurrence counting
(which _wants_ to know "how many times"); this module answers "is
something wrong right now" and "when did that start/stop," which only
makes sense as an edge, not a level. `_last_known_bad` (module-level, not
`state[...]`) tracks the previous reading purely for edge detection — it
resets on every restart, same as this app's other `_last_*`/cache
module-level dicts, and re-alerting once after a restart if something is
still bad is the safer default over silently assuming a human already
knows.

## Three cases, two different detection shapes

- **Kill switch** (`risk.halted`) and **WS connectivity**
  (`state["trade_stream_status"]`/`state["index_stream_status"]`'s own
  `connected` field) are polled once per tick via `check_and_alert(cfg)`,
  called from `main.py`'s trading loop next to `_maybe_run_backup`/
  `_maybe_scan_catalog_batch`. Cheap — a handful of dict/attribute reads,
  one DB write only on an actual transition.
- **Crash** is *not* polled here at all — it's a direct hook inside
  `services/task_supervisor.py`'s own exception handler, right next to
  the existing `fault_log.record(...)` call, gated to `restart=True`
  tasks only (`trading_loop`/`trade_stream`/`index_stream` — the loops
  that must never just stay dead). That's the exact moment a crash
  happens and already carries the component/operation/exception this
  module needs; polling for it here would just be a slower, less precise
  version of the same information task_supervisor already has in hand.
  A `restart=False` one-shot background task (a catalog scan, a discovery
  refresh) crashing already degrades gracefully and retries next cycle on
  its own — not alarm-worthy at the "wake someone up" level, so those are
  deliberately excluded.

## Notification delivery — generic webhook, opt-in, unset by default

`alerting.webhook_url` (`config/settings.yaml`'s `alerting` section) —
same "ships fully built, opt-in" precedent as `risk.max_total_exposure_pct`
and friends. A no-op until set. When set, every alert (both the "went bad"
and the "recovered" transition) POSTs a JSON body shaped for broad
compatibility without this module needing to know the destination:

```json
{"text": "[CRITICAL] kill_switch: Kill switch tripped: ...",
 "category": "kill_switch", "severity": "critical",
 "message": "...", "triggered_at": 1787500000.0}
```

- **Slack** / **Discord** incoming webhooks both read the top-level
  `text`/`content`-shaped field set — Slack matches `text` directly;
  Discord wants `content` instead, so a Discord destination needs a thin
  adapter (or a service like Slack-compatible relay) in front of this URL,
  not a code change here.
- **ntfy.sh** accepts a plain POST body as the notification text, or a
  JSON payload per its own docs — this shape works with its JSON mode.
- **A custom endpoint** gets the full structured fields
  (`category`/`severity`/`message`/`triggered_at`) to parse programmatically,
  the `text` field is just a convenience for services that only look at
  that.
- **PagerDuty**'s Events API v2 needs a different envelope
  (`routing_key`/`event_action`/`payload`) — not compatible with this
  payload as-is; would need its own adapter if ever wanted.

Dispatch is always fire-and-forget (`task_supervisor.supervise`,
`restart=False` — a failed notification isn't worth retrying forever) so
a slow or unreachable webhook can never stall the trading loop, and always
best-effort: a failed delivery still leaves the alert recorded and visible
via `GET /api/alerts/active`/`history`, so nothing is ever silently lost
even if notification delivery itself is broken.

## Handoff

- **Downstream:** `routes.py`'s two GET routes plus the manual-resolve POST
  below; no dashboard panel wired up as of this writing.
- Restoring/acting on `kill_switch`/connectivity alerts is automatic
  (`_check_transition` resolves the moment the polled condition clears);
  `crash` alerts resolve via the mechanism below. This module still never
  takes any corrective action itself (no auto-resume, no auto-restart
  beyond what `task_supervisor.supervise(restart=True)` already does
  independently of this module).

## Crash-alert resolution (`crash` category)

`"crash"` alerts (recorded by `task_supervisor.py`'s own exception handler,
not this module's `_check_transition`) are discrete per-occurrence events —
one row per crash, no ongoing condition to poll back to "OK" the way
`kill_switch`/connectivity have. Two independent resolution paths, added
2026-08-24 after a gap found live while building QCP Task 18's System
Health UI (`resolve_category` was never called for `"crash"`, so every
crash alert stayed `resolved_at: None` forever):

- **Auto-expire (default):** `check_and_alert`'s per-tick call to
  `_expire_stale_crash_alerts` ages each unresolved `"crash"` row out
  independently via `expire_old_alerts(category, max_age_sec, now)` once
  it's older than `alerting.crash_auto_resolve_after_sec` (default 1800s/
  30min — long enough not to flap on a normal `restart_delay_sec=5s`
  bounce-back, short enough not to leave a dashboard stuck red for hours
  unattended). A component that keeps crash-looping still shows a live
  alert from its most recent occurrence while older, non-repeating rows
  fall away on their own. Set to `0`/`null` to disable entirely (manual-
  only). Each auto-expiry fires the same fire-and-forget "resolved" webhook
  notification `kill_switch`/connectivity clears already use.
- **Manual acknowledge:** `resolve_alert(alert_id, now=None) -> bool` +
  `POST /api/alerts/{alert_id}/resolve` → `{"resolved": bool}` — generic by
  id rather than crash-specific (simpler than category-branching, and
  incidentally usable on any alert row), idempotent-by-rowcount like
  `resolve_category` (no 404 branch). No notification dispatch on this
  path — the human resolving it already knows.

**Correction to an earlier claim:** the original gap writeup (still in
`docs/roadmap-archive-2026-08-23.md`/git history) asserted a stuck crash
alert "pins `GET /api/quality/summary`'s overall `status` to `error`
permanently." That's incorrect — `services/quality/routes.py` composes
`status`/`counts` only from `observability.runtime_findings()` +
`storage_health.storage_findings()` (see `services/quality/CHEATSHEET.md`'s
own field table); `alerting.active_alerts()` is exposed as a separate,
uncombined `alerts` field that never feeds into `status`. The real,
narrower effect of the gap was `GET /api/alerts/active`/the dashboard's
"Alerts: N active" line staying wrong forever, which is what this fix
actually corrects. Whether a critical active alert *should* be able to
drive `overall_status()` to `"error"` is a separate, still-open question —
see `ROADMAP.md`.
