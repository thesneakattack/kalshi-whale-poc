"""
Real Kalshi taker-fee formula. Verified 2026-08-09 directly against three
real fills on this app's own connected account (see
docs/prediction-markets-research-reference.md Part 2.4) rather than trusted
from scraped fee-schedule blogs, which converge on the same coefficient but
disagreed on rounding precision:

    taker_fee = ceil_to_$0.000001( 0.07 * contracts * price * (1 - price) )

Checked 2026-08-09 against real fee_cost values under the ceiling then
documented ($0.0001): 14.11 contracts @ $0.84 -> predicted/actual $0.1328.
9.13 @ $0.53 -> predicted/actual $0.1592. 21.75 @ $0.17 -> predicted/actual
$0.2149. All three exact at that precision.

Trade API 3.29.0 (2026-08-30, issue #253) documents the trade-fee ceiling
as $0.000001, six decimal places, not $0.0001 (docs/kalshi/
fee_rounding.md:18,22 - "Fees are six-decimal dollar amounts
($0.000001 granularity)"; the trade-fee component is "rounded up to the
nearest $0.000001"; pre-3.29.0 the same lines said $0.0001). The ceiling
below was updated to match; the three real-fill numbers above are the
historical calibration at the OLD precision (kalshi_account.trading_enabled
has always been false, so there is no real fill to re-verify the new,
finer ceiling against) - see tests/test_kalshi_fees.py's own comment on
what the re-ceiled values are and why they're a recomputation, not a fresh
real-fill match. PR #224's per-fill (not per-contract) application argument
is unaffected: a finer ceiling only tightens the per-fill overstatement
bound (now up to $0.000001, not $0.0001), it doesn't change which quantity
the ceiling is applied to.

Before this, services/paper_broker.py modeled zero fees anywhere - every
paper-mode P&L figure (including shadow mode's own "would I trust this with
real money" read) was systematically more optimistic than real trading
would produce. See docs/prediction-market-strategy-alignment-plan.md
Part 2.2.

Deliberately price/(1-price)-symmetric, so a caller never needs to pick
"the yes price" vs "the no price" specially - the formula returns the same
number either way (0.84*(1-0.84) == 0.16*(1-0.16)), same as every other
place in this app that already treats price as "always the yes-side price
by convention" (see paper_broker.py's module docstring).

Maker fee (confirmed 2026-08-14 against docs/kalshi/kalshi-fee-schedule.pdf
- exactly 1/4 of the taker rate, 0.0175 vs 0.07, matching what was
previously only secondary-sourced) is still NOT implemented here - this
app has no maker/limit-order path yet (services/kalshi/orders.py's
real order path defaults to "immediate_or_cancel", a taker order). Revisit
once that changes.

Per-series fee multiplier (2026-08-14, direct request to reconcile against
the real fee schedule PDF; corrected 2026-08-15 against Kalshi's own live
GET /series/{ticker} and GET /series endpoints - see docs.kalshi.com's
"Get Series"/"Get Series List" pages, each series' own `fee_multiplier`
field). The static PDF-sourced list this app shipped with (2026-08-14) only
found ten multiplier-0 series and missed real deviations entirely - a
direct live sample of every one of the 13,029 real series on Kalshi
(2026-08-15) found 33 total: 14 at multiplier 0 (four more than the PDF
list had - KXEXPAND, KXNEXTIRANLEADER, KXTRUMPOUT, KXGDPYEAR - either
missed by that pass or added since) and, previously entirely unmodeled, 19
at multiplier 0.5 - the ENTIRE MLB proposition-market family (spread,
total, outs, HR, hits, F5/F3/F7 innings, RBI, stolen bases, strikeouts,
total bases, team total, ...), one of this app's most actively-traded
series families in real trade history. Every MLB trade before this fix was
therefore charged double the real fee. See _FEE_MULTIPLIER_BY_SERIES below
- every series not listed defaults to multiplier 1, unchanged. Like the
PDF this replaces, this is still a point-in-time snapshot, not a live
fetch - Kalshi's own schedule can change again; revisit the same way if a
future real-fill fee_cost stops matching this formula.

Also home, since 2026-08-30 (issue #212), to unit_cost() - the one
side-aware per-contract cost every dollar figure in this app derives from.
It lives here rather than in a new module because this is the money-math
module every consumer of it already imports, and breakeven_unit_cost()
consumes its output directly.

Event-level fee overrides and the quadratic_with_combo_maker_fees fee type
(2026-08-30, issues #264/#258). `fee_type` was never read here at all
before this (grep for it in the pre-fix module found nothing) - every fee
went through _multiplier(ticker), a per-SERIES lookup only, even though:

(1) services/title_cache.py and services/market_watch/event_metadata.py
    already capture and persist `fee_type_override`/`fee_multiplier_override`
    into event_titles for every event (get_event()'s own response fields -
    nothing new is fetched by this fix). docs/kalshi/
    get-event-fee-changes.md: "Event fees are an override layered on top
    of the parent series' fee structure. If fee_type_override and
    fee_multiplier_override are null, that indicates the override is
    cleared." Each column falls back independently - an event can override
    only the multiplier, only the type, both, or neither.
(2) docs/kalshi/get-series-list.md's FeeType schema: "'quadratic_with_
    combo_maker_fees' is the same maker-fee structure with a 0.5 maker
    multiplier instead of 0.25" - corroborated independently by docs/
    kalshi/changelog-index.md's 2026-08-22 "Combo RFQ fee assignment for
    briefly resting orders" entry ("The maker fee uses a fee multiplier of
    0.5, rather than the standard 0.25"). Both docs describe a MAKER-fee-
    only difference - the taker rate (General Trading Fees Table) is the
    same across quadratic/quadratic_with_maker_fees/
    quadratic_with_combo_maker_fees, so this only ever changes maker_fee().
    `flat` (the fourth FeeType value, "Specific Trading Fees Table") isn't
    modeled here - no market/event in this app's own data has ever
    resolved to it, and inventing its formula from the enum name alone
    would be exactly the guess CLAUDE.md's "never guess" rule forbids;
    revisit if one ever does.

_event_fee_override() resolves both via title_cache.fee_override_for_ticker
- a single indexed join (market_titles.event_ticker -> event_titles), not
the full-table load_market_titles()/load_event_titles() scans main.py uses
to rebuild its in-memory caches. Fee calculation happens once per fill
(order open/close), not on the WS ingestion hot path CLAUDE.md's data-plane
rule is about, so one small indexed read per call is the right tradeoff
here over threading a new parameter through every existing call site
(paper_broker.py x4, exits/exit_engine.py, exits/position_netting.py,
series_watcher.py, reset/trade_archive.py) - which would also risk a
forgotten call site silently never seeing an override, the exact
completeness failure the data-plane rule calls out.

Issue #258's KXMVECROSSCATEGORY0-SHARD1 NFL-combo maker-fee exemption is
its own separate mechanism, not a case of the above: it's a SERIES-wide
policy (docs/kalshi/changelog-index.md's 2026-08-20 entry, "no maker fee"),
not a per-EVENT override, and this app has no pipeline for a series' own
base (non-override) fee_type - only per-event overrides are captured. It
also can't reuse _FEE_MULTIPLIER_BY_SERIES (shared by taker_fee() AND
maker_fee() via _multiplier()) - the changelog says nothing about the
taker fee for this series, so a 0.0 entry there would incorrectly zero
taker fees too. See _is_nfl_combo_maker_exempt() below for why it also
can't be matched via series_of() + a dict key.
"""
import math

