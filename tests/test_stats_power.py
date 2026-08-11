import math

import pytest

from services import stats_power as sp


# --- margin_of_error_pts --------------------------------------------------

def test_margin_of_error_matches_known_textbook_value():
    # Classic case: n=100, p=0.5, 95% CI -> margin = 1.96 * sqrt(0.25/100) * 100 = 9.8pts
    assert sp.margin_of_error_pts(100, observed_pct=50.0) == pytest.approx(9.8, abs=0.05)


def test_margin_of_error_shrinks_as_n_grows():
    small_n = sp.margin_of_error_pts(30, observed_pct=50.0)
    large_n = sp.margin_of_error_pts(3000, observed_pct=50.0)
    assert large_n < small_n


def test_margin_of_error_is_widest_at_50_percent():
    at_50 = sp.margin_of_error_pts(200, observed_pct=50.0)
    at_10 = sp.margin_of_error_pts(200, observed_pct=10.0)
    at_90 = sp.margin_of_error_pts(200, observed_pct=90.0)
    assert at_50 > at_10
    assert at_50 > at_90


def test_margin_of_error_symmetric_around_50():
    at_20 = sp.margin_of_error_pts(150, observed_pct=20.0)
    at_80 = sp.margin_of_error_pts(150, observed_pct=80.0)
    assert at_20 == pytest.approx(at_80, abs=1e-9)


def test_margin_of_error_widens_with_higher_confidence_level():
    at_90 = sp.margin_of_error_pts(100, confidence_level=0.90)
    at_95 = sp.margin_of_error_pts(100, confidence_level=0.95)
    at_99 = sp.margin_of_error_pts(100, confidence_level=0.99)
    assert at_90 < at_95 < at_99


def test_margin_of_error_infinite_at_zero_n():
    assert sp.margin_of_error_pts(0) == float("inf")


def test_margin_of_error_unknown_confidence_level_falls_back_to_95pct():
    default = sp.margin_of_error_pts(100, confidence_level=0.95)
    unknown = sp.margin_of_error_pts(100, confidence_level=0.83)
    assert unknown == default


# --- min_n_for_margin ------------------------------------------------------

def test_min_n_for_margin_matches_known_textbook_value():
    # Solving n for a 5pt margin at p=0.5, 95% CI: n = 1.96^2 * 0.25 / 0.05^2 ~= 384.16 -> ceil 385
    assert sp.min_n_for_margin(5.0, observed_pct=50.0) == 385


def test_min_n_for_margin_and_margin_of_error_are_inverses():
    n = sp.min_n_for_margin(5.0, observed_pct=50.0)
    achieved_margin = sp.margin_of_error_pts(n, observed_pct=50.0)
    assert achieved_margin <= 5.0


def test_min_n_for_margin_larger_margin_needs_fewer_samples():
    loose = sp.min_n_for_margin(10.0)
    tight = sp.min_n_for_margin(2.0)
    assert loose < tight


def test_min_n_for_margin_infinite_at_zero_margin():
    assert sp.min_n_for_margin(0.0) == float("inf")
