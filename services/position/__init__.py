"""Position module - see routes.py and account_positions.py (moved in here
2026-08-27, backend services modularization, Task 3 of
docs/archive/lane-4-analytics-advisory-research/plans/
2026-08-27-backend-services-modularization.md (moved there 2026-09-06,
planning-lanes migration) - previously a flat
top-level services/*.py file that was this package's own logic all along).
The underlying broker (services/paper_broker.py) is already a fully clean,
self-contained module and stays where it is for now - see the
modularization plan's "Folder-per-concern restructuring" section for why
it isn't retroactively moved into this package yet."""
