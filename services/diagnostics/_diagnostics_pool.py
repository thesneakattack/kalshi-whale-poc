"""Dedicated worker pool for services/diagnostics/diagnostics.py's
run_offline() - see docs/superpowers/specs/2026-09-01-whale-scoring-
connection-reuse-design.md section 1b/4a for why this needs its own
pool, not services.tick_executor's shared one: run_offline()'s
per-series raw_trades aggregate queries (services/series_watcher.py's
funnel()) plus check_confidence_input_coverage()'s unscoped signal_log
fetch (docs/open-decisions.md, 2026-09-01) grew expensive enough to
permanently occupy both of tick_executor's 2 workers - starving the
trading-critical writes that pool exists to protect (confirmed live,
2026-09-01: both workers in futex_do_wait for 5h10m+, capture_writer
lock faults recurring, reproducing again within ~11 minutes of a fresh
process restart).

2 workers: sized for realistic known concurrent callers of
GET /api/quality/summary - the dashboard's own 5s poll, plus this
repo's own .claude/hooks/guard_workflow.py and CLAUDE.md routing
sessions to this exact endpoint as their first investigation step,
plus tools/quality_coordination.py. Not tick_executor's 2 (shared with
trading-critical work, the problem being fixed) or the whale-scoring
pool's 4 (a different, higher-frequency workload). If 2 isn't enough,
that shows up as measurable backlog on this pool specifically, not a
guess to get exactly right on the first try."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="diagnostics")


async def run(fn: Callable[[], T]) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn)
