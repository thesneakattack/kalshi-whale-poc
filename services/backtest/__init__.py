"""
Backtesting - stateless replay of already-logged resolved signals against a
hypothetical gate threshold, answering "if this one gate's value were
different, what would the win rate of what it accepted have looked like."
No new persistence, no replay of the trading loop itself - pure functions
over data services/signal_log.py already has. See backtest.py and
README.md for the real, disclosed scope boundary (which gates this can
and can't replay).
"""
