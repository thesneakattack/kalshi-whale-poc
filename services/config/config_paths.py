"""
Generic "section.field" config-path helpers - not analyst-specific, even
though today's only callers are the market-analyst suggestion-apply routes'
staleness checks. Extracted 2026-08-21 as part of main.py's modularization
pass: originally slated for services/analytics/market_analyst_orchestrator.py
(since that's the only current caller), but a config-value comparator
belongs to the config concern, not the concern that happens to call it.
"""


def _config_value_at_path(cfg: dict, config_path: str):
    """Reads a "section.field" path out of a live config dict - the read
    side of the same section/field split every apply route already does
    for writes (config_store.update({section: {field: value}})). Used to
    catch a stale suggestion: an LLM-derived suggestion (series/full-
    spectrum analyst) is looked up from what was persisted at analysis
    time, not recomputed fresh the way a rule-based Advisory recommendation
    is - if the live config's actual current value has since drifted from
    what the suggestion assumed (a manual edit, an auto-apply, or a second
    analysis elsewhere), blindly applying it would silently overwrite based
    on a stale premise and log a fabricated "before" value that was never
    actually live. Missing section/field reads as None, same as dict.get."""
    section, _, field = config_path.partition(".")
    return (cfg.get(section) or {}).get(field)


def _types_compatible(a, b) -> bool:
    """Loose type-compatibility check for a full-spectrum suggestion's
    value against the field's current one - int/float are interchangeable
    (a human editing the Config tab's number inputs doesn't distinguish
    them either), bool is checked strictly on both sides since Python's
    bool is technically an int subclass and a stray True/False landing in
    a numeric field would be a real, confusing config corruption, not a
    reasonable suggestion."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return True
    return type(a) is type(b)
