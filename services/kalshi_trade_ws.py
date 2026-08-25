"""Compatibility facade for the Kalshi WebSocket stream gateway.

The transport implementation moved verbatim behind the integration
boundary at Phase A Task A11 — services/kalshi/websocket.py's
KalshiStreamGateway owns connection/auth/subscription/reconnect/dispatch
now. This module keeps the import path and class name production wiring
(services/app_state.py) and the test suite already use, via subclass
delegation with deliberately zero behavioral overrides (asserted by
tests/test_kalshi_trade_ws.py). The full history of this transport —
the subscription-sid bug, the exchange-wide trade rationale, the ingest
queue sizing — travels with the implementation; see the gateway module.
"""
from services.kalshi.websocket import KalshiStreamGateway


class KalshiTradeWebSocketClient(KalshiStreamGateway):
    pass