from services import title_cache
from services.signal_log import series_of

_TAKER_RATE = 0.07
# Confirmed 2026-08-14 against docs/kalshi/kalshi-fee-schedule.pdf - exactly
# 1/4 the taker rate (0.0175 vs 0.07). See maker_fee()'s own docstring for
# why nothing called this until 2026-08-15.
_MAKER_RATE = _TAKER_RATE / 4

# Live-verified 2026-08-15 via Kalshi's real GET /series (single) and
# GET /series (list) endpoints' fee_multiplier field, sampled across all
# 13,029 real series - see this module's own docstring. Every series not
# listed here uses the default multiplier of 1 (today's existing formula,
# unchanged).
_FEE_MULTIPLIER_BY_SERIES = {
    # Full waiver (multiplier 0)
    "KXBTCY": 0.0, "KXCITRINI": 0.0, "KXDOED": 0.0, "KXELECTIRAN": 0.0,
    "KXETHY": 0.0, "KXEXPAND": 0.0, "KXGAMBLINGREPEAL": 0.0, "KXGDPYEAR": 0.0,
    "KXGREENLAND": 0.0, "KXIRANDEMOCRACY": 0.0, "KXLAYOFFSYINFO": 0.0,
    "KXNEXTIRANLEADER": 0.0, "KXPAHLAVIHEAD": 0.0, "KXTRUMPOUT": 0.0,
    # Half rate (multiplier 0.5) - the entire MLB proposition-market family.
    "KXMLBEXTRAS": 0.5, "KXMLBF3": 0.5, "KXMLBF5": 0.5, "KXMLBF5SPREAD": 0.5,
    "KXMLBF5TOTAL": 0.5, "KXMLBF7": 0.5, "KXMLBGAME": 0.5, "KXMLBHIT": 0.5,
    "KXMLBHR": 0.5, "KXMLBHRR": 0.5, "KXMLBKS": 0.5, "KXMLBOUTS": 0.5,
    "KXMLBRBI": 0.5, "KXMLBRFI": 0.5, "KXMLBSB": 0.5, "KXMLBSPREAD": 0.5,
    "KXMLBTB": 0.5, "KXMLBTEAMTOTAL": 0.5, "KXMLBTOTAL": 0.5,
}


