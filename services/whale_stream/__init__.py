"""Whale stream module - real-time trade ingestion and signal dispatch.
See decision_bridge.py, whale_stream_handlers.py, index_stream_handlers.py.
services/kalshi_trade_ws.py (the websocket transport) and
services/whalewatchers/ (whale-detection logic) are already clean and stay
where they are - see the modularization plan's "Folder-per-concern
restructuring" section."""
