"""services/analytics/routes.py - GET /api/candidate-log/summary offload.

Root-caused 2026-08-26 via a live py-spy stack trace during a real
17-38s event-loop stall (ROADMAP.md's "Path to production" entry):
population_gate_summary() runs an unbounded scan of rejection_events
(6.2M rows and growing, no retention) and was called directly and
synchronously from this route - every dashboard poll blocked the event
loop for the full duration of that scan. This only covers the wiring
(does the route hand the call to tick_executor instead of running it
inline) - population_gate_summary()'s own behavior/output is already
covered by tests/test_candidate_log.py and its scaling behavior by
tests/test_performance_regressions.py.
"""
import asyncio

import pytest

from services import candidate_log
from services import tick_executor
from services.analytics import routes as analytics_routes


async def _stub_gate_summary_async():
    """Shared async stand-in for candidate_log.gate_summary_async(), used
    everywhere a test needs the "gates" half of the response populated
    cheaply without touching a real DB - same role the old sync
    `lambda: [{"stub": "gates"}]` played before issue #605 moved this
    route off the blocking sync gate_summary()."""
    return [{"stub": "gates"}]


async def _noop_async(min_samples):
    """Stub for candidate_log.population_gate_summary_async, for tests
    below that only care about the BANDED field's cache behavior and would
    otherwise reach the real DB_PATH (never monkeypatched in this file) via
    the unbanded call each get_candidate_log_summary() call also makes."""
    return [{"stub": "pop"}]


@pytest.fixture(autouse=True)
def _reset_population_gates_cache():
    """Task 6b of docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md
    added a module-level _population_gates_cache to get_candidate_log_
    summary. Without this reset, one test's cached (mocked) population_gates
    result could leak into the next test that calls this route within the
    30s TTL, since _population_gates_cache is a plain module attribute that
    persists across test functions in the same pytest process.

    Also resets services.candidate_log._population_gates_banded_cache
    (issue #616 D1) - the banded field's cache is a SEPARATE module
    attribute, deliberately not folded into _population_gates_cache above
    (see that constant's own comment in services/candidate_log.py), but it
    is exactly as persistent across test functions and needs the same
    reset for the same reason."""
    analytics_routes._population_gates_cache = {"cached_at": None, "value": None}
    candidate_log._population_gates_banded_cache = {"cached_at": None, "value": None}
    yield
    analytics_routes._population_gates_cache = {"cached_at": None, "value": None}
    candidate_log._population_gates_banded_cache = {"cached_at": None, "value": None}


def test_candidate_log_summary_never_routes_population_gates_through_tick_executor(monkeypatch):
    """Deliberately the INVERSE of the assertion this test made until issue
    #410 (it was named ..._runs_population_gate_summary_via_tick_executor).

    docs/superpowers/specs/2026-09-04-issue-410-pool-vs-aiosqlite-design.md
    Sec 5 asks for exactly this inversion as its permanent
    detection-for-recurrence: the route must reach candidate_log's native
    aiosqlite path, never tick_executor's 2-worker pool, which is shared
    with candidate_ledger.claim()/record_decision() on the live per-signal
    decision path. A future refactor that silently routes this 15-22s query
    back onto that pool fails here instead of quietly costing trade
    latency.

    Guards at the mechanism level rather than the function name (adversarial
    review of this PR, finding F5): patching `tick_executor.run` misses a
    call reached via `from services.tick_executor import run as _te_run`
    (the name is bound before the patch applies) and misses a call made
    from INSIDE a function this same test stubs out (population_gate_
    summary_async here is fully replaced, so a tick_executor call moved
    inside it would never execute). Both were reproduced live against the
    pre-fix version of this test. `tick_executor.run()`'s own body
    (services/tick_executor.py) is `loop.run_in_executor(_executor, fn)`,
    which CPython's asyncio implements as `_executor.submit(fn)` - patching
    `_executor.submit` directly is therefore the actual choke point: any
    call that reaches this pool, by any name, any import alias, from any
    call depth, submits work to this one ThreadPoolExecutor instance."""
    submitted = []
    async_calls = []
    banded_async_calls = []
    original_submit = tick_executor._executor.submit

    def _spy_submit(fn, *args, **kwargs):
        submitted.append(fn)
        return original_submit(fn, *args, **kwargs)

    async def _stub_async(min_samples):
        async_calls.append(min_samples)
        return [{"stub": "pop"}]

    async def _stub_banded_async(bands, min_samples):
        banded_async_calls.append(min_samples)
        return [{"stub": "pop_banded"}]

    monkeypatch.setattr(tick_executor._executor, "submit", _spy_submit)
    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary_async", _stub_gate_summary_async)
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_async", _stub_async)
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_banded_async", _stub_banded_async)

    result = asyncio.run(analytics_routes.get_candidate_log_summary())

    assert submitted == [], "population_gates must not submit any work to tick_executor's pool any more"
    assert async_calls == [30], "the async aiosqlite path should have been called once, with min_samples"
    assert banded_async_calls == [30], (
        "the banded async aiosqlite path should have been called once, with min_samples, "
        "same as the unbanded one - never via tick_executor either"
    )
    assert result == {
        "gates": [{"stub": "gates"}],
        "population_gates": [{"stub": "pop"}],
        "population_gates_banded": [{"stub": "pop_banded"}],
    }


