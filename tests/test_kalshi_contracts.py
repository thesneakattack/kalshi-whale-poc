"""
Fixture-based Kalshi contract tests (QCP Task 13,
docs/superpowers/plans/2026-08-24-quality-control-plane.md) - feeds
canonical, doc-sourced message/object shapes from tests/fixtures/kalshi/
into the SAME functions production uses (never re-implements or
re-derives a parser here), so a future Kalshi field rename is caught by a
failing assertion instead of a silent no-op in production.

Every fixture file has a top-level "_meta" key recording which
docs/kalshi/ page it was built from (Step 1's own requirement: "do not put
undocumented invented fields into fixtures"). Three of these tests exist
because building this file surfaced REAL, currently-shipping bugs - fixed
in the same commit as these tests, not left as documented-but-broken:

1. _process_stream_fill keyed fill identity on "fill_id", a field that
   does not exist at all on the real WS user-fills message (only on the
   REST GetFills schema) - every real WS fill was silently discarded.
2. services/kalshi_trade_ws.py's _handle_message dispatched on the
   per-message `type` field against "market_positions" (the plural
   subscription CHANNEL name) instead of "market_position" (the real,
   singular, per-message type) - on_position was never even invoked for a
   real position update.
3. _process_stream_position read "ticker" from the position payload, but
   the real WS message carries "market_ticker" instead (a genuine
   REST-vs-WS naming split, same shape as expected_expiration_time/
   expected_expiration_ts documented in docs/kalshi/CHEATSHEET.md).

None of these could have been caught by live traffic before this session:
kalshi_account.trading_enabled has always been false (CLAUDE.md's P0
safety gate), so no real fill or position update has ever reached this
app's account-message handlers.

Relies on tests/conftest.py's global install_runtime_isolation() for
every registered persistence module's DB_PATH, same as
tests/test_quality_routes.py.
"""
import asyncio
import copy
import json
from pathlib import Path

import pytest

import main  # noqa: E402
from services.kalshi_account_client import KalshiAccountClient  # noqa: E402
from services.kalshi_trade_ws import KalshiTradeWebSocketClient  # noqa: E402
from services.market_watch import _MARKET_FIELDS  # noqa: E402
from services.whalewatchers.kalshi_trade_tape import _notional_usd, _taker_side  # noqa: E402

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "kalshi"

# main.state is one process-global dict shared by every test file in this
# suite (see tests/test_trading_gate.py's own module docstring on why
# DB_PATH redirection alone doesn't cover it). This file's own tests are
# the first ones to touch account/latest_prices/markets/lifecycle_stream_
# stats/event_titles by wholesale-replacing them rather than mutating a
# known sub-field - real, live-caught leak: an earlier draft left
# state["account"] missing "trading_enabled" after this file's fill/
# position tests ran, breaking an unrelated GET /api/state assertion in
# tests/test_trading_gate.py purely from collection order (this file
# alphabetically precedes it). Snapshot/restore every key this file
# touches, regardless of which individual test touches which key.
_STATE_KEYS_TOUCHED = ("account", "latest_prices", "markets", "lifecycle_stream_stats", "event_titles")


@pytest.fixture(autouse=True)
def _restore_shared_state():
    snapshot = {key: copy.deepcopy(main.state.get(key)) for key in _STATE_KEYS_TOUCHED}
    yield
    for key, value in snapshot.items():
        main.state[key] = value


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _payload(name: str) -> dict:
    fixture = _fixture(name)
    assert "source_doc" in fixture["_meta"], f"{name} is missing a _meta.source_doc pointer"
    return fixture["payload"]


def _reset_account_state(connected: bool = True, fills=None, positions=None) -> None:
    """main.state is a single process-global dict shared by every test file
    in this suite - replacing state["account"] wholesale with a stripped
    dict (missing e.g. trading_enabled) leaks into unrelated tests in
    tests/test_trading_gate.py that assume the full default shape
    (services/app_state.py's own "account" literal) stays intact. Always
    restore every key that shape has, not just the ones a given test
    happens to touch."""
    main.state["account"] = {
        "connected": connected, "balance": None,
        "positions": positions if positions is not None else {"market_positions": [], "event_positions": []},
        "fills": fills if fills is not None else {"fills": []},
        "error": None, "trading_enabled": False,
    }


# --- trade: normalizer (services/kalshi_trade_ws.py) + provider parsing ---
# (services/whalewatchers/kalshi_trade_tape.py) -----------------------------


