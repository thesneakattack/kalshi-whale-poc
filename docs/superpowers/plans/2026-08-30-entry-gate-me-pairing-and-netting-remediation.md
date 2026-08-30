# Entry-Gate ME-Pairing Coverage Fix, Netting Fee Visibility, Milestone Coverage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a verified, real-dollar entry-side bug (whale-follow buying both sides of a two-outcome market because the ME-pairing gate can't see off-watchlist candidates), make position_netting's hidden exit-fee cost visible instead of silently absorbed, and close a separate, verified gap where a fully-built-and-tested broad milestone API (`get_milestones_bulk`) has zero callers, so live score/clock capture shares the same watchlist blind spot as the entry-gate bug.

**Architecture:** Three independently-shippable parts on one branch, one commit per task, matching this repo's numbered-plan convention. Part 1 (Tasks 1-2) adds a new, broad-cache-backed conflict check to `mutual_exclusivity.py` and wires it into `decision_bridge.py` as a fallback — `strategy_engine.py` needs no changes. Part 2 (Tasks 3-4) adds a computed fee-cost field to `position_netting.describe_groups`'s `locked_loss` recommendation and threads it through to a new `trades` column, mirroring the existing issue #213 columns exactly — no trading-behavior change. Part 3 (Tasks 5-6) adds a new `milestone_scan` scheduler (mirrors `catalog_scan`/`mve_scan` exactly) that populates a broad `event_ticker -> milestone_id` cache, then wires `_fetch_live_status` to check it before falling back to its existing per-event REST call.

**Tech Stack:** Python, FastAPI, sqlite3 (stdlib), pytest, asyncio.

**Spec:** `docs/superpowers/specs/2026-08-30-entry-gate-me-pairing-and-netting-remediation-design.md`

## Global Constraints

- Paper-mode only. No change to `mode`, `kalshi_account.trading_enabled`, the kill switch, or any real-order path.
- All DB schema changes are additive only (`_add_column_if_missing`), never destructive; new columns are `NULL` for every pre-existing row, never a fabricated default.
- No change to `_LIVE_STATUS_LOOKBACK_SEC` / `_LIVE_STATUS_LOOKAHEAD_SEC` / `_LIVE_STATUS_REPOLL_SEC` / `_LIVE_STATUS_MAX_POLL_PER_TICK`.
- No change to `strategy_engine.py`'s `me_complement` gate logic (`services/strategy_engine.py:545`) — only the value it's handed improves.
- No change to `catalog_scan.propagate_milestone_winners` (settlement-outcome code — explicitly out of scope, see spec).
- Every `tests/*.py` file redirects `DB_PATH` via `tmp_path`/`monkeypatch` — never touches a live `data/*.db` file.
- Before Part 1's edits (Tasks 1-2), run a GitNexus `impact`/`context`/`trace` check on `decision_bridge.py`, `strategy_engine.py`, and `mutual_exclusivity.py` per CLAUDE.md's rule for strategy/shared-state code — see Task 1's own first step.

---

## Task 1: `find_open_confirmed_conflict` in `services/mutual_exclusivity.py`

**Files:**
- Modify: `services/mutual_exclusivity.py`
- Test: `tests/test_mutual_exclusivity.py`

**Interfaces:**
- Produces: `find_open_confirmed_conflict(ticker: str, market_titles: dict, event_titles: dict, open_position_tickers: set[str]) -> str | None`
- Produces: `me_pairing_stats() -> dict` — returns `{"me_pairing_unknown_total": int}`

- [ ] **Step 0: GitNexus impact check (before touching shared-state code)**

Run: `mcp__gitnexus__impact` (or the `impact` tool as exposed in this session) on `services/mutual_exclusivity.py`'s `find_me_pairs` and on `services/whale_stream/decision_bridge.py`'s `_handle_signal`, to confirm the full caller/consumer set matches what this plan assumes (only `main.py:838` calls `find_me_pairs`; only `decision_bridge.py:107` reads `state["me_pairs"]`). If it surfaces an additional caller not accounted for in the spec, stop and reconcile before continuing.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mutual_exclusivity.py`:

```python
from services.mutual_exclusivity import find_open_confirmed_conflict, me_pairing_stats


def test_find_open_confirmed_conflict_returns_the_open_sibling():
    market_titles = {
        "BON": {"event_ticker": "EVT-1"},
        "BUS": {"event_ticker": "EVT-1"},
    }
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    assert find_open_confirmed_conflict("BUS", market_titles, event_titles, {"BON"}) == "BON"


def test_find_open_confirmed_conflict_none_when_no_sibling_open():
    market_titles = {
        "BON": {"event_ticker": "EVT-1"},
        "BUS": {"event_ticker": "EVT-1"},
    }
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    assert find_open_confirmed_conflict("BUS", market_titles, event_titles, set()) is None


def test_find_open_confirmed_conflict_none_when_not_confirmed_true():
    market_titles = {
        "BON": {"event_ticker": "EVT-1"},
        "BUS": {"event_ticker": "EVT-1"},
    }
    for flag in (False, None):
        event_titles = {"EVT-1": {"mutually_exclusive": flag}}
        assert find_open_confirmed_conflict("BUS", market_titles, event_titles, {"BON"}) is None


def test_find_open_confirmed_conflict_ignores_a_different_event():
    market_titles = {
        "BON": {"event_ticker": "EVT-1"},
        "OTHER": {"event_ticker": "EVT-2"},
    }
    event_titles = {
        "EVT-1": {"mutually_exclusive": True},
        "EVT-2": {"mutually_exclusive": True},
    }
    assert find_open_confirmed_conflict("BON", market_titles, event_titles, {"OTHER"}) is None


def test_find_open_confirmed_conflict_never_returns_the_candidate_itself():
    market_titles = {"BON": {"event_ticker": "EVT-1"}}
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    assert find_open_confirmed_conflict("BON", market_titles, event_titles, {"BON"}) is None