def test_candidate_log_summary_population_gates_is_cached_within_ttl(monkeypatch):
    """Task 6b of docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md:
    population_gate_summary() cannot be scoped with since_ts either (same
    total-sample-gate reasoning as resolved_signals_with_factors(), verified
    in this task's own research), so the fix is a short-TTL cache on the
    ROUTE - confirmed live cost is real (~4.8s currently, this route's own
    existing comment documents the pre-2026-08-26-fix 17-38s figure)."""
    calls = []

    async def _spy_async(min_samples):
        calls.append(1)
        return [{"stub": "pop"}]

    async def _stub_banded_async(bands, min_samples):
        return [{"stub": "pop_banded"}]

    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary_async", _stub_gate_summary_async)
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_async", _spy_async)
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_banded_async", _stub_banded_async)

    asyncio.run(analytics_routes.get_candidate_log_summary())
    asyncio.run(analytics_routes.get_candidate_log_summary())

    assert len(calls) == 1, "second call within TTL should reuse the cached population_gates result"


def test_candidate_log_summary_population_gates_recomputes_after_ttl_expires(monkeypatch):
    """Sibling of the above - confirms the cache is time-bounded, not
    permanent, by advancing a monkeypatched time.time() past
    _POPULATION_GATES_CACHE_TTL_SEC between the two calls."""
    calls = []

    async def _spy_async(min_samples):
        calls.append(1)
        return [{"stub": "pop"}]

    async def _stub_banded_async(bands, min_samples):
        return [{"stub": "pop_banded"}]

    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary_async", _stub_gate_summary_async)
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_async", _spy_async)
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_banded_async", _stub_banded_async)

    fake_now = [1_000_000.0]
    monkeypatch.setattr(analytics_routes.time, "time", lambda: fake_now[0])
    monkeypatch.setattr(analytics_routes.candidate_log.time, "time", lambda: fake_now[0])

    asyncio.run(analytics_routes.get_candidate_log_summary())
    fake_now[0] += analytics_routes._POPULATION_GATES_CACHE_TTL_SEC + 1
    asyncio.run(analytics_routes.get_candidate_log_summary())

    assert len(calls) == 2, "a call after the TTL has elapsed should recompute, not reuse the stale cache"


