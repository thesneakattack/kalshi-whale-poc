"""Live (not replay-based) watchlist-scale stress test - applies a sequence of
temporary config patches against the running app's own POST /api/config, measures
GET /api/health/pipeline + GET /api/observability/history after each, and always
restores only the config sections it touched. Standalone tool (CLAUDE.md's
"Workflow/tooling and application code must never overlap" rule) - never imported by
main.py/services/, reads/writes the live app only through its own public HTTP API,
the same one-way coupling tools/quality_coordination.py's fetch_app_report already
establishes.

Realtime data-plane remediation plan, Phase P3.5 (Task 17a/17b) - feeds P4/P5's
design with real-scale measurements before either is implemented."""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Callable

HttpGetter = Callable[[str, float], dict]
HttpPoster = Callable[[str, dict, float], dict]

# loop_watchdog is this phase's own P4/P5 concern; the three subscription_churn
# counters are CH1's own metrics (Phase P2.5 above) - captured here
# too so a widened-scope step doubles as a larger-scale churn data point for CH3,
# not just a P4/P5 input (see this phase's header note).
_OBSERVABILITY_METRICS = (
    "loop_watchdog.stall_max_ms",
    "trade_stream.ingest.subscription_churn.syncs_window",
    "trade_stream.ingest.subscription_churn.tickers_added_window",
    "trade_stream.ingest.subscription_churn.tickers_removed_window",
)


def _default_get(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-whale-poc-stress-test"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _default_post(url: str, body: dict, timeout: float) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "kalshi-whale-poc-stress-test"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def run_stress_steps(
    base_url: str, steps: list[dict], *,
    getter: HttpGetter | None = None, poster: HttpPoster | None = None,
    sleep_fn: Callable[[float], None] | None = None, measure_after_sec: float = 120.0,
) -> dict:
    """Applies each step's config patch in turn (POST /api/config), sleeps
    measure_after_sec, then snapshots GET /api/health/pipeline and
    GET /api/observability/history for each of _OBSERVABILITY_METRICS. Reverts, in a
    finally block regardless of any step raising, only the top-level config sections
    the steps actually touched - never the entire captured config, which would
    include kalshi_account and trip POST /api/config's own trading_enabled guard
    (services/config/routes.py), and would needlessly touch advisory/
    confidence_calibration auto-apply flags this tool has no business changing."""
    get = getter or _default_get
    post = poster or _default_post
    sleep = sleep_fn or time.sleep

    original_config = get(f"{base_url}/api/config", 5.0)
    touched_sections = {key for step in steps for key in step["patch"]}
    revert_patch = {s: original_config[s] for s in touched_sections if s in original_config}

    results = []
    try:
        for step in steps:
            post(f"{base_url}/api/config", {"patch": step["patch"]}, 5.0)
            sleep(measure_after_sec)
            pipeline = get(f"{base_url}/api/health/pipeline", 5.0)
            observability = {
                metric: get(f"{base_url}/api/observability/history?metric={metric}&hours=1", 5.0)
                for metric in _OBSERVABILITY_METRICS
            }
            results.append({
                "label": step["label"], "patch": step["patch"],
                "pipeline": pipeline, "observability": observability,
            })
    finally:
        if revert_patch:
            post(f"{base_url}/api/config", {"patch": revert_patch}, 5.0)

    return {"original_config": original_config, "results": results}


def probe_market_search(
    base_url: str, *, q: str = "", min_volume: float = 0, category: str = "",
    limit: int = 200, live_only: bool = True,
    getter: HttpGetter | None = None, sleep_fn: Callable[[float], None] | None = None,
) -> dict:
    """One direct, wide GET /api/markets/search call - a distinct, user-facing
    on-demand search path (services/market_catalog/routes.py:177), not the automatic
    watchlist pipeline `run_stress_steps` exercises, so it needs its own explicit
    call rather than a config patch. Snapshots GET /api/health/pipeline immediately
    before and 5s after, to see whether a broad, high-limit, live_only search
    materially moves REST demand or queue health. Elapsed time via
    time.monotonic() - unaffected by wall-clock adjustments, unlike time.time()."""
    get = getter or _default_get
    sleep = sleep_fn or time.sleep

    pipeline_before = get(f"{base_url}/api/health/pipeline", 5.0)
    t0 = time.monotonic()
    url = (
        f"{base_url}/api/markets/search?q={q}&min_volume={min_volume}"
        f"&category={category}&limit={limit}&live_only={str(live_only).lower()}"
    )
    result = get(url, 30.0)  # a wide live_only search fans out to real REST calls - longer timeout than the config/pipeline reads above
    elapsed_sec = time.monotonic() - t0
    sleep(5.0)  # let any REST-demand blip show up in the next pipeline snapshot
    pipeline_after = get(f"{base_url}/api/health/pipeline", 5.0)

    return {
        "elapsed_sec": round(elapsed_sec, 3),
        "result_count": len(result.get("markets") or []),
        "pipeline_before": pipeline_before,
        "pipeline_after": pipeline_after,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://fastapi:8000")
    parser.add_argument("--measure-after-sec", type=float, default=120.0)
    args = parser.parse_args()

    # Task 17b's live steps - see that task for the reasoning behind each value.
    live_steps = [
        {"label": "widen_scope", "patch": {"kalshi": {"min_volume_24h": 1000, "categories": ["Sports", "Crypto"]}}},
        {"label": "widen_scope_non_live_only", "patch": {"kalshi": {"live_markets_only": False}}},
        {"label": "widen_scope_strategy_live_only", "patch": {"kalshi": {"live_markets_only": True}, "strategy": {"live_markets_only": True}}},
        {"label": "widen_scope_cap_removed", "patch": {"kalshi": {"max_children_per_parent": None}}},
        {"label": "widen_scope_whale_signal", "patch": {"whale_watcher_kalshi": {"min_contracts": 1000, "min_contracts_by_series": {"KXBTC15M": 500}}}},
    ]
    output = run_stress_steps(args.base_url, live_steps, measure_after_sec=args.measure_after_sec)
    output["search_probe"] = probe_market_search(args.base_url)
    print(json.dumps(output, indent=2))