def _multiplier(ticker: str | None) -> float:
    """The real per-series fee multiplier for `ticker` (1.0 for every series
    not in _FEE_MULTIPLIER_BY_SERIES, and 1.0 when no ticker is in scope at
    the call site). One lookup, shared by every fee function here, so a
    future correction to the table can never land in one of them only.
    This is the SERIES-level fallback - _effective_multiplier() below is
    what every fee function actually calls, and prefers an event-level
    override over this when one is active."""
    if ticker is None:
        return 1.0
    return _FEE_MULTIPLIER_BY_SERIES.get(series_of(ticker), 1.0)


def _event_fee_override(ticker: str | None) -> tuple[str | None, float | None]:
    """(fee_type_override, fee_multiplier_override) the event `ticker`
    belongs to has active right now, via title_cache.fee_override_for_ticker
    (services/title_cache.py - already persists both columns from every
    get_event() fetch; nothing here re-fetches anything). (None, None) for
    ticker=None, an uncached market/event, or an event with no active
    override in either column (docs/kalshi/get-event-fee-changes.md - null
    means "override cleared") - every fee function below falls back to
    _FEE_MULTIPLIER_BY_SERIES / the standard 0.25 maker rate in that case,
    identical to before this override existed."""
    if ticker is None:
        return None, None
    return title_cache.fee_override_for_ticker(ticker)


def _effective_multiplier(ticker: str | None, fee_multiplier_override: float | None) -> float:
    """The multiplier that actually applies: the event-level override when
    it's set (docs/kalshi/get-event-fee-changes.md - an event override is
    "layered on top of the parent series' fee structure" and, per that same
    line, wins whenever it isn't null - including an override of exactly
    0.0, a real waiver, not "no override"), otherwise the series-level
    _multiplier() lookup, unchanged from before event overrides existed
    here."""
    if fee_multiplier_override is not None:
        return fee_multiplier_override
    return _multiplier(ticker)


# docs/kalshi/get-series-list.md's FeeType schema (see this module's own
# docstring) - only this one fee_type changes the MAKER multiplier from
# the standard 0.25 to 0.5, i.e. 2x. Every other value (including no
# override at all, the None key .get() falls back to) leaves it unchanged.
_MAKER_TYPE_MULTIPLIER = {"quadratic_with_combo_maker_fees": 2.0}

# Issue #258: docs/kalshi/changelog-index.md's 2026-08-20 "Maker fee
# exemption for independent NFL combo markets" entry - independent-NFL-
# component combo markets created after 2026-08-19 under this series pay
# NO maker fee (taker fee unaffected - see this module's own docstring for
# why that rules out _FEE_MULTIPLIER_BY_SERIES). Verified as the literal
# series ticker by grepping docs/kalshi/changelog-index.md directly, not
# copied from a paraphrase.
_NFL_COMBO_MAKER_EXEMPT_SERIES = "KXMVECROSSCATEGORY0-SHARD1"


