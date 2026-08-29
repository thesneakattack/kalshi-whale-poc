# Next action

Correlate the drop episode against read-limiter saturation in `data/observability.db`:
find when `trade_stream.dropped_messages` jumped, then check whether read-limiter
waiters and `background_live_status` errors spiked in that same window. ~20 min.

Confirmed already, in source - do not re-derive: one serial consumer drains the ingest
queue (`services/kalshi/websocket.py:547`) and the trade path awaits rate-limited REST
inside it (`services/http_client.py:37`), so a REST stall fills the 20,000 queue and
`put_nowait` sheds (`websocket.py:848`). That is the drop mechanism.

If they coincide: root cause confirmed - design the fix as decoupling REST from the
serial consumer, NOT raising queue capacity (CLAUDE.md's hard rule names that exact
anti-move). If they do not: next hypothesis is that the stall sits inside
`_handle_message` outside the instrumented stages - `handler_total` max 56,382 ms vs
largest stage 5,411 ms leaves ~51 s unaccounted; add stage timing before guessing.

Do not open a branch, write a plan, or implement until this correlation is answered.
