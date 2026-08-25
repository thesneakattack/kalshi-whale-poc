# Realtime Data-Plane Evidence Rule

Applies to work on Kalshi latency, WebSocket throughput, queueing, rate limits, missed
signals, trade capture, market enrichment, and realtime scheduling.

## Do not tune by intuition

Do not change:

- queue capacity;
- consumer count;
- connection count;
- subscription scope;
- REST rate;
- batching size;
- cache TTL;
- retry count;
- polling frequency;
- thread/process count;

merely because a change "should help."

First identify the measured bottleneck and expected causal mechanism.

## Required evidence chain

For a claimed root cause, prefer:

1. deterministic reproduction;
2. correlated runtime telemetry;
3. source-code state-transition proof;
4. authoritative protocol/library documentation.

A statement such as "queue depth was high at the same time as latency" is correlation, not
yet causation.

## Candidate solution rule

For any major confirmed bottleneck, compare multiple plausible solution families.

Record:

- expected mechanism;
- assumptions;
- benchmark/load result;
- correctness implications;
- failure behavior;
- operational complexity;
- reasons accepted/rejected.

## Kalshi docs

Use `.claude/skills/kalshi-contract-review/SKILL.md` whenever a candidate solution depends
on Kalshi endpoint/channel/rate-limit semantics.

## External research

For Python/asyncio/WebSocket architecture, consult current authoritative upstream
documentation matching the installed versions rather than generic blog-memory advice.

## Hot-path rule

A diagnostic or abstraction placed on the exchange-wide hot path must be measured for
runtime cost.

## Completion rule

The investigation is not complete when one plausible fix works.

It is complete when:

- the bottleneck is causal and quantified;
- alternatives were explored;
- the selected solution wins on an explicit evidence matrix;
- rejected options have documented reasons;
- permanent detection exists for recurrence.