def test_candidate_log_summary_cache_is_stamped_at_completion_not_request_receipt(monkeypatch):
    """Issue #410 cache-alignment bug (docs/superpowers/research/2026-09-04-
    issue-410-tick-executor-measurement.md Sec 3.4): `cached_at` was stamped
    with a `now` captured BEFORE awaiting the query, so a 15-22s query
    burned 53-73% of its own 30s TTL before the entry was even written.

    The frontend polls on a 30000ms throttle measured from when it last
    FIRED (frontend/src/js/main.js:79-98), so the next poll lands at
    T_fire + [30, 36)s - at or past a TTL window measured from the previous
    FIRE instant, meaning essentially every scheduled poll missed. Each miss
    re-occupies one of tick_executor's 2 workers, the same pool
    candidate_ledger.claim()/record_decision() uses on the live per-signal
    decision path.

    This reproduces the real steady-state pattern rather than the
    implementation detail: a 20s query, then a second poll 33s after the
    FIRST FIRED (mid-window). Stamping at completion makes that a hit."""
    calls = []
    fake_now = [1_000_000.0]
    query_duration_sec = 20.0

    async def _slow_async(min_samples):
        calls.append(1)
        fake_now[0] += query_duration_sec  # the query itself takes 20s
        return [{"stub": "pop"}]

    async def _stub_banded_async(bands, min_samples):
        return [{"stub": "pop_banded"}]

    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary_async", _stub_gate_summary_async)
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_async", _slow_async)
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_banded_async", _stub_banded_async)
    monkeypatch.setattr(analytics_routes.time, "time", lambda: fake_now[0])
    monkeypatch.setattr(analytics_routes.candidate_log.time, "time", lambda: fake_now[0])

    # Poll 1 fires at t=0, completes at t=20.
    asyncio.run(analytics_routes.get_candidate_log_summary())
    assert calls == [1]
    assert analytics_routes._population_gates_cache["cached_at"] == 1_000_000.0 + query_duration_sec, (
        "cached_at must be the completion instant, not the request-receipt instant"
    )

    # Poll 2 fires 33s after poll 1 FIRED - squarely inside the real
    # frontend's [30, 36)s window. 33 - 20 = 13s of cache age < 30s TTL.
    fake_now[0] = 1_000_000.0 + 33.0
    asyncio.run(analytics_routes.get_candidate_log_summary())

    assert len(calls) == 1, (
        "a poll landing in the frontend's real [30, 36)s window must hit the cache; "
        "stamping cached_at at request-receipt made every scheduled poll a miss"
    )


def test_candidate_log_summary_never_calls_the_blocking_sync_gate_summary(monkeypatch):
    """Issue #605: gate_summary() (services/candidate_log.py) used to be
    called directly and synchronously from this route - a bare unindexed
    SELECT over rejected_candidates (258,526 rows measured live 2026-09-06,
    ~4.2x growth in 3 days since this route's own comment last measured it
    at 62K and left it inline deliberately) followed by a Python GROUP BY
    loop, with no offload of any kind. A live stack capture caught the
    event loop genuinely blocked inside that loop during a real,
    naturally-recurring stall.

    Fixed the same way issue #410 fixed population_gate_summary() above:
    gate_summary_async() is the aiosqlite-native sibling this route must
    call instead. Guards at the call itself (monkeypatching the sync
    gate_summary to raise) rather than only asserting the new function's
    presence - a future refactor that silently reintroduces the direct
    sync call fails here instead of quietly reblocking the event loop.

    Also stubs population_gate_summary_banded_async (issue #616 D1, merged
    into main after this test was first written) since the route now
    always calls it too - not this test's own concern, but the route call
    still has to succeed for the assertion on gate_summary_async to be
    reached at all."""
    def _blocking_sync_gate_summary():
        raise AssertionError(
            "route must not call the blocking sync gate_summary() - use gate_summary_async()"
        )

    async_calls = []

    async def _spy_gate_async():
        async_calls.append(1)
        return [{"stub": "gates"}]

    async def _stub_population_async(min_samples):
        return [{"stub": "pop"}]

    async def _stub_population_banded_async(bands, min_samples):
        return [{"stub": "pop_banded"}]

    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary", _blocking_sync_gate_summary)
    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary_async", _spy_gate_async)
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_async", _stub_population_async)
    monkeypatch.setattr(
        analytics_routes.candidate_log, "population_gate_summary_banded_async", _stub_population_banded_async
    )

    result = asyncio.run(analytics_routes.get_candidate_log_summary())

    assert async_calls == [1], "gate_summary_async() should have been awaited exactly once"
    assert result == {
        "gates": [{"stub": "gates"}],
        "population_gates": [{"stub": "pop"}],
        "population_gates_banded": [{"stub": "pop_banded"}],
    }