def _is_nfl_combo_maker_exempt(ticker: str | None) -> bool:
    """Whether `ticker` belongs to the KXMVECROSSCATEGORY0-SHARD1 maker-fee
    exemption. NOT implemented as series_of(ticker) in a dict: series_of()
    (services/signal_log.py) splits on the FIRST hyphen only, and this
    series ticker itself contains one -
    series_of("KXMVECROSSCATEGORY0-SHARD1-25NOV02-X") returns just
    "KXMVECROSSCATEGORY0", silently dropping "-SHARD1". Every entry in
    _FEE_MULTIPLIER_BY_SERIES is hyphen-free, so that table's series_of()
    convention has never had to handle this before. Matched by exact
    ticker or a "<series>-" prefix instead, so an unrelated series that
    happens to share the truncated series_of() prefix (e.g.
    KXMVECROSSCATEGORY0-SHARD11, or -OTHER) is never mistaken for it."""
    if ticker is None:
        return False
    return ticker == _NFL_COMBO_MAKER_EXEMPT_SERIES or ticker.startswith(_NFL_COMBO_MAKER_EXEMPT_SERIES + "-")


def taker_fee(contracts: float, price: float, ticker: str | None = None) -> float:
    """Real Kalshi taker fee for one leg of a trade (open OR close - Kalshi
    charges per fill, not per round-trip), in dollars. contracts <= 0
    returns 0.0 rather than a negative/nonsensical fee.

    ticker: optional, same series_of() definition used everywhere else in
    this app (signal_log.series_of) - when given, applies (in precedence
    order) the event-level fee_multiplier_override if title_cache has one
    cached (issue #264), else the real per-series multiplier in
    _FEE_MULTIPLIER_BY_SERIES (1.0, i.e. no change, for every series not
    listed there). Omitting it (any call site that genuinely has no ticker
    in scope) keeps the default multiplier of 1 everywhere, same as this
    function's original, real-fill-verified formula. fee_type never
    changes the taker rate (see this module's own docstring), so
    fee_type_override is not consulted here."""
    if contracts <= 0 or price <= 0 or price >= 1:
        return 0.0
    _, fee_multiplier_override = _event_fee_override(ticker)
    multiplier = _effective_multiplier(ticker, fee_multiplier_override)
    if multiplier == 0.0:
        return 0.0
    raw = _TAKER_RATE * multiplier * contracts * price * (1 - price)
    return math.ceil(raw * 1_000_000) / 1_000_000


def maker_fee(contracts: float, price: float, ticker: str | None = None) -> float:
    """Real Kalshi maker fee for one leg of a trade - same formula/rounding/
    per-series-multiplier convention as taker_fee(), just at _MAKER_RATE
    instead of _TAKER_RATE. Not implemented until 2026-08-15 (docs/profit-
    maximization-assessment-2026-08-15.md, direct request) because this app
    had no maker/limit-order path at all before then - every real order
    (services/kalshi/orders.py) defaulted to immediate_or_cancel (a
    taker order), and the paper broker filled everything instantly at the
    quoted price, which is also inherently a taker fill. See services/
    paper_broker.py's PendingOrder/check_pending_fills for the paper-mode
    limit-order simulation this now feeds.

    Two 2026-08-30 additions layered on top of that, both from this
    module's own docstring: KXMVECROSSCATEGORY0-SHARD1 is a full,
    unconditional exemption (issue #258, checked first, before any
    override lookup); otherwise an event-level fee_type_override of
    quadratic_with_combo_maker_fees doubles the maker multiplier from 0.25
    to 0.5 (issue #264), independently of whatever fee_multiplier_override
    or the series table says."""
    if contracts <= 0 or price <= 0 or price >= 1:
        return 0.0
    if _is_nfl_combo_maker_exempt(ticker):
        return 0.0
    fee_type_override, fee_multiplier_override = _event_fee_override(ticker)
    multiplier = _effective_multiplier(ticker, fee_multiplier_override)
    if multiplier == 0.0:
        return 0.0
    maker_type_factor = _MAKER_TYPE_MULTIPLIER.get(fee_type_override, 1.0)
    raw = _MAKER_RATE * maker_type_factor * multiplier * contracts * price * (1 - price)
    return math.ceil(raw * 1_000_000) / 1_000_000


