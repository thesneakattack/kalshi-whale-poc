"""Closed semantic value types for the Kalshi boundary — Phase C Task C2.

Type policy (design spec "Numeric policy" + C2): closed semantics use
Literal types where an unknown value is UNSAFE to act on — direction is
the canonical case, since a guessed side once flipped every unreadable
trade to a confident "no". Open vendor extension values (fee_type,
lifecycle event_type, category/facet strings, ...) deliberately stay
plain strings: live Kalshi has already grown at least one enum beyond
its documented values safely (fee_type "quadratic_with_combo_maker_fees",
2026-08-21 — see services/kalshi/public.py's get_series_list), and a
closed type there would turn a harmless addition into a crash.

The resolution maps below are the ONE copy of the yes/no ⇄ bid/ask
vocabulary equivalence (docs/kalshi/order_direction.md: "bid ≡ yes,
ask ≡ no, always") — trade.py and fill.py both consume them, so the two
channels can never disagree about the mapping. Their .get() lookups are
also what narrows an untrusted wire string to the Literal type: an
unknown value maps to None, never to a guessed member.
"""
from __future__ import annotations

from typing import Literal

# Which outcome a participant is positioned for. Closed: docs/kalshi/
# order_direction.md defines exactly these two, and acting on anything
# else would be acting on a guess.
OutcomeSide = Literal["yes", "no"]

# Book-vocabulary side (create-order-v2.md's BookSide: everything quoted
# from the YES leg - bid means buy YES, ask means sell YES).
BookSide = Literal["bid", "ask"]

# Wire string -> OutcomeSide, None for anything unknown.
AS_OUTCOME_SIDE: dict[str, OutcomeSide] = {"yes": "yes", "no": "no"}

# Book vocabulary -> outcome vocabulary (bid ≡ yes, ask ≡ no, always).
BOOK_SIDE_TO_OUTCOME: dict[str, OutcomeSide] = {"bid": "yes", "ask": "no"}
