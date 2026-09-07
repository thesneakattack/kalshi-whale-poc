# Plan: weather index ingestion — 2026-08-31

Status: REVIEWED, GO (self-review + independent adversarial review +
consolidation complete, 3 fixes applied in place — see
`2026-08-31-weather-index-ingestion-consolidation.md`). **Not executed**.
No `services/weather_index/`
code exists yet, no `data/weather_index.db` created, no poller wired into
`main.py`. Per this repo's "workflow/tooling and application code never
overlap" convention this plan lives entirely in `services/` scope — nothing
here touches `tools/`.

## Input

Design stage, reviewed and consolidated GO (with a correction applied in
place):
`docs/archive/lane-1-kalshi-ingestion/specs/2026-08-30-weather-index-ingestion-design.md` +
`...-design-review.md` + `...-design-consolidation.md`. This plan turns
that design directly into TDD tasks, folding in the consolidation's 4-item
fix list (refreshed city ranking, THOU/TDAL/AUS reconsideration, re-run the
ranking query once the catalog has cycled, and treat the design's own
already-flagged open items as real tasks rather than silently dropping
them).

## Scope reminder (from the design, restated so this plan is self-contained)

**Ingestion only.** No settlement-edge validation study, no entry logic, no
strategy wiring — this plan stops at a working poller that stores raw,
fidelity-preserving ticks. Whether an edge exists here at all stays an
open, unanswered question; nothing in this plan answers it.

## Constraints

- TDD throughout, per this repo's `test-driven-development` skill and the
  design's own "Testing" section — each task's tests are written and shown
  failing before the implementation that makes them pass, not written
  after.
- New package `services/weather_index/`, own `ingestion.py`, own
  `data/weather_index.db` — not an extension of `services/index_feed/`
  (per the design's own reasoning: different mechanism, same conceptual
  role, split by responsibility).
- `dimensional-analysis` pass required before trusting any arithmetic in
  this plan's implementation (poll-interval seconds, Fahrenheit precision,
  rate-limit budget math) per CLAUDE.md's HARD RULE — flagged per-task
  below, not assumed done here.
- Nothing in this plan enables real trading, touches `risk_manager.py`,
  `paper_broker.py`, or any strategy/entry code — pure ingestion, matching
  the design's explicit out-of-scope list.
- One commit per task, per this repo's "meaningful checkpoint commits"
  convention.

### Task 1: Live-verify the `city` path-parameter spelling

- [ ] Not a code task — a verification task the design doc explicitly
      flagged as unresolved ("must be verified against Kalshi's real
      city-ID list before coding — not inferred from the `KXHIGH*`
      series-ticker suffixes"). Call `GET /trade-api/v2/live_data/weather/{city}`
      against a plausible candidate (`docs/kalshi/get-weather-index.md`'s
      own example uses lowercase `miami`) via the existing
      `services/kalshi/public.py` client pattern, read-only, no state
      written. Record the confirmed spelling convention for each of the
      cities Task 2 below will target.
- [ ] Blocks Task 3 (the real poller needs real, confirmed city IDs).

### Task 2: Re-confirm the city starting list against fresh catalog data

- [ ] Not a code task. Re-run the `market_catalog.db` `KXHIGH%` ranking
      query (the one this plan's design-review cycle already ran once,
      2026-08-31) to confirm `catalog_scan.py` has now cycled fully
      through the widened `kalshi.categories` set (per
      `docs/open-decisions.md`'s 2026-08-30 entry, the read used for the
      design review was still mid-cycle). Finalize the starting city list
      from that reading — default expectation (not guaranteed) is `LAX`,
      `MIA`, `NY`, `CHI`, with `THOU`/`TDAL`/`AUS` explicitly considered
      given how close their volume sat to CHI's in the mid-cycle read, not
      assumed excluded.
- [ ] Record the finalized list and the query's evidence (timestamp, ranked
      volumes) in this plan's own commit message for Task 3 — the list
      used matters more than this task's own commit, since it's consumed
      immediately after.

