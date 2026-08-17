"""
Physically-achievable ranges for config values, and validation against them.

Direct request (2026-08-17): "another major thing for you to look at is the
acceptable value ranges. the avisory is askings me to change my max loss
when it comes to auto closing is 1.75 the cost of opening my position."

Several `strategy.*` fields are expressed as a fraction of cost basis, and
a binary contract puts a hard ceiling on what those fractions can ever
reach. Nothing enforced it, so both hand-tuning and
`advisory_engine._exit_pct_recommendation` could set a value that is not
merely aggressive but *unreachable* - which does not fail loudly, it just
silently disables the rule forever.

The arithmetic, for a contract bought at unit cost c (what the taker
actually paid, side-aware - see PaperBroker.cost_basis):

    settlement pays either $1 or $0 per contract
    max GAIN  = (1 - c) / c   as a fraction of cost basis
    max LOSS  = 1.0           as a fraction of cost basis (the whole stake)

So `take_profit_pct` has a ceiling that depends entirely on the entry price
band, while `stop_loss_pct` above 1.0 is meaningless for any entry at all.

Measured consequence of not having this, 2026-08-17: with
`min_unit_cost/max_unit_cost = 0.5/0.8` and `take_profit_pct = 0.8`,
take-profit was reachable only for entries priced at or below c = 0.556 -
a sliver of the allowed band - while stop-loss remained reachable for
every position. A one-way ratchet: every position could lose, most could
not take profit. Observed over 24h: 25 stop-losses (-$6,422) against 3
take-profits.

Read-only and pure - this module computes and reports, it never mutates
config. `advisory_engine` calls `clamp()` on its own suggestions;
`diagnostics` surfaces violations; `/api/config` warns.
"""

# Fields whose value is a fraction of cost basis and therefore bounded by
# the settlement arithmetic above, rather than by taste.
_COST_BASIS_FRACTION_FIELDS = ("take_profit_pct", "stop_loss_pct")

# The tradeable price range, as an invariant rather than a setting (direct
# instruction, 2026-08-17: "whale bets at cost 0 or 100c are just plain
# wrong. youre not even allowed to open positions at that point, even
# 1c/99c" — and, sharper: such prints "are just plain wrong to be logged in
# the first place").
#
# Expressed as unit cost, which is already side-aware, so a "no" print at a
# 1c yes-price is caught by the same bound that catches a "yes" print at
# 99c.
#
# Why this is not config: min_unit_cost/max_unit_cost express a strategy
# preference about where the edge lives and are meant to be tuned. This is
# a statement about what a binary contract can arithmetically do. At unit
# cost 1.00 the best possible outcome is breaking even; at 0.99 a win pays
# 1c against 99c at risk, needing 99% accuracy just to break even (EV per
# contract is exactly p - c). No configuration should be able to reach past
# that, so nothing reads these from settings.yaml.
#
# Enforced in TWO places, deliberately:
#   - services/whalewatchers/kalshi_trade_tape.py, at signal creation, so
#     such a print never reaches signal_log at all. This is the important
#     one: signal_log is what every accuracy statistic in this app reads,
#     and near-certain prints are trivially "correct", so logging them
#     inflates the headline whale win rate while describing trades nobody
#     could ever profitably take.
#   - services/strategy_engine.py, at entry, as defence in depth for any
#     other signal source (the simulator, a future provider).
MIN_TRADEABLE_UNIT_COST = 0.02
MAX_TRADEABLE_UNIT_COST = 0.98


def is_tradeable_unit_cost(unit_cost: float | None) -> bool:
    """One definition, shared by the provider and the strategy engine, so
    the thing that refuses to log a print and the thing that refuses to
    trade it can never disagree about where the boundary is."""
    if unit_cost is None:
        return False
    return MIN_TRADEABLE_UNIT_COST <= unit_cost <= MAX_TRADEABLE_UNIT_COST

# A loss can slightly exceed the stake once entry+exit fees are counted
# (services/kalshi_fees.py), so the stop-loss ceiling is not exactly 1.0.
# Kept small and explicit rather than hand-waved: this is headroom for
# fees, not permission to set an arbitrary number.
_FEE_HEADROOM = 0.05

# These bounds are computed as (1 - c) / c, which is not exact in binary
# floating point: (1 - 0.8) / 0.8 evaluates to 0.24999999999999994, so a
# take_profit_pct of exactly 0.25 - the correct, precisely-reachable value
# for a 0.8 ceiling - would otherwise be reported as unreachable. Real bug,
# caught 2026-08-17 immediately after setting that value for real.
_EPS = 1e-9


def max_gain_fraction(unit_cost: float) -> float:
    """Largest achievable gain, as a fraction of cost basis, for a position
    entered at `unit_cost`. (1 - c) / c — at c = 0.5 a win doubles the
    stake (1.0); at c = 0.8 it can only ever return 25%."""
    if unit_cost <= 0:
        return float("inf")
    return (1.0 - unit_cost) / unit_cost


def take_profit_ceiling(strat_cfg: dict) -> float | None:
    """The most generous take_profit_pct that ANY position allowed by this
    config's price band could ever reach — i.e. the one entered at the
    cheapest permitted unit cost. A value above this can never trigger for
    any position at all."""
    lo = strat_cfg.get("min_unit_cost")
    if lo is None:
        return None
    return max_gain_fraction(float(lo))