def test_find_open_confirmed_conflict_counts_missing_market_titles_entry():
    before = me_pairing_stats()["me_pairing_unknown_total"]
    assert find_open_confirmed_conflict("UNKNOWN", {}, {}, {"BON"}) is None
    after = me_pairing_stats()["me_pairing_unknown_total"]
    assert after == before + 1


def test_find_open_confirmed_conflict_does_not_count_a_genuine_no_conflict():
    market_titles = {"BON": {"event_ticker": "EVT-1"}, "BUS": {"event_ticker": "EVT-1"}}
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    before = me_pairing_stats()["me_pairing_unknown_total"]
    assert find_open_confirmed_conflict("BUS", market_titles, event_titles, set()) is None
    assert me_pairing_stats()["me_pairing_unknown_total"] == before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python -m pytest tests/test_mutual_exclusivity.py -v -k find_open_confirmed_conflict`
Expected: FAIL with `ImportError: cannot import name 'find_open_confirmed_conflict'`.

- [ ] **Step 3: Implement in `services/mutual_exclusivity.py`**

Add near the top, after `_PRICE_SUM_TOLERANCE = 0.02`:

```python
# Lifetime counter (never reset except by process restart, same idiom as
# strategy_engine.py's own _me_gate_stats) - distinct from that module's
# me_gate_unknown_total, which tracks a different, not-yet-implemented
# gate (PR #202's parked event-scoped ME gate). This one counts how often
# find_open_confirmed_conflict couldn't determine an answer because
# market_titles had no cached entry yet for the candidate ticker (a
# brand-new market the catalog scan hasn't reached), as distinct from a
# genuine "checked, no conflict" result.
_me_pairing_stats = {"me_pairing_unknown_total": 0}


def me_pairing_stats() -> dict:
    """Pure read for observability - see _me_pairing_stats above."""
    return dict(_me_pairing_stats)
```

Add at the end of the file:

```python
def find_open_confirmed_conflict(
    ticker: str, market_titles: dict, event_titles: dict, open_position_tickers: set[str],
) -> str | None:
    """The ticker of a currently-open position that is Kalshi-confirmed
    mutually-exclusive with `ticker` (same event_ticker,
    event_titles[...].mutually_exclusive is True), or None.

    Unlike find_me_pairs (which needs both siblings in the same tick's
    REST-fetched `markets` batch - narrow, watchlist-scoped), this reads
    market_titles/event_titles: the persisted, catalog-wide caches
    (services/title_cache.py) that decision_bridge.py already reads for
    every signal regardless of watchlist membership. Works for a candidate
    ticker that has never been on the watchlist - see docs/superpowers/
    specs/2026-08-30-entry-gate-me-pairing-and-netting-remediation-design.md.

    Scoped to open_position_tickers (small, already computed once per tick
    at main.py's open_position_tickers) rather than scanning the full
    market_titles catalog by event_ticker - same cost shape as
    position_netting.find_groups, which already does this safely on the
    hot path. O(open positions), not O(catalog)."""
    info = market_titles.get(ticker)
    if info is None:
        _me_pairing_stats["me_pairing_unknown_total"] += 1
        return None
    event_ticker = info.get("event_ticker")
    if not event_ticker:
        return None
    if (event_titles.get(event_ticker) or {}).get("mutually_exclusive") is not True:
        return None
    for open_ticker in open_position_tickers:
        if open_ticker == ticker:
            continue
        if (market_titles.get(open_ticker) or {}).get("event_ticker") == event_ticker:
            return open_ticker
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_mutual_exclusivity.py -v`
Expected: PASS, all tests including the pre-existing `find_me_pairs` ones (unmodified).

- [ ] **Step 5: Commit**

```bash
git add services/mutual_exclusivity.py tests/test_mutual_exclusivity.py
git commit -m "feat: add broad-cache ME-pairing conflict check (Part 1 of entry-gate fix)"
```

---

## Task 2: Wire the new check into `decision_bridge.py`

**Files:**
- Modify: `services/whale_stream/decision_bridge.py`
- Modify: `tests/test_whale_stream_decision_bridge.py`

**Interfaces:**
- Consumes: `mutual_exclusivity.find_open_confirmed_conflict(...)` from Task 1.
- Produces: `_handle_signal`'s `strategy.evaluate(...)` call now receives a `me_complement` that also catches the broad-cache case — no change to `_handle_signal`'s own signature or return value.

- [ ] **Step 1: Write the failing tests**

In `tests/test_whale_stream_decision_bridge.py`, first extend `_FakeStrategy` to record the kwargs it was called with (additive — existing tests only check `.calls`, so this doesn't break them):

```python
class _FakeStrategy:
    """Stands in for services.app_state's real FollowTheWhaleStrategy -
    _handle_signal only needs .evaluate() to return an action dict shaped
    like strategy_engine.StrategyEngine._skip()'s real output; driving the
    real strategy would require a fully configured broker/risk stack this
    test doesn't need to prove ledger gating."""
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0
        self.last_kwargs = None

    def evaluate(self, signal, cfg, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return self.decision
```

Then add, near the bottom of the file:

```python
from services.app_state import state


def _set_me_state(monkeypatch, market_titles, event_titles, open_position_tickers, me_pairs=None):
    monkeypatch.setitem(state, "market_titles", market_titles)
    monkeypatch.setitem(state, "event_titles", event_titles)
    monkeypatch.setitem(state, "open_position_tickers", open_position_tickers)
    monkeypatch.setitem(state, "me_pairs", me_pairs or {})


def test_handle_signal_passes_broad_me_complement_when_watchlist_missed_it(monkeypatch):
    """The exact ATP-match failure mode this fix closes: BUS is a fresh
    candidate never on the watchlist, so state["me_pairs"] (built from the
    narrow per-tick markets list) has nothing for it - but
    market_titles/event_titles (the broad, persisted caches) and
    open_position_tickers (BON already open) are enough for the new
    fallback to find the conflict."""
    decision = {"action": "trade", "signal": {}, "reason": None}
    fake_strategy = _FakeStrategy(decision)
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)
    _set_me_state(
        monkeypatch,
        market_titles={
            "BON": {"event_ticker": "EVT-1"},
            "BUS": {"event_ticker": "EVT-1"},
        },
        event_titles={"EVT-1": {"mutually_exclusive": True}},
        open_position_tickers={"BON"},
    )

    signal = _make_signal(id="bus1", ticker="BUS", side="yes")
    asyncio.run(decision_bridge._handle_signal(signal, _base_cfg(), {}, "", 0.0))

    assert fake_strategy.last_kwargs["me_complement"] == "BON"