# --- population_gates_banded's own, DECOUPLED cache (issue #616 D1) -------
#
# services.candidate_log.population_gate_summary_banded_cached_async() owns
# this cache (see its own module-level comment in services/candidate_log.py
# for the full measured-cost reasoning and why it lives there rather than
# alongside _population_gates_cache above). These tests exercise it through
# the route, same as the population_gates tests above, plus one test that
# proves the two caches' TTLs are genuinely independent of each other -
# the actual defect this task fixes (the prior WIP commit wired the banded
# field with no cache of its own at all, meaning every poll paid its full
# ~2x-unbanded cost).


def test_candidate_log_summary_population_gates_banded_is_cached_within_its_own_ttl(monkeypatch):
    calls = []

    async def _spy_banded_async(bands, min_samples):
        calls.append(1)
        return [{"stub": "pop_banded"}]

    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary", lambda: [{"stub": "gates"}])
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_async", _noop_async)
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_banded_async", _spy_banded_async)

    asyncio.run(analytics_routes.get_candidate_log_summary())
    asyncio.run(analytics_routes.get_candidate_log_summary())

    assert len(calls) == 1, "second call within TTL should reuse the cached population_gates_banded result"


def test_candidate_log_summary_population_gates_banded_recomputes_after_its_own_ttl_expires(monkeypatch):
    calls = []

    async def _spy_banded_async(bands, min_samples):
        calls.append(1)
        return [{"stub": "pop_banded"}]

    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary", lambda: [{"stub": "gates"}])
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_async", _noop_async)
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_banded_async", _spy_banded_async)

    fake_now = [1_000_000.0]
    monkeypatch.setattr(analytics_routes.candidate_log.time, "time", lambda: fake_now[0])

    asyncio.run(analytics_routes.get_candidate_log_summary())
    fake_now[0] += candidate_log._POPULATION_GATES_BANDED_CACHE_TTL_SEC + 1
    asyncio.run(analytics_routes.get_candidate_log_summary())

    assert len(calls) == 2, "a call after the banded cache's own TTL has elapsed should recompute"


def test_candidate_log_summary_population_gates_banded_cache_is_stamped_at_completion_not_request_receipt(monkeypatch):
    """Same issue #410 lesson as the unbanded field's own version of this
    test above, reapplied here rather than re-learned: a ~70s worst-case
    banded query stamped with the request-receipt instant would burn a real
    fraction of its own 300s TTL before the cache entry was even written."""
    calls = []
    fake_now = [1_000_000.0]
    query_duration_sec = 70.0

    async def _slow_banded_async(bands, min_samples):
        calls.append(1)
        fake_now[0] += query_duration_sec
        return [{"stub": "pop_banded"}]

    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary", lambda: [{"stub": "gates"}])
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_async", _noop_async)
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_banded_async", _slow_banded_async)
    monkeypatch.setattr(analytics_routes.candidate_log.time, "time", lambda: fake_now[0])

    asyncio.run(analytics_routes.get_candidate_log_summary())
    assert calls == [1]
    assert candidate_log._population_gates_banded_cache["cached_at"] == 1_000_000.0 + query_duration_sec, (
        "cached_at must be the completion instant, not the request-receipt instant"
    )

    # A poll landing 290s after the first FIRED - inside the 300s TTL
    # measured from completion (70s + 220s = 290s of cache age < 300s).
    fake_now[0] = 1_000_000.0 + 290.0
    asyncio.run(analytics_routes.get_candidate_log_summary())

    assert len(calls) == 1, "a poll inside the completion-stamped TTL window must hit the cache"


