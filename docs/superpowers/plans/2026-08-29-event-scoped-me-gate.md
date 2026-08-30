# Event-Scoped ME Entry Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop doomed same-event position pairs from forming, by gating entries on the event_ticker every resolved market already carries instead of the watchlist-scoped me_pairs map.

**Architecture:** Thread `event_ticker` from the resolved market object through `WhaleSignal` and onto `Position` (additive column); replace `evaluate()`'s `me_complement` check with an event-scoped check against open positions, verdict from the event's documented `mutually_exclusive` flag; ensure that flag on-demand via the existing `get_events` batch, cached in `event_titles`/`title_cache`. Fail-open-but-counted on unknown.

**Tech Stack:** Python/FastAPI app, SQLite additive migrations, pytest (sync tests + `asyncio.run`, no pytest-asyncio).

**Spec:** `docs/superpowers/specs/2026-08-29-event-scoped-me-gate-design.md`

## Global Constraints

- Kalshi facts come from `docs/kalshi/` only: `event_ticker` is a field on market objects (get-market.md:141); `mutually_exclusive` is a boolean on event objects (get-events.md:236). NEVER derive event from the ticker string.
- Schema changes additive-only (`_add_column_if_missing`); tests monkeypatch `DB_PATH`/use tmp paths; never touch live `data/*.db`.
- The gate may only reduce entries — no new entry path, no trading-enablement surface.
- Unknown ME flag fails OPEN and is counted + fault-logged, never silent.
- Run per-task tests via `ddev exec -s fastapi sh -c "cd /app/.claude/worktrees/feat-me-event-gate && python3 -m pytest -q -p no:testmon <files>"` from the primary root. Cite plan tasks as `event-scoped-me-gate Task N` (plans README rule).
- `cite docs read` in the final commit: docs/kalshi/get-market.md, get-markets.md, get-events.md.

---

### Task 1: `WhaleSignal.event_ticker`, populated by the real provider

**Files:**
- Modify: `services/confidence_scoring.py` (WhaleSignal dataclass, ~line 34)
- Modify: `services/whalewatchers/kalshi_trade_tape.py` (WhaleSignal construction, ~line 759)
- Test: append to `tests/test_whalewatchers_kalshi_trade_tape.py`

**Interfaces:**
- Produces: `WhaleSignal.event_ticker: str | None` (None from the simulator — same convention as `factors`). Later tasks read `signal.event_ticker`.

- [ ] **Step 1: Write the failing test**

```python
def test_fetch_signals_carries_the_markets_event_ticker():
    # The gate (Task 3) keys on this field; a market object's own
    # event_ticker (docs/kalshi/get-market.md:141) is the ONLY legal
    # source - never the ticker string prefix.
    provider = KalshiTradeTapeProvider()
    market = _market(ticker="KXATPMATCH-26AUG29FERBUS-FER")
    market["event_ticker"] = "KXATPMATCH-26AUG29FERBUS"
    trade = _trade(ticker="KXATPMATCH-26AUG29FERBUS-FER",
                   count_fp="50000.00", taker_side="yes")
    ctx = {"markets": [market], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert len(signals) == 1
    assert signals[0].event_ticker == "KXATPMATCH-26AUG29FERBUS"


def test_fetch_signals_event_ticker_none_when_market_lacks_it():
    provider = KalshiTradeTapeProvider()
    market = _market(ticker="K1")
    market.pop("event_ticker", None)
    trade = _trade(ticker="K1", count_fp="50000.00", taker_side="yes")
    ctx = {"markets": [market], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].event_ticker is None
```

(Verified against the file's real helpers this session: `_market(ticker=...)`
and `_trade(ticker=..., count_fp=..., taker_side=...)` exist with those
exact parameters; there is no `_provider`/`_fetch` helper - construction is
`KalshiTradeTapeProvider()` and invocation is
`asyncio.run(provider.fetch_signals(market_context={...}))`, the idiom of
`test_fetch_signals_skips_trades_below_contract_threshold`. count_fp
"50000.00" clears the code-default min_contracts with cfg={}.)

- [ ] **Step 2: Run to verify both fail** — `TypeError`/`AttributeError: event_ticker`.

- [ ] **Step 3: Implement.** In `confidence_scoring.py`, after `close_time: str | None = None` add:

```python
    # The market's own event_ticker (docs/kalshi/get-market.md) - the
    # event-scoped ME entry gate keys on this. None from the simulator,
    # same convention as factors above. Never derived from the ticker
    # string: no doc guarantees the SERIES-EVENT-MARKET prefix shape.
    event_ticker: str | None = None
```

