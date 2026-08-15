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


# --- two_proportion_z_score ------------------------------------------------

def test_two_proportion_z_score_matches_known_textbook_value():
    # n_a=100 p_a=60%, n_b=100 p_b=50%: pooled p=0.55, se=sqrt(0.55*0.45*0.02)~=0.070356,
    # z = 0.1 / 0.070356 ~= 1.4213
    z = sp.two_proportion_z_score(100, 60.0, 100, 50.0)
    assert z == pytest.approx(1.4213, abs=0.001)


def test_two_proportion_z_score_zero_when_rates_are_equal():
    assert sp.two_proportion_z_score(100, 55.0, 200, 55.0) == pytest.approx(0.0, abs=1e-9)


def test_two_proportion_z_score_sign_reflects_direction():
    a_higher = sp.two_proportion_z_score(100, 70.0, 100, 50.0)
    b_higher = sp.two_proportion_z_score(100, 50.0, 100, 70.0)
    assert a_higher > 0
    assert b_higher < 0
    assert a_higher == pytest.approx(-b_higher, abs=1e-9)


def test_two_proportion_z_score_grows_with_more_samples_at_same_gap():
    small_n = sp.two_proportion_z_score(20, 70.0, 20, 50.0)
    large_n = sp.two_proportion_z_score(2000, 70.0, 2000, 50.0)
    assert large_n > small_n  # same 20pt gap, more confidence with more data


def test_two_proportion_z_score_none_when_either_sample_empty():
    assert sp.two_proportion_z_score(0, 50.0, 100, 50.0) is None
    assert sp.two_proportion_z_score(100, 50.0, 0, 50.0) is None


def test_two_proportion_z_score_none_when_pooled_proportion_is_zero():
    # every trade on both sides lost - zero variance, no gap to explain
    assert sp.two_proportion_z_score(50, 0.0, 50, 0.0) is None


def test_two_proportion_z_score_none_when_pooled_proportion_is_one():
    assert sp.two_proportion_z_score(50, 100.0, 50, 100.0) is None


# --- one_sample_t_score -----------------------------------------------------

def test_one_sample_t_score_matches_known_textbook_value():
    # values [2, 4, 6, 8, 10]: mean=6, sample stdev~=3.1623, se=3.1623/sqrt(5)~=1.4142
    # t = (6 - 0) / 1.4142 ~= 4.2426
    t = sp.one_sample_t_score([2, 4, 6, 8, 10])
    assert t == pytest.approx(4.2426, abs=0.001)


def test_one_sample_t_score_against_nonzero_reference():
    # same values, testing against reference=6 (the sample mean) -> t=0
    t = sp.one_sample_t_score([2, 4, 6, 8, 10], reference=6.0)
    assert t == pytest.approx(0.0, abs=1e-9)


def test_one_sample_t_score_sign_reflects_direction():
    positive = sp.one_sample_t_score([1.0, 2.0, 3.0])
    negative = sp.one_sample_t_score([-1.0, -2.0, -3.0])
    assert positive > 0
    assert negative < 0


def test_one_sample_t_score_grows_with_more_samples_at_same_mean_and_spread():
    small_n = sp.one_sample_t_score([1.0, 2.0, 3.0])
    large_n = sp.one_sample_t_score([1.0, 2.0, 3.0] * 100)
    assert large_n > small_n


def test_one_sample_t_score_none_with_fewer_than_two_values():
    assert sp.one_sample_t_score([]) is None
    assert sp.one_sample_t_score([5.0]) is None


def test_one_sample_t_score_none_when_all_values_identical():
    assert sp.one_sample_t_score([3.0, 3.0, 3.0]) is None
