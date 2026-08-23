"""Wrap a background asyncio task so a raised exception is recorded instead
of silently ending the task.

Before this, every `asyncio.create_task(...)` call site in `main.py` /
`services/market_watch/` (catalog_scan.py/discovery_cache.py) stored the `Task` object (mostly
just to `.cancel()` it on shutdown) but never awaited or inspected it. An
unhandled exception inside one of those coroutines just ended the task with
nothing logged and nothing restarted - confirmed as the shape behind three
separate diagnosed incidents (see git log: `6973974`, `a31ae51`, `12323cc`).

This does not add a task registry, a supervisor tree, or a retry/backoff
library - `supervise()` is a single wrapping function around
`asyncio.create_task`, and its return value is still a plain `asyncio.Task`,
`.cancel()`-able exactly like today.
"""
import asyncio
import logging
from typing import Awaitable, Callable

from services import fault_log

logger = logging.getLogger(__name__)


def supervise(
    coro_fn: Callable[[], Awaitable[None]],
    *,
    component: str,
    operation: str,
    restart: bool = False,
    restart_delay_sec: float = 5.0,
) -> asyncio.Task:
    """coro_fn must be a zero-arg callable returning a fresh coroutine each
    call (a coroutine object can only be awaited once, which matters for
    the restart=True retry loop - use a lambda to bind arguments).

    restart=True is for the long-running loops (trading_loop, the websocket
    stream runners) that should never just stay dead. restart=False
    (default) is for one-shot background tasks - log and record the fault,
    then let the task end, matching today's behavior otherwise.
    """

    async def _run() -> None:
        while True:
            try:
                await coro_fn()
                if not restart:
                    return
                logger.warning("%s.%s exited without raising; restarting", component, operation)
            except asyncio.CancelledError:
                raise  # never swallow cancellation during shutdown
            except Exception as exc:
                logger.exception("%s.%s crashed", component, operation)
                fault_log.record(component, operation, exc)
                if restart:
                    # Local import - services/alerting/alerting.py imports
                    # this module (for its own fire-and-forget notification
                    # dispatch), so a top-level import here would be
                    # circular. Only restart=True crashes alert - those are
                    # the loops (trading_loop/trade_stream/index_stream)
                    # that must never just stay dead; a restart=False
                    # one-shot task already degrades gracefully and retries
                    # next cycle on its own, not alarm-worthy at this level.
                    from services.alerting import alerting
                    alerting.record_alert(
                        "crash", "critical",
                        f"{component}.{operation} crashed: {type(exc).__name__}: {exc}",
                    )
                if not restart:
                    return
            await asyncio.sleep(restart_delay_sec)

    return asyncio.create_task(_run())
