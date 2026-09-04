import pytest

from services import title_cache
from services.kalshi_fees import (
    breakeven_unit_cost, forced_exit_quote, maker_fee, sellable_quote, taker_fee, taker_fee_per_contract,
    unit_cost,
)


def _seed_fee_override(tmp_path, monkeypatch, ticker, event_ticker, fee_type_override=None, fee_multiplier_override=None):
    """Seeds title_cache with the market->event link and the event's fee
    override, isolated to a fresh per-test DB file - same monkeypatch
    convention as tests/test_title_cache.py's own _tc() helper. Every new
    test below calls this (even ones that assert NO override applies) so
    none of them can pick up another test's leftover row from a shared
    session-wide temp DB (the exact collection-order class of bug
    tests/conftest.py's own docstring records)."""
    monkeypatch.setattr(title_cache, "DB_PATH", tmp_path / "title_cache.db")
    title_cache.save_market_titles({
        ticker: {"title": ticker, "yes_sub_title": None, "no_sub_title": None, "event_ticker": event_ticker},
    })
    title_cache.save_event_titles({
        event_ticker: {
            "title": event_ticker, "sub_title": None, "category": None,
            "fee_type_override": fee_type_override, "fee_multiplier_override": fee_multiplier_override,
        },
    })


def test_taker_fee_matches_real_verified_fills():
    # Verified 2026-08-09 directly against three real fills on a connected
    # Kalshi account (docs/prediction-markets-research-reference.md Part 2.4) -
    # not derived from the formula itself, these are the real numbers, and
    # under the pre-3.29.0 $0.0001 trade-fee ceiling then documented, all
    # three matched exactly (0.1328, 0.1592, 0.2149).
    #
    # Trade API 3.29.0 (issue #253) documents the trade-fee ceiling as
    # $0.000001, not $0.0001 (docs/kalshi/fee_rounding.md:18,22 - "Fees are
    # six-decimal dollar amounts"; pre-3.29.0 the same lines said $0.0001).
    # The values below are the SAME rate/formula re-ceiled to that finer
    # precision - a recomputation, not a fresh real-fill re-verification
    # (kalshi_account.trading_enabled has always been false, so no real
    # fill exists to re-check the exact 6dp value against; see this
    # module's own docstring). 9.13 @ 0.53 happens to land on the same
    # value at both precisions (0.15919981 ceils to 0.1592 either way);
    # the other two shift by a fraction of a cent, exactly the "up to
    # $0.000099" overstatement issue #253 named.
    assert taker_fee(14.11, 0.84) == 0.132747
    assert taker_fee(9.13, 0.53) == 0.1592
    assert taker_fee(21.75, 0.17) == 0.214825


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


# --- Event-level fee overrides (issue #264) --------------------------------
# services/title_cache.py and services/market_watch/event_metadata.py
# already capture and persist fee_type_override/fee_multiplier_override for
# every event; before this fix kalshi_fees.py never read either column
# back - every fee went through the series-level table only.


def test_taker_fee_honours_an_event_multiplier_override_absent_from_the_series_table(tmp_path, monkeypatch):
    # KXNFLGAME isn't in _FEE_MULTIPLIER_BY_SERIES (default multiplier 1.0)
    # - an event-level override must still cut the fee, which the OLD code
    # (never reading event_titles at all) could not do: it would have
    # returned the same value as the un-overridden baseline below.
    ticker, event_ticker = "KXNFLGAME-26AUG15MINNYG-MIN", "KXNFLGAME-26AUG15MINNYG"
    _seed_fee_override(tmp_path, monkeypatch, ticker, event_ticker, fee_multiplier_override=0.25)
    baseline = taker_fee(100, 0.5)  # no ticker -> default multiplier 1.0
    assert taker_fee(100, 0.5, ticker=ticker) == pytest.approx(baseline / 4, abs=0.0001)


def test_event_multiplier_override_takes_precedence_over_a_nonzero_series_table_entry(tmp_path, monkeypatch):
    # docs/kalshi/get-event-fee-changes.md: an event-level override is
    # "layered on top of the parent series' fee structure" and wins when
    # non-null - even when the series table ALREADY has a real, nonzero
    # entry (KXMLBGAME is 0.5 in _FEE_MULTIPLIER_BY_SERIES). The OLD code
    # never looked at the override, so it always used the series table's
    # 0.5 here regardless.
    ticker, event_ticker = "KXMLBGAME-26AUG13GBPIT-PIT", "KXMLBGAME-26AUG13GBPIT"
    _seed_fee_override(tmp_path, monkeypatch, ticker, event_ticker, fee_multiplier_override=1.0)
    standard = taker_fee(100, 0.5)  # multiplier 1.0, no ticker
    assert taker_fee(100, 0.5, ticker=ticker) == standard  # NOT the series table's 0.5x


