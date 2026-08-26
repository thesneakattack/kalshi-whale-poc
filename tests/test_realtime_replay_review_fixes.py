"""I12 adversarial-review fixes to the two replay harnesses.

Each test pins a modelling defect the review found: the WS harness served
every class group on its own independent server (one asyncio loop cannot),
counted prefiltered messages in the 'sustained' denominator, and never
attached REST-enrichment latency to whale candidates; the REST harness broke
same-instant ties alphabetically, which put live-status ahead of the
position fetch although main.py launches live-status a phase later.
"""

from tools import realtime_pipeline_replay as p
from tools import rest_scheduler_replay as r


def _workload(**over) -> p.Workload:
    base = dict(
        seed=1, duration_sec=20.0,
        rates={"trade": 50.0, "ticker": 100.0, "fill": 0.5},
        service={"trade": p.Fixed(0.001), "ticker": p.Fixed(0.009), "fill": p.Fixed(0.001)},
        candidate_fraction=0.1, candidate_service=p.Fixed(0.002),
    )
    base.update(over)
    return p.Workload(**base)


def test_single_loop_serialises_class_groups():
    multi = p.simulate(_workload(), p.StagedTopology())
    single = p.simulate(_workload(), p.StagedTopology(), single_loop=True,
                        service_order=("critical", "ticker", "trade"))
    # Independent servers never make a candidate wait behind ticker work;
    # one loop with ticker priority must.
    assert multi["candidate_latency"]["p95_ms"] < 1.0
    assert single["candidate_latency"]["p95_ms"] > multi["candidate_latency"]["p95_ms"]
    assert sum(single["busy_fraction"].values()) <= 1.0 + 1e-9
    assert single["single_loop"] is True


def test_sustained_excludes_prefiltered_from_denominator():
    # 1000 prints/s of which 2% are candidates costing 200 ms each: the
    # candidate consumer can serve 5/s of the 20/s generated, so most
    # candidates strand - the old denominator (all received prints) hid that.
    wl = _workload(rates={"trade": 1000.0}, service={"trade": p.Fixed(0.0001)},
                   candidate_fraction=0.02, candidate_service=p.Fixed(0.2), duration_sec=10.0)
    res = p.simulate(wl, p.StagedTopology())
    assert res["prefiltered"] > 9000
    assert res["candidates_generated"] == res["candidates_served"] + res["candidates_stranded"]
    assert res["candidates_stranded"] > 0
    assert res["sustained"] is False


def test_enrichment_attaches_to_candidates_when_candidates_are_modelled():
    wl = _workload(rates={"trade": 10.0}, service={"trade": p.Fixed(0.001)},
                   candidate_fraction=1.0, candidate_service=p.Fixed(0.001),
                   enrichment=p.Enrichment(probability=1.0, latency_sec=0.5), duration_sec=10.0)
    msgs = wl.messages()
    assert all(m.service >= 0.5 for m in msgs if m.candidate)


def test_enrichment_baseline_unchanged_without_candidates():
    # I6 baselines (candidate_fraction=0) must stay byte-identical.
    wl = _workload(rates={"trade": 10.0}, service={"trade": p.Fixed(0.001)},
                   candidate_fraction=0.0, candidate_service=None,
                   enrichment=p.Enrichment(probability=1.0, latency_sec=0.5), duration_sec=10.0)
    assert all(m.service >= 0.5 for m in wl.messages() if m.kind == "trade")


def test_rest_same_instant_launch_order_follows_the_tick():
    # main.py spawns the catalog task before the critical gather and fetches
    # live-status only after the market fetch: at the same instant the order
    # is catalog, position, whale, live-status - not alphabetical.
    demand = r.Demand(seed=1, duration_sec=1.0, classes={
        "background_live_status": r.ClassDemand(burst_size=1, burst_every=10.0),
        "critical_position": r.ClassDemand(burst_size=1, burst_every=10.0),
        "background_catalog": r.ClassDemand(burst_size=1, burst_every=10.0),
    })
    order = [req.cls for req in demand.requests() if req.arrival == 0.0]
    assert order == ["background_catalog", "critical_position", "background_live_status"]


def test_rest_presets_phase_live_status_after_the_market_fetch():
    for name, preset in r.PRESETS.items():
        spec = preset["classes"].get("background_live_status")
        if spec is not None and spec.burst_size:
            assert spec.burst_phase > 0.0, name
