# Kalshi API cheat sheet — known answers, checked before re-deriving

Living, append-only reference of specific "what field/endpoint answers
this" questions already resolved by actually reading `docs/kalshi/`, kept
here so they don't get re-derived (or re-guessed) from scratch next
session. **Check this file first** for anything it already covers before
grepping the full 215-page mirror cold — and add a new entry any time a
`docs/kalshi/` page resolves a real data question, especially one that
took a wrong guess to get to. See `CLAUDE.md`'s "Kalshi API documentation"
section for the full standing rule this file backs.

### Entry format

Each real entry below is an `##` heading (a short question), so
`.claude/hooks/session_orient.sh` can list titles at session start via
`grep '^## '` — keep this section itself at `###` so it doesn't get
matched as an entry. Each entry then has `**Answer:**`, `**Source:**`,
`**Gotcha:**` (if there is one), `**Found:**` (date + one-line context).

---

## What sport/league does an event belong to? ("subcategory" grouping)
**Answer:** `event.competition` (e.g. `"Pro Baseball"`) is the real
per-event field — confirmed on a live event object and documented as
`competition (string, nullable)` on the event-metadata response. For the
coarser SPORT grouping (e.g. `"Baseball"`), reverse-map through
`filters_by_sports`: `{sport: {scopes, competitions: {competition:
{scopes}}}}` — several competitions nest under one sport (`"Baseball"` →
`"Pro Baseball"`, `"Japan NPB"`, `"Korea KBO"`, `"Mexico LMB"`).
**Source:** `get-event-metadata.md` (`competition` field),
`get-filters-for-sports.md` (`filters_by_sports` sport→competition shape).
**Gotcha:** `category_tags` on an event object LOOKS like per-event tag
data but isn't — it's the identical full facet-filter vocabulary listed on
every event in a category (every Sports event lists all ~20 sports),
carrying zero per-event signal. Confirmed live, not derivable from that
field no matter how it's parsed.
**Found:** 2026-08-16, building whale-confidence subcategory segmentation
(`services/trade_category.py`, `main.py`'s `_sport_for_event`).

---

## Can a market's `close_time`/status change after it's already been read?
**Answer:** Yes. `close_time` can move (`close_date_updated` event) when a
market is closed ahead of its scheduled close time, including before
determination. Full real lifecycle: `active` ↔ `inactive`
(`deactivated`/`activated`) → `closed` → `determined` (result set) →
`finalized` (paid out, via `settled`) — `amended`/`disputed` also possible.
A `market_lifecycle_v2` WS event fires for every one of these transitions
(`created`, `activated`, `deactivated`, `close_date_updated`, `determined`,
`settled`, `metadata_updated`).
**Source:** `market_lifecycle.md` (full status table + WS event list).
**Gotcha:** A locally-cached/scanned row (e.g. `market_catalog.db`) can
keep showing a stale `close_time`/`status` indefinitely until its next
scan — a `close_ts > now` filter alone can't catch this, since it's
trusting the same stale column that's wrong. Any status past `active`
(`closed`/`determined`/`disputed`/`amended`/`finalized`) means the
catalog's belief about that market is no longer trustworthy regardless of
what `close_ts` says.
**Found:** 2026-08-16 investigation (see `main.py`'s
`_DISCOVERY_TERMINAL_STATUSES`), first surfaced by a real finalized MLB
market whose catalog row still showed `status=active` 24 minutes after
Kalshi's own API had moved it to `finalized`.
