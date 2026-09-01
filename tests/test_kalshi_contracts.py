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
from services.kalshi.account import (  # noqa: E402
    KalshiAccountGateway,
    classify_api_key_attestation,
    user_data_age_sec,
)
from services.kalshi.account_client import KalshiAccountClient  # noqa: E402
from services.kalshi.websocket import KalshiStreamGateway  # noqa: E402
from services.market_watch import _MARKET_FIELDS  # noqa: E402
from services.market_watch import mve_scan  # noqa: E402
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

    normalized = KalshiStreamGateway.normalize_trade(trade)

    assert normalized["ticker"] == "HIGHNY-22DEC23-B53.5"
    assert normalized["taker_outcome_side"] == "no"
    assert normalized["taker_book_side"] == "ask"
    assert normalized["taker_side"] == "no"  # canonical and legacy agree here


def test_normalize_trade_prefers_canonical_field_when_legacy_taker_side_is_absent():
    # The exact historical bug class this fixture set exists to catch: the
    # old code read only the deprecated taker_side and defaulted anything
    # unreadable to "no" - see docs/kalshi/CHEATSHEET.md's own entry.
    trade = _payload("public_trade_no_deprecated_side.json")

    normalized = KalshiStreamGateway.normalize_trade(trade)

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
    from services.kalshi.contracts import ticker as ticker_contract
    ticker_msg = ticker_contract.normalize_ticker(_payload("market_ticker.json"))

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
    # exchange_index (2026-08-30, issue #251, Trade API 3.29.0): required on
    # the WS fill message (user-fills.md:207) - identifies which exchange
    # shard the fill occurred on. _FILL_FIELDS used to drop it silently.
    assert fills[0]["exchange_index"] == 2


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
    client = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2")
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
    from services.kalshi.contracts import position as position_contract
    raw = _payload("market_position.json")
    assert "ticker" not in raw  # the real wire shape carries market_ticker only
    position_msg = position_contract.normalize_position(raw)  # gateway does this before the callback

    asyncio.run(main._process_stream_position(position_msg))

    market_positions = main.state["account"]["positions"]["market_positions"]
    assert len(market_positions) == 1
    assert market_positions[0]["ticker"] == "FED-23DEC-T3.00"


def test_process_stream_position_updates_an_existing_entry_in_place():
    _reset_account_state(
        positions={"market_positions": [{"ticker": "FED-23DEC-T3.00", "position_fp": "1.00"}], "event_positions": []},
    )
    from services.kalshi.contracts import position as position_contract
    position_msg = position_contract.normalize_position(_payload("market_position.json"))

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

    from services.kalshi.contracts import lifecycle as lifecycle_contract
    asyncio.run(main._process_stream_lifecycle(
        lifecycle_contract.normalize_lifecycle(_payload("market_lifecycle_determined.json"))))

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


# --- multivariate (combo) event REST object: services/market_watch/
# mve_scan.py (issue #268) - the ONLY place a combo's own title/sub_title/
# mutually_exclusive comes from, since plain get_events "excludes
# multivariate events" per its own doc (docs/kalshi/get-events.md).

def test_event_title_fields_reads_the_documented_multivariate_event_fields():
    event = _payload("multivariate_event.json")
    result = mve_scan._event_title_fields(event)
    assert result["sub_title"] == "MVE"
    assert result["mutually_exclusive"] is False
    assert result["series_ticker"] == "KXMVECROSSCATEGORY-SHARD1"
    assert result["category"] == "Exotics"


def test_market_rows_from_event_tags_each_market_with_the_parent_events_series_and_category():
    # The Market schema itself carries neither series_ticker nor category
    # (confirmed against docs/kalshi/get-multivariate-events.md's own
    # Market schema and a live response) - only the parent EventData does.
    event = _payload("multivariate_event.json")
    rows = mve_scan._market_rows_from_event(event)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "KXMVECROSSCATEGORY-SHARD1-S2026FF7C4259D33-71884BBDB4F"
    assert rows[0]["series_ticker"] == "KXMVECROSSCATEGORY-SHARD1"
    assert rows[0]["category"] == "Exotics"
    assert rows[0]["occurrence_datetime"] is None  # never populated for a combo market


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


# --- I8/issue #266+#261: exchange-side staleness + API-key attestation -----


class _FakeUserDataTimestampSDKClient:
    def __init__(self, response: dict):
        self.calls = []
        self._response = response

    async def get_user_data_timestamp(self, **kwargs):
        self.calls.append(("get_user_data_timestamp", kwargs))
        return _FakeResp(self._response)


