"""Kalshi WebSocket stream gateway — Phase A Task A11.

The one implementation of the vendor WebSocket transport behind the
integration boundary: connection/auth (RSA-PSS signed headers), channel
subscription/update commands, the bounded reader->worker ingest queue with
its backpressure/drop metrics, reconnect/backoff, and per-message dispatch
into channel-specific normalizers (services/kalshi/contracts/, Task A10).
Business callbacks (on_trade/on_ticker/on_fill/...) remain application
code — this gateway never interprets vendor fields itself and never
decides what the app should do with a message.

services/kalshi_trade_ws.py was the compatibility facade production
wiring imports (services/app_state.py constructs one instance for the
trade/ticker/lifecycle connection and a second, physically isolated one
for index feeds — that independence is constructor policy, preserved
unchanged). Moved here verbatim from that module: no transport rewrite,
same sockets, same queue bounds, same metrics (design-spec rule: "without
an unnecessary transport rewrite").
"""
import asyncio
import base64
import contextvars
import json
import logging
import os
import time

import websockets

logger = logging.getLogger(__name__)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # typing-only - runtime imports unchanged
    from websockets.asyncio.client import ClientConnection

# Channel-specific semantic normalization lives behind the integration
# boundary (Phase A Task A10) - this transport delegates and never
# interprets vendor fields itself.
from services.kalshi.contracts import fill as fill_contract
from services.kalshi.contracts import lifecycle as lifecycle_contract
from services.kalshi.contracts import position as position_contract
from services.kalshi.contracts import ticker as ticker_contract
from services.kalshi.contracts import trade as trade_contract
from services.kalshi.provenance import ContractDocs
from services import candidate_log
from services import fault_log
from services import series_watcher
from services import whale_gate
from services.config.config_store import config_store
from services.latency_agg import LatencyAgg, bucket_for, empty_buckets, p95_upper_bound

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "run": (
        "docs/kalshi/websocket-connection.md",
        "docs/kalshi/websockets.md",
    ),
    "set_market_tickers": ("docs/kalshi/websocket-connection.md",),
    "request_index_list": (
        "docs/kalshi/cfbenchmarks-value.md",
        "docs/kalshi/pyth-value.md",
    ),
    "enabled": ("docs/kalshi/websocket-connection.md",),
    "status": ("docs/kalshi/websocket-connection.md",),
    # I1 queue-health surface: classifies Kalshi's own server-side error
    # codes (error 25 = subscription buffer overflow, the documented
    # "reduce scope or improve read throughput" signal) separately from
    # local QueueFull drops, and exists to verify the quick-start's
    # buffering/throughput guidance against measured behavior.
    "ingest_metrics": ("docs/kalshi/websocket-connection.md", "docs/kalshi/quick_start_websockets.md"),
    "reset_ingest_window": ("docs/kalshi/websocket-connection.md",),
    # Consumer-stall backstop (issue #145): quick_start_websockets.md's own
    # reconnection guidance ("implement reconnection logic with exponential
    # backoff") is the documented recovery path these two reuse - there is
    # no per-message gap-detection/resume capability on this API tier (P7's
    # own research, websocket-connection.md's AsyncAPI schema), so a forced
    # reconnect through the existing backoff path is the correct mechanism,
    # not a Kalshi-specific operation of its own.
    "force_reconnect": ("docs/kalshi/websocket-connection.md", "docs/kalshi/quick_start_websockets.md"),
    "ensure_consumer_progressing": ("docs/kalshi/websocket-connection.md", "docs/kalshi/quick_start_websockets.md"),
}

_PROD_WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
_DEMO_WS_URL = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"

# Bound on the reader->worker handoff queue (see run()). Sized to absorb a
# real burst without becoming an unbounded memory sink: at exchange-wide
# trade volume this is a few seconds of backlog. Overflow increments
# dropped_messages rather than blocking the reader, because blocking the
# reader is exactly the failure this split exists to prevent.
_INGEST_QUEUE_MAX = 20000

# Bounded message-class labels for ingest metrics (realtime data-plane
# investigation task I1). Keyed by the per-message `type` field each channel
# doc declares; anything unrecognised lands in one "other" bucket so the
# label set can never grow with vendor changes. "control" is the command/
# acknowledgement traffic (subscribed/ok/error/...), kept as its own class
# because server-side `error` messages - error 25, subscription buffer
# overflow, in particular - are a distinct failure point from a local
# QueueFull and must never be conflated with it.
_CLASS_BY_MESSAGE_TYPE: dict[str, str] = {
    "trade": "trade",
    "ticker": "ticker",
    "fill": "fill",
    position_contract.WS_MESSAGE_TYPE: "position",
    "market_lifecycle_v2": "lifecycle",
    "cfbenchmarks_value": "index",
    "pyth_value": "index",
    "subscribed": "control",
    "unsubscribed": "control",
    "ok": "control",
    "error": "control",
    "cfbenchmarks_value_indexlist": "control",
    "pyth_value_underlying_list": "control",
}
_OTHER_CLASS = "other"

# P4 Task 18: classes routed to the critical queue when
# realtime_data_plane.two_consumer_mode is on. Decision-critical, low-volume
# flow (account fills/positions, lifecycle transitions, protocol control
# frames) is isolated from bulk market flow (trade/ticker/index/other) so a
# slow handler class can only ever stall its own queue - the 2026-08-29
# settlement-cascade finding (serial lifecycle handling starved trades) and
# the cfbenchmarks starvation incident in run()'s comments are both this
# failure with different slow classes.
_CRITICAL_CLASSES = frozenset({"fill", "position", "lifecycle", "control"})

# P4 Task 19a: pseudo-class for the market consumer's wake-up sentinel. Pushed
# into the market queue only on the coalescing map's empty -> non-empty
# transition (the queue cannot be full then only when it matters: a parked
# consumer implies an empty queue, so the sentinel always lands when it is
# needed; when the queue is busy the drain loop reaches the map on its own).
# Never dispatched to _process_item and never counted as a message.
_TICKER_WAKE = "_ticker_wake"


def _update_ts_ms(msg: dict) -> float | None:
    """A ticker update's exchange timestamp, normalized to MILLISECONDS.
    docs/kalshi/market-ticker.md marks `ts` (seconds) and `time` (RFC3339)
    deprecated in favor of `ts_ms`; prefer ts_ms, fall back to ts * 1000
    (seconds -> ms), else None (arrival order decides). Never compare a raw
    ts to a raw ts_ms - they differ by 1000x."""
    ts_ms = msg.get("ts_ms")
    if isinstance(ts_ms, (int, float)):
        return float(ts_ms)
    ts = msg.get("ts")
    if isinstance(ts, (int, float)):
        return float(ts) * 1000.0
    return None

# The monotonic enqueue timestamp of the message currently being handled,
# visible to application callbacks for the duration of their call (I2:
# services/whale_stream/whale_stream_handlers.py reads it to measure true
# receive->decision latency without the gateway stamping a private key
# into the vendor payload, which would leak into archival raw_json). None
# outside a _process_item call.
MESSAGE_ENQUEUED_AT: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "kalshi_ws_message_enqueued_at", default=None,
)
_KALSHI_SUBSCRIPTION_OVERFLOW_CODE = 25  # docs/kalshi/websocket-connection.md error table
_MAX_SERVER_ERROR_CODES_TRACKED = 64  # documented codes are a small fixed set; cap defensively
_MAX_DISCONNECT_REASON_CHARS = 200

# Bounds every message's handler dispatch (issue #145's consumer-stall
# incident, 2026-08-27: a hung await inside a handler - most likely
# services/whalewatchers/kalshi_trade_tape.py's unbounded
# asyncio.to_thread(self._process_trades_timed, ...) - stalled the consumer
# forever while the reader kept enqueuing, filling the queue and dropping
# messages with no automated recovery. Ten seconds against a real window
# avg_ms of ~7 and p-max under 1s (see /api/health/pipeline's
# receive_to_handler_end.window) leaves large headroom while still bounding
# the worst case. Cancelling a timed-out asyncio.to_thread call does NOT
# stop the underlying OS thread (confirmed against CPython's own
# Future.cancel(): "cannot be cancelled if it is running") - a known,
# accepted, not-yet-fixed tradeoff, tracked in issue #150, watched via
# handler_timeouts_total/handler_timeouts_by_class below rather than
# guessed at.
_HANDLER_TIMEOUT_SEC = 10.0

