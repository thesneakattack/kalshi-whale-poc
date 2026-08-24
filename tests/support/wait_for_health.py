"""
Bounded-retry HTTP poll used by both `.woodpecker/quality-browser-e2e.yml`
and the GitHub Actions `browser-e2e` fallback job to wait for
tests/support/e2e_server.py's uvicorn process to come up before pointing
Selenium at it. A blind `sleep N` either wastes time on every green run or
races a slow container start on a loaded CI agent - this exits the moment
the server actually answers, and fails fast with the last real error if it
never does.
"""
from __future__ import annotations

import sys
import time
import urllib.request


def wait_for_health(url: str, attempts: int = 30, delay_sec: float = 1.0) -> None:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except OSError as exc:
            last_error = exc
        time.sleep(delay_sec)
    raise SystemExit(
        f"server at {url} never became healthy after {attempts} attempts "
        f"({delay_sec}s apart): {last_error}"
    )


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/api/state"
    wait_for_health(target)
    print(f"{target} is healthy")
