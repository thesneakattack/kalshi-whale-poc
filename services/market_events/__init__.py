"""
Market events - activity-phase classification (pre-tail/mid-series/
post-tail, event_lifecycle.py), real-world start-time resolution when
Kalshi's own open/close/occurrence timestamps don't reflect it
(event_schedule.py), and an ad-hoc event/series inspection utility
(event_inspector.py, a standalone diagnostic script, not imported by any
other module). No routes.py - these are consumed internally by
services/market_watch/, services/whale_stream/decision_bridge.py, and
main.py, same shape as services/whale_stream/ (a subpackage without its
own route surface). See CHEATSHEET.md.
"""