def test_normalize_trade_keeps_all_three_direction_fields():
    trade = _payload("public_trade.json")

    normalized = KalshiTradeWebSocketClient.normalize_trade(trade)

    assert normalized["ticker"] == "HIGHNY-22DEC23-B53.5"
    assert normalized["taker_outcome_side"] == "no"
    assert normalized["taker_book_side"] == "ask"
    assert normalized["taker_side"] == "no"  # canonical and legacy agree here


def test_normalize_trade_prefers_canonical_field_when_legacy_taker_side_is_absent():
    # The exact historical bug class this fixture set exists to catch: the
    # old code read only the deprecated taker_side and defaulted anything
    # unreadable to "no" - see docs/kalshi/CHEATSHEET.md's own entry.
    trade = _payload("public_trade_no_deprecated_side.json")

    normalized = KalshiTradeWebSocketClient.normalize_trade(trade)

    assert normalized["taker_outcome_side"] == "yes"
    assert normalized["taker_side"] == "yes"  # NOT the "no" a naive default would produce


def test_taker_side_resolves_from_canonical_field():
    trade = _payload("public_trade.json")

    assert _taker_side(trade) == "no"


def test_taker_side_returns_none_rather_than_guessing_when_direction_is_unreadable():
    trade = dict(_payload("public_trade.json"))
    del trade["taker_outcome_side"], trade["taker_book_side"], trade["taker_side"]

    assert _taker_side(trade) is None


def test_notional_usd_uses_the_takers_own_side_price():
    trade = _payload("public_trade.json")
    side = _taker_side(trade)

    notional = _notional_usd(trade, side)

    assert side == "no"
    assert notional == round(136.00 * 0.640, 6)


# --- ticker: services/whale_stream/whale_stream_handlers.py::_process_stream_ticker ---


def test_process_stream_ticker_reads_documented_price_fields():
    main.state["latest_prices"] = {}
    main.state["markets"] = [{"ticker": "FED-23DEC-T3.00", "yes_ask_dollars": None}]
    ticker_msg = _payload("market_ticker.json")

    asyncio.run(main._process_stream_ticker(ticker_msg))

    assert main.state["latest_prices"]["FED-23DEC-T3.00"] == 0.450  # yes_bid_dollars
    assert main.state["markets"][0]["yes_ask_dollars"] == "0.530"


# --- fill: services/whale_stream/whale_stream_handlers.py::_process_stream_fill ---


def test_process_stream_fill_records_a_real_ws_fill_by_trade_id():
    _reset_account_state()
    fill_msg = _payload("fill.json")
    assert "fill_id" not in fill_msg  # confirms the fixture matches the real (fill_id-less) wire shape

    asyncio.run(main._process_stream_fill(fill_msg))

    fills = main.state["account"]["fills"]["fills"]
    assert len(fills) == 1
    assert fills[0]["trade_id"] == "d91bc706-ee49-470d-82d8-11418bda6fed"
    assert fills[0]["market_ticker"] == "HIGHNY-22DEC23-B53.5"


def test_process_stream_fill_dedupes_a_repeated_message_by_trade_id():
    _reset_account_state()
    fill_msg = _payload("fill.json")

    asyncio.run(main._process_stream_fill(fill_msg))
    asyncio.run(main._process_stream_fill(fill_msg))

    assert len(main.state["account"]["fills"]["fills"]) == 1


def test_process_stream_fill_is_a_noop_when_account_not_connected():
    _reset_account_state(connected=False)

    asyncio.run(main._process_stream_fill(_payload("fill.json")))

    assert main.state["account"]["fills"]["fills"] == []


# --- position: dispatch (services/kalshi_trade_ws.py) + field extraction ---
# (services/whale_stream/whale_stream_handlers.py::_process_stream_position) -


def test_market_position_envelope_dispatches_to_on_position():
    client = KalshiTradeWebSocketClient("https://external-api.kalshi.com/trade-api/v2")
    envelope = _fixture("market_position_envelope.json")
    received = []

    async def on_position(msg):
        received.append(msg)

    asyncio.run(client._handle_message(
        json.dumps(envelope["payload"]), on_trade=None, on_ticker=None, on_status=None, on_position=on_position,
    ))

    assert len(received) == 1
    assert received[0]["market_ticker"] == "FED-23DEC-T3.00"