def test_candidate_log_summary_population_gates_and_banded_caches_have_independent_ttls(monkeypatch):
    """The actual decoupling proof this task exists to establish: population_
    gates' 30s cache and population_gates_banded's 300s cache expire
    independently of each other, never coupled through a shared dict or a
    shared TTL. Directly seeds each cache's own `cached_at` (rather than
    driving both through many real-time-feeling polls) so each direction is
    isolated and the arithmetic is checkable by inspection:

    Direction A (banded HIT while unbanded MISSES): unbanded's own
    cached_at is old enough to have crossed ITS 30s TTL; banded's own
    cached_at is the SAME age but that age is still under ITS 300s TTL.
    Direction B (the reverse: unbanded HIT while banded MISSES): unbanded's
    own cached_at is recent (under 30s old); banded's own cached_at is
    independently old enough to have crossed ITS 300s TTL. This is only
    possible at all if the two caches are genuinely separate objects with
    separate TTL constants - a shared cache/TTL could never produce a hit
    on one field and a miss on the other from the same poll."""
    assert analytics_routes._POPULATION_GATES_CACHE_TTL_SEC == 30
    assert candidate_log._POPULATION_GATES_BANDED_CACHE_TTL_SEC == 300

    unbanded_calls = []
    banded_calls = []

    async def _spy_async(min_samples):
        unbanded_calls.append(1)
        return [{"stub": "pop_fresh"}]

    async def _spy_banded_async(bands, min_samples):
        banded_calls.append(1)
        return [{"stub": "pop_banded_fresh"}]

    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary", lambda: [{"stub": "gates"}])
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_async", _spy_async)
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_banded_async", _spy_banded_async)

    # Direction A: same cache age (35s) for both - past unbanded's 30s TTL,
    # short of banded's 300s TTL.
    fake_now = [1000.0]
    monkeypatch.setattr(analytics_routes.time, "time", lambda: fake_now[0])
    monkeypatch.setattr(analytics_routes.candidate_log.time, "time", lambda: fake_now[0])
    analytics_routes._population_gates_cache = {"cached_at": 965.0, "value": [{"stub": "pop_stale"}]}
    candidate_log._population_gates_banded_cache = {"cached_at": 965.0, "value": [{"stub": "pop_banded_cached"}]}

    result = asyncio.run(analytics_routes.get_candidate_log_summary())

    assert unbanded_calls == [1], "unbanded (35s old, > 30s TTL) must recompute"
    assert banded_calls == [], "banded (35s old, < 300s TTL) must stay cache-hit"
    assert result["population_gates"] == [{"stub": "pop_fresh"}]
    assert result["population_gates_banded"] == [{"stub": "pop_banded_cached"}], (
        "banded must still return the value seeded in ITS OWN cache, untouched by "
        "the unbanded field's cache miss in the same request"
    )

    # Direction B: unbanded's cache is recent (20s old, < 30s TTL); banded's
    # is independently old (400s, > 300s TTL) - only possible with two
    # genuinely separate cache dicts/TTLs.
    fake_now[0] = 2000.0
    analytics_routes._population_gates_cache = {"cached_at": 1980.0, "value": [{"stub": "pop_still_cached"}]}
    candidate_log._population_gates_banded_cache = {"cached_at": 1600.0, "value": [{"stub": "pop_banded_stale"}]}

    result = asyncio.run(analytics_routes.get_candidate_log_summary())

    assert unbanded_calls == [1], "unbanded (20s old, < 30s TTL) must stay cache-hit - no second call"
    assert banded_calls == [1], "banded (400s old, > 300s TTL) must recompute"
    assert result["population_gates"] == [{"stub": "pop_still_cached"}]
    assert result["population_gates_banded"] == [{"stub": "pop_banded_fresh"}]
