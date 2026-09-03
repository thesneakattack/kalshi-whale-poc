"""
One shared FastAPI dependency for clamping a route's `limit` query
parameter before it reaches any SQL LIMIT ?/OFFSET ? or REST fetch-fan-out
cap. Closes the gap the second architecture audit's own research (first
audit's §9.2 finding #5, re-verified fresh 2026-09-03) found: 16 routes in
this app accept `limit`, only 8 clamped it before this file existed.

This is the app's first use of FastAPI's Depends() mechanism (2026-09-03,
Task 6a of docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md) -
`grep -rln "Depends(" services/*.py services/**/*.py` returned zero hits
before this file. Standard FastAPI machinery, not a hand-rolled
abstraction the 2026-08-30 "prefer proven" rule would flag.
"""
def paginate(max_limit: int = 200):
    """Returns a dependency callable clamping `limit` to [1, max_limit].
    200 matches the ceiling this app's own already-clamped routes mostly
    already use (get_signal_history/get_trading_history/get_advisory_
    applied_changes all use exactly 200) - not an independently chosen
    number. Call as Depends(paginate()) for the default ceiling, or
    Depends(paginate(max_limit=N)) for a route that needs a different one.

    Deviation from this plan's own literal code sample (2026-09-03, Task 6a
    of docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md): the
    plan's sample wrapped the inner default in `Query(default=50)`, which
    breaks calling the returned dependency directly as a plain function
    (`dep()` resolves `limit` to the `Query` FieldInfo object itself, not
    50, raising `TypeError: '<' not supported between instances of 'int'
    and 'Query'` - reproduced live via this task's own
    test_paginate_default_is_the_dependencys_own_default) and contradicts
    the plan's own Interfaces section, which states the signature as
    plain `(limit: int = 50) -> int`. A bare scalar default (no Query()
    wrapper) is resolved identically by FastAPI's real request pipeline -
    every one of this app's existing 16 `limit`-accepting routes already
    relies on exactly this bare-default behavior with no Query() import
    anywhere in the codebase before this file - so this fix changes
    nothing about real HTTP behavior, only restores direct callability."""
    def _dependency(limit: int = 50) -> int:
        return max(1, min(limit, max_limit))
    return _dependency