def taker_fee_per_contract(price: float, ticker: str | None = None) -> float:
    """Taker fee in DOLLARS PER CONTRACT at `price` - the same rate, same
    per-series multiplier, as taker_fee(), deliberately WITHOUT its
    per-fill $0.000001 ceiling (docs/kalshi/fee_rounding.md:22, updated
    2026-08-30 for Trade API 3.29.0 - issue #253; was $0.0001 pre-3.29.0).

    That ceiling is charged once per fill, not once per contract: the real
    fills this module was originally verified against (14.11 contracts @
    $0.84 -> $0.1328 under the OLD $0.0001 ceiling) round the whole order
    total exactly once. Carrying it into a per-contract rate would price
    every contract as if it were its own one-contract order, overstating
    the rate by up to $0.000001/contract, and would make an aggregate
    metric depend on a fill size that isn't real. So this is the exact
    rate, and taker_fee(n, p)/n converges to it as n grows - PR #224's
    convergence argument is unaffected by the finer ceiling: ceil_k(raw) -
    raw < 10^-k by definition, so shrinking k from 4 to 6 only tightens the
    per-fill (and therefore per-contract, spread over n) overstatement
    bound, from < 1e-4/n to < 1e-6/n. It does not change which quantity the
    ceiling is applied to or that it's applied once per fill.

    Exists because "what does a contract at price c really cost me" is a
    question two separate analytics modules ask (series_watcher.reconcile,
    reset.trade_archive._summarise) and neither may re-derive 0.07 or the
    multiplier table locally.

    Same event-level fee_multiplier_override precedence as taker_fee()
    (issue #264) - breakeven_unit_cost() below calls this, so an override
    reaches breakeven math automatically, with no separate plumbing."""
    if price <= 0 or price >= 1:
        return 0.0
    _, fee_multiplier_override = _event_fee_override(ticker)
    return _TAKER_RATE * _effective_multiplier(ticker, fee_multiplier_override) * price * (1 - price)


def unit_cost(side: str, yes_price: float | None) -> float | None:
    """Per-contract cost, in dollars, of one `side` contract when the YES
    price is `yes_price`: the yes price itself for "yes", its complement
    (1 - yes_price) for "no". Kalshi quotes in yes terms and a no position
    at yes price X costs (1 - X) - "a bid for yes at price X is equivalent
    to an ask for no at price (100-X)" (docs/kalshi/get-market-orderbook.md).
    Every price this app carries (WhaleSignal.price, Position.entry_price,
    PendingOrder.limit_price, trades.price, market snapshots) is the yes
    price by convention, so every side-aware dollar figure - cost basis,
    proceeds, fill cost, the admission band - goes through here.

    The arithmetic is strategy_engine._validate_entry_price's own gate
    expression, `price if side == "yes" else (1 - price)`, bit for bit
    (tests/test_kalshi_fees.py pins it), so the band a signal is admitted
    against, the charge the broker takes and every analytics read agree.
    Issue #212: 26 inline copies and three byte-identical private helpers
    (diagnostics, series_watcher, reset.trade_archive) had converged on
    this one line - the no-side inversion is one of the two shipped bugs
    CLAUDE.md cites under "A displayed value must match its label";
    tools/quality_audit/unit_cost.py now fails CI on a fresh inline copy.

    `side` must be exactly "yes" or "no", the two strings every producer
    emits (services/kalshi/contracts/trade.py's OutcomeSide, WhaleSignal
    .side, Position.side; all 655 persisted trades and 93,943 signals
    checked 2026-08-30 carry nothing else). Any other value raises: the
    inline copies silently treated an unknown side as "no" - wrong
    direction AND wrong cost with no trace, the exact failure
    resolve_taker_outcome_side's docstring records - and a guessed side is
    worse than a loud one.

    A None price stays None ("no price known" must never become an
    invented cost) - the contract the three private helpers had, which
    the WebSocket reader gate relies on to record a rejection with
    unit_cost=None rather than 0.0.

    The result is also the market-implied probability of `side` (a
    contract pays $1 or $0), which is why breakeven_unit_cost() below can
    add a fee to it directly."""
    if side == "yes":
        return yes_price
    if side == "no":
        return None if yes_price is None else 1 - yes_price
    raise ValueError(f"side must be exactly 'yes' or 'no', got {side!r}")