class _FakeApiKeysHttpInfo:
    """Mirrors kalshi_python_async's real ApiResponse shape for
    get_api_keys_with_http_info: .data is the (lossy) parsed model, .raw_data
    is the real response bytes. Built from two separately-controllable
    dicts so tests can prove the recovery path actually reads raw_data
    rather than trivially already having the field on .data."""

    def __init__(self, model_payload: dict, raw_payload: dict):
        self.data = _FakeResp(model_payload)
        self.raw_data = json.dumps(raw_payload).encode("utf-8")


class _FakeApiKeysSDKClient:
    def __init__(self, model_payload: dict, raw_payload: dict):
        self.calls = []
        self._http_info = _FakeApiKeysHttpInfo(model_payload, raw_payload)

    async def get_api_keys_with_http_info(self, **kwargs):
        self.calls.append(("get_api_keys_with_http_info", kwargs))
        return self._http_info


def test_get_user_data_timestamp_returns_the_documented_shape():
    fixture = _payload("user_data_timestamp.json")
    fake = _FakeUserDataTimestampSDKClient(fixture)
    gateway = KalshiAccountGateway(fake)

    result = asyncio.run(gateway.get_user_data_timestamp())

    assert result == fixture
    assert set(result.keys()) == {"as_of_time"}


def test_get_api_keys_recovers_the_field_the_vendored_sdk_model_drops():
    """The regression this whole feature is about: the installed
    kalshi_python_async 3.27.0 GetApiKeysResponse model has no
    api_key_region_expiration_ts field and silently drops it on parse
    (confirmed directly against the real installed SDK, 2026-08-30) - so
    .data (the parsed model) here deliberately does NOT carry the field,
    only .raw_data does, proving get_api_keys() reads the raw body rather
    than trusting the model."""
    fixture = _payload("api_keys_attested.json")
    model_only = {"api_keys": fixture["api_keys"]}  # what the real SDK model actually keeps
    fake = _FakeApiKeysSDKClient(model_payload=model_only, raw_payload=fixture)
    gateway = KalshiAccountGateway(fake)

    result = asyncio.run(gateway.get_api_keys())

    assert result["api_key_region_expiration_ts"] == fixture["api_key_region_expiration_ts"]
    assert result["api_keys"] == fixture["api_keys"]
    assert fake.calls == [("get_api_keys_with_http_info", {})]


def test_get_api_keys_never_attested_has_no_expiration_field():
    """Absent is a documented, valid state (\"Absent when the account has
    never attested\") - the response must not fabricate a null/zero in its
    place."""
    fixture = _payload("api_keys_never_attested.json")
    fake = _FakeApiKeysSDKClient(model_payload=fixture, raw_payload=fixture)
    gateway = KalshiAccountGateway(fake)

    result = asyncio.run(gateway.get_api_keys())

    assert "api_key_region_expiration_ts" not in result
    assert result["api_keys"] == fixture["api_keys"]


def test_classify_api_key_attestation_never_attested_when_field_is_absent():
    payload = _payload("api_keys_never_attested.json")

    status = classify_api_key_attestation(payload, now=1_000_000_000.0)

    assert status == {"status": "never_attested", "region_expiration_ts": None, "seconds_until_expiration": None}


def test_classify_api_key_attestation_active_when_expiration_is_in_the_future():
    payload = _payload("api_keys_attested.json")
    ts = payload["api_key_region_expiration_ts"]

    status = classify_api_key_attestation(payload, now=ts - 3600.0)

    assert status["status"] == "active"
    assert status["region_expiration_ts"] == ts
    assert status["seconds_until_expiration"] == pytest.approx(3600.0)


def test_classify_api_key_attestation_lapsed_when_expiration_has_passed():
    payload = _payload("api_keys_lapsed.json")
    ts = payload["api_key_region_expiration_ts"]

    status = classify_api_key_attestation(payload, now=ts + 3600.0)

    assert status["status"] == "lapsed"
    assert status["seconds_until_expiration"] == pytest.approx(-3600.0)


def test_user_data_age_sec_computes_age_from_as_of_time():
    fixture = _payload("user_data_timestamp.json")
    from datetime import datetime

    as_of_epoch = datetime.fromisoformat(fixture["as_of_time"]).timestamp()

    age = user_data_age_sec(fixture, now=as_of_epoch + 5.0)

    assert age == pytest.approx(5.0)


def test_user_data_age_sec_returns_none_rather_than_guessing_when_as_of_time_is_missing():
    assert user_data_age_sec({}, now=1_000_000_000.0) is None


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
    assert KalshiStreamGateway.normalize_trade is trade_contract.normalize_trade


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


# --- A14: stream handlers consume canonical fields, not vendor aliases ------
# Production dispatch (services/kalshi/websocket.py) normalizes every
# message before the callback, so a handler only ever sees the canonical
# `ticker` key present. These prove the handlers actually READ the
# canonical key - a message carrying only `ticker` (no market_ticker
# alias) must process fully, which fails while any handler still reads
# the vendor alias directly.