# ensure_consumer_progressing's backstop threshold: consecutive liveness
# checks with zero processed-count progress (while new messages keep
# arriving and the queue isn't empty) before forcing a reconnect. Catches
# whatever _HANDLER_TIMEOUT_SEC doesn't structurally cover - e.g. a hang
# inside _sync_subscriptions, which runs on the reader's own task, not the
# consumer's.
_LIVENESS_STUCK_SAMPLES_THRESHOLD = 3


class KalshiStreamGateway:
    def __init__(self, base_url: str, exchange_wide_trades: bool = False,
                 index_ids: list[str] | None = None,
                 underlying_tickers: list[str] | None = None,
                 subscribe_lifecycle: bool = False,
                 ingest_queue_max: int = _INGEST_QUEUE_MAX,
                 handler_timeout_sec: float = _HANDLER_TIMEOUT_SEC):
        # exchange_wide_trades (2026-08-17, direct goal: "realtime data
        # across everything" / "zero latency and maximum insight"):
        # subscribe the `trade` channel with NO market_tickers, which
        # docs/kalshi/public-trades.md explicitly permits ("market
        # specification optional"), so every trade on the exchange arrives
        # instead of only those on this app's own rotating watchlist.
        #
        # That watchlist scoping was the single largest measured gap in the
        # system: a print on an unwatched market was never received at all -
        # not filtered, not logged, not counted as rejected - and 425
        # markets were observed trading in a 30-second window against 15
        # watched, with 5 of 5 whale prints >=$2,500 invisible.
        #
        # The `ticker` channel deliberately stays scoped to the desired set
        # even in this mode. market-ticker.md permits going wide there too,
        # but a price update on every market on the exchange fires on every
        # field change and would be a genuine firehose, for data this app
        # only needs on markets it might actually trade.
        self.exchange_wide_trades = exchange_wide_trades
        # market_lifecycle_v2 (2026-08-17, REST-vs-websocket architecture
        # finding, docs/next-session-pickup-2026-08-17.md item #2): push-
        # based open/close/settlement notifications, replacing the 6-second
        # REST poll's own close_time/result checks for the specific case of
        # a close date getting revised ahead of schedule - see
        # main.py._process_stream_lifecycle. docs/kalshi/market-and-event-
        # lifecycle.md is explicit that "market_ticker filters are not
        # supported" - like exchange-wide trade, there is no way to scope
        # this to a watchlist, so it's a constructor-level opt-in (default
        # off) rather than always-on, kept OFF the dedicated index_stream
        # connection specifically to preserve that connection's physical
        # isolation from any other channel's volume (see app_state.py).
        self.subscribe_lifecycle = subscribe_lifecycle
        # CF Benchmarks index IDs to stream (docs/kalshi/cfbenchmarks-value.md).
        # This channel is what several crypto series literally settle
        # against - KXBTC15M's own rules_primary, read live 2026-08-17:
        # "the simple average of the sixty seconds of CF Benchmarks' BRTI
        # before <close>" - so its last_60s_windowed_average_15min is not a
        # prediction of the outcome, it IS the outcome, accumulating one
        # observation per second. Empty list disables the subscription.
        #
        # NOTE the channel takes index_ids, NOT market_tickers - the page is
        # explicit that "market_ticker/market_tickers/market_id/market_ids
        # are not supported for this channel".
        self.index_ids = list(index_ids or [])
        # Pyth underlyings (docs/kalshi/pyth-value.md) - same shape, different
        # param name (underlying_tickers) and no windowed averages.
        self.underlying_tickers = list(underlying_tickers or [])
        self.base_url = (base_url or "").strip().lower()
        self.key_id = os.getenv("KALSHI_API_KEY_ID", "").strip()
        self.private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()
        self.ws_url = _DEMO_WS_URL if "demo.kalshi" in self.base_url else _PROD_WS_URL
        self._private_key: rsa.RSAPrivateKey | None = None
        self._load_error = None
        self._ws: ClientConnection | None = None
        self._lock = asyncio.Lock()
        self._desired_tickers: set[str] = set()
        self._subscribed_tickers: set[str] = set()
        self._subscription_sids: dict[str, int] = {}
        # Tracks specifically whether THIS connection has ever sent the
        # initial trade/ticker subscribe - kept separate from
        # _subscription_sids, which also accumulates the unrelated
        # account-wide fill/market_positions sids (see run()'s own subscribe
        # for those). Real, live-confirmed bug (2026-08-16, direct report:
        # "the whale watching stream has halted completely, no signals at
        # all"): using `not self._subscription_sids` to mean "no real
        # per-ticker subscribe sent yet" broke the moment fill/
        # market_positions started populating that same dict - once those
        # two sids landed, the check went false even though trade/ticker had
        # never actually been subscribed (their own force-subscribe attempt
        # fired before the trading loop's first set_market_tickers() call,
        # when desired tickers were still empty, so it no-opped). Every
        # later real ticker-set update then got misrouted through the
        # incremental to_add/to_remove path, sending update_subscription
        # against the fill/market_positions sids instead of ever subscribing
        # trade/ticker - deterministic on every fresh connect, since the raw
        # WS handshake always wins the race against the REST-based
        # market-discovery pipeline that feeds set_market_tickers().
        #
        # Split into one flag per channel 2026-08-17 (see
        # _sync_subscriptions): exchange-wide trade goes up on connect with
        # no watchlist, while ticker still waits for one, so a single shared
        # flag would either duplicate the trade subscription or block it.
        self._trade_subscribed = False
        self._ticker_subscribed = False
        self._index_subscribed = False
        self._lifecycle_subscribed = False
        self._message_id = 1
        self._update_event = asyncio.Event()
        self._stop = False
        self._logged_fill_shape = False
        self._logged_position_shape = False
        self._logged_lifecycle_event_types: set[str] = set()
        # Ingest counters - exposed through status so a stalled consumer
        # shows up as a number rather than as a market that looks quiet.
        self.messages_received = 0
        self.dropped_messages = 0
        # Queue-health metrics (I1). Lifetime counters are monotone so any
        # two persisted observability samples can be differenced; "window"
        # accumulators are reset by the observability sampler right after
        # it persists them (services/observability/observability.py's
        # maybe_capture), so a window is one persisted sample's worth of
        # time. Every clock the queue path reads is monotonic and injectable
        # (now=...), which is what makes tests/test_kalshi_ws_ingest_metrics.py
        # deterministic without a socket.
        self._ingest_queue_max = ingest_queue_max
        self._queue: asyncio.Queue | None = None
        # P4 Task 18: populated alongside _queue; used only when
        # realtime_data_plane.two_consumer_mode is on (default off).
        self._critical_queue: asyncio.Queue | None = None
        self._market_queue: asyncio.Queue | None = None
        # P4 Task 19a: pending coalesced ticker state, ticker -> (enqueue
        # monotonic ts, parsed message). A ticker burst for one market is
        # price state, not history - only the newest update matters (the
        # consumer overwrites state["latest_prices"]), so consecutive
        # updates collapse here, newest exchange `ts` winning
        # (docs/kalshi/market-ticker.md). _coalesced counts absorbed
        # updates (superseded or stale-discarded) - visible in
        # ingest_metrics, never silent.
        self._ticker_by_market: dict[str, tuple[float, dict]] = {}
        self._coalesced = 0
        self.malformed_messages = 0
        self._received_by_class: dict[str, int] = {}
        self._processed_by_class: dict[str, int] = {}
        self._dropped_by_class: dict[str, int] = {}
        self._dropped_window = 0
        self._handler_exceptions_by_class: dict[str, int] = {}
        self._handler_exceptions_total = 0
        self._fault_logged_classes_this_window: set[str] = set()
        # Consumer-stall bound + backstop (issue #145/#150) - see
        # _HANDLER_TIMEOUT_SEC's own comment for the incident this answers.
        self._handler_timeout_sec = handler_timeout_sec
        self._handler_timeouts_by_class: dict[str, int] = {}
        self._handler_timeouts_total = 0
        self._fault_logged_timeout_classes_this_window: set[str] = set()
        self._liveness_last_processed: dict[str, int] | None = None
        self._liveness_last_messages_received = 0
        self._liveness_stuck_samples = 0
        self._queue_high_water = 0
        self._wait_last: float | None = None
        self._wait_lifetime = LatencyAgg()
        self._wait_window = LatencyAgg()
        self._wait_buckets: dict[str, int] = empty_buckets()
        self._handler_lifetime: dict[str, LatencyAgg] = {}
        self._handler_window: dict[str, LatencyAgg] = {}
        self._server_errors_total = 0
        self._server_errors_by_code: dict[str, int] = {}
        self._server_error_last: dict | None = None
        self._error_25_total = 0
        self._error_25_window = 0
        # Subscription-set churn (realtime data-plane investigation, new
        # hypothesis found 2026-08-26 investigating a direct report: "the
        # way the websocket subscriptions per Market change after every
        # Market discovery scan is also a major problem"). Nothing tracked
        # how often _sync_subscriptions actually sends an add_markets/
        # delete_markets diff, or how many tickers churn per sync, before
        # this - see ROADMAP.md.
        self._subscription_syncs_total = 0
        self._subscription_syncs_window = 0
        self._subscription_tickers_added_total = 0
        self._subscription_tickers_added_window = 0
        self._subscription_tickers_removed_total = 0
        self._subscription_tickers_removed_window = 0
        # Reader-side whale-size gate counters (realtime data-plane
        # remediation P0 Task 3 / P3 Task 17). gate_would_reject counts
        # what the gate rejected, in shadow mode AND when actually
        # filtering (the name predates Task 17's live-filtering flip -
        # kept as-is since it's an existing tested field, not renamed).
        # gate_exceptions counts the gate's own failures (fall-open: a
        # broken gate must never hide a whale - the message is enqueued
        # exactly as it is today either way, never filtered on an
        # exception). prefiltered_by_class is the same trigger, exposed
        # per-class (today only ever "trade") to match ingest_metrics()'s
        # existing received_by_class/dropped_by_class shape.
        self._gate_would_reject = 0
        self._gate_exceptions = 0
        self._prefiltered_by_class: dict[str, int] = {}
        # (timestamp, cfg) cache for _shadow_gate_check (code-review finding
        # #6, /code-review high pass against PR #23) - same shape as
        # services/series_watcher.py's own _quarantine_active() cache.
        # config_store.get()'s own docstring assumes "a handful of times per
        # tick, not per message"; _shadow_gate_check runs on the reader's
        # hot path, once per inbound trade-class WS message (thousands/
        # minute exchange-wide), which broke that assumption. Measured
        # directly (not assumed) in this environment: config_store.get()'s
        # unchanged-file path costs ~11us/call - roughly 3x json.loads' own
        # cost for a typical trade message and, since this is a pure
        # shadow-mode counter, config a second stale cannot mislabel
        # anything that matters (this gate never drops a message either
        # way - see this method's own docstring).
        self._gate_cfg_cache: tuple[float, dict] | None = None
        self._connects = 0
        self._reconnects = 0
        self._last_disconnect: dict | None = None
        # Reconnect gap duration (P8 Task 34) - lifetime holds the most
        # recent measured outage; window is consumed by the observability
        # sampler (one persisted sample per reconnect event, so the series
        # in observability.db is a real distribution over time).
        self._last_gap_sec: float | None = None
        self._gap_sec_window: float | None = None
        if self.key_id and self.private_key_path:
            try:
                with open(self.private_key_path, "rb") as f:
                    loaded_key = serialization.load_pem_private_key(f.read(), password=None)
                    if not isinstance(loaded_key, rsa.RSAPrivateKey):
                        # Kalshi API keys are RSA (docs/kalshi/api_keys.md);
                        # the RSA-PSS signing in _auth_headers is only
                        # defined for RSA. A non-RSA key always failed
                        # later, deep inside sign(), with a confusing
                        # error - fail here with a real one instead.
                        raise ValueError("KALSHI_PRIVATE_KEY_PATH is not an RSA private key")
                    self._private_key = loaded_key
            except Exception as exc:
                self._load_error = str(exc)

    @property
    def enabled(self) -> bool:
        return self._private_key is not None and bool(self.key_id)

    @property
    def status(self) -> dict:
        if self.enabled:
            return {"enabled": True, "error": None, "ws_url": self.ws_url}
        if self.key_id or self.private_key_path:
            return {"enabled": False, "error": self._load_error or "incomplete websocket credentials", "ws_url": self.ws_url}
        return {"enabled": False, "error": None, "ws_url": self.ws_url}

    async def close(self) -> None:
        self._stop = True
        self._update_event.set()
        async with self._lock:
            ws = self._ws
        if ws is not None:
            await ws.close()

    async def force_reconnect(self, reason: str) -> None:
        """Closes only the current connection - unlike close(), never sets
        self._stop, so run()'s existing exception -> _record_disconnect ->
        backoff -> reconnect path takes over exactly as it would for a real
        network drop. That path already discards the stuck consumer task
        (run()'s own `finally: consumer.cancel()`) and starts a fresh one on
        the next connection. Called by ensure_consumer_progressing(); see
        its docstring and issue #145 for why this exists."""
        logger.warning("kalshi_websocket: forcing reconnect (%s)", reason)
        fault_log.record_fault("kalshi_websocket", "consumer_stalled_forced_reconnect", reason, severity="error")
        async with self._lock:
            ws = self._ws
        if ws is not None:
            await ws.close()

    async def ensure_consumer_progressing(self) -> bool:
        """Backstop liveness check (issue #145) for whatever
        _HANDLER_TIMEOUT_SEC doesn't structurally bound - e.g. a hang inside
        _sync_subscriptions, which runs on the reader's own task, not the
        consumer's _consume() task. Call on a timer (main.py).

        Signal is direct, not a proxy: total processed-message count held
        flat across _LIVENESS_STUCK_SAMPLES_THRESHOLD consecutive calls
        while messages_received kept climbing and the queue holds a
        backlog. That's "new work arrived, nothing got consumed" - the
        exact shape of the 2026-08-27 incident (queue at capacity,
        oldest_message_age_sec growing 1:1 with wall clock, zero drain).
        A genuinely quiet market (messages_received not climbing) or a
        slow-but-progressing consumer (processed count still advancing)
        both correctly report not-stuck.

        Returns whether this call forced a reconnect."""
        if self._queue is None:
            return False
        # Per-group progress (P4 Task 18): a wedged market consumer must not
        # hide behind a healthy critical consumer's advancing total (the
        # exact hang class this backstop exists for, issue #145). Each group
        # is checked against the queue its own consumer drains; the single
        # queue keeps the original total-progress check, since one consumer
        # drains every class there.
        critical_processed = sum(
            n for cls, n in self._processed_by_class.items() if cls in _CRITICAL_CLASSES)
        processed = {
            "total": sum(self._processed_by_class.values()),
            "critical": critical_processed,
        }
        processed["market"] = processed["total"] - critical_processed
        received_total = self.messages_received
        last = self._liveness_last_processed
        market_backlog = (
            (self._market_queue.qsize() if self._market_queue is not None else 0)
            + len(self._ticker_by_market)
        )
        frozen_with_backlog = last is not None and (
            (processed["total"] == last["total"] and self._queue.qsize() > 0)
            or (processed["critical"] == last["critical"]
                and self._critical_queue is not None and self._critical_queue.qsize() > 0)
            or (processed["market"] == last["market"] and market_backlog > 0)
        )
        stuck = frozen_with_backlog and received_total > self._liveness_last_messages_received
        self._liveness_last_processed = processed
        self._liveness_last_messages_received = received_total
        if not stuck:
            self._liveness_stuck_samples = 0
            return False
        self._liveness_stuck_samples += 1
        if self._liveness_stuck_samples < _LIVENESS_STUCK_SAMPLES_THRESHOLD:
            return False
        self._liveness_stuck_samples = 0
        await self.force_reconnect(
            f"consumer made no progress across {_LIVENESS_STUCK_SAMPLES_THRESHOLD} liveness checks "
            f"while messages kept arriving (queue depth {self._total_queue_depth()})"
        )
        return True

    async def set_market_tickers(self, tickers: list[str]) -> None:
        normalized = {t for t in tickers if t}
        if normalized == self._desired_tickers:
            return
        self._desired_tickers = normalized
        self._update_event.set()

    async def request_index_list(self) -> None:
        """Ask the server which CF Benchmarks index IDs actually exist
        (docs/kalshi/cfbenchmarks-value.md's `indexlist` action, which
        "returns the available index IDs ... without modifying the
        subscription"). The reply arrives as a cfbenchmarks_value_indexlist
        message.

        Worth having as a real call rather than a hardcoded list: a live
        sweep of settlement rules on 2026-08-17 found the SAME index named
        three different ways across series - "BRTI", "ETHUSDRTI" (no
        underscore) and "ERTI" - none of which is guaranteed to be the
        channel's own identifier. Asking is the only way to know."""
        sid = self._subscription_sids.get("cfbenchmarks_value")
        if sid is None:
            return
        await self._send({
            "id": self._next_message_id(),
            "cmd": "update_subscription",
            "params": {"sid": sid, "action": "indexlist"},
        })

    async def run(self, on_trade, on_ticker, on_status=None, on_fill=None, on_position=None,
                  on_index=None, on_lifecycle=None) -> None:
        backoff = 1.0
        while not self._stop:
            if not self.enabled:
                if on_status is not None:
                    await on_status({"connected": False, **self.status})
                await asyncio.sleep(5)
                continue
            try:
                headers = self._auth_headers()
                async with websockets.connect(
                    self.ws_url,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=1024,
                ) as websocket:
                    async with self._lock:
                        self._ws = websocket
                        self._subscription_sids = {}
                        self._subscribed_tickers = set()
                        self._trade_subscribed = False
                        self._ticker_subscribed = False
                        self._lifecycle_subscribed = False
                        self._message_id = 1
                    if on_status is not None:
                        await on_status({"connected": True, "error": None, "ws_url": self.ws_url})
                    await self._sync_subscriptions(force_subscribe=True)
                    if on_fill is not None or on_position is not None:
                        # Account-wide channels (2026-08-15 direct request: "the
                        # open positions should feed from the websocket
                        # stream") - deliberately NOT part of _sync_subscriptions
                        # above, which is entirely about the per-ticker desired
                        # set (trade/ticker channels only make sense scoped to
                        # specific markets; fill/market_positions are inherently
                        # scoped to this authenticated account as a whole, same
                        # credentials already used for trade/ticker - confirmed
                        # via docs/kalshi/websocket-connection.md's channel
                        # list, no market_ticker param involved). Subscribed
                        # once per connection here, never touched by
                        # _sync_subscriptions' add/remove-markets logic.
                        await self._send({
                            "id": self._next_message_id(),
                            "cmd": "subscribe",
                            "params": {"channels": ["fill", position_contract.SUBSCRIPTION_CHANNEL]},
                        })
                    backoff = 1.0
                    # Ingest and processing are separate tasks (2026-08-17).
                    #
                    # They used to be one loop: receive a message, then await
                    # its full handler before receiving the next. That was
                    # survivable while the trade channel was scoped to a
                    # ~150-market watchlist, and stopped being survivable the
                    # moment it went exchange-wide. Every trade message costs
                    # a thread hop (whale_provider.fetch_signals ->
                    # asyncio.to_thread) plus DB reads, so at exchange volume
                    # the socket was only drained as fast as the slowest
                    # handler - and the ~1/second index ticks, which decide
                    # settlement, ended up queued behind thousands of trades.
                    #
                    # Confirmed rather than theorised: a standalone
                    # connection subscribed only to cfbenchmarks_value
                    # received exactly 50 messages in 50 seconds (the
                    # documented 1/sec), while the same channel on the shared
                    # connection delivered ~20 and then appeared frozen for
                    # minutes.
                    #
                    # Now the reader does nothing but recv, parse, classify
                    # and enqueue (see _ingest_raw - a few microseconds), and
                    # a worker drains the queue. The queue is BOUNDED and
                    # overflow is counted rather than silently absorbed - a
                    # stalled consumer must be visible (see
                    # self.dropped_messages / ingest_metrics()), not disguised
                    # as a quiet market.
                    queue = self._begin_connection()
                    # _begin_connection just created all three; local,
                    # non-Optional handles for the consumer tasks below.
                    market_queue = self._ensure_split_queues()
                    critical_queue = self._critical_queue
                    assert critical_queue is not None

                    def _spawn(q):
                        return asyncio.create_task(self._consume_from(
                            q, on_trade, on_ticker, on_status, on_fill,
                            on_position, on_index, on_lifecycle,
                        ))

                    # P4 Task 18: all three consumers run every connection
                    # (an idle queue's consumer just parks on .get()), so a
                    # runtime two_consumer_mode flip mid-connection strands
                    # nothing - the reader's routing decides which queues
                    # actually receive work. Same per-connection create_task
                    # lifecycle as the original single consumer: teardown
                    # cancels them with the socket, ensure_consumer_
                    # progressing() + the per-message handler timeout cover
                    # stalls (issue #145), which supervise(restart=True)
                    # could not - a consumer's queue closure dies with its
                    # connection, so restarting the coroutine alone would
                    # resurrect it on a dead queue.
                    consumers = [
                        _spawn(queue), _spawn(critical_queue),
                        asyncio.create_task(self._consume_market_from(
                            market_queue, on_trade, on_ticker, on_status, on_fill,
                            on_position, on_index, on_lifecycle,
                        )),
                    ]
                    try:
                        while not self._stop:
                            update_task = asyncio.create_task(self._update_event.wait())
                            recv_task = asyncio.create_task(websocket.recv())
                            done, pending = await asyncio.wait(
                                {update_task, recv_task}, return_when=asyncio.FIRST_COMPLETED,
                            )
                            for task in pending:
                                task.cancel()
                            if update_task in done and self._update_event.is_set():
                                self._update_event.clear()
                                await self._sync_subscriptions()
                            if recv_task in done:
                                self._ingest_raw(recv_task.result())
                    finally:
                        for consumer in consumers:
                            consumer.cancel()
            except Exception as exc:
                self._record_disconnect(exc)
                if on_status is not None:
                    await on_status({"connected": False, "error": str(exc), "ws_url": self.ws_url})
                async with self._lock:
                    self._ws = None
                    self._subscription_sids = {}
                    self._subscribed_tickers = set()
                    self._trade_subscribed = False
                    self._ticker_subscribed = False
                    self._lifecycle_subscribed = False
                if self._stop:
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _handle_message(self, raw_message, on_trade, on_ticker, on_status, on_fill=None, on_position=None, on_index=None, on_lifecycle=None) -> None:
        # Accepts the reader's already-parsed dict (run() path, via
        # _process_item) or a raw JSON string (direct callers and the
        # pre-I1 tests) - one parse per message either way.
        data = raw_message if isinstance(raw_message, dict) else json.loads(raw_message)
        msg_type = data.get("type")
        if msg_type == "subscribed":
            msg = data.get("msg") or {}
            channel = msg.get("channel")
            sid = msg.get("sid")
            if channel and sid:
                self._subscription_sids[channel] = sid
            return
        if msg_type == "ok":
            return
        if msg_type == "error":
            err = data.get("msg") or {}
            self._record_server_error(err)
            if on_status is not None:
                await on_status({"connected": True, "error": f"Kalshi WS error {err.get('code')}: {err.get('msg')}", "ws_url": self.ws_url})
            return
        # Index feeds (docs/kalshi/cfbenchmarks-value.md, pyth-value.md).
        # Dispatched with the message type attached because the two payloads
        # are genuinely different shapes - cfbenchmarks carries the windowed
        # settlement averages, pyth is a bare price - and the handler has to
        # tell them apart rather than duck-type its way through.
        if msg_type in ("cfbenchmarks_value", "pyth_value"):
            if on_index is not None:
                await on_index(msg_type, data.get("msg") or {})
            return
        if msg_type in ("cfbenchmarks_value_indexlist", "pyth_value_underlying_list"):
            # Discovery reply - see request_index_list(). Printed rather than
            # routed anywhere, because its whole purpose is telling a human
            # which identifiers are real (a live sweep found the same index
            # named BRTI / ETHUSDRTI / ERTI across different series' rules).
            logger.info("%s: %r", msg_type, data.get('msg'))
            return
        if msg_type == "trade":
            await on_trade(trade_contract.normalize_trade(data.get("msg") or {}))
            return
        if msg_type == "market_lifecycle_v2":
            # docs/kalshi/market-and-event-lifecycle.md's msg.event_type
            # names which of created/activated/deactivated/close_date_
            # updated/determined/settled/price_level_structure_updated/
            # metadata_updated this is. Each shape is logged once (same
            # "verify against a real payload before trusting it" idiom as
            # fill/market_positions below) since only close_date_updated
            # has been wired to do anything yet - see main.py's
            # _process_stream_lifecycle.
            msg = data.get("msg") or {}
            event_type = msg.get("event_type")
            if event_type and event_type not in self._logged_lifecycle_event_types:
                self._logged_lifecycle_event_types.add(event_type)
                logger.info("first real 'market_lifecycle_v2' %r shape (verify parsing against this): %r", event_type, msg)
            if on_lifecycle is not None:
                await on_lifecycle(lifecycle_contract.normalize_lifecycle(msg))
            return
        if msg_type == "ticker":
            await on_ticker(ticker_contract.normalize_ticker(data.get("msg") or {}))
            return
        if msg_type == "fill":
            # 2026-08-15 direct request: "the open positions should feed
            # from the websocket stream." Real message shape has never
            # been observed live (fill events only fire on a real order
            # fill, and kalshi_account.trading_enabled is off, the standing
            # P0 safety gate - see CLAUDE.md - so none can occur yet) - the
            # raw payload is logged once, the first time one ever arrives,
            # specifically so it can be verified/corrected against real
            # data rather than trusted blind. main.py's handler is
            # defensive (dict.get() throughout, never assumes a field
            # exists) for the same reason.
            if not self._logged_fill_shape:
                self._logged_fill_shape = True
                logger.info("first real 'fill' message shape (verify parsing against this): %r", data)
            if on_fill is not None:
                await on_fill(fill_contract.normalize_fill(data.get("msg") or {}))
            return
        if msg_type == position_contract.WS_MESSAGE_TYPE:
            # Real per-message `type` is "market_position" (SINGULAR) per
            # docs/kalshi/market-positions.md's own schema
            # (`const: market_position`) - confirmed 2026-08-24 building
            # tests/test_kalshi_contracts.py (QCP Task 13). The
            # *subscription channel* name is "market_positions" (plural,
            # see run()'s subscribe params a few lines up) - easy to
            # confuse the two, and this dispatch previously checked the
            # plural channel name here by mistake, so a real position
            # update's `type` field never matched and on_position was
            # never invoked at all, for any real account.
            if not self._logged_position_shape:
                self._logged_position_shape = True
                logger.info("first real 'market_position' message shape (verify parsing against this): %r", data)
            if on_position is not None:
                await on_position(position_contract.normalize_position(data.get("msg") or {}))

    # --- ingest queue health (realtime data-plane investigation, I1) --------
    #
    # The four failure points docs/superpowers/research/2026-08-25-realtime-
    # data-plane-known-findings.md requires telling apart - Kalshi-side
    # subscription overflow (server error 25), the `websockets` receive
    # buffer, this application queue overflowing (QueueFull), and downstream
    # processing backlog (queue wait / oldest age) - each get their own
    # counter here. Before I1 the first and third collapsed into one
    # ephemeral status string plus one lifetime int.

    def _begin_connection(self, now: float | None = None) -> asyncio.Queue:
        """Fresh bounded reader->consumer queue for one physical connection.
        run() calls this right after the socket is up, so a reconnect starts
        empty with its own depth history; tests call it directly.

        Reconnect gap duration (P8 Task 34): when this connection follows a
        recorded disconnect, the wall-clock gap between the two is the real
        outage duration - the input the staleness benchmark (P8 Task 40)
        needs a measured distribution of, not an assumed one. Negative gaps
        (wall-clock skew - _record_disconnect and this both read
        time.time()) are dropped rather than recorded as fabricated data."""
        self._queue = asyncio.Queue(maxsize=self._ingest_queue_max)
        # P4 Task 18: the split queues are rebuilt alongside the single one
        # every connection - always constructed (cheap, empty) so a runtime
        # flag flip mid-connection routes into a real queue either way.
        self._critical_queue = asyncio.Queue(maxsize=self._ingest_queue_max)
        self._market_queue = asyncio.Queue(maxsize=self._ingest_queue_max)
        # Pending coalesced tickers die with the connection, same as queued
        # messages today - the ticker channel re-snapshots on resubscribe
        # (send_initial_snapshot) and staleness is visible either way.
        self._ticker_by_market = {}
        self._connects += 1
        if self._last_disconnect is not None:
            gap = (now if now is not None else time.time()) - self._last_disconnect["at"]
            if gap >= 0.0:
                self._last_gap_sec = round(gap, 3)
                self._gap_sec_window = self._last_gap_sec
        return self._queue

    @staticmethod
    def _message_class(data) -> str:
        msg_type = data.get("type") if isinstance(data, dict) else None
        if not isinstance(msg_type, str):
            return _OTHER_CLASS
        return _CLASS_BY_MESSAGE_TYPE.get(msg_type, _OTHER_CLASS)

    def _cached_gate_cfg(self) -> dict:
        """config_store.get(), cached for a second (finding #6 - see
        self._gate_cfg_cache's own comment in __init__). A one-second lag
        cannot mislabel anything a pure shadow-mode counter reports."""
        now = time.time()
        if self._gate_cfg_cache is None or (now - self._gate_cfg_cache[0]) > 1.0:
            self._gate_cfg_cache = (now, config_store.get())
        return self._gate_cfg_cache[1]

    def _two_consumer_mode(self) -> bool:
        """P4 Task 18 flag, read through the same 1s-cached config as the
        reader gate - a flag flip routes new messages within a second; the
        consumers for both topologies are always running (run() launches
        them per-connection), so no message is stranded by a mid-connection
        flip."""
        return bool((self._cached_gate_cfg().get("realtime_data_plane") or {}).get("two_consumer_mode"))

    def _total_queue_depth(self) -> int:
        """Backlog across every ingest queue this connection may be using -
        the liveness backstop and depth metrics must see a backlog wherever
        it lives (single, critical, or market queue)."""
        return sum(q.qsize() for q in (self._queue, self._critical_queue, self._market_queue) if q is not None)

    def _gate_check_and_maybe_filter(self, trade_msg: dict) -> bool:
        """Realtime data-plane remediation P0 Task 3 (shadow counting) / P3
        Task 17 (live filtering, gated by config/settings.yaml's
        realtime_data_plane.reader_gate_enabled, default false). Returns
        whether _ingest_raw should skip enqueueing this message - only
        True when the reader gate is actually enabled AND whale_gate.
        passes() rejected it.

        gate_would_reject/prefiltered_by_class always count a rejection,
        in shadow mode or live - the design spec's shadow-counting
        contract never stops just because filtering is also happening. A
        gate exception falls open (counted, not raised, never treated as
        a rejection) so a bug in the gate itself can never hide a real
        whale print; the message is enqueued exactly as it is today
        either way.

        When actually filtering (live, rejected), this method also does
        what would otherwise have happened downstream after dequeue -
        series_watcher.record_trade and candidate_log.record_rejection
        (design spec's capture contract: filtering must never mean
        losing the data) - because _ingest_raw will not enqueue this
        message, so on_trade/_process_stream_trade's own calls to both
        will never run for it. In shadow mode, or when the gate passes,
        neither is called here: the message still reaches the consumer
        normally, which already calls both exactly as it does today - a
        second call here would be redundant work on the hot path, not a
        correctness issue (record_trade's own trade_id PRIMARY KEY makes
        a duplicate harmless), but there is no reason to pay for it.

        Deliberately simpler than kalshi_trade_tape.py's own multi-stage
        rejection taxonomy (unparseable_count / unparseable_price /
        market_unresolved / min_contracts, on/off-watchlist distinguished)
        - full parity would mean duplicating watchlist/market-catalog
        resolution into this transport-layer reader, which belongs in
        kalshi_trade_tape.py, not here. Two things ARE still worth
        replicating cheaply (both are single dict-field reads on data
        already in hand, no lookup, no extra cost):
        - side unreadable (resolve_taker_outcome_side returns None): skip
          recording entirely, matching kalshi_trade_tape.py's own
          `if side is None: continue` - there is nothing to attribute a
          hypothetical win/loss to. Still filtered (not enqueued) either
          way - downstream would not have produced a candidate from it.
        - count unparseable (trade_contract_count returns None):
          gate_name="unparseable_count", not "min_contracts" - conflating
          "too small" with "couldn't even be read" would mislabel
          population_gate_summary()'s own per-gate breakdown.
        unit_cost is simply None when yes_price_dollars is unparseable
        (record_rejection's own established "unknown, not fabricated"
        contract) rather than its own third gate_name - a real, accepted
        simplification relative to kalshi_trade_tape.py's own separate
        "unparseable_price" bucket, documented here rather than silently
        diverging."""
        cfg = self._cached_gate_cfg()
        ticker = trade_msg.get("market_ticker") or ""
        min_contracts = whale_gate.min_contracts_for(ticker, cfg)
        try:
            rejected = not whale_gate.passes(trade_msg, min_contracts=min_contracts)
        except Exception as exc:
            self._gate_exceptions += 1
            fault_log.record("whale_gate", "passes", exc)
            return False
        if not rejected:
            return False
        self._gate_would_reject += 1
        self._prefiltered_by_class["trade"] = self._prefiltered_by_class.get("trade", 0) + 1
        if not (cfg.get("realtime_data_plane") or {}).get("reader_gate_enabled"):
            return False  # shadow mode - still enqueue below exactly as today
        side = trade_contract.resolve_taker_outcome_side(trade_msg)
        if side is None:
            return True  # filter, but nothing to record - see docstring
        series_watcher.record_trade(trade_msg)
        count = trade_contract.trade_contract_count(trade_msg)
        if count is None:
            candidate_log.record_rejection(ticker, "whale_watcher", "unparseable_count", 0.0, 0.0, side=side)
            return True
        price = trade_contract._dollars(trade_msg.get("yes_price_dollars"))
        unit_cost = (price if side == "yes" else (1.0 - price)) if price is not None else None
        candidate_log.record_rejection(
            ticker, "whale_watcher", "min_contracts", count, min_contracts, side=side, unit_cost=unit_cost,
        )
        return True

    def _ingest_raw(self, raw_message, now: float | None = None) -> bool:
        """Reader side: parse, classify, count, then enqueue or drop. Returns
        whether the message was enqueued.

        json.loads moved here from the consumer (still exactly one parse per
        message, just earlier) because a drop has to know the message's
        class to be attributable, and the enqueue timestamp has to be taken
        at receive time - not dequeue time - for queue wait to mean
        anything. A frame that isn't JSON is counted as malformed and never
        enqueued: it is not a drop (the queue had room) and must not tear
        the connection down the way an exception escaping the reader loop
        would. `now` is monotonic; injectable for deterministic tests."""
        self.messages_received += 1
        try:
            data = json.loads(raw_message)
        except (TypeError, ValueError):
            self.malformed_messages += 1
            return False
        cls = self._message_class(data)
        self._received_by_class[cls] = self._received_by_class.get(cls, 0) + 1
        if cls == "trade" and self._gate_check_and_maybe_filter(data.get("msg") or {}):
            return False  # reader gate live and rejected - not a drop (queue had room), not enqueued
        if self._two_consumer_mode():
            if cls == "ticker":
                return self._coalesce_ticker(data, now)
            market_queue = self._ensure_split_queues()
            critical_queue = self._critical_queue
            assert critical_queue is not None  # _ensure_split_queues just set it
            queue = critical_queue if cls in _CRITICAL_CLASSES else market_queue
        else:
            single_queue = self._queue
            if single_queue is None:
                single_queue = self._queue = asyncio.Queue(maxsize=self._ingest_queue_max)
            queue = single_queue
        if now is None:
            now = time.monotonic()
        try:
            queue.put_nowait((now, cls, data))
        except asyncio.QueueFull:
            self.dropped_messages += 1
            self._dropped_window += 1
            self._dropped_by_class[cls] = self._dropped_by_class.get(cls, 0) + 1
            return False
        depth = queue.qsize()
        if depth > self._queue_high_water:
            self._queue_high_water = depth
        return True

    def _ensure_split_queues(self) -> asyncio.Queue:
        """Lazy-create the split queues, same fallback pattern as
        self._queue in _ingest_raw - direct-ingest callers (tests, replay)
        may not have run _begin_connection. Returns the market queue (the
        one every caller of this helper needs a non-None handle to)."""
        if self._market_queue is None or self._critical_queue is None:
            self._critical_queue = asyncio.Queue(maxsize=self._ingest_queue_max)
            self._market_queue = asyncio.Queue(maxsize=self._ingest_queue_max)
        return self._market_queue

    def _coalesce_ticker(self, data: dict, now: float | None) -> bool:
        """P4 Task 19a (two-consumer mode only): fold this ticker update into
        the per-market pending map, newest exchange `ts` winning. Returns
        True (the update is accepted - absorbed, not dropped; _coalesced
        counts every absorption so the volume stays visible)."""
        msg = data.get("msg") or {}
        ticker = msg.get("market_ticker") or msg.get("ticker")
        if now is None:
            now = time.monotonic()
        market_queue = self._ensure_split_queues()
        if not ticker:
            # Unroutable without a market key - hand it to the market queue
            # unchanged rather than inventing a coalescing identity for it.
            try:
                market_queue.put_nowait((now, "ticker", data))
            except asyncio.QueueFull:
                self.dropped_messages += 1
                self._dropped_window += 1
                self._dropped_by_class["ticker"] = self._dropped_by_class.get("ticker", 0) + 1
                return False
            return True
        existing = self._ticker_by_market.get(ticker)
        if existing is not None:
            new_ts, old_ts = _update_ts_ms(msg), _update_ts_ms(existing[1].get("msg") or {})
            if new_ts is not None and old_ts is not None and new_ts < old_ts:
                self._coalesced += 1  # stale (older exchange ts arrived later) - discarded
                return True
            self._coalesced += 1  # supersedes the unconsumed pending update
            self._ticker_by_market[ticker] = (now, data)
            return True
        was_empty = not self._ticker_by_market
        self._ticker_by_market[ticker] = (now, data)
        if was_empty:
            # Wake a market consumer parked on an empty queue. Only the
            # empty -> non-empty transition needs it, and put_nowait cannot
            # fail precisely when it matters (a parked consumer implies an
            # empty queue); with a busy queue the drain loop reaches the
            # map on its own, so a Full here is safely ignored.
            try:
                market_queue.put_nowait((now, _TICKER_WAKE, None))
            except asyncio.QueueFull:
                pass
        return True

    def _pending_ticker_by_market(self) -> dict[str, dict]:
        return {ticker: data for ticker, (_, data) in self._ticker_by_market.items()}

    async def _consume_market_from(self, queue: asyncio.Queue, on_trade=None, on_ticker=None, on_status=None,
                                   on_fill=None, on_position=None, on_index=None, on_lifecycle=None) -> None:
        """Market-queue consumer (P4 Task 19a): FIFO-drains real queue items
        first, then processes pending coalesced tickers one per iteration
        when the queue is momentarily empty - trades keep strict arrival
        order, tickers are level state where cross-market order is
        deliberately unguaranteed (design spec §3)."""
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                if self._ticker_by_market:
                    ticker = next(iter(self._ticker_by_market))
                    enqueued_at, data = self._ticker_by_market.pop(ticker)
                    await self._process_item(
                        (enqueued_at, "ticker", data), on_trade, on_ticker, on_status,
                        on_fill, on_position, on_index, on_lifecycle,
                    )
                    continue
                item = await queue.get()
            try:
                if item[1] != _TICKER_WAKE:
                    await self._process_item(
                        item, on_trade, on_ticker, on_status, on_fill,
                        on_position, on_index, on_lifecycle,
                    )
            finally:
                queue.task_done()

    async def _consume_from(self, queue: asyncio.Queue, on_trade=None, on_ticker=None, on_status=None,
                            on_fill=None, on_position=None, on_index=None, on_lifecycle=None) -> None:
        """Drain one ingest queue forever (P4 Task 18 - the former run()-local
        _consume(), extracted so run() can launch one per queue). _process_item
        never raises for a handler failure: it counts it and fault-logs once
        per class per window, so one mishandled message still can't tear down
        the socket - but it no longer vanishes either (I1: the old bare
        `except: pass` here made a systematically failing handler class
        indistinguishable from a quiet market)."""
        while True:
            item = await queue.get()
            try:
                await self._process_item(
                    item, on_trade, on_ticker, on_status, on_fill,
                    on_position, on_index, on_lifecycle,
                )
            finally:
                queue.task_done()

    async def _process_item(self, item, on_trade, on_ticker, on_status, on_fill=None, on_position=None,
                            on_index=None, on_lifecycle=None, now: float | None = None) -> None:
        """Consumer side: record this message's queue wait, time its handler
        by class, count the outcome. Never raises for a handler failure."""
        enqueued_at, cls, data = item
        if now is None:
            now = time.monotonic()
        wait = now - enqueued_at
        if wait < 0.0:
            wait = 0.0
        self._wait_last = wait
        self._wait_lifetime.add(wait)
        self._wait_window.add(wait)
        self._wait_buckets[bucket_for(wait)] += 1
        started = time.monotonic()
        token = MESSAGE_ENQUEUED_AT.set(enqueued_at)
        try:
            await asyncio.wait_for(
                self._handle_message(
                    data, on_trade, on_ticker, on_status, on_fill, on_position, on_index, on_lifecycle,
                ),
                timeout=self._handler_timeout_sec,
            )
        except TimeoutError:
            # Bounds an otherwise-unbounded hang (issue #145) - does NOT
            # free a stuck asyncio.to_thread's underlying OS thread (issue
            # #150, known/accepted). Kept distinct from handler_exceptions_*
            # below: conflating them would hide exactly the signal that
            # tells us whether this is happening often enough to matter.
            self._handler_timeouts_total += 1
            self._handler_timeouts_by_class[cls] = self._handler_timeouts_by_class.get(cls, 0) + 1
            if cls not in self._fault_logged_timeout_classes_this_window:
                self._fault_logged_timeout_classes_this_window.add(cls)
                fault_log.record_fault(
                    "kalshi_websocket", f"handle_message_timeout:{cls}",
                    f"{cls} handler exceeded {self._handler_timeout_sec:.0f}s - see issue #150 "
                    "for the known thread-pool-leak tradeoff this can incur",
                    severity="warn",
                )
        except Exception as exc:
            self._handler_exceptions_total += 1
            self._handler_exceptions_by_class[cls] = self._handler_exceptions_by_class.get(cls, 0) + 1
            if cls not in self._fault_logged_classes_this_window:
                # One durable fault row per class per window - a storm at
                # exchange-wide rate must not become a SQLite write per
                # message on the consumer's own critical path. The
                # in-memory counter above still carries the full count.
                self._fault_logged_classes_this_window.add(cls)
                fault_log.record("kalshi_websocket", f"handle_message:{cls}", exc)
        finally:
            MESSAGE_ENQUEUED_AT.reset(token)
            elapsed = time.monotonic() - started
            self._processed_by_class[cls] = self._processed_by_class.get(cls, 0) + 1
            lifetime = self._handler_lifetime.get(cls)
            if lifetime is None:
                lifetime = self._handler_lifetime[cls] = LatencyAgg()
            lifetime.add(elapsed)
            window = self._handler_window.get(cls)
            if window is None:
                window = self._handler_window[cls] = LatencyAgg()
            window.add(elapsed)

    def _record_server_error(self, err: dict) -> None:
        code = err.get("code")
        self._server_errors_total += 1
        key = str(code) if code is not None else "unknown"
        if key in self._server_errors_by_code or len(self._server_errors_by_code) < _MAX_SERVER_ERROR_CODES_TRACKED:
            self._server_errors_by_code[key] = self._server_errors_by_code.get(key, 0) + 1
        self._server_error_last = {"code": code, "msg": err.get("msg"), "at": time.time()}
        if code == _KALSHI_SUBSCRIPTION_OVERFLOW_CODE:
            self._error_25_total += 1
            self._error_25_window += 1

    def _record_disconnect(self, exc: BaseException, now: float | None = None) -> None:
        self._reconnects += 1
        self._last_disconnect = {
            "reason": f"{type(exc).__name__}: {exc}"[:_MAX_DISCONNECT_REASON_CHARS],
            "at": now if now is not None else time.time(),
        }

    def reset_ingest_window(self) -> None:
        """Owned by the observability sampler: called right after a sample
        is persisted, so every "window" figure covers exactly one persisted
        sample's span. Lifetime counters are untouched."""
        self._dropped_window = 0
        self._error_25_window = 0
        self._wait_window = LatencyAgg()
        self._wait_buckets = empty_buckets()
        self._handler_window = {cls: LatencyAgg() for cls in self._handler_lifetime}
        self._fault_logged_classes_this_window.clear()
        self._fault_logged_timeout_classes_this_window.clear()
        self._subscription_syncs_window = 0
        self._subscription_tickers_added_window = 0
        self._subscription_tickers_removed_window = 0
        self._gap_sec_window = None

    def _oldest_message_age(self, now: float) -> float | None:
        """Age of the oldest unconsumed message across every ingest queue in
        use (single/critical/market - P4 Task 18): staleness lives wherever
        the backlog does, so this is the max over the non-empty queues."""
        ages: list[float] = []
        for queue in (self._queue, self._critical_queue, self._market_queue):
            if queue is None or queue.empty():
                continue
            try:
                # asyncio.Queue keeps its items in a deque named _queue (the
                # attribute its own stdlib subclasses override). Peeking the
                # head is read-only; if the implementation ever changes,
                # report unknown (None) rather than a fabricated age.
                head = queue._queue[0]  # type: ignore[attr-defined]
            except Exception:
                return None
            ages.append(max(now - head[0], 0.0))
        return round(max(ages), 4) if ages else 0.0

    def ingest_metrics(self, now: float | None = None) -> dict:
        """Point-in-time queue-health snapshot. Pure read - no resets (the
        observability sampler owns reset_ingest_window). `now` is monotonic,
        injectable for tests."""
        if now is None:
            now = time.monotonic()
        queue = self._queue
        wait_window = self._wait_window.snapshot(1.0, "sec")
        wait_window["p95_upper_bound_sec"] = p95_upper_bound(self._wait_buckets, self._wait_window.count)
        return {
            "messages_received": self.messages_received,
            "dropped_messages": self.dropped_messages,
            "dropped_window": self._dropped_window,
            "malformed_messages": self.malformed_messages,
            "received_by_class": dict(self._received_by_class),
            "processed_by_class": dict(self._processed_by_class),
            "dropped_by_class": dict(self._dropped_by_class),
            "handler_exceptions_total": self._handler_exceptions_total,
            "handler_exceptions_by_class": dict(self._handler_exceptions_by_class),
            "handler_timeouts_total": self._handler_timeouts_total,
            "handler_timeouts_by_class": dict(self._handler_timeouts_by_class),
            "queue": {
                # Total backlog across every queue in use (P4 Task 18) -
                # in single-consumer mode this equals the one queue's depth.
                "depth": self._total_queue_depth(),
                "capacity": self._ingest_queue_max,
                # Task 19a coalescing visibility: absorbed ticker updates
                # (lifetime, monotone) and the pending map's current size.
                "coalesced_tickers": self._coalesced,
                "pending_tickers": len(self._ticker_by_market),
                "high_water": self._queue_high_water,
                "oldest_message_age_sec": self._oldest_message_age(now),
            },
            "queue_wait": {
                "last_sec": round(self._wait_last, 6) if self._wait_last is not None else None,
                "lifetime": self._wait_lifetime.snapshot(1.0, "sec"),
                "window": wait_window,
                "buckets": dict(self._wait_buckets),
            },
            "handler_time_by_class": {
                cls: {
                    "window": self._handler_window.get(cls, LatencyAgg()).snapshot(1000.0, "ms"),
                    "lifetime": agg.snapshot(1000.0, "ms"),
                }
                for cls, agg in self._handler_lifetime.items()
            },
            "server_errors": {
                "total": self._server_errors_total,
                "by_code": dict(self._server_errors_by_code),
                "last": dict(self._server_error_last) if self._server_error_last else None,
            },
            "error_25_total": self._error_25_total,
            "error_25_window": self._error_25_window,
            "gate_would_reject": self._gate_would_reject,
            "gate_exceptions": self._gate_exceptions,
            "prefiltered": dict(self._prefiltered_by_class),
            "connection": {
                "connects": self._connects,
                "reconnects": self._reconnects,
                "last_disconnect": dict(self._last_disconnect) if self._last_disconnect else None,
                "last_gap_sec": self._last_gap_sec,
                "gap_sec_window": self._gap_sec_window,
            },
            "subscription_churn": {
                "syncs_total": self._subscription_syncs_total,
                "syncs_window": self._subscription_syncs_window,
                "tickers_added_total": self._subscription_tickers_added_total,
                "tickers_added_window": self._subscription_tickers_added_window,
                "tickers_removed_total": self._subscription_tickers_removed_total,
                "tickers_removed_window": self._subscription_tickers_removed_window,
            },
        }

    async def _sync_subscriptions(self, force_subscribe: bool = False) -> None:
        # force_subscribe is now implied rather than read: run() resets both
        # per-channel flags before calling this on a fresh connection, which
        # is exactly what the parameter used to express. Kept in the
        # signature so the call site still reads as intentional.
        async with self._lock:
            ws = self._ws
        if ws is None:
            return
        desired = set(self._desired_tickers)

        # trade and ticker are tracked separately (2026-08-17). They used to
        # share one _market_channels_subscribed flag, which was fine only
        # because both were subscribed in the same breath off the same
        # watchlist. In exchange-wide mode they have genuinely different
        # lifecycles: trade can and should go up immediately on connect with
        # no watchlist at all, while ticker still has to wait for one. One
        # shared flag would either re-subscribe trade every time a watchlist
        # finally arrived (duplicate firehose) or block trade until it did
        # (defeating the point).
        # Connect-time subscribes. Exchange-wide trade and market_lifecycle_v2
        # take no channel-specific params, so they share ONE subscribe
        # message (P7 Task 33, 2026-08-28) - the same combined shape
        # fill+market_positions already use in run(). Each channel keeps
        # its own gate; the message just carries whichever are due. A
        # watchlist-scoped trade subscribe stays its own message: its
        # market_tickers live at the top level of the params object and
        # would wrongly apply to lifecycle, which takes no market filter at
        # all (docs/kalshi/market-and-event-lifecycle.md). ticker and the
        # index channels stay separate for the same reason. Kalshi answers
        # a multi-channel subscribe with one `subscribed` per channel
        # (websocket-connection.md's Subscribed Response schema), which
        # _handle_message already processes one channel/sid at a time.
        combined: list[str] = []
        if not self._trade_subscribed:
            if self.exchange_wide_trades:
                combined.append("trade")
            elif desired:
                await self._send({
                    "id": self._next_message_id(),
                    "cmd": "subscribe",
                    "params": {"channels": ["trade"], "market_tickers": sorted(desired)},
                })
                self._trade_subscribed = True
                self._subscribed_tickers = desired

        # market_lifecycle_v2 - opt-in (see __init__), unconditionally
        # exchange-wide like trade above, so it goes up once on connect and
        # never participates in add_markets/delete_markets either.
        if self.subscribe_lifecycle and not self._lifecycle_subscribed:
            combined.append("market_lifecycle_v2")

        if combined:
            await self._send({
                "id": self._next_message_id(),
                "cmd": "subscribe",
                "params": {"channels": combined},
            })
            if "trade" in combined:
                self._trade_subscribed = True
            if "market_lifecycle_v2" in combined:
                self._lifecycle_subscribed = True

        # Index feeds are wholly independent of the watchlist - they take
        # index_ids/underlying_tickers, and the docs are explicit that
        # market_ticker(s)/market_id(s) "are not supported for this
        # channel". So, like exchange-wide trades, they go up on connect
        # and never participate in the add_markets/delete_markets path.
        if not self._index_subscribed and (self.index_ids or self.underlying_tickers):
            if self.index_ids:
                await self._send({
                    "id": self._next_message_id(),
                    "cmd": "subscribe",
                    "params": {"channels": ["cfbenchmarks_value"], "index_ids": list(self.index_ids)},
                })
            if self.underlying_tickers:
                await self._send({
                    "id": self._next_message_id(),
                    "cmd": "subscribe",
                    "params": {"channels": ["pyth_value"],
                               "underlying_tickers": list(self.underlying_tickers)},
                })
            self._index_subscribed = True

        if not self._ticker_subscribed:
            if desired:
                await self._send({
                    "id": self._next_message_id(),
                    "cmd": "subscribe",
                    "params": {"channels": ["ticker"], "market_tickers": sorted(desired), "send_initial_snapshot": True},
                })
                self._ticker_subscribed = True
                self._subscribed_tickers = desired
            return

        to_add = sorted(desired - self._subscribed_tickers)
        to_remove = sorted(self._subscribed_tickers - desired)
        # Only the trade/ticker sids - self._subscription_sids also holds the
        # unrelated account-wide fill/market_positions sids (see run()), which
        # don't take a market_tickers add/remove payload at all.
        #
        # And in exchange-wide mode, not `trade` either: that subscription
        # has no market list to add to or delete from, and
        # docs/kalshi/websocket-connection.md documents no action for
        # switching one between scoped and unscoped. Sending add_markets
        # against it would either error or, worse, silently narrow the
        # firehose back down to a watchlist.
        # cfbenchmarks_value/pyth_value are excluded for a stronger reason
        # than exchange-wide trade is: they don't accept market_tickers at
        # all, so an add_markets against their sid is malformed by
        # construction, not merely unwanted.
        market_channels = ("ticker",) if self.exchange_wide_trades else ("trade", "ticker")
        market_channel_sids = [self._subscription_sids[c] for c in market_channels if c in self._subscription_sids]
        if to_add or to_remove:
            self._subscription_syncs_total += 1
            self._subscription_syncs_window += 1
            self._subscription_tickers_added_total += len(to_add)
            self._subscription_tickers_added_window += len(to_add)
            self._subscription_tickers_removed_total += len(to_remove)
            self._subscription_tickers_removed_window += len(to_remove)
        if to_add:
            ticker_sid = self._subscription_sids.get("ticker")
            for sid in market_channel_sids:
                params: dict = {"sid": sid, "market_tickers": to_add, "action": "add_markets"}
                if sid == ticker_sid:
                    # P7 Task 33 / R3: a ticker added mid-connection (a
                    # position opening while already connected) gets its
                    # first price from WS immediately instead of waiting for
                    # its next natural tick or Task 29's REST seed. Documented
                    # for "newly added market tickers on the ticker channel"
                    # only (websocket-connection.md), so it never rides the
                    # trade sid's update.
                    params["send_initial_snapshot"] = True
                await self._send({
                    "id": self._next_message_id(),
                    "cmd": "update_subscription",
                    "params": params,
                })
        if to_remove:
            for sid in market_channel_sids:
                await self._send({
                    "id": self._next_message_id(),
                    "cmd": "update_subscription",
                    "params": {"sid": sid, "market_tickers": to_remove, "action": "delete_markets"},
                })
        self._subscribed_tickers = desired

    async def _send(self, payload: dict) -> None:
        async with self._lock:
            if self._ws is None:
                return
            await self._ws.send(json.dumps(payload))

    def _auth_headers(self) -> dict[str, str]:
        assert self._private_key is not None  # only called when enabled (credentials loaded)
        timestamp = str(int(time.time() * 1000))
        message = (timestamp + "GET" + "/trade-api/ws/v2").encode("utf-8")
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

    def _next_message_id(self) -> int:
        current = self._message_id
        self._message_id += 1
        return current

    # Compatibility alias: semantic normalization moved verbatim to
    # services/kalshi/contracts/trade.py (A10). Same-object assignment (not
    # a wrapper) so the boundary implementation and this legacy access path
    # can never drift apart - asserted by tests/test_kalshi_contracts.py.
    normalize_trade = staticmethod(trade_contract.normalize_trade)