def test_taker_fee_falls_back_to_the_series_table_when_no_override_is_cached(tmp_path, monkeypatch):
    # The overwhelmingly common case: an event that has never had a
    # scheduled fee change carries fee_type_override/fee_multiplier_override
    # = NULL/NULL - "the override is cleared" per the docs, meaning "use the
    # parent series' own fee structure", i.e. exactly today's pre-fix
    # behavior for that ticker.
    ticker, event_ticker = "KXMLBGAME-26AUG13GBPIT-PIT", "KXMLBGAME-26AUG13GBPIT"
    _seed_fee_override(tmp_path, monkeypatch, ticker, event_ticker)  # both overrides None
    assert taker_fee(100, 0.5, ticker=ticker) == pytest.approx(taker_fee(100, 0.5) / 2, abs=0.0001)


def test_breakeven_unit_cost_honours_an_event_multiplier_override(tmp_path, monkeypatch):
    # breakeven_unit_cost -> taker_fee_per_contract must see the same
    # override as taker_fee() itself, with no separate plumbing.
    ticker, event_ticker = "KXNFLGAME-26AUG15MINNYG-MIN", "KXNFLGAME-26AUG15MINNYG"
    _seed_fee_override(tmp_path, monkeypatch, ticker, event_ticker, fee_multiplier_override=0.0)
    assert breakeven_unit_cost(0.70, ticker=ticker) == pytest.approx(0.70)


# --- quadratic_with_combo_maker_fees: 0.5 maker multiplier (issue #264) ----
# docs/kalshi/get-series-list.md's FeeType schema: "the same maker-fee
# structure with a 0.5 maker multiplier instead of 0.25" - corroborated by
# docs/kalshi/changelog-index.md's 2026-08-22 "Combo RFQ fee assignment for
# briefly resting orders" entry (same 0.5-vs-0.25 language). Before this
# fix, kalshi_fees.py never read fee_type at all, so every maker fee -
# including this one - was charged at the standard 0.25 rate.


def test_maker_fee_doubles_for_the_documented_combo_fee_type(tmp_path, monkeypatch):
    ticker, event_ticker = "KXCOMBOGAME-26AUG20XYZ-A", "KXCOMBOGAME-26AUG20XYZ"
    _seed_fee_override(tmp_path, monkeypatch, ticker, event_ticker, fee_type_override="quadratic_with_combo_maker_fees")
    standard = maker_fee(100, 0.5)  # no ticker -> standard 0.25 maker rate
    assert maker_fee(100, 0.5, ticker=ticker) == pytest.approx(standard * 2, abs=0.0001)


def test_combo_fee_type_override_leaves_taker_fee_unaffected(tmp_path, monkeypatch):
    # Both docs describe a MAKER-fee-only structural difference - the
    # General Trading Fees Table (taker side) is the same across
    # quadratic/quadratic_with_maker_fees/quadratic_with_combo_maker_fees.
    ticker, event_ticker = "KXCOMBOGAME-26AUG20XYZ-A", "KXCOMBOGAME-26AUG20XYZ"
    _seed_fee_override(tmp_path, monkeypatch, ticker, event_ticker, fee_type_override="quadratic_with_combo_maker_fees")
    assert taker_fee(100, 0.5, ticker=ticker) == taker_fee(100, 0.5)


def test_maker_fee_combo_type_and_multiplier_override_both_apply_independently(tmp_path, monkeypatch):
    # Issue #264's own fix note: "both could apply - the series table for
    # combo/sport-specific overrides, fee_type for this structural
    # difference". Independently nullable per docs/kalshi/
    # get-event-fee-changes.md, so an event can carry both at once.
    ticker, event_ticker = "KXCOMBOGAME-26AUG20XYZ-A", "KXCOMBOGAME-26AUG20XYZ"
    _seed_fee_override(
        tmp_path, monkeypatch, ticker, event_ticker,
        fee_type_override="quadratic_with_combo_maker_fees", fee_multiplier_override=0.5,
    )
    standard = maker_fee(100, 0.5)
    # 0.5 multiplier halves it, the combo fee_type doubles it - net unchanged.
    assert maker_fee(100, 0.5, ticker=ticker) == pytest.approx(standard, abs=0.0001)