def _canonical_only(normalized: dict) -> dict:
    stripped = dict(normalized)
    stripped.pop("market_ticker", None)
    return stripped


def test_process_stream_ticker_consumes_the_canonical_ticker_key():
    from services.kalshi.contracts import ticker as ticker_contract
    main.state["latest_prices"] = {}
    main.state["markets"] = [{"ticker": "FED-23DEC-T3.00", "yes_ask_dollars": None}]
    msg = _canonical_only(ticker_contract.normalize_ticker(_payload("market_ticker.json")))

    asyncio.run(main._process_stream_ticker(msg))

    assert main.state["latest_prices"]["FED-23DEC-T3.00"] == 0.450


def test_process_stream_position_consumes_the_canonical_ticker_key():
    from services.kalshi.contracts import position as position_contract
    _reset_account_state()
    msg = _canonical_only(position_contract.normalize_position(_payload("market_position.json")))

    asyncio.run(main._process_stream_position(msg))

    market_positions = main.state["account"]["positions"]["market_positions"]
    assert len(market_positions) == 1
    assert market_positions[0]["ticker"] == "FED-23DEC-T3.00"


def test_process_stream_lifecycle_consumes_the_canonical_ticker_key():
    from services.kalshi.contracts import lifecycle as lifecycle_contract
    main.state["lifecycle_stream_stats"] = {
        "events_by_type": {}, "close_time_updates_applied": 0, "last_event_at": None,
        "catalog_updates_applied": 0, "outcomes_resolved_via_lifecycle": 0,
    }
    msg = _canonical_only(lifecycle_contract.normalize_lifecycle(_payload("market_lifecycle_determined.json")))

    asyncio.run(main._process_stream_lifecycle(msg))

    assert main.state["lifecycle_stream_stats"]["events_by_type"]["determined"] == 1


# --- A16: production-used surface without fixture coverage until now --------


def test_rest_position_slims_to_the_documented_presentation_fields():
    from services.position.account_positions import _slim_position
    position = _payload("rest_market_position.json")
    slimmed = _slim_position(position)
    assert slimmed["ticker"] == "FED-23DEC-T3.00"
    assert slimmed["position_fp"] == "-40.00"
    assert slimmed["realized_pnl_dollars"] == "1.2500"
    assert slimmed["fees_paid_dollars"] == "0.3400"
    # exchange_index (2026-08-30, issue #251, Trade API 3.29.0): required on
    # the REST MarketPosition schema (get-positions.md:189) - identifies
    # which exchange shard the position lives on. _POSITION_FIELDS used to
    # drop it silently.
    assert slimmed["exchange_index"] == 0


def test_flatten_closes_a_doc_sourced_rest_no_position_by_buying_yes():
    """The real-money flatten side mapping, grounded in the documented
    REST shape rather than a synthetic dict: position_fp is negative
    ('Negative means NO contracts'), so the close order must BUY yes
    (side='bid') at the pinned 0.9900 - the exact semantics
    get-positions.md + create-order-v2.md document."""
    from services import execution

    class _Account:
        def __init__(self, position):
            self._position = position
            self.orders = []

        async def get_positions(self):
            return {"market_positions": [self._position]}

        async def create_order(self, **kwargs):
            self.orders.append(kwargs)
            return {"ok": True}

    account = _Account(_payload("rest_market_position.json"))
    results = asyncio.run(execution.flatten_all_real_positions(account))
    assert len(results) == 1
    assert results[0]["error"] is None
    order = account.orders[0]
    assert order["side"] == "bid"
    assert order["price"] == "0.9900"
    assert order["count"] == "40.00"
    assert order["is_closing_order"] is True


def test_rest_fill_slims_with_both_documented_identity_spellings():
    from services.position.account_positions import _slim_fill
    fill = _payload("rest_fill.json")
    slimmed = _slim_fill(fill)
    # get-fills.md: trade_id is 'legacy field name, same as fill_id';
    # market_ticker is 'legacy field name, same as ticker'.
    assert slimmed["fill_id"] == slimmed["trade_id"] == "aa1e3f60-8ec6-4441-9d67-c2cf6a2c9d1e"
    assert slimmed["ticker"] == slimmed["market_ticker"] == "HIGHNY-22DEC23-B53.5"
    assert slimmed["count_fp"] == "10.00"
    assert slimmed["created_time"] == "2022-12-23T18:30:00Z"
    # exchange_index (2026-08-30, issue #251, Trade API 3.29.0): required on
    # the REST Fill schema (get-fills.md:194) - identifies which exchange
    # shard the fill occurred on. _FILL_FIELDS used to drop it silently.
    assert slimmed["exchange_index"] == 0