def test_process_stream_position_reads_market_ticker_and_normalizes_to_ticker():
    _reset_account_state()
    position_msg = _payload("market_position.json")
    assert "ticker" not in position_msg  # confirms the fixture matches the real (ticker-less) wire shape

    asyncio.run(main._process_stream_position(position_msg))

    market_positions = main.state["account"]["positions"]["market_positions"]
    assert len(market_positions) == 1
    assert market_positions[0]["ticker"] == "FED-23DEC-T3.00"


def test_process_stream_position_updates_an_existing_entry_in_place():
    _reset_account_state(
        positions={"market_positions": [{"ticker": "FED-23DEC-T3.00", "position_fp": "1.00"}], "event_positions": []},
    )
    position_msg = _payload("market_position.json")

    asyncio.run(main._process_stream_position(position_msg))

    market_positions = main.state["account"]["positions"]["market_positions"]
    assert len(market_positions) == 1  # replaced, not appended
    assert market_positions[0]["position_fp"] == "100.00"


# --- lifecycle: services/whale_stream/whale_stream_handlers.py::_process_stream_lifecycle ---
# Lean - tests/test_trading_gate.py already has exhaustive coverage of the
# determined-vs-finalized distinction (test_lifecycle_determined_updates_
# catalog_status_but_does_not_resolve_outcome and neighbors). These two
# just add fixture-file source-doc traceability per Step 1's requirement.


def test_lifecycle_determined_does_not_resolve_outcome():
    main.state["lifecycle_stream_stats"] = {
        "events_by_type": {}, "close_time_updates_applied": 0, "last_event_at": None,
        "catalog_updates_applied": 0, "outcomes_resolved_via_lifecycle": 0,
    }

    asyncio.run(main._process_stream_lifecycle(_payload("market_lifecycle_determined.json")))

    stats = main.state["lifecycle_stream_stats"]
    assert stats["events_by_type"]["determined"] == 1
    assert stats["outcomes_resolved_via_lifecycle"] == 0


def test_lifecycle_settled_payload_carries_no_result_field():
    # The doc-verified reason _process_stream_lifecycle's settled branch
    # does a fresh REST read rather than trusting this event's own payload.
    settled = _payload("market_lifecycle_settled.json")

    assert "result" not in settled


# --- market REST object: services/market_watch/market_fetch.py::_slim_market ---


def test_slim_market_keeps_every_documented_field_it_declares():
    market = _payload("market.json")

    slimmed = main._slim_market(market)

    assert set(slimmed.keys()) == set(_MARKET_FIELDS)
    assert slimmed["ticker"] == "HIGHNY-22DEC23-B53.5"
    assert slimmed["expected_expiration_time"] == "2022-12-23T12:00:00Z"


# --- event REST object: services/market_watch/event_metadata.py::_fetch_event_titles ---


class _FakeEventsClient:
    def __init__(self, events: list[dict]):
        self._events = events

    async def get_events(self, event_tickers):
        return [e for e in self._events if e.get("event_ticker") in event_tickers]


def test_fetch_event_titles_reads_sub_title_not_subtitle():
    # Real historical bug (services/market_watch/event_metadata.py's own
    # docstring): event.get("subtitle") looked plausible but silently
    # returned None for every event - the real field is sub_title.
    main.state["event_titles"] = {}
    event = _payload("event.json")
    fake_client = _FakeEventsClient([event])
    markets = [{"ticker": "T-A", "event_ticker": event["event_ticker"]}]

    result = asyncio.run(main._fetch_event_titles(fake_client, markets))

    assert result[event["event_ticker"]]["sub_title"] == "SD vs AZ (Dec 23)"
    assert result[event["event_ticker"]]["mutually_exclusive"] is True


# --- create/cancel order: services/kalshi_account_client.py -----------------
# Lean - tests/test_kalshi_account_client.py already has exhaustive
# coverage (test_create_order_uses_v2_shape/test_cancel_order_uses_v2_shape).
# These add fixture-file source-doc traceability.


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self, mode="json"):
        return self._payload


class _FakeOrderSDKClient:
    def __init__(self, cancel_response: dict):
        self.calls = []
        self._cancel_response = cancel_response

    async def create_order_v2(self, **kwargs):
        self.calls.append(("create_order_v2", kwargs))
        return _FakeResp({"ok": True})

    async def cancel_order_v2(self, order_id, **kwargs):
        self.calls.append(("cancel_order_v2", {"order_id": order_id, **kwargs}))
        return _FakeResp(self._cancel_response)


