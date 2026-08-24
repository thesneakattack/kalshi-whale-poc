---
name: observability-performance
description: Use for latency, CPU, polling cadence, REST/API volume, WebSocket throughput, SQLite query/connect cost, caching, metrics, resource growth, or optimization work. Inventory and measure existing instrumentation first, then make evidence-driven changes without slowing the trading hot path.
---

# Observability and Performance

1. Inventory existing evidence before instrumenting:
   - `last_tick_duration_sec`
   - `tick_phase_timings`
   - `last_tick_rate_limit_hits`
   - `trade_stream_perf`
   - stream receive/drop counters
   - task supervisor/fault/alert state
   - pipeline write ages
   - backup state
   - HTTP/rate-limit counters
2. Record a baseline measurement.
3. Identify the actual bottleneck/missing evidence.
4. Prefer stable operation counts over noisy wall-clock microbenchmarks:
   query count, connection count, API attempt count, duplicate computation.
5. New runtime telemetry must:
   - aggregate/sample rather than persist every event,
   - use bounded label cardinality,
   - avoid stack inspection in hot paths,
   - avoid blocking WS/entry/exit/execution paths.
6. Background non-trivial collection/aggregation using established
   `task_supervisor` patterns.
7. Measure after the change and compare to baseline.
8. If monitoring itself causes measurable latency/storage growth, treat that
   as a bug.
9. Convert repeatedly useful temporary instrumentation into durable
   observability or a synthetic CI regression check.
