"""One-line stdlib logging setup, called once at process start.

Before this, the app had zero `logging` usage anywhere - only `print()` and
`services/fault_log.py`'s durable fault table. `fault_log` stays as the
durable, queryable record; this adds the immediate, human-readable trace
that shows up in `ddev logs -s fastapi` while a session is live.
"""
import logging
import os


def configure() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        # `force` guards against something else (e.g. a library imported
        # before this call, or uvicorn's own logging setup) having already
        # called basicConfig first - without it, ours would silently no-op.
        force=True,
    )