def test_create_order_supplies_every_documented_required_field():
    fixture = _fixture("create_order_request.json")
    order = fixture["payload"]
    fake = _FakeOrderSDKClient(cancel_response={})
    client = KalshiAccountClient(base_url="https://example.test/trade-api/v2", request_timeout_sec=5, trading_enabled=True)
    client._client = fake

    asyncio.run(client.create_order(
        ticker=order["ticker"], side=order["side"], count=order["count"], price=order["price"],
        time_in_force=order["time_in_force"], self_trade_prevention_type=order["self_trade_prevention_type"],
    ))

    _, kwargs = fake.calls[0]
    for field in fixture["required_fields"]:
        assert field in kwargs, f"create_order_v2 call is missing documented required field {field!r}"


def test_cancel_order_returns_the_documented_v2_response_shape():
    response = _payload("cancel_order_response.json")
    fake = _FakeOrderSDKClient(cancel_response=response)
    client = KalshiAccountClient(base_url="https://example.test/trade-api/v2", request_timeout_sec=5, trading_enabled=True)
    client._client = fake

    result = asyncio.run(client.cancel_order(response["order_id"]))

    assert result == response
    assert set(result.keys()) == {"order_id", "client_order_id", "reduced_by", "ts_ms"}


# --- meta: every fixture actually has a source_doc pointer -----------------


def test_every_fixture_file_documents_its_source():
    fixture_files = sorted(_FIXTURES_DIR.glob("*.json"))
    assert len(fixture_files) >= 9  # one per Step 1 message/request type, at minimum
    for path in fixture_files:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        assert "_meta" in fixture, f"{path.name} has no _meta block"
        assert fixture["_meta"].get("source_doc"), f"{path.name}'s _meta has no source_doc"
        assert "payload" in fixture, f"{path.name} has no payload block"


# --- A10: channel-specific semantic normalizers (services/kalshi/contracts/) ---
# One implementation per WS channel's known high-risk semantics, fed by the
# same doc-sourced fixtures as everything above. Imports are inside the
# tests (not module-level) so a missing module fails ITS tests, not this
# whole file's collection.


def test_ws_client_trade_normalizer_is_the_boundary_implementation():
    from services.kalshi.contracts import trade as trade_contract
    # Delegation without re-implementation: the compatibility staticmethod
    # and the boundary function must be the same object, so the two can
    # never drift apart.
    assert KalshiTradeWebSocketClient.normalize_trade is trade_contract.normalize_trade


def test_trade_normalizer_leaves_direction_none_when_unreadable():
    from services.kalshi.contracts import trade as trade_contract
    msg = dict(_payload("public_trade.json"))
    del msg["taker_outcome_side"], msg["taker_side"]
    normalized = trade_contract.normalize_trade(msg)
    # No guess: an unreadable direction stays unreadable (the old
    # pre-2026-08-17 code turned every unreadable trade into a confident
    # "no" - the exact bug class this fixture set exists to prevent).
    assert normalized["taker_outcome_side"] is None
    assert normalized["taker_side"] is None
    assert normalized["taker_book_side"] == "ask"  # raw field still passes through


def test_trade_normalizer_preserves_every_raw_field():
    from services.kalshi.contracts import trade as trade_contract
    msg = _payload("public_trade.json")
    normalized = trade_contract.normalize_trade(msg)
    for key, value in msg.items():
        if key in ("taker_side", "taker_outcome_side"):
            continue  # deliberately overlaid with canonical-first precedence
        assert normalized[key] == value, f"raw field {key!r} was shaved off"


def test_ticker_normalizer_aliases_market_ticker_and_preserves_raw():
    from services.kalshi.contracts import ticker as ticker_contract
    msg = _payload("market_ticker.json")
    normalized = ticker_contract.normalize_ticker(msg)
    assert normalized["ticker"] == "FED-23DEC-T3.00"
    assert normalized["market_ticker"] == "FED-23DEC-T3.00"
    for key, value in msg.items():
        assert normalized[key] == value


def test_fill_normalizer_keeps_ws_trade_id_identity_and_never_invents_fill_id():
    from services.kalshi.contracts import fill as fill_contract
    msg = _payload("fill.json")
    assert "fill_id" not in msg  # the real WS wire shape has no fill_id
    normalized = fill_contract.normalize_fill(msg)
    assert normalized["trade_id"] == "d91bc706-ee49-470d-82d8-11418bda6fed"
    assert "fill_id" not in normalized  # REST-only name must not be invented here
    assert normalized["ticker"] == "HIGHNY-22DEC23-B53.5"
    for key, value in msg.items():
        assert normalized[key] == value


