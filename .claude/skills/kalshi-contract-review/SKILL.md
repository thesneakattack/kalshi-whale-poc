---
name: kalshi-contract-review
description: Use before changing or auditing any Kalshi REST/WebSocket request, response, message type, field, order/fill/position behavior, market lifecycle, rate-limit assumption, settlement semantics, or Kalshi contract fixture. Read docs/kalshi first and verify current documented behavior instead of relying on memory.
---

# Kalshi Contract Review

1. Identify the exact endpoint/channel/message/semantic behavior involved.
2. Read the matching current page(s) under `docs/kalshi/` and check
   `docs/kalshi/CHEATSHEET.md` for already-resolved answers.
3. If the task is specifically about doc drift or local docs may be stale,
   verify the current official Kalshi docs before coding.
4. Compare documentation with:
   - current client call,
   - parser/normalizer,
   - persistence,
   - strategy/consumer,
   - UI if relevant,
   - tests/fixtures.
5. Historical high-risk classes to explicitly rule out:
   - deprecated/renamed side fields,
   - singular/plural WS message types,
   - fill identifier assumptions,
   - `determined` vs `finalized`,
   - old create/cancel order schema,
   - close/expiration-time semantics.
6. Build fixtures from documented shapes, not current implementation quirks.
7. Exercise production handler/normalizer entry points in tests; don't merely
   assert fixture keys.
8. Never use real-trading enablement or real order placement for verification.
9. When a contract mismatch is found, decide whether it belongs in fixture
   tests, docs-drift CI, a read-only canary, or more than one.
