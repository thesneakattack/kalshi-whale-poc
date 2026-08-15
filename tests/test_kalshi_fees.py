import pytest

from services.kalshi_fees import taker_fee


def test_taker_fee_matches_real_verified_fills():
    # Verified 2026-08-09 directly against three real fills on a connected
    # Kalshi account (docs/prediction-markets-research-reference.md Part 2.4) -
    # not derived from the formula itself, these are the real numbers.
    assert taker_fee(14.11, 0.84) == 0.1328
    assert taker_fee(9.13, 0.53) == 0.1592
    assert taker_fee(21.75, 0.17) == 0.2149


def test_taker_fee_is_symmetric_in_price_and_its_complement():
    # price*(1-price) is the same either way - a caller never needs to pick
    # "the yes price" vs "the no price" specially. Tolerance of one
    # ten-thousandth: (1-0.3) isn't bit-identical to the literal 0.7 in
    # IEEE754, which can tip the ceil() to round up on one side but not the
    # other - the same real-world precision this fee schedule already has.
    assert taker_fee(100, 0.3) == pytest.approx(taker_fee(100, 0.7), abs=0.0001)


def test_taker_fee_is_zero_at_price_extremes():
    # Matches settlement's terminal $1/$0 payout not being a fee-charged
    # trade (services/paper_broker.py's close_if_settled path).
    assert taker_fee(100, 0.0) == 0.0
    assert taker_fee(100, 1.0) == 0.0


def test_taker_fee_is_zero_for_non_positive_contracts():
    assert taker_fee(0, 0.5) == 0.0
    assert taker_fee(-5, 0.5) == 0.0


def test_taker_fee_is_maximal_at_fifty_cents():
    # The parabolic fee curve peaks at P=0.5 - worst case exactly
    # $0.0175/contract.
    fee_at_mid = taker_fee(1, 0.5)
    assert fee_at_mid == pytest.approx(0.0175, abs=0.0001)
    assert taker_fee(1, 0.2) < fee_at_mid
    assert taker_fee(1, 0.8) < fee_at_mid


def test_taker_fee_is_zero_for_real_zero_fee_series():
    # docs/kalshi/kalshi-fee-schedule.pdf's "Non-Standard Fees" table lists
    # a real multiplier of 0 (full fee waiver) for these ten series -
    # 2026-08-14 direct request to reconcile the app's fee model against
    # the real schedule.
    assert taker_fee(100, 0.5, ticker="KXETHY-26-T5000") == 0.0
    assert taker_fee(100, 0.5, ticker="KXBTCY-26-T150000") == 0.0
    assert taker_fee(100, 0.5, ticker="KXGREENLAND-26") == 0.0


def test_taker_fee_ticker_omitted_or_ordinary_series_is_unaffected():
    # Every series not in the real schedule's zero-multiplier list uses the
    # default multiplier of 1 - identical to omitting ticker entirely, and
    # identical to every pre-2026-08-14 call site's behavior.
    assert taker_fee(100, 0.5, ticker="KXNFLGAME-26AUG15MINNYG-MIN") == taker_fee(100, 0.5)
    assert taker_fee(100, 0.5, ticker=None) == taker_fee(100, 0.5)