def test_position_message_type_is_singular_and_channel_is_plural():
    from services.kalshi.contracts import position as position_contract
    # The exact dispatch bug QCP Task 13 caught: per-message `type` is
    # "market_position" (singular, market-positions.md's own
    # `const: market_position`); the *subscription channel* is
    # "market_positions" (plural). Both spellings now live in one module.
    assert position_contract.WS_MESSAGE_TYPE == "market_position"
    assert position_contract.SUBSCRIPTION_CHANNEL == "market_positions"
    assert position_contract.WS_MESSAGE_TYPE != position_contract.SUBSCRIPTION_CHANNEL


def test_position_normalizer_aliases_market_ticker_and_preserves_raw():
    from services.kalshi.contracts import position as position_contract
    msg = _payload("market_position.json")
    assert "ticker" not in msg  # real WS wire shape carries market_ticker only
    normalized = position_contract.normalize_position(msg)
    assert normalized["ticker"] == "FED-23DEC-T3.00"
    for key, value in msg.items():
        assert normalized[key] == value


def test_lifecycle_determined_does_not_resolve_settled_does():
    from services.kalshi.contracts import lifecycle as lifecycle_contract
    # determined is NOT terminal (market_lifecycle.md: the result may be
    # disputed -> amended before "finalized"); only settled may trigger
    # outcome resolution, and even then via a fresh REST read gated on
    # status == "finalized", because the settled payload carries no result.
    assert lifecycle_contract.resolves_outcome("determined") is False
    assert lifecycle_contract.resolves_outcome("settled") is True
    assert lifecycle_contract.resolves_outcome("close_date_updated") is False
    assert lifecycle_contract.resolves_outcome(None) is False
    assert lifecycle_contract.TERMINAL_REST_STATUS == "finalized"


def test_lifecycle_normalizer_preserves_raw_and_aliases_ticker():
    from services.kalshi.contracts import lifecycle as lifecycle_contract
    for fixture_name in ("market_lifecycle_determined.json", "market_lifecycle_settled.json"):
        msg = _payload(fixture_name)
        normalized = lifecycle_contract.normalize_lifecycle(msg)
        assert normalized["ticker"] == msg["market_ticker"]
        for key, value in msg.items():
            assert normalized[key] == value


# --- A12: canonical Kalshi contracts (likely-final interfaces for Phase C) ---
# Lightweight slots dataclasses + per-module factory functions (names are
# globally unique because provenance.validate_operations flags the same
# operation name declared in two boundary modules). Constructed on demand
# by consumers (A13/A14) - the hot dispatch path keeps emitting cheap
# dicts, so these carry no blanket runtime validation.


def test_public_trade_resolves_outcome_side_from_canonical_field():
    from services.kalshi.contracts import trade as trade_contract
    t = trade_contract.public_trade_from_ws(_payload("public_trade.json"))
    assert t.trade_id == "d91bc706-ee49-470d-82d8-11418bda6fed"
    assert t.ticker == "HIGHNY-22DEC23-B53.5"
    assert t.outcome_side == "no"
    assert t.count == 136.0
    assert t.yes_price == 0.360
    assert t.no_price == 0.640
    assert t.raw_payload == _payload("public_trade.json")


def test_public_trade_resolves_direction_from_book_side_when_outcome_absent():
    from services.kalshi.contracts import trade as trade_contract
    msg = dict(_payload("public_trade.json"))
    del msg["taker_outcome_side"], msg["taker_side"]
    assert msg["taker_book_side"] == "ask"  # bid == yes, ask == no (order_direction.md)
    t = trade_contract.public_trade_from_ws(msg)
    assert t.outcome_side == "no"


def test_public_trade_never_guesses_an_unknown_direction_value():
    from services.kalshi.contracts import trade as trade_contract
    msg = dict(_payload("public_trade.json"))
    msg["taker_outcome_side"] = "maybe"   # unknown closed-enum value
    msg["taker_book_side"] = "sideways"
    msg["taker_side"] = "perhaps"
    t = trade_contract.public_trade_from_ws(msg)
    assert t.outcome_side is None  # unknown stays unknown - never a guessed yes/no


