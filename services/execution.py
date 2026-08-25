"""Application execution service — high-level REAL-order orchestration
(Kalshi Integration Phase A Task A9).

Per the design spec's capability boundaries: "the application execution
service owns high-level policy such as emergency flattening/close
orchestration. It composes account/order primitives while keeping raw
write capability narrow." The vendor adapter (services/kalshi/orders.py)
exposes gated create/cancel primitives; deciding WHAT to do with them —
which positions to close, at what price, in what order, how to handle
partial failure — is application policy and lives here, above the
integration boundary.

Paper-mode execution is unchanged and elsewhere: PaperBroker.
close_all_positions is the paper half of the emergency flatten;
services/exits/ owns per-position exit *decisions*. This module is only
the real-account half, called by main.py's POST /api/trading/flatten-all
route behind its typed-confirmation gate.
"""
from services.kalshi.interfaces import FlattenCapable



async def flatten_all_real_positions(account: FlattenCapable) -> list[dict]:
    """Closes every currently-open real market position via an
    aggressive IOC order per ticker (2026-08-23 gap-check finding: no
    "get flat immediately" path existed for the real account either -
    the paper-mode half is PaperBroker.close_all_positions). No bulk
    flatten endpoint exists on Kalshi (confirmed against docs/kalshi/ -
    order-groups/trigger-order-group only cancel resting orders, never
    touch open positions), so this is the only real path: one
    create_order call per ticker.

    `account` is the KalshiAccountClient facade (or anything exposing its
    gated get_positions/create_order surface) - every order placed here
    still passes through KalshiOrderGateway's trading_enabled gate, and
    uses is_closing_order=True to bypass only the risk-halt guard
    (flattening during a halt is risk-reducing - exactly what an
    emergency flatten mid-halt must do).

    Side/price mapping verified against docs/kalshi/create-order-v2.md's
    BookSide description ("this endpoint quotes everything from the
    YES side: bid means buy YES, ask means sell YES") and
    docs/kalshi/get-positions.md's position_fp description ("negative
    means NO contracts and positive means YES contracts") - NOT
    guessed from the legacy action/side vocabulary in
    docs/kalshi/order_direction.md, which uses a different vocabulary
    for a different (non-v2) surface and would give the wrong mapping
    here if followed directly. A held YES position (position_fp > 0)
    closes by SELLING yes (side="ask"); a held NO position
    (position_fp < 0) closes by BUYING yes (side="bid"), which nets
    against the held NO contracts per Kalshi's binary-market
    settlement (1 YES + 1 NO always nets to exactly $1). Price is
    pinned to the extreme end of the 1-99 cent range on each side
    (0.01 for an ask, 0.99 for a bid) so the IOC order is guaranteed to
    cross the current book rather than rest - an emergency flatten
    needs the fill, not the best price.

    Disclosed, not silently assumed: this exact call path has never
    been exercised against a real fill (trading_enabled is false by
    default and no strategy code calls create_order today - this is
    the first real caller). The side/price mapping above is grounded
    directly in the docs, same confidence level as the rest of the
    already-implemented, doc-verified order schema - but "schema is
    correct" and "has produced one real observed fill" are different
    claims, and only the first one is true here yet. Same disclosure
    discipline services/account_positions.py's own REST-vs-WS deferral
    already applies to unverified real-money paths in this codebase."""
    snapshot = await account.get_positions()
    results = []
    for pos in (snapshot.get("market_positions") or []):
        ticker = pos.get("ticker")
        position_fp = float(pos.get("position_fp") or 0)
        if not ticker or position_fp == 0:
            continue
        side = "ask" if position_fp > 0 else "bid"
        price = "0.0100" if side == "ask" else "0.9900"
        count = f"{abs(position_fp):.2f}"
        try:
            order = await account.create_order(
                ticker=ticker, side=side, count=count, price=price,
                time_in_force="immediate_or_cancel", is_closing_order=True,
            )
            results.append({"ticker": ticker, "position_fp": position_fp, "order": order, "error": None})
        except Exception as e:
            results.append({"ticker": ticker, "position_fp": position_fp, "order": None, "error": str(e)})
    return results