def test_cfbenchmarks_value_records_settlement_average_and_spot():
    from services import index_feed
    msg = _payload("cfbenchmarks_value.json")
    accepted, _ = index_feed.record_cfbenchmarks(msg, now=1755990000.2)
    assert accepted is True
    latest = index_feed.latest("BRTI")
    assert latest is not None
    assert latest["value"] == 65001.23  # upstream index level from the nested data frame
    assert latest["q15_value"] == 64999.87  # the KXBTC15M settlement quantity itself
    assert latest["avg_60s_value"] == 64998.51


def test_pyth_value_records_a_straight_underlying_price():
    from services import index_feed
    msg = _payload("pyth_value.json")
    accepted, _ = index_feed.record_pyth(msg, now=1755990000.2)
    assert accepted is True
    latest = index_feed.latest("Crypto.BTC/USD")
    assert latest is not None
    assert latest["value"] == 65002.41
    assert latest["q15_value"] is None  # pyth carries no windowed averages


# --- C2: strict closed semantics, tolerant open vendor values ---------------


def test_direction_vocabulary_has_exactly_one_shared_copy():
    """trade.py and fill.py narrow through the SAME mapping objects in
    contracts/types.py - the two channels structurally cannot disagree
    about yes/no ⇄ bid/ask equivalence."""
    from services.kalshi.contracts import fill as fill_contract
    from services.kalshi.contracts import trade as trade_contract
    from services.kalshi.contracts import types as boundary_types
    assert trade_contract.AS_OUTCOME_SIDE is boundary_types.AS_OUTCOME_SIDE
    assert fill_contract.AS_OUTCOME_SIDE is boundary_types.AS_OUTCOME_SIDE
    assert trade_contract.BOOK_SIDE_TO_OUTCOME is fill_contract.BOOK_SIDE_TO_OUTCOME


def test_unknown_open_enum_value_survives_normalization_untouched():
    """Open vendor values (fee_type is the proven live case - Kalshi grew
    quadratic_with_combo_maker_fees beyond its documented enum in 2026-08)
    must pass through normalization and canonical construction untouched:
    tolerated, preserved, never validated into a crash."""
    from services.kalshi.contracts import trade as trade_contract
    msg = dict(_payload("public_trade.json"))
    msg["fee_type"] = "a_fee_type_kalshi_invents_tomorrow"
    normalized = trade_contract.normalize_trade(msg)
    assert normalized["fee_type"] == "a_fee_type_kalshi_invents_tomorrow"
    canonical = trade_contract.public_trade_from_ws(msg)
    assert canonical.raw_payload["fee_type"] == "a_fee_type_kalshi_invents_tomorrow"
    assert canonical.outcome_side == "no"  # closed semantics still resolve strictly beside it


# --- C4: canonical identity is structurally closed --------------------------


def test_canonical_objects_expose_no_alias_spellings():
    """C4: a consumer holding the canonical object CANNOT choose REST-vs-WS
    alias spellings itself - slots-frozen dataclasses expose exactly the
    canonical names (trade_id, ticker); fill_id / market_ticker exist only
    inside raw_payload, where archival/diagnostics can still reach them."""
    from services.kalshi.contracts import fill as fill_contract
    from services.kalshi.contracts import position as position_contract
    from services.kalshi.contracts import trade as trade_contract

    f = fill_contract.user_fill_from_ws(_payload("fill.json"))
    p = position_contract.market_position_from_ws(_payload("market_position.json"))
    t = trade_contract.public_trade_from_ws(_payload("public_trade.json"))

    for obj, alias in ((f, "fill_id"), (f, "market_ticker"),
                       (p, "market_ticker"), (t, "market_ticker"), (t, "taker_side")):
        with pytest.raises(AttributeError):
            getattr(obj, alias)
    # ...and the aliases are still preserved for archival, in raw_payload
    assert f.raw_payload["market_ticker"] == f.ticker
    assert p.raw_payload["market_ticker"] == p.ticker


def test_lifecycle_event_never_implies_determined_is_final_even_with_a_result():
    """The determined fixture CARRIES result + settlement_value - exactly
    the bait that once caused premature outcome resolution. The canonical
    event still says may_resolve_outcome=False; the result stays in
    raw_payload for diagnostics only."""
    from services.kalshi.contracts import lifecycle as lifecycle_contract
    msg = _payload("market_lifecycle_determined.json")
    assert "result" in msg  # the bait is real
    event = lifecycle_contract.lifecycle_event_from_ws(msg)
    assert event.may_resolve_outcome is False
    with pytest.raises(AttributeError):
        event.result  # never a first-class attribute - raw_payload only