### Task 3: `services/weather_index/ingestion.py` — schema, poll, upsert

- [ ] **Test first**: raw-payload fidelity — a stored row's `raw_json`
      round-trips to exactly what a (mocked) `GET .../weather/{city}`
      response returned, for a single successful poll.
- [ ] **Test first**: the quorum-gap-is-really-absent semantic — a mocked
      response with a missing minute results in **no row** for that
      minute, not a null value, not an interpolated one.
- [ ] **Test first**: the distinct `incomplete`-status case — per
      `docs/kalshi/get-weather-index.md`, a `detailed=true` response can
      return a point for the trailing minute still inside its receipt
      deadline with no `v` (value) field, which is NOT the same thing as a
      quorum-failure gap. A mocked response with an `incomplete` point
      results in a row written with `value=NULL` (upserted to a real value
      once a later poll completes it) — distinct from Task 3's other test,
      which covers a point genuinely absent from the response. The
      nullable `value REAL` column already handles this correctly by
      design; this test exists to prove it, not to add new logic.
- [ ] Implement `DB_PATH`, table, and `_connect()` per this repo's
      persistence idiom (`CREATE TABLE IF NOT EXISTS`, `.parent.parent.parent`
      verified 3-deep against `index_feed.ingestion`'s real line, per the
      design doc's own citation):
      ```python
      DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "weather_index.db"

      def _connect() -> sqlite3.Connection:
          DB_PATH.parent.mkdir(exist_ok=True)
          conn = sqlite3.connect(DB_PATH)
          conn.execute("""
              CREATE TABLE IF NOT EXISTS weather_index_ticks (
                  city TEXT NOT NULL,
                  minute_ts INTEGER NOT NULL,
                  value REAL,
                  raw_json TEXT NOT NULL,
                  polled_at REAL NOT NULL,
                  PRIMARY KEY (city, minute_ts)
              )
          """)
          return conn
      ```
- [ ] Implement `poll_city(city: str, last_sec: int) -> list[dict]` calling
      `GET /trade-api/v2/live_data/weather/{city}?detailed=true&last_sec={last_sec}`
      via the existing `services/kalshi/public.py` client pattern (never a
      hand-rolled request — per the Kalshi Integration Authority rule, this
      goes through `services/kalshi/`, not a new ad hoc HTTP call).
- [ ] Implement `record_tick(city, minute_ts, value, raw_point)` as a
      direct upsert per point (no batch buffer — per the design's own
      corrected reasoning, a 60s poll cadence doesn't fit
      `record_cfbenchmarks`'s 200-row buffer-then-flush shape):
      ```python
      conn.execute("""
          INSERT INTO weather_index_ticks (city, minute_ts, value, raw_json, polled_at)
          VALUES (?, ?, ?, ?, ?)
          ON CONFLICT(city, minute_ts) DO UPDATE SET
              value=excluded.value, raw_json=excluded.raw_json, polled_at=excluded.polled_at
      """, (city, minute_ts, value, json.dumps(raw_point, default=str), time.time()))
      ```
- [ ] `dimensional-analysis` pass on the value/precision handling (Fahrenheit,
      0.01 precision per the design's schema comment) before this task is
      trusted done, per the HARD RULE.

### Task 4: Overlapping-window and revision correctness

- [ ] **Test first**: two overlapping polls covering the same minute upsert
      to exactly one row (no duplicate primary keys, no error).
- [ ] **Test first**: simulating a QC revision (two polls returning
      different `value` for the same `minute_ts`) — the second (later-
      polled) value wins, matching `capture_writer.py`'s upsert semantics
      the design cites.
- [ ] No new production code expected here beyond what Task 3 already
      wrote — this task is confirming the upsert design actually delivers
      the property it was designed for, via tests, not adding logic.

### Task 5: Error handling

- [ ] **Test first**: a failed poll (network error, non-200) logs a fault
      via `fault_log` (same call shape as this repo's other schedulers,
      e.g. `services/index_feed/ingestion.py`'s own fault-logging calls —
      verify the exact call signature there before copying it, per
      "never guess") and does not raise/crash the poller.
- [ ] **Test first**: a failed poll does not prevent the next scheduled
      poll from running.
- [ ] Implement the try/except-and-fault-log wrapper around `poll_city`,
      calling `fault_log.record("weather_index", "poll_city", exc)` — real
      call shape confirmed from `services/fault_log.py:85`'s actual
      signature (`record(component, operation, exc, context=None,
      severity="error", now=None)`) and the real call site at
      `services/index_feed/ingestion.py:150`; the second positional
      argument is `operation`, not `context` (an earlier draft of this
      plan mislabeled it — caught by this plan's own adversarial review).

### Task 6: Measure real per-city REST cost before fixing a polling interval

- [ ] Not a pure code task. The design doc explicitly defers "exact
      interval and per-city REST cost against this account's real
      rate-limit budget" to implementation time, per the data-plane HARD
      RULE's "identify the measured bottleneck first" — this task does
      that measurement: with the finalized city list from Task 2, compute
      actual REST calls/minute at a 60s floor (the index's own minute
      resolution, per the design) and compare against this account's
      measured rate-limit headroom (`GET /api/health/pipeline` or
      equivalent — per CLAUDE.md's "Start investigations here" ordering).
      `dimensional-analysis` pass on this arithmetic before trusting it.
- [ ] Decide the actual poll interval from that measurement, not a guess —
      record the decision and its evidence in the task's commit.

### Task 7: Wire the poller into the scheduler

- [ ] Add a `_maybe_poll_weather_index` function and register it in
      `main.py`'s scheduler tuple list, confirmed real (line 566-567:
      `[("backup", _maybe_run_backup), ("backup_large",
      _maybe_run_large_backup), ...]`) — following the existing
      `_maybe_run_backup`-style pattern (in-memory last-run timestamp,
      guarded by the interval decided in Task 6). This poller's own
      last-run state needs the same cold-start-reload guard `backup.py`
      was fixed for (in-memory last-run state + `uvicorn --reload` = a
      spurious full run on every dev-server reload) — real fix confirmed
      at `services/backup/backup.py:337`'s `_maybe_run_backup`: on cold
      start (`last_started_at == 0.0`) it seeds from persisted history
      (`latest(tier="regular")` reading `backup_log.db`) rather than
      trusting in-memory zero as "never ran." `weather_index` needs its
      own equivalent persisted-history seed (e.g. `MAX(polled_at)` from
      `weather_index_ticks` itself) — the two tables aren't the same
      shape, so this is not a literal copy-paste of `backup.py`'s query.
- [ ] **Test first**: the scheduler call is gated by the interval (doesn't
      poll on every tick), and survives a mocked `uvicorn --reload`-style
      cold start without a spurious immediate full poll.

### Task 8: Live verification under ddev

- [ ] Not a code task. `ddev restart` (new module needs a real process
      restart, not just hot-reload, per this repo's dev-workflow
      convention), confirm the poller actually runs, produces rows in
      `data/weather_index.db` for the finalized city list, and that a
      quorum-gap minute (if one occurs live during the check window)
      really produces no row rather than a null. Check `GET
      /api/health/faults` for any fault entries from this new poller.
- [ ] Confirm no regression to existing schedulers (backup, index_feed,
      market_watch) sharing the same tick loop — per the data-plane HARD
      RULE, a new diagnostic/poller on any shared hot path is measured for
      runtime cost before it ships; check `last_tick_duration_sec` before
      and after this task.

## What this plan deliberately does not do

Build anything past raw ingestion. No `services/weather_settlement_edge.py`,
no entry logic, no strategy wiring, no widening of `kalshi.categories`
further (already resolved separately) — all explicitly out of scope per the
design, restated here so a future session picking this plan up doesn't
scope-creep past what was actually decided.

Execute itself. This plan is written and reviewed, not run — see the
review cycle in this same directory (`...-review.md`, `...-consolidation.md`)
for the sign-off that it's ready, and the user go-ahead this repo's
Toolchain/planning conventions require before implementation of a new
data-ingestion module begins.
