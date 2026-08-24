---
name: root-cause-debugging
description: Use whenever there is an unexpected bug, failing test, live incident, contradictory metric, strange runtime behavior, resource leak, data gap, or performance anomaly. Use before proposing or applying a fix; prove root cause with the least-invasive measurement and turn recurring failure classes into permanent guards.
---

# Root-Cause Debugging

Do not patch symptoms first.

1. State the observed failure precisely.
2. Produce the smallest reliable reproduction.
3. List the plausible competing hypotheses; avoid inventing a single cause
   before evidence exists.
4. Choose the least-invasive measurement that distinguishes the hypotheses.
   Prefer existing diagnostics, `/api/quality/summary`,
   `/api/health/pipeline`, faults, observability, logs, and stored history
   over a new scratch script.
5. Measure. If evidence contradicts the initial hypothesis, follow evidence.
6. State the confirmed root cause and the causal chain.
7. Determine why existing tests/diagnostics/CI failed to detect it.
8. Write the regression test before changing behavior where practical.
9. Implement the smallest correct fix; keep unrelated cleanup separate.
10. Re-run the reproduction, targeted tests, and relevant broader checks.
11. Apply the investigation-to-guard rule:
    runtime diagnostic / CI guard / both / existing guard / one-off.
12. If a temporary probe was uniquely useful, decide whether to promote its
    measurement into an existing service or CI check rather than discarding it.
13. Commit the focused bugfix separately with evidence and guard disposition.