def test_unrecognised_fee_type_override_does_not_change_the_maker_multiplier(tmp_path, monkeypatch):
    # Only quadratic_with_combo_maker_fees is documented to change the
    # maker multiplier - "quadratic_with_maker_fees" (the presumed default
    # everywhere already), "quadratic", "flat" (unmodeled - see this
    # module's own docstring), or a genuinely unknown future value must
    # all leave the standard 0.25 rate alone rather than guess.
    ticker, event_ticker = "KXCOMBOGAME-26AUG20XYZ-A", "KXCOMBOGAME-26AUG20XYZ"
    _seed_fee_override(tmp_path, monkeypatch, ticker, event_ticker, fee_type_override="quadratic_with_maker_fees")
    assert maker_fee(100, 0.5, ticker=ticker) == maker_fee(100, 0.5)


# --- KXMVECROSSCATEGORY0-SHARD1: full maker-fee exemption (issue #258) -----
# docs/kalshi/changelog-index.md's 2026-08-20 "Maker fee exemption for
# independent NFL combo markets" entry: combo markets under this series
# have NO maker fee. Before this fix there was no entry for this series
# anywhere in kalshi_fees.py, so it was charged the standard maker rate.


def test_maker_fee_is_zero_for_the_nfl_combo_exempt_series(tmp_path, monkeypatch):
    monkeypatch.setattr(title_cache, "DB_PATH", tmp_path / "title_cache.db")
    assert maker_fee(100, 0.5, ticker="KXMVECROSSCATEGORY0-SHARD1-26NOV01NFL-XYZ") == 0.0


def test_nfl_combo_maker_exemption_does_not_zero_the_taker_fee(tmp_path, monkeypatch):
    # The changelog documents a MAKER-fee-only exemption ("no maker fee") -
    # nothing says the taker fee changes. Folding this into
    # _FEE_MULTIPLIER_BY_SERIES (shared by taker_fee() and maker_fee() via
    # _multiplier()) would have incorrectly zeroed the taker fee too.
    monkeypatch.setattr(title_cache, "DB_PATH", tmp_path / "title_cache.db")
    ticker = "KXMVECROSSCATEGORY0-SHARD1-26NOV01NFL-XYZ"
    assert taker_fee(100, 0.5, ticker=ticker) == taker_fee(100, 0.5)


def test_nfl_combo_maker_exemption_matches_the_exact_series_not_a_series_of_prefix(tmp_path, monkeypatch):
    # series_of() (services/signal_log.py) splits on the FIRST hyphen only,
    # so series_of("KXMVECROSSCATEGORY0-SHARD1-...") is just
    # "KXMVECROSSCATEGORY0" - dropping "-SHARD1". A series that merely
    # shares that truncated prefix (a hypothetical shard 11, or an
    # unrelated "-OTHER" suffix) must NOT be treated as exempt.
    monkeypatch.setattr(title_cache, "DB_PATH", tmp_path / "title_cache.db")
    standard = maker_fee(100, 0.5)
    assert maker_fee(100, 0.5, ticker="KXMVECROSSCATEGORY0-SHARD11-26NOV01-X") == standard
    assert maker_fee(100, 0.5, ticker="KXMVECROSSCATEGORY0-OTHER-26NOV01-X") == standard


def test_taker_fee_per_contract_is_the_rate_without_the_per_fill_ceiling():
    """The $0.000001 ceiling in taker_fee() (issue #253, Trade API 3.29.0 -
    was $0.0001) is charged once per FILL, not once per contract - the
    real-fill evidence in kalshi_fees' own docstring rounds the whole
    14.11-contract order, not each contract. A per-contract rate therefore
    must not carry it, and is exactly the limit of the per-fill fee spread
    across many contracts."""
    assert taker_fee_per_contract(0.70) == pytest.approx(0.0147, abs=1e-12)
    assert taker_fee_per_contract(0.85) == pytest.approx(0.008925, abs=1e-12)
    # The per-fill ceiling adds at most $0.000001 to the ORDER (was $0.0001
    # pre-3.29.0), so spread over n contracts the gap is bounded by 1e-6/n
    # and is one-sided (the per-fill rate never understates). n = 1e5 leaves
    # an order of magnitude of margin rather than asserting exactly on the
    # bound - tighter than before (was margin against a 1e-4/n bound), not
    # looser.
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


