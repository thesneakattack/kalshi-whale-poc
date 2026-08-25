---
name: kalshi-contract-review
description: Use before changing or auditing any Kalshi REST/WebSocket request, response, message type, field, order/fill/position behavior, market lifecycle, rate-limit assumption, settlement semantics, or Kalshi contract fixture. Read docs/kalshi first and verify current documented behavior instead of relying on memory.
---

# Kalshi Contract Review

1. Identify the exact endpoint/channel/message/semantic behavior involved.
2. Read the matching current page(s) under `docs/kalshi/` and check
   `docs/kalshi/CHEATSHEET.md` for already-resolved answers. **Name the exact
   file(s)** you actually read (e.g. `docs/kalshi/get-markets.md`,
   `docs/kalshi/pagination.md`) in your commit message or report — "checked
   docs/kalshi/" is not exact doc identification, a specific filename is.
   If the operation lives (or will live) under `services/kalshi/`, that
   module's `CONTRACT_DOCS` mapping *is* the durable record of which exact
   files back it — keep it accurate rather than treating step 2 as a
   one-time lookup. `tools/quality_audit/kalshi_contract_docs.py` (run via
   `python -m tools.quality_audit`, wired into the architecture-audit CI
   job) mechanically enforces that every public operation there has a
   `CONTRACT_DOCS` entry and that every entry points at a file that exists —
   it cannot check that the mapping is the *right* doc, only that one exists
   and resolves, so the judgment call in this step still matters.
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
   **Fixture comparison is explicit, not implicit**: for every field a
   fixture asserts on, confirm that field's name/type/semantics against the
   doc body itself — not against what the current normalizer already
   happens to read. A fixture that merely mirrors existing code can pass
   while the code and the docs have quietly diverged.
7. Exercise production handler/normalizer entry points in tests; don't merely
   assert fixture keys.
8. Never use real-trading enablement or real order placement for verification.
9. When a contract mismatch is found, decide whether it belongs in fixture
   tests, docs-drift CI, a read-only canary, or more than one. **Record the
   discrepancy, don't just fix it silently**: add a dated entry to
   `docs/kalshi/CHEATSHEET.md` describing what the docs say vs. what the app
   assumed/did, following that file's existing append-only entry style — the
   same discipline `CLAUDE.md`'s "Kalshi API documentation" section already
   requires for any doc lookup that resolves a real data question, extended
   here to explicitly cover discrepancies found during contract review.
