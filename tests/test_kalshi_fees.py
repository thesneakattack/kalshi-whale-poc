import pytest

from services.kalshi_fees import breakeven_unit_cost, taker_fee, taker_fee_per_contract, unit_cost


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


def test_taker_fee_half_rate_for_the_real_mlb_proposition_family():
    # Live-verified 2026-08-15 directly against Kalshi's own GET /series
    # endpoint (fee_multiplier field) - the entire MLB proposition-market
    # family (spread/total/outs/HR/hits/...) charges HALF the standard
    # rate, previously entirely unmodeled (every MLB trade was overcharged
    # 2x before this fix). See services/kalshi_fees.py's own docstring.
    assert taker_fee(100, 0.5, ticker="KXMLBGAME-26AUG13GBPIT-PIT") == pytest.approx(taker_fee(100, 0.5) / 2, abs=0.0001)
    assert taker_fee(100, 0.5, ticker="KXMLBTOTAL-26AUG13GBPIT-8") == pytest.approx(taker_fee(100, 0.5) / 2, abs=0.0001)
    assert taker_fee(100, 0.5, ticker="KXMLBSPREAD-26AUG13GBPIT-PIT2") == pytest.approx(taker_fee(100, 0.5) / 2, abs=0.0001)


def test_taker_fee_zero_for_series_missed_by_the_original_pdf_pass():
    # Live-verified 2026-08-15: these four are genuine multiplier-0 series
    # that the 2026-08-14 PDF-sourced list didn't include.
    assert taker_fee(100, 0.5, ticker="KXEXPAND-26") == 0.0
    assert taker_fee(100, 0.5, ticker="KXNEXTIRANLEADER-26") == 0.0
    assert taker_fee(100, 0.5, ticker="KXTRUMPOUT-26") == 0.0
    assert taker_fee(100, 0.5, ticker="KXGDPYEAR-26") == 0.0


def test_taker_fee_per_contract_is_the_rate_without_the_per_fill_ceiling():
    """The $0.0001 ceiling in taker_fee() is charged once per FILL, not once
    per contract - the real-fill evidence in kalshi_fees' own docstring
    rounds the whole 14.11-contract order, not each contract. A per-contract
    rate therefore must not carry it, and is exactly the limit of the
    per-fill fee spread across many contracts."""
    assert taker_fee_per_contract(0.70) == pytest.approx(0.0147, abs=1e-12)
    assert taker_fee_per_contract(0.85) == pytest.approx(0.008925, abs=1e-12)
    # The per-fill ceiling adds at most $0.0001 to the ORDER, so spread over
    # n contracts the gap is bounded by 1e-4/n and is one-sided (the per-fill
    # rate never understates). n = 1e5 leaves an order of magnitude of margin
    # rather than asserting exactly on the bound.
    assert taker_fee(100000, 0.85) / 100000 == pytest.approx(
        taker_fee_per_contract(0.85), abs=1e-8)


def test_taker_fee_per_contract_is_zero_at_the_price_extremes():
    assert taker_fee_per_contract(0.0) == 0.0
    assert taker_fee_per_contract(1.0) == 0.0


def test_breakeven_unit_cost_is_the_price_plus_the_taker_fee():
    """Issue #205: a contract bought at unit cost c pays $1 or $0, so its
    breakeven win probability is what it COST, and what it cost includes the
    taker fee on the fill. Table verified 2026-08-30 against
    c + 0.07*c*(1-c)."""
    assert breakeven_unit_cost(0.60) == pytest.approx(0.6168, abs=1e-12)
    assert breakeven_unit_cost(0.70) == pytest.approx(0.7147, abs=1e-12)
    assert breakeven_unit_cost(0.80) == pytest.approx(0.8112, abs=1e-12)
    assert breakeven_unit_cost(0.85) == pytest.approx(0.858925, abs=1e-12)


def test_breakeven_unit_cost_honours_the_real_per_series_fee_multiplier():
    """A multiplier-0 series charges no taker fee at all, so its breakeven
    really IS the entry price; the MLB proposition family pays half rate.
    Applying the default rate to either would be the same mislabelling this
    fix removes, just in the other direction."""
    assert breakeven_unit_cost(0.70, ticker="KXBTCY-26-T150000") == pytest.approx(0.70)
    assert breakeven_unit_cost(0.70, ticker="KXMLBGAME-26AUG13GBPIT-PIT") == pytest.approx(
        0.70735, abs=1e-12)
    assert breakeven_unit_cost(0.70, ticker="KXBTC15M-26AUG30") == pytest.approx(0.7147, abs=1e-12)


# --- unit_cost: the one side-aware per-contract cost (issue #212) ----------


def test_unit_cost_yes_side_is_the_yes_price_itself():
    assert unit_cost("yes", 0.3) == 0.3
    assert unit_cost("yes", 0.84) == 0.84


def test_unit_cost_no_side_is_the_complement_of_the_yes_price():
    # docs/kalshi/get-market-orderbook.md: "a bid for yes at price X is
    # equivalent to an ask for no at price (100-X)" - a NO contract at yes
    # price 0.3 costs 0.7, the exact inversion the shipped no-side bug
    # (CLAUDE.md, "A displayed value must match its label") got wrong.
    assert unit_cost("no", 0.3) == 1 - 0.3
    assert unit_cost("no", 0.84) == pytest.approx(0.16)


@pytest.mark.parametrize("side,yes_price,expected", [
    ("yes", 0.0, 0.0), ("no", 0.0, 1.0),
    ("yes", 1.0, 1.0), ("no", 1.0, 0.0),
])
def test_unit_cost_at_the_price_boundaries(side, yes_price, expected):
    # 0 and 1 are the two prices config_bounds.is_tradeable_unit_cost
    # refuses on either side; the helper itself stays pure arithmetic and
    # never clamps, so the gate sees exactly what was quoted.
    assert unit_cost(side, yes_price) == expected


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("price", [0.0, 0.01, 0.3, 0.5, 0.7, 0.99, 1.0])
def test_unit_cost_is_bit_identical_to_the_strategy_engine_gate_expression(side, price):
    # strategy_engine._validate_entry_price's own gate line is the canonical
    # semantics this helper replaces (issue #212); a last-ulp drift here
    # would let the admission band and the broker's charge disagree.
    assert unit_cost(side, price) == (price if side == "yes" else (1 - price))


def test_unit_cost_none_price_stays_none_for_both_sides():
    # "no price known" must never become an invented cost - the contract
    # the three private _unit_cost copies (diagnostics, series_watcher,
    # reset.trade_archive) already had, and websocket.py's reader gate
    # relies on to record a rejection with unit_cost=None.
    assert unit_cost("yes", None) is None
    assert unit_cost("no", None) is None


@pytest.mark.parametrize("bad_side", ["YES", "Yes", "NO", " no", "", "bid", "buy_yes", None, 1])
def test_unit_cost_rejects_any_side_that_is_not_exactly_yes_or_no(bad_side):
    # Every producer in this app emits exactly "yes"/"no" (contracts/trade.py
    # OutcomeSide, WhaleSignal.side, Position.side; every persisted row
    # checked 2026-08-30). The inline copies silently treated anything else
    # as "no" - wrong direction and wrong cost with no trace - so an
    # unknown spelling is rejected loudly, never normalised or guessed.
    with pytest.raises(ValueError):
        unit_cost(bad_side, 0.3)
    with pytest.raises(ValueError):
        unit_cost(bad_side, None)
