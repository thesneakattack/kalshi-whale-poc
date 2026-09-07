"""I8 throwaway/experimental no-write quality coordination simulator.

Prototype status: EXPERIMENTAL. Not wired into production CI or any GitHub API -- see
docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-25-autonomous-quality-coordination-investigation.md I8 and
docs/archive/lane-9-tooling-ci-process-governance/research/2026-08-25-quality-coordinator-simulation.md. Exists to prove, with a
scenario matrix, that the I1 (identity), I2 (persistence), and I3 (suppression) policies compose
into an explainable, non-duplicating, no-write coordination state machine before I10 selects a
production architecture. This package makes no network calls and holds no credentials.
"""
