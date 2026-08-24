---
name: root-cause-debugging
description: Use whenever there is an unexpected bug, failing test, live incident, contradictory metric, strange runtime behavior, resource leak, data gap, or performance anomaly. Use before proposing or applying a fix; prove root cause with the least-invasive measurement and turn recurring failure classes into permanent runtime or CI guards.
---

# Root-Cause Debugging

Do not patch symptoms first.

1. State the observed failure precisely.
2. Produce the smallest reliable reproduction.
3. List plausible competing hypotheses.
4. Choose the least-invasive measurement that distinguishes them. Prefer
   existing diagnostics, health endpoints, faults, observability, logs,
   stored history, and existing CI output over a new scratch script.
5. Measure. If evidence contradicts the initial hypothesis, follow evidence.
6. State the confirmed root cause and causal chain.
7. Determine why existing tests/diagnostics/CI failed to detect it.
8. Write the regression test before changing behavior where practical.
9. Implement the smallest correct fix; keep unrelated cleanup separate.
10. Re-run reproduction, targeted tests, and relevant broader checks.
11. Apply the investigation-to-guard rule.
12. If the resulting guard is deterministic, safe in a clean checkout, and
    does not require live application state, load `ci-cd-guardrails` and make
    CI/CD its default permanent owner in the same logical work.
13. If the condition only exists in a running system, integrate the
    measurement into runtime diagnostics/observability instead.
14. If a temporary probe was uniquely useful, decide whether to promote its
    measurement into an existing runtime service or CI checker rather than
    discarding it.
15. Commit the focused bugfix/guard work with evidence and ownership recorded.
