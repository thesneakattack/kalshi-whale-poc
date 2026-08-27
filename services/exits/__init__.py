"""
Exit position management - the concern that decides *whether and why* an
already-open position gets closed, as opposed to services/strategy_engine.py
(entry-only after this split) and services/paper_broker.py (the broker
mechanic that actually executes a close once a reason has been decided).

See exit_engine.py (per-position rules: settlement, take-profit, stop-loss,
time-to-close, sentiment-reversal, auto-exit composite scoring) and
position_netting.py (group-level hedge/concentration management across
confirmed mutually-exclusive events, runs after exit_engine's per-position
pass). See README.md.
"""