def sellable_quote(
    side: str, yes_bid: float | None, yes_ask: float | None,
    *, crossed_against: float | None = None,
) -> float | None:
    """The price, in YES terms, at which an open `side` position can actually
    be SOLD right now - or None when it cannot be sold at all.

    Companion to unit_cost() above and deliberately in the same module: that
    function answers "what does one contract of this side cost at this yes
    price", this one answers "which yes price is this side's sale actually
    struck at". Both encode the same one-sided convention, so a caller that
    gets the first right and the second wrong still books the wrong money.

    Kalshi returns yes bids and no bids only: "a bid for yes at price X is
    equivalent to an ask for no at price (100-X)" (docs/kalshi/
    get-market-orderbook.md:7). Reading that in the selling direction, the
    bid a holder hits is yes_bid for a YES position and (1 - yes_ask) for a
    NO one. Returning yes_ask for the NO case, rather than the NO bid
    directly, keeps every caller on this app's one universal convention -
    every price it carries is a yes price - so unit_cost(side, quote) turns
    the result into per-contract dollars unchanged.

    The 2026-09-04 incident this exists to prevent: exits priced BOTH sides
    off state["latest_prices"] (yes_bid), valuing a NO position at
    (1 - yes_bid) - the NO *ask*, what it costs to BUY no, not what a seller
    receives. On an empty yes book (yes_bid 0.000) that paid $1.00/contract
    as if the market had settled NO. 506 auto-exits booked +$174,727 against
    -$72,361 of real settlements over two days, and the same phantom mark
    maxed the auto-exit confidence factor so the exit fired.

    Returns None rather than falling back to a guessed quote, in three
    cases: no ask at all for a NO position, an ask of 1.00 (a NO bid of
    0.00 - the empty-book shape above), and a book crossed against the bid.
    A caller that must not refuse (a manual flatten) uses
    forced_exit_quote() below instead of inventing its own fallback.

    crossed_against: the bid the ask is checked against for a crossed book,
    when that differs from the `yes_bid` used for pricing. services/exits/
    exit_engine.py may substitute a REST-corroborated bid (up to 120s old,
    from market_history) for the WS one before pricing a YES exit; comparing
    a stale REST bid against a live WS ask is not like-for-like and would
    refuse legitimate NO exits on a market that has genuinely moved, so that
    caller passes the raw WS bid here while still pricing off the
    corroborated one. Defaults to `yes_bid` (same value for both jobs)."""
    if side not in ("yes", "no"):
        raise ValueError(f"side must be exactly 'yes' or 'no', got {side!r}")
    if side == "yes":
        # No bid at all means nobody will buy this YES position.
        return yes_bid if yes_bid is not None and yes_bid > 0.0 else None
    if yes_ask is None or yes_ask >= 1.0:
        return None
    bid = crossed_against if crossed_against is not None else yes_bid
    if bid is not None and yes_ask < bid:
        return None
    return yes_ask


def forced_exit_quote(side: str, yes_bid: float | None, yes_ask: float | None) -> float:
    """sellable_quote(), but never None - for the manual "get flat now" paths
    (POST /api/trading/flatten-all, POST /api/trading/close-positions,
    position netting) where refusing to close is not an option the caller
    has, unlike an automated exit check that can simply leave the position
    alone until the next tick.

    An unsellable book resolves to ZERO proceeds - yes_price 0.0 for a YES
    position, 1.00 for a NO one, both of which unit_cost() turns into $0.00
    per contract - never to the fabricated $1.00 the old (1 - yes_bid) path
    produced. "Nobody will buy this" is worth nothing, not everything; the
    2026-09-04 repair credited exactly $0.00 to the one live position that
    hit this case (KXETHD-26SEP0418-T2449.99, yes_ask 1.00 with zero resting
    size, market resolving YES), rather than guess a price."""
    quote = sellable_quote(side, yes_bid, yes_ask)
    if quote is not None:
        return quote
    return 0.0 if side == "yes" else 1.0


def breakeven_unit_cost(unit_cost: float, ticker: str | None = None) -> float:
    """The win probability a contract bought at `unit_cost` needs just to
    break even, expressed as a unit cost in dollars per contract (the two
    are the same number: a contract pays $1 or $0, so EV per contract is
    exactly p - unit_cost - fee, and EV = 0 at p = unit_cost + fee).

    `unit_cost` is the side-aware per-contract cost - (1 - yes_price) for a
    no-side position - not the raw yes price. The fee is symmetric in price
    and its complement (see this module's docstring), so it is the same
    either way, but the cost it is added to is not.

    Fee-free breakeven ("breakeven accuracy IS the entry price") was shipped
    and displayed until 2026-08-30 - issue #205, the "a displayed value must
    match its label" class in CLAUDE.md. The understatement is exactly
    100 * _TAKER_RATE * multiplier * c * (1-c) points: 1.75 at c = 0.50 down
    to 0.63 at c = 0.90, the band config/settings.yaml actually enforces
    (min_unit_cost 0.5 / max_unit_cost 0.9, read 2026-08-30 - the 0.60-0.85
    quoted in older notes is stale), halved for the MLB proposition family
    and exactly zero for the multiplier-0 series."""
    return unit_cost + taker_fee_per_contract(unit_cost, ticker=ticker)
