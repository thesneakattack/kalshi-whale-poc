"""Quality Control Plane static audit framework (Tasks 3-5 of
docs/archive/lane-6-observability-quality-safety/plans/2026-08-24-quality-control-plane.md, moved there 2026-09-06, planning-lanes migration). Run via
`python -m tools.quality_audit`.

`run_audit()` and the `_SCANNERS` registry live in `__main__.py`, not here -
that matches the plan's Task 4/5 file lists, which only ever modify
__main__.py to wire in a newly added scanner module. This module reuses
services/quality/models.py's QualityFinding/QualityReport (Task 1) rather
than duplicating that contract under tools/quality_audit/models.py, which
the plan's original file list called for before Task 1 existed.
"""