# --- sellable_quote / forced_exit_quote (2026-09-04) ------------------------
# docs/kalshi/get-market-orderbook.md:7 - "a bid for yes at price X is
# equivalent to an ask for no at price (100-X)". Read in the selling
# direction: a YES holder hits yes_bid, a NO holder hits (1 - yes_ask).

def test_sellable_quote_yes_sells_into_the_yes_bid():
    assert sellable_quote("yes", 0.20, 0.30) == 0.20


def test_sellable_quote_no_sells_into_the_no_bid_which_is_the_yes_ask():
    # The bug: this used to return the yes_bid for both sides, so a NO
    # position was valued at (1 - yes_bid) = the NO ASK.
    assert sellable_quote("no", 0.20, 0.30) == 0.30
    assert unit_cost("no", sellable_quote("no", 0.20, 0.30)) == pytest.approx(0.70)


def test_sellable_quote_refuses_an_empty_yes_book_for_a_no_position():
    # yes_ask 1.00 is a NO bid of 0.00 - the shape that paid $1.00/contract.
    assert sellable_quote("no", 0.0, 1.0) is None


def test_sellable_quote_refuses_a_yes_position_with_no_bid():
    assert sellable_quote("yes", 0.0, 0.02) is None


def test_sellable_quote_refuses_a_missing_ask_rather_than_guessing():
    assert sellable_quote("no", 0.20, None) is None


def test_sellable_quote_refuses_a_crossed_book():
    assert sellable_quote("no", 0.62, 0.60) is None


def test_sellable_quote_crossed_check_uses_the_bid_the_caller_nominates():
    # exit_engine prices off a REST-corroborated bid but must check crossing
    # against the raw WS bid - comparing a stale REST bid to a live ask
    # otherwise reads as "crossed" and disables every NO exit on a rally.
    assert sellable_quote("no", 0.62, 0.60, crossed_against=0.20) == 0.60


def test_sellable_quote_rejects_an_unknown_side():
    with pytest.raises(ValueError):
        sellable_quote("maybe", 0.2, 0.3)


def test_forced_exit_quote_matches_sellable_quote_when_the_book_is_real():
    assert forced_exit_quote("no", 0.20, 0.30, unknown_fallback=0.9) == 0.30
    assert forced_exit_quote("yes", 0.20, 0.30, unknown_fallback=0.9) == 0.20


def test_forced_exit_quote_pays_zero_not_one_on_an_empty_book():
    # "Nobody will buy this" is worth nothing, not everything. The old path
    # returned (1 - yes_bid) = $1.00/contract here.
    assert unit_cost("no", forced_exit_quote("no", 0.0, 1.0, unknown_fallback=0.9)) == pytest.approx(0.0)
    assert unit_cost("yes", forced_exit_quote("yes", 0.0, None, unknown_fallback=0.9)) == pytest.approx(0.0)


def test_forced_exit_quote_uses_the_fallback_when_the_ask_is_merely_UNKNOWN():
    # 2026-09-04 adversarial review D5: state["latest_asks"] is deliberately
    # sparse (12% of active markets carried no ask when measured), so folding
    # "no ask on file" into the empty-book branch would book a total loss on
    # every one of them - a worse error than the bug being fixed. Missing
    # data is not evidence of an empty book.
    assert forced_exit_quote("no", 0.20, None, unknown_fallback=0.62) == 0.62


def test_forced_exit_quote_yes_side_never_consults_the_ask():
    # A YES position sells into the bid, so a missing ask is irrelevant to it
    # and must not divert it to the fallback.
    assert forced_exit_quote("yes", 0.20, None, unknown_fallback=0.99) == 0.20


def test_sellable_quote_explicit_none_crossed_against_skips_the_crossed_check():
    # An explicit None means "no valid bid to compare against", which must
    # NOT collapse back to the yes_bid comparison - that is what a caller
    # means when yes_bid is only a historical entry-price fallback.
    assert sellable_quote("no", 0.90, 0.40, crossed_against=None) == 0.40
    # Omitting it entirely still checks against yes_bid.
    assert sellable_quote("no", 0.90, 0.40) is None