def test_handle_signal_prefers_existing_me_pairs_hit_over_the_new_fallback(monkeypatch):
    """state["me_pairs"] (the existing, narrower mechanism) still wins when
    it already has an answer - the new check is a fallback, not a
    replacement, and must not override it."""
    decision = {"action": "trade", "signal": {}, "reason": None}
    fake_strategy = _FakeStrategy(decision)
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)
    _set_me_state(
        monkeypatch,
        market_titles={"BUS": {"event_ticker": "EVT-1"}},  # no BON entry at all
        event_titles={"EVT-1": {"mutually_exclusive": True}},
        open_position_tickers=set(),  # nothing open - the new check alone would find nothing
        me_pairs={"BUS": "BON-FROM-OLD-MECHANISM"},
    )

    signal = _make_signal(id="bus2", ticker="BUS", side="yes")
    asyncio.run(decision_bridge._handle_signal(signal, _base_cfg(), {}, "", 0.0))

    assert fake_strategy.last_kwargs["me_complement"] == "BON-FROM-OLD-MECHANISM"


def test_handle_signal_me_complement_is_none_when_neither_mechanism_finds_a_conflict(monkeypatch):
    decision = {"action": "trade", "signal": {}, "reason": None}
    fake_strategy = _FakeStrategy(decision)
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)
    _set_me_state(
        monkeypatch, market_titles={}, event_titles={}, open_position_tickers=set(),
    )

    signal = _make_signal(id="bus3", ticker="BUS", side="yes")
    asyncio.run(decision_bridge._handle_signal(signal, _base_cfg(), {}, "", 0.0))

    assert fake_strategy.last_kwargs["me_complement"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python -m pytest tests/test_whale_stream_decision_bridge.py -v -k me_complement`
Expected: FAIL — `test_handle_signal_passes_broad_me_complement_when_watchlist_missed_it` fails because `me_complement` is `None` (old behavior), not `"BON"`.

- [ ] **Step 3: Implement in `services/whale_stream/decision_bridge.py`**

Add to the import block at the top:

```python
from services import candidate_ledger, mutual_exclusivity, signal_log, tick_executor, trade_category
```

Change line 107 from:

```python
    me_complement = (state.get("me_pairs") or {}).get(signal.ticker)
```

to:

```python
    me_complement = (state.get("me_pairs") or {}).get(signal.ticker) or \
        mutual_exclusivity.find_open_confirmed_conflict(
            signal.ticker, state["market_titles"], state["event_titles"], state["open_position_tickers"],
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_whale_stream_decision_bridge.py -v`
Expected: PASS, all tests in the file.

- [ ] **Step 5: Regression-check `strategy_engine.py`'s own gate (no code change expected)**

Run: `ddev exec -s fastapi python -m pytest tests/test_strategy_engine.py -v -k me_complement`
Expected: PASS with zero modifications to `services/strategy_engine.py` — confirms the spec's "no change needed" claim (its existing `if me_complement and me_complement in self.broker.positions:` gate already does the real membership check).

- [ ] **Step 6: Commit**

```bash
git add services/whale_stream/decision_bridge.py tests/test_whale_stream_decision_bridge.py
git commit -m "fix: entry gate now sees off-watchlist ME conflicts (closes the KXATPMATCH-style guaranteed-loss entry bug)"
```

---

## Task 3: `position_netting.describe_groups` reports `exit_fee_cost_usd` on `locked_loss`

**Files:**
- Modify: `services/exits/position_netting.py`
- Modify: `tests/test_position_netting.py`

**Interfaces:**
- Produces: `describe_groups(...)`'s `locked_loss` recommendation dict gains `"exit_fee_cost_usd": float` (rounded to 2dp). Absent (not `None`, not present as a key) on `locked_profit` and `variable` recommendations.

- [ ] **Step 1: Write the failing tests**

Modify the existing `test_describe_groups_locked_loss_recommends_close_all` test (around line 170) to also assert the new field — rename it to reflect the added assertion:

```python
def test_describe_groups_locked_loss_recommends_close_all_and_reports_exit_fee_cost():
    from services.paper_broker import Position
    a = Position(ticker="A", side="yes", size=100, entry_price=0.6, opened_at=time.time(), entry_fee=0.0)
    b = Position(ticker="B", side="yes", size=100, entry_price=0.6, opened_at=time.time(), entry_fee=0.0)

    class _FakeBroker:
        positions = {"A": a, "B": b}

    market_titles = _titles({"A": "EVT-1", "B": "EVT-1"})
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    latest_prices = {"A": 0.6, "B": 0.6}
    cfg = {"position_netting": {"enabled": True, "min_dwell_sec": 0}}
    groups = describe_groups(_FakeBroker(), market_titles, event_titles, latest_prices, cfg)
    rec = groups[0]["recommendation"]
    assert groups[0]["status"] == "locked_loss"
    assert rec["action"] == "close_all"
    assert set(rec["tickers"]) == {"A", "B"}
    expected_fee = taker_fee(100, 0.6, ticker="A") + taker_fee(100, 0.6, ticker="B")
    assert rec["exit_fee_cost_usd"] == round(expected_fee, 2)
```

Add a new test right after it:

```python
def test_describe_groups_variable_recommendation_has_no_exit_fee_cost_field(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("A", "yes", 200, 0.76, "r")
    broker.open_position("B", "no", 50, 0.25, "r")
    market_titles = _titles({"A": "EVT-1", "B": "EVT-1"})
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    latest_prices = {"A": 0.75, "B": 0.24}
    cfg = {"position_netting": {"enabled": True, "min_dwell_sec": 0, "min_edge_improvement_usd": 1.0, "normal_volatility": None}}
    groups = describe_groups(broker, market_titles, event_titles, latest_prices, cfg)
    assert "exit_fee_cost_usd" not in groups[0]["recommendation"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python -m pytest tests/test_position_netting.py -v -k "exit_fee_cost"`
Expected: FAIL with `KeyError: 'exit_fee_cost_usd'`.

- [ ] **Step 3: Implement in `services/exits/position_netting.py`**

Change the `locked_loss` branch inside `describe_groups` (around line 314) from:

```python
        elif status == "locked_loss":
            entry["recommendation"] = {
                "action": "close_all",
                "tickers": [t for t, _ in members],
                "reason": "payout is negative under every possible outcome - the loss is already fixed regardless of timing; closing now frees up bankroll/position headroom instead of leaving it dead until settlement",
            }
```

to:

```python
        elif status == "locked_loss":
            # Real, avoidable cost of closing now instead of holding to
            # Kalshi's fee-free settlement (kalshi_fees.taker_fee returns
            # 0.0 at price 0/1 - see this module's own top-of-file
            # docstring). Reported, not acted on: whether the bankroll/
            # position-headroom benefit below is worth this cost is an
            # open, unresolved tradeoff (docs/open-decisions.md) - this
            # only makes the number visible instead of buried in realized
            # P&L with no attribution.
            exit_fee_cost = sum(
                kalshi_fees.taker_fee(pos.size, latest_prices.get(t, pos.entry_price), ticker=t)
                for t, pos in members
            )
            entry["recommendation"] = {
                "action": "close_all",
                "tickers": [t for t, _ in members],
                "exit_fee_cost_usd": round(exit_fee_cost, 2),
                "reason": "payout is negative under every possible outcome - the loss is already fixed regardless of timing; closing now frees up bankroll/position headroom instead of leaving it dead until settlement",
            }
```

(`kalshi_fees` is already imported at the top of this file — no new import needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_position_netting.py -v`
Expected: PASS, all tests in the file (confirms the rename of the existing test didn't break anything else referencing its old name — grep the repo for the old test name first if unsure).

- [ ] **Step 5: Commit**

```bash
git add services/exits/position_netting.py tests/test_position_netting.py
git commit -m "feat: surface position_netting locked_loss exit-fee cost (no behavior change)"
```

---

## Task 4: Persist `netting_exit_fee_usd` on the trade row

**Files:**
- Modify: `services/exits/position_netting.py` (`review()`)
- Modify: `services/paper_broker.py` (`Trade`, `close_position`, `_add_column_if_missing`, the `INSERT INTO trades` statement)
- Modify: `tests/test_position_netting.py`
- Modify: `docs/open-decisions.md`

**Interfaces:**
- Consumes: `exit_fee_cost_usd` from Task 3's recommendation dict.
- Produces: `PaperBroker.close_position(..., netting_exit_fee_usd: float | None = None)`; `Trade.netting_exit_fee_usd: float | None`; `trades.netting_exit_fee_usd` column.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_position_netting.py`:

```python
def test_review_persists_exit_fee_cost_on_a_locked_loss_close(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("A", "yes", 100, 0.6, "r")
    broker.open_position("B", "yes", 100, 0.6, "r")
    market_titles = _titles({"A": "EVT-1", "B": "EVT-1"})
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    latest_prices = {"A": 0.6, "B": 0.6}
    cfg = {"position_netting": {"enabled": True, "min_dwell_sec": 0}}

    decisions = review(broker, market_titles, event_titles, latest_prices, cfg)
    assert {d["ticker"] for d in decisions} == {"A", "B"}

    expected_fee = round(taker_fee(100, 0.6, ticker="A") + taker_fee(100, 0.6, ticker="B"), 2)
    with sqlite3.connect(pb_module.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = {r["ticker"]: r for r in conn.execute(
            "SELECT ticker, netting_exit_fee_usd FROM trades WHERE reason LIKE 'closed: position netting%'"
        )}
    assert rows["A"]["netting_exit_fee_usd"] == expected_fee
    assert rows["B"]["netting_exit_fee_usd"] == expected_fee


def test_review_leaves_netting_exit_fee_usd_null_for_a_variable_close(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    market_titles, event_titles, latest_prices = _variable_pair(broker)
    now = time.time()
    _flat_history(("A",), now)
    _wiggly_history("B", now)
    cfg = _net_cfg(min_edge_improvement_usd=1.0)

    decisions = review(broker, market_titles, event_titles, latest_prices, cfg, now=now)
    assert [d["ticker"] for d in decisions] == ["B"]

    with sqlite3.connect(pb_module.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT netting_exit_fee_usd FROM trades WHERE ticker = 'B' AND reason LIKE 'closed: position netting%'"
        ).fetchone()
    assert row["netting_exit_fee_usd"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python -m pytest tests/test_position_netting.py -v -k netting_exit_fee_usd`
Expected: FAIL — `sqlite3.OperationalError: no such column: netting_exit_fee_usd`.

- [ ] **Step 3: Implement in `services/paper_broker.py`**

Add the field to the `Trade` dataclass, right after `netting_vol_ratio: float | None = None` (around line 119):

```python
    netting_vol_ratio: float | None = None
    # Real Kalshi exit-taker-fee cost of a locked_loss position_netting
    # close (services/exits/position_netting.py, docs/superpowers/specs/
    # 2026-08-30-entry-gate-me-pairing-and-netting-remediation-design.md
    # Part 2) - the module's own docstring argues unwinding a locked
    # position early only adds fee drag versus Kalshi's fee-free
    # settlement; this makes that cost measurable instead of buried in
    # realized P&L. None for every entry, every non-netting close, every
    # locked_profit/variable netting close, and every row written before
    # this existed.
    netting_exit_fee_usd: float | None = None
```

Add the column migration right after the existing three (around line 216):

```python
    _add_column_if_missing(conn, "trades", "netting_vol_ratio", "REAL")
    _add_column_if_missing(conn, "trades", "netting_exit_fee_usd", "REAL")
```

Update `close_position`'s signature and body (around line 547):

```python
    def close_position(
        self, ticker: str, exit_price: float, reason: str, *,
        netting_improvement_usd: float | None = None, netting_bar_usd: float | None = None,
        netting_vol_ratio: float | None = None, netting_exit_fee_usd: float | None = None,
    ) -> Trade | None:
```

In the same method, update the `Trade(...)` construction:

```python
        trade = Trade(
            id=str(uuid.uuid4())[:8],
            ticker=ticker,
            side=pos.side,
            size=pos.size,
            price=exit_price,
            reason=f"closed: {reason} (realized {realized_pnl:+.2f})",
            timestamp=time.time(),
            config_fingerprint=pos.config_fingerprint,
            fee=close_fee,
            netting_improvement_usd=netting_improvement_usd,
            netting_bar_usd=netting_bar_usd,
            netting_vol_ratio=netting_vol_ratio,
            netting_exit_fee_usd=netting_exit_fee_usd,
        )
```

And the `INSERT INTO trades` call in the same method:

```python
            conn.execute(
                "INSERT INTO trades (id, ticker, side, size, price, reason, timestamp, config_fingerprint, fee, "
                "netting_improvement_usd, netting_bar_usd, netting_vol_ratio, netting_exit_fee_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade.id, trade.ticker, trade.side, trade.size, trade.price, trade.reason, trade.timestamp,
                 trade.config_fingerprint, close_fee,
                 trade.netting_improvement_usd, trade.netting_bar_usd, trade.netting_vol_ratio,
                 trade.netting_exit_fee_usd),
            )
```

- [ ] **Step 4: Implement in `services/exits/position_netting.py`**

In `review()`, update the `broker.close_position(...)` call (around line 379):

```python
            trade = broker.close_position(
                ticker, price, reason,
                netting_improvement_usd=rec.get("expected_value_improvement_usd"),
                netting_bar_usd=rec.get("materiality_bar_usd"),
                netting_vol_ratio=rec.get("vol_ratio"),
                netting_exit_fee_usd=rec.get("exit_fee_cost_usd"),
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_position_netting.py -v`
Expected: PASS, all tests in the file, including the pre-existing
`test_review_persists_netting_decision_inputs_that_agree_with_the_reason_prose` (unmodified — confirms the new column doesn't disturb the existing three).

- [ ] **Step 6: Add the deferred-decision line to `docs/open-decisions.md`**

Append one line (this file's existing one-line-per-item format):

```
- `position_netting`'s `locked_loss` branch closes immediately (frees bankroll/position headroom) rather than holding to Kalshi's fee-free settlement; the fee cost is now measured (`trades.netting_exit_fee_usd`) but the tradeoff itself is unresolved · decide once a few weeks of the new column's data shows the real fee-vs-headroom tradeoff · you · 2026-08-30
```

- [ ] **Step 7: Commit**

```bash
git add services/paper_broker.py services/exits/position_netting.py tests/test_position_netting.py docs/open-decisions.md
git commit -m "feat: persist position_netting locked_loss exit-fee cost as a trades column (issue #213-style)"
```

---

## Task 5: `services/market_watch/milestone_scan.py` — broad milestone discovery

**Files:**
- Create: `services/market_watch/milestone_scan.py`
- Modify: `services/market_watch/__init__.py`
- Modify: `services/app_state.py`
- Modify: `main.py` (import block, `_SCHEDULER_TRIGGERS`)
- Modify: `services/diagnostics/routes.py` (`_scheduler_status`)
- Create: `tests/test_milestone_scan.py`

**Interfaces:**
- Produces: `state["milestone_by_event"]: dict[str, str]` (event_ticker -> milestone_id, broad, watchlist-independent).
- Produces: `_maybe_scan_milestone_batch(cfg: dict) -> None` (the scheduler trigger, registered in `main.py`'s `_SCHEDULER_TRIGGERS`).
- Consumes: `services.kalshi.public.KalshiPublicGateway.get_milestones_bulk(category: str, min_updated_ts: int | None = None, limit: int = 500) -> list[dict]` (already implemented, Task 5 is its first real caller).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_milestone_scan.py`:

```python
"""services/market_watch/milestone_scan.py - broad, watchlist-independent
event_ticker -> milestone_id discovery (docs/superpowers/specs/
2026-08-30-entry-gate-me-pairing-and-netting-remediation-design.md, Part
3). Mirrors tests/test_mve_scan.py's shape: a fake KalshiPublicGateway
stands in for the real gateway, app_state's real `state` dict is reset
between tests since this module reads/writes it directly."""
import asyncio

import pytest

from services.app_state import state
from services.market_watch import milestone_scan


@pytest.fixture(autouse=True)
def _isolated_state():
    state["milestone_scan"] = {"scanning": False, "last_started_at": 0.0, "task": None, "watermark": 0.0}
    state["milestone_by_event"] = {}
    yield


def _milestone(id_, related_event_tickers, last_updated_ts="2026-08-30T00:00:00Z"):
    return {"id": id_, "related_event_tickers": related_event_tickers, "last_updated_ts": last_updated_ts}


class _FakeMilestoneClient:
    def __init__(self, by_category):
        self._by_category = by_category  # category -> list[milestone dict]
        self.calls = []  # (category, min_updated_ts)

    async def get_milestones_bulk(self, category, min_updated_ts=None, limit=500):
        self.calls.append((category, min_updated_ts))
        return self._by_category.get(category, [])


def _cfg(categories):
    return {"kalshi": {"categories": categories}}


def test_scan_builds_the_broad_event_ticker_to_milestone_id_map():
    client = _FakeMilestoneClient({
        "Sports": [_milestone("ms-1", ["EVT-A", "EVT-B"])],
    })
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports"])))
    assert state["milestone_by_event"] == {"EVT-A": "ms-1", "EVT-B": "ms-1"}


def test_scan_covers_every_configured_category():
    client = _FakeMilestoneClient({
        "Sports": [_milestone("ms-1", ["EVT-A"])],
        "Politics": [_milestone("ms-2", ["EVT-C"])],
    })
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports", "Politics"])))
    assert state["milestone_by_event"] == {"EVT-A": "ms-1", "EVT-C": "ms-2"}
    assert {c for c, _ in client.calls} == {"Sports", "Politics"}


def test_scan_skips_milestones_with_no_related_event_tickers():
    client = _FakeMilestoneClient({"Sports": [_milestone("ms-1", [])]})
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports"])))
    assert state["milestone_by_event"] == {}


def test_scan_first_call_passes_no_watermark_then_advances_it():
    client = _FakeMilestoneClient({"Sports": []})
    assert state["milestone_scan"]["watermark"] == 0.0
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports"])))
    assert client.calls == [("Sports", None)]  # cold start - no watermark yet
    assert state["milestone_scan"]["watermark"] > 0.0


def test_scan_survives_one_category_failing(capsys):
    class _PartialFailClient(_FakeMilestoneClient):
        async def get_milestones_bulk(self, category, min_updated_ts=None, limit=500):
            if category == "Politics":
                raise RuntimeError("boom")
            return await super().get_milestones_bulk(category, min_updated_ts, limit)

    client = _PartialFailClient({"Sports": [_milestone("ms-1", ["EVT-A"])]})
    asyncio.run(milestone_scan._scan_milestone_batch(client, _cfg(["Sports", "Politics"])))
    assert state["milestone_by_event"] == {"EVT-A": "ms-1"}  # Sports still landed


def test_maybe_scan_milestone_batch_respects_the_due_interval():
    import time
    state["milestone_scan"]["last_started_at"] = time.time()  # just started
    milestone_scan._maybe_scan_milestone_batch(_cfg(["Sports"]))
    assert state["milestone_scan"]["scanning"] is False  # not due yet, nothing kicked off
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python -m pytest tests/test_milestone_scan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.market_watch.milestone_scan'`.

- [ ] **Step 3: Add state keys to `services/app_state.py`**

Add near the existing `"mve_scan"` / `"mve_series_cache"` entries:

```python
    "milestone_scan": {"scanning": False, "last_started_at": 0.0, "task": None, "watermark": 0.0},
    # Broad, watchlist-independent event_ticker -> milestone_id map
    # (services/market_watch/milestone_scan.py, issue: entry-gate-me-
    # pairing-and-netting-remediation Part 3) - independent of the per-tick
    # `markets` list live_status.py's _fetch_live_status otherwise depends
    # on for milestone lookup.
    "milestone_by_event": {},
```

- [ ] **Step 4: Create `services/market_watch/milestone_scan.py`**

```python
"""
Broad, watchlist-independent event_ticker -> milestone_id discovery
(docs/superpowers/specs/2026-08-30-entry-gate-me-pairing-and-netting-
remediation-design.md, Part 3). services/kalshi/public.py's
get_milestones_bulk (category-scoped, batched - live-verified 2026-08-15:
one call covered 1,483 distinct related_event_tickers) has been
implemented and tested since before this module existed, but had zero
callers anywhere in the app - confirmed by repo-wide grep, not assumed.
The only wired milestone lookup (services/market_watch/live_status.py's
_fetch_live_status) iterates the same per-tick watchlist-scoped `markets`
list the entry-gate ME-pairing bug (services/mutual_exclusivity.py) does,
so today score/clock capture (services/game_state.py) only ever sees
whatever's on the watchlist.

This module does not fetch live data itself - only WHICH events have a
milestone and what its id is, independent of markets. live_status.py's
_fetch_live_status checks state["milestone_by_event"] first and only
falls back to its existing per-event get_milestones_for_event call when
this broad cache hasn't covered an event yet, so a cold cache is
byte-identical to pre-existing behavior.

Deliberately NOT rewiring catalog_scan.propagate_milestone_winners onto
this same cache (it shares the identical narrow pattern, found during
this module's own design spec's self-review) - that function feeds
settlement-outcome data real trading decisions consume, so touching it is
its own, separately-reviewed follow-up.
"""
import asyncio
import time

from services import http_client, task_supervisor
from services.app_state import state
from services.kalshi.public import KalshiPublicGateway

_MILESTONE_SCAN_MIN_INTERVAL_SEC = 300  # Discovery only (which events have
# a milestone + its id), not the live score/clock read itself - that stays
# on live_status.py's own, more frequent _LIVE_STATUS_REPOLL_SEC (5 min)
# cadence. A starting point, not a measured optimum - see the design
# spec's Part 3 "Verification after shipping" section.


async def _scan_milestone_batch(client: KalshiPublicGateway, cfg: dict) -> None:
    """One get_milestones_bulk call per configured category
    (cfg["kalshi"]["categories"]), watermarked since this function's own
    last successful run (a single scalar shared across every category in
    one cycle - the cycle's own start time, so nothing created mid-cycle
    is missed). Every related_event_ticker on every returned milestone
    maps to that milestone's id in state["milestone_by_event"] -
    last-write-wins on a collision, the same plain-dict-overwrite
    convention every other cache in this package uses (mve_scan's
    new_event_titles, event_metadata's _fetch_event_titles). A category
    call that raises is logged and skipped for this cycle, same posture as
    mve_scan._scan_mve_batch's own per-series failure handling - no
    logging framework exists yet (ddev logs -s fastapi is the visibility
    path), and the rest of the batch still lands."""
    milestone_state = state["milestone_scan"]
    watermark = milestone_state["watermark"] or None
    scan_started_at = time.time()
    categories = cfg["kalshi"]["categories"]
    results = await asyncio.gather(
        *(client.get_milestones_bulk(category, min_updated_ts=watermark) for category in categories),
        return_exceptions=True,
    )
    for category, result in zip(categories, results):
        if not isinstance(result, list):
            print(f"[milestone_scan] scan failed for category {category!r}, will retry next cycle: {result!r}")
            continue
        for ms in result:
            ms_id = ms.get("id")
            if not ms_id:
                continue
            for event_ticker in ms.get("related_event_tickers") or []:
                state["milestone_by_event"][event_ticker] = ms_id
    milestone_state["watermark"] = scan_started_at


def _maybe_scan_milestone_batch(cfg: dict) -> None:
    """Triggers _scan_milestone_batch as an independent background task on
    its own steady interval - mirrors mve_scan._maybe_scan_mve_batch
    exactly (same overlap guard shape, same task_supervisor wiring)."""
    milestone_state = state["milestone_scan"]
    now_ts = time.time()
    due = now_ts - milestone_state["last_started_at"] > _MILESTONE_SCAN_MIN_INTERVAL_SEC
    if due and not milestone_state["scanning"]:
        milestone_state["scanning"] = True
        milestone_state["last_started_at"] = now_ts
        milestone_state["task"] = task_supervisor.supervise(
            lambda: _scan_milestone_batch_background(cfg),
            component="milestone_scan", operation="scan_batch",
        )


@http_client.classify("background_catalog")
async def _scan_milestone_batch_background(cfg: dict) -> None:
    """Owns its own KalshiPublicGateway - same reasoning as
    mve_scan._scan_mve_batch_background (the calling tick's own client
    closes at the end of that same tick)."""
    milestone_state = state["milestone_scan"]
    client = KalshiPublicGateway(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        await _scan_milestone_batch(client, cfg)
    finally:
        milestone_state["scanning"] = False
        await client.close()
```

- [ ] **Step 5: Re-export from `services/market_watch/__init__.py`**

Add, after the existing `mve_scan` import block:

```python
from services.market_watch import milestone_scan  # noqa: F401
from services.market_watch.milestone_scan import (  # noqa: F401
    _maybe_scan_milestone_batch, _MILESTONE_SCAN_MIN_INTERVAL_SEC, _scan_milestone_batch,
    _scan_milestone_batch_background,
)
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_milestone_scan.py -v`
Expected: PASS, all tests in the file.

- [ ] **Step 7: Register the scheduler in `main.py`**

Add to the `from services.market_watch import (...)` block (alongside the existing `_maybe_scan_mve_batch` import):

```python
    _LIVE_STATUS_MAX_POLL_PER_TICK, _LIVE_STATUS_REPOLL_SEC, _maybe_scan_catalog_batch,
    _maybe_scan_milestone_batch, _maybe_scan_mve_batch, _MILESTONE_REPOLL_SEC, propagate_milestone_winners,
    _refresh_discovery_cache, _refresh_discovery_cache_background, _scan_catalog_batch, _slim_market,
```

Add to `_SCHEDULER_TRIGGERS` (right after `mve_scan`):

```python
    ("mve_scan", _maybe_scan_mve_batch),
    # Broad milestone discovery (entry-gate-me-pairing-and-netting-
    # remediation Part 3) - independent of `markets`/watchlist scope, see
    # services/market_watch/milestone_scan.py's own module docstring.
    ("milestone_scan", _maybe_scan_milestone_batch),
    ("auto_apply", _maybe_run_auto_apply),
```

- [ ] **Step 8: Wire into `/api/health/pipeline`'s scheduler status (`services/diagnostics/routes.py`)**

Add a line to `_scheduler_status`'s returned dict, alongside the existing `"mve_scan"` line:

```python
        "mve_scan": _entry("mve_scan", "last_started_at", "scanning"),
        "milestone_scan": _entry("milestone_scan", "last_started_at", "scanning"),
```

- [ ] **Step 9: Run the full test suite for touched files**

Run: `ddev exec -s fastapi python -m pytest tests/test_milestone_scan.py tests/test_mve_scan.py tests/test_trading_gate.py -v`
Expected: PASS. (`test_trading_gate.py` is included because it holds the pipeline-health/scheduler-status tests this step touches.)

- [ ] **Step 10: Commit**

```bash
git add services/market_watch/milestone_scan.py services/market_watch/__init__.py services/app_state.py main.py services/diagnostics/routes.py tests/test_milestone_scan.py
git commit -m "feat: add milestone_scan scheduler - wires the unused, tested get_milestones_bulk into a broad event_ticker->milestone_id cache"
```

---

## Task 6: Consume the broad cache in `_fetch_live_status`

**Files:**
- Modify: `services/market_watch/live_status.py`
- Modify: `tests/test_trading_gate.py`

**Interfaces:**
- Consumes: `state["milestone_by_event"]` from Task 5.
- No change to `_fetch_live_status`'s own signature or return shape.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_trading_gate.py`, near the existing `_FakeLiveClient`-based tests:

```python
def test_fetch_live_status_uses_broad_milestone_cache_and_skips_the_per_event_call():
    main.state["live_status_cache"].clear()
    main.state["milestone_by_event"] = {"EVT-A": "ms-from-bulk-scan"}
    fake = _FakeLiveClient(widget_status="live", has_milestone=True)
    markets = [_market_at(offset_sec=-300)]
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "live"}
    assert fake.milestone_calls == []  # broad cache already had it - no per-event REST call needed
    assert fake.live_datas_calls == [["ms-from-bulk-scan"]]


def test_fetch_live_status_falls_back_to_per_event_call_when_broad_cache_misses():
    main.state["live_status_cache"].clear()
    main.state["milestone_by_event"] = {}  # cold cache - milestone_scan hasn't reached this event yet
    fake = _FakeLiveClient(widget_status="live", has_milestone=True)
    markets = [_market_at(offset_sec=-300)]
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "live"}
    assert fake.milestone_calls == ["EVT-A"]  # unchanged, pre-existing behavior
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python -m pytest tests/test_trading_gate.py -v -k broad_milestone_cache`
Expected: FAIL — `test_fetch_live_status_uses_broad_milestone_cache_and_skips_the_per_event_call` fails because `fake.milestone_calls` is `["EVT-A"]`, not `[]` (the cache isn't consulted yet).

- [ ] **Step 3: Implement in `services/market_watch/live_status.py`**

Replace this block inside `_fetch_live_status` (around line 145):

```python
    milestone_results = await asyncio.gather(
        *(client.get_milestones_for_event(et) for et in to_poll), return_exceptions=True
    )
    # Batched (2026-08-16 API-doc audit finding B3.2, docs/kalshi/
    # get-multiple-live-data.md) - was N individual get_live_data() calls via
    # asyncio.gather, one per event with a milestone. Live-verified: 3
    # individual = 0.99s wall, 1 batched get_live_datas call = 0.02s wall.
    milestone_by_event = {}
    has_milestone = set()
    for et, ms_result in zip(to_poll, milestone_results):
        if isinstance(ms_result, list) and ms_result:
            ms = ms_result[0]
            if ms.get("id") and ms.get("type"):
                has_milestone.add(et)
                milestone_by_event[et] = ms["id"]
```

with:

```python
    # Broad, watchlist-independent cache first (services/market_watch/
    # milestone_scan.py, entry-gate-me-pairing-and-netting-remediation
    # Part 3) - an event already covered there skips the per-event REST
    # call entirely. A cache miss falls back to the exact pre-existing
    # per-event get_milestones_for_event call, so a cold/not-yet-covered
    # cache reproduces today's behavior byte for byte.
    broad_cache = state["milestone_by_event"]
    milestone_by_event: dict[str, str] = {}
    has_milestone: set[str] = set()
    needs_fetch = []
    for et in to_poll:
        ms_id = broad_cache.get(et)
        if ms_id:
            milestone_by_event[et] = ms_id
            has_milestone.add(et)
        else:
            needs_fetch.append(et)

    if needs_fetch:
        # Batched (2026-08-16 API-doc audit finding B3.2, docs/kalshi/
        # get-multiple-live-data.md) - was N individual get_live_data()
        # calls via asyncio.gather, one per event with a milestone.
        # Live-verified: 3 individual = 0.99s wall, 1 batched get_live_datas
        # call = 0.02s wall.
        milestone_results = await asyncio.gather(
            *(client.get_milestones_for_event(et) for et in needs_fetch), return_exceptions=True
        )
        for et, ms_result in zip(needs_fetch, milestone_results):
            if isinstance(ms_result, list) and ms_result:
                ms = ms_result[0]
                if ms.get("id") and ms.get("type"):
                    has_milestone.add(et)
                    milestone_by_event[et] = ms["id"]
```

The rest of the function (the `live_datas = await client.get_live_datas(list(milestone_by_event.values()))` block onward) is unchanged — it already only depends on `milestone_by_event`/`has_milestone`, both still populated the same way.

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_trading_gate.py -v`
Expected: PASS, all tests in the file — including every pre-existing `_fetch_live_status` test (they all leave `main.state["milestone_by_event"]` at whatever the previous test left it; if any pre-existing test now fails because a leftover cache entry short-circuits its REST-call assertion, add `main.state["milestone_by_event"] = {}` to that test rather than changing this task's implementation — isolation gap in the test, not a defect in the fix.

- [ ] **Step 5: Verify coverage end to end (manual, in the running app)**

Per the spec's "Verification after shipping" section — not a unit test, a live check:

```bash
curl -s https://kalshi-whale-poc.ddev.site:8443/api/health/pipeline -k | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['schedulers'].get('milestone_scan'))"
```

Expected: a dict with `last_started_sec_ago`/`busy` keys (confirms Task 5's scheduler is actually firing, not just registered). Then, after letting it run for a few cycles:

```python
from services import game_state
print(game_state.stats())
```

(via `ddev exec -s fastapi python -c "..."`) — compare `distinct_events`/`with_score` against a pre-change baseline if one was captured; a real increase confirms broadened coverage materialized, not just that the code path exists.

- [ ] **Step 6: Commit**

```bash
git add services/market_watch/live_status.py tests/test_trading_gate.py
git commit -m "feat: _fetch_live_status checks the broad milestone cache before its per-event REST call"
```

---

## Self-Review Notes (completed during plan authoring)

- **Spec coverage:** Part 1 → Tasks 1-2. Part 2 → Tasks 3-4 (including the deferred-decision `open-decisions.md` line from the spec's Part 2). Part 3 → Tasks 5-6 (including the pipeline-health wiring and the "second consumer, deliberately out of scope" note carried as a code comment in Task 5's new module, not re-implemented).
- **Placeholder scan:** none found — every step has real, repo-verified code (exact signatures for `Trade`, `close_position`, `_add_column_if_missing`, `_SCHEDULER_TRIGGERS`, `_scheduler_status`, `get_milestones_bulk` all confirmed by reading the actual files, not assumed).
- **Type consistency:** `find_open_confirmed_conflict` returns `str | None` in Task 1 and is consumed as such (via `or`) in Task 2. `exit_fee_cost_usd` (dict key, Task 3) → `netting_exit_fee_usd` (keyword arg and column name, Task 4) is a deliberate rename at the persistence boundary, matching the existing convention where `rec["expected_value_improvement_usd"]` becomes the `netting_improvement_usd` column — not an inconsistency.
- **Task Right-Sizing check:** Tasks 1/2 and 5/6 are each split at the exact point where "correct in isolation" and "correctly wired into the live path" become separately reviewable/testable claims; Tasks 3/4 are split at "computed" vs. "persisted." No task depends on a file another task in this plan hasn't yet created.
