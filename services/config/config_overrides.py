"""
Generic per-category/per-series config override resolver, used by
FollowTheWhaleStrategy (and, until its 2026-08-22 removal, the
now-deleted Market-Native strategy too - a field-agnostic dict-merge, not
tied to either strategy specifically). Direct request (2026-08-15): "these
strategies need to be able to be tweaked for individual series (pga tour,
basketball championship, bitcoin price every 15 minutes, what trump will
say on tv, etc)."

Before this, every strategy.* tuning field was a single
flat scalar applied identically to every series and category - a fast
15-minute crypto market and a multi-day tournament shared the same
cooldown/stop-loss/entry-threshold, with no general way to say otherwise.
The one prior exception, strategy.entry_threshold_by_category, was a
bespoke single-field mechanism (see strategy_engine.py's own history) -
this replaces it with one generic resolver any field can use.

Resolution order, most-general to most-specific, most-specific wins:
    global default (the flat scalar already in strat_cfg) -> category -> series
Each layer only overrides the specific fields it sets - an unset field
falls through to the next-coarser layer. Category is coarser than series
(one category like "Sports" spans many series like KXNFLGAME); series is
the finer, per-recurring-market-type grouping (signal_log.series_of).
There is deliberately no by_market (single-ticker) tier - nothing in this
codebase generates or edits one (no suggestion engine targets a single
ticker, no manual per-ticker editor exists), so it would ship as dead
weight; a clean additive extension if that's ever actually needed.
"""

_TIERS = ("by_category", "by_series")


def resolve(strat_cfg: dict, overrides: dict | None, category: str | None = None, series: str | None = None) -> dict:
    """Returns a NEW dict: strat_cfg with by_category then by_series
    overrides layered on top (series wins over category, matching "most
    specific wins"). Never mutates strat_cfg or overrides. Missing/None
    overrides, or no matching category/series entry, is a complete no-op -
    every existing caller with no strategy_overrides populated yet sees
    identical behavior to today."""
    resolved = dict(strat_cfg)
    overrides = overrides or {}
    if category:
        resolved.update((overrides.get("by_category") or {}).get(category) or {})
    if series:
        resolved.update((overrides.get("by_series") or {}).get(series) or {})
    return resolved


def merge_override(overrides: dict | None, scope: str, key: str, field: str, value) -> dict:
    """Returns a NEW overrides dict with overrides[scope][key][field] = value,
    every other existing entry preserved untouched - fixes config_store.
    update()'s one-level-deep-merge footgun (CLAUDE.md) for this specific
    nested shape, once, centrally, rather than every writer hand-
    reconstructing the whole nested dict inline. scope is "by_category" or
    "by_series"."""
    if scope not in _TIERS:
        raise ValueError(f'scope must be one of {_TIERS}, got {scope!r}')
    result = {tier: {k: dict(v) for k, v in (overrides or {}).get(tier, {}).items()} for tier in _TIERS}
    result[scope].setdefault(key, {})
    result[scope][key] = {**result[scope][key], field: value}
    return result


def remove_override(overrides: dict | None, scope: str, key: str, field: str | None = None) -> dict:
    """Inverse of merge_override, same non-mutating/preserve-siblings
    contract. field=None drops the whole key (e.g. deleting a series'
    override entirely, not just one field of it); a given field drops just
    that field, and the key itself is dropped too if that empties it - so a
    resolved-to-empty entry never lingers as a visible-but-inert {} in the
    config. Removing a field/key that isn't there is a no-op, not an error
    - the Config-tab UI this backs can't easily guarantee it never double-
    fires a remove click."""
    if scope not in _TIERS:
        raise ValueError(f'scope must be one of {_TIERS}, got {scope!r}')
    result = {tier: {k: dict(v) for k, v in (overrides or {}).get(tier, {}).items()} for tier in _TIERS}
    if key not in result[scope]:
        return result
    if field is None:
        del result[scope][key]
        return result
    result[scope][key].pop(field, None)
    if not result[scope][key]:
        del result[scope][key]
    return result
