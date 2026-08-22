"""
Diagnostics, archive, and index-feed routes - the surface that answers "is
this system doing what its settings say, and is the data behind that
answer real." Every check is read-only with respect to trading and
degrades to an explicit "unknown" rather than a fabricated number.

Moved into this package 2026-08-22 (modularization Phase 8/9) from
services/diagnostics.py + routers/diagnostics_routes.py (the latter a
one-off top-level directory, the Phase-0 template for the rest of the
prior 7-phase main.py modularization - now normalized to the same
services/<name>/{routes.py, ..., CHEATSHEET.md} shape every later concern
already uses). series_watcher.py and settlement_edge.py stay flat
deliberately - both have real importers outside diagnostics (the live
trade/index stream capture path), not diagnostics-exclusive, see
CHEATSHEET.md. See diagnostics.py, routes.py, and CHEATSHEET.md.
"""