In `kalshi_trade_tape.py`'s `WhaleSignal(...)` construction add:

```python
                close_time=market.get("close_time"),
                event_ticker=market.get("event_ticker"),
```

- [ ] **Step 4: Run to verify both pass.**
- [ ] **Step 5: Run the file's full suite** (`tests/test_whalewatchers_kalshi_trade_tape.py`) for regressions.
- [ ] **Step 6: Commit:** `feat: WhaleSignal carries the resolved market's event_ticker (event-scoped-me-gate Task 1)`

---

### Task 2: `Position.event_ticker` — additive column, stamped at open (no backfill, by design)

**Files:**
- Modify: `services/paper_broker.py` (Position dataclass ~25, `_connect` migrations ~113-150, `open_position` ~289, `_load` SELECT ~260)
- Modify: `services/strategy_engine.py` (`open_position` call, ~line 555)
- Test: append to `tests/test_paper_broker.py`

**Interfaces:**
- Consumes: `WhaleSignal.event_ticker` (Task 1).
- Produces: `Position.event_ticker: str | None` (persisted); `PaperBroker.open_position(..., event_ticker: str | None = None)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_open_position_stamps_and_persists_event_ticker(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("K-EVT-A", "yes", 10, 0.6, "test",
                         event_ticker="K-EVT")
    assert broker.positions["K-EVT-A"].event_ticker == "K-EVT"
    reloaded = _broker(tmp_path, monkeypatch)  # same db path -> loads rows
    assert reloaded.positions["K-EVT-A"].event_ticker == "K-EVT"


def test_legacy_position_row_loads_with_event_ticker_none(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("K1", "yes", 10, 0.6, "test")  # no event passed
    reloaded = _broker(tmp_path, monkeypatch)
    assert reloaded.positions["K1"].event_ticker is None
```

(The real helper is `_broker(tmp_path, monkeypatch, starting_bankroll=1000.0)`
- tests/test_paper_broker.py:10, verified this session - both fixtures are
required.)

- [ ] **Step 2: Run to verify FAIL** (unexpected keyword `event_ticker`).

- [ ] **Step 3: Implement.**
  - Dataclass: after `hold_to_settlement: bool = False` (check current last field order — keep new field LAST) add `event_ticker: str | None = None` with a comment citing the gate.
  - Migration block: `_add_column_if_missing(conn, "positions", "event_ticker", "TEXT")`.
  - `open_position(...)`: add keyword `event_ticker: str | None = None`; pass to `Position(...)`; add the column to the INSERT that persists positions (read the INSERT statement first and extend both column list and values).
  - `_load`: extend the SELECT to `..., hold_to_settlement, event_ticker FROM positions` and the `Position(...)` construction accordingly.
  - `strategy_engine.py:555` call: add `event_ticker=signal.event_ticker,`.
  - Lazy catalog backfill is NOT done at load (keep load pure); unknown stays None and Task 3 counts it — spec §4.2's fail-open.

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: Full `tests/test_paper_broker.py` regression run.**
- [ ] **Step 6: Commit:** `feat: positions carry event_ticker, additive column (event-scoped-me-gate Task 2)`

---

### Task 3: The gate — replace `me_complement` with the event-scoped check

**Files:**
- Modify: `services/strategy_engine.py` (`evaluate()` signature ~208 and the me_complement block at ~441-452)
- Modify: `services/whale_stream/decision_bridge.py` (~107-112: stop computing/passing `me_complement`)
- Test: append to `tests/test_strategy_engine.py`; update the two existing me_complement tests there

