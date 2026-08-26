"""services/whalewatchers/generic_rest.py - GenericRestProvider.

Code-review fix (finding #5, /code-review high pass against PR #23):
WhaleSignal.id used to be a fresh uuid.uuid4() per fetch, so the exact
same underlying print - fetched again on a later poll before this API's
own window rolled it off - was treated as a brand-new trade every single
time, defeating candidate_ledger's trade_id dedup entirely for this
registered, selectable provider (WHALE_WATCHER_PROVIDER=generic_rest).
Fixed by preferring a configured id field (WHALE_WATCHER_ID_FIELD,
default "id") when the payload has one, and falling back to a stable
SHA-256 hash of (ticker, side, size, price) when it doesn't - the module's
own docstring documents the accepted limitation this fallback carries
(two genuinely different trades sharing all four fields within one poll
collide onto the same id).
"""
import pytest

from services.whalewatchers.generic_rest import GenericRestProvider


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    from services import accounts_store
    # Never let a real .env WHALE_WATCHER_* value or a stored account leak
    # into this file's tests - every test builds its own provider config
    # explicitly instead.
    for var in (
        "WHALE_WATCHER_API_URL", "WHALE_WATCHER_API_KEY", "WHALE_WATCHER_ID_FIELD",
        "WHALE_WATCHER_TICKER_FIELD", "WHALE_WATCHER_SIDE_FIELD",
        "WHALE_WATCHER_SIZE_FIELD", "WHALE_WATCHER_PRICE_FIELD",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(accounts_store, "load", lambda provider: None)
    yield


def _provider() -> GenericRestProvider:
    return GenericRestProvider()


# ------------------------------------------------------------- _signal_id

def test_signal_id_uses_the_configured_id_field_when_present():
    p = _provider()
    item = {"id": "abc-123", "ticker": "KXHIGHNY-1", "side": "yes", "size": 12000, "price": 0.63}
    assert p._signal_id(item, "KXHIGHNY-1", "yes", 12000, 0.63) == "abc-123"


def test_signal_id_stringifies_a_non_string_id_field():
    p = _provider()
    item = {"id": 987654321}
    assert p._signal_id(item, "T", "yes", 100, 0.5) == "987654321"


def test_signal_id_falls_back_to_a_stable_content_hash_when_no_id_field():
    p = _provider()
    item = {"ticker": "KXHIGHNY-1", "side": "yes", "size": 12000, "price": 0.63}  # no "id" key at all
    signal_id = p._signal_id(item, "KXHIGHNY-1", "yes", 12000, 0.63)
    assert signal_id  # non-empty
    assert signal_id != "id"  # not accidentally the literal field name


def test_signal_id_fallback_is_stable_across_repeated_calls_for_the_same_trade():
    """The core regression this fix closes: the SAME print must produce
    the SAME id every time, not a fresh random one per call."""
    p = _provider()
    item = {"ticker": "KXHIGHNY-1", "side": "yes", "size": 12000, "price": 0.63}
    first = p._signal_id(item, "KXHIGHNY-1", "yes", 12000, 0.63)
    second = p._signal_id(item, "KXHIGHNY-1", "yes", 12000, 0.63)
    assert first == second


def test_signal_id_fallback_differs_for_a_genuinely_different_trade():
    p = _provider()
    item = {}
    id_a = p._signal_id(item, "KXHIGHNY-1", "yes", 12000, 0.63)
    id_b = p._signal_id(item, "KXHIGHNY-1", "yes", 12000, 0.64)  # different price
    id_c = p._signal_id(item, "KXHIGHNY-2", "yes", 12000, 0.63)  # different ticker
    id_d = p._signal_id(item, "KXHIGHNY-1", "no", 12000, 0.63)  # different side
    id_e = p._signal_id(item, "KXHIGHNY-1", "yes", 5000, 0.63)  # different size
    assert len({id_a, id_b, id_c, id_d, id_e}) == 5


def test_signal_id_ignores_a_blank_configured_id_field_value():
    """An empty string in the mapped id field (e.g. a provider that always
    includes the key but sometimes leaves it blank) must fall back to the
    content hash, not produce an empty/meaningless id."""
    p = _provider()
    item = {"id": "", "ticker": "KXHIGHNY-1", "side": "yes", "size": 12000, "price": 0.63}
    signal_id = p._signal_id(item, "KXHIGHNY-1", "yes", 12000, 0.63)
    assert signal_id != ""


def test_signal_id_respects_a_custom_configured_id_field_name(monkeypatch):
    monkeypatch.setenv("WHALE_WATCHER_ID_FIELD", "trade_uuid")
    p = _provider()
    item = {"trade_uuid": "custom-id-1", "ticker": "T"}
    assert p._signal_id(item, "T", "yes", 100, 0.5) == "custom-id-1"


# ----------------------------------------------------------- fetch_signals

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    async def get(self, url, headers=None, timeout=None):
        self.calls += 1
        return _FakeResponse(self._payload)


def test_fetch_signals_produces_a_stable_id_across_repeated_polls_of_the_same_print(monkeypatch):
    """End-to-end proof of the fix: fetch_signals(), called twice with the
    SAME underlying payload (simulating two poll cycles before the
    upstream API's own window rolls the print off), must yield the SAME
    WhaleSignal.id both times - not a fresh random one per call, which is
    the exact defect this finding reported."""
    import asyncio

    payload = [{"ticker": "KXHIGHNY-1", "side": "yes", "size": 12000, "price": 0.63}]
    fake_client = _FakeAsyncClient(payload)
    monkeypatch.setattr(
        "services.whalewatchers.generic_rest.get_client", lambda: fake_client,
    )

    provider = _provider()
    provider.api_url = "https://example.test/whales"

    first_signals = asyncio.run(provider.fetch_signals())
    second_signals = asyncio.run(provider.fetch_signals())

    assert len(first_signals) == 1
    assert len(second_signals) == 1
    assert first_signals[0].id == second_signals[0].id
    assert fake_client.calls == 2  # confirms this really was two separate fetches, not one cached result


def test_fetch_signals_uses_a_real_id_field_when_the_payload_has_one(monkeypatch):
    import asyncio

    payload = [{"id": "provider-trade-42", "ticker": "KXHIGHNY-1", "side": "yes", "size": 12000, "price": 0.63}]
    fake_client = _FakeAsyncClient(payload)
    monkeypatch.setattr(
        "services.whalewatchers.generic_rest.get_client", lambda: fake_client,
    )

    provider = _provider()
    provider.api_url = "https://example.test/whales"

    signals = asyncio.run(provider.fetch_signals())
    assert signals[0].id == "provider-trade-42"


def test_fetch_signals_gives_two_genuinely_different_prints_two_different_ids(monkeypatch):
    import asyncio

    payload = [
        {"ticker": "KXHIGHNY-1", "side": "yes", "size": 12000, "price": 0.63},
        {"ticker": "KXHIGHNY-2", "side": "no", "size": 8000, "price": 0.41},
    ]
    fake_client = _FakeAsyncClient(payload)
    monkeypatch.setattr(
        "services.whalewatchers.generic_rest.get_client", lambda: fake_client,
    )

    provider = _provider()
    provider.api_url = "https://example.test/whales"

    signals = asyncio.run(provider.fetch_signals())
    assert len(signals) == 2
    assert signals[0].id != signals[1].id