def take_profit_universal(strat_cfg: dict) -> float | None:
    """The largest take_profit_pct reachable by EVERY position in the band —
    the one entered at the most expensive permitted unit cost. Between this
    and take_profit_ceiling(), a take-profit is reachable for some entries
    but not others, which is legitimate but worth stating explicitly."""
    hi = strat_cfg.get("max_unit_cost")
    if hi is None:
        return None
    return max_gain_fraction(float(hi))


def reachable_below(take_profit_pct: float) -> float:
    """The highest unit cost whose max gain still reaches this
    take_profit_pct. Inverts (1-c)/c >= tp  ->  c <= 1/(1+tp)."""
    return 1.0 / (1.0 + take_profit_pct)


def check(cfg: dict, scope: str = "strategy") -> list[dict]:
    """Every bound violation in one resolved strategy config. Returns a list
    of {field, value, bound, severity, detail}; empty means everything is
    at least physically reachable. severity "unreachable" means the rule can
    never fire for any position (it is silently disabled); "partial" means
    it fires for only part of the allowed price band."""
    out = []
    stop_loss = cfg.get("stop_loss_pct")
    if stop_loss is not None and stop_loss > 1.0 + _FEE_HEADROOM:
        out.append({
            "scope": scope, "field": "stop_loss_pct", "value": stop_loss,
            "bound": round(1.0 + _FEE_HEADROOM, 3), "severity": "unreachable",
            "detail": (
                f"a position can lose at most its whole stake (1.0 of cost basis, plus fees), so a "
                f"stop_loss_pct of {stop_loss} can never trigger — the stop is effectively disabled "
                f"and every losing position rides to settlement"
            ),
        })

    tp = cfg.get("take_profit_pct")
    ceiling = take_profit_ceiling(cfg)
    universal = take_profit_universal(cfg)
    if tp is not None and ceiling is not None:
        if tp > ceiling + _EPS:
            out.append({
                "scope": scope, "field": "take_profit_pct", "value": tp,
                "bound": round(ceiling, 3), "severity": "unreachable",
                "detail": (
                    f"max achievable gain at the cheapest permitted entry (unit cost "
                    f"{cfg.get('min_unit_cost')}) is {ceiling:.3f} of cost basis, so a take_profit_pct "
                    f"of {tp} can never trigger for any position this config allows"
                ),
            })
        elif universal is not None and tp > universal + _EPS:
            c_max = reachable_below(tp)
            out.append({
                "scope": scope, "field": "take_profit_pct", "value": tp,
                "bound": round(universal, 3), "severity": "partial",
                "detail": (
                    f"reachable only for entries at unit cost <= {c_max:.3f}; unreachable for anything "
                    f"priced {c_max:.3f}-{cfg.get('max_unit_cost')}. Stop-loss stays reachable for all "
                    f"of them, so most positions can lose but not take profit"
                ),
            })

    lo, hi = cfg.get("min_unit_cost"), cfg.get("max_unit_cost")
    if lo is not None and hi is not None and float(lo) > float(hi):
        out.append({
            "scope": scope, "field": "min_unit_cost", "value": lo, "bound": hi,
            "severity": "unreachable",
            "detail": f"min_unit_cost {lo} exceeds max_unit_cost {hi} — the band is empty, no entry can qualify",
        })
    for field in ("min_unit_cost", "max_unit_cost"):
        v = cfg.get(field)
        if v is not None and not (0.0 < float(v) < 1.0):
            out.append({
                "scope": scope, "field": field, "value": v, "bound": "0 < c < 1",
                "severity": "unreachable",
                "detail": f"{field} must lie strictly between 0 and 1 — a contract's price cannot leave that range",
            })
    return out


def check_all(cfg: dict) -> list[dict]:
    """check() across the global strategy config and every category/series
    override, since an override can be individually broken while the global
    default is fine (and vice versa)."""
    from services import config_overrides

    base = cfg.get("strategy") or {}
    overrides = cfg.get("strategy_overrides") or {}
    out = list(check(base, "strategy"))
    for tier, label in (("by_category", "category"), ("by_series", "series")):
        for key in (overrides.get(tier) or {}):
            resolved = config_overrides.resolve(
                base, overrides,
                category=key if tier == "by_category" else None,
                series=key if tier == "by_series" else None,
            )
            out.extend(check(resolved, f"{label}:{key}"))
    return out


def clamp(field: str, value: float, strat_cfg: dict) -> tuple[float, str | None]:
    """Bound an advisory suggestion to something physically achievable.
    Returns (clamped_value, note) — note is None when nothing was changed.

    advisory_engine._exit_pct_recommendation had no upper bound at all on
    its take-profit branch (`current_value + avg_left_pct`), which is how it
    came to suggest a value well above 1.0."""
    if field == "stop_loss_pct":
        ceiling = 1.0 + _FEE_HEADROOM
    elif field == "take_profit_pct":
        ceiling = take_profit_ceiling(strat_cfg)
    else:
        return value, None
    if ceiling is None or value <= ceiling:
        return value, None
    return round(ceiling, 3), (
        f"clamped from {value} to {ceiling:.3f}, the largest value any position allowed by the "
        f"current price band could actually reach"
    )