**Interfaces:**
- Consumes: `signal.event_ticker` (Task 1), `Position.event_ticker` (Task 2), `event_titles` dict already passed into `evaluate()`.
- Produces: gate name `"me_event_gate"` in candidate_log rejections; `StrategyEngine.me_gate_unknown_total: int` counter (read by Task 4's wiring/observability).

- [ ] **Step 1: Write the failing tests** (match `tests/test_strategy_engine.py`'s `_cfg`/`_signal`/`_strategy` helpers — read the existing `test_skip_when_position_already_open_on_me_complement` first and mirror its setup):

```python
def _me_signal(ticker="EV-A", event="EV"):
    s = _signal(ticker=ticker)
    s.event_ticker = event
    return s

def test_me_true_event_with_open_sibling_position_is_skipped():
    strat = _strategy()
    strat.broker.open_position("EV-B", "yes", 10, 0.6, "t", event_ticker="EV")
    d = strat.evaluate(_me_signal(), _cfg(),
                       event_titles={"EV": {"mutually_exclusive": True}})
    assert d["action"] == "skip"
    assert "one-winner event" in d["reason"]

def test_me_false_event_allows_second_position():
    strat = _strategy()
    strat.broker.open_position("EV-B", "yes", 10, 0.6, "t", event_ticker="EV")
    d = strat.evaluate(_me_signal(), _cfg(),
                       event_titles={"EV": {"mutually_exclusive": False}})
    assert d["action"] != "skip" or "one-winner" not in d.get("reason", "")

def test_unknown_me_flag_fails_open_counts_and_fault_logs_once(monkeypatch):
    faults = []
    monkeypatch.setattr("services.fault_log.record_fault",
                        lambda *a, **k: faults.append(a) or True)
    strat = _strategy()
    strat.broker.open_position("EV-B", "yes", 10, 0.6, "t", event_ticker="EV")
    d = strat.evaluate(_me_signal(), _cfg(), event_titles={})
    assert "one-winner" not in d.get("reason", "")
    d = strat.evaluate(_me_signal(), _cfg(), event_titles={})  # same event again
    assert strat.me_gate_unknown_total == 2       # counter: every occurrence
    assert len(faults) == 1                        # fault row: once per event

def test_nway_me_event_third_market_is_skipped():
    # The shape find_me_pairs could never catch (len(siblings) != 2).
    strat = _strategy()
    strat.broker.open_position("GOLF-P1", "yes", 10, 0.6, "t", event_ticker="GOLF")
    d = strat.evaluate(_me_signal(ticker="GOLF-P3", event="GOLF"), _cfg(),
                       event_titles={"GOLF": {"mutually_exclusive": True}})
    assert d["action"] == "skip"

def test_simulator_signal_without_event_passes_gate():
    strat = _strategy()
    strat.broker.open_position("EV-B", "yes", 10, 0.6, "t", event_ticker="EV")
    d = strat.evaluate(_signal(ticker="EV-A"), _cfg(),  # event_ticker None
                       event_titles={"EV": {"mutually_exclusive": True}})
    assert "one-winner" not in d.get("reason", "")
```

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Implement.** In `evaluate()`:
  - Remove `me_complement: str | None = None` from the signature (grep all callers: decision_bridge.py is the only live one; the two old tests update in Step 4).
  - Replace the block at ~441-452 with:

```python
        # Event-scoped ME gate (2026-08-29 spec, replacing the me_pairs
        # complement check): entries arrive from the exchange-wide stream,
        # so the pair map computed off the ~15-market watchlist almost
        # never covered them - all 22 reconstructed same-event pairs
        # summed over 1.00 combined cost (min_unit_cost >= 0.5 makes any
        # pair >= 1.00 by construction), 12 mathematically locked at the
        # second leg's entry. Gate on the event itself: one open position
        # per mutually-exclusive (one-winner) event, any side - the
        # opposite-bet and disguised-double loss shapes share the trigger.
        signal_event = getattr(signal, "event_ticker", None)
        if signal_event:
            held_events = {p.event_ticker for p in self.broker.positions.values() if p.event_ticker}
            if signal_event in held_events:
                me_flag = ((event_titles or {}).get(signal_event) or {}).get("mutually_exclusive")
                if me_flag is True:
                    candidate_log.record_rejection(
                        signal.ticker, "whale_follow", "me_event_gate", 1.0, 0.0,
                        side=signal.side, unit_cost=unit_cost,
                    )
                    return self._skip(
                        signal,
                        f'already holding a position on one-winner event "{signal_event}"',
                    )
                if me_flag is None:
                    # Fail OPEN (uniform rule) but never silently: the
                    # counter increments every time (the recurrence
                    # signal that Task 4's plumbing has a hole); the
                    # fault row is once per event per process - a broken
                    # metadata path at signal rate must not become a
                    # SQLite write per signal (websocket.py's
                    # once-per-class-per-window reasoning).
                    self.me_gate_unknown_total += 1
                    if signal_event not in self._me_gate_unknown_logged:
                        self._me_gate_unknown_logged.add(signal_event)
                        fault_log.record_fault(
                            "strategy_engine", "me_flag_unknown",
                            f"{signal_event}: mutually_exclusive unknown at entry - gate failed open",
                            severity="warn",
                        )
```

  - `__init__`: add `self.me_gate_unknown_total = 0` and
    `self._me_gate_unknown_logged: set[str] = set()`. strategy_engine does
    NOT currently import fault_log (verified: its `from services import`
    line at :9 carries candidate_log, market_history, signal_log) - extend
    that exact import line with `fault_log`.
  - Rewrite the me_complement paragraph in `evaluate()`'s docstring
    (~lines 247-258) to describe the event-scoped gate - a removed
    parameter documented as current is worse than no docs.
  - `decision_bridge.py`: delete the `me_complement = (state.get("me_pairs") or {}).get(signal.ticker)` line and the `me_complement=me_complement,` kwarg.

- [ ] **Step 4: Update the two legacy tests** (`test_skip_when_position_already_open_on_me_complement`, `test_trades_when_me_complement_has_no_open_position`): rewrite them against the new gate semantics (rename to match; the skip case becomes ME-true + shared event) rather than deleting — they are the gate's original contract.

- [ ] **Step 5: Run `tests/test_strategy_engine.py` fully.** Expected: PASS.
- [ ] **Step 6: Commit:** `feat: event-scoped ME entry gate replaces the watchlist pair map (event-scoped-me-gate Task 3)`

---

### Task 4: ME-flag on-demand ensure in the resolve path

**Files:**
- Modify: `services/whalewatchers/kalshi_trade_tape.py` (`_resolve_unknown_markets`, ~line 360+)
- Test: append to `tests/test_whalewatchers_kalshi_trade_tape.py`

**Interfaces:**
- Consumes: `client.get_events(event_tickers)` (existing `services/kalshi/public.py` method — verify its exact name/signature by reading the file first; main's `_fetch_event_titles` calls it) and `state["event_titles"]` via `services.app_state`.
- Produces: after a whale-sized off-list print resolves its market, that market's event has an `event_titles` entry (with `mutually_exclusive`) before the signal reaches `evaluate()`.

- [ ] **Step 1: Write the failing test**

```python
def test_resolving_an_offlist_market_also_ensures_its_events_me_flag(monkeypatch):
    # The gate is only as good as flag availability at entry time (spec
    # 4.3): resolving a market whose event is unseen must fetch the event
    # (one get_events call, cached thereafter) so Task 3's lookup hits.
    from services.app_state import state
    state["event_titles"].pop("EV-NEW", None)
    calls = []
    class _Client:
        async def get_markets_by_tickers(self, tickers):
            return {t: {"ticker": t, "event_ticker": "EV-NEW",
                        "yes_bid_dollars": 0.6, "volume_24h_fp": "1000"} for t in tickers}
        async def get_events(self, event_tickers):
            calls.append(list(event_tickers))
            return [{"event_ticker": "EV-NEW", "title": "T",
                     "mutually_exclusive": True, "category": "Sports"}]
    provider = KalshiTradeTapeProvider()
    trade = _trade(ticker="EV-NEW-A", count_fp="50000.00", taker_side="yes")
    asyncio.run(provider._resolve_unknown_markets(
        [trade], {}, {}, _Client(), now=time.time(), counts={}))
    assert calls == [["EV-NEW"]]
    assert state["event_titles"]["EV-NEW"]["mutually_exclusive"] is True
```

(Signature verified this session:
`_resolve_unknown_markets(self, trade_tape, markets_by_ticker, cfg, client,
now, counts=None)`; there is no `_cfg` helper in this file - plain `{}` is
the idiom, and the code-default min_contracts applies (count_fp "50000.00"
clears it). For `get_events`' return-shape consumption, read main.py's
`_fetch_event_titles` field extraction and reuse it verbatim, persisting
via `title_cache.save_event_titles` exactly as main.py:864 does.)

- [ ] **Step 2: Run to verify FAIL** (no get_events call made).
- [ ] **Step 3: Implement** inside `_resolve_unknown_markets`, after the market batch resolves: collect `event_ticker`s of newly resolved markets not present in `state["event_titles"]`; if any, one `client.get_events(missing)` call (it already runs under the `critical_whale` caller class via the method's `@http_client.classify` decorator — verify, don't assume), extract the same fields `_fetch_event_titles` extracts (title, category, `mutually_exclusive`, mutually-exclusive-adjacent fields it keeps), write into `state["event_titles"]` and persist via `title_cache.save_event_titles({...})`. A get_events failure is caught, fault-logged once, and skipped — resolution of the market itself must not fail because event metadata didn't arrive (the gate then counts an unknown, which is the designed degradation).
- [ ] **Step 4: Run to verify PASS; run the file's full suite.**
- [ ] **Step 5: Commit:** `feat: resolve path ensures event ME flags for the entry gate (event-scoped-me-gate Task 4)`

---

### Task 5: Real-pair regression, observability, docs

**Files:**
- Test: append to `tests/test_strategy_engine.py`
- Modify: `services/observability/observability.py` (one gauge line — mirror how an existing strategy counter is captured; read `capture_from_runtime` first)
- Modify: `docs/superpowers/research/2026-08-29-trade-performance-analysis.md` (§13 watch-items: add the gate), `docs/next-action.md` if stale

**Interfaces:**
- Consumes: everything above.
- Produces: `strategy.me_gate_unknown_total` visible in observability samples; regression test pinning the real-world shape.

- [ ] **Step 1: Write the failing regression test**

```python
def test_regression_kxatpmatch_ferbus_second_leg_is_refused():
    # Reconstructs the real 2026-08-29 pair: first leg entered, second leg
    # 1,816s later at combined unit cost 1.41 - mathematically locked at
    # entry (research doc section 13). With the event gate, leg 2 never opens.
    strat = _strategy()
    strat.broker.open_position(
        "KXATPMATCH-26AUG29FERBUS-FER", "yes", 590, 0.75, "whale print",
        event_ticker="KXATPMATCH-26AUG29FERBUS")
    leg2 = _signal(ticker="KXATPMATCH-26AUG29FERBUS-BUS")
    leg2.event_ticker = "KXATPMATCH-26AUG29FERBUS"
    leg2.price = 0.66
    d = strat.evaluate(leg2, _cfg(), event_titles={
        "KXATPMATCH-26AUG29FERBUS": {"mutually_exclusive": True}})
    assert d["action"] == "skip"
    assert "one-winner event" in d["reason"]
```

- [ ] **Step 2: Run to verify it passes already** (it should, from Task 3 — this is a pinning test; if it fails, Task 3 has a bug: stop and fix there).
- [ ] **Step 3: Observability:** observability.py captures NO strategy_engine
counter today (verified by grep - no anchor to mirror blindly). The correct
existing pattern is how `candidate_retry`'s counters flow: `capture_from_runtime`
reads `candidate_retry.snapshot()` (see `tests/test_observability.py::
test_candidate_retry_metrics_flow_into_the_snapshot`). Mirror THAT: import
`strategy` from `services.app_state`, emit one gauge
(`strategy.me_gate.unknown_total`) from `strategy.me_gate_unknown_total`
guarded for None/missing (observability must never crash on a
partially-initialized app), plus one test asserting the metric name appears
in a capture, mirroring the candidate_retry test's structure.
- [ ] **Step 4: Docs:** add to research doc §13 watch items: "me_gate_unknown_total near zero in steady state; candidate_log gate_summary shows me_event_gate rejections". Cross-post one dated line to `services/exits/README.md`'s netting section pointing at the gate as the formation fix.
- [ ] **Step 5: Run the four touched test files together.** Expected: PASS.
- [ ] **Step 6: Commit:** `feat: real-pair regression + gate observability (event-scoped-me-gate Task 5)` — cite docs read: docs/kalshi/get-market.md, get-markets.md, get-events.md.

---

## Self-review

**Spec coverage:** §4.1 gate → Task 3; §4.2 plumbing → Tasks 1-2 (WhaleSignal, Position; catalog backfill deliberately narrowed to fail-open-counted per Task 2 Step 3, matching §4.2's "participates as nothing"); §4.3 flag availability + measurement → Task 4 + Task 5 observability; §4.4 retirements → Task 3 (me_complement removed; find_me_pairs untouched); §6 tests 1-5 → Task 3, test 6 → Task 2, test 7 → Task 5, test 8 → Task 5 Step 4 (gate_summary is automatic once record_rejection fires; the watch item verifies). candidate_retry recovered-signal path (§4.2 last bullet): covered structurally — recovery re-scores through `score_recovered_trade` → `_process_trades_sync`, the same constructor Task 1 modifies; Task 1's suite run catches it.

**Placeholder scan:** every code step shows real code; the three "read X first" instructions name the exact file/line and why (matching real signatures at implementation time), which is this repo's established grounding idiom, not deferral.

**Type consistency:** `event_ticker: str | None` everywhere; `open_position(..., event_ticker=None)` used identically in Tasks 2, 3, 5; gate name `"me_event_gate"` identical in Tasks 3 and 5; counter `me_gate_unknown_total` identical in Tasks 3 and 5.
