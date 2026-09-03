"""
services/pagination.py - the paginate() FastAPI dependency factory that
closes the "16 routes accept limit, only 8 clamp it" gap (Task 6a of
docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md).
"""
from services.pagination import paginate


def test_paginate_clamps_within_bounds():
    dep = paginate(max_limit=200)
    assert dep(limit=50) == 50


def test_paginate_clamps_above_max():
    dep = paginate(max_limit=200)
    assert dep(limit=999999) == 200


def test_paginate_clamps_below_one():
    dep = paginate(max_limit=200)
    assert dep(limit=-5) == 1
    assert dep(limit=0) == 1


def test_paginate_default_is_the_dependencys_own_default():
    dep = paginate(max_limit=200)
    assert dep() == 50


def test_paginate_respects_a_different_max_limit_per_route():
    dep = paginate(max_limit=25)
    assert dep(limit=100) == 25
