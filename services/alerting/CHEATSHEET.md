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

- **Downstream:** `routes.py`'s two GET routes; no dashboard panel wired
  up as of this writing.
- Restoring/acting on an alert is manual — this module only detects and
  notifies, it never takes any corrective action itself (no auto-resume,
  no auto-restart beyond what `task_supervisor.supervise(restart=True)`
  already does independently of this module).
- **Known gap, found live 2026-08-24 building QCP Task 18's System Health
  UI, not yet fixed (see `ROADMAP.md`):** the `"crash"` category (recorded
  by `task_supervisor.py`'s own crash handler, not this module's
  `_check_transition`) has no resolution path anywhere in this codebase —
  `resolve_category` is only ever called for the two continuously-
  monitored transition-based conditions above. A single crash alert stays
  `resolved_at: None` forever, which pins `GET /api/quality/summary`'s
  overall `status` to `"error"` permanently even after the underlying
  issue is long since fixed. Live-observed: 5 real but already-self-
  corrected `trading_loop` crashes from mid-session development left
  status red 105 minutes later. The right fix (auto-expire after N clean
  ticks? require human acknowledgment? both?) is an open design decision,
  not a quick patch — see `ROADMAP.md`'s P4 section for the full writeup.