def test_public_trade_keeps_unknown_extension_fields_in_raw_payload():
    from services.kalshi.contracts import trade as trade_contract
    msg = dict(_payload("public_trade.json"))
    msg["field_kalshi_adds_tomorrow"] = {"nested": True}
    t = trade_contract.public_trade_from_ws(msg)
    assert t.raw_payload["field_kalshi_adds_tomorrow"] == {"nested": True}


def test_ticker_update_carries_app_price_fields_and_raw():
    from services.kalshi.contracts import ticker as ticker_contract
    msg = _payload("market_ticker.json")
    u = ticker_contract.ticker_update_from_ws(msg)
    assert u.ticker == "FED-23DEC-T3.00"
    assert u.yes_bid == 0.450
    assert u.yes_ask == 0.530
    assert u.price == 0.480
    assert u.ts_ms == msg["ts_ms"]
    assert u.raw_payload is msg


def test_user_fill_keys_on_trade_id_and_resolves_side_canonically():
    from services.kalshi.contracts import fill as fill_contract
    msg = _payload("fill.json")
    f = fill_contract.user_fill_from_ws(msg)
    assert f.trade_id == "d91bc706-ee49-470d-82d8-11418bda6fed"
    assert f.ticker == "HIGHNY-22DEC23-B53.5"
    assert f.outcome_side == msg["outcome_side"]
    assert f.action == msg["action"]
    assert f.count == float(msg["count_fp"])
    assert f.raw_payload is msg


def test_user_fill_never_guesses_an_unknown_side():
    from services.kalshi.contracts import fill as fill_contract
    msg = dict(_payload("fill.json"))
    msg["outcome_side"] = "maybe"
    msg["book_side"] = "sideways"
    msg["side"] = "perhaps"
    f = fill_contract.user_fill_from_ws(msg)
    assert f.outcome_side is None


def test_market_position_from_ws_carries_documented_fields_and_raw():
    from services.kalshi.contracts import position as position_contract
    msg = _payload("market_position.json")
    p = position_contract.market_position_from_ws(msg)
    assert p.ticker == "FED-23DEC-T3.00"
    assert p.position == 100.0  # positive means YES contracts (get-positions.md)
    assert p.realized_pnl == float(msg["realized_pnl_dollars"])
    assert p.fees_paid == float(msg["fees_paid_dollars"])
    assert p.raw_payload is msg


def test_lifecycle_event_from_ws_classifies_without_resolving_determined():
    from services.kalshi.contracts import lifecycle as lifecycle_contract
    determined = lifecycle_contract.lifecycle_event_from_ws(_payload("market_lifecycle_determined.json"))
    settled = lifecycle_contract.lifecycle_event_from_ws(_payload("market_lifecycle_settled.json"))
    assert determined.event_type == "determined"
    assert determined.may_resolve_outcome is False
    assert settled.event_type == "settled"
    assert settled.may_resolve_outcome is True
    assert settled.ticker == settled.raw_payload["market_ticker"]


def test_create_order_request_produces_the_documented_v2_kwargs():
    from services.kalshi.contracts import order as order_contract
    req = order_contract.CreateOrderRequest(
        ticker="TICK-A", side="bid", count="10.00", price="0.5600",
    )
    kwargs = order_contract.create_order_kwargs(req)
    assert kwargs["ticker"] == "TICK-A"
    assert kwargs["side"] == "bid"
    assert kwargs["count"] == "10.00"
    assert kwargs["price"] == "0.5600"
    assert kwargs["time_in_force"] == "immediate_or_cancel"
    assert kwargs["self_trade_prevention_type"] == "taker_at_cross"
    # legacy (non-v2) shape must not leak in
    for legacy in ("action", "yes_price", "no_price", "type"):
        assert legacy not in kwargs


def test_create_order_request_rejects_a_non_v2_side_vocabulary():
    from services.kalshi.contracts import order as order_contract
    with pytest.raises(ValueError):
        order_contract.CreateOrderRequest(ticker="TICK-A", side="yes", count="1.00", price="0.5000")


def test_cancel_order_result_from_documented_v2_response():
    from services.kalshi.contracts import order as order_contract
    resp = _payload("cancel_order_response.json")
    result = order_contract.cancel_order_result_from_response(resp)
    assert result.order_id == resp["order_id"]
    assert result.client_order_id == resp["client_order_id"]
    assert result.reduced_by == resp["reduced_by"]
    assert result.raw_payload is resp
